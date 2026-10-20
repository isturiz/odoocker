"""Native report parameters, scoped to initialized dev without loading Odoo."""

import json
import sys
from types import SimpleNamespace

import pytest

from odoocker import reports
from odoocker.errors import OdxError


class DatabaseError(Exception):
    pass


@pytest.fixture
def database(tmp_path, monkeypatch):
    config = tmp_path / "odoo.conf"
    config.write_text(
        "[options]\ndb_name=dev\ndb_host=db\ndb_port=5432\ndb_user=odoo\n"
        "db_password=private-value\nhttp_port=8077\n"
    )
    state = SimpleNamespace(
        config=config,
        owned=(True,),
        tables=("ir_config_parameter", "ir_module_module"),
        base=("installed",),
        parameters={
            "web.base.url": "http://client-main.localhost:8080",
            "web.base.url.freeze": "True",
        },
        connections=[],
        queries=[],
        writes=[],
        fail=None,
    )

    class Connection:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            state.connections.append(self)

        def __enter__(self):
            self.before = state.parameters.copy()
            return self

        def __exit__(self, kind, error, traceback):
            if error is not None:
                state.parameters = self.before

        def close(self):
            self.closed = True

        def cursor(self):
            return Cursor(self)

    class Cursor:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, params=None):
            state.queries.append((self.connection.kwargs["dbname"], query, params))
            if "FROM pg_database" in query:
                assert params == ["dev"]
                self.row = state.owned
            elif "to_regclass" in query:
                self.row = state.tables
            elif "FROM public.ir_module_module" in query:
                assert params == ["base"]
                self.row = state.base
            elif query.startswith("SELECT value"):
                assert params == ["report.url"]
                self.row = (
                    (state.parameters["report.url"],) if "report.url" in state.parameters else None
                )
            elif query.startswith("INSERT INTO public.ir_config_parameter"):
                state.writes.append(params)
                state.parameters[params[0]] = params[1]
                if state.fail:
                    raise DatabaseError(state.fail)
            else:
                pytest.fail(f"Unexpected SQL: {query}")

        def fetchone(self):
            return self.row

    monkeypatch.setitem(
        sys.modules, "psycopg2", SimpleNamespace(connect=Connection, Error=DatabaseError)
    )
    return state


@pytest.mark.parametrize("existing", [None, "https://production.invalid", "http://127.0.0.1:8077"])
def test_prepare_only_sets_native_report_url_and_preserves_browser_url(database, existing):
    if existing is not None:
        database.parameters["report.url"] = existing
    before = database.parameters.copy()
    assert reports.prepare(database.config) is True
    assert database.parameters == {**before, "report.url": "http://127.0.0.1:8077"}
    assert [c.kwargs["dbname"] for c in database.connections] == ["postgres", "dev"]
    assert all(c.closed and c.kwargs["connect_timeout"] == 10 for c in database.connections)
    assert database.writes == (
        [] if existing == "http://127.0.0.1:8077" else [["report.url", "http://127.0.0.1:8077"]]
    )
    writes = len(database.writes)
    assert reports.prepare(database.config) is True
    assert len(database.writes) == writes


def test_absent_dev_is_not_created(database):
    database.owned = None
    assert reports.prepare(database.config) is False
    assert len(database.connections) == 1
    assert not database.writes


def test_foreign_or_protected_dev_is_rejected(database):
    database.owned = (False,)
    with pytest.raises(OdxError, match="owned, connectable"):
        reports.prepare(database.config)
    assert len(database.connections) == 1
    assert not database.writes
    assert database.connections[0].closed


@pytest.mark.parametrize(
    "tables", [(None, None), (None, "ir_module_module"), ("ir_config_parameter", None)]
)
def test_empty_or_partial_schema_is_not_prepared(database, tables):
    database.tables = tables
    assert reports.prepare(database.config) is False
    assert not database.writes


@pytest.mark.parametrize("base", [None, ("uninstalled",), ("to install",), ("to remove",)])
def test_uninitialized_base_is_not_prepared(database, base):
    database.base = base
    assert reports.prepare(database.config) is False
    assert not database.writes


def test_pending_explicit_base_upgrade_can_prepare_reports(database):
    database.base = ("to upgrade",)
    assert reports.prepare(database.config) is True


@pytest.mark.parametrize(
    "key,value",
    [
        ("db_name", "postgres"),
        ("db_name", "odx_test_123"),
        ("db_name", "sandbox_copy"),
        ("db_host", "other"),
        ("db_port", "5433"),
        ("db_user", "postgres"),
    ],
)
def test_report_preparation_rejects_non_generated_targets_before_connecting(database, key, value):
    text = database.config.read_text()
    original = {"db_name": "dev", "db_host": "db", "db_port": "5432", "db_user": "odoo"}[key]
    database.config.write_text(text.replace(f"{key}={original}", f"{key}={value}"))
    with pytest.raises(OdxError, match="generated dev database"):
        reports.prepare(database.config)
    assert not database.connections


@pytest.mark.parametrize("port", ["0", "65536", "-1", "invalid", ""])
def test_invalid_report_http_port_rejected_before_connecting(database, port):
    database.config.write_text(
        database.config.read_text().replace("http_port=8077", f"http_port={port}")
    )
    with pytest.raises(OdxError, match="HTTP port"):
        reports.prepare(database.config)
    assert not database.connections


def test_default_report_port():
    assert reports.internal_url({}) == "http://127.0.0.1:8069"


def test_missing_config_rejected_before_connecting(database):
    with pytest.raises(OdxError, match="Missing generated"):
        reports.prepare(database.config.with_name("absent"))
    assert not database.connections


def test_report_write_failure_rolls_back_and_closes_connections(database):
    before = database.parameters.copy()
    database.fail = "injected write failure"
    with pytest.raises(DatabaseError, match="injected"):
        reports.prepare(database.config)
    assert database.parameters == before
    assert all(c.closed for c in database.connections)


@pytest.mark.parametrize("initialized", [False, True])
def test_report_worker_json_protocol(database, monkeypatch, capsys, initialized):
    database.owned = (True,) if initialized else None
    monkeypatch.setattr(sys, "argv", ["reports", "--config", str(database.config)])
    assert reports.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"initialized": initialized}
    assert not captured.err


def test_report_worker_failure_has_no_success_json(database, monkeypatch, capsys):
    database.fail = "injected write failure"
    monkeypatch.setattr(sys, "argv", ["reports", "--config", str(database.config)])
    assert reports.main() == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "injected write failure" in captured.err
    assert "private-value" not in captured.err
