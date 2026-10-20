"""Commands available to agents inside Linux, without access to the Docker socket."""

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from .errors import OdxError
from .locking import locked
from .odoo_compat import check_python_minimum
from .runtime import run_command, write_private

STATE = Path("/var/lib/odoocker")
PYTHON = "/opt/venv/bin/python"
ODOO = "/opt/odoo/odoo-bin"
CONFIG = "/etc/odoocker/odoo.conf"
MODULES = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*(?:,[a-zA-Z][a-zA-Z0-9_]*)*\Z")


def context():
    return json.loads(Path("/etc/odoocker/context.json").read_text())


def requirements():
    result = [Path("/opt/odoo/requirements.txt")]
    settings = context()
    result.extend(Path(item) for item in settings.get("requirements", []))
    for item in settings["addons"]:
        root = Path(item)
        if (root / "requirements.txt").is_file():
            result.append(root / "requirements.txt")
        for module in sorted(root.iterdir()):
            if (module / "__manifest__.py").is_file() and (module / "requirements.txt").is_file():
                result.append(module / "requirements.txt")
    return result


def dependency_fingerprint():
    digest = hashlib.sha256()
    for requirement in requirements():
        digest.update(str(requirement).encode())
        digest.update(requirement.read_bytes())
    return digest.hexdigest()


def require_ready():
    check_python_minimum(Path(ODOO).parent)
    stamp = STATE / "dependencies.json"
    if not Path(PYTHON).is_file() or not stamp.exists():
        raise OdxError("Linux dependencies are not installed; run odx deps")
    try:
        metadata = json.loads(stamp.read_text())
    except ValueError as error:
        raise OdxError("Invalid dependency marker; run odx deps") from error
    if not isinstance(metadata, dict) or metadata.get("fingerprint") != dependency_fingerprint():
        raise OdxError("Dependency declarations changed; run odx deps before executing Odoo")


def install_dependencies(*, environment_locked=False, only_if_needed=False):
    # setup holds the Linux maintenance lease in another container for the whole
    # preparation. Normal `odx deps` still acquires its own exclusive lock.
    with nullcontext() if environment_locked else locked(STATE / "environment.lock"):
        check_python_minimum(Path(ODOO).parent)
        if only_if_needed:
            try:
                require_ready()
            except (OdxError, ValueError):
                pass
            else:
                print("Dependencies unchanged; using the prepared Linux environment")
                return 0
        # An interrupted or failed installation must not leave a ready marker.
        (STATE / "dependencies.json").unlink(missing_ok=True)
        if not Path(PYTHON).is_file():
            run_command(["uv", "venv", "--python", "/usr/local/bin/python3", "/opt/venv"])
        args = [
            "uv",
            "pip",
            "install",
            "--python",
            PYTHON,
            "--constraint",
            "/opt/odoo/requirements.txt",
            "setuptools<81",
        ]
        for requirement in requirements():
            args.extend(["--requirement", str(requirement)])
        run_command(args)
        run_command(["uv", "pip", "check", "--python", PYTHON])
        write_private(
            STATE / "dependencies.json",
            json.dumps(
                {
                    "fingerprint": dependency_fingerprint(),
                }
            ),
        )
    return 0


def module_names(value):
    if not MODULES.fullmatch(value):
        raise OdxError("Modules must be comma-separated technical names")
    return value


def run_odoo(*, install=None, update=None, stop=False):
    with locked(STATE / "environment.lock", shared=True), locked(STATE / "dev-database.lock"):
        require_ready()
        args = [PYTHON, ODOO, "--config", CONFIG]
        if install:
            args.extend(["--init", module_names(install)])
        if update:
            args.extend(["--update", module_names(update)])
        initialized = prepare_reports()
        if stop:
            code = run_command([*args, "--stop-after-init", "--no-http"], check=False).returncode
            if code == 0:
                require_reports()
            return code
        if not initialized:
            if not install:
                raise OdxError(
                    "Initialize dev with odx run --init base --stop-after-init, or restore a backup"
                )
            code = run_command([*args, "--stop-after-init", "--no-http"], check=False).returncode
            if code:
                return code
            require_reports()
            # Initialization/update flags were consumed by the successful offline phase.
            args = [PYTHON, ODOO, "--config", CONFIG]
        return run_command([*args, "--dev=all"], check=False).returncode


def prepare_reports():
    result = run_command([PYTHON, "-m", "odoocker.reports", "--config", CONFIG], capture=True)
    try:
        state = json.loads(result.stdout)
        if type(state["initialized"]) is not bool:
            raise ValueError("Invalid initialization state")
        return state["initialized"]
    except (ValueError, TypeError, KeyError) as error:
        raise OdxError("Invalid development report preparation result") from error


def require_reports():
    if not prepare_reports():
        raise OdxError("dev is not initialized; cannot prepare development report assets")


def database_arguments(args):
    if args.command == "passwd":
        result = ["passwd"]
        if args.login is not None:
            result.extend(["--login", args.login])
        if args.password_stdin:
            result.append("--password-stdin")
    else:
        result = ["db", args.database_command]
        if args.database_command == "list":
            if args.json:
                result.append("--json")
        else:
            result.append(args.database)
            if args.dry_run:
                result.append("--dry-run")
            if args.yes:
                result.append("--yes")
    return result


def database_command(args):
    # psycopg2 and Odoo belong to the Linux virtualenv, not the engine interpreter.
    if not Path(PYTHON).is_file():
        raise OdxError("Linux dependencies are not installed; run odx deps")
    return run_command(
        [PYTHON, "-m", "odoocker.database", *database_arguments(args)], check=False
    ).returncode


def open_shell():
    # Shells share the environment and keep maintenance/deps from replacing it.
    with locked(STATE / "environment.lock", shared=True):
        return run_command(["/bin/bash"], check=False).returncode


@contextmanager
def test_port():
    """Coordinate port selection between managed tests in this container namespace."""
    with locked(STATE / "port-selection.lock"):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            # The file lock is kept until Odoo exits; another managed run cannot claim it.
            lock = locked(STATE / f"test-port-{port}.lock")
            lock.__enter__()
    try:
        yield port
    finally:
        lock.__exit__(None, None, None)


def _database_cleanup(database):
    # Execute using the Linux environment that owns psycopg2, not the engine's Python.
    script = """
import configparser
import sys
import psycopg2
from psycopg2 import sql
config = configparser.ConfigParser(interpolation=None)
config.read('/etc/odoocker/odoo.conf')
options = config['options']
connection = psycopg2.connect(host=options['db_host'], user=options['db_user'],
    password=options['db_password'], dbname='postgres')
connection.autocommit = True
with connection.cursor() as cursor:
    cursor.execute(sql.SQL('DROP DATABASE IF EXISTS {}').format(sql.Identifier(sys.argv[1])))
connection.close()
"""
    if not re.fullmatch(r"odx_test_[0-9a-f]{32}", database):
        raise OdxError("Refusing to clean a database outside the generated test namespace")
    run_command([PYTHON, "-c", script, database])


def _test_process(args, logfile):
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        with logfile.open("x") as output:
            for line in process.stdout:
                print(line, end="", flush=True)
                output.write(line)
        return process.wait()
    except BaseException:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise
    finally:
        process.stdout.close()


def test_summary(logfile):
    matches = re.findall(
        r"(\d+) failed, (\d+) error\(s\) of (\d+) tests",
        logfile.read_text(),
    )
    if not matches:
        return {"tests": 0, "failures": 0, "errors": 0}
    failures, errors, count = map(int, matches[-1])
    return {"tests": count, "failures": failures, "errors": errors}


def run_tests(modules, *, tags=None, keep=False):
    module_names(modules)
    run_id = uuid.uuid4().hex
    database = f"odx_test_{run_id}"
    directory = STATE / "tests" / run_id
    directory.mkdir(parents=True, mode=0o700)
    result = {"id": run_id, "database": database, "status": "starting", "kept": keep}
    print(f"Test run: {run_id}\nDatabase: {database}\nArtifacts: {directory}", flush=True)
    code = 1
    with locked(STATE / "environment.lock", shared=True), test_port() as port:
        require_ready()
        args = [
            PYTHON,
            ODOO,
            "--config",
            CONFIG,
            "--database",
            database,
            "--db-filter",
            f"^{database}$",
            "--init",
            modules,
            "--test-enable",
            "--stop-after-init",
            "--workers=0",
            "--max-cron-threads=0",
            "--http-port",
            str(port),
            "--http-interface",
            "127.0.0.1",
            "--data-dir",
            str(directory / "data"),
        ]
        if tags:
            args.extend(["--test-tags", tags])
        write_private(directory / "result.json", json.dumps(result, indent=2))
        try:
            code = _test_process(args, directory / "odoo.log")
            summary = test_summary(directory / "odoo.log")
            result.update(summary)
            if code == 0 and (not summary["tests"] or summary["failures"] or summary["errors"]):
                code = 1
                result["reason"] = "Odoo did not report a successful non-empty test execution"
            result["status"] = "passed" if code == 0 else "failed"
            result["exit_code"] = code
        except BaseException as error:
            interrupted = isinstance(error, KeyboardInterrupt)
            code = 130 if interrupted else 1
            result["status"] = "interrupted" if interrupted else "failed"
            result["exit_code"] = code
            raise
        finally:
            if not keep:
                try:
                    _database_cleanup(database)
                    if (directory / "data").exists():
                        shutil.rmtree(directory / "data")
                    result["cleaned"] = True
                except (OdxError, OSError) as error:
                    result["cleanup_error"] = str(error)
                    code = code or 1
                    result["exit_code"] = code
                    result["status"] = "cleanup_failed"
            write_private(directory / "result.json", json.dumps(result, indent=2))
            print(json.dumps(result), flush=True)
    return code


def serve_opencode():
    require_ready()
    # No background service is started; this process belongs exclusively to this container.
    os.execvp("opencode", ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"])
