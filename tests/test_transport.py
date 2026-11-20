"""The public client identifies itself, without weakening its refusal to forward credentials on redirects."""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def transport():
    script = Path(__file__).parents[1] / "plugin/scripts/apersona_safe.py"
    spec = importlib.util.spec_from_file_location("transport_safe", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def site():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("User-Agent"), self.headers.get("Authorization")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/must-not-receive-credentials")
                self.end_headers()
                return
            agent = self.headers.get("User-Agent", "")
            self.send_response(403 if agent.startswith("Python-urllib/") else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"user_agent": agent}).encode())

        def log_message(self, *args: object):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_the_user_agent_names_the_version_in_the_manifest(transport):
    manifest = Path(__file__).parents[1] / "plugin/.claude-plugin/plugin.json"
    version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    assert transport.USER_AGENT == f"APersona-Plugin/{version}"


def test_stop_copies_only_the_masked_terminal_report_from_the_assistants_words(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "plugin/scripts"))
    import apersona_hook as hook

    requests, paths = [], []
    monkeypatch.setattr(hook, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(hook, "list_left", lambda rows: (1, "整理測試"))
    def receiving(req, _):
        requests.append(json.loads(req.data))
        paths.append(req.full_url)
        return {"verdict": "asking"}
    monkeypatch.setattr(hook, "fetch_json", receiving)
    key = "sk-" + "a" * 40
    transcript = tmp_path / "chat.jsonl"
    transcript.write_text("", encoding="utf8")
    fields = {"session_id": "test", "transcript_path": str(transcript)}
    for said in ("〈下一步〉是舊範例。\n已完成。", f"已完成。\n〈下一步〉整理測試\n\n〈請決定〉交付 {key} 的版本。"):
        assert hook.stand_in("http://localhost", "test", dict(fields, last_assistant_message=said), 1) == ""
    assert "stopReportV1" not in requests[0]
    assert paths == ["http://localhost/api/memory/stand-in", "http://localhost/api/memory/stand-in/report"]
    shown = requests[1]["stopReportV1"]
    assert shown.startswith("〈下一步〉整理測試\n\n〈請決定〉") and requests[1]["said"].endswith(shown)
    assert key not in json.dumps(requests, ensure_ascii=False)
    monkeypatch.setattr(hook, "fetch_json", lambda *_: {"verdict": "continue", "text": "〈人格代答〉繼續"})
    assert hook.stand_in("http://localhost", "test", dict(fields, last_assistant_message="〈下一步〉發布"), 1) == ""
    plain = hook.stand_in("http://localhost", "test", dict(fields, last_assistant_message="已整理完成。"), 1)
    assert json.loads(plain)["decision"] == "block"


@pytest.mark.parametrize("agent", [None, "APersona-Integration/Test"])
def test_public_requests_identify_the_plugin_and_preserve_an_explicit_caller_agent(transport, site, agent):
    url, calls = site
    headers = {} if agent is None else {"User-Agent": agent}
    with transport.open_url(urllib.request.Request(url, headers=headers), timeout=3) as response:
        assert response.status == 200
        assert json.load(response) == {"user_agent": agent or transport.USER_AGENT}
    assert len(calls) == 1


def test_identification_does_not_forward_credentials_to_a_redirect(transport, site):
    url, calls = site
    request = urllib.request.Request(url + "/redirect", headers={"Authorization": "Bearer synthetic-token"})
    with pytest.raises(urllib.error.HTTPError) as error:
        transport.open_url(request, timeout=3)
    assert error.value.code == 302
    assert calls == [("/redirect", transport.USER_AGENT, "Bearer synthetic-token")]
