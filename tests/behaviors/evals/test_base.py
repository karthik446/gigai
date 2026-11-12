"""``gigai <gig> eval base``: the shared eval sets on the model ladder, with the measurement gates a-e.

Every run here answers from the committed ci slices' canned completions (``--deterministic``) or, for the call path,
through the deterministic adapter standing in for a ladder rung. Adapter construction and ``DeterministicAdapter.invoke``
are counted; no socket leaves 127.0.0.1 and no other adapter is ever built.
"""

from __future__ import annotations

import copy
import importlib
import json
import logging
import math
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from gigai.adapters import factory
from gigai.adapters.deterministic import DeterministicAdapter
from gigai.adapters.ollama_local import OllamaLocalAdapterError, OllamaLocalIdentityError, OllamaLocalResponseError
from gigai.adapters.port import (
    CapabilityMismatchError,
    ModelAuthenticationRequired,
    ModelInvocationCancelled,
    ModelInvocationError,
    ModelOutputNotJSONError,
    UnsupportedRequestFieldError,
)
from gigai.config import load_config
from gigai.evals import base
from gigai.evals import datasets as ds
from gigai.evals import run as eval_run
from gigai.evals.records import evals_db_path, open_evals_db
from gigai.evals.run import CapReachedError
from gigai.model_call import BoundedCallError
from gigai.sdk import GigContext
from gigai.evals.stats import NonFiniteScoreError
from tests.behaviors.gigs.test_loader import FIXTURES, _add_fixture, _run
from tests.behaviors.sdk.test_ctx_model import MANIFEST, loopback_only, make_home

GIG = "eval-fixture"
PACKAGE = FIXTURES / GIG / "gig_eval_fixture"
GATE_SETS = ("contractnli", "ragtruth", "quality", "ifeval", "jsonschemabench", "rewardbench2")
#: The committed snapshot: each gate set's score (points) when every rung answers with the ci slices' completions.
SNAPSHOT = {"contractnli": 100.0, "ragtruth": 100.0, "quality": 100.0, "ifeval": 60.0, "jsonschemabench": 30.0, "rewardbench2": 100.0}
CI_ITEMS = {"contractnli": 10, "ragtruth": 20, "quality": 10, "ifeval": 10, "jsonschemabench": 10, "rewardbench2": 10}
#: claude-haiku alone on the gate tier: 560 items, 100 noise items x 2 more repeats (its main answers are repeat 0),
#: and, as the only rung, both 100-item calling canaries.
HAIKU_GATE_CALLS = 560 + 200 + 200
assert loopback_only  # the autouse socket guard, imported to apply here


@pytest.fixture
def home(tmp_path: Path) -> Path:
    make_home(tmp_path / "home")
    return tmp_path / "home"


def context(home: Path, manifest: dict[str, Any] | None = None) -> GigContext:
    return GigContext(home, GIG, manifest or MANIFEST, logging.getLogger("gigai.gig.eval-fixture"))


@pytest.fixture
def gig(home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[GigContext]:
    monkeypatch.syspath_prepend(str(FIXTURES / GIG))
    monkeypatch.setattr(eval_run, "_package_dir", lambda name: PACKAGE if name == GIG else None)
    importlib.import_module("gig_eval_fixture")
    yield context(home)
    for name in [name for name in sys.modules if name.split(".")[0] == "gig_eval_fixture"]:
        sys.modules.pop(name)


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every adapter the base run builds (target names) and, prefixed ``invoke:``, every call that reaches one."""

    made: list[str] = []
    resolve = factory.resolve_model_adapter

    def counted(config: Any, target: str, **kwargs: Any) -> Any:
        made.append(target)
        return resolve(config, target, **kwargs)

    invoke = DeterministicAdapter.invoke

    def invoked(self: DeterministicAdapter, request: Any) -> Any:
        made.append(f"invoke:{request.target_name}")
        return invoke(self, request)

    monkeypatch.setattr(factory, "resolve_model_adapter", counted)
    monkeypatch.setattr(DeterministicAdapter, "invoke", invoked)
    return made


def invoke(gig: GigContext, *argv: str) -> Any:
    return CliRunner().invoke(base.base_command, list(argv), obj=gig, catch_exceptions=False)


def refused(gig: GigContext, *argv: str) -> str:
    result = invoke(gig, *argv)
    assert result.exit_code == 2, (result.exit_code, result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
    return result.stderr


def deterministic_run(gig: GigContext, *argv: str) -> dict[str, Any]:
    result = invoke(gig, "--deterministic", "--json", *argv)
    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def rows(home: Path, sql: str, *args: object) -> list[tuple]:
    connection = sqlite3.connect(evals_db_path(GIG, home))
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def ci_items(set_name: str) -> list[ds.Item]:
    return ds.read_slice(Path(ds.CI_DIR) / f"{set_name}.jsonl")


def with_gate(**extra: Any) -> dict[str, Any]:
    """MANIFEST with ``extra`` merged into every step's release gate."""

    manifest = copy.deepcopy(MANIFEST)
    for step in manifest["evals"]["steps"]:
        gate = (step.get("gates") or {}).get("release")
        if gate is not None:
            gate.update(extra)
    return manifest


# --- the ladder -------------------------------------------------------------------------------------------------


def test_the_shipped_ladder_has_six_rungs_three_tiers_and_the_registered_gates() -> None:
    ladder = base.load_ladder()
    assert [rung.name for rung in ladder.rungs] == ["claude-sonnet", "claude-haiku", "gpt-5-mini", "gemini-flash-lite", "gemma", "deterministic"]
    ranks = [rung.rank for rung in ladder.rungs]
    assert ranks == sorted(ranks) and ranks[0] == 1
    assert [rung.name for rung in ladder.rungs if rung.deterministic] == ["deterministic"]
    assert tuple(ladder.tiers["gate"]) == GATE_SETS and sum(ladder.tiers["gate"].values()) == 560
    assert sum(ladder.tiers["judge"].values()) == 250
    assert sum(ladder.tiers["full"].values()) == 560 + 250 + 200
    assert ladder.order == {"min_tau": 0.6, "min_rungs": 3, "saturated_at": 0.97}
    assert ladder.noise["k"] == 3 and ladder.noise["max_sd_points"] == 2.0 and ladder.noise["max_flip_rate"] == 0.10
    assert (ladder.noise["rung"], ladder.noise["items"], ladder.canaries["rung"]) == ("claude-haiku", 100, "gemini-flash-lite")
    assert [ladder.canaries[name].get("items") for name in base.CANARIES] == [100, 100, None]
    assert ladder.replay["max_abs_delta"] == 0.0005
    for rung in ladder.rungs:
        assert set(rung.knobs) <= set(base.KNOBS)
        assert rung.knobs or rung.deterministic or rung.no_knobs


def test_the_ollama_rung_pins_a_tagged_model_because_ollama_lists_name_tag() -> None:
    ollama = [rung for rung in base.load_ladder().rungs if rung.adapter == "ollama_local"]
    assert [rung.model for rung in ollama] == ["gemma3:4b"]
    for rung in ollama:
        name, _, tag = rung.model.partition(":")
        assert name and tag and ":" not in tag and rung.model.strip() == rung.model


def test_the_fixture_gig_pins_the_shipped_ladder_sha256() -> None:
    pins = {step["gates"]["release"]["prereg"]["ladder_sha256"] for step in MANIFEST["evals"]["steps"] if "release" in step.get("gates", {})}
    assert pins == {base.ladder_sha256()} == {base.load_ladder().sha256}


def test_a_calling_canary_takes_its_items_or_100_and_spreads_them_round_robin(tmp_path: Path) -> None:
    path = tmp_path / "ladder.toml"
    assert 'sets = ["ifeval", "quality"]\nitems = 100\n' in SHIPPED and "keep = 0.25\nitems = 100\n" in SHIPPED
    path.write_text(SHIPPED.replace('sets = ["ifeval", "quality"]\nitems = 100\n', 'sets = ["ifeval", "quality"]\n').replace("keep = 0.25\nitems = 100\n", "keep = 0.25\nitems = 7\n"), encoding="utf-8")
    ladder = base.load_ladder(path)
    assert (ladder.canaries["broken_prompt"]["items"], ladder.canaries["truncated_context"]["items"]) == (base.CANARY_ITEMS, 7) == (100, 7)
    assert base.canary_items(ladder, {"quality": 2, "ragtruth": 10, "ifeval": 300}) == {
        "broken_prompt": {"ifeval": 98, "quality": 2},
        "truncated_context": {"quality": 2, "ragtruth": 5},
    }


# --- gate a: Kendall tau-b against the published ranks ---------------------------------------------------------


def test_kendall_tau_b_values_ties_and_non_finite_input() -> None:
    assert base.kendall_tau_b([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert base.kendall_tau_b([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert base.kendall_tau_b([1, 2, 3, 4], [1, 2, 3, 3]) == pytest.approx(5 / math.sqrt(30), abs=1e-12)
    assert base.kendall_tau_b([1, 2, 3], [5, 5, 5]) is None
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(NonFiniteScoreError):
            base.kendall_tau_b([1, 2, bad], [1, 2, 3])


def gate_a(scores: list[float | None], *, min_tau: float = 0.6, min_rungs: int = 3) -> dict[str, Any]:
    names = [f"r{index}" for index in range(len(scores))]
    return base.order_gate(dict(zip(names, scores, strict=True)), {name: index + 1 for index, name in enumerate(names)}, min_tau=min_tau, min_rungs=min_rungs)


def test_order_gate_compares_scores_with_ranks_where_one_is_the_strongest() -> None:
    aligned = gate_a([90, 80, 70, 60, 50])
    assert (aligned["tau"], aligned["status"]) == (1.0, "pass")
    reverse = gate_a([50, 60, 70, 80, 90])
    assert (reverse["tau"], reverse["status"]) == (-1.0, "fail")
    # Two discordant pairs out of ten: tau 0.6, exactly the line, passes; three fail.
    at_line = gate_a([80, 90, 70, 50, 60])
    assert (at_line["tau"], at_line["status"]) == (0.6, "pass")
    below = gate_a([70, 90, 80, 50, 60])
    assert below["tau"] == pytest.approx(0.4) and below["status"] == "fail"
    assert gate_a([80, 90, 70, 50, 60], min_tau=0.61)["status"] == "fail"


def test_order_gate_needs_three_measured_rungs_and_undefined_tau_is_not_validated() -> None:
    assert gate_a([90, 80])["status"] == "not_validated"
    assert gate_a([90, None, 80])["status"] == "not_validated"
    assert gate_a([90, 80, 70])["status"] == "pass"
    tied = gate_a([50, 50, 50])
    assert (tied["tau"], tied["status"]) == (None, "not_validated")


# --- gate b: the noise floor -------------------------------------------------------------------------------------


def repeats(right: list[int], n: int = 100, set_name: str = "s") -> list[tuple[str, list[float]]]:
    """``n`` items over ``len(right)`` repeats: in repeat r the first ``right[r]`` items are right (1.0)."""

    return [(set_name, [1.0 if index < count else 0.0 for count in right]) for index in range(n)]


def test_noise_gate_sd_is_the_sample_sd_of_suite_points_and_the_line_is_inclusive() -> None:
    at_line = base.noise_gate(repeats([50, 52, 54]), max_sd_points=2.0, max_flip_rate=0.10)
    assert at_line["suite_scores"] == [50.0, 52.0, 54.0]
    assert (at_line["sd"], at_line["flips"], at_line["flip_rate"], at_line["status"]) == (2.0, 4, 0.04, "pass")
    assert base.noise_gate(repeats([50, 52, 55]), max_sd_points=2.0, max_flip_rate=0.10)["status"] == "fail"
    small = base.noise_gate(repeats([500, 520, 541], n=1000), max_sd_points=2.0, max_flip_rate=0.10)
    # suite points 50, 52, 54.1: deviations -61/30, -1/30, 62/30 from the mean.
    assert small["sd"] == pytest.approx(math.sqrt((61**2 + 1 + 62**2) / 900 / 2), abs=1e-12) and small["status"] == "fail"


def flips(count: int, n: int = 100) -> list[tuple[str, list[float]]]:
    """``count`` items flip between two repeats, balanced so the suite score barely moves."""

    out = []
    for index in range(n):
        if index < count:
            out.append(("s", [1.0, 0.0] if index % 2 else [0.0, 1.0]))
        else:
            out.append(("s", [1.0, 1.0] if index % 2 else [0.0, 0.0]))
    return out


def test_noise_gate_flip_rate_counts_items_whose_right_wrong_status_changes() -> None:
    at_line = base.noise_gate(flips(10), max_sd_points=2.0, max_flip_rate=0.10)
    assert (at_line["sd"], at_line["flip_rate"], at_line["status"]) == (0.0, 0.10, "pass")
    over = base.noise_gate(flips(11), max_sd_points=2.0, max_flip_rate=0.10)
    assert over["flip_rate"] == 0.11 and over["sd"] < 2.0 and over["status"] == "fail"
    # 0.5 is right; a value moving within one side never flips.
    edge = base.noise_gate([("s", [0.5, 0.49]), ("s", [0.5, 0.9]), ("s", [0.2, 0.0])], max_sd_points=100.0, max_flip_rate=1.0)
    assert edge["flips"] == 1


def test_noise_gate_sets_weigh_equally_and_bad_input_is_handled() -> None:
    mixed = base.noise_gate(repeats([10, 10], n=10, set_name="a") + repeats([0, 50], n=100, set_name="b"), max_sd_points=100.0, max_flip_rate=1.0)
    assert mixed["suite_scores"] == [50.0, 75.0]
    assert base.noise_gate([("s", [1.0, 0.0])], max_sd_points=2.0, max_flip_rate=0.1)["status"] == "not_validated"
    assert base.noise_gate(repeats([1], n=10), max_sd_points=2.0, max_flip_rate=0.1)["status"] == "not_validated"
    with pytest.raises(NonFiniteScoreError):
        base.noise_gate([("s", [1.0, math.nan]), ("s", [1.0, 1.0])], max_sd_points=2.0, max_flip_rate=0.1)


# --- gate c: canaries --------------------------------------------------------------------------------------------


def planted(drop: int, n: int = 100) -> tuple[list[float], list[float]]:
    """Every baseline item right; the canary loses ``drop`` of them."""

    return [1.0] * n, [0.0] * drop + [1.0] * (n - drop)


@pytest.mark.parametrize(("name", "drop", "min_drop"), [("broken_prompt", 12, 10), ("truncated_context", 18, 15), ("wrong_label", 35, 30)])
def test_a_planted_canary_drop_above_its_line_passes(name: str, drop: int, min_drop: float) -> None:
    verdict = base.canary_verdict(name, *planted(drop), min_drop=min_drop, max_p=0.01)
    assert (verdict["drop"], verdict["b"], verdict["c"], verdict["status"]) == (float(drop), drop, 0, "pass")
    assert verdict["p"] == pytest.approx(2 * 0.5**drop)


def test_a_canary_fails_on_a_small_drop_a_rise_or_a_large_p_and_names_itself() -> None:
    small = base.canary_verdict("broken_prompt", *planted(5), min_drop=10, max_p=0.01)
    assert small["status"] == "fail" and small["reason"] == "canary broken_prompt: drop 5.00 points < 10"
    assert base.canary_verdict("broken_prompt", *planted(10), min_drop=10, max_p=0.01)["status"] == "pass"
    assert base.canary_verdict("broken_prompt", *planted(9), min_drop=10, max_p=0.01)["status"] == "fail"
    after, before = planted(20)
    rise = base.canary_verdict("truncated_context", before, after, min_drop=15, max_p=0.01)
    assert rise["drop"] == -20.0 and rise["status"] == "fail"
    # 20 items lost, 10 gained: a 10-point drop, but McNemar p (about 0.099) is not below 0.01.
    baseline = [1.0] * 20 + [0.0] * 10 + [1.0] * 70
    canary = [0.0] * 20 + [1.0] * 10 + [1.0] * 70
    noisy = base.canary_verdict("wrong_label", baseline, canary, min_drop=10, max_p=0.01)
    assert (noisy["drop"], noisy["b"], noisy["c"], noisy["status"]) == (10.0, 20, 10, "fail")
    assert noisy["reason"].startswith("canary wrong_label: McNemar p 0.09")
    with pytest.raises(NonFiniteScoreError):
        base.canary_verdict("wrong_label", [1.0, math.inf], [0.0, 0.0], min_drop=10, max_p=0.01)
    assert base.canary_verdict("wrong_label", [], [], min_drop=10, max_p=0.01)["status"] == "not_validated"


def test_canary_transforms_are_seeded_and_wrong_labels_differ_from_gold() -> None:
    prompt = "First sentence here. Second one follows. Third closes it. Fourth adds more. Fifth ends."
    assert base.broken_prompt(prompt, "id-1") == base.broken_prompt(prompt, "id-1") != prompt
    assert sorted(base.broken_prompt(prompt, "id-1").split()) == sorted(prompt.split())
    assert len(base.truncated_context(prompt, 0.25)) < len(prompt) / 2
    for set_name in ("quality", "ragtruth", "contractnli", "rewardbench2", "judgebench", "mtbench_human"):
        for item in ci_items(set_name):
            assert base.wrong_label(set_name, item.expected) != item.expected


def ifeval(instruction: str, text: str, **kwargs: Any) -> float | None:
    return base.ifeval_score(text, {"instruction_id_list": [instruction], "kwargs": [kwargs]})


def test_approximate_ifeval_checks_a_non_blank_title_and_a_quoted_ending() -> None:
    assert ifeval("detectable_format:title", "<<Rain>>\nIt rained.") == 1.0
    assert ifeval("detectable_format:title", "<< >>\nIt rained.") == 0.0
    assert ifeval("detectable_format:title", "<<>> no title") == 0.0
    assert ifeval("detectable_format:title", "<<\nRain>>") == 0.0
    end = "Any other questions?"
    assert ifeval("startend:end_checker", f'"It rained. {end}"', end_phrase=end) == 1.0
    assert ifeval("startend:end_checker", f"It rained. {end}  ", end_phrase=end) == 1.0
    assert ifeval("startend:end_checker", f"{end} It rained.", end_phrase=end) == 0.0
    assert ifeval("length_constraints:number_words", "two words", num_words=2, relation="at least") is None
    assert base.IFEVAL_NOTE == "approximate IFEval: 15 of 25 instruction types; others unscored"


# --- the call cap and the plan -----------------------------------------------------------------------------------


def test_the_cap_refuses_before_the_call_and_counts_failed_calls() -> None:
    made: list[int] = []
    cap = base.CallCap(2)
    assert cap.call(lambda: made.append(1)) is None
    with pytest.raises(ZeroDivisionError):
        cap.call(lambda: 1 / 0)
    with pytest.raises(CapReachedError, match=r"^cap 2 reached before call 3$"):
        cap.call(lambda: made.append(3))
    assert (made, cap.made) == ([1], 2)
    with pytest.raises(CapReachedError, match=r"^cap 0 reached before call 1$"):
        base.CallCap(0).call(lambda: made.append(0))
    assert made == [1]


def test_allocate_takes_round_robin_within_each_set() -> None:
    assert base.allocate({"a": 10, "b": 3, "c": 100}, 20) == {"a": 9, "b": 3, "c": 8}
    assert base.allocate({"a": 2, "b": 3}, 200) == {"a": 2, "b": 3}


def test_the_plan_counts_calls_and_dollars_per_rung() -> None:
    ladder = base.load_ladder()
    haiku = base.plan(ladder, [ladder.rung("claude-haiku")], ladder.tiers["gate"], False)
    row = haiku["rungs"][0]
    assert (row["calls"], row["noise_calls"], row["canary_calls"], row["model_calls"]) == (HAIKU_GATE_CALLS, 200, 200, HAIKU_GATE_CALLS)
    assert row["dollars"] == 28.58 and haiku["dollars"] == 28.58
    # The approved minimum run: noise on claude-haiku, canaries on gemini-flash-lite, gemma free.
    minimum = base.plan(ladder, [ladder.rung(name) for name in ("claude-haiku", "gemini-flash-lite", "gemma")], ladder.tiers["gate"], False)
    assert [(r["rung"], r["calls"], r["noise_calls"], r["canary_calls"], r["model_calls"], r["dollars"]) for r in minimum["rungs"]] == [
        ("claude-haiku", 760, 200, 0, 760, 22.02),
        ("gemini-flash-lite", 760, 0, 200, 760, 2.00),
        ("gemma", 560, 0, 0, 560, 0.0),
    ]
    assert (minimum["noise_rung"], minimum["canary_rung"], minimum["model_calls"], minimum["dollars"]) == ("claude-haiku", "gemini-flash-lite", 2080, 24.02)
    flash = ladder.rung("gemini-flash-lite")
    assert ladder.cost(flash, "quality", 0.25) == pytest.approx((29232 * 0.25 * 0.10 + 5607 * 0.40) / 1_000_000, rel=1e-12)
    assert ladder.cost(flash, "quality") == pytest.approx((29232 * 0.10 + 5607 * 0.40) / 1_000_000, rel=1e-12)
    whole = base.plan(ladder, list(ladder.rungs), ladder.tiers["gate"], False)
    assert whole["model_calls"] == 3 * 560 + 2 * 760 and whole["dollars"] == 76.74
    assert [r["model_calls"] for r in whole["rungs"]][-1] == 0 and whole["rungs"][-1]["calls"] == 560
    assert base.plan(ladder, list(ladder.rungs), ladder.tiers["gate"], True)["model_calls"] == 0
    judge = base.plan(ladder, [ladder.rung("claude-haiku")], ladder.tiers["judge"], False)["rungs"][0]
    assert (judge["calls"], judge["noise_calls"], judge["canary_calls"]) == (250 + 200, 200, 0)


def test_plan_prints_and_builds_nothing(gig: GigContext, built: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "_ollama_models", lambda url: pytest.fail("--plan probes nothing"))
    result = invoke(gig, "--plan", "--tier", "gate", "--rungs", "claude-haiku,gemini-flash-lite,gemma")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "total: 2080 model calls, $24.02"
    assert "noise 200; canaries 0; 760 calls (760 model calls), $22.02" in result.stdout
    assert "noise 0; canaries 200; 760 calls (760 model calls), $2.00" in result.stdout
    as_json = json.loads(invoke(gig, "--plan", "--json", "--rungs", "claude-haiku").stdout)
    assert as_json["plan"]["model_calls"] == HAIKU_GATE_CALLS and "gates" not in as_json and "paid_calls" not in as_json["plan"]
    assert built == [] and not evals_db_path(GIG, gig.home).exists()


# --- refusals: exit 2 before any row or call ---------------------------------------------------------------------


def test_cap_refusals_come_before_reachability_rows_and_adapters(gig: GigContext, built: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "_ollama_models", lambda url: pytest.fail("a refused run probes nothing"))
    prefix = f"gigai {GIG} eval: "
    assert refused(gig, "--rungs", "claude-haiku", "--max-calls", "0") == (
        f"{prefix}cap 0 reached before call 1 (the plan needs {HAIKU_GATE_CALLS} model calls; see --plan)\n"
    )
    # gemma is free but still a model: alone it also answers the noise and canary runs.
    assert refused(gig, "--rungs", "gemma", "--max-calls", "0").startswith(f"{prefix}cap 0 reached before call 1 (the plan needs {HAIKU_GATE_CALLS} model calls")
    assert refused(gig, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS - 1)).startswith(
        f"{prefix}cap {HAIKU_GATE_CALLS - 1} reached before call {HAIKU_GATE_CALLS} ("
    )
    assert refused(gig, "--rungs", "claude-haiku") == (
        f"{prefix}the base run plans {HAIKU_GATE_CALLS} model calls: pass --max-calls N (or give a release gate max_calls)\n"
    )
    assert refused(context(gig.home, with_gate(max_calls=0)), "--rungs", "claude-haiku", "--max-calls", "5000").startswith(
        f"{prefix}cap 0 reached before call 1 ("
    )
    assert built == [] and not evals_db_path(GIG, gig.home).exists()


def test_a_cap_at_the_plan_runs_and_names_the_unreachable_rung(gig: GigContext, built: list[str]) -> None:
    result = invoke(gig, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS), "--json")
    assert result.exit_code == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["skipped"] == [{"rung": "claude-haiku", "reason": "no model target 'claude-haiku' in config.toml"}]
    assert [(r["name"], r["status"], r["calls_made"]) for r in report["rungs"]] == [("claude-haiku", "skipped", 0)]
    assert {key: gate["status"] for key, gate in report["gates"].items() if key != "c"} == dict.fromkeys("abde", "not_validated")
    assert report["calls_made"] == 0 and built == [] and not evals_db_path(GIG, gig.home).exists()


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--rungs", "nope", "--deterministic"], "--rungs: no rung 'nope' in the ladder (rungs: claude-sonnet, claude-haiku, gpt-5-mini, gemini-flash-lite, gemma, deterministic)"),
        (["--rungs", "", "--deterministic"], "--rungs needs comma-separated rung names"),
        (["--rungs", "gemma,,deterministic", "--deterministic"], "--rungs needs comma-separated rung names"),
        (["--rungs", "gemma,gemma", "--deterministic"], "--rungs names a rung twice"),
    ],
)
def test_bad_rung_lists_exit_2(gig: GigContext, argv: list[str], message: str) -> None:
    assert refused(gig, *argv) == f"gigai {GIG} eval: {message}\n"
    assert not evals_db_path(GIG, gig.home).exists()


def test_bad_options_exit_2(gig: GigContext, tmp_path: Path) -> None:
    assert "--plan runs nothing; drop --report" in refused(gig, "--plan", "--report", str(tmp_path / "r.md"))
    assert refused(gig, "--deterministic", "--report", str(tmp_path / "missing" / "r.md")) == (
        f"gigai {GIG} eval: --report: folder {tmp_path / 'missing'} does not exist\n"
    )
    assert "--max-calls" in refused(gig, "--max-calls", "-1")
    assert "--tier" in refused(gig, "--tier", "huge")
    assert not evals_db_path(GIG, gig.home).exists()


def test_prereg_and_manifest_cap_problems_exit_2(gig: GigContext) -> None:
    shipped = base.ladder_sha256()
    other = "0" * 64
    assert refused(context(gig.home, with_gate(prereg={"registered_at": "2026-11-09", "ladder_sha256": other})), "--deterministic") == (
        f"gigai {GIG} eval: the gig registered ladder sha256 {other}; the shipped ladder is {shipped}\n"
    )
    for bad in ({"registered_at": "2026-11-09", "ladder_sha256": "abc"}, {"ladder_sha256": shipped}, "pinned"):
        assert refused(context(gig.home, with_gate(prereg=bad)), "--deterministic") == (
            f"gigai {GIG} eval: a release gate's prereg needs registered_at and a 64-hex ladder_sha256\n"
        )
    for bad in ("ten", -1, True):
        assert refused(context(gig.home, with_gate(max_calls=bad)), "--deterministic").endswith("max_calls must be an integer >= 0\n")
    assert not evals_db_path(GIG, gig.home).exists()


def test_deterministic_needs_the_committed_ci_slices(gig: GigContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ds, "CI_DIR", tmp_path / "no-checkout")
    assert "only in a gigai source checkout" in refused(gig, "--deterministic")
    assert not evals_db_path(GIG, gig.home).exists()


SHIPPED = base.LADDER_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (None, "[[rungs\n", "ladder.toml: cannot read it"),
        ('registered_at = "2026-11-09"', "", "ladder.toml: registered_at must be a non-empty string"),
        ("price_in = 1.00", "", "ladder.toml: rungs[1].price_in must be a finite number"),
        ('adapter = "codex_cli"', 'adapter = "telepathy"', "ladder.toml: rungs[2].adapter"),
        ("price_in = 1.00", "price_in = -1.0", "ladder.toml: rungs[1].price_in must lie in"),
        ("price_in = 1.00", "price_in = nan", "ladder.toml: rungs[1].price_in must be a finite number"),
        ("price_out = 5.00", "price_out = inf", "ladder.toml: rungs[1].price_out must be a finite number"),
        ("rank = 2\n", "rank = true\n", "ladder.toml: rungs[1].rank"),
        ('name = "gemma"', 'name = "claude-haiku"', "ladder.toml: rung names must be unique"),
        ('name = "gemma"', 'name = "Gemma!"', "ladder.toml: rungs[4].name"),
        ("[tiers.gate]\n", "[tiers.gate]\nmmlu_pro = 10\n", "ladder.toml: tiers.gate.mmlu_pro has no [sets.mmlu_pro] anchors"),
        ("[noise]\n", "[noise]\nk = 1\n", None),
        ("max_p = 0.01", "max_p = 1.5", "ladder.toml: canaries.max_p must lie in"),
        ("keep = 0.25", "keep = 1.0", "ladder.toml: canaries.truncated_context.keep must lie in"),
        ("max_abs_delta = 0.0005", "max_abs_delta = 0.0", "ladder.toml: replay.max_abs_delta must lie in"),
        ('rung = "claude-haiku"\nitems', 'rung = "claude-opus"\nitems', "ladder.toml: noise.rung names no rung of the ladder"),
        ('rung = "gemini-flash-lite"', 'rung = "nope"', "ladder.toml: canaries.rung names no rung of the ladder"),
        ("keep = 0.25\nitems = 100", "keep = 0.25\nitems = 0", "ladder.toml: canaries.truncated_context.items must be an integer >= 1"),
        ('sets = ["ifeval", "quality"]\nitems = 100', 'sets = ["ifeval", "quality"]\nitems = true', "ladder.toml: canaries.broken_prompt.items must be an integer >= 1"),
        ('sets = ["ifeval", "quality"]\nitems = 100', 'sets = ["ifeval", "quality"]\nitems = 2.5', "ladder.toml: canaries.broken_prompt.items must be an integer >= 1"),
    ],
)
def test_a_malformed_ladder_exits_2_naming_the_key(
    gig: GigContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old: str | None, new: str, message: str | None
) -> None:
    if old is None:
        text = SHIPPED + new
    elif old == "[noise]\n":
        assert "\nk = 3\n" in SHIPPED
        text = SHIPPED.replace("\nk = 3\n", "\nk = 1\n", 1)
    else:
        assert old in SHIPPED
        text = SHIPPED.replace(old, new, 1)
    path = tmp_path / "ladder.toml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(base, "LADDER_PATH", path)
    stderr = refused(gig, "--deterministic", "--plan")
    assert stderr.startswith(f"gigai {GIG} eval: {message or 'ladder.toml: noise.k must be an integer >= 2'}")
    assert not evals_db_path(GIG, gig.home).exists()


def test_a_changed_ladder_no_longer_matches_the_gig_pin(gig: GigContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ladder.toml"
    path.write_text(SHIPPED.replace("min_drop = 10", "min_drop = 9", 1), encoding="utf-8")
    monkeypatch.setattr(base, "LADDER_PATH", path)
    assert "the gig registered ladder sha256" in refused(gig, "--deterministic", "--plan")


# --- the deterministic run ---------------------------------------------------------------------------------------


def test_the_deterministic_gate_run_records_every_gate_with_no_call(gig: GigContext, built: list[str], tmp_path: Path) -> None:
    report = deterministic_run(gig, "--max-calls", "0", "--report", str(tmp_path / "base.md"))
    assert (report["source"], report["tier"], report["ladder_sha256"], report["calls_made"]) == ("ci", "gate", base.ladder_sha256(), 0)
    assert len(report["runs"]) == 6 * 6
    for row in report["runs"]:
        assert (row["items"], row["scored"], row["score"], row["calls_made"]) == (CI_ITEMS[row["set"]], CI_ITEMS[row["set"]], SNAPSHOT[row["set"]], 0)
    assert [s["set"] for s in report["sets"] if s["saturated"]] == ["contractnli", "ragtruth", "quality", "rewardbench2"]
    gates = report["gates"]
    assert (gates["a"]["tau"], gates["a"]["status"]) == (None, "not_validated")
    assert gates["a"]["excluded_sets"] == ["contractnli", "quality", "ragtruth", "rewardbench2"]
    assert (gates["b"]["sd"], gates["b"]["flip_rate"], gates["b"]["n"], gates["b"]["status"]) == (0.0, 0.0, 20, "pass")
    assert [c["canary"] for c in gates["c"]] == list(base.CANARIES)
    assert [(c["drop"], c["status"]) for c in gates["c"]] == [(80.0, "pass"), (100.0, "pass"), (100.0, "pass")]
    assert all(c["p"] < 0.01 for c in gates["c"])
    assert gates["d"]["status"] == "not_validated"
    assert (gates["e"]["max_abs_delta"], gates["e"]["calls_made"], gates["e"]["traces"], gates["e"]["status"]) == (0.0, 0, 10, "pass")
    assert built == []
    # 36 main runs, the noise run, two canary runs that call and the replay run; every recorded call count is 0.
    assert rows(gig.home, "SELECT count(*), sum(calls_made) FROM runs") == [(40, 0)]
    assert rows(gig.home, "SELECT count(*) FROM traces WHERE model_id IS NOT NULL") == [(0,)]
    markdown = (tmp_path / "base.md").read_text(encoding="utf-8")
    assert "| e replay | max abs delta 0.000000, 0 calls | pass |" in markdown
    assert "\n| ifeval (approximate IFEval: 15 of 25 instruction types; others unscored; IFBench: not available) | " in markdown
    output = json.dumps(report) + markdown
    for set_name in GATE_SETS:
        for item in ci_items(set_name)[:3]:
            assert item.prompt[:40] not in output


def test_the_text_report_lists_rungs_sets_and_gates(gig: GigContext) -> None:
    result = invoke(gig, "--deterministic")
    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"base run: gate tier on the ci slices, ladder {base.ladder_sha256()[:12]}"
    assert "  claude-haiku: rank 2, ran, 0 calls, suite 45.00" in lines
    assert "  a order: tau-b n/a (min 0.6): not_validated (every measured rung scored the same, so tau-b is undefined)" in lines
    assert lines[-1] == "calls made: 0"


@pytest.mark.parametrize(("tamper", "items", "status"), [(0.01, 10, "fail"), (0.004, 1, "pass"), (0.006, 1, "fail")])
def test_replay_compares_stored_scores_with_a_rescore_of_copied_traces(gig: GigContext, tamper: float, items: int, status: str) -> None:
    report = deterministic_run(gig, "--rungs", "deterministic,gemma,claude-haiku")
    source = next(row["run_id"] for row in report["runs"] if row["set"] == "contractnli" and row["rung"] == "gemma")
    connection = open_evals_db(GIG, gig.home)
    try:
        traces = [trace for (trace,) in connection.execute("SELECT trace_id FROM traces WHERE run_id = ? ORDER BY trace_id", (source,))]
        with connection:
            connection.executemany(
                "UPDATE scores SET value = value - ? WHERE trace_id = ? AND scorer_id = 'base:contractnli'", [(tamper, t) for t in traces[:items]]
            )
        run = base._Context(gig, base.load_ladder(), connection, True, "ci", base.CallCap(0), items={"contractnli": ci_items("contractnli")})
        record = run.run_record("contractnli", "base:gemma:replay", replay_of=source)
        verdict = base.replay_gate(
            connection, source, {i.id: i for i in run.items["contractnli"]}, "contractnli", max_abs_delta=0.0005, run=record, cap=base.CallCap(0)
        )
    finally:
        connection.close()
    assert verdict["max_abs_delta"] == pytest.approx(tamper * items / 10, abs=1e-12)
    assert (verdict["traces"], verdict["calls_made"], verdict["status"]) == (10, 0, status)


def test_replay_fails_on_any_call_and_at_the_line() -> None:
    assert base.replay_verdict([1.0, 0.0], [1.0, 0.0], 0, max_abs_delta=0.0005) == {"max_abs_delta": 0.0, "status": "pass"}
    called = base.replay_verdict([1.0, 0.0], [1.0, 0.0], 1, max_abs_delta=0.0005)
    assert called == {"max_abs_delta": 0.0, "status": "fail", "reason": "the replay made 1 call(s)"}
    assert base.replay_verdict([1.0], [0.5], 0, max_abs_delta=0.5)["status"] == "fail"
    assert base.replay_verdict([1.0], [0.5], 0, max_abs_delta=0.5000001)["status"] == "pass"
    # A small move either way counts by its size.
    assert base.replay_verdict([0.5], [0.5004], 0, max_abs_delta=0.0005)["status"] == "pass"
    assert base.replay_verdict([0.5004], [0.5], 0, max_abs_delta=0.0005)["status"] == "pass"
    assert base.replay_verdict([0.5], [0.5006], 0, max_abs_delta=0.0005)["status"] == "fail"
    assert base.replay_verdict([0.5006], [0.5], 0, max_abs_delta=0.0005)["max_abs_delta"] == pytest.approx(0.0006, abs=1e-12)
    assert base.replay_verdict([], [1.0], 0, max_abs_delta=0.5)["status"] == "not_validated"
    assert base.replay_verdict([1.0], [], 0, max_abs_delta=0.5)["status"] == "not_validated"
    for bad in (math.nan, math.inf):
        with pytest.raises(NonFiniteScoreError):
            base.replay_verdict([1.0], [bad], 0, max_abs_delta=0.5)


def test_a_replay_that_calls_records_the_calls_and_fails(gig: GigContext, monkeypatch: pytest.MonkeyPatch) -> None:
    report = deterministic_run(gig, "--rungs", "deterministic,gemma,claude-haiku")
    source = next(row["run_id"] for row in report["runs"] if row["set"] == "contractnli" and row["rung"] == "gemma")
    cap = base.CallCap(None)
    copy_traces = base.records.copy_traces

    def calling(*args: Any, **kwargs: Any) -> Any:
        cap.call(lambda: None)
        return copy_traces(*args, **kwargs)

    monkeypatch.setattr(base.records, "copy_traces", calling)
    connection = open_evals_db(GIG, gig.home)
    try:
        run = base._Context(gig, base.load_ladder(), connection, True, "ci", cap, items={"contractnli": ci_items("contractnli")})
        record = run.run_record("contractnli", "base:gemma:replay", replay_of=source)
        verdict = base.replay_gate(connection, source, {i.id: i for i in run.items["contractnli"]}, "contractnli", max_abs_delta=0.0005, run=record, cap=cap)
        stored = connection.execute("SELECT calls_made FROM runs WHERE run_id = ?", (record.run_id,)).fetchone()[0]
    finally:
        connection.close()
    assert (verdict["max_abs_delta"], verdict["calls_made"], stored, verdict["status"]) == (0.0, 1, 1, "fail")


def test_the_cli_mounts_base_without_importing_it(tmp_path: Path) -> None:
    make_home(tmp_path / "home")
    site = tmp_path / "site"
    _add_fixture(site, GIG)
    env = {"GIGAI_HOME": str(tmp_path / "home")}
    done = _run(tmp_path, site, GIG, "eval", "base", "--deterministic", "--json", env_extra=env)
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert set(report["gates"]) == set("abcde") and report["calls_made"] == 0
    capped = _run(tmp_path, site, GIG, "eval", "base", "--rungs", "claude-haiku", "--max-calls", "0", env_extra=env)
    assert (capped.returncode, capped.stderr.splitlines()[0]) == (2, f"gigai {GIG} eval: cap 0 reached before call 1 (the plan needs {HAIKU_GATE_CALLS} model calls; see --plan)")
    probe = subprocess.run(
        [sys.executable, "-c", "import sys, gigai.cli; print('gigai.evals.base' in sys.modules)"], capture_output=True, text=True, check=True
    )
    assert probe.stdout.strip() == "False"


# --- the call path ------------------------------------------------------------------------------------------------


def test_unreachable_names_the_missing_target_other_model_binary_or_key(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(home)
    ladder = base.load_ladder()
    assert base._unreachable(ladder.rung("claude-haiku"), config) == "no model target 'claude-haiku' in config.toml"
    assert base._unreachable(replace(ladder.rung("gpt-5-mini"), target="deterministic-a"), config) == "model target 'deterministic-a' is not a codex_cli target"
    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    # The claude-cli target runs sonnet: the haiku rung is refused on the model before the binary is looked for.
    assert base._unreachable(replace(ladder.rung("claude-haiku"), target="claude-cli"), config) == (
        "model target 'claude-cli' runs 'sonnet'; the ladder pins 'haiku'"
    )
    assert base._unreachable(replace(ladder.rung("claude-sonnet"), target="claude-cli"), config) == "no claude executable on PATH"
    assert base._unreachable(replace(ladder.rung("gemini-flash-lite"), target="openrouter:fake"), config) == (
        "model target 'openrouter:fake' runs 'fake/model-1'; the ladder pins 'google/gemini-2.5-flash-lite'"
    )
    router = replace(ladder.rung("gemini-flash-lite"), target="openrouter:fake", model="fake/model-1")
    monkeypatch.delenv("BK2_FAKE_OPENROUTER_KEY", raising=False)
    assert base._unreachable(router, config) == "no key in $BK2_FAKE_OPENROUTER_KEY"
    monkeypatch.setenv("BK2_FAKE_OPENROUTER_KEY", "sk-or-v1-bk2-fake-key-do-not-use-0000")
    assert base._unreachable(router, config) is None


def test_the_ollama_rung_needs_a_loopback_server_that_has_pulled_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    gemma = base.load_ladder().rung("gemma")
    probed: list[str] = []
    answer: list[Any] = [None]

    def fake(url: str) -> list[str] | None:
        probed.append(url)
        return answer[0]

    def config(base_url: str | None) -> Any:
        return SimpleNamespace(
            model_targets=[SimpleNamespace(name=gemma.target, endpoint="local", model=gemma.model)],
            endpoints=[SimpleNamespace(name="local", adapter="ollama_local", credential=None, base_url=base_url)],
            credentials=[],
        )

    monkeypatch.setattr(base, "_ollama_models", fake)
    monkeypatch.setattr(base.shutil, "which", lambda name: f"/bin/{name}")
    for url in (None, "http://localhost:11434", "http://10.0.0.5:11434", "http://127.0.0.1"):
        assert base._unreachable(gemma, config(url)) == "endpoint 'local' is not a loopback http://127.0.0.1:<port> URL"
    assert probed == []
    assert base._unreachable(gemma, config("http://127.0.0.1:11434")) == "no Ollama server answers at http://127.0.0.1:11434"
    answer[0] = ["gemma3:latest", "gemma3:12b", "gemma3"]
    assert base._unreachable(gemma, config("http://127.0.0.1:11434/")) == "the Ollama server at http://127.0.0.1:11434 has not pulled 'gemma3:4b'"
    answer[0] = ["llama3:8b", "gemma3:4b"]
    assert base._unreachable(gemma, config("http://127.0.0.1:11434")) is None
    assert probed == ["http://127.0.0.1:11434"] * 3
    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    assert base._unreachable(gemma, config("http://127.0.0.1:11434")) == "no ollama executable on PATH"
    assert len(probed) == 3


def test_the_ollama_probe_reads_api_tags_through_no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    opened: list[tuple[str, float]] = []
    body: list[Any] = [b'{"models": [{"name": "gemma3:4b"}, {"name": 7}, "x", {"name": "llama3:8b"}]}']

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 1_048_576
            if isinstance(body[0], Exception):
                raise body[0]
            return body[0]

    class Opener:
        def open(self, url: str, timeout: float) -> Response:
            opened.append((url, timeout))
            return Response()

    handlers: list[Any] = []

    def build_opener(*given: Any) -> Opener:
        handlers.extend(given)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    assert base._ollama_models("http://127.0.0.1:11434") == ["gemma3:4b", "llama3:8b"]
    assert opened == [("http://127.0.0.1:11434/api/tags", 2)]
    assert [type(handler) for handler in handlers] == [urllib.request.ProxyHandler] and handlers[0].proxies == {}
    for bad in (b"not json", b'{"models": "none"}', b"[]", OSError("refused")):
        body[0] = bad
        assert base._ollama_models("http://127.0.0.1:11434") is None


def test_canned_answers_are_keyed_by_item_id_and_only_for_the_items_own_prompt(gig: GigContext) -> None:
    first, second = ci_items("quality")[:2]
    twin = replace(second, prompt=first.prompt)
    run = base._Context(gig, base.load_ladder(), None, True, "ci", base.CallCap(0))
    run.canned = {"quality": {item.id: (item.prompt, f"answer {n}") for n, item in enumerate((first, twin))}}
    rung = run.ladder.rung("claude-haiku")
    assert base._Context.answer(run, rung, "quality", first, first.prompt) == ("answer 0", None)
    assert base._Context.answer(run, rung, "quality", twin, first.prompt) == ("answer 1", None)
    assert base._Context.answer(run, rung, "quality", first, base.broken_prompt(first.prompt, first.id)) == ("", None)
    assert base._Context.answer(run, rung, "quality", replace(first, id="unknown"), first.prompt) == ("", None)
    assert run.cap.made == 0


def answer_live(gig: GigContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """Release slices from the ci slices; every live rung stands on ``deterministic-a``, answering with the ci completions."""

    datasets = gig.home / "gigs" / GIG / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    canned: dict[str, str] = {}
    for set_name in GATE_SETS:
        shutil.copyfile(Path(ds.CI_DIR) / f"{set_name}.jsonl", datasets / f"{set_name}-release.jsonl")
        shutil.copyfile(Path(ds.CI_DIR) / f"{set_name}.sha256", datasets / f"{set_name}-release.sha256")
        canned.update({item.prompt: item.completion or "" for item in ci_items(set_name)})
    monkeypatch.setattr(ds, "download_release", lambda *a, **k: pytest.fail("no download"))
    monkeypatch.setattr(base, "_unreachable", lambda rung, config: None)
    resolve = factory.resolve_model_adapter
    monkeypatch.setattr(factory, "resolve_model_adapter", lambda config, target, **kw: resolve(config, "deterministic-a", **kw))
    counted = DeterministicAdapter.invoke

    def answering(self: DeterministicAdapter, request: Any) -> Any:
        self._responses = {**self._responses, request.prompt: canned.get(request.prompt) or "no answer"}
        return counted(self, request)

    monkeypatch.setattr(DeterministicAdapter, "invoke", answering)


def test_a_live_rung_calls_once_per_item_noise_repeat_and_canary(gig: GigContext, built: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """claude-haiku stands on the deterministic adapter (``deterministic-a``), answering with the ci completions."""

    answer_live(gig, monkeypatch)
    result = invoke(gig, "--rungs", "claude-haiku,deterministic", "--max-calls", str(HAIKU_GATE_CALLS), "--json")
    assert result.exit_code == 0, result.stderr
    report = json.loads(result.stdout)
    main, noise, canaries = sum(CI_ITEMS.values()), 2 * sum(CI_ITEMS.values()), (10 + 10) + (10 + 20 + 10)
    assert report["source"] == "release" and report["calls_made"] == main + noise + canaries == 270
    by_rung = {r["name"]: (r["status"], r["calls_made"], r["main_calls"]) for r in report["rungs"]}
    assert by_rung == {"claude-haiku": ("ran", main + noise + canaries, main), "deterministic": ("ran", 0, 0)}
    assert built.count("deterministic-a") == main + noise + canaries
    assert built.count("invoke:deterministic-a") == main + noise + canaries
    assert report["gates"]["b"]["rung"] == "claude-haiku" and report["gates"]["b"]["k"] == 3
    assert report["gates"]["b"]["repeat_0"] == "the rung's main runs"
    noise_run = report["gates"]["b"]["run_id"]
    assert rows(gig.home, "SELECT repeat_index, count(*) FROM traces WHERE run_id = ? GROUP BY repeat_index ORDER BY repeat_index", noise_run) == [(1, 70), (2, 70)]
    assert [c["rung"] for c in report["gates"]["c"]] == ["claude-haiku"] * 3
    assert rows(gig.home, "SELECT sum(calls_made) FROM runs") == [(main + noise + canaries,)]
    called = "SELECT count(*) FROM traces WHERE model_id IS NOT NULL AND run_id NOT IN (SELECT run_id FROM runs WHERE replay_of IS NOT NULL)"
    assert rows(gig.home, called) == [(main + noise + canaries,)]
    # The replay copies the first main run's traces (claude-haiku on contractnli) with their call metadata, calling nothing.
    assert report["gates"]["e"]["traces"] == 10 and report["gates"]["e"]["calls_made"] == 0


def test_a_calling_canary_asks_only_its_items_when_the_slice_holds_more(gig: GigContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ladder.toml"
    path.write_text(
        SHIPPED.replace('sets = ["ifeval", "quality"]\nitems = 100\n', 'sets = ["ifeval", "quality"]\nitems = 4\n').replace(
            "keep = 0.25\nitems = 100\n", "keep = 0.25\nitems = 5\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(base, "LADDER_PATH", path)
    pinned = context(gig.home, with_gate(prereg={"registered_at": "2026-11-09", "ladder_sha256": base.ladder_sha256(path)}))
    answer_live(pinned, monkeypatch)
    result = invoke(pinned, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS), "--json")
    assert result.exit_code == 0, result.stderr
    report = json.loads(result.stdout)
    main, noise = sum(CI_ITEMS.values()), 2 * sum(CI_ITEMS.values())
    # broken_prompt: 4 items round robin over ifeval, quality; truncated_context: 5 over quality, ragtruth, contractnli.
    calling = [c for c in report["gates"]["c"] if c["canary"] in base.CALLING_CANARIES]
    asked = {c["canary"]: rows(gig.home, "SELECT calls_made FROM runs WHERE run_id = ?", c["run_id"])[0][0] for c in calling}
    assert asked == {"broken_prompt": 4, "truncated_context": 5}
    assert [(c["canary"], c["items"]) for c in calling] == [("broken_prompt", 4), ("truncated_context", 5)]
    for entry in calling:
        assert rows(gig.home, "SELECT count(*) FROM traces WHERE run_id = ?", entry["run_id"]) == [(asked[entry["canary"]],)]
    assert report["calls_made"] == main + noise + 4 + 5


def test_a_failed_call_exits_1_naming_the_input_sha_only(gig: GigContext, monkeypatch: pytest.MonkeyPatch) -> None:
    datasets = gig.home / "gigs" / GIG / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    for set_name in GATE_SETS:
        shutil.copyfile(Path(ds.CI_DIR) / f"{set_name}.jsonl", datasets / f"{set_name}-release.jsonl")
        shutil.copyfile(Path(ds.CI_DIR) / f"{set_name}.sha256", datasets / f"{set_name}-release.sha256")
    monkeypatch.setattr(base, "_unreachable", lambda rung, config: None)
    resolve = factory.resolve_model_adapter
    monkeypatch.setattr(factory, "resolve_model_adapter", lambda config, target, **kw: resolve(config, "deterministic-a", **kw))
    result = invoke(gig, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS))
    assert result.exit_code == 1
    first = ci_items("contractnli")[0]
    assert result.stderr.startswith(f"gigai {GIG} eval: rung claude-haiku: call 1 (input sha256 ")
    assert re.search(r"failed: [A-Za-z]+Error\n$", result.stderr), result.stderr
    assert first.prompt[:40] not in result.stderr


# --- failed calls -----------------------------------------------------------------------------------------------


def server_error() -> OllamaLocalAdapterError:
    return OllamaLocalAdapterError("local Ollama endpoint returned HTTP 500 for POST /api/chat")


def http_error(status: int) -> ModelInvocationError:
    """What the HTTP adapters raise for an HTTP error status: a plain ModelInvocationError caused by httpx's error."""

    request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    error = ModelInvocationError("model HTTP invocation failed: HTTPStatusError")
    error.__cause__ = httpx.HTTPStatusError("refused", request=request, response=httpx.Response(status, request=request))
    return error


def failing(monkeypatch: pytest.MonkeyPatch, fail: Callable[[int, Any], BaseException | None]) -> list[Any]:
    """Wrap ``answer_live``'s invoke: call ``n`` (from 1) raises what ``fail(n, request)`` returns; returns every request."""

    seen: list[Any] = []
    answering = DeterministicAdapter.invoke

    def invoked(self: DeterministicAdapter, request: Any) -> Any:
        seen.append(request)
        error = fail(len(seen), request)
        if error is not None:
            raise error
        return answering(self, request)

    monkeypatch.setattr(DeterministicAdapter, "invoke", invoked)
    return seen


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (server_error(), "item"),
        (OllamaLocalResponseError("local Ollama response has no answer"), "item"),
        (ModelInvocationError("claude exited with status 1"), "item"),
        (http_error(500), "item"),
        (http_error(429), "item"),
        (BoundedCallError("timed_out", "slow"), "item"),
        (BoundedCallError("failed", "no result"), "item"),
        (ModelOutputNotJSONError("not json"), "answer"),
        (ModelAuthenticationRequired("log in"), "run"),
        (CapabilityMismatchError("no tools"), "run"),
        (UnsupportedRequestFieldError("ollama_local", ["seed"]), "run"),
        (ModelInvocationCancelled("stopped"), "run"),
        (OllamaLocalIdentityError("digest changed"), "run"),
        (http_error(401), "run"),
        (http_error(402), "run"),
        (http_error(403), "run"),
        (BoundedCallError("cancelled", "stopped"), "run"),
        (BoundedCallError("budget_exhausted", "zero"), "run"),
        (ValueError("boom"), "run"),
        (KeyError("boom"), "run"),
    ],
)
def test_failure_kind_sorts_one_item_failures_from_run_failures(error: BaseException, kind: str) -> None:
    assert base.failure_kind(error, schema_mode=False) == kind
    assert base.failure_kind(error, schema_mode=True) == kind


@pytest.mark.parametrize(
    ("error", "schema_mode", "kind"),
    [
        (http_error(400), True, "answer"),
        (http_error(400), False, "item"),
        (OllamaLocalAdapterError("local Ollama endpoint returned HTTP 400 for POST /api/chat"), True, "answer"),
        (OllamaLocalAdapterError("local Ollama endpoint returned HTTP 400 for POST /api/chat"), False, "item"),
    ],
)
def test_an_http_400_to_a_schema_call_is_a_wrong_answer_and_otherwise_an_item_failure(error: BaseException, schema_mode: bool, kind: str) -> None:
    assert base.failure_kind(error, schema_mode=schema_mode) == kind


def test_http_status_comes_from_the_cause_or_an_ollama_message_only() -> None:
    assert base.http_status(server_error()) == 500
    assert base.http_status(http_error(402)) == 402
    assert base.http_status(ModelInvocationError("returned HTTP 500")) is None
    assert base.http_status(ValueError("boom")) is None


def test_one_failed_item_call_is_recorded_unscored_and_the_run_completes(gig: GigContext, monkeypatch: pytest.MonkeyPatch) -> None:
    answer_live(gig, monkeypatch)
    first = ci_items("contractnli")[0]
    # Only the first main call for contractnli's first item fails (its noise repeats ask the same prompt again).
    first_ask = lambda n, request: request.prompt == first.prompt and [r.prompt for r in seen].count(first.prompt) == 1  # noqa: E731
    seen = failing(monkeypatch, lambda n, request: server_error() if first_ask(n, request) else None)
    result = invoke(gig, "--rungs", "claude-haiku,deterministic", "--max-calls", str(HAIKU_GATE_CALLS), "--json")
    assert result.exit_code == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["calls_made"] == 270
    by_rung = {r["name"]: (r["calls_made"], r["failed_calls"], r["failures"]) for r in report["rungs"]}
    assert by_rung == {"claude-haiku": (270, 1, {"OllamaLocalAdapterError": 1}), "deterministic": (0, 0, {})}
    called = "AND run_id NOT IN (SELECT run_id FROM runs WHERE replay_of IS NOT NULL)"  # gate e's replay copies it
    failed = rows(gig.home, f"SELECT trace_id, item_id, output_raw_text, parse_status, error, model_id FROM traces WHERE outcome = 'failed' {called}")
    error = {"code": "OllamaLocalAdapterError", "message": "OllamaLocalAdapterError (HTTP 500)", "retryable": False}
    assert [(item, text, status, json.loads(stored), model) for _, item, text, status, stored, model in failed] == [(first.id, "", "not_parsed", error, None)]
    assert rows(gig.home, "SELECT count(*) FROM scores WHERE trace_id = ?", failed[0][0]) == [(0,)]
    contract = next(r for r in report["runs"] if (r["rung"], r["set"]) == ("claude-haiku", "contractnli"))
    assert (contract["items"], contract["scored"]) == (10, 9)
    # gate e replays the contractnli run: the failed trace is copied and not re-scored, so the mean still agrees.
    assert report["gates"]["e"]["traces"] == 10 and report["gates"]["e"]["status"] == "pass"
    markdown = base.render_markdown(report)
    assert "| ran | 270 (1 failed: OllamaLocalAdapterError 1) |" in markdown
    assert "| ran | 0 |" in markdown
    assert "270 calls (1 failed: OllamaLocalAdapterError 1), suite" in "\n".join(base.report_lines(report))


def test_a_run_with_no_failed_call_shows_none(gig: GigContext) -> None:
    report = deterministic_run(gig)
    assert {(r["failed_calls"], json.dumps(r["failures"])) for r in report["rungs"]} == {(0, "{}")}
    assert " failed: " not in base.render_markdown(report)
    assert " failed: " not in "\n".join(base.report_lines(report))


def test_failed_calls_past_the_limit_abort_naming_rung_count_and_limit(gig: GigContext, monkeypatch: pytest.MonkeyPatch) -> None:
    answer_live(gig, monkeypatch)
    failing(monkeypatch, lambda n, request: server_error() if n % 2 else None)
    result = invoke(gig, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS))
    assert result.exit_code == 1
    # 10% of the 960 planned calls is 96: the 97th failure (call 193) aborts.
    assert result.stderr == f"gigai {GIG} eval: rung claude-haiku: 97 of {HAIKU_GATE_CALLS} calls failed (limit 10%); last error OllamaLocalAdapterError\n"
    assert rows(gig.home, "SELECT outcome, count(*) FROM traces GROUP BY outcome ORDER BY outcome") == [("failed", 97), ("succeeded", 96)]
    assert rows(gig.home, "SELECT count(DISTINCT run_id) FROM traces")[0][0] >= 6


def test_failed_calls_exactly_at_the_limit_do_not_abort(gig: GigContext, monkeypatch: pytest.MonkeyPatch) -> None:
    answer_live(gig, monkeypatch)
    failing(monkeypatch, lambda n, request: server_error() if n % 2 and n <= 191 else None)
    result = invoke(gig, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS), "--json")
    assert result.exit_code == 0, result.stderr
    (haiku,) = json.loads(result.stdout)["rungs"]
    assert (haiku["calls_made"], haiku["failed_calls"], haiku["failures"]) == (270, 96, {"OllamaLocalAdapterError": 96})


@pytest.mark.parametrize(
    "error",
    [ModelAuthenticationRequired("log in"), OllamaLocalIdentityError("digest changed"), http_error(401), ValueError("boom")],
    ids=["auth", "identity", "http-401", "unknown"],
)
def test_a_run_level_failure_aborts_at_its_first_occurrence(gig: GigContext, monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    answer_live(gig, monkeypatch)
    failing(monkeypatch, lambda n, request: error if n == 3 else None)
    result = invoke(gig, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS))
    assert result.exit_code == 1
    name = type(error).__name__
    assert re.fullmatch(rf"gigai {GIG} eval: rung claude-haiku: call 3 \(input sha256 [0-9a-f]{{12}}\) failed: {name}\n", result.stderr), result.stderr
    assert rows(gig.home, "SELECT outcome, count(*) FROM traces GROUP BY outcome") == [("succeeded", 2)]


def test_failed_noise_repeat_and_canary_calls_are_counted_and_unscored(gig: GigContext, monkeypatch: pytest.MonkeyPatch) -> None:
    answer_live(gig, monkeypatch)
    main, noise = sum(CI_ITEMS.values()), 2 * sum(CI_ITEMS.values())
    # Calls run main answers, then noise repeats, then the calling canaries: fail the first of each of the last two.
    failing(monkeypatch, lambda n, request: server_error() if n in (main + 1, main + noise + 1) else None)
    result = invoke(gig, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS), "--json")
    assert result.exit_code == 0, result.stderr
    report = json.loads(result.stdout)
    (haiku,) = report["rungs"]
    assert (haiku["calls_made"], haiku["failed_calls"], haiku["failures"]) == (270, 2, {"OllamaLocalAdapterError": 2})
    noise_run = report["gates"]["b"]["run_id"]
    canary_runs = {c["run_id"] for c in report["gates"]["c"] if c["canary"] in base.CALLING_CANARIES}
    failed = rows(gig.home, "SELECT run_id, repeat_index FROM traces WHERE outcome = 'failed'")
    where = sorted("noise" if (run, repeat > 0) == (noise_run, True) else "canary" if run in canary_runs else run for run, repeat in failed)
    assert where == ["canary", "noise"]
    assert rows(gig.home, "SELECT count(*) FROM traces t JOIN scores s ON s.trace_id = t.trace_id WHERE t.outcome = 'failed'") == [(0,)]


def test_a_schema_tagged_item_is_asked_in_schema_mode_with_its_schema(gig: GigContext, monkeypatch: pytest.MonkeyPatch) -> None:
    answer_live(gig, monkeypatch)
    seen = failing(monkeypatch, lambda n, request: None)
    result = invoke(gig, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS), "--json")
    assert result.exit_code == 0, result.stderr
    assert all("output_mode:schema" in item.tags for item in ci_items("jsonschemabench"))
    # The ci jsonschemabench items share one prompt; only the schema each request carries tells them apart.
    schemas = [item.expected["schema"] for item in ci_items("jsonschemabench")]
    (prompt,) = {item.prompt for item in ci_items("jsonschemabench")}
    plain = {item.prompt for item in ci_items("contractnli")}
    asked = [r for r in seen if r.prompt == prompt]
    other = [r for r in seen if r.prompt in plain]
    assert all(r.output_mode == "schema" and r.schema in schemas for r in asked)
    assert all(any(r.schema == schema for r in asked) for schema in schemas)
    # The first item is a noise item too: its main answer and both noise repeats carry its schema.
    assert sum(r.schema == schemas[0] for r in asked) == 3
    assert len(other) > len(plain) and all(r.output_mode is None and r.schema is None for r in other)


@pytest.mark.parametrize("error", [ModelOutputNotJSONError("not json"), http_error(400)], ids=["not-json", "http-400"])
def test_a_non_json_reply_to_a_schema_request_is_a_wrong_answer_not_a_failed_call(gig: GigContext, monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    answer_live(gig, monkeypatch)
    first = ci_items("jsonschemabench")[0]
    failing(monkeypatch, lambda n, request: error if request.prompt == first.prompt else None)
    result = invoke(gig, "--rungs", "claude-haiku", "--max-calls", str(HAIKU_GATE_CALLS), "--json")
    assert result.exit_code == 0, result.stderr
    (haiku,) = json.loads(result.stdout)["rungs"]
    assert (haiku["calls_made"], haiku["failed_calls"], haiku["failures"]) == (270, 0, {})
    answered = "SELECT t.output_raw_text, t.parse_status, t.outcome, s.value FROM traces t LEFT JOIN scores s ON s.trace_id = t.trace_id WHERE t.input = ?"
    stored = rows(gig.home, answered, first.prompt)
    assert stored and set(stored) == {("", "not_json", "succeeded", 0.0)}
