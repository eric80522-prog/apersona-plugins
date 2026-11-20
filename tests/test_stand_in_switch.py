"""A local reply pause must not grant capture or turn the whole plugin on."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parents[1] / "plugin/scripts/apersona_hook.py"


def command(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(HOOK), "--stand-in", *args],
        env={**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path)},
        capture_output=True,
        timeout=10,
    )


def test_reply_switch_preserves_global_off_consent_counts_and_forgets_paused_feedback(tmp_path: Path) -> None:
    data = tmp_path / "apersona"
    data.mkdir()
    global_switch = tmp_path / "apersona.json"
    global_switch.write_text('{"on": false}')
    consent = data / "capture-consent.json"
    consent.write_text('{"on": true, "ownerWords": "只收我輸入的"}', encoding="utf-8")
    before = consent.read_bytes()
    state = data / "stand_in.json"
    state.write_text(json.dumps({"stood_in": 4, "overturned": 1, "sessions": {"old": {"answered": True}}}))
    for mode in ("off", "on"):
        result = command(tmp_path, mode)
        assert result.returncode == 0, result.stderr
        assert json.loads(global_switch.read_text()) == {"on": False}
        assert consent.read_bytes() == before
        kept = json.loads(state.read_text())
        assert (kept["stood_in"], kept["overturned"]) == (4, 1)
        assert json.loads((data / "stand-in-switch.json").read_text()) == {"on": mode == "on"}
    assert kept["sessions"] == {}


@pytest.mark.parametrize(
    "extra", [("--client", "codex"), ("--switch", "on"), ("--consent", "yes"), ("--project-directory", ".")]
)
def test_mixed_scope_is_rejected_without_creating_settings(tmp_path: Path, extra: tuple[str, ...]) -> None:
    assert command(tmp_path, "off", *extra).returncode != 0
    assert list(tmp_path.iterdir()) == []


def test_unreadable_settings_are_not_overwritten(tmp_path: Path) -> None:
    data = tmp_path / "apersona"
    data.mkdir()
    state = data / "stand_in.json"
    state.write_text('{"stood_in":')
    assert command(tmp_path, "on").returncode != 0
    assert state.read_text() == '{"stood_in":'
