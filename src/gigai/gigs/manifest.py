"""The gig manifest (``gig.toml``): schema validation, the ``[gig] core`` range check, installed-gig discovery.

``gigai gig list|info|check`` read every manifest through this module. Each manifest is validated against
``gigai/schemas/gig-manifest.schema.json`` (manifest format 1) when it is loaded; a manifest that fails the
schema lists as ``invalid``. Mounting (``loader.mount_gigs``) stays as lenient as P1 made it.

The core range is checked against the installed core's release number: ``0.2.0a1`` counts as ``0.2.0``, so a
gig declaring ``core = ">=0.2,<0.3"`` runs on every 0.2 pre-release. GigAI has no ``packaging`` dependency;
``_Version`` and ``_Specifier`` implement the PEP 440 subset a ``core`` range needs (``== != < <= > >= ~= ===``,
``==X.*``, pre/post/dev releases).
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib import metadata, resources
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any

from .loader import ENTRY_POINT_GROUP, MANIFEST_NAME, read_manifest

SCHEMA_RESOURCE = "gig-manifest.schema.json"
#: Verbs core adds to every gig's group; a gig must not list them in ``[commands] provides``.
CORE_VERBS = ("status", "doctor", "config", "export", "import", "eval")


class ManifestError(Exception):
    """A manifest file that cannot be read or parsed as TOML."""


@cache
def _validator() -> Any:
    from jsonschema import Draft202012Validator

    text = resources.files("gigai.schemas").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")
    return Draft202012Validator(json.loads(text))


def manifest_errors(document: dict[str, Any]) -> list[str]:
    """Schema errors of a parsed manifest, one ``<path>: <message>`` line each; empty when valid."""

    errors = sorted(_validator().iter_errors(document), key=lambda error: [str(part) for part in error.path])
    return [f"{'/'.join(map(str, error.path)) or '(top level)'}: {error.message}" for error in errors]


def read_manifest_file(path: Path) -> dict[str, Any]:
    """Parse one ``gig.toml`` file; raises ``ManifestError`` when it cannot be read or parsed."""

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc


def installed_core_version() -> str:
    """The installed core's version string, e.g. ``0.2.0a1``."""

    return metadata.version("gigai")


def core_problem(core: str, installed: str) -> str | None:
    """``None`` when ``installed`` (its release number) satisfies ``core``, else ``needs gigai <core>, you have <installed>``.

    Raises ``ValueError`` when ``core`` or ``installed`` is not a PEP 440 specifier / version.
    """

    if _Specifier(core).contains(_Version.parse(installed).base()):
        return None
    return f"needs gigai {core}, you have {installed}"


def gig_table(manifest: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """``manifest[key]`` when it is a table, else ``{}``."""

    table = manifest.get(key) if manifest else None
    return table if isinstance(table, dict) else {}


@dataclass(frozen=True)
class InstalledGig:
    """One ``gigai.gigs`` entry point, its manifest (or why it has none) and the manifest's schema errors."""

    entry_point: EntryPoint
    manifest: dict[str, Any] | None
    problem: str | None
    errors: tuple[str, ...]
    manifest_path: Path | None

    @property
    def name(self) -> str:
        return self.entry_point.name

    @property
    def dist(self) -> str | None:
        dist = self.entry_point.dist
        return dist.name if dist is not None else None

    @property
    def version(self) -> str | None:
        dist = self.entry_point.dist
        return dist.version if dist is not None else None

    def status(self, installed: str) -> tuple[str, str | None]:
        """``(status, detail)``: ``cannot load`` (no readable manifest), ``invalid`` (schema), ``incompatible`` or ``ok``."""

        if self.problem is not None:
            return "cannot load", self.problem
        return manifest_status(self.manifest, self.errors, installed)


def manifest_status(manifest: dict[str, Any] | None, errors: Sequence[str], installed: str) -> tuple[str, str | None]:
    """``(status, detail)`` of a readable manifest: ``invalid`` (schema), ``incompatible`` (core range) or ``ok``."""

    if errors:
        return "invalid", errors[0]
    core = gig_table(manifest, "gig").get("core")
    try:
        problem = core_problem(core, installed) if isinstance(core, str) else None
    except ValueError as exc:
        return "invalid", f"gig/core: {exc}"
    if problem is not None:
        return "incompatible", problem
    return "ok", None


def discover() -> list[InstalledGig]:
    """Every installed ``gigai.gigs`` entry point, sorted by name; reads manifests without importing any gig."""

    return [inspect_entry_point(entry_point) for entry_point in sorted(entry_points(), key=lambda ep: ep.name)]


def entry_points() -> tuple[EntryPoint, ...]:
    return tuple(metadata.entry_points(group=ENTRY_POINT_GROUP))


def inspect_entry_point(entry_point: EntryPoint) -> InstalledGig:
    try:
        manifest: dict[str, Any] | None = read_manifest(entry_point)
        problem = None
    except Exception as exc:  # noqa: BLE001 - any unreadable manifest is reported as `cannot load`, never raised
        manifest, problem = None, str(exc) or type(exc).__name__
    errors = tuple(manifest_errors(manifest)) if manifest is not None else ()
    return InstalledGig(entry_point, manifest, problem, errors, manifest_location(entry_point))


def manifest_location(entry_point: EntryPoint) -> Path | None:
    """Where the gig's ``gig.toml`` lives on disk (the same lookup as ``loader.read_manifest``), without importing it."""

    if EntryPoint.pattern.match(entry_point.value) is None:
        return None
    package = entry_point.module.partition(".")[0]
    dist = entry_point.dist
    for path in (dist.files if dist is not None else None) or ():
        if path.name == MANIFEST_NAME and path.parts[:-1] == (package,):
            return Path(str(path.locate()))
    from importlib.util import find_spec

    try:
        spec = find_spec(package)
    except (ImportError, ValueError):
        return None
    for location in (spec.submodule_search_locations if spec is not None else None) or ():
        candidate = Path(location) / MANIFEST_NAME
        if candidate.is_file():
            return candidate
    return None


# --- PEP 440 subset ---------------------------------------------------------------------------------------

_VERSION = re.compile(
    r"""^v?(?P<release>[0-9]+(?:\.[0-9]+)*)
    (?:[-_.]?(?P<pre_l>alpha|beta|preview|pre|rc|a|b|c)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?:-(?P<post_implicit>[0-9]+)|[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n>[0-9]+)?)?
    (?P<dev_l>[-_.]?dev[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?$""",
    re.VERBOSE | re.IGNORECASE,
)
_PRE_PHASE = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2, "c": 2, "pre": 2, "preview": 2}
_CLAUSE = re.compile(r"^(~=|===|==|!=|<=|>=|<|>)\s*(\S+)$")


@dataclass(frozen=True)
class _Version:
    release: tuple[int, ...]
    pre: tuple[int, int] | None = None
    post: int | None = None
    dev: int | None = None

    @classmethod
    def parse(cls, text: str) -> _Version:
        match = _VERSION.match(text.strip())
        if match is None:
            raise ValueError(f"not a version: {text!r}")
        pre = None
        if match["pre_l"]:
            pre = (_PRE_PHASE[match["pre_l"].lower()], int(match["pre_n"] or 0))
        post = None
        if match["post_implicit"] is not None:
            post = int(match["post_implicit"])
        elif match["post_l"]:
            post = int(match["post_n"] or 0)
        dev = int(match["dev_n"] or 0) if match["dev_l"] is not None else None
        return cls(tuple(int(part) for part in match["release"].split(".")), pre, post, dev)

    def base(self) -> _Version:
        return _Version(self.release)

    def key(self) -> tuple[Any, ...]:
        release = list(self.release)
        while len(release) > 1 and release[-1] == 0:
            release.pop()
        if self.pre is None and self.post is None and self.dev is not None:
            pre: tuple[int, int] = (-1, 0)  # X.devN sorts before X's pre-releases
        else:
            pre = self.pre if self.pre is not None else (3, 0)
        post = -1 if self.post is None else self.post
        dev = (0, self.dev) if self.dev is not None else (1, 0)
        return (tuple(release), pre, post, dev)


class _Specifier:
    """Comma-separated PEP 440 clauses; ``contains`` is meant for final releases (callers pass ``base()``)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.clauses: list[tuple[str, str]] = []
        for raw in text.split(","):
            match = _CLAUSE.match(raw.strip())
            if match is None:
                raise ValueError(f"not a version specifier: {text!r}")
            operator, value = match.groups()
            if operator != "===":
                wildcard = value.endswith(".*")
                if wildcard and operator not in ("==", "!="):
                    raise ValueError(f"not a version specifier: {text!r}")
                _Version.parse(value[:-2] if wildcard else value)
                if operator == "~=" and len(_Version.parse(value).release) < 2:
                    raise ValueError(f"not a version specifier: {text!r}")
            self.clauses.append((operator, value))

    def contains(self, version: _Version) -> bool:
        return all(_clause_matches(operator, value, version) for operator, value in self.clauses)


def _clause_matches(operator: str, value: str, version: _Version) -> bool:
    if operator == "===":
        return ".".join(map(str, version.release)) == value
    if value.endswith(".*"):
        prefix = _Version.parse(value[:-2]).release
        padded = version.release + (0,) * max(0, len(prefix) - len(version.release))
        matched = padded[: len(prefix)] == prefix
        return matched if operator == "==" else not matched
    other = _Version.parse(value)
    mine, theirs = version.key(), other.key()
    if operator == "==":
        return mine == theirs
    if operator == "!=":
        return mine != theirs
    if operator == ">=":
        return mine >= theirs
    if operator == "<=":
        return mine <= theirs
    if operator == ">":
        return mine > theirs
    if operator == "<":
        return mine < theirs
    # "~=X.Y": >= X.Y and == X.*
    prefix = other.release[:-1]
    padded = version.release + (0,) * max(0, len(prefix) - len(version.release))
    return mine >= theirs and padded[: len(prefix)] == prefix


def same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False
