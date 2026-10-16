"""Update transactions and the old-image maintenance protocol without Docker."""

import io
import json
import os
import subprocess
import tarfile
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from odoocker import cli
from odoocker import runtime as runtime_module
from odoocker.adapters import opencode
from odoocker.adapters import opencode_update as updater
from odoocker.config import MANIFEST, Workspace
from odoocker.errors import OdxError
from odoocker.locking import locked
from odoocker.runtime import Runtime

MANIFEST_TEXT = """schema = 1
[workspace]
name = "example"
[odoo]
version = "19.0"
[opencode] # preserve table comment
version = "2.0.30" # preserve version comment
[postgres]
version = "18"
pgvector = true
"""


@pytest.fixture
def runtime(tmp_path):
    (tmp_path / MANIFEST).write_text(MANIFEST_TEXT)
    workspace = Workspace.load(tmp_path)
    (workspace.source / "odoo").mkdir(parents=True)
    (workspace.source / "odoo-bin").touch()
    (workspace.source / "odoo/release.py").write_text("version_info = (19, 0, 0)")
    runtime = Runtime.load(workspace)
    runtime.generate()
    return runtime


@pytest.fixture
def transaction(runtime, monkeypatch):
    events = []
    state = SimpleNamespace(
        fail=None, sessions={}, running=True, backup_error=False, manifest_change=False
    )
    monkeypatch.setattr(opencode, "host_version", lambda: "2.0.31")

    def info(new):
        events.append("verify")
        assert new.workspace.opencode_version == "2.0.31"
        assert new.identity == runtime.identity and new.password == runtime.password
        return 1 if state.fail == "verify" else 0

    monkeypatch.setattr(opencode, "info", info)

    def run(args, **kwargs):
        if args[0] == "opencode":
            assert args[-1] == "/api/session/active"
            assert kwargs["env"]["OPENCODE_PASSWORD"] == runtime.password
            assert runtime.password not in " ".join(args)
            return SimpleNamespace(stdout=json.dumps({"data": state.sessions}))
        assert args[:2] == ["docker", "compose"]
        assert args[args.index("--project-name") + 1] == runtime.project
        command = args[args.index("--file") + 2 :]
        events.append(command)
        if command[0] == state.fail:
            raise OdxError("simulated " + state.fail + " failure")
        stdout = ""
        if command[0] == "ps":
            stdout = "odoo\nopencode\ndb\n" if state.running else ""
        elif command[0] == "port":
            stdout = "127.0.0.1:49152\n"
        return SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr(updater, "run_command", run)
    monkeypatch.setattr(runtime_module, "run_command", run)

    @contextmanager
    def maintenance(old, saved, directory):
        events.append("locked")
        for lock in (old.operation_lock, old.workspace.root / ".odoocker/opencode-update.lock"):
            with pytest.raises(OdxError, match="Resource busy"), locked(lock):
                pytest.fail("update must retain host locks")

        def backup():
            events.append("backup")
            assert (old.workspace.root / MANIFEST).read_text() == MANIFEST_TEXT
            if state.backup_error:
                raise OdxError("backup failed")
            (directory / "opencode-state.tar.gz").write_bytes(b"fixture archive")
            if state.manifest_change:
                (old.workspace.root / MANIFEST).write_text(MANIFEST_TEXT + "# concurrent edit\n")

        guard = SimpleNamespace(stopped=False, backup=backup, image="sha256:" + "a" * 64)
        try:
            yield guard
        except BaseException:
            if guard.stopped:
                events.append("stop-before-unlock")
            raise
        finally:
            events.append("unlocked")

    monkeypatch.setattr(updater, "maintenance", maintenance)
    return events, state


@pytest.mark.parametrize("quote", ['"', "'"])
@pytest.mark.parametrize("line_end", ["\n", "\r\n"])
def test_pin_preserves_comments_unrelated_values_and_line_endings(quote, line_end):
    original = MANIFEST_TEXT.replace('version = "2.0.30"', f"version = {quote}2.0.30{quote}")
    original = original.replace("\n", line_end)
    assert updater.pin_version(original, "2.1.0-beta.1") == original.replace(
        "2.0.30", "2.1.0-beta.1"
    )


@pytest.mark.parametrize(
    "layout",
    [
        'opencode = {version = "2.0.30"}\n',
        '[opencode]\nversion = """2.0.30"""\n',
    ],
)
def test_unsupported_toml_layout_is_not_rewritten(layout):
    with pytest.raises(OdxError, match="table|quoted"):
        updater.pin_version(layout, "2.0.31")


def test_same_version_retains_even_inline_manifest_verbatim():
    text = 'opencode = {version = "2.0.30"} # unchanged\n'
    assert updater.pin_version(text, "2.0.30") == text


@pytest.mark.parametrize("version", ["latest", "1.0.1", "2.0.31\n", '2.0.31"\nmalicious = true'])
def test_invalid_target_versions_rejected(version):
    with pytest.raises(OdxError, match="exact"):
        updater.pin_version(MANIFEST_TEXT, version)


@pytest.mark.parametrize(
    "older,newer",
    [
        ("2.0.30", "2.0.31"),
        ("2.0.99", "2.1.0"),
        ("2.1.0-beta.2", "2.1.0-beta.10"),
        ("2.1.0-beta.10", "2.1.0"),
    ],
)
def test_release_order_rejects_downgrades_and_handles_prereleases(older, newer):
    assert updater.release_key(older) < updater.release_key(newer)


def test_host_older_than_saved_server_cannot_trigger_a_downgrade(runtime, transaction, monkeypatch):
    events, _ = transaction
    monkeypatch.setattr(opencode, "host_version", lambda: "2.0.29")
    with pytest.raises(OdxError, match="Refusing OpenCode downgrade"):
        updater.update(runtime)
    assert events == []
    assert (runtime.workspace.root / MANIFEST).read_text() == MANIFEST_TEXT


def test_update_keeps_identity_resources_and_backs_up_before_editing(runtime, transaction):
    events, _ = transaction
    previous = json.loads(runtime.compose_file.read_text())
    # A new engine must be able to stop/back up an older saved image.
    previous["services"]["opencode"]["image"] = "old-engine:oc2.0.30"
    runtime.compose_file.write_text(json.dumps(previous))
    assert updater.update(runtime) == 0
    workspace = Workspace.load(runtime.workspace.root)
    assert workspace.opencode_version == "2.0.31"
    after = json.loads(runtime.compose_file.read_text())
    assert after["services"]["db"] == previous["services"]["db"]
    assert after["volumes"] == previous["volumes"]
    assert after["services"]["opencode"]["volumes"] == previous["services"]["opencode"]["volumes"]
    (directory,) = (runtime.directory / "opencode-updates").iterdir()
    assert (directory / MANIFEST).read_text() == MANIFEST_TEXT
    assert json.loads((directory / "compose.json").read_text()) == previous
    record = json.loads((directory / "result.json").read_text())
    assert record["status"] == "completed" and record["backup_complete"]
    assert len(record["archive_sha256"]) == 64
    assert events.index("backup") < events.index(["build", "odoo", "opencode"])
    assert events.index("verify") < events.index("unlocked")
    assert all(
        "down" not in event and "--volumes" not in event
        for event in events
        if isinstance(event, list)
    )


@pytest.mark.parametrize("failure", ["build", "up", "verify"])
def test_failures_keep_backup_and_do_not_automatically_downgrade(runtime, transaction, failure):
    events, state = transaction
    state.fail = failure
    with pytest.raises(OdxError, match="No automatic downgrade"):
        updater.update(runtime)
    assert Workspace.load(runtime.workspace.root).opencode_version == "2.0.31"
    (directory,) = (runtime.directory / "opencode-updates").iterdir()
    result = json.loads((directory / "result.json").read_text())
    assert result["status"] == "failed" and result["backup_complete"]
    assert (directory / MANIFEST).read_text() == MANIFEST_TEXT
    assert events.index("stop-before-unlock") < events.index("unlocked")


def test_failed_backup_never_edits_manifest_or_starts_new_services(runtime, transaction):
    events, state = transaction
    state.backup_error = True
    with pytest.raises(OdxError, match="backup failed"):
        updater.update(runtime)
    assert (runtime.workspace.root / MANIFEST).read_text() == MANIFEST_TEXT
    assert ["build", "odoo", "opencode"] not in events
    (directory,) = (runtime.directory / "opencode-updates").iterdir()
    assert json.loads((directory / "result.json").read_text())["backup_complete"] is False


def test_active_sessions_abort_without_stopping_or_writing_backup(runtime, transaction):
    events, state = transaction
    state.sessions = {"ses_example": {"status": "busy"}}
    with pytest.raises(OdxError, match="active sessions"):
        updater.update(runtime)
    assert "locked" not in events
    assert not (runtime.directory / "opencode-updates").exists()
    assert (runtime.workspace.root / MANIFEST).read_text() == MANIFEST_TEXT


def test_concurrent_manifest_edit_is_not_overwritten(runtime, transaction):
    _, state = transaction
    state.manifest_change = True
    with pytest.raises(OdxError, match="configuration changed during"):
        updater.update(runtime)
    assert (runtime.workspace.root / MANIFEST).read_text() == MANIFEST_TEXT + "# concurrent edit\n"


def test_stopped_server_does_not_start_host_api_discovery(runtime, transaction):
    _, state = transaction
    state.running = False
    assert updater.update(runtime) == 0


def test_other_pending_workspace_changes_abort_before_docker(runtime, monkeypatch):
    changed = replace(runtime, workspace=replace(runtime.workspace, postgres_version="16"))
    with pytest.raises(OdxError, match="Other workspace settings"):
        updater.saved_configuration(changed)


@pytest.mark.parametrize(
    "changes",
    [
        {"odoo_options": {"limit_time_cpu": 500}},
        {"requirement_files": ("new-requirements.txt",)},
    ],
)
def test_pending_settings_hidden_in_labels_are_not_applied_by_update(runtime, changes):
    changed = replace(runtime, workspace=replace(runtime.workspace, **changes))
    with pytest.raises(OdxError, match="Other workspace settings"):
        updater.saved_configuration(changed)


def test_missing_instance_is_not_implicitly_initialized(runtime):
    runtime.compose_file.unlink()
    with pytest.raises(OdxError, match="Initialize this instance"):
        updater.saved_configuration(runtime)


def test_concurrent_host_updates_fail_before_stopping(runtime, transaction):
    events, _ = transaction
    with locked(runtime.workspace.root / ".odoocker/opencode-update.lock"):
        with pytest.raises(OdxError, match="Resource busy"):
            updater.update(runtime)
    assert events == []


def test_guard_backs_up_readonly_sources_and_holds_locks_until_release(tmp_path):
    state = tmp_path / "runtime"
    state.mkdir()
    sources = [tmp_path / name for name in ("config", "data", "state")]
    for source in sources:
        source.mkdir()
        (source / "fixture").write_text(source.name)
    (sources[1] / "symlink").symlink_to(sources[0] / "fixture")
    archive = tmp_path / "backup.tar.gz"
    process = subprocess.Popen(
        [
            os.sys.executable,
            "-u",
            "-c",
            updater.GUARD,
            str(state),
            str(archive),
            *map(str, sources),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        guard = updater.Maintenance(process)
        guard.expect("locked")
        with pytest.raises(OdxError, match="Resource busy"), locked(state / "environment.lock"):
            pytest.fail("guard must hold environment lock")
        guard.backup()
        with tarfile.open(archive) as backup:
            assert backup.extractfile("config/fixture").read() == b"config"
            assert backup.extractfile("data/fixture").read() == b"data"
            assert backup.extractfile("state/fixture").read() == b"state"
            assert backup.getmember("data/symlink").issym()
        with pytest.raises(OdxError, match="Resource busy"), locked(state / "dev-database.lock"):
            pytest.fail("guard must still hold lock after backup")
    finally:
        process.stdin.close()
        process.wait(timeout=10)
        process.stdout.close()
    assert archive.stat().st_mode & 0o777 == 0o600
    with locked(state / "environment.lock"):
        pass


def test_guard_refuses_busy_environment_before_writing_archive(tmp_path):
    archive = tmp_path / "backup.tar.gz"
    with locked(tmp_path / "environment.lock"):
        result = subprocess.run(
            [os.sys.executable, "-c", updater.GUARD, str(tmp_path), str(archive)],
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 1 and "busy" in result.stdout
    assert not archive.exists()


def test_maintenance_uses_external_readonly_volumes_and_stops_before_unlock(runtime, monkeypatch):
    directory = runtime.directory / "fixture-backup"
    directory.mkdir()
    state = directory / "runtime"
    state.mkdir()
    sources = [directory / name for name in ("config", "data", "state")]
    for source in sources:
        source.mkdir()
    popen = subprocess.Popen
    calls = []

    def start(args, **kwargs):
        assert args[:2] == ["docker", "compose"]
        assert args[args.index("--project-name") + 1] == runtime.project
        document = json.loads((directory / "maintenance.compose.json").read_text())
        for name, entry in document["volumes"].items():
            assert entry == {"external": True, "name": f"{runtime.project}_{name}"}
        service = document["services"]["guard"]
        assert service["network_mode"] == "none"
        assert "docker.sock" not in json.dumps(document)
        assert all(mount["read_only"] for mount in service["volumes"][1:4])
        assert service["volumes"][0]["target"] == "/runtime"
        # Run the identical protocol locally, never Docker in a unit test.
        return popen(
            [
                os.sys.executable,
                "-u",
                "-c",
                updater.GUARD,
                str(state),
                str(directory / "opencode-state.tar.gz"),
                *map(str, sources),
            ],
            **kwargs,
        )

    def stop(*args, **kwargs):
        if args[1:2] == ("images",):
            return SimpleNamespace(stdout="sha256:" + "a" * 64 + "\n")
        with pytest.raises(OdxError, match="Resource busy"), locked(state / "environment.lock"):
            pytest.fail("must stop before releasing maintenance locks")
        calls.append(args)

    monkeypatch.setattr(updater.subprocess, "Popen", start)
    monkeypatch.setattr(updater, "saved_compose", stop)
    with pytest.raises(OdxError, match="fixture failure"):
        with updater.maintenance(runtime, updater.saved_configuration(runtime), directory) as guard:
            guard.stopped = True
            guard.backup()
            raise OdxError("fixture failure")
    assert len(calls) == 1
    with locked(state / "environment.lock"):
        pass


def test_maintenance_rejects_busy_and_timeout_responses():
    process = SimpleNamespace(stdout=io.StringIO('ODX_UPDATE {"status": "busy"}\n'))
    # StringIO has no file descriptor; exercise the exact protocol with select mocked.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(updater.select, "select", lambda *args: ([process.stdout], [], []))
        with pytest.raises(OdxError, match="Maintenance lock"):
            updater.Maintenance(process).expect("locked")
        patch.setattr(updater.select, "select", lambda *args: ([], [], []))
        with pytest.raises(OdxError, match="Timed out"):
            updater.Maintenance(process).expect("locked")


def test_cli_routes_update_but_preserves_escaped_tui_arguments(runtime, monkeypatch):
    monkeypatch.delenv("ODOOCKER_CONTAINER", raising=False)
    calls = []
    monkeypatch.setattr(updater, "update", lambda runtime: calls.append("update") or 0)
    monkeypatch.setattr(opencode, "connect", lambda runtime, args: calls.append(args) or 0)
    args = ["--workspace", str(runtime.workspace.root), "code", "opencode"]
    assert cli.main(args + ["update"]) == 0
    assert cli.main(args + ["--", "update"]) == 0
    assert calls == ["update", ["update"]]


@pytest.mark.parametrize("extra", [["update", "--force"], ["--info", "update"]])
def test_cli_rejects_ambiguous_update_options(runtime, monkeypatch, extra):
    monkeypatch.delenv("ODOOCKER_CONTAINER", raising=False)
    assert cli.main(["--workspace", str(runtime.workspace.root), "code", "opencode", *extra]) == 1
