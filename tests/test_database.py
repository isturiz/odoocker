"""Database/password safety with fake drivers, no Odoo or Docker dependency."""

import io
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from odoocker import cli, container, database
from odoocker.errors import OdxError
from odoocker.locking import locked


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setattr(container, "STATE", root)
    return root


@pytest.fixture
def backend(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    result = SimpleNamespace(
        data=root,
        rows=[
            {
                "name": "dev",
                "oid": 42,
                "bytes": 1024,
                "owner": "odoo",
                "kind": "development",
                "protected": False,
                "connections": 0,
            }
        ],
        dropped=[],
        queries=0,
    )
    result.connect = lambda: SimpleNamespace(close=lambda: None)

    def inventory(connection):
        result.queries += 1
        return [dict(row) for row in result.rows]

    def filestore(name):
        path = root / "filestore" / name
        database.safe_directory(path, root)
        return path

    def drop(connection, name):
        result.dropped.append(name)
        result.rows = [row for row in result.rows if row["name"] != name]

    result.inventory, result.filestore, result.drop_database = inventory, filestore, drop
    path = filestore("dev")
    path.mkdir(parents=True)
    (path / "attachment").write_text("content")
    return result


@pytest.mark.parametrize("name", ["dev", "sample-19.0", "odx_restore_" + "a" * 32])
def test_exact_database_names(name):
    assert database.database_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "..",
        "../dev",
        "/dev",
        "dev\\x",
        "dev*",
        "dev\n",
        "a" * 64,
        "dev;DROP DATABASE dev",
        "--all",
        "dev\0",
    ],
)
def test_unsafe_or_bulk_names_are_rejected(name, backend):
    with pytest.raises(OdxError, match="exact database name"):
        database.drop(backend, name, yes=True)
    assert not backend.dropped


def test_drop_removes_only_exact_pair_and_retains_private_audit(backend, state):
    other = backend.filestore("keep")
    other.mkdir()
    (other / "attachment").write_text("preserved")
    assert database.drop(backend, "dev", yes=True) == 0
    assert backend.dropped == ["dev"]
    assert not backend.filestore("dev").exists()
    assert (other / "attachment").read_text() == "preserved"
    journal = next(state.glob("db-drops/*/result.json"))
    assert json.loads(journal.read_text())["status"] == "completed"
    assert journal.stat().st_mode & 0o077 == 0
    assert journal.parent.stat().st_mode & 0o077 == 0


def test_dry_run_has_no_deletions_or_audit_writes(backend, state):
    assert database.drop(backend, "dev", dry_run=True) == 0
    assert not backend.dropped and not (state / "db-drops").exists()
    assert (backend.filestore("dev") / "attachment").exists()


@pytest.mark.parametrize("change", [{"protected": True}, {"connections": 1}])
def test_protected_and_connected_databases_are_never_dropped(backend, change):
    backend.rows[0].update(change)
    with pytest.raises(OdxError, match="protected|active connections"):
        database.drop(backend, "dev", yes=True)
    assert not backend.dropped


def test_orphan_filestore_is_not_deleted(backend):
    backend.rows = []
    with pytest.raises(OdxError, match="orphan filestores"):
        database.drop(backend, "dev", yes=True)
    assert (backend.filestore("dev") / "attachment").exists()


@pytest.mark.parametrize("answer", ["no", "delete dev", "eof"])
def test_interactive_confirmation(backend, monkeypatch, answer):
    monkeypatch.setattr(database.sys.stdin, "isatty", lambda: True)

    def confirm(prompt):
        if answer == "eof":
            raise EOFError()
        return answer

    monkeypatch.setattr("builtins.input", confirm)
    if answer == "delete dev":
        assert database.drop(backend, "dev") == 0
    else:
        with pytest.raises((OdxError, EOFError)):
            database.drop(backend, "dev")
        assert not backend.dropped


def test_noninteractive_delete_requires_yes(backend, monkeypatch):
    monkeypatch.setattr(database.sys, "stdin", io.StringIO("delete dev\n"))
    with pytest.raises(OdxError, match="explicit --yes"):
        database.drop(backend, "dev")
    assert not backend.dropped


@pytest.mark.parametrize("change", ["oid", "connections", "inode"])
def test_recheck_after_confirmation_rejects_changed_resources(backend, monkeypatch, change):
    monkeypatch.setattr(database.sys.stdin, "isatty", lambda: True)

    def confirm(prompt):
        if change in ("oid", "connections"):
            backend.rows[0][change] += 1
        else:
            path = backend.filestore("dev")
            path.rename(path.with_name("retained"))
            path.mkdir()
        return "delete dev"

    monkeypatch.setattr("builtins.input", confirm)
    with pytest.raises(OdxError, match="changed|active connections"):
        database.drop(backend, "dev")
    assert not backend.dropped


@pytest.mark.parametrize("part", ["data", "filestore", "database"])
def test_symlink_filestore_parents_block_before_database_drop(backend, part):
    path = {
        "data": backend.data,
        "filestore": backend.data / "filestore",
        "database": backend.filestore("dev"),
    }[part]
    outside = path.with_name(path.name + "-outside")
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OdxError, match="Unsafe filestore"):
        database.drop(backend, "dev", yes=True)
    assert not backend.dropped


def test_filestore_child_symlinks_do_not_delete_external_files(backend, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private").write_text("retained")
    (backend.filestore("dev") / "link").symlink_to(outside, target_is_directory=True)
    database.drop(backend, "dev", yes=True)
    assert (outside / "private").read_text() == "retained"


def test_database_drop_failure_keeps_filestore(backend):
    def fail(*args):
        raise RuntimeError("PostgreSQL refused an active connection")

    backend.drop_database = fail
    with pytest.raises(OdxError, match="not confirmed; filestore retained"):
        database.drop(backend, "dev", yes=True)
    assert (backend.filestore("dev") / "attachment").exists()


def test_filestore_failure_records_partial_deletion_and_quarantine(backend, state, monkeypatch):
    def fail(*args):
        raise PermissionError("injected")

    monkeypatch.setattr(database.shutil, "rmtree", fail)
    with pytest.raises(OdxError, match="Database deleted, but filestore cleanup"):
        database.drop(backend, "dev", yes=True)
    record = json.loads(next(state.glob("db-drops/*/result.json")).read_text())
    assert record["database_dropped"] and record["status"] == "filestore_pending"
    assert (Path(record["quarantine"]) / "attachment").read_text() == "content"


def test_database_recreated_after_drop_keeps_filestore(backend, monkeypatch):
    def drop(connection, name):
        backend.rows[0]["oid"] += 1

    monkeypatch.setattr(backend, "drop_database", drop)
    with pytest.raises(OdxError, match="cleanup is incomplete"):
        database.drop(backend, "dev", yes=True)
    assert (backend.filestore("dev") / "attachment").exists()


@pytest.mark.parametrize(
    "status,finalized",
    [("running", False), ("failed", False), ("completed", False), ("completed", True)],
)
def test_recovery_copies_require_matching_successfully_finalized_record(
    tmp_path,
    monkeypatch,
    status,
    finalized,
):
    backend = object.__new__(database.Backend)
    backend.data = tmp_path / "data"
    backend.context = {"project": "project", "instance": "main"}
    restore_id = "a" * 32
    name = "odx_restore_" + restore_id
    record = {"id": restore_id, "project": "project", "status": status, "host_finalized": finalized}
    real_read = Path.read_text

    def read(path, *args, **kwargs):
        if "restores" in path.parts:
            return json.dumps(record)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    if status == "completed" and finalized:
        assert backend.filestore(name) == backend.data / "filestore" / name
    else:
        with pytest.raises(OdxError, match="unfinished restore"):
            backend.filestore(name)


def test_retained_tests_use_their_isolated_filestore_not_dev_data(tmp_path, state):
    backend = object.__new__(database.Backend)
    backend.data = tmp_path / "data"
    run_id = "b" * 32
    name = "odx_test_" + run_id
    directory = state / "tests" / run_id
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(json.dumps({"database": name}))
    assert backend.filestore(name) == directory / "data/filestore" / name


@pytest.mark.parametrize(
    "value,expected",
    [("secret\n", "secret"), ("secret\r\n", "secret"), (" with spaces ", " with spaces ")],
)
def test_password_stdin_is_not_an_argument_and_preserves_spaces(monkeypatch, value, expected):
    monkeypatch.setattr(database.sys, "stdin", io.StringIO(value))
    assert database.read_password(stdin=True) == expected


@pytest.mark.parametrize("value", ["", "\n", "a\nb", "a\0b", "a" * 4097])
def test_empty_multiline_and_oversized_passwords_are_rejected(monkeypatch, value):
    monkeypatch.setattr(database.sys, "stdin", io.StringIO(value))
    with pytest.raises(OdxError, match="one nonempty line"):
        database.read_password(stdin=True)


def test_password_requires_explicit_stdin_mode_without_terminal(monkeypatch):
    monkeypatch.setattr(database.sys, "stdin", io.StringIO("secret"))
    with pytest.raises(OdxError, match="--password-stdin"):
        database.read_password()


@pytest.mark.parametrize("matching", [True, False])
def test_interactive_password_is_confirmed_without_echo(monkeypatch, matching):
    monkeypatch.setattr(database.sys.stdin, "isatty", lambda: True)
    answers = iter(["secret", "secret" if matching else "different"])
    monkeypatch.setattr(database.getpass, "getpass", lambda prompt: next(answers))
    if matching:
        assert database.read_password() == "secret"
    else:
        with pytest.raises(OdxError, match="do not match"):
            database.read_password()


def test_getpass_echo_fallback_is_rejected(monkeypatch):
    import warnings

    monkeypatch.setattr(database.sys.stdin, "isatty", lambda: True)

    def insecure(prompt):
        warnings.warn("cannot disable echo", getpass_warning, stacklevel=2)
        pytest.fail("must not fall back to echoed password input")

    getpass_warning = database.getpass.GetPassWarning
    monkeypatch.setattr(database.getpass, "getpass", insecure)
    with pytest.raises(OdxError, match="Secure password input unavailable"):
        database.read_password()


class User:
    _name = "res.users"
    id = 7
    login = "original-login"
    active = False

    def __len__(self):
        return 1


@pytest.fixture
def environment():
    events = []
    user = User()
    cursor = SimpleNamespace(
        execute=lambda *args: events.append(args),
        fetchone=lambda: ("hash", user.login, user.active),
    )
    users = SimpleNamespace()
    users.sudo = lambda: users
    users.with_context = lambda **kwargs: users
    users.search = lambda domain, **kwargs: user
    users._crypt_context = lambda: SimpleNamespace(
        hash=lambda secret: "hash", verify=lambda secret, hashed: hashed == "hash"
    )
    users._set_encrypted_password = lambda uid, hashed: events.append((uid, hashed))

    class Environment(dict):
        cr = cursor

        def ref(self, xmlid, **kwargs):
            assert xmlid == "base.user_admin"
            return user

    return Environment({"res.users": users}), events, user


@pytest.mark.parametrize("login", [None, "original-login"])
def test_native_password_setter_only_changes_hash_and_preserves_inactive_user(environment, login):
    env, events, user = environment
    assert database.set_password(env, "secret", login) == user.login
    assert events[0] == (7, "hash")
    assert not user.active and user.login == "original-login"
    assert "secret" not in repr(events)


def test_missing_password_capability_fails_without_fallback_hash(environment):
    env, events, user = environment
    env["res.users"]._crypt_context = None
    with pytest.raises(OdxError, match="native password hashing capability"):
        database.set_password(env, "secret")
    assert not events


@pytest.mark.parametrize("case", ["missing", "model", "identity", "hash"])
def test_password_verification_refuses_wrong_account_or_failed_write(environment, case):
    env, events, user = environment
    if case == "missing":
        env.ref = lambda *args, **kwargs: None
    elif case == "model":
        user._name = "other.model"
    elif case == "identity":
        env.cr.fetchone = lambda: ("hash", "changed-login", False)
    else:
        env.cr.fetchone = lambda: ("wrong-hash", user.login, False)
    with pytest.raises(OdxError, match="account|required|verification failed"):
        database.set_password(env, "secret")


@pytest.mark.parametrize("argv", [["db", "drop", "dev", "--yes"], ["passwd", "--password-stdin"]])
def test_mutating_commands_hold_database_and_environment_leases(monkeypatch, backend, argv):
    monkeypatch.setattr(database, "Backend", lambda: backend)

    def work(*args, **kwargs):
        for name in ("dev-database.lock", "environment.lock"):
            with pytest.raises(OdxError, match="Resource busy"), locked(container.STATE / name):
                pytest.fail("maintenance lease missing")
        return 17

    monkeypatch.setattr(database, "drop", work)
    monkeypatch.setattr(database, "passwd", work)
    monkeypatch.setattr(database, "read_password", lambda **kwargs: "secret")
    assert database.execute(cli.parser().parse_args(argv)) == 17


def test_list_json_is_read_only_and_includes_sizes(monkeypatch, backend, capsys):
    monkeypatch.setattr(database, "Backend", lambda: backend)
    assert database.execute(cli.parser().parse_args(["db", "list", "--json"])) == 0
    assert json.loads(capsys.readouterr().out)[0]["bytes"] == 1024
    assert not backend.dropped


@pytest.mark.parametrize("argv", [["db", "list"], ["db", "drop", "dev", "--yes"], ["passwd"]])
def test_busy_environment_blocks_before_database_access(monkeypatch, argv):
    def forbidden():
        pytest.fail("busy lease must be checked before DB access")

    monkeypatch.setattr(database, "Backend", forbidden)
    with locked(container.STATE / "environment.lock"):
        with pytest.raises(OdxError, match="Resource busy"):
            database.execute(cli.parser().parse_args(argv))


def test_worker_sanitizes_driver_errors_including_passwords(monkeypatch, capsys):
    monkeypatch.setattr(database.sys, "argv", ["worker", "db", "list"])

    def fail(args):
        raise RuntimeError("password=secret hashed=also-secret")

    monkeypatch.setattr(database, "execute", fail)
    assert database.main() == 1
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err and "secret" not in captured.err


def test_inventory_classifies_protection_without_loading_odoo():
    backend = object.__new__(database.Backend)
    backend.options = {"db_template": "custom_template"}
    cursor = SimpleNamespace(
        execute=lambda query: None,
        fetchall=lambda: [
            ("dev", 1, 100, "odoo", False, True, True, 0),
            ("postgres", 2, 100, "odoo", False, True, True, 0),
            ("custom_template", 3, 100, "odoo", False, True, True, 0),
            ("foreign", 4, 100, "other", False, True, False, 0),
            ("template", 5, 100, "odoo", True, True, True, 0),
            ("closed", 6, 100, "odoo", False, False, True, 0),
            ("odx_update_" + "a" * 32, 7, 100, "odoo", False, True, True, 0),
        ],
    )
    connection = SimpleNamespace(cursor=lambda: nullcontext(cursor))
    rows = backend.inventory(connection)
    assert [row["protected"] for row in rows] == [False, True, True, True, True, True, False]
    assert rows[-1]["kind"] == "update-checkpoint"


@pytest.mark.parametrize(
    "key,value",
    [
        ("db_host", "production"),
        ("db_port", "5433"),
        ("db_name", "client"),
        ("data_dir", "/outside"),
    ],
)
def test_non_native_database_endpoints_are_rejected(tmp_path, monkeypatch, key, value):
    config = tmp_path / "odoo.conf"
    settings = {
        "db_host": "db",
        "db_port": "5432",
        "db_user": "odoo",
        "db_name": "dev",
        "data_dir": "/home/odoo/.local/share/Odoo",
    }
    settings[key] = value
    config.write_text("[options]\n" + "".join(f"{k} = {v}\n" for k, v in settings.items()))
    monkeypatch.setattr(container, "CONFIG", str(config))
    monkeypatch.setattr(container, "context", lambda: {})
    with pytest.raises(OdxError, match="generated instance configuration"):
        database.Backend()
