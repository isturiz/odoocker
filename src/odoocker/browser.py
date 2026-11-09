"""Opt-in, authenticated host browse transport; never expose the relay's API."""

import hashlib
import http.client
import json
import os
import plistlib
import re
import secrets
import shutil
import socket
import sys
import time
import uuid
from pathlib import Path

from . import __version__
from .browser_bridge.service import ENVIRONMENT, read_token
from .errors import OdxError
from .locking import locked
from .runtime import run_command, write_private

LABEL = "io.odoocker.browser"
PILOT_LABEL = "io.odoocker.browser-bridge.realista"
RESOURCES = Path(__file__).parent / "browser_bridge"
FILES = ("pyproject.toml", "uv.lock", "service.py")


def directory():
    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")).expanduser().resolve()
        / "odoocker/browser"
    )


def logs():
    return (
        Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")).expanduser().resolve()
        / "odoocker/browser"
    )


def plist_path(label=LABEL):
    return Path.home() / "Library/LaunchAgents" / f"{label}.plist"


def domain(label=LABEL):
    return f"gui/{os.getuid()}/{label}"


def supported():
    if sys.platform != "darwin":
        raise OdxError("Shared browser setup currently supports macOS + Docker Desktop only")


def release():
    digest = hashlib.sha256()
    for name in FILES:
        digest.update(name.encode())
        digest.update((RESOURCES / name).read_bytes())
    return digest.hexdigest()


def load():
    path = directory() / "settings.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if (
            type(data["schema"]) is not int
            or data["schema"] != 1
            or type(data["port"]) is not int
            or not 1024 <= data["port"] <= 65535
            or type(data["enabled"]) is not bool
            or not isinstance(data["release"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", data["release"])
            or not isinstance(data["browse"], str)
            or not Path(data["browse"]).is_absolute()
            or type(data.get("adopted_pilot", False)) is not bool
        ):
            raise ValueError("invalid settings")
        return data
    except (KeyError, ValueError, TypeError) as error:
        raise OdxError(f"Invalid browser settings: {path}; inspect before retrying") from error


def token(path=None):
    try:
        return read_token(path or directory() / "token")
    except (OSError, ValueError) as error:
        raise OdxError(
            "Missing or unsafe browser bridge credential; run odx browser setup"
        ) from error


def request(port, credential, method, path, body=None, headers=None):
    # No proxy environment, redirects, LAN binds or credentials in process arguments.
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            method,
            path,
            body,
            {
                "Authorization": f"Bearer {credential}",
                **(headers or {}),
            },
        )
        response = connection.getresponse()
        payload = response.read(8 * 1024 * 1024)
        return response.status, dict(response.getheaders()), payload
    finally:
        connection.close()


def healthy(settings, credential):
    try:
        code, headers, _ = request(settings["port"], credential, "GET", "/healthz")
        identity = {key.lower(): value for key, value in headers.items()}
        return code == 204 and (
            "release" not in settings
            or identity.get("x-odoocker-browser-release") == settings["release"]
        )
    except (OSError, http.client.HTTPException):
        return False


def rpc_status(settings, credential):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    transport = None

    def send(method, params=None, sequence=None):
        nonlocal transport
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if sequence is not None:
            message["id"] = sequence
        status, returned, body = request(
            settings["port"], credential, "POST", "/mcp", json.dumps(message), headers
        )
        transport = next(
            (v for k, v in returned.items() if k.lower() == "mcp-session-id"), transport
        )
        if transport:
            headers["Mcp-Session-Id"] = transport
        if status not in (200, 202):
            raise OdxError(f"Browser MCP request failed (HTTP {status})")
        value = json.loads(body) if body else {}
        if "error" in value:
            raise OdxError("Browser MCP rejected a diagnostic request")
        return value.get("result", {})

    try:
        send(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "odoocker-browser-check", "version": __version__},
            },
            1,
        )
        send("notifications/initialized")
        result = send("tools/call", {"name": "status", "arguments": {}}, 2)
        if result.get("isError"):
            raise OdxError("browse status failed; inspect the bridge log")
        if "structuredContent" in result:
            return result["structuredContent"]
        return json.loads(result["content"][0]["text"])
    except (OSError, ValueError, KeyError, http.client.HTTPException) as error:
        raise OdxError("Cannot read browser MCP status; inspect the bridge log") from error
    finally:
        if transport:
            try:
                request(settings["port"], credential, "DELETE", "/mcp", headers=headers)
            except (OSError, http.client.HTTPException):
                pass


def quiet(settings, credential):
    status = rpc_status(settings, credential)["status"]
    if any(session.get("connected") for session in status.get("sessions", [])):
        raise OdxError(
            "Browser sessions are still connected; finish them before bridge maintenance"
        )


def loaded(label=LABEL):
    return (
        run_command(["launchctl", "print", domain(label)], capture=True, check=False).returncode
        == 0
    )


def verify_plist(path, settings):
    try:
        actual = plistlib.loads(path.read_bytes())
        expected = [
            str(directory() / "releases" / settings["release"] / ".venv/bin/python"),
            str(directory() / "releases" / settings["release"] / "service.py"),
            str(directory()),
        ]
        if actual.get("Label") != LABEL or actual.get("ProgramArguments") != expected:
            raise ValueError("not the managed service")
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        raise OdxError("Refusing to replace or stop an unrecognized browser LaunchAgent") from error


def launch_agent(settings):
    runtime = directory() / "releases" / settings["release"]
    environment = {name: os.environ[name] for name in ENVIRONMENT if name in os.environ}
    environment["HOME"] = str(Path.home())
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(runtime / ".venv/bin/python"),
            str(runtime / "service.py"),
            str(directory()),
        ],
        "WorkingDirectory": str(directory()),
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "Umask": 0o077,
        "StandardOutPath": str(logs() / "stdout.log"),
        "StandardErrorPath": str(logs() / "stderr.log"),
    }


def wait_healthy(settings, credential):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if healthy(settings, credential):
            return
        time.sleep(0.2)
    raise OdxError(f"Browser bridge did not become healthy; inspect {logs() / 'stderr.log'}")


def boot(path, label=LABEL):
    run_command(["launchctl", "enable", domain(label)], capture=True)
    run_command(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], capture=True)


def unload(path, label=LABEL):
    if loaded(label):
        run_command(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture=True)


def pilot():
    path = plist_path(PILOT_LABEL)
    old_root = Path.home() / ".local/share/odoocker/browser-bridge"
    old_token = Path.home() / ".config/odoocker/browser-bridge/realista.token"
    try:
        data = plistlib.loads(path.read_bytes())
        if data.get("Label") != PILOT_LABEL or data.get("ProgramArguments") != [
            str(old_root / ".venv/bin/python"),
            str(old_root / "run.py"),
        ]:
            raise ValueError("unknown pilot")
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        raise OdxError(
            "Known Realista pilot not found; refusing to adopt another service"
        ) from error
    return path, token(old_token)


def setup(*, port=None, adopt=False):
    supported()
    root = directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    with locked(root / "setup.lock"):
        previous = load()
        old_pilot = None
        if adopt and not (previous and previous.get("adopted_pilot")):
            if previous:
                raise OdxError("Cannot adopt the pilot over another managed bridge")
            old_pilot, credential = pilot()
            chosen = 19989
            if port not in (None, chosen):
                raise OdxError("Pilot adoption retains its existing port (19989)")
            if not healthy({"port": chosen}, credential):
                raise OdxError("Pilot is not healthy with its credential; leave it unchanged")
            quiet({"port": chosen}, credential)
        else:
            if plist_path(PILOT_LABEL).exists() and not previous:
                raise OdxError(
                    "Existing pilot detected; use odx browser setup --adopt-pilot explicitly"
                )
            chosen = previous["port"] if previous else (port if port is not None else 19989)
            if previous and port not in (None, chosen):
                raise OdxError(
                    "Browser bridge port is already fixed; do not strand enrolled instances"
                )
            credential = token() if previous else secrets.token_urlsafe(32)
        if type(chosen) is not int or not 1024 <= chosen <= 65535:
            raise OdxError("Browser port must be an integer from 1024 through 65535")
        executable = shutil.which("browse")
        if not executable:
            raise OdxError(
                "Install and pair browse on the host first; the engine does not pair browsers"
            )
        executable = str(Path(executable).resolve())
        settings = {
            "schema": 1,
            "port": chosen,
            "enabled": True,
            "release": release(),
            "browse": executable,
            "engine": __version__,
            "adopted_pilot": bool(old_pilot or (previous and previous.get("adopted_pilot"))),
        }
        if plist_path().exists():
            if not previous:
                raise OdxError("Existing browser LaunchAgent has no ownership state; inspect it")
            verify_plist(plist_path(), previous)
        elif previous:
            raise OdxError("Managed browser LaunchAgent is missing; inspect it before setup")
        was_loaded = loaded()
        if was_loaded and previous is None:
            raise OdxError("Browser service label is already registered without ownership state")
        if (
            previous
            and previous["release"] == settings["release"]
            and previous["browse"] == executable
        ):
            start()
            return
        if previous and healthy(previous, credential):
            quiet(previous, credential)
        elif not old_pilot:
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", chosen))
                except OSError as error:
                    raise OdxError(
                        "Browser port is occupied by an unverified service; nothing stopped"
                    ) from error
        target = root / "releases" / settings["release"]
        target.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            destination = target / name
            if destination.exists() and destination.read_bytes() != (RESOURCES / name).read_bytes():
                raise OdxError("Staged browser runtime differs from its fingerprint; inspect it")
            if not destination.exists():
                shutil.copyfile(RESOURCES / name, destination)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "UV_PROJECT_ENVIRONMENT",
                "UV_PROJECT",
                "UV_WORKING_DIRECTORY",
                "VIRTUAL_ENV",
            }
        }
        run_command(
            ["uv", "sync", "--project", str(target), "--frozen", "--no-dev", "--python", "3.12"],
            env=environment,
        )
        logs().mkdir(parents=True, exist_ok=True, mode=0o700)
        logs().chmod(0o700)
        plist_path().parent.mkdir(parents=True, exist_ok=True)
        backup = root / "backups" / uuid.uuid4().hex
        backup.mkdir(parents=True, mode=0o700)
        for path in (root / "settings.json", root / "token", plist_path(), old_pilot):
            if path and path.exists():
                write_private(backup / path.name, path.read_text())
        if previous and healthy(previous, credential):
            quiet(previous, credential)
        if old_pilot:
            quiet({"port": chosen}, credential)
        try:
            if previous:
                unload(plist_path())
            if old_pilot:
                unload(old_pilot, PILOT_LABEL)
            write_private(root / "token", credential)
            write_private(root / "settings.json", json.dumps(settings, indent=2) + "\n")
            write_private(plist_path(), plistlib.dumps(launch_agent(settings)).decode())
            boot(plist_path())
            wait_healthy(settings, credential)
            if old_pilot:
                run_command(["launchctl", "disable", domain(PILOT_LABEL)], capture=True)
        except BaseException:
            # Restore only this attempt's owned files/services; retain all backups.
            unload(plist_path())
            for path in (root / "settings.json", root / "token", plist_path()):
                saved = backup / path.name
                if saved.exists():
                    write_private(path, saved.read_text())
                elif path.exists():
                    path.unlink()
            if previous and was_loaded:
                boot(plist_path())
            if previous and not previous["enabled"]:
                run_command(["launchctl", "disable", domain()], capture=True)
            if old_pilot:
                boot(old_pilot, PILOT_LABEL)
            raise
        print(f"Shared browser bridge prepared on localhost:{chosen}; dependencies locked")


def start():
    supported()
    settings = load()
    if settings is None:
        raise OdxError("Browser bridge is not installed; run odx browser setup")
    verify_plist(plist_path(), settings)
    credential = token()
    was_loaded = loaded()
    try:
        if not was_loaded:
            boot(plist_path())
        wait_healthy(settings, credential)
    except BaseException:
        if not settings["enabled"]:
            run_command(["launchctl", "disable", domain()], capture=True)
            if not was_loaded:
                unload(plist_path())
        raise
    if not settings["enabled"]:
        settings["enabled"] = True
        write_private(directory() / "settings.json", json.dumps(settings, indent=2) + "\n")


def stop():
    supported()
    with locked(directory() / "setup.lock"):
        settings = load()
        if settings is None:
            raise OdxError("Browser bridge is not installed")
        verify_plist(plist_path(), settings)
        credential = token()
        if healthy(settings, credential):
            quiet(settings, credential)
        run_command(["launchctl", "disable", domain()], capture=True)
        unload(plist_path())
        settings["enabled"] = False
        write_private(directory() / "settings.json", json.dumps(settings, indent=2) + "\n")
    print(
        "Shared browser bridge disabled for every enrolled workspace; native host browse unchanged"
    )


def connection():
    supported()
    settings = load()
    if settings is None or not settings["enabled"]:
        raise OdxError("Browser integration needs odx browser setup (or browser start if disabled)")
    return settings


def ensure():
    connection()
    with locked(directory() / "setup.lock"):
        start()


def server_config():
    settings = connection()
    return {
        "type": "remote",
        "url": f"http://host.docker.internal:{settings['port']}/mcp",
        "oauth": False,
        "headers": {"Authorization": "Bearer {file:/run/odoocker/browser-token}"},
    }


def status():
    settings = load()
    result = {"supported": sys.platform == "darwin", "installed": settings is not None}
    if settings:
        result.update(
            {
                "enabled": settings["enabled"],
                "port": settings["port"],
                "runtime_current": settings["release"] == release(),
                "healthy": healthy(settings, token()),
                "log": str(logs() / "stderr.log"),
            }
        )
    return result


def adopt_instance(runtime):
    from .browser_container import pilot_server

    if not runtime.workspace.browser:
        raise OdxError("Declare opencode.browser = true before adopting the instance's pilot")
    settings = connection()
    if not settings.get("adopted_pilot"):
        raise OdxError("First run odx browser setup --adopt-pilot on this host")
    legacy = runtime.workspace.root / ".odoocker/browser-bridge/token"
    if token(legacy) != token():
        raise OdxError("Workspace pilot credential differs; nothing was adopted")
    with locked(runtime.operation_lock):
        write_private(
            runtime.directory / "browser-adopt-pilot.json",
            json.dumps(
                {
                    "schema": 1,
                    "server": pilot_server(),
                },
                indent=2,
            )
            + "\n",
        )
    print(
        "Pilot adoption marked; its exact private MCP entry will be backed up "
        "on next managed startup"
    )


CHECK = r"""
import http.client, pathlib, sys
credential = pathlib.Path('/run/odoocker/browser-token').read_text()
connection = http.client.HTTPConnection('host.docker.internal', int(sys.argv[1]), timeout=5)
try:
    connection.request('GET', '/healthz', headers={'Authorization': 'Bearer ' + credential})
    response = connection.getresponse()
    response.read()
    if response.status != 204:
        raise SystemExit('Browser bridge rejected the container health probe')
    print('container_transport: connected')
finally:
    connection.close()
"""


def check(runtime):
    if not runtime.workspace.browser:
        raise OdxError("This workspace has not enabled opencode.browser")
    settings = connection()
    credential = token()
    if not healthy(settings, credential):
        raise OdxError("Browser bridge is unavailable; inspect odx browser status")
    result = rpc_status(settings, credential)
    runtime.compose("exec", "-T", "opencode", "python3", "-c", CHECK, str(settings["port"]))
    if not result["status"]["connected"]:
        raise OdxError("Transport works, but the paired browser is offline; open its profile")
    print("browse extension: connected; host and container use the existing shared browser")
    print("This check opens no tabs and does not verify Odoo HTTP or change any application data")


def command(args):
    action = args.browser_command
    if action == "setup":
        setup(port=args.port, adopt=args.adopt_pilot)
    elif action == "start":
        with locked(directory() / "setup.lock"):
            start()
    elif action == "stop":
        stop()
    elif action == "status":
        print(json.dumps(status(), indent=2))
    else:
        from .config import Workspace, find_workspace
        from .runtime import Runtime

        runtime = Runtime.load(Workspace.load(find_workspace(args.workspace)), args.instance)
        if action == "adopt-pilot":
            adopt_instance(runtime)
        else:
            check(runtime)
    return 0
