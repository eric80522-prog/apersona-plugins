"""開讀前先問的外掛那一半 (cutting, what can be used and pricing are kept once, on the site): the plugin sends his files
as they are to the site, which cuts them and says what can be read; he says yes once; the site reads them. Nothing
leaves the machine before he has seen the list and said yes, once, to sending and reading it.

Standard library only, like the hooks: nothing of the product is installed on his machine.

- `ask <folder>`      on this machine only: walk the folder (`.gitignore`／`.apersonaignore` and always `.git`,
                      `node_modules`, `.env*` and shortcuts left out: `apersona_ignore.py`, the same rules the site's
                      own intake keeps) and print what `send` would send: each file with its size, how many and how
                      large in all, and which would not be sent and why. It asks the site only for the account's
                      上傳前換代號 (`GET /api/intake/settings`, no file in it; when the site cannot be asked, as if on:
                      寧可多遮). His home folder itself, or more than `MAX_ASK_FILES` files, stops here first and asks
                      him (`--large` once he has said yes). The list is kept (`plan.json`) for `send`.
- `send [--read]`     after his yes to the list (with `--read`: that one yes is to sending and reading together,
                      and the read is queued right after the upload, nothing more is asked): those files
                      exactly (one changed since `ask` stops it), with
                      secrets dropped from every text file on this machine first, and names, phone numbers, emails,
                      ID numbers and addresses swapped for codes only when 上傳前換代號 is on (as it was at `ask`).
                      With codes on, a .docx or .pdf is not sent (its text cannot be masked here), nor a text file
                      that is not UTF-8; with them off a .docx or .pdf goes as it is (the site drops its secrets),
                      and a text file that is not UTF-8 is read as Big5 / cp950, masked, and sent as UTF-8, or not
                      sent when it cannot be read. Each file goes up under a random name (`src-<hex>` with its
                      extension): its path stays here, in `files.json`. The files go in batches the site takes
                      (`POST /api/intake/files`, at most `MAX_FILES` files and `MAX_TOTAL_BYTES` a batch, a file over
                      `MAX_FILE_BYTES` not sent); the site keeps them prepared, waiting for his yes, reads nothing and
                      spends nothing. What it found is printed as the site's data (`fenced`), and then the plugin's
                      own sentence to agree to: 「用你的額度讀這 N 份」, on the free extraction 「用免費額度讀這 N 份」.
- `consent yes|no`    his answer to that sentence. Yes: each batch's yes (`POST /api/intake/files/consent`), and the
                      site queues the read, or says why not (the free extraction used, nothing left to read). No:
                      each batch's no (`POST /api/intake/files/decline`): nothing is read, now or later; the site
                      keeps what it prepared and never reads it (as for a batch left without a yes for 7 days).
- `status`            where each batch's read stands (`GET /api/intake/files`).
- `cancel`            the parts of the batches he agreed to that are not read yet are not read (`POST
                      /api/intake/cancel`); what went up is kept (取消是「這次先不要」). Which batches count as agreed
                      to is asked of the site (`GET /api/intake/files`), so a yes the site took but whose reply never
                      reached this machine is cancelled too; `status` reads the site the same way.

The work folder is `~/.apersona/intake/<hash of the folder's path>/` (700, its files 600), never inside the material
folder, which may be synced to a cloud drive or committed to git. The code table (`aliases.json`) is never sent.

A failure is 暫時 (no connection, 5xx, 429: the same request again, MAX_RETRIES times at most, waiting `retry_after`
or BACKOFF_S) or 永久 (the rest: it stops, says why and what to do). Exit codes: 3 stopped for good, 5 still failing
after the retries (run it again later), 6 stopped to ask him first (his home folder, or too many files).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from functools import partial
from io import TextIOWrapper
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apersona_ignore import Walk  # noqa: E402
from apersona_mask import Masker, redact_json, redact_secrets  # noqa: E402
from apersona_safe import (  # noqa: E402
    EXIT_ASK_FIRST,
    EXIT_PAUSED,
    EXIT_REFUSED,
    exit_line,
    fenced,
    open_url,
    private_dir,
)

FILES_PATH = "/api/intake/files"
FILES_CONSENT_PATH = "/api/intake/files/consent"
FILES_DECLINE_PATH = "/api/intake/files/decline"
#: what the upload step does, in the site's own words
PREPARED = "先送上來準備，同意後才讀；不同意就不讀（7 天沒回答也當作不讀）。"
SEGMENTS_PATH = "/api/intake/segments"
CANCEL_PATH = "/api/intake/cancel"
SETTINGS_PATH = "/api/intake/settings"
#: what the site takes in one request: more goes in another batch
MAX_FILES = 100
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
#: the kinds the site reads; the text ones are masked here
SUFFIXES = frozenset({".md", ".txt", ".docx", ".pdf", ".csv", ".json", ".jsonl"})
TEXT_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json", ".jsonl"})
TIMEOUT_S = 120
MAX_RETRIES = 3
BACKOFF_S = (1, 2, 4)
RETRY_AFTER_MAX_S = 120
TEMPORARY_ERRORS = ("rate_limited", "unavailable")
#: more files than this in one `ask` stops to ask him first
MAX_ASK_FILES = 200
#: what a text file that is not UTF-8 is tried as, with codes off (Taiwan's older Windows notes)
LEGACY_ENCODINGS = ("big5", "cp950")
TOO_LARGE_HERE = f"檔案太大（一份最多 {MAX_FILE_BYTES // (1024 * 1024)} MB），這份沒送"
NOT_UTF8_HERE = "不是 UTF-8 文字，這份沒送；先另存成 UTF-8 再送"
MASK_FAILED_HERE = "這份在本機遮不了秘密，沒送；先另存成 UTF-8 的 .txt 或 .md 再送"
LINKED_HERE = "這是捷徑（連結），不追過去讀，這份沒送"
NO_MASK_HERE = "「上傳前換代號」開著，這種檔在本機遮不了身分，這份沒送；要讀就先另存成 .txt 或 .md 再送"


class RefusedError(Exception):
    "Why nothing (more) was done, in the owner's words. Stopped for good: running it again does not help."


class PausedError(RefusedError):
    "A 暫時 failure that outlasted the retries: run it again later."


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError:  # one exception per clause: the plugin runs on Python 3.9
        return None


def write_json(path: Path, value: object) -> None:
    "The file written whole and put in place at once, made 600 from the start (never readable by others meanwhile)."
    temporary = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False))
    if sys.platform != "win32":
        os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))


def work_for(folder: Path) -> Path:
    "This folder's work folder: under `~/.apersona/intake/`, named by a hash of its full path, made 700."
    name = hashlib.sha256(str(folder.expanduser().resolve()).encode("utf-8")).hexdigest()[:16]
    return private_dir(Path.home() / ".apersona" / "intake" / name)


def options() -> tuple[str, str, Callable[[], str]]:
    """The site, the persona to name (always empty: the site takes the login's own) and the bearer of the device login
    `/apersona:login` left, fetched again for each request (an access token lasts an hour and a big folder takes
    longer; this is no hook, so an ended one is traded here). An old key in the plugin's options is never read."""
    import apersona_login  # beside this file

    try:
        where = apersona_login.site_of()
    except apersona_login.LoginError as e:
        raise RefusedError(str(e)) from None
    if not apersona_login.logged_in(where):
        raise RefusedError("還沒登入：先執行 /apersona:login")
    return where, "", partial(apersona_login.bearer, where)


def who(persona: str) -> dict[str, str]:
    return {"personaId": persona} if persona else {}


def _call(request: urllib.request.Request) -> dict[str, object]:
    with open_url(request, TIMEOUT_S) as response:
        raw = response.read()
    try:
        answer: object = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        answer = {}
    return cast("dict[str, object]", answer) if isinstance(answer, dict) else {}


def _headers(key: Callable[[], str] | str, **more: str) -> dict[str, str]:
    if callable(key):
        import apersona_login

        try:
            key = key()
        except apersona_login.LoginError as e:
            raise RefusedError(str(e)) from None
    return {"Authorization": "Bearer " + key, "Accept": "application/json", **more}


def get(site: str, path: str, key: Callable[[], str] | str, query: dict[str, str]) -> dict[str, object]:
    url = site + path + "?" + urllib.parse.urlencode(query)
    return _call(urllib.request.Request(url, headers=_headers(key), method="GET"))


def post_json(site: str, path: str, key: Callable[[], str] | str, body: dict[str, object]) -> dict[str, object]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = _headers(key, **{"Content-Type": "application/json"})
    return _call(urllib.request.Request(site + path, data=data, headers=headers, method="POST"))


def multipart(fields: list[tuple[str, str]], files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    "A multipart/form-data body (RFC 7578) of text fields and `files` parts, and its Content-Type."
    boundary = "apersona-" + uuid.uuid4().hex
    out: list[bytes] = []
    for name, value in fields:
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.append(value.encode("utf-8") + b"\r\n")
    for filename, data in files:
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        )
        out += [head.encode(), data, b"\r\n"]
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def post_files(
    site: str, key: Callable[[], str] | str, persona: str, files: list[tuple[str, bytes]], me: list[str]
) -> dict[str, object]:
    body, kind = multipart([*who(persona).items(), *(("me", m) for m in me)], files)
    request = urllib.request.Request(
        site + FILES_PATH, data=body, headers=_headers(key, **{"Content-Type": kind}), method="POST"
    )
    return _call(request)


class Failure:
    "One failed request, sorted: 暫時 (worth sending again) or 永久, with what the site said."

    def __init__(self, e: OSError) -> None:
        self.status, self.code, self.said = 0, "", ""
        self.retry_after: float | None = None
        header: str | None = None
        if isinstance(e, urllib.error.HTTPError):
            self.status = e.code
            header = e.headers.get("Retry-After") if e.headers else None
            try:
                answer: object = json.loads(e.read().decode("utf-8"))
            except ValueError:  # one exception per clause: the plugin runs on Python 3.9
                answer = None
            except OSError:
                answer = None
            fields = cast("dict[str, object]", answer) if isinstance(answer, dict) else {}
            code, said, wait = fields.get("error"), fields.get("message"), fields.get("retry_after")
            self.code = code if isinstance(code, str) else ""
            self.said = said if isinstance(said, str) else ""
            for given in (wait, header):
                if isinstance(given, (int, float)) and not isinstance(given, bool) and given >= 0:
                    self.retry_after = float(given)
                    break
                if isinstance(given, str) and given.strip().isdigit():
                    self.retry_after = float(given.strip())
                    break
        answered = isinstance(e, urllib.error.HTTPError)
        self.temporary = not answered or self.code in TEMPORARY_ERRORS or self.status == 429 or self.status >= 500
        self.what = type(e).__name__


def stop_for_good(f: Failure) -> str:
    "Why a 永久 refusal will not get better by running it again, and what to do instead, in the owner's words."
    said = "\n" + fenced(f.said) if f.said else ""
    if f.status == 401:
        return "停下，不用再試：登入失效了；執行 /apersona:login 重新登入（舊金鑰就到網站設定頁重發一把）" + said
    if f.status == 403:
        return "停下，不用再試：網站不認這個同意（上傳的內容跟同意的那一句對不上）；重跑 ask 再問本人" + said
    if f.status == 404:
        return (
            "停下，不用再試：網站找不到這個人格或這次上傳，或這個登入開不了它；"
            "重新執行 /apersona:login（舊金鑰就檢查 persona 與金鑰）" + said
        )
    if f.status == 409:
        return "停下：網站說這次上傳現在不能這樣做" + said
    if f.status == 413:
        return "停下：這一批太大，網站不收；把資料夾分小一點再送" + said
    return f"停下，不用再試：網站回了 {f.status}，不是等一下就會好的錯誤；把這句話回報給開發者" + said


def patiently(call: Callable[[], dict[str, object]], what: str) -> dict[str, object]:
    """`call` once, and again on a 暫時 failure after the time the site names or BACKOFF_S, MAX_RETRIES times at most;
    a 永久 one stops at once."""
    scale = float(os.environ.get("APERSONA_INTAKE_WAIT_SCALE", "1"))  # tests set 0
    attempt = 0
    while True:
        try:
            return call()
        except OSError as e:
            f = Failure(e)
            if not f.temporary:
                raise RefusedError(stop_for_good(f)) from None
            wait = f.retry_after if f.retry_after is not None else float(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
            if attempt >= MAX_RETRIES or wait > RETRY_AFTER_MAX_S:
                reason = f"網站要你等 {int(wait)} 秒" if wait > RETRY_AFTER_MAX_S else f"試了 {attempt + 1} 次都沒成功"
                raise PausedError(
                    f"{what}暫停（{f.status or f.what}，{reason}）；這是暫時的，過一會兒再跑一次"
                ) from None
            print(f"暫時連不上（{f.status or f.what}），{int(wait)} 秒後再試（第 {attempt + 1}/{MAX_RETRIES} 次）")
            time.sleep(wait * scale)
            attempt += 1


def codes_on(site: str, persona: str, key: Callable[[], str] | str) -> bool:
    "上傳前換代號 as the site says now; on when it cannot be asked (寧可多遮), and he is told which."
    try:
        found = get(site, SETTINGS_PATH, key, who(persona)).get("aliasCodes")
    except OSError:
        found = None
    if not isinstance(found, bool):
        print(
            "問不到網站上帳號的「上傳前換代號」設定，這次照開著處理：人名、電話、Email、身分證字號、地址換成代號再上傳"
        )
        return True
    if found:
        print("帳號的「上傳前換代號」開著：人名、電話、Email、身分證字號、地址在這台電腦上換成代號再上傳")
    else:
        print("帳號的「上傳前換代號」關著：檔案原樣上傳、原樣送去讀（金鑰、密碼這類秘密一律先在這台電腦上遮掉）")
    return found


def read_bytes(path: Path) -> bytes:
    "The file's bytes, never through a shortcut (symlink): one put there after the folder was walked is refused too."
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read(MAX_FILE_BYTES + 1)


def prepared(path: Path, masker: Masker | None, names: list[str]) -> tuple[bytes | None, str]:
    """The bytes to send of one file and, when it is not sent, why: a text file with its secrets dropped (and codes
    swapped when `masker`); a .docx or .pdf as it is, or not at all with codes on."""
    try:
        if path.is_symlink():
            return None, LINKED_HERE
        size = path.stat().st_size
    except OSError:
        return None, "讀不到這份檔"
    if size > MAX_FILE_BYTES:
        return None, TOO_LARGE_HERE
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES and masker is not None:
        return None, NO_MASK_HERE
    try:
        raw = read_bytes(path)
    except OSError:
        return None, "讀不到這份檔（或它是捷徑）"
    if len(raw) > MAX_FILE_BYTES:
        return None, TOO_LARGE_HERE
    if suffix not in TEXT_SUFFIXES:
        return raw, ""
    text = decoded(raw, legacy=masker is None)
    if text is None:
        return None, NOT_UTF8_HERE

    def mask(s: str) -> str:
        return masker.mask(s, names) if masker is not None else redact_secrets(s)

    try:
        if suffix == ".json":
            try:
                return json.dumps(redact_json(json.loads(text), mask), ensure_ascii=False).encode("utf-8"), ""
            except ValueError:
                pass
        if suffix == ".jsonl":  # one JSON object per line: each line masked as JSON, a line that is not stays text
            lines: list[str] = []
            for line in text.split("\n"):
                try:
                    lines.append(json.dumps(redact_json(json.loads(line), mask), ensure_ascii=False))
                except ValueError:
                    lines.append(mask(line))
            return "\n".join(lines).encode("utf-8"), ""
        return mask(text).encode("utf-8"), ""
    except Exception:  # noqa: BLE001  whatever went wrong, a file not masked is never sent
        return None, MASK_FAILED_HERE


def decoded(raw: bytes, legacy: bool) -> str | None:
    "The text of a file: UTF-8, else (when `legacy`) Big5 or cp950; None when it is none of them."
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if not legacy:
        return None
    for encoding in LEGACY_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _text(path: Path) -> str:
    try:
        return read_bytes(path).decode("utf-8")
    except OSError:
        return ""
    except UnicodeDecodeError:  # one exception per clause: the plugin runs on Python 3.9
        return ""


def batches(files: list[tuple[str, bytes]]) -> list[list[tuple[str, bytes]]]:
    "The files in batches the site takes in one request."
    out: list[list[tuple[str, bytes]]] = []
    now: list[tuple[str, bytes]] = []
    size = 0
    for one in files:
        if now and (len(now) >= MAX_FILES or size + len(one[1]) > MAX_TOTAL_BYTES):
            out.append(now)
            now, size = [], 0
        now.append(one)
        size += len(one[1])
    if now:
        out.append(now)
    return out


def size_text(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def digest(path: Path) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


def too_broad(folder: Path, count: int) -> str:
    "Why this folder stops to ask him first, or empty: his home folder itself, the disk's root, or too many files."
    where = folder.resolve()
    if where == Path.home().resolve() or where.parent == where:
        return f"這是整個{'家目錄' if where.parent != where else '磁碟'}（{where}），裡面可能有很多不是素材的東西"
    if count > MAX_ASK_FILES:
        return f"有 {count} 份檔（超過 {MAX_ASK_FILES} 份）"
    return ""


def ask(folder: Path, work: Path, large: bool) -> int:
    "`ask`: what `send` would send, listed here; nothing of the files leaves the machine."
    site, persona, key = options()
    if not folder.is_dir():
        raise RefusedError(f"找不到素材資料夾：{folder}")
    private_dir(work)
    on = codes_on(site, persona, key)
    walk = Walk(folder, SUFFIXES)
    broad = too_broad(folder, len(walk.files))
    if broad and not large:
        print(f"先停下來問本人：{broad}。確定要整個送嗎？")
        print("本人說確定，才加 --large 再跑一次 ask；不然請挑一個只放素材的資料夾。")
        return EXIT_ASK_FIRST
    probe = Masker([], []) if on else None  # only to learn which files could not go; nothing is coded or kept
    rows: list[dict[str, object]] = []
    held: list[tuple[str, str]] = []
    for relative in walk.files:
        path = folder / relative
        _, why = prepared(path, probe, [])
        if why:
            held.append((relative, why))
            continue
        rows.append({"path": relative, "size": path.stat().st_size, "sha256": digest(path)})
    write_json(
        work / "plan.json",
        {"folder": str(folder.resolve()), "codes": on, "files": rows, "held": held, "left_out": walk.left_out},
    )
    total = sum(int(cast(int, r["size"])) for r in rows)
    lines = [f"還沒送出任何東西。本人同意後，會把這 {len(rows)} 份（共 {size_text(total)}）送到 {site}："]
    lines += [f"- {r['path']}（{size_text(int(cast(int, r['size'])))}）" for r in rows]
    lines += [f"- 不送：{path}：{why}" for path, why in held]
    if walk.left_out:
        lines.append(f"照 .gitignore／.apersonaignore、捷徑與一律不讀的檔，排除了 {walk.left_out} 份，不送")
    lines.append(f"本機工作資料夾：{work}")
    if rows:
        lines.append(
            "要把這幾份送上網站，並讓網站接著讀進人格嗎？（讀取先用免費提取額度，用完才用你的額度；"
            "花費不超過同意上限。）這是唯一要問的一句，之後不會再問。"
            "本人說好，才執行 send --read；說不要就什麼都不送、不讀。"
        )
    else:
        lines.append("沒有要送的檔。")
    print("\n".join(lines))
    return 0


def send(work: Path, names_file: Path | None, me: list[str], read: bool = False) -> int:
    """`send`: the files `ask` listed, exactly (masked here as the switch says), and what the site found.
    With `read` his one yes to `ask`'s sentence covered the read too: the site is told yes for every batch right
    after the upload, and no second sentence is put to him."""
    site, persona, key = options()
    found = read_json(work / "plan.json")
    plan = cast("dict[str, object]", found) if isinstance(found, dict) else {}
    if not plan.get("files"):
        raise RefusedError("還沒有要送的清單：先跑 ask，給本人看過再 send")
    folder = Path(str(plan["folder"]))
    on = codes_on(site, persona, key)
    if on != plan.get("codes"):
        raise RefusedError("「上傳前換代號」在 ask 之後改了；重跑 ask，再給本人看一次清單")
    planned = cast("list[dict[str, object]]", plan["files"])
    for row in planned:
        try:
            same = digest(folder / str(row["path"])) == row["sha256"]
        except OSError:
            same = False
        if not same:
            raise RefusedError(f"{row['path']} 在 ask 之後改過或不見了；重跑 ask，再給本人看一次清單")
    if names_file is None and (work / "names.txt").is_file():
        names_file = work / "names.txt"
    names = []
    if names_file is not None:
        names = [n.strip() for n in names_file.read_text(encoding="utf-8").splitlines() if n.strip()]
    stored = read_json(work / "aliases.json")
    table = cast("dict[str, object]", stored) if isinstance(stored, dict) else {}
    entries = [(e[0], e[1], e[2]) for e in cast("list[list[str]]", table.get("entries") or []) if len(e) == 3]
    relatives = [str(r["path"]) for r in planned]
    masker: Masker | None = None
    if on:  # a code must not already be in the material, or the way back would be ambiguous
        every = [_text(folder / r) for r in relatives if Path(r).suffix.lower() in TEXT_SUFFIXES]
        masker = Masker(entries, [*every, *names])
    local: dict[str, str] = {}  # the name it goes up under -> its path here
    held = [(str(h[0]), str(h[1])) for h in cast("list[list[str]]", plan.get("held") or [])]
    sending: list[tuple[str, bytes]] = []
    for relative in relatives:
        data, why = prepared(folder / relative, masker, names)
        if data is None:
            held.append((relative, why))
            continue
        code = f"src-{secrets.token_hex(6)}{Path(relative).suffix.lower()}"
        local[code] = relative
        sending.append((code, data))
    if masker is not None:
        table["entries"] = [list(e) for e in masker.entries]
        write_json(work / "aliases.json", table)
    write_json(work / "files.json", local)
    answers: list[dict[str, object]] = []
    groups = batches(sending)
    for i, group in enumerate(groups, 1):
        if len(groups) > 1:
            print(f"上傳第 {i} 批／共 {len(groups)} 批")
        answers.append(patiently(partial(post_files, site, key, persona, group, me), "上傳"))
    write_json(work / "uploads.json", {"answers": answers, "consent": None})
    print(said(answers, local, held, int(cast(int, plan.get("left_out") or 0)), asking=not read))
    if read and any(a.get("consentSha256") for a in answers):
        return consent(work, True)
    return 0


def said(
    answers: list[dict[str, object]],
    local: dict[str, str],
    held: list[tuple[str, str]],
    left_out: int,
    asking: bool = True,
) -> str:
    """What the site found, summed over the batches, each file by its path here, as the site's data; then the
    plugin's own sentence to agree to (never the site's words: what he is asked to say is not up to the site)."""
    files = [cast("dict[str, object]", f) for a in answers for f in cast("list[object]", a.get("files") or [])]
    readable = sum(int(cast(int, a.get("readableParts") or 0)) for a in answers)
    why: dict[str, int] = {}
    for a in answers:
        for row in cast("list[dict[str, object]]", a.get("unreadableParts") or []):
            why[str(row.get("reason"))] = why.get(str(row.get("reason")), 0) + int(cast(int, row.get("count") or 0))
    found = [f"收到 {len(files)} 份，{readable} 段能讀"]
    if why:
        found[0] += f"，{sum(why.values())} 段讀不了（" + "、".join(f"{k} {n}" for k, n in why.items()) + "）"
    for f in files:
        name = local.get(str(f.get("name")), "（不明的檔）")
        found.append(f"- {name}：{f.get('reason') or str(f.get('usable')) + ' 段能讀'}")
    agreeable = [a for a in answers if a.get("consentSha256")]
    found += [
        str(a.get("estimateWords") or a.get("free")) for a in agreeable[:1] if a.get("estimateWords") or a.get("free")
    ]
    lines = [fenced("\n".join(found))]
    if local:
        skipped = f"，沒送 {len(held)} 份" if held else ""
        lines.append(f"本機送出 {len(local)} 份{skipped}；網站上看到的檔名是隨機的，對照如下：")
        lines += [f"- {name} ＝ {path}" for name, path in sorted(local.items(), key=lambda item: item[1])]
        if files and len(files) != len(local):
            lines.append(f"注意：本機送出 {len(local)} 份，網站只回報收到 {len(files)} 份；對不上的請回報給開發者")
    lines += [f"- 沒送：{path}：{reason}" for path, reason in held]
    if left_out:
        lines.append(f"照 .gitignore／.apersonaignore、捷徑與一律不讀的檔，排除了 {left_out} 份，沒有送")
    if agreeable and not asking:
        lines.append("你已經在送出前同意送與讀，不再問第二次。")
    elif agreeable:
        n = sum(1 for f in files if not f.get("reason"))
        lines.append(PREPARED if readable else "同意後只補登完整訊息觀測，不重新讀取、不花額度；不同意不補登。")
        action = "要開始讀" if readable else "要補登觀測"
        lines.append(f"{action}，請本人親口說同意：「{question(agreeable, n)}」")
    else:
        lines.append("這次沒有能讀的段落，不用同意。")
    return "\n".join(lines)


def _on_free(answer: dict[str, object]) -> bool:
    "Whether the site says this batch's yes is on the free extraction: its `onFree`, else (older site) its sentence."
    if "onFree" in answer:
        return answer["onFree"] is True
    return str(answer.get("question") or "").startswith("用免費額度")


def question(agreeable: list[dict[str, object]], n: int) -> str:
    "The one sentence for every batch, made here from the count: on the free extraction when the site says so."
    metadata = sum(int(cast(int, a.get("metadataParts") or 0)) for a in agreeable)
    readable = sum(int(cast(int, a.get("readableParts") or 0)) for a in agreeable)
    if metadata and not readable:
        return f"補登這 {metadata} 份完整訊息觀測，不重新讀取、不花額度"
    free = all(_on_free(a) for a in agreeable)
    answer = f"用免費額度讀這 {n} 份" if free else f"用你的額度讀這 {n} 份"
    return answer + (f"；另補登 {metadata} 份完整訊息觀測，不重讀、不花額度" if metadata else "")


def _uploads(work: Path) -> dict[str, object]:
    found = read_json(work / "uploads.json")
    record = cast("dict[str, object]", found) if isinstance(found, dict) else {}
    if not record.get("answers"):
        raise RefusedError("還沒有送過檔：先跑 ask")
    return record


def consent(work: Path, yes: bool) -> int:
    "`consent yes|no`: his answer to the sentence, for every batch of the last `ask`."
    site, persona, key = options()
    record = _uploads(work)
    answers = cast("list[dict[str, object]]", record["answers"])
    if not yes:
        said_last = ""
        for a in answers:
            if not a.get("consentSha256"):
                continue
            body = {**who(persona), "jobId": a["jobId"]}
            said_last = str(patiently(partial(post_json, site, FILES_DECLINE_PATH, key, body), "不同意").get("message"))
        record["consent"] = False
        write_json(work / "uploads.json", record)
        print(fenced(said_last) if said_last else "沒同意：什麼都不讀、不花錢")
        return 0
    agreed: list[str] = []
    for a in answers:
        if not a.get("consentSha256"):
            continue
        body: dict[str, object] = {**who(persona), "jobId": a["jobId"], "consentSha256": a["consentSha256"]}
        reply = patiently(partial(post_json, site, FILES_CONSENT_PATH, key, body), "同意")
        if reply.get("message"):
            print(fenced(str(reply["message"])))
        if reply.get("queued"):
            agreed.append(str(a["jobId"]))
    record["consent"] = True
    record["agreed"] = agreed
    write_json(work / "uploads.json", record)
    if agreed:
        print("進度：" + "、".join(agreed) + "（用 status 看，或在網站的「讀取進度」）")
    return 0


def status(work: Path) -> int:
    "`status`: where each batch's read stands, and which file is which under the random names the site shows."
    site, persona, key = options()
    for a in cast("list[dict[str, object]]", _uploads(work)["answers"]):
        query = {**who(persona), "job": str(a["jobId"])}
        now = patiently(partial(get, site, FILES_PATH, key, query), "查進度")
        if not now.get("agreed"):
            print(fenced(f"{a['jobId']}：還沒同意"))
        else:
            print(fenced(f"{a['jobId']}：{now.get('state')}，已讀 {now.get('done')}／{now.get('total')} 段"))
    mapping = read_json(work / "files.json")
    if isinstance(mapping, dict) and mapping:
        print("檔名對照（網站上看到的名字 ＝ 本機檔）：")
        for name, path in sorted(cast("dict[str, object]", mapping).items(), key=lambda item: str(item[1])):
            print(f"- {name} ＝ {path}")
    return 0


def agreed_on_site(
    site: str, persona: str, key: Callable[[], str] | str, answers: list[dict[str, object]]
) -> list[str]:
    """The batches of the last `ask` the site holds as agreed to, asked of the site itself: a yes whose reply was lost
    on the way here (timeout, dropped connection) still counts there, and what this machine wrote down does not."""
    agreed: list[str] = []
    for a in answers:
        query = {**who(persona), "job": str(a["jobId"])}
        if patiently(partial(get, site, FILES_PATH, key, query), "查進度").get("agreed"):
            agreed.append(str(a["jobId"]))
    return agreed


def cancel(work: Path) -> int:
    """`cancel`: what he agreed to and is not read yet is not read; what went up stays. Which batches he agreed to is
    the site's record, not this machine's: a yes the site took but whose reply never arrived is cancelled too."""
    site, persona, key = options()
    record = _uploads(work)
    said_last = ""
    for job in agreed_on_site(site, persona, key, cast("list[dict[str, object]]", record["answers"])):
        query = {**who(persona), "job": job}
        prints: object = patiently(partial(get, site, SEGMENTS_PATH, key, query), "取消").get("fingerprints") or []
        body: dict[str, object] = {**who(persona), "jobId": job, "fingerprints": prints}
        said_last = str(patiently(partial(post_json, site, CANCEL_PATH, key, body), "取消").get("message") or "")
    record["consent"] = False
    record["agreed"] = []
    write_json(work / "uploads.json", record)
    print(fenced(said_last) if said_last else "沒有正在讀的；送上去的檔沒有同意就不會讀")
    return 0


def main(argv: list[str] | None = None) -> int:
    code = _main(argv)
    if code:
        print(exit_line(code))
    return code


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apersona_intake.py")
    parser.add_argument("--folder", type=Path, help="素材資料夾（send、consent、status、cancel 用來找它的工作資料夾）")
    parser.add_argument("--work", type=Path, help="改用別的本機工作資料夾（預設 ~/.apersona/intake/<資料夾雜湊>）")
    commands = parser.add_subparsers(dest="command", required=True)
    asking = commands.add_parser("ask", help="只在本機列出要送哪些檔、幾份、多大；什麼都不送")
    asking.add_argument("folder", type=Path)
    asking.add_argument("--large", action="store_true", help="本人確定要送整個家目錄或超過 200 份時才加")
    sending = commands.add_parser("send", help="本人看過清單說好之後：送 ask 列的那些檔上網站切段")
    sending.add_argument("--names-file", type=Path, help="上傳前換代號開著時要換成代號的人名，一行一個")
    sending.add_argument(
        "--read", action="store_true", help="本人對 ask 那一句（送與讀）說好：送完直接排進讀取，不再問"
    )
    sending.add_argument("--me", action="append", default=[], help="聊天匯出檔裡本人的名字或帳號 ID（可多個）")
    answer = commands.add_parser("consent")
    answer.add_argument("answer", choices=("yes", "no"))
    commands.add_parser("status")
    commands.add_parser("cancel")
    args = parser.parse_args(argv)
    try:
        folder: Path | None = args.folder
        if args.work is not None:
            work: Path = args.work
        elif folder is not None:
            work = work_for(folder)
        else:
            raise RefusedError("要給素材資料夾：--folder <資料夾>")
        if args.command == "ask":
            assert folder is not None  # the subcommand's own argument
            return ask(folder, work, bool(args.large))
        if args.command == "send":
            return send(work, args.names_file, list(args.me), bool(args.read))
        if args.command == "consent":
            return consent(work, args.answer == "yes")
        if args.command == "status":
            return status(work)
        return cancel(work)
    except PausedError as e:
        print(str(e))
        return EXIT_PAUSED
    except RefusedError as e:
        print(str(e))
        return EXIT_REFUSED
    except OSError as e:
        print(f"連不上網站或讀不到檔（{type(e).__name__}）；過一會兒再跑一次")
        return EXIT_PAUSED


if __name__ == "__main__":
    # AI clients consume the CLI's pipes as UTF-8, including on Windows with Python 3.9's legacy locale.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
