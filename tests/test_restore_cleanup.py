"""Successful SQL disposal is narrowly scoped and never removes recovery copies."""

import json

import pytest

from odoocker.restore.cleanup import cleanup_sql


@pytest.fixture
def operation(tmp_path):
    restore_id = "a" * 32
    directory = tmp_path / "operations" / restore_id
    directory.mkdir(parents=True)
    record = {
        "schema": 1,
        "id": restore_id,
        "status": "completed",
        "published": True,
        "host_finalized": True,
        "options": {"keep_sql": False},
        "archive_sha256": "fixture",
    }
    result = directory / "result.json"
    result.write_text(json.dumps(record))
    root = tmp_path / "data"
    extracted = root / ".odoocker-restores" / restore_id / "extracted"
    extracted.mkdir(parents=True)
    dump = extracted / "dump.sql"
    dump.write_text("SELECT 1;")
    (extracted / ".extraction.json").write_text(json.dumps({"id": restore_id, "sha256": "fixture"}))
    return directory, root, dump, record


def save(directory, record):
    (directory / "result.json").write_text(json.dumps(record))


def test_only_sql_is_removed_after_success_and_cleanup_is_idempotent(operation, tmp_path):
    directory, root, dump, record = operation
    preserved = [
        tmp_path / "original.zip",
        directory / "restore.log",
        dump.parent / "manifest.json",
        root / "filestore/dev/attachment",
        root / ("filestore/odx_restore_" + record["id"]) / "attachment",
        root / ".odoocker-restores/other/extracted/dump.sql",
    ]
    for file in preserved:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("preserved")
    assert cleanup_sql(directory, root) == {"status": "removed", "bytes": 9}
    assert not dump.exists()
    assert all(file.read_text() == "preserved" for file in preserved)
    assert cleanup_sql(directory, root) == {"status": "absent"}


@pytest.mark.parametrize(
    "change",
    [
        {"status": "failed"},
        {"status": "running"},
        {"host_finalized": False},
        {"published": False},
        {"schema": 2},
        {"id": "../other"},
        {"id": "b" * 32},
    ],
)
def test_failed_incomplete_or_invalid_operations_keep_sql(operation, change):
    directory, root, dump, record = operation
    record.update(change)
    save(directory, record)
    with pytest.raises(ValueError, match="successfully finalized"):
        cleanup_sql(directory, root)
    assert dump.read_text() == "SELECT 1;"


def test_keep_sql_and_legacy_records(operation):
    directory, root, dump, record = operation
    record["options"]["keep_sql"] = True
    save(directory, record)
    assert cleanup_sql(directory, root) == {"status": "retained"}
    assert dump.exists()
    del record["options"]["keep_sql"]
    save(directory, record)
    assert cleanup_sql(directory, root)["status"] == "removed"


@pytest.mark.parametrize("part", ["root", "scratch", "operation", "extracted", "dump", "receipt"])
def test_symlink_paths_are_never_followed(operation, part):
    directory, root, dump, record = operation
    paths = {
        "root": root,
        "scratch": root / ".odoocker-restores",
        "operation": dump.parent.parent,
        "extracted": dump.parent,
        "dump": dump,
        "receipt": dump.parent / ".extraction.json",
    }
    path = paths[part]
    retained = path.with_name(path.name + "-retained")
    path.rename(retained)
    path.symlink_to(retained, target_is_directory=retained.is_dir())
    with pytest.raises(ValueError, match="Unsafe|receipt"):
        cleanup_sql(directory, root)
    assert dump.read_text() == "SELECT 1;"


def test_mismatched_extraction_receipt_keeps_sql(operation):
    directory, root, dump, record = operation
    (dump.parent / ".extraction.json").write_text(
        json.dumps({"id": record["id"], "sha256": "other"})
    )
    with pytest.raises(ValueError, match="receipt"):
        cleanup_sql(directory, root)
    assert dump.exists()


def test_unlink_failure_preserves_sql(operation, monkeypatch):
    directory, root, dump, record = operation

    def denied(path):
        raise PermissionError("injected cleanup failure")

    monkeypatch.setattr(type(dump), "unlink", denied)
    with pytest.raises(PermissionError):
        cleanup_sql(directory, root)
    assert dump.exists()
