"""Shared host proxy contracts; no Docker or installed Caddy required."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from odoocker import cli, proxy
from odoocker.config import Workspace
from odoocker.errors import OdxError
from odoocker.locking import locked
from odoocker.runtime import Runtime

native_caddy = proxy.caddy


@pytest.fixture(autouse=True)
def isolated_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "shared"))
    monkeypatch.delenv("ODOOCKER_CONTAINER", raising=False)
    calls = []
    state = {"running": False}
    monkeypatch.setattr(proxy, "running", lambda: state["running"])

    def caddy(*args):
        calls.append(args)
        if args[0] in ("start", "stop"):
            state["running"] = args[0] == "start"

    monkeypatch.setattr(proxy, "caddy", caddy)
    return calls


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "client", "client", "19.0", "2.0.30", tmp_path)
    runtime = Runtime.load(workspace)
    monkeypatch.setattr(Runtime, "url", lambda *args: "http://127.0.0.1:49000")
    monkeypatch.setattr(Runtime, "compose", lambda *a, **kw: SimpleNamespace(stdout="odoo\n"))
    return runtime


def test_global_start_needs_no_workspace_and_is_idempotent(isolated_proxy, monkeypatch):
    assert cli.main(["proxy", "start"]) == 0
    assert proxy.load() == {"schema": 1, "port": 8080, "enabled": True, "routes": {}}
    assert isolated_proxy[0][0] == "start"
    monkeypatch.setattr(proxy, "running", lambda: True)
    assert cli.main(["proxy", "start"]) == 0
    assert isolated_proxy[-1][0] == "reload"
    assert cli.main(["proxy", "start", "--port", "8081"]) == 1


def test_proxy_starts_and_registers_without_prior_configuration(runtime, isolated_proxy):
    assert proxy.sync(runtime) == "http://client-main.localhost:8080"
    assert proxy.running()
    assert isolated_proxy[0][0] == "start"
    assert proxy.load()["routes"]["client-main.localhost"]["project"] == runtime.project


def test_database_aliases_share_upstream_but_isolate_browser_cookie_hosts(runtime):
    first = proxy.sync(runtime, service="odoo", database="octubre")
    second_host = proxy.hostname(runtime, database="noviembre")
    assert first == f"http://{proxy.hostname(runtime, database='octubre')}:8080"
    assert second_host not in first
    routes = proxy.load()["routes"]
    alias = routes[proxy.hostname(runtime, database="octubre")]
    assert alias["database"] == "octubre"
    assert alias["upstream"] == routes["client-main.localhost"]["upstream"]
    assert proxy.url(runtime, "http://127.0.0.1:49000") == "http://client-main.localhost:8080"
    assert proxy.url(runtime, "http://127.0.0.1:49000", database="octubre") == first


def test_finalized_restore_aliases_are_refreshed_by_normal_lifecycle(runtime):
    directory = runtime.directory / "restores" / ("a" * 32)
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "id": directory.name,
                "project": runtime.project,
                "database": "octubre",
                "status": "completed",
                "published": True,
                "host_finalized": True,
            }
        )
    )
    proxy.sync(runtime)
    assert proxy.hostname(runtime, database="octubre") in proxy.load()["routes"]
    document = proxy.document(proxy.load())
    upstreams = [
        route["handle"][0]["upstreams"]
        for route in document["apps"]["http"]["servers"]["odx"]["routes"][:-1]
    ]
    assert upstreams[0] == upstreams[1]


def test_database_hostnames_are_stable_and_do_not_alias_case_or_underscores(runtime):
    names = ["a-b", "a_b", "October", "october", "a" * 63]
    hosts = {proxy.hostname(runtime, database=name) for name in names}
    assert len(hosts) == len(names)
    assert all(proxy.HOST.fullmatch(host) for host in hosts)


def test_stopped_proxy_restarts_with_existing_port_and_routes(runtime, isolated_proxy):
    cli.main(["proxy", "start", "--port", "8088"])
    proxy.register(replace(runtime, instance="other"))
    proxy.caddy("stop")  # Simulate a stopped process without disabling automatic startup.
    assert proxy.sync(runtime) == "http://client-main.localhost:8088"
    assert isolated_proxy[-1][0] == "start"
    assert proxy.load()["port"] == 8088
    assert len(proxy.load()["routes"]) == 2


def test_disabled_proxy_stays_disabled_until_explicit_start(runtime, isolated_proxy):
    assert cli.main(["proxy", "stop"]) == 0
    before = proxy.load()
    calls = len(isolated_proxy)
    assert proxy.sync(runtime) is None
    assert proxy.load() == before
    assert len(isolated_proxy) == calls
    assert not proxy.running()
    assert cli.main(["proxy", "start"]) == 0
    assert proxy.sync(runtime) == "http://client-main.localhost:8080"


def test_missing_caddy_or_busy_port_preserves_registry_and_direct_access(
    runtime, monkeypatch, capsys
):
    cli.main(["proxy", "start"])
    before = proxy.load()
    monkeypatch.setattr(proxy, "running", lambda: False)

    def fail(*args):
        raise OdxError("Caddy could not start (missing binary or port busy)")

    monkeypatch.setattr(proxy, "caddy", fail)
    assert proxy.sync(runtime) is None
    assert proxy.load() == before
    assert "Direct access remains available" in capsys.readouterr().err


def test_down_does_not_start_or_enable_proxy(runtime, isolated_proxy):
    assert proxy.sync(runtime, remove=True) is None
    assert proxy.load() is None
    assert not isolated_proxy


def test_routes_survive_port_changes_and_remove_only_their_owner(runtime, monkeypatch):
    cli.main(["proxy", "start"])
    first = proxy.register(runtime)
    other = replace(runtime, instance="testing")
    proxy.register(other)
    monkeypatch.setattr(Runtime, "url", lambda *args: "http://127.0.0.1:49001")
    assert proxy.register(runtime) == first == "http://client-main.localhost:8080"
    assert proxy.load()["routes"]["client-main.localhost"]["upstream"] == "127.0.0.1:49001"
    assert proxy.url(runtime, "http://127.0.0.1:49000") is None
    assert proxy.url(runtime, "http://127.0.0.1:49001") == first
    proxy.unregister(runtime)
    assert list(proxy.load()["routes"]) == ["client-testing.localhost"]


def test_collision_does_not_overwrite_another_workspace(runtime):
    cli.main(["proxy", "start"])
    proxy.register(runtime)
    with pytest.raises(OdxError, match="another instance"):
        proxy.register(replace(runtime, identity="another"))
    assert proxy.load()["routes"]["client-main.localhost"]["project"] == runtime.project


def test_reload_failure_keeps_registry(runtime, monkeypatch):
    cli.main(["proxy", "start"])
    proxy.register(runtime)
    before = proxy.load()
    monkeypatch.setattr(proxy, "running", lambda: True)

    def fail(*args):
        raise OdxError("Caddy rejected config")

    monkeypatch.setattr(proxy, "caddy", fail)
    with pytest.raises(OdxError, match="rejected"):
        proxy.register(replace(runtime, instance="testing"))
    assert proxy.load() == before
    proxy.sync(runtime)


def test_concurrent_changes_are_rejected(runtime):
    cli.main(["proxy", "start"])
    with locked(proxy.directory() / "proxy.lock"):
        with pytest.raises(OdxError, match="Resource busy"):
            proxy.register(runtime)


def test_document_is_loopback_http_only_and_defaults_to_404(runtime):
    cli.main(["proxy", "start"])
    proxy.register(runtime)
    config = proxy.document(proxy.load())
    assert config["admin"]["listen"].startswith("unix/")
    assert config["admin"]["config"]["persist"] is False
    server = config["apps"]["http"]["servers"]["odx"]
    assert server["listen"] == ["127.0.0.1:8080"]
    assert server["automatic_https"] == {"disable": True}
    assert server["routes"][0]["match"] == [{"host": ["client-main.localhost"]}]
    handler = server["routes"][0]["handle"][0]
    assert handler["flush_interval"] == -1
    assert handler["stream_close_delay"] == 300000000000
    assert server["routes"][-1]["handle"][0]["status_code"] == 404


def test_stop_keeps_routes_and_remove_is_exact(runtime, isolated_proxy, monkeypatch):
    cli.main(["proxy", "start"])
    proxy.register(runtime)
    monkeypatch.setattr(proxy, "running", lambda: True)
    assert cli.main(["proxy", "stop"]) == 0
    assert isolated_proxy[-1][0] == "stop"
    assert len(proxy.load()["routes"]) == 1
    assert cli.main(["proxy", "remove", "*"]) == 1
    assert cli.main(["proxy", "remove", "client-main.localhost"]) == 0
    assert proxy.load()["routes"] == {}


@pytest.mark.parametrize("port", ["0", "80", "65536", "-1"])
def test_invalid_ports_do_not_enable_proxy(port):
    assert cli.main(["proxy", "start", "--port", port]) == 1
    assert proxy.load() is None


@pytest.mark.parametrize("upstream", ["http://0.0.0.0:8069", "http://127.0.0.1:99999"])
def test_nonlocal_or_invalid_upstream_rejected(runtime, monkeypatch, upstream):
    cli.main(["proxy", "start"])
    monkeypatch.setattr(Runtime, "url", lambda *args: upstream)
    with pytest.raises(OdxError, match="127.0.0.1"):
        proxy.register(runtime)


def test_corrupt_registry_not_overwritten():
    cli.main(["proxy", "start"])
    file = proxy.directory() / "routes.json"
    file.write_text('{"schema": 99}')
    assert cli.main(["proxy", "start"]) == 1
    assert json.loads(file.read_text()) == {"schema": 99}


def test_long_host_and_underscore_normalization(runtime):
    assert proxy.hostname(replace(runtime, instance="test_one")) == "client-test-one.localhost"
    with pytest.raises(OdxError, match="63-character"):
        proxy.hostname(replace(runtime, instance="x" * 60))


def test_both_services_refresh_and_remove_independently(runtime, monkeypatch):
    cli.main(["proxy", "start"])
    monkeypatch.setattr(
        Runtime, "compose", lambda *a, **kw: SimpleNamespace(stdout="odoo\nopencode\ndb\n")
    )
    proxy.register(runtime)
    assert set(proxy.load()["routes"]) == {
        "client-main.localhost",
        "opencode-client-main.localhost",
    }
    monkeypatch.setattr(Runtime, "url", lambda *args: "http://127.0.0.1:49002")
    assert proxy.register(runtime, service="opencode") == (
        "http://opencode-client-main.localhost:8080"
    )
    routes = proxy.load()["routes"]
    assert routes["client-main.localhost"]["upstream"] == "127.0.0.1:49000"
    assert routes["opencode-client-main.localhost"]["upstream"] == "127.0.0.1:49002"
    assert proxy.url(runtime, "http://127.0.0.1:49002", "opencode") is not None
    proxy.unregister(runtime)
    assert proxy.load()["routes"] == {}


def test_registration_without_opencode_removes_its_stale_route(runtime):
    cli.main(["proxy", "start"])
    proxy.register(runtime, service="opencode")
    proxy.register(runtime)
    assert list(proxy.load()["routes"]) == ["client-main.localhost"]


def test_legacy_odoo_registry_remains_readable(runtime):
    cli.main(["proxy", "start"])
    state = proxy.load()
    del state["enabled"]
    state["routes"]["client-main.localhost"] = {
        "project": runtime.project,
        "upstream": "127.0.0.1:49000",
    }
    proxy.save(state)
    assert proxy.url(runtime, "http://127.0.0.1:49000") is not None
    proxy.register(runtime, service="opencode")
    assert len(proxy.load()["routes"]) == 2


def test_invalid_enabled_flag_is_not_overwritten(runtime):
    cli.main(["proxy", "start"])
    file = proxy.directory() / "routes.json"
    state = proxy.load()
    state["enabled"] = "false"
    file.write_text(json.dumps(state))
    assert proxy.sync(runtime) is None
    assert json.loads(file.read_text()) == state


def test_cross_service_hostname_collision_is_not_overwritten(runtime):
    cli.main(["proxy", "start"])
    proxy.register(runtime, service="opencode")
    other = replace(runtime, workspace=replace(runtime.workspace, name="opencode-client"))
    with pytest.raises(OdxError, match="another instance"):
        proxy.register(other)


def test_no_services_does_not_remove_existing_routes(runtime, monkeypatch):
    cli.main(["proxy", "start"])
    proxy.register(runtime)
    before = proxy.load()
    monkeypatch.setattr(Runtime, "compose", lambda *a, **kw: SimpleNamespace(stdout="db\n"))
    with pytest.raises(OdxError, match="No running"):
        proxy.register(runtime)
    assert proxy.load() == before


def test_caddy_uses_private_profile(monkeypatch):
    calls = []
    monkeypatch.setenv("XDG_DATA_HOME", "/example")
    monkeypatch.setattr(proxy, "run_command", lambda *args, **kw: calls.append((args, kw)))
    native_caddy("reload")
    args, kwargs = calls[0]
    assert args[0] == ["caddy", "reload"]
    assert kwargs["env"]["XDG_DATA_HOME"] == "/example/odoocker/proxy/data"


def test_native_start_logs_to_file_instead_of_inherited_pipes(monkeypatch):
    calls = []
    proxy.directory().mkdir(parents=True)
    monkeypatch.setattr(proxy.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
    native_caddy("start", "--config", "test.json")
    args, options = calls[0]
    assert args[0] == ["caddy", "start", "--config", "test.json"]
    assert options["stdout"].name == str(proxy.directory() / "caddy.log")
    assert options["stderr"] == proxy.subprocess.STDOUT
    assert options["timeout"] == 15
    assert (proxy.directory() / "caddy.log").stat().st_mode & 0o777 == 0o600


def test_native_start_reports_missing_installation(monkeypatch):
    proxy.directory().mkdir(parents=True)

    def missing(*a, **kw):
        raise FileNotFoundError("caddy")

    monkeypatch.setattr(proxy.subprocess, "run", missing)
    with pytest.raises(OdxError, match="install it explicitly"):
        native_caddy("start")


def test_status_reports_stable_and_direct_url(runtime, monkeypatch, capsys):
    cli.main(["proxy", "start"])
    proxy.register(runtime)
    runtime.directory.mkdir(parents=True, exist_ok=True)
    runtime.compose_file.write_text("{}")
    monkeypatch.setattr(
        Runtime,
        "compose",
        lambda *a, **kw: SimpleNamespace(
            stdout='[{"Service": "odoo", "State": "running"},'
            '{"Service": "opencode", "State": "running"}]'
        ),
    )
    proxy.register(runtime, service="opencode")
    monkeypatch.setattr(proxy, "running", lambda: False)
    capsys.readouterr()
    cli._status(runtime, as_json=True)
    info = json.loads(capsys.readouterr().out)
    assert info["odoo_url"] == "http://127.0.0.1:49000"
    assert info["odoo_proxy_url"] == "http://client-main.localhost:8080"
    assert info["opencode_proxy_url"] == "http://opencode-client-main.localhost:8080"
    assert info["proxy_running"] is False
