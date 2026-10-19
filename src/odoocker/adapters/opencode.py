"""OpenCode-specific client connection; never touches the host's shared service."""

import os
import re
import sys
from pathlib import Path

from .. import proxy
from ..errors import OdxError
from ..runtime import run_command


def host_version():
    output = run_command(["opencode", "--version"], capture=True).stdout.strip()
    match = re.fullmatch(r"(?:opencode\s+)?v?(2\.\d+\.\d+(?:-[\w.-]+)?)", output)
    if not match:
        raise OdxError("OpenCode V2 is required on the host")
    return match.group(1)


def authentication(runtime):
    return {**os.environ, "OPENCODE_PASSWORD": runtime.password}


def verify(runtime):
    version = host_version()
    if version != runtime.workspace.opencode_version:
        raise OdxError(
            f"Host OpenCode is {version}, workspace pins {runtime.workspace.opencode_version}. "
            "Align the manifest and rebuild deliberately; the engine will not update your host."
        )
    actual = runtime.compose("exec", "-T", "opencode", "opencode", "--version", capture=True)
    output = actual.stdout.strip()
    match = re.fullmatch(r"(?:opencode\s+)?v?(2\.\d+\.\d+(?:-[\w.-]+)?)", output)
    if not match or match.group(1) != version:
        raise OdxError("OpenCode container version does not match the workspace")


def server_url(runtime):
    direct = runtime.url("opencode", 4096)
    if proxy.load() is None:
        return direct
    if not proxy.running():
        print(
            "odx: proxy is stopped; using the direct OpenCode port. "
            "Run odx proxy start before connecting for a stable URL.",
            file=sys.stderr,
        )
        return direct
    return proxy.register(runtime, service="opencode")


def connect(runtime, args):
    verify(runtime)
    url = server_url(runtime)
    home = Path.home()
    environment = authentication(runtime)
    for key, fallback in (("XDG_DATA_HOME", ".local/share"), ("XDG_STATE_HOME", ".local/state")):
        environment[key] = str(
            Path(os.environ.get(key) or home / fallback) / "odoocker-clients" / runtime.project
        )
    return run_command(
        ["opencode", "--server", url, *args],
        env=environment,
        check=False,
        cwd=runtime.workspace.root,
    ).returncode


def info(runtime):
    verify(runtime)
    return run_command(
        [
            "opencode",
            "api",
            "--server",
            server_url(runtime),
            "get",
            "/api/info",
        ],
        env=authentication(runtime),
        check=False,
    ).returncode
