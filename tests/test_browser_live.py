"""Explicit live smoke test; only its disposable, explicitly scoped Docker project."""

import http.server
import inspect
import json
import os
import struct
import subprocess
import threading
import urllib.request
import uuid
from http.cookies import SimpleCookie
from pathlib import Path

import pytest

from odoocker import browser

HOST_DATA_HOME = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))

pytestmark = pytest.mark.skipif(
    os.environ.get("ODOOCKER_BROWSER_LIVE") != "1",
    reason="Opt in with ODOOCKER_BROWSER_LIVE=1; requires Docker Desktop and paired browse",
)


class BrowserClient:
    def __init__(self, url, credential):
        self.url = url
        self.headers = {
            "Authorization": "Bearer " + credential,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.identity = "odoocker-live-" + uuid.uuid4().hex
        self.sequence = 0

    def request(self, method, params=None, notification=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notification:
            self.sequence += 1
            message["id"] = self.sequence
        request = urllib.request.Request(
            self.url, data=json.dumps(message).encode(), headers=self.headers
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.headers.get("mcp-session-id"):
                self.headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
            body = response.read()
        if not body:
            return {}
        message = json.loads(body)
        if "error" in message:
            raise RuntimeError(message["error"])
        return message["result"]

    def initialize(self):
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "odoocker-live-check", "version": "1"},
            },
        )
        self.request("notifications/initialized", notification=True)

    def call(self, name, arguments=None):
        result = self.request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
                "_meta": {"ai.opencode/sessionID": self.identity},
            },
        )
        if result.get("isError"):
            raise RuntimeError(result)
        return result

    def value(self, name, arguments=None):
        result = self.call(name, arguments)
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])

    def execute(self, code):
        result = self.value("execute", {"code": code})
        if result.get("aftermath", {}).get("challenge"):
            raise RuntimeError("Stop: browser challenge needs human attention")
        return result.get("value")

    def close(self):
        request = urllib.request.Request(self.url, method="DELETE", headers=self.headers)
        with urllib.request.urlopen(request, timeout=10):
            pass


def test_shared_browser_from_disposable_docker_project(tmp_path, monkeypatch):
    # Unit-test fixtures intentionally isolate host state. This explicit opt-in
    # test restores only the real bridge's data root, not normal proxy behavior.
    monkeypatch.setenv("XDG_DATA_HOME", HOST_DATA_HOME)
    settings = browser.connection()
    credential = browser.token()
    assert browser.healthy(settings, credential)
    nonce = uuid.uuid4().hex
    prefix = "/odoocker-browser-live-" + nonce + "/"
    cookie_name = "odx_test_" + nonce

    class Fixture(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in (prefix, prefix + "clear"):
                self.send_error(404)
                return
            shared = cookie_name in SimpleCookie(self.headers.get("Cookie", ""))
            body = (
                "<!doctype html><html><title>Odoocker browser check</title>"
                "<h1>Shared browser verified</h1><p>No Odoo database was used.</p>"
                f'<p data-testid="shared">{"yes" if shared else "no"}</p></html>'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            clear = self.path.endswith("clear")
            self.send_header(
                "Set-Cookie",
                f"{cookie_name}=test; Path={prefix}; Max-Age={0 if clear else 120}; "
                "HttpOnly; SameSite=Strict",
            )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    fixture = http.server.HTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{fixture.server_port}"
    url = origin + prefix
    file = tmp_path / "compose.json"
    # Match the real OpenCode UID and the exact read-only host token mount.
    # The artifact directory contains no credential and is writable for that UID.
    tmp_path.chmod(0o777)
    file.write_text(
        json.dumps(
            {
                "services": {
                    "probe": {
                        "image": "python:3.12.13-slim-bookworm",
                        "user": "1000:1000",
                        "volumes": [
                            {"type": "bind", "source": str(tmp_path), "target": "/probe"},
                            {
                                "type": "bind",
                                "source": str(browser.directory() / "token"),
                                "target": "/run/odoocker/browser-token",
                                "read_only": True,
                            },
                        ],
                        "working_dir": "/probe",
                    }
                },
            }
        )
    )
    compose = [
        "docker",
        "compose",
        "--project-name",
        "odx-browser-check-" + nonce,
        "--file",
        str(file),
        "--project-directory",
        str(tmp_path),
    ]
    client = BrowserClient(f"http://127.0.0.1:{settings['port']}/mcp", credential)
    created = False
    try:
        client.initialize()
        session = client.value(
            "session_new",
            {
                "id": "odx-host-" + nonce,
                "allowedOrigins": [origin],
            },
        )["session"]
        created = True
        client.execute("return await snapshot()")
        client.execute(
            f"await page.goto({json.dumps(url)}); state.owner='host'; return await snapshot()"
        )
        script = (
            "import base64,json,pathlib,urllib.request,uuid\n"
            + inspect.getsource(BrowserClient)
            + f"\nc=BrowserClient('http://host.docker.internal:{settings['port']}/mcp',"
            "pathlib.Path('/run/odoocker/browser-token').read_text())\n"
            + "c.initialize()\ncreated=False\ntry:\n"
            + f" s=c.value('session_new',{{'id':'odx-container-{nonce}',"
            f"'allowedOrigins':[{origin!r}]}})['session']\n"
            + " created=True\n c.execute('return await snapshot()')\n"
            + f" c.execute('await page.goto({json.dumps(url)}); "
            'state.owner="container"; return await snapshot()\')\n'
            + " shared=c.execute(\"return await page.getByTestId('shared').innerText()\")\n"
            + " image=c.call('execute',{'code':'return await page.screenshot()'})\n"
            + " png=next(x for x in image['content'] if x['type']=='image')\n"
            + " pathlib.Path('/probe/screenshot.png').write_bytes("
            "base64.b64decode(png['data'],validate=True))\n"
            + " print(json.dumps({'browser':s['browser'],'shared':shared}))\n"
            + "finally:\n if created:c.value('session_delete')\n c.close()\n"
        )
        result = subprocess.run(
            compose + ["run", "--rm", "--no-deps", "-T", "probe", "python3", "-c", script],
            capture_output=True,
            text=True,
            timeout=150,
        )
        assert result.returncode == 0, result.stderr
        answer = json.loads(result.stdout)
        assert answer == {"browser": session["browser"], "shared": "yes"}
        assert client.execute("return {owner:state.owner,url:page.url()}") == {
            "owner": "host",
            "url": url,
        }
        image = (tmp_path / "screenshot.png").read_bytes()
        assert image[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", image[16:24])
        assert width > 0 and height > 0
        print(f"Shared browser/cookies, separate sessions, PNG {width}x{height}: verified")
    finally:
        try:
            if created:
                try:
                    client.execute(
                        f"await page.goto({json.dumps(url + 'clear')}); return page.url()"
                    )
                finally:
                    client.value("session_delete")
        finally:
            client.close()
            fixture.shutdown()
            fixture.server_close()
            thread.join()
            subprocess.run(
                compose + ["down"], capture_output=True, text=True, timeout=30, check=True
            )
