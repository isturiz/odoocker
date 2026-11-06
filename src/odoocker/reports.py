"""Native report URLs for an explicitly selected development database, without a registry.

The run controller holds environment/database leases throughout this worker. The
generated dev-only base configuration anchors the endpoints for every target.
"""

import argparse
import configparser
import json
import sys
from contextlib import closing

from .errors import OdxError
from .targets import development_database


def internal_url(options):
    try:
        port = int(options.get("http_port", "8069"))
    except (TypeError, ValueError) as error:
        raise OdxError("Invalid Odoo HTTP port for report assets") from error
    if not 1 <= port <= 65535:
        raise OdxError("Invalid Odoo HTTP port for report assets")
    return f"http://127.0.0.1:{port}"


def prepare(config_path, database="dev"):
    import psycopg2

    development_database(database)
    config = configparser.ConfigParser(interpolation=None)
    if not config.read(config_path) or "options" not in config:
        raise OdxError("Missing generated Odoo configuration for report assets")
    options = config["options"]
    if any(
        options.get(key) != value
        for key, value in {
            "db_name": "dev",
            "db_host": "db",
            "db_port": "5432",
            "db_user": "odoo",
        }.items()
    ):
        raise OdxError("Report preparation requires the generated dev database configuration")
    url = internal_url(options)
    credentials = {
        "host": options["db_host"],
        "port": options["db_port"],
        "user": options["db_user"],
        "password": options["db_password"],
        "connect_timeout": 10,
    }
    with closing(psycopg2.connect(**credentials, dbname="postgres")) as connection:
        with connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT datdba=(SELECT oid FROM pg_roles WHERE rolname=current_user) "
                "AND NOT datistemplate AND datallowconn FROM pg_database WHERE datname=%s",
                [database],
            )
            identity = cursor.fetchone()
            if identity is None:
                return False
            if not identity[0]:
                raise OdxError("Report preparation requires an owned, connectable database")
    with closing(psycopg2.connect(**credentials, dbname=database)) as connection:
        with connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('public.ir_config_parameter'), "
                "to_regclass('public.ir_module_module')"
            )
            if not all(cursor.fetchone()):
                return False
            cursor.execute("SELECT state FROM public.ir_module_module WHERE name=%s", ["base"])
            base = cursor.fetchone()
            if base is None or base[0] not in {"installed", "to upgrade"}:
                return False
            cursor.execute(
                "SELECT value FROM public.ir_config_parameter WHERE key=%s", ["report.url"]
            )
            if cursor.fetchone() != (url,):
                cursor.execute(
                    "INSERT INTO public.ir_config_parameter(key,value) VALUES (%s,%s) "
                    "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value "
                    "WHERE ir_config_parameter.value IS DISTINCT FROM EXCLUDED.value",
                    ["report.url", url],
                )
    return True


def main():
    import psycopg2

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--database", default="dev")
    args = parser.parse_args()
    try:
        initialized = prepare(args.config, args.database)
    except (OdxError, psycopg2.Error, OSError, KeyError, configparser.Error) as error:
        print(f"Cannot prepare development report assets: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"initialized": initialized}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
