import os
import subprocess
import shutil
import datetime
from pathlib import Path
import click
from .utils import find_project_root, get_project_name, container_path_exists


@click.command("create-backup")
@click.option("-d", "--database", required=True, help="Database name")
def create_backup(database):
    """Create a backup of the database and filestore"""
    try:
        project_dir = find_project_root()
        if not project_dir:
            raise click.UsageError("Project not found. Run from project root.")

        project = get_project_name()
        container_postgre = f"{project}_postgres"
        container_odoo = f"{project}_odoo"

        db_user = os.getenv("POSTGRES_USER", "odoo")
        db_password = os.getenv("POSTGRES_PASSWORD", "odoo")

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        backup_name = f"{database}_{timestamp}"
        working_dir = Path("/tmp") / backup_name
        backups_dir = project_dir / "backups"
        filestore_path = f"/var/lib/odoo/filestore/{database}"

        os.makedirs(working_dir, exist_ok=True)
        os.makedirs(backups_dir, exist_ok=True)

        # 1. Dump in binary format
        click.echo("📦 Dumping database (binary)...")
        subprocess.run([
            "docker", "exec", "-e", f"PGPASSWORD={
                db_password}", container_postgre,
            "pg_dump", "-Fc", "-h", "localhost", "-U", db_user,
            "-d", database, "-f", f"/tmp/{database}.dump"
        ], check=True)
        subprocess.run([
            "docker", "cp", f"{
                container_postgre}:/tmp/{database}.dump", str(working_dir / "dump.dump")
        ], check=True)
        subprocess.run([
            "docker", "exec", container_postgre, "rm", f"/tmp/{database}.dump"
        ], check=True)

        # 2. Dump in plain SQL
        click.echo("📄 Dumping database (plain SQL)...")
        subprocess.run([
            "docker", "exec", "-e", f"PGPASSWORD={
                db_password}", container_postgre,
            "pg_dump", "-Fp", "-h", "localhost", "-U", db_user,
            "-d", database, "-f", f"/tmp/{database}.sql"
        ], check=True)
        subprocess.run([
            "docker", "cp", f"{
                container_postgre}:/tmp/{database}.sql", str(working_dir / "dump.sql")
        ], check=True)
        subprocess.run([
            "docker", "exec", container_postgre, "rm", f"/tmp/{database}.sql"
        ], check=True)

        # 3. Filestore copy
        click.echo("🗃️ Copying filestore...")
        if not container_path_exists(container_odoo, filestore_path):
            click.echo(f"⚠️  Filestore path {
                       filestore_path} not found in container. Continuing backup without filestore (DB may be new or unused).")
        else:
            subprocess.run([
                "docker", "cp", f"{container_odoo}:{
                    filestore_path}", str(working_dir / "filestore")
            ], check=True)

        # 4. Tar.gz compression
        archive_path = backups_dir / f"{backup_name}.gz"
        click.echo(f"🗜️ Compressing to {archive_path}...")
        shutil.make_archive(str(archive_path.with_suffix("")),
                            "gztar", root_dir=working_dir)

        shutil.rmtree(working_dir)
        click.echo("✅ Backup completed successfully")

    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Backup error: {
                   e.stderr.decode() if e.stderr else str(e)}")
    except Exception as e:
        click.echo(f"❌ Error: {str(e)}")


