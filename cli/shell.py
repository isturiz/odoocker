import click
import os
import subprocess
import socket
from .utils import find_project_root, get_project_name


@click.command("sh")
@click.argument("service", required=False, default="odoo")
@click.option("-s", "--shell", default="bash", help="Shell to use (bash, sh, zsh, etc.)")
def sh(service, shell):
    """
    Open a shell in a project container. Default: odoo
    Usage: odoo sh [service] [--shell bash]
    """
    find_project_root()
    project = get_project_name()
    container = f"{project}_{service}"

    # Get all running containers' names
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True
    )
    containers = result.stdout.splitlines()
    if container not in containers:
        click.echo(f"❌ Container '{container}' not found.")
        click.echo("Available containers for this project:")
        for name in containers:
            if name.startswith(f"{project}_"):
                click.echo(f"  - {name.replace(project + '_', '')}")
        click.echo(f"\n👉 Try: odoo sh [service]\nFor example: odoo sh odoo")
        return

    click.echo(f"🔗 Connecting to shell in '{container}'...")
    try:
        subprocess.run(["docker", "exec", "-it", container, shell])
    except Exception as e:
        click.echo(f"❌ Error: {e}")


@click.command("ps")
def ps():
    """
    List all running containers for this project.
    """
    project = get_project_name()
    click.echo(f"📦 Containers matching '{project}':")
    subprocess.run(["docker", "ps", "--filter", f"name={project}"])
