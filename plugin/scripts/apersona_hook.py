"""開場包 at SessionStart (`--kind opening`): ask the site and print what it gives for the agent.

Standard library only, on whatever python3 the machine has (3.9 and up): the plugin carries no packages. The site is
reached with the device login (`/apersona:login`, `apersona_login.py`) and nothing else; the site comes from the
plugin's `site` option or is ours. Of the hook's 3 seconds, counted from when the wrapper started, the request gets
what is left of 2.2 and runs in a daemon thread that is abandoned at the deadline (urllib's timeout bounds each socket
step, not the whole request). Anything short of a good answer prints nothing; a too-long one is cut and says so, never
dropped. The log gets the end line (the wrapper wrote the start line): result, error kind and milliseconds, never
content. Claude Code has no per-prompt hook: the model calls recall itself, and the PostToolUse `receipt` hook tells
the site the reply arrived. Stop captures only explicitly consented new user messages, redacted before transmission,
and records successful message ids for retries.

`--client codex` is the same three hooks for the Codex CLI (its `turn` still asks for a hint), reading the site
from `~/.apersona/codex/config.json` (same device login) and keeping its own log and clock. Codex's rollout is not a
stable format to read, so each typed prompt is spooled at UserPromptSubmit (only after consent) and sent at Stop, and
only when the rollout's first line says a person typed it (`cli` or `vscode`, never `codex exec` or a subagent).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apersona_mask import redact_secrets  # noqa: E402  (the site's own masking, word for word)
from apersona_projects import allows, project_path, set_project  # noqa: E402
from apersona_safe import EXIT_FAILED, clean, exit_line, fenced, open_url, terminal_input  # noqa: E402

#: the hook's own share of the 3 s; `APERSONA_HOOK_BUDGET_S` widens it only where a test runs on a busy machine
BUDGET_S = float(os.environ.get("APERSONA_HOOK_BUDGET_S") or 2.2)
#: the most of the site's words one hook puts in front of the model: the opening (as the site's own 8,000) and a turn
MAX_TEXT = 8_000
MAX_TURN_TEXT = 300
CUT_NOTICE = "（資料未完整交付；以下只有前段，未登記為已收到。）"
OPENING_CUT_NOTICE = "（開場資料未完整交付；本人更正或規矩可能缺漏，可用 opening 取回伺服器開場包。）"
#: what the model is told when the opening did not arrive in time: one line, so it fetches the pack itself
OPENING_LATE_NOTICE = "（開場包這次在時限內沒拿到；請先自己呼叫 apersona 的 opening 工具取回，再回答本人。）"
#: a user row of the transcript that is not his typing: what `!` commands, slash commands and the app itself put there
NOT_TYPED = (
    "<bash-",
    "<local-command",
    "<command-",
    "<task-notification",
    "<side-sessions-event",
    "<cross-session-message",
    "<ci-monitor-event",
    "[Request interrupted",
)
#: the app's own notes around his text, in the same row
REMINDERS = re.compile(r"\s*<system-reminder>.*?</system-reminder>", re.DOTALL)
#: word for word the site's DECISION_CONTEXT_CONSENT (the hook imports nothing of apersona); the site checks the hash
CONSENT_TEXT = (
    "同意收錄本人在 Claude Code 新打的字，以及每次查判讀（lookup）前後那一句與 AI 的回覆當決策前後文"
    "（AI 回覆只給決策中心看、算採用率，不讀進人格）；秘密先遮、可撤回、素材每日合併讀。"
)
CONSENT_SHA256 = hashlib.sha256(CONSENT_TEXT.encode()).hexdigest()
#: the earlier text: a consent given on it keeps sending his typed text, never the AI's replies
LEGACY_CONSENT_TEXT = "同意收錄本人在 Claude Code 新打的字；秘密先遮、可撤回、素材每日合併讀。"
LEGACY_CONSENT_SHA256 = hashlib.sha256(LEGACY_CONSENT_TEXT.encode()).hexdigest()
CODEX_CONSENT_TEXT = "同意收錄本人在 Codex 親自新打的字（codex exec 與子代理不收）；秘密先遮、可撤回、素材每日合併讀。"
CODEX_CONSENT_SHA256 = hashlib.sha256(CODEX_CONSENT_TEXT.encode()).hexdigest()
CODEX_INTERACTIVE = frozenset({"cli", "vscode"})
#: what he types himself, at a terminal, to turn 收錄 on; nothing else does
CONSENT_PHRASE = "我同意"
#: how long telling the site about a consent may take (a login trade and one request); the hook's own turns are not held
CONSENT_REPORT_S = 8.0
SESSION_META_CAP = 1024 * 1024
MAX_CAPTURED_TEXT = 100_000
# The site will not change its mind about these, so a spooled prompt it refused is dropped, not retried.
# 401/404 stay retryable: a revoked key or a mistyped site is fixed by the person, and the Claude path retries too.
PERMANENT_REFUSALS = frozenset({"http_400", "http_403", "http_409", "http_413"})


class MissError(Exception):
    "Why nothing was printed, as one short code for the log."


def data_dir(client: str = "claude") -> Path | None:
    "Where this client keeps its log, turns, consent and acks; None when there is no such place."
    if client == "codex":
        try:
            return Path.home() / ".apersona" / "codex"
        except RuntimeError:
            return None
    data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(data) / "apersona" if data else None


def consent_path(client: str = "claude") -> Path:
    data = data_dir(client)
    if data is None:
        raise MissError("not_configured")
    return data / "capture-consent.json"


def consent(client: str = "claude") -> dict[str, object]:
    try:
        value = json.loads(consent_path(client).read_text(encoding="utf-8"))
    except OSError:
        return {}
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    record = cast("dict[str, object]", value)
    wanted = (CODEX_CONSENT_SHA256,) if client == "codex" else (CONSENT_SHA256, LEGACY_CONSENT_SHA256)
    return record if record.get("on") is True and record.get("sha256") in wanted else {}


def has_context_consent(approval: dict[str, object]) -> bool:
    "Whether the consent on record is the current text, the only one that lets the AI's replies be sent."
    return approval.get("sha256") == CONSENT_SHA256


def consent_record(client: str, on: bool, by_agent: bool = False) -> dict[str, object]:
    """What the consent file holds: the answer, the text's hash, when, and (for a yes) who opened it: `owner` he typed
    the phrase at his terminal, `agent` an AI tool did with his authorization (`--by-agent`), and through which tool."""
    sha = CODEX_CONSENT_SHA256 if client == "codex" else CONSENT_SHA256
    record: dict[str, object] = {"on": on, "sha256": sha, "since": datetime.now(timezone.utc).isoformat()}  # noqa: UP017
    if on:
        record["by"] = "agent" if by_agent else "owner"
        record["tool"] = client
    return record


def write_consent(client: str, on: bool, by_agent: bool = False) -> dict[str, object]:
    "Record his answer (see `consent_record`); a no also drops what Codex spooled. Returns what was written."
    path = consent_path(client)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = consent_record(client, on, by_agent)
    path.write_text(json.dumps(record), encoding="utf-8")
    data = data_dir(client)
    if not on and client == "codex" and data is not None:
        clear_spool(data)
    return record


def report_consent(client: str, record: dict[str, object]) -> str:
    """Tell the site 收錄 went on or off on this device, so the 裝置頁 shows who opened it (and mails him when an AI
    tool did) and his 撤回 there can stop the uploads. "" when it is recorded, else the code of why not
    (`owner_revoked`: he took it back on the website, only he opens it again; `not_configured`: not logged in)."""
    try:
        data = data_dir(client)
        if client == "codex":
            if data is None:
                raise MissError("not_configured")
            hint = codex_config(data)
        else:
            hint = os.environ.get("CLAUDE_PLUGIN_OPTION_SITE", "")
    except MissError as refused:
        return str(refused)
    return tell_site(hint, record, client)


def report_refused(refused: str) -> int:
    "Say why 收錄 stayed off when an AI tool tried to open it (`report_consent`'s code); the exit code."
    why = (
        "本人在網站撤回過這台裝置的收錄，要他本人在終端機親手開"
        if refused == "owner_revoked"
        else "伺服器沒有記下這次代開（" + refused + "），所以沒有開；登入好之後再試"
    )
    print("沒有開啟，收錄維持關閉：" + why)
    print(exit_line(EXIT_FAILED))
    return EXIT_FAILED


def tell_site(hint: str, record: dict[str, object], tool: str) -> str:
    """Post one consent record (`on`, `by`) to the site as `tool`'s, with the device login (`hint` the site it names,
    "" for the login's own). "" when the site recorded it, else the code of why not; `apersona_feed.py` uses it too."""
    try:
        site, key = login_bearer(hint, CONSENT_REPORT_S)
        payload = {"on": record.get("on") is True, "byAgent": record.get("by") == "agent", "tool": tool}
        request = urllib.request.Request(
            site.rstrip("/") + "/api/memory/capture-consent",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST",
        )
        fetch_json(request, CONSENT_REPORT_S)
    except MissError as refused:
        return str(refused)
    return ""


def turn_off_locally(client: str) -> None:
    "The site refused an upload because he took the 收錄 back there: the consent file goes off, so no next turn sends."
    write_consent(client, False)


def answer_consent(client: str, answer: str, by_agent: bool = False) -> int:
    """`--consent yes|no`. No is recorded at once (and told to the site, best effort). Yes comes in two ways. He at a
    terminal (stdin a TTY) reads the consent text and types CONSENT_PHRASE himself: recorded as `owner`. Or, with
    `--by-agent`, an AI tool he has authorized opens it with no terminal: the consent text is printed in full the same,
    the site must record it (and mail him) first, and the record says `agent`; a site that cannot be reached, or that
    he took the 收錄 back on, means it stays off. Anything else (a pipe, no flag) cannot turn 收錄 on. 0 when what was
    asked for is recorded."""
    if answer == "no":
        report_consent(client, write_consent(client, False))
        print("收錄已關閉。")
        return 0
    text = CODEX_CONSENT_TEXT if client == "codex" else CONSENT_TEXT
    if by_agent:
        print("同意書：" + text)
        print("以上由 AI 工具代本人開啟（本人已授權）；開啟後會寄通知信給本人，網站「裝置」頁可一鍵撤回。")
        record = consent_record(client, True, True)
        refused = report_consent(client, record)
        if refused:
            return report_refused(refused)
        write_consent(client, True, True)
        print("收錄已開啟（由 AI 工具代開）；隨時可用 --consent no 撤回，本人也能在網站裝置頁撤回。")
        return 0
    if not terminal_input():
        print("收錄要本人在自己的終端機打開；或由本人明說授權的 AI 工具加 --by-agent 代開。本人請在終端機執行：")
        here = f'python3 "{Path(__file__).resolve()}" --consent yes' + (" --client codex" if client == "codex" else "")
        data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        print(f'  CLAUDE_PLUGIN_DATA="{data}" {here}' if client == "claude" and data else "  " + here)
        print(exit_line(EXIT_FAILED))
        return EXIT_FAILED
    print("同意書：" + text)
    try:
        typed = input(f"要開，請輸入「{CONSENT_PHRASE}」；其他任何輸入都是不開：").strip()
    except EOFError:
        typed = ""
    except OSError:
        typed = ""
    on = typed == CONSENT_PHRASE
    record = write_consent(client, on)
    if on:
        report_consent(client, record)  # best effort: his own yes needs no site to be real
        print("收錄已開啟；隨時可用 --consent no 撤回。")
        return 0
    print("沒有開啟，收錄維持關閉。")
    print(exit_line(EXIT_FAILED))
    return EXIT_FAILED


def capture_stop(site: str, key: str, hook_input: str, deadline: float) -> None:
    "Stop's capture; if the site says he took the 收錄 back on the website, the local consent goes off at once."
    try:
        send_captured(site, key, hook_input, deadline)
    except MissError as refused:
        if str(refused) != "consent_revoked":
            raise
        turn_off_locally("claude")


def send_captured(site: str, key: str, hook_input: str, deadline: float) -> None:
    "Only user text after explicit consent; retries reuse the transcript message uuid."
    approval = consent()
    if not approval:
        return
    body = json.loads(hook_input)
    if not isinstance(body, dict):
        return
    fields = cast("dict[str, object]", body)
    session, transcript = fields.get("session_id"), fields.get("transcript_path")
    if not isinstance(session, str) or not isinstance(transcript, str):
        return
    since = datetime.fromisoformat(str(approval.get("since", "")).replace("Z", "+00:00"))
    if since.tzinfo is None:
        return
    ack = consent_path().parent / "captured" / (hashlib.sha256((site + session).encode()).hexdigest() + ".json")
    try:
        sent: set[str] = set(cast("list[str]", json.loads(ack.read_text(encoding="utf-8"))))
    except OSError:
        sent = set()
    except ValueError:
        sent = set()
    with Path(transcript).open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 8_000_000))
        lines = stream.read().decode("utf-8", errors="replace").split("\n")
    rows = transcript_rows(lines)
    marked = marks_origin(rows)
    for row in rows:
        if time.monotonic() >= deadline or consent() != approval:
            break
        content = typed_text(row, marked)
        if not content or not row.get("uuid"):
            continue
        if not isinstance(row.get("timestamp"), str) or row.get("uuid") in sent:
            continue
        try:
            written = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if written.tzinfo is None or written < since or not allows(fields.get("cwd"), str(row["timestamp"])):
            continue
        content = redact_secrets(content)
        identifier = str(row["uuid"])
        payload = {
            "turnId": session + ":" + identifier,
            "messageId": identifier,
            "text": content,
            "consentSha256": approval.get("sha256", CONSENT_SHA256),  # the text he agreed to, new or earlier
        }
        request = urllib.request.Request(
            site.rstrip("/") + "/api/memory/conversation",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST",
        )
        fetch_text(request, max(0, deadline - time.monotonic()))
        sent.add(identifier)
        save_ack(ack, sent)
    # Claude Code only; Codex's rollout carries no tool calls the hook can read, so it sends no 決策前後文.
    if has_context_consent(approval):
        capture_decision_context(site, key, fields, lines, approval, since, sent, ack, deadline)


#: 做完自己接下去: the tail of the AI's last words the site is asked about (its last paragraph is what is judged), the
#: start of what he typed next, how many conversations' runs are kept, and what tells him 代答 went off by itself
STAND_IN_SAID_CHARS = 2_000
STAND_IN_NEXT_CHARS = 400
STAND_IN_SESSIONS = 20
STAND_IN_OFF_NOTICE = "Apersona：代答被你推翻的比例過高，已自動關掉代答（開場的規矩照舊）。/apersona:on 重新打開。"


def list_left(rows: list[dict[str, object]]) -> tuple[int, str]:
    """How many items of the AI's task list are not done, and the first of them, replayed from the list tools it called
    in this conversation: `TodoWrite` (the whole list each time) and the desktop app's `update_plan` (the whole list,
    or one step forward) with `wrap_up` (closes the step in progress). No list is (0, "")."""
    items: list[dict[str, object]] = []

    def close_current() -> None:
        for item in items:
            if item.get("status") == "in_progress":
                item["status"] = "completed"

    for row in rows:
        if row.get("type") != "assistant" or row.get("isSidechain"):
            continue
        for block in content_blocks(row):
            given = block.get("input")
            if block.get("type") != "tool_use" or not isinstance(given, dict):
                continue
            name, call = str(block.get("name", "")), cast("dict[str, object]", given)
            whole = call.get("todos") if name == "TodoWrite" else call.get("items")
            if name != "TodoWrite" and not name.endswith(("update_plan", "wrap_up")):
                continue
            if name.endswith("wrap_up"):
                close_current()
                continue
            if isinstance(whole, list):
                items = [dict(cast("dict[str, object]", i)) for i in cast("list[object]", whole) if isinstance(i, dict)]
            if call.get("advance") is True:
                close_current()
                for item in items:
                    if item.get("status") == "pending":
                        item["status"] = "in_progress"
                        break
            done, start = call.get("done"), call.get("start")
            numbers = [n for n in cast("list[object]", done) if isinstance(n, int)] if isinstance(done, list) else []
            for number in numbers:
                if 1 <= number <= len(items):
                    items[number - 1]["status"] = "completed"
            if isinstance(start, int) and 1 <= start <= len(items):
                items[start - 1]["status"] = "in_progress"
    left = [item for item in items if item.get("status") != "completed"]
    return len(left), str(left[0].get("content", "")) if left else ""


def stand_in(site: str, key: str, fields: dict[str, object], left_s: float) -> str:
    """做完自己接下去: what the Stop hook prints so the AI goes on with the next item, or "" to let it stop
    and wait for him. The site decides (its one table of major matters; no model); this only sends the end of the AI's
    last words, how much of its list is left, what he typed after the last answer, and the counts kept in
    `stand_in.json` (answers in a row for this conversation, answers given, answers he overturned). Off, no list
    left, no answer in time or anything unreadable is "": the AI waits for him as always."""
    data = data_dir()
    said, session, transcript = (fields.get(k) for k in ("last_assistant_message", "session_id", "transcript_path"))
    if data is None or not isinstance(said, str) or not isinstance(session, str) or not isinstance(transcript, str):
        return ""
    path = data / "stand_in.json"
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        kept = {}
    except ValueError:
        kept = {}
    state = cast("dict[str, object]", kept) if isinstance(kept, dict) else {}
    if state.get("off") is True or not said.strip():
        return ""
    with Path(transcript).open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 8_000_000))
        rows = transcript_rows(stream.read().decode("utf-8", errors="replace").split("\n"))
    left, item = list_left(rows)
    if not left:
        return ""
    marked = marks_origin(rows)
    typed = [(str(row.get("uuid", "")), text) for row in rows for text in [typed_text(row, marked)] if text]
    held = state.get("sessions")
    sessions = cast("dict[str, dict[str, object]]", held) if isinstance(held, dict) else {}
    mine = sessions.get(session, {})
    seen = [uuid for uuid, _ in typed]
    since = typed[seen.index(str(mine["last"])) + 1 :] if mine.get("last") in seen else []
    run = 0 if since else int(cast("int", mine.get("run", 0)))
    stood_in, overturned = (int(cast("int", state.get(k, 0))) for k in ("stood_in", "overturned"))
    ask: dict[str, object] = {
        "said": redact_secrets(said[-STAND_IN_SAID_CHARS:]),
        "left": left,
        "next": redact_secrets(item),
        "run": run,
        "stoodIn": stood_in,
        "overturned": overturned,
    }
    if since and mine.get("pending") is True:  # the first thing he typed after the last answer: was it taken back
        ask["ownerNext"] = redact_secrets(since[0][1][:STAND_IN_NEXT_CHARS])
    request = urllib.request.Request(
        site.rstrip("/") + "/api/memory/stand-in",
        data=json.dumps(ask).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    body = fetch_json(request, left_s)
    reply = cast("dict[str, object]", body) if isinstance(body, dict) else {}
    verdict, words = reply.get("verdict"), reply.get("text")
    answered = verdict == "continue" and isinstance(words, str) and words.startswith("〈人格代答")
    waits = answered or (mine.get("pending") is True and "ownerNext" not in ask)  # an answer he has not typed after
    mine = cast(
        "dict[str, object]", {"run": run + 1 if answered else run, "last": seen[-1] if seen else "", "pending": waits}
    )
    sessions.pop(session, None)
    sessions[session] = mine
    state["sessions"] = dict(list(sessions.items())[-STAND_IN_SESSIONS:])
    state["stood_in"] = stood_in + (1 if answered else 0)
    state["overturned"] = overturned + (1 if reply.get("overturned") is True else 0)
    state["off"] = verdict == "overturned_often"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    if answered:
        return json.dumps({"decision": "block", "reason": words}, ensure_ascii=False)
    return json.dumps({"systemMessage": STAND_IN_OFF_NOTICE}, ensure_ascii=False) if state["off"] else ""


def save_ack(ack: Path, sent: set[str]) -> None:
    "Keep what was sent: user message uuids and `ctx:<turnId>:<role>` keys, in the one list."
    ack.parent.mkdir(parents=True, exist_ok=True)
    ack.write_text(json.dumps(sorted(sent)), encoding="utf-8")


def transcript_rows(lines: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(cast("dict[str, object]", row))
    return rows


def content_blocks(row: dict[str, object]) -> list[dict[str, object]]:
    "The blocks of a row's message content (a plain string is one text block)."
    message = row.get("message")
    content = cast("dict[str, object]", message).get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    return [cast("dict[str, object]", b) for b in cast("list[object]", content) if isinstance(b, dict)]


def block_text(blocks: list[dict[str, object]]) -> str:
    return "\n".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")


def marks_origin(rows: list[dict[str, object]]) -> bool:
    "Whether this transcript's app version says who wrote each user row."
    return any(isinstance(row.get("origin"), dict) for row in rows)


def typed_text(row: dict[str, object], marked: bool) -> str:
    """What he typed in this row; empty when it is not his typing. The one filter of everything that reads his words.

    A compacted conversation's summary, a subagent's prompt, a background task's notice and another session's
    message are user rows too. Where the app marks the origin (`marked`), only a row it calls human is his.
    """
    if row.get("type") != "user" or row.get("isMeta") or row.get("isCompactSummary") or row.get("isSidechain"):
        return ""
    origin = row.get("origin")
    if isinstance(origin, dict) and cast("dict[str, object]", origin).get("kind") != "human":
        return ""
    if marked and not isinstance(origin, dict):
        return ""
    message = row.get("message")
    if not isinstance(message, dict) or cast("dict[str, object]", message).get("role") != "user":
        return ""
    text = REMINDERS.sub("", block_text(content_blocks(row))).strip()
    return "" if text.startswith(NOT_TYPED) else text


def lookup_call_ids(row: dict[str, object]) -> list[str]:
    "The ids of the apersona lookup calls an assistant row makes."
    if row.get("type") != "assistant":
        return []
    found: list[str] = []
    for block in content_blocks(row):
        name, call = block.get("name"), block.get("id")
        if block.get("type") == "tool_use" and isinstance(name, str) and isinstance(call, str) and call:
            if name.endswith("__lookup") and "apersona" in name:
                found.append(call)
    return found


def receipt_turn(rows: list[dict[str, object]], start: int, call: str) -> tuple[int, str]:
    "(index of the row holding the call's result, the 交付編號 in it); (-1, '') when there is none."
    for index in range(start, len(rows)):
        if rows[index].get("type") != "user":
            continue
        for block in content_blocks(rows[index]):
            if block.get("type") != "tool_result" or block.get("tool_use_id") != call:
                continue
            result = block.get("content")
            if isinstance(result, list):
                result = block_text(
                    [cast("dict[str, object]", b) for b in cast("list[object]", result) if isinstance(b, dict)]
                )
            found = re.findall(r"交付編號：([\w.:-]+)", result) if isinstance(result, str) else []
            return index, found[-1].strip() if found else ""
    return -1, ""


def capture_decision_context(
    site: str,
    key: str,
    fields: dict[str, object],
    lines: list[str],
    approval: dict[str, object],
    since: datetime,
    sent: set[str],
    ack: Path,
    deadline: float,
) -> None:
    """Around each lookup of the conversation send the sentence that set it off, the AI's reply with the packet and
    his next sentence, once each. A refusal of the site is remembered when it is final and raised at the end."""
    rows = transcript_rows(lines)
    marked = marks_origin(rows)

    def counts(row: dict[str, object]) -> bool:
        stamp = row.get("timestamp")
        if not isinstance(stamp, str):
            return False
        try:
            written = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return False
        return written.tzinfo is not None and written >= since and allows(fields.get("cwd"), stamp)

    def typed_before(index: int) -> str:
        for back in range(index - 1, -1, -1):
            text = typed_text(rows[back], marked)
            if text:
                return text if counts(rows[back]) else ""
        return ""

    def typed_after(index: int) -> str:
        for ahead in range(index + 1, len(rows)):
            text = typed_text(rows[ahead], marked)
            if text:
                return text if counts(rows[ahead]) else ""
        return ""

    first_miss: MissError | None = None
    for index, row in enumerate(rows):
        if not counts(row):
            continue
        for call in lookup_call_ids(row):
            answered, turn = receipt_turn(rows, index + 1, call)
            if not turn:
                continue
            texts = {"prompt": typed_before(index), "assistant": "", "owner_next": ""}
            for later in range(answered + 1, len(rows)):
                reply = block_text(content_blocks(rows[later])) if rows[later].get("type") == "assistant" else ""
                if reply.strip():
                    texts["assistant"] = reply if counts(rows[later]) else ""
                    texts["owner_next"] = typed_after(later)
                    break
            for role, text in texts.items():
                marker = f"ctx:{turn}:{role}"
                if not text or marker in sent:
                    continue
                if time.monotonic() >= deadline or consent() != approval:
                    if first_miss is not None:
                        raise first_miss
                    return
                payload = {
                    "turnId": turn,
                    "role": role,
                    "text": redact_secrets(text)[:MAX_CAPTURED_TEXT],
                    "consentSha256": CONSENT_SHA256,
                }
                request = urllib.request.Request(
                    site.rstrip("/") + "/api/memory/decision-context",
                    data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    fetch_json(request, max(0, deadline - time.monotonic()))
                except MissError as refused:
                    if str(refused) == "consent_revoked":
                        raise
                    first_miss = first_miss or refused
                    if str(refused) not in PERMANENT_REFUSALS:
                        continue
                sent.add(marker)
                save_ack(ack, sent)
    if first_miss is not None:
        raise first_miss


def login_bearer(site: str, left_s: float) -> tuple[str, str]:
    """The site and access token of the device login (`apersona_login.py`), the only way the hook reaches the site;
    no persona: the site takes the one the login stands for. The hook never logs in and never trades: with none it
    is `not_configured`, with an access token at its end `token_expiring` (a trade is started in the background)."""
    try:
        import apersona_login  # beside this file (copied beside it for Codex)
    except ImportError:
        raise MissError("not_configured") from None
    try:
        where = apersona_login.site_of(site)
        return where, apersona_login.bearer(where, max(0.1, left_s), trade=False)
    except apersona_login.LoginError as e:
        raise MissError("not_configured" if e.kind == "not_logged_in" else e.kind) from None


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def opening_request(site: str, key: str) -> urllib.request.Request:
    url = site.rstrip("/") + "/api/memory/opening"
    return urllib.request.Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json"})


def turn_request(site: str, key: str, session: str, prompt: str, received: list[str] | None) -> urllib.request.Request:
    # what he typed leaves the machine with its secrets dropped first, as everything the plugin sends
    fields: dict[str, object] = {"sessionId": session, "prompt": redact_secrets(prompt)}  # the login's persona
    if received is not None:
        fields["received"] = received
    body = json.dumps(fields).encode("utf-8")
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json", "Content-Type": "application/json"}
    return urllib.request.Request(site.rstrip("/") + "/api/memory/turn", data=body, headers=headers, method="POST")


RECALL_TOOLS = ("mcp__apersona__recall", "mcp__plugin_apersona_apersona__recall")


def recall_receipt(content: object) -> tuple[str, str] | None:
    "A complete reply footer, with the exact server-issued conversation and receipt."
    if isinstance(content, list) and len(cast("list[object]", content)) == 1:
        part = cast("list[object]", content)[0]
        block = cast("dict[str, object]", part) if isinstance(part, dict) else {}
        content = block.get("text") if block.get("type") == "text" else None
    if not isinstance(content, str):
        return None
    found = re.search(
        r"(?:^|\n)〔本段對話識別：([^\r\n。]{1,128})。這段對話之後每次叫 Apersona 的工具都帶 "
        r'conversation_id="([^"\r\n]+)"[^\r\n]*〕\r?\n(?:[ \t]*\r?\n)*'
        r"收件識別：([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        r"\r?\n(?:[ \t]*\r?\n)*交付編號：[^\n]+\s*\Z",
        content,
    )
    if not found or found[1] != found[2] or len(found[1].encode("utf-8")) > 128:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in found[1]):
        return None
    return found[1], found[3]


def post_tool_receipt(fields: dict[str, object]) -> tuple[str, str] | None:
    "Only the host's successful recall result; unknown tool response shapes fail closed."
    if fields.get("hook_event_name") != "PostToolUse" or fields.get("tool_name") not in RECALL_TOOLS:
        return None
    if not all(isinstance(fields.get(k), str) and fields.get(k) for k in ("session_id", "tool_use_id")):
        return None
    response = fields.get("tool_response")
    if not isinstance(response, dict):
        return None
    result = cast("dict[str, object]", response)
    if any(result.get(k) or fields.get(k) for k in ("isError", "is_error", "truncated", "is_truncated")):
        return None
    return recall_receipt(result.get("content"))


#: the 403 codes the site gives for a 收錄 he took back; they are not a plain `http_403`, which is dropped for good
CONSENT_REFUSALS = ("consent_revoked", "owner_revoked")


def refusal_code(error: urllib.error.HTTPError) -> str:
    "`http_<status>`, except a 403 whose body names one of CONSENT_REFUSALS: that code."
    if error.code == 403:
        try:
            body = json.loads(error.read().decode("utf-8"))
        except OSError:  # two clauses: the formatter would drop the parentheses python 3.9 needs
            return "http_403"
        except ValueError:
            return "http_403"
        named = cast("dict[str, object]", body).get("error") if isinstance(body, dict) else None
        if named in CONSENT_REFUSALS:
            return str(named)
    return f"http_{error.code}"


def fetch_json(request: urllib.request.Request, deadline_s: float) -> object:
    "The answer's JSON body; a refusal, a timeout or a body that is not JSON is a miss."
    if deadline_s <= 0:
        raise MissError("deadline")
    outcome: list[object] = []

    def call() -> None:
        try:
            with open_url(request, deadline_s) as response:
                outcome.append(json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            outcome.append(MissError(refusal_code(e)))
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            # on 3.9 a socket timeout is socket.timeout, which only became TimeoutError in 3.10
            timed_out = isinstance(reason, (TimeoutError, socket.timeout))
            outcome.append(MissError("deadline" if timed_out else type(reason).__name__))
        except ValueError:
            outcome.append(MissError("bad_response"))

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(deadline_s)
    if not outcome:
        raise MissError("deadline")
    body = outcome[0]
    if isinstance(body, MissError):
        raise body
    return body


def fetch_text(request: urllib.request.Request, deadline_s: float) -> tuple[str, list[str]]:
    "The answer's `text`, possibly empty, and its `ids` (none when it has none); anything else is a miss."
    body = fetch_json(request, deadline_s)
    fields = cast("dict[str, object]", body) if isinstance(body, dict) else {}
    text, ids = fields.get("text"), fields.get("ids")
    if not isinstance(text, str):
        raise MissError("bad_response")
    return text, [i for i in cast("list[object]", ids) if isinstance(i, str)] if isinstance(ids, list) else []


def append_log(row: dict[str, object], client: str = "claude") -> bool:
    data = data_dir(client)
    if data is None:
        return False
    try:
        data.mkdir(parents=True, exist_ok=True)
        with (data / "events.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True


def miss(event_id: str, kind: str, error: MissError, started_ms: int, client: str) -> None:
    "Log a miss; an opening that ran out of time also leaves the one line that sends the model to fetch it."
    if kind == "opening" and str(error) == "deadline":
        sys.stdout.buffer.write((OPENING_LATE_NOTICE + "\n").encode("utf-8"))
        sys.stdout.flush()
    log_end(event_id, kind, "miss", str(error), started_ms, client)


def log_end(
    event_id: str,
    kind: str,
    result: str,
    error: str,
    started_ms: int,
    client: str = "claude",
    sizes: dict[str, int] | None = None,
) -> None:
    if not event_id:
        return
    row: dict[str, object] = {
        "event_id": event_id,
        "kind": kind,
        "phase": "end",
        "result": result,
        "ms": now_ms() - started_ms,
    }
    if error:
        row["error"] = error
    if sizes is not None:
        row.update(sizes)
    append_log(row, client)


def deliver(text: str, kind: str, event_id: str, started_ms: int, client: str, error: str = "") -> None:
    """Print what the site gave as data: control characters out, cut to MAX_TEXT (a turn to MAX_TURN_TEXT), between
    the plugin's own two lines, and log how it went."""
    server_chars = len(text)
    text = clean(text).strip()
    cleaned_chars = len(text)
    if not text:
        log_end(event_id, kind, "empty", error, started_ms, client)
        return
    # UTF-8 bytes, not the console's code page: on a Chinese-locale Windows the default encoding cannot write it all.
    limit = MAX_TURN_TEXT if kind == "turn" else MAX_TEXT
    cut = len(text) > limit
    if cut:
        notice = CUT_NOTICE if kind == "turn" else OPENING_CUT_NOTICE
        text = notice + "\n" + text[:limit]
    output = (fenced(text) + "\n").encode("utf-8")
    sys.stdout.buffer.write(output)
    sys.stdout.flush()
    log_end(
        event_id,
        kind,
        "truncated" if cut else "ok",
        error,
        started_ms,
        client,
        {
            "server_chars": server_chars,
            "cleaned_chars": cleaned_chars,
            "selected_chars": min(cleaned_chars, limit),
            "stdout_utf8_bytes": len(output),
        },
    )


def conversation_line(raw: str) -> str:
    "The plugin's own line telling the model this conversation's identifier ('' without a usable session_id)."
    session = hook_fields(raw).get("session_id")
    if not isinstance(session, str):
        return ""
    session = "".join(clean(session).split())
    if not session:
        return ""
    return f'本段對話識別：{session}。叫 apersona 的 lookup、recall、opening 時帶 conversation_id="{session}"。\n'


def print_conversation_line(raw: str) -> None:
    line = conversation_line(raw)
    if line:
        sys.stdout.buffer.write(line.encode("utf-8"))
        sys.stdout.flush()


def hook_fields(raw: str) -> dict[str, object]:
    "The hook's JSON on stdin as a dict ({} when it is not one)."
    try:
        given = json.loads(raw)
    except ValueError:
        return {}
    return cast("dict[str, object]", given) if isinstance(given, dict) else {}


def session_source(fields: dict[str, object]) -> str | None:
    """Where Codex says the session came from, read from the first line of its rollout file and nothing else of it.

    None when that cannot be known: no path, no file, no complete first line, or a first line that is not session info.
    """
    raw = fields.get("transcript_path")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        path = Path(raw)
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            head = handle.read(SESSION_META_CAP)
        end = head.find(b"\n")
        if end < 0:
            return None
        first = json.loads(head[:end].decode("utf-8"))
    except OSError:  # separate clauses: the formatter would drop the parentheses python 3.9 needs
        return None
    except ValueError:
        return None
    meta = cast("dict[str, object]", first) if isinstance(first, dict) else {}
    payload = meta.get("payload") if meta.get("type") == "session_meta" else None
    if not isinstance(payload, dict):
        return None
    source = cast("dict[str, object]", payload).get("source")
    if isinstance(source, str):
        label = source
    elif isinstance(source, dict) and source:
        label = str(next(iter(cast("dict[str, object]", source))))
    else:
        label = "unknown"
    return label if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", label) else "unknown"


def switched_off(data: Path) -> bool:
    try:
        text = (data / "apersona.json").read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r'"on"\s*:\s*false', text) is not None


def codex_config(data: Path) -> str:
    "The site from the installer's config.json (the login is the device login's); unreadable is not_configured."
    try:
        site = cast("dict[str, object]", json.loads((data / "config.json").read_text(encoding="utf-8")))["site"]
    except OSError:
        raise MissError("not_configured") from None
    except ValueError:
        raise MissError("not_configured") from None
    except KeyError:
        raise MissError("not_configured") from None
    except TypeError:
        raise MissError("not_configured") from None
    if not isinstance(site, str) or not site:
        raise MissError("not_configured")
    return site


@contextmanager
def spool_lock(data: Path, deadline: float | None = None) -> Generator[None, None, None]:  # noqa: UP043 (Python 3.9)
    "Serialize short spool/ack file changes across hook processes; never held during a request."
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(data / ".spool.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        until = time.monotonic() + BUDGET_S if deadline is None else deadline
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= until:
                    raise MissError("spool_busy") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def spool_file(data: Path, session: str) -> Path:
    return data / "spool" / (hashlib.sha256(session.encode("utf-8")).hexdigest()[:32] + ".jsonl")


def spool_turn(data: Path, session: str, fields: dict[str, object]) -> None:
    "Keep the typed prompt until Stop can check who typed it. The caller has checked the Codex consent."
    turn_id, prompt = fields.get("turn_id"), fields.get("prompt")
    if not isinstance(turn_id, str) or not turn_id or not isinstance(prompt, str):
        return
    text = redact_secrets(prompt.strip())[:MAX_CAPTURED_TEXT]
    if not text.strip():
        return
    row = {
        "turn_id": turn_id,
        "text": text,
        "at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "cwd": str(project_path(fields.get("cwd"))),
    }
    path = spool_file(data, session)
    with spool_lock(data):
        if not consent("codex") or not allows(row["cwd"], row["at"]):
            return
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as out:
            out.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))


def read_spool(path: Path) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return []
    entries: list[dict[str, str]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        fields = cast("dict[str, object]", row) if isinstance(row, dict) else {}
        turn_id, text, at = fields.get("turn_id"), fields.get("text"), fields.get("at")
        if isinstance(turn_id, str) and isinstance(text, str):
            entries.append(
                {
                    "turn_id": turn_id,
                    "text": text,
                    "at": at if isinstance(at, str) else "",
                    "cwd": str(fields.get("cwd") or ""),
                }
            )
    return entries


def write_spool(path: Path, entries: list[dict[str, str]]) -> None:
    "Keep exactly these entries (none: remove the file), replacing the file whole so a crash never leaves half."
    if not entries:
        path.unlink(missing_ok=True)
        return
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries).encode("utf-8"))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def clear_spool(data: Path) -> None:
    "Remove every spooled prompt (consent withdrawn)."
    folder = data / "spool"
    with spool_lock(data):
        if folder.is_dir():
            for leftover in folder.iterdir():
                if leftover.is_file():
                    leftover.unlink(missing_ok=True)


def typed_since(at: str, since: datetime) -> bool:
    "Whether a spooled prompt was typed after the current consent was given; an unreadable time says no."
    try:
        when = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return when.tzinfo is not None and since.tzinfo is not None and when >= since


def codex_stop(data: Path, site: str, key: str, fields: dict[str, object], deadline: float) -> tuple[str, str]:
    "(result, error) for the log. Sends the spooled prompts of a session a person typed; fails closed on the source."
    session = fields.get("session_id")
    if not isinstance(session, str) or not session:
        return "miss", "bad_input"
    spool = spool_file(data, session)
    approval = consent("codex")
    if not approval:
        with spool_lock(data, deadline):
            if not consent("codex"):
                spool.unlink(missing_ok=True)
        return "ok", ""
    source = session_source(fields)
    if source not in CODEX_INTERACTIVE:
        with spool_lock(data, deadline):
            spool.unlink(missing_ok=True)
        return "skipped", "source_" + (source or "unknown")
    try:
        since = datetime.fromisoformat(str(approval.get("since", "")).replace("Z", "+00:00"))
    except ValueError:
        return "miss", "bad_consent"
    entries = [e for e in read_spool(spool) if typed_since(e.get("at", ""), since)]
    ack = data / "captured" / (hashlib.sha256((site + session).encode()).hexdigest() + ".json")
    try:
        sent: set[str] = set(cast("list[str]", json.loads(ack.read_text(encoding="utf-8"))))
    except OSError:
        sent = set()
    except ValueError:
        sent = set()
    remaining = list(entries)
    dropped: list[str] = []
    try:
        for entry in entries:
            identifier = entry["turn_id"]
            if not entry.get("cwd"):
                continue  # Old spool has no project attribution; do not guess its permission.
            if not allows(entry["cwd"], entry["at"]):
                remaining.remove(entry)
                dropped.append("project_disabled")
                continue
            if identifier not in sent:
                if time.monotonic() >= deadline or consent("codex") != approval:
                    break
                payload = {
                    "turnId": session + ":" + identifier,
                    "messageId": identifier,
                    "text": entry["text"],
                    "consentSha256": CODEX_CONSENT_SHA256,
                }
                request = urllib.request.Request(
                    site.rstrip("/") + "/api/memory/conversation",
                    data=json.dumps(payload).encode(),
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    fetch_text(request, max(0, deadline - time.monotonic()))
                except MissError as refused:
                    if str(refused) == "consent_revoked":
                        turn_off_locally("codex")  # also drops the spool
                        return "ok", "consent_revoked"
                    if str(refused) not in PERMANENT_REFUSALS:
                        raise
                    dropped.append("dropped_" + str(refused))
                    remaining.remove(entry)
                    continue
                sent.add(identifier)
                with spool_lock(data, deadline):
                    try:
                        sent.update(cast("list[str]", json.loads(ack.read_text(encoding="utf-8"))))
                    except OSError:
                        pass
                    except ValueError:
                        pass
                    ack.parent.mkdir(parents=True, exist_ok=True)
                    temp = ack.with_name(f".{ack.name}.{uuid.uuid4().hex}.tmp")
                    try:
                        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(fd, "w", encoding="utf-8") as out:
                            out.write(json.dumps(sorted(sent)))
                        os.replace(temp, ack)
                    finally:
                        temp.unlink(missing_ok=True)
            remaining.remove(entry)
    finally:
        with spool_lock(data, deadline):
            current = consent("codex")
            if not current:
                spool.unlink(missing_ok=True)
            elif current == approval:
                completed = [e for e in entries if e not in remaining]
                # Re-read under the append lock: a new prompt is not in this Stop's snapshot.
                pending = [e for e in read_spool(spool) if e not in completed and typed_since(e.get("at", ""), since)]
                write_spool(spool, pending)
    return "ok", ",".join(dropped)


def codex_main(kind: str, consent_answer: str | None, by_agent: bool = False) -> int:
    "The same three hooks for Codex: no wrapper script, so this does the switch, the start line and the clock itself."
    started_ms = now_ms()
    data = data_dir("codex")
    if data is None:
        return 0
    if consent_answer is not None:
        return answer_consent("codex", consent_answer, by_agent)
    hook_input = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    fields = hook_fields(hook_input)
    if switched_off(data) or not allows(fields.get("cwd")):
        return 0
    event_id = f"ev-{started_ms}-{os.getpid()}-{os.urandom(2).hex()}"
    if not append_log({"event_id": event_id, "kind": kind, "phase": "start", "ts_ms": started_ms}, "codex"):
        return 0
    note = ""
    try:
        if fields.get("agent_id") is not None:
            log_end(event_id, kind, "skipped", "skipped_agent", started_ms, "codex")
            return 0
        if kind != "stop":
            source = session_source(fields)
            if source is not None and source not in CODEX_INTERACTIVE:
                log_end(event_id, kind, "skipped", "skipped_" + source, started_ms, "codex")
                return 0
        site, key = login_bearer(codex_config(data), BUDGET_S - (now_ms() - started_ms) / 1000)
        if kind == "stop":
            deadline = time.monotonic() + max(0, BUDGET_S - (now_ms() - started_ms) / 1000)
            try:
                result, error = codex_stop(data, site, key, fields, deadline)
            except OSError:
                result, error = "miss", "capture_failed"
            log_end(event_id, kind, result, error, started_ms, "codex")
            return 0
        if kind == "turn":
            given_session, prompt = fields.get("session_id"), fields.get("prompt")
            if not isinstance(given_session, str) or not isinstance(prompt, str):
                raise MissError("bad_input")
            session = given_session
            if consent("codex"):
                try:
                    spool_turn(data, session, fields)
                except OSError:
                    note = "spool_failed"
            request = turn_request(site, key, session, prompt, [])
        else:
            request = opening_request(site, key)
        text, _ids = fetch_text(request, BUDGET_S - (now_ms() - started_ms) / 1000)
        if not text and kind == "opening":
            raise MissError("bad_response")
    except MissError as e:
        miss(event_id, kind, e, started_ms, "codex")
        if kind == "opening":
            print_conversation_line(hook_input)
        return 0
    deliver(text, kind, event_id, started_ms, "codex", note)
    if kind == "opening":
        print_conversation_line(hook_input)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apersona_hook.py")
    parser.add_argument("--kind", choices=("opening", "turn", "stop", "receipt"), default="opening")
    parser.add_argument("--consent", choices=("yes", "no"), help="本人二選一：同意收錄或不要")
    parser.add_argument(
        "--by-agent",
        action="store_true",
        help="本人授權的 AI 工具代開收錄（不需終端機；記成由 AI 工具代開、寄通知信給本人）；開之前先跟本人說一聲",
    )
    parser.add_argument("--event-id", default="")
    parser.add_argument("--started-ms", type=int, default=0)
    parser.add_argument("--client", choices=("claude", "codex"), default="claude")
    parser.add_argument("--project-state", choices=("on", "off"))
    parser.add_argument("--project-directory", default=".")
    args = parser.parse_args(argv)
    if args.project_state is not None:
        try:
            set_project(args.project_directory, args.project_state == "on")
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            print(exit_line(EXIT_FAILED), file=sys.stderr)
            return EXIT_FAILED
        print("專案 hook 已" + ("開啟；只收錄重新開啟後的內容" if args.project_state == "on" else "停用"))
        return 0
    if args.client == "codex" and args.kind == "receipt":
        return 0
    if args.client == "codex":
        return codex_main(args.kind, args.consent, args.by_agent)
    if args.kind == "turn":
        parser.error("Claude Code has no per-prompt hook; --kind turn is for --client codex")
    if args.consent is not None:
        try:
            return answer_consent("claude", args.consent, args.by_agent)
        except MissError:
            print("找不到外掛的資料夾（CLAUDE_PLUGIN_DATA）；照 /apersona:login 最後給的那一行執行")
            print(exit_line(EXIT_FAILED))
            return EXIT_FAILED
    started_ms = args.started_ms or now_ms()
    hook_input = sys.stdin.buffer.read(8 * 1024 * 1024 + 1).decode("utf-8", errors="replace")
    if not allows(hook_fields(hook_input).get("cwd")):
        log_end(args.event_id, args.kind, "skipped", "project_disabled", started_ms)
        return 0
    receipt = post_tool_receipt(hook_fields(hook_input)) if args.kind == "receipt" else None
    if args.kind == "receipt" and (receipt is None or len(hook_input.encode("utf-8")) > 8 * 1024 * 1024):
        log_end(args.event_id, args.kind, "skipped", "no_receipt", started_ms)
        return 0
    try:
        # the device login only: an old key left in the plugin's options is never read, let alone sent
        site, key = login_bearer(
            os.environ.get("CLAUDE_PLUGIN_OPTION_SITE", ""), BUDGET_S - (now_ms() - started_ms) / 1000
        )
        if args.kind == "receipt" and receipt is not None:
            request = turn_request(site, key, receipt[0], "", [receipt[1]])
            fetch_text(request, BUDGET_S - (now_ms() - started_ms) / 1000)
            log_end(args.event_id, args.kind, "ok", "", started_ms)
            return 0
        if args.kind == "stop":
            try:  # 代答 first: it is what he waits on; 收錄 takes what is left of the budget and retries next time
                line = stand_in(site, key, hook_fields(hook_input), BUDGET_S - (now_ms() - started_ms) / 1000)
            except MissError:  # separate clauses: the formatter would drop the parentheses python 3.9 needs
                line = ""
            except OSError:
                line = ""
            except ValueError:
                line = ""
            if line:
                print(line, flush=True)
            try:
                capture_stop(
                    site,
                    key,
                    hook_input,
                    time.monotonic() + max(0, BUDGET_S - (now_ms() - started_ms) / 1000),
                )
                log_end(args.event_id, args.kind, "ok", "", started_ms)
            except OSError:
                log_end(args.event_id, args.kind, "miss", "capture_failed", started_ms)
            except ValueError:
                log_end(args.event_id, args.kind, "miss", "capture_failed", started_ms)
            return 0
        request = opening_request(site, key)
        text, _ids = fetch_text(request, BUDGET_S - (now_ms() - started_ms) / 1000)
        if not text and args.kind == "opening":
            raise MissError("bad_response")
    except MissError as e:
        miss(args.event_id, args.kind, e, started_ms, "claude")
        if args.kind == "opening":
            print_conversation_line(hook_input)
        return 0
    deliver(text, args.kind, args.event_id, started_ms, "claude")
    if args.kind == "opening":
        print_conversation_line(hook_input)  # outside the fence: the plugin's own words, not the site's
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
