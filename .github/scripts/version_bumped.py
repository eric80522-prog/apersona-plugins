"""A pull request that changes what users install must raise the version of each package it changes.

Usage: python .github/scripts/version_bumped.py <base ref>

plugin/ holds four packages, each installed and updated by its own manifest's version: the Codex, Gemini and
Antigravity packages in their own folders, and the Claude Code plugin in the rest of plugin/. Plugin managers update by
version: when a package's files differ from the base but its manifest keeps the base's version (or lowers it), users
keep the old copy. That fails here, naming the package and the fix.
"""

from __future__ import annotations

import json
import subprocess
import sys

#: package folder -> its manifest; a file belongs to the longest folder that holds it
PACKAGES = {
    "plugin/codex/": "plugin/codex/.codex-plugin/plugin.json",
    "plugin/gemini/": "plugin/gemini/gemini-extension.json",
    "plugin/antigravity/": "plugin/antigravity/.antigravity-plugin/plugin.json",
    "plugin/": "plugin/.claude-plugin/plugin.json",
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in json.loads(text)["version"].split("."))


def package(path: str) -> str:
    return max((folder for folder in PACKAGES if path.startswith(folder)), key=len)


def main() -> int:
    base = sys.argv[1]
    changed: dict[str, int] = {}
    for path in git("diff", "--name-only", f"{base}...HEAD", "--", "plugin/").split():
        changed[package(path)] = changed.get(package(path), 0) + 1
    if not changed:
        print("plugin/ unchanged: nothing reaches users, no version needed")
        return 0
    failed = False
    for folder, count in sorted(changed.items()):
        manifest = PACKAGES[folder]
        old = version(git("show", f"{base}:{manifest}"))
        with open(manifest, encoding="utf-8") as handle:
            new = version(handle.read())
        was, now = ".".join(map(str, old)), ".".join(map(str, new))
        if new > old:
            print(f"{folder} changed ({count} files) and its version goes {was} -> {now}")
            continue
        failed = True
        print(f"{folder} changed ({count} files) but {manifest} is {now}, not above main's {was}:")
        print("  users of that package would keep the old copy. Raise its version in this pull request.")
        if folder == "plugin/":
            print("  For the Claude Code plugin, raise USER_AGENT in plugin/scripts/apersona_safe.py to match.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
