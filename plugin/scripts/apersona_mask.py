"""遮秘密: recognised credentials (key blocks, passwords in URLs, Authorization headers, `api_key=…`／`密碼：…`, and
tokens known by their own shape) become `SECRET` before anything is sent, stored or given to a model. Nothing restores
them. Names, phone numbers, e-mail addresses and the like are not touched (上傳前換代號 was retired).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final, cast

SECRET: Final = "[秘密已遮]"
#: a value after `key =`／`key:`／`密碼：`: quoted (spaces and all) or up to a space or a comma of either width
_VALUE: Final = r"(?:\"[^\"\n]*\"|'[^'\n]*'|「[^」\n]*」|“[^”\n]*”|[^\s,;，；。、]+)"
# These two shapes historically also protect mixed-script values. Prefer a match ending before Han prose;
# keep a non-ASCII fragment inside a value when more token text follows it. A wholly ambiguous value stays masked.
_SK_BODY: Final = r"(?u:[\w-]{12,})"
_BEARER_BODY: Final = r"(?u:[\w./+=-]+)"
_BEFORE_HAN_PROSE: Final = r"(?<![㐀-鿿豈-﫿])"
_SECRETS: Final = (
    # key blocks, PGP's included: `-----BEGIN … PRIVATE KEY[ BLOCK]-----` to its end line
    (re.compile(r"(?s)-----BEGIN [^-\n]*PRIVATE KEY[^-\n]*-----.*?-----END [^-\n]*PRIVATE KEY[^-\n]*-----"), SECRET),
    # the password part of `scheme://user:password@host`; the user and host stay
    (re.compile(r"(?i)((?a:\b)[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)"), r"\1" + SECRET + r"\2"),
    # an Authorization header, whatever its scheme (Basic, Bearer, Token, Digest)
    (
        re.compile(r"(?im)(authorization[\"']?\s*[:=：]\s*)(?:(?:basic|bearer|token|digest)\s+)?" + _VALUE),
        r"\1" + SECRET,
    ),
    # `api_key=…`, `aws_secret_access_key = "…"`, `"password": "…"`, `密碼：…`: the value goes, the name stays
    (
        re.compile(
            r"(?im)((?:api[_-]?key|token|passw(?:or)?d|secret|credential|private[_-]?key|密碼|密码|口令|金鑰|密鑰|私鑰)"
            r"[\w.-]{0,40}[\"']?\s*"
            # In 金鑰是https://user:…@host the URL pass already hid the password; its colon is not an assignment.
            rf"(?!://[^\s:/@]+:{re.escape(SECRET)}@)[:=：]\s*)" + _VALUE
        ),
        r"\1" + SECRET,
    ),
    # credential shapes recognised by their own look
    (
        re.compile(
            rf"(?:\bsk-(?:{_SK_BODY}{_BEFORE_HAN_PROSE}|{_SK_BODY})"
            r"|apersona_(?:token|at|rt)_[A-Za-z0-9_-]+"
            rf"|(?i:\bBearer)(?u:\s+)(?:{_BEARER_BODY}{_BEFORE_HAN_PROSE}|{_BEARER_BODY})"
            r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
            r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"
            r"|\bxox[abpr]-[A-Za-z0-9-]{10,}"
            r"|\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}"
            r"|\bAIza[0-9A-Za-z_-]{35}"
            r"|\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,})",
            re.ASCII,
        ),
        SECRET,
    ),
)
#: a JSON key whose value is a secret whatever it looks like (`client_secret`, `refresh_token`, `apiKey`, `password`);
#: `author` and `authority` are not ones, `Authorization` is
SECRET_KEY: Final = re.compile(
    r"(?i)passw(?:or)?d|secret|token|api[_-]?key|credential|private[_-]?key|auth(?!or(?!i[sz]))"
)


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


def _sub_ranges(
    pattern: re.Pattern[str],
    replacement: str | Callable[[re.Match[str]], str],
    text: str,
    ranges: tuple[tuple[int, int], ...],
) -> tuple[str, tuple[tuple[int, int], ...]]:
    "Apply the exact masking substitutions, retaining every overlapping excluded character's replacement."
    pieces: list[str] = []
    mapped: list[tuple[int, int]] = []
    before = after = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        pieces.append(text[before:start])
        for lo, hi in ranges:
            a, b = max(lo, before), min(hi, start)
            if a < b:
                mapped.append((after + a - before, after + b - before))
        after += start - before
        value = replacement(match) if callable(replacement) else match.expand(replacement)
        pieces.append(value)
        if value and any(lo < end and start < hi for lo, hi in ranges):
            mapped.append((after, after + len(value)))
        after += len(value)
        before = end
    pieces.append(text[before:])
    for lo, hi in ranges:
        if max(lo, before) < hi:
            mapped.append((after + max(lo, before) - before, after + hi - before))
    return "".join(pieces), tuple(sorted(mapped))


def redact_with_ranges(text: str, ranges: tuple[tuple[int, int], ...]) -> tuple[str, tuple[tuple[int, int], ...]]:
    "Redact secrets without losing exclusion ranges when replacement lengths differ."
    for pattern, replacement in _SECRETS:
        text, ranges = _sub_ranges(pattern, replacement, text, ranges)
    return text, ranges
