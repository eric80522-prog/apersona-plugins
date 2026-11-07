"""遮身分 (Eric 11-04 第三次裁): before anything leaves the machine, identifying strings become random codes.

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
from collections.abc import Iterable, Sequence
from typing import Final

SECRET: Final = "[秘密已遮]"
_SECRETS: Final = (
    (re.compile(r"(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----"), SECRET),
    (re.compile(r"(?i)(?:sk-[\w-]{12,}|apersona_token_[\w-]+|Bearer\s+[\w./+=-]+)"), SECRET),
    (re.compile(r"(?im)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+"), r"\1" + SECRET),
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
