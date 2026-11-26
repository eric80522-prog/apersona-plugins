"""裝置碼登入 (OAuth 2.1 device flow, RFC 8628) for the plugin's hooks and file sender, and the credentials it leaves.

    apersona_login.py login [--site URL]   show the address and short code, then wait for him to press 允許 on the
                                           website; run again to go on waiting (a code lives ten minutes)
    apersona_login.py status               logged in or not, asked of the site (never prints a token)
    apersona_login.py refresh              trade an ending access token now, quietly (what the hook starts)
    apersona_login.py logout               sign out this device on the site and delete the credentials file

Standard library only, on whatever python3 the machine has (3.9 and up). The credentials are in
`~/.apersona/plugin-credentials.json` (the folder 700, the file 600, except on Windows): site, client_id, access,
refresh and when the access token ends. This login is the only way the plugin reaches the site.

Whether a process trades now is one decision (`Trade`, `_due`). The hook and the MCP forwarder never trade (either
may be killed at any moment, and a trade cut off after the site answered would leave the new pair unwritten and the
device signed out on the next try): ten minutes before the access token ends they start `refresh` as a process of its
own, which trades then; with an ended token they wait, within their own time, for that trade's pair (`token_expiring`
only when it does not come in time). The commands he runs trade when the access token has ended. The site makes
every refresh token good once and signs the whole device out when an old one comes back, so a new pair is written to
the file before anything else is done with it, and only one process at a time may trade (a lock file; a second one
waits for the first one's answer instead of presenting the same refresh token again).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apersona_safe import (  # noqa: E402
    EXIT_FAILED,
    EXIT_NOT_LOGGED_IN,
    EXIT_UNFINISHED,
    checked_site,
    exit_line,
    open_url,
    private_dir,
    read_json,
    utf8_pipes,
)

DEFAULT_SITE = "https://www.apersonaai.com"
CLIENT_NAME = "Apersona plugin"
#: a device login never sends the browser anywhere, but registration wants one address; this one goes nowhere
REDIRECT_URI = "http://127.0.0.1/apersona-device"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
#: a token this close to its end is traded before it is used (the hook skips the turn instead)
SKEW_S = 60
#: a token this close to its end is traded early by `refresh`, which the hook and the forwarder start while they still
#: use the token
AHEAD_S = 600
LOCK_WAIT_S = 1.5
LOCK_STALE_S = 30
HTTP_TIMEOUT_S = 10
#: the longest a hook or the forwarder waits for the background trade's pair: its answer, and a lock wait before it
TRADE_WAIT_S = HTTP_TIMEOUT_S + LOCK_WAIT_S + 1
#: how long one `login` waits for him before it says to run it again
LOGIN_WAIT_S = 90


class Trade(Enum):
    "When the asking process trades the refresh token itself: the one place each caller says so."

    #: never here: start `refresh` as a process of its own and wait for its pair (the hook, the MCP forwarder)
    WAIT = "wait"
    #: a token at its end (`SKEW_S`): the commands he runs
    ENDED = "ended"
    #: a token within `AHEAD_S` of its end: the background `refresh`, so the token never ends while he works
    AHEAD = "ahead"
    #: even a token that has not ended: `status`, since only the site knows whether he signed this device out
    ALWAYS = "always"


def _due(trade: Trade, left: float) -> bool:
    "Whether `trade` trades a token with `left` seconds to go."
    if trade is Trade.ALWAYS:
        return True
    return left <= (AHEAD_S if trade is Trade.AHEAD else SKEW_S)


class LoginError(Exception):
    "Why a login or a trade did not happen, in words he can read; `kind` is the short code the hook's log gets."

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind


def login_error_message(error: LoginError) -> str:
    "Shared client wording; only refresh errors carry a safe, locally generated diagnostic."
    if error.kind in ("refresh_failed", "token_expiring"):
        return str(error)
    return "Apersona login unavailable; run /apersona:login, then try again."


def credentials_path() -> Path:
    return Path.home() / ".apersona" / "plugin-credentials.json"


def read_credentials() -> dict[str, object]:
    found = read_json(credentials_path())
    return cast("dict[str, object]", found) if isinstance(found, dict) else {}


def write_credentials(record: dict[str, object]) -> None:
    "Write the file whole and put it in place at once (a reader sees the old file or the new one, never half)."
    path = credentials_path()
    private_dir(path.parent)
    temporary = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False))
        stream.flush()
        os.fsync(stream.fileno())
    if sys.platform != "win32":
        os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))


def _post(
    site: str,
    path: str,
    form: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
    timeout: float = HTTP_TIMEOUT_S,
) -> dict[str, object]:
    "A form (token endpoints) or a JSON body (registration); the answer's JSON, errors included (`error` in it)."
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
    else:
        data = urllib.parse.urlencode(form or {}).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    request = urllib.request.Request(site + path, data=data, headers=headers, method="POST")
    try:
        with open_url(request, timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except (urllib.error.URLError, OSError) as e:
        raise LoginError("unreachable", "連線錯誤：" + type(getattr(e, "reason", e)).__name__) from None
    try:
        answer: object = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        answer = {}
    result = cast("dict[str, object]", answer) if isinstance(answer, dict) else {}
    return {**result, "status": status}


def register(site: str) -> str:
    "DCR (RFC 7591): a public client, once per site."
    answer = _post(
        site,
        "/oauth2/register",
        body={
            "client_name": CLIENT_NAME,
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token", DEVICE_GRANT],
            "response_types": ["code"],
        },
    )
    client_id = answer.get("client_id")
    if answer.get("status") not in (200, 201) or not isinstance(client_id, str):
        raise LoginError("register_failed", "網站不收這個登記（/oauth2/register 回 " + str(answer.get("status")) + "）")
    return client_id


def _tokens_into(record: dict[str, object], answer: dict[str, object], now: float) -> None:
    access, refresh, life = answer.get("access_token"), answer.get("refresh_token"), answer.get("expires_in")
    if not (isinstance(access, str) and isinstance(refresh, str)):
        raise LoginError("bad_response", "網站回的通行證不完整")
    record.update(
        {
            "access": access,
            "refresh": refresh,
            "expires_at": now + (float(life) if isinstance(life, (int, float)) else 3600.0),
        }
    )
    record.pop("pending", None)
    record.pop("refresh_failure", None)


def login(
    site: str,
    say: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    wait_s: float = LOGIN_WAIT_S,
) -> bool:
    """Device flow: register once, ask for a code unless one is waiting, show it, poll at the site's interval (slower
    when it says `slow_down`). True once he has allowed it and the tokens are in the file; False when he has not yet
    (run again), and a `LoginError` for a refusal or a code that ended."""
    record = read_credentials()
    if record.get("site") != site:
        record = cast("dict[str, object]", {"site": site})
    client_id = record.get("client_id")
    if not isinstance(client_id, str):
        client_id = register(site)
        record["client_id"] = client_id
        write_credentials(record)
    pending = record.get("pending")
    shown: dict[str, object] = cast("dict[str, object]", pending) if isinstance(pending, dict) else {}
    if float(cast("float", shown.get("expires_at") or 0)) <= clock():
        started = _post(site, "/oauth2/device", form={"client_id": client_id})
        if started.get("status") != 200 or not isinstance(started.get("device_code"), str):
            raise LoginError("device_failed", "網站不給裝置碼（/oauth2/device 回 " + str(started.get("status")) + "）")
        life = started.get("expires_in")
        every = started.get("interval")
        shown = {
            "device_code": started["device_code"],
            "user_code": started.get("user_code", ""),
            "uri": started.get("verification_uri_complete") or started.get("verification_uri") or site + "/authorize",
            "expires_at": clock() + (float(life) if isinstance(life, (int, float)) else 600.0),
            "interval": float(every) if isinstance(every, (int, float)) else 5.0,
        }
        record["pending"] = shown
        write_credentials(record)
    say(f"請在瀏覽器打開 {shown['uri']}，登入後輸入代碼 {shown['user_code']}，按「允許」。")
    interval = float(cast("float", shown["interval"]))
    deadline = clock() + wait_s
    while clock() < float(cast("float", shown["expires_at"])):
        if clock() >= deadline:
            say("還在等你按允許。按完之後再執行一次 /apersona:login。")
            return False
        sleep(interval)
        answer = _post(
            site,
            "/oauth2/token",
            form={"grant_type": DEVICE_GRANT, "device_code": str(shown["device_code"]), "client_id": client_id},
        )
        error = answer.get("error")
        if error is None and answer.get("status") == 200:
            _tokens_into(record, answer, clock())
            write_credentials(record)
            return True
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5.0
            continue
        record.pop("pending", None)
        write_credentials(record)
        if error == "access_denied":
            raise LoginError("access_denied", "你在網站上按了拒絕；要登入就再執行一次 /apersona:login")
        if error == "expired_token":
            raise LoginError("expired_code", "代碼過期了；再執行一次 /apersona:login 拿新的")
        raise LoginError("login_failed", "登入失敗：" + str(error or answer.get("status")))
    record.pop("pending", None)
    write_credentials(record)
    raise LoginError("expired_code", "代碼過期了；再執行一次 /apersona:login 拿新的")


def _lock_path() -> Path:
    path = credentials_path()
    return path.with_name(path.name + ".lock")


def _take_lock(wait_s: float, clock: Callable[[], float], sleep: Callable[[float], None]) -> bool:
    lock = _lock_path()
    private_dir(lock.parent)
    end = clock() + wait_s
    while True:
        try:
            os.close(os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            return True
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_S:  # a trade that died holding it
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            except OSError:
                return False
        except OSError:
            return False
        if clock() >= end:
            return False
        sleep(0.05)


@contextmanager
def _refresh_lock(wait_s: float, clock: Callable[[], float], sleep: Callable[[float], None]) -> Iterator[None]:
    """The OS releases this lock when its process exits. Keep the older sentinel while trading for older clients."""
    marker = _lock_path()
    private_dir(marker.parent)
    fd = os.open(str(marker.with_suffix(".guard")), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+b") as stream:
        end = time.monotonic() + wait_s
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise LoginError("login_busy", "另一個程序正在換通行證") from None
                sleep(0.05)
        if not _take_lock(max(0.0, end - time.monotonic()), clock, sleep):
            raise LoginError("login_busy", "另一個程序正在換通行證")
        try:
            yield
        finally:
            marker.unlink(missing_ok=True)


def site_of(given: str = "") -> str:
    "The site to log in to: `given`, else the plugin's `site` option, else ours; `LoginError` unless it is https."
    try:
        return checked_site(given or os.environ.get("CLAUDE_PLUGIN_OPTION_SITE", "") or DEFAULT_SITE)
    except ValueError as e:
        raise LoginError("insecure_site", str(e)) from None


def bearer(
    site: str,
    timeout_s: float = HTTP_TIMEOUT_S,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    trade: Trade = Trade.ENDED,
) -> str:
    """The access token for `site`, traded for a new pair first when `trade` says it is due. `LoginError`
    `not_logged_in` when `login` was never run for this site (or the device was signed out: then the file is
    removed and he logs in again), `login_busy` when another process is trading and did not finish in time, and
    for `Trade.WAIT` `token_expiring` (or `refresh_failed`) when the background trade's pair did not come in time."""
    record = read_credentials()
    access, refresh, client_id = record.get("access"), record.get("refresh"), record.get("client_id")
    if record.get("site") != site or not (isinstance(access, str) and isinstance(refresh, str)):
        raise LoginError("not_logged_in", "還沒登入；執行 /apersona:login")
    if trade is Trade.WAIT:
        return _wait_for_trade(site, record, min(timeout_s, TRADE_WAIT_S), clock, sleep)
    if not _due(trade, float(cast("float", record.get("expires_at") or 0)) - clock()):
        return access
    if not isinstance(client_id, str):
        raise LoginError("not_logged_in", "還沒登入；執行 /apersona:login")
    observed_refresh = refresh
    with _refresh_lock(timeout_s, clock, sleep):
        record = read_credentials()  # the one that held the lock may have traded already: use its pair
        access, refresh = record.get("access"), record.get("refresh")
        if record.get("site") != site or not (isinstance(access, str) and isinstance(refresh, str)):
            raise LoginError("not_logged_in", "還沒登入；執行 /apersona:login")
        if refresh != observed_refresh or not _due(
            trade, float(cast("float", record.get("expires_at") or 0)) - clock()
        ):
            return access
        return _refresh_tokens(site, record, timeout_s, clock)


def _wait_for_trade(
    site: str, record: dict[str, object], wait_s: float, clock: Callable[[], float], sleep: Callable[[float], None]
) -> str:
    """The access token for a process that never trades: within `AHEAD_S` of its end `refresh` is started as a
    process of its own; a token still good is used as is, an ended one is waited for (up to `wait_s`) until that
    trade writes its pair. A failure the trade records while waiting ends the wait (`refresh_failed`)."""
    if float(cast("float", record.get("expires_at") or 0)) - clock() <= AHEAD_S:
        refresh_in_background(site)
    earlier = record.get("refresh_failure")
    end = clock() + wait_s
    while True:
        access, failure = record.get("access"), record.get("refresh_failure")
        if record.get("site") != site or not isinstance(access, str):
            raise LoginError("not_logged_in", "還沒登入；執行 /apersona:login")
        if float(cast("float", record.get("expires_at") or 0)) - clock() > SKEW_S:
            return access
        if isinstance(failure, dict) and (failure != earlier or clock() >= end):
            detail = cast("dict[str, object]", failure)
            raise LoginError("refresh_failed", f"換新通行證失敗：{detail.get('reason')}（{detail.get('at')}）")
        if clock() >= end:
            raise LoginError("token_expiring", "通行證快到期，背景正在換新；下一句就會帶")
        sleep(0.1)
        record = read_credentials()


def _refresh_tokens(site: str, record: dict[str, object], timeout_s: float, clock: Callable[[], float]) -> str:
    "Exchange and persist the pair or its failure while the caller holds the refresh lock."
    try:
        answer = _post(
            site,
            "/oauth2/token",
            form={
                "grant_type": "refresh_token",
                "refresh_token": str(record["refresh"]),
                "client_id": str(record["client_id"]),
            },
            timeout=timeout_s,
        )
        error = answer.get("error")
        if error in ("invalid_grant", "invalid_client"):
            # signed out on the website, or an old refresh token: the site has ended this login for good
            credentials_path().unlink()
            raise LoginError("login_ended", "這台已被登出；執行 /apersona:login 重新登入")
        if error is not None or answer.get("status") != 200:
            reason = f"伺服器回 {answer.get('status')}"
            if answer.get("status") == 403:
                reason += "，可能是網站被存取控管擋住"
            raise LoginError("refresh_failed", reason)
        _tokens_into(record, answer, clock())
        write_credentials(record)  # in place before the new access token is used for anything
        return str(record["access"])
    except LoginError as e:
        if e.kind in ("refresh_failed", "unreachable", "bad_response"):
            record["refresh_failure"] = {
                "reason": str(e),
                "at": datetime.fromtimestamp(clock(), timezone.utc).isoformat(),  # noqa: UP017 (Python 3.9)
            }
            write_credentials(record)
        raise


def refresh_in_background(site: str) -> None:
    "Start `refresh` for `site` as a process of its own (its own session): the hook's 3-second kill never reaches it."
    command = [sys.executable, str(Path(__file__).resolve()), "refresh", "--site", site]
    quiet = subprocess.DEVNULL
    try:
        if sys.platform == "win32":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(command, creationflags=flags, stdin=quiet, stdout=quiet, stderr=quiet, close_fds=True)
        else:
            subprocess.Popen(command, start_new_session=True, stdin=quiet, stdout=quiet, stderr=quiet, close_fds=True)
    except OSError:
        pass  # the next turn tries again; `/apersona:login status` trades too


def logout(site: str) -> str:
    """Sign this machine out: the refresh token revoked on the website (RFC 7009 `/oauth2/revoke`, `client_id` and
    `token`; that signs the whole grant out, the access token with it), then the credentials file deleted, whether
    the site could be reached or not. What happened, in his words."""
    record = read_credentials()
    if not record:
        return "這台本來就沒有登入"
    refresh, client_id, where = record.get("refresh"), record.get("client_id"), record.get("site")
    said = ""
    if isinstance(refresh, str) and isinstance(client_id, str) and isinstance(where, str):
        try:
            answer = _post(checked_site(where), "/oauth2/revoke", form={"client_id": client_id, "token": refresh})
            said = "網站上已登出這台" if answer.get("status") == 200 else f"網站沒收到登出（{answer.get('status')}）"
        except LoginError:  # one exception per clause: the formatter would drop the parentheses python 3.9 needs
            said = "連不上網站，網站上請按「登出這台」"
        except ValueError:
            said = "連不上網站，網站上請按「登出這台」"
    try:
        credentials_path().unlink()
    except OSError:
        pass
    return (said + "；" if said else "") + "這台的登入檔已刪除"


def sign_out(site: str) -> int:
    """Revoke this device and remove its local credential."""
    print(logout(site))
    return 0


def connection_check(site: str) -> str:
    """One line on whether the login just saved really works: the 開場包 is asked for once with it, as the next
    conversation's SessionStart hook will. Never raises, never prints the pack or a token; a miss says what to run."""
    retry = "登入已存好；等一下執行 /apersona:login 的 status 再確認一次。"
    try:
        key = bearer(site)
    except LoginError as e:
        return f"接通實測沒過：{e}。{retry}"
    request = urllib.request.Request(
        site + "/api/memory/opening?hooks=0", headers={"Authorization": "Bearer " + key, "Accept": "application/json"}
    )
    try:
        with open_url(request, HTTP_TIMEOUT_S) as response:
            answer: object = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return f"接通實測沒過：網站回 {e.code}，開場包沒拿到。{retry}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f"接通實測沒過：開場包沒拿到（{e}）。{retry}"
    text = cast("dict[str, object]", answer).get("text") if isinstance(answer, dict) else None
    if not isinstance(text, str) or not text.strip():
        return "接通實測：連得上網站，但這個人格的開場包是空的；先用 /apersona:read 送資料。"
    return f"接通實測通過：剛用這台的登入向網站要了一次開場包，拿到 {len(text)} 個字。"


def logged_in(site: str) -> bool:
    record = read_credentials()
    return record.get("site") == site and isinstance(record.get("refresh"), str)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apersona_login.py")
    parser.add_argument("command", choices=("login", "status", "refresh", "logout"))
    parser.add_argument("--site", default="")
    args = parser.parse_args(argv)
    code = _main(args)
    if code:
        print(exit_line(code))
    return code


def _main(args: argparse.Namespace) -> int:
    try:
        site = site_of(args.site)
    except LoginError as e:
        print(str(e))
        return EXIT_FAILED
    if args.command == "logout":
        return sign_out(site)
    if args.command in ("status", "refresh"):
        if not logged_in(site):
            if args.command == "status":
                print(f"還沒登入 {site}；執行 /apersona:login")
            return EXIT_NOT_LOGGED_IN
        try:
            # status asks the site (a trade): a device signed out on the website still holds a token that looks fine
            bearer(site, trade=Trade.ALWAYS if args.command == "status" else Trade.AHEAD)
        except LoginError as e:
            if args.command == "status" and e.kind == "unreachable":
                print(f"本機有 {site} 的登入檔，但連不上網站，無法確認是否已在網站撤銷（{e}）")
                return EXIT_FAILED
            if args.command == "status":
                print(str(e))
            return EXIT_NOT_LOGGED_IN if e.kind in ("not_logged_in", "login_ended") else EXIT_FAILED
        if args.command == "status":
            print(f"已登入 {site}")
        return 0
    try:
        done = login(site, lambda line: print(line, flush=True))
    except LoginError as e:
        print(str(e))
        return EXIT_FAILED
    if done:
        print(f"登入好了（{site}）。下一個對話起，開場包就會帶本人的記憶。")
        print(f"這台的登入憑證存在 {credentials_path()}；/apersona:logout 會刪掉它並在網站登出這台。")
        print(connection_check(site))
    return 0 if done else EXIT_UNFINISHED


if __name__ == "__main__":
    utf8_pipes()
    raise SystemExit(main())
