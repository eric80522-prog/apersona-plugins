"""Install or run only the Cursor SessionStart hook for the Apersona opening pack."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import cast

from apersona_hook import BUDGET_S, MAX_TEXT, OPENING_CUT_NOTICE, MissError, clean, fenced, fetch_text, hook_fields, login_bearer, opening_request
from apersona_projects import allows
from apersona_safe import checked_site, read_json, utf8_pipes, write_replacing


def install(on: bool, site: str) -> None:
    path = Path.home() / ".cursor" / "hooks.json"
    raw = read_json(path, {"version": 1, "hooks": {}})
    if not isinstance(raw, dict) or not isinstance(raw.get("hooks"), dict):
        raise ValueError(f"{path} 的 hooks 格式無法讀取")
    hooks = cast("dict[str, list[object]]", raw["hooks"])
    kept = [
        item for item in hooks.get("sessionStart") or []
        if not (isinstance(item, dict) and any(
            name in str(item.get("command", "")) for name in ("apersona_cursor.py", "apersona_feed.py")
        ))
    ]
    if on:
        command = f"{json.dumps(sys.executable)} {json.dumps(str(Path(__file__).resolve()))} run"
        if site:
            command += " --site " + json.dumps(site)
        kept.append({"command": command})
    if kept:
        hooks["sessionStart"] = kept
    else:
        hooks.pop("sessionStart", None)
    write_replacing(path, (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), backup=True)
    print(f"開場 hook 已{'安裝' if on else '移除'}：{path}")


def run(raw: str, site: str) -> dict[str, str]:
    fields = hook_fields(raw)
    if not allows(fields.get("cwd")):
        return {}
    session = fields.get("conversation_id") or fields.get("session_id")
    try:
        started = time.monotonic()
        resolved, key = login_bearer(site, BUDGET_S)
        text, _ = fetch_text(
            opening_request(resolved, key, session, fields.get("cwd")),
            BUDGET_S - (time.monotonic() - started),
        )
    except MissError:
        return {}
    visible = clean(text).strip()
    if not visible:
        return {}
    if len(visible) > MAX_TEXT:
        visible = OPENING_CUT_NOTICE + "\n" + visible[:MAX_TEXT]
    if isinstance(session, str) and session:
        identifier = "".join(clean(session).split())
        return {"additional_context": fenced(visible) + f'\n本段對話識別：{identifier}。'}
    return {"additional_context": fenced(visible)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("install", "uninstall", "run"))
    parser.add_argument("--site", default="")
    args = parser.parse_args()
    if args.command == "run":
        print(json.dumps(run(sys.stdin.read(), args.site), ensure_ascii=False))
    else:
        install(args.command == "install", checked_site(args.site) if args.site else "")
    return 0


if __name__ == "__main__":
    utf8_pipes()
    raise SystemExit(main())
