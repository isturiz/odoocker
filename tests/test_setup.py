"""Concrete setup orchestration, with Docker/OpenCode replaced by test doubles."""

import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from odoocker import cli, setup
from odoocker import runtime as runtime_module
from odoocker.config import MANIFEST, Workspace
from odoocker.errors import OdxError
from odoocker.locking import locked
from odoocker.maintenance import maintenance_process
from odoocker.runtime import Runtime

MANIFEST_TEXT = """schema = 1
[workspace]
name = "client"
[odoo]
version = "19.0"
[opencode]
version = "2.0.30"
"""


@pytest.fixture
def runtime(tmp_path):
    (tmp_path / MANIFEST).write_text(MANIFEST_TEXT)
    workspace = Workspace.load(tmp_path)
    (workspace.source / "odoo").mkdir(parents=True)
    (workspace.source / "odoo-bin").touch()
    (workspace.source / "odoo/release.py").write_text("version_info = (19, 0, 0)")
    return Runtime.load(workspace)


@pytest.fixture
def preparation(runtime, monkeypatch):
    monkeypatch.delenv("ODOOCKER_CONTAINER", raising=False)
    events = []
    state = SimpleNamespace(
        running=[],
        busy=False,
        active=False,
        host="2.0.30",
        fail=None,
        leased=False,
        changed=False,
        info_code=0,
    )

    def host_version():
        events.append("host-version")
        if state.host is None:
            raise OdxError("OpenCode executable missing")
        return state.host

    def active(instance):
        events.append("active-check")
        if state.active:
            raise OdxError("OpenCode has active sessions")

    def info(instance):
        assert state.leased
        events.append("info")
        return state.info_code

    @contextmanager
    def lease(args, **kwargs):
        events.append("lease")
        assert kwargs["protocol"] == "ODX_SETUP"
        assert "--project-name" in args and runtime.project in args
        if state.busy:
            raise OdxError("Maintenance lock/backup failed; an Odoo operation may be active")
        with pytest.raises(OdxError, match="Resource busy"), locked(runtime.operation_lock):
            pytest.fail("setup must hold host operation lock")
        if state.changed:
            (runtime.workspace.root / MANIFEST).write_text(
                MANIFEST_TEXT.replace("client", "changed")
            )
        state.leased = True
        try:
            yield SimpleNamespace()
        finally:
            state.leased = False
            events.append("released")

    def run(args, **kwargs):
        assert args[:2] == ["docker", "compose"], args
        assert args[args.index("--project-name") + 1] == runtime.project
        command = args[args.index("--file") + 2 :]
        events.append(command)
        if command[0] == state.fail:
            raise OdxError("simulated " + state.fail + " failure")
        if command[0] == "build":
            assert not state.leased
            file = args[args.index("--file") + 1]
            with open(file) as stream:
                document = json.load(stream)
            assert set(document["volumes"]) == {"runtime"}
            service = document["services"]["setup-guard"]
            assert service["network_mode"] == "none"
            assert service["volumes"] == [
                {"type": "volume", "source": "runtime", "target": "/var/lib/odoocker"}
            ]
            assert "docker.sock" not in json.dumps(document)
            assert args[args.index("--env-file") + 1].endswith("compose.env")
        elif command[0] in ("up", "exec", "stop"):
            assert state.leased, command
        stdout = ""
        if command[0] == "ps":
            stdout = "\n".join(state.running) if "--services" in command else "[]"
        return SimpleNamespace(returncode=0, stdout=stdout)

    monkeypatch.setattr(setup.opencode, "host_version", host_version)
    monkeypatch.setattr(setup.opencode, "info", info)
    monkeypatch.setattr(setup, "active_sessions", active)
    monkeypatch.setattr(setup, "maintenance_process", lease)
    monkeypatch.setattr(setup, "run_command", run)
    monkeypatch.setattr(runtime_module, "run_command", run)
    return events, state


def actions(events):
    return [event for event in events if isinstance(event, list)]


@pytest.mark.parametrize("code", [False, True])
def test_fresh_setup_prepares_deps_before_optional_opencode(runtime, preparation, code):
    events, _ = preparation
    original = (runtime.workspace.root / MANIFEST).read_bytes()
    assert setup.prepare(runtime, code=code) == 0
    commands = actions(events)
    assert [cmd[0] for cmd in commands] == ["build", "up", "exec"] + (["up"] if code else [])
    assert commands[1][-1] == "odoo"
    assert "environment_locked=True, only_if_needed=True" in commands[2][-1]
    if code:
        assert commands[-1][-1] == "opencode"
        assert "--no-deps" in commands[-1]
        assert events.index("info") < events.index("released")
    else:
        assert "host-version" not in events and "info" not in events
    assert (runtime.workspace.root / MANIFEST).read_bytes() == original
    assert runtime.compose_file.exists()
    assert runtime.workspace.source.is_dir()
    assert not list(runtime.directory.glob("setup-*"))
    assert all(
        not {"down", "--init", "--update", "--volumes", "fetch", "clone"}.intersection(cmd)
        for cmd in commands
    )


def test_plain_setup_needs_no_host_opencode(runtime, preparation):
    _, state = preparation
    state.host = None
    assert setup.prepare(runtime) == 0


def test_missing_host_opencode_with_code_aborts_before_build(runtime, preparation):
    events, state = preparation
    state.host = None
    with pytest.raises(OdxError, match="executable missing"):
        setup.prepare(runtime, code=True)
    assert actions(events) == []
    assert not runtime.compose_file.exists()


def test_older_host_advises_upgrading_host_not_downgrading_container(runtime, preparation, capsys):
    _, state = preparation
    state.host = "2.0.29"
    setup.prepare(runtime, code=True)
    output = capsys.readouterr().out
    assert "Update OpenCode on the host" in output
    assert "Run odx code opencode update" not in output


def test_failed_api_check_releases_lease_and_reports_error(runtime, preparation):
    events, state = preparation
    state.info_code = 1
    with pytest.raises(OdxError, match="API verification failed"):
        setup.prepare(runtime, code=True)
    assert events[-1] == "released"


def test_pinned_version_mismatch_warns_without_upgrading(runtime, preparation, capsys):
    events, state = preparation
    state.host = "2.0.31"
    setup.prepare(runtime, code=True)
    assert Workspace.load(runtime.workspace.root).opencode_version == "2.0.30"
    assert "odx code opencode update" in capsys.readouterr().out
    assert "info" not in events  # a strict matching-client check would fail here


def test_idle_running_opencode_is_stopped_and_restored_even_without_flag(runtime, preparation):
    runtime.generate()
    events, state = preparation
    state.running = ["db", "odoo", "opencode"]
    setup.prepare(runtime)
    commands = actions(events)
    assert ["stop", "--timeout", "30", "opencode"] in commands
    assert commands[-1][-1] == "opencode"
    assert events.count("active-check") == 2


def test_active_session_aborts_before_build_or_configuration_changes(runtime, preparation):
    runtime.generate()
    events, state = preparation
    state.running = ["opencode"]
    state.active = True
    original = runtime.compose_file.read_bytes()
    with pytest.raises(OdxError, match="active sessions"):
        setup.prepare(runtime)
    assert not any(cmd[0] in ("build", "up", "exec", "stop") for cmd in actions(events))
    assert runtime.compose_file.read_bytes() == original


@pytest.mark.parametrize("configured", [False, True])
def test_busy_linux_lease_never_replaces_config_or_stops_services(runtime, preparation, configured):
    if configured:
        runtime.generate()
    original = runtime.compose_file.read_bytes() if configured else None
    events, state = preparation
    state.busy = True
    with pytest.raises(OdxError, match="Maintenance lock"):
        setup.prepare(runtime)
    assert not any(cmd[0] in ("up", "exec", "stop") for cmd in actions(events))
    assert (
        runtime.compose_file.read_bytes() if runtime.compose_file.exists() else None
    ) == original
    assert not list(runtime.directory.glob("setup-*"))


@pytest.mark.parametrize("failure", ["build", "up", "exec"])
def test_failure_stops_pipeline_and_releases_lease(runtime, preparation, failure):
    events, state = preparation
    state.fail = failure
    with pytest.raises(OdxError, match="simulated"):
        setup.prepare(runtime, code=True)
    commands = actions(events)
    assert commands[-1][0] == failure
    assert not any(cmd[-1] == "opencode" and cmd[0] == "up" for cmd in commands)
    assert not state.leased
    assert (runtime.workspace.root / MANIFEST).read_text() == MANIFEST_TEXT


def test_missing_source_is_not_implicitly_fetched(runtime, preparation):
    events, _ = preparation
    (runtime.workspace.source / "odoo-bin").unlink()
    with pytest.raises(OdxError, match="run odx fetch"):
        setup.prepare(runtime)
    assert events == []


@pytest.mark.parametrize("changes", [{"postgres_version": "18"}, {"odoo_version": "17.0"}])
def test_setup_cannot_switch_database_or_odoo_versions(runtime, preparation, changes):
    runtime.generate()
    events, _ = preparation
    changed = replace(runtime, workspace=replace(runtime.workspace, **changes))
    if "odoo_version" in changes:
        (runtime.workspace.source / "odoo/release.py").write_text("version_info = (17, 0, 0)")
    with pytest.raises(OdxError, match="PostgreSQL changes|across Odoo versions"):
        setup.prepare(changed)
    assert events == []


def test_changed_opencode_pin_requires_backed_up_update(runtime, preparation):
    runtime.generate()
    events, _ = preparation
    changed = replace(runtime, workspace=replace(runtime.workspace, opencode_version="2.0.31"))
    with pytest.raises(OdxError, match="backed-up version changes"):
        setup.prepare(changed, code=True)
    assert events == []


def test_concurrent_manifest_edit_is_preserved(runtime, preparation):
    _, state = preparation
    state.changed = True
    with pytest.raises(OdxError, match="configuration changed during setup"):
        setup.prepare(runtime)
    assert "changed" in (runtime.workspace.root / MANIFEST).read_text()
    assert not runtime.compose_file.exists()


def test_setup_respects_operation_lock(runtime, preparation):
    events, _ = preparation
    with locked(runtime.operation_lock):
        with pytest.raises(OdxError, match="Resource busy"):
            setup.prepare(runtime)
    assert events == []


def test_cli_dispatches_setup_with_explicit_workspace_and_instance(runtime, preparation):
    assert cli.main(["--workspace", str(runtime.workspace.root), "setup", "--code"]) == 0


def test_container_rejects_setup_without_docker_access(monkeypatch, capsys):
    monkeypatch.setenv("ODOOCKER_CONTAINER", "1")
    assert cli.main(["setup", "--code"]) == 1
    assert "host command" in capsys.readouterr().err


def test_real_setup_guard_holds_both_locks_until_process_release(tmp_path):
    args = [os.sys.executable, "-u", "-c", setup.GUARD, str(tmp_path)]
    with maintenance_process(args, env=os.environ, protocol="ODX_SETUP"):
        for name in ("environment.lock", "dev-database.lock"):
            with pytest.raises(OdxError, match="Resource busy"), locked(tmp_path / name):
                pytest.fail("guard must retain both locks")
    with locked(tmp_path / "environment.lock"):
        pass


def test_real_guard_rejects_busy_environment_without_waiting(tmp_path):
    with locked(tmp_path / "environment.lock", shared=True):
        result = subprocess.run(
            [os.sys.executable, "-c", setup.GUARD, str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 1 and "busy" in result.stdout
