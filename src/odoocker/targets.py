"""Explicit development targets; restore journals are the durable publication catalog."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlencode

from .errors import OdxError


def development_database(name):
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,62}", name)
        or name.lower() in {"postgres", "template0", "template1"}
        or name.lower().startswith("odx_")
    ):
        raise OdxError("Use an exact development database name; system and odx_ names are reserved")
    return name


def database_lock(state, name):
    development_database(name)
    # Preserve the existing lease for dev and legacy recovery workers.
    return state / (
        "dev-database.lock"
        if name == "dev"
        else "database-" + hashlib.sha256(name.encode()).hexdigest() + ".lock"
    )


def published_databases(directory, project):
    names = {"dev"}
    for file in sorted((directory / "restores").glob("*/result.json")):
        try:
            record = json.loads(file.read_text())
        except (OSError, ValueError) as error:
            raise OdxError(f"Cannot inspect restore catalog: {file}") from error
        if not isinstance(record, dict):
            raise OdxError(f"Invalid restore catalog entry: {file}")
        if (
            record.get("schema") == 1
            and record.get("id") == file.parent.name
            and record.get("project") == project
            and record.get("status") == "completed"
            and record.get("host_finalized")
            and record.get("published")
            and record.get("database") is not None
        ):
            names.add(development_database(record["database"]))
    return ["dev", *sorted(names - {"dev"})]


def container_databases():
    from .container import context

    settings = context()
    directory = Path("/workspace/.odoocker/instances") / settings["instance"]
    return published_databases(directory, settings["project"])


def browser_url(url, database):
    development_database(database)
    return url if database == "dev" else url.rstrip("/") + "/web?" + urlencode({"db": database})
