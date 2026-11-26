"""開讀前先問的外掛那一半 (cutting, what can be used and pricing are kept once, on the site): the plugin sends his files
as they are to the site, which cuts them and says what can be read; he says yes once; the site reads them. Nothing
leaves the machine before he has seen the list and said yes, once, to sending and reading it.

Standard library only, like the hooks: nothing of the product is installed on his machine.

- `ask <folder>`      on this machine only: walk the folder (`.gitignore`／`.apersonaignore` and always `.git`,
                      `node_modules`, `.env*` and shortcuts left out: `apersona_ignore.py`, the same rules the site's
                      own intake keeps) and print what `send` would send: each file with its size, how many and how
                      large in all, and which would not be sent and why. It asks the site nothing. His home folder
                      itself, or more than `MAX_ASK_FILES` files, stops here first and asks him (`--large` once he has
                      said yes). The list is kept (`plan.json`) for `send`.
- `--ai ask`          the same, for his AI conversation records on this machine instead of a folder, found in one go:
                      Claude Code's main conversations (`claude_conversations`) and the Codex
                      sessions a person typed in (`~/.codex/sessions`, by the rollout's first line,
                      `apersona_hook.session_source`; `codex exec` and subagents are not sent), each as it is; an
                      `.apersonaignore` in either place is kept. Printed as how many and how large per tool, not file
                      by file. `--ai` then stands for the folder in every command below.
- `send`              after his yes to sending the list: those files exactly (one changed since `ask` stops it), with
                      secrets dropped from every text file on this machine first; every other word goes as written
                      (上傳前換代號 was retired). A .docx or .pdf goes as it is (the site drops its secrets), and a
                      text file that is not UTF-8 is read as Big5 / cp950, masked, and sent as UTF-8, or not sent when
                      it cannot be read. The site keeps the whole text of each file it reads (secrets dropped) and
                      reads only its cut parts. Each file goes up under a random name (`src-<hex>` with its
                      extension): its path stays here, in `files.json`; what the path says that the site reads
                      by goes with it instead (`origins`: a Slack day's 對話種類 by its folder, a LinkedIn export or
                      résumé by its name), never the path or the name itself. The files go in batches the site takes
                      (`POST /api/intake/files`, at most `MAX_FILES` files and `MAX_TOTAL_BYTES` a batch, a file over
                      `MAX_FILE_BYTES` not sent); the site keeps them prepared, waiting for his yes, reads nothing and
                      spends nothing. What it found is printed as the site's data (`fenced`), and then the site's
                      sentence to agree to, word for word: 「讀這 N 份，預估 US$X，最多 US$Y」, with its
                      confirmation code. Reading spends money, so its yes is his own, after he has seen the amounts:
                      the plugin never says it for him.
                      A part he deleted on the site and sends again is not taken (刪過的內容，這次先不收): the files
                      are named, with how to take one back.
- `send --take-deleted <path>`  his yes for that file of the list (one flag a file): only those files go, and
                      the parts of them he deleted are taken as new sources (read on his yes to the sentence).
- `consent yes --words "<his sentence>"|no`  his answer to that sentence. Yes: each batch's yes with his own words
                      (`POST /api/intake/files/consent`, `ownerReply`: the site takes it only when they carry the
                      sentence's confirmation code, 跨模組 11-11), and the site queues the read, or says why not
                      (only test accounts are read, nothing left to read; 「今天已經花了…要繼續嗎？」 answered
                      with `--keep-going`). No:
                      each batch's no (`POST /api/intake/files/decline`): nothing is read, now or later; the site
                      keeps what it prepared and never reads it (as for a batch left without a yes for 7 days).
- `status`            where each batch's read stands (`GET /api/intake/files`).
- `cancel`            the parts of the batches he agreed to that are not read yet are not read (`POST
                      /api/intake/cancel`); what went up is kept (取消是「這次先不要」). Which batches count as agreed
                      to is asked of the site (`GET /api/intake/files`), so a yes the site took but whose reply never
                      reached this machine is cancelled too; `status` reads the site the same way.

The work folder is `~/.apersona/intake/<hash of the folder's path>/` (700, its files 600), never inside the material
folder, which may be synced to a cloud drive or committed to git.

A failure is 暫時 (no connection, 5xx, 429: the same request again, MAX_RETRIES times at most, waiting `retry_after`
or BACKOFF_S) or 永久 (the rest: it stops, says why and what to do). Exit codes: 3 stopped for good, 5 still failing
after the retries (run it again later), 6 stopped to ask him first (his home folder, or too many files).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from decimal import ROUND_CEILING, Decimal
from functools import partial
from io import TextIOWrapper
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apersona_ignore import Walk, unread_sentence  # noqa: E402
from apersona_mask import redact_json, redact_secrets  # noqa: E402
from apersona_projects import with_project  # noqa: E402
from apersona_safe import (  # noqa: E402
    EXIT_ASK_FIRST,
    EXIT_PAUSED,
    EXIT_REFUSED,
    exit_line,
    fenced,
    open_url,
    private_dir,
    read_json,
)

FILES_PATH = "/api/intake/files"
FILES_CONSENT_PATH = "/api/intake/files/consent"
FILES_DECLINE_PATH = "/api/intake/files/decline"
#: what the upload step does, in the site's own words
PREPARED = "先送上來準備，同意後才讀；不同意就不讀（7 天沒回答也當作不讀）。"
SEGMENTS_PATH = "/api/intake/segments"
CANCEL_PATH = "/api/intake/cancel"
#: what the site takes in one request: more goes in another batch
MAX_FILES = 100
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
#: the kinds the site reads; the text ones are masked here
SUFFIXES = frozenset({".md", ".txt", ".docx", ".pdf", ".csv", ".json", ".jsonl", ".mbox", ".eml"})
TEXT_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json", ".jsonl", ".mbox", ".eml"})
TIMEOUT_S = 120
MAX_RETRIES = 3
BACKOFF_S = (1, 2, 4)
RETRY_AFTER_MAX_S = 120
TEMPORARY_ERRORS = ("rate_limited", "unavailable")
#: more files than this in one `ask` stops to ask him first
MAX_ASK_FILES = 200
#: what a text file that is not UTF-8 is tried as (Taiwan's older Windows notes)
LEGACY_ENCODINGS = ("big5", "cp950")
TOO_LARGE_HERE = f"檔案太大（一份最多 {MAX_FILE_BYTES // (1024 * 1024)} MB），這份沒送"
NOT_UTF8_HERE = "不是 UTF-8、Big5 文字，這份沒送；先另存成 UTF-8 再送"
MASK_FAILED_HERE = "這份在本機遮不了秘密，沒送；先另存成 UTF-8 的 .txt 或 .md 再送"
LINKED_HERE = "這是捷徑（連結），不追過去讀，這份沒送"
#: a Slack export keeps each conversation's days (`2024-02-14.json`) in a folder: a direct message's is named by its
#: id (D…, upper case), a channel's by its name, a group DM's `mpdm-…`. The folder never goes up, so what it says goes
#: with the file as its 對話種類 (`origins`, the site's `FileOrigin.chat_kind`)
SLACK_DAY = r"\d{4}-\d{2}-\d{2}\.json"
SLACK_DM_FOLDER = r"D[A-Z0-9]{8,}"
#: a file whose own name says it is his LinkedIn export or résumé, the site's own words
#: (`intake.planning.OWN_RECORD_NAME`): the name never goes up, so it goes with the file as `ownRecord`
OWN_RECORD_NAME = r"linkedin|r[ée]sum[ée]|(?<![a-z])cv(?![a-z])|履歷|履历|简历|自傳|自传"
#: what he says a batch of his own writing is for, the site's own list (`memory.types.MATERIAL_PURPOSES`): article
#: 我的文章, about_me 關於我, rules 我的規則. Sent as 我寫的 with that 用途, so what is recalled of it says which
PURPOSES = ("article", "about_me", "rules")


class RefusedError(Exception):
    "Why nothing (more) was done, in the owner's words. Stopped for good: running it again does not help."


class PausedError(RefusedError):
    "A 暫時 failure that outlasted the retries: run it again later."


def write_json(path: Path, value: object) -> None:
    "The file written whole and put in place at once, made 600 from the start (never readable by others meanwhile)."
    temporary = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False))
    if sys.platform != "win32":
        os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))


def claude_conversations() -> tuple[list[Path], int, list[tuple[str, str]]]:
    """The main conversations Claude Code keeps on this machine, how many an `.apersonaignore` there (or the
    always-excluded names) left out, and the folders (in full, why) left out whole because their rule file could not
    be used; a subagent's file lives one folder deeper and is not one."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    walk = Walk(root, {".jsonl"})
    unread = [(str(root if folder == "." else root / folder), why) for folder, why in walk.rules_unread]
    return [root / relative for relative in walk.files if relative.count("/") == 1], walk.left_out, unread


def ai_records() -> tuple[list[tuple[str, Path]], list[tuple[str, str]], int, list[tuple[str, str]]]:
    """His AI conversation records on this machine: (which tool, file) for each to send, (file, why) for each that is
    not, how many the ignore rules left out, and the folders (in full, why) left out whole because their rule file
    could not be used."""
    import apersona_codex  # beside this file: where Codex keeps its sessions
    import apersona_hook

    claude, left_out, unread = claude_conversations()
    found = [("Claude Code", path) for path in claude]
    held: list[tuple[str, str]] = []
    root = apersona_codex.codex_home() / "sessions"
    walk = Walk(root, {".jsonl"})
    for relative in walk.files:
        path = root / relative
        if apersona_hook.session_source({"transcript_path": str(path)}) in apersona_hook.CODEX_INTERACTIVE:
            found.append(("Codex", path))
        else:
            held.append((str(path), "不是本人親手打的 Codex 對話（codex exec 或子代理），不送"))
    unread += [(str(root if folder == "." else root / folder), why) for folder, why in walk.rules_unread]
    return found, held, left_out + walk.left_out, unread


def ai_work() -> Path:
    "The work folder of `--ai`: one for his AI records, beside the folders' own."
    return private_dir(Path.home() / ".apersona" / "intake" / "ai")


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


def origin_of(relative: str) -> dict[str, object]:
    """What this machine sees of a file that the random name it goes up under does not say (the site's
    `FileOrigin`): a Slack day's 對話種類 by the folder it is kept in, and whether its own name says it is his
    LinkedIn export or résumé. Empty when it says neither; nothing of the folder or the name itself goes up."""
    path = Path(relative)
    out: dict[str, object] = {}
    folder = path.parent.name
    if folder and re.fullmatch(SLACK_DAY, path.name):
        out["chatKind"] = "one_to_one" if re.fullmatch(SLACK_DM_FOLDER, folder) else "group"
    if re.search(OWN_RECORD_NAME, path.name, re.IGNORECASE):
        out["ownRecord"] = True
    return out


def post_files(
    site: str,
    key: Callable[[], str] | str,
    persona: str,
    files: list[tuple[str, bytes]],
    me: list[str],
    take: bool = False,
    origins: dict[str, dict[str, object]] | None = None,
    purpose: str = "",
) -> dict[str, object]:
    seen = {name: (origins or {})[name] for name, _ in files if (origins or {}).get(name)}
    fields = [*who(persona).items(), *(("me", m) for m in me), *([("takeDeleted", "1")] if take else [])]
    fields += [("relation", "mine"), ("purpose", purpose)] if purpose else []
    fields += [("origins", json.dumps(seen, ensure_ascii=False))] if seen else []
    body, kind = multipart(fields, files)
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
        return "停下，不用再試：登入失效了；執行 /apersona:login 重新登入" + said
    if f.status == 403:
        return "停下，不用再試：網站不認這個同意（上傳的內容跟同意的那一句對不上）；重跑 ask 再問本人" + said
    if f.status == 404:
        return (
            "停下，不用再試：網站找不到這個人格或這次上傳，或這個登入開不了它；"
            "重新執行 /apersona:login，再從 ask 開始" + said
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


def read_bytes(path: Path) -> bytes:
    "The file's bytes, never through a shortcut (symlink): one put there after the folder was walked is refused too."
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read(MAX_FILE_BYTES + 1)


def prepared(path: Path, ai: bool = False) -> tuple[bytes | None, str]:
    """The bytes to send of one file and, when it is not sent, why: a text file with its secrets dropped; a .docx or
    .pdf as it is. `ai`: an AI conversation record, whose rows go with their folder put as the project's key
    (`with_project`), never the path."""
    try:
        if path.is_symlink():
            return None, LINKED_HERE
        size = path.stat().st_size
    except OSError:
        return None, "讀不到這份檔"
    if size > MAX_FILE_BYTES:
        return None, TOO_LARGE_HERE
    suffix = path.suffix.lower()
    try:
        raw = read_bytes(path)
    except OSError:
        return None, "讀不到這份檔（或它是捷徑）"
    if len(raw) > MAX_FILE_BYTES:
        return None, TOO_LARGE_HERE
    if suffix not in TEXT_SUFFIXES:
        return raw, ""
    text = decoded(raw)
    if text is None:
        return None, NOT_UTF8_HERE
    try:
        if suffix == ".json":
            try:
                return json.dumps(redact_json(json.loads(text), redact_secrets), ensure_ascii=False).encode("utf-8"), ""
            except ValueError:
                pass
        if suffix == ".jsonl":  # one JSON object per line: each line masked as JSON, a line that is not stays text
            lines: list[str] = []
            for line in text.split("\n"):
                try:
                    row = json.loads(line)
                    row = with_project(row) if ai else row
                    lines.append(json.dumps(redact_json(row, redact_secrets), ensure_ascii=False))
                except ValueError:
                    lines.append(redact_secrets(line))
            return "\n".join(lines).encode("utf-8"), ""
        return redact_secrets(text).encode("utf-8"), ""
    except Exception:  # noqa: BLE001  whatever went wrong, a file not masked is never sent
        return None, MASK_FAILED_HERE


def decoded(raw: bytes) -> str | None:
    "The text of a file: UTF-8, else Big5 or cp950; None when it is none of them."
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for encoding in LEGACY_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


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


def per_tool(rows: list[dict[str, object]], tools: dict[str, str], held: list[tuple[str, str]]) -> list[str]:
    "His AI records as `ask` lists them: how many and how large per tool (they run to hundreds), and why not sent."
    lines: list[str] = []
    for tool in ("Claude Code", "Codex"):
        mine = [int(cast(int, r["size"])) for r in rows if tools[str(r["path"])] == tool]
        lines.append(f"- {tool} 對話：{len(mine)} 份（{size_text(sum(mine))}），原檔照送，秘密先在這台遮掉")
    reasons: dict[str, int] = {}
    for _, why in held:
        reasons[why] = reasons.get(why, 0) + 1
    return lines + [f"- 不送 {n} 份：{why}" for why, n in reasons.items()]


def keep_as_asked(base: Path, relative: str, asked: Path) -> Path:
    """His AI record as it is now, copied into `asked` (600) under the same relative name: a conversation still being
    written grows after `ask`, and what goes is what he was shown, not what it became (no list to show again).
    `send` deletes the copies once they are up (`drop_copies`)."""
    copy = asked / relative.lstrip("/")
    private_dir(copy.parent)
    descriptor = os.open(str(copy), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(read_bytes(base / relative))
    return copy


def ask(folder: Path | None, work: Path, large: bool) -> int:
    """`ask`: what `send` would send, listed here; nothing of the files leaves the machine. No `folder`: his AI
    records (`ai_records`), listed per tool; the plan names them from his home folder (or in full, outside it)."""
    site, _, _ = options()  # logged in first: nothing is listed for a machine that cannot send
    if folder is not None and not folder.is_dir():
        raise RefusedError(f"找不到素材資料夾：{folder}")
    private_dir(work)
    if folder is None:
        base = Path.home().resolve()
        records, held, left_out, unread = ai_records()
        tools = {
            str(path.resolve().relative_to(base)) if base in path.resolve().parents else str(path.resolve()): tool
            for tool, path in records
        }
    else:
        base, held = folder.resolve(), []
        walk = Walk(folder, SUFFIXES)
        tools, left_out, unread = dict.fromkeys(walk.files, ""), walk.left_out, walk.rules_unread
        broad = too_broad(folder, len(walk.files))
        if broad and not large:
            print(f"先停下來問本人：{broad}。確定要整個送嗎？")
            print("本人說確定，才加 --large 再跑一次 ask；不然請挑一個只放素材的資料夾。")
            return EXIT_ASK_FIRST
    rows: list[dict[str, object]] = []
    asked = work / "asked"
    shutil.rmtree(str(asked), ignore_errors=True)
    for relative in list(tools):
        path = base / relative
        _, why = prepared(path)
        if why:
            held.append((relative, why))
            continue
        if folder is None:  # a record outside his home folder is named in full; its copy is under `asked` all the same
            path = keep_as_asked(base, relative, asked)
            tools[path.relative_to(asked).as_posix()] = tools[relative]
            relative = path.relative_to(asked).as_posix()
        rows.append({"path": relative, "size": path.stat().st_size, "sha256": digest(path)})
    write_json(
        work / "plan.json",
        {
            "folder": str(base if folder is not None else asked),
            "ai": folder is None,
            "files": rows,
            "held": held,
            "left_out": left_out,
            "rules_unread": unread,
        },
    )
    total = sum(int(cast(int, r["size"])) for r in rows)
    lines = [f"還沒送出任何東西。本人同意後，會把這 {len(rows)} 份（共 {size_text(total)}）送到 {site}："]
    if folder is None:
        lines += per_tool(rows, tools, held)
    else:
        lines += [f"- {r['path']}（{size_text(int(cast(int, r['size'])))}）" for r in rows]
        lines += [f"- 不送：{path}：{why}" for path, why in held]
    if left_out:
        lines.append(f"照 .gitignore／.apersonaignore、捷徑與一律不讀的檔，排除了 {left_out} 份，不送")
    if unread:
        lines.append(unread_sentence(unread))
    lines.append(f"本機工作資料夾：{work}")
    if rows:
        lines.append(
            "要把這幾份送上網站嗎？（送上去只保存、還不讀；網站算好要花多少，再請你同意讀。目前只開放測試帳號讀取。）"
            "本人說好，才執行 send；說不要就什麼都不送。"
        )
    else:
        lines.append("沒有要送的檔。")
    print("\n".join(lines))
    return 0


def _listed(folder: Path, given: str, planned: list[str]) -> str:
    "One path he named, as `ask` listed it (relative to the folder, or absolute inside it); refused when not listed."
    path = Path(given)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(folder.resolve())
        except ValueError:
            path = Path(given)
    relative = path.as_posix()
    for one in planned:
        if one.replace("\\", "/") == relative:
            return one
    raise RefusedError(f"{given} 不在 ask 列的清單裡；--take-deleted 給的要是清單上的那份檔")


def _plan_as_asked(work: Path) -> dict[str, object]:
    """The list `ask` showed him; refused when there is none, or when a file on it changed or went away: he is shown the
    list again before anything goes."""
    found = read_json(work / "plan.json")
    plan = cast("dict[str, object]", found) if isinstance(found, dict) else {}
    if not plan.get("files"):
        raise RefusedError("還沒有要送的清單：先跑 ask，給本人看過再 send")
    folder = Path(str(plan["folder"]))
    for row in cast("list[dict[str, object]]", plan["files"]):
        try:
            same = digest(folder / str(row["path"])) == row["sha256"]
        except OSError:
            same = False
        if not same:
            raise RefusedError(f"{row['path']} 在 ask 之後改過或不見了；重跑 ask，再給本人看一次清單")
    return plan


def send(work: Path, me: list[str], take: list[str] | None = None, purpose: str = "") -> int:
    """`send`: the files `ask` listed, exactly (their secrets dropped here), and what the site found, with the site's
    sentence for his yes to the read (its amounts and confirmation code). `take` the files of the list he said yes for
    after the site held parts of them he had deleted: only those go, and those parts are taken as new. `purpose` what
    he said these files of his own writing are for (`PURPOSES`); his AI records are conversations and have none."""
    site, persona, key = options()
    plan = _plan_as_asked(work)
    if purpose and plan.get("ai") is True:
        raise RefusedError("AI 對話紀錄是對話，不帶 --purpose；--purpose 只給他自己寫的文章、自介或規則")
    folder = Path(str(plan["folder"]))
    planned = cast("list[dict[str, object]]", plan["files"])
    relatives = [str(r["path"]) for r in planned]
    if take:
        relatives = list(dict.fromkeys(_listed(folder, t, relatives) for t in take))
    local: dict[str, str] = {}  # the name it goes up under -> its path here
    origins: dict[str, dict[str, object]] = {}  # the name it goes up under -> what this machine saw of it
    held = [] if take else [(str(h[0]), str(h[1])) for h in cast("list[list[str]]", plan.get("held") or [])]
    sending: list[tuple[str, bytes]] = []
    for relative in relatives:
        data, why = prepared(folder / relative, ai=plan.get("ai") is True)
        if data is None:
            held.append((relative, why))
            continue
        code = f"src-{secrets.token_hex(6)}{Path(relative).suffix.lower()}"
        local[code] = relative
        origins[code] = {} if plan.get("ai") is True else origin_of(relative)
        sending.append((code, data))
    write_json(work / "files.json", local)
    answers: list[dict[str, object]] = []
    groups = batches(sending)
    for i, group in enumerate(groups, 1):
        if len(groups) > 1:
            print(f"上傳第 {i} 批／共 {len(groups)} 批")
        posting = partial(post_files, site, key, persona, group, me, bool(take), origins, purpose)
        answers.append(patiently(posting, "上傳"))
    write_json(work / "uploads.json", {"answers": answers, "consent": None})
    left_out = 0 if take else int(cast(int, plan.get("left_out") or 0))
    unread = [(str(f), str(why)) for f, why in cast("list[list[object]]", plan.get("rules_unread") or [])]
    print(said(answers, local, held, left_out, rules_unread=[] if take else unread))
    if plan.get("ai") is True:
        drop_copies(work, answers)
    return 0


def drop_copies(work: Path, answers: list[dict[str, object]]) -> None:
    """The copies of his AI records `ask` made, deleted now they are up, so his conversations are not kept twice here;
    kept while the site held parts he had deleted, since --take-deleted sends from them (the next `ask` clears them)."""
    files = [cast("dict[str, object]", f) for a in answers for f in cast("list[object]", a.get("files") or [])]
    if any(f.get("held") for f in files):
        print(f"本機的對話複本先留著（{work / 'asked'}），等本人決定要不要收回刪過的那幾份；下次 ask 會清掉。")
        return
    shutil.rmtree(str(work / "asked"), ignore_errors=True)
    print("送出的對話複本已從本機刪掉。")


def _agreeable(answers: list[dict[str, object]]) -> list[dict[str, object]]:
    "The batches with a sentence to agree to: not one holding only parts he deleted (nothing in it is read)."
    return [a for a in answers if a.get("consentSha256") and not a.get("heldOnly")]


def into_line(answers: list[dict[str, object]]) -> list[str]:
    "「送進人格：…」 when his account has more than one persona (the site then names it, `into`); none otherwise."
    into = sorted({str(a["into"]) for a in answers if a.get("into")})
    return ["送進人格：" + "、".join(into)] if into else []


def said(
    answers: list[dict[str, object]],
    local: dict[str, str],
    held: list[tuple[str, str]],
    left_out: int,
    rules_unread: list[tuple[str, str]] | None = None,
) -> str:
    """What the site found, summed over the batches, each file by its path here, as the site's data; then the
    plugin's own sentence to agree to (`question`: the site's numbers, never its words)."""
    files = [cast("dict[str, object]", f) for a in answers for f in cast("list[object]", a.get("files") or [])]
    readable = sum(int(cast(int, a.get("readableParts") or 0)) for a in answers)
    why: dict[str, int] = {}
    for a in answers:
        for row in cast("list[dict[str, object]]", a.get("unreadableParts") or []):
            why[str(row.get("reason"))] = why.get(str(row.get("reason")), 0) + int(cast(int, row.get("count") or 0))
    found = into_line(answers) + [f"收到 {len(files)} 份，{readable} 段能讀"]
    if why:
        found[-1] += f"，{sum(why.values())} 段讀不了（" + "、".join(f"{k} {n}" for k, n in why.items()) + "）"
    deleted: list[str] = []
    for f in files:
        name = local.get(str(f.get("name")), "（不明的檔）")
        line = f"- {name}：{f.get('reason') or str(f.get('usable')) + ' 段能讀'}"
        if f.get("held"):
            deleted.append(name)
            if not f.get("reason"):
                line += f"；其中 {f.get('held')} 段有刪過的內容，這次先不收"
        found.append(line)
    agreeable = _agreeable(answers)
    found += [str(a["estimateWords"]) for a in agreeable[:1] if a.get("estimateWords")]
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
    if rules_unread:
        lines.append(unread_sentence(rules_unread))
    if deleted:
        lines.append("這幾份裡有刪過的內容，這次先不收：" + "、".join(deleted))
        lines.append(
            '要收回其中哪一份，要本人說出是哪一份、說要收回，才跑 send --take-deleted "<那份的路徑>"'
            "（一份一個 --take-deleted）；那些內容會當成新的來源讀。本人沒說就不收。"
            "也可以到網站資料頁恢復刪掉的那份來源。"
        )
    if agreeable:
        lines.append(PREPARED if readable else "同意後只補登完整訊息觀測，不重新讀取、不花錢；不同意不補登。")
        action = "要開始讀" if readable else "要補登觀測"
        sentence, codes = question(agreeable)
        code = f"，並說出確認碼 {codes}" if codes else ""
        lines.append(
            f"{action}，請本人親口說同意：「{sentence}」{code}；AI 不能代為同意。"
            '本人說了，才跑 consent yes --words "<本人那句原話>"'
        )
    rules = rules_line(answers)
    if not agreeable and not rules:
        lines.append("這次沒有能讀的段落，不用同意。")
    return "\n".join(lines + ([rules] if rules else []))


def rules_line(answers: list[dict[str, object]]) -> str:
    "The site's 「只整理規矩」 sentence, when a batch has one, for him to say before `consent rules`; empty otherwise."
    rules = [cast("dict[str, object]", a["rulesOnly"]) for a in answers if isinstance(a.get("rulesOnly"), dict)]
    if not rules:
        return ""
    sentence = rules[0].get("question")
    codes = " ".join(c for c in (str(r.get("confirmCode") or "") for r in rules) if c.isdigit())
    return (
        f"也可以只整理規矩（不逐段讀；之前送過的檔也能再整理一次；會花錢）：本人親口說「{sentence}」"
        f"並說出確認碼 {codes}，才跑 consent rules --words \"<本人那句原話>\"。"
    )


#: the site's sentence, piece by piece (`reading.consent.decision`: QUESTION, SUPPLEMENT, TAKEN_BACK and the paid
#: entry's 「，預估 …，最多 …」); the site's tests compare a sentence made here with its own
READ = "讀這 {} 份"
SUPPLEMENT = "補登這 {} 份來源類型或完整訊息觀測，不重新讀取、不花錢"
TAKEN_BACK = "；其中 {} 段是刪過的內容，照你說的收回，當成新的來源"


def money(usd: Decimal) -> str:
    "An amount as the site says it: two decimals rounded up, or 「不到 US$0.01」 above nothing."
    if Decimal(0) < usd < Decimal("0.01"):
        return "不到 US$0.01"
    return "US$" + str(usd.quantize(Decimal("0.01"), ROUND_CEILING))


def question(agreeable: list[dict[str, object]]) -> tuple[str, str]:
    """The one sentence for every batch, made here (never the site's words: what he is asked to say is not up to the
    site) from the site's counts and its quote's numbers (`estimate`: the expected cost and the most, summed over the
    batches), and the confirmation code(s) it must carry (digits only)."""
    files = [cast("dict[str, object]", f) for a in agreeable for f in cast("list[object]", a.get("files") or [])]
    n = sum(1 for f in files if not f.get("reason"))
    metadata = sum(int(cast(int, a.get("metadataParts") or 0)) for a in agreeable)
    readable = sum(int(cast(int, a.get("readableParts") or 0)) for a in agreeable)
    back = sum(int(cast(int, f.get("takenBack") or 0)) for f in files)
    tail = TAKEN_BACK.format(back) if back else ""
    if not readable:
        return (SUPPLEMENT.format(metadata) if metadata else READ.format(0)) + tail, ""
    sentence = READ.format(n)
    quoted = [cast("dict[str, object]", a["estimate"]) for a in agreeable if isinstance(a.get("estimate"), dict)]
    if quoted and len(quoted) == len(agreeable):
        expected = sum((Decimal(str(q.get("amount"))) for q in quoted), Decimal(0))
        cap = sum((Decimal(str(q.get("cap"))) for q in quoted), Decimal(0))
        sentence += f"，預估 {money(expected)}，最多 {money(cap)}"
    sentence += ("；另" + SUPPLEMENT.format(metadata) if metadata else "") + tail
    codes = [str(a.get("confirmCode") or "") for a in agreeable if a.get("readableParts")]
    return sentence, " ".join(c for c in codes if c.isdigit())


def _uploads(work: Path) -> dict[str, object]:
    found = read_json(work / "uploads.json")
    record = cast("dict[str, object]", found) if isinstance(found, dict) else {}
    if not record.get("answers"):
        raise RefusedError("還沒有送過檔：先跑 ask")
    return record


def consent(work: Path, yes: bool, rules_only: bool = False, words: str = "", keep_going: bool = False) -> int:
    """`consent yes|no|rules`: his answer to the sentence, for every batch of the last `ask`. `rules`: his yes to the
    site's second sentence (`rulesOnly`, 「只整理成長期規矩，不逐段讀」) for each batch that has one, a batch of files
    sent before included (nothing new to read, but 長期規矩 is made again over what is kept); a batch without it
    (nothing he said to an AI in it) is left unanswered. `words`: his own sentence, as said (the site takes a yes
    that spends only when it carries the sentence's confirmation code); `keep_going`: his 「要繼續」 to what today
    spent."""
    if yes and not words.strip():
        raise RefusedError('要本人親口說的那句（含確認碼）：consent yes --words "<本人那句原話>"')
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
    for a in answers if rules_only else _agreeable(answers):
        body: dict[str, object] = {**who(persona), "jobId": a["jobId"], "consentSha256": a["consentSha256"]}
        body |= {"ownerReply": words, "keepGoing": keep_going}
        if rules_only:
            offered = a.get("rulesOnly")
            if not isinstance(offered, dict):
                print(f"{a['jobId']}：這批沒有你對 AI 講的話，不能只整理規矩；沒有回答它")
                continue
            body |= {"consentSha256": cast("dict[str, object]", offered).get("consentSha256"), "depth": "rules"}
        reply = patiently(partial(post_json, site, FILES_CONSENT_PATH, key, body), "同意")
        if reply.get("message"):
            print(fenced(str(reply["message"])))
        if reply.get("queued") or "rulesOnly" in reply:
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
    parser.add_argument("--ai", action="store_true", help="不給資料夾：送這台的 Claude Code 與 Codex 對話紀錄")
    commands = parser.add_subparsers(dest="command", required=True)
    asking = commands.add_parser("ask", help="只在本機列出要送哪些檔、幾份、多大；什麼都不送")
    asking.add_argument("folder", type=Path, nargs="?", help="素材資料夾；用 --ai 時不給")
    asking.add_argument("--large", action="store_true", help="本人確定要送整個家目錄或超過 200 份時才加")
    sending = commands.add_parser("send", help="本人看過清單說好之後：送 ask 列的那些檔上網站切段")
    sending.add_argument("--me", action="append", default=[], help="聊天匯出檔裡本人的名字或帳號 ID（可多個）")
    sending.add_argument(
        "--take-deleted",
        action="append",
        default=[],
        metavar="PATH",
        help="本人說要收回的那份檔（ask 清單上的路徑，一份一個）：只送這幾份，刪過的內容當成新的來源",
    )
    sending.add_argument(
        "--purpose",
        choices=PURPOSES,
        default="",
        help="本人說這批是他自己寫的、說了用途才加：article 我的文章、about_me 關於我、rules 我的規則",
    )
    answer = commands.add_parser("consent")
    answer.add_argument("answer", choices=("yes", "no", "rules"))
    answer.add_argument("--words", default="", help="本人同意時親口說的那一句（要有確認碼）；AI 不能代為同意")
    answer.add_argument("--keep-going", action="store_true", help="本人對「今天已經花了…要繼續嗎？」說要繼續")
    commands.add_parser("status")
    commands.add_parser("cancel")
    args = parser.parse_args(argv)
    try:
        folder: Path | None = None if args.ai else args.folder
        if args.work is not None:
            work: Path = args.work
        elif args.ai:
            work = ai_work()
        elif folder is not None:
            work = work_for(folder)
        else:
            raise RefusedError("要給素材資料夾：--folder <資料夾>，或用 --ai 送這台的 AI 對話紀錄")
        if args.command == "ask":
            if folder is None and not args.ai:
                raise RefusedError("要給素材資料夾，或用 --ai 送這台的 AI 對話紀錄")
            return ask(folder, work, bool(args.large))
        if args.command == "send":
            return send(work, list(args.me), list(args.take_deleted), str(args.purpose))
        if args.command == "consent":
            return consent(work, args.answer != "no", args.answer == "rules", str(args.words), bool(args.keep_going))
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
