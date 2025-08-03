import click
import os
import subprocess
from .utils import find_project_root, get_project_name


@click.group()
def db():
    """
    Database utilities for Odoo project (list, drop).
    """
    pass


@db.command("drop", short_help="Drop a database")
@click.argument("database", required=True)
@click.option("--force", is_flag=True, help="Do not prompt for confirmation")
def drop_database(database, force):
    """
    Drop (delete) a database from the Postgres container.
    """
    find_project_root()
    project = get_project_name()
    container_postgre = f"{project}_postgres"
    db_user = os.getenv("POSTGRES_USER", "odoo")
    db_password = os.getenv("POSTGRES_PASSWORD", "odoo")

    if not force:
        confirm = click.confirm(f"Are you sure you want to permanently DROP the database '{
                                database}'?", default=False)
        if not confirm:
            click.echo("❎ Operation cancelled.")
            return

    # 1. Terminate connections
    terminate_sql = (
        f"SELECT pg_terminate_backend(pid) "
        f"FROM pg_stat_activity WHERE datname = '{
            database}' AND pid <> pg_backend_pid();"
    )
    try:
        subprocess.run([
            "docker", "exec", "-e", f"PGPASSWORD={db_password}",
            container_postgre, "psql", "-U", db_user, "-d", "postgres", "-c", terminate_sql
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Error terminating connections: {
                   e.stderr.decode() if e.stderr else str(e)}")
        return

    # 2. Drop the database
    try:
        subprocess.run([
            "docker", "exec", "-e", f"PGPASSWORD={db_password}",
            container_postgre, "dropdb", "-U", db_user, database
        ], check=True, capture_output=True)
        click.echo(f"✅ Database '{database}' dropped successfully.")
    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Error dropping database: {
                   e.stderr.decode() if e.stderr else str(e)}")
    except Exception as e:
        click.echo(f"❌ Error: {e}")


@db.command("list", short_help="List all databases")
def list_database():
    """
    List all databases in the Postgres container.
    """
    find_project_root()
    project = get_project_name()
    container_postgre = f"{project}_postgres"
    db_user = os.getenv("POSTGRES_USER", "odoo")
    db_password = os.getenv("POSTGRES_PASSWORD", "odoo")
    query = "SELECT datname FROM pg_database WHERE datistemplate = false;"
    try:
        subprocess.run([
            "docker", "exec", "-e", f"PGPASSWORD={db_password}",
            container_postgre, "psql", "-U", db_user, "-d", "postgres", "-c", query
        ], check=True)
    except Exception as e:
        click.echo(f"❌ Error: {e}")
