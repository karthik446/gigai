"""``gigai gig list | info | check``: inspect installed gigs (``gigai.gigs`` entry points) and gig manifests.

Nothing here imports a gig except ``gig check <name>`` items 4-6, which must load it. Registered with one
``cli.add_command(gig_group)`` in ``gigai.cli``; later packets add ``new`` and ``install|update|uninstall``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from . import manifest as gig_manifest
from .check import FAIL, Result, check_file, check_installed
from .loader import LazyGig
from .manifest import InstalledGig, ManifestError, gig_table


@click.group("gig")
def gig_group() -> None:
    """Inspect installed gigs and check gig manifests."""


@gig_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Print one JSON object.")
@click.pass_context
def gig_list_command(context: click.Context, as_json: bool) -> None:
    """List installed gigs with their status (ok, incompatible, invalid, cannot load, not mounted)."""

    installed = gig_manifest.installed_core_version()
    root = context.find_root().command
    rows = [_list_row(gig, installed, root) for gig in gig_manifest.discover()]
    if as_json:
        click.echo(json.dumps({"schema_version": "1.0", "core_version": installed, "gigs": rows}, indent=2, sort_keys=True))
        return
    if not rows:
        click.echo("No installed gigs.")
        return
    click.echo("NAME  STATUS  VERSION  DIST  DETAIL")
    for row in rows:
        detail = row["detail"] if row["detail"] is not None else row["description"] or ""
        click.echo(f"{row['name']}  {row['status']}  {row['version'] or '-'}  {row['dist'] or '-'}  {detail}")


def _list_row(gig: InstalledGig, installed: str, root: click.Command) -> dict[str, Any]:
    status, detail = gig.status(installed)
    mounted, why = _mounted(gig, root)
    if not mounted:
        status, detail = "not mounted", why
    table = gig_table(gig.manifest, "gig")
    return {
        "name": gig.name,
        "dist": gig.dist,
        "version": gig.version,
        "entry_point": gig.entry_point.value,
        "description": table.get("description") if isinstance(table.get("description"), str) else None,
        "core": table.get("core") if isinstance(table.get("core"), str) else None,
        "mounted": mounted,
        "status": status,
        "detail": detail,
    }


def _mounted(gig: InstalledGig, root: click.Command) -> tuple[bool, str | None]:
    """Whether ``gigai <name>`` is this gig (P1's loader skips a name a core verb or another dist already took)."""

    command = root.commands.get(gig.name) if isinstance(root, click.Group) else None
    if isinstance(command, LazyGig):
        if command.entry_point.value == gig.entry_point.value and _dist_name(command) == gig.dist:
            return True, None
        return False, f"{gig.name!r} is already mounted from {_dist_name(command) or command.entry_point.value}"
    if command is None:
        return False, f"{gig.name!r} was not mounted in this process"
    return False, f"{gig.name!r} is a gigai command"


def _dist_name(command: LazyGig) -> str | None:
    dist = command.entry_point.dist
    return dist.name if dist is not None else None


@gig_group.command("info")
@click.argument("gig")
def gig_info_command(gig: str) -> None:
    """Show one installed gig (or a gig.toml file): status, core range, verbs, keys, data dirs, docs."""

    installed = gig_manifest.installed_core_version()
    found, path_manifest, path = _resolve(gig)
    if found is not None:
        if found.manifest is None:
            raise click.ClickException(f"gig {found.name!r} ({found.dist}): cannot load ({found.problem})")
        status, detail = found.status(installed)
        click.echo(f"{found.name} {found.version or '-'} ({found.dist or found.entry_point.value})")
        _print_info(found.manifest, status, detail, found.manifest_path)
        return
    assert path_manifest is not None and path is not None
    status, detail = gig_manifest.manifest_status(path_manifest, gig_manifest.manifest_errors(path_manifest), installed)
    click.echo(f"{gig_table(path_manifest, 'gig').get('name', '?')} (manifest file, not installed)")
    _print_info(path_manifest, status, detail, path)


def _print_info(manifest: dict[str, Any], status: str, detail: str | None, path: Path | None) -> None:
    table = gig_table(manifest, "gig")
    commands = gig_table(manifest, "commands")
    if isinstance(table.get("description"), str):
        click.echo(f"  {table['description']}")
    click.echo(f"  status: {status}" + (f" ({detail})" if detail else ""))
    click.echo(f"  core: {table.get('core', '-')}")
    if "group" in commands:
        click.echo(f"  commands: group {commands['group']}")
    elif "executable" in commands:
        click.echo(f"  commands: executable {commands['executable']}")
    click.echo(f"  provides: {_joined(commands.get('provides'))}")
    keys = manifest.get("keys")
    key_lines = [_key_line(key) for key in keys if isinstance(key, dict)] if isinstance(keys, list) else []
    click.echo(f"  keys: {', '.join(key_lines) or '-'}")
    click.echo(f"  data dirs: {_joined(gig_table(manifest, 'data').get('dirs'))}")
    click.echo(f"  docs page: {gig_table(manifest, 'docs').get('page', '-')}")
    server = gig_table(manifest, "server")
    if server:
        click.echo(f"  server: {server.get('mode', '?')} at {server.get('mount_path', '-')}")
    for kind, granted in gig_table(manifest, "permissions").items():
        click.echo(f"  permissions ({kind}): {_joined(granted)}")
    if path is not None:
        click.echo(f"  manifest: {path}")


def _joined(values: Any) -> str:
    return ", ".join(map(str, values)) if isinstance(values, list) and values else "-"


def _key_line(key: dict[str, Any]) -> str:
    parts = [f"env {key['env']}"] if isinstance(key.get("env"), str) else []
    parts.append("required" if key.get("required") else "optional")
    return f"{key.get('service', '?')} ({', '.join(parts)})"


@gig_group.command("check")
@click.argument("gig")
def gig_check_command(gig: str) -> None:
    """Check an installed gig (or a gig.toml file) against the gig contract; exit 1 on any FAIL."""

    installed = gig_manifest.installed_core_version()
    found, path_manifest, path = _resolve(gig)
    if found is not None:
        label = f"{found.name} ({found.dist or found.entry_point.value})"
        results = check_installed(found, installed=installed)
    else:
        assert path_manifest is not None
        label = str(path)
        results = check_file(path_manifest, installed=installed)
    click.echo(f"gigai gig check {label}")
    for result in results:
        click.echo(_result_line(result))
    counts = {outcome: sum(1 for result in results if result.outcome == outcome) for outcome in ("PASS", "FAIL", "WARN", "SKIP")}
    click.echo(", ".join(f"{count} {outcome}" for outcome, count in counts.items()))
    if counts[FAIL]:
        raise click.exceptions.Exit(1)


def _result_line(result: Result) -> str:
    return f"{result.outcome:<4}  {result.item:>4}  {result.message}"


def _resolve(target: str) -> tuple[InstalledGig | None, dict[str, Any] | None, Path | None]:
    """An installed gig by name, or a manifest file (mapped back to its installed gig when it is one's manifest)."""

    path = Path(target)
    if target.endswith(".toml") or path.exists():
        if not path.is_file():
            raise click.ClickException(f"{target}: no such manifest file")
        for gig in gig_manifest.discover():
            if gig.manifest_path is not None and gig_manifest.same_file(gig.manifest_path, path):
                return gig, None, None
        try:
            return None, gig_manifest.read_manifest_file(path), path
        except ManifestError as exc:
            raise click.ClickException(str(exc)) from exc
    matches = [gig for gig in gig_manifest.discover() if gig.name == target]
    if not matches:
        raise click.ClickException(f"no installed gig named {target!r}; `gigai gig list` shows the installed gigs")
    return matches[0], None, None
