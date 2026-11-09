"""Standalone host service, staged with its own locked Python environment."""

import json
import os
import re
import stat
import sys
from pathlib import Path

ENVIRONMENT = (
    "HOME",
    "PATH",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TZ",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "OPENCODE_BROWSER_PORT",
)


def read_token(path):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("Browser token must be a user-owned regular file with mode 0600")
    value = path.read_text()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError("Invalid browser bridge token")
    return value


def image_only_execute(name, result):
    """browse 0.0.16 emits images without its advertised structured output."""
    return (
        name == "execute"
        and result.structuredContent is None
        and any(item.type == "image" for item in result.content)
        and all(item.type in {"image", "text"} for item in result.content)
    )


def proxy_arguments(root, settings, environment):
    args = [
        "mcp-proxy",
        "--host",
        "127.0.0.1",
        "--port",
        str(settings["port"]),
        "--allowed-host",
        f"host.docker.internal:{settings['port']}",
        "--initialization-timeout",
        "30",
        "--cwd",
        str(root),
    ]
    # The MCP SDK otherwise inherits only its default subset, losing custom XDG
    # paths and native relay ports. Never forward provider/auth credentials.
    for name in ENVIRONMENT:
        if name in environment:
            args.extend(["--env", name, environment[name]])
    return args + [
        "--env",
        "OPENCODE_BROWSER_RUNTIME",
        "auto-update",
        "--",
        settings["browse"],
        "mcp",
    ]


def main():
    root = Path(sys.argv[1])
    settings = json.loads((root / "settings.json").read_text())
    port = settings["port"]
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Invalid browser bridge port")
    token = read_token(root / "token")
    environment = {name: os.environ[name] for name in ENVIRONMENT if name in os.environ}
    environment["MCP_PROXY_AUTH_TOKEN"] = token

    from mcp.client.session import ClientSession
    from mcp_proxy import mcp_server
    from mcp_proxy.__main__ import main as proxy_main
    from starlette.responses import Response

    class BrowseSession(ClientSession):
        async def _validate_tool_result(self, name, result):
            if not image_only_execute(name, result):
                await super()._validate_tool_result(name, result)

    mcp_server.ClientSession = BrowseSession

    async def owned_health(_request):
        return Response(
            status_code=204,
            headers={"X-Odoocker-Browser-Release": Path(__file__).parent.name},
        )

    # Keep the proxy's authentication/authority middleware in front of this
    # endpoint. Replacement must not mistake a still-stopping pilot for us.
    mcp_server._handle_healthz = owned_health
    os.environ.clear()
    os.environ.update(environment)
    sys.argv = proxy_arguments(root, settings, environment)
    proxy_main()


if __name__ == "__main__":
    main()
