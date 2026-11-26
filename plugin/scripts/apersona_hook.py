"""Apersona 開場、Codex 每輪提示與 Claude Code 回想收件。只用 Python 3.9 以上的標準函式庫。"""

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
import urllib.parse
import urllib.request
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apersona_mask import redact_secrets  # noqa: E402  (the site's own masking, word for word)
from apersona_projects import allows, project_key, project_path, set_project, write_switch  # noqa: E402
from apersona_safe import EXIT_FAILED, STATUS_EXITS, clean, exit_line, fenced, open_url, read_json, terminal_input, utf8_pipes  # noqa: E402,E501

#: the hook's own share of the 3 s; `APERSONA_HOOK_BUDGET_S` widens it only where a test runs on a busy machine
BUDGET_S = float(os.environ.get("APERSONA_HOOK_BUDGET_S") or 2.2)
#: the most of the site's words one hook puts in front of the model: the opening (as the site's own 8,000) and a turn
MAX_TEXT = 8_000
MAX_TURN_TEXT = 300
CUT_NOTICE = "（資料未完整交付；以下只有前段，未登記為已收到。）"
OPENING_CUT_NOTICE = "（開場資料未完整交付；本人更正或規矩可能缺漏，可用 opening 取回伺服器開場包。）"
#: what the model is told when the opening did not arrive in time: one line, so it fetches the pack itself
#: of the hook's time, what the request after the login keeps for itself (the 開場包 answers in about 0.9 s)
FETCH_RESERVE_S = 1.0
OPENING_LATE_NOTICE = "（開場包這次在時限內沒拿到；請先自己呼叫 apersona 的 opening 工具取回，再回答本人。）"
#: The first line of a Codex transcript names its source.
CODEX_INTERACTIVE = frozenset({"cli", "vscode"})
SESSION_META_CAP = 1024 * 1024
HINT_SESSIONS = 64


class MissError(Exception):
    "Why nothing was printed, as one short code for the log."

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.message = message


def data_dir(client: str = "claude") -> Path | None:
    "Where this client keeps its log, turns, consent and acks; None when there is no such place."
    if client == "codex":
        try:
            return Path.home() / ".apersona" / "codex"
        except RuntimeError:
            return None
    data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(data) / "apersona" if data else None


def switch_file(client: str = "claude") -> Path | None:
    "The client's global switch (`/apersona:on`／`off`, for Codex `apersona_codex.py on|off`); None with no place."
    if client == "codex":
        data = data_dir(client)
        return data / "apersona.json" if data is not None else None
    plugin = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(plugin) / "apersona.json" if plugin else None


def login_bearer(site: str, left_s: float) -> tuple[str, str]:
    """The site and access token of the device login (`apersona_login.py`), the only way the hook reaches the site;
    no persona: the site takes the one the login stands for. The hook never logs in and never trades: with none it
    is `not_configured`; with an access token at its end it waits for the trade it started in the background, and
    is `token_expiring` when that does not come in time."""
    try:
        import apersona_login  # beside this file (copied beside it for Codex)
    except ImportError:
        raise MissError("not_configured") from None
    try:
        where = apersona_login.site_of(site)
        # leave the request itself its share: an ended token waits only for what is left after it
        wait_s = max(0.1, left_s - FETCH_RESERVE_S)
        return where, apersona_login.bearer(where, wait_s, trade=apersona_login.Trade.WAIT)
    except apersona_login.LoginError as e:
        raise MissError(
            "not_configured" if e.kind == "not_logged_in" else e.kind,
            apersona_login.login_error_message(e) if e.kind == "refresh_failed" else "",
        ) from None


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def opening_request(site: str, key: str, session: object = None, cwd: object = None) -> urllib.request.Request:
    """The 開場包 request; with the hook's `session` the site keeps it on the receipt as the conversation, and with
    its `cwd` the pack holds the rules of that project besides those of every project (`project_key`)."""
    url = site.rstrip("/") + "/api/memory/opening"
    query = {"hooks": "0"}
    if isinstance(session, str) and session:
        query["sessionId"] = session
    if isinstance(cwd, str) and cwd:
        query["project"] = project_key(cwd)
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return urllib.request.Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json"})


def turn_request(
    site: str, key: str, session: str, prompt: str, received: list[str] | None, hints: bool = False
) -> urllib.request.Request:
    "`hints`: this hook reports the hints it showed whole (`hint_receipts`); the site tells a 作廢通知 only then."
    # what he typed leaves the machine with its secrets dropped first, as everything the plugin sends
    fields: dict[str, object] = {"sessionId": session, "prompt": redact_secrets(prompt)}  # the login's persona
    if received is not None:
        fields["received"] = received
    if hints:
        fields["hintLimit"] = MAX_TURN_TEXT
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
            outcome.append(MissError(f"http_{e.code}"))
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


def fetch_text(request: urllib.request.Request, deadline_s: float) -> tuple[str, str]:
    """Read the server's text and optional hint receipt."""
    body = fetch_json(request, deadline_s)
    fields = cast("dict[str, object]", body) if isinstance(body, dict) else {}
    text, receipt = fields.get("text"), fields.get("receipt")
    if not isinstance(text, str):
        raise MissError("bad_response")
    return text, receipt if isinstance(receipt, str) else ""


def hint_receipts(data: Path) -> dict[str, str]:
    "The hint each conversation was shown whole and has not reported received yet, by its receipt."
    kept = read_json(data / "hint_receipts.json")
    held = cast("dict[str, object]", kept).get("sessions") if isinstance(kept, dict) else None
    sessions = cast("dict[str, object]", held) if isinstance(held, dict) else {}
    return {session: receipt for session, receipt in sessions.items() if isinstance(receipt, str)}


def keep_hint_receipt(data: Path, session: str, receipt: str) -> None:
    """After a per-turn reply came back: the hint reported with that request is forgotten, and the one just shown
    whole (`receipt`, '' for none) is kept for the next request to report."""
    sessions = hint_receipts(data)
    if sessions.pop(session, "") == receipt:
        return
    if receipt:
        sessions[session] = receipt
    state = {"sessions": dict(list(sessions.items())[-HINT_SESSIONS:])}
    try:
        (data / "hint_receipts.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:  # not kept: the site says that 作廢通知 again
        pass


def codex_turn(data: Path, site: str, key: str, fields: dict[str, object]) -> tuple[urllib.request.Request, str]:
    "The per-turn hint request and its conversation identifier."
    session, prompt = fields.get("session_id"), fields.get("prompt")
    if not isinstance(session, str) or not isinstance(prompt, str):
        raise MissError("bad_input")
    shown = hint_receipts(data).get(session, "")
    return turn_request(site, key, session, prompt, [shown] if shown else [], hints=True), session


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
    if error.message:
        sys.stdout.buffer.write((error.message + "\n").encode("utf-8"))
        sys.stdout.flush()
    elif kind == "opening" and str(error) in ("deadline", "token_expiring"):
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


def deliver(text: str, kind: str, event_id: str, started_ms: int, client: str, error: str = "") -> bool:
    """Print what the site gave as data: control characters out, cut to MAX_TEXT (a turn to MAX_TURN_TEXT), between
    the plugin's own two lines, and log how it went. True when all of it was printed."""
    server_chars = len(text)
    text = clean(text).strip()
    cleaned_chars = len(text)
    if not text:
        log_end(event_id, kind, "empty", error, started_ms, client)
        return False
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
    return not cut


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
    record = read_json(data / "config.json")
    site = cast("dict[str, object]", record).get("site") if isinstance(record, dict) else None
    if not isinstance(site, str) or not site:
        raise MissError("not_configured")
    return site


def codex_main(kind: str) -> int:
    "The same three hooks for Codex: no wrapper script, so this does the switch, the start line and the clock itself."
    started_ms = now_ms()
    data = data_dir("codex")
    if data is None:
        return 0
    hook_input = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    fields = hook_fields(hook_input)
    if switched_off(data) or not allows(fields.get("cwd")):
        return 0
    event_id = f"ev-{started_ms}-{os.getpid()}-{os.urandom(2).hex()}"
    if not append_log({"event_id": event_id, "kind": kind, "phase": "start", "ts_ms": started_ms}, "codex"):
        return 0
    session = ""
    try:
        if fields.get("agent_id") is not None:
            log_end(event_id, kind, "skipped", "skipped_agent", started_ms, "codex")
            return 0
        source = session_source(fields)
        if source is not None and source not in CODEX_INTERACTIVE:
            log_end(event_id, kind, "skipped", "skipped_" + source, started_ms, "codex")
            return 0
        site, key = login_bearer(codex_config(data), BUDGET_S - (now_ms() - started_ms) / 1000)
        if kind == "turn":
            request, session = codex_turn(data, site, key, fields)
        else:
            request = opening_request(site, key, fields.get("session_id"), fields.get("cwd"))
        text, receipt = fetch_text(request, BUDGET_S - (now_ms() - started_ms) / 1000)
        if not text and kind == "opening":
            raise MissError("bad_response")
    except MissError as e:
        miss(event_id, kind, e, started_ms, "codex")
        if kind == "opening":
            print_conversation_line(hook_input)
        return 0
    whole = deliver(text, kind, event_id, started_ms, "codex")
    if kind == "opening":
        print_conversation_line(hook_input)
    else:  # a hint cut or empty was not shown whole
        keep_hint_receipt(data, session, receipt if whole else "")
    return 0


def set_switch(client: str, on: bool) -> str:
    """Toggle this client's automatic opening and prompts."""
    path = switch_file(client)
    if path is None:
        raise MissError("not_configured")
    write_switch(path, on)
    return "Apersona 開場與提示已" + ("開啟。" if on else "停用。")


def switch_command(client: str, on: bool, project: str | None) -> int:
    "`--switch on|off`: the whole machine's switch, or with `--project-directory` this project's; the exit code."
    try:
        if project is None:
            print(set_switch(client, on))
            return 0
        set_project(project, on)
    except MissError:
        print("找不到外掛的資料夾（CLAUDE_PLUGIN_DATA）；照 /apersona:on 的指令帶上它再執行", file=sys.stderr)
        print(exit_line(EXIT_FAILED), file=sys.stderr)
        return EXIT_FAILED
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        print(exit_line(EXIT_FAILED), file=sys.stderr)
        return EXIT_FAILED
    print("專案 hook 已" + ("開啟" if on else "停用"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apersona_hook.py")
    parser.add_argument("--kind", choices=("opening", "turn", "receipt"), default="opening")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--started-ms", type=int, default=0)
    parser.add_argument("--client", choices=("claude", "codex"), default="claude")
    parser.add_argument("--switch", choices=("on", "off"), help="整台開關；加 --project-directory 只這專案")
    parser.add_argument("--project-directory")
    args = parser.parse_args(argv)
    if args.switch is not None:
        return switch_command(args.client, args.switch == "on", args.project_directory)
    if args.client == "codex":
        return 0 if args.kind == "receipt" else codex_main(args.kind)
    if args.kind == "turn":
        parser.error("Claude Code 沒有每輪提示 hook")
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
        request = opening_request(site, key, *(hook_fields(hook_input).get(k) for k in ("session_id", "cwd")))
        text, _receipt = fetch_text(request, BUDGET_S - (now_ms() - started_ms) / 1000)
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
    utf8_pipes()
    raise SystemExit(main())
