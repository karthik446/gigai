"""The pre-registration file of an ablation (``eval ablate --prereg FILE``) and the block every run of it carries.

The file is TOML, written before the first run:

.. code-block:: toml

    id = "solve-sampling"           # the study; every run's prereg block names it
    registered_at = "2026-11-10"    # copied onto every run as is
    step = "solve"
    suite = "ci"
    n_planned = 10                  # items per run: must equal the suite's item count
    k = 1                           # repeats per item (optional, default 1)
    primary_metric = "match"
    alpha = 0.05                    # optional, default 0.05
    direction = "two-sided"         # optional: two-sided | greater | less

    [axes.temperature]
    levels = [0.0, 0.5, 1.0]        # first level = baseline
    [axes.output_mode]
    levels = ["text", "json", "schema"]
    schema = { type = "object" }    # sent with the schema level when an item has no expected.schema

    [[comparisons]]                 # the Holm family; every other level pair is reported as exploratory
    axis = "temperature"
    levels = [0.0, 0.5]

Its identity is the digest of its bytes exactly as read (``canonical.digest_imported_bytes``): a run records that
digest, and a later ablation under the same ``id`` from a file that hashes differently is refused as
"pre-registration changed". Comment edits count; a changed plan is a new study with a new ``id``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..canonical import digest_imported_bytes
from . import records

SUITES = ("ci", "release", "upgrade")
DIRECTIONS = ("two-sided", "greater", "less")
_TOP_KEYS = {"id", "registered_at", "step", "suite", "n_planned", "k", "primary_metric", "alpha", "direction", "axes", "comparisons"}
_SCALARS = (str, int, float, bool)


class PreregError(ValueError):
    """The pre-registration file cannot be used (missing, unreadable or not the format above)."""


@dataclass(frozen=True)
class Comparison:
    axis: str
    a: Any
    b: Any


@dataclass(frozen=True)
class Registration:
    """A parsed pre-registration file."""

    path: Path
    sha256: str
    id: str
    registered_at: str
    step: str
    suite: str
    n_planned: int
    k: int
    primary_metric: str
    alpha: float
    direction: str
    #: axis id -> registered levels (first = baseline); axes the file does not list fall back to the runner's defaults.
    axes: Mapping[str, Sequence[Any]]
    #: axis id -> the schema its ``schema`` level sends (``output_mode`` only).
    schemas: Mapping[str, Mapping[str, Any]]
    comparisons: Sequence[Comparison]


@dataclass(frozen=True)
class AblationPrereg(records.Prereg):
    """The prereg block of one ablation run: ``records.Prereg`` plus the study, its digest, design and this run's cell."""

    ablation: str = ""
    prereg_sha256: str = ""
    design: str = ""
    axes: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    cell: Mapping[str, Any] = field(default_factory=dict)
    alpha: float = 0.05
    direction: str = "two-sided"
    comparisons: Sequence[Mapping[str, Any]] = field(default_factory=list)


def load_registration(path: Path) -> Registration:
    """Read and check the pre-registration file at ``path``; ``PreregError`` names the first problem."""

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PreregError(f"cannot read pre-registration {path}: {exc.strerror or exc}") from exc
    try:
        table = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PreregError(f"pre-registration {path} is not valid TOML: {exc}") from exc
    unknown = sorted(set(table) - _TOP_KEYS)
    if unknown:
        raise PreregError(f"pre-registration {path} has unknown key(s) {', '.join(unknown)}")
    missing = sorted({"id", "registered_at", "step", "suite", "n_planned", "primary_metric"} - set(table))
    if missing:
        raise PreregError(f"pre-registration {path} lacks {', '.join(missing)}")
    for key in ("id", "registered_at", "step", "primary_metric"):
        if type(table[key]) is not str or not table[key].strip():
            raise PreregError(f"pre-registration {path}: {key} must be a non-empty string")
    if table["suite"] not in SUITES:
        raise PreregError(f"pre-registration {path}: suite must be one of {', '.join(SUITES)}")
    n_planned = table["n_planned"]
    if type(n_planned) is not int or n_planned < 1:
        raise PreregError(f"pre-registration {path}: n_planned must be a positive integer")
    k = table.get("k", 1)
    if type(k) is not int or k < 1:
        raise PreregError(f"pre-registration {path}: k must be a positive integer")
    alpha = table.get("alpha", 0.05)
    if type(alpha) not in (int, float) or not 0 < alpha < 1:
        raise PreregError(f"pre-registration {path}: alpha must be a number between 0 and 1")
    direction = table.get("direction", "two-sided")
    if direction not in DIRECTIONS:
        raise PreregError(f"pre-registration {path}: direction must be one of {', '.join(DIRECTIONS)}")
    axes, schemas = _axes(path, table.get("axes", {}))
    return Registration(
        path=path,
        sha256=digest_imported_bytes(data),
        id=table["id"],
        registered_at=table["registered_at"],
        step=table["step"],
        suite=table["suite"],
        n_planned=n_planned,
        k=k,
        primary_metric=table["primary_metric"],
        alpha=float(alpha),
        direction=direction,
        axes=axes,
        schemas=schemas,
        comparisons=_comparisons(path, table.get("comparisons", [])),
    )


def _axes(path: Path, raw: Any) -> tuple[dict[str, tuple[Any, ...]], dict[str, dict[str, Any]]]:
    if not isinstance(raw, dict):
        raise PreregError(f"pre-registration {path}: axes must be a table of [axes.<id>] tables")
    axes: dict[str, tuple[Any, ...]] = {}
    schemas: dict[str, dict[str, Any]] = {}
    for axis, spec in raw.items():
        if not isinstance(spec, dict) or set(spec) - {"levels", "schema"}:
            raise PreregError(f"pre-registration {path}: [axes.{axis}] takes levels and (for output_mode) schema only")
        levels = spec.get("levels")
        if not isinstance(levels, list) or len(levels) < 2 or any(type(level) not in _SCALARS for level in levels):
            raise PreregError(f"pre-registration {path}: [axes.{axis}] levels must list at least two strings or numbers")
        axes[axis] = tuple(levels)
        if "schema" in spec:
            if axis != "output_mode" or not isinstance(spec["schema"], dict):
                raise PreregError(f"pre-registration {path}: schema is a table and belongs to [axes.output_mode] only")
            schemas[axis] = dict(spec["schema"])
    return axes, schemas


def _comparisons(path: Path, raw: Any) -> tuple[Comparison, ...]:
    if not isinstance(raw, list):
        raise PreregError(f"pre-registration {path}: comparisons must be [[comparisons]] tables")
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"axis", "levels"}:
            raise PreregError(f"pre-registration {path}: each [[comparisons]] table has exactly axis and levels")
        levels = entry["levels"]
        if type(entry["axis"]) is not str or not isinstance(levels, list) or len(levels) != 2 or levels[0] == levels[1]:
            raise PreregError(f"pre-registration {path}: a comparison names one axis and two different levels")
        if any(type(level) not in _SCALARS for level in levels):
            raise PreregError(f"pre-registration {path}: comparison levels must be strings or numbers")
        out.append(Comparison(entry["axis"], levels[0], levels[1]))
    return tuple(out)


def block(registration: Registration, design: str, axes: Mapping[str, Sequence[Any]], cell: Mapping[str, Any]) -> AblationPrereg:
    """The prereg block of the run at ``cell`` of ``design`` over ``axes`` (the resolved levels, defaults included)."""

    return AblationPrereg(
        primary_metric=registration.primary_metric,
        pass_rule="holm",
        margin=0.0,
        n_planned=registration.n_planned * registration.k,
        registered_at=registration.registered_at,
        guard_metric=None,
        ablation=registration.id,
        prereg_sha256=registration.sha256,
        design=design,
        axes={axis: list(levels) for axis, levels in axes.items()},
        cell=dict(cell),
        alpha=registration.alpha,
        direction=registration.direction,
        comparisons=[{"axis": c.axis, "levels": [c.a, c.b]} for c in registration.comparisons],
    )
