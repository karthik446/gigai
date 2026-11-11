"""Click group mounted by core as `gigai baseline-behavior`.

Heavy imports go inside commands, never at module level (keeps `gigai --help` fast): the work lives in
`baseline.py`, imported by each command. Core passes each command a `gigai.sdk.GigContext` as the Click context
object; it is `None` when the group runs outside gigai.

Exit codes: 0 done (diff: the snapshots match), 1 failed (diff: they differ), 2 bad input or unknown snapshot.
"""

from __future__ import annotations

import json
import tomllib
from importlib import resources
from typing import TYPE_CHECKING, Any, NoReturn

import click

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from gigai.sdk import GigContext


def _manifest() -> dict:
    return tomllib.loads(resources.files(__package__).joinpath("gig.toml").read_text(encoding="utf-8"))


def _context(ctx: GigContext | None) -> GigContext:
    if ctx is None:
        raise click.UsageError("run this through gigai: gigai baseline-behavior ...")
    return ctx


def _fail(message: str, code: int) -> NoReturn:
    click.echo(f"gigai baseline-behavior: {message}", err=True)
    raise click.exceptions.Exit(code)


def _work(call: Callable[[], Any]) -> Any:
    """Run ``call``; a refusal from the gig or from core's store checks is one line and an exit code (core's
    ``GigFolderError`` is a ``ClickException``: Click prints it and exits 1)."""

    from gigai.sdk.store import StoreError

    from .baseline import BaselineError

    try:
        return call()
    except BaselineError as exc:
        _fail(str(exc), exc.code)
    except StoreError as exc:
        _fail(str(exc), 1)


@click.group("baseline-behavior")
def baseline_behavior() -> None:
    """Capture what an HTTP service answers today as snapshots, diff two snapshots, report."""


@baseline_behavior.command("init")
@click.option("--target", required=True, help="Base URL of the HTTP service under test (http or https).")
@click.pass_obj
def init(ctx: GigContext | None, target: str) -> None:
    """Probe TARGET with one GET and save it as the gig's target in config.toml (rewriting a broken one)."""
    from . import baseline

    gig = _context(ctx)

    def run() -> tuple[str, int, Path, list[str]]:
        url = baseline.check_target(target)
        config, dropped = baseline.init_config(gig)
        answer = baseline.fetch(url, baseline.Request("GET", "/"), config["timeout_seconds"])
        config["target"] = url
        return url, answer.status, baseline.write_config(gig, config), dropped

    url, status, path, dropped = _work(run)
    click.echo(f"target {url} answered GET / with {status}; saved in {path}")
    if dropped:
        click.echo(f"dropped from the old config.toml: {', '.join(dropped)}")


@baseline_behavior.command("capture")
@click.option("--snapshot", "name", required=True, help="Name of the new snapshot; snapshots never change.")
@click.option(
    "--requests",
    "requests_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="JSONL file, one request per line: method, path, body. Default: one GET /.",
)
@click.pass_obj
def capture(ctx: GigContext | None, name: str, requests_file: str | None) -> None:
    """Send the requests to the target and store every answer as snapshot NAME."""
    from . import baseline

    gig = _context(ctx)
    result = _work(lambda: baseline.capture(gig, name, baseline.read_requests(requests_file)))
    for response in result["responses"]:
        click.echo(f"{response['status']}  {response['content_sha256'][:12]}  {response['request']}")
    click.echo(f"captured snapshot {name}: {len(result['responses'])} responses from {result['target']}")


@baseline_behavior.command("diff")
@click.argument("a")
@click.argument("b")
@click.pass_obj
def diff(ctx: GigContext | None, a: str, b: str) -> None:
    """Compare snapshot A with snapshot B per request; exit 1 when they differ."""
    from . import baseline

    gig = _context(ctx)
    result = _work(lambda: baseline.diff(gig, a, b))
    for change in result["changes"]:
        before, after = ("-" if status is None else status for status in change["status"])
        click.echo(f"{change['change']:<8} {change['request']}  status {before} -> {after}")
    click.echo(f"{a} vs {b}: {len(result['changes'])} differ, {result['same']} same")
    if result["changes"]:
        raise click.exceptions.Exit(1)


@baseline_behavior.command("report")
@click.option("--snapshot", "name", required=True, help="Snapshot to report on.")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
@click.pass_obj
def report(ctx: GigContext | None, name: str, as_json: bool) -> None:
    """Show snapshot NAME's target, capture time, request count and statuses."""
    from . import baseline

    gig = _context(ctx)
    result = _work(lambda: baseline.report(gig, name))
    if as_json:
        click.echo(json.dumps(result, sort_keys=True))
        return
    click.echo(f"snapshot {name}: {result['requests']} requests to {result['target']} at {result['captured_at']}")
    for status, count in result["statuses"].items():
        click.echo(f"  {status}: {count}")


@baseline_behavior.command("generate-tests")
@click.option("--snapshot", "name", required=True, help="Snapshot to draft characterization tests from.")
@click.option("--dry-run", is_flag=True, help="Print the prompt for model step draft-tests; make no model call.")
@click.pass_obj
def generate_tests(ctx: GigContext | None, name: str, dry_run: bool) -> None:
    """Draft characterization tests from snapshot NAME (only --dry-run ships today)."""
    from . import baseline

    gig = _context(ctx)
    if not dry_run:
        _fail("the draft-tests model step ships later; run with --dry-run to see the prompt it will send", 2)
    click.echo(_work(lambda: baseline.draft_tests_prompt(gig, name)))


@baseline_behavior.command("describe", hidden=True)
def describe() -> None:
    """Print gig.toml as JSON (the out-of-process contract; same document core reads in-process)."""
    click.echo(json.dumps(_manifest(), sort_keys=True))
