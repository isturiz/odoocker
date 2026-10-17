"""Host orchestration and the small command surface also available to agents."""

import argparse
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

from . import __version__
from .adapters import opencode
from .config import (
    LOCAL,
    MANIFEST,
    VALIDATED_ODOO_VERSIONS,
    Workspace,
    find_workspace,
    validate_name,
    validate_odoo_version,
)
from .errors import OdxError
from .locking import locked
from .repositories import fetch_repositories
from .runtime import Runtime, run_command, write_private


def parser():
    result = argparse.ArgumentParser(prog="odx", description="Isolated Odoo development workspaces")
    result.add_argument("--version", action="version", version=f"odoocker {__version__}")
    result.add_argument(
        "--workspace", type=Path, help="Workspace directory (otherwise discover upward)"
    )
    result.add_argument(
        "--instance", default="main", help="Explicit runtime instance (default: main)"
    )
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init", help="Create a new declarative workspace, without starting Docker"
    )
    init.add_argument("directory", type=Path)
    init.add_argument("--name")
    init.add_argument("--odoo", default="19.0", metavar="MAJOR.0")
    init.add_argument("--source", type=Path, help="Existing Odoo checkout, mounted read-only")
    init.add_argument(
        "--opencode-version", help="Exact V2 release; defaults to installed host version"
    )
    fetch_parser = commands.add_parser(
        "fetch", help="Clone missing core/addon sources; keep existing checkouts unchanged"
    )
    fetch_parser.add_argument("--dry-run", action="store_true", help="Preview without cloning")
    configure = commands.add_parser(
        "configure", help="Generate instance-owned Docker and Odoo configuration"
    )
    configure.add_argument(
        "--allow-database-switch",
        action="store_true",
        help="Select another PostgreSQL major's independent volume; never migrates/deletes data",
    )
    up = commands.add_parser("up", help="Build and start the Odoo development services")
    up.add_argument(
        "--code", action="store_true", help="Also start the OpenCode server (requires deps)"
    )
    up.add_argument("--no-build", action="store_true")
    commands.add_parser("down", help="Stop this instance; keep every persistent volume")
    status = commands.add_parser("status", help="Show instance context and running services")
    status.add_argument("--json", action="store_true")
    commands.add_parser("doctor", help="Read-only diagnostics for this workspace")
    setup = commands.add_parser("setup", help="Configure, prepare services and install needed deps")
    setup.add_argument(
        "--code", action="store_true", help="Also prepare the pinned OpenCode server"
    )
    commands.add_parser(
        "deps", help="Install Linux Odoo/addon requirements under an exclusive lock"
    )
    run = commands.add_parser(
        "run", help="Run Odoo in the foreground on the instance's dev database"
    )
    run.add_argument("--init", dest="install")
    run.add_argument("--update")
    run.add_argument("--stop-after-init", action="store_true")
    tests = commands.add_parser(
        "test", help="Run tests on a fresh isolated database and data directory"
    )
    tests.add_argument("--module", required=True)
    tests.add_argument("--tags")
    tests.add_argument(
        "--keep", action="store_true", help="Keep test DB and filestore for diagnosis"
    )
    db = commands.add_parser("db", help="Development database maintenance")
    databases = db.add_subparsers(dest="database_command", required=True)
    listing = databases.add_parser("list", help="List this instance's databases and sizes")
    listing.add_argument("--json", action="store_true", help="Print machine-readable inventory")
    dropping = databases.add_parser("drop", help="Delete one exact database and its filestore")
    dropping.add_argument("database", help="Exact database name; no patterns or bulk selectors")
    dropping.add_argument("--dry-run", action="store_true", help="Inspect without deleting")
    dropping.add_argument(
        "--yes", action="store_true", help="Explicitly skip interactive confirmation"
    )
    restore = databases.add_parser(
        "restore", help="Restore a trusted Odoo SQL ZIP into dev, with neutralization"
    )
    restore.add_argument("backup", type=Path, nargs="?")
    restore.add_argument("--resume", metavar="ID", help="Continue a retained restore operation")
    restore.add_argument(
        "--keep-sql",
        action="store_true",
        help="Keep the extracted SQL after a successful restore (retained on failure regardless)",
    )
    restore.add_argument(
        "--keep-checkpoints",
        action="store_true",
        help="Keep this restore's update checkpoints after success (always retained on failure)",
    )
    restore.add_argument(
        "--create-missing-roles",
        action="store_true",
        help="Explicitly create missing dump roles as restricted NOLOGIN roles",
    )
    restore.add_argument(
        "--reset-admin-password",
        action="store_true",
        help="Set base.user_admin's password to 'admin'; keep its login unchanged",
    )
    updates = restore.add_mutually_exclusive_group()
    updates.add_argument("--update", help="Explicit comma-separated installed modules; not all")
    updates.add_argument(
        "--update-outdated",
        action="store_true",
        help="Update installed addons newer locally or responsible for missing stored columns",
    )
    restore.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly replace dev, retaining the previous database and filestore",
    )
    passwd = commands.add_parser("passwd", help="Change an Odoo user's password in dev")
    passwd.add_argument(
        "--login", help="Exact login; defaults to base.user_admin, not a numeric ID"
    )
    passwd.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the new password from stdin, not arguments",
    )
    shell = commands.add_parser("sh", help="Open a terminal in one explicitly selected service")
    shell.add_argument("service", choices=["odoo", "pg", "opencode"])
    shell.add_argument("--psql", action="store_true", help="With pg, open psql connected to dev")
    commands.add_parser("_shell", help=argparse.SUPPRESS)
    code = commands.add_parser("code", help="Open a supported coding assistant")
    assistants = code.add_subparsers(dest="assistant", required=True)
    oc = assistants.add_parser(
        "opencode",
        help="Connect to OpenCode, or update its container to the host version",
        epilog="Use 'odx code opencode update' to back up and update to the host version.",
    )
    oc.add_argument(
        "--info", action="store_true", help="Check authenticated server API instead of TUI"
    )
    oc.add_argument("args", nargs=argparse.REMAINDER, help="OpenCode flags after --")
    logs = commands.add_parser("logs", help="Read service logs or the artifacts of a test run")
    logs.add_argument("service", choices=["odoo", "opencode", "db"], nargs="?", default="odoo")
    logs.add_argument("--test", dest="test_id")
    logs.add_argument("--result", action="store_true", help="Show test result JSON rather than log")
    commands.add_parser("_serve", help=argparse.SUPPRESS)
    return result


def init_workspace(args):
    root = args.directory.expanduser().resolve()
    name = validate_name(args.name or root.name, "workspace name")
    validate_odoo_version(args.odoo)
    version = args.opencode_version or opencode.host_version()
    # Validate all content before creating any user-facing files.
    if not re.fullmatch(r"2\.\d+\.\d+(?:-[\w.-]+)?", version):
        raise OdxError("An exact OpenCode V2 release is required")
    if root.exists() and any(root.iterdir()):
        raise OdxError(f"Refusing to initialize a non-empty directory: {root}")
    source = args.source.expanduser().resolve() if args.source else None
    if source:
        Workspace(root, name, args.odoo, version, source).validate_sources()
    root.mkdir(parents=True, exist_ok=True)
    write_private(
        root / MANIFEST,
        (
            f'schema = 1\n\n[workspace]\nname = "{name}"\n\n'
            f'[odoo]\nversion = "{args.odoo}"\n\n[opencode]\nversion = "{version}"\n'
        ),
    )
    if source:
        write_private(root / LOCAL, f"[odoo]\nsource = {json.dumps(str(source))}\naddons = []\n")
    write_private(root / ".gitignore", ".odoocker/\nodoocker.local.toml\n.env\n.DS_Store\n")
    write_private(
        root / "AGENTS.md",
        (
            "# Odoo development workspace\n\n"
            "Use the installed Linux `odx` inside the OpenCode container.\n"
            "- `odx status`: inspect the explicit instance context.\n"
            "- `odx test --module <technical_name>`: fresh test DB and filestore per run.\n"
            "- `odx test --module <name> --tags <tags>`: scope tests explicitly.\n"
            "- Tests retain logs/results; `--keep` also preserves DB and filestore.\n"
            "- `odx db list`: inspect databases; `db drop NAME --dry-run`: preview deletion.\n"
            "- `db drop NAME --yes` deletes that DB and filestore; stop managed work first.\n"
            "- `odx passwd`: change a dev user's password; never pass passwords as arguments.\n"
            "- `odx run` uses the development DB; do not use it for tests.\n"
            "- Worktrees isolate files, not databases or external addon checkouts.\n"
            "- Do not change dependencies while another agent is using the environment.\n"
            "- Do not manage Docker or replace native OpenCode worktree management.\n"
        ),
    )
    print(f"Created {root}\nNext: cd {root}; then odx fetch and odx setup --code")
    warn_unvalidated(args.odoo)
    return 0


def warn_unvalidated(version):
    if version not in VALIDATED_ODOO_VERSIONS:
        validated = ", ".join(VALIDATED_ODOO_VERSIONS)
        print(
            f"Odoo {version} is not live-validated (validated: {validated}). "
            "Runtime capabilities, Python and addon dependencies must still be compatible.",
            file=sys.stderr,
        )


def fetch(workspace, *, dry_run=False):
    if dry_run:
        fetch_core(workspace, dry_run=True)
        fetch_repositories(workspace, dry_run=True)
        return 0
    with locked(workspace.root / ".odoocker" / "fetch.lock"):
        fetch_core(workspace)
        fetch_repositories(workspace)
    return 0


def fetch_core(workspace, *, dry_run=False):
    if workspace.source.exists():
        workspace.validate_core()
        print(f"Keeping existing source: {workspace.source}")
        return 0
    expected = workspace.root / ".odoocker" / "sources" / "odoo"
    if workspace.source != expected:
        raise OdxError("Explicit local source does not exist; it will not be created automatically")
    if dry_run:
        print(f"Clone Odoo {workspace.odoo_version} -> {workspace.source}")
        return 0
    workspace.source.parent.mkdir(parents=True, exist_ok=True)
    temporary = workspace.source.with_name(f"odoo-{uuid.uuid4().hex}.partial")
    try:
        run_command(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                workspace.odoo_version,
                "https://github.com/odoo/odoo.git",
                str(temporary),
            ]
        )
        temporary.rename(workspace.source)
    except BaseException:
        # Only this operation's uniquely named incomplete clone is eligible for cleanup.
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return 0


def in_container(args):
    from . import container

    if args.workspace or args.instance != "main":
        raise OdxError("Container context is fixed by its instance; select instances from the host")
    if args.command == "status":
        print(json.dumps(container.context(), indent=2))
        return 0
    if args.command == "deps":
        return container.install_dependencies()
    if args.command == "run":
        return container.run_odoo(
            install=args.install,
            update=args.update,
            stop=args.stop_after_init,
        )
    if args.command == "test":
        return container.run_tests(args.module, tags=args.tags, keep=args.keep)
    if args.command == "passwd" or (
        args.command == "db" and args.database_command in ("list", "drop")
    ):
        return container.database_command(args)
    if args.command == "_shell":
        return container.open_shell()
    if args.command == "_serve":
        return container.serve_opencode()
    raise OdxError(
        f"{args.command} is a host command; agents can use "
        "status, deps, run, test, db list/drop, passwd"
    )


def _status(runtime, as_json=False):
    info = {
        "workspace": runtime.workspace.name,
        "directory": str(runtime.workspace.root),
        "source": str(runtime.workspace.source),
        "odoo": runtime.workspace.odoo_version,
        "postgres": runtime.workspace.postgres_version,
        "pgvector": runtime.workspace.pgvector,
        "instance": runtime.instance,
        "project": runtime.project,
        "generated": runtime.compose_file.exists(),
    }
    if runtime.compose_file.exists():
        services = runtime.compose("ps", "--format", "json", capture=True)
        # Compose releases support either JSON lines or an array.
        text = services.stdout.strip()
        rows = (
            json.loads(text)
            if text.startswith("[")
            else [json.loads(line) for line in text.splitlines() if line.strip()]
        )
        info["services"] = rows
        for row in rows:
            service = row.get("Service")
            port = {"odoo": 8069, "opencode": 4096}.get(service)
            if port and row.get("State") == "running":
                info[f"{service}_url"] = runtime.url(service, port)
    if as_json:
        print(json.dumps(info, indent=2))
    else:
        for key, value in info.items():
            if key != "services":
                print(f"{key}: {value}")
        for service in info.get("services", []):
            print(f"  {service['Service']}: {service.get('State', 'unknown')}")
    return 0


def host(args):
    if args.command == "init":
        return init_workspace(args)
    workspace = Workspace.load(find_workspace(args.workspace))
    warn_unvalidated(workspace.odoo_version)
    if args.command == "fetch":
        return fetch(workspace, dry_run=args.dry_run)
    if args.command == "doctor":
        workspace.validate_sources()
        run_command(["docker", "compose", "version"])
        run_command(["docker", "info", "--format", "{{.ServerVersion}}"])
        print(f"OpenCode host: {opencode.host_version()} (pinned: {workspace.opencode_version})")
        print(f"Workspace: {workspace.root}\nOdoo source: {workspace.source}")
        print("Source version checked; live worktree and PDF support still require validation.")
        return 0
    runtime = Runtime.load(workspace, args.instance)
    if args.command == "db" and args.database_command == "restore":
        from .restore.host import restore

        return restore(runtime, args)
    if args.command == "passwd" or args.command == "db":
        from .container import database_arguments

        command = ["exec", *([] if sys.stdin.isatty() else ["-T"]), "odoo", "odx"]
        command.extend(database_arguments(args))
        with locked(runtime.operation_lock):
            return runtime.compose(*command, check=False).returncode
    if args.command == "sh":
        if args.psql and args.service != "pg":
            raise OdxError("--psql is only supported with odx sh pg")
        service = "db" if args.service == "pg" else args.service
        program = (
            ["psql", "-X", "-U", "odoo", "--dbname=dev"]
            if args.psql
            else ["sh"]
            if service == "db"
            else ["odx", "_shell"]
        )
        with locked(runtime.operation_lock):
            return runtime.compose(
                "exec",
                *([] if sys.stdin.isatty() else ["-T"]),
                *(["--user", "postgres"] if service == "db" else []),
                service,
                *program,
                check=False,
            ).returncode
    if args.command == "setup":
        from .setup import prepare

        prepare(runtime, code=args.code)
        return _status(runtime)
    if args.command == "configure":
        if args.allow_database_switch and runtime.compose_file.exists():
            running = runtime.compose("ps", "--status", "running", "--quiet", capture=True)
            if running.stdout.strip():
                raise OdxError("Stop this instance with odx down before switching database volumes")
        runtime.generate(allow_database_switch=args.allow_database_switch)
        print(runtime.compose_file)
        return 0
    if args.command == "status":
        return _status(runtime, args.json)
    if args.command == "up":
        if not runtime.compose_file.exists():
            runtime.generate()
        with locked(runtime.operation_lock):
            command = ["up", "-d", "--wait", "--wait-timeout", "120"]
            command.append("--no-build" if args.no_build else "--build")
            command.extend(["odoo", "opencode"] if args.code else ["odoo"])
            if args.code:
                opencode.host_version()
            runtime.compose(*command)
        return _status(runtime)
    if args.command == "down":
        with locked(runtime.operation_lock):
            runtime.compose("--profile", "code", "down")
        return 0
    if args.command == "deps":
        return runtime.compose("exec", "-T", "odoo", "odx", "deps", check=False).returncode
    if args.command == "run":
        command = ["exec"]
        if not sys.stdin.isatty():
            command.append("-T")
        command.extend(["odoo", "odx", "run"])
        if args.install:
            command.extend(["--init", args.install])
        if args.update:
            command.extend(["--update", args.update])
        if args.stop_after_init:
            command.append("--stop-after-init")
        return runtime.compose(*command, check=False).returncode
    if args.command == "test":
        # Every host-invoked test gets its own network namespace; no published test ports.
        command = ["run", "--rm", "--no-deps", "-T", "odoo", "odx", "test", "--module", args.module]
        if args.tags:
            command.append(f"--tags={args.tags}")
        if args.keep:
            command.append("--keep")
        return runtime.compose(*command, check=False).returncode
    if args.command == "code":
        # Reserve only an unescaped exact action; `-- update` still reaches the TUI.
        if args.args == ["update"]:
            if args.info:
                raise OdxError("update cannot be combined with --info")
            from .adapters.opencode_update import update

            return update(runtime)
        if args.args[:1] == ["update"]:
            raise OdxError("Usage: odx code opencode update (no additional arguments)")
        if args.args[:1] == ["--"]:
            args.args = args.args[1:]
        if args.info:
            return opencode.info(runtime)
        return opencode.connect(runtime, args.args)
    if args.command == "logs":
        if args.test_id:
            if not re.fullmatch(r"[0-9a-f]{32}", args.test_id):
                raise OdxError("Test ID must be the generated 32-character hexadecimal run ID")
            filename = "result.json" if args.result else "odoo.log"
            runtime.compose(
                "exec", "-T", "odoo", "cat", f"/var/lib/odoocker/tests/{args.test_id}/{filename}"
            )
        else:
            runtime.compose("logs", "--tail", "100", args.service)
        return 0
    raise OdxError("This command is only available inside the managed Linux container")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return in_container(args) if os.environ.get("ODOOCKER_CONTAINER") == "1" else host(args)
    except OdxError as error:
        print(f"odx: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nodx: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
