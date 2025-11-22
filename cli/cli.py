import click

from .db import db
from .db_backup import create_backup
from .db_restore import restore_backup
from .update_module import update_module
from .manage_user import manage_user
from .shell import sh, ps
from .build import build
from .install_cli import install_cli
from .odools_config import odools
from .requirements import requirements


@click.group(context_settings=dict(help_option_names=['-h', '--help']))
def cli():
    """Unified Odoo CLI tool"""
    pass


cli.add_command(db)
cli.add_command(create_backup)
cli.add_command(restore_backup)
cli.add_command(update_module)
cli.add_command(manage_user)
cli.add_command(sh)
cli.add_command(ps)
cli.add_command(build)
cli.add_command(install_cli)
cli.add_command(odools)
cli.add_command(requirements)

if __name__ == "__main__":
    cli()
