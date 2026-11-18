"""Users install the plugin folder from this public repo: what is in it must be fit to be read there."""

from __future__ import annotations

import ast
import re
from pathlib import Path

PLUGIN = Path(__file__).parents[1] / "plugin"
#: names that only mean something inside the private product repo, and the install line that needs its key
PRIVATE = re.compile(
    r"Eric|PRD|AGENTS(?!\.md)|研究庫|產品庫|persona_framework|git\+ssh|v5\.0|M3-\d|機台|src/apersona|plugin/bin"
)


def public_files() -> list[Path]:
    return [p for p in sorted(PLUGIN.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]


def test_nothing_in_the_plugin_folder_names_the_private_repo_or_its_paths():
    found = {
        p.relative_to(PLUGIN).as_posix(): sorted(set(PRIVATE.findall(p.read_text(encoding="utf-8"))))
        for p in public_files()
    }
    assert {name: hits for name, hits in found.items() if hits} == {}


def test_public_scripts_keep_python_3_9_syntax_even_when_developed_on_a_newer_python():
    for script in (PLUGIN / "scripts").glob("*.py"):
        ast.parse(script.read_text(encoding="utf-8"), filename=str(script), feature_version=(3, 9))
