"""Finalized checkpoint disposal, native identities and interrupted cleanup recovery."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoocker import container, database
from odoocker.errors import OdxError
from odoocker.locking import locked
from odoocker.restore import checkpoints


@pytest.fixture
def operation(tmp_path, monkeypatch):
    restore_id = "a" * 32
    directory = tmp_path / "restores" / restore_id
    directory.mkdir(parents=True)
    data = tmp_path / "data"
    name = "odx_restore_" + restore_id
    path = data / "filestore" / name
    path.mkdir(parents=True)
    (path / "attachment").write_text("checkpoint")
    preserved = [
        tmp_path / "source.zip",
        directory / "restore.log",
        data / "filestore/dev/attachment",
        data / ("filestore/odx_previous_" + restore_id) / "attachment",
        data / ("filestore/odx_restore_" + "b" * 32) / "attachment",
    ]
    for file in preserved:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("preserved")
    record = {
        "schema": 1,
        "id": restore_id,
        "project": "fixture-project",
        "status": "completed",
        "published": True,
        "host_finalized": True,
        "checkpoint_ready": True,
        "temporary": name,
        "working_database": "odx_update_" + restore_id,
        "checkpoint_identity": {"database": 42, "filestore": path.stat().st_ino},
        "publish_identity": {
            "database": 43,
            "filestore": (data / "filestore/dev").stat().st_ino,
        },
        "options": {"keep_checkpoints": False},
    }
    result = directory / "result.json"
    result.write_text(json.dumps(record))
    backend = SimpleNamespace(
        data=data,
        context={"project": "fixture-project"},
        rows=[
            {"name": name, "oid": 42, "protected": False, "connections": 0},
            {"name": "dev", "oid": 43, "protected": False, "connections": 0},
            {"name": "odx_previous_" + restore_id, "oid": 44},
        ],
        dropped=[],
    )
    backend.connect = lambda: SimpleNamespace(close=lambda: None)
    backend.inventory = lambda connection: [dict(row) for row in backend.rows]

    def filestore(name):
        target = data / "filestore" / name
        database.safe_directory(target, data)
        return target

    def drop(connection, name):
        backend.dropped.append(name)
        backend.rows = [row for row in backend.rows if row["name"] != name]

    backend.filestore, backend.drop_database = filestore, drop
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(container, "STATE", state)
    monkeypatch.setattr(checkpoints, "Backend", lambda: backend)
    return SimpleNamespace(
        directory=directory,
        path=path,
        backend=backend,
        record=record,
        result=result,
        preserved=preserved,
        state=state,
        journal=directory / "checkpoint-cleanup.json",
        quarantine=path.with_name(".odx-checkpoint-" + restore_id),
    )


def save(operation):
    operation.result.write_text(json.dumps(operation.record))


def test_success_removes_only_own_checkpoint_and_is_idempotent(operation):
    result = checkpoints.execute(operation.directory)
    assert result["status"] == "completed"
    assert result["database_removed"] and result["filestore_removed"]
    assert operation.backend.dropped == [operation.path.name]
    assert not operation.path.exists() and not operation.quarantine.exists()
    assert all(p.read_text() == "preserved" for p in operation.preserved)
    assert [row["oid"] for row in operation.backend.rows] == [43, 44]
    assert operation.journal.stat().st_mode & 0o077 == 0
    assert checkpoints.execute(operation.directory) == result
    assert operation.backend.dropped == [operation.path.name]


def test_injected_script_entrypoint_uses_native_leases_and_emits_json(
    operation,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(database, "Backend", lambda: operation.backend)
    monkeypatch.setattr(sys, "argv", ["-c", str(operation.directory)])
    source = Path(checkpoints.__file__).read_text()
    exec(compile(source, "checkpoint-cleanup", "exec"), {"__name__": "__main__"})
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    assert operation.backend.dropped == [operation.path.name]


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "failed"},
        {"status": "running"},
        {"host_finalized": False},
        {"published": False},
        {"schema": 2},
        {"id": "../other"},
        {"id": "b" * 32},
        {"project": "another-project"},
    ],
)
def test_unfinished_or_mismatched_operations_are_never_cleaned(operation, changes):
    operation.record.update(changes)
    save(operation)
    with pytest.raises(OdxError, match="successfully finalized"):
        checkpoints.execute(operation.directory)
    assert not operation.backend.dropped and operation.path.exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_explicit_retention_and_legacy_records_are_preserved(operation, legacy):
    operation.record["options"] = {} if legacy else {"keep_checkpoints": True}
    save(operation)
    assert checkpoints.execute(operation.directory) == {"status": "retained"}
    assert not operation.backend.dropped and operation.path.exists()


def test_restore_without_module_updates_has_no_checkpoint_to_dispose(operation):
    operation.record["checkpoint_ready"] = False
    save(operation)
    assert checkpoints.execute(operation.directory) == {
        "status": "completed",
        "reason": "no-update-checkpoint",
    }
    assert not operation.backend.dropped and operation.path.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"temporary": "dev"},
        {"working_database": "dev"},
        {"checkpoint_identity": {}},
        {"publish_identity": {}},
        {"checkpoint_identity": {"database": True, "filestore": 1}},
        {"publish_identity": {"database": 42, "filestore": 1}},
    ],
)
def test_invalid_or_overlapping_checkpoint_identities_are_refused(operation, change):
    operation.record.update(change)
    save(operation)
    with pytest.raises(OdxError, match="identity"):
        checkpoints.execute(operation.directory)
    assert not operation.backend.dropped and operation.path.exists()


def test_checkpoint_filestore_cannot_overlap_published_filestore(operation):
    operation.record["publish_identity"]["filestore"] = operation.path.stat().st_ino
    save(operation)
    with pytest.raises(OdxError, match="overlaps"):
        checkpoints.execute(operation.directory)
    assert not operation.backend.dropped


def test_symlink_cleanup_journal_is_never_followed(operation):
    operation.journal.symlink_to(operation.preserved[0])
    with pytest.raises(OdxError, match="Unsafe checkpoint cleanup journal"):
        checkpoints.execute(operation.directory)
    assert not operation.backend.dropped
    assert operation.preserved[0].read_text() == "preserved"


def test_cleanup_journal_for_another_pair_is_refused(operation):
    operation.journal.write_text(json.dumps({"database": "dev", "identity": {}}))
    with pytest.raises(OdxError, match="another pair"):
        checkpoints.execute(operation.directory)
    assert not operation.backend.dropped


@pytest.mark.parametrize("change", [{"oid": 99}, {"protected": True}, {"connections": 1}])
def test_changed_protected_or_connected_checkpoint_is_not_deleted(operation, change):
    operation.backend.rows[0].update(change)
    with pytest.raises(OdxError, match="cleanup refused"):
        checkpoints.execute(operation.directory)
    assert not operation.backend.dropped and operation.path.exists()


def test_replaced_filestore_is_not_deleted(operation):
    retained = operation.path.with_name("retained")
    operation.path.rename(retained)
    operation.path.mkdir()
    with pytest.raises(OdxError, match="filestore changed"):
        checkpoints.execute(operation.directory)
    assert not operation.backend.dropped
    assert (retained / "attachment").read_text() == "checkpoint"


@pytest.mark.parametrize("part", ["data", "filestore", "checkpoint", "quarantine"])
def test_unsafe_paths_are_refused_before_database_drop(operation, part):
    path = {
        "data": operation.backend.data,
        "filestore": operation.path.parent,
        "checkpoint": operation.path,
        "quarantine": operation.quarantine,
    }[part]
    retained = path.with_name(path.name + "-retained")
    if path.exists():
        path.rename(retained)
    else:
        retained.mkdir()
    path.symlink_to(retained, target_is_directory=True)
    with pytest.raises(OdxError, match="Unsafe filestore"):
        checkpoints.execute(operation.directory)
    assert not operation.backend.dropped


def test_symlinks_inside_checkpoint_do_not_delete_external_files(operation):
    (operation.path / "link").symlink_to(operation.preserved[0])
    checkpoints.execute(operation.directory)
    assert operation.preserved[0].read_text() == "preserved"


def test_missing_database_does_not_trigger_orphan_filestore_removal(operation):
    operation.backend.rows.pop(0)
    with pytest.raises(OdxError, match="orphan filestore retained"):
        checkpoints.execute(operation.directory)
    assert operation.path.exists()


def test_unconfirmed_database_drop_keeps_filestore_and_can_retry(operation, monkeypatch):
    original = operation.backend.drop_database

    def fail(connection, name):
        raise OSError("injected drop failure")

    with monkeypatch.context() as patch:
        patch.setattr(operation.backend, "drop_database", fail)
        with pytest.raises(OSError):
            checkpoints.execute(operation.directory)
    assert (operation.path / "attachment").exists()
    assert json.loads(operation.journal.read_text())["drop_started"]
    assert operation.backend.drop_database == original
    assert checkpoints.execute(operation.directory)["status"] == "completed"


def test_lost_database_drop_acknowledgment_is_reconciled(operation, monkeypatch):
    original = operation.backend.drop_database

    def fail_after_drop(connection, name):
        original(connection, name)
        raise OSError("lost acknowledgment")

    with monkeypatch.context() as patch:
        patch.setattr(operation.backend, "drop_database", fail_after_drop)
        with pytest.raises(OSError):
            checkpoints.execute(operation.directory)
    assert operation.path.exists()
    assert checkpoints.execute(operation.directory)["status"] == "completed"
    assert len(operation.backend.dropped) == 1


def test_database_recreated_after_drop_keeps_filestore(operation, monkeypatch):
    original = operation.backend.drop_database

    def recreate(connection, name):
        original(connection, name)
        operation.backend.rows.append({"name": name, "oid": 100})

    monkeypatch.setattr(operation.backend, "drop_database", recreate)
    with pytest.raises(OdxError, match="reappeared"):
        checkpoints.execute(operation.directory)
    assert (operation.path / "attachment").exists()


def test_partial_filestore_deletion_is_resumable_without_repeating_database_drop(
    operation,
    monkeypatch,
):
    def fail(path):
        (path / "attachment").unlink()
        raise PermissionError("partial deletion")

    with monkeypatch.context() as patch:
        patch.setattr(checkpoints.shutil, "rmtree", fail)
        with pytest.raises(PermissionError):
            checkpoints.execute(operation.directory)
    progress = json.loads(operation.journal.read_text())
    assert progress["database_removed"] and progress["removal_started"]
    assert operation.quarantine.is_dir() and not operation.path.exists()
    # Host summaries are not the cleanup worker's durable journal.
    operation.record["checkpoint_cleanup"] = {"status": "pending", "error": "host disconnected"}
    save(operation)
    assert checkpoints.execute(operation.directory)["status"] == "completed"
    assert len(operation.backend.dropped) == 1
    assert not operation.quarantine.exists()


def test_replaced_quarantine_after_partial_cleanup_is_never_deleted(operation, monkeypatch):
    def fail(path):
        raise PermissionError("injected")

    with monkeypatch.context() as patch:
        patch.setattr(checkpoints.shutil, "rmtree", fail)
        with pytest.raises(PermissionError):
            checkpoints.execute(operation.directory)
    retained = operation.quarantine.with_name("retained")
    operation.quarantine.rename(retained)
    operation.quarantine.mkdir()
    (operation.quarantine / "unrelated").write_text("preserved")
    with pytest.raises(OdxError, match="filestore changed"):
        checkpoints.execute(operation.directory)
    assert (operation.quarantine / "unrelated").read_text() == "preserved"
    assert (retained / "attachment").read_text() == "checkpoint"


@pytest.mark.parametrize("after", ["rename", "rmtree"])
def test_cleanup_recovers_interruptions_between_native_changes_and_journal_writes(
    operation,
    monkeypatch,
    after,
):
    if after == "rename":
        original = Path.rename

        def interrupt(path, target):
            original(path, target)
            raise KeyboardInterrupt()

        patch_target, method = Path, "rename"
    else:
        original = checkpoints.shutil.rmtree

        def interrupt(path):
            original(path)
            raise KeyboardInterrupt()

        patch_target, method = checkpoints.shutil, "rmtree"
    with monkeypatch.context() as patch:
        patch.setattr(patch_target, method, interrupt)
        with pytest.raises(KeyboardInterrupt):
            checkpoints.execute(operation.directory)
    assert checkpoints.execute(operation.directory)["status"] == "completed"
    assert len(operation.backend.dropped) == 1
    assert not operation.path.exists() and not operation.quarantine.exists()


@pytest.mark.parametrize("name", ["environment.lock", "dev-database.lock"])
def test_busy_environment_or_database_blocks_cleanup_without_changes(operation, name):
    with locked(operation.state / name), pytest.raises(OdxError, match="Resource busy"):
        checkpoints.execute(operation.directory)
    assert not operation.backend.dropped and operation.path.exists()
