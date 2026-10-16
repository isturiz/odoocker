"""Strict, bounded extraction of native Odoo ZIP backups."""

import hashlib
import json
import re
import shutil
import stat
import zipfile
from pathlib import PurePosixPath

from ..errors import OdxError


def inventory(archive):
    seen = set()
    files = archive.infolist()
    if len(files) > 2_000_000:
        raise OdxError("Backup contains too many entries")
    for item in files:
        name = item.filename
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or "\x00" in name
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != name.rstrip("/")
            or name in seen
            or item.flag_bits & 1
        ):
            raise OdxError("Unsafe, encrypted, or duplicate ZIP entry")
        seen.add(name)
        mode = item.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise OdxError("ZIP links and special files are not supported")
        if not (
            name in ("dump.sql", "manifest.json", "filestore/")
            or re.fullmatch(r"filestore/[a-f0-9]{2}/(?:[a-f0-9]{40})?", name)
            or re.fullmatch(r"filestore/checklist/(?:[a-f0-9]{2}/(?:[a-f0-9]{40})?)?", name)
            or name == "filestore/checklist/"
        ):
            raise OdxError("Unsupported backup entry; expected native Odoo SQL and filestore")
    if "dump.sql" not in seen or archive.getinfo("dump.sql").is_dir():
        raise OdxError("Backup must contain dump.sql")
    if "manifest.json" in seen and archive.getinfo("manifest.json").file_size > 1024 * 1024:
        raise OdxError("Backup manifest is too large")
    return files


def extract(source, destination, *, version, postgres):
    if destination.exists():
        raise OdxError("Extraction destination already exists; inspect retained restore artifacts")
    destination.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(source) as archive:
            files = inventory(archive)
            needed = sum(item.file_size for item in files)
            if shutil.disk_usage(destination).free < needed + 256 * 1024**2:
                raise OdxError("Insufficient space to extract the backup")
            if "manifest.json" in archive.namelist():
                manifest = json.loads(archive.read("manifest.json"))
                if not isinstance(manifest, dict):
                    raise OdxError("Invalid Odoo backup manifest")
                backup_version = str(manifest.get("version", ""))
                major = re.match(r"(\d+\.\d+)(?:\D|$)", backup_version)
                if backup_version and (not major or major[1] != version):
                    raise OdxError("Backup Odoo version differs from the workspace; no migration")
            for item in files:
                target = destination / item.filename
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(item) as incoming, target.open("xb") as output:
                    shutil.copyfileobj(incoming, output, 1024 * 1024)
                target.chmod(0o600)
            (destination / "filestore").mkdir(exist_ok=True, mode=0o700)
            with (destination / "dump.sql").open("rb") as stream:
                header = stream.read(8192)
            if header.startswith(b"PGDMP"):
                raise OdxError("Custom-format dumps are not supported; use a native Odoo SQL ZIP")
            match = re.search(rb"-- Dumped from database version (\d+)", header)
            if match and int(match[1]) > int(postgres):
                raise OdxError("Backup PostgreSQL is newer than the destination; no downgrade")
    except (zipfile.BadZipFile, UnicodeError, ValueError) as error:
        raise OdxError("Invalid or corrupt Odoo ZIP backup") from error


def verify_attachments(root, rows):
    count = 0
    for name, minimum, maximum, size, maximum_size in rows:
        if not re.fullmatch(r"[a-f0-9]{2}/[a-f0-9]{40}", name):
            raise OdxError("Unsafe attachment path in restored database")
        path = root / name
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            raise OdxError("Referenced attachment missing or not a regular file")
        if minimum != maximum or size != maximum_size or path.stat().st_size != size:
            raise OdxError("Inconsistent attachment metadata or size")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha1").hexdigest()
        if digest != minimum:
            raise OdxError("Attachment checksum mismatch")
        count += 1
    return count
