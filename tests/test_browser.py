"""Browser opt-in, ownership and adoption contracts; no Docker, launchd or browser."""

import json
import plistlib
import stat
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoocker import browser, browser_container, cli
from odoocker.browser_bridge.service import ENVIRONMENT, image_only_execute, proxy_arguments
from odoocker.config import MANIFEST, Workspace
from odoocker.errors import OdxError
from odoocker.runtime import Runtime, write_private

RPC_STATUS = browser.rpc_status
HEALTHY = browser.healthy

MANIFEST_TEXT = """schema = 1
[workspace]
name = "browser-fixture"
[odoo]
version = "19.0"
[opencode]
version = "2.0.30"
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.delenv("ODOOCKER_CONTAINER", raising=False)
    monkeypatch.setattr(browser.sys, "platform", "darwin")
    monkeypatch.setattr(browser.shutil, "which", lambda name: str(home / "bin/browse"))
    monkeypatch.setattr(
        browser, "rpc_status", lambda *a: {"status": {"connected": True, "sessions": []}}
    )

    def forbidden(*a, **kw):
        pytest.fail("Browser unit tests must not launch external processes")

    monkeypatch.setattr(browser, "run_command", forbidden)
    return home


@pytest.fixture
def processes(monkeypatch):
    state = {"running": set(), "calls": [], "fail": False}

    def run(args, **kwargs):
        state["calls"].append((args, kwargs))
        if args[:2] == ["launchctl", "print"]:
            return SimpleNamespace(returncode=0 if args[2] in state["running"] else 1)
        if args[:2] == ["launchctl", "bootstrap"]:
            label = plistlib.loads(Path(args[3]).read_bytes())["Label"]
            if state["fail"] and label == browser.LABEL:
                raise OdxError("test bootstrap failed")
            state["running"].add(browser.domain(label))
        if args[:2] == ["launchctl", "bootout"]:
            label = plistlib.loads(Path(args[3]).read_bytes())["Label"]
            state["running"].discard(browser.domain(label))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(browser, "run_command", run)
    monkeypatch.setattr(browser, "healthy", lambda *a: bool(state["running"]))
    return state


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / MANIFEST).write_text(MANIFEST_TEXT)
    source = root / ".odoocker/sources/odoo"
    (source / "odoo").mkdir(parents=True)
    (source / "odoo-bin").touch()
    (source / "odoo/release.py").write_text("version_info = (19, 0, 0)")
    return root


def enable(root):
    (root / MANIFEST).write_text(MANIFEST_TEXT + "browser = true\n")
    return Workspace.load(root)


def install_pilot(home, processes):
    root = home / ".local/share/odoocker/browser-bridge"
    file = browser.plist_path(browser.PILOT_LABEL)
    file.parent.mkdir(parents=True)
    file.write_bytes(
        plistlib.dumps(
            {
                "Label": browser.PILOT_LABEL,
                "ProgramArguments": [str(root / ".venv/bin/python"), str(root / "run.py")],
            }
        )
    )
    credential = home / ".config/odoocker/browser-bridge/realista.token"
    write_private(credential, "a" * 43)
    processes["running"].add(browser.domain(browser.PILOT_LABEL))
    return file, credential


def test_default_preserves_historical_document_and_no_host_dependency(root):
    workspace = Workspace.load(root)
    assert workspace.browser is False
    runtime = Runtime.load(workspace)
    runtime.generate()
    document = runtime.compose_document()
    assert "browser" not in json.dumps(document)
    assert not (runtime.directory / "browser-token").exists()
    historical = asdict(workspace)
    historical.pop("browser")
    historical.pop("skill_sources")
    import hashlib

    expected = hashlib.sha256(
        json.dumps(historical, default=str, sort_keys=True).encode()
    ).hexdigest()
    assert document["services"]["opencode"]["labels"]["io.odoocker.configuration"] == expected


@pytest.mark.parametrize("value", ['"true"', "1", "[]", "{}"])
def test_browser_flag_is_a_strict_boolean(root, value):
    (root / MANIFEST).write_text(MANIFEST_TEXT + f"browser = {value}\n")
    with pytest.raises(OdxError, match="browser must be a boolean"):
        Workspace.load(root)


def test_opt_in_generates_one_native_config_and_opencode_only_token(root, processes):
    browser.setup(port=19879)
    workspace = enable(root)
    runtime = Runtime.load(workspace)
    runtime.generate()
    native = json.loads((runtime.directory / "opencode-skills.json").read_text())
    server = native["mcp"]["servers"]["browse"]
    assert server["url"] == "http://host.docker.internal:19879/mcp"
    assert server["oauth"] is False
    assert server["headers"]["Authorization"] == "Bearer {file:/run/odoocker/browser-token}"
    assert browser.token() not in json.dumps(native)
    file = browser.directory() / "token"
    assert file.read_text() == browser.token()
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    document = runtime.compose_document()
    for name, service in document["services"].items():
        if name == "opencode":
            assert any(
                m["target"] == "/run/odoocker/browser-token" and m["read_only"]
                for m in service["volumes"]
            )
        else:
            assert "/run/odoocker/browser-token" not in json.dumps(service)
    assert "docker.sock" not in json.dumps(document)


def test_opt_in_shares_native_config_with_skills(root, processes):
    browser.setup(port=19879)
    workspace = enable(root)
    from odoocker.skills import SkillSource

    skills = root / "skills"
    skills.mkdir()
    (skills / "SKILL.md").write_text("---\ndescription: Test\n---\n")
    runtime = Runtime.load(replace(workspace, skill_sources=(SkillSource(skills, "review"),)))
    runtime.generate()
    native = json.loads((runtime.directory / "opencode-skills.json").read_text())
    assert native["skills"] == ["/mnt/odoocker/skills/0"]
    assert native["mcp"]["servers"]["browse"]["type"] == "remote"
    mounts = runtime.compose_document()["services"]["opencode"]["volumes"]
    assert sum(m["target"] == "/etc/opencode/opencode.json" for m in mounts) == 1


def test_enabled_workspace_requires_explicit_host_preparation(root):
    runtime = Runtime.load(Workspace.load(root))
    runtime.generate()
    original = (runtime.directory / "odoo.conf").read_bytes()
    with pytest.raises(OdxError, match="needs odx browser setup"):
        Runtime.load(enable(root)).generate()
    assert (runtime.directory / "odoo.conf").read_bytes() == original


def test_disabling_preserves_skills_and_never_deletes_credentials(root, processes):
    from odoocker.skills import SkillSource

    browser.setup(port=19879)
    skills = root / "skills"
    skills.mkdir()
    (skills / "SKILL.md").write_text("---\ndescription: Test\n---\n")
    workspace = replace(enable(root), skill_sources=(SkillSource(skills, "review"),))
    runtime = Runtime.load(workspace)
    runtime.generate()
    secret = (browser.directory() / "token").read_bytes()
    disabled = replace(runtime, workspace=replace(workspace, browser=False))
    disabled.generate()
    native = json.loads((runtime.directory / "opencode-skills.json").read_text())
    assert native == {
        "$schema": "https://opencode.ai/config.json",
        "skills": ["/mnt/odoocker/skills/0"],
    }
    assert (browser.directory() / "token").read_bytes() == secret
    assert not any(
        m["target"] == "/run/odoocker/browser-token"
        for m in disabled.compose_document()["services"]["opencode"]["volumes"]
    )


def test_unsupported_platform_has_no_process_side_effects(monkeypatch):
    monkeypatch.setattr(browser.sys, "platform", "linux")
    with pytest.raises(OdxError, match="macOS"):
        browser.setup()
    assert not browser.directory().exists()


def test_host_status_is_read_only_and_works_without_a_workspace(capsys):
    assert cli.main(["browser", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"supported": True, "installed": False}
    assert not browser.directory().exists()


def test_setup_is_idempotent_locked_and_keeps_credentials(processes):
    browser.setup(port=19879)
    before = browser.token()
    first = browser.load()
    calls = list(processes["calls"])
    browser.setup()
    assert browser.token() == before
    assert browser.load() == first
    assert sum(args[:2] == ["uv", "sync"] for args, kw in processes["calls"]) == 1
    assert any("--frozen" in args and "--python" in args for args, kw in calls)
    agent = plistlib.loads(browser.plist_path().read_bytes())
    assert agent["KeepAlive"] and agent["RunAtLoad"]
    assert "MCP_PROXY_AUTH_TOKEN" not in agent["EnvironmentVariables"]
    assert before not in browser.plist_path().read_text()


def test_stop_and_explicit_start_preserve_native_browse(processes):
    browser.setup(port=19879)
    value = browser.token()
    browser.stop()
    assert browser.load()["enabled"] is False
    with pytest.raises(OdxError, match="browser start"):
        browser.ensure()
    browser.start()
    assert browser.load()["enabled"] is True
    assert browser.token() == value
    assert not any(args[0] == "browse" for args, kw in processes["calls"])


def test_port_cannot_change_after_setup(processes):
    browser.setup(port=19879)
    before = len(processes["calls"])
    with pytest.raises(OdxError, match="already fixed"):
        browser.setup(port=19880)
    assert len(processes["calls"]) == before


def test_unrecognized_service_is_never_replaced_or_stopped(processes):
    browser.setup(port=19879)
    browser.plist_path().write_bytes(
        plistlib.dumps({"Label": browser.LABEL, "ProgramArguments": ["foreign"]})
    )
    before = len(processes["calls"])
    with pytest.raises(OdxError, match="unrecognized"):
        browser.stop()
    assert len(processes["calls"]) == before


def test_foreign_registered_label_without_our_plist_is_never_stopped(processes):
    processes["running"].add(browser.domain())
    with pytest.raises(OdxError, match="already registered"):
        browser.setup(port=19879)
    assert not browser.plist_path().exists()
    assert not any(args[:2] == ["launchctl", "bootout"] for args, kw in processes["calls"])


def test_active_browser_sessions_prevent_maintenance(processes, monkeypatch):
    browser.setup(port=19879)
    monkeypatch.setattr(
        browser, "rpc_status", lambda *a: {"status": {"sessions": [{"connected": True}]}}
    )
    with pytest.raises(OdxError, match="still connected"):
        browser.stop()
    assert browser.domain() in processes["running"]
    assert browser.load()["enabled"] is True


def test_pilot_requires_explicit_adoption_and_keeps_existing_token(isolated, processes):
    old, credential = install_pilot(isolated, processes)
    before = old.read_bytes()
    with pytest.raises(OdxError, match="explicitly"):
        browser.setup()
    browser.setup(adopt=True)
    assert browser.token() == credential.read_text()
    assert old.read_bytes() == before
    assert browser.load()["adopted_pilot"]
    assert browser.domain(browser.PILOT_LABEL) not in processes["running"]
    assert browser.domain() in processes["running"]
    assert any(
        (p / old.name).read_bytes() == before for p in (browser.directory() / "backups").iterdir()
    )


def test_failed_adoption_restores_the_original_pilot(isolated, processes):
    old, credential = install_pilot(isolated, processes)
    processes["fail"] = True
    with pytest.raises(OdxError, match="bootstrap failed"):
        browser.setup(adopt=True)
    assert browser.domain(browser.PILOT_LABEL) in processes["running"]
    assert browser.load() is None
    assert not browser.plist_path().exists()
    assert credential.read_text() == "a" * 43


def test_interrupted_adoption_also_restores_the_pilot(isolated, processes, monkeypatch):
    install_pilot(isolated, processes)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(browser, "wait_healthy", interrupt)
    with pytest.raises(KeyboardInterrupt):
        browser.setup(adopt=True)
    assert browser.domain(browser.PILOT_LABEL) in processes["running"]
    assert browser.load() is None


def test_failed_replacement_does_not_start_a_previously_disabled_bridge(processes, monkeypatch):
    browser.setup(port=19879)
    browser.stop()
    original = browser.load()
    monkeypatch.setattr(browser, "release", lambda: "f" * 64)
    processes["fail"] = True
    with pytest.raises(OdxError, match="bootstrap failed"):
        browser.setup()
    assert browser.load() == original
    assert browser.domain() not in processes["running"]
    assert processes["calls"][-1][0] == ["launchctl", "disable", browser.domain()]


def test_failed_runtime_replacement_restores_owned_service_state(processes, monkeypatch):
    browser.setup(port=19879)
    before = browser.load()
    previous_plist = browser.plist_path().read_bytes()
    previous_token = browser.token()
    monkeypatch.setattr(browser, "release", lambda: "f" * 64)

    def fail(*args):
        raise OdxError("candidate did not become healthy")

    monkeypatch.setattr(browser, "wait_healthy", fail)
    with pytest.raises(OdxError, match="did not become healthy"):
        browser.setup()
    assert browser.load() == before
    assert browser.plist_path().read_bytes() == previous_plist
    assert browser.token() == previous_token
    assert browser.domain() in processes["running"]


def test_instance_adoption_is_explicit_and_requires_matching_credential(root, isolated, processes):
    install_pilot(isolated, processes)
    browser.setup(adopt=True)
    runtime = Runtime.load(enable(root))
    legacy = root / ".odoocker/browser-bridge/token"
    write_private(legacy, "b" * 43)
    with pytest.raises(OdxError, match="credential differs"):
        browser.adopt_instance(runtime)
    write_private(legacy, browser.token())
    browser.adopt_instance(runtime)
    assert (runtime.directory / "browser-adopt-pilot.json").is_file()
    assert not runtime.compose_file.exists()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o400])
def test_unsafe_credentials_fail_closed(processes, mode):
    browser.setup(port=19879)
    (browser.directory() / "token").chmod(mode)
    with pytest.raises(OdxError, match="unsafe"):
        browser.token()


def test_image_compatibility_is_narrow_and_no_host_credentials_are_inherited():
    image = SimpleNamespace(type="image")
    text = SimpleNamespace(type="text")
    result = SimpleNamespace(structuredContent=None, content=[image, text])
    assert image_only_execute("execute", result)
    assert not image_only_execute("another_tool", result)
    assert not image_only_execute("execute", SimpleNamespace(structuredContent={}, content=[image]))
    assert not image_only_execute(
        "execute", SimpleNamespace(structuredContent=None, content=[text])
    )
    assert not {"OPENAI_API_KEY", "OPENCODE_PASSWORD", "MCP_PROXY_AUTH_TOKEN"}.intersection(
        ENVIRONMENT
    )


def test_service_forwards_xdg_and_relay_settings_but_never_auth_credentials(tmp_path):
    secret = "not-a-real-token"
    arguments = proxy_arguments(
        tmp_path,
        {"port": 19879, "browse": "/bin/browse"},
        {
            "XDG_DATA_HOME": "/custom/data",
            "OPENCODE_BROWSER_PORT": "19878",
            "MCP_PROXY_AUTH_TOKEN": secret,
            "OPENAI_API_KEY": secret,
        },
    )
    assert secret not in " ".join(arguments)
    assert "XDG_DATA_HOME" in arguments and "/custom/data" in arguments
    assert "OPENCODE_BROWSER_PORT" in arguments and "19878" in arguments
    assert arguments[-3:] == ["--", "/bin/browse", "mcp"]
    assert "0.0.0.0" not in arguments


def test_diagnostic_mcp_handshake_uses_auth_and_terminates_transport(monkeypatch):
    calls = []

    def request(port, credential, method, path, body=None, headers=None):
        calls.append((port, credential, method, path, body, dict(headers or {})))
        if method == "DELETE":
            return 200, {}, b""
        message = json.loads(body)
        if message["method"] == "initialize":
            return 200, {"Mcp-Session-Id": "test-transport"}, b'{"result":{}}'
        if message["method"] == "notifications/initialized":
            return 202, {}, b""
        result = {"structuredContent": {"status": {"connected": True}}}
        return 200, {}, json.dumps({"result": result}).encode()

    monkeypatch.setattr(browser, "request", request)
    result = RPC_STATUS({"port": 19879}, "test-credential")
    assert result["status"]["connected"]
    assert calls[-1][2:4] == ("DELETE", "/mcp")
    assert calls[-1][-1]["Mcp-Session-Id"] == "test-transport"


def test_health_requires_the_managed_runtime_identity(monkeypatch):
    monkeypatch.setattr(browser, "request", lambda *a: (204, {}, b""))
    assert HEALTHY({"port": 19879}, "test")  # Known pilot, before adoption.
    assert not HEALTHY({"port": 19879, "release": "a" * 64}, "test")
    monkeypatch.setattr(
        browser,
        "request",
        lambda *a: (
            204,
            {"X-Odoocker-Browser-Release": "a" * 64},
            b"",
        ),
    )
    assert HEALTHY({"port": 19879, "release": "a" * 64}, "test")


def test_check_only_uses_the_selected_runtime_transport(root, processes, monkeypatch):
    browser.setup(port=19879)
    runtime = Runtime.load(enable(root))
    calls = []
    monkeypatch.setattr(Runtime, "compose", lambda *a, **kw: calls.append(a))
    browser.check(runtime)
    assert calls[0][1:6] == ("exec", "-T", "opencode", "python3", "-c")
    assert browser.token() not in " ".join(str(a) for a in calls[0][1:])


def test_container_pilot_cleanup_preserves_unrelated_settings_and_is_idempotent(tmp_path):
    root = tmp_path / "instance"
    config = tmp_path / "config"
    root.mkdir()
    config.mkdir()
    original = {
        "model": "test/model",
        "mcp": {
            "servers": {
                "browse": browser_container.pilot_server(),
                "other": {"type": "local", "command": ["test"]},
            }
        },
    }
    write_private(
        root / "browser-adopt-pilot.json",
        json.dumps({"schema": 1, "server": browser_container.pilot_server()}),
    )
    file = config / "opencode.json"
    write_private(file, json.dumps(original))
    managed = {"mcp": {"servers": {"browse": {"type": "remote"}}}}
    browser_container.adopt_pilot(root, config, managed)
    assert json.loads((root / "browser-pilot-opencode.before.json").read_text()) == original
    changed = json.loads(file.read_text())
    assert changed["model"] == original["model"]
    assert changed["mcp"]["servers"] == {"other": original["mcp"]["servers"]["other"]}
    # Once adopted, disabling the generated integration must not re-run migration.
    browser_container.adopt_pilot(root, config, {})
    assert json.loads(file.read_text()) == changed


def test_container_cleanup_never_replaces_a_different_browse_entry(tmp_path):
    root = tmp_path / "instance"
    config = tmp_path / "config"
    root.mkdir()
    config.mkdir()
    write_private(
        root / "browser-adopt-pilot.json",
        json.dumps({"schema": 1, "server": browser_container.pilot_server()}),
    )
    original = {
        "mcp": {"servers": {"browse": {"type": "remote", "url": "https://other.invalid/mcp"}}}
    }
    write_private(config / "opencode.json", json.dumps(original))
    with pytest.raises(OdxError, match="differs from the pilot"):
        browser_container.adopt_pilot(root, config, {"mcp": {"servers": {"browse": {}}}})
    assert json.loads((config / "opencode.json").read_text()) == original
    assert not (root / "browser-pilot-opencode.before.json").exists()
