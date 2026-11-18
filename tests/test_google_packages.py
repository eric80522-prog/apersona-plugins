"""Installable Google packages must stay isolated from device-login adapters."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"


def test_google_packages_use_host_native_remote_transport():
    antigravity = json.loads((PLUGIN / "antigravity/mcp_config.json").read_text())
    gemini = json.loads((PLUGIN / "gemini/gemini-extension.json").read_text())
    ag = antigravity["mcpServers"]["apersona"]
    gm = gemini["mcpServers"]["apersona"]
    assert ag["serverUrl"] == gm["httpUrl"] == "https://www.apersonaai.com/mcp"
    for config in (ag, gm):
        # No local transport, stored credential, trust bypass or mandatory variable.
        assert not ({"command", "args", "headers", "env", "oauth", "trust", "variables"} & config.keys())
    assert all(value["eager"] for value in ag["tools"].values())
    assert {"opening", "lookup", "recall"} <= ag["tools"].keys()
    assert not (PLUGIN / "gemini/hooks").exists()


@pytest.mark.parametrize("platform", ["antigravity", "gemini"])
def test_google_skills_are_self_contained(platform, tmp_path):
    installed = tmp_path / platform
    shutil.copytree(PLUGIN / platform, installed)
    skill = installed / "skills/apersona/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\nname: apersona\n")
    assert "description:" in text.split("---", 2)[1]
    assert not any(path.is_symlink() for path in installed.rglob("*"))
    assert not (installed / "scripts/apersona_login.py").exists()


@pytest.mark.parametrize("event", ["SessionStart", "PreInvocation"])
def test_installed_antigravity_guidance_does_not_expose_input(event, tmp_path):
    installed = tmp_path / "installed plugin"
    shutil.copytree(PLUGIN / "antigravity", installed)
    hooks = json.loads((installed / "hooks.json").read_text())
    handler = hooks["apersona-guidance"][event][0]
    # Exercise the actual manifest target after relocation, outside the repo.
    target = handler["command"].split('"')[1].replace("${PLUGIN_ROOT}", str(installed))
    assert Path(target).resolve().is_relative_to(installed.resolve())
    secret = "private-owner-input-do-not-echo"
    result = subprocess.run(
        [sys.executable, target],
        input=json.dumps(
            {
                "conversationId": "chat-a",
                "lastUserInput": secret,
                "transcriptPath": str(tmp_path / "missing.jsonl"),
                "invocationNum": 9,
            }
        ),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=True,
    )
    output = json.loads(result.stdout)
    assert "terminationBehavior" not in output
    assert len(output["injectSteps"]) == 1
    assert isinstance(output["injectSteps"][0]["ephemeralMessage"], str)
    assert secret not in result.stdout
    assert "Stop" not in hooks["apersona-guidance"]
    assert not result.stderr


@pytest.mark.parametrize("payload", ["not-json", "[]", "{}", '{"conversationId": 3}'])
def test_antigravity_guidance_ignores_unrecognized_context(payload, tmp_path):
    result = subprocess.run(
        [sys.executable, str(PLUGIN / "antigravity/scripts/remind.py")],
        input=payload,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=True,
    )
    assert json.loads(result.stdout) == {}
    assert result.stderr == ""
