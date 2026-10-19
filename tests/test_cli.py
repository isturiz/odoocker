"""CLI contracts, with host processes and Docker replaced by test doubles."""

import json
from types import SimpleNamespace

import pytest

from odoocker import cli
from odoocker import container as container_module
from odoocker import runtime as runtime_module
from odoocker.config import LOCAL, MANIFEST, Workspace
from odoocker.errors import OdxError
from odoocker.locking import locked
from odoocker.runtime import Runtime


@pytest.fixture(autouse=True)
def no_external_processes(monkeypatch, tmp_path):
    monkeypatch.delenv("ODOOCKER_CONTAINER", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "host-data"))

    def forbidden(*args, **kwargs):
        pytest.fail("Unit tests must not invoke an external process")

    monkeypatch.setattr(runtime_module.subprocess, "run", forbidden)


@pytest.fixture
def host_runtime(tmp_path):
    root = tmp_path / "workspace"
    args = cli.parser().parse_args(["init", str(root), "--opencode-version", "2.0.30"])
    cli.init_workspace(args)
    workspace = Workspace.load(root)
    (workspace.source / "odoo").mkdir(parents=True)
    (workspace.source / "odoo-bin").write_text("# fake Odoo\n")
    (workspace.source / "odoo" / "release.py").write_text("version_info = (19, 0, 0)\n")
    runtime = Runtime.load(workspace, "feature")
    runtime.generate()
    return runtime


@pytest.fixture
def docker_calls(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=7, stdout="[]")

    monkeypatch.setattr(runtime_module, "run_command", fake_run)
    return calls


def host_args(runtime, *args):
    return ["--workspace", str(runtime.workspace.root), "--instance", runtime.instance, *args]


def assert_scoped_call(runtime, call, command, *, check=False):
    args, options = call
    assert args == [
        "docker",
        "compose",
        "--project-directory",
        str(runtime.directory),
        "--env-file",
        str(runtime.directory / "compose.env"),
        "--project-name",
        runtime.project,
        "--file",
        str(runtime.compose_file),
        *command,
    ]
    assert options["check"] is check


def test_only_explicit_opencode_assistant_is_accepted():
    args = cli.parser().parse_args(["code", "opencode", "--", "--model", "example/model"])
    assert args.command == "code"
    assert args.assistant == "opencode"
    assert args.args == ["--", "--model", "example/model"]


@pytest.mark.parametrize("action", ["up", "down", "setup"])
def test_cli_refreshes_proxy_after_managed_lifecycle(host_runtime, monkeypatch, action):
    from odoocker import proxy, setup

    calls = []
    monkeypatch.setattr(proxy, "sync", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(Runtime, "compose", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="[]"
    ))
    monkeypatch.setattr(setup, "prepare", lambda *a, **kw: None)
    assert cli.main(host_args(host_runtime, action)) == 0
    assert len(calls) == 1
    assert calls[0][0][0].project == host_runtime.project
    assert calls[0][1] == ({"remove": True} if action == "down" else {})


@pytest.mark.parametrize("argv", [["code"], ["code", "claude"], ["code", "codex"], ["opencode"]])
def test_missing_or_unsupported_assistant_is_an_argparse_error(argv, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.parser().parse_args(argv)
    assert caught.value.code == 2
    assert "usage: odx" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["17.0", "19.0"])
def test_init_explicit_version_needs_no_host_opencode(tmp_path, version):
    root = tmp_path / "new-workspace"
    assert (
        cli.main(
            [
                "init",
                str(root),
                "--odoo",
                version,
                "--opencode-version",
                "2.0.30",
            ]
        )
        == 0
    )
    workspace = Workspace.load(root)
    assert workspace.name == "new-workspace"
    assert workspace.odoo_version == version
    assert workspace.opencode_version == "2.0.30"
    assert not (root / LOCAL).exists()
    assert not (root / ".odoocker").exists()
    assert ".odoocker/" in (root / ".gitignore").read_text()
    assert "odx test --module" in (root / "AGENTS.md").read_text()


def test_init_nonempty_directory_preserves_every_existing_file(tmp_path, capsys):
    root = tmp_path / "workspace"
    root.mkdir()
    existing = root / "existing.txt"
    existing.write_bytes(b"user content\x00")
    assert cli.main(["init", str(root), "--opencode-version", "2.0.30"]) == 1
    assert "non-empty" in capsys.readouterr().err
    assert existing.read_bytes() == b"user content\x00"
    assert list(root.iterdir()) == [existing]


@pytest.mark.parametrize("name", ["BadName", "../escape", "name with spaces", "a" * 49])
def test_init_invalid_name_does_not_create_directory(tmp_path, name, capsys):
    root = tmp_path / "new-workspace"
    assert (
        cli.main(
            [
                "init",
                str(root),
                "--name",
                name,
                "--opencode-version",
                "2.0.30",
            ]
        )
        == 1
    )
    assert "workspace name must match" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.parametrize("version", ["1.2.3", "2", "2.0", "latest", "2.0.30\n", "v2.0.30"])
def test_init_invalid_version_writes_nothing(tmp_path, version, capsys):
    root = tmp_path / "workspace"
    assert cli.main(["init", str(root), "--opencode-version", version]) == 1
    assert "exact OpenCode V2 release" in capsys.readouterr().err
    assert not root.exists()


def test_init_invalid_explicit_source_writes_nothing(tmp_path, capsys):
    root = tmp_path / "workspace"
    assert (
        cli.main(
            [
                "init",
                str(root),
                "--opencode-version",
                "2.0.30",
                "--source",
                str(tmp_path / "missing"),
            ]
        )
        == 1
    )
    assert "Odoo source missing" in capsys.readouterr().err
    assert not root.exists()


@pytest.mark.parametrize("marker,expected", [(None, "host"), ("0", "host"), ("1", "container")])
def test_main_routes_only_exact_container_marker(monkeypatch, marker, expected):
    if marker is not None:
        monkeypatch.setenv("ODOOCKER_CONTAINER", marker)
    calls = []

    def host(args):
        calls.append(("host", args.command))
        return 17

    def inside(args):
        calls.append(("container", args.command))
        return 17

    monkeypatch.setattr(cli, "host", host)
    monkeypatch.setattr(cli, "in_container", inside)
    assert cli.main(["status"]) == 17
    assert calls == [(expected, "status")]


@pytest.mark.parametrize(
    "error,code,text",
    [
        (OdxError("safe diagnostic"), 1, "odx: safe diagnostic"),
        (KeyboardInterrupt(), 130, "odx: interrupted"),
    ],
)
def test_main_reports_expected_errors_without_tracebacks(monkeypatch, capsys, error, code, text):
    def fail(args):
        raise error

    monkeypatch.setattr(cli, "host", fail)
    assert cli.main(["status"]) == code
    captured = capsys.readouterr()
    assert text in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["--workspace", "/other", "status"],
        ["--instance", "other", "deps"],
    ],
)
def test_container_rejects_host_context_selection(monkeypatch, capsys, argv):
    monkeypatch.setenv("ODOOCKER_CONTAINER", "1")
    assert cli.main(argv) == 1
    assert "Container context is fixed" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["configure", "down", "fetch", "code opencode"])
def test_container_rejects_host_commands(monkeypatch, capsys, command):
    monkeypatch.setenv("ODOOCKER_CONTAINER", "1")
    assert cli.main(command.split()) == 1
    assert "host command" in capsys.readouterr().err


def test_database_switch_requires_stopped_services(host_runtime, monkeypatch, capsys):
    calls = []

    def compose(self, *args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="running-container\n")

    monkeypatch.setattr(Runtime, "compose", compose)
    before = host_runtime.compose_file.read_bytes()
    assert (
        cli.main(
            [
                "--workspace",
                str(host_runtime.workspace.root),
                "--instance",
                host_runtime.instance,
                "configure",
                "--allow-database-switch",
            ]
        )
        == 1
    )
    assert "odx down" in capsys.readouterr().err
    assert calls == [("ps", "--status", "running", "--quiet")]
    assert host_runtime.compose_file.read_bytes() == before


@pytest.mark.parametrize(
    "argv,function,expected_args,expected_kwargs",
    [
        (["deps"], "install_dependencies", (), {}),
        (
            ["run", "--init", "sale", "--update", "base", "--stop-after-init"],
            "run_odoo",
            (),
            {"install": "sale", "update": "base", "stop": True},
        ),
        (
            ["test", "--module", "sale", "--tags", "/sale", "--keep"],
            "run_tests",
            ("sale",),
            {"tags": "/sale", "keep": True},
        ),
        (["_serve"], "serve_opencode", (), {}),
    ],
)
def test_container_dispatch_preserves_exit_code(
    monkeypatch,
    argv,
    function,
    expected_args,
    expected_kwargs,
):
    monkeypatch.setenv("ODOOCKER_CONTAINER", "1")
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return 23

    monkeypatch.setattr(container_module, function, fake)
    assert cli.main(argv) == 23
    assert calls == [(expected_args, expected_kwargs)]


def test_container_status_uses_context_without_docker(monkeypatch, capsys):
    monkeypatch.setenv("ODOOCKER_CONTAINER", "1")
    monkeypatch.setattr(container_module, "context", lambda: {"instance": "isolated"})
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"instance": "isolated"}


@pytest.mark.parametrize("tags", ["/sale", "-standard,/sale:TestSale"])
def test_host_tests_use_scoped_oneoff_without_dependencies_or_ports(
    host_runtime, docker_calls, tags
):
    assert (
        cli.main(
            host_args(
                host_runtime,
                "test",
                "--module",
                "sale,stock",
                f"--tags={tags}",
                "--keep",
            )
        )
        == 7
    )
    assert len(docker_calls) == 1
    assert_scoped_call(
        host_runtime,
        docker_calls[0],
        [
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "odoo",
            "odx",
            "test",
            "--module",
            "sale,stock",
            f"--tags={tags}",
            "--keep",
        ],
    )
    assert "--service-ports" not in docker_calls[0][0]


def test_host_deps_preserves_exit_code(host_runtime, docker_calls):
    assert cli.main(host_args(host_runtime, "deps")) == 7
    assert_scoped_call(host_runtime, docker_calls[0], ["exec", "-T", "odoo", "odx", "deps"])


@pytest.mark.parametrize(
    "argv",
    [
        ["db", "list", "--json"],
        ["db", "drop", "sample-19", "--dry-run"],
        ["db", "drop", "sample", "--yes"],
        ["passwd", "--password-stdin"],
        ["passwd", "--login", "renamed-admin"],
    ],
)
@pytest.mark.parametrize("tty", [False, True])
def test_host_database_commands_preserve_context_and_stdin(
    host_runtime,
    docker_calls,
    monkeypatch,
    argv,
    tty,
):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: tty)
    assert cli.main(host_args(host_runtime, *argv)) == 7
    assert_scoped_call(
        host_runtime, docker_calls[0], ["exec", *([] if tty else ["-T"]), "odoo", "odx", *argv]
    )


@pytest.mark.parametrize(
    "service,program",
    [
        ("odoo", ["odoo", "odx", "_shell"]),
        ("opencode", ["opencode", "odx", "_shell"]),
        ("pg", ["--user", "postgres", "db", "sh"]),
    ],
)
@pytest.mark.parametrize("tty", [False, True])
def test_shell_selects_only_requested_service_and_tty(
    host_runtime,
    docker_calls,
    monkeypatch,
    service,
    program,
    tty,
):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: tty)
    assert cli.main(host_args(host_runtime, "sh", service)) == 7
    assert_scoped_call(host_runtime, docker_calls[0], ["exec", *([] if tty else ["-T"]), *program])


def test_psql_targets_this_instances_dev(host_runtime, docker_calls, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(host_args(host_runtime, "sh", "pg", "--psql")) == 7
    assert_scoped_call(
        host_runtime,
        docker_calls[0],
        ["exec", "-T", "--user", "postgres", "db", "psql", "-X", "-U", "odoo", "--dbname=dev"],
    )


@pytest.mark.parametrize("service", ["odoo", "opencode"])
def test_psql_flag_rejected_for_other_services(host_runtime, docker_calls, service):
    assert cli.main(host_args(host_runtime, "sh", service, "--psql")) == 1
    assert not docker_calls


@pytest.mark.parametrize(
    "argv",
    [
        ["sh"],
        ["sh", "db"],
        ["db", "drop"],
        ["db", "drop", "--all"],
        ["passwd", "--password", "unsafe"],
        ["db", "drop", "a", "b"],
    ],
)
def test_maintenance_cli_rejects_implicit_targets_bulk_deletes_and_password_arguments(argv):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(argv)


@pytest.mark.parametrize("argv", [["sh", "pg"], ["db", "list"], ["passwd"]])
def test_maintenance_commands_hold_host_operation_lock(host_runtime, docker_calls, argv):
    with locked(host_runtime.operation_lock):
        assert cli.main(host_args(host_runtime, *argv)) == 1
    assert not docker_calls


@pytest.mark.parametrize(
    "argv",
    [
        ["db", "list"],
        ["db", "drop", "dev", "--yes"],
        ["passwd", "--login", "renamed-admin", "--password-stdin"],
    ],
)
def test_container_database_dispatch_without_docker(monkeypatch, argv):
    monkeypatch.setenv("ODOOCKER_CONTAINER", "1")
    calls = []
    monkeypatch.setattr(container_module, "database_command", lambda args: calls.append(args) or 23)
    assert cli.main(argv) == 23
    assert container_module.database_arguments(calls[0]) == argv


@pytest.mark.parametrize("tty", [False, True])
def test_host_run_preserves_flags_and_tty(host_runtime, docker_calls, monkeypatch, tty):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: tty)
    assert (
        cli.main(
            host_args(
                host_runtime,
                "run",
                "--init",
                "sale",
                "--update",
                "base",
                "--stop-after-init",
            )
        )
        == 7
    )
    assert_scoped_call(
        host_runtime,
        docker_calls[0],
        [
            "exec",
            *([] if tty else ["-T"]),
            "odoo",
            "odx",
            "run",
            "--init",
            "sale",
            "--update",
            "base",
            "--stop-after-init",
        ],
    )


def test_down_keeps_volumes_and_is_project_scoped(host_runtime, docker_calls):
    assert cli.main(host_args(host_runtime, "down")) == 0
    assert len(docker_calls) == 1
    assert_scoped_call(host_runtime, docker_calls[0], ["--profile", "code", "down"], check=True)
    assert not {"-v", "--volumes", "--rmi"}.intersection(docker_calls[0][0])


def test_down_respects_operation_lock(host_runtime, docker_calls, capsys):
    with locked(host_runtime.operation_lock):
        assert cli.main(host_args(host_runtime, "down")) == 1
    assert "Resource busy" in capsys.readouterr().err
    assert docker_calls == []


@pytest.mark.parametrize("info", [False, True])
def test_host_opencode_dispatch_strips_separator(host_runtime, monkeypatch, info):
    calls = []

    def connect(runtime, args):
        calls.append((runtime, args))
        return 19

    def inspect(runtime):
        calls.append((runtime, None))
        return 19

    monkeypatch.setattr(cli.opencode, "connect", connect)
    monkeypatch.setattr(cli.opencode, "info", inspect)
    argv = (
        ["code", "opencode", "--info"]
        if info
        else [
            "code",
            "opencode",
            "--",
            "--model",
            "example/model",
        ]
    )
    assert cli.main(host_args(host_runtime, *argv)) == 19
    assert calls == [(host_runtime, None if info else ["--model", "example/model"])]


@pytest.mark.parametrize("test_id", ["../escape", "a" * 31, "A" * 32, "0" * 32 + "/file"])
def test_log_artifacts_reject_non_generated_ids(host_runtime, docker_calls, capsys, test_id):
    assert cli.main(host_args(host_runtime, "logs", "--test", test_id)) == 1
    assert "Test ID must be" in capsys.readouterr().err
    assert docker_calls == []


@pytest.mark.parametrize("result,filename", [(False, "odoo.log"), (True, "result.json")])
def test_log_artifact_paths_are_fixed(host_runtime, docker_calls, result, filename):
    run_id = "a" * 32
    argv = ["logs", "--test", run_id, *(["--result"] if result else [])]
    assert cli.main(host_args(host_runtime, *argv)) == 0
    assert_scoped_call(
        host_runtime,
        docker_calls[0],
        [
            "exec",
            "-T",
            "odoo",
            "cat",
            f"/var/lib/odoocker/tests/{run_id}/{filename}",
        ],
        check=True,
    )


def test_main_missing_workspace_is_a_user_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["status"]) == 1
    assert f"No {MANIFEST} found" in capsys.readouterr().err
