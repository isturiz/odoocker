"""Prepare one development instance without fetching code or initializing databases."""

import json
import tempfile
from pathlib import Path

from .adapters import opencode
from .adapters.opencode_update import active_sessions, release_key
from .config import Workspace
from .errors import OdxError
from .locking import locked
from .maintenance import maintenance_process
from .runtime import _literals, compose_env, run_command, write_private

GUARD = r"""
import fcntl, json, sys
from contextlib import ExitStack
from pathlib import Path
with ExitStack() as stack:
    for name in ('environment.lock', 'dev-database.lock'):
        stream = stack.enter_context((Path(sys.argv[1]) / name).open('a+'))
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('ODX_SETUP ' + json.dumps({'status':'busy'}), flush=True)
            raise SystemExit(1)
    print('ODX_SETUP ' + json.dumps({'status':'locked'}), flush=True)
    sys.stdin.readline()
"""


def guard_document(runtime, directory):
    runtime.prepare_build(directory / "build")
    build = dict(runtime.compose_document()["services"]["odoo"]["build"])
    build["context"] = str(directory / "build")
    return {
        "services": {
            "setup-guard": {
                "image": runtime.image(),
                "build": build,
                "init": True,
                "network_mode": "none",
                "command": ["python3", "-u", "-c", GUARD, "/var/lib/odoocker"],
                "volumes": [{"type": "volume", "source": "runtime", "target": "/var/lib/odoocker"}],
            }
        },
        "volumes": {"runtime": {"name": f"{runtime.project}_runtime"}},
    }


def prepare(runtime, *, code=False):
    with locked(runtime.operation_lock):
        runtime.workspace.validate_sources()
        runtime.validate_instance()
        previous = runtime.compose_file.read_bytes() if runtime.compose_file.exists() else None
        if previous is not None:
            saved = json.loads(previous)
            try:
                old_pin = saved["services"]["opencode"]["build"]["args"]["OPENCODE_VERSION"]
            except (KeyError, TypeError) as error:
                raise OdxError(
                    "Invalid saved configuration; inspect this instance before setup"
                ) from error
            if old_pin != runtime.workspace.opencode_version:
                raise OdxError(
                    "OpenCode pin changed; use odx code opencode update "
                    "for backed-up version changes"
                )
            running = runtime.compose(
                "ps", "--status", "running", "--services", capture=True
            ).stdout.split()
        else:
            running = []
        keep_code = code or "opencode" in running
        host = opencode.host_version() if keep_code else None
        if host and host != runtime.workspace.opencode_version:
            instruction = (
                "Update OpenCode on the host before connecting."
                if release_key(host) < release_key(runtime.workspace.opencode_version)
                else "Run odx code opencode update after setup before connecting."
            )
            print(
                f"OpenCode host is {host}; setup keeps the pinned "
                f"{runtime.workspace.opencode_version}. {instruction}",
                flush=True,
            )
        if "opencode" in running:
            active_sessions(runtime)
        # Building is deliberately staged: do not overwrite mounted configuration
        # or touch the venv before acquiring the Linux lease. Docker reuses layers.
        with tempfile.TemporaryDirectory(prefix="setup-", dir=runtime.directory) as temporary:
            directory = Path(temporary)
            file = directory / "compose.json"
            env_file = directory / "compose.env"
            write_private(file, json.dumps(_literals(guard_document(runtime, directory))))
            write_private(env_file, "")
            env = {**compose_env(), "COMPOSE_IGNORE_ORPHANS": "true"}

            def staged(*args):
                return runtime.compose_args(*args, file=file, env_file=env_file)

            print("setup: build the pinned runtime image (cached layers are reused)", flush=True)
            run_command(staged("build", "setup-guard"), env=env)
            with maintenance_process(
                staged("run", "--rm", "--no-deps", "--pull", "never", "-T", "setup-guard"),
                env=env,
                protocol="ODX_SETUP",
            ):
                if Workspace.load(runtime.workspace.root) != runtime.workspace:
                    raise OdxError(
                        "Workspace configuration changed during setup; retry deliberately"
                    )
                actual = (
                    runtime.compose_file.read_bytes() if runtime.compose_file.exists() else None
                )
                if actual != previous:
                    raise OdxError(
                        "Generated configuration changed during setup; retry deliberately"
                    )
                if "opencode" in running:
                    active_sessions(runtime)
                    # Stop the idle server before changing shared dependencies.
                    run_command(
                        runtime.compose_args("stop", "--timeout", "30", "opencode"), env=env
                    )
                print("setup: configure and prepare Odoo/PostgreSQL", flush=True)
                runtime.generate(operation_locked=True)
                run_command(
                    runtime.compose_args(
                        "up",
                        "-d",
                        "--wait",
                        "--wait-timeout",
                        "120",
                        "--no-build",
                        "odoo",
                    ),
                    env=env,
                )
                print("setup: prepare dependencies if needed", flush=True)
                # The maintenance process owns the exclusive environment lease.
                run_command(
                    runtime.compose_args(
                        "exec",
                        "-T",
                        "odoo",
                        "python3",
                        "-c",
                        "from odoocker.container import install_dependencies; "
                        "raise SystemExit(install_dependencies("
                        "environment_locked=True, only_if_needed=True))",
                    ),
                    env=env,
                )
                if keep_code:
                    print("setup: start the pinned OpenCode server", flush=True)
                    run_command(
                        runtime.compose_args(
                            "up",
                            "-d",
                            "--wait",
                            "--wait-timeout",
                            "120",
                            "--no-build",
                            "--no-deps",
                            "opencode",
                        ),
                        env=env,
                    )
                    if host == runtime.workspace.opencode_version and opencode.info(runtime):
                        raise OdxError("OpenCode API verification failed after setup")
                print(
                    "setup: environment prepared; no development database was initialized",
                    flush=True,
                )
    return 0
