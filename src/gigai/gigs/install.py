"""``gigai gig install | update | uninstall``: add, refresh or remove a gig in the environment gigai runs from.

How gigai was installed decides the uv command (PG1, ``research/gigai-2/gaps/PG1-uv-tool-and-core-size.md``):

- a ``uv tool`` env (``uv-receipt.toml`` next to the interpreter's prefix): ``uv tool install`` again, in that env's
  tool dir and bin dir, with the receipt's full ``--with`` list changed by one entry, its ``--python`` and its index
  options, so the gig is in the receipt and survives ``uv tool upgrade gigai``. Never ``uv pip`` into that env (lost
  on the next upgrade), never ``--reinstall`` (moves core to the newest version) and never ``-c gigai==...`` (stored
  in the receipt, then blocks upgrades). Install and update run twice: first with core pinned ``gigai==<installed>``,
  so a gig needing another gigai fails at resolution and nothing changes, then as the receipt has it, which keeps
  what the first run installed and drops the pin. Should core move anyway, the receipt is rebuilt as it was and the
  command refuses.
- a plain venv: ``uv pip install --python <this interpreter> -c <gigai==installed>``, so core cannot move.
- anything else (pipx, a system Python): refused with exit 2 (decision #33).

Every command prints its argv before running it; ``--dry-run`` prints it and stops.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit, urlunsplit

import click

from . import manifest as gig_manifest
from .loader import CORE_DIST, ENTRY_POINT_GROUP

RECEIPT = "uv-receipt.toml"
SUPPORTED = "install gigai with `uv tool install gigai`, or into a venv, then run this again"
CONSTRAINTS = "constraints.txt"

# ``[tool.options]`` keys of a uv receipt and the ``uv tool install`` flag that writes each back (uv 0.9.17).
_LIST_OPTIONS = {"find-links": "--find-links", "extra-index-url": "--extra-index-url"}
_TEXT_OPTIONS = {
    "index-url": "--index-url",
    "index-strategy": "--index-strategy",
    "keyring-provider": "--keyring-provider",
    "resolution": "--resolution",
    "prerelease": "--prerelease",
    "fork-strategy": "--fork-strategy",
    "exclude-newer": "--exclude-newer",
    "link-mode": "--link-mode",
}
_FLAG_OPTIONS = {
    "no-index": "--no-index",
    "no-sources": "--no-sources",
    "compile-bytecode": "--compile-bytecode",
    "no-build-isolation": "--no-build-isolation",
}
# Receipt requirement lists besides ``requirements`` and the file flag each is passed back through.
_RECEIPT_FILES = {
    "constraints": ("--constraints", CONSTRAINTS),
    "overrides": ("--overrides", "overrides.txt"),
    "build-constraint-dependencies": ("--build-constraints", "build-constraints.txt"),
}
_REQUIREMENT_KEYS = {"name", "specifier", "extras", "marker", "path", "directory", "url", "git", "editable"}
_SOURCE_KEYS = {"path", "directory", "url", "git", "editable"}
_NEEDS_CORE = re.compile(
    r"(?:all versions of (?P<all>[A-Za-z0-9][A-Za-z0-9._-]*) depend"
    r"|(?P<one>[A-Za-z0-9][A-Za-z0-9._-]*?)(?:[=<>!~]=?\S*)? depends)"
    r" on gigai(?P<spec>[=<>!~]=?[^\s,]+(?:,\s?[=<>!~]=?[^\s,]+)*)"
)


class Refused(Exception):
    """The command cannot run here; exit 2 with this message."""


class UvFailed(Exception):
    """uv exited non-zero for another reason than a gig needing another gigai; exit with uv's code."""

    def __init__(self, returncode: int) -> None:
        super().__init__(returncode)
        self.returncode = returncode


@dataclass(frozen=True)
class Spec:
    """What ``gig install SPEC`` installs: the argument uv gets, its distribution name and, for a local wheel, sdist or
    project dir, the gig names its ``gigai.gigs`` entry points declare (``None`` when unknown before installing)."""

    requirement: str
    dist: str
    gigs: tuple[str, ...] | None


@dataclass
class Plan:
    """One uv command: ``argv`` starts with ``uv``; each ``files`` key is a file name used in ``argv`` whose content is
    written to a temporary directory just before running. ``then`` is the command that follows a successful run."""

    argv: list[str]
    files: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    then: Plan | None = None


@dataclass(frozen=True)
class Environment:
    kind: str  # "uv tool" or "venv"
    prefix: Path
    python: str
    receipt: dict[str, Any] | None


# ---------------------------------------------------------------------------------------------------------- commands


@click.command("install")
@click.argument("spec")
@click.option("--dry-run", is_flag=True, help="Print the uv command and do not run it.")
def install_command(spec: str, dry_run: bool) -> None:
    """Install a gig (a wheel, an sdist, a project directory or a PyPI name) next to gigai, without moving gigai."""

    _main(lambda env: _install(env, spec, dry_run))


@click.command("update")
@click.argument("gig")
@click.option("--dry-run", is_flag=True, help="Print the uv command and do not run it.")
def update_command(gig: str, dry_run: bool) -> None:
    """Reinstall an installed gig (by gig or distribution name) from where it was installed from."""

    _main(lambda env: _update(env, gig, dry_run))


@click.command("uninstall")
@click.argument("gig")
@click.option("--dry-run", is_flag=True, help="Print the uv command and do not run it.")
def uninstall_command(gig: str, dry_run: bool) -> None:
    """Remove an installed gig (by gig or distribution name); its gig folder and data are kept."""

    _main(lambda env: _uninstall(env, gig, dry_run))


def _main(action: Callable[[Environment], None]) -> None:
    try:
        action(detect_environment())
    except Refused as refused:
        click.echo(f"gigai gig: {refused}", err=True)
        raise click.exceptions.Exit(2) from None
    except UvFailed as failed:
        click.echo("gigai gig: uv failed", err=True)
        raise click.exceptions.Exit(failed.returncode) from None


def _install(env: Environment, raw: str, dry_run: bool) -> None:
    spec = read_spec(raw)
    if _normalize(spec.dist) == CORE_DIST:
        raise Refused(f"{spec.dist} is gigai itself, not a gig; upgrade it with `uv tool upgrade {CORE_DIST}` (or in its venv)")
    if spec.gigs is not None and (taken := _collisions(spec.dist, spec.gigs)):
        raise Refused(_collision_message(spec.dist, taken, "not installed (rename the gig's entry point)"))
    was_installed = _is_installed(spec.dist)
    before = gig_manifest.installed_core_version()
    plan = install_plan(env, spec)
    if not _run(plan, dry_run, env):
        return
    gigs = _installed_gigs(spec.dist)
    if spec.gigs is None and (taken := _collisions(spec.dist, gigs)):
        refused = _collision_message(spec.dist, taken, "not installed (rename the gig's entry point)")
        if env.kind == "uv tool":
            _restore(env, before, refused)
        elif was_installed:
            raise Refused(_collision_message(spec.dist, taken, "it was installed before this command, so it is left in place"))
        else:
            _run(Plan(["uv", "pip", "uninstall", "--python", env.python, spec.dist]), False, env)
        raise Refused(refused)
    _unpin(env, plan, before)
    _keep_core(env, spec.dist, before)
    _verdict(spec.dist, gigs)


def _update(env: Environment, gig: str, dry_run: bool) -> None:
    dist = _dist_of(gig)
    before = gig_manifest.installed_core_version()
    plan = update_plan(env, dist)
    if _run(plan, dry_run, env):
        _unpin(env, plan, before)
        _keep_core(env, dist, before)
        _verdict(dist, _installed_gigs(dist))


def _unpin(env: Environment, plan: Plan, before: str) -> None:
    """Run ``plan.then``, the receipt's own rebuild after the core-pinned gig step: it keeps every version that step
    installed and drops the ``gigai==`` pin from the receipt again, so ``uv tool upgrade gigai`` still moves core."""

    if plan.then is None:
        return
    try:
        _run(plan.then, False, env, core=before)
    except (UvFailed, Refused):
        raise Refused(
            f"the gig is installed, but this env's receipt still pins {CORE_DIST}=={before}, so `uv tool upgrade "
            f"{CORE_DIST}` cannot move it; run the last uv command above again to drop the pin"
        ) from None


def _keep_core(env: Environment, dist: str, before: str) -> None:
    """Refuse when core moved anyway: the pinned gig step cannot move it, so only the unpinned rebuild after it (or a
    core installed from a path, URL or git source, which is never pinned) can. Rebuild the env as it was, then exit 2
    naming the gigai version ``dist`` asks for."""

    after = gig_manifest.installed_core_version()
    if after == before:
        return
    if env.kind != "uv tool":
        click.echo(f"note: gigai moved from {before} to {after}", err=True)
        return
    needs = _core_requirement(dist)
    asks = f"needs gigai {needs}" if needs else f"moved gigai to {after}"
    refused = f"gig {dist} {asks}, you have {before}; run `uv tool upgrade {CORE_DIST}` first"
    _restore(env, before, refused)
    raise Refused(f"{refused} (gigai and its gigs are back as they were)")


def _restore(env: Environment, before: str, refused: str) -> None:
    """Rebuild the receipt as it was before this command. A plain ``uv tool install`` keeps whatever core is installed,
    so when core moved, run it first with core pinned to ``before``, then as the receipt had it (which keeps ``before``
    and drops the pin again). A core installed from a path, URL or git source is rebuilt from that source only. When
    that fails, raise ``refused`` with how to finish by hand."""

    core, others = _receipt_requirements(env)
    kept = _with_args(others)
    by_hand = f"run `uv tool install {CORE_DIST}=={before}`, then install the gigs again"
    try:
        if gig_manifest.installed_core_version() != before and not set(core) & _SOURCE_KEYS:
            _run(_tool_plan(env, {**core, "specifier": f"=={before}"}, kept), False, env, core=before)
        _run(_tool_plan(env, core, kept), False, env, core=before)
    except (UvFailed, Refused):
        raise Refused(f"{refused}; putting gigai {before} and the gigs back failed (see above): {by_hand}") from None
    if (now := gig_manifest.installed_core_version()) != before:
        raise Refused(f"{refused}; gigai is still {now}, not {before}: {by_hand}")


def _core_requirement(dist: str) -> str | None:
    """The version range ``dist`` requires of gigai (``>=0.3``), from its installed metadata; ``None`` when indirect."""

    try:
        requirements = metadata.requires(dist) or []
    except metadata.PackageNotFoundError:
        return None
    for requirement in requirements:
        match = re.match(rf"\s*{CORE_DIST}(?![A-Za-z0-9._-])\s*(?:\[[^\]]*\])?\s*\(?([^;()]*)", requirement, re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return None


def _is_installed(dist: str) -> bool:
    try:
        metadata.distribution(dist)
    except metadata.PackageNotFoundError:
        return False
    return True


def _uninstall(env: Environment, gig: str, dry_run: bool) -> None:
    dist = _dist_of(gig)
    if _run(uninstall_plan(env, dist), dry_run, env):
        click.echo(f"uninstalled {dist}; its gig folder under the gigai home is kept")


# ------------------------------------------------------------------------------------------------------- environment


def detect_environment() -> Environment:
    """The environment this interpreter runs in; raises ``Refused`` for one ``gig install`` does not support."""

    import tomllib

    prefix = Path(sys.prefix)
    receipt = prefix / RECEIPT
    if receipt.is_file():
        try:
            document = tomllib.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise Refused(f"cannot read {receipt}: {error}") from None
        return Environment("uv tool", prefix, sys.executable, document.get("tool") or {})
    if (prefix / "pipx_metadata.json").is_file():
        raise Refused(f"gigai runs from a pipx env ({prefix}), which gig install does not support: {SUPPORTED}")
    if sys.prefix == sys.base_prefix:
        raise Refused(f"gigai runs from a Python that is not a venv ({sys.executable}): {SUPPORTED}")
    return Environment("venv", prefix, sys.executable, None)


# ------------------------------------------------------------------------------------------------------------- plans


def install_plan(env: Environment, spec: Spec) -> Plan:
    if env.kind == "venv":
        return _venv_install(env, [spec.requirement])
    core, others = _receipt_requirements(env)
    kept = [entry for entry in others if _normalize(entry["name"]) != _normalize(spec.dist)]
    return _pinned_then_plain(env, core, [*_with_args(kept), "--with", spec.requirement], [])


def update_plan(env: Environment, dist: str) -> Plan:
    """Reinstall ``dist`` (a new build of the same version) and let it move to its newest release that works with the
    installed gigai (uv otherwise keeps the installed version); nothing else is upgraded."""

    refresh = ["--reinstall-package", dist, "--upgrade-package", dist]
    if env.kind == "venv":
        plan = _venv_install(env, [_installed_source(dist)])
        plan.argv[3:3] = refresh
        return plan
    core, others = _receipt_requirements(env)
    _receipt_entry(others, dist)
    return _pinned_then_plain(env, core, _with_args(others), refresh)


def _pinned_then_plain(env: Environment, core: dict[str, Any], with_args: list[str], extra: list[str]) -> Plan:
    """The gig step with core pinned to the installed version (``extra`` appended), then, as ``then``, the receipt's
    own rebuild with the same ``--with`` list. The pin makes uv fail at resolution, changing nothing, when the gig
    needs another gigai; the rebuild keeps what the gig step installed and drops the pin, which uv stores in the
    receipt. A core installed from a path, URL or git source cannot carry a version pin: one unpinned step."""

    plain = _tool_plan(env, core, with_args)
    if set(core) & _SOURCE_KEYS:
        plain.argv += extra
        return plain
    pinned = _tool_plan(env, {**core, "specifier": f"=={gig_manifest.installed_core_version()}"}, with_args)
    pinned.argv += extra
    plain.notes = []  # the same notes, already printed with the pinned step
    pinned.then = plain
    return pinned


def uninstall_plan(env: Environment, dist: str) -> Plan:
    if env.kind == "venv":
        try:
            metadata.distribution(dist)
        except metadata.PackageNotFoundError:
            raise click.ClickException(f"{dist} is not installed in {env.prefix}") from None
        return Plan(["uv", "pip", "uninstall", "--python", env.python, dist])
    core, others = _receipt_requirements(env)
    entry = _receipt_entry(others, dist)
    return _tool_plan(env, core, _with_args([other for other in others if other is not entry]))


def _venv_install(env: Environment, requirements: list[str]) -> Plan:
    core = gig_manifest.installed_core_version()
    return Plan(
        ["uv", "pip", "install", "--python", env.python, "-c", CONSTRAINTS, *requirements],
        files={CONSTRAINTS: f"{CORE_DIST}=={core}\n"},
    )


def _tool_plan(env: Environment, core: dict[str, Any], with_args: list[str]) -> Plan:
    receipt = env.receipt or {}
    argv = ["uv", "tool", "install", *_core_args(core), *with_args]
    plan = Plan(argv)
    python = receipt.get("python")
    if isinstance(python, str):
        argv += ["--python", python]
    for key, (flag, name) in _RECEIPT_FILES.items():
        entries = receipt.get(key)
        if entries:
            plan.files[name] = "".join(f"{requirement_string(entry)}\n" for entry in entries)
            argv += [flag, name]
    options, unknown = option_args(receipt.get("options") or {})
    argv += options
    plan.notes += [f"receipt option {key!r} is not carried over; check it after this runs" for key in unknown]
    return plan


def _receipt_requirements(env: Environment) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    requirements = (env.receipt or {}).get("requirements")
    if not isinstance(requirements, list) or not all(isinstance(entry, dict) and "name" in entry for entry in requirements):
        raise Refused(f"{env.prefix / RECEIPT} has no readable [tool] requirements")
    core = [entry for entry in requirements if _normalize(entry["name"]) == CORE_DIST]
    if not core:
        raise Refused(f"{env.prefix / RECEIPT} does not install {CORE_DIST}")
    return core[0], [entry for entry in requirements if entry is not core[0]]


def _receipt_entry(others: list[dict[str, Any]], dist: str) -> dict[str, Any]:
    for entry in others:
        if _normalize(entry["name"]) == _normalize(dist):
            return entry
    raise click.ClickException(
        f"{dist} is not in this uv tool env's receipt (not installed with `gigai gig install`); run `gigai gig install <spec>`"
    )


def _core_args(core: dict[str, Any]) -> list[str]:
    if core.get("editable") and "directory" in core:
        return ["--editable", str(core["directory"])]
    return [requirement_string(core)]


def _with_args(entries: list[dict[str, Any]]) -> list[str]:
    args: list[str] = []
    for entry in entries:
        if entry.get("editable") and "directory" in entry:
            args += ["--with-editable", str(entry["directory"])]
        else:
            args += ["--with", requirement_string(entry)]
    return args


def requirement_string(entry: dict[str, Any]) -> str:
    """A receipt requirement table as the requirement string uv records the same way, e.g. ``x[a]>=1 ; marker``."""

    unknown = set(entry) - _REQUIREMENT_KEYS
    if unknown:
        raise Refused(f"cannot rebuild receipt entry {entry!r}: unknown keys {sorted(unknown)}")
    name = str(entry["name"])
    if entry.get("extras"):
        name += f"[{','.join(entry['extras'])}]"
    if "path" in entry or "directory" in entry:
        text = f"{name} @ {Path(entry.get('path') or entry['directory']).as_uri()}"
    elif "url" in entry:
        text = f"{name} @ {entry['url']}"
    elif "git" in entry:
        text = f"{name} @ {_git_url(str(entry['git']))}"
    else:
        text = name + str(entry.get("specifier") or "")
    if entry.get("marker"):
        text += f" ; {entry['marker']}"
    return text


def _git_url(stored: str) -> str:
    """uv's receipt form ``<url>?rev=<ref>&subdirectory=<dir>#<commit>`` as ``git+<url>@<ref>#subdirectory=<dir>``."""

    parts = urlsplit(stored)
    query = parse_qs(parts.query)
    ref = next((query[key][0] for key in ("rev", "tag", "branch") if key in query), None)
    url = "git+" + urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    if ref:
        url += f"@{ref}"
    if "subdirectory" in query:
        url += f"#subdirectory={query['subdirectory'][0]}"
    return url


def option_args(options: dict[str, Any]) -> tuple[list[str], list[str]]:
    """``uv tool install`` flags that store ``options`` (a receipt's ``[tool.options]``) again, and the keys not carried."""

    args: list[str] = []
    unknown: list[str] = []
    for key, value in options.items():
        if key in _LIST_OPTIONS and isinstance(value, list):
            for item in value:
                args += [_LIST_OPTIONS[key], str(item)]
        elif key in _TEXT_OPTIONS and isinstance(value, str):
            args += [_TEXT_OPTIONS[key], value]
        elif key in _FLAG_OPTIONS and isinstance(value, bool):
            args += [_FLAG_OPTIONS[key]] if value else []
        elif key == "index" and isinstance(value, list) and all(_simple_index(index) for index in value):
            for index in value:
                if index.get("default"):
                    args += ["--default-index", index["url"]]
                else:
                    args += ["--index", f"{index['name']}={index['url']}" if index.get("name") else index["url"]]
        else:
            unknown.append(key)
    return args, unknown


def _simple_index(index: Any) -> bool:
    return (
        isinstance(index, dict)
        and isinstance(index.get("url"), str)
        and not index.get("explicit")
        and index.get("format", "simple") == "simple"
    )


def _installed_source(dist: str) -> str:
    """Where ``dist`` was installed from (its ``direct_url.json``, PEP 610) as a requirement, else its name."""

    import json

    try:
        found = metadata.distribution(dist)
    except metadata.PackageNotFoundError:
        raise click.ClickException(f"{dist} is not installed in {sys.prefix}") from None
    try:
        direct = json.loads(found.read_text("direct_url.json") or "null")
    except json.JSONDecodeError:
        direct = None
    if not isinstance(direct, dict) or not isinstance(direct.get("url"), str):
        return dist
    if isinstance(direct.get("vcs_info"), dict):
        vcs = direct["vcs_info"]
        revision = vcs.get("requested_revision")
        return f"{dist} @ {vcs.get('vcs', 'git')}+{direct['url']}" + (f"@{revision}" if revision else "")
    return f"{dist} @ {direct['url']}"


# -------------------------------------------------------------------------------------------------------------- spec


def read_spec(raw: str) -> Spec:
    """``SPEC`` of ``gig install``: a wheel, an sdist or a project directory on disk, else a requirement (PyPI name)."""

    path = Path(raw).expanduser()
    if path.is_dir():
        project = path / "pyproject.toml"
        if not project.is_file():
            raise click.ClickException(f"{raw} is a directory without pyproject.toml, not a gig project")
        name, gigs = _project_gigs(project.read_text(encoding="utf-8"), raw)
        return Spec(str(path.resolve()), name, gigs)
    if path.is_file():
        if path.name.endswith(".whl"):
            dist, gigs = _wheel_gigs(path)
        elif path.name.endswith((".tar.gz", ".zip")):
            dist, gigs = _sdist_gigs(path)
        else:
            raise click.ClickException(f"{raw} is not a wheel (.whl) or an sdist (.tar.gz, .zip)")
        return Spec(str(path.resolve()), dist, gigs)
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", raw)
    if match is None or raw.startswith((".", "/", "~")):
        raise click.ClickException(f"{raw}: no such file or directory, and not a package name")
    return Spec(raw, match.group(1), None)


def _wheel_gigs(path: Path) -> tuple[str, tuple[str, ...]]:
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            info = next(name.split("/")[0] for name in names if re.fullmatch(r"[^/]+\.dist-info/METADATA", name))
            dist = _metadata_name(archive.read(f"{info}/METADATA").decode("utf-8"))
            entry_points = archive.read(f"{info}/entry_points.txt").decode("utf-8") if f"{info}/entry_points.txt" in names else ""
    except (OSError, zipfile.BadZipFile, StopIteration, KeyError, UnicodeDecodeError) as error:
        raise click.ClickException(f"cannot read wheel {path}: {error!r}") from None
    return dist, _entry_point_names(entry_points)


def _sdist_gigs(path: Path) -> tuple[str, tuple[str, ...] | None]:
    """Name and gig names of an sdist: its ``pyproject.toml`` ``[project]`` table, else ``PKG-INFO`` (names unknown)."""

    import tarfile
    import zipfile

    files: dict[str, str] = {}
    try:
        if path.name.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    if member.count("/") == 1 and member.endswith(("/pyproject.toml", "/PKG-INFO")):
                        files[member.split("/")[1]] = archive.read(member).decode("utf-8")
        else:
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    if member.isfile() and member.name.count("/") == 1 and member.name.endswith(("/pyproject.toml", "/PKG-INFO")):
                        extracted = archive.extractfile(member)
                        if extracted is not None:
                            files[member.name.split("/")[1]] = extracted.read().decode("utf-8")
    except (OSError, tarfile.TarError, zipfile.BadZipFile, UnicodeDecodeError) as error:
        raise click.ClickException(f"cannot read sdist {path}: {error!r}") from None
    if "pyproject.toml" in files:
        name, gigs = _project_gigs(files["pyproject.toml"], str(path))
        if name:
            return name, gigs
    if "PKG-INFO" in files:
        return _metadata_name(files["PKG-INFO"]), None
    raise click.ClickException(f"{path} has neither pyproject.toml nor PKG-INFO at its top level: not an sdist")


def _project_gigs(text: str, where: str) -> tuple[str, tuple[str, ...] | None]:
    import tomllib

    try:
        project = tomllib.loads(text).get("project") or {}
    except tomllib.TOMLDecodeError as error:
        raise click.ClickException(f"cannot parse {where}'s pyproject.toml: {error}") from None
    name = project.get("name")
    if not isinstance(name, str):
        raise click.ClickException(f"{where}'s pyproject.toml has no [project] name")
    if "entry-points" in (project.get("dynamic") or []):
        return name, None
    group = (project.get("entry-points") or {}).get(ENTRY_POINT_GROUP) or {}
    return name, tuple(sorted(group))


def _metadata_name(text: str) -> str:
    match = re.search(r"^Name:\s*(\S+)", text, re.MULTILINE)
    if match is None:
        raise click.ClickException("package metadata has no Name")
    return match.group(1)


def _entry_point_names(text: str) -> tuple[str, ...]:
    import configparser

    parser = configparser.ConfigParser(delimiters=("=",), interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text)
    return tuple(sorted(parser[ENTRY_POINT_GROUP])) if parser.has_section(ENTRY_POINT_GROUP) else ()


def _dist_of(gig: str) -> str:
    """The distribution of the installed gig named ``gig``, else ``gig`` itself taken as a distribution name."""

    found = [installed.dist for installed in gig_manifest.discover() if installed.name == gig and installed.dist]
    dist = found[0] if found else gig
    if _normalize(dist) == CORE_DIST:
        raise Refused(f"{gig} is part of gigai itself, not a separately installed gig: use `uv tool upgrade {CORE_DIST}`")
    return dist


# --------------------------------------------------------------------------------------------------------- collisions


def _taken_names(own: str) -> dict[str, str]:
    """Names a gig of distribution ``own`` cannot take, each with what has it: core's own commands, the gigs core ships
    and the gigs of every other installed distribution (``mountable`` mounts whichever came first)."""

    from .loader import _root_core_commands

    taken = {name: "a gigai command" for name in _root_core_commands()}
    for entry_point in gig_manifest.entry_points():
        if entry_point.dist is None:
            continue
        owner = _normalize(entry_point.dist.name)
        if owner == CORE_DIST:
            taken.setdefault(entry_point.name, "a gigai command")
        elif owner != _normalize(own):
            taken.setdefault(entry_point.name, f"a gig from {entry_point.dist.name}")
    return taken


def _collisions(dist: str, gigs: tuple[str, ...] | list[str]) -> dict[str, str]:
    taken = _taken_names(dist)
    return {gig: taken[gig] for gig in sorted(gigs) if gig in taken}


def _collision_message(dist: str, taken: dict[str, str], outcome: str) -> str:
    names = ", and ".join(f"gig {name!r}, already {owner}" for name, owner in taken.items())
    return f"{dist} declares {names}; {outcome}"


# ---------------------------------------------------------------------------------------------------------- running


def _run(plan: Plan, dry_run: bool, env: Environment, core: str | None = None) -> bool:
    """Print and (unless ``dry_run``) run ``plan``; ``True`` when uv ran and succeeded. ``--dry-run`` and a missing uv
    print ``plan.then`` too; a real run never runs it (the caller does). ``uv tool`` commands run in the tool dir and
    bin dir this env lives in (see ``_child_env``). A failure that is a gig needing another gigai refuses (exit 2)
    naming ``core`` as the gigai you have (default: the installed one; a rollback passes the version it restores)."""

    import shlex
    import subprocess
    import tempfile

    for note in plan.notes:
        click.echo(f"note: {note}", err=True)
    uv = shutil.which("uv")
    if dry_run or uv is None:
        step: Plan | None = plan
        while step is not None:
            click.echo(shlex.join(step.argv))
            for name, content in step.files.items():
                click.echo(f"  {name}: {content.strip()}")
            step = step.then
        if uv is None and not dry_run:
            run = "the commands above, in order" if plan.then else "the command above"
            raise Refused(f"uv is not on PATH; install uv (https://docs.astral.sh/uv/) and run {run}")
        return False
    with tempfile.TemporaryDirectory(prefix="gigai-gig-") as scratch:
        argv = list(plan.argv)
        for name, content in plan.files.items():
            target = Path(scratch) / name
            target.write_text(content, encoding="utf-8")
            argv = [str(target) if arg == name else arg for arg in argv]
        click.echo(shlex.join(argv))
        installed = core or gig_manifest.installed_core_version()
        completed = subprocess.run([uv, *argv[1:]], stderr=subprocess.PIPE, text=True, env=_child_env(env), check=False, shell=False)
    if completed.stderr:
        click.echo(completed.stderr.rstrip("\n"), err=True)
    if completed.returncode != 0:
        if refused := resolver_message(completed.stderr, installed, env.kind):
            raise Refused(refused)
        raise UvFailed(completed.returncode)
    importlib.invalidate_caches()
    return True


def _child_env(env: Environment) -> dict[str, str] | None:
    """For ``uv tool``: ``UV_TOOL_DIR`` = the dir this env lives in and ``UV_TOOL_BIN_DIR`` = where the receipt says
    the ``gigai`` executable went, so a different value in the caller's shell cannot make uv rebuild another env or
    move the executable off PATH. ``None`` (inherit) for a venv."""

    if env.kind != "uv tool":
        return None
    child = {**os.environ, "UV_TOOL_DIR": str(env.prefix.parent)}
    entrypoints = (env.receipt or {}).get("entrypoints")
    paths = [entry.get("install-path") for entry in entrypoints or () if isinstance(entry, dict)]
    if installed := next((path for path in paths if isinstance(path, str) and path), None):
        child["UV_TOOL_BIN_DIR"] = str(Path(installed).parent)
    return child


def resolver_message(stderr: str, installed: str, kind: str) -> str | None:
    """uv's ``... X==v depends on gigai>=B ...`` as ``gig X needs gigai >=B, you have A`` and what to do; else ``None``."""

    match = _NEEDS_CORE.search(" ".join(stderr.split()))
    if match is None:
        return None
    gig = match.group("all") or match.group("one")
    upgrade = f"run `uv tool upgrade {CORE_DIST}`" if kind == "uv tool" else f"upgrade {CORE_DIST} in this venv"
    return f"gig {gig} needs gigai {match.group('spec')}, you have {installed}; {upgrade} first"


def _installed_gigs(dist: str) -> list[str]:
    return sorted(
        entry_point.name
        for entry_point in metadata.entry_points(group=ENTRY_POINT_GROUP)
        if entry_point.dist is not None and _normalize(entry_point.dist.name) == _normalize(dist)
    )


_CHECK = "from gigai.cli import cli; cli(prog_name='gigai')"


def _verdict(dist: str, gigs: list[str]) -> None:
    """``gigai gig check`` of each gig ``dist`` declares, in a fresh interpreter that sees the changed environment."""

    import subprocess

    if not gigs:
        click.echo(f"note: {dist} declares no {ENTRY_POINT_GROUP} entry point, so it adds no gig", err=True)
        return
    for gig in gigs:
        completed = subprocess.run([sys.executable, "-c", _CHECK, "gig", "check", gig], check=False, shell=False)
        verdict = "passes" if completed.returncode == 0 else "has FAIL items (it is installed; see above)"
        click.echo(f"gig {gig}: gig check {verdict}")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()
