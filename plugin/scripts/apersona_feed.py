"""收錄 for the platforms that have no event proving a human typed a turn: Gemini CLI (`gemini`), Grok (`grok`) and any
AI tool whose only hook is the one before a tool runs (`pretool`). Claude and Codex keep their own paths in
`apersona_hook.py`; nothing here touches them.

    python apersona_feed.py hook --platform gemini|pretool        (the platform's hook JSON on stdin; prints `{}`)
    python apersona_feed.py send --platform P --session S --id ID (--text T | --text-file F)
    python apersona_feed.py flush|status --platform P
    python apersona_feed.py consent --platform P yes|no
    python apersona_feed.py setup --platform P [--site URL]

What each platform may send is the contract of `docs/development/plugin_platforms.md`:

- The text is only what he sends himself (`send`, at his terminal, after he reads it and types y), with an id he gives
  for the turn. No id is `id_required`, nothing kept, nothing sent: an id is never made from the text, the time or the
  session. Gemini's BeforeAgent has no stable turn id, so that hook is also `id_required` and takes nothing.
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
}
CONSENT_SHA256 = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in CONSENT_TEXTS.items()}
#: hook event -> what it may do. `flush` wakes the sending of what is already consented; `id_required` is a human
#: prompt with no stable id (nothing is taken); an event not listed is not ours. Grok has no hook at all.
EVENT_ROLES = {
    "gemini": {"BeforeAgent": "id_required", "AfterAgent": "flush"},
    "pretool": {"PreToolUse": "flush"},
    "grok": {},
}


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


def write_consent(platform: str, on: bool) -> None:
    "Record his answer; an off also drops everything waiting, and an on starts a new `since`."
    data = data_dir(platform)
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {"on": on, "sha256": CONSENT_SHA256[platform], "since": datetime.now(timezone.utc).isoformat()}  # noqa: UP017
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


def answer_consent(platform: str, answer: str) -> int:
    "`consent yes|no`; a yes only from a person at a terminal who types the phrase (an agent or a pipe cannot)."
    if answer == "no":
        write_consent(platform, False)
        print("收錄已關閉。")
        return 0
    if not sys.stdin.isatty():
        print("收錄要本人在自己的終端機打開（不能由 AI 代為執行）。")
        return 1
    print("同意書：" + CONSENT_TEXTS[platform])
    try:
        typed = input(f"要開，請輸入「{hook.CONSENT_PHRASE}」；其他任何輸入都是不開：").strip()
    except EOFError:
        typed = ""
    except OSError:
        typed = ""
    write_consent(platform, typed == hook.CONSENT_PHRASE)
    print("收錄已開啟；隨時可用 consent no 撤回。" if typed == hook.CONSENT_PHRASE else "沒有開啟，收錄維持關閉。")
    return 0 if typed == hook.CONSENT_PHRASE else 1


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
        lines = path.read_text(encoding="utf-8").splitlines()
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


def run_hook(platform: str, raw: str) -> tuple[str, str, str]:
    "(event, result, error) of one hook event. The event's prompt, response and tool fields are never looked at."
    fields = hook.hook_fields(raw)
    event = fields.get("hook_event_name")
    name = event if isinstance(event, str) else "unknown"
    role = EVENT_ROLES[platform].get(name)
    if platform == "grok":
        return name, "skipped", "no_hook_contract"
    if role is None:
        return name, "skipped", "unsupported_event"
    if role == "id_required":
        return name, "skipped", "id_required"
    if fields.get("stop_hook_active") is True:
        return name, "skipped", "retry_ignored"
    if not allows(fields.get("cwd")):
        return name, "skipped", "project_disabled"
    try:
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
    if not sys.stdin.isatty():
        return False
    print(f"要送到 Apersona（{platform}）的字：\n{redact_secrets(text.strip())}")
    try:
        return input("確定送出請輸入 y：").strip().lower() == "y"
    except EOFError:
        return False
    except OSError:
        return False


def say(status: str, error: str = "") -> int:
    body = {"status": status, **({"error": error} if error else {})}
    print(json.dumps(body, ensure_ascii=False))
    return 0 if status in ("sent", "pending", "ok") else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apersona_feed.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("hook", "send", "flush", "status", "consent", "setup"):
        command = commands.add_parser(name)
        command.add_argument("--platform", required=True, choices=sorted(CONSENT_TEXTS))
        if name == "send":
            command.add_argument("--session", default="")
            command.add_argument("--id", dest="message_id", default="")
            command.add_argument("--text", default="")
            command.add_argument("--text-file", default="")
        if name == "consent":
            command.add_argument("answer", choices=("yes", "no"))
        if name == "setup":
            command.add_argument("--site", default="")
    args = parser.parse_args(argv)
    platform: str = args.platform
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    if args.command == "consent":
        return answer_consent(platform, args.answer)
    if args.command == "setup":
        data = data_dir(platform)
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        (data / "config.json").write_text(json.dumps({"site": args.site}), encoding="utf-8")
        print("已記下網站；收錄預設關，要開請執行 consent yes。")
        return 0
    if args.command == "status":
        print(
            "收錄：" + ("開" if consent(platform) else "關") + f"；待送 {sum(len(r) for _, r in waiting(platform))} 筆"
        )
        return 0
    if args.command == "hook":
        event, result, error = run_hook(platform, sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        log(platform, event, result, error)
        print("{}")  # Gemini's hook stdout must be JSON; an empty object vetoes nothing and asks for nothing
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
