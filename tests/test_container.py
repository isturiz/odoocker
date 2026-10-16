"""Linux command safety and test artifacts, without Odoo, PostgreSQL, or Docker."""

import io
import json
import signal
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoocker import container
from odoocker.errors import OdxError
from odoocker.locking import locked

SUCCESS_LOG = (
    "test output\nsecond line\n"
    "2026-10-15 12:00:00 INFO test_db odoo.tests.result: "
    "0 failed, 0 error(s) of 3 tests when loading database 'test_db'\n"
)


@pytest.fixture(autouse=True)
def isolated_container(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    python = tmp_path / "venv" / "bin" / "python"
    config = tmp_path / "odoo.conf"
    config.write_text("[options]\ndb_name = dev\n")
    requirement = tmp_path / "requirements.txt"
    requirement.write_text("example==1.0\n")
    monkeypatch.setattr(container, "STATE", state)
    monkeypatch.setattr(container, "PYTHON", str(python))
    monkeypatch.setattr(container, "CONFIG", str(config))
    monkeypatch.setattr(container, "requirements", lambda: [requirement])

    def forbidden(*args, **kwargs):
        pytest.fail("Unit tests must not start external processes")

    monkeypatch.setattr(container, "run_command", forbidden)
    monkeypatch.setattr(container.subprocess, "Popen", forbidden)
    monkeypatch.setattr(container.os, "killpg", forbidden)
    return SimpleNamespace(state=state, python=python, requirement=requirement)


@pytest.fixture
def ready(isolated_container):
    isolated_container.python.parent.mkdir(parents=True)
    isolated_container.python.write_text("fake executable")
    (container.STATE / "dependencies.json").write_text(
        json.dumps(
            {
                "fingerprint": container.dependency_fingerprint(),
            }
        )
    )
    return isolated_container


@pytest.fixture
def test_process(monkeypatch):
    calls = []
    processes = []

    def install(*, code=0, wait_error=None, output=SUCCESS_LOG):
        def popen(args, **kwargs):
            calls.append((args, kwargs))
            data = Path(args[args.index("--data-dir") + 1])
            data.mkdir(parents=True)
            (data / "filestore").write_text("test-only data")
            process = SimpleNamespace(
                stdout=io.StringIO(output),
                pid=12345,
                waits=[],
            )

            def wait(timeout=None):
                process.waits.append(timeout)
                if wait_error is not None and len(process.waits) == 1:
                    raise wait_error
                return code

            process.wait = wait
            processes.append(process)
            return process

        monkeypatch.setattr(container.subprocess, "Popen", popen)
        monkeypatch.setattr(container, "test_port", lambda: nullcontext(45678))
        return calls, processes

    return install


def test_require_ready_rejects_missing_python_even_with_marker(isolated_container):
    (container.STATE / "dependencies.json").write_text(
        json.dumps(
            {
                "fingerprint": container.dependency_fingerprint(),
            }
        )
    )
    with pytest.raises(OdxError, match="dependencies are not installed"):
        container.require_ready()


def test_require_ready_rejects_missing_stamp(ready):
    (container.STATE / "dependencies.json").unlink()
    with pytest.raises(OdxError, match="dependencies are not installed"):
        container.require_ready()


def test_require_ready_accepts_current_dependencies(ready):
    assert container.require_ready() is None


@pytest.mark.parametrize("operation", ["require_ready", "install_dependencies"])
def test_declared_python_minimum_blocks_an_incompatible_runtime_without_deleting_marker(
    ready, monkeypatch, tmp_path, operation
):
    source = tmp_path / "future-source"
    (source / "odoo").mkdir(parents=True)
    (source / "odoo/release.py").write_text("MIN_PY_VERSION = (99, 0)\n")
    monkeypatch.setattr(container, "ODOO", str(source / "odoo-bin"))
    with pytest.raises(OdxError, match="requires Python >= 99.0"):
        getattr(container, operation)()
    assert (container.STATE / "dependencies.json").exists()


def test_conditional_dependencies_skip_valid_environment_without_installing(ready, capsys):
    original = (container.STATE / "dependencies.json").read_bytes()
    assert container.install_dependencies(only_if_needed=True) == 0
    assert "Dependencies unchanged" in capsys.readouterr().out
    assert (container.STATE / "dependencies.json").read_bytes() == original


def test_setup_dependencies_use_the_held_external_environment_lease(ready):
    with locked(container.STATE / "environment.lock"):
        assert container.install_dependencies(environment_locked=True, only_if_needed=True) == 0
        with (
            pytest.raises(OdxError, match="Resource busy"),
            locked(container.STATE / "environment.lock"),
        ):
            pytest.fail("external lease must remain held")


def test_conditional_dependencies_still_reject_busy_lock(ready):
    with locked(container.STATE / "environment.lock", shared=True):
        with pytest.raises(OdxError, match="Resource busy"):
            container.install_dependencies(only_if_needed=True)


@pytest.mark.parametrize("corrupt", [False, True, "null", "[]", '"text"', "123"])
def test_conditional_dependencies_reinstall_stale_or_corrupt_marker(ready, monkeypatch, corrupt):
    if corrupt:
        (container.STATE / "dependencies.json").write_text(
            "not json" if corrupt is True else corrupt
        )
    else:
        ready.requirement.write_text("example==2.0\n")
    calls = []
    monkeypatch.setattr(container, "run_command", lambda args, **kwargs: calls.append(args))
    assert container.install_dependencies(only_if_needed=True) == 0
    assert len(calls) == 2
    assert calls[0][:3] == ["uv", "pip", "install"]
    assert calls[1][:3] == ["uv", "pip", "check"]
    container.require_ready()


def test_require_ready_rejects_changed_requirements(ready):
    ready.requirement.write_text("example==2.0\n")
    with pytest.raises(OdxError, match="Dependency declarations changed"):
        container.require_ready()


def test_fingerprint_includes_requirement_path_and_content(isolated_container, monkeypatch):
    original = container.dependency_fingerprint()
    isolated_container.requirement.write_text("example==2.0\n")
    assert container.dependency_fingerprint() != original
    other = isolated_container.requirement.with_name("another.txt")
    other.write_text("example==1.0\n")
    monkeypatch.setattr(container, "requirements", lambda: [other])
    assert container.dependency_fingerprint() != original


@pytest.mark.parametrize("value", ["sale", "sale,stock", "base_automation", "A1,other_2"])
def test_module_names_accepts_only_technical_name_lists(value):
    assert container.module_names(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sale,",
        ",sale",
        "sale,,stock",
        "sale stock",
        "sale;id",
        "../sale",
        "--database=dev",
        "1sale",
        "sale\n",
        "sale,$(id)",
        "sale-stock",
    ],
)
def test_module_names_rejects_invalid_or_command_like_input(value):
    with pytest.raises(OdxError, match="comma-separated technical names"):
        container.module_names(value)


@pytest.mark.parametrize("stop", [False, True])
def test_run_odoo_checks_readiness_holds_locks_and_returns_code(ready, monkeypatch, stop):
    calls = []

    def run(args, **kwargs):
        for filename in ("environment.lock", "dev-database.lock"):
            with pytest.raises(OdxError, match="Resource busy"), locked(container.STATE / filename):
                pytest.fail("Run must hold both locks while Odoo executes")
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr(container, "run_command", run)
    assert container.run_odoo(install="sale,stock", update="base", stop=stop) == 9
    assert calls == [
        (
            [
                container.PYTHON,
                container.ODOO,
                "--config",
                container.CONFIG,
                "--init",
                "sale,stock",
                "--update",
                "base",
                *(["--stop-after-init", "--no-http"] if stop else ["--dev=all"]),
            ],
            {"check": False},
        )
    ]
    with (
        locked(container.STATE / "environment.lock"),
        locked(container.STATE / "dev-database.lock"),
    ):
        pass


def test_run_odoo_requires_dependencies(isolated_container):
    with pytest.raises(OdxError, match="dependencies are not installed"):
        container.run_odoo()


@pytest.mark.parametrize("option", ["install", "update"])
def test_run_odoo_rejects_invalid_modules_without_starting_process(ready, option):
    with pytest.raises(OdxError, match="technical names"):
        container.run_odoo(**{option: "sale;id"})


@pytest.mark.parametrize("filename", ["environment.lock", "dev-database.lock"])
def test_run_odoo_rejects_busy_resources(ready, filename):
    with locked(container.STATE / filename):
        with pytest.raises(OdxError, match="Resource busy"):
            container.run_odoo()


@pytest.mark.parametrize("failure_at", [0, 1])
def test_failed_dependency_install_invalidates_previous_marker(ready, monkeypatch, failure_at):
    calls = []

    def run(args, **kwargs):
        assert not (container.STATE / "dependencies.json").exists()
        with (
            pytest.raises(OdxError, match="Resource busy"),
            locked(
                container.STATE / "environment.lock",
                shared=True,
            ),
        ):
            pytest.fail("Installation must hold the exclusive environment lock")
        calls.append(args)
        if len(calls) - 1 == failure_at:
            raise OdxError("dependency install failed")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(container, "run_command", run)
    with pytest.raises(OdxError, match="dependency install failed"):
        container.install_dependencies()
    assert len(calls) == failure_at + 1
    assert not (container.STATE / "dependencies.json").exists()
    with pytest.raises(OdxError, match="dependencies are not installed"):
        container.require_ready()


def test_interrupted_dependency_install_invalidates_marker(ready, monkeypatch):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(container, "run_command", interrupt)
    with pytest.raises(KeyboardInterrupt):
        container.install_dependencies()
    assert not (container.STATE / "dependencies.json").exists()


def test_dependency_install_writes_marker_only_after_success(ready, monkeypatch):
    calls = []

    def run(args, **kwargs):
        assert not (container.STATE / "dependencies.json").exists()
        calls.append((args, kwargs))

    monkeypatch.setattr(container, "run_command", run)
    assert container.install_dependencies() == 0
    assert calls == [
        (
            [
                "uv",
                "pip",
                "install",
                "--python",
                container.PYTHON,
                "--constraint",
                "/opt/odoo/requirements.txt",
                "setuptools<81",
                "--requirement",
                str(ready.requirement),
            ],
            {},
        ),
        (["uv", "pip", "check", "--python", container.PYTHON], {}),
    ]
    assert json.loads((container.STATE / "dependencies.json").read_text()) == {
        "fingerprint": container.dependency_fingerprint(),
    }
    assert container.require_ready() is None


def test_dependency_install_creates_missing_venv(isolated_container, monkeypatch):
    calls = []
    monkeypatch.setattr(container, "run_command", lambda args, **kwargs: calls.append(args))
    assert container.install_dependencies() == 0
    assert calls[0] == ["uv", "venv", "--python", "/usr/local/bin/python3", "/opt/venv"]
    assert calls[-1] == ["uv", "pip", "check", "--python", container.PYTHON]


def test_dependency_install_busy_lock_preserves_existing_marker(ready):
    marker = container.STATE / "dependencies.json"
    before = marker.read_bytes()
    with locked(container.STATE / "environment.lock", shared=True):
        with pytest.raises(OdxError, match="Resource busy"):
            container.install_dependencies()
    assert marker.read_bytes() == before


@pytest.mark.parametrize(
    "database",
    [
        "dev",
        "postgres",
        "customer",
        "odx_test_",
        "odx_test_" + "a" * 31,
        "odx_test_" + "A" * 32,
        "odx_test_" + "a" * 32 + "; DROP DATABASE dev",
        "odx_test_" + "a" * 32 + "\n",
    ],
)
def test_database_cleanup_refuses_arbitrary_database(database):
    with pytest.raises(OdxError, match="outside the generated test namespace"):
        container._database_cleanup(database)


def test_database_cleanup_uses_linux_python_and_identifier_argument(monkeypatch):
    calls = []
    monkeypatch.setattr(
        container,
        "run_command",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )
    database = "odx_test_" + "a" * 32
    container._database_cleanup(database)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == [container.PYTHON, "-c"]
    assert args[3:] == [database]
    assert "sql.Identifier(sys.argv[1])" in args[2]
    assert database not in args[2]
    assert kwargs == {}


def test_run_tests_rejects_invalid_modules_before_creating_artifacts():
    with pytest.raises(OdxError, match="technical names"):
        container.run_tests("sale;id")
    assert not (container.STATE / "tests").exists()


def test_run_tests_requires_current_dependencies(isolated_container, monkeypatch):
    monkeypatch.setattr(container, "test_port", lambda: nullcontext(45678))
    with pytest.raises(OdxError, match="dependencies are not installed"):
        container.run_tests("sale")


def test_run_tests_refuses_exclusive_environment_operation(ready):
    with locked(container.STATE / "environment.lock"):
        with pytest.raises(OdxError, match="Resource busy"):
            container.run_tests("sale")


@pytest.mark.parametrize("code,status", [(0, "passed"), (8, "failed")])
def test_run_tests_isolates_resources_retains_artifacts_and_returns_code(
    ready,
    test_process,
    monkeypatch,
    capsys,
    code,
    status,
):
    calls, processes = test_process(code=code)
    cleanup = []

    def run(args, **kwargs):
        with (
            pytest.raises(OdxError, match="Resource busy"),
            locked(
                container.STATE / "environment.lock",
            ),
        ):
            pytest.fail("Test cleanup must remain under the environment lock")
        cleanup.append(args)

    monkeypatch.setattr(container, "run_command", run)
    assert container.run_tests("sale,stock", tags="/sale:TestSale") == code
    directories = list((container.STATE / "tests").iterdir())
    assert len(directories) == 1
    directory = directories[0]
    assert len(directory.name) == 32
    assert all(char in "0123456789abcdef" for char in directory.name)
    database = f"odx_test_{directory.name}"
    args, options = calls[0]
    assert args == [
        container.PYTHON,
        container.ODOO,
        "--config",
        container.CONFIG,
        "--database",
        database,
        "--db-filter",
        f"^{database}$",
        "--init",
        "sale,stock",
        "--test-enable",
        "--stop-after-init",
        "--workers=0",
        "--max-cron-threads=0",
        "--http-port",
        "45678",
        "--http-interface",
        "127.0.0.1",
        "--data-dir",
        str(directory / "data"),
        "--test-tags",
        "/sale:TestSale",
    ]
    assert options == {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "start_new_session": True,
    }
    assert len(cleanup) == 1
    assert cleanup[0][-1] == database
    result = json.loads((directory / "result.json").read_text())
    assert result["id"] == directory.name
    assert result["database"] == database
    assert result["status"] == status
    assert result["exit_code"] == code
    assert result["tests"] == 3
    assert result["failures"] == 0
    assert result["errors"] == 0
    assert result["kept"] is False
    assert result["cleaned"] is True
    assert not (directory / "data").exists()
    assert (directory / "odoo.log").read_text() == SUCCESS_LOG
    assert processes[0].stdout.closed
    output = capsys.readouterr().out
    assert "Test run:" in output
    assert "test output" in output


@pytest.mark.parametrize("code", [0, 8])
def test_keep_preserves_database_and_filestore_without_cleanup(ready, test_process, code):
    test_process(code=code)
    assert container.run_tests("sale", keep=True) == code
    (directory,) = (container.STATE / "tests").iterdir()
    assert (directory / "data" / "filestore").read_text() == "test-only data"
    result = json.loads((directory / "result.json").read_text())
    assert result["kept"] is True
    assert not result.get("cleaned", False)
    assert result["exit_code"] == code
    assert (directory / "odoo.log").is_file()


def test_interrupted_tests_terminate_process_group_cleanup_and_keep_artifacts(
    ready,
    test_process,
    monkeypatch,
):
    _, processes = test_process(wait_error=KeyboardInterrupt())
    killed = []
    cleanup = []
    monkeypatch.setattr(container.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(container, "run_command", lambda args, **kwargs: cleanup.append(args))
    with pytest.raises(KeyboardInterrupt):
        container.run_tests("sale")
    (directory,) = (container.STATE / "tests").iterdir()
    assert killed == [(12345, signal.SIGTERM)]
    assert processes[0].waits == [None, 15]
    assert processes[0].stdout.closed
    assert cleanup[0][-1] == f"odx_test_{directory.name}"
    assert not (directory / "data").exists()
    result = json.loads((directory / "result.json").read_text())
    assert result["status"] == "interrupted"
    assert result["exit_code"] == 130
    assert result["cleaned"] is True
    assert (directory / "odoo.log").is_file()


def test_interrupted_tests_with_keep_do_not_cleanup(ready, test_process, monkeypatch):
    test_process(wait_error=KeyboardInterrupt())
    monkeypatch.setattr(container.os, "killpg", lambda *args: None)
    with pytest.raises(KeyboardInterrupt):
        container.run_tests("sale", keep=True)
    (directory,) = (container.STATE / "tests").iterdir()
    assert (directory / "data" / "filestore").exists()
    result = json.loads((directory / "result.json").read_text())
    assert result["status"] == "interrupted"
    assert result["kept"] is True
    assert not result.get("cleaned", False)


@pytest.mark.parametrize("process_code,expected_code", [(0, 1), (8, 8)])
def test_cleanup_failure_returns_nonzero_and_records_diagnostic(
    ready,
    test_process,
    monkeypatch,
    process_code,
    expected_code,
):
    test_process(code=process_code)

    def failed_cleanup(*args, **kwargs):
        raise OdxError("cleanup unavailable")

    monkeypatch.setattr(container, "run_command", failed_cleanup)
    assert container.run_tests("sale") == expected_code
    (directory,) = (container.STATE / "tests").iterdir()
    result = json.loads((directory / "result.json").read_text())
    assert result["cleanup_error"] == "cleanup unavailable"
    assert result["exit_code"] == expected_code
    assert result["status"] == "cleanup_failed"
    assert not result.get("cleaned", False)
    assert (directory / "odoo.log").is_file()


def test_filestore_cleanup_error_is_recorded_without_losing_test_artifacts(
    ready,
    test_process,
    monkeypatch,
):
    test_process()
    cleanup = []
    monkeypatch.setattr(container, "run_command", lambda args, **kwargs: cleanup.append(args))

    def denied(path):
        assert path.name == "data"
        assert path.parent.parent == container.STATE / "tests"
        raise PermissionError("filestore permission denied")

    monkeypatch.setattr(container.shutil, "rmtree", denied)
    assert container.run_tests("sale") == 1
    (directory,) = (container.STATE / "tests").iterdir()
    assert cleanup[0][-1] == f"odx_test_{directory.name}"
    result = json.loads((directory / "result.json").read_text())
    assert result["status"] == "cleanup_failed"
    assert result["exit_code"] == 1
    assert "filestore permission denied" in result["cleanup_error"]
    assert not result.get("cleaned", False)
    assert (directory / "odoo.log").is_file()
    assert (directory / "data" / "filestore").is_file()


@pytest.mark.parametrize(
    "output,tests,failures,errors",
    [
        ("Odoo exited without running tests\n", 0, 0, 0),
        ("0 failed, 0 error(s) of 0 tests\n", 0, 0, 0),
        ("1 failed, 0 error(s) of 3 tests\n", 3, 1, 0),
        ("0 failed, 1 error(s) of 3 tests\n", 3, 0, 1),
    ],
)
def test_zero_process_exit_does_not_hide_missing_or_failing_tests(
    ready,
    test_process,
    monkeypatch,
    output,
    tests,
    failures,
    errors,
):
    test_process(code=0, output=output)
    cleanup = []
    monkeypatch.setattr(container, "run_command", lambda args, **kwargs: cleanup.append(args))
    assert container.run_tests("sale") == 1
    (directory,) = (container.STATE / "tests").iterdir()
    result = json.loads((directory / "result.json").read_text())
    assert result["status"] == "failed"
    assert result["exit_code"] == 1
    assert result["tests"] == tests
    assert result["failures"] == failures
    assert result["errors"] == errors
    assert result["cleaned"] is True
    assert cleanup[0][-1] == f"odx_test_{directory.name}"
    assert (directory / "odoo.log").read_text() == output


def test_final_test_summary_controls_result(ready, test_process):
    test_process(output=("0 failed, 0 error(s) of 1 tests\n1 failed, 0 error(s) of 3 tests\n"))
    assert container.run_tests("sale", keep=True) == 1
    (directory,) = (container.STATE / "tests").iterdir()
    result = json.loads((directory / "result.json").read_text())
    assert result["tests"] == 3
    assert result["failures"] == 1
    assert result["status"] == "failed"


def test_process_start_failure_still_attempts_cleanup_and_records_result(
    ready,
    monkeypatch,
):
    cleanup = []
    monkeypatch.setattr(container, "test_port", lambda: nullcontext(45678))
    monkeypatch.setattr(container, "run_command", lambda args, **kwargs: cleanup.append(args))

    def failed_start(*args, **kwargs):
        raise OSError("unable to launch Odoo")

    monkeypatch.setattr(container.subprocess, "Popen", failed_start)
    with pytest.raises(OSError, match="unable to launch Odoo"):
        container.run_tests("sale")
    (directory,) = (container.STATE / "tests").iterdir()
    assert cleanup[0][-1] == f"odx_test_{directory.name}"
    result = json.loads((directory / "result.json").read_text())
    assert result["status"] != "passed"
    assert result["cleaned"] is True


def test_test_process_escalates_interrupt_to_sigkill_when_termination_times_out(
    tmp_path,
    monkeypatch,
):
    process = SimpleNamespace(stdout=io.StringIO("last output\n"), pid=12345)
    waits = []

    def wait(timeout=None):
        waits.append(timeout)
        if len(waits) == 1:
            raise KeyboardInterrupt
        if len(waits) == 2:
            raise subprocess.TimeoutExpired("fake Odoo", timeout)
        return -signal.SIGKILL

    process.wait = wait
    monkeypatch.setattr(container.subprocess, "Popen", lambda *args, **kwargs: process)
    killed = []
    monkeypatch.setattr(container.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    logfile = tmp_path / "odoo.log"
    with pytest.raises(KeyboardInterrupt):
        container._test_process([container.PYTHON, container.ODOO], logfile)
    assert killed == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
    assert waits == [None, 15, None]
    assert process.stdout.closed
    assert logfile.read_text() == "last output\n"


def test_test_runs_never_reuse_database_or_data_directory(ready, test_process, monkeypatch):
    calls, _ = test_process()
    monkeypatch.setattr(container, "run_command", lambda *args, **kwargs: None)
    assert container.run_tests("sale") == 0
    assert container.run_tests("sale") == 0
    assert (
        calls[0][0][calls[0][0].index("--database") + 1]
        != (calls[1][0][calls[1][0].index("--database") + 1])
    )
    assert (
        calls[0][0][calls[0][0].index("--data-dir") + 1]
        != (calls[1][0][calls[1][0].index("--data-dir") + 1])
    )


def test_serve_opencode_requires_dependencies(isolated_container):
    with pytest.raises(OdxError, match="dependencies are not installed"):
        container.serve_opencode()


def test_serve_opencode_execs_only_its_own_foreground_server(ready, monkeypatch):
    calls = []
    monkeypatch.setattr(container.os, "execvp", lambda *args: calls.append(args))
    container.serve_opencode()
    assert calls == [
        (
            "opencode",
            [
                "opencode",
                "serve",
                "--hostname",
                "0.0.0.0",
                "--port",
                "4096",
            ],
        )
    ]
