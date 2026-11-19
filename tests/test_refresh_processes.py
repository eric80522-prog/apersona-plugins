"""Concurrent local clients share a single refresh-token exchange."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

SCRIPTS = Path(__file__).parents[1] / "plugin/scripts"
CLIENT = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import apersona_login as login
site, marker, mode = sys.argv[2:]
read = login.read_credentials
def observed():
    record = read()
    Path(marker).touch()
    return record
login.read_credentials = observed
assert login.bearer(site, timeout_s=8, trade=login.Trade[mode]) == "test-access-new"
"""


def test_concurrent_refresh_and_status_share_one_trade_even_if_the_marker_is_old(tmp_path):
    requests = []
    received = threading.Event()
    duplicate = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            requests.append((self.path, form.get("refresh_token")))
            if len(requests) > 1:
                duplicate.set()
            received.set()
            release.wait(7)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"access_token":"test-access-new","refresh_token":"test-refresh-new","expires_in":3600}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    site = "http://127.0.0.1:%d" % server.server_port
    creds = tmp_path / ".apersona/plugin-credentials.json"
    creds.parent.mkdir()
    creds.write_text(
        json.dumps(
            dict(site=site, client_id="test-client", access="test-old", refresh="test-refresh-old", expires_at=1)
        ),
        encoding="utf-8",
    )
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    children = []

    def start(mode):
        marker = tmp_path / mode
        children.append(
            subprocess.Popen(
                [sys.executable, "-c", CLIENT, str(SCRIPTS), site, str(marker), mode],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        )
        return marker

    try:
        start("AHEAD")
        assert received.wait(5)
        old = time.time() - 90
        os.utime(str(creds) + ".lock", (old, old))
        observed = start("ALWAYS")
        deadline = time.monotonic() + 5
        while not observed.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert observed.exists()
        assert not duplicate.wait(0.4), "an active refresh lost its lock"
        release.set()
        for child in children:
            _, errors = child.communicate(timeout=10)
            assert child.returncode == 0, errors.decode(errors="replace")
        assert requests == [("/oauth2/token", ["test-refresh-old"])]
        assert json.loads(creds.read_text(encoding="utf-8"))["refresh"] == "test-refresh-new"
    finally:
        release.set()
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
        server.shutdown()
        server.server_close()
        thread.join()
