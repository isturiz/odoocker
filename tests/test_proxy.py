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
    monkeypatch.setattr(proxy, "running", lambda: False)
    calls = []
    monkeypatch.setattr(proxy, "caddy", lambda *args: calls.append(args))
    return calls


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "client", "client", "19.0", "2.0.30", tmp_path)
    runtime = Runtime.load(workspace)
    monkeypatch.setattr(Runtime, "url", lambda *args: "http://127.0.0.1:49000")
    return runtime


def test_global_start_needs_no_workspace_and_is_idempotent(isolated_proxy, monkeypatch):
    assert cli.main(["proxy", "start"]) == 0
    assert proxy.load() == {"schema": 1, "port": 8080, "routes": {}}
    assert isolated_proxy[0][0] == "start"
    monkeypatch.setattr(proxy, "running", lambda: True)
    assert cli.main(["proxy", "start"]) == 0
    assert isolated_proxy[-1][0] == "reload"
    assert cli.main(["proxy", "start", "--port", "8081"]) == 1


def test_proxy_is_opt_in(runtime):
    proxy.sync(runtime)
    assert not (proxy.directory() / "routes.json").exists()
    with pytest.raises(OdxError, match="not configured"):
        proxy.register(runtime)


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
    monkeypatch.setattr(Runtime, "compose", lambda *a, **kw: SimpleNamespace(
        stdout='[{"Service": "odoo", "State": "running"}]'
    ))
    capsys.readouterr()
    cli._status(runtime, as_json=True)
    info = json.loads(capsys.readouterr().out)
    assert info["odoo_url"] == "http://127.0.0.1:49000"
    assert info["odoo_proxy_url"] == "http://client-main.localhost:8080"
    assert info["proxy_running"] is False
