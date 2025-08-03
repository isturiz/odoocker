import click
import os
import subprocess
from .utils import find_project_root, get_project_name


@click.command("manage-user")
@click.option("-d", "--database", required=True, help="Database name")
@click.option("-u", "--user", default=None, help="User to update")
@click.option("--list-admins", is_flag=True, help="List admin users")
@click.option("-p", "--password", help="Custom password to set (default from .env)")
def manage_users(database, user, list_admins, password):
    try:
        project_dir = find_project_root()
        if not project_dir:
            raise click.UsageError(
                "Docker project not found. Execute from project directory.")

        project = get_project_name()
        container_postgre = f"{project}_postgres"
        container_odoo = f"{project}_odoo"

        db_user = os.getenv("POSTGRES_USER", "odoo")
        db_password = os.getenv("POSTGRES_PASSWORD", "odoo")
        new_password = password or os.getenv("RESET_PASSWORD", "admin")

        if list_admins:
            query = """
                SELECT ru.login, rp.name 
                FROM res_users ru 
                JOIN res_groups_users_rel gurel ON ru.id = gurel.uid 
                JOIN res_groups rg ON gurel.gid = rg.id 
                JOIN ir_model_data imd ON imd.res_id = rg.id AND imd.model = 'res.groups' 
                JOIN res_partner rp ON ru.partner_id = rp.id 
                WHERE imd.module = 'base' AND imd.name = 'group_system';
            """
            cmd = [
                "docker", "exec", "-e", f"PGPASSWORD={db_password}",
                container_postgre, "psql",
                "--host", "localhost",
                "-U", db_user,
                "-d", database,
                "-c", query,
            ]
            click.echo("👔 Admin users:")
            subprocess.run(cmd, check=True)
            return

        if not user:
            raise click.UsageError(
                "must specify user with -u or use --list-admins")

        query = f"""
            UPDATE res_users
            SET password = '{new_password}'
            WHERE login = '{user}';
        """
        cmd = [
            "docker", "exec", "-e", f"PGPASSWORD={db_password}",
            container_postgre, "psql",
            "--host", "localhost",
            "-U", db_user,
            "-d", database,
            "-c", query,
        ]
        subprocess.run(cmd, check=True)
        click.echo(f"✅ Password for {user} reset successfully")

    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Database error: {
                   e.stderr.decode() if e.stderr else str(e)}")
    except Exception as e:
        click.echo(f"❌ Error: {str(e)}")


@click.command("update-modules")
@click.option("-d", "--database", required=True, help="Database name")
@click.option("-m", "--modules", required=True, help="Module(s) to update (comma-separated or 'all')")
@click.option("--show-logs", is_flag=True, help="Show full Odoo logs")
@click.option("--stop-after-init", is_flag=True, help="Stop server after update")
def update_modules(database, modules, show_logs, stop_after_init):
    """Update Odoo modules using Docker"""
    try:
        project_dir = find_project_root()
        if not project_dir:
            raise click.UsageError(
                "Docker project not found. Execute from project directory.")

        project = get_project_name()
        container_odoo = f"{project}_odoo"

        update_cmd = [
            "docker", "exec", "-u", "odoo", container_odoo,
            "odoo", "-d", database, "-u", modules
        ]

        if stop_after_init:
            update_cmd.append("--stop-after-init")

        click.echo(f"🔄 Updating modules: {modules}...")
        result = subprocess.run(
            update_cmd,
            capture_output=not show_logs,
            text=True
        )

        if result.returncode == 0:
            click.echo(f"✅ Modules updated successfully in {database}")
        else:
            error_msg = result.stderr if result.stderr else "Check logs with --show-logs"
            raise click.ClickException(f"Module update failed: {error_msg}")

    except subprocess.CalledProcessError as e:
        click.echo(f"❌ Docker error: {e.stderr if e.stderr else str(e)}")
    except Exception as e:
        click.echo(f"❌ Error: {str(e)}")
