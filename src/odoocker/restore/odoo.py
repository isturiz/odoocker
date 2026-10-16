"""Odoo operations, executed only in the network-isolated Linux worker."""

import json
import os
import signal
import subprocess
import sys
from contextlib import closing, contextmanager
from pathlib import Path

from ..errors import OdxError
from ..odoo_compat import (
    check_python_minimum,
    configure,
    registration_boundary,
    without_demo_argument,
)
from .archive import verify_attachments


def child(args, logfile, *, env=None):
    """Do not leave psql/Odoo running if the worker is interrupted."""
    with logfile.open("a") as output:
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        try:
            return process.wait()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def missing_columns(registry, columns):
    result = []
    for name in registry:
        model = registry[name]
        if not model._auto or model._abstract:
            continue
        for name, field in model._fields.items():
            if (
                field.store
                and field.column_type
                and not field.inherited
                and name not in columns.get(model._table, set())
            ):
                result.append(
                    {
                        "model": model._name,
                        "field": name,
                        "table": model._table,
                        "modules": sorted(getattr(field, "_modules", ()) or ()),
                    }
                )
    return result


def choose_updates(installed, manifests, gaps, *, outdated=False, explicit=(), compare):
    selected = set(explicit)
    if "all" in selected or selected - installed.keys():
        raise OdxError("Only explicitly installed technical modules can be updated; not 'all'")
    unavailable = sorted(name for name in installed if not manifests.get(name))
    if unavailable:
        raise OdxError("Installed addons are unavailable locally; see private diagnosis")
    older = set()
    for name, version in installed.items():
        local = manifests[name]["version"]
        order = compare(local, version)
        if order < 0:
            older.add(name)
        elif order > 0 and outdated:
            selected.add(name)
    if outdated:
        for gap in gaps:
            owners = set(gap["modules"]) & installed.keys()
            if not owners:
                raise OdxError("Missing field has no installed owner; specify modules after review")
            selected.update(owners)
    if selected & older:
        raise OdxError("Local addon code is older than the database; refusing module downgrade")
    return sorted(selected), sorted(older)


class _MetadataReady(BaseException):
    pass


class Backend:
    def __init__(self, directory, record):
        check_python_minimum("/opt/odoo")
        sys.path.insert(0, "/opt/odoo")
        import odoo
        from odoo import sql_db
        from odoo.modules.module import get_manifest
        from odoo.service import db
        from odoo.tools import config

        configure(
            config,
            ["-c", "/operation/odoo.conf", "--no-http", "--max-cron-threads=0", "--workers=0"],
            init_logger=getattr(getattr(odoo, "netsvc", None), "init_logger", None),
        )
        config["db_name"] = []
        config["list_db"] = True
        self.odoo, self.sql_db, self.service, self.config = odoo, sql_db, db, config
        self.manifest = get_manifest
        self.directory, self.record = directory, record
        self.root = Path(config["data_dir"]) / "filestore"
        self.scratch = Path(config["data_dir"]) / ".odoocker-restores" / record["id"]
        self.scratch.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def cursor(self, name):
        try:
            with self.sql_db.db_connect(name).cursor() as cursor:
                yield cursor
        finally:
            if name != "postgres":
                self.sql_db.close_db(name)

    def close(self, name):
        self.odoo.modules.registry.Registry.delete(name)
        self.sql_db.close_db(name)

    def ddl(self, query, *names):
        from psycopg2 import sql

        with closing(self.sql_db.db_connect("postgres").cursor()) as cursor:
            cursor._cnx.autocommit = True
            cursor.execute(sql.SQL(query).format(*(sql.Identifier(name) for name in names)))

    def identity(self, name):
        path = self.root / name
        if self.root.is_symlink() or path.is_symlink() or (path.exists() and not path.is_dir()):
            raise OdxError("Unsafe filestore path")
        with self.cursor("postgres") as cursor:
            cursor.execute(
                "SELECT oid, datdba=(SELECT usesysid FROM pg_user WHERE usename=current_user), "
                "datistemplate FROM pg_database WHERE datname=%s",
                [name],
            )
            row = cursor.fetchone()
        if row and (not row[1] or row[2]):
            raise OdxError("Database is a template or belongs to another user")
        return {
            "database": row[0] if row else None,
            "filestore": path.stat().st_ino if path.exists() else None,
        }

    def idle(self, names):
        with self.cursor("postgres") as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=ANY(%s) "
                "AND backend_type='client backend'",
                [list(names)],
            )
            if cursor.fetchone()[0]:
                raise OdxError("Database has active clients; no connections were terminated")

    def receipt(self, name):
        with self.cursor(name) as cursor:
            cursor.execute("SELECT to_regclass('public.ir_config_parameter')")
            if not cursor.fetchone()[0]:
                return False
            cursor.execute("SELECT value FROM ir_config_parameter WHERE key='odoocker.restore.id'")
            row = cursor.fetchone()
            return bool(row and row[0] == self.record["id"])

    def role_inventory(self, required, create):
        from psycopg2 import sql

        with self.cursor("postgres") as cursor:
            cursor.execute("SELECT rolname FROM pg_roles")
            missing = sorted(set(required) - {row[0] for row in cursor.fetchall()})
            # Keep the planned roles even if a host interruption follows COMMIT.
            self.record["role_inventory"] = {"required": required, "missing": missing}
            self.save()
            if missing and not create:
                raise OdxError(
                    "Missing PostgreSQL roles; inspect result.json and resume with "
                    "--create-missing-roles to create restricted NOLOGIN roles"
                )
            attempt = None
            if missing:
                attempt = {"roles": missing, "committed": False}
                self.record.setdefault("role_attempts", []).append(attempt)
                self.save()
            for name in missing:
                if name.startswith("pg_"):
                    raise OdxError("Cannot create PostgreSQL-reserved roles")
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOINHERIT NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(name))
                )
        if attempt is not None:
            attempt["committed"] = True
        self.record["created_roles"] = sorted(
            set(self.record.get("created_roles", [])) | set(missing)
        )
        self.save()

    def save(self):
        from ..runtime import write_private

        write_private(self.directory / "result.json", json.dumps(self.record, indent=2))

    def create(self, name):
        self.service._create_empty_database(name)
        self.close(name)

    def admin_snapshot(self, name):
        with self.cursor(name) as cursor:
            cursor.execute(
                "SELECT u.id,u.login,u.password FROM res_users u JOIN ir_model_data d "
                "ON d.res_id=u.id WHERE d.module='base' AND d.name='user_admin' "
                "AND d.model='res.users'"
            )
            row = cursor.fetchone()
        if not row:
            raise OdxError("Administrator XML ID base.user_admin is missing")
        return {"id": row[0], "login": row[1], "hash": row[2]}

    def import_sql(self, name, dump):
        from odoo.tools.misc import exec_pg_environ, find_pg_tool

        receipt = (
            "INSERT INTO public.ir_config_parameter(key,value) VALUES "
            f"('odoocker.restore.id','{self.record['id']}') "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value"
        )
        code = child(
            [
                find_pg_tool("psql"),
                "-X",
                "-q",
                "--set=ON_ERROR_STOP=on",
                "--single-transaction",
                "--dbname=" + name,
                "-f",
                str(dump),
                "-c",
                receipt,
            ],
            self.directory / "psql.log",
            env=exec_pg_environ(),
        )
        if code:
            raise OdxError("SQL import failed; details retained in private psql.log")

    def installed(self, name):
        with self.cursor(name) as cursor:
            cursor.execute(
                "SELECT name,state,latest_version FROM ir_module_module "
                "WHERE state IN ('installed','to install','to upgrade','to remove')"
            )
            rows = cursor.fetchall()
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM ir_config_parameter "
                "WHERE key='base.partially_updated_database')"
            )
            if cursor.fetchone()[0] or any(row[1] != "installed" for row in rows):
                raise OdxError(
                    "Database has pending/partial module updates; manual review required"
                )
        installed = {row[0]: row[2] for row in rows}
        base = installed.get("base", "")
        if not base.startswith(self.record["odoo"] + "."):
            raise OdxError("Restored Odoo major differs from the workspace; no migration")
        return installed

    def neutralize(self, name):
        from odoo.modules.neutralize import neutralize_database

        with self.cursor(name) as cursor:
            neutralize_database(cursor)
            cursor.execute("UPDATE ir_cron SET active=false")
            cursor.execute("SELECT to_regclass('public.mail_mail')")
            if cursor.fetchone()[0]:
                cursor.execute("UPDATE mail_mail SET state='cancel' WHERE state='outgoing'")
            cursor.execute("SELECT to_regclass('public.fetchmail_server')")
            if cursor.fetchone()[0]:
                cursor.execute("UPDATE fetchmail_server SET active=false")
            cursor.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='ir_act_server' AND column_name='webhook_url'"
            )
            if cursor.fetchone():
                cursor.execute(
                    "UPDATE ir_act_server SET webhook_url='neutralization - disable webhook' "
                    "WHERE state='webhook'"
                )

    @staticmethod
    def columns(cursor):
        cursor.execute(
            "SELECT c.relname,a.attname FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid "
            "WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped"
        )
        result = {}
        for table, column in cursor.fetchall():
            result.setdefault(table, set()).add(column)
        return result

    def diagnose(self, name):
        """Read the complete model metadata, but abort BEFORE registration hooks."""
        from odoo.modules import loading

        hook, environment = registration_boundary(loading.load_modules)
        result = None

        def trace(frame, event, arg):
            nonlocal result
            if frame.f_code is not loading.load_modules.__code__:
                return None
            if event == "line" and frame.f_lineno == hook:
                env = frame.f_locals[environment]
                result = missing_columns(env.registry, self.columns(env.cr))
                raise _MetadataReady()
            return trace

        previous = sys.gettrace()
        sys.settrace(trace)
        try:
            self.odoo.modules.registry.Registry.new(name)
        except _MetadataReady:
            pass
        finally:
            sys.settrace(previous)
            self.close(name)
        if result is None:
            raise OdxError("Could not capture Odoo model metadata safely")
        return result

    def select_updates(self, name, gaps, options):
        from odoo.tools.parse_version import parse_version

        installed = self.installed(name)
        manifests = {module: self.manifest(module) for module in installed}
        self.record["diagnosis"] = {
            "missing_columns": gaps,
            "unavailable_modules": sorted(k for k, value in manifests.items() if not value),
        }
        self.save()
        selected, older = choose_updates(
            installed,
            manifests,
            gaps,
            outdated=options["update_outdated"],
            explicit=options["update"],
            compare=lambda a, b: (
                (parse_version(a) > parse_version(b)) - (parse_version(a) < parse_version(b))
            ),
        )
        self.record["diagnosis"].update(selected_modules=selected, local_code_older=older)
        self.save()
        return selected

    def upgrade(self, name, modules):
        code = child(
            [
                sys.executable,
                "/opt/odoo/odoo-bin",
                "-c",
                "/operation/odoo.conf",
                "-d",
                name,
                "-u",
                ",".join(modules),
                "--no-http",
                "--stop-after-init",
                "--workers=0",
                "--max-cron-threads=0",
                without_demo_argument(self.config),
            ],
            self.directory / "upgrade.log",
        )
        if code:
            raise OdxError("Module update failed; checkpoint and private upgrade.log retained")

    def prepare(self, name, reset_password):
        self.installed(name)
        registry = self.odoo.modules.registry.Registry.new(name)
        try:
            with registry.cursor() as cursor:
                env = self.odoo.api.Environment(cursor, self.odoo.SUPERUSER_ID, {})
                if missing_columns(registry, self.columns(cursor)):
                    raise OdxError("Schema still differs from local code")
                module = env["ir.module.module"]
                required = module.search([("state", "=", "installed")]).mapped("name")
                if set(required) - registry._init_modules:
                    raise OdxError("Some installed modules were not loaded")
                parameters = env["ir.config_parameter"]
                old = [parameters.get_param(key) for key in ("database.uuid", "database.secret")]
                parameters.init(force=True)
                if any(
                    parameters.get_param(key) == value
                    for key, value in zip(("database.uuid", "database.secret"), old, strict=True)
                ):
                    raise OdxError("Copy identifiers were not regenerated")
                admin = env.ref("base.user_admin", raise_if_not_found=False)
                if not admin or admin._name != "res.users":
                    raise OdxError("Administrator XML ID base.user_admin is missing")
                if reset_password:
                    if not admin.active:
                        raise OdxError(
                            "Administrator is inactive; refusing to activate it implicitly"
                        )
                    users = env["res.users"].sudo()
                    users._set_encrypted_password(admin.id, users._crypt_context().hash("admin"))
                cursor.execute("SELECT password,login FROM res_users WHERE id=%s", [admin.id])
                password_hash, login = cursor.fetchone()
                original = self.record["original_admin"]
                if admin.id != original["id"] or login != original["login"]:
                    raise OdxError("An update/startup hook changed the administrator identity")
                if not reset_password and password_hash != original["hash"]:
                    raise OdxError("A startup hook changed the restored administrator password")
                self.record["admin"] = {"id": admin.id, "hash": password_hash, "login": login}
                self.record["copy_parameters"] = {
                    key: parameters.get_param(key) for key in ("database.uuid", "database.secret")
                }
        finally:
            self.close(name)
        self.neutralize(name)
        self.save()

    def verify(self, name, *, registry=True, attachments=True):
        self.installed(name)
        if registry:
            self.odoo.modules.registry.Registry.new(name)
            self.close(name)
        with self.cursor(name) as cursor:
            cursor.execute("SELECT count(*) FROM ir_cron WHERE active")
            if cursor.fetchone()[0]:
                raise OdxError(
                    "A startup hook reactivated cron jobs; fix the addon before publishing"
                )
            cursor.execute(
                "SELECT count(*) FROM ir_mail_server WHERE active AND "
                "(smtp_host IS DISTINCT FROM 'invalid' OR smtp_port IS DISTINCT FROM 1025)"
            )
            if cursor.fetchone()[0]:
                raise OdxError("Real outgoing mail servers remain active")
            cursor.execute("SELECT to_regclass('public.fetchmail_server')")
            if cursor.fetchone()[0]:
                cursor.execute("SELECT count(*) FROM fetchmail_server WHERE active")
                if cursor.fetchone()[0]:
                    raise OdxError("Incoming mail servers remain active")
            cursor.execute(
                "SELECT value FROM ir_config_parameter WHERE key='database.is_neutralized'"
            )
            row = cursor.fetchone()
            if not row or row[0].lower() != "true":
                raise OdxError("Database neutralization flag is missing")
            cursor.execute(
                "SELECT password FROM res_users WHERE id=%s", [self.record["admin"]["id"]]
            )
            if cursor.fetchone() != (self.record["admin"]["hash"],):
                raise OdxError("Administrator password changed during startup")
            cursor.execute(
                "SELECT key,value FROM ir_config_parameter "
                "WHERE key IN ('database.uuid','database.secret')"
            )
            if dict(cursor.fetchall()) != self.record["copy_parameters"]:
                raise OdxError("Copy identifiers changed during startup")
            if attachments:
                cursor.execute(
                    "SELECT store_fname,min(checksum),max(checksum),min(file_size),max(file_size) "
                    "FROM ir_attachment WHERE store_fname IS NOT NULL AND store_fname<>'' "
                    "GROUP BY store_fname"
                )
                return verify_attachments(self.root / name, cursor.fetchall())
        return self.record.get("verified_attachments", 0)
