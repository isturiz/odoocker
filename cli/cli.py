import click

from .db import db
from .db_backup import create_backup
from .db_restore import restore_backup
from .update_module import update_modules, manage_users
from .shell import sh, ps, status
from .pgadmin import pgadmin


@click.group(context_settings=dict(help_option_names=['-h', '--help']))
def cli():
    """Unified Odoo CLI tool"""
    pass


cli.add_command(db)
cli.add_command(create_backup)
cli.add_command(restore_backup)
cli.add_command(update_modules)
cli.add_command(manage_users)
cli.add_command(sh)
cli.add_command(ps)
cli.add_command(status)
cli.add_command(pgadmin)

if __name__ == "__main__":
    cli()
