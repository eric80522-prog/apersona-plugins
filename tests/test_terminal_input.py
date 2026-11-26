"""Terminal detection must reject redirection, including Windows NUL's misleading isatty result."""

import importlib.util
import io
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "plugin/scripts/apersona_safe.py"


def safe():
    spec = importlib.util.spec_from_file_location("safe_terminal_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("redirected", ["pipe", "null"])
def test_real_redirected_stdin_is_never_a_terminal(redirected):
    code = (
        f"import sys; sys.path.insert(0, {str(SCRIPT.parent)!r}); "
        "from apersona_safe import terminal_input; print(terminal_input())"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE if redirected == "pipe" else subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0 and result.stdout.strip() == "False"


@pytest.mark.parametrize("isatty", [False, True])
def test_non_windows_keeps_its_terminal_semantics(monkeypatch, isatty):
    module = safe()
    stream = io.StringIO()
    monkeypatch.setattr(stream, "isatty", lambda: isatty)
    monkeypatch.setattr(module.sys, "stdin", stream)
    monkeypatch.setattr(module.sys, "platform", "linux")
    assert module.terminal_input() is isatty


def test_missing_or_closed_stdin_is_not_a_terminal(monkeypatch):
    module = safe()
    monkeypatch.setattr(module.sys, "stdin", None)
    assert module.terminal_input() is False
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(module.sys, "stdin", closed)
    assert module.terminal_input() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console handle contract")
def test_real_windows_console_is_accepted_but_null_character_device_is_not():
    code = f"""
import sys, os
sys.path.insert(0, {str(SCRIPT.parent)!r})
from apersona_safe import terminal_input
with open('CONIN$') as console:
    sys.stdin = console
    assert terminal_input()
with open(os.devnull) as null:
    sys.stdin = null
    assert null.isatty()
    assert not terminal_input()
print('console accepted; NUL refused')
"""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run(
        [sys.executable, "-c", code],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startup,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "console accepted; NUL refused"
