"""開讀前先問的外掛那一半 (PRD 02「切段、能不能用、估價只維護一份，放雲端由伺服器跑」): the plugin sends his files as
they are to the site, which cuts them and says what can be read; he says yes once; the site reads them.

Standard library only, like the hooks: nothing of the product is installed on his machine.

- `ask <folder>`      walk the folder (`.gitignore`／`.apersonaignore` and always `.git`, `node_modules`, `.env*`
                      left out: `apersona_ignore.py`, the same rules the site's own intake keeps), drop secrets from
                      every text file on this machine first, and swap names, phone numbers, emails, ID numbers and
                      addresses for codes only when the account's 上傳前換代號 is on (`GET /api/intake/settings`; when
                      the site cannot be asked, as if on: 寧可多遮). With codes on, a .docx or .pdf is not sent (its
                      text cannot be masked here); with them off it goes as it is and the site drops its secrets.
                      Each file goes up under a random name (`src-<hex>` with its extension): its path stays here, in
                      `files.json`. The files go in batches the site takes (`POST /api/intake/files`, at most
                      `MAX_FILES` files and `MAX_TOTAL_BYTES` a batch, a file over `MAX_FILE_BYTES` not sent); the
                      site keeps them prepared, waiting for his yes, reads nothing and spends nothing. What it found
                      is printed: how many files, how many parts can be read, how many cannot and why, each file by
                      its path, `PREPARED`, and the one sentence to agree to (「用你的額度讀這 N 份」, on the free
                      extraction 「用免費額度讀這 N 份」: the site's own sentence).
- `consent yes|no`    his answer to that sentence. Yes: each batch's yes (`POST /api/intake/files/consent`), and the
                      site queues the read, or says why not (the free extraction used, nothing left to read). No:
                      each batch's no (`POST /api/intake/files/decline`): nothing is read, now or later; the site
                      keeps what it prepared and never reads it (as for a batch left without a yes for 7 days).
- `status`            where each batch's read stands (`GET /api/intake/files`).
- `cancel`            the parts of the batches he agreed to that are not read yet are not read (`POST
                      /api/intake/cancel`); what went up is kept (Eric 11-05: 取消是「這次先不要」).

A failure is 暫時 (no connection, 5xx, 429: the same request again, MAX_RETRIES times at most, waiting `retry_after`
or BACKOFF_S) or 永久 (the rest: it stops, says why and what to do). Exit codes: 3 stopped for good, 5 still failing
after the retries (run it again later). The code table (`aliases.json`, 600) is never sent.
"""

from __future__ import annotations

import argparse
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
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apersona_ignore import Walk  # noqa: E402
from apersona_mask import Masker, redact_secrets  # noqa: E402

FILES_PATH = "/api/intake/files"
FILES_CONSENT_PATH = "/api/intake/files/consent"
FILES_DECLINE_PATH = "/api/intake/files/decline"
#: what the upload step does, said as the site says it (`mcp/cloud.py` PREPARED)
PREPARED = "先送上來準備，同意後才讀；不同意就不讀（7 天沒回答也當作不讀）。"
SEGMENTS_PATH = "/api/intake/segments"
CANCEL_PATH = "/api/intake/cancel"
SETTINGS_PATH = "/api/intake/settings"
#: what the site takes in one request (`mcp/numbers.py` UPLOAD_*): more goes in another batch
MAX_FILES = 100
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
#: the kinds the site reads; the text ones are masked here
SUFFIXES = frozenset({".md", ".txt", ".docx", ".pdf", ".csv", ".json"})
TEXT_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json"})
TIMEOUT_S = 120
MAX_RETRIES = 3
BACKOFF_S = (1, 2, 4)
RETRY_AFTER_MAX_S = 120
TEMPORARY_ERRORS = ("rate_limited", "unavailable")
EXIT_REFUSED = 3
EXIT_PAUSED = 5
TOO_LARGE_HERE = f"檔案太大（一份最多 {MAX_FILE_BYTES // (1024 * 1024)} MB），這份沒送"
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
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def options() -> tuple[str, str, str]:
    site, persona, key = (os.environ.get("CLAUDE_PLUGIN_OPTION_" + k, "") for k in ("SITE", "PERSONA", "KEY"))
    if not (site and persona and key):
        raise RefusedError("外掛沒設定 site、persona 或 key")
    return site.rstrip("/"), persona, key


def _call(request: urllib.request.Request) -> dict[str, object]:
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        raw = response.read()
    try:
        answer: object = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        answer = {}
    return cast("dict[str, object]", answer) if isinstance(answer, dict) else {}


def _headers(key: str, **more: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + key, "Accept": "application/json", **more}


def get(site: str, path: str, key: str, query: dict[str, str]) -> dict[str, object]:
    url = site + path + "?" + urllib.parse.urlencode(query)
    return _call(urllib.request.Request(url, headers=_headers(key), method="GET"))


def post_json(site: str, path: str, key: str, body: dict[str, object]) -> dict[str, object]:
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


def post_files(site: str, key: str, persona: str, files: list[tuple[str, bytes]], me: list[str]) -> dict[str, object]:
    body, kind = multipart([("personaId", persona), *(("me", m) for m in me)], files)
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
    said = f"（網站說：{f.said}）" if f.said else ""
    if f.status == 401:
        return "停下，不用再試：安裝金鑰失效，到網站設定頁重發一把，再用新的金鑰重新安裝外掛" + said
    if f.status == 403:
        return "停下，不用再試：網站不認這個同意（上傳的內容跟同意的那一句對不上）；重跑 ask 再問本人" + said
    if f.status == 404:
        return "停下，不用再試：網站找不到這個人格或這次上傳，或這把金鑰開不了它；檢查外掛設定的 persona 與金鑰" + said
    if f.status == 409:
        return "停下：" + (f.said or "網站說這次上傳現在不能這樣做")
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


def codes_on(site: str, persona: str, key: str) -> bool:
    "上傳前換代號 as the site says now; on when it cannot be asked (寧可多遮), and he is told which."
    try:
        found = get(site, SETTINGS_PATH, key, {"personaId": persona}).get("aliasCodes")
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


def _mask_json(value: object, mask: Callable[[str], str]) -> object:
    "Every string of a JSON value masked, so the export stays JSON (a secret's pattern may run over a quote)."
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, list):
        return [_mask_json(v, mask) for v in cast("list[object]", value)]
    if isinstance(value, dict):
        return {k: _mask_json(v, mask) for k, v in cast("dict[str, object]", value).items()}
    return value


def prepared(path: Path, masker: Masker | None, names: list[str]) -> tuple[bytes | None, str]:
    """The bytes to send of one file and, when it is not sent, why: a text file with its secrets dropped (and codes
    swapped when `masker`); a .docx or .pdf as it is, or not at all with codes on."""
    try:
        size = path.stat().st_size
    except OSError:
        return None, "讀不到這份檔"
    if size > MAX_FILE_BYTES:
        return None, TOO_LARGE_HERE
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        return (None, NO_MASK_HERE) if masker is not None else (path.read_bytes(), "")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw, ""  # the site says it is not UTF-8; nothing could be masked in it anyway

    def mask(s: str) -> str:
        return masker.mask(s, names) if masker is not None else redact_secrets(s)

    if suffix == ".json":
        try:
            return json.dumps(_mask_json(json.loads(text), mask), ensure_ascii=False).encode("utf-8"), ""
        except ValueError:
            pass
    return mask(text).encode("utf-8"), ""


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.stat().st_size <= MAX_FILE_BYTES else ""
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


def ask(folder: Path, work: Path, names_file: Path | None, me: list[str]) -> int:
    "`ask`: the folder's files sent as they are (masked here as the switch says), and what the site found."
    site, persona, key = options()
    if not folder.is_dir():
        raise RefusedError(f"找不到素材資料夾：{folder}")
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    on = codes_on(site, persona, key)
    names = []
    if names_file is not None:
        names = [n.strip() for n in names_file.read_text(encoding="utf-8").splitlines() if n.strip()]
    stored = read_json(work / "aliases.json")
    table = cast("dict[str, object]", stored) if isinstance(stored, dict) else {}
    entries = [(e[0], e[1], e[2]) for e in cast("list[list[str]]", table.get("entries") or []) if len(e) == 3]
    walk = Walk(folder, SUFFIXES)
    masker: Masker | None = None
    if on:  # a code must not already be in the material, or the way back would be ambiguous
        every = [_text(folder / r) for r in walk.files if Path(r).suffix.lower() in TEXT_SUFFIXES]
        masker = Masker(entries, [*every, *names])
    local: dict[str, str] = {}  # the name it goes up under -> its path here
    held: list[tuple[str, str]] = []  # (path, why) of the files not sent
    sending: list[tuple[str, bytes]] = []
    for relative in walk.files:
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
    print(said(answers, local, held, walk.left_out))
    return 0


def said(answers: list[dict[str, object]], local: dict[str, str], held: list[tuple[str, str]], left_out: int) -> str:
    "What the site found, summed over the batches, each file by its path here; and the sentence to agree to."
    files = [cast("dict[str, object]", f) for a in answers for f in cast("list[object]", a.get("files") or [])]
    readable = sum(int(cast(int, a.get("readableParts") or 0)) for a in answers)
    why: dict[str, int] = {}
    for a in answers:
        for row in cast("list[dict[str, object]]", a.get("unreadableParts") or []):
            why[str(row.get("reason"))] = why.get(str(row.get("reason")), 0) + int(cast(int, row.get("count") or 0))
    lines = [f"收到 {len(files) + len(held)} 份，{readable} 段能讀"]
    if why:
        lines[0] += f"，{sum(why.values())} 段讀不了（" + "、".join(f"{k} {n}" for k, n in why.items()) + "）"
    if left_out:
        lines.append(f"照 .gitignore／.apersonaignore 排除了 {left_out} 份，沒有送")
    for f in files:
        name = local.get(str(f.get("name")), str(f.get("name")))
        lines.append(f"- {name}：{f.get('reason') or str(f.get('usable')) + ' 段能讀'}")
    lines += [f"- {path}：{reason}" for path, reason in held]
    agreeable = [a for a in answers if a.get("consentSha256")]
    if agreeable:
        n = sum(1 for f in files if not f.get("reason"))
        free = next((str(a["free"]) for a in agreeable if a.get("free")), "")
        if free:
            lines.append(free)
        lines.append(PREPARED)
        lines.append(f"要開始讀，請本人親口說同意：「{question(agreeable, n)}」")
    else:
        lines.append("這次沒有能讀的段落，不用同意。")
    return "\n".join(lines)


def question(agreeable: list[dict[str, object]], n: int) -> str:
    """The one sentence for every batch: the site's own when there is one batch; else the same words with the files
    of all of them (on the free extraction when every batch's sentence is)."""
    if len(agreeable) == 1 and agreeable[0].get("question"):
        return str(agreeable[0]["question"])
    free = all(str(a.get("question") or "").startswith("用免費額度") for a in agreeable)
    return f"用免費額度讀這 {n} 份" if free else f"用你的額度讀這 {n} 份"


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
            body = {"personaId": persona, "jobId": a["jobId"]}
            said_last = str(patiently(partial(post_json, site, FILES_DECLINE_PATH, key, body), "不同意").get("message"))
        record["consent"] = False
        write_json(work / "uploads.json", record)
        print(said_last or "沒同意：什麼都不讀、不花錢")
        return 0
    agreed: list[str] = []
    for a in answers:
        if not a.get("consentSha256"):
            continue
        body: dict[str, object] = {"personaId": persona, "jobId": a["jobId"], "consentSha256": a["consentSha256"]}
        reply = patiently(partial(post_json, site, FILES_CONSENT_PATH, key, body), "同意")
        print(str(reply.get("message") or ""))
        if reply.get("queued"):
            agreed.append(str(a["jobId"]))
    record["consent"] = True
    record["agreed"] = agreed
    write_json(work / "uploads.json", record)
    if agreed:
        print("進度：" + "、".join(agreed) + "（用 status 看，或在網站的「讀取進度」）")
    return 0


def status(work: Path) -> int:
    "`status`: where each batch's read stands."
    site, persona, key = options()
    for a in cast("list[dict[str, object]]", _uploads(work)["answers"]):
        query = {"personaId": persona, "job": str(a["jobId"])}
        now = patiently(partial(get, site, FILES_PATH, key, query), "查進度")
        if not now.get("agreed"):
            print(f"{a['jobId']}：還沒同意")
        else:
            print(f"{a['jobId']}：{now.get('state')}，已讀 {now.get('done')}／{now.get('total')} 段")
    return 0


def cancel(work: Path) -> int:
    "`cancel`: what he agreed to and is not read yet is not read; what went up stays."
    site, persona, key = options()
    record = _uploads(work)
    said_last = ""
    for job in cast("list[str]", record.get("agreed") or []):
        query = {"personaId": persona, "job": job}
        prints: object = patiently(partial(get, site, SEGMENTS_PATH, key, query), "取消").get("fingerprints") or []
        body: dict[str, object] = {"personaId": persona, "jobId": job, "fingerprints": prints}
        said_last = str(patiently(partial(post_json, site, CANCEL_PATH, key, body), "取消").get("message") or "")
    record["consent"] = False
    record["agreed"] = []
    write_json(work / "uploads.json", record)
    print(said_last or "沒有正在讀的；送上去的檔沒有同意就不會讀")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apersona_intake.py")
    parser.add_argument("--work", type=Path, required=True, help="本機工作資料夾（例：<素材資料夾>/.apersona-intake）")
    commands = parser.add_subparsers(dest="command", required=True)
    asking = commands.add_parser("ask", help="把資料夾的檔送上網站切段，列出能讀的與讀不了的")
    asking.add_argument("folder", type=Path)
    asking.add_argument("--names-file", type=Path, help="上傳前換代號開著時要換成代號的人名，一行一個")
    asking.add_argument("--me", action="append", default=[], help="聊天匯出檔裡本人的名字或帳號 ID（可多個）")
    answer = commands.add_parser("consent")
    answer.add_argument("answer", choices=("yes", "no"))
    commands.add_parser("status")
    commands.add_parser("cancel")
    args = parser.parse_args(argv)
    work: Path = args.work
    try:
        if args.command == "ask":
            return ask(args.folder, work, args.names_file, list(args.me))
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
    raise SystemExit(main())
