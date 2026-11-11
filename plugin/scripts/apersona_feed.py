"""收錄 for the platforms that have no event proving a human typed a turn: Gemini CLI (`gemini`), Grok (`grok`) and any
AI tool whose only hook is the one before a tool runs (`pretool`); plus Cursor (`cursor`), whose `generation_id` names
each turn so its prompts are taken by the hook, and Antigravity (`antigravity`), which only gets the opening and takes
no words at all. Claude and Codex keep their own paths in `apersona_hook.py`; nothing here touches them.

    python apersona_feed.py hook --platform gemini|pretool|cursor|antigravity [--event NAME]
                                                                  (the platform's hook JSON on stdin; prints JSON)
    python apersona_feed.py send --platform P --session S --id ID (--text T | --text-file F)
    python apersona_feed.py flush|status --platform P
    python apersona_feed.py consent --platform P yes|no [--by-agent]
    python apersona_feed.py setup --platform P [--site URL]

What each platform may send:

- The text is only what he sends himself (`send`, at his terminal, after he reads it and types y), with an id he gives
  for the turn. No id is `id_required`, nothing kept, nothing sent: an id is never made from the text, the time or the
  session. Gemini's BeforeAgent has no stable turn id, so that hook is also `id_required` and takes nothing.
- Cursor is the exception: its `beforeSubmitPrompt` carries the prompt he submitted and a `generation_id`, so the hook
  keeps it (secrets masked) under `cursor:<conversation_id>:<generation_id>` and `stop` sends it. Without the
  `generation_id` nothing is kept. Antigravity takes no words: its SessionStart only gets the opening.
- A hook event only wakes the flush of what he already sent and consented to. Its prompt, model response, tool name,
  tool arguments and tool output are never read, kept or sent.
- Each platform has its own consent text and hash; Claude's and Codex's do not count. 收錄 off clears what is waiting;
  turning it on again starts a new `since`, and nothing typed before it is brought back.
- A send that could not finish (network, 5xx, 429) stays waiting and the next flush sends it again under the same
  ids, which the site de-duplicates. A permanent refusal drops the row.

Standard library only, python 3.9 and up (one exception per `except`: the formatter would drop the parentheses that
python 3.9 needs around several). No key is read or written: the site is reached with the device login.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apersona_hook as hook  # noqa: E402
from apersona_mask import redact_secrets  # noqa: E402
from apersona_projects import allows  # noqa: E402
from apersona_safe import EXIT_FAILED, EXIT_UNFINISHED, exit_line, terminal_input  # noqa: E402

#: his consent text per platform; the site holds the same words (`memory/conversation.py`) and checks the hash
CONSENT_TEXTS = {
    "gemini": (
        "同意收錄本人在 Gemini CLI 明示送入的字（沒有穩定輪次識別，不自動收；模型回答與工具參數不收）；"
        "秘密先遮、可撤回、素材每日合併讀。"
    ),
    "grok": (
        "同意收錄本人在 Grok 明示送入的字（沒有每輪 hook，不自動收；connector 改寫的內容與工具參數不收）；"
        "秘密先遮、可撤回、素材每日合併讀。"
    ),
    "pretool": (
        "同意收錄本人在只有工具前事件的 AI 工具明示送入的字（工具名稱、參數與輸出不收）；"
        "秘密先遮、可撤回、素材每日合併讀。"
    ),
    "cursor": (
        "同意收錄本人在 Cursor 送出的字（以 Cursor 給的 generation_id 當輪次識別；模型回答、工具參數與輸出不收）；"
        "秘密先遮、可撤回、素材每日合併讀。"
    ),
}
#: platforms with no capture (so no consent text): Antigravity only receives the opening
NO_CAPTURE = ("antigravity",)
CONSENT_SHA256 = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in CONSENT_TEXTS.items()}
#: hook event -> what it may do. `flush` wakes the sending of what is already consented; `id_required` is a human
#: prompt with no stable id (nothing is taken); `capture` keeps the submitted prompt under its generation id;
#: `opening` hands the opening pack back to the tool; an event not listed is not ours. Grok has no hook at all.
EVENT_ROLES = {
    "gemini": {"BeforeAgent": "id_required", "AfterAgent": "flush"},
    "pretool": {"PreToolUse": "flush"},
    "grok": {},
    "cursor": {"sessionStart": "opening", "beforeSubmitPrompt": "capture", "stop": "flush"},
    "antigravity": {"SessionStart": "opening"},
}
#: what the platform's hook must print on stdout when there is nothing else to say (Cursor's submit hook needs
#: `continue`, or it would be read as a veto)
QUIET_OUTPUT = {"cursor": {"beforeSubmitPrompt": {"continue": True}}}


def data_dir(platform: str) -> Path:
    return Path.home() / ".apersona" / platform


def consent(platform: str) -> dict[str, object]:
    "His current consent for this platform ({} when off, unreadable or given for another text)."
    try:
        value = json.loads((data_dir(platform) / "capture-consent.json").read_text(encoding="utf-8"))
    except OSError:
        return {}
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    record = cast("dict[str, object]", value)
    return record if record.get("on") is True and record.get("sha256") == CONSENT_SHA256[platform] else {}


def write_consent(platform: str, on: bool, by_agent: bool = False) -> dict[str, object]:
    """Record his answer and return it; an off also drops everything waiting, and an on starts a new `since`. An on
    also says who opened it: `owner` he typed the phrase at his terminal, `agent` an AI tool did with his
    authorization (`--by-agent`), and through which platform (the same record the Claude and Codex hook writes)."""
    data = data_dir(platform)
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    record: dict[str, object] = {
        "on": on,
        "sha256": CONSENT_SHA256[platform],
        "since": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }
    if on:
        record["by"] = "agent" if by_agent else "owner"
        record["tool"] = platform
    temp = data / f".capture-consent.{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(json.dumps(record))
        os.replace(temp, data / "capture-consent.json")
    finally:
        temp.unlink(missing_ok=True)
    if not on:
        hook.clear_spool(data)
    return record


def report_consent(platform: str, record: dict[str, object]) -> str:
    "Tell the site this device's 收錄 went on or off (the hook's `tell_site`); \"\" when recorded, else why not."
    return hook.tell_site(site_of(platform), record, platform)


def answer_consent(platform: str, answer: str, by_agent: bool = False) -> int:
    """`consent yes|no`. No is recorded at once (and told to the site, best effort). Yes comes in the same two ways as
    Claude's: he at a terminal types the phrase (recorded `owner`); or, with `--by-agent`, an AI tool he authorized
    opens it with no terminal: the consent text is printed in full the same, the site must record it (and mail him)
    first, and a site that cannot be reached, or that he took the 收錄 back on, means it stays off. A pipe with no flag
    cannot turn it on."""
    if answer == "no":
        report_consent(platform, write_consent(platform, False))
        print("收錄已關閉。")
        return 0
    if by_agent:
        print("同意書：" + CONSENT_TEXTS[platform])
        print("以上由 AI 工具代本人開啟（本人已授權）；開啟後會寄通知信給本人，網站「裝置」頁可一鍵撤回。")
        refused = report_consent(platform, {"on": True, "by": "agent"})
        if refused:
            return hook.report_refused(refused)
        write_consent(platform, True, True)
        print("收錄已開啟（由 AI 工具代開）；隨時可用 consent no 撤回，本人也能在網站裝置頁撤回。")
        return 0
    if not terminal_input():
        print("收錄要本人在自己的終端機打開；或由本人明說授權的 AI 工具加 --by-agent 代開。")
        print(exit_line(EXIT_FAILED))
        return EXIT_FAILED
    print("同意書：" + CONSENT_TEXTS[platform])
    try:
        typed = input(f"要開，請輸入「{hook.CONSENT_PHRASE}」；其他任何輸入都是不開：").strip()
    except EOFError:
        typed = ""
    except OSError:
        typed = ""
    record = write_consent(platform, typed == hook.CONSENT_PHRASE)
    if typed == hook.CONSENT_PHRASE:
        report_consent(platform, record)  # best effort: his own yes needs no site to be real
        print("收錄已開啟；隨時可用 consent no 撤回。")
        return 0
    print("沒有開啟，收錄維持關閉。")
    print(exit_line(EXIT_FAILED))
    return EXIT_FAILED


def site_of(platform: str) -> str:
    "The site `setup` wrote down, else ''  (the device login's own default)."
    try:
        site = json.loads((data_dir(platform) / "config.json").read_text(encoding="utf-8")).get("site")
    except OSError:
        return ""
    except ValueError:
        return ""
    except AttributeError:
        return ""
    return site if isinstance(site, str) else ""


def log(platform: str, event: str, result: str, error: str) -> None:
    "One line of how it went: event, result, error kind, time. Never his words."
    row = {"kind": event, "result": result, "ts_ms": hook.now_ms()}
    if error:
        row["error"] = error
    data = data_dir(platform)
    try:
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (data / "events.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def drop_files(data: Path) -> None:
    "Remove every waiting file; the caller holds the spool lock."
    folder = data / "spool"
    if folder.is_dir():
        for leftover in folder.iterdir():
            if leftover.is_file():
                leftover.unlink(missing_ok=True)


def read_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return rows
    for line in lines:
        try:
            given = json.loads(line)
        except ValueError:
            continue
        fields = cast("dict[str, object]", given) if isinstance(given, dict) else {}
        values = {key: fields.get(key) for key in ("turn_id", "message_id", "text", "at", "cwd")}
        if all(isinstance(v, str) for v in values.values()):
            rows.append(cast("dict[str, str]", values))
    return rows


def waiting(platform: str) -> list[tuple[Path, list[dict[str, str]]]]:
    "Every spool file of this platform with its rows."
    folder = data_dir(platform) / "spool"
    return [(p, read_rows(p)) for p in sorted(folder.glob("*.jsonl"))] if folder.is_dir() else []


def stage(platform: str, session: str, message_id: str, text: str, cwd: str) -> str:
    """Keep what he sent until the flush has it acknowledged. The result is '' when kept, else why not:
    `id_required`, `consent_off`, `empty`, `project_disabled`."""
    if not session.strip() or not message_id.strip():
        return "id_required"
    kept = redact_secrets(text.strip())[: hook.MAX_CAPTURED_TEXT]
    if not kept.strip():
        return "empty"
    at = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    row = {
        "turn_id": f"{platform}:{session}:{message_id}",
        "message_id": message_id,
        "text": kept,
        "at": at,
        "cwd": str(hook.project_path(cwd)),
    }
    data = data_dir(platform)
    path = hook.spool_file(data, session)
    with hook.spool_lock(data):
        if not consent(platform):
            return "consent_off"
        if not allows(row["cwd"], at):
            return "project_disabled"
        if any(r["turn_id"] == row["turn_id"] for r in read_rows(path)):
            return ""  # the same turn sent twice is one row; the site would answer the same either way
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as out:
            out.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
    return ""


def post(platform: str, site: str, key: str, row: dict[str, str], deadline: float) -> None:
    payload = {
        "turnId": row["turn_id"],
        "messageId": row["message_id"],
        "text": row["text"],
        "consentSha256": CONSENT_SHA256[platform],
        "origin": "human",
    }
    request = urllib.request.Request(
        site.rstrip("/") + "/api/memory/conversation",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    hook.fetch_text(request, max(0, deadline - time.monotonic()))


def flush(platform: str, deadline: float, site_login: tuple[str, str] | None = None) -> tuple[str, str]:
    """(result, error) after sending what is waiting. With his consent off the waiting rows are dropped first and
    nothing else happens (no login needed). A transient failure raises `MissError` and keeps every unsent row."""
    data = data_dir(platform)
    approval = consent(platform)
    if not approval:
        hook.clear_spool(data)
        return "skipped", "consent_off"
    try:
        since = datetime.fromisoformat(str(approval.get("since", "")).replace("Z", "+00:00"))
    except ValueError:
        return "miss", "bad_consent"
    site, key = site_login or hook.login_bearer(site_of(platform), max(0.1, deadline - time.monotonic()))
    done: set[str] = set()
    dropped: list[str] = []
    try:
        for _, rows in waiting(platform):
            for row in rows:
                if not hook.typed_since(row["at"], since):
                    done.add(row["turn_id"])  # typed before he agreed this time: never sent, never kept
                    continue
                if not allows(row["cwd"], row["at"]):
                    done.add(row["turn_id"])
                    dropped.append("project_disabled")
                    continue
                if time.monotonic() >= deadline or consent(platform) != approval:
                    return "pending", "interrupted"
                try:
                    post(platform, site, key, row, deadline)
                except hook.MissError as refused:
                    if str(refused) == "consent_revoked":  # he took the 收錄 back on the website: off here too
                        write_consent(platform, False)  # also drops what waits
                        return "skipped", "consent_revoked"
                    if str(refused) not in hook.PERMANENT_REFUSALS:
                        raise
                    dropped.append("dropped_" + str(refused))
                done.add(row["turn_id"])
    finally:
        with hook.spool_lock(data):
            current = consent(platform)
            if not current:
                drop_files(data)
            elif current == approval:
                for path, rows in waiting(platform):
                    hook.write_spool(path, [r for r in rows if r["turn_id"] not in done])  # type: ignore[arg-type]
    return "ok", ",".join(dropped)


def project_dir(fields: dict[str, object]) -> object:
    "The project directory of an event: `cwd`, else the first workspace root the tool names (Cursor, Antigravity)."
    if fields.get("cwd"):
        return fields.get("cwd")
    for key in ("workspace_roots", "workspacePaths"):
        roots = fields.get(key)
        if isinstance(roots, list) and roots and isinstance(roots[0], str):
            return roots[0]
    return None


def text_field(fields: dict[str, object], *keys: str) -> str:
    "The first of these fields that is a non-blank string ('' when none)."
    for key in keys:
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def opening_output(platform: str, fields: dict[str, object]) -> tuple[str, str, dict[str, object]]:
    """(result, error, stdout object) of a SessionStart: the opening pack as the tool's own context field, fenced as
    data, with the conversation line. The pack is only fetched; nothing of his is sent."""
    conversation = text_field(fields, "conversation_id", "conversationId", "session_id")
    started = time.monotonic()
    site, key = hook.login_bearer(site_of(platform), hook.BUDGET_S)
    text, _ids = hook.fetch_text(
        hook.opening_request(site, key), max(0.1, hook.BUDGET_S - (time.monotonic() - started))
    )
    text = hook.clean(text).strip()
    if not text:
        raise hook.MissError("bad_response")
    cut = len(text) > hook.MAX_TEXT
    if cut:
        text = hook.OPENING_CUT_NOTICE + "\n" + text[: hook.MAX_TEXT]
    line = ""
    safe = "".join(hook.clean(conversation).split())
    if safe:
        line = f'\n本段對話識別：{safe}。叫 apersona 的 lookup、recall、opening 時帶 conversation_id="{safe}"。'
    body = hook.fenced(text) + line
    if platform == "antigravity":
        return ("truncated" if cut else "ok"), "", {"injectSteps": [{"systemMessage": {"systemMessage": body}}]}
    return ("truncated" if cut else "ok"), "", {"additional_context": body}


def capture_prompt(platform: str, fields: dict[str, object]) -> tuple[str, str]:
    "(result, error) of a submit event: keep the prompt under conversation + generation id; no id keeps nothing."
    session = text_field(fields, "conversation_id")
    generation = text_field(fields, "generation_id")
    prompt = fields.get("prompt")
    if not session or not generation or not isinstance(prompt, str):
        return "skipped", "id_required"
    try:
        problem = stage(platform, session, generation, prompt, str(project_dir(fields) or ""))
    except OSError:
        return "miss", "capture_failed"
    return ("ok", "") if not problem else ("skipped", problem)


def run_hook(
    platform: str, raw: str, out: list[dict[str, object]] | None = None, event_hint: str = ""
) -> tuple[str, str, str]:
    """(event, result, error) of one hook event. The event's response and tool fields are never looked at; the one
    prompt read is Cursor's submit event. `out` receives the JSON object the tool should be handed, if any.
    `event_hint` names the event for a tool whose stdin does not (Antigravity)."""
    fields = hook.hook_fields(raw)
    event = fields.get("hook_event_name")
    name = event if isinstance(event, str) else (event_hint or "unknown")
    role = EVENT_ROLES[platform].get(name)
    if platform == "grok":
        return name, "skipped", "no_hook_contract"
    if role is None:
        return name, "skipped", "unsupported_event"
    if role == "id_required":
        return name, "skipped", "id_required"
    if fields.get("stop_hook_active") is True:
        return name, "skipped", "retry_ignored"
    if not allows(project_dir(fields)):
        return name, "skipped", "project_disabled"
    try:
        if role == "opening":
            result, error, given = opening_output(platform, fields)
            if out is not None:
                out.append(given)
            return name, result, error
        if role == "capture":
            return (name, *capture_prompt(platform, fields))
        result, error = flush(platform, time.monotonic() + hook.BUDGET_S)
    except hook.MissError as miss:
        return name, "miss", str(miss)
    except OSError:
        return name, "miss", "flush_failed"
    return name, result, error


def send(platform: str, session: str, message_id: str, text: str, cwd: str) -> tuple[str, str]:
    "(status, error) of one explicit send: `sent`, `pending` (kept, will go on the next flush) or why not."
    problem = stage(platform, session, message_id, text, cwd)
    if problem:
        return problem, ""
    try:
        result, error = flush(platform, time.monotonic() + hook.BUDGET_S)
    except hook.MissError as miss:
        return "pending", str(miss)
    except OSError:
        return "pending", "flush_failed"
    if result == "ok" and "dropped_" in error:
        return "refused", error
    return ("sent" if result == "ok" else result), error


def confirmed(platform: str, text: str) -> bool:
    "He reads exactly what will go and types y at his terminal; a pipe or an agent cannot."
    if not terminal_input():
        return False
    print(f"要送到 Apersona（{platform}）的字：\n{redact_secrets(text.strip())}")
    try:
        return input("確定送出請輸入 y：").strip().lower() == "y"
    except EOFError:
        return False
    except OSError:
        return False


def say(status: str, error: str = "") -> int:
    "The one JSON line: the status, why not, and with a non-zero exit what to do next (錯誤碼總表)."
    code = 0 if status in ("sent", "pending", "ok") else EXIT_UNFINISHED
    body = {"status": status, **({"error": error} if error else {}), **({"next": exit_line(code)} if code else {})}
    print(json.dumps(body, ensure_ascii=False))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apersona_feed.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("hook", "send", "flush", "status", "consent", "setup"):
        command = commands.add_parser(name)
        command.add_argument("--platform", required=True, choices=sorted([*CONSENT_TEXTS, *NO_CAPTURE]))
        if name == "hook":
            command.add_argument("--event", default="")
        if name == "send":
            command.add_argument("--session", default="")
            command.add_argument("--id", dest="message_id", default="")
            command.add_argument("--text", default="")
            command.add_argument("--text-file", default="")
        if name == "consent":
            command.add_argument("answer", choices=("yes", "no"))
            command.add_argument(
                "--by-agent",
                action="store_true",
                help="本人授權的 AI 工具代開收錄（不需終端機；寄通知信給本人）；開之前先跟本人說一聲",
            )
        if name == "setup":
            command.add_argument("--site", default="")
    args = parser.parse_args(argv)
    platform: str = args.platform
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    if platform in NO_CAPTURE and args.command in ("consent", "send", "flush"):
        return say("no_capture")  # nothing of his is taken here, so there is nothing to agree to or send
    if args.command == "consent":
        return answer_consent(platform, args.answer, args.by_agent)
    if args.command == "setup":
        data = data_dir(platform)
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        (data / "config.json").write_text(json.dumps({"site": args.site}), encoding="utf-8")
        print("已記下網站；收錄預設關，要開請執行 consent yes。")
        return 0
    if args.command == "status" and platform in NO_CAPTURE:
        print("收錄：此平台不收字（只接開場）")
        return 0
    if args.command == "status":
        print(
            "收錄：" + ("開" if consent(platform) else "關") + f"；待送 {sum(len(r) for _, r in waiting(platform))} 筆"
        )
        return 0
    if args.command == "hook":
        given: list[dict[str, object]] = []
        event, result, error = run_hook(
            platform, sys.stdin.buffer.read().decode("utf-8", errors="replace"), given, args.event
        )
        log(platform, event, result, error)
        # the tool's hook stdout must be JSON; the default vetoes nothing and asks for nothing
        print(json.dumps(given[0] if given else QUIET_OUTPUT.get(platform, {}).get(event, {}), ensure_ascii=False))
        return 0
    if args.command == "flush":
        try:
            result, error = flush(platform, time.monotonic() + hook.BUDGET_S)
        except hook.MissError as miss:
            return say("pending", str(miss))
        return say("ok" if result == "ok" else result, error)
    text = args.text
    if args.text_file:
        try:
            text = Path(args.text_file).read_text(encoding="utf-8")
        except OSError:
            return say("unreadable")
    if not args.session.strip() or not args.message_id.strip():
        return say("id_required")
    if not consent(platform):
        return say("consent_off")
    if not confirmed(platform, text):
        return say("needs_terminal")
    status, error = send(platform, args.session, args.message_id, text, os.getcwd())
    return say(status, error)


if __name__ == "__main__":
    raise SystemExit(main())
