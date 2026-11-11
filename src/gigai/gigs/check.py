"""``gigai gig check``: the gig conformance checks, items 1-8 and 13 of the framework's list.

1 schema-valid manifest · 2 exactly one entry point, name == ``[gig] name``, value == ``[commands] group`` ·
3 manifest readable without importing the entry-point module or its package · 4 group loads, ``--help`` exits 0, every ``provides`` verb
exists with help · 5 no core verb listed in ``provides`` (a core verb defined in code is reported as
``override: <verb>``) · 6 hidden ``describe`` prints the manifest · 7 ``[config.defaults]`` validate against
``[config] schema`` · 8 docs files present, the CLI manual covers every verb · 13 the gig's own tests pass.

Items 9-12 (dist pins, import weight, home provisioning, tools) belong to later packets. A manifest file
that is not an installed gig's gets item 1 only (plus the core-range line); 2-8 and 13 need the installed gig.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import click

from .loader import _which
from .manifest import (
    CORE_VERBS,
    InstalledGig,
    core_problem,
    entry_points,
    gig_table,
    manifest_errors,
)

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"
_NEEDS_INSTALL = "needs the installed gig: run `gigai gig check <name>`"


@dataclass(frozen=True)
class Result:
    outcome: str
    item: str
    message: str


def check_manifest(manifest: dict[str, Any], *, installed: str) -> list[Result]:
    """Item 1, the description hint for executable gigs and the core-range line; needs only the parsed manifest."""

    errors = manifest_errors(manifest)
    results = [Result(FAIL, "1", f"schema: {error}") for error in errors]
    if not errors:
        results.append(Result(PASS, "1", "schema: valid gig manifest (manifest format 1)"))
    commands = gig_table(manifest, "commands")
    executable = commands.get("executable")
    if "group" not in commands and isinstance(executable, str) and "description" not in gig_table(manifest, "gig"):
        results.append(
            Result(WARN, "1", f"no [gig] description: every `gigai --help` runs `{executable} describe` to get one")
        )
    core = gig_table(manifest, "gig").get("core")
    if isinstance(core, str):
        try:
            problem = core_problem(core, installed)
        except ValueError as exc:
            results.append(Result(FAIL, "1", f"schema: gig/core: {exc}"))
        else:
            if problem is None:
                results.append(Result(PASS, "core", f"core {core} includes gigai {installed}"))
            else:
                results.append(Result(WARN, "core", f"{problem} (`gigai gig list` shows it as incompatible)"))
    return results


def check_file(manifest: dict[str, Any], *, installed: str) -> list[Result]:
    """A manifest file that is not an installed gig's: item 1 and the core range; the rest is skipped."""

    results = check_manifest(manifest, installed=installed)
    results += [Result(SKIP, item, _NEEDS_INSTALL) for item in ("2", "3", "4", "5", "6", "7", "8", "13")]
    return results


def check_installed(gig: InstalledGig, *, installed: str) -> list[Result]:
    """Items 1-8 and 13 for one installed gig."""

    if gig.manifest is None:
        return [Result(FAIL, "1", f"cannot read {gig.name}'s manifest: {gig.problem}")]
    manifest = gig.manifest
    results = check_manifest(manifest, installed=installed)
    results += _entry_point_item(gig, manifest)
    results.append(_no_import_item(gig))
    package_dir = gig.manifest_path.parent if gig.manifest_path is not None else None
    executable = _executable(manifest)
    if executable is not None:
        verbs, loaded = _executable_items(gig.name, executable, manifest, results)
    else:
        verbs, loaded = _group_items(gig, manifest, results)
    if not loaded:
        results += [Result(SKIP, item, "the gig did not load (item 4)") for item in ("5", "6")]
    results.append(_config_item(manifest, package_dir))
    results += _docs_items(gig.name, manifest, package_dir, verbs)
    results.append(_tests_item(gig, package_dir))
    return results


# --- 2, 3 -------------------------------------------------------------------------------------------------


def _entry_point_item(gig: InstalledGig, manifest: dict[str, Any]) -> list[Result]:
    results = []
    dist = gig.entry_point.dist
    if dist is not None:
        own = [ep for ep in entry_points() if ep.dist is not None and _normalize(ep.dist.name) == _normalize(dist.name)]
        if len(own) != 1:
            names = ", ".join(sorted(f"{ep.name} = {ep.value}" for ep in own))
            results.append(Result(FAIL, "2", f"{dist.name} declares {len(own)} gigai.gigs entry points, exactly one allowed: {names}"))
    name = gig_table(manifest, "gig").get("name")
    if name != gig.name:
        results.append(Result(FAIL, "2", f"entry point name {gig.name!r} != [gig] name {name!r}"))
    group = gig_table(manifest, "commands").get("group")
    if group is not None and group != gig.entry_point.value:
        results.append(Result(FAIL, "2", f"entry point value {gig.entry_point.value!r} != [commands] group {group!r}"))
    if not results:
        results.append(Result(PASS, "2", f"entry point gigai.gigs/{gig.name} = {gig.entry_point.value}"))
    return results


_READ_WITHOUT_IMPORT = """\
import json, sys
from importlib.metadata import entry_points
from gigai.gigs.loader import read_manifest
name, value, *modules = sys.argv[1:]
(entry_point,) = [ep for ep in entry_points(group="gigai.gigs") if ep.name == name and ep.value == value]
manifest = read_manifest(entry_point)
print(json.dumps({"imported": [module for module in modules if module in sys.modules], "manifest": manifest}))
"""


def _gig_modules(module: str) -> list[str]:
    """The entry-point module, the package holding it and its top-level package: the gig's own code. The top-level
    package is left out when it is core's ``gigai`` (Scout is ``gigai.scout.gig``), which reading any manifest imports."""

    parts = module.split(".")
    watched = [module, ".".join(parts[:-1]), parts[0] if parts[0] != "gigai" else ""]
    return [name for index, name in enumerate(watched) if name and name not in watched[:index]]


def _no_import_item(gig: InstalledGig) -> Result:
    """In a fresh interpreter (this one may already hold the gig), read the manifest as core does and look at sys.modules."""

    modules = _gig_modules(gig.entry_point.module)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _READ_WITHOUT_IMPORT, gig.name, gig.entry_point.value, *modules],
            capture_output=True, text=True, env=_child_env(), timeout=120, check=False, shell=False,
        )
        report = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return Result(FAIL, "3", f"cannot read the manifest in a fresh interpreter: {exc}")
    if not isinstance(report, dict):
        return Result(FAIL, "3", f"cannot read the manifest in a fresh interpreter: {_last_line(completed.stderr)}")
    if report.get("imported"):
        return Result(FAIL, "3", f"reading the manifest imported {report['imported'][-1]!r}")
    if report.get("manifest") != gig.manifest:
        return Result(FAIL, "3", "a fresh interpreter read a different manifest")
    return Result(PASS, "3", f"manifest read without importing {modules[-1]!r}")


# --- 4, 5, 6 ----------------------------------------------------------------------------------------------


def _executable(manifest: dict[str, Any]) -> str | None:
    commands = gig_table(manifest, "commands")
    executable = commands.get("executable")
    return executable if "group" not in commands and isinstance(executable, str) else None


def _provides(manifest: dict[str, Any]) -> list[str]:
    provides = gig_table(manifest, "commands").get("provides")
    return [verb for verb in provides if isinstance(verb, str)] if isinstance(provides, list) else []


def _core_verbs_listed(manifest: dict[str, Any]) -> list[Result]:
    listed = [verb for verb in _provides(manifest) if verb in CORE_VERBS]
    if listed:
        return [Result(FAIL, "5", f"[commands] provides lists core verb(s) {', '.join(listed)}: core adds them to every gig")]
    return []


def _group_items(gig: InstalledGig, manifest: dict[str, Any], results: list[Result]) -> tuple[list[str], bool]:
    from click.testing import CliRunner

    try:
        group = gig.entry_point.load()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - any failure in gig code is a FAIL line, never a crash
        results.append(Result(FAIL, "4", f"{gig.entry_point.value} cannot load: {type(exc).__name__}: {exc}"))
        return _provides(manifest), False
    if not isinstance(group, click.Group):
        results.append(Result(FAIL, "4", f"{gig.entry_point.value} is not a Click group"))
        return _provides(manifest), False
    runner = CliRunner()
    failures = []
    helped = runner.invoke(group, ["--help"], prog_name=f"gigai {gig.name}")
    if helped.exit_code != 0:
        failures.append(f"`gigai {gig.name} --help` exited {helped.exit_code}")
    context = click.Context(group, info_name=gig.name)
    for verb in _provides(manifest):
        command = group.get_command(context, verb)
        if command is None:
            failures.append(f"provides {verb!r} but the group has no such command")
        elif not (command.help or command.short_help or "").strip():
            failures.append(f"{verb!r} has no help text")
        elif (verb_help := runner.invoke(group, [verb, "--help"], prog_name=f"gigai {gig.name}")).exit_code != 0:
            failures.append(f"`gigai {gig.name} {verb} --help` exited {verb_help.exit_code}")
    results += [Result(FAIL, "4", failure) for failure in failures]
    if not failures:
        results.append(Result(PASS, "4", f"group loads; --help and {len(_provides(manifest))} provided verb(s) answer"))
    core_listed = _core_verbs_listed(manifest)
    results += core_listed
    defined = sorted(set(group.list_commands(context)) & set(CORE_VERBS))
    results += [Result(WARN, "5", f"override: {verb}") for verb in defined]
    if not core_listed:
        results.append(Result(PASS, "5", "no core verb listed in [commands] provides"))
    results.append(_describe_in_process(group, context, gig.manifest, runner))
    verbs = sorted(" ".join(path) for path in _visible_leaves(group, context, ()))
    return verbs, True


def _visible_leaves(group: click.Group, context: click.Context, path: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    for name in group.list_commands(context):
        command = group.get_command(context, name)
        if command is None or command.hidden:
            continue
        if isinstance(command, click.Group):
            yield from _visible_leaves(command, click.Context(command, info_name=name, parent=context), (*path, name))
        else:
            yield (*path, name)


def _describe_in_process(group: click.Group, context: click.Context, manifest: Any, runner: Any) -> Result:
    command = group.get_command(context, "describe")
    if command is None:
        return Result(FAIL, "6", "no hidden `describe` verb")
    if not command.hidden:
        return Result(FAIL, "6", "`describe` is not hidden")
    described = runner.invoke(group, ["describe"])
    return _compare_described(described.exit_code, described.stdout, manifest)


def _compare_described(exit_code: int, output: str, manifest: Any) -> Result:
    if exit_code != 0:
        return Result(FAIL, "6", f"`describe` exited {exit_code}")
    try:
        document = json.loads(output)
    except ValueError:
        return Result(FAIL, "6", "`describe` did not print JSON")
    if document != manifest:
        return Result(FAIL, "6", "`describe` JSON differs from gig.toml")
    return Result(PASS, "6", "`describe` prints gig.toml as JSON")


def _executable_items(name: str, executable: str, manifest: dict[str, Any], results: list[Result]) -> tuple[list[str], bool]:
    provides = _provides(manifest)
    path = _which(executable)
    if path is None:
        results.append(Result(FAIL, "4", f"executable {executable!r} not found on PATH"))
        return provides, False
    failures = []
    for argv in ([], *([verb] for verb in provides)):
        code, _ = _run(path, *argv, "--help")
        if code != 0:
            failures.append(f"`{executable} {' '.join([*argv, '--help'])}` exited {code}")
    results += [Result(FAIL, "4", failure) for failure in failures]
    if not failures:
        results.append(Result(PASS, "4", f"{executable} --help and {len(provides)} provided verb(s) answer"))
    core_listed = _core_verbs_listed(manifest)
    results += core_listed
    if not core_listed:
        results.append(Result(PASS, "5", "no core verb listed in [commands] provides"))
    code, output = _run(path, "describe")
    results.append(_compare_described(code, output, manifest))
    return provides, True


def _run(path: str, *args: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=60, check=False, shell=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)
    return completed.returncode, completed.stdout


# --- 7, 8 -------------------------------------------------------------------------------------------------


def _package_file(package_dir: Path | None, relative: str) -> Path | None:
    """``relative`` inside the gig's package directory; ``None`` when it escapes the package or does not exist."""

    if package_dir is None:
        return None
    root = package_dir.resolve()
    path = (root / relative).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def _config_item(manifest: dict[str, Any], package_dir: Path | None) -> Result:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    config = gig_table(manifest, "config")
    schema_name = config.get("schema")
    if not isinstance(schema_name, str):
        return Result(SKIP, "7", "no [config] schema")
    path = _package_file(package_dir, schema_name)
    if path is None:
        return Result(FAIL, "7", f"[config] schema {schema_name!r} is not a file in the gig's package")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, ValueError, SchemaError) as exc:
        return Result(FAIL, "7", f"[config] schema {schema_name!r} is not a JSON Schema: {exc}")
    defaults = config.get("defaults", {})
    errors = sorted(Draft202012Validator(schema).iter_errors(defaults), key=lambda error: [str(p) for p in error.path])
    if errors:
        first = errors[0]
        return Result(FAIL, "7", f"[config.defaults] {'/'.join(map(str, first.path)) or '(top level)'}: {first.message}")
    return Result(PASS, "7", f"[config.defaults] validate against {schema_name}")


def _docs_items(name: str, manifest: dict[str, Any], package_dir: Path | None, verbs: list[str]) -> list[Result]:
    docs = gig_table(manifest, "docs")
    if not docs:
        return [Result(SKIP, "8", "no [docs] table")]
    results = []
    for key in ("page", "cli_manual", "skill"):
        relative = docs.get(key)
        if isinstance(relative, str) and _package_file(package_dir, relative) is None:
            results.append(Result(FAIL, "8", f"[docs] {key} {relative!r} is not a file in the gig's package"))
    manual = docs.get("cli_manual")
    manual_path = _package_file(package_dir, manual) if isinstance(manual, str) else None
    if manual_path is not None:
        try:
            covered = _manual_paths(name, json.loads(manual_path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            results.append(
                Result(FAIL, "8", f"[docs] cli_manual {manual!r}: not JSON-compatible YAML (core reads it like its own commands.yaml): {exc}")
            )
        else:
            missing = [verb for verb in verbs if verb not in covered]
            if missing:
                results.append(Result(FAIL, "8", f"[docs] cli_manual {manual!r} has no entry for: {', '.join(missing)}"))
    if not results:
        results.append(Result(PASS, "8", "[docs] files present" + (f"; cli_manual covers {len(verbs)} verb(s)" if manual_path else "")))
    return results


def _manual_paths(name: str, document: Any) -> set[str]:
    """Command paths named by a manual in core's ``commands.yaml`` shape (a ``commands`` map or list of ``path``)."""

    commands = document.get("commands") if isinstance(document, dict) else None
    if isinstance(commands, dict):
        paths = [key for key in commands if isinstance(key, str)]
    elif isinstance(commands, list):
        paths = [entry["path"] for entry in commands if isinstance(entry, dict) and isinstance(entry.get("path"), str)]
    else:
        raise ValueError("no `commands` map or list")
    prefix = f"{name} "
    return {path.removeprefix(prefix) for path in paths}


# --- 13 ---------------------------------------------------------------------------------------------------


def _tests_item(gig: InstalledGig, package_dir: Path | None) -> Result:
    project = _project_root(gig, package_dir)
    if project is None:
        return Result(SKIP, "13", "no project tests/ next to the installed package (check a source checkout or editable install)")
    if find_spec("pytest") is None:
        return Result(SKIP, "13", f"pytest is not installed; cannot run {project / 'tests'}")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests"],
            cwd=project, capture_output=True, text=True, env=_child_env(), timeout=900, check=False, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Result(FAIL, "13", f"pytest in {project} did not finish: {exc}")
    summary = _last_line(completed.stdout)
    if completed.returncode == 0:
        return Result(PASS, "13", f"{project / 'tests'}: {summary}")
    if completed.returncode == 5:
        return Result(WARN, "13", f"{project / 'tests'}: no tests collected")
    return Result(FAIL, "13", f"{project / 'tests'}: pytest exited {completed.returncode}: {summary}")


def _project_root(gig: InstalledGig, package_dir: Path | None) -> Path | None:
    """The gig's source project (flat or src layout): a ``pyproject.toml`` naming the gig's dist, with ``tests/``."""

    if package_dir is None or gig.dist is None:
        return None
    for candidate in (package_dir.parent, package_dir.parent.parent):
        pyproject = candidate / "pyproject.toml"
        if not (pyproject.is_file() and (candidate / "tests").is_dir()):
            continue
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        if isinstance(project, dict) and _normalize(str(project.get("name", ""))) == _normalize(gig.dist):
            return candidate
    return None


# --- helpers ----------------------------------------------------------------------------------------------


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _child_env() -> dict[str, str]:
    """This interpreter's import path for a child, so it sees the same installed gigs."""

    return {**os.environ, "PYTHONPATH": os.pathsep.join(entry for entry in sys.path if entry)}


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1].strip() if lines else "(no output)"
