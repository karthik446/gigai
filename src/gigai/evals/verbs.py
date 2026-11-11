"""``gigai <gig> eval ...``: core's eval verbs, on every in-process gig whose manifest has an ``[evals]`` table.

``loader.LazyGig.load`` adds the group next to core's ``export`` (``eval_verbs``). Each subverb is one line of
``EVAL_VERBS``: its name and the ``module:attr`` of its Click command, imported only when the subverb is looked up,
so mounting the group imports none of them. ``list``, ``run`` and ``rescore`` are here; ``compare``, ``label``,
``report`` and ``gate`` (jhf section 9.3) each add their line.
"""

from __future__ import annotations

import importlib
from typing import Any

import click

EVAL_VERBS: dict[str, str] = {
    "list": "gigai.evals.list:list_command",
    "run": "gigai.evals.run:run_command",
    "rescore": "gigai.evals.run:rescore_command",
    "validate-judge": "gigai.evals.judge:validate_judge_command",
    "compare": "gigai.evals.compare:compare_command",
    "gate": "gigai.evals.gates:gate_command",
    "label": "gigai.evals.label:label_command",
    "report": "gigai.evals.report:report_command",
    "base": "gigai.evals.base:base_command",
}


class EvalGroup(click.Group):
    """The ``eval`` group: its subverbs come from ``EVAL_VERBS``."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(EVAL_VERBS)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        target = EVAL_VERBS.get(cmd_name)
        if target is None:
            return None
        module, _, attr = target.partition(":")
        command = getattr(importlib.import_module(module), attr)
        assert isinstance(command, click.Command), target
        return command


def eval_verbs(manifest: dict[str, Any] | None) -> dict[str, click.Command]:
    """``{"eval": <group>}`` for a manifest with an ``[evals]`` table, else ``{}``."""

    if not isinstance((manifest or {}).get("evals"), dict):
        return {}
    return {"eval": EvalGroup("eval", help="This gig's evals: the steps, datasets, scorers and gates its [evals] table declares.")}
