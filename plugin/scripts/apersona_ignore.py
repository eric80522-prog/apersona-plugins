"""排除清單: which files in the owner's folder are not read at all (not cut, not priced, not sent).

Three kinds, all decided on this machine from names only, before any file is opened:

- always: a shortcut (symlink), file or folder, which is never followed and counted as left out when its name is of
  a kind read; a folder called `.git` or `node_modules` and a file whose name starts with `.env`, at any depth. No rule
  file can bring them back.
- `.gitignore` and `.apersonaignore` in the folder and in every folder below it, read by the `.gitignore` syntax
  (the part of it that matters for notes: `#` comments, `!` to bring a path back, a trailing `/` for folders only, a
  `/` at the start or in the middle to pin a pattern to its own folder, `*` `?` `[a-z]` that never cross a `/`,
  and `**` as a whole path piece). A file deeper down overrides one above it, and within one folder
  `.apersonaignore` is read after `.gitignore`, so it has the last word. As in git, a path inside an ignored folder
  cannot be brought back. Rule files above the chosen folder are not looked at.
- a rule file that is there but cannot be used (a shortcut, not a plain file, over the size limit, or unreadable) means
  the rules of its folder are unknown, so that folder and everything below it is left out whole and named in
  `Walk.rules_unread` with the reason, for the caller to tell the owner. A rule file that is not there is no rules.

No package is used: none is in the lockfile, and the rules fit in this file.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

#: the rule files, in the order they are read inside one folder (the later one has the last word)
RULE_FILES = (".gitignore", ".apersonaignore")
#: folder names never read, at any depth
ALWAYS_DIRS = frozenset({".git", "node_modules"})
#: a file whose name starts with this is never read
ALWAYS_FILE_PREFIX = ".env"
#: a rule file larger than this is read no further (it is a notes folder, not a data dump)
MAX_RULE_BYTES = 1_000_000


class RuleProblem(Enum):
    "Why a rule file that is there could not be used; the value is what the owner is told."

    SYMLINK = "是捷徑"
    NOT_A_FILE = "不是一般檔案"
    TOO_LARGE = f"超過 {MAX_RULE_BYTES // 1_000_000} MB 上限"
    UNREADABLE = "讀不到"


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    negated: bool
    folders_only: bool


def always_excluded(parts: tuple[str, ...], *, is_dir: bool = False) -> bool:
    "A path (its pieces from the chosen folder) that is never read: `.git`/`node_modules` anywhere, `.env*` files."
    folders = parts if is_dir else parts[:-1]
    if any(p in ALWAYS_DIRS for p in folders):
        return True
    return not is_dir and bool(parts) and parts[-1].startswith(ALWAYS_FILE_PREFIX)


def _class(body: str) -> str:
    "A `[...]` body to a regex class; `[!a]` and `[^a]` are negated."
    if body[:1] in ("!", "^"):
        return "[^/" + re.escape(body[1:]).replace(r"\-", "-") + "]"
    return "[" + re.escape(body).replace(r"\-", "-") + "]"


def _translate(body: str) -> str:
    "One gitignore pattern (no leading `/`, no trailing `/`) as the regex source for the path below its folder."
    out: list[str] = []
    pieces = body.split("/")
    last = len(pieces) - 1
    for k, piece in enumerate(pieces):
        if piece == "**":
            if k == last:
                out.append(".+")  # `a/**`: everything inside a, not a itself
            elif k == 0:
                out.append("(?:.*/)?")  # `**/a`: a at any depth
                continue
            else:
                out.append("(?:.*/)?")  # `a/**/b`: zero or more folders between
                continue
        else:
            out.append(_piece(piece))
            if k != last:
                out.append("/")
    return "".join(out)


def _piece(piece: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(piece):
        c = piece[i]
        if c == "\\" and i + 1 < len(piece):
            out.append(re.escape(piece[i + 1]))
            i += 2
            continue
        if c == "*":
            while i < len(piece) and piece[i] == "*":
                i += 1
            out.append("[^/]*")
            continue
        if c == "?":
            out.append("[^/]")
        elif c == "[":
            close = piece.find("]", i + 2 if piece[i + 1 : i + 2] in ("!", "^") else i + 2)
            if close == -1:
                out.append(r"\[")
            else:
                out.append(_class(piece[i + 1 : close]))
                i = close + 1
                continue
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def parse(text: str) -> list[Rule]:
    "The rules in a rule file's text, in order; blank lines and comments are skipped."
    rules: list[Rule] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        # trailing spaces do not count unless escaped with a backslash
        stripped = line.rstrip(" ")
        if stripped.endswith("\\") and len(stripped) < len(line):
            stripped += " "
        line = stripped
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        elif line.startswith("\\#") or line.startswith("\\!"):
            line = line[1:]
        folders_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        line = line.lstrip("/")
        if not line:
            continue
        source = _translate(line)
        try:
            pattern = re.compile(("^" if anchored else "^(?:.*/)?") + source + "$", re.DOTALL)
        except re.error:
            continue  # a pattern that cannot be read matches nothing, as git skips what it cannot parse
        rules.append(Rule(pattern, negated, folders_only))
    return rules


def _load(folder: Path) -> tuple[list[Rule], RuleProblem | None]:
    """The rules of one folder, or why they cannot be known: a rule file that is there but unusable gives no rules at
    all, since the part of them that could be read may not be the part that matters."""
    rules: list[Rule] = []
    for name in RULE_FILES:
        path = folder / name
        try:
            mode = path.lstat()
            if stat.S_ISLNK(mode.st_mode):
                return [], RuleProblem.SYMLINK
            if not stat.S_ISREG(mode.st_mode):
                return [], RuleProblem.NOT_A_FILE
            if mode.st_size > MAX_RULE_BYTES:
                return [], RuleProblem.TOO_LARGE
            rules += parse(path.read_text(encoding="utf-8", errors="replace"))
        except FileNotFoundError:
            continue  # no rule file: no rules, nothing is hidden
        except OSError:
            return [], RuleProblem.UNREADABLE
    return rules, None


def unread_sentence(rules_unread: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> str:
    "The sentence that tells the owner which folders were left out whole because their rule file could not be used."
    named = "、".join(f"{'選的資料夾本身' if folder == '.' else folder}（規則檔{why}）" for folder, why in rules_unread)
    return f"規則檔用不了，所以這些資料夾（含底下）整個不送：{named}。"


def is_ignored(parts: tuple[str, ...], is_dir: bool, levels: list[tuple[tuple[str, ...], list[Rule]]]) -> bool:
    """Whether a path (its pieces from the chosen folder) is left out by the rules, `levels` being the rule lists of its
    folders from the top down, each with the pieces of the folder it belongs to: the last rule that matches wins."""
    state = False
    for base, rules in levels:
        relative = "/".join(parts[len(base) :])
        for rule in rules:
            if rule.folders_only and not is_dir:
                continue
            if rule.pattern.match(relative):
                state = not rule.negated
    return state


class Walk:
    """What a folder holds that is to be read, and how many files were left out. `files` are paths from the folder, in
    sorted order, of the kinds `suffixes` names, none hidden (a dot in front of any piece); `left_out` counts those
    kinds of files that the rules or the always-list kept out, folders left out counted file by file. `rules_unread`
    lists (folder from the chosen one, `.` for it, why) of each folder left out whole because its rule file is there
    but unusable (`RuleProblem`); its files are in `left_out` too."""

    def __init__(self, root: Path, suffixes: frozenset[str] | set[str]) -> None:
        self.root = root
        self.suffixes = suffixes
        self.files: list[str] = []
        self.left_out = 0
        self.rules_unread: list[tuple[str, str]] = []
        self._enter(root, (), [])

    def _enter(self, folder: Path, parts: tuple[str, ...], levels: list[tuple[tuple[str, ...], list[Rule]]]) -> None:
        "Read a folder's rules, then walk it; when its rules cannot be known, leave it out whole and say why."
        rules, problem = _load(folder)
        if problem is None:
            self._visit(folder, parts, [*levels, (parts, rules)])
            return
        self.left_out += self._count(folder)
        self.rules_unread.append(("/".join(parts) or ".", problem.value))

    def _visit(self, folder: Path, parts: tuple[str, ...], levels: list[tuple[tuple[str, ...], list[Rule]]]) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue  # hidden: skipped as it always was, not counted
            below = (*parts, entry.name)
            if entry.is_symlink():  # a shortcut is never followed: it may point anywhere on the machine
                if Path(entry.name).suffix.lower() in self.suffixes:
                    self.left_out += 1
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            if is_dir:
                if entry.name in ALWAYS_DIRS or is_ignored(below, True, levels):
                    self.left_out += self._count(Path(entry.path))
                else:
                    self._enter(Path(entry.path), below, levels)
                continue
            if Path(entry.name).suffix.lower() not in self.suffixes or not entry.is_file(follow_symlinks=False):
                continue
            if always_excluded(below) or is_ignored(below, False, levels):
                self.left_out += 1
            else:
                self.files.append("/".join(below))

    def _count(self, folder: Path) -> int:
        "Files of the kinds read inside a folder that is left out whole (hidden pieces skipped, as everywhere)."
        total = 0
        for _, folders, names in os.walk(folder):
            folders[:] = sorted(f for f in folders if not f.startswith("."))
            total += sum(1 for n in names if not n.startswith(".") and Path(n).suffix.lower() in self.suffixes)
        return total
