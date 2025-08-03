import click
import os
import subprocess
from .utils import find_project_root
from pathlib import Path
import shutil


@click.command("build", short_help="Build/rebuild Docker images dynamically")
@click.option('--no-cache', is_flag=True, help="Build Docker images without cache.")
def build(no_cache):
    """
    Build/rebuild Docker images for the project, selecting the correct Dockerfile by ODOO_VERSION in .env.
    """
    find_project_root()
    odoo_version = os.getenv("ODOO_VERSION")
    if not odoo_version:
        raise click.UsageError("ODOO_VERSION not set in .env")

    src_dockerfile = Path("Dockerfiles") / str(odoo_version) / "Dockerfile"
    dest_dockerfile = Path("Dockerfile")

    if not src_dockerfile.exists():
        raise click.ClickException(
            f"Dockerfile for Odoo version {
                odoo_version} not found at {src_dockerfile}"
        )

    shutil.copy2(src_dockerfile, dest_dockerfile)
    click.echo(f"📝 Copied {src_dockerfile} → {dest_dockerfile}")

    # Build the image
    click.echo("🔨 Building Docker images...")
    result = subprocess.run(["docker", "compose", "build"])
    if result.returncode == 0:
        click.echo("✅ Build completed successfully")
    else:
        raise click.ClickException("Build failed. Check the logs.")
