"""Untrusted ZIP metadata and pg_dump lexical boundaries, without Odoo or Docker."""

import hashlib
import io
import json
import stat
import zipfile

import pytest

from odoocker.errors import OdxError
from odoocker.restore.archive import extract, inventory, verify_attachments
from odoocker.restore.roles import required_roles


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("GRANT USAGE ON SCHEMA public TO public_user;", ["public_user"]),
        (
            'GRANT SELECT ON TABLE "to" TO "Mixed Case", reader WITH GRANT OPTION;',
            ["Mixed Case", "reader"],
        ),
        (
            "ALTER DEFAULT PRIVILEGES FOR ROLE owner IN SCHEMA public "
            "GRANT SELECT ON TABLES TO reader;",
            ["owner", "reader"],
        ),
        (
            "ALTER DEFAULT PRIVILEGES FOR ROLE owner, other REVOKE ALL ON TABLES FROM PUBLIC;",
            ["other", "owner"],
        ),
        ('ALTER TABLE public.t OWNER TO "a""b";', ['a"b']),
        ("SET SESSION AUTHORIZATION owner; SET ROLE DEFAULT;", ["owner"]),
        ("REVOKE ALL ON TABLE t FROM PUBLIC, CURRENT_USER, SESSION_USER;", []),
        ("GRANT SELECT ON t TO reader GRANTED BY owner;", ["owner", "reader"]),
        (
            "-- GRANT ALL ON t TO ignored;\n/* outer /* nested */ */ "
            "GRANT /* ignored */ USAGE ON SCHEMA public\nTO reader;",
            ["reader"],
        ),
        ("COMMENT ON SCHEMA public IS 'GRANT ALL ON t TO ignored;';", []),
        (
            "CREATE FUNCTION f() RETURNS void AS $body$\n"
            "BEGIN; GRANT ALL ON t TO ignored; END;\n$body$ LANGUAGE plpgsql;",
            [],
        ),
        (
            "COPY public.t (value) FROM stdin;\nGRANT ALL ON t TO ignored;\n"
            "\\! ignored\n\\.\nGRANT SELECT ON t TO reader;",
            ["reader"],
        ),
        (
            "\\restrict abc123\nGRANT USAGE ON SCHEMA public TO reader;\n\\unrestrict abc123\n",
            ["reader"],
        ),
        ('GRANT SELECT ON t TO "public", PUBLIC;', ["public"]),
    ],
)
def test_role_inventory_understands_pg_dump_boundaries(sql, expected):
    assert required_roles(io.StringIO(sql)) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "\\! touch file\n",
        "\\connect production\n",
        "\\i file\n",
        "GRANT SELECT ON t\n\\! cmd\nTO r;",
        "BEGIN; SELECT 1; COMMIT;",
        "CREATE ROLE r;",
        "ALTER USER r SUPERUSER;",
        "DROP DATABASE other;",
        "GRANT pg_read_server_files TO reader;",
        "REVOKE reader FROM other;",
        "COPY t TO PROGRAM 'cmd';",
        "COPY t FROM stdin;\nunterminated\n",
        'GRANT SELECT ON t TO "unterminated;',
        "SELECT $$unterminated;",
        "GRANT SELECT ON t TO reader,;",
        'GRANT SELECT ON t TO "";',
        "SELECT 1",
        "/* unterminated",
    ],
)
def test_unsupported_or_incomplete_dump_fails_closed(sql):
    with pytest.raises(OdxError):
        required_roles(io.StringIO(sql))


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return path


@pytest.mark.parametrize(
    "name",
    [
        "../dump.sql",
        "/dump.sql",
        "filestore/../../secret",
        "filestore\\aa\\file",
        "./dump.sql",
        "filestore//aa",
        "unexpected.txt",
    ],
)
def test_archive_rejects_unsafe_or_unsupported_names(tmp_path, name):
    source = make_zip(tmp_path / "bad.zip", [("dump.sql", "SELECT 1;"), (name, "data")])
    with zipfile.ZipFile(source) as archive, pytest.raises(OdxError):
        inventory(archive)


def test_archive_rejects_symlinks(tmp_path):
    source = tmp_path / "bad.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("dump.sql", "SELECT 1;")
        entry = zipfile.ZipInfo("filestore/" + "a" * 2 + "/" + "a" * 40)
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "/etc/passwd")
    with zipfile.ZipFile(source) as archive, pytest.raises(OdxError, match="links"):
        inventory(archive)


def test_native_archive_extracts_and_checks_referenced_attachments(tmp_path):
    content = b"isolated fixture attachment"
    digest = hashlib.sha1(content).hexdigest()
    fname = digest[:2] + "/" + digest
    source = make_zip(
        tmp_path / "backup.zip",
        [
            ("dump.sql", "-- Dumped from database version 16.13\nSELECT 1;"),
            ("manifest.json", json.dumps({"version": "19.0"})),
            ("filestore/" + fname, content),
        ],
    )
    target = tmp_path / "out"
    extract(source, target, version="19.0", postgres="18")
    assert (
        verify_attachments(
            target / "filestore", [(fname, digest, digest, len(content), len(content))]
        )
        == 1
    )
    assert (target / "dump.sql").stat().st_mode & 0o077 == 0
    (target / "filestore" / fname).write_bytes(b"wrong")
    with pytest.raises(OdxError, match="size"):
        verify_attachments(
            target / "filestore", [(fname, digest, digest, len(content), len(content))]
        )


@pytest.mark.parametrize(
    "dump,manifest,odoo,postgres",
    [
        ("PGDMPcustom", {}, "19.0", "18"),
        ("-- Dumped from database version 18.1\nSELECT 1;", {}, "19.0", "16"),
        ("SELECT 1;", {"version": "17.0"}, "19.0", "18"),
    ],
)
def test_archive_rejects_incompatible_formats_and_majors(tmp_path, dump, manifest, odoo, postgres):
    source = make_zip(
        tmp_path / "backup.zip",
        [
            ("dump.sql", dump),
            ("manifest.json", json.dumps(manifest)),
        ],
    )
    with pytest.raises(OdxError):
        extract(source, tmp_path / "out", version=odoo, postgres=postgres)


def test_crc_failure_is_reported_without_trusting_extracted_files(tmp_path):
    source = make_zip(tmp_path / "bad.zip", [("dump.sql", "SELECT fixture_content;")])
    data = source.read_bytes().replace(b"fixture_content", b"changed_content", 1)
    source.write_bytes(data)
    with pytest.raises(OdxError, match="corrupt"):
        extract(source, tmp_path / "out", version="19.0", postgres="18")


@pytest.mark.parametrize("fname", ["../outside", "aa/not-a-hash", "/absolute"])
def test_attachment_paths_cannot_escape_filestore(tmp_path, fname):
    with pytest.raises(OdxError, match="Unsafe"):
        verify_attachments(tmp_path, [(fname, "a", "a", 1, 1)])
