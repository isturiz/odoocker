"""Instance-scoped database maintenance inside Linux; no Docker access."""

import configparser
import getpass
import json
import re
import shutil
import stat
import sys
import uuid
import warnings
from contextlib import closing
from pathlib import Path

from . import container
from .errors import OdxError
from .locking import locked
from .runtime import write_private

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,62}\Z")
GENERATED = re.compile(r"odx_(restore|update|previous|test)_([a-f0-9]{32})\Z")


def database_name(name):
    if not NAME.fullmatch(name):
        raise OdxError("Use an exact database name: letters, digits, underscores, dots or hyphens")
    return name


def kind(name):
    match = GENERATED.fullmatch(name)
    return (
        {
            "restore": "import-checkpoint",
            "update": "update-checkpoint",
            "previous": "previous",
            "test": "test",
        }[match[1]]
        if match
        else ("development" if name == "dev" else "other")
    )


def safe_directory(path, anchor):
    if not path.is_relative_to(anchor) or ".." in path.relative_to(anchor).parts:
        raise OdxError("Filestore escaped managed data directories")
    relative = path.relative_to(anchor)
    for candidate in [anchor, *(anchor / parent for parent in reversed(relative.parents)), path]:
        try:
            mode = candidate.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            raise OdxError("Unsafe filestore directory; no database deleted")


class Backend:
    def __init__(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read(container.CONFIG)
        self.options = dict(config["options"])
        self.context = container.context()
        self.data = Path(self.options["data_dir"])
        if any(
            self.options.get(key) != expected
            for key, expected in {
                "db_host": "db",
                "db_port": "5432",
                "db_user": "odoo",
                "db_name": "dev",
                "data_dir": "/home/odoo/.local/share/Odoo",
            }.items()
        ):
            raise OdxError("Database maintenance requires the generated instance configuration")

    def connect(self):
        import psycopg2

        connection = psycopg2.connect(
            host=self.options["db_host"],
            port=self.options["db_port"],
            user=self.options["db_user"],
            password=self.options["db_password"],
            dbname="postgres",
            connect_timeout=10,
        )
        connection.autocommit = True
        return connection

    def inventory(self, connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT d.datname,d.oid,pg_database_size(d.oid),r.rolname,"
                "d.datistemplate,d.datallowconn,r.rolname=current_user,"
                "(SELECT count(*) FROM pg_stat_activity a WHERE a.datname=d.datname "
                "AND a.pid<>pg_backend_pid()) "
                "FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba ORDER BY d.datname"
            )
            result = []
            for name, oid, size, owner, template, connectable, owned, clients in cursor.fetchall():
                protected = (
                    name
                    in {
                        "postgres",
                        "template0",
                        "template1",
                        self.options.get("db_template", "template0"),
                    }
                    or template
                )
                result.append(
                    {
                        "name": name,
                        "oid": oid,
                        "bytes": size,
                        "owner": owner,
                        "kind": kind(name),
                        "protected": bool(protected or not owned or not connectable),
                        "connections": clients,
                    }
                )
            return result

    def filestore(self, name):
        match = GENERATED.fullmatch(name)
        anchor = self.data
        path = anchor / "filestore" / name
        if match and match[1] == "test":
            anchor = container.STATE
            directory = anchor / "tests" / match[2]
            safe_directory(directory, anchor)
            record = json.loads((directory / "result.json").read_text())
            if record.get("database") != name:
                raise OdxError("Test record does not match the selected database")
            path = directory / "data/filestore" / name
        elif match:
            directory = (
                Path("/workspace/.odoocker/instances")
                / self.context["instance"]
                / "restores"
                / match[2]
            )
            record = json.loads((directory / "result.json").read_text())
            if (
                record.get("id") != match[2]
                or record.get("project") != self.context["project"]
                or record.get("status") != "completed"
                or not record.get("host_finalized")
            ):
                raise OdxError(
                    "Recovery copy belongs to an unfinished restore; inspect --resume first"
                )
        safe_directory(path, anchor)
        return path

    def drop_database(self, connection, name):
        from psycopg2 import sql

        with connection.cursor() as cursor:
            # No FORCE and no pg_terminate_backend: PostgreSQL refuses new active clients too.
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def selected(backend, connection, name):
    row = next((row for row in backend.inventory(connection) if row["name"] == name), None)
    if row is None:
        raise OdxError("Database does not exist; orphan filestores are not deleted")
    if row["protected"]:
        raise OdxError(
            "Database is protected, a template, not connectable or owned by another role"
        )
    if row["connections"]:
        raise OdxError("Database has active connections; stop clients first (none were terminated)")
    return row


def drop(backend, name, *, dry_run=False, yes=False):
    database_name(name)
    with closing(backend.connect()) as connection:
        before = selected(backend, connection, name)
        path = backend.filestore(name)
        inode = path.stat().st_ino if path.exists() else None
        print(f"Delete {name} ({before['kind']}, {before['bytes'] / 2**30:.2f} GiB) and {path}")
        if before["kind"] in ("previous", "import-checkpoint", "update-checkpoint"):
            print("WARNING: this explicitly discards a restore recovery copy.")
        if dry_run:
            return 0
        if not yes:
            if not sys.stdin.isatty():
                raise OdxError("Deletion needs an interactive confirmation or explicit --yes")
            try:
                confirmation = input(f"Type 'delete {name}' to confirm: ")
            except EOFError:
                confirmation = ""
            if confirmation != f"delete {name}":
                raise OdxError("Deletion cancelled; no resources removed")
        current = selected(backend, connection, name)
        current_path = backend.filestore(name)
        current_inode = current_path.stat().st_ino if current_path.exists() else None
        if current["oid"] != before["oid"] or current_path != path or current_inode != inode:
            raise OdxError("Database or filestore identity changed; no resources removed")
        operation = container.STATE / "db-drops" / uuid.uuid4().hex
        quarantine = path.with_name(".odx-drop-" + operation.name)
        record = {
            "database": name,
            "oid": before["oid"],
            "filestore": str(path),
            "filestore_inode": inode,
            "quarantine": str(quarantine),
            "status": "prepared",
        }

        def save():
            write_private(operation / "result.json", json.dumps(record, indent=2))
            operation.chmod(0o700)

        save()
        try:
            backend.drop_database(connection, name)
        except Exception as error:
            record.update(status="database_drop_unconfirmed", error=type(error).__name__)
            save()
            raise OdxError(
                f"Database drop was not confirmed; filestore retained. Inspect {operation} "
                "before retrying. No connections were terminated"
            ) from None
        try:
            record.update(database_dropped=True, status="filestore")
            save()
            if inode is not None:
                if any(row["name"] == name for row in backend.inventory(connection)):
                    raise OdxError("Database name reappeared after deletion")
                backend.filestore(name)
                if path.stat().st_ino != inode or quarantine.exists() or quarantine.is_symlink():
                    raise OdxError("Filestore changed after database deletion")
                path.rename(quarantine)
                shutil.rmtree(quarantine)
            record["status"] = "completed"
            save()
        except (OSError, OdxError, ValueError) as error:
            record.update(status="filestore_pending", error=type(error).__name__)
            try:
                save()
            except OSError:
                pass  # Still report the partial deletion even if the volume is full.
            raise OdxError(
                f"Database deleted, but filestore cleanup is incomplete. Inspect {operation}; "
                "do not repeat the database drop or remove unrelated paths"
            ) from None
        print(f"Deleted {name} and its filestore. Private record: {operation}")
    return 0


def read_password(*, stdin=False):
    if stdin:
        password = sys.stdin.read(4098).removesuffix("\n").removesuffix("\r")
    else:
        if not sys.stdin.isatty():
            raise OdxError("Password changes need a terminal or --password-stdin")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("New password: ")
                confirmation = getpass.getpass("Confirm password: ")
        except (EOFError, getpass.GetPassWarning):
            raise OdxError("Secure password input unavailable; use --password-stdin") from None
        if password != confirmation:
            raise OdxError("Passwords do not match; nothing changed")
    if not password or len(password) > 4096 or any(char in password for char in "\r\n\0"):
        raise OdxError("Password must be one nonempty line, at most 4096 characters")
    return password


def set_password(env, password, login=None):
    users = env["res.users"].sudo().with_context(active_test=False)
    user = (
        users.search([("login", "=", login)], limit=2)
        if login is not None
        else env.ref("base.user_admin", raise_if_not_found=False)
    )
    if not user or len(user) != 1 or user._name != "res.users":
        raise OdxError(
            "Exactly one res.users account is required; administrator XML ID may be missing"
        )
    identity = user.login, user.active
    crypt = getattr(users, "_crypt_context", None)
    setter = getattr(users, "_set_encrypted_password", None)
    if not callable(crypt) or not callable(setter):
        raise OdxError("Odoo has no recognized native password hashing capability")
    setter(user.id, crypt().hash(password))
    env.cr.execute("SELECT password,login,active FROM res_users WHERE id=%s", [user.id])
    stored, actual_login, active = env.cr.fetchone()
    if (actual_login, active) != identity or not crypt().verify(password, stored):
        raise OdxError("Password verification failed; transaction rolled back")
    return actual_login


def passwd(password, login=None):
    from .odoo_compat import configure, loadable_module_names

    container.require_ready()
    sys.path.insert(0, "/opt/odoo")
    import odoo
    from odoo import sql_db
    from odoo.tools import config

    configure(
        config,
        [
            "-c",
            container.CONFIG,
            "--no-http",
            "--workers=0",
            "--max-cron-threads=0",
            "--log-level=warn",
            "--log-handler=odoo.sql_db:WARNING",
        ],
        init_logger=getattr(getattr(odoo, "netsvc", None), "init_logger", None),
    )
    config["db_name"] = []
    database = "dev"
    try:
        with sql_db.db_connect(database).cursor() as cursor:
            cursor.execute(
                "SELECT name,state,latest_version FROM ir_module_module "
                "WHERE state IN ('installed','to install','to upgrade','to remove')"
            )
            modules = cursor.fetchall()
            if any(state != "installed" for _, state, _ in modules):
                raise OdxError("Pending module operations; password change refused")
            version = next((version for name, _, version in modules if name == "base"), "")
            if not version.startswith(container.context()["version"] + "."):
                raise OdxError("Database Odoo version differs from the instance")
            cursor.execute(
                "SELECT 1 FROM ir_config_parameter WHERE key='base.partially_updated_database'"
            )
            if cursor.fetchone():
                raise OdxError("Partial module update; password change refused")
        registry = odoo.modules.registry.Registry.new(database)
        with registry.cursor() as cursor:
            env = odoo.api.Environment(cursor, odoo.SUPERUSER_ID, {})
            if set(loadable_module_names(env["ir.module.module"])) - registry._init_modules:
                raise OdxError("Installed Python modules were not loaded; password change refused")
            changed_login = set_password(env, password, login)
        print(
            f"Password changed in dev for {changed_login!r}; login, activation and MFA unchanged."
        )
    finally:
        odoo.modules.registry.Registry.delete(database)
        sql_db.close_db(database)
    return 0


def execute(args):
    exclusive = args.command == "db" and args.database_command == "drop"
    with locked(container.STATE / "environment.lock", shared=not exclusive):
        if args.command == "passwd":
            if args.login == "":
                raise OdxError("--login must be a nonempty exact login")
            with locked(container.STATE / "dev-database.lock"):
                Backend()  # Validate generated endpoints before loading any registry.
                return passwd(read_password(stdin=args.password_stdin), args.login)
        backend = Backend()
        if args.database_command == "drop":
            with locked(container.STATE / "dev-database.lock"):
                return drop(backend, args.database, dry_run=args.dry_run, yes=args.yes)
        with closing(backend.connect()) as connection:
            rows = backend.inventory(connection)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print("NAME\tGiB\tTYPE\tCONNECTIONS\tPROTECTED")
            for row in rows:
                name = row["name"] if row["name"].isprintable() else repr(row["name"])
                print(
                    f"{name}\t{row['bytes'] / 2**30:.2f}\t{row['kind']}\t"
                    f"{row['connections']}\t{row['protected']}"
                )
    return 0


def main():
    from .cli import parser

    args = parser().parse_args()
    if args.command not in ("db", "passwd") or (
        args.command == "db" and args.database_command not in ("list", "drop")
    ):
        raise SystemExit("Only db list/drop and passwd are supported by this worker")
    try:
        return execute(args)
    except OdxError as error:
        print(f"odx: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        # Driver/ORM failures must never echo a password, DSN, SQL parameters or hash.
        print(
            f"odx: database maintenance failed ({type(error).__name__}); "
            "inspect private state/logs",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
