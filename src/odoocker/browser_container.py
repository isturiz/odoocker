"""Explicit, backed-up removal of only the known pilot's private MCP entry."""

import json
import os
from pathlib import Path

from .errors import OdxError
from .runtime import write_private


def pilot_server(port=19989):
    return {
        "type": "remote",
        "url": f"http://host.docker.internal:{port}/mcp",
        "oauth": False,
        "headers": {"Authorization": "Bearer {file:/workspace/.odoocker/browser-bridge/token}"},
    }


def adopt_pilot(root, config, managed):
    marker = root / "browser-adopt-pilot.json"
    completed = root / "browser-pilot-adopted.json"
    if not marker.exists() or completed.exists():
        return
    expected = json.loads(marker.read_text())
    if expected != {"schema": 1, "server": pilot_server()}:
        raise OdxError("Invalid browser pilot adoption marker; inspect it before retrying")
    if "browse" not in managed.get("mcp", {}).get("servers", {}):
        raise OdxError("Pilot adoption requires the generated browser MCP configuration")
    if (config / "opencode.jsonc").exists():
        raise OdxError("Pilot adoption refuses to edit JSONC; merge the browse entry explicitly")
    file = config / "opencode.json"
    if not file.exists():
        write_private(completed, '{"schema": 1}\n')
        return
    try:
        data = json.loads(file.read_text())
        servers = data.get("mcp", {}).get("servers", {})
        previous = servers.get("browse")
    except (ValueError, AttributeError) as error:
        raise OdxError("Invalid private OpenCode configuration; pilot was not removed") from error
    if previous is None:
        write_private(completed, '{"schema": 1}\n')
        return
    if previous != expected["server"]:
        raise OdxError("Existing browse entry differs from the pilot; refusing to replace it")
    backup = root / "browser-pilot-opencode.before.json"
    if backup.exists() and backup.read_bytes() != file.read_bytes():
        raise OdxError("Pilot backup already differs; inspect it before retrying")
    if not backup.exists():
        write_private(backup, file.read_text())
    del servers["browse"]
    write_private(file, json.dumps(data, indent=2) + "\n")
    write_private(completed, '{"schema": 1}\n')


def prepare():
    managed = Path("/etc/opencode/opencode.json")
    if not managed.exists():
        return
    context = json.loads(Path("/etc/odoocker/context.json").read_text())
    root = Path("/workspace/.odoocker/instances") / context["instance"]
    if not (root / "browser-adopt-pilot.json").exists():
        return
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode"
    adopt_pilot(root, config, json.loads(managed.read_text()) if managed.exists() else {})
