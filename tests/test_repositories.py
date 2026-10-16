"""Declarative addons and safe fetching, using only disposable local Git fixtures."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from odoocker import cli, container, repositories
from odoocker.config import Repository, Workspace
from odoocker.errors import OdxError
from odoocker.runtime import Runtime

BASE = """schema = 1
[workspace]
name = "client"
[odoo]
version = "19.0"
[opencode]
version = "2.0.30"
"""
ADDON = """
[[addons]]
path = "addons/custom/example"
url = "git@github.com:example/addons.git"
branch = "19.0"
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "odoocker.toml").write_text(BASE)
    return tmp_path


def test_declarations_supply_addons_paths_automatically(root):
    (root / "odoocker.toml").write_text(BASE + ADDON)
    workspace = Workspace.load(root)
    assert workspace.repositories == (
        Repository(root / "addons/custom/example", "git@github.com:example/addons.git", "19.0"),
    )
    assert workspace.addons == (root / "addons/custom/example",)
    assert not workspace.addons[0].exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/addons.git",
        "ssh://git@example.com/team/addons.git",
        "git@example.com:team/addons.git",
    ],
)
def test_supported_git_urls(root, url):
    (root / "odoocker.toml").write_text(
        BASE + ADDON.replace("git@github.com:example/addons.git", url)
    )
    assert Workspace.load(root).repositories[0].url == url


@pytest.mark.parametrize(
    "url",
    [
        "--upload-pack=evil",
        "file:///private/repo",
        "https://user:secret@example.com/repo",
        "https://token@example.com/repo",
        "https://example.com/repo?token=secret",
        "git@example.com:repo with spaces",
        "https://[invalid",
        "",
        "https://example.com",
    ],
)
def test_unsafe_urls_rejected(root, url):
    text = BASE + ADDON.replace('"git@github.com:example/addons.git"', json.dumps(url))
    (root / "odoocker.toml").write_text(text)
    with pytest.raises(OdxError, match="addon.url"):
        Workspace.load(root)


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/absolute",
        ".odoocker/sources/addons",
        ".",
        "addons",
        "addons/repo/.git",
    ],
)
def test_addons_destinations_are_confined(root, path):
    (root / "odoocker.toml").write_text(BASE + ADDON.replace("addons/custom/example", path))
    with pytest.raises(OdxError):
        Workspace.load(root)


def test_addon_parent_symlink_cannot_escape(root, tmp_path):
    external = tmp_path / "outside"
    external.mkdir()
    (root / "addons").symlink_to(external)
    (root / "odoocker.toml").write_text(BASE + ADDON)
    with pytest.raises(OdxError):
        Workspace.load(root)


@pytest.mark.parametrize("branch", ["--evil", "../main", "a..b", "a.lock", "a//b", "a/.hidden", ""])
def test_invalid_branches_rejected(root, branch):
    (root / "odoocker.toml").write_text(
        BASE + ADDON.replace('branch = "19.0"', f'branch = "{branch}"')
    )
    with pytest.raises(OdxError, match="addon.branch"):
        Workspace.load(root)


@pytest.mark.parametrize(
    "second", ["addons/custom/example", "addons/custom/example/nested", "addons/custom"]
)
def test_overlapping_checkout_paths_rejected(root, second):
    (root / "odoocker.toml").write_text(
        BASE + ADDON + ADDON.replace("addons/custom/example", second)
    )
    with pytest.raises(OdxError, match="overlapping"):
        Workspace.load(root)


def test_manual_addons_can_be_appended_without_duplicate_declared_paths(root):
    (root / "odoocker.toml").write_text(BASE + ADDON)
    (root / "odoocker.local.toml").write_text('[odoo]\naddons = ["extra"]')
    assert Workspace.load(root).addons == (root / "addons/custom/example", root / "extra")
    (root / "odoocker.local.toml").write_text('[odoo]\naddons = ["addons/custom/example"]')
    with pytest.raises(OdxError, match="Duplicate addon"):
        Workspace.load(root)


@pytest.mark.parametrize(
    "field,value",
    [
        ("requirements", '"requirements.txt"'),
        ("requirements", '["../outside.txt"]'),
        ("requirements", '["/absolute.txt"]'),
        ("requirements", '[".odoocker/private.txt"]'),
        ("requirements", '["requirements.txt", "./requirements.txt"]'),
        ("options", '{db_password = "secret"}'),
        ("options", '{addons_path = "unsafe"}'),
        ("options", "{limit_time_cpu = true}"),
        ("options", "{limit_request = -1}"),
    ],
)
def test_requirements_and_development_options_are_strict(root, field, value):
    (root / "odoocker.toml").write_text(
        BASE.replace('version = "19.0"', f'version = "19.0"\n{field} = {value}')
    )
    with pytest.raises(OdxError):
        Workspace.load(root)


def test_project_requirements_and_options_reach_generated_runtime(root, monkeypatch):
    (root / "requirements.txt").write_text("requests==2.31.0\n")
    (root / "odoocker.toml").write_text(
        BASE.replace(
            'version = "19.0"',
            'version = "19.0"\nrequirements = ["requirements.txt"]\n'
            "options = {limit_time_real = 0, limit_time_real_cron = -1}",
        )
    )
    workspace = Workspace.load(root)
    (workspace.source / "odoo").mkdir(parents=True)
    (workspace.source / "odoo-bin").touch()
    (workspace.source / "odoo/release.py").write_text("version_info = (19, 0, 0)")
    runtime = Runtime.load(workspace)
    runtime.generate()
    settings = json.loads((runtime.directory / "context.json").read_text())
    assert settings["requirements"] == ["/workspace/requirements.txt"]
    config = (runtime.directory / "odoo.conf").read_text()
    assert "limit_time_real = 0\n" in config and "limit_time_real_cron = -1\n" in config
    monkeypatch.setattr(container, "context", lambda: settings)
    assert Path("/workspace/requirements.txt") in container.requirements()
    changed = replace(runtime, workspace=replace(workspace, odoo_options={"limit_time_real": 60}))
    assert changed.compose_document() != runtime.compose_document()


def test_missing_workspace_requirements_prevents_configuration(root):
    workspace = Workspace.load(root)
    (workspace.source / "odoo").mkdir(parents=True)
    (workspace.source / "odoo-bin").touch()
    (workspace.source / "odoo/release.py").write_text("version_info = (19, 0, 0)")
    workspace = replace(workspace, requirement_files=(root / "missing.txt",))
    with pytest.raises(OdxError, match="requirements file does not exist"):
        workspace.validate_sources()


def test_dry_run_does_not_write_or_access_network(root, monkeypatch, capsys):
    (root / "odoocker.toml").write_text(BASE + ADDON)

    def forbidden(*args, **kwargs):
        pytest.fail("Dry-run tried to execute a Git or Docker command")

    monkeypatch.setattr(cli, "run_command", forbidden)
    monkeypatch.setattr(repositories, "run_command", forbidden)
    assert cli.main(["--workspace", str(root), "fetch", "--dry-run"]) == 0
    assert not (root / ".odoocker").exists()
    assert not (root / "addons").exists()
    assert "Clone Odoo 19.0" in capsys.readouterr().out


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    # Import a synthetic fixture commit, never commit in the user's project.
    stream = (
        b"blob\nmark :1\ndata 5\nhello\n"
        b"commit refs/heads/19.0\ncommitter Fixture <fixture@example.invalid> 1700000000 +0000\n"
        b"data 7\nfixture\nM 100644 :1 README\n\n"
    )
    subprocess.run(
        ["git", "--git-dir", str(path), "fast-import", "--quiet"], input=stream, check=True
    )
    return path


def test_fetch_creates_independent_clone_and_keeps_dirty_feature_branch(root, remote):
    repo = Repository(root / "addons/custom/example", str(remote), "19.0")
    workspace = replace(Workspace.load(root), repositories=(repo,), addons=(repo.path,))
    repositories.fetch_repositories(workspace)
    assert (repo.path / "README").read_text() == "hello"
    subprocess.run(["git", "-C", str(repo.path), "switch", "-c", "feature"], check=True)
    (repo.path / "README").write_text("my local changes")
    (repo.path / "untracked.txt").write_text("do not delete")
    repositories.fetch_repositories(workspace)
    assert (repo.path / "README").read_text() == "my local changes"
    assert (repo.path / "untracked.txt").read_text() == "do not delete"
    branch = subprocess.run(
        ["git", "-C", str(repo.path), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert branch.stdout.strip() == "feature"
    assert not list(repo.path.parent.glob("*.partial"))


def test_wrong_origin_is_rejected_without_modifying_checkout(root, remote):
    repo = Repository(root / "addons/example", str(remote), "19.0")
    workspace = replace(Workspace.load(root), repositories=(repo,))
    repositories.fetch_repositories(workspace)
    changed = replace(workspace, repositories=(replace(repo, url="git@example.com:other/repo"),))
    with pytest.raises(OdxError, match="another origin"):
        repositories.fetch_repositories(changed)
    assert (repo.path / "README").read_text() == "hello"


def test_existing_non_git_directory_is_never_replaced(root):
    repo = Repository(root / "addons/example", "git@example.com:team/repo", "19.0")
    repo.path.mkdir(parents=True)
    marker = repo.path / "important.txt"
    marker.write_text("keep")
    with pytest.raises(OdxError, match="non-Git"):
        repositories.fetch_repositories(replace(Workspace.load(root), repositories=(repo,)))
    assert marker.read_text() == "keep"


def test_failed_clone_cleans_only_its_partial_directory(root, monkeypatch):
    repo = Repository(root / "addons/example", "git@example.com:team/repo", "19.0")
    unrelated = root / "addons/unrelated.partial"
    unrelated.mkdir(parents=True)

    def fail(args, **kwargs):
        destination = Path(args[-1])
        destination.mkdir()
        (destination / "partial.txt").touch()
        raise OdxError("Git authentication failed")

    monkeypatch.setattr(repositories, "run_command", fail)
    with pytest.raises(OdxError, match="authentication failed"):
        repositories.fetch_repositories(replace(Workspace.load(root), repositories=(repo,)))
    assert not repo.path.exists()
    assert list(repo.path.parent.iterdir()) == [unrelated]
