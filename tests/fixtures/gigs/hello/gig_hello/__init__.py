"""Test-only gig: one Click group, mounted by gigai as `gigai hello`."""

import json
from importlib.metadata import PackageNotFoundError, version

import click


@click.group()
def hello():
    """test gig: hello"""


@hello.command()
@click.option("--name", default="world")
def greet(name):
    click.echo(f"hello {name}")


@hello.command()
def describe():
    """JSON manifest over stdout (the out-of-process contract)."""
    try:
        dist_version = version("gigai-test-gig-hello")
    except PackageNotFoundError:
        dist_version = None
    click.echo(json.dumps({"gig": {"name": "hello", "description": "Test-only gig: says hello"},
                           "dist": "gigai-test-gig-hello", "version": dist_version, "commands": ["greet"]}))
