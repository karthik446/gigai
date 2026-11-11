"""The ``[evals]`` table of a gig manifest, read as data: steps, per-suite datasets, ``{data}`` paths, licences.

``gigai gig check`` item 11 and ``gigai <gig> eval list`` both read the table through this module; neither imports
the gig (item 11 imports the scorer symbols in a child interpreter, ``eval list`` never does). A dataset path is
package-relative unless it starts with ``{data}``: the gig folder ``<home>/gigs/<gig>/`` (``gigs.home.gig_folder``),
the meaning process mode gives ``{data}``. Data there is private: it is never shipped, so its absence is reported,
not failed, except for the ``ci`` suite, whose rows must be in the package for a 0-call run. A path that leaves its
root (``..``, an absolute path, a symlink out) is ``outside its root``, a failure in every suite.

A shipped (package-relative) dataset, and every ``ci`` dataset, must carry a licence on ``SPDX_ALLOWED`` (B G1,
research/gigai-2/evals-respike/B-capability-map-criteria.md:105). Any other licence (``CC-BY-NC-*``, "research only")
or ``private`` is allowed only for private measurement data under ``{data}`` outside the ``ci`` suite, never for rows
the gig redistributes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .home import GigNameError, gig_folder

#: SPDX ids a dataset the gig ships (and every ``ci`` dataset) may carry; compared case-insensitively.
SPDX_ALLOWED = (
    "CC0-1.0",
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "MIT",
    "Apache-2.0",
    "BSD-3-Clause",
    "ODC-By-1.0",
    "CDLA-Permissive-2.0",
)
DATA_TOKEN = "{data}"
SUITES = ("ci", "release", "upgrade")
PRESENT, ABSENT_PRIVATE, MISSING, UNCHECKED = "present", "absent (private)", "missing", "not checked"
OUTSIDE = "outside its root"

_ALLOWED = frozenset(licence.casefold() for licence in SPDX_ALLOWED)


@dataclass(frozen=True)
class EvalDataset:
    """One suite's dataset of one step, as the manifest declares it (a bare path carries no provenance)."""

    suite: str
    path: str
    source: str | None = None
    licence: str | None = None
    split: str | None = None
    sha256: str | None = None
    slice_recipe: str | dict[str, Any] | None = None
    seed: int | None = None

    @property
    def private(self) -> bool:
        """Under ``{data}``: the operator's own rows, never in the package."""

        return self.path == DATA_TOKEN or self.path.startswith(DATA_TOKEN + "/")

    def provenance(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source": self.source,
            "licence": self.licence,
            "split": self.split,
            "sha256": self.sha256,
            "slice_recipe": self.slice_recipe,
            "seed": self.seed,
        }


def eval_steps(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The ``[[evals.steps]]`` tables of ``manifest``, in order; ``[]`` when it has none."""

    evals = manifest.get("evals") if manifest else None
    steps = evals.get("steps") if isinstance(evals, dict) else None
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def step_datasets(step: dict[str, Any]) -> list[EvalDataset]:
    """The step's datasets in suite order; the old bare ``dataset`` path stands for ``release`` when that is unset."""

    table = step.get("datasets")
    table = dict(table) if isinstance(table, dict) else {}
    if "release" not in table and isinstance(step.get("dataset"), str):
        table["release"] = step["dataset"]
    found = []
    for suite in SUITES:
        value = table.get(suite)
        if isinstance(value, str):
            found.append(EvalDataset(suite, value))
        elif isinstance(value, dict) and isinstance(value.get("path"), str):
            found.append(
                EvalDataset(
                    suite,
                    value["path"],
                    source=value.get("source"),
                    licence=value.get("licence"),
                    split=value.get("split"),
                    sha256=value.get("sha256"),
                    slice_recipe=value.get("slice_recipe"),
                    seed=value.get("seed"),
                )
            )
    return found


def licence_problem(dataset: EvalDataset) -> str | None:
    """Why ``dataset``'s licence is not acceptable, or ``None``: allow-listed, or private data under ``{data}``
    outside ``ci``."""

    licence = dataset.licence
    if isinstance(licence, str) and licence.casefold() in _ALLOWED:
        return None
    allowed = ", ".join(SPDX_ALLOWED)
    named = "no licence" if licence is None else f"licence {licence!r}"
    if dataset.suite == "ci":
        return f"{named} on a ci dataset: a ci dataset ships in the package, so it needs one of {allowed}"
    if dataset.private:
        return None
    return f"{named} on a shipped dataset: use one of {allowed}, or keep the rows private under {DATA_TOKEN}"


def data_folder(home: Path, gig: object) -> Path | None:
    """What ``{data}`` stands for: the gig folder of ``gig`` under ``home``; ``None`` for a name no folder can have."""

    try:
        return gig_folder(home, gig)  # type: ignore[arg-type] - check_name refuses a non-string
    except GigNameError:
        return None


def resolve(path: str, package_dir: Path | None, data_dir: Path | None) -> Path | None:
    """Where ``path`` points: under ``data_dir`` for ``{data}/...``, else under ``package_dir``. ``None`` when that
    root is unknown or the path leaves it."""

    if path == DATA_TOKEN or path.startswith(DATA_TOKEN + "/"):
        root, relative = data_dir, path[len(DATA_TOKEN) :].lstrip("/")
    else:
        root, relative = package_dir, path
    if root is None:
        return None
    base = root.resolve()
    target = (base / relative).resolve()
    return target if target.is_relative_to(base) else None


def dataset_status(dataset: EvalDataset, package_dir: Path | None, data_dir: Path | None) -> tuple[str, Path | None]:
    """``(present | absent (private) | missing | outside its root | not checked, resolved path)``; ``not checked``
    when the root is unknown (a bare manifest file has no package), ``outside its root`` when the path (or a symlink
    on it) leaves the package or ``{data}``."""

    root = data_dir if dataset.private else package_dir
    if root is None:
        return UNCHECKED, None
    target = resolve(dataset.path, package_dir, data_dir)
    if target is None:
        return OUTSIDE, None
    if target.is_file():
        return PRESENT, target
    return (ABSENT_PRIVATE if dataset.private else MISSING), target


def file_sha256(path: Path) -> str:
    """SHA-256 (hex) of the file's bytes."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
