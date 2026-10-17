"""Dispose of a finalized restore's own import checkpoint, with resumable journaling."""

import json
import re
import shutil
from contextlib import closing
from pathlib import Path

from odoocker import container
from odoocker.database import Backend, safe_directory
from odoocker.errors import OdxError
from odoocker.locking import locked
from odoocker.runtime import write_private


def cleanup_checkpoints(directory, backend):
    record = json.loads((directory / "result.json").read_text())
    restore_id = record.get("id", "")
    if (
        not re.fullmatch(r"[a-f0-9]{32}", restore_id)
        or directory.name != restore_id
        or record.get("schema") != 1
        or record.get("project") != backend.context["project"]
        or record.get("status") != "completed"
        or not record.get("published")
        or not record.get("host_finalized")
    ):
        raise OdxError("Checkpoint cleanup requires a matching, successfully finalized restore")
    if record["options"].get("keep_checkpoints", True):
        return {"status": "retained"}
    journal = directory / "checkpoint-cleanup.json"
    if journal.is_symlink():
        raise OdxError("Unsafe checkpoint cleanup journal")
    progress = json.loads(journal.read_text()) if journal.exists() else {}
    if progress.get("status") == "completed":
        return progress

    def save(**changes):
        progress.update(changes)
        write_private(journal, json.dumps(progress, indent=2))

    if not record.get("checkpoint_ready"):
        save(status="completed", reason="no-update-checkpoint")
        return progress
    name = "odx_restore_" + restore_id
    expected = record.get("checkpoint_identity", {})
    published = record.get("publish_identity", {})
    if (
        record.get("temporary") != name
        or record.get("working_database") != "odx_update_" + restore_id
        or any(
            type(expected.get(key)) is not int or expected[key] <= 0
            for key in ("database", "filestore")
        )
        or any(expected[key] == published.get(key) for key in ("database", "filestore"))
        or not all(published.get(key) for key in ("database", "filestore"))
    ):
        raise OdxError("Checkpoint identity is missing or overlaps the published pair")
    if progress and (progress.get("database") != name or progress.get("identity") != expected):
        raise OdxError("Checkpoint cleanup journal belongs to another pair")
    path = backend.filestore(name)
    quarantine = path.with_name(".odx-checkpoint-" + restore_id)

    def files():
        safe_directory(path, backend.data)
        safe_directory(quarantine, backend.data)
        origin = path.stat().st_ino if path.exists() else None
        moved = quarantine.stat().st_ino if quarantine.exists() else None
        if (
            (origin is not None and origin != expected["filestore"])
            or (moved is not None and moved != expected["filestore"])
            or (origin is not None and moved is not None)
            or (moved is not None and not progress.get("quarantine_started"))
            or (origin is None and moved is None and not progress.get("removal_started"))
        ):
            raise OdxError("Checkpoint filestore changed; no unrelated files removed")
        return origin, moved

    with closing(backend.connect()) as connection:

        def database():
            return next((row for row in backend.inventory(connection) if row["name"] == name), None)

        row = database()
        files()  # Reject unsafe paths and identity changes before any DROP DATABASE.
        if row is not None:
            if row["oid"] != expected["database"] or row["protected"] or row["connections"]:
                raise OdxError(
                    "Checkpoint database changed, protected or connected; cleanup refused"
                )
            if progress.get("database_removed"):
                raise OdxError("Checkpoint database reappeared; cleanup refused")
            save(status="pending", database=name, identity=expected, drop_started=True)
            backend.drop_database(connection, name)  # Never FORCE or terminate clients.
        elif not progress.get("drop_started"):
            raise OdxError("Checkpoint database is missing; orphan filestore retained")
        if database() is not None:
            raise OdxError("Checkpoint database still exists or reappeared; filestore retained")
        save(database_removed=True)
        origin, moved = files()
        if origin is not None:
            save(quarantine_started=True)
            path.rename(quarantine)
            moved = expected["filestore"]
        if moved is not None:
            save(removal_started=True)
            files()
            shutil.rmtree(quarantine)
        save(status="completed", filestore_removed=True)
    return progress


def execute(directory):
    with (
        locked(container.STATE / "environment.lock"),
        locked(container.STATE / "dev-database.lock"),
    ):
        return cleanup_checkpoints(directory, Backend())


if __name__ == "__main__":
    import sys

    print(json.dumps(execute(Path(sys.argv[1]))))
