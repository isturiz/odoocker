"""Compatibility follows exposed capabilities, including unknown/future releases."""

import inspect
from functools import wraps
from types import SimpleNamespace

import pytest

from odoocker import cli
from odoocker.errors import OdxError
from odoocker.odoo_compat import (
    check_python_minimum,
    configure,
    registration_boundary,
    without_demo_argument,
)


def test_configuration_uses_the_exposed_logging_keyword():
    calls = []

    def parse(args, *, setup_logging=False):
        calls.append((args, setup_logging))

    configure(SimpleNamespace(parse_config=parse), ["--no-http"])
    assert calls == [(["--no-http"], True)]


def test_configuration_uses_separate_logger_when_keyword_is_absent():
    calls = []

    def parse(args):
        calls.append(args)

    configure(
        SimpleNamespace(parse_config=parse),
        ["--no-http"],
        init_logger=lambda: calls.append("logger"),
    )
    assert calls == [["--no-http"], "logger"]


def test_configuration_does_not_hide_typeerror_inside_the_native_api():
    calls = []

    def parse(args, setup_logging=False):
        calls.append("parse")
        raise TypeError("real failure inside Odoo")

    with pytest.raises(TypeError, match="real failure inside"):
        configure(
            SimpleNamespace(parse_config=parse), [], init_logger=lambda: calls.append("fallback")
        )
    assert calls == ["parse"]


def test_configuration_rejects_unknown_logging_before_calling_parse():
    with pytest.raises(OdxError, match="logging"):
        configure(SimpleNamespace(parse_config=lambda args: pytest.fail("must not parse")), [])


@pytest.mark.parametrize(
    "action,dest,kind,nargs,expected",
    [
        ("store", "without_demo", "string", 1, "--without-demo=all"),
        ("store", "with_demo", "without_demo", "?", "--without-demo=true"),
        ("store_false", "with_demo", None, 0, "--without-demo"),
        ("store_true", "without_demo", None, 0, "--without-demo"),
    ],
)
def test_demo_flag_is_selected_by_option_definition(action, dest, kind, nargs, expected):
    option = SimpleNamespace(action=action, dest=dest, type=kind, nargs=nargs)
    config = SimpleNamespace(parser=SimpleNamespace(get_option=lambda name: option))
    assert without_demo_argument(config) == expected


@pytest.mark.parametrize(
    "option", [None, SimpleNamespace(action="store", dest="other", type="float", nargs=1)]
)
def test_unknown_demo_semantics_fail_instead_of_guessing(option):
    config = SimpleNamespace(parser=SimpleNamespace(get_option=lambda name: option))
    with pytest.raises(OdxError, match="refusing to guess"):
        without_demo_argument(config)


def loading_with_different_layout(other_environment):
    for entry in other_environment.values():
        entry._register_hook()


def loading_with_no_hook(environment):
    for entry in environment.values():
        entry.other_method()


def loading_with_two_hook_loops(environment):
    for model in environment.values():
        model._register_hook()
    for entry in environment.values():
        entry._register_hook()


def loading_with_nested_hook(environment):
    def inner():
        for entry in environment.values():
            entry._register_hook()

    inner()


def test_registration_boundary_uses_ast_not_a_literal_source_line():
    line, environment = registration_boundary(loading_with_different_layout)
    source, start = inspect.getsourcelines(loading_with_different_layout)
    assert "for entry in" in source[line - start]
    assert environment == "other_environment"


@pytest.mark.parametrize(
    "function", [loading_with_no_hook, loading_with_two_hook_loops, loading_with_nested_hook]
)
def test_unknown_or_ambiguous_registration_boundary_fails_closed(function):
    with pytest.raises(OdxError, match="Unsupported/ambiguous"):
        registration_boundary(function)


def test_registration_boundary_does_not_confuse_wrapped_source_with_executed_code():
    @wraps(loading_with_different_layout)
    def wrapped(environment):
        return loading_with_different_layout(environment)

    with pytest.raises(OdxError, match="Wrapped"):
        registration_boundary(wrapped)


@pytest.mark.parametrize("relative", ["odoo/release.py", "odoo/__init__.py", "odoo/init.py"])
def test_python_requirement_comes_from_source_metadata(tmp_path, relative):
    file = tmp_path / relative
    file.parent.mkdir()
    file.write_text("MIN_PY_VERSION = (3, 13)\n")
    with pytest.raises(OdxError, match="requires Python >= 3.13"):
        check_python_minimum(tmp_path, version=(3, 12, 13))
    assert check_python_minimum(tmp_path, version=(3, 13, 0)) == (3, 13)


def test_python_metadata_is_never_executed_on_the_host(tmp_path):
    package = tmp_path / "odoo"
    package.mkdir()
    sentinel = tmp_path / "unexpected"
    (package / "release.py").write_text(
        f"MIN_PY_VERSION = (3, 10)\nopen({str(sentinel)!r}, 'w').write('unsafe')\n"
    )
    assert check_python_minimum(tmp_path, version=(3, 12, 0)) == (3, 10)
    assert not sentinel.exists()


def test_dynamic_or_conflicting_python_requirements_are_not_guessed(tmp_path):
    package = tmp_path / "odoo"
    package.mkdir()
    release = package / "release.py"
    release.write_text("MIN_PY_VERSION = compute_version()\n")
    with pytest.raises(OdxError, match="literal tuple"):
        check_python_minimum(tmp_path)
    release.write_text("MIN_PY_VERSION = (3, 12)\n")
    (package / "__init__.py").write_text("MIN_PY_VERSION = (3, 13)\n")
    with pytest.raises(OdxError, match="Conflicting"):
        check_python_minimum(tmp_path)


def test_absent_python_metadata_is_not_replaced_with_a_version_table(tmp_path):
    assert check_python_minimum(tmp_path) is None


def test_cli_accepts_unvalidated_stable_major_but_warns_without_corrupting_stdout(tmp_path, capsys):
    assert (
        cli.main(
            ["init", str(tmp_path / "workspace"), "--odoo", "18.0", "--opencode-version", "2.0.31"]
        )
        == 0
    )
    output = capsys.readouterr()
    assert "not live-validated" in output.err
    assert "not live-validated" not in output.out


@pytest.mark.parametrize("version", ["../19.0", "19.1", "latest", "19.0\n"])
def test_cli_rejects_invalid_version_before_creating_files(tmp_path, version):
    directory = tmp_path / "workspace"
    assert (
        cli.main(["init", str(directory), "--odoo", version, "--opencode-version", "2.0.31"]) == 1
    )
    assert not directory.exists()
