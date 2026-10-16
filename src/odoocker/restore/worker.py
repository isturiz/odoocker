"""Resumable restore pipeline. No Docker access, outbound network, or client rules."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import time
from contextlib import ExitStack
from pathlib import Path

from ..errors import OdxError
from ..runtime import write_private
from .archive import extract
from .roles import required_roles

ID = re.compile(r"[a-f0-9]{32}\Z")


class Pipeline:
    def __init__(self, directory, record, backend):
        self.directory, self.record, self.backend = directory, record, backend

    def save(self, phase=None):
        if phase:
            self.record.update(phase=phase, status="running")
            self.record.pop("error", None)
            self.record.pop("message", None)
        self.record["updated"] = time.time()
        write_private(self.directory / "result.json", json.dumps(self.record, indent=2))

    def checkpoint(self, modules):
        record, backend = self.record, self.backend
        if record.get("checkpoint_ready"):
            return
        source = record["temporary"]
        target = "odx_update_" + record["id"]
        backend.idle([source, target])
        if not record.get("checkpoint_started"):
            if any(backend.identity(target).values()):
                raise OdxError("Checkpoint name collision; no database or files replaced")
            record["checkpoint_started"] = True
            self.save("checkpointing")
        if not backend.identity(target)["database"]:
            backend.ddl("CREATE DATABASE {} WITH TEMPLATE {}", target, source)
        elif not backend.receipt(target):
            raise OdxError("Checkpoint database does not belong to this restore")
        destination = backend.root / target
        if not destination.exists():
            partial = backend.scratch / "checkpoint-filestore.partial"
            if partial.is_symlink():
                raise OdxError("Unsafe checkpoint extraction path")
            if partial.exists():
                # Only this operation's generated incomplete COPY is disposable.
                shutil.rmtree(partial)
            source_path = backend.root / source
            needed = sum(p.stat().st_size for p in source_path.rglob("*") if p.is_file())
            if shutil.disk_usage(backend.root).free < needed + 512 * 1024**2:
                raise OdxError("Insufficient space for the update filestore checkpoint")
            shutil.copytree(source_path, partial)
            partial.rename(destination)
        record.update(checkpoint_ready=True, working_database=target)
        self.save()

    def _move_database(self, source, target, expected):
        if expected is None:
            return
        origin = self.backend.identity(source)["database"]
        destination = self.backend.identity(target)["database"]
        if destination == expected and origin != expected:
            return  # A crash followed ALTER DATABASE but preceded the journal write.
        if origin != expected or destination is not None:
            raise OdxError("Database publication collision; all recovery copies retained")
        self.backend.idle([source, target])
        self.backend.ddl("ALTER DATABASE {} RENAME TO {}", source, target)

    def _move_filestore(self, source, target, expected):
        if expected is None:
            return
        origin = self.backend.identity(source)["filestore"]
        destination = self.backend.identity(target)["filestore"]
        if destination == expected and origin != expected:
            return
        if origin != expected or destination is not None:
            raise OdxError("Filestore publication collision; no directories merged or overwritten")
        (self.backend.root / source).rename(self.backend.root / target)

    def publish(self):
        record, backend = self.record, self.backend
        target, previous, source = "dev", record["previous"], record["working_database"]
        if not record.get("publication_started"):
            backend.idle([source, target, previous])
            if backend.identity(target) != record["target_before"]:
                raise OdxError("Destination changed during restoration; refusing replacement")
            if any(backend.identity(previous).values()):
                raise OdxError("Previous-copy name collision; no publication attempted")
            record["publish_identity"] = backend.identity(source)
            record["publication_started"] = True
            self.save("publishing")
        before = record["target_before"]
        identity = record["publish_identity"]
        # Intent is durable before any move. Each step reconciles actual native
        # identities on --resume, including a crash between a move and save().
        self._move_database(target, previous, before["database"])
        self._move_filestore(target, previous, before["filestore"])
        self._move_filestore(source, target, identity["filestore"])
        self._move_database(source, target, identity["database"])
        backend.verify(target, registry=False, attachments=False)
        record.update(status="completed", phase="completed", published=True)
        self.save()
        write_private(
            self.directory / "access.json",
            json.dumps(
                {
                    "login": record["admin"]["login"],
                    "password": "admin" if record["options"]["reset_admin_password"] else None,
                    "password_reset": record["options"]["reset_admin_password"],
                },
                indent=2,
            ),
        )

    def run(self):
        record, backend = self.record, self.backend
        if record.get("publication_started"):
            self.publish()
            return
        options = record["options"]
        name = record["temporary"]
        if "target_before" not in record:
            target = backend.identity("dev")
            if any(target.values()) and not options["replace"]:
                raise OdxError(
                    "dev or its filestore already exists; use --replace to retain and replace"
                )
            record["target_before"] = target
            self.save("preparing")
        elif backend.identity("dev") != record["target_before"]:
            raise OdxError("Destination identity changed since this operation began")
        extracted = backend.scratch / "extracted"
        if not record.get("archive_extracted") and extracted.exists():
            try:
                receipt = json.loads((extracted / ".extraction.json").read_text())
            except (OSError, ValueError) as error:
                raise OdxError(
                    "Extraction has no completion receipt; inspect recovery files"
                ) from error
            if receipt != {"id": record["id"], "sha256": record["archive_sha256"]}:
                raise OdxError("Extraction completion receipt differs from this operation")
            record["archive_extracted"] = True
            self.save()
        if not record.get("archive_extracted"):
            self.save("extracting")
            source = Path("/input/backup.zip")
            with source.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != record["archive_sha256"]:
                    raise OdxError("Backup changed; original input fingerprint no longer matches")
            partial = backend.scratch / "extracted.partial"
            if partial.is_symlink():
                raise OdxError("Unsafe extraction path")
            if partial.exists():
                shutil.rmtree(partial)  # Only the generated, incomplete extraction.
            extract(source, partial, version=record["odoo"], postgres=record["postgres"])
            write_private(
                partial / ".extraction.json",
                json.dumps(
                    {
                        "id": record["id"],
                        "sha256": record["archive_sha256"],
                    }
                ),
            )
            if extracted.exists():
                raise OdxError("Unexpected existing extraction; inspect operation artifacts")
            partial.rename(extracted)
            record["archive_extracted"] = True
            self.save()
        if not record.get("database_imported"):
            self.save("roles")
            with (extracted / "dump.sql").open() as stream:
                roles = required_roles(stream)
            backend.role_inventory(roles, options["create_missing_roles"])
            existing = backend.identity(name)
            if not record.get("creating_database"):
                if any(existing.values()):
                    raise OdxError("Temporary resource collision; refusing to replace it")
                record["creating_database"] = True
                self.save("importing")
            if not existing["database"]:
                backend.create(name)
            if not backend.receipt(name):
                # A failed --single-transaction import rolls back the receipt too.
                backend.import_sql(name, extracted / "dump.sql")
            if not backend.receipt(name):
                raise OdxError("SQL import completion receipt missing")
            record["database_imported"] = True
            self.save()
        if not record.get("filestore_ready"):
            self.save("filestore")
            destination = backend.root / name
            if "extracted_filestore_inode" not in record:
                record["extracted_filestore_inode"] = (extracted / "filestore").stat().st_ino
                self.save()
            if destination.exists():
                if (extracted / "filestore").exists() or destination.stat().st_ino != record[
                    "extracted_filestore_inode"
                ]:
                    raise OdxError("Temporary filestore collision")
            else:
                (extracted / "filestore").rename(destination)
            record.update(filestore_ready=True, working_database=name)
            self.save()
        name = record["working_database"]
        if "original_admin" not in record:
            record["original_admin"] = backend.admin_snapshot(name)
            self.save()
        backend.installed(name)  # Reject partial source updates before any registry.
        self.save("neutralizing")
        backend.neutralize(name)  # Also BEFORE diagnosis or any normal startup hooks.
        gaps = backend.diagnose(name)
        modules = backend.select_updates(name, gaps, options)
        if modules:
            self.checkpoint(modules)
            name = record["working_database"]
            backend.installed(name)
            record["selected_modules"] = modules
            self.save("updating")
            backend.upgrade(name, modules)
            record["updated_modules"] = sorted(
                set(record.get("updated_modules", [])) | set(modules)
            )
            self.save()
            backend.neutralize(name)
            gaps = backend.diagnose(name)
        if gaps:
            record.setdefault("diagnosis", {})["missing_columns"] = gaps
            self.save()
            raise OdxError(
                "Schema differs from local code; resume with --update-outdated or --update"
            )
        self.save("preparing_copy")
        backend.prepare(name, options["reset_admin_password"])
        self.save("verifying")
        record["verified_attachments"] = backend.verify(name)
        self.save()
        self.publish()


def main():
    os.umask(0o077)
    directory = Path("/operation")
    record = json.loads((directory / "result.json").read_text())
    if record.get("schema") != 1 or not ID.fullmatch(record.get("id", "")):
        raise OdxError("Invalid restore record")
    with (directory / "restore.log").open("a", buffering=1) as output:
        os.dup2(output.fileno(), 1)
        os.dup2(output.fileno(), 2)

        def interrupt(signum, frame):
            raise KeyboardInterrupt()

        signal.signal(signal.SIGTERM, interrupt)
        try:
            with ExitStack() as stack:
                for name in ("environment.lock", "dev-database.lock"):
                    stream = stack.enter_context((Path("/var/lib/odoocker") / name).open("a+"))
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                if len(Path("/proc/net/route").read_text().splitlines()) != 1:
                    raise OdxError("Restore worker unexpectedly has an IP route")
                from .odoo import Backend

                Pipeline(directory, record, Backend(directory, record)).run()
            return 0
        except BaseException as error:
            import traceback

            traceback.print_exc()
            record.update(status="failed", error=type(error).__name__)
            if isinstance(error, OdxError):
                record["message"] = str(error)
            write_private(directory / "result.json", json.dumps(record, indent=2))
            return 130 if isinstance(error, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
