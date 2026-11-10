"""Test-only gig: one Click group, mounted by gigai as `gigai hookgig`."""

import click


@click.group()
def hookgig():
    """test gig: hookgig"""


@hookgig.command()
def ping():
    click.echo("pong")
