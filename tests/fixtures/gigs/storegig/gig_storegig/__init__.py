"""Test-only gig: opens its one declared store through `ctx.store.db` and steps its version."""

import click

from gigai.sdk.store import StoreError, migrate

# STEPS[i] moves the notes store from version i to i + 1.
STEPS = (
    lambda db: db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"),
    lambda db: db.execute("ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"),
)


@click.group()
def storegig():
    """test gig: storegig"""


@storegig.command("open")
@click.pass_obj
def open_store(ctx):
    """Open the notes store and print its path."""
    ctx.store.db(ctx.name, "notes").close()
    click.echo(ctx.store.path(ctx.name, "notes"))


@storegig.command("migrate")
@click.option("--known", type=int, default=len(STEPS), help="How many of STEPS this build of the gig knows.")
@click.pass_obj
def migrate_store(ctx, known):
    """Open the notes store and walk it up to version KNOWN."""
    ctx.store.db(ctx.name, "notes").close()
    try:
        click.echo(f"version {migrate(ctx.home, ctx.store.path(ctx.name, 'notes'), STEPS[:known])}")
    except StoreError as exc:
        raise click.ClickException(str(exc)) from exc


@storegig.command("add")
@click.argument("text")
@click.pass_obj
def add_note(ctx, text):
    """Add one note (the store must be at version 1 or later)."""
    db = ctx.store.db(ctx.name, "notes")
    with db:  # commits
        db.execute("INSERT INTO notes (text) VALUES (?)", (text,))
    db.close()
