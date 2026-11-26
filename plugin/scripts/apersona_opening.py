"""Get the Apersona opening pack for Cursor or an explicitly wired Antigravity hook."""

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
            name in str(item.get("command", "")) for name in ("apersona_opening.py", "apersona_cursor.py", "apersona_feed.py")
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
    paths = fields.get("workspace_roots") or fields.get("workspacePaths")
    cwd = fields.get("cwd") or (paths[0] if isinstance(paths, list) and paths else None)
    if fields.get("stop_hook_active") is True or not allows(cwd):
        return {}
    session = fields.get("conversation_id") or fields.get("conversationId") or fields.get("session_id")
    try:
        started = time.monotonic()
        resolved, key = login_bearer(site, BUDGET_S)
        text, _ = fetch_text(
            opening_request(resolved, key, session, cwd),
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
        return {"additional_context": fenced(visible) + f'\n本段對話識別：{identifier}。叫 apersona 的 lookup、recall、opening 時帶 conversation_id="{identifier}"。'}
    return {"additional_context": fenced(visible)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("install", "uninstall", "run"))
    parser.add_argument("--site", default="")
    parser.add_argument("--platform", choices=("cursor", "antigravity"), default="cursor")
    parser.add_argument("--event", default="")
    args = parser.parse_args()
    if args.command == "run":
        raw = sys.stdin.read()
        if args.platform == "antigravity":
            event = hook_fields(raw).get("hook_event_name") or args.event
            text = run(raw, args.site).get("additional_context") if event == "SessionStart" else None
            result = {"injectSteps": [{"systemMessage": {"systemMessage": text}}]} if text else {}
        else:
            event = hook_fields(raw).get("hook_event_name") or args.event
            result = run(raw, args.site) if event in ("sessionStart", "") else {}
        print(json.dumps(result, ensure_ascii=False))
    elif args.platform == "cursor":
        install(args.command == "install", checked_site(args.site) if args.site else "")
    else:
        parser.error("Antigravity 開場請由本人把 SessionStart hook 接上")
    return 0


if __name__ == "__main__":
    utf8_pipes()
    raise SystemExit(main())
