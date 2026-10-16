"""Workspace validation without real Odoo checkouts or machine-local configuration."""

import json

import pytest

from odoocker.config import LOCAL, MANIFEST, Workspace, find_workspace, validate_name
from odoocker.errors import OdxError

VALID_MANIFEST = """\
schema = 1
[workspace]
name = "example"
[odoo]
version = "17.0"
[opencode]
version = "2.0.30"
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / MANIFEST).write_text(VALID_MANIFEST)
    return tmp_path


def fake_source(path, major):
    (path / "odoo").mkdir(parents=True)
    (path / "odoo-bin").write_text("# fake Odoo executable\n")
    (path / "odoo" / "release.py").write_text(
        f"version_info = ({major}, 0, 0, 'final', 0)\n"
        "raise AssertionError('release metadata must never be executed')\n"
    )


def test_load_defaults(root):
    workspace = Workspace.load(root)
    assert workspace.root == root.resolve()
    assert workspace.name == "example"
    assert workspace.odoo_version == "17.0"
    assert workspace.opencode_version == "2.0.30"
    assert workspace.source == root / ".odoocker" / "sources" / "odoo"
    assert workspace.addons == ()
    assert workspace.postgres_version == "16"
    assert workspace.pgvector is False


@pytest.mark.parametrize("major", ["16", "18"])
@pytest.mark.parametrize("vector", [True, False])
def test_postgres_configuration(root, major, vector):
    (root / MANIFEST).write_text(
        VALID_MANIFEST + f'\n[postgres]\nversion = "{major}"\npgvector = {str(vector).lower()}\n'
    )
    workspace = Workspace.load(root)
    assert workspace.postgres_version == major
    assert workspace.pgvector is vector


@pytest.mark.parametrize(
    "settings",
    [
        'postgres = "18"\n',
        "postgres = []\n",
        "[postgres]\nversion = 18\n",
        "[postgres]\nversion = true\n",
        '[postgres]\nversion = "17"\n',
        "[postgres]\nversion = []\n",
        '[postgres]\npgvector = "true"\n',
        "[postgres]\npgvector = 1\n",
        '[postgres]\nimage = "arbitrary"\n',
    ],
)
def test_invalid_postgres_configuration(root, settings):
    (root / MANIFEST).write_text(
        settings + VALID_MANIFEST if not settings.startswith("[") else VALID_MANIFEST + settings
    )
    with pytest.raises(OdxError, match="postgres"):
        Workspace.load(root)


@pytest.mark.parametrize("schema", [None, "true", '"1"', "1.0", "0", "2", "[]", "{}"])
def test_schema_requires_exact_integer_one(root, schema):
    text = VALID_MANIFEST.replace("schema = 1\n", "" if schema is None else f"schema = {schema}\n")
    (root / MANIFEST).write_text(text)
    with pytest.raises(OdxError, match="Manifest schema must be 1"):
        Workspace.load(root)


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("extra = true\n" + VALID_MANIFEST, "manifest"),
        (VALID_MANIFEST.replace('name = "example"', 'name = "example"\nextra = true'), "workspace"),
        (VALID_MANIFEST.replace('version = "17.0"', 'version = "17.0"\nextra = true'), "odoo"),
        (VALID_MANIFEST + "extra = true\n", "opencode"),
    ],
)
def test_unknown_manifest_keys_are_rejected(root, text, label):
    (root / MANIFEST).write_text(text)
    with pytest.raises(OdxError, match=f"Unknown {label} keys: extra"):
        Workspace.load(root)


@pytest.mark.parametrize("table", ["workspace", "odoo", "opencode"])
@pytest.mark.parametrize("value", ['"not a table"', "[]", "false", "1"])
def test_manifest_sections_require_tables(root, table, value):
    lines = VALID_MANIFEST.splitlines()
    start = lines.index(f"[{table}]")
    del lines[start : start + 2]
    (root / MANIFEST).write_text(f"{table} = {value}\n" + "\n".join(lines))
    with pytest.raises(OdxError, match=f"{table} must be a table"):
        Workspace.load(root)


@pytest.mark.parametrize("table", ["workspace", "odoo", "opencode"])
def test_required_sections_cannot_be_omitted(root, table):
    lines = VALID_MANIFEST.splitlines()
    start = lines.index(f"[{table}]")
    del lines[start : start + 2]
    (root / MANIFEST).write_text("\n".join(lines))
    with pytest.raises(OdxError):
        Workspace.load(root)


@pytest.mark.parametrize(
    "value",
    [
        '"17"',
        '"0.0"',
        '"018.0"',
        '"18.1"',
        '"latest"',
        '"../18.0"',
        '"18.0\\n"',
        "17",
        "true",
        "[]",
        "{}",
    ],
)
def test_odoo_version_requires_stable_major_string(root, value):
    (root / MANIFEST).write_text(VALID_MANIFEST.replace('"17.0"', value))
    with pytest.raises(OdxError, match="odoo.version must be a stable major version string"):
        Workspace.load(root)


@pytest.mark.parametrize("version", ["18.0", "20.0", "25.0"])
def test_unvalidated_versions_are_not_rejected_by_a_release_whitelist(root, version):
    (root / MANIFEST).write_text(VALID_MANIFEST.replace('"17.0"', f'"{version}"'))
    assert Workspace.load(root).odoo_version == version


@pytest.mark.parametrize(
    "value", ['"latest"', '"^2.0.30"', '"1.0.30"', '"2.0"', '"v2.0.30"', "2", "true", "[]"]
)
def test_opencode_version_requires_exact_v2_release(root, value):
    (root / MANIFEST).write_text(VALID_MANIFEST.replace('"2.0.30"', value))
    with pytest.raises(OdxError, match="exact OpenCode V2 release"):
        Workspace.load(root)


@pytest.mark.parametrize("version", ["2.0.30", "2.1.0-beta.1"])
def test_opencode_exact_versions(root, version):
    (root / MANIFEST).write_text(VALID_MANIFEST.replace("2.0.30", version))
    assert Workspace.load(root).opencode_version == version


@pytest.mark.parametrize(
    "name",
    [
        None,
        True,
        1,
        [],
        {},
        "",
        "Main",
        "1main",
        "bad.name",
        "bad name",
        "a" * 49,
        "../other",
        "a/b",
        "a\\b",
        "/absolute",
        ".",
        "..",
        "main\n",
    ],
)
def test_bad_names_are_rejected(name):
    with pytest.raises(OdxError, match="workspace.name must match"):
        validate_name(name, "workspace.name")


@pytest.mark.parametrize("name", ["a", "main", "branch_1-test", "a" * 48])
def test_valid_names_are_returned_unchanged(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("value", ["true", "1", "[]", '"../escape"'])
def test_workspace_name_is_validated_on_load(root, value):
    (root / MANIFEST).write_text(VALID_MANIFEST.replace('"example"', value))
    with pytest.raises(OdxError, match="workspace.name must match"):
        Workspace.load(root)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("extra = true", "Unknown local configuration keys: extra"),
        ("[odoo]\nextra = true", "Unknown local odoo keys: extra"),
        ('odoo = "bad"', "local odoo must be a table"),
        ("odoo = []", "local odoo must be a table"),
        ('[odoo]\naddons = "addons"', "must be an array of paths"),
        ("[odoo]\naddons = {}", "must be an array of paths"),
        ("[odoo]\naddons = false", "must be an array of paths"),
        ('[odoo]\nsource = ""', "odoo.source must be a non-empty path"),
        ('[odoo]\nsource = "  "', "odoo.source must be a non-empty path"),
        ("[odoo]\nsource = 1", "odoo.source must be a non-empty path"),
        ("[odoo]\nsource = false", "odoo.source must be a non-empty path"),
        ("[odoo]\nsource = []", "odoo.source must be a non-empty path"),
        ('[odoo]\naddons = [""]', "odoo.addons must be a non-empty path"),
        ("[odoo]\naddons = [1]", "odoo.addons must be a non-empty path"),
        ("[odoo]\naddons = [false]", "odoo.addons must be a non-empty path"),
    ],
)
def test_strict_local_configuration(root, text, message):
    (root / LOCAL).write_text(text)
    with pytest.raises(OdxError, match=message):
        Workspace.load(root)


def test_local_paths_resolve_from_workspace_not_cwd(root, monkeypatch):
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    (root / LOCAL).write_text(
        '[odoo]\nsource = "checkout/../odoo"\naddons = ["addons", "../shared"]\n'
    )
    workspace = Workspace.load(root)
    assert workspace.source == root / "odoo"
    assert workspace.addons == (root / "addons", root.parent / "shared")
    assert workspace.odoo_version == "17.0"


def test_absolute_and_home_paths(root, monkeypatch):
    home = root / "home"
    monkeypatch.setenv("HOME", str(home))
    absolute = root / "external addons"
    (root / LOCAL).write_text(
        f'[odoo]\nsource = "~/source"\naddons = [{json.dumps(str(absolute))}, "~/addons"]\n'
    )
    workspace = Workspace.load(root)
    assert workspace.source == home / "source"
    assert workspace.addons == (absolute, home / "addons")


@pytest.mark.parametrize("other", ["addons", "./addons", "nested/../addons"])
def test_duplicate_addons_are_rejected_after_resolution(root, other):
    (root / LOCAL).write_text(f'[odoo]\naddons = ["addons", "{other}"]\n')
    with pytest.raises(OdxError, match="Duplicate addon paths"):
        Workspace.load(root)


def test_symlink_addon_duplicates_are_rejected(root):
    (root / "addons").mkdir()
    (root / "alias").symlink_to(root / "addons", target_is_directory=True)
    (root / LOCAL).write_text('[odoo]\naddons = ["addons", "alias"]\n')
    with pytest.raises(OdxError, match="Duplicate addon paths"):
        Workspace.load(root)


@pytest.mark.parametrize("as_file", [False, True])
def test_find_workspace_searches_upward(root, as_file):
    start = root / "nested" / "deeper"
    start.mkdir(parents=True)
    if as_file:
        start = start / "file.py"
        start.touch()
    assert find_workspace(start) == root


def test_find_workspace_defaults_to_cwd_and_uses_nearest_manifest(root, monkeypatch):
    nested = root / "nested"
    nested.mkdir()
    (nested / MANIFEST).write_text(VALID_MANIFEST)
    monkeypatch.chdir(nested)
    assert find_workspace() == nested


def test_find_workspace_resolves_symlinks(root):
    target = root / "nested"
    target.mkdir()
    alias = root / "alias"
    alias.symlink_to(target, target_is_directory=True)
    assert find_workspace(alias) == root


def test_find_workspace_reports_missing_manifest(tmp_path):
    with pytest.raises(OdxError, match="No odoocker.toml found above"):
        find_workspace(tmp_path)


@pytest.mark.parametrize("filename", [MANIFEST, LOCAL])
def test_malformed_toml_is_reported_as_domain_error(root, filename):
    (root / filename).write_text("[invalid\n")
    with pytest.raises(OdxError, match="Cannot read"):
        Workspace.load(root)


def test_missing_manifest_is_reported_as_domain_error(tmp_path):
    with pytest.raises(OdxError, match="Cannot read"):
        Workspace.load(tmp_path)


@pytest.mark.parametrize("version", ["17.0", "19.0"])
def test_validate_fake_release_sources(root, version):
    (root / MANIFEST).write_text(VALID_MANIFEST.replace("17.0", version))
    (root / LOCAL).write_text('[odoo]\naddons = ["custom"]\n')
    (root / "custom").mkdir()
    workspace = Workspace.load(root)
    fake_source(workspace.source, version.split(".")[0])
    workspace.validate_sources()


@pytest.mark.parametrize(("expected", "actual"), [("17.0", 19), ("19.0", 17)])
def test_release_version_mismatch(root, expected, actual):
    (root / MANIFEST).write_text(VALID_MANIFEST.replace("17.0", expected))
    workspace = Workspace.load(root)
    fake_source(workspace.source, actual)
    with pytest.raises(OdxError, match=f"does not match Odoo {expected}"):
        workspace.validate_sources()


@pytest.mark.parametrize(
    "metadata", ["", "version_info = (170, 0, 0)", "version_info = (17, 1, 0)"]
)
def test_release_metadata_must_contain_requested_major_and_minor(root, metadata):
    workspace = Workspace.load(root)
    fake_source(workspace.source, 17)
    (workspace.source / "odoo" / "release.py").write_text(metadata)
    with pytest.raises(OdxError, match="does not match Odoo 17.0"):
        workspace.validate_sources()


def test_missing_source(root):
    with pytest.raises(OdxError, match="Odoo source missing"):
        Workspace.load(root).validate_sources()


def test_missing_release_metadata(root):
    workspace = Workspace.load(root)
    workspace.source.mkdir(parents=True)
    (workspace.source / "odoo-bin").touch()
    with pytest.raises(OdxError, match="Missing Odoo release metadata"):
        workspace.validate_sources()


@pytest.mark.parametrize("is_file", [False, True])
def test_addon_must_be_existing_directory(root, is_file):
    (root / LOCAL).write_text('[odoo]\naddons = ["custom"]\n')
    if is_file:
        (root / "custom").touch()
    workspace = Workspace.load(root)
    fake_source(workspace.source, 17)
    with pytest.raises(OdxError, match="Addon directory does not exist"):
        workspace.validate_sources()
