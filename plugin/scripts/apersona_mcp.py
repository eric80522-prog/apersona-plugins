"""Local stdio to the site's stateless JSON MCP endpoint, using the hook's device login.

No tool arguments, conversation ids or receipt confirmations are invented here. HTTP requests are attempted once;
after a timeout or cancellation the remote operation may still finish. Cancellation suppresses the local reply,
not the remote operation. Server-initiated messages and SSE are not supported by this JSON-only transport.
The process keeps at most four requests in flight, bounded frames and hard response deadlines. Network workers
are daemons: a hung peer cannot keep the process alive after EOF, cancellation or an interrupted terminal.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, TextIO, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apersona_login  # noqa: E402
from apersona_safe import open_url  # noqa: E402

FRAME_BYTES = 1024 * 1024
REPLY_BYTES = 8 * 1024 * 1024
IN_FLIGHT = 4
REQUEST_TIMEOUT = 30.0
# Exception tuples keep this script valid on Python 3.9 under newer formatters.
WIRE_ERRORS = (OSError, ValueError, RecursionError)
INPUT_ERRORS = (ValueError, UnicodeError, RecursionError)
STREAM_ERRORS = (OSError, ValueError)
EXIT_ERRORS = (OSError, KeyboardInterrupt)


def _error(identifier: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def _constant(value: str) -> None:
    raise ValueError("non-JSON constant")


def _object(raw: bytes) -> dict[str, object]:
    value: object = json.loads(raw.decode("utf-8"), parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("object required")
    return cast("dict[str, object]", value)


def _post(site: str, message: dict[str, object], protocol: str, timeout: float) -> dict[str, object] | None:
    "One authenticated HTTP attempt, with no redirect or replay of a possibly executed operation."
    identifier = message.get("id")
    try:
        # The host may kill a stdio worker at any time. As with hooks, refresh belongs to the existing
        # independent process, never a cancellable thread that might lose a just-rotated refresh token.
        bearer = apersona_login.bearer(site, timeout_s=timeout, trade=False)
        headers = {
            "Authorization": "Bearer " + bearer,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if protocol:
            headers["MCP-Protocol-Version"] = protocol
        request = urllib.request.Request(
            site + "/mcp",
            data=json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with open_url(request, timeout) as response:
            if "id" not in message:
                return None  # notifications have no stdio response, including HTTP 202
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise ValueError("unsupported response")
            raw = response.read(REPLY_BYTES + 1)
            if len(raw) > REPLY_BYTES:
                raise ValueError("response too large")
            answer = _object(raw)
        if (
            answer.get("jsonrpc") != "2.0"
            or type(answer.get("id")) is not type(identifier)
            or answer.get("id") != identifier
            or ("result" in answer) == ("error" in answer)
        ):
            raise ValueError("mismatched response")
        return answer
    except apersona_login.LoginError:
        return _error(identifier, -32001, "Apersona login unavailable; run /apersona:login, then try again.")
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 401:
            return _error(
                identifier, -32001, "Apersona rejected this login; sign in again. The request was not retried."
            )
        return _error(
            identifier, -32002, "Apersona HTTP request failed. It was not retried; check before repeating changes."
        )
    except WIRE_ERRORS:
        return _error(identifier, -32002, "Apersona response unavailable or invalid. The request was not retried.")


def _read_frames(stream: BinaryIO, events: queue.Queue[tuple[str, object]]) -> None:
    try:
        while True:
            line = stream.readline(FRAME_BYTES + 2)
            if not line:
                break
            oversized = len(line) > FRAME_BYTES or not line.endswith(b"\n")
            if oversized:
                while line and not line.endswith(b"\n"):
                    line = stream.readline(FRAME_BYTES + 2)
                events.put(("invalid", None))
            else:
                events.put(("input", line))
    except STREAM_ERRORS:
        pass
    finally:
        events.put(("eof", None))


class _Flight:
    def __init__(self, message: dict[str, object], deadline: float) -> None:
        self.message = message
        self.deadline = deadline
        self.finished = False


def serve(stream: BinaryIO, output: TextIO, site: str, *, timeout: float = REQUEST_TIMEOUT) -> int:
    "Forward bounded concurrent requests; EOF drains live responses only until their hard deadlines."
    site = apersona_login.site_of(site)
    events: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=16)
    threading.Thread(target=_read_frames, args=(stream, events), daemon=True).start()
    flights: dict[int, _Flight] = {}
    next_job, protocol, eof = 0, "", False

    def emit(value: dict[str, object]) -> None:
        output.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        output.flush()

    def request(job: int, message: dict[str, object], version: str) -> None:
        result = _post(site, message, version, timeout)
        events.put(("reply", (job, result)))

    try:
        while not eof or any(not flight.finished for flight in flights.values()):
            now = time.monotonic()
            for flight in flights.values():
                if not flight.finished and now >= flight.deadline:
                    flight.finished = True
                    if "id" in flight.message:
                        emit(
                            _error(
                                flight.message["id"],
                                -32003,
                                "Apersona request timed out; it was not retried. Its result may be unknown.",
                            )
                        )
            try:
                kind, value = events.get(timeout=0.02)
            except queue.Empty:
                continue
            if kind == "eof":
                eof = True
                continue
            if kind == "reply":
                job, result = cast("tuple[int, dict[str, object] | None]", value)
                flight = flights.pop(job, None)
                if flight is None or flight.finished:
                    continue
                if "id" in flight.message and result is not None:
                    if flight.message.get("method") == "initialize" and isinstance(result.get("result"), dict):
                        details = cast("dict[str, object]", result["result"])
                        version = details.get("protocolVersion")
                        if (
                            isinstance(version, str)
                            and 0 < len(version) <= 64
                            and version.isascii()
                            and version.isprintable()
                            and not any(c.isspace() for c in version)
                        ):
                            protocol = version
                    emit(result)
                continue
            if kind == "invalid":
                emit(_error(None, -32600, "MCP input exceeds the local transport limit."))
                continue
            try:
                message = _object(cast(bytes, value))
                identifier = message.get("id")
                if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
                    raise ValueError("invalid request")
                if "id" in message and type(identifier) not in (int, str):
                    raise ValueError("invalid id")
                if "params" in message and not isinstance(message["params"], dict):
                    raise ValueError("invalid parameters")
            except INPUT_ERRORS:
                emit(_error(None, -32600, "Invalid MCP JSON-RPC request."))
                continue
            if message["method"] == "notifications/cancelled" and "id" not in message:
                params = cast("dict[str, object]", message.get("params", {}))
                cancelled = params.get("requestId")
                for flight in flights.values():
                    if type(flight.message.get("id")) is type(cancelled) and flight.message.get("id") == cancelled:
                        flight.finished = True
                continue
            if len(flights) >= IN_FLIGHT or (
                "id" in message
                and any(
                    type(f.message.get("id")) is type(identifier) and f.message.get("id") == identifier
                    for f in flights.values()
                )
            ):
                if "id" in message:
                    emit(
                        _error(identifier, -32004, "MCP transport busy or request id already in use; no request sent.")
                    )
                continue
            next_job += 1
            flights[next_job] = _Flight(message, time.monotonic() + timeout)
            threading.Thread(target=request, args=(next_job, message, protocol), daemon=True).start()
        return 0
    except EXIT_ERRORS:
        return 0  # the host closed the transport; do not keep network workers alive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apersona MCP using the local device login")
    parser.add_argument("--site", default="")
    args = parser.parse_args(argv)
    configure = cast("Callable[..., None] | None", getattr(sys.stdout, "reconfigure", None))
    if configure is not None:
        configure(encoding="utf-8", newline="\n")
    try:
        return serve(sys.stdin.buffer, sys.stdout, args.site)
    except apersona_login.LoginError:
        print("Apersona site is invalid; check the configured address.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
