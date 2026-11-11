"""Scout as a gig: the ``gigai.gigs`` entry point ``scout = "gigai.scout.gig:cli"`` (manifest: ``gig.toml`` here).

The loader imports this module only when ``gigai scout ...`` runs or its ``--help`` is rendered; ``gigai.cli``
imports no Scout module.
"""

from __future__ import annotations

import copy

import click

from .learning_cli import learning_group
from .scout_cli import scout_group

# `gigai scout learning list | show | open | import` (0.1.11.10); cli.py registered it until Scout became a gig.
scout_group.add_command(learning_group)


@click.command("describe", hidden=True)
def describe() -> None:
    """Print gig.toml as JSON (the gig contract's hidden ``describe`` verb)."""

    import json
    import tomllib
    from importlib.resources import files

    manifest = tomllib.loads(files(__package__).joinpath("gig.toml").read_text(encoding="utf-8"))
    click.echo(json.dumps(manifest, indent=2, ensure_ascii=False))


class _ScoutGig(type(scout_group)):  # type: ignore[misc] - Scout's own Group class, so its lookup stays first
    """``gigai scout`` plus ``describe``, resolved by name only: not in ``list_commands`` (help, completion) nor in
    ``Group.commands``, whose walk (``agent-context``) documents the verbs ``data/cli/commands.yaml`` describes."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        command = super().get_command(ctx, cmd_name)
        return command if command is not None or cmd_name != "describe" else describe


cli = copy.copy(scout_group)
cli.__class__ = _ScoutGig

__all__ = ["cli"]
