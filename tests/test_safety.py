"""Regression tests for configuration changes and trustworthy test results."""

import json
from dataclasses import replace

import pytest

from odoocker.config import Workspace
from odoocker.container import test_summary as parse_summary
from odoocker.errors import OdxError
from odoocker.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    source = tmp_path / "core"
    (source / "odoo").mkdir(parents=True)
    (source / "odoo-bin").touch()
    (source / "odoo" / "release.py").write_text("version_info = (19, 0, 0, 'final', 0)")
    return Runtime.load(Workspace(tmp_path, "example", "19.0", "2.0.30", source))


def test_changed_configuration_blocks_operations_before_docker(runtime, monkeypatch):
    runtime.generate()
    changed = replace(runtime, workspace=replace(runtime.workspace, opencode_version="2.0.31"))
    monkeypatch.setattr(
        "odoocker.runtime.run_command", lambda *a, **kw: pytest.fail("Docker called")
    )
    with pytest.raises(OdxError, match="configuration changed"):
        changed.compose("up", "-d", "odoo")


def test_version_changes_cannot_reuse_existing_database(runtime):
    runtime.generate()
    (runtime.workspace.source / "odoo" / "release.py").write_text("version_info = (17, 0, 0)")
    changed = replace(runtime, workspace=replace(runtime.workspace, odoo_version="17.0"))
    original = runtime.compose_file.read_text()
    with pytest.raises(OdxError, match="across Odoo versions"):
        changed.generate()
    assert runtime.compose_file.read_text() == original


def test_same_version_reconfiguration_keeps_identity_and_credentials(runtime):
    runtime.generate()
    changed = replace(runtime, workspace=replace(runtime.workspace, opencode_version="2.0.31"))
    changed.generate()
    assert changed.identity == runtime.identity
    assert changed.password == runtime.password
    assert "oc2.0.31" in json.loads(changed.compose_file.read_text())["services"]["odoo"]["image"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0 failed, 0 error(s) of 1 tests", {"tests": 1, "failures": 0, "errors": 0}),
        ("1 failed, 2 error(s) of 8 tests", {"tests": 8, "failures": 1, "errors": 2}),
        ("0 failed, 0 error(s) of 0 tests", {"tests": 0, "failures": 0, "errors": 0}),
        ("no summary", {"tests": 0, "failures": 0, "errors": 0}),
        (
            "0 failed, 0 error(s) of 1 tests\n0 failed, 0 error(s) of 4 tests",
            {"tests": 4, "failures": 0, "errors": 0},
        ),
    ],
)
def test_parse_odoo_summary(tmp_path, text, expected):
    logfile = tmp_path / "odoo.log"
    logfile.write_text(text)
    assert parse_summary(logfile) == expected
