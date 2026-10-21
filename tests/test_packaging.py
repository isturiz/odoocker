"""Keep the engine's source, generated lock, CLI and package versions aligned."""

import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest

from odoocker import __version__
from odoocker.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_is_derived_from_runtime_source():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in metadata["project"]
    assert "version" in metadata["project"]["dynamic"]
    assert metadata["tool"]["hatch"]["version"] == {"path": "src/odoocker/__init__.py"}


def test_generated_lock_keeps_engine_version_dynamic():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    (package,) = [package for package in lock["package"] if package["name"] == "odoocker"]
    assert "version" not in package
    assert package["source"] == {"editable": "."}


def test_uv_rebuilds_metadata_when_only_the_version_source_changes():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["tool"]["uv"]["cache-keys"] == [
        {"file": "pyproject.toml"},
        {"file": "src/odoocker/__init__.py"},
    ]


def test_installed_metadata_matches_engine_version():
    assert version("odoocker") == __version__


def test_cli_reports_engine_version(capsys):
    with pytest.raises(SystemExit) as exit:
        main(["--version"])
    assert exit.value.code == 0
    assert capsys.readouterr().out.strip() == f"odoocker {__version__}"
