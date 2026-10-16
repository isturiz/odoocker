"""OpenCode connection contracts; never contact the host's shared service."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from odoocker.adapters import opencode
from odoocker.errors import OdxError


@pytest.fixture(autouse=True)
def no_external_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unit tests must not invoke the real OpenCode client")

    monkeypatch.setattr(opencode, "run_command", forbidden)


@pytest.fixture
def runtime(tmp_path):
    calls = []

    def compose(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="2.0.30\n", returncode=0)

    def url(service, port):
        assert (service, port) == ("opencode", 4096)
        return "http://127.0.0.1:49152"

    return SimpleNamespace(
        workspace=SimpleNamespace(root=tmp_path, opencode_version="2.0.30"),
        password="test-only-private-password",
        project="odx-test-feature",
        compose=compose,
        compose_calls=calls,
        url=url,
    )


@pytest.mark.parametrize(
    "output,expected",
    [
        ("2.0.30\n", "2.0.30"),
        ("opencode 2.0.30\n", "2.0.30"),
        ("v2.0.30", "2.0.30"),
        ("opencode v2.0.30", "2.0.30"),
        ("2.1.0-beta.1", "2.1.0-beta.1"),
    ],
)
def test_host_version_parses_exact_v2_release(monkeypatch, output, expected):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(opencode, "run_command", run)
    assert opencode.host_version() == expected
    assert calls == [(["opencode", "--version"], {"capture": True})]


@pytest.mark.parametrize(
    "output",
    [
        "",
        "1.2.3",
        "12.0.30",
        "2.0",
        "2.0.30 extra",
        "prefix 2.0.30",
        "2.0.30\n2.0.31",
        "opencode 1.0.30",
        "latest",
        "2.0.30+build",
    ],
)
def test_host_version_rejects_nonexact_output(monkeypatch, output):
    monkeypatch.setattr(
        opencode,
        "run_command",
        lambda *args, **kwargs: SimpleNamespace(stdout=output),
    )
    with pytest.raises(OdxError, match="OpenCode V2 is required"):
        opencode.host_version()


def test_host_version_propagates_missing_executable(monkeypatch):
    def missing(*args, **kwargs):
        raise OdxError("Executable not found: opencode")

    monkeypatch.setattr(opencode, "run_command", missing)
    with pytest.raises(OdxError, match="Executable not found: opencode"):
        opencode.host_version()


def test_verify_checks_workspace_pin_before_container(runtime, monkeypatch):
    monkeypatch.setattr(opencode, "host_version", lambda: "2.0.31")
    with pytest.raises(OdxError, match="Host OpenCode is 2.0.31, workspace pins 2.0.30"):
        opencode.verify(runtime)
    assert runtime.compose_calls == []


def test_verify_checks_scoped_container_version(runtime, monkeypatch):
    monkeypatch.setattr(opencode, "host_version", lambda: "2.0.30")
    assert opencode.verify(runtime) is None
    assert runtime.compose_calls == [
        (
            ("exec", "-T", "opencode", "opencode", "--version"),
            {"capture": True},
        )
    ]


@pytest.mark.parametrize("output", ["2.0.31\n", "1.0.30\n", "12.0.30\n", "", "2.0.30-beta.1\n"])
def test_verify_rejects_container_mismatch(runtime, monkeypatch, output):
    monkeypatch.setattr(opencode, "host_version", lambda: "2.0.30")
    runtime.compose = lambda *args, **kwargs: SimpleNamespace(stdout=output)
    with pytest.raises(OdxError, match="container version does not match"):
        opencode.verify(runtime)


def test_authentication_copies_environment_without_mutating_host(runtime, monkeypatch):
    monkeypatch.setenv("OPENCODE_PASSWORD", "unrelated-host-password")
    monkeypatch.setenv("ODOOCKER_TEST_PRESERVED", "yes")
    environment = opencode.authentication(runtime)
    assert environment["OPENCODE_PASSWORD"] == runtime.password
    assert environment["ODOOCKER_TEST_PRESERVED"] == "yes"
    assert opencode.os.environ["OPENCODE_PASSWORD"] == "unrelated-host-password"


@pytest.mark.parametrize("custom_xdg", [False, True])
def test_connect_uses_authenticated_explicit_server_and_isolated_client_state(
    runtime,
    tmp_path,
    monkeypatch,
    custom_xdg,
):
    home = tmp_path / "host-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("OPENCODE_PASSWORD", "unrelated-host-password")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    roots = {}
    for key, fallback in (("XDG_DATA_HOME", ".local/share"), ("XDG_STATE_HOME", ".local/state")):
        if custom_xdg:
            roots[key] = tmp_path / key.lower()
            monkeypatch.setenv(key, str(roots[key]))
        else:
            roots[key] = home / fallback
            monkeypatch.delenv(key, raising=False)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args == ["opencode", "--version"]:
            return SimpleNamespace(stdout="2.0.30\n")
        return SimpleNamespace(returncode=27)

    monkeypatch.setattr(opencode, "run_command", run)
    assert opencode.connect(runtime, ["--model", "example/model"]) == 27
    assert len(calls) == 2
    args, options = calls[-1]
    assert args == [
        "opencode",
        "--server",
        "http://127.0.0.1:49152",
        "--model",
        "example/model",
    ]
    assert runtime.password not in " ".join(args)
    assert options["env"]["OPENCODE_PASSWORD"] == runtime.password
    assert options["env"]["XDG_CONFIG_HOME"] == str(tmp_path / "config")
    for key, root in roots.items():
        assert options["env"][key] == str(root / "odoocker-clients" / runtime.project)
        assert opencode.os.environ.get(key) == (str(root) if custom_xdg else None)
    assert options["check"] is False
    assert options["cwd"] == runtime.workspace.root
    assert opencode.os.environ["OPENCODE_PASSWORD"] == "unrelated-host-password"
    assert not home.exists()


def test_connect_isolates_different_instance_state(runtime, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(opencode, "verify", lambda runtime: None)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    def run(args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(opencode, "run_command", run)
    assert opencode.connect(runtime, []) == 0
    runtime.project = "odx-test-other"
    assert opencode.connect(runtime, []) == 0
    for key in ("XDG_DATA_HOME", "XDG_STATE_HOME"):
        assert calls[0]["env"][key] != calls[1]["env"][key]


def test_info_uses_authenticated_api_without_secret_arguments(runtime, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args == ["opencode", "--version"]:
            return SimpleNamespace(stdout="2.0.30")
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr(opencode, "run_command", run)
    assert opencode.info(runtime) == 9
    args, options = calls[-1]
    assert args == [
        "opencode",
        "api",
        "--server",
        "http://127.0.0.1:49152",
        "get",
        "/api/info",
    ]
    assert runtime.password not in " ".join(args)
    assert options["env"]["OPENCODE_PASSWORD"] == runtime.password
    assert options["check"] is False


@pytest.mark.parametrize("operation", ["connect", "info"])
def test_version_mismatch_never_starts_client(runtime, monkeypatch, operation):
    monkeypatch.setattr(opencode, "host_version", lambda: "2.0.31")
    with pytest.raises(OdxError, match="workspace pins"):
        if operation == "connect":
            opencode.connect(runtime, [])
        else:
            opencode.info(runtime)
    assert runtime.compose_calls == []
