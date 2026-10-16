"""Remove only a completed operation's disposable SQL, never recovery pairs."""

import json
import re
import stat
from pathlib import Path


def cleanup_sql(directory, data_root):
    record = json.loads((directory / "result.json").read_text())
    restore_id = record.get("id", "")
    if (
        not re.fullmatch(r"[a-f0-9]{32}", restore_id)
        or directory.name != restore_id
        or record.get("schema") != 1
        or record.get("status") != "completed"
        or not record.get("published")
        or not record.get("host_finalized")
    ):
        raise ValueError("SQL cleanup requires a successfully finalized restore")
    if record["options"].get("keep_sql", False):
        return {"status": "retained"}
    extracted = data_root / ".odoocker-restores" / restore_id / "extracted"
    # Inspect every parent before touching a file in the generated scratch tree.
    for path in (data_root, data_root / ".odoocker-restores", extracted.parent, extracted):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return {"status": "absent"}
        if not stat.S_ISDIR(mode):
            raise ValueError("Unsafe SQL cleanup directory")
    dump = extracted / "dump.sql"
    try:
        info = dump.lstat()
    except FileNotFoundError:
        return {"status": "absent"}
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Unsafe extracted SQL file")
    receipt = extracted / ".extraction.json"
    if receipt.is_symlink() or json.loads(receipt.read_text()) != {
        "id": restore_id,
        "sha256": record["archive_sha256"],
    }:
        raise ValueError("SQL extraction receipt differs from this operation")
    dump.unlink()
    return {"status": "removed", "bytes": info.st_size}


if __name__ == "__main__":
    import configparser
    import sys

    config = configparser.ConfigParser(interpolation=None)
    config.read("/etc/odoocker/odoo.conf")
    print(json.dumps(cleanup_sql(Path(sys.argv[1]), Path(config["options"]["data_dir"]))))
