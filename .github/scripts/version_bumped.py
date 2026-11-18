"""A pull request that changes what users install must raise the plugin's version.

Usage: python .github/scripts/version_bumped.py <base ref>

Plugin managers update by version: when plugin/ differs from the base but plugin/.claude-plugin/plugin.json keeps the
base's version (or lowers it), users keep the old copy. That fails here, naming the fix.
"""

from __future__ import annotations

import json
import subprocess
import sys

MANIFEST = "plugin/.claude-plugin/plugin.json"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in json.loads(text)["version"].split("."))


def main() -> int:
    base = sys.argv[1]
    changed = git("diff", "--name-only", f"{base}...HEAD", "--", "plugin/").split()
    if not changed:
        print("plugin/ unchanged: nothing reaches users, no version needed")
        return 0
    old = version(git("show", f"{base}:{MANIFEST}"))
    new = version(open(MANIFEST, encoding="utf-8").read())
    was, now = ".".join(map(str, old)), ".".join(map(str, new))
    if new > old:
        print(f"plugin/ changed ({len(changed)} files) and the version goes {was} -> {now}")
        return 0
    print(f"plugin/ changed ({len(changed)} files) but {MANIFEST} is {now}, not above main's {was}:")
    print("users would keep the old copy.")
    print("Raise its version (and USER_AGENT in plugin/scripts/apersona_safe.py to match) in this pull request.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
