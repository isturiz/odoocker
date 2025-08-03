import os
import subprocess
import shutil
import datetime
import zipfile
from pathlib import Path
import click
from .utils import find_project_root, get_project_name


@click.command("restore-backup")
@click.option("-d", "--database", required=True, help="Original backup name prefix")
@click.option("--to", "target_db", required=True, help="New database name to restore to")
def restore_backup(database, target_db):
    """
    Restore a database and filestore from a backup (.tar.gz or .zip, Odoo web or custom CLI).
    """
    try:
        project_dir = find_project_root()
        if not project_dir:
            raise click.UsageError("Project not found. Run from project root.")

        project = get_project_name()
        container_postgre = f"{project}_postgres"
        container_odoo = f"{project}_odoo"

        db_user = os.getenv("POSTGRES_USER", "odoo")
        db_password = os.getenv("POSTGRES_PASSWORD", "odoo")

        backups_dir = project_dir / "backups"

        # Gather all matching files
        pattern_files = list(backups_dir.glob(f"{database}*.tar.gz")) + \
            list(backups_dir.glob(f"{database}*.tar.tar.gz")) + \
            list(backups_dir.glob(f"{database}*.zip"))

        exact_files = [backups_dir /
                       database] if (backups_dir / database).exists() else []

        # Combine and deduplicate
        all_files = {file.resolve(): file for file in pattern_files +
                     exact_files}.values()

        # Sort by modification time, newest first
        backup_files = sorted(
            all_files, key=lambda f: f.stat().st_mtime, reverse=True)

        if not backup_files:
            raise click.ClickException(f"No backup found for '{database}'.")

        backup_path = backup_files[0]

        working_dir = Path(
            "/tmp") / f"restore_{target_db}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        os.makedirs(working_dir, exist_ok=True)

        # Extraction
        if backup_path.suffix == ".zip":
            click.echo(f"📦 Extracting ZIP {backup_path.name}...")
            with zipfile.ZipFile(backup_path, "r") as zip_ref:
                zip_ref.extractall(working_dir)
        else:
            click.echo(f"📦 Extracting TAR {backup_path.name}...")
            subprocess.run(["tar", "-xzf", str(backup_path),
                           "-C", str(working_dir)], check=True)

        click.echo("🗑️ Dropping existing database (if any)...")
        subprocess.run([
            "docker", "exec", "-e", f"PGPASSWORD={
                db_password}", container_postgre,
            "dropdb", "--if-exists", "-h", "localhost", "-U", db_user, target_db
        ], check=True)

        click.echo("🧱 Creating new database...")
        subprocess.run([
            "docker", "exec", "-e", f"PGPASSWORD={
                db_password}", container_postgre,
            "createdb", "-h", "localhost", "-U", db_user, target_db
        ], check=True)

        click.echo("📄 Restoring from SQL dump...")
        sql_dump = working_dir / "dump.sql"
        if not sql_dump.exists():
            raise click.ClickException(
                "SQL dump file 'dump.sql' not found in backup.")
        subprocess.run([
            "docker", "cp", str(sql_dump), f"{container_postgre}:/tmp/dump.sql"
        ], check=True)
        subprocess.run([
            "docker", "exec", "-e", f"PGPASSWORD={
                db_password}", container_postgre,
            "psql", "-h", "localhost", "-U", db_user, "-d", target_db, "-f", "/tmp/dump.sql"
        ], check=True)

        # Restore filestore if present
        filestore_src = working_dir / "filestore"
        if filestore_src.exists():
            click.echo("🗃️ Restoring filestore...")
            subprocess.run([
                "docker", "exec", container_odoo, "mkdir", "-p", f"/var/lib/odoo/filestore/{
                    target_db}"
            ], check=True)
            subprocess.run([
                "docker", "cp",
                str(filestore_src) + "/.",
                f"{container_odoo}:/var/lib/odoo/filestore/{target_db}"
            ], check=True)
        else:
            click.echo(
                "⚠️  No filestore found in backup. Skipping filestore restore.")

        shutil.rmtree(working_dir)
        click.echo("✅ Restore completed successfully")

    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Restore error: {
                   e.stderr.decode() if e.stderr else str(e)}")
    except Exception as e:
        click.echo(f"❌ Error: {str(e)}")
