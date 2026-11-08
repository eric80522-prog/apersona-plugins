"""Put the three Apersona hooks (開場包, 每輪記憶, 收錄) into the Codex CLI, or take them out again. For Codex only;
Claude Code uses the plugin itself.

    python apersona_codex.py install [--site URL]
    python apersona_codex.py uninstall | on | off | status | consent yes|no

Standard library only, on python 3.9 and up. The hook script and what it imports are copied to
`~/.apersona/codex/bin/` (the commands in Codex's hooks.json carry absolute paths, because Codex gives a hook no plugin
variables), the site goes to `~/.apersona/codex/config.json`, and our three handlers are merged into
`<CODEX_HOME or ~/.codex>/hooks.json` without touching anyone else's. The hooks reach the site with the same device
login as the Claude Code plugin (`~/.apersona/plugin-credentials.json`): when there is none, install runs it (an
address and a short code, then it waits for him to press 允許). No key is read or written anywhere.

`config.toml` is never written: the MCP lines are only printed. Codex reaches a Streamable HTTP MCP server with OAuth
itself (`codex mcp add <name> --url <url>`, then `codex mcp login <name>`, registering by DCR; Codex's MCP page,
read 2026-11-07), so the fixed address `<site>/mcp` needs no token from us at all, and nothing long-lived is written
into Codex's settings.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apersona_login  # noqa: E402
from apersona_safe import checked_site, private_dir  # noqa: E402

HOOK_NAME = "apersona_hook.py"
#: what the hook imports from beside itself, copied with it
HOOK_SIBLINGS = ("apersona_mask.py", "apersona_safe.py", "apersona_login.py", "apersona_projects.py")
BACKUP_TAG = ".apersona-backup-"
# (Codex event, hook kind, timeout in seconds, additionalContextLimit in tokens). Codex cuts a hook's additionalContext
# past about 2,500 tokens by default into a head-and-tail preview (https://developers.openai.com/codex/hooks, read
# 2026-11-07); the hook itself already cuts at MAX_TEXT = 8,000 characters, from the end. A Chinese character can take
# more than one token, so the limit is two tokens a character: Codex never cuts again what the hook let through, and
# never out of the middle.
CONTEXT_LIMIT = 20_000
EVENTS = (
    ("SessionStart", "opening", 5, CONTEXT_LIMIT),
    ("UserPromptSubmit", "turn", 5, CONTEXT_LIMIT),
    ("Stop", "stop", 10, 0),
)
CONSENT_PHRASE = "我同意"


class InstallError(Exception):
    "Something that stops the install or uninstall before it changes anything, in words for the person."


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def data_home() -> Path:
    return Path.home() / ".apersona" / "codex"


def load_hook(path: Path | None = None) -> ModuleType:
    "A hook script (default: the sibling) as a module: the single source of the consent text and of where files live."
    spec = importlib.util.spec_from_file_location("apersona_hook", path or Path(__file__).with_name(HOOK_NAME))
    if spec is None or spec.loader is None:
        raise InstallError("找不到 " + HOOK_NAME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quote(value: str) -> str:
    # Codex runs the command through cmd.exe on Windows and through `$SHELL -lc` elsewhere.
    return f'"{value}"' if os.name == "nt" else shlex.quote(value)


def hook_command(python: str, script: str, kind: str) -> str:
    return f"{quote(python)} {quote(script)} --client codex --kind {kind}"


def hook_groups(python: str, script: str) -> list[tuple[str, dict[str, object]]]:
    groups: list[tuple[str, dict[str, object]]] = []
    for event, kind, timeout, limit in EVENTS:
        handler: dict[str, object] = {
            "type": "command",
            "command": hook_command(python, script, kind),
            "timeout": timeout,
        }
        if limit:
            handler["additionalContextLimit"] = limit
        groups.append((event, {"hooks": [handler]}))
    return groups


def is_ours(entry: object) -> bool:
    command = cast("dict[str, object]", entry).get("command") if isinstance(entry, dict) else None
    return isinstance(command, str) and HOOK_NAME in command and "--client codex" in command


def group_entries(group: object) -> list[object]:
    "The handlers of one hooks.json group ([] when it has no such list)."
    entries = cast("dict[str, object]", group).get("hooks") if isinstance(group, dict) else None
    return cast("list[object]", entries) if isinstance(entries, list) else []


def without_ours(hooks: dict[str, object]) -> dict[str, object]:
    "Every event's groups with our handlers taken out; a group left empty by that goes, other groups stay as they are."
    kept: dict[str, object] = {}
    for event, groups in hooks.items():
        remaining: list[object] = []
        for group in cast("list[object]", groups):
            entries = group_entries(group)
            if any(is_ours(e) for e in entries):
                rest = [e for e in entries if not is_ours(e)]
                if rest:
                    remaining.append({**cast("dict[str, object]", group), "hooks": rest})
            else:
                remaining.append(group)
        if remaining or not groups:
            kept[event] = remaining
    return kept


def read_hooks(path: Path) -> dict[str, object]:
    "hooks.json as a dict with a `hooks` dict of lists; a missing file is an empty one."
    if path.is_symlink():
        raise InstallError(f"{path} 是連結（symlink），不會改寫連結")
    if not path.exists():
        return {"hooks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise InstallError(f"讀不懂 {path}（{type(e).__name__}）") from None
    hooks = cast("dict[str, object]", data).get("hooks", {}) if isinstance(data, dict) else None
    if not isinstance(hooks, dict) or any(not isinstance(v, list) for v in cast("dict[str, object]", hooks).values()):
        raise InstallError(f"{path} 的格式不是 Codex 的 hooks 檔（最外層要有 hooks，裡面每個事件是一個清單）")
    return cast("dict[str, object]", data)


def render_hooks(data: dict[str, object], groups: list[tuple[str, dict[str, object]]]) -> str:
    hooks = without_ours(cast("dict[str, object]", data.get("hooks", {})))
    for event, group in groups:
        hooks[event] = [*cast("list[object]", hooks.get(event, [])), group]
    return json.dumps({**data, "hooks": hooks}, indent=2, ensure_ascii=False) + "\n"


def create_backup(path: Path, old: bytes) -> Path:
    "Copy `old` to <name>.apersona-backup-<stamp>[-N]; an earlier backup is never overwritten."
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    count = 1
    while True:
        backup = path.with_name(f"{path.name}{BACKUP_TAG}{stamp}" + ("" if count == 1 else f"-{count}"))
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            count += 1
            continue
        with os.fdopen(fd, "wb") as handle:
            handle.write(old)
        return backup


def write_replacing(path: Path, data: bytes, *, backup: bool = False, private: bool = False) -> Path | None:
    "Replace the file whole (temp file, then rename), after a backup of the old one when asked. Returns the backup."
    made = create_backup(path, path.read_bytes()) if backup and path.exists() else None
    mode = 0o600 if private or not path.exists() else path.stat().st_mode & 0o777
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return made


def checked_site_of(site: str) -> str:
    try:
        return checked_site(site or apersona_login.DEFAULT_SITE)
    except ValueError as e:
        raise InstallError("--site：" + str(e)) from None


def ensure_login(site: str) -> bool:
    "The device login for `site`, run now when there is none; True when this machine is logged in."
    if apersona_login.logged_in(site):
        print(f"已登入 {site}（與 Claude Code 外掛共用同一份登入）")
        return True
    print(f"這台還沒登入 {site}；開場包、每輪記憶與收錄都要先登入：")
    try:
        done = apersona_login.login(site, lambda line: print(line, flush=True))
    except apersona_login.LoginError as e:
        print(str(e))
        done = False
    if done:
        print(f"登入好了（{site}）")
    else:
        me = f"{quote(sys.executable)} {quote(str(Path(__file__).with_name('apersona_login.py').resolve()))}"
        print(f"還沒登入完；按完允許後執行 {me} login，hook 登入前什麼都不做")
    return done


def command_problem(python: str, script: str) -> str | None:
    # cmd.exe would act on these characters inside the command.
    if os.name == "nt":
        for value in (python, script):
            if "%" in value or '"' in value:
                return f'路徑含有 % 或 "（{value}），Codex 的 hooks 指令會被 shell 誤讀，沒有寫入任何設定'
    return None


def install(site: str) -> int:
    site = checked_site_of(site)
    hooks_path, data = codex_home() / "hooks.json", data_home()
    config_path, script = data / "config.json", data / "bin" / HOOK_NAME
    if config_path.is_symlink():
        raise InstallError(f"{config_path} 是連結（symlink），不會改寫連結")
    problem = command_problem(sys.executable, str(script))
    if problem:
        raise InstallError(problem)
    current = read_hooks(hooks_path)
    text = render_hooks(current, hook_groups(sys.executable, str(script)))
    source = Path(__file__).with_name(HOOK_NAME)
    config = json.dumps({"site": site}, indent=2) + "\n"
    try:
        private_dir(data / "bin")
        write_replacing(script, source.read_bytes())
        for sibling in HOOK_SIBLINGS:
            write_replacing(script.with_name(sibling), source.with_name(sibling).read_bytes())
        write_replacing(config_path, config.encode("utf-8"), private=True)
        backup = None
        if not (hooks_path.exists() and hooks_path.read_text(encoding="utf-8-sig") == text):
            backup = write_replacing(hooks_path, text.encode("utf-8"), backup=True)
    except OSError as e:
        raise InstallError(f"寫入失敗（{type(e).__name__}：{e}）；hooks 沒有裝好") from None
    print(f"已裝好：{hooks_path}" + (f"（原檔備份在 {backup}）" if backup else ""))
    print(f"設定檔：{config_path}（只記網站網址；不放任何金鑰）")
    ensure_login(site)
    ask_consent(script)
    print()
    print("下一步：")
    print("  1. 打開 Codex，執行 /hooks，信任這三個 apersona hook（Codex 要你信任一次才會跑）。")
    print("  2. 把 MCP 加進 Codex（固定網址，Codex 自己走 OAuth 登入，不用金鑰）：")
    print(f"     codex mcp add apersona --url {site}/mcp")
    print("     codex mcp login apersona")
    me = f"{quote(sys.executable)} {quote(str(Path(__file__).resolve()))}"
    print(f"關掉／再打開：{me} off ／ on；看狀態：{me} status；拆掉：{me} uninstall")
    return 0


def uninstall() -> int:
    hooks_path, data = codex_home() / "hooks.json", data_home()
    current = read_hooks(hooks_path)
    config_path = data / "config.json"
    if config_path.is_symlink():
        raise InstallError(f"{config_path} 是連結（symlink），不會動連結")
    try:
        hooks = cast("dict[str, object]", current["hooks"])
        if without_ours(hooks) != hooks:
            backup = write_replacing(hooks_path, render_hooks(current, []).encode("utf-8"), backup=True)
            print(f"已從 {hooks_path} 拿掉 apersona 的 hook（原檔備份在 {backup}）")
        config_path.unlink(missing_ok=True)
        for name in (HOOK_NAME, *HOOK_SIBLINGS):
            (data / "bin" / name).unlink(missing_ok=True)
    except OSError as e:
        raise InstallError(f"刪除失敗（{type(e).__name__}：{e}）") from None
    print("已刪掉設定檔與複製的 hook 腳本；記錄、同意檔與暫存留在 " + str(data))
    print("MCP 連線要自己拿掉：codex mcp remove apersona")
    print(
        "登入共用 Claude Code 外掛的那一份；要登出這台："
        + quote(sys.executable)
        + " "
        + quote(str(Path(__file__).with_name("apersona_login.py").resolve()))
        + " logout"
    )
    return 0


def set_switch(on: bool, project: str | None = None) -> int:
    if project is not None:
        try:
            load_hook().set_project(project, on)
        except (OSError, ValueError) as error:
            raise InstallError(str(error)) from None
        print("專案 hook 已" + ("開啟；只收錄重新開啟後的內容" if on else "停用"))
        return 0
    path = data_home() / "apersona.json"
    try:
        write_replacing(path, json.dumps({"on": on}).encode("utf-8"))
    except OSError as e:
        raise InstallError(f"寫不進 {path}（{type(e).__name__}）") from None
    print("開場包與每輪記憶已" + ("打開" if on else "關掉"))
    return 0


def status() -> int:
    data = data_home()
    hook = load_hook()
    installed = (data / "config.json").exists() and (data / "bin" / HOOK_NAME).exists()
    print("已安裝" if installed else "沒有安裝")
    try:
        site = str(json.loads((data / "config.json").read_text(encoding="utf-8")).get("site") or "")
    except OSError:  # one exception per clause: the formatter would drop the parentheses python 3.9 needs
        site = ""
    except ValueError:
        site = ""
    except AttributeError:
        site = ""
    if site:
        print("登入：" + ("已登入 " + site if apersona_login.logged_in(site) else "還沒登入 " + site))
    print("開場包與每輪記憶：" + ("關" if hook.switched_off(data) else "開"))
    print("收錄：" + ("開（Codex 版同意）" if hook.consent("codex") else "關"))
    return 0


def record_consent(script: Path, answer: str) -> bool:
    "Have the copied hook write the consent file (its text's hash is the one recorded); True when it did."
    try:
        load_hook(script).write_consent("codex", answer == "yes")
    except OSError:
        return False
    return True


def ask_consent(script: Path) -> bool:
    """Show the Codex consent text (read from the copied hook, whose hash is the one recorded) and turn 收錄 on only
    for the exact phrase typed by a person at a terminal; any other answer records it off. True when it is on."""
    hook = load_hook(script)
    was_on = bool(hook.consent("codex"))
    run_it = f"{quote(sys.executable)} {quote(str(Path(__file__).resolve()))} consent"
    if not sys.stdin.isatty():
        print(
            "收錄現在是開的。要關掉：" + run_it + " no"
            if was_on
            else "收錄現在是關的。要打開，請在終端機執行（會先給你看同意書）：" + run_it + " yes"
        )
        return was_on
    print()
    print("要不要開啟收錄？目前是" + ("開" if was_on else "關") + "。同意書：")
    print("  " + hook.CODEX_CONSENT_TEXT)
    try:
        answer = input(f"要開，請輸入「{CONSENT_PHRASE}」；其他任何輸入都是不開：").strip()
    except EOFError:
        answer = ""
    except OSError:
        answer = ""
    if answer != CONSENT_PHRASE:
        if not record_consent(script, "no"):
            print("記錄「不同意」失敗；請執行 " + run_it + " no 確認收錄是關的。")
            return was_on
        print("收錄已關閉。之後想開：" + run_it + " yes")
        return False
    if not record_consent(script, "yes"):
        print("記錄同意失敗，收錄維持原狀。")
        return was_on
    print("收錄已開啟；隨時可用 consent no 撤回。")
    return True


def set_consent(answer: str) -> int:
    script = data_home() / "bin" / HOOK_NAME
    if not script.exists():
        raise InstallError("還沒安裝；先執行 install")
    if answer == "yes":
        return 0 if ask_consent(script) else 1
    if not record_consent(script, "no"):
        return 1
    print("收錄已關閉。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apersona_codex.py", description="把 Apersona 的 hook 裝進 Codex")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("install", help="裝三個 hook（沒登入就先跑裝置碼登入）")
    add.add_argument("--site", default="", help="只有連自己架的網站才填；預設 https://www.apersonaai.com")
    for name in ("uninstall", "on", "off", "status"):
        command = commands.add_parser(name)
        if name in ("on", "off"):
            command.add_argument("--project", metavar="DIRECTORY")
    commands.add_parser("consent").add_argument("answer", choices=("yes", "no"))
    args = parser.parse_args(argv)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    try:
        if args.command == "install":
            return install(args.site)
        if args.command == "uninstall":
            return uninstall()
        if args.command == "consent":
            return set_consent(args.answer)
        if args.command == "status":
            return status()
        return set_switch(args.command == "on", args.project)
    except InstallError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
