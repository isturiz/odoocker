"""CLI and maintenance choreography, with every external process replaced."""

import json
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from odoocker import cli
from odoocker.config import MANIFEST, Workspace
from odoocker.errors import OdxError
from odoocker.locking import locked
from odoocker.restore import host
from odoocker.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    (tmp_path / MANIFEST).write_text("""schema=1
[workspace]
name="restore-fixture"
[odoo]
version="19.0"
[opencode]
version="2.0.31"
""")
    workspace = Workspace.load(tmp_path)
    (workspace.source / "odoo").mkdir(parents=True)
    (workspace.source / "odoo-bin").touch()
    (workspace.source / "odoo/release.py").write_text("version_info=(19,0,0)")
    runtime = Runtime.load(workspace)
    runtime.generate()
    return runtime


@pytest.fixture
def backup(tmp_path):
    path = tmp_path / "backup.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("dump.sql", "SELECT 1;")
    return path


def args(backup=None, **kwargs):
    values = dict(
        backup=backup,
        resume=None,
        create_missing_roles=False,
        reset_admin_password=False,
        update_outdated=False,
        update=None,
        replace=False,
        keep_sql=False,
        keep_checkpoints=False,
    )
    values.update(kwargs)
    return SimpleNamespace(**values)


@pytest.fixture
def processes(runtime, monkeypatch):
    calls = []
    state = SimpleNamespace(
        busy=False,
        active=False,
        failed=False,
        running=["db", "odoo"],
        target=False,
        postgres="16",
        leased=False,
        fail_command=None,
    )

    @contextmanager
    def maintenance(command, **kwargs):
        assert kwargs["protocol"] == "ODX_SETUP"
        with pytest.raises(OdxError, match="Resource busy"), locked(runtime.operation_lock):
            pytest.fail("Host lock must be held")
        calls.append(("lease",))
        if state.busy:
            raise OdxError("Linux environment busy")
        state.leased = True
        try:
            yield SimpleNamespace()
        finally:
            state.leased = False
            calls.append(("released",))

    def active(instance):
        calls.append(("active",))
        if state.active:
            raise OdxError("OpenCode has active sessions")

    def run(command, **kwargs):
        assert command[:2] == ["docker", "compose"]
        assert command[command.index("--project-name") + 1] == runtime.project
        assert "--env-file" in command and "--file" in command
        file = command[command.index("--file") + 1]
        action = command[command.index("--file") + 2 :]
        calls.append(tuple(action))
        if action[0] == state.fail_command:
            raise OdxError("injected command failure")
        output = ""
        if action[0] == "ps":
            output = "\n".join(state.running)
        elif action[0] == "images":
            output = "a" * 64
        elif action[0] == "port":
            output = "127.0.0.1:53210"
        elif action[0] == "exec" and host.PROBE in action:
            output = json.dumps(
                {"database": state.target, "filestore": False, "postgres": state.postgres}
            )
        elif action[0] == "exec" and host.CLEANUP_SQL in action:
            output = json.dumps({"status": "removed", "bytes": 42})
        elif action[0] == "exec" and host.CLEANUP_CHECKPOINTS in action:
            from pathlib import Path

            directory = runtime.workspace.root / Path(action[-1]).relative_to("/workspace")
            record = json.loads((directory / "result.json").read_text())
            assert record["host_finalized"] and record["status"] == "completed"
            output = json.dumps({"status": "completed", "database_removed": True})
        elif action[0] == "up" and action[-1] == "restore-worker":
            assert state.leased
            document = json.loads(open(file).read())
            assert all(
                service["network_mode"] == "none" for service in document["services"].values()
            )
            assert "docker.sock" not in json.dumps(document)
            mount = next(
                x
                for x in document["services"]["restore-worker"]["volumes"]
                if x["target"] == "/operation"
            )
            from pathlib import Path

            directory = Path(mount["source"])
            assert (directory / "engine/odoocker/restore/worker.py").is_file()
            record = json.loads((directory / "result.json").read_text())
            record.update(
                status="failed" if state.failed else "completed",
                admin={"login": "local-admin"},
                verified_attachments=3,
                published=not state.failed,
            )
            (directory / "result.json").write_text(json.dumps(record))
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(host, "run_command", run)
    monkeypatch.setattr("odoocker.runtime.run_command", run)
    monkeypatch.setattr(host, "maintenance_process", maintenance)
    monkeypatch.setattr(host, "active_sessions", active)
    return calls, state


def test_cli_restore_flags_and_host_only_dispatch(monkeypatch):
    parsed = cli.parser().parse_args(
        [
            "db",
            "restore",
            "backup.zip",
            "--create-missing-roles",
            "--reset-admin-password",
            "--update-outdated",
            "--replace",
            "--keep-sql",
            "--keep-checkpoints",
        ]
    )
    assert parsed.database_command == "restore"
    assert parsed.create_missing_roles and parsed.reset_admin_password and parsed.update_outdated
    assert parsed.keep_sql
    assert parsed.keep_checkpoints
    defaults = cli.parser().parse_args(["db", "restore", "backup.zip"])
    assert not defaults.keep_sql and not defaults.keep_checkpoints
    monkeypatch.setenv("ODOOCKER_CONTAINER", "1")
    assert cli.main(["db", "restore", "backup.zip"]) == 1


def test_cli_update_modes_are_exclusive():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(
            ["db", "restore", "backup.zip", "--update", "sale", "--update-outdated"]
        )


def test_host_restore_is_scoped_private_and_keeps_database_offline_until_worker_exits(
    runtime, backup, processes
):
    calls, state = processes
    state.running.append("opencode")
    manifest = (runtime.workspace.root / MANIFEST).read_bytes()
    assert host.restore(runtime, args(backup, reset_admin_password=True)) == 0
    stop = next(i for i, c in enumerate(calls) if c[:3] == ("stop", "--timeout", "60"))
    launch = next(i for i, c in enumerate(calls) if c[0] == "up" and c[-1] == "restore-worker")
    wait = next(i for i, c in enumerate(calls) if c[0] == "wait")
    restart = next(i for i, c in enumerate(calls) if c[0] == "start")
    assert stop < launch < wait < restart
    assert (runtime.workspace.root / MANIFEST).read_bytes() == manifest
    result = next((runtime.directory / "restores").glob("*/result.json"))
    record = json.loads(result.read_text())
    assert record["host_finalized"]
    assert result.stat().st_mode & 0o077 == 0
    assert not any("down" in c or "--volumes" in c or "--build" in c for c in calls)


@pytest.mark.parametrize("dbfilter", ["^dev$", "^(dev|sandbox_[a-z0-9_]+)$", "^%d$", ""])
def test_restore_accepts_configured_manager_filter_without_retargeting_dev(
    runtime, backup, processes, dbfilter
):
    manifest = runtime.workspace.root / MANIFEST
    manifest.write_text(
        manifest.read_text()
        + f"\n[odoo.options]\nlist_db = true\ndbfilter = {json.dumps(dbfilter)}\n"
    )
    configured = Runtime.load(Workspace.load(runtime.workspace.root))
    configured.generate()
    assert host.restore(configured, args(backup)) == 0
    assert configured.admin_password == Runtime.load(configured.workspace).admin_password


def test_restore_rejects_filter_not_declared_in_manifest(runtime):
    path = runtime.directory / "odoo.conf"
    path.write_text(path.read_text().replace("dbfilter = ^dev$", "dbfilter = .*"))
    with pytest.raises(OdxError, match="Generated database configuration changed"):
        host.topology(runtime)


@pytest.mark.parametrize("busy,active", [(True, False), (False, True)])
def test_busy_or_active_sessions_do_not_stop_services(runtime, backup, processes, busy, active):
    calls, state = processes
    state.running.append("opencode")
    state.busy, state.active = busy, active
    before = runtime.compose_file.read_bytes()
    with pytest.raises(OdxError):
        host.restore(runtime, args(backup))
    assert runtime.compose_file.read_bytes() == before
    assert not any(c[0] in ("stop", "up", "start") for c in calls)


def test_worker_failure_stops_offline_postgres_before_restarting_normal_services(
    runtime, backup, processes
):
    calls, state = processes
    state.failed = True
    with pytest.raises(OdxError, match="private"):
        host.restore(runtime, args(backup))
    stop = next(i for i, c in enumerate(calls) if c[0] == "stop" and "restore-db" in c)
    start = next(i for i, c in enumerate(calls) if c[0] == "start")
    assert stop < start


def test_failed_offline_stop_never_starts_another_postgres(runtime, backup, processes):
    calls, state = processes
    state.fail_command = "wait"
    original = host.run_command

    def run(command, **kwargs):
        action = command[command.index("--file") + 2 :]
        if action[0] == "stop" and "restore-db" in action:
            raise OdxError("could not stop isolated PostgreSQL")
        return original(command, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(host, "run_command", run)
        with pytest.raises(OdxError):
            host.restore(runtime, args(backup))
    assert not any(c[0] == "start" for c in calls)


def test_existing_destination_requires_explicit_replace_before_service_changes(
    runtime, backup, processes
):
    calls, state = processes
    state.target = True
    with pytest.raises(OdxError, match="replace"):
        host.restore(runtime, args(backup))
    assert not any(c[0] in ("stop", "start", "up") for c in calls)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"backup": "both", "resume": "a" * 32},
        {"resume": "../escape"},
        {"update": "all"},
    ],
)
def test_invalid_request_is_rejected(runtime, backup, processes, kwargs):
    value = args(**{"backup": backup if kwargs.get("update") else None, **kwargs})
    with pytest.raises(OdxError):
        host.restore(runtime, value)


def test_modified_generated_database_endpoint_is_rejected(runtime, backup, processes):
    file = runtime.directory / "odoo.conf"
    file.write_text(file.read_text().replace("db_host = db", "db_host = remote.invalid"))
    with pytest.raises(OdxError, match="non-native"):
        host.restore(runtime, args(backup))


def test_resume_does_not_rewrite_busy_workers_snapshot_or_state(runtime, backup, processes):
    calls, state = processes
    host.restore(runtime, args(backup))
    directory = next((runtime.directory / "restores").iterdir())
    record = json.loads((directory / "result.json").read_text())
    record.update(status="failed", host_finalized=False)
    (directory / "result.json").write_text(json.dumps(record))
    before = (directory / "result.json").read_bytes()
    engine = (directory / "engine/odoocker/restore/worker.py").read_bytes()
    state.busy = True
    with pytest.raises(OdxError, match="busy"):
        host.restore(runtime, args(resume=record["id"], reset_admin_password=True))
    assert (directory / "result.json").read_bytes() == before
    assert (directory / "engine/odoocker/restore/worker.py").read_bytes() == engine


def test_sql_cleanup_runs_only_after_successful_host_finalization(runtime, backup, processes):
    calls, state = processes
    state.running.append("opencode")
    host.restore(runtime, args(backup))
    cleanup = next(i for i, c in enumerate(calls) if host.CLEANUP_SQL in c)
    finalize = next(i for i, c in enumerate(calls) if host.FINALIZE in c)
    restart = next(i for i, c in enumerate(calls) if c[0] == "start" and c[-1] == "opencode")
    assert finalize < restart < cleanup
    record = json.loads(next((runtime.directory / "restores").glob("*/result.json")).read_text())
    assert record["sql_cleanup"]["status"] == "removed"


def test_keep_sql_prevents_cleanup_and_is_retained_on_resume(runtime, backup, processes):
    calls, state = processes
    state.failed = True
    with pytest.raises(OdxError):
        host.restore(runtime, args(backup, keep_sql=True))
    record = json.loads(next((runtime.directory / "restores").glob("*/result.json")).read_text())
    state.failed = False
    host.restore(runtime, args(resume=record["id"]))
    assert not any(host.CLEANUP_SQL in c for c in calls)
    assert any(host.CLEANUP_CHECKPOINTS in c for c in calls)
    assert host.options(args(), record["options"])["keep_sql"]


@pytest.mark.parametrize("stage", ["worker", "finalize", "opencode"])
def test_failed_restore_or_finalization_does_not_clean_sql(
    runtime,
    backup,
    processes,
    monkeypatch,
    stage,
):
    calls, state = processes
    state.failed = stage == "worker"
    state.running.append("opencode")
    original = host.run_command

    def run(command, **kwargs):
        if stage == "finalize" and host.FINALIZE in command:
            raise OdxError("finalize failure")
        if stage == "opencode" and command[-1] == "opencode" and "start" in command:
            raise OdxError("restart failure")
        return original(command, **kwargs)

    monkeypatch.setattr(host, "run_command", run)
    with pytest.raises(OdxError):
        host.restore(runtime, args(backup))
    assert not any(host.CLEANUP_SQL in c for c in calls)
    assert not any(host.CLEANUP_CHECKPOINTS in c for c in calls)


def test_cleanup_failure_warns_without_failing_dev_and_resume_retries_only_cleanup(
    runtime,
    backup,
    processes,
    monkeypatch,
    capsys,
):
    calls, state = processes
    original = host.run_command

    def run(command, **kwargs):
        if host.CLEANUP_SQL in command:
            raise OdxError("cleanup failure")
        return original(command, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(host, "run_command", run)
        assert host.restore(runtime, args(backup)) == 0
    directory = next((runtime.directory / "restores").iterdir())
    record = json.loads((directory / "result.json").read_text())
    assert record["status"] == "completed" and record["host_finalized"]
    assert record["sql_cleanup"]["status"] == "pending"
    assert "cleanup is pending" in capsys.readouterr().err
    calls.clear()
    assert host.restore(runtime, args(resume=record["id"])) == 0
    assert len(calls) == 1 and host.CLEANUP_SQL in calls[0]
    assert json.loads((directory / "result.json").read_text())["sql_cleanup"]["status"] == "removed"
    calls.clear()
    assert host.restore(runtime, args(resume=record["id"])) == 0
    assert not calls


def test_legacy_completed_resume_can_clean_sql_without_changing_publication_options(
    runtime,
    backup,
    processes,
):
    calls, state = processes
    host.restore(runtime, args(backup))
    directory = next((runtime.directory / "restores").iterdir())
    record = json.loads((directory / "result.json").read_text())
    record.pop("sql_cleanup")
    record["options"].pop("keep_sql")
    record["options"].pop("keep_checkpoints")
    record.pop("checkpoint_cleanup")
    record["publication_started"] = True
    (directory / "result.json").write_text(json.dumps(record))
    calls.clear()
    assert host.restore(runtime, args(resume=record["id"])) == 0
    assert len(calls) == 1 and host.CLEANUP_SQL in calls[0]


def test_checkpoint_cleanup_follows_sql_and_finalization(runtime, backup, processes):
    calls, state = processes
    host.restore(runtime, args(backup))
    finalize = next(i for i, c in enumerate(calls) if host.FINALIZE in c)
    sql = next(i for i, c in enumerate(calls) if host.CLEANUP_SQL in c)
    checkpoints = next(i for i, c in enumerate(calls) if host.CLEANUP_CHECKPOINTS in c)
    assert finalize < sql < checkpoints


def test_keep_checkpoints_survives_failure_and_resume_independently_of_sql(
    runtime,
    backup,
    processes,
):
    calls, state = processes
    state.failed = True
    with pytest.raises(OdxError):
        host.restore(runtime, args(backup, keep_checkpoints=True))
    record = json.loads(next((runtime.directory / "restores").glob("*/result.json")).read_text())
    state.failed = False
    host.restore(runtime, args(resume=record["id"]))
    assert not any(host.CLEANUP_CHECKPOINTS in c for c in calls)
    assert any(host.CLEANUP_SQL in c for c in calls)
    assert host.options(args(), record["options"])["keep_checkpoints"]


def test_checkpoint_cleanup_failure_keeps_journal_and_resume_only_retries_cleanup(
    runtime,
    backup,
    processes,
    monkeypatch,
    capsys,
):
    calls, state = processes
    original = host.run_command

    def run(command, **kwargs):
        if host.CLEANUP_CHECKPOINTS in command:
            path = next((runtime.directory / "restores").glob("*/result.json"))
            (path.parent / "checkpoint-cleanup.json").write_text(
                json.dumps({"database_removed": True, "removal_started": True})
            )
            raise OdxError("injected partial cleanup failure")
        return original(command, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(host, "run_command", run)
        assert host.restore(runtime, args(backup)) == 0
    directory = next((runtime.directory / "restores").iterdir())
    record = json.loads((directory / "result.json").read_text())
    assert record["status"] == "completed" and record["host_finalized"]
    assert record["checkpoint_cleanup"]["status"] == "pending"
    assert record["checkpoint_cleanup"]["database_removed"]
    assert record["checkpoint_cleanup"]["removal_started"]
    assert "checkpoint cleanup is pending" in capsys.readouterr().err
    calls.clear()
    assert host.restore(runtime, args(resume=record["id"])) == 0
    assert len(calls) == 1 and host.CLEANUP_CHECKPOINTS in calls[0]
    calls.clear()
    assert host.restore(runtime, args(resume=record["id"])) == 0
    assert not calls


def test_legacy_options_keep_checkpoints_without_changing_new_restore_default():
    assert not host.options(args())["keep_checkpoints"]
    assert host.options(args(), {"keep_sql": False})["keep_checkpoints"]


def test_checkpoint_retention_cannot_change_after_publication(runtime, backup, processes):
    host.restore(runtime, args(backup))
    result = next((runtime.directory / "restores").glob("*/result.json"))
    record = json.loads(result.read_text())
    record["publication_started"] = True
    result.write_text(json.dumps(record))
    with pytest.raises(OdxError, match="Publication has begun"):
        host.restore(runtime, args(resume=record["id"], keep_checkpoints=True))
