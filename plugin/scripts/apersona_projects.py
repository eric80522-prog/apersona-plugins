"""Project-local permission to run hooks; enabling never grants capture consent.

An off parent applies to its children. Re-enabling starts a new time boundary so
prompts typed while disabled are never recovered from an old transcript or spool.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import cast


def project_path(cwd: object) -> Path:
    return Path(cwd).resolve() if isinstance(cwd, str) and cwd else Path.cwd().resolve()


def allows(cwd: object, at: str | None = None) -> bool:
    path = project_path(cwd)
    for directory in (path, *path.parents):
        config = directory / ".apersona.json"
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
        if at is not None:
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
    path = directory / ".apersona.json"
    if path.is_symlink():
        raise ValueError("專案開關是連結，不會改寫")
    # Keep unrelated settings; refuse malformed input instead of overwriting it.
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        record = {}
    if not isinstance(record, dict):
        raise ValueError("專案開關格式不是物件")
    settings = cast("dict[str, object]", record)
    settings.update(on=on, since=datetime.now(timezone.utc).isoformat())  # noqa: UP017
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(settings, out, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
