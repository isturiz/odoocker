"""Host skill sharing contracts using synthetic files only; no Docker/OpenCode calls."""

import hashlib
import json
import os
import stat
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from odoocker.adapters import opencode_update
from odoocker.config import LOCAL, MANIFEST, Workspace
from odoocker.errors import OdxError
from odoocker.restore.host import topology
from odoocker.runtime import Runtime
from odoocker.setup import guard_document

MANIFEST_TEXT = """schema = 1
[workspace]
name = "skills-fixture"
[odoo]
version = "19.0"
[opencode]
version = "2.0.30"
"""


@pytest.fixture(autouse=True)
def no_external_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Skill tests must not invoke Docker or OpenCode")

    monkeypatch.setattr("odoocker.runtime.run_command", forbidden)


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


@pytest.fixture
def skills(tmp_path):
    root = tmp_path / "host-skills"
    skill = root / "review"
    (skill / "scripts").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: Display label is not the ID\ndescription: Synthetic review\n---\n"
        "Read references/policy.md and run scripts/check.py.\n"
    )
    script = skill / "scripts/check.py"
    script.write_text("#!/usr/bin/env python3\nprint('fixture')\n")
    script.chmod(0o755)
    (skill / "references/policy.md").write_text("Synthetic reference\n")
    (root / "flat.md").write_text("---\ndescription: Synthetic flat skill\n---\n")
    nested = root / "teams/release"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("---\ndescription: Synthetic release\n---\n")
    return root


def load(root, entries):
    (root / LOCAL).write_text("[opencode]\nskill_sources = " + json.dumps(entries) + "\n")
    return Workspace.load(root)


@pytest.mark.parametrize("entries", [None, []])
def test_default_keeps_native_storage_and_historical_configuration_label(root, entries):
    workspace = Workspace.load(root) if entries is None else load(root, entries)
    assert workspace.skill_sources == ()
    runtime = Runtime.load(workspace)
    runtime.generate()
    document = runtime.compose_document()
    assert not (runtime.directory / "opencode-skills.json").exists()
    assert "/mnt/odoocker" not in json.dumps(document)
    assert "/etc/opencode" not in json.dumps(document)
    historical = asdict(workspace)
    historical.pop("skill_sources")
    expected = hashlib.sha256(
        json.dumps(historical, default=str, sort_keys=True).encode()
    ).hexdigest()
    assert document["services"]["opencode"]["labels"]["io.odoocker.configuration"] == expected


def test_collection_mount_preserves_full_tree_and_is_opencode_only(root, skills):
    workspace = load(root, [str(skills)])
    assert workspace.skill_sources[0].path == skills
    assert workspace.skill_sources[0].directory is None
    runtime = Runtime.load(workspace)
    runtime.generate()
    document = runtime.compose_document()
    config = runtime.directory / "opencode-skills.json"
    assert json.loads(config.read_text()) == {
        "$schema": "https://opencode.ai/config.json",
        "skills": ["/mnt/odoocker/skills/0"],
    }
    assert stat.S_IMODE(config.stat().st_mode) == 0o644
    assert stat.S_IMODE(runtime.directory.stat().st_mode) == 0o700
    code = document["services"]["opencode"]
    shared = [mount for mount in code["volumes"] if mount["target"].startswith("/mnt/")]
    assert shared == [
        {
            "type": "bind",
            "source": str(skills),
            "target": "/mnt/odoocker/skills/0",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
    assert {
        "type": "bind",
        "source": str(config),
        "target": "/etc/opencode/opencode.json",
        "read_only": True,
        "bind": {"create_host_path": False},
    } in code["volumes"]
    for name in ("init", "db", "odoo"):
        assert str(skills) not in json.dumps(document["services"][name])
        assert "/etc/opencode" not in json.dumps(document["services"][name])
    assert (skills / "review/scripts/check.py").stat().st_mode & stat.S_IXUSR
    assert not (runtime.directory / "review").exists()  # No synchronization/copy of content.


def test_selected_skills_get_virtual_parents_and_keep_ids_and_order(root, skills):
    runtime = Runtime.load(load(root, [str(skills / "review"), str(skills / "teams/release")]))
    runtime.generate()
    document = runtime.compose_document()
    shared = [
        mount
        for mount in document["services"]["opencode"]["volumes"]
        if mount["target"].startswith("/mnt/")
    ]
    assert [mount["target"] for mount in shared] == [
        "/mnt/odoocker/skills/0/review",
        "/mnt/odoocker/skills/1/release",
    ]
    assert [mount["source"] for mount in shared] == [
        str(skills / "review"),
        str(skills / "teams/release"),
    ]
    assert json.loads((runtime.directory / "opencode-skills.json").read_text())["skills"] == [
        "/mnt/odoocker/skills/0",
        "/mnt/odoocker/skills/1",
    ]
    # A single skill's supporting root-level Markdown is not another skill ID.
    (skills / "review/review.md").write_text("Supporting file, not a second definition\n")
    Workspace.load(root).validate_sources()


def test_same_id_in_different_sources_uses_array_order_without_renaming(root, skills, tmp_path):
    other = tmp_path / "other/review"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\ndescription: Override\n---\n")
    workspace = load(root, [str(skills), str(other)])
    assert [source.directory for source in workspace.skill_sources] == [None, "review"]
    runtime = Runtime.load(workspace)
    runtime.generate()
    config = json.loads((runtime.directory / "opencode-skills.json").read_text())
    # Native explicit sources win by array order; no custom collision/rename machinery.
    assert config["skills"] == ["/mnt/odoocker/skills/0", "/mnt/odoocker/skills/1"]


def test_home_xdg_and_symlinked_profile_resolve_only_the_skills_directory(
    root, tmp_path, monkeypatch
):
    home = tmp_path / "home"
    profile = home / "config/opencode"
    skill = profile / "skills/review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\ndescription: Synthetic\n---\n")
    for name in ("opencode.json", "cli.json", "auth.json"):
        (profile / name).write_text("DO NOT SHARE\n")
    (profile / "plugins").mkdir()
    (home / ".config").mkdir()
    (home / ".config/opencode").symlink_to(profile, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    workspace = load(root, ["~/.config/opencode/skills"])
    assert workspace.skill_sources[0].path == profile / "skills"
    runtime = Runtime.load(workspace)
    runtime.generate()
    host_mounts = [
        mount["source"]
        for mount in runtime.compose_document()["services"]["opencode"]["volumes"]
        if mount["target"].startswith("/mnt/")
    ]
    assert host_mounts == [str(profile / "skills")]
    assert "DO NOT SHARE" not in (runtime.directory / "opencode-skills.json").read_text()
    with pytest.raises(OdxError, match="not a host profile"):
        load(root, ["~/.config/opencode"])


def test_relative_paths_resolve_from_workspace_not_terminal(root, skills, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    relative = os.path.relpath(skills, root)
    assert load(root, [relative]).skill_sources[0].path == skills


def test_selected_symlink_uses_canonical_mount_and_configured_directory_id(root, skills):
    alias = skills.parent / "alias-review"
    alias.symlink_to(skills / "review", target_is_directory=True)
    source = load(root, [str(alias)]).skill_sources[0]
    assert source.path == skills / "review"
    assert source.directory == "alias-review"


@pytest.mark.parametrize("entries", [False, "~/skills", {}, 1, [False], [1], [""], ["  "], ["a\n"]])
def test_strict_local_skill_types(root, entries):
    # JSON literals are also TOML here, except JSON's lowercase booleans are intentional.
    with pytest.raises(OdxError, match="opencode.skill_sources"):
        load(root, entries)


@pytest.mark.parametrize("text", ["opencode = false", "[opencode]\nunknown = []"])
def test_local_opencode_rejects_wrong_table_and_unknown_keys(root, text):
    (root / LOCAL).write_text(text)
    with pytest.raises(OdxError, match="local opencode"):
        Workspace.load(root)


def test_personal_paths_are_not_accepted_in_versioned_manifest(root):
    (root / MANIFEST).write_text(MANIFEST_TEXT + 'skill_sources = ["~/skills"]\n')
    with pytest.raises(OdxError, match="Unknown opencode keys: skill_sources"):
        Workspace.load(root)


@pytest.mark.parametrize("kind", ["missing", "file", "empty", "no-entry"])
def test_source_requires_existing_readable_directory_with_skill_entries(root, tmp_path, kind):
    path = tmp_path / "invalid"
    if kind == "file":
        path.write_text("not a directory")
    elif kind in {"empty", "no-entry"}:
        path.mkdir()
        if kind == "no-entry":
            (path / "scripts").mkdir()
            (path / "scripts/check.py").write_text("pass\n")
    with pytest.raises(OdxError, match="skill source|Skill source|No skills"):
        load(root, [str(path)])


@pytest.mark.parametrize("relative", ["", "review", "teams/release"])
def test_duplicate_and_overlapping_sources_rejected_after_resolution(root, skills, relative):
    with pytest.raises(OdxError, match="Duplicate or overlapping"):
        load(root, [str(skills), str(skills / relative)])


def test_symlink_alias_duplicate_rejected(root, skills):
    alias = skills.parent / "alias"
    alias.symlink_to(skills, target_is_directory=True)
    with pytest.raises(OdxError, match="Duplicate or overlapping"):
        load(root, [str(skills), str(alias)])


@pytest.mark.parametrize("kind", ["flat-directory", "nested"])
def test_duplicate_ids_inside_one_source_rejected(root, skills, kind):
    if kind == "flat-directory":
        (skills / "review.md").write_text("---\ndescription: Duplicate\n---\n")
    else:
        directory = skills / "another/release"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text("---\ndescription: Duplicate\n---\n")
    with pytest.raises(OdxError, match="Duplicate skill ID"):
        load(root, [str(skills)])


def test_relative_internal_links_preserve_supporting_content(root, skills):
    link = skills / "review/scripts/policy.md"
    link.symlink_to("../references/policy.md")
    assert load(root, [str(skills / "review")]).skill_sources
    assert link.read_text() == "Synthetic reference\n"
    assert link.readlink() == Path("../references/policy.md")


@pytest.mark.parametrize("kind", ["absolute", "escape", "broken", "cycle", "directory-cycle"])
def test_nonportable_unsafe_and_cyclic_links_rejected(root, skills, tmp_path, kind):
    link = skills / "review/scripts/link"
    if kind == "absolute":
        link.symlink_to(skills / "review/references/policy.md")
    elif kind == "escape":
        outside = tmp_path / "secret"
        outside.write_text("DO NOT SHARE")
        link.symlink_to("../../../secret")
    elif kind == "broken":
        link.symlink_to("missing")
    elif kind == "cycle":
        link.symlink_to("link")
    else:
        link.symlink_to("..", target_is_directory=True)
    with pytest.raises(OdxError, match="Absolute|escapes|Cannot read|Cyclic"):
        load(root, [str(skills)])


def test_relative_directory_link_inside_source_is_supported(root, skills):
    (skills / "review/policies").symlink_to("references", target_is_directory=True)
    assert load(root, [str(skills / "review")]).skill_sources


def test_selected_skill_cannot_link_to_unselected_sibling(root, skills):
    (skills / "support").mkdir()
    (skills / "support/policy.txt").write_text("Shared supporting reference\n")
    (skills / "review/other").symlink_to("../support", target_is_directory=True)
    # It works only if the entire collection is intentionally shared.
    assert load(root, [str(skills)]).skill_sources
    with pytest.raises(OdxError, match="escapes"):
        load(root, [str(skills / "review")])


def test_special_files_rejected_without_opening_them(root, skills):
    os.mkfifo(skills / "review/pipe")
    with pytest.raises(OdxError, match="only files/directories"):
        load(root, [str(skills)])


@pytest.mark.parametrize("name", ["opencode.json", "opencode.jsonc", "cli.json", "auth.json"])
def test_profile_configuration_at_source_root_is_rejected(root, skills, name):
    (skills / name).write_text("DO NOT SHARE")
    with pytest.raises(OdxError, match="not a host profile"):
        load(root, [str(skills)])


def test_read_errors_are_actionable_domain_errors(root, skills, monkeypatch):
    original = Path.iterdir

    def iterdir(path):
        if path == skills / "review/references":
            raise PermissionError("synthetic unreadable directory")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    with pytest.raises(OdxError, match="Cannot read skill source.*unreadable"):
        load(root, [str(skills)])


def test_generate_revalidates_links_before_replacing_active_configuration(root, skills):
    runtime = Runtime.load(load(root, [str(skills)]))
    runtime.generate()
    before = runtime.compose_file.read_bytes()
    config = (runtime.directory / "opencode-skills.json").read_bytes()
    (skills / "review/escape").symlink_to("../../outside")
    with pytest.raises(OdxError, match="escapes"):
        runtime.generate()
    assert runtime.compose_file.read_bytes() == before
    assert (runtime.directory / "opencode-skills.json").read_bytes() == config


def test_switching_disabling_and_reenabling_only_changes_managed_mounts(root, skills):
    initial = Runtime.load(load(root, [str(skills)]))
    initial.generate()
    native = [
        mount
        for mount in initial.compose_document()["services"]["opencode"]["volumes"]
        if mount["type"] == "volume"
    ]
    script = skills / "review/scripts/check.py"
    before = (script.read_bytes(), script.stat().st_mode, script.stat().st_mtime_ns)
    # Project skills remain in the ordinary workspace bind, never copied/managed.
    project_skill = root / ".opencode/skills/review/SKILL.md"
    project_skill.parent.mkdir(parents=True)
    project_skill.write_text("Project-owned definition\n")
    for entries in ([str(skills / "review")], [], [str(skills)]):
        current = Runtime.load(load(root, entries))
        current.generate()
        mounts = current.compose_document()["services"]["opencode"]["volumes"]
        assert [mount for mount in mounts if mount["type"] == "volume"] == native
        assert current.identity == initial.identity
        assert current.password == initial.password
        assert project_skill.read_text() == "Project-owned definition\n"
        assert (script.read_bytes(), script.stat().st_mode, script.stat().st_mtime_ns) == before
        assert bool([mount for mount in mounts if mount["target"].startswith("/mnt/")]) == bool(
            entries
        )
    # Disabling works even after the host directory is gone; no old source is read/cleaned.
    moved = skills.with_name("moved-skills")
    skills.rename(moved)
    Runtime.load(load(root, [])).generate()
    assert (moved / "review/scripts/check.py").read_bytes() == before[0]


def test_literal_dollars_in_host_paths_and_skill_ids_are_escaped_only_for_compose(root, skills):
    path = skills / "review"
    renamed = path.rename(skills / "$REVIEW")
    runtime = Runtime.load(load(root, [str(renamed)]))
    runtime.generate()
    generated = json.loads(runtime.compose_file.read_text())
    mount = generated["services"]["opencode"]["volumes"][-1]
    assert mount["source"] == str(renamed).replace("$", "$$")
    assert mount["target"] == "/mnt/odoocker/skills/0/$$REVIEW"
    assert json.loads((runtime.directory / "opencode-skills.json").read_text())["skills"] == [
        "/mnt/odoocker/skills/0"
    ]


def test_new_sources_are_stale_until_explicit_generation(root, skills):
    original = Runtime.load(Workspace.load(root))
    original.generate()
    changed = Runtime.load(load(root, [str(skills)]))
    with pytest.raises(OdxError, match="configuration changed"):
        changed.compose("up", "-d", "opencode")
    with pytest.raises(OdxError, match="Other workspace settings changed"):
        opencode_update.saved_configuration(changed)
    changed.generate()
    assert opencode_update.saved_configuration(changed)
    assert topology(changed)[0] == changed.compose_document()


def test_setup_guard_does_not_receive_host_skills_or_personal_profile(root, skills):
    runtime = Runtime.load(load(root, [str(skills)]))
    document = guard_document(runtime, runtime.directory / "guard-fixture")
    assert str(skills) not in json.dumps(document)
    assert "/etc/opencode" not in json.dumps(document)


def test_instances_share_only_readonly_sources_not_generated_configs_or_native_volumes(
    root, skills
):
    workspace = load(root, [str(skills)])
    first = Runtime.load(workspace, "main")
    second = Runtime.load(workspace, "feature")
    for runtime in (first, second):
        runtime.generate()
    assert first.project != second.project
    assert first.directory != second.directory
    assert (first.directory / "opencode-skills.json").is_file()
    assert (second.directory / "opencode-skills.json").is_file()
    assert first.password != second.password


def test_updater_allows_only_version_changes_with_shared_skills(root, skills):
    runtime = Runtime.load(load(root, [str(skills)]))
    runtime.generate()
    newer = replace(runtime, workspace=replace(runtime.workspace, opencode_version="2.0.31"))
    assert opencode_update.saved_configuration(newer)
