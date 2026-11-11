"""``gigai gig check``: the gig conformance checks, items 1-8, 11 and 13 of the framework's list.

1 schema-valid manifest · 2 exactly one entry point, name == ``[gig] name``, value == ``[commands] group`` ·
3 manifest readable without importing the entry-point module or its package · 4 group loads, ``--help`` exits 0, every ``provides`` verb
exists with help · 5 no core verb listed in ``provides`` (a core verb defined in code is reported as
``override: <verb>``) · 6 hidden ``describe`` prints the manifest · 7 ``[config.defaults]`` validate against
``[config] schema`` · 8 docs files present, the CLI manual covers every verb · 11 the ``[evals]`` table: every scorer
and postprocess symbol imports (in a child interpreter), every ``step:<id>`` scorer names a judge step, every prompt
file exists (its sha256 printed), every dataset exists inside its root (a private one under ``{data}`` may be absent,
except in the ``ci`` suite; a path that leaves the package or ``{data}`` fails), every ``ci`` dataset's sha256
matches, every licence is on the allow-list or the data is private under ``{data}`` and not ``ci``
(``eval_manifest``) · 13 the gig's own tests pass.

Items 9, 10 and 12 (dist pins, import weight, tools) belong to later packets; item 11 reports nothing for a manifest
without ``[evals]``. A manifest file that is not an installed gig's gets item 1 only (plus the core-range line) and
the reference and licence half of item 11; 2-8 and 13 need the installed gig. When the file sits in a package (a
project directory or checkout), item 11 also checks that package's files and symbols.
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

from .eval_manifest import (
    ABSENT_PRIVATE,
    DATA_TOKEN,
    MISSING,
    OUTSIDE,
    UNCHECKED,
    EvalDataset,
    data_folder,
    dataset_status,
    eval_steps,
    file_sha256,
    licence_problem,
    step_datasets,
)
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


def check_file(manifest: dict[str, Any], *, installed: str, manifest_path: Path | None = None) -> list[Result]:
    """A manifest file that is not an installed gig's: item 1, the core range and item 11; the rest is skipped.

    Item 11 checks files and symbols only when ``manifest_path`` sits in a package (its folder has ``__init__.py``)."""

    results = check_manifest(manifest, installed=installed)
    results += [Result(SKIP, item, _NEEDS_INSTALL) for item in ("2", "3", "4", "5", "6", "7", "8", "13")]
    package_dir = manifest_path.parent if manifest_path is not None and (manifest_path.parent / "__init__.py").is_file() else None
    results += _evals_items(manifest, package_dir, _data_dir(gig_table(manifest, "gig").get("name")))
    return results


def check_installed(gig: InstalledGig, *, installed: str) -> list[Result]:
    """Items 1-8, 11 and 13 for one installed gig."""

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
    results += _evals_items(manifest, package_dir, _data_dir(gig.name))
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


# --- 11 ---------------------------------------------------------------------------------------------------


_IMPORT_SYMBOLS = """\
import importlib, json, sys
problems = {}
for symbol in sys.argv[1:]:
    module, _, attr = symbol.partition(":")
    try:
        if not callable(getattr(importlib.import_module(module), attr)):
            problems[symbol] = "not callable"
    except BaseException as exc:
        problems[symbol] = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
print()
print(json.dumps(problems))
"""


def _data_dir(name: object) -> Path | None:
    """What ``{data}`` stands for when checking gig ``name``: its gig folder under the gigai home."""

    from ..setup import default_home_root

    return data_folder(default_home_root(), name)


def _evals_items(manifest: dict[str, Any], package_dir: Path | None, data_dir: Path | None) -> list[Result]:
    """Item 11; ``[]`` when the manifest has no ``[evals]`` table. Without ``package_dir`` only the references and
    licences are checked."""

    if not isinstance(manifest.get("evals"), dict):
        return []
    steps = eval_steps(manifest)
    ids = [step.get("id") for step in steps]
    judges = {step["id"] for step in steps if isinstance(step.get("id"), str) and isinstance(step.get("grades"), str)}
    results = [
        Result(FAIL, "11", f"evals.steps: step id {step_id!r} is declared more than once")
        for step_id in sorted({step_id for step_id in ids if isinstance(step_id, str) and ids.count(step_id) > 1})
    ]
    symbols: dict[str, list[str]] = {}
    for step in steps:
        field = f"evals.steps.{step.get('id', '?')}"
        results += _step_references(field, step, ids, judges, symbols)
        for dataset in step_datasets(step):
            problem = licence_problem(dataset)
            if problem is not None:
                results.append(Result(FAIL, "11", f"{field}.datasets.{dataset.suite}.licence: {problem}"))
    if not results:
        results.append(Result(PASS, "11", f"[evals]: {len(steps)} step(s); step references and dataset licences check out"))
    if package_dir is None:
        results.append(Result(SKIP, "11", "[evals] files and symbols: needs the gig's package (check the installed gig or its project directory)"))
        return results
    for step in steps:
        field = f"evals.steps.{step.get('id', '?')}"
        prompt = step.get("prompt")
        if isinstance(prompt, dict) and isinstance(prompt.get("file"), str):
            results.append(_prompt_result(f"{field}.prompt.file", prompt["file"], package_dir))
        for dataset in step_datasets(step):
            results.append(_dataset_result(f"{field}.datasets.{dataset.suite}", dataset, package_dir, data_dir))
    for key, entry in gig_table(manifest, "steps").items():
        if isinstance(entry, dict) and isinstance(entry.get("prompt"), str):
            results.append(_prompt_result(f"steps.{key}.prompt", entry["prompt"], package_dir))
    results += _symbol_results(symbols, package_dir)
    return results


def _step_references(field: str, step: dict[str, Any], ids: list[Any], judges: set[str], symbols: dict[str, list[str]]) -> list[Result]:
    """Static half of item 11 for one step: ``step:<id>`` scorers and a local ``grades`` name a step here; collects
    the ``module:attr`` symbols (scorers, postprocess) into ``symbols`` for the import check."""

    results = []
    scorers = step.get("scorers")
    for scorer in scorers if isinstance(scorers, list) else []:
        if not isinstance(scorer, str):
            continue
        if scorer.startswith("step:"):
            if scorer[len("step:") :] not in judges:
                results.append(Result(FAIL, "11", f"{field}.scorers: {scorer!r} names no judge step (a step with `grades`) of this manifest"))
        else:
            symbols.setdefault(scorer, []).append(f"{field}.scorers")
    if isinstance(step.get("postprocess"), str):
        symbols.setdefault(step["postprocess"], []).append(f"{field}.postprocess")
    grades = step.get("grades")
    if isinstance(grades, str) and ":" not in grades and grades not in ids:
        results.append(Result(FAIL, "11", f"{field}.grades: {grades!r} names no step of this manifest"))
    return results


def _prompt_result(field: str, relative: str, package_dir: Path) -> Result:
    path = _package_file(package_dir, relative)
    if path is None:
        return Result(FAIL, "11", f"{field}: {relative!r} is not a file in the gig's package")
    return Result(PASS, "11", f"{field}: {relative} sha256 {file_sha256(path)}")


def _dataset_result(field: str, dataset: EvalDataset, package_dir: Path, data_dir: Path | None) -> Result:
    status, path = dataset_status(dataset, package_dir, data_dir)
    if status == MISSING:
        return Result(FAIL, "11", f"{field}.path: {dataset.path!r} is not a file in the gig's package")
    if status == OUTSIDE:
        root = f"{DATA_TOKEN} ({data_dir})" if dataset.private else "the gig's package"
        return Result(FAIL, "11", f"{field}.path: {dataset.path!r} leaves {root} (`..`, an absolute path or a symlink out)")
    if status == UNCHECKED:
        return Result(SKIP, "11", f"{field}: {dataset.path} not checked (no gig folder for this gig name)")
    if status == ABSENT_PRIVATE:
        if dataset.suite == "ci":
            return Result(FAIL, "11", f"{field}.path: {dataset.path} is absent (private); a ci dataset must ship in the package")
        return Result(SKIP, "11", f"{field}: {dataset.path} absent (private)")
    assert path is not None
    actual = file_sha256(path)
    if dataset.sha256 is not None and dataset.sha256 != actual:
        outcome = FAIL if dataset.suite == "ci" else WARN
        return Result(outcome, "11", f"{field}.sha256: declared {dataset.sha256}, {dataset.path} has {actual}")
    return Result(PASS, "11", f"{field}: {dataset.path} sha256 {actual}")


def _symbol_results(symbols: dict[str, list[str]], package_dir: Path) -> list[Result]:
    """Import every ``module:attr`` in a fresh interpreter that also sees the package's own folder."""

    if not symbols:
        return []
    root = package_dir.resolve()
    while (root.parent / "__init__.py").is_file():
        root = root.parent
    env = _child_env()
    env["PYTHONPATH"] = os.pathsep.join([str(root.parent), env["PYTHONPATH"]])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _IMPORT_SYMBOLS, *symbols],
            capture_output=True, text=True, env=env, timeout=120, check=False, shell=False,
        )
        problems = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return [Result(FAIL, "11", f"cannot import the [evals] symbols in a fresh interpreter: {exc}")]
    if not isinstance(problems, dict):
        return [Result(FAIL, "11", f"cannot import the [evals] symbols in a fresh interpreter: {_last_line(completed.stderr)}")]
    results = [
        Result(FAIL, "11", f"{', '.join(fields)}: {symbol!r} does not import ({problems[symbol]})")
        for symbol, fields in symbols.items()
        if symbol in problems
    ]
    if not results:
        results.append(Result(PASS, "11", f"{len(symbols)} scorer/postprocess symbol(s) import"))
    return results


# --- helpers ----------------------------------------------------------------------------------------------


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _child_env() -> dict[str, str]:
    """This interpreter's import path for a child, so it sees the same installed gigs."""

    return {**os.environ, "PYTHONPATH": os.pathsep.join(entry for entry in sys.path if entry)}


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1].strip() if lines else "(no output)"
