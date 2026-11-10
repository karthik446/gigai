"""Test-only gig: one command, no store."""

import click


@click.group()
def nostoregig():
    """test gig: nostoregig"""


@nostoregig.command()
def ping():
    click.echo("pong")
