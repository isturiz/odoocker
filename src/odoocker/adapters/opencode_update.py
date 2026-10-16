"""Explicit, backed-up OpenCode updates; never update the host or downgrade state."""

import hashlib
import json
import re
import sys
import tomllib
import uuid
from contextlib import contextmanager
from dataclasses import replace

from ..config import MANIFEST, VERSION, Workspace
from ..errors import OdxError
from ..locking import locked
from ..maintenance import Maintenance as Maintenance
from ..maintenance import maintenance_process
from ..runtime import _literals, compose_env, run_command, write_private
from . import opencode

# Run with the OLD image, using only its standard library, not the new engine.
# Remain alive across stop/build/start so managed operations cannot race the update.
GUARD = r"""
import fcntl, json, os, sys, tarfile
from contextlib import ExitStack
from pathlib import Path
os.umask(0o077)
def reply(status):
    print('ODX_UPDATE ' + json.dumps({'status': status}), flush=True)
with ExitStack() as stack:
    for name in ('environment.lock', 'dev-database.lock'):
        stream = stack.enter_context((Path(sys.argv[1]) / name).open('a+'))
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            reply('busy')
            raise SystemExit(1)
    reply('locked')
    if sys.stdin.readline().strip() != 'backup':
        raise SystemExit(0)
    target = Path(sys.argv[2])
    temporary = target.with_suffix('.partial')
    with temporary.open('xb') as output:
        with tarfile.open(fileobj=output, mode='w:gz', dereference=False) as archive:
            for name, source in zip(('config', 'data', 'state'), sys.argv[3:]):
                archive.add(source, arcname=name)
        output.flush()
        os.fsync(output.fileno())
    temporary.rename(target)
    reply('backed_up')
    sys.stdin.readline()  # EOF or release; process exit releases both flock locks.
"""


def pin_version(text, version):
    """Preserve comments and unrelated TOML; refuse unsupported layouts before stopping."""
    if not VERSION.fullmatch(version):
        raise OdxError("An exact OpenCode V2 release is required")
    before = tomllib.loads(text)
    if before["opencode"]["version"] == version:
        return text
    table = re.search(
        r"(?m)^\s*\[\s*(?:opencode|\"opencode\"|'opencode')\s*\][ \t]*(?:#[^\n]*)?\r?$"
        r"(?P<body>[\s\S]*?)(?=^\s*\[|\Z)",
        text,
    )
    if table is None:
        raise OdxError("Use a separate [opencode] table with a quoted version before updating")
    body = table.group("body")
    value = re.search(
        r"""(?m)^[ \t]*(?:version|"version"|'version')[ \t]*=[ \t]*(["'])(2\.[^\r\n"']+)\1""", body
    )
    if value is None:
        raise OdxError("Cannot safely edit opencode.version; use a single-line quoted value")
    start = table.start("body") + value.start(2)
    end = table.start("body") + value.end(2)
    result = text[:start] + version + text[end:]
    expected = {**before, "opencode": {**before["opencode"], "version": version}}
    if tomllib.loads(result) != expected:
        raise OdxError("Refusing to change unrelated manifest settings")
    return result


def release_key(version):
    """SemVer precedence, including numeric prerelease identifiers."""
    if not VERSION.fullmatch(version):
        raise OdxError("Invalid OpenCode release")
    core, separator, prerelease = version.partition("-")
    if separator and any(not part for part in prerelease.split(".")):
        raise OdxError("Invalid OpenCode prerelease")
    suffix = (
        (
            0,
            tuple(
                (0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split(".")
            ),
        )
        if separator
        else (1,)
    )
    return (*map(int, core.split(".")), suffix)


def saved_configuration(runtime):
    if not runtime.compose_file.is_file():
        raise OdxError("Initialize this instance with odx up, odx deps and odx up --code first")
    saved = json.loads(runtime.compose_file.read_text())

    # Engine/image changes are expected. Other pending changes must not silently
    # switch databases, resource ownership, addon mounts or authentication.
    def topology(document):
        document = json.loads(json.dumps(document))
        for name in ("odoo", "opencode"):
            service = document["services"][name]
            for key in ("image", "build", "labels"):
                service.pop(key, None)
        return document

    try:
        previous_version = saved["services"]["opencode"]["build"]["args"]["OPENCODE_VERSION"]
        if not isinstance(previous_version, str) or not VERSION.fullmatch(previous_version):
            raise OdxError("Invalid saved OpenCode version; configure this instance first")
        previous_workspace = replace(runtime.workspace, opencode_version=previous_version)
        expected = replace(runtime, workspace=previous_workspace).compose_document()
        label = "io.odoocker.configuration"
        if (
            saved["services"]["opencode"]["labels"][label]
            != expected["services"]["opencode"]["labels"][label]
        ):
            raise OdxError(
                "Other workspace settings changed; configure/up them before updating OpenCode"
            )
        if topology(saved) != topology(_literals(runtime.compose_document())):
            raise OdxError(
                "Other workspace settings changed; configure/up them before updating OpenCode"
            )
    except (KeyError, TypeError) as error:
        raise OdxError("Invalid generated configuration; refusing an OpenCode update") from error
    return saved


def saved_compose(runtime, *args, **kwargs):
    # Deliberately use the validated saved file even when the engine hash changed.
    # The temporary guard is deliberately outside the normal service graph.
    env = {**compose_env(), "COMPOSE_IGNORE_ORPHANS": "true"}
    return run_command(runtime.compose_args(*args), env=env, **kwargs)


def active_sessions(runtime):
    result = run_command(
        [
            "opencode",
            "api",
            "--server",
            runtime.url("opencode", 4096),
            "get",
            "/api/session/active",
        ],
        env=opencode.authentication(runtime),
        capture=True,
    )
    try:
        active = json.loads(result.stdout)["data"]
        if not isinstance(active, dict):
            raise ValueError("invalid active sessions")
    except (KeyError, ValueError, TypeError) as error:
        raise OdxError(
            "Cannot determine active sessions; stop OpenCode deliberately before updating"
        ) from error
    if active:
        raise OdxError("OpenCode has active sessions; finish/interrupt them before updating")


@contextmanager
def maintenance(runtime, saved, directory):
    services = saved["services"]
    # A failed build can leave the generated file pointing at a nonexistent NEW
    # image. The stopped/running service container still identifies the actual
    # OLD image, allowing a backed-up retry without starting a migrated server.
    images = saved_compose(runtime, "images", "--quiet", "opencode", capture=True).stdout.split()
    if len(images) != 1 or not re.fullmatch(r"(?:sha256:)?[a-f0-9]{12,64}", images[0]):
        raise OdxError(
            "An existing OpenCode container image is required; inspect this instance first"
        )
    mounts = {
        item["source"]: item for item in services["opencode"]["volumes"] if item["type"] == "volume"
    }
    sources = ["runtime", "opencode-config", "opencode-data", "opencode-state"]
    if any(name not in mounts for name in sources):
        raise OdxError("Generated OpenCode volumes are incomplete; refusing to update")
    document = {
        "services": {
            "guard": {
                "image": images[0],
                "init": True,
                "network_mode": "none",
                "command": [
                    "python3",
                    "-u",
                    "-c",
                    GUARD,
                    "/runtime",
                    "/backup/opencode-state.tar.gz",
                    "/source/config",
                    "/source/data",
                    "/source/state",
                ],
                "volumes": [
                    {"type": "volume", "source": name, "target": target, "read_only": readonly}
                    for name, target, readonly in zip(
                        sources,
                        ["/runtime", "/source/config", "/source/data", "/source/state"],
                        [False, True, True, True],
                        strict=True,
                    )
                ]
                + [
                    {
                        "type": "bind",
                        "source": str(directory),
                        "target": "/backup",
                        "bind": {"create_host_path": False},
                    }
                ],
            }
        },
        # External prevents a missing volume from silently becoming an empty backup.
        "volumes": {
            name: {"external": True, "name": f"{runtime.project}_{name}"} for name in sources
        },
    }
    file = directory / "maintenance.compose.json"
    write_private(file, json.dumps(_literals(document)))
    args = runtime.compose_args(
        "run", "--rm", "--no-deps", "--pull", "never", "-T", "guard", file=file
    )
    with maintenance_process(
        args, env={**compose_env(), "COMPOSE_IGNORE_ORPHANS": "true"}
    ) as guard:
        guard.image = images[0]
        try:
            yield guard
        except BaseException:
            # Stop attempted new servers BEFORE releasing the Linux locks.
            if guard.stopped:
                try:
                    saved_compose(runtime, "stop", "--timeout", "30", "odoo", "opencode")
                except (OdxError, OSError):
                    print(
                        "Could not confirm services stopped; inspect odx status.", file=sys.stderr
                    )
            raise


def update(runtime):
    target = opencode.host_version()
    manifest = runtime.workspace.root / MANIFEST
    with (
        locked(runtime.workspace.root / ".odoocker" / "opencode-update.lock"),
        locked(runtime.operation_lock),
    ):
        original = manifest.read_bytes()
        updated = pin_version(original.decode(), target)
        runtime.workspace.validate_sources()
        saved = saved_configuration(runtime)
        previous_version = saved["services"]["opencode"]["build"]["args"]["OPENCODE_VERSION"]
        if release_key(target) < release_key(previous_version):
            raise OdxError(
                f"Refusing OpenCode downgrade from {previous_version} to {target}. "
                "Use a compatible state backup for deliberate recovery instead."
            )
        running = runtime.compose(
            "ps", "--status", "running", "--services", capture=True
        ).stdout.split()
        if "opencode" in running:
            active_sessions(runtime)
        directory = runtime.directory / "opencode-updates" / uuid.uuid4().hex
        directory.mkdir(parents=True, mode=0o700)
        record = {
            "project": runtime.project,
            "target": target,
            "status": "preparing",
            "previous_image": saved["services"]["opencode"]["image"],
            "backup_complete": False,
        }

        def stage(value):
            record["status"] = value
            write_private(directory / "result.json", json.dumps(record, indent=2))

        stage("preparing")
        print(f"OpenCode: workspace {runtime.workspace.opencode_version} -> host {target}")
        print(f"Private backup/recovery files: {directory}", flush=True)
        try:
            with maintenance(runtime, saved, directory) as guard:
                record["previous_image"] = guard.image
                # Recheck after acquiring the Linux lock, before stopping either service.
                if "opencode" in running:
                    active_sessions(runtime)
                write_private(directory / MANIFEST, original.decode())
                for name in (
                    "compose.json",
                    "compose.env",
                    "context.json",
                    "odoo.conf",
                    "secrets.json",
                ):
                    write_private(directory / name, (runtime.directory / name).read_text())
                stage("stopping")
                guard.stopped = True
                saved_compose(runtime, "stop", "--timeout", "30", "odoo", "opencode")
                stage("backing_up")
                guard.backup()
                archive = directory / "opencode-state.tar.gz"
                with archive.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                record.update(backup_complete=True, archive_sha256=digest)
                stage("backed_up")
                if (
                    manifest.read_bytes() != original
                    or Workspace.load(runtime.workspace.root) != runtime.workspace
                ):
                    raise OdxError(
                        "Workspace configuration changed during the update; left unchanged"
                    )
                stage("configuring")
                write_private(manifest, updated)
                replacement = replace(runtime, workspace=Workspace.load(runtime.workspace.root))
                replacement.generate(operation_locked=True)
                stage("building")
                saved_compose(replacement, "build", "odoo", "opencode")
                stage("starting")
                saved_compose(
                    replacement,
                    "up",
                    "-d",
                    "--wait",
                    "--wait-timeout",
                    "120",
                    "--no-build",
                    "--no-deps",
                    "odoo",
                    "opencode",
                )
                stage("verifying")
                if opencode.info(replacement):
                    raise OdxError("Updated OpenCode API verification failed")
                stage("completed")
        except BaseException as error:
            phase = record["status"]
            record.update(status="failed", phase=phase, error=type(error).__name__)
            write_private(directory / "result.json", json.dumps(record, indent=2))
            message = (
                f"OpenCode update failed during {phase}. Recovery files: {directory}. "
                "No automatic downgrade or state restore was attempted."
            )
            if isinstance(error, KeyboardInterrupt):
                print(message, file=sys.stderr)
                raise
            raise OdxError(f"{message} {error}") from error
        print(f"OpenCode {target} verified. Backup retained: {directory}")
        return 0
