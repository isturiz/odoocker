import click

@click.command("install-cli")
@click.option('--local', is_flag=True, help="Install in .bin directory for project-local usage")
def install_cli(local):
    """Install the odoo CLI as a symlink in a local .bin directory (default)"""
    from pathlib import Path

    script_name = "odoo"
    target_dir = Path(".bin") if local else Path.home() / ".local" / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)

    source = Path(__file__).resolve()
    destination = target_dir / script_name

    if destination.exists() or destination.is_symlink():
        destination.unlink()
    destination.symlink_to(source)

    click.echo(f"✔ Symlink created at: {destination} -> {source}")
    if local:
        click.echo("\n👉 Add this to your shell config (or use a wrapper):")
        click.echo(f'  export PATH="{target_dir.resolve()}:$PATH"')
        click.echo("Or run this manually in your terminal:")
        click.echo(f'  export PATH="{target_dir.resolve()}:$PATH"')
    else:
        click.echo("✅ 'odoo' command is now globally available.")
