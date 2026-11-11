"""Write stored eval runs as Inspect log files (``gigai <gig> eval report --export inspect``), shaped for inspect-ai 0.3.288.

One JSON log per run, in ``<home>/gigs/<gig>/inspect/`` (next to ``evals.sqlite``), named
``<UTC created_at as YYYY-MM-DDTHH-MM-SS>_<gig>-<step>_<run id>.json`` with every character of the task and id parts
outside ``[A-Za-z0-9.-]`` turned into ``-``: the ``{timestamp}_{task}_{id}.json`` shape ``inspect view`` lists
(``inspect_ai/log/_file.py`` ``is_log_file``).

The top-level keys are written in the order ``version, status, eval, plan, results, stats, samples``: Inspect's
header-only read (``read_eval_log(header_only=True)``, ``/api/log-headers``, which the viewer's log list uses) scans the
keys up to ``samples``, needs ``version`` among them and validates ``plan`` and ``stats``. The ``eval`` spec carries
``created``, ``task``, ``dataset``, ``model`` and ``config`` (the fields the pinned ``EvalSpec`` requires) plus
``task_id``, ``run_id`` and ``metadata``; ``results`` holds each metric's mean, and there is one sample per trace
(``id`` the item id, ``epoch`` the repeat + 1, ``input``, ``target``, ``output.choices[0].message``, ``scores``). A
metric is named by the part of its scorer id after the last ``:``, or by the whole id when two scorers share it.

Privacy: without ``--include-inputs`` a sample's ``input`` is the trace input's SHA-256, its ``target`` is empty and
its output message is the raw output's SHA-256; with it they hold the prompt, the dataset row's ``expected`` and the
raw output. The file is written mode 0600 in a 0700 folder. A file is only ever replaced by a new export of the run
it holds: two runs mapping to one name, or a name taken by anything else, are refused before anything is written.

The ``inspect`` extra (``pip install 'gigai[inspect]'``) is only checked for, never imported: the log is plain JSON.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..sdk.context import GigContext
from . import records
from .report import metric_names
from .run import RunRefused

#: The inspect-ai release the log shape follows (the ``inspect`` extra pins it).
INSPECT_VERSION = "0.3.288"
INSTALL_HINT = "pip install 'gigai[inspect]'"
#: The folder under the gig folder the logs go to.
LOG_FOLDER = "inspect"
#: Every exported file name matches this.
FILE_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d-]+_[^_]+_[^_]+\.json$")


def require_inspect() -> None:
    """Refuse (exit 2) when the ``inspect`` extra is not installed; nothing is imported."""

    if importlib.util.find_spec("inspect_ai") is None:
        raise RunRefused(f"--export inspect needs the inspect extra (inspect-ai {INSPECT_VERSION}): install it with {INSTALL_HINT}")


def log_folder(gig: GigContext) -> Path:
    """Where the logs of ``gig`` go: ``inspect/`` in the gig folder, next to the eval store."""

    return records.evals_db_path(gig.name, gig.home).parent / LOG_FOLDER


def file_name(gig_name: str, run: Mapping[str, Any]) -> str:
    """``<UTC created_at>_<gig>-<step>_<run id>.json``, the name ``inspect view`` lists."""

    return f"{_created(run['created_at']).strftime('%Y-%m-%dT%H-%M-%S')}_{_task(gig_name, run)}_{_part(run['run'])}.json"


def build_log(gig_name: str, run: Mapping[str, Any], *, include_inputs: bool = False) -> dict[str, Any]:
    """The Inspect log of one ``eval report`` run entry (``report.build``), as plain JSON data in the key order the
    header-only read needs."""

    items = run["items"]
    models = sorted({item["model"] for item in items if item["model"]})
    names = metric_names({m["scorer"] for m in run["metrics"]} | {scorer for item in items for scorer in item["scores"]})
    samples = [_sample(item, names, include_inputs) for item in items]
    created = _created(run["created_at"]).isoformat(timespec="seconds")
    return {
        "version": 2,
        "status": "success",
        "eval": {
            "run_id": run["run"],
            "created": created,
            "task": _task(gig_name, run),
            "task_id": _part(run["run"]),
            "dataset": {"name": run["dataset"]["id"], "samples": run["n"]},
            "model": ",".join(models) or "none",
            "config": {"epochs": run["k"]},
            "metadata": {
                "gigai": {"gig": gig_name, "step": run["step"], "suite": run["suite"], "dataset_sha256": run["dataset"]["sha256"]}
            },
        },
        "plan": {"name": "plan", "steps": []},
        "results": {
            "total_samples": len(samples),
            "completed_samples": len(samples),
            "scores": [
                {"name": names[m["scorer"]], "scorer": m["scorer"], "metrics": {"mean": {"name": "mean", "value": m["mean"]}}}
                for m in run["metrics"]
            ],
        },
        "stats": {"started_at": created},
        "samples": samples,
    }


def write_logs(gig: GigContext, result: Mapping[str, Any], *, include_inputs: bool = False) -> list[Path]:
    """Write one log per run of ``result`` (``report.build``). Every log is built and every name checked before the
    first is written: two runs mapping to one name, or a name taken by anything but a log of the same run, refuse."""

    folder = log_folder(gig)
    if folder.is_symlink():
        raise RunRefused(f"{folder} is a symlink: remove it and export again")
    logs: dict[str, tuple[str, dict[str, Any]]] = {}
    for run in result["runs"]:
        name = file_name(gig.name, run)
        if name in logs:
            raise RunRefused(f"cannot export: runs {logs[name][0]} and {run['run']} map to the same file name {name}")
        logs[name] = (run["run"], build_log(gig.name, run, include_inputs=include_inputs))
    for name, (run_id, _log) in logs.items():
        if (folder / name).is_symlink() or (folder / name).exists() and _logged_run(folder / name) != run_id:
            raise RunRefused(f"cannot export run {run_id}: {folder / name} holds something else; move it away and export again")
    folder.mkdir(mode=0o700, exist_ok=True)
    written: list[Path] = []
    for name, (_run_id, log) in logs.items():
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=".export-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(log, stream, indent=2)  # key order kept: version first, samples last
            os.replace(temporary, folder / name)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        written.append(folder / name)
    return written


# --- internals -------------------------------------------------------------------------------------------------


def _logged_run(path: Path) -> str | None:
    """The run id of the gigai export at ``path``, or ``None`` when it is not one."""

    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    run_id = log.get("eval", {}).get("run_id") if isinstance(log, dict) and isinstance(log.get("eval"), dict) else None
    return run_id if isinstance(run_id, str) else None


def _sample(item: Mapping[str, Any], names: Mapping[str, str], include_inputs: bool) -> dict[str, Any]:
    expected = item.get("expected") if include_inputs else None
    target = "" if expected is None else expected if isinstance(expected, str) else json.dumps(expected, sort_keys=True)
    content = item["output"] if include_inputs else item["output_sha256"]
    return {
        "id": item["item"],
        "epoch": item["repeat"] + 1,
        "input": item["input"] if include_inputs else item["input_sha256"],
        "target": target,
        "output": {
            "model": item["model"] or "none",
            "choices": [{"message": {"role": "assistant", "content": content}, "stop_reason": "stop"}],
        },
        "scores": {names[scorer]: {"value": value} for scorer, value in sorted(item["scores"].items())},
        "metadata": {"cluster": item["cluster"], "trace": item["trace"]},
    }


def _task(gig_name: str, run: Mapping[str, Any]) -> str:
    return _part(f"{gig_name}-{run['step']}")


def _part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]", "-", text)


def _created(stamp: str) -> datetime:
    try:
        created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise RunRefused(f"cannot export: created_at {stamp!r} is not an ISO time") from exc
    return (created if created.tzinfo else created.replace(tzinfo=UTC)).astimezone(UTC)
