"""Opt-in host Caddy, shared by workspaces without Docker socket access."""

import http.client
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

from .errors import OdxError
from .locking import locked
from .runtime import run_command, write_private

HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.localhost\Z")
UPSTREAM = re.compile(r"127\.0\.0\.1:([0-9]+)\Z")
SERVICES = {"odoo": 8069, "opencode": 4096}


def directory():
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base.expanduser().resolve() / "odoocker/proxy"


class AdminConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(str(directory() / "admin.sock"))


def running():
    connection = AdminConnection("localhost", timeout=2)
    try:
        connection.request("GET", "/config/")
        response = connection.getresponse()
        response.read()
        return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def load():
    file = directory() / "routes.json"
    if not file.exists():
        return None
    try:
        state = json.loads(file.read_text())
        if (
            state["schema"] != 1
            or type(state["port"]) is not int
            or not 1024 <= state["port"] <= 65535
            or not isinstance(state["routes"], dict)
        ):
            raise ValueError("Invalid proxy settings")
        for host, route in state["routes"].items():
            match = UPSTREAM.fullmatch(route["upstream"])
            if (
                not HOST.fullmatch(host)
                or not match
                or not 1 <= int(match[1]) <= 65535
                or not isinstance(route["project"], str)
                or route.get("service", "odoo") not in SERVICES
            ):
                raise ValueError("Invalid proxy route")
        return state
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise OdxError(f"Invalid proxy registry: {file}; inspect it before retrying") from error


def document(state):
    routes = [
        {
            "match": [{"host": [host]}],
            "handle": [{
                "handler": "reverse_proxy",
                "upstreams": [{"dial": route["upstream"]}],
                # Flush event streams immediately; retain WebSockets during route reloads.
                "flush_interval": -1,
                "stream_close_delay": 300000000000,
            }],
            "terminal": True,
        }
        for host, route in sorted(state["routes"].items())
    ]
    routes.append({"handle": [{"handler": "static_response", "status_code": 404}]})
    return {
        "admin": {
            "listen": f"unix/{directory() / 'admin.sock'}",
            "config": {"persist": False},
        },
        "apps": {
            "http": {
                "servers": {
                    "odx": {
                        "listen": [f"127.0.0.1:{state['port']}"],
                        "automatic_https": {"disable": True},
                        "routes": routes,
                    }
                }
            }
        },
    }


def caddy(*args):
    # Do not use Caddy's global profile or an unrelated system service.
    env = {
        **os.environ,
        "XDG_CONFIG_HOME": str(directory() / "config"),
        "XDG_DATA_HOME": str(directory() / "data"),
    }
    if args[0] == "start":
        # The daemon inherits stdout/stderr. Pipes would keep communicate() waiting
        # for EOF for the daemon's entire lifetime; use a private persistent log.
        log = directory() / "caddy.log"
        with log.open("a") as stream:
            log.chmod(0o600)
            try:
                return subprocess.run(
                    ["caddy", *args],
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    env=env,
                    check=True,
                    timeout=15,
                )
            except FileNotFoundError as error:
                raise OdxError("Caddy not found; install it explicitly, then retry") from error
            except (subprocess.SubprocessError, OSError) as error:
                raise OdxError(f"Caddy could not start; inspect {log}") from error
    return run_command(["caddy", *args], capture=True, env=env)


def save(state, *, start=False):
    """Caddy reload is atomic; retain the previous registry if it rejects a change."""
    root = directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    candidate = root / "candidate.json"
    write_private(candidate, json.dumps(document(state), indent=2))
    if start or running():
        if running():
            caddy("reload", "--config", str(candidate), "--address", f"unix/{root / 'admin.sock'}")
        else:
            caddy("start", "--config", str(candidate))
    write_private(root / "routes.json", json.dumps(state, indent=2))
    candidate.replace(root / "caddy.json")


def hostname(runtime, service="odoo"):
    prefix = "opencode-" if service == "opencode" else ""
    host = f"{prefix}{runtime.workspace.name}-{runtime.instance}".replace("_", "-") + ".localhost"
    if not HOST.fullmatch(host):
        raise OdxError("Proxy workspace-instance hostname must fit in a 63-character DNS label")
    return host


def register(runtime, *, service=None):
    with locked(directory() / "proxy.lock"):
        state = load()
        if state is None:
            raise OdxError("Proxy is not configured; first run odx proxy start")
        services = [service] if service else runtime.compose(
            "ps", "--status", "running", "--services", capture=True
        ).stdout.split()
        services = [name for name in SERVICES if name in services]
        if not services:
            raise OdxError("No running Odoo/OpenCode services; run odx up (with --code if needed)")
        routes = {}
        for name in services:
            host = hostname(runtime, name)
            previous = state["routes"].get(host)
            if previous and (
                previous["project"] != runtime.project
                or previous.get("service", "odoo") != name
            ):
                raise OdxError(f"Proxy hostname already belongs to another instance: {host}")
            upstream = runtime.url(name, SERVICES[name]).removeprefix("http://")
            match = UPSTREAM.fullmatch(upstream)
            if not match or not 1 <= int(match[1]) <= 65535:
                raise OdxError(f"Proxy requires a {name} port published on 127.0.0.1")
            routes[host] = {"project": runtime.project, "upstream": upstream, "service": name}
        state["routes"] = {
            key: value
            for key, value in state["routes"].items()
            if value["project"] != runtime.project
            or (service and value.get("service", "odoo") != service)
        }
        state["routes"].update(routes)
        save(state)
        if not running():
            print("odx: proxy is stopped; run odx proxy start", file=sys.stderr)
        return f"http://{hostname(runtime, services[0])}:{state['port']}"


def unregister(runtime):
    with locked(directory() / "proxy.lock"):
        state = load()
        if state is not None:
            state["routes"] = {
                host: route
                for host, route in state["routes"].items()
                if route["project"] != runtime.project
            }
            save(state)


def sync(runtime, *, remove=False):
    """Proxy errors must not disguise the outcome of a successful Docker operation."""
    if not (directory() / "routes.json").exists():
        return
    try:
        if remove:
            unregister(runtime)
        else:
            register(runtime)
    except OdxError as error:
        recovery = "odx proxy remove HOSTNAME" if remove else "odx proxy register"
        print(f"odx: proxy route update failed: {error}; retry with {recovery}", file=sys.stderr)


def url(runtime, direct, service="odoo"):
    state = load()
    if state:
        for host, route in state["routes"].items():
            if (
                route["project"] == runtime.project
                and route["upstream"] == direct.removeprefix("http://")
                and route.get("service", "odoo") == service
            ):
                return f"http://{host}:{state['port']}"
    return None


def command(args):
    action = args.proxy_command
    if action == "register":
        from .config import Workspace, find_workspace
        from .runtime import Runtime

        runtime = Runtime.load(Workspace.load(find_workspace(args.workspace)), args.instance)
        with locked(runtime.operation_lock):
            print(register(runtime))
        return 0
    with locked(directory() / "proxy.lock"):
        state = load()
        if action == "start":
            if state and args.port is not None and args.port != state["port"]:
                raise OdxError("Proxy port is already configured; keep it stable")
            port = 8080 if args.port is None else args.port
            state = state or {"schema": 1, "port": port, "routes": {}}
            if not 1024 <= state["port"] <= 65535:
                raise OdxError("Proxy port must be between 1024 and 65535")
            save(state, start=True)
            print(f"Proxy listening on 127.0.0.1:{state['port']}")
        elif action == "stop":
            if running():
                caddy("stop", "--address", f"unix/{directory() / 'admin.sock'}")
            print("Proxy stopped; routes retained")
        elif action == "remove":
            if not state or args.hostname not in state["routes"]:
                raise OdxError("No registered proxy route with that exact hostname")
            del state["routes"][args.hostname]
            save(state)
        elif action == "status":
            info = {"running": running(), "directory": str(directory()), **(state or {})}
            print(json.dumps(info, indent=2))
    return 0
