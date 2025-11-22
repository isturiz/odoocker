import subprocess
from pathlib import Path

import click

from .utils import find_project_root


@click.command(
    "requirements",
    short_help="Install Python dependencies inside the Odoo container",
)
@click.option(
    "--mode",
    type=click.Choice(["auto", "file"], case_sensitive=False),
    default="file",
    show_default=True,
    help=(
        "auto: detect requirements.txt files inside /mnt/extra-addons.\n"
        "file: use a single requirements file from the host (default: odoo/requirements.txt)."
    ),
)
@click.option(
    "--file",
    "req_file",
    default="odoo/requirements.txt",
    show_default=True,
    help="Path to the requirements file on the host (used only with mode=file).",
)
@click.option(
    "--service",
    default="odoo",
    show_default=True,
    help="Name of the Odoo service in docker compose.",
)
@click.option(
    "--with-cache/--no-cache",
    default=False,
    show_default=True,
    help="Enable or disable pip cache (default: no-cache).",
)
def requirements(mode, req_file, service, with_cache):
    """
    Install Python dependencies inside the Odoo container.

    - mode=auto: scans /mnt/extra-addons inside the container for requirements.txt files.
    - mode=file: installs dependencies from a single requirements file on the host.
    """
    # Ensure we're running from the project root (where compose.yaml is located)
    find_project_root()

    pip_cache_flag = [] if with_cache else ["--no-cache-dir"]
    # Needed in Debian-based Odoo images to allow user-level installs
    pip_break_flag = ["--break-system-packages"]

    mode = mode.lower()

    # ----------------------------------------------------------------------
    # AUTO MODE: Scan addons for requirements.txt files
    # ----------------------------------------------------------------------
    if mode == "auto":
        click.echo(
            "🔍 Auto mode: scanning for requirements.txt inside /mnt/extra-addons in the container..."
        )

        cmd = [
            "docker", "compose", "exec", "-T", service,
            "bash", "-lc",
            (
                "set -e;"
                "echo '🔎 Searching for requirements.txt...';"
                "FOUND=0;"
                "while IFS= read -r -d '' req; do "
                "  FOUND=1;"
                "  echo \"📦 Installing dependencies from $req\";"
                f"  python3 -m pip install {' '.join(pip_cache_flag + pip_break_flag)} -r \"$req\";"
                "done < <(find /mnt/extra-addons -type f -name 'requirements.txt' -print0);"
                "if [ $FOUND -eq 0 ]; then "
                "  echo 'ℹ️ No requirements.txt files were found inside /mnt/extra-addons';"
                "fi"
            ),
        ]

        try:
            subprocess.run(cmd, check=True)
            click.echo("✅ Requirements installation (auto mode) completed")
        except subprocess.CalledProcessError as e:
            raise click.ClickException(
                f"Failed to install dependencies in auto mode. "
                f"Ensure the '{service}' service is running (docker compose up -d)."
            ) from e

    # ----------------------------------------------------------------------
    # FILE MODE: Install requirements from a single host file
    # ----------------------------------------------------------------------
    elif mode == "file":
        req_path = Path(req_file)

        if not req_path.exists():
            raise click.ClickException(
                f"Requirements file not found on host: {req_path}"
            )

        click.echo(
            f"📄 File mode: installing dependencies from {req_path} inside the container..."
        )

        # Parse the file and build a list of package specifiers
        specs: list[str] = []
        for raw_line in req_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # We don't support complex pip flags inside the file
            if line.startswith(("-r ", "--requirement", "-f ", "--find-links")):
                raise click.ClickException(
                    "This command does not support advanced pip directives "
                    "(-r, --requirement, -f, --find-links, etc).\n"
                    "Install these manually or use mode=auto."
                )

            specs.append(line)

        if not specs:
            click.echo(
                "ℹ️ The requirements file is empty (or contains only comments). Nothing to install."
            )
            return

        # Run pip inside the container with the package list as arguments
        cmd = [
            "docker", "compose", "exec", "-T", service,
            "python3", "-m", "pip", "install",
            *pip_cache_flag,
            *pip_break_flag,
            *specs,
        ]

        try:
            subprocess.run(cmd, check=True)
            click.echo("✅ Requirements installation (file mode) completed")
        except subprocess.CalledProcessError as e:
            raise click.ClickException(
                f"Failed to install dependencies from {req_path}. "
                f"Ensure the '{service}' service is running (docker compose up -d)."
            ) from e

    # ----------------------------------------------------------------------
    # Unknown mode
    # ----------------------------------------------------------------------
    else:
        raise click.ClickException(f"Unknown mode: {mode}")

