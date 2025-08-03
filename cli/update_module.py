import click
import subprocess
from .utils import find_project_root, get_project_name


@click.command("update-module")
@click.option("-d", "--database", required=True, help="Database name")
@click.option("-m", "--modules", required=True, help="Module(s) to update (comma-separated or 'all')")
@click.option("--show-logs", is_flag=True, help="Show full Odoo logs")
@click.option("--stop-after-init", is_flag=True, help="Stop server after update")
def update_module(database, modules, show_logs, stop_after_init):
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
