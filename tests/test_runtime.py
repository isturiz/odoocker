"""Generated runtime contracts; these tests never invoke Docker."""

import configparser
import json
import stat
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoocker import runtime as runtime_module
from odoocker.config import Workspace
from odoocker.errors import OdxError
from odoocker.locking import locked
from odoocker.runtime import Runtime, run_command, write_private


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "source"
    (source / "odoo").mkdir(parents=True)
    (source / "odoo-bin").write_text("# fake Odoo\n")
    (source / "odoo" / "release.py").write_text("version_info = (17, 0, 0, 'final', 0)\n")
    addon = root / "editable addons"
    addon.mkdir()
    return Workspace(root, "example", "17.0", "2.0.30", source, (addon,))


@pytest.fixture
def runtime(workspace):
    return Runtime.load(workspace)


def test_identity_and_secret_are_stable_for_path_and_instance(workspace):
    first = Runtime.load(workspace, "feature")
    second = Runtime.load(Workspace(**vars(workspace)), "feature")
    assert str(uuid.UUID(first.identity)) == first.identity
    assert first == second
    assert first.project == f"odx-{first.identity.replace('-', '')}-feature"
    state = json.loads((workspace.root / ".odoocker" / "identity.json").read_text())
    assert state == {"id": first.identity, "root": str(workspace.root)}
    assert json.loads((first.directory / "secrets.json").read_text()) == {
        "password": first.password
    }


def test_instances_share_workspace_uuid_but_not_secrets_or_resources(workspace):
    main = Runtime.load(workspace)
    feature = Runtime.load(workspace, "feature")
    assert main.identity == feature.identity
    assert main.password != feature.password
    assert main.project != feature.project
    assert main.directory != feature.directory
    assert main.compose_file != feature.compose_file
    assert main.operation_lock != feature.operation_lock
    assert Runtime.load(workspace).password == main.password
    assert Runtime.load(workspace, "feature").password == feature.password


def test_same_basename_different_roots_have_distinct_identities(workspace, tmp_path):
    other_root = tmp_path / "elsewhere" / workspace.root.name
    other_root.mkdir(parents=True)
    other = replace(workspace, root=other_root)
    first = Runtime.load(workspace)
    second = Runtime.load(other)
    assert first.workspace.root.name == second.workspace.root.name
    assert first.identity != second.identity
    assert first.project != second.project
    assert first.password != second.password


def test_copied_identity_is_rejected_without_altering_state(runtime, tmp_path):
    new_root = tmp_path / "copy"
    state = new_root / ".odoocker" / "identity.json"
    state.parent.mkdir(parents=True)
    original = (runtime.workspace.root / ".odoocker" / "identity.json").read_bytes()
    state.write_bytes(original)
    with pytest.raises(OdxError, match="Workspace state belongs to another path"):
        Runtime.load(replace(runtime.workspace, root=new_root))
    assert state.read_bytes() == original
    assert not (state.parent / "instances").exists()
    assert Runtime.load(runtime.workspace) == runtime


@pytest.mark.parametrize(
    "instance", ["../escape", "..", ".", "/absolute", "a/b", "a\\b", "Main", "", None, 1]
)
def test_invalid_instance_rejected_before_writing(workspace, instance):
    with pytest.raises(OdxError, match="instance must match"):
        Runtime.load(workspace, instance)
    assert not (workspace.root / ".odoocker").exists()


@pytest.mark.parametrize("content", ["not json", "{}", "[]", "null", '{"id": "not-a-uuid"}'])
def test_corrupt_identity_is_rejected_without_replacement(workspace, content):
    path = workspace.root / ".odoocker" / "identity.json"
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises(OdxError, match="Invalid workspace identity"):
        Runtime.load(workspace)
    assert path.read_text() == content


@pytest.mark.parametrize("content", ["not json", "{}", "[]", "null"])
def test_corrupt_secrets_are_rejected_without_rotation(runtime, content):
    path = runtime.directory / "secrets.json"
    path.write_text(content)
    with pytest.raises(OdxError, match="Invalid credentials file"):
        Runtime.load(runtime.workspace)
    assert path.read_text() == content


@pytest.mark.parametrize("password", ["", None, False, 1, [], {}])
def test_empty_or_non_string_secrets_are_rejected(runtime, password):
    (runtime.directory / "secrets.json").write_text(json.dumps({"password": password}))
    with pytest.raises(OdxError, match="Empty instance credentials"):
        Runtime.load(runtime.workspace)


def test_identity_lock_fails_fast(workspace):
    with locked(workspace.root / ".odoocker" / "identity.lock"):
        with pytest.raises(OdxError, match="Resource busy.*identity.lock"):
            Runtime.load(workspace)
    assert Runtime.load(workspace).identity


def test_operation_lock_prevents_generation(runtime):
    with locked(runtime.operation_lock):
        with pytest.raises(OdxError, match="Resource busy.*operation.lock"):
            runtime.generate()
    assert not runtime.compose_file.exists()


def test_compose_resource_names_are_project_scoped(runtime):
    document = runtime.compose_document()
    assert document["name"] == runtime.project
    assert set(document["services"]) == {"init", "db", "odoo", "opencode"}
    for service in document["services"].values():
        assert "container_name" not in service
        assert "network_mode" not in service
        assert "networks" not in service
    assert document.get("networks", {}) == {}
    assert set(document["volumes"]) == {
        "venv",
        "runtime",
        "db-data",
        "odoo-data",
        "opencode-config",
        "opencode-data",
        "opencode-state",
    }
    assert all(definition == {} for definition in document["volumes"].values())
    assert "docker.sock" not in json.dumps(document)


def test_only_dynamic_localhost_application_ports_are_published(runtime):
    services = runtime.compose_document()["services"]
    assert services["odoo"]["ports"] == [{"target": 8069, "host_ip": "127.0.0.1"}]
    assert services["opencode"]["ports"] == [{"target": 4096, "host_ip": "127.0.0.1"}]
    assert "ports" not in services["db"]
    assert "ports" not in services["init"]
    assert services["db"]["environment"]["POSTGRES_PASSWORD"] == runtime.password


@pytest.mark.parametrize("service", ["odoo", "opencode"])
def test_source_is_readonly_and_workspace_addons_are_editable(runtime, service):
    mounts = runtime.compose_document()["services"][service]["volumes"]
    binds = {mount["target"]: mount for mount in mounts if mount["type"] == "bind"}
    assert binds["/opt/odoo"]["source"] == str(runtime.workspace.source)
    assert binds["/opt/odoo"]["read_only"] is True
    assert binds["/workspace"]["source"] == str(runtime.workspace.root)
    assert binds["/workspace"]["read_only"] is False
    assert binds["/opt/addons/0"]["source"] == str(runtime.workspace.addons[0])
    assert binds["/opt/addons/0"]["read_only"] is False
    assert binds["/etc/odoocker/odoo.conf"]["read_only"] is True
    assert binds["/etc/odoocker/context.json"]["read_only"] is True
    assert all(mount["bind"] == {"create_host_path": False} for mount in binds.values())


def test_opencode_is_opt_in_with_independent_native_storage(runtime):
    services = runtime.compose_document()["services"]
    code = services["opencode"]
    assert code["profiles"] == ["code"]
    assert all("profiles" not in services[name] for name in ("db", "init", "odoo"))
    assert code["environment"]["OPENCODE_PASSWORD"] == runtime.password
    assert "OPENCODE_PASSWORD" not in services["odoo"]["environment"]
    native = {
        "opencode-config": "/home/odoo/.config/opencode",
        "opencode-data": "/home/odoo/.local/share/opencode",
        "opencode-state": "/home/odoo/.local/state/opencode",
    }
    code_volumes = {mount["source"]: mount["target"] for mount in code["volumes"]}
    for name, target in native.items():
        assert code_volumes[name] == target
    for name in ("db", "init", "odoo"):
        assert not set(native).intersection(mount["source"] for mount in services[name]["volumes"])


@pytest.mark.parametrize("version", ["17.0", "19.0"])
def test_generated_files_are_valid_and_have_expected_permissions(runtime, version):
    runtime = replace(runtime, workspace=replace(runtime.workspace, odoo_version=version))
    (runtime.workspace.source / "odoo" / "release.py").write_text(
        f"version_info = ({version.split('.')[0]}, 0, 0, 'final', 0)\n"
    )
    runtime.generate()
    document = json.loads(runtime.compose_file.read_text())
    assert document == runtime.compose_document()
    context = json.loads((runtime.directory / "context.json").read_text())
    assert context == {
        "project": runtime.project,
        "instance": "main",
        "workspace": "example",
        "version": version,
        "addons": ["/opt/addons/0"],
        "requirements": [],
        "postgres_version": "16",
        "pgvector": False,
    }
    config = configparser.ConfigParser()
    config.read(runtime.directory / "odoo.conf")
    options = config["options"]
    assert options["db_host"] == "db"
    assert options.getint("db_port") == 5432
    assert options["db_password"] == runtime.password
    assert options["db_name"] == "dev"
    assert options["dbfilter"] == "^dev$"
    assert options["addons_path"].split(",") == [
        "/opt/odoo/odoo/addons",
        "/opt/odoo/addons",
        "/opt/addons/0",
    ]
    assert not options.getboolean("list_db")
    assert options.getint("http_port") == 8069
    assert (runtime.directory / "compose.env").read_text() == ""
    for path, mode in [
        (runtime.directory, 0o700),
        (runtime.workspace.root / ".odoocker" / "identity.json", 0o600),
        (runtime.directory / "secrets.json", 0o600),
        (runtime.compose_file, 0o600),
        (runtime.directory / "compose.env", 0o600),
        (runtime.directory / "odoo.conf", 0o644),
        (runtime.directory / "context.json", 0o644),
    ]:
        assert stat.S_IMODE(path.stat().st_mode) == mode, path
    build = runtime.directory / "build"
    assert (build / "runtime.Dockerfile").read_bytes() == (
        runtime_module.PACKAGE / "runtime.Dockerfile"
    ).read_bytes()
    assert (build / "odoocker" / "runtime.py").is_file()
    assert not list(build.rglob("*.pyc"))
    assert not list(runtime.directory.glob(".*.tmp"))


def test_regeneration_preserves_identity_secrets_and_document(runtime):
    runtime.generate()
    original = runtime.compose_file.read_bytes()
    loaded = Runtime.load(runtime.workspace)
    loaded.generate()
    assert loaded == runtime
    assert loaded.compose_file.read_bytes() == original


@pytest.mark.parametrize("dbfilter", [None, "^(dev|sandbox_[a-z0-9_]+)$", "^%d$"])
def test_database_manager_is_opt_in_with_private_master_password(runtime, dbfilter):
    options = {"list_db": True}
    if dbfilter is not None:
        options["dbfilter"] = dbfilter
    enabled = Runtime.load(replace(runtime.workspace, odoo_options=options))
    enabled.generate()
    config = configparser.ConfigParser(interpolation=None)
    config.read(enabled.directory / "odoo.conf")
    assert config["options"].getboolean("list_db")
    assert config["options"]["dbfilter"] == (dbfilter if dbfilter is not None else "^dev$")
    assert config["options"]["db_name"] == "dev"
    assert config["options"]["admin_passwd"] == enabled.admin_password
    assert enabled.admin_password != runtime.password
    assert len(enabled.admin_password) >= 32
    credentials = json.loads((enabled.directory / "secrets.json").read_text())
    assert credentials == {"password": runtime.password, "admin_password": enabled.admin_password}
    assert enabled.password == runtime.password
    assert enabled.identity == runtime.identity
    assert enabled.admin_password not in enabled.compose_file.read_text()
    assert enabled.admin_password not in (enabled.directory / "context.json").read_text()
    assert enabled.compose_document()["services"]["odoo"]["ports"] == [
        {"target": 8069, "host_ip": "127.0.0.1"}
    ]
    assert stat.S_IMODE((enabled.directory / "secrets.json").stat().st_mode) == 0o600
    original = (enabled.directory / "odoo.conf").read_bytes()
    reloaded = Runtime.load(enabled.workspace)
    reloaded.generate()
    assert reloaded.admin_password == enabled.admin_password
    assert (reloaded.directory / "odoo.conf").read_bytes() == original


def test_database_manager_secret_survives_disable_and_reenable(runtime):
    enabled_workspace = replace(runtime.workspace, odoo_options={"list_db": True})
    enabled = Runtime.load(enabled_workspace)
    enabled.generate()
    disabled = Runtime.load(runtime.workspace)
    disabled.generate()
    config = configparser.ConfigParser()
    config.read(disabled.directory / "odoo.conf")
    assert not config["options"].getboolean("list_db")
    assert config["options"]["admin_passwd"] == enabled.admin_password
    assert Runtime.load(enabled_workspace).admin_password == enabled.admin_password


def test_database_manager_secrets_are_instance_scoped(workspace):
    workspace = replace(workspace, odoo_options={"list_db": True})
    main = Runtime.load(workspace)
    feature = Runtime.load(workspace, "feature")
    assert main.admin_password != feature.admin_password
    assert Runtime.load(workspace).admin_password == main.admin_password
    assert Runtime.load(workspace, "feature").admin_password == feature.admin_password


@pytest.mark.parametrize("secret", [None, "", False, 1, [], {}, "bad\nconfig", "bad%value"])
def test_invalid_master_password_is_not_silently_replaced(runtime, secret):
    path = runtime.directory / "secrets.json"
    content = json.dumps({"password": runtime.password, "admin_password": secret})
    path.write_text(content)
    with pytest.raises(OdxError, match="Invalid database manager credentials"):
        Runtime.load(replace(runtime.workspace, odoo_options={"list_db": True}))
    assert path.read_text() == content


def test_generate_refuses_uninitialized_manager_credentials(runtime):
    changed = replace(runtime, workspace=replace(runtime.workspace, odoo_options={"list_db": True}))
    with pytest.raises(OdxError, match="Database manager credentials are missing"):
        changed.generate()
    assert not runtime.compose_file.exists()


@pytest.mark.parametrize("major", ["16", "18"])
@pytest.mark.parametrize("vector", [False, True])
def test_database_profiles_keep_major_specific_storage_and_client(runtime, major, vector):
    runtime = replace(
        runtime,
        workspace=replace(
            runtime.workspace,
            postgres_version=major,
            pgvector=vector,
        ),
    )
    document = runtime.compose_document()
    db = document["services"]["db"]
    assert db["image"] == (
        f"pgvector/pgvector:0.8.7-pg{major}-bookworm"
        if vector
        else {"16": "postgres:16.13-bookworm", "18": "postgres:18-bookworm"}[major]
    )
    name = "db-data" if major == "16" else "db-data-pg18"
    target = "/var/lib/postgresql/data" if major == "16" else "/var/lib/postgresql"
    assert db["volumes"] == [{"type": "volume", "source": name, "target": target}]
    assert name in document["volumes"]
    assert "ports" not in db
    assert db["shm_size"] == "256m"
    for service in ("odoo", "opencode"):
        assert document["services"][service]["build"]["args"]["POSTGRES_VERSION"] == major
        assert f"-pg{major}-" in document["services"][service]["image"]


@pytest.mark.parametrize("legacy", [False, True])
def test_database_major_switch_requires_consent_and_never_reuses_pg16_volume(runtime, legacy):
    runtime.generate()
    if legacy:
        path = runtime.directory / "context.json"
        previous = json.loads(path.read_text())
        del previous["postgres_version"]
        del previous["pgvector"]
        path.write_text(json.dumps(previous))
    before = runtime.compose_file.read_bytes()
    changed = replace(
        runtime, workspace=replace(runtime.workspace, postgres_version="18", pgvector=True)
    )
    with pytest.raises(OdxError, match="allow-database-switch"):
        changed.generate()
    assert runtime.compose_file.read_bytes() == before
    changed.generate(allow_database_switch=True)
    assert changed.password == runtime.password
    assert changed.database_volume != runtime.database_volume
    assert json.loads((runtime.directory / "context.json").read_text())["postgres_version"] == "18"
    with pytest.raises(OdxError, match="allow-database-switch"):
        runtime.generate()
    runtime.generate(allow_database_switch=True)
    assert runtime.database_volume == "db-data"


def test_pgvector_toggle_keeps_same_major_storage(runtime):
    runtime.generate()
    changed = replace(runtime, workspace=replace(runtime.workspace, pgvector=True))
    changed.generate()
    assert changed.database_volume == runtime.database_volume


def test_client_image_depends_on_postgres_major(runtime):
    changed = replace(runtime, workspace=replace(runtime.workspace, postgres_version="18"))
    assert changed.image() != runtime.image()


def test_runtime_dockerfile_changes_invalidate_both_service_images(runtime, tmp_path, monkeypatch):
    package = tmp_path / "engine"
    package.mkdir()
    dockerfile = package / "runtime.Dockerfile"
    dockerfile.write_text("FROM example\n")
    monkeypatch.setattr(runtime_module, "PACKAGE", package)
    before = runtime.compose_document()
    dockerfile.write_text("FROM example\nRUN install-pdf-dependencies\n")
    after = runtime.compose_document()
    assert before["volumes"] == after["volumes"]
    assert before["services"]["db"] == after["services"]["db"]
    for service in ("odoo", "opencode"):
        assert before["services"][service]["image"] != after["services"][service]["image"]
        assert before["services"][service]["volumes"] == after["services"][service]["volumes"]
    assert after["services"]["odoo"]["image"] == after["services"]["opencode"]["image"]


def test_generate_rejects_invalid_source_without_creating_compose(runtime):
    (runtime.workspace.source / "odoo-bin").unlink()
    with pytest.raises(OdxError, match="Odoo source missing"):
        runtime.generate()
    assert not runtime.compose_file.exists()


def test_compose_uses_explicit_context_and_ignores_ambient_compose(runtime, monkeypatch):
    runtime.generate()
    for key, value in {
        "COMPOSE_FILE": "/unrelated/compose.yml",
        "COMPOSE_PROJECT_NAME": "unrelated",
        "COMPOSE_PROFILES": "production",
        "COMPOSE_ENV_FILES": "/unrelated/.env",
        "COMPOSE_PATH_SEPARATOR": ";",
        "COMPOSE_DISABLE_ENV_FILE": "0",
        "ODOOCKER_TEST_PRESERVED": "yes",
    }.items():
        monkeypatch.setenv(key, value)
    calls = []
    sentinel = object()

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(runtime_module, "run_command", fake_run)
    assert runtime.compose("ps", "--format", "json", capture=True, check=False) is sentinel
    assert len(calls) == 1
    args, kwargs = calls[0]
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
        "ps",
        "--format",
        "json",
    ]
    assert kwargs["capture"] is True
    assert kwargs["check"] is False
    assert not any(key.startswith("COMPOSE_") for key in kwargs["env"])
    assert kwargs["env"]["ODOOCKER_TEST_PRESERVED"] == "yes"
    assert runtime_module.os.environ["COMPOSE_PROJECT_NAME"] == "unrelated"


def test_compose_requires_generated_file(runtime, monkeypatch):
    def unexpected_run(*args, **kwargs):
        pytest.fail("Docker must not be called before generation")

    monkeypatch.setattr(runtime_module, "run_command", unexpected_run)
    with pytest.raises(OdxError, match="Runtime is not generated"):
        runtime.compose("up")


def test_compose_escapes_literal_dollars_in_paths_and_secrets(runtime):
    root = runtime.workspace.root / "$WORKSPACE"
    root.mkdir()
    source = runtime.workspace.source.rename(root / "${ODOO_SOURCE}")
    addon = runtime.workspace.addons[0].rename(root / "$ADDON")
    workspace = replace(runtime.workspace, root=root, source=source, addons=(addon,))
    runtime = replace(Runtime.load(workspace), password="literal$PASSWORD:${NOT_AN_ENV}$$end")
    runtime.generate()
    document = json.loads(runtime.compose_file.read_text())
    services = document["services"]
    escaped = "literal$$PASSWORD:$${NOT_AN_ENV}$$$$end"
    assert services["db"]["environment"]["POSTGRES_PASSWORD"] == escaped
    assert services["opencode"]["environment"]["OPENCODE_PASSWORD"] == escaped
    assert services["odoo"]["build"]["context"] == str(runtime.directory / "build").replace(
        "$", "$$"
    )
    binds = {mount["target"]: mount for mount in services["odoo"]["volumes"]}
    assert binds["/workspace"]["source"] == str(root).replace("$", "$$")
    assert binds["/opt/odoo"]["source"] == str(source).replace("$", "$$")
    assert binds["/opt/addons/0"]["source"] == str(addon).replace("$", "$$")
    assert f"db_password = {runtime.password}\n" in (runtime.directory / "odoo.conf").read_text()


def test_url_queries_dynamic_localhost_port(runtime, monkeypatch):
    runtime.generate()
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="127.0.0.1:49152\n")

    monkeypatch.setattr(runtime_module, "run_command", fake_run)
    assert runtime.url("odoo", 8069) == "http://127.0.0.1:49152"
    assert calls[0][0][-3:] == ["port", "odoo", "8069"]
    assert calls[0][1]["capture"] is True


@pytest.mark.parametrize("address", ["", "0.0.0.0:8069", "[::]:8069", "127.0.0.1:bad"])
def test_url_rejects_non_localhost_or_invalid_port(runtime, monkeypatch, address):
    runtime.generate()
    monkeypatch.setattr(
        runtime_module, "run_command", lambda *args, **kwargs: SimpleNamespace(stdout=address)
    )
    with pytest.raises(OdxError, match="No localhost port for odoo"):
        runtime.url("odoo", 8069)


def test_write_private_replaces_content_and_permissions(tmp_path):
    path = tmp_path / "nested" / "private.json"
    write_private(path, '{"first": true}')
    path.chmod(0o644)
    write_private(path, '{"second": true}')
    assert json.loads(path.read_text()) == {"second": True}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.iterdir()) == [path]


def test_run_command_passes_argument_list_and_options(monkeypatch, tmp_path):
    calls = []
    sentinel = object()

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(runtime_module.subprocess, "run", fake_run)
    env = {"EXAMPLE": "value"}
    assert (
        run_command(
            ["program", Path("a path"), "literal;$(value)"],
            capture=True,
            check=False,
            env=env,
            cwd=tmp_path,
        )
        is sentinel
    )
    assert calls == [
        (
            ["program", "a path", "literal;$(value)"],
            {"check": False, "text": True, "capture_output": True, "env": env, "cwd": tmp_path},
        )
    ]


def test_run_command_reports_missing_executable(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(runtime_module.subprocess, "run", missing)
    with pytest.raises(OdxError, match="Executable not found: missing-program"):
        run_command(["missing-program"])


@pytest.mark.parametrize("capture", [False, True])
def test_run_command_failure_does_not_echo_secret_arguments(monkeypatch, capture):
    def failed(args, **kwargs):
        raise subprocess.CalledProcessError(7, args, stderr="safe diagnostic\n")

    monkeypatch.setattr(runtime_module.subprocess, "run", failed)
    with pytest.raises(OdxError) as caught:
        run_command(["program", "secret-password"], capture=capture)
    assert "program exited with status 7" in str(caught.value)
    assert "secret-password" not in str(caught.value)
    assert ("safe diagnostic" in str(caught.value)) is capture
