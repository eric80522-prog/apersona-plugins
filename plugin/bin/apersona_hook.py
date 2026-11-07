"""開場包 at SessionStart (`--kind opening`) and 每輪記憶 at UserPromptSubmit (`--kind turn`, the hook's JSON with
`session_id` and `prompt` on stdin): ask the site and print what it gives for the agent.

Standard library only, on whatever python3 the machine has (3.9 and up): the plugin carries no packages. The shape
of the old `pfc_read.py dongwo-opening`: the site, persona and key come from the plugin's options
(CLAUDE_PLUGIN_OPTION_SITE / _PERSONA / _KEY); of the hook's 3 seconds, counted from when the wrapper started, the
request gets what is left of 2.2 and runs in a daemon thread that is abandoned at the deadline (urllib's timeout
bounds each socket step, not the whole request). Anything short of a good answer prints nothing; a too-long
one is cut to MAX_TEXT and says so, never dropped. The log gets the
end line (the wrapper wrote the start line): result, error kind and milliseconds, never content. A turn with nothing
related is `empty`, not a miss.

A turn gives only a relevance hint; recall tools deliver the source text. Stop captures only explicitly
consented new user messages, redacted before transmission, and records successful message ids for retries.

`--client codex` is the same three hooks for the Codex CLI. Codex has no plugin options, so the site, persona and key
file come from `~/.apersona/codex/config.json`, and the hook writes its own log lines and keeps its own clock. Codex's
rollout is not a stable format to read, so each typed prompt is spooled at UserPromptSubmit (only after consent) and
sent at Stop, and only when the rollout's first line says a person typed it (`cli` or `vscode`, never `codex exec`
or a subagent).
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
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

BUDGET_S = 2.2
MAX_TEXT = 10_000
CUT_NOTICE = "（開場包超過上限，已截）"
CONSENT_TEXT = "同意收錄本人在 Claude Code 新打的字；秘密先遮、可撤回、素材每日合併讀。"
CONSENT_SHA256 = hashlib.sha256(CONSENT_TEXT.encode()).hexdigest()
CODEX_CONSENT_TEXT = "同意收錄本人在 Codex 親自新打的字（codex exec 與子代理不收）；秘密先遮、可撤回、素材每日合併讀。"
CODEX_CONSENT_SHA256 = hashlib.sha256(CODEX_CONSENT_TEXT.encode()).hexdigest()
CODEX_INTERACTIVE = frozenset({"cli", "vscode"})
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


def redact(content: str) -> str:
    content = re.sub(r"(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[秘密已遮]", content)
    content = re.sub(r"(?i)(?:sk-[\w-]{12,}|apersona_token_[\w-]+|Bearer\s+[\w./+=-]+)", "[秘密已遮]", content)
    return re.sub(r"(?im)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+", r"\1[秘密已遮]", content)


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
    wanted = CODEX_CONSENT_SHA256 if client == "codex" else CONSENT_SHA256
    return record if record.get("on") is True and record.get("sha256") == wanted else {}


def capture_stop(site: str, persona: str, key: str, hook_input: str, deadline: float) -> None:
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
    ack = (
        consent_path().parent / "captured" / (hashlib.sha256((site + persona + session).encode()).hexdigest() + ".json")
    )
    try:
        sent: set[str] = set(cast("list[str]", json.loads(ack.read_text(encoding="utf-8"))))
    except OSError:
        sent = set()
    except ValueError:
        sent = set()
    with Path(transcript).open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 8_000_000))
        lines = stream.read().decode("utf-8", errors="replace").splitlines()
    for line in lines:
        if time.monotonic() >= deadline or consent() != approval:
            break
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        row = cast("dict[str, object]", row)
        if row.get("type") != "user" or row.get("isMeta"):
            continue
        if not isinstance(row.get("timestamp"), str) or row.get("uuid") in sent:
            continue
        try:
            written = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if written.tzinfo is None or written < since:
            continue
        message = row.get("message", {})
        if not isinstance(message, dict):
            continue
        message = cast("dict[str, object]", message)
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                str(cast("dict[str, object]", block).get("text", ""))
                for block in cast("list[object]", content)
                if isinstance(block, dict) and cast("dict[str, object]", block).get("type") == "text"
            )
        if not isinstance(content, str) or not content.strip() or not row.get("uuid"):
            continue
        content = redact(content)
        identifier = str(row["uuid"])
        payload = {
            "personaId": persona,
            "turnId": session + ":" + identifier,
            "messageId": identifier,
            "text": content,
            "consentSha256": CONSENT_SHA256,
        }
        request = urllib.request.Request(
            site.rstrip("/") + "/api/memory/conversation",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST",
        )
        fetch_text(request, max(0, deadline - time.monotonic()))
        sent.add(identifier)
        ack.parent.mkdir(parents=True, exist_ok=True)
        ack.write_text(json.dumps(sorted(sent)), encoding="utf-8")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def opening_request(site: str, persona: str, key: str) -> urllib.request.Request:
    url = site.rstrip("/") + "/api/memory/opening?" + urllib.parse.urlencode({"personaId": persona})
    return urllib.request.Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json"})


def typed_turn(hook_input: str) -> tuple[str, str]:
    "The conversation and the prompt from the hook's JSON."
    try:
        given = json.loads(hook_input)
    except ValueError:
        raise MissError("bad_input") from None
    fields = cast("dict[str, object]", given) if isinstance(given, dict) else {}
    session, prompt = fields.get("session_id"), fields.get("prompt")
    if not isinstance(session, str) or not isinstance(prompt, str):
        raise MissError("bad_input")
    return session, prompt


def turn_request(
    site: str, persona: str, key: str, session: str, prompt: str, received: list[str] | None
) -> urllib.request.Request:
    fields: dict[str, object] = {"personaId": persona, "sessionId": session, "prompt": prompt}
    if received is not None:
        fields["received"] = received
    body = json.dumps(fields).encode("utf-8")
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json", "Content-Type": "application/json"}
    return urllib.request.Request(site.rstrip("/") + "/api/memory/turn", data=body, headers=headers, method="POST")


def received_file(session: str, client: str = "claude") -> Path | None:
    "Where the ids of the last turn this conversation got are kept; None without the plugin's data folder."
    data = data_dir(client)
    if data is None:
        return None
    name = hashlib.sha256(session.encode("utf-8")).hexdigest()[:32]
    return data / "turns" / (name + ".json")


def load_received(session: str, client: str = "claude") -> list[str] | None:
    "The ids the last turn got ([] when none or unreadable); None when they cannot be kept, so none are sent."
    path = received_file(session, client)
    if path is None:
        return None
    try:
        ids = json.loads(path.read_text(encoding="utf-8"))
    except OSError:  # two clauses: the formatter would drop the parentheses python 3.9 needs
        return []
    except ValueError:
        return []
    return [i for i in cast("list[object]", ids) if isinstance(i, str)] if isinstance(ids, list) else []


def save_received(session: str, ids: list[str], client: str = "claude") -> None:
    path = received_file(session, client)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ids), encoding="utf-8")
    except OSError:
        pass


def fetch_text(request: urllib.request.Request, deadline_s: float) -> tuple[str, list[str]]:
    "The answer's `text`, possibly empty, and its `ids` (none when it has none); anything else is a miss."
    if deadline_s <= 0:
        raise MissError("deadline")
    outcome: list[object] = []

    def call() -> None:
        try:
            with urllib.request.urlopen(request, timeout=deadline_s) as response:
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


def log_end(event_id: str, kind: str, result: str, error: str, started_ms: int, client: str = "claude") -> None:
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
    append_log(row, client)


def deliver(
    text: str, ids: list[str], session: str, kind: str, event_id: str, started_ms: int, client: str, error: str = ""
) -> None:
    "Print what the site gave (cut to MAX_TEXT), keep the ids of a turn, and log how it went."
    if not text:
        if session:
            save_received(session, [], client)
        log_end(event_id, kind, "empty", error, started_ms, client)
        return
    # UTF-8 bytes, not the console's code page: on a Chinese-locale Windows the default encoding cannot write it all.
    cut = len(text) > MAX_TEXT
    if cut:
        text = text[:MAX_TEXT] + "\n" + CUT_NOTICE
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()
    if session:
        save_received(session, ids, client)
    log_end(event_id, kind, "truncated" if cut else "ok", error, started_ms, client)


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


def codex_config(data: Path) -> tuple[str, str, str]:
    "Site, persona and key from the installer's config.json; anything missing or unreadable is not_configured."
    try:
        fields = cast("dict[str, object]", json.loads((data / "config.json").read_text(encoding="utf-8")))
        site, persona, key_file = fields["site"], fields["persona"], fields["key_file"]
        key = Path(str(key_file)).read_text(encoding="utf-8").strip()
    except OSError:
        raise MissError("not_configured") from None
    except ValueError:
        raise MissError("not_configured") from None
    except KeyError:
        raise MissError("not_configured") from None
    except AttributeError:
        raise MissError("not_configured") from None
    if not (isinstance(site, str) and isinstance(persona, str) and site and persona and key):
        raise MissError("not_configured")
    return site, persona, key


def spool_file(data: Path, session: str) -> Path:
    return data / "spool" / (hashlib.sha256(session.encode("utf-8")).hexdigest()[:32] + ".jsonl")


def spool_turn(data: Path, session: str, fields: dict[str, object]) -> None:
    "Keep the typed prompt until Stop can check who typed it. The caller has checked the Codex consent."
    turn_id, prompt = fields.get("turn_id"), fields.get("prompt")
    if not isinstance(turn_id, str) or not turn_id or not isinstance(prompt, str):
        return
    text = redact(prompt.strip())[:MAX_CAPTURED_TEXT]
    if not text.strip():
        return
    row = {"turn_id": turn_id, "text": text, "at": datetime.now(timezone.utc).isoformat()}  # noqa: UP017
    path = spool_file(data, session)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as out:
        out.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))


def read_spool(path: Path) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
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
            entries.append({"turn_id": turn_id, "text": text, "at": at if isinstance(at, str) else ""})
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


def codex_stop(
    data: Path, site: str, persona: str, key: str, fields: dict[str, object], deadline: float
) -> tuple[str, str]:
    "(result, error) for the log. Sends the spooled prompts of a session a person typed; fails closed on the source."
    session = fields.get("session_id")
    if not isinstance(session, str) or not session:
        return "miss", "bad_input"
    spool = spool_file(data, session)
    approval = consent("codex")
    if not approval:
        spool.unlink(missing_ok=True)
        return "ok", ""
    source = session_source(fields)
    if source not in CODEX_INTERACTIVE:
        spool.unlink(missing_ok=True)
        return "skipped", "source_" + (source or "unknown")
    try:
        since = datetime.fromisoformat(str(approval.get("since", "")).replace("Z", "+00:00"))
    except ValueError:
        return "miss", "bad_consent"
    entries = [e for e in read_spool(spool) if typed_since(e.get("at", ""), since)]
    ack = data / "captured" / (hashlib.sha256((site + persona + session).encode()).hexdigest() + ".json")
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
            if identifier not in sent:
                if time.monotonic() >= deadline or consent("codex") != approval:
                    break
                payload = {
                    "personaId": persona,
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
                    if str(refused) not in PERMANENT_REFUSALS:
                        raise
                    dropped.append("dropped_" + str(refused))
                    remaining.remove(entry)
                    continue
                sent.add(identifier)
                ack.parent.mkdir(parents=True, exist_ok=True)
                ack.write_text(json.dumps(sorted(sent)), encoding="utf-8")
            remaining.remove(entry)
    finally:
        write_spool(spool, remaining)
    return "ok", ",".join(dropped)


def codex_main(kind: str, consent_answer: str | None) -> int:
    "The same three hooks for Codex: no wrapper script, so this does the switch, the start line and the clock itself."
    started_ms = now_ms()
    data = data_dir("codex")
    if data is None:
        return 0
    if consent_answer is not None:
        path = consent_path("codex")
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "on": consent_answer == "yes",
            "sha256": CODEX_CONSENT_SHA256,
            "since": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        }
        path.write_text(json.dumps(record), encoding="utf-8")
        if consent_answer == "no":
            clear_spool(data)
        return 0
    if switched_off(data):
        return 0
    event_id = f"ev-{started_ms}-{os.getpid()}-{os.urandom(2).hex()}"
    if not append_log({"event_id": event_id, "kind": kind, "phase": "start", "ts_ms": started_ms}, "codex"):
        return 0
    session, note = "", ""
    try:
        site, persona, key = codex_config(data)
        fields = hook_fields(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        if fields.get("agent_id") is not None:
            log_end(event_id, kind, "skipped", "skipped_agent", started_ms, "codex")
            return 0
        if kind == "stop":
            deadline = time.monotonic() + max(0, BUDGET_S - (now_ms() - started_ms) / 1000)
            try:
                result, error = codex_stop(data, site, persona, key, fields, deadline)
            except OSError:
                result, error = "miss", "capture_failed"
            log_end(event_id, kind, result, error, started_ms, "codex")
            return 0
        source = session_source(fields)
        if source is not None and source not in CODEX_INTERACTIVE:
            log_end(event_id, kind, "skipped", "skipped_" + source, started_ms, "codex")
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
            request = turn_request(site, persona, key, session, prompt, load_received(session, "codex"))
        else:
            request = opening_request(site, persona, key)
        text, ids = fetch_text(request, BUDGET_S - (now_ms() - started_ms) / 1000)
        if not text and kind == "opening":
            raise MissError("bad_response")
    except MissError as e:
        log_end(event_id, kind, "miss", str(e), started_ms, "codex")
        return 0
    deliver(text, ids, session, kind, event_id, started_ms, "codex", note)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apersona_hook.py")
    parser.add_argument("--kind", choices=("opening", "turn", "stop"), default="opening")
    parser.add_argument("--consent", choices=("yes", "no"), help="本人二選一：同意收錄或不要")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--started-ms", type=int, default=0)
    parser.add_argument("--client", choices=("claude", "codex"), default="claude")
    args = parser.parse_args(argv)
    if args.client == "codex":
        return codex_main(args.kind, args.consent)
    if args.consent is not None:
        path = consent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "on": args.consent == "yes",
                    "sha256": CONSENT_SHA256,
                    "since": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
                }
            ),
            encoding="utf-8",
        )
        return 0
    started_ms = args.started_ms or now_ms()
    site, persona, key = (os.environ.get("CLAUDE_PLUGIN_OPTION_" + k, "") for k in ("SITE", "PERSONA", "KEY"))
    session = ""
    try:
        if not (site and persona and key):
            raise MissError("not_configured")
        if args.kind == "stop":
            try:
                capture_stop(
                    site,
                    persona,
                    key,
                    sys.stdin.buffer.read().decode("utf-8", errors="replace"),
                    time.monotonic() + max(0, BUDGET_S - (now_ms() - started_ms) / 1000),
                )
                log_end(args.event_id, args.kind, "ok", "", started_ms)
            except OSError:
                log_end(args.event_id, args.kind, "miss", "capture_failed", started_ms)
            except ValueError:
                log_end(args.event_id, args.kind, "miss", "capture_failed", started_ms)
            return 0
        if args.kind == "turn":
            hook_input = sys.stdin.buffer.read().decode("utf-8", errors="replace")
            session, prompt = typed_turn(hook_input)
            request = turn_request(site, persona, key, session, prompt, load_received(session))
        else:
            request = opening_request(site, persona, key)
        text, ids = fetch_text(request, BUDGET_S - (now_ms() - started_ms) / 1000)
        if not text and args.kind == "opening":
            raise MissError("bad_response")
    except MissError as e:
        log_end(args.event_id, args.kind, "miss", str(e), started_ms)
        return 0
    deliver(text, ids, session, args.kind, args.event_id, started_ms, "claude")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
