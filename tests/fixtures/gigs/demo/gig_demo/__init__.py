"""Test-only gig: core supervises its server (`[server] mode = "process"`); its group has one verb of its own."""

import click


@click.group()
def demo():
    """test gig: demo"""


@demo.command("hello")
def hello():
    """Print a greeting (the gig's own verb; core adds run, stop and status)."""
    click.echo("hello from demo")
