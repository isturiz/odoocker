#!/usr/bin/env python3
"""
Clean orphaned attachments from Odoo database.
Removes ir.attachment records that reference missing files in the filestore.
"""
import os
import sys
from pathlib import Path
import psycopg2


def clean_missing_attachments(db_name):
    """Remove attachment records whose files don't exist"""
    from odoo import tools

    config = tools.config

    # Connect to database (use TCP for devcontainer)
    db_host = config.get('db_host') or 'pgdb'
    db_port = config.get('db_port') or 5432

    conn = psycopg2.connect(
        dbname=db_name,
        user=config.get('db_user') or 'odoo',
        password=config.get('db_password') or 'odoo',
        host=db_host,
        port=int(db_port) if db_port else 5432,
    )

    cr = conn.cursor()

    try:
        cr.execute(
            """
            SELECT id, name, store_fname FROM ir_attachment
            WHERE type = 'binary' AND store_fname IS NOT NULL
            """
        )

        attachments = cr.fetchall()
        filestore_base = (
            Path.home() / '.local' / 'share' / 'Odoo' / 'filestore' / db_name
        )

        print(f"Checking {len(attachments)} attachments...")
        print(f"Filestore path: {filestore_base}")

        missing_ids = []
        for att_id, att_name, store_fname in attachments:
            file_path = filestore_base / store_fname
            if not file_path.exists():
                print(f"  Missing: {att_name} ({store_fname})")
                missing_ids.append(att_id)

        if missing_ids:
            placeholders = ','.join(['%s'] * len(missing_ids))
            cr.execute(
                f"DELETE FROM ir_attachment WHERE id IN ({placeholders})",
                missing_ids,
            )
            conn.commit()
            print(f"\nCleaned {len(missing_ids)} orphaned attachments")
        else:
            print("\nAll attachments are valid - no cleanup needed")

    except Exception as exc:
        conn.rollback()
        print(f"Error: {exc}")
        sys.exit(1)
    finally:
        cr.close()
        conn.close()


if __name__ == '__main__':
    os.environ.setdefault("ODOO_CONF", "/workspace/odoo.conf")
    sys.path.insert(0, '/workspace/odoo')

    if len(sys.argv) < 2:
        print("Usage: python clean_missing_attachments.py <database_name>")
        sys.exit(1)

    clean_missing_attachments(sys.argv[1])
