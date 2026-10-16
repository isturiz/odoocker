"""Restore ordering, recovery identities and explicit options using a fake Odoo backend."""

import json

import pytest

from odoocker.errors import OdxError
from odoocker.restore import worker
from odoocker.restore.odoo import choose_updates


def compare(a, b):
    return (tuple(map(int, a.split("."))) > tuple(map(int, b.split(".")))) - (
        tuple(map(int, a.split("."))) < tuple(map(int, b.split(".")))
    )


def test_update_selection_is_explicit_and_includes_same_version_schema_owners():
    installed = {"old": "19.0.1.0", "same": "19.0.1.0", "ahead": "19.0.3.0"}
    manifests = {
        "old": {"version": "19.0.2.0"},
        "same": {"version": "19.0.1.0"},
        "ahead": {"version": "19.0.2.0"},
    }
    gaps = [{"modules": ["same"]}]
    assert choose_updates(installed, manifests, gaps, compare=compare) == ([], ["ahead"])
    assert choose_updates(installed, manifests, gaps, outdated=True, compare=compare) == (
        ["old", "same"],
        ["ahead"],
    )
    with pytest.raises(OdxError, match="downgrade"):
        choose_updates(installed, manifests, gaps, explicit=["ahead"], compare=compare)


@pytest.mark.parametrize("explicit", [["all"], ["uninstalled"]])
def test_update_selection_never_installs_arbitrary_modules(explicit):
    with pytest.raises(OdxError):
        choose_updates(
            {"a": "19.0.1"}, {"a": {"version": "19.0.1"}}, [], explicit=explicit, compare=compare
        )


class FakeBackend:
    def __init__(self, directory, record):
        self.record = record
        self.root = directory / "filestore"
        self.root.mkdir()
        self.scratch = directory / "scratch"
        self.scratch.mkdir()
        self.databases = {}
        self.events = []
        self.receipts = set()
        self.counter = 100
        self.roles = set()
        self.fail = None
        self.gaps = []

    def identity(self, name):
        path = self.root / name
        return {
            "database": self.databases.get(name),
            "filestore": path.stat().st_ino if path.exists() else None,
        }

    def idle(self, names):
        self.events.append(("idle", tuple(names)))

    def create(self, name):
        self.counter += 1
        self.databases[name] = self.counter
        self.events.append(("create", name))

    def import_sql(self, name, dump):
        self.events.append(("import", name))
        if self.fail == "import":
            raise OdxError("injected import failure")
        self.receipts.add(name)

    def receipt(self, name):
        return name in self.receipts

    def role_inventory(self, roles, create):
        self.events.append(("roles", roles, create))
        if set(roles) - self.roles and not create:
            raise OdxError("missing roles")
        self.roles.update(roles)

    def installed(self, name):
        self.events.append(("installed", name))
        return {"base": "19.0.1"}

    def admin_snapshot(self, name):
        return {"id": 7, "login": "unchanged-login", "hash": "fixture-hash"}

    def neutralize(self, name):
        self.events.append(("neutralize", name))

    def diagnose(self, name):
        self.events.append(("diagnose", name))
        return list(self.gaps)

    def select_updates(self, name, gaps, options):
        return ["addon"] if gaps and (options["update_outdated"] or options["update"]) else []

    def upgrade(self, name, modules):
        self.events.append(("upgrade", name, modules))
        if self.fail == "upgrade":
            raise OdxError("injected upgrade failure")
        self.gaps = []

    def prepare(self, name, reset):
        self.events.append(("prepare", name, reset))
        self.record["admin"] = {"login": "unchanged-login"}

    def verify(self, name, **kwargs):
        self.events.append(("verify", name, kwargs))
        if self.fail == "verify":
            raise OdxError("injected verification failure")
        return 2

    def ddl(self, query, source, target):
        self.events.append(("ddl", query, source, target))
        if query.startswith("CREATE"):
            self.create(source)
            self.receipts.add(source)
        else:
            self.databases[target] = self.databases.pop(source)
            if source in self.receipts:
                self.receipts.remove(source)
                self.receipts.add(target)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    record = {
        "id": "a" * 32,
        "odoo": "19.0",
        "postgres": "18",
        "temporary": "odx_restore_" + "a" * 32,
        "working_database": "odx_restore_" + "a" * 32,
        "previous": "odx_previous_" + "a" * 32,
        "options": {
            "replace": False,
            "create_missing_roles": False,
            "update": [],
            "update_outdated": False,
            "reset_admin_password": False,
        },
    }
    backend = FakeBackend(tmp_path, record)
    extracted = backend.scratch / "extracted"
    extracted.mkdir()
    (extracted / "dump.sql").write_text("SELECT 1;")
    (extracted / "filestore").mkdir()
    (extracted / "filestore" / "fixture").write_text("retained content")
    record["archive_extracted"] = True
    return worker.Pipeline(tmp_path, record, backend)


def test_default_pipeline_neutralizes_before_hooks_and_does_not_reset_password(pipeline):
    pipeline.run()
    events = pipeline.backend.events
    kinds = [event[0] for event in events]
    assert (
        kinds.index("neutralize")
        < kinds.index("diagnose")
        < kinds.index("prepare")
        < kinds.index("verify")
    )
    assert ("prepare", pipeline.record["temporary"], False) in events
    assert pipeline.record["status"] == "completed"
    assert pipeline.record["published"]
    assert "dev" in pipeline.backend.databases
    assert not any(kind == "upgrade" for kind in kinds)
    assert json.loads((pipeline.directory / "access.json").read_text())["password"] is None


def test_default_role_policy_stops_before_import_and_can_resume(pipeline):
    (pipeline.backend.scratch / "extracted/dump.sql").write_text(
        "GRANT USAGE ON SCHEMA public TO reader;"
    )
    with pytest.raises(OdxError, match="missing roles"):
        pipeline.run()
    assert not pipeline.backend.databases
    pipeline.record["options"]["create_missing_roles"] = True
    pipeline.run()
    assert pipeline.backend.roles == {"reader"}
    assert sum(event[0] == "import" for event in pipeline.backend.events) == 1


def test_failed_import_retries_the_transaction_not_archive_extraction(pipeline):
    pipeline.backend.fail = "import"
    with pytest.raises(OdxError, match="import"):
        pipeline.run()
    assert "dev" not in pipeline.backend.databases
    pipeline.backend.fail = None
    pipeline.run()
    assert sum(event[0] == "create" for event in pipeline.backend.events) == 1


def test_import_receipt_recovers_crash_before_state_save(pipeline):
    name = pipeline.record["temporary"]
    pipeline.record["creating_database"] = True
    pipeline.backend.create(name)
    pipeline.backend.receipts.add(name)
    pipeline.run()
    assert not any(event[0] == "import" for event in pipeline.backend.events)


def test_existing_target_requires_replace_and_preserves_old_database_and_filestore(pipeline):
    backend = pipeline.backend
    backend.create("dev")
    (backend.root / "dev").mkdir()
    (backend.root / "dev" / "old").write_text("old filestore")
    before = backend.identity("dev")
    with pytest.raises(OdxError, match="replace"):
        pipeline.run()
    assert backend.identity("dev") == before
    pipeline.record["options"]["replace"] = True
    pipeline.run()
    assert backend.identity(pipeline.record["previous"]) == before
    assert (backend.root / pipeline.record["previous"] / "old").read_text() == "old filestore"


def test_schema_failure_retains_import_and_resume_updates_only_requested_modules(pipeline):
    backend = pipeline.backend
    backend.gaps = [{"modules": ["addon"]}]
    with pytest.raises(OdxError, match="Schema"):
        pipeline.run()
    assert pipeline.record["database_imported"]
    baseline = backend.identity(pipeline.record["temporary"])
    pipeline.record["options"]["update_outdated"] = True
    pipeline.record["options"]["reset_admin_password"] = True
    pipeline.run()
    assert sum(event[0] == "import" for event in backend.events) == 1
    assert backend.identity(pipeline.record["temporary"]) == baseline
    assert ("upgrade", "odx_update_" + "a" * 32, ["addon"]) in backend.events
    assert json.loads((pipeline.directory / "access.json").read_text()) == {
        "login": "unchanged-login",
        "password": "admin",
        "password_reset": True,
    }


@pytest.mark.parametrize("failure", ["upgrade", "verify"])
def test_failed_updates_or_verification_do_not_publish(pipeline, failure):
    pipeline.backend.gaps = [{"modules": ["addon"]}]
    pipeline.record["options"]["update_outdated"] = True
    pipeline.backend.fail = failure
    with pytest.raises(OdxError):
        pipeline.run()
    assert "dev" not in pipeline.backend.databases
    assert pipeline.backend.identity(pipeline.record["temporary"])["database"]
    assert (pipeline.backend.root / pipeline.record["temporary"] / "fixture").is_file()


@pytest.mark.parametrize("crash_after", [1, 2, 3, 4])
def test_publication_journal_recovers_each_partial_move(pipeline, monkeypatch, crash_after):
    backend = pipeline.backend
    backend.create("dev")
    (backend.root / "dev").mkdir()
    old = backend.identity("dev")
    pipeline.record["options"]["replace"] = True
    count = 0
    original_db = pipeline._move_database
    original_fs = pipeline._move_filestore

    def interrupt(method, *args):
        nonlocal count
        method(*args)
        count += 1
        if count == crash_after:
            raise KeyboardInterrupt()

    monkeypatch.setattr(pipeline, "_move_database", lambda *a: interrupt(original_db, *a))
    monkeypatch.setattr(pipeline, "_move_filestore", lambda *a: interrupt(original_fs, *a))
    with pytest.raises(KeyboardInterrupt):
        pipeline.run()
    assert pipeline.record["publication_started"]
    monkeypatch.setattr(pipeline, "_move_database", original_db)
    monkeypatch.setattr(pipeline, "_move_filestore", original_fs)
    pipeline.run()
    assert pipeline.record["status"] == "completed"
    assert backend.identity(pipeline.record["previous"]) == old


def test_changed_target_is_not_silently_replaced(pipeline):
    pipeline.backend.fail = "verify"
    with pytest.raises(OdxError):
        pipeline.run()
    pipeline.backend.create("dev")
    pipeline.backend.fail = None
    with pytest.raises(OdxError, match="identity changed"):
        pipeline.run()
