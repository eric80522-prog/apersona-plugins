"""遮身分: before anything leaves the machine, identifying strings become random codes, and secrets are dropped.

Only what can be decided mechanically is masked: the person names the owner lists, phone numbers, e-mail addresses,
Taiwanese ID numbers, Taiwanese street addresses, and recognised credentials (those are dropped, not coded). A name
nobody listed is not found; the consent text says so. The code table (K7 = 王大明) is kept only in the local work
folder, readable by the owner alone, so the local AI can put the real names back when it answers.
"""

from __future__ import annotations

import re
import secrets
import string
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from typing import Final, cast

SECRET: Final = "[秘密已遮]"
#: a value after `key =`／`key:`／`密碼：`: quoted (spaces and all) or up to a space or a comma of either width
_VALUE: Final = r"(?:\"[^\"\n]*\"|'[^'\n]*'|「[^」\n]*」|“[^”\n]*”|[^\s,;，；。、]+)"
_SECRETS: Final = (
    # key blocks, PGP's included: `-----BEGIN … PRIVATE KEY[ BLOCK]-----` to its end line
    (re.compile(r"(?s)-----BEGIN [^-\n]*PRIVATE KEY[^-\n]*-----.*?-----END [^-\n]*PRIVATE KEY[^-\n]*-----"), SECRET),
    # the password part of `scheme://user:password@host`; the user and host stay
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)"), r"\1" + SECRET + r"\2"),
    # an Authorization header, whatever its scheme (Basic, Bearer, Token, Digest)
    (
        re.compile(r"(?im)(authorization[\"']?\s*[:=：]\s*)(?:(?:basic|bearer|token|digest)\s+)?" + _VALUE),
        r"\1" + SECRET,
    ),
    # credential shapes recognised by their own look
    (
        re.compile(
            r"(?:\bsk-[\w-]{12,}"  # OpenAI and the like
            r"|apersona_token_[\w-]+"
            r"|(?i:\bBearer\s+[\w./+=-]+)"
            r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"  # AWS access key id
            r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"  # GitHub
            r"|\bxox[abpr]-[A-Za-z0-9-]{10,}"  # Slack
            r"|\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}"  # Stripe
            r"|\bAIza[0-9A-Za-z_-]{35}"  # Google API key
            r"|\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,})"  # JWT
        ),
        SECRET,
    ),
    # `api_key=…`, `aws_secret_access_key = "…"`, `"password": "…"`, `密碼：…`: the value goes, the name stays
    (
        re.compile(
            r"(?im)((?:api[_-]?key|token|passw(?:or)?d|secret|credential|private[_-]?key|密碼|密码|口令|金鑰|密鑰|私鑰)"
            r"[\w.-]{0,40}[\"']?\s*[:=：]\s*)" + _VALUE
        ),
        r"\1" + SECRET,
    ),
)
#: a JSON key whose value is a secret whatever it looks like (`client_secret`, `refresh_token`, `apiKey`, `password`);
#: `author` and `authority` are not ones, `Authorization` is
SECRET_KEY: Final = re.compile(
    r"(?i)passw(?:or)?d|secret|token|api[_-]?key|credential|private[_-]?key|auth(?!or(?!i[sz]))"
)
_DIGITS: Final = "0-9０-９"
#: kind -> pattern, in the order applied (e-mail before phone: an address may hold digits; address before phone).
PATTERNS: Final = (
    ("Email", re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    ("身分證", re.compile(r"(?<![A-Za-z0-9])[A-Z][1289][0-9]{8}(?![A-Za-z0-9])")),
    (
        "地址",
        re.compile(
            rf"(?:[一-鿿]{{1,3}}[縣市])?(?:[一-鿿]{{1,3}}[區鄉鎮市])?[一-鿿{_DIGITS}]{{1,6}}"
            rf"(?:路|街|大道)(?:[一二三四五六七八九十{_DIGITS}]+段)?(?:[{_DIGITS}]+巷)?(?:[{_DIGITS}]+弄)?"
            rf"[{_DIGITS}]+(?:之[{_DIGITS}]+)?號(?:[{_DIGITS}]+樓(?:之[{_DIGITS}]+)?)?"
        ),
    ),
    (
        "電話",
        re.compile(
            r"(?<![0-9])(?:(?:\+886[-\s]?|0)9[0-9]{2}[-\s]?[0-9]{3}[-\s]?[0-9]{3}"
            r"|(?:\+886[-\s]?\(?|\(?0)[2-8][0-9]?\)?[-\s]?[0-9]{3,4}[-\s]?[0-9]{4})(?![0-9])"
        ),
    ),
)
KINDS: Final = ("人名", *(kind for kind, _ in PATTERNS))


def redact_secrets(text: str) -> str:
    "Recognised credential shapes are dropped outright; nothing restores them."
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def redact_json(value: object, mask: Callable[[str], str]) -> object:
    """A JSON value with every string masked by `mask` (so an export stays JSON) and the whole value of a key named like
    a secret (`SECRET_KEY`) dropped, whatever its type."""
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, list):
        return [redact_json(v, mask) for v in cast("list[object]", value)]
    if isinstance(value, dict):
        return {
            k: SECRET if SECRET_KEY.search(k) else redact_json(v, mask)
            for k, v in cast("dict[str, object]", value).items()
        }
    return value


class Masker:
    """One owner's code table: the same name is the same code in every run that keeps the table (so the same content
    masks to the same text and is sent and paid for once). `entries` is what the local file holds."""

    def __init__(self, entries: Iterable[tuple[str, str, str]], forbidden: Iterable[str]) -> None:
        self.entries: list[tuple[str, str, str]] = list(entries)  # (code, kind, original)
        self._code = {(kind, value): code for code, kind, value in self.entries}
        self._forbidden = "\n".join(forbidden)
        self.counts: Counter[str] = Counter()

    def _new_code(self, kind: str) -> str:
        used = {code for code, _, _ in self.entries}
        letters = string.ascii_uppercase
        width = 1
        while True:
            for _ in range(64):
                code = secrets.choice(letters) + "".join(secrets.choice(string.digits) for _ in range(width))
                if kind != "人名":
                    code = f"〔{kind} {code}〕"
                # A code already in the material would make the way back ambiguous.
                if code not in used and code not in self._forbidden:
                    return code
            width += 1

    def code(self, kind: str, value: str) -> str:
        found = self._code.get((kind, value))
        if found is None:
            found = self._new_code(kind)
            self._code[(kind, value)] = found
            self.entries.append((found, kind, value))
        return found

    def mask(self, text: str, names: Sequence[str]) -> str:
        text = redact_secrets(text)
        for kind, pattern in PATTERNS:

            def coded(match: re.Match[str], kind: str = kind) -> str:
                self.counts[kind] += 1
                return self.code(kind, match.group(0))

            text = pattern.sub(coded, text)
        # Longer names first, so 王大明 is not left as 王 plus the code of 大明.
        for name in sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True):
            hits = text.count(name)
            if hits:
                self.counts["人名"] += hits
                text = text.replace(name, self.code("人名", name))
        return text
