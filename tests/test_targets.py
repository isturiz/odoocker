"""Development targets never implicitly include recovery/test/system databases."""

import json
from pathlib import Path

import pytest

from odoocker import targets
from odoocker.errors import OdxError


@pytest.mark.parametrize("name", ["dev", "octubre", "sandbox_nueva", "sample-19.0", "Old"])
def test_explicit_development_names(name):
    assert targets.development_database(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "postgres",
        "template0",
        "template1",
        "odx_test_x",
        "ODX_previous_x",
        "a,b",
        "../dev",
        "dev\n",
        "a" * 64,
        "",
    ],
)
def test_reserved_or_unsafe_development_names(name):
    with pytest.raises(OdxError, match="development database name"):
        targets.development_database(name)


def test_database_leases_preserve_dev_without_aliasing_names(tmp_path):
    assert targets.database_lock(tmp_path, "dev") == tmp_path / "dev-database.lock"
    paths = {targets.database_lock(tmp_path, name) for name in ["Old", "old", "a-b", "a_b"]}
    assert len(paths) == 4
    assert all(path.parent == tmp_path for path in paths)


def record(directory, identifier, **changes):
    value = dict(
        schema=1,
        id=identifier,
        project="project",
        database="octubre",
        status="completed",
        published=True,
        host_finalized=True,
    )
    value.update(changes)
    file = directory / "restores" / identifier / "result.json"
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps(value))


def test_catalog_only_admits_finalized_publications_of_this_instance(tmp_path):
    record(tmp_path, "a", database="octubre")
    record(tmp_path, "b", database="noviembre")
    record(tmp_path, "c", database="unfinished", host_finalized=False)
    record(tmp_path, "d", database="failed", status="failed")
    record(tmp_path, "e", database="foreign", project="other")
    record(tmp_path, "f", database="checkpoint", published=False)
    record(tmp_path, "g", database=None)  # Legacy dev-only restore.
    record(tmp_path, "h", database="wrong-id", id="elsewhere")
    assert targets.published_databases(tmp_path, "project") == ["dev", "noviembre", "octubre"]


def test_missing_catalog_preserves_single_database_default(tmp_path):
    assert targets.published_databases(tmp_path, "project") == ["dev"]


@pytest.mark.parametrize("content", ["{", "[]", "null"])
def test_corrupt_catalog_is_not_silently_ignored(tmp_path, content):
    record(tmp_path, "a")
    (tmp_path / "restores/a/result.json").write_text(content)
    with pytest.raises(OdxError, match="catalog"):
        targets.published_databases(tmp_path, "project")


def test_container_catalog_is_instance_scoped(monkeypatch):
    from odoocker import container

    monkeypatch.setattr(container, "context", lambda: {"instance": "comparison", "project": "p"})
    calls = []
    monkeypatch.setattr(
        targets, "published_databases", lambda *args: calls.append(args) or ["dev", "octubre"]
    )
    assert targets.container_databases() == ["dev", "octubre"]
    assert calls == [(Path("/workspace/.odoocker/instances/comparison"), "p")]


def test_browser_links_select_only_the_explicit_database():
    assert targets.browser_url("http://local", "dev") == "http://local"
    assert targets.browser_url("http://local", "octubre") == "http://local/web?db=octubre"
