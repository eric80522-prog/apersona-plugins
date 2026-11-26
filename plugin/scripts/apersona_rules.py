#!/usr/bin/env python3
"""規矩檔修改提案: the rules his opening carries for this project that the project's rule files
do not say yet, proposed as lines to add to the file kept out of git; written only after his own yes.

The rule files are read on this machine only and never sent: the site gives the rules (`GET /api/dev/rules`, the
same rules the opening for this project carries), the comparison is here. Standard library only, like the hooks.

- `propose [--cwd <folder>]`  prints the project's rule files read (`RULE_FILES`; a link to a file already read, as
                               `AGENTS.md` often is to `CLAUDE.md`, is read once), each rule of the opening those
                               files do not say (`written`: by the letters, no model), the lines to add to
                               `CLAUDE.local.md` (his own, kept out of git: a personal rule is never proposed for the
                               shared file) and the proposal's number. Writes nothing.
- `apply <number> [--cwd …]`  works the proposal out again and, only when it is the one with that number (the rules
                               and the files as he saw them), adds its lines under `SECTION` in `CLAUDE.local.md`.
                               Run only after his own yes.
- `flow`                       流程話的出口 (16.2 第 3 項): words he says again that are no rule (「狀態回報」),
                               the ones said in the most conversations, each with its number and exit: 以後替我回
                               (`stand_in`, only words that tell the AI to go on) or 做成指令 (`command`).
- `exit <id> stand_in`         his choice, kept on the site (`POST /api/dev/flow-exit`): 代答 names those words.
- `exit <id> command --name <n>`  his choice, then the slash command `/<n>` written on this machine
                               (`<CLAUDE_CONFIG_DIR or ~/.claude>/commands/<n>.md`, his words as its prompt; a command
                               of that name already there is never overwritten). Both only after his own yes.

Exit codes: 0 done (or nothing to propose), 3 refused (not logged in, the site refused, the proposal changed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apersona_hook import MissError, fetch_json, login_bearer  # noqa: E402
from apersona_projects import project_key, project_root  # noqa: E402
from apersona_safe import EXIT_REFUSED, clean, exit_line, fenced  # noqa: E402
from apersona_stats import SAME_SHARE, pairs  # noqa: E402

#: the files Claude Code and Codex read a project's rules from; the last is his own, the one a proposal adds to
RULE_FILES = ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md")
OWN_FILE = RULE_FILES[-1]
#: the heading the lines he takes go under in his own file
SECTION = "## 長期規矩（Apersona 提議、本人說好才加）"
FETCH_S = 10.0


def site_json(path: str, query: dict[str, str] | None = None, body: dict[str, object] | None = None) -> object:
    "The site's JSON answer at `path` (GET with `query`, or POST of `body`), with the device login."
    site, key = login_bearer(os.environ.get("CLAUDE_PLUGIN_OPTION_SITE", ""), FETCH_S)
    url = site.rstrip("/") + path + ("?" + urllib.parse.urlencode(query) if query else "")
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json", "Content-Type": "application/json"}
    data = None if body is None else json.dumps(body).encode("utf-8")
    return fetch_json(urllib.request.Request(url, data=data, headers=headers), FETCH_S)


def listed(cwd: Path, name: str) -> list[dict[str, object]]:
    "One list the site gives for the project of `cwd`: `rules` (what its opening carries) or `flow` (流程話)."
    body = site_json("/api/dev/rules", {"project": project_key(str(cwd))})
    found = cast("dict[str, object]", body).get(name) if isinstance(body, dict) else None
    if not isinstance(found, list):
        raise MissError("bad_response")
    rows = [cast("dict[str, object]", r) for r in cast("list[object]", found) if isinstance(r, dict)]
    return [r for r in rows if isinstance(r.get("text"), str)]


def rules_of(cwd: Path) -> list[dict[str, object]]:
    return listed(cwd, "rules")


def rule_files(root: Path) -> list[tuple[str, str]]:
    "(name, text) of each rule file of the project there is, a file reached again through a link left out."
    seen: set[Path] = set()
    out: list[tuple[str, str]] = []
    for name in RULE_FILES:
        path = root / name
        try:
            real = path.resolve(strict=True)
            if real in seen:
                continue
            seen.add(real)
            out.append((name, path.read_text(encoding="utf-8")))
        except OSError:  # separate clauses: the formatter would drop the parentheses python 3.9 needs
            continue
        except UnicodeDecodeError:
            continue
    return out


def written(rule: str, texts: list[str]) -> bool:
    "Whether a file says the rule: `SAME_SHARE` of its character pairs stand in one of its lines."
    mine = pairs(rule)
    return any(len(mine & pairs(line)) >= SAME_SHARE * len(mine) for text in texts for line in text.splitlines())


def one_line(text: str) -> str:
    return " ".join(clean(text).split())


def proposal(root: Path, rules: list[dict[str, object]]) -> tuple[list[tuple[str, str]], list[str], str]:
    "(the files read, the lines to add, the proposal's number: the lines and every file's text as they are now)."
    files = rule_files(root)
    texts = [text for _, text in files]
    lines = list(dict.fromkeys(f"- {one_line(str(r['text']))}" for r in rules if not written(str(r["text"]), texts)))
    number = hashlib.sha256(json.dumps([str(root), files, lines], ensure_ascii=False).encode("utf-8")).hexdigest()
    return files, lines, number[:12]


def with_lines(text: str, lines: list[str]) -> str:
    "His own file with `lines` added at the end of `SECTION` (the section made at the end when it is not there)."
    rows = text.splitlines()
    if SECTION not in rows:
        return text.rstrip("\n") + ("\n\n" if text.strip() else "") + "\n".join([SECTION, "", *lines]) + "\n"
    start = rows.index(SECTION)
    end = start + 1
    while end < len(rows) and not rows[end].startswith("## "):
        end += 1
    while end > start + 1 and not rows[end - 1].strip():
        end -= 1
    return "\n".join([*rows[:end], *lines, *rows[end:]]) + "\n"


def kept_out_of_git(root: Path) -> bool | None:
    "Whether git leaves his own file out (None: not a repository, or git cannot be asked)."
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", OWN_FILE], capture_output=True, timeout=5, check=False
        )
    except OSError:
        return None
    except subprocess.SubprocessError:
        return None
    return {0: True, 1: False}.get(done.returncode)


def propose(cwd: Path) -> int:
    root = project_root(str(cwd))
    rules = rules_of(cwd)
    files, lines, number = proposal(root, rules)
    read = "、".join(name for name, _ in files) or "沒有"
    print(f"這個專案（{root}）的規則檔：{read}（只在這台讀，不上傳）")
    print(f"開場包裡的規矩 {len(rules)} 條，規則檔沒寫的 {len(lines)} 條。")
    if not lines:
        print("沒有要加的。")
        return 0
    print(f"提議加進 {root / OWN_FILE}（你自己的、不入版控的檔；共用的規則檔不動）的「{SECTION[3:]}」：")
    print(fenced("\n".join(lines)))
    if kept_out_of_git(root) is False:
        print(f"注意：{OWN_FILE} 現在沒被 .gitignore 擋，加進去之前先把它加進 .gitignore。")
    print(
        f'提議編號：{number}。本人親口說好，才執行：python3 "{Path(__file__).resolve()}" apply {number} --cwd "{cwd}"'
    )
    return 0


def apply(cwd: Path, number: str) -> int:
    root = project_root(str(cwd))
    _, lines, now = proposal(root, rules_of(cwd))
    if now != number or not lines:
        print("規矩或規則檔在提議之後變了，沒有寫入；重跑 propose，再給本人看一次。")
        print(exit_line(EXIT_REFUSED))
        return EXIT_REFUSED
    own = root / OWN_FILE
    before = own.read_text(encoding="utf-8") if own.is_file() else ""
    own.write_text(with_lines(before, lines), encoding="utf-8")
    print(f"已加進 {own}：{len(lines)} 條。")
    return 0


#: what each exit is called to him
EXITS = {"stand_in": "以後替我回", "command": "做成指令", "": "還沒選"}
#: a command's name: what he types after the slash
COMMAND_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def flow(cwd: Path) -> int:
    sayings = listed(cwd, "flow")
    if not sayings:
        print("還沒有重複講、又不是規矩的話（流程話）。")
        return 0
    print("你重複講、但不是規矩的話（流程話），各給一個出口：")
    for r in sayings:
        offer = "以後替我回或做成指令" if r.get("canStandIn") is True else "做成指令"
        now = EXITS.get(str(r.get("exit")), "")
        print(f"{r['id']}. 「{one_line(str(r['text']))}」{r.get('conversations')} 段對話；現在：{now}；可選：{offer}")
    me = Path(__file__).resolve()
    print(f'本人選好才執行：python3 "{me}" exit <編號> stand_in，或 exit <編號> command --name <指令名>')
    return 0


def choose(rule: str, chosen: str, name: str) -> int:
    path = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "commands" / f"{name}.md"
    said = next((one_line(str(r["text"])) for r in listed(Path.cwd(), "flow") if str(r.get("id")) == rule), "")
    if not said or (chosen == "command" and (not COMMAND_NAME.fullmatch(name) or path.exists())):
        print(f"沒有這句流程話，或指令名不是小寫英數與 -、或已有同名的指令（{path}）；沒有改任何東西。")
        print(exit_line(EXIT_REFUSED))
        return EXIT_REFUSED
    site_json("/api/dev/flow-exit", body={"ruleId": int(rule), "exit": chosen})
    if chosen == "command":
        path.parent.mkdir(parents=True, exist_ok=True)
        head = f"---\ndescription: 你常打的「{said}」（Apersona 流程話做成的指令）\n---\n\n"
        path.write_text(head + said + "\n", encoding="utf-8")
        print(f"已做成指令 /{name}（{path}）：打 /{name} 就等於打「{said}」。")
    else:
        print(f"已選「{said}」：{EXITS[chosen]}。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("propose", "apply", "flow", "exit"))
    parser.add_argument("number", nargs="?", default="")
    parser.add_argument("exit", nargs="?", choices=("stand_in", "command", ""), default="")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--name", default="")
    args = parser.parse_args(argv)
    try:
        if args.action == "flow":
            return flow(args.cwd)
        if args.action == "exit":
            return choose(args.number, args.exit, args.name)
        return apply(args.cwd, args.number) if args.action == "apply" else propose(args.cwd)
    except MissError as e:
        print(f"網站沒給或沒接受（{e}）；沒有改任何東西。還沒登入就先 /apersona:login。")
        print(exit_line(EXIT_REFUSED))
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
