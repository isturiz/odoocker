"""Host-only restore orchestration using explicitly scoped Compose resources."""

import configparser
import hashlib
import json
import re
import shutil
import sys
import uuid
import zipfile

from ..adapters.opencode_update import active_sessions
from ..config import Workspace
from ..errors import OdxError
from ..locking import locked
from ..maintenance import maintenance_process
from ..reports import internal_url
from ..runtime import PACKAGE, _literals, bind, compose_env, run_command, write_private
from ..setup import GUARD
from .archive import inventory

PROBE = r"""
import configparser,json
from pathlib import Path
import psycopg2
from odoocker.container import require_ready
require_ready()
for process in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        args=process.read_bytes().split(b'\0')
    except (FileNotFoundError,PermissionError):
        continue
    if any(arg.rsplit(b'/',1)[-1] == b'odoo-bin' for arg in args):
        raise SystemExit('An unmanaged Odoo server is active; stop it before restoring')
config=configparser.ConfigParser(interpolation=None)
config.read('/etc/odoocker/odoo.conf')
o=config['options']
with psycopg2.connect(host=o['db_host'],user=o['db_user'],
                     password=o['db_password'],dbname='postgres') as db:
    with db.cursor() as cr:
        cr.execute("SELECT count(*) FROM pg_stat_activity "
                   "WHERE datname NOT IN ('postgres','template0','template1') "
                   "AND backend_type='client backend'")
        if cr.fetchone()[0]:
            raise SystemExit('Database clients are active; no services changed')
        cr.execute("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='dev')")
        exists=cr.fetchone()[0]
        cr.execute('SHOW server_version_num')
        major=int(cr.fetchone()[0])//10000
print(json.dumps({'database':exists,'filestore':(Path(o['data_dir'])/'filestore/dev').exists(),'postgres':str(major)}))
"""

FINALIZE = r"""
import configparser,sys
from pathlib import Path
import psycopg2
from odoocker.locking import locked
c=configparser.ConfigParser(interpolation=None)
c.read('/etc/odoocker/odoo.conf')
o=c['options']
with locked(Path('/var/lib/odoocker/environment.lock'),shared=True), \
     locked(Path('/var/lib/odoocker/dev-database.lock')):
    with psycopg2.connect(host=o['db_host'],port=o['db_port'],user=o['db_user'],
                         password=o['db_password'],dbname='dev') as db:
        with db.cursor() as cr:
            for key,value in [('web.base.url',sys.argv[1]),('web.base.url.freeze','True'),
                              ('report.url',sys.argv[2])]:
                cr.execute('INSERT INTO ir_config_parameter(key,value) VALUES (%s,%s) '
                           'ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value '
                           'WHERE ir_config_parameter.value IS DISTINCT FROM EXCLUDED.value',
                           [key,value])
"""

CLEANUP_SQL = (PACKAGE / "restore/cleanup.py").read_text()
CLEANUP_CHECKPOINTS = (PACKAGE / "restore/checkpoints.py").read_text()


def topology(runtime):
    if not runtime.compose_file.is_file():
        raise OdxError("Prepare this instance with odx setup before restoring")
    runtime.validate_instance()
    saved = json.loads(runtime.compose_file.read_text())
    expected = _literals(runtime.compose_document())
    try:
        config_text = (runtime.directory / "odoo.conf").read_text()
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(config_text)
        context = json.loads((runtime.directory / "context.json").read_text())
        required = {
            "db_host": "db",
            "db_port": "5432",
            "db_user": "odoo",
            "db_password": runtime.password,
            "db_name": "dev",
            "dbfilter": runtime.workspace.odoo_options.get("dbfilter", "^dev$"),
            "data_dir": "/home/odoo/.local/share/Odoo",
        }
        if any(config["options"].get(key) != value for key, value in required.items()):
            raise OdxError(
                "Generated database configuration changed; refusing non-native endpoints"
            )
        if (
            context["project"] != runtime.project
            or context["version"] != runtime.workspace.odoo_version
        ):
            raise OdxError("Generated context belongs to another instance/version")
    except (OSError, ValueError, KeyError, configparser.Error) as error:
        raise OdxError("Invalid generated restore context/configuration") from error

    def normalized(document):
        document = json.loads(json.dumps(document))
        for name in ("odoo", "opencode"):
            for key in ("image", "build"):
                document["services"][name].pop(key, None)
        return document

    if normalized(saved) != normalized(expected):
        raise OdxError("Pending workspace configuration changes; deliberately run setup first")
    digest = hashlib.sha256(
        json.dumps(
            {
                "compose": normalized(saved),
                "config": config_text,
                "context": context,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return saved, digest


def options(args, previous=None):
    result = dict(
        previous
        or {
            "create_missing_roles": False,
            "reset_admin_password": False,
            "update_outdated": False,
            "update": [],
            "replace": False,
        }
    )
    result.setdefault("keep_sql", False)
    # Old operations promised retained recovery pairs. Never opt them into deletion.
    result.setdefault("keep_checkpoints", previous is not None)
    for key in (
        "create_missing_roles",
        "reset_admin_password",
        "replace",
        "keep_sql",
        "keep_checkpoints",
    ):
        if getattr(args, key):
            result[key] = True
    if args.update_outdated:
        result.update(update_outdated=True, update=[])
    elif args.update:
        from ..container import module_names

        result.update(update=module_names(args.update).split(","), update_outdated=False)
        if "all" in result["update"]:
            raise OdxError("--update requires specific installed modules, not 'all'")
    return result


def cleanup_sql(runtime, directory, record):
    if record["options"].get("keep_sql"):
        return
    if record.get("sql_cleanup", {}).get("status") in ("removed", "absent"):
        return
    container_directory = "/workspace/" + directory.relative_to(runtime.workspace.root).as_posix()
    try:
        result = run_command(
            runtime.compose_args(
                "exec",
                "-T",
                "odoo",
                "python3",
                "-c",
                CLEANUP_SQL,
                container_directory,
            ),
            env=compose_env(),
            capture=True,
        )
        record["sql_cleanup"] = json.loads(result.stdout)
    except (OdxError, ValueError) as error:
        # Publication succeeded. A housekeeping failure must not imply a bad dev.
        record["sql_cleanup"] = {"status": "pending", "error": type(error).__name__}
        print(
            "WARNING: dev is restored, but temporary SQL cleanup is pending; "
            "retry with --resume " + record["id"],
            file=sys.stderr,
        )
    write_private(directory / "result.json", json.dumps(record, indent=2))


def cleanup_checkpoints(runtime, directory, record):
    if record["options"].get("keep_checkpoints", True):
        return
    if record.get("checkpoint_cleanup", {}).get("status") == "completed":
        return
    container_directory = "/workspace/" + directory.relative_to(runtime.workspace.root).as_posix()
    try:
        result = run_command(
            runtime.compose_args(
                "exec",
                "-T",
                "odoo",
                "/opt/venv/bin/python",
                "-c",
                CLEANUP_CHECKPOINTS,
                container_directory,
            ),
            env=compose_env(),
            capture=True,
        )
        record["checkpoint_cleanup"] = json.loads(result.stdout)
    except (OdxError, OSError, ValueError) as error:
        # The container journals each destructive step. Keep that progress on failure.
        # A disconnected exec may still hold the native lease. Its dedicated
        # journal must never be overwritten by this host-side summary.
        try:
            progress = json.loads((directory / "checkpoint-cleanup.json").read_text())
        except (OSError, ValueError):
            progress = {}
        progress.update(status="pending", error=type(error).__name__)
        record["checkpoint_cleanup"] = progress
        print(
            "WARNING: dev is restored, but checkpoint cleanup is pending; "
            "retry with --resume " + record["id"],
            file=sys.stderr,
        )
    write_private(directory / "result.json", json.dumps(record, indent=2))
    if record["checkpoint_cleanup"].get("status") == "completed" and record[
        "checkpoint_cleanup"
    ].get("database_removed"):
        print("Restore update checkpoint and its filestore removed.")


def document(runtime, saved, directory, image, database_image, record):
    def literal(value):
        if isinstance(value, str):
            return value.replace("$$", "$")
        if isinstance(value, dict):
            return {key: literal(item) for key, item in value.items()}
        if isinstance(value, list):
            return [literal(item) for item in value]
        return value

    saved = literal(saved)
    mounts = [
        mount
        for mount in saved["services"]["odoo"]["volumes"]
        if mount["target"] not in ("/workspace", "/etc/odoocker/odoo.conf")
    ]
    mounts = json.loads(json.dumps(mounts))
    for mount in mounts:
        if (
            mount["target"] != "/home/odoo/.local/share/Odoo"
            and mount["target"] != "/var/lib/odoocker"
        ):
            mount["read_only"] = True
    mounts += [
        bind(directory, "/operation"),
        bind(directory / "engine", "/restore-engine", readonly=True),
        {"type": "volume", "source": "restore-socket", "target": "/var/run/postgresql"},
    ]
    if not record.get("archive_extracted"):
        mounts.append(bind(record["source"], "/input/backup.zip", readonly=True))
    return {
        "services": {
            "restore-guard": {
                "image": image,
                "init": True,
                "network_mode": "none",
                "command": ["python3", "-u", "-c", GUARD, "/var/lib/odoocker"],
                "volumes": [{"type": "volume", "source": "runtime", "target": "/var/lib/odoocker"}],
            },
            "restore-db": {
                "image": database_image,
                "network_mode": "none",
                "shm_size": "256m",
                "environment": saved["services"]["db"]["environment"],
                "command": [
                    "postgres",
                    "-c",
                    "listen_addresses=",
                    "-c",
                    "unix_socket_directories=/var/run/postgresql",
                    "-c",
                    "unix_socket_permissions=0777",
                ],
                "volumes": saved["services"]["db"]["volumes"]
                + [{"type": "volume", "source": "restore-socket", "target": "/var/run/postgresql"}],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "pg_isready",
                        "-h",
                        "/var/run/postgresql",
                        "-U",
                        "odoo",
                        "-d",
                        "postgres",
                    ],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 30,
                },
            },
            "restore-worker": {
                "image": image,
                "init": True,
                "network_mode": "none",
                "working_dir": "/operation",
                "environment": {"PYTHONPATH": "/restore-engine", "PYTHONDONTWRITEBYTECODE": "1"},
                "command": ["/opt/venv/bin/python", "-m", "odoocker.restore.worker"],
                "volumes": mounts,
            },
        },
        "volumes": {
            **{
                name: {"external": True, "name": f"{runtime.project}_{name}"}
                for name in saved["volumes"]
            },
            "restore-socket": {"name": f"{runtime.project}_restore-{record['id']}-socket"},
        },
    }


def restore(runtime, args):
    if bool(args.backup) == bool(args.resume):
        raise OdxError("Provide exactly one ZIP backup or --resume <restore-id>")
    if args.resume and not re.fullmatch(r"[a-f0-9]{32}", args.resume):
        raise OdxError("Restore ID must be 32 lowercase hexadecimal characters")
    with locked(runtime.operation_lock):
        runtime.workspace.validate_sources()
        saved, fingerprint = topology(runtime)
        if args.resume:
            directory = runtime.directory / "restores" / args.resume
            try:
                original_record = (directory / "result.json").read_bytes()
                record = json.loads(original_record)
            except (OSError, ValueError) as error:
                raise OdxError("Restore record not found or invalid") from error
            if (
                record.get("schema") != 1
                or record.get("id") != args.resume
                or record.get("project") != runtime.project
                or record.get("configuration") != fingerprint
            ):
                raise OdxError(
                    "Restore belongs to another instance/configuration; no services changed"
                )
            changed = options(args, record["options"])
            if record.get("publication_started") and changed != {
                "keep_sql": False,
                "keep_checkpoints": True,
                **record["options"],
            }:
                raise OdxError("Publication has begun; resume without changing restore options")
            record["options"] = changed
            if record.get("status") == "completed" and record.get("host_finalized"):
                cleanup_sql(runtime, directory, record)
                cleanup_checkpoints(runtime, directory, record)
                print(f"Restore already completed: {directory}")
                return 0
        else:
            try:
                source = args.backup.expanduser().resolve(strict=True)
            except OSError as error:
                raise OdxError("Backup does not exist or cannot be accessed") from error
            if not source.is_file():
                raise OdxError("Backup must be a regular Odoo ZIP file")
            try:
                with zipfile.ZipFile(source) as archive:
                    inventory(archive)
            except zipfile.BadZipFile as error:
                raise OdxError("Backup is not a valid Odoo ZIP") from error
            restore_id = uuid.uuid4().hex
            directory = runtime.directory / "restores" / restore_id
            directory.mkdir(parents=True, mode=0o700)
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            record = {
                "schema": 1,
                "id": restore_id,
                "project": runtime.project,
                "configuration": fingerprint,
                "odoo": runtime.workspace.odoo_version,
                "postgres": runtime.workspace.postgres_version,
                "source": str(source),
                "archive_sha256": digest,
                "temporary": "odx_restore_" + restore_id,
                "working_database": "odx_restore_" + restore_id,
                "previous": "odx_previous_" + restore_id,
                "options": options(args),
                "status": "preparing",
                "phase": "preparing",
            }
        directory.chmod(0o700)
        print(f"Restore ID: {record['id']}\nPrivate recovery artifacts: {directory}", flush=True)
        if record["options"]["reset_admin_password"]:
            print("WARNING: the local administrator password will be 'admin' (development only).")

        def save():
            write_private(directory / "result.json", json.dumps(record, indent=2))

        if not args.resume:
            save()
        env = {**compose_env(), "COMPOSE_IGNORE_ORPHANS": "true"}

        def regular(*command, **kwargs):
            return run_command(runtime.compose_args(*command), env=env, **kwargs)

        running = regular("ps", "--status", "running", "--services", capture=True).stdout.split()
        if args.resume and not {"db", "odoo"}.issubset(running):
            # Recover a disconnected HOST controller, not an active worker. The
            # native leases remain the authority; never run two PostgreSQLs.
            recovery_file = directory / "maintenance.compose.json"
            if recovery_file.is_file():

                def recovery(*command, **kwargs):
                    return run_command(
                        runtime.compose_args(
                            *command, file=recovery_file, env_file=directory / "compose.env"
                        ),
                        env=env,
                        **kwargs,
                    )

                text = recovery("ps", "--all", "--format", "json", capture=True).stdout.strip()
                rows = (
                    json.loads(text)
                    if text.startswith("[")
                    else [json.loads(line) for line in text.splitlines() if line]
                )
                if any(
                    row.get("Service") == "restore-worker"
                    and row.get("State") in ("running", "restarting", "paused")
                    for row in rows
                ):
                    raise OdxError("Restore worker is still active; wait for it before resuming")
                with maintenance_process(
                    runtime.compose_args(
                        "run",
                        "--rm",
                        "--no-deps",
                        "--pull",
                        "never",
                        "-T",
                        "restore-guard",
                        file=recovery_file,
                        env_file=directory / "compose.env",
                    ),
                    env=env,
                    protocol="ODX_SETUP",
                ):
                    recovery(
                        "stop", "--timeout", "30", "restore-worker", "restore-db", capture=True
                    )
                    regular("start", "--wait", "--wait-timeout", "120", "db", "odoo", capture=True)
                running = regular(
                    "ps", "--status", "running", "--services", capture=True
                ).stdout.split()
        if not {"db", "odoo"}.issubset(running):
            raise OdxError("Run odx setup before restoring; db and odoo services must be prepared")
        if "opencode" in running:
            active_sessions(runtime)
        record.setdefault("previous_running", running)
        # Existing runtime dependencies are checked without rebuilding or replacing it.
        probe = json.loads(
            regular("exec", "-T", "odoo", "/opt/venv/bin/python", "-c", PROBE, capture=True).stdout
        )
        if probe["postgres"] != record["postgres"]:
            raise OdxError("Actual PostgreSQL major differs from the configured instance")
        if (
            (probe["database"] or probe["filestore"])
            and not record["options"]["replace"]
            and not record.get("publication_started")
        ):
            raise OdxError(
                "dev or its filestore exists; --replace explicitly preserves and replaces it"
            )
        images = []
        for service in ("odoo", "db"):
            image = regular("images", "--quiet", service, capture=True).stdout.strip()
            if not re.fullmatch(r"(?:sha256:)?[a-f0-9]{12,64}", image):
                raise OdxError("Existing prepared container images are required")
            images.append(image)

        def stage():
            # Never replace a running orphan worker's engine, config, or state.
            if args.resume and (directory / "result.json").read_bytes() != original_record:
                raise OdxError("Restore state changed during preparation; retry --resume")
            engine = directory / "engine" / "odoocker"
            if engine.exists():
                shutil.rmtree(engine)  # Only this operation's generated engine snapshot.
            shutil.copytree(PACKAGE, engine, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            config = configparser.ConfigParser(interpolation=None)
            config.read(runtime.directory / "odoo.conf")
            config["options"]["db_host"] = "/var/run/postgresql"
            config["options"]["db_port"] = "5432"
            from io import StringIO

            output = StringIO()
            config.write(output)
            write_private(directory / "odoo.conf", output.getvalue())
            save()

        file = directory / "maintenance.compose.json"
        write_private(
            file, json.dumps(_literals(document(runtime, saved, directory, *images, record)))
        )
        write_private(directory / "compose.env", "")

        def scoped(*command, **kwargs):
            return run_command(
                runtime.compose_args(*command, file=file, env_file=directory / "compose.env"),
                env=env,
                **kwargs,
            )

        began = False
        try:
            with maintenance_process(
                runtime.compose_args(
                    "run",
                    "--rm",
                    "--no-deps",
                    "--pull",
                    "never",
                    "-T",
                    "restore-guard",
                    file=file,
                    env_file=directory / "compose.env",
                ),
                env=env,
                protocol="ODX_SETUP",
            ):
                if (
                    Workspace.load(runtime.workspace.root) != runtime.workspace
                    or topology(runtime)[1] != fingerprint
                ):
                    raise OdxError("Workspace changed during restore preparation")
                stage()
                # Check again under the exclusive Linux lease, before stopping anything.
                regular("exec", "-T", "odoo", "/opt/venv/bin/python", "-c", PROBE, capture=True)
                if "opencode" in running:
                    active_sessions(runtime)
                    regular("stop", "--timeout", "30", "opencode", capture=True)
                regular("stop", "--timeout", "60", "odoo", "db", capture=True)
                began = True
                scoped(
                    "up",
                    "-d",
                    "--no-build",
                    "--wait",
                    "--wait-timeout",
                    "120",
                    "restore-db",
                    capture=True,
                )
                scoped("up", "-d", "--no-build", "--no-deps", "restore-worker", capture=True)
            # Worker acquires both leases as the guard releases them and keeps them
            # even if this host process disappears. Never reimport blindly on resume.
            result = scoped("wait", "restore-worker", capture=True, check=False)
            record = json.loads((directory / "result.json").read_text())
            if result.returncode or record.get("status") != "completed":
                raise OdxError(
                    record.get("message", "Restore failed; inspect the private restore log")
                )
        except BaseException:
            print(
                f"Restore stopped. Recovery ID: {record['id']}. Artifacts: {directory}",
                file=sys.stderr,
            )
            raise
        finally:
            if began:
                # A failed stop prevents the normal PostgreSQL from starting.
                scoped("stop", "--timeout", "30", "restore-worker", "restore-db", capture=True)
                regular("start", "--wait", "--wait-timeout", "120", "db", "odoo", capture=True)
        url = runtime.url("odoo", 8069)
        report_config = configparser.ConfigParser(interpolation=None)
        report_config.read(runtime.directory / "odoo.conf")
        regular(
            "exec",
            "-T",
            "odoo",
            "/opt/venv/bin/python",
            "-c",
            FINALIZE,
            url,
            internal_url(report_config["options"]),
            capture=True,
        )
        access = {
            "login": record["admin"]["login"],
            "url": url,
            "password": "admin" if record["options"]["reset_admin_password"] else None,
            "password_reset": record["options"]["reset_admin_password"],
        }
        write_private(directory / "access.json", json.dumps(access, indent=2))
        if "opencode" in record["previous_running"]:
            regular("start", "--wait", "--wait-timeout", "120", "opencode", capture=True)
        record["host_finalized"] = True
        save()
        cleanup_sql(runtime, directory, record)
        cleanup_checkpoints(runtime, directory, record)
        print(f"Restored dev; {record['verified_attachments']} attachments verified. Run odx run.")
        print(f"Local access details: {directory / 'access.json'}")
        return 0
