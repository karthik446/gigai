"""GigAI's deliberately minimal installed command surface."""

from __future__ import annotations

import click


@click.command(
    context_settings={"help_option_names": ["--help"]},
    help=(
        "Inspect this contract-first GigAI installation. No operational "
        "commands are implemented yet."
    ),
)
@click.version_option(
    package_name="gigai",
    prog_name="gigai",
    message="%(prog)s %(version)s",
)
def cli() -> None:
    """Expose only installation help and package-metadata version output."""

    raise click.UsageError(
        "No operational command is implemented; use --help or --version."
    )
