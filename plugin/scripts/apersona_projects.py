"""The hook switches: one per project (`.apersona.json`, an off parent applies to its children) and the client's
global one (`/apersona:on`／`off`); turning one on never grants capture consent.

Which project a folder is (`project_key`, 規矩分專案): the folder holding its `.git`, and for a git worktree the main
checkout it was made from, so every worktree of one repository is one project. Only a digest of that path leaves the
machine (`PROJECT_PREFIX` and 16 hex digits): the opening asks for the rules of this project by it, and `with_project`
puts it in place of the folder of every row of an AI conversation record before the record is sent.

Every switch is written the same way (`write_switch`): with the time it last changed, so turning it on again starts a
new time boundary and nothing typed while it was off is recovered from an old transcript or spool (`allows`).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import cast


def project_path(cwd: object) -> Path:
    return Path(cwd).resolve() if isinstance(cwd, str) and cwd else Path.cwd().resolve()


#: what a project's key starts with; the site takes a folder only in this form (`intake.chats.PROJECT_KEY`)
PROJECT_PREFIX = "project:"


def project_root(cwd: object) -> Path:
    """The folder of the project `cwd` is in: the nearest folder up holding `.git`; when that `.git` is a worktree's
    file pointing into another checkout's `.git/worktrees/`, that checkout. A folder in no repository (or one gone
    from this machine) is its own project."""
    path = project_path(cwd)
    for directory in (path, *path.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return directory
        if not marker.is_file():
            continue
        try:
            pointer = marker.read_text(encoding="utf-8").strip()
        except OSError:
            return directory
        if not pointer.startswith("gitdir:"):
            return directory
        gitdir = Path(pointer[len("gitdir:") :].strip())
        gitdir = gitdir if gitdir.is_absolute() else (directory / gitdir).resolve()
        if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
            return gitdir.parent.parent.parent
        return directory
    return path


def project_key(cwd: object) -> str:
    "The project of `cwd` as it leaves the machine: a digest of `project_root`, never the path."
    return PROJECT_PREFIX + hashlib.sha256(str(project_root(cwd)).encode("utf-8")).hexdigest()[:16]


def with_project(row: object) -> object:
    """A row of a Claude Code (`cwd`) or Codex (`payload.cwd`) conversation record with its folder put as the
    project's key; any other row as it is."""
    if not isinstance(row, dict):
        return row
    fields = cast("dict[str, object]", row)
    if _folder(fields.get("cwd")):
        fields = {**fields, "cwd": project_key(fields["cwd"])}
    payload = fields.get("payload")
    if isinstance(payload, dict) and _folder(cast("dict[str, object]", payload).get("cwd")):
        inner = cast("dict[str, object]", payload)
        fields = {**fields, "payload": {**inner, "cwd": project_key(inner["cwd"])}}
    return fields


def _folder(value: object) -> bool:
    return isinstance(value, str) and bool(value) and not value.startswith(PROJECT_PREFIX)


def allows(cwd: object, at: str | None = None, switch: Path | None = None) -> bool:
    """Whether the hooks may run for `cwd` (and, with `at`, whether a message written then may be taken): every
    project switch from `cwd` up and the client's global `switch` must be on, and `at` after each one's last turn on.
    A missing switch is on; one with no time (written by hand, or by an older plugin) sets no boundary."""
    path = project_path(cwd)
    configs = [directory / ".apersona.json" for directory in (path, *path.parents)]
    for config in [switch, *configs] if switch is not None else configs:
        try:
            record = json.loads(config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except OSError:
            return False
        except ValueError:
            return False
        if not isinstance(record, dict):
            return False
        settings = cast("dict[str, object]", record)
        if settings.get("on") is not True:
            return False
        if at is not None and "since" in settings:
            try:
                given = settings.get("since")
                if not isinstance(given, str):
                    return False
                since = datetime.fromisoformat(given.replace("Z", "+00:00"))
                written = datetime.fromisoformat(at.replace("Z", "+00:00"))
                if since.tzinfo is None or written.tzinfo is None or written < since:
                    return False
            except ValueError:
                return False
    return True


def set_project(cwd: str, on: bool) -> None:
    directory = project_path(cwd)
    if not directory.is_dir():
        raise ValueError("專案目錄不存在")
    write_switch(directory / ".apersona.json", on)


def write_switch(path: Path, on: bool) -> None:
    "Turn one switch on or off from now; other settings in its file are kept, a malformed file is refused."
    if path.is_symlink():
        raise ValueError("開關檔是連結，不會改寫")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        record = {}
    if not isinstance(record, dict):
        raise ValueError("開關檔格式不是物件")
    settings = cast("dict[str, object]", record)
    settings.update(on=on, since=datetime.now(timezone.utc).isoformat())  # noqa: UP017
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(settings, out, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
