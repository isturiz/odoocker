import click
import os
import webbrowser
from .utils import find_project_root


@click.command("pgadmin")
def pgadmin():
    """
    Open PgAdmin in your browser.
    """
    find_project_root()
    port = os.getenv("PGADMIN_HOST_PORT", "8008")
    url = f"http://localhost:{port}"
    click.echo(f"🌐 Opening PgAdmin at {url}")
    webbrowser.open(url)
