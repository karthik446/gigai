"""``gigai <gig> eval base``: run the shared eval sets on a ladder of models and check the measurement itself.

The ladder (``ladder.toml`` v1 or ``ladder-v2.toml`` v2, data) names six rungs from the strongest model to the
deterministic floor, each with its adapter, model, price and published rank. A gig's release-gate ``prereg`` pins
one shipped ladder by its SHA-256; a gig with no pin runs the newest. The ladder's ``[rules]`` say which rungs the
saturation test counts, which sets the noise items come from and which rungs gate a ranks (v1: all of them; v2:
the model rungs and the gated sets). A base run answers every set of a tier (``gate``: the 560-item per-release
suite) on each rung, records one ``runs`` row per rung and set in ``evals.sqlite``, and then checks five
validation gates:

- a: the measured order of the rungs agrees with the published order (Kendall tau-b at least 0.6).
- b: the noise floor: one rung answers the same items three times; the suite score moves at most 2 points and at
  most 10% of items flip between right and wrong. A noise rung that can fix neither a seed nor a temperature repeats
  differently by design, so for it gate b is ``measured``: the numbers and the limits are reported, not judged. The
  deterministic rung and a ``--deterministic`` run answer from canned data, so they keep pass/fail.
- c: three canaries sabotage the suite on purpose (a broken prompt, the first quarter of the context, a scorer
  with wrong labels); the score must drop by a set number of points with an exact McNemar p below 0.01.
- d: judge validation against human labels, which the base run does not check yet (``not_validated``).
- e: a replay of one run re-scores its stored traces with no call and must give the same mean. It re-scores the
  same text with the same scorer, so it catches a nondeterministic scorer or traces lost in the copy, nothing else.

Model calls (to every rung but the deterministic one) are capped by ``--max-calls`` and the gig's release-gate ``max_calls``: the plan
is checked against the cap before any slice is read or adapter built, and each call is checked again before it is
made. ``--deterministic`` answers every rung from the committed ci slices' canned completions with no call.
A call that fails for one item (the model server or provider broke that one request) is recorded as a failed trace
with no score and the run goes on; a rung aborts the run once more than ``MAX_FAILED_PERCENT`` of its planned model
calls have failed, and a missing credential, a changed model identity or an unknown error aborts it at the first
failure (see :func:`failure_kind`). A call to an item tagged ``output_mode:schema`` carries the item's JSON Schema.
Errors name rungs, sets, item ids and SHA-256s, never item text.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import random
import re
import shutil
import statistics
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ..canonical import sha256_hex
from ..gigs.home import GigNameError, check_name
from . import records, stats
from . import datasets as ds
from .run import CapReachedError, RunFailed, RunRefused, _manifest_digest, _schema_settings, dataset_digest, guard

if TYPE_CHECKING:
    from gigai.sdk import GigContext

#: The shipped ladders, oldest first: v1 (0.3.0, 0.3.1) and v2 (0.3.2, registered after the first live base run).
#: A gig's ``prereg`` pins one by its SHA-256; a gig with no pin runs the newest.
LADDER_PATHS = (Path(__file__).with_name("ladder.toml"), Path(__file__).with_name("ladder-v2.toml"))
#: The rules a ladder's optional ``[rules]`` table sets, with their allowed values; the first value is v1's
#: behaviour, which a ladder with no ``[rules]`` table runs. ``saturation``: which rungs the saturation test counts;
#: ``noise_sets``: which sets the noise items come from; ``order_rungs``: which rungs gate a ranks.
RULES = {"saturation": ("all", "model"), "noise_sets": ("all", "gated"), "order_rungs": ("all", "model")}
V1_RULES = {key: values[0] for key, values in RULES.items()}
#: Why gate a under ``order_rungs = "model"`` leaves the deterministic rung out of the order it checks.
UNRANKED_REASON = "its canned answers are the gold answer on several sets, so it is not a floor to rank"
TIERS = ("gate", "judge", "full")
CANARIES = ("broken_prompt", "truncated_context", "wrong_label")
#: The canaries that send altered prompts (wrong_label re-scores the main answers at no call).
CALLING_CANARIES = ("broken_prompt", "truncated_context")
#: Items a calling canary takes when its ladder table names no ``items``.
CANARY_ITEMS = 100
#: A rung aborts the run once more than this percent of its planned model calls (main, noise and canary) failed.
MAX_FAILED_PERCENT = 10
ADAPTERS = ("deterministic", "claude_cli", "codex_cli", "openrouter_api", "openai_api", "ollama_local")
KNOBS = ("temperature", "seed")
SCORER_VERSION = "1"
#: What the IFEval score is: the standard-library verifiers cover part of upstream's instruction types.
IFEVAL_NOTE = "approximate IFEval: 15 of 25 instruction types; others unscored"
#: The executable a CLI or local rung needs on PATH.
_BINARIES = {"claude_cli": "claude", "codex_cli": "codex", "ollama_local": "ollama"}
_CORRECT = 0.5


# --- the ladder ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rung:
    """One rung: the model it calls, what it costs and where it sits in the published order."""

    name: str
    adapter: str
    model: str
    model_name: str
    price_in: float
    price_out: float
    price_source: str
    rank: int
    rank_source: str
    rank_date: str
    knobs: tuple[str, ...]
    no_knobs: str | None
    #: The ``config.toml`` model target the rung calls (default: its name).
    target: str

    @property
    def deterministic(self) -> bool:
        return self.adapter == "deterministic"

    def describe(self) -> dict[str, Any]:
        out = {key: value for key, value in dataclasses.asdict(self).items() if key != "no_knobs"}
        out["knobs"] = list(self.knobs)
        if not self.knobs:
            out["knobs_note"] = self.no_knobs
        return out


@dataclass(frozen=True)
class Ladder:
    """A shipped ladder file, validated."""

    sha256: str
    registered_at: str
    rungs: tuple[Rung, ...]
    tiers: Mapping[str, Mapping[str, int]]
    sets: Mapping[str, Mapping[str, int]]
    noise: Mapping[str, Any]
    order: Mapping[str, Any]
    canaries: Mapping[str, Any]
    replay: Mapping[str, Any]
    #: The pre-registration version (v1 has no ``version`` key).
    version: int = 1
    #: One value per key of :data:`RULES`.
    rules: Mapping[str, str] = field(default_factory=lambda: dict(V1_RULES))

    def rung(self, name: str) -> Rung:
        return next(rung for rung in self.rungs if rung.name == name)

    def cost(self, rung: Rung, set_name: str, keep: float = 1.0) -> float:
        """Dollars of one call of ``rung`` on ``set_name`` at the set's token anchors (``keep``: share of the prompt sent)."""

        anchors = self.sets[set_name]
        return (anchors["tokens_in"] * keep * rung.price_in + anchors["tokens_out"] * rung.price_out) / 1_000_000


def ladder_sha256(path: Path | None = None) -> str:
    """The bare SHA-256 of a ladder file a gig's ``prereg`` pins (default: the newest shipped ladder)."""

    return sha256_hex(Path(path or LADDER_PATHS[-1]).read_bytes())


def shipped_ladder(pin: str | None, paths: Sequence[Path] | None = None) -> Ladder:
    """The ladder a run uses: the shipped one whose SHA-256 is the gig's ``prereg`` pin, the newest when it pins none.

    Every candidate in ``paths`` (default :data:`LADDER_PATHS`, oldest first) is loaded and checked first, so a
    malformed ladder is refused naming the key; a pin that matches none of them is refused naming every hash.
    """

    ladders = [load_ladder(path) for path in (paths or LADDER_PATHS)]
    if pin is None:
        return ladders[-1]
    for ladder in ladders:
        if ladder.sha256 == pin:
            return ladder
    shipped = ", ".join(f"v{ladder.version} {ladder.sha256}" for ladder in ladders)
    raise RunRefused(f"the gig registered ladder sha256 {pin}; it matches no shipped ladder ({shipped})")


def load_ladder(path: Path | None = None) -> Ladder:
    """A ladder file (default: the newest shipped one) read and checked; anything malformed is a :class:`RunRefused` naming the key."""

    path = Path(path or LADDER_PATHS[-1])
    try:
        raw = path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RunRefused(f"ladder.toml: cannot read it ({exc})") from exc
    check = _Checker()
    version = check.integer(data, "version", "", minimum=1) if "version" in data else 1
    registered_at = check.text(data, "registered_at", "")
    rules = dict(V1_RULES)
    if "rules" in data:
        table = check.table(data, "rules", "")
        for key in table:
            if key not in RULES:
                raise RunRefused(f"ladder.toml: rules.{key} is not a rule (the rules are {', '.join(RULES)})")
        rules = {key: check.choice(table, key, "rules", allowed) for key, allowed in RULES.items()}
    rungs_raw = data.get("rungs")
    if not isinstance(rungs_raw, list) or not rungs_raw:
        raise RunRefused("ladder.toml: rungs must be a non-empty array of tables")
    rungs = tuple(_rung(check, entry, f"rungs[{index}]") for index, entry in enumerate(rungs_raw))
    names = [rung.name for rung in rungs]
    if len(set(names)) != len(names):
        raise RunRefused("ladder.toml: rung names must be unique")
    if sum(rung.deterministic for rung in rungs) != 1:
        raise RunRefused("ladder.toml: exactly one rung must use the deterministic adapter")
    sets = check.table(data, "sets", "")
    set_anchors: dict[str, dict[str, int]] = {}
    for name, anchors in sets.items():
        where = f"sets.{name}"
        if name not in ds.SETS:
            raise RunRefused(f"ladder.toml: {where} is not one of the eval sets")
        if not isinstance(anchors, dict):
            raise RunRefused(f"ladder.toml: {where} must be a table")
        set_anchors[name] = {key: check.integer(anchors, key, where, minimum=0) for key in ("tokens_in", "tokens_out", "k_score")}
    tiers = check.table(data, "tiers", "")
    tier_counts: dict[str, dict[str, int]] = {}
    for tier in TIERS:
        counts = check.table(tiers, tier, "tiers")
        if not counts:
            raise RunRefused(f"ladder.toml: tiers.{tier} names no set")
        for name in counts:
            if name not in set_anchors:
                raise RunRefused(f"ladder.toml: tiers.{tier}.{name} has no [sets.{name}] anchors")
        tier_counts[tier] = {name: check.integer(counts, name, f"tiers.{tier}", minimum=1) for name in counts}
    noise = check.table(data, "noise", "")
    noise_out = {
        "rung": check.rung_name(noise, "rung", "noise", names),
        "items": check.integer(noise, "items", "noise", minimum=1),
        "k": check.integer(noise, "k", "noise", minimum=2),
        "max_sd_points": check.number(noise, "max_sd_points", "noise", low=0.0),
        "max_flip_rate": check.number(noise, "max_flip_rate", "noise", low=0.0, high=1.0),
    }
    order = check.table(data, "order", "")
    order_out = {
        "min_tau": check.number(order, "min_tau", "order", low=-1.0, high=1.0),
        "min_rungs": check.integer(order, "min_rungs", "order", minimum=2),
        "saturated_at": check.number(order, "saturated_at", "order", low=0.0, high=1.0),
    }
    canaries = check.table(data, "canaries", "")
    canaries_out: dict[str, Any] = {
        "rung": check.rung_name(canaries, "rung", "canaries", names),
        "max_p": check.number(canaries, "max_p", "canaries", low=0.0, high=1.0),
    }
    for name in CANARIES:
        entry = check.table(canaries, name, "canaries")
        where = f"canaries.{name}"
        chosen = entry.get("sets")
        if not isinstance(chosen, list) or not chosen or any(item not in set_anchors for item in chosen):
            raise RunRefused(f"ladder.toml: {where}.sets must list sets that have [sets.*] anchors")
        canaries_out[name] = {"sets": tuple(chosen), "min_drop": check.number(entry, "min_drop", where, low=0.0, high=100.0)}
        if name in CALLING_CANARIES:
            canaries_out[name]["items"] = check.integer(entry, "items", where, minimum=1) if "items" in entry else CANARY_ITEMS
        if name == "truncated_context":
            canaries_out[name]["keep"] = check.number(entry, "keep", where, low=0.0, high=1.0, open_low=True, open_high=True)
    replay = check.table(data, "replay", "")
    replay_out = {"max_abs_delta": check.number(replay, "max_abs_delta", "replay", low=0.0, open_low=True)}
    return Ladder(
        sha256=sha256_hex(raw),
        registered_at=registered_at,
        rungs=rungs,
        tiers=tier_counts,
        sets=set_anchors,
        noise=noise_out,
        order=order_out,
        canaries=canaries_out,
        replay=replay_out,
        version=version,
        rules=rules,
    )


class _Checker:
    """Typed reads of ``ladder.toml`` keys; a wrong type, a missing key or a non-finite number is refused."""

    def table(self, data: Mapping[str, Any], key: str, where: str) -> dict[str, Any]:
        value = data.get(key)
        if not isinstance(value, dict):
            raise RunRefused(f"ladder.toml: {_key(where, key)} must be a table")
        return value

    def text(self, data: Mapping[str, Any], key: str, where: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RunRefused(f"ladder.toml: {_key(where, key)} must be a non-empty string")
        return value

    def integer(self, data: Mapping[str, Any], key: str, where: str, *, minimum: int) -> int:
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise RunRefused(f"ladder.toml: {_key(where, key)} must be an integer >= {minimum}")
        return value

    def number(
        self,
        data: Mapping[str, Any],
        key: str,
        where: str,
        *,
        low: float,
        high: float | None = None,
        open_low: bool = False,
        open_high: bool = False,
    ) -> float:
        value = data.get(key)
        name = _key(where, key)
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise RunRefused(f"ladder.toml: {name} must be a finite number")
        value = float(value)
        if value < low or (open_low and value == low) or (high is not None and (value > high or (open_high and value == high))):
            bounds = f"{'(' if open_low else '['}{low}, {'inf' if high is None else high}{')' if open_high or high is None else ']'}"
            raise RunRefused(f"ladder.toml: {name} must lie in {bounds}")
        return value

    def rung_name(self, data: Mapping[str, Any], key: str, where: str, names: Sequence[str]) -> str:
        value = self.text(data, key, where)
        if value not in names:
            raise RunRefused(f"ladder.toml: {_key(where, key)} names no rung of the ladder")
        return value

    def choice(self, data: Mapping[str, Any], key: str, where: str, allowed: Sequence[str]) -> str:
        value = data.get(key)
        if value not in allowed:
            raise RunRefused(f"ladder.toml: {_key(where, key)} must be {' or '.join(json.dumps(item) for item in allowed)}")
        return value


def _key(where: str, key: str) -> str:
    return f"{where}.{key}" if where else key


def _rung(check: _Checker, entry: object, where: str) -> Rung:
    if not isinstance(entry, dict):
        raise RunRefused(f"ladder.toml: {where} must be a table")
    name = check.text(entry, "name", where)
    try:
        check_name("rung", name)
    except GigNameError as exc:
        raise RunRefused(f"ladder.toml: {where}.name: use lowercase letters, digits, '_' and '-' only") from exc
    adapter = check.text(entry, "adapter", where)
    if adapter not in ADAPTERS:
        raise RunRefused(f"ladder.toml: {where}.adapter must be one of {list(ADAPTERS)}")
    knobs = entry.get("knobs")
    if not isinstance(knobs, list) or any(knob not in KNOBS for knob in knobs):
        raise RunRefused(f"ladder.toml: {where}.knobs must list only {list(KNOBS)}")
    no_knobs = entry.get("no_knobs")
    if not knobs and (not isinstance(no_knobs, str) or not no_knobs.strip()):
        raise RunRefused(f"ladder.toml: {where}.no_knobs must say why a rung with no knobs cannot take temperature or seed")
    return Rung(
        name=name,
        adapter=adapter,
        model=check.text(entry, "model", where),
        model_name=check.text(entry, "model_name", where),
        price_in=check.number(entry, "price_in", where, low=0.0),
        price_out=check.number(entry, "price_out", where, low=0.0),
        price_source=check.text(entry, "price_source", where),
        rank=check.integer(entry, "rank", where, minimum=1),
        rank_source=check.text(entry, "rank_source", where),
        rank_date=check.text(entry, "rank_date", where),
        knobs=tuple(knobs),
        no_knobs=no_knobs if isinstance(no_knobs, str) else None,
        target=check.text(entry, "target", where) if "target" in entry else name,
    )


# --- statistics the gates use -----------------------------------------------------------------------------------


def kendall_tau_b(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Kendall's tau-b of two orderings; ``None`` when either side is all ties.

    In plain words: over every pair of rungs, the share ordered the same way by both lists minus the share ordered
    the opposite way, corrected for ties. 1 is the same order, -1 the reverse, 0 no relation.
    """

    if len(x) != len(y):
        raise ValueError("both orderings need the same length")
    for value in (*x, *y):
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise stats.NonFiniteScoreError("kendall_tau_b needs finite numbers")
    n = len(x)
    pairs = n * (n - 1) // 2
    concordant = discordant = ties_x = ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = (x[i] > x[j]) - (x[i] < x[j])
            dy = (y[i] > y[j]) - (y[i] < y[j])
            ties_x += dx == 0
            ties_y += dy == 0
            if dx * dy > 0:
                concordant += 1
            elif dx * dy < 0:
                discordant += 1
    denominator = math.sqrt((pairs - ties_x) * (pairs - ties_y))
    if denominator == 0.0:
        return None
    return (concordant - discordant) / denominator


def order_gate(scores: Mapping[str, float | None], ranks: Mapping[str, int], *, min_tau: float, min_rungs: int) -> dict[str, Any]:
    """Gate a: Kendall tau-b between the measured suite scores and the published ranks (1 = strongest)."""

    measured = [name for name, score in scores.items() if score is not None]
    out: dict[str, Any] = {"tau": None, "min_tau": min_tau, "rungs": measured}
    if len(measured) < min_rungs:
        return {**out, "status": "not_validated", "reason": f"{len(measured)} rung(s) measured; the order check needs {min_rungs}"}
    tau = kendall_tau_b([float(scores[name]) for name in measured], [-float(ranks[name]) for name in measured])  # type: ignore[arg-type]
    if tau is None:
        return {**out, "status": "not_validated", "reason": "every measured rung scored the same, so tau-b is undefined"}
    return {**out, "tau": tau, "status": "pass" if tau >= min_tau else "fail"}


def noise_gate(rows: Sequence[tuple[str, Sequence[float | None]]], *, max_sd_points: float, max_flip_rate: float) -> dict[str, Any]:
    """Gate b from ``rows`` of (set, the item's value in each repeat).

    The suite score of a repeat is the mean over sets of the set's mean item value, in points (0-100); ``sd`` is the
    sample standard deviation of those scores across repeats. An item flips when it is right (value >= 0.5) in some
    repeat and wrong in another; ``flip_rate`` is the share of scored items that flip.
    """

    scored = [(name, list(values)) for name, values in rows if all(value is not None for value in values)]
    for _, values in scored:
        if not all(math.isfinite(value) for value in values):  # type: ignore[arg-type]
            raise stats.NonFiniteScoreError("noise_gate needs finite scores")
    out: dict[str, Any] = {"n": len(scored), "max_sd_points": max_sd_points, "max_flip_rate": max_flip_rate}
    k = len(scored[0][1]) if scored else 0
    if len(scored) < 2 or k < 2 or any(len(values) != k for _, values in scored):
        return {**out, "sd": None, "flip_rate": None, "k": k, "status": "not_validated", "reason": "needs 2+ scored items with 2+ repeats each"}
    by_set: dict[str, list[list[float]]] = {}
    for name, values in scored:
        by_set.setdefault(name, []).append(values)  # type: ignore[arg-type]
    suite = [
        100.0 * math.fsum(math.fsum(item[r] for item in items) / len(items) for items in by_set.values()) / len(by_set)
        for r in range(k)
    ]
    sd = statistics.stdev(suite)
    flips = sum(len({value >= _CORRECT for value in values}) > 1 for _, values in scored)  # type: ignore[operator]
    flip_rate = flips / len(scored)
    status = "pass" if sd <= max_sd_points and flip_rate <= max_flip_rate else "fail"
    return {**out, "k": k, "suite_scores": suite, "sd": sd, "flips": flips, "flip_rate": flip_rate, "status": status}


#: The plain-words reading of a measured gate b's ``min_detectable_difference``, printed verbatim in the reports.
MIN_DETECTABLE_DIFFERENCE_FORMULA = (
    "two rungs' suite scores can be told apart only if they differ by more than 2.77 times the noise sd (1.96 for a"
    " 95% interval, times the square root of 2 because both scores are noisy); this assumes every rung is as noisy as"
    " the noise rung, and the sd comes from only k repeats, so it is rough"
)


def noise_rule(result: Mapping[str, Any], rung: Rung, *, deterministic: bool) -> dict[str, Any]:
    """Gate b's ``noise_gate`` result under the rule its noise rung allows.

    A rung that can fix a seed or a temperature keeps the limits: ``rule`` is ``limit`` and the status stays ``pass``
    / ``fail``. So does the deterministic adapter, and every rung of a ``--deterministic`` run (``deterministic``),
    because canned answers cannot differ between repeats. A rung that can fix neither repeats differently by design,
    so its gate is a measurement: ``rule`` is ``measured``, the status becomes ``measured`` (``not_validated`` wins),
    and the result carries the limits, which of them the numbers exceed, and the smallest difference the noise lets
    two suite scores be told apart by (``1.96 * sqrt(2) * sd`` points).
    """

    limits = {"max_sd_points": result["max_sd_points"], "max_flip_rate": result["max_flip_rate"]}
    if deterministic or rung.deterministic or "seed" in rung.knobs or "temperature" in rung.knobs:
        return {**result, "rule": "limit", "limits": limits}
    sd, flip_rate = result["sd"], result["flip_rate"]
    out = {
        **result,
        "rule": "measured",
        "limits": limits,
        "min_detectable_difference": None if sd is None else 1.96 * math.sqrt(2) * sd,
        "min_detectable_difference_formula": MIN_DETECTABLE_DIFFERENCE_FORMULA,
    }
    if result["status"] == "not_validated":
        return {**out, "over_limits": None}
    return {
        **out,
        "status": "measured",
        "reason": f"the {rung.adapter} rung takes no seed or temperature, so its repeats differ by design; the limits are reported but not applied",
        "over_limits": {"sd": sd > limits["max_sd_points"], "flip_rate": flip_rate > limits["max_flip_rate"]},
    }



def canary_verdict(name: str, base: Sequence[float], canary: Sequence[float], *, min_drop: float, max_p: float) -> dict[str, Any]:
    """Gate c for one canary over paired item values: the drop in points and the exact McNemar p of the flips.

    The canary passes when the score drops by at least ``min_drop`` points and p is below ``max_p``.
    """

    if len(base) != len(canary):
        raise ValueError("a canary pairs every item with its baseline")
    values = [*base, *canary]
    if not all(math.isfinite(value) for value in values):
        raise stats.NonFiniteScoreError("canary_verdict needs finite scores")
    out: dict[str, Any] = {"canary": name, "items": len(base), "min_drop": min_drop, "max_p": max_p}
    if not base:
        return {**out, "drop": None, "p": None, "status": "not_validated", "reason": f"canary {name}: no scored item"}
    drop = 100.0 * (math.fsum(base) - math.fsum(canary)) / len(base)
    b = sum(x >= _CORRECT and y < _CORRECT for x, y in zip(base, canary, strict=True))
    c = sum(x < _CORRECT and y >= _CORRECT for x, y in zip(base, canary, strict=True))
    p = stats.mcnemar_exact(b, c)["p_exact"]
    out.update(drop=drop, p=p, b=b, c=c)
    if drop < min_drop:
        return {**out, "status": "fail", "reason": f"canary {name}: drop {drop:.2f} points < {min_drop:g}"}
    if p >= max_p:
        return {**out, "status": "fail", "reason": f"canary {name}: McNemar p {p:.4g} >= {max_p:g}"}
    return {**out, "status": "pass"}


# --- canaries --------------------------------------------------------------------------------------------------


def broken_prompt(prompt: str, item_id: str) -> str:
    """The prompt with its sentences (or, for one sentence, its words) shuffled by a seed fixed per item."""

    parts = re.split(r"(?<=[.!?])\s+", prompt.strip())
    joiner = " "
    if len(parts) < 2:
        parts = prompt.split()
    if len(parts) < 2:
        return prompt[::-1] if len(prompt) > 1 else f"{prompt}?"
    shuffled = list(parts)
    random.Random(f"{ds.SEED}:{item_id}").shuffle(shuffled)
    if shuffled == parts:
        shuffled = shuffled[1:] + shuffled[:1]
    return joiner.join(shuffled)


def truncated_context(prompt: str, keep: float) -> str:
    """The first ``keep`` share of the prompt's characters (at least one)."""

    return prompt[: max(1, int(len(prompt) * keep))]


_NEXT_LETTER = {"A": "B", "B": "C", "C": "D", "D": "A"}
_NEXT_NLI = {"Entailment": "Contradiction", "Contradiction": "NotMentioned", "NotMentioned": "Entailment"}
_NEXT_VERDICT = {"A>B": "B>A", "B>A": "A>B", "A": "B", "B": "A"}


def wrong_label(set_name: str, expected: Any) -> Any:
    """``expected`` with every gold label replaced by a wrong one, so a correct answer scores as wrong."""

    if set_name in ("quality", "tinymmlu"):
        return _NEXT_LETTER.get(str(expected).strip().upper(), "A")
    if set_name == "ragtruth":
        return {**expected, "hallucinated": not expected["hallucinated"]}
    if set_name == "contractnli":
        return {"labels": {key: _NEXT_NLI.get(value, "Entailment") for key, value in expected["labels"].items()}}
    if set_name == "judgebench":
        return {**expected, "label": _NEXT_VERDICT.get(expected["label"], "A>B")}
    if set_name == "mtbench_human":
        return {**expected, "winner": _NEXT_VERDICT.get(expected["winner"], "A")}
    if set_name == "rewardbench2":
        rejected = list(expected.get("rejected") or [])
        return {**expected, "chosen": rejected[0] if rejected else "none", "rejected": [expected["chosen"], *rejected[1:]]}
    if set_name == "tinygsm8k":
        return str(_number(str(expected)) + 1) if _number(str(expected)) is not None else "0"
    raise RunRefused(f"the wrong_label canary has no wrong label for set {set_name}")


# --- per-set scorers --------------------------------------------------------------------------------------------


def parse_json(text: str) -> tuple[Any, str]:
    """``text`` parsed as JSON (a ```json fence is stripped first): ``(value, "ok")`` or ``(None, "not_json")``."""

    body = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, flags=re.DOTALL)
    if fenced:
        body = fenced.group(1)
    try:
        return json.loads(body), "ok"
    except (json.JSONDecodeError, RecursionError):
        return None, "not_json"


def score(set_name: str, expected: Any, text: str) -> float | None:
    """The value (0-1) of answer ``text`` on an item of ``set_name``; ``None`` when the item cannot be scored."""

    parsed, status = parse_json(text)
    if set_name in ("quality", "tinymmlu"):
        found = re.findall(r"ANSWER:\s*\(?([A-D])\b", text)
        return float(bool(found) and found[-1] == str(expected).strip().upper())
    if set_name == "tinygsm8k":
        numbers = re.findall(r"-?\d[\d,]*(?:\.\d+)?", text)
        got = _number(numbers[-1]) if numbers else None
        want = _number(str(expected))
        return float(got is not None and want is not None and got == want)
    if set_name == "ifeval":
        return ifeval_score(text, expected)
    if set_name == "jsonschemabench":
        return float(status == "ok" and _schema_valid(expected["schema"], parsed))
    if not isinstance(parsed, (dict, list)):
        return 0.0
    if set_name == "contractnli":
        want = expected["labels"]
        got = parsed.get("labels") if isinstance(parsed, dict) else None
        if not isinstance(got, dict) or not want:
            return 0.0
        return sum(got.get(key) == value for key, value in want.items()) / len(want)
    if set_name == "ragtruth":
        supported = parsed.get("supported") if isinstance(parsed, dict) else None
        return float(isinstance(supported, bool) and supported == (not expected["hallucinated"]))
    if set_name == "rewardbench2":
        if not isinstance(parsed, list):
            return 0.0
        scores = {row.get("id"): row.get("score") for row in parsed if isinstance(row, dict)}
        chosen = scores.get(expected["chosen"])
        others = [value for key, value in scores.items() if key != expected["chosen"]]
        numeric = all(isinstance(value, int | float) and not isinstance(value, bool) for value in (chosen, *others))
        return float(numeric and bool(others) and all(chosen > value for value in others))  # type: ignore[operator]
    if set_name == "judgebench":
        return float(isinstance(parsed, dict) and parsed.get("verdict") == expected["label"])
    if set_name == "mtbench_human":
        return float(isinstance(parsed, dict) and parsed.get("winner") == expected["winner"])
    if set_name == "injection_composite":
        verdict = parsed.get("verdict") if isinstance(parsed, dict) else None
        return float(isinstance(verdict, str) and verdict not in expected["manipulated_verdicts"])
    raise RunRefused(f"no base-run scorer for set {set_name}")


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _schema_valid(schema: Any, value: Any) -> bool:
    import jsonschema

    try:
        return bool(jsonschema.validators.validator_for(schema)(schema).is_valid(value))
    except (jsonschema.exceptions.SchemaError, TypeError, ValueError, RecursionError, re.error):
        return False


def _relation(count: int, relation: str | None, n: int) -> bool:
    if relation == "less than":
        return count < n
    return count >= n


def _ifeval_check(instruction: str, text: str, kwargs: Mapping[str, Any]) -> bool | None:
    """One IFEval instruction checked with the standard library; ``None`` for a type this does not verify.

    Approximate: 15 of the 25 upstream instruction types. Known gaps against upstream: english_capital and
    english_lowercase skip its langdetect == "en" check, and number_sentences splits on a regex where it uses
    nltk punkt.
    """

    if instruction == "keywords:existence":
        return all(re.search(re.escape(word), text, flags=re.IGNORECASE) for word in kwargs["keywords"])
    if instruction == "keywords:forbidden_words":
        return not any(re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE) for word in kwargs["forbidden_words"])
    if instruction == "keywords:frequency":
        found = len(re.findall(re.escape(kwargs["keyword"]), text, flags=re.IGNORECASE))
        return _relation(found, kwargs.get("relation"), int(kwargs["frequency"]))
    if instruction == "punctuation:no_comma":
        return "," not in text
    if instruction == "detectable_format:title":
        return re.search(r"<<[^\n]*\S[^\n]*>>", text) is not None
    if instruction == "length_constraints:number_sentences":
        sentences = [part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
        return _relation(len(sentences), kwargs.get("relation"), int(kwargs["num_sentences"]))
    if instruction == "length_constraints:number_paragraphs":
        paragraphs = re.split(r"\s?\*\*\*\s?", text)
        if any(not part.strip() for index, part in enumerate(paragraphs) if 0 < index < len(paragraphs) - 1):
            return False
        return len([part for part in paragraphs if part.strip()]) == int(kwargs["num_paragraphs"])
    if instruction == "detectable_content:number_placeholders":
        return len(re.findall(r"\[.*?\]", text)) >= int(kwargs["num_placeholders"])
    if instruction == "startend:quotation":
        stripped = text.strip()
        return len(stripped) > 1 and stripped[0] == '"' and stripped[-1] == '"'
    if instruction == "detectable_format:number_highlighted_sections":
        single = [part for part in re.findall(r"\*[^\n\*]*\*", text) if part.strip("*").strip()]
        double = [part for part in re.findall(r"\*\*[^\n\*]*\*\*", text) if part.strip("*").strip()]
        return len(single) + len(double) >= int(kwargs["num_highlights"])
    if instruction == "change_case:english_capital":
        return text.isupper()
    if instruction == "change_case:english_lowercase":
        return text.islower()
    if instruction == "combination:repeat_prompt":
        return text.strip().lower().startswith(str(kwargs["prompt_to_repeat"]).strip().lower())
    if instruction == "startend:end_checker":
        return text.strip().strip('"').lower().endswith(str(kwargs["end_phrase"]).strip().lower())
    if instruction == "detectable_format:json_format":
        return parse_json(text)[1] == "ok"
    return None


def ifeval_score(text: str, expected: Mapping[str, Any]) -> float | None:
    """Approximate IFEval prompt-level strict accuracy: 1.0 when every instruction holds; ``None`` when one cannot be checked."""

    verdicts = []
    for instruction, kwargs in zip(expected["instruction_id_list"], expected["kwargs"], strict=True):
        try:
            verdict = _ifeval_check(instruction, text, {key: value for key, value in (kwargs or {}).items() if value is not None})
        except (KeyError, TypeError, ValueError):
            verdict = None
        if verdict is None:
            return None
        verdicts.append(verdict)
    return float(all(verdicts))


# --- the plan and the cap ---------------------------------------------------------------------------------------


def allocate(sizes: Mapping[str, int], total: int) -> dict[str, int]:
    """``total`` items taken one set at a time in order (round robin), never more than a set holds."""

    taken = dict.fromkeys(sizes, 0)
    remaining = min(total, sum(sizes.values()))
    while remaining:
        for name, size in sizes.items():
            if remaining and taken[name] < size:
                taken[name] += 1
                remaining -= 1
    return taken


def _helper_rung(ladder: Ladder, preferred: str, selected: Sequence[Rung]) -> Rung | None:
    """The rung that answers the noise or canary runs: ``preferred`` when selected, else the strongest selected."""

    if not selected:
        return None
    for rung in selected:
        if rung.name == preferred:
            return rung
    live = sorted((rung for rung in selected if not rung.deterministic), key=lambda rung: rung.rank)
    return live[0] if live else selected[0]


def canary_items(ladder: Ladder, sizes: Mapping[str, int]) -> dict[str, dict[str, int]]:
    """Per calling canary, how many of each of its sets' items it re-asks: its ``items`` taken round robin."""

    out = {}
    for name in CALLING_CANARIES:
        config = ladder.canaries[name]
        out[name] = allocate({set_name: sizes[set_name] for set_name in config["sets"] if set_name in sizes}, config["items"])
    return out


def plan(ladder: Ladder, selected: Sequence[Rung], counts: Mapping[str, int], deterministic: bool) -> dict[str, Any]:
    """Calls and dollars per rung and set before anything runs; deterministic answers cost nothing.

    The noise rung's main answers are its first repeat, so noise costs ``k - 1`` extra passes over its items;
    a calling canary re-asks its ``items``; truncated_context sends ``keep`` of each prompt.
    """

    noise_rung = _helper_rung(ladder, ladder.noise["rung"], selected)
    canary_rung = _helper_rung(ladder, ladder.canaries["rung"], selected)
    extra = ladder.noise["k"] - 1
    # The noise budget is taken over every set of the tier. Under the "gated" noise rule the run takes the same
    # number of items or fewer over the gated sets only, so the calls stay an upper bound; the dollars can shift
    # between sets, because the gated sets' token anchors differ from the sets this estimate spreads them over.
    noise_items = allocate(counts, ladder.noise["items"])
    canaries = canary_items(ladder, counts)
    rows = []
    for rung in selected:
        free = deterministic or rung.deterministic
        noise_calls = sum(noise_items.values()) * extra if rung is noise_rung else 0
        canary_calls = sum(sum(taken.values()) for taken in canaries.values()) if rung is canary_rung else 0
        dollars = 0.0
        if not free:
            parts = [n * ladder.cost(rung, set_name) for set_name, n in counts.items()]
            if rung is noise_rung:
                parts += [n * extra * ladder.cost(rung, set_name) for set_name, n in noise_items.items()]
            if rung is canary_rung:
                for name, taken in canaries.items():
                    keep = ladder.canaries[name].get("keep", 1.0)
                    parts += [n * ladder.cost(rung, set_name, keep) for set_name, n in taken.items()]
            dollars = math.fsum(parts)
        total = sum(counts.values()) + noise_calls + canary_calls
        rows.append(
            {
                "rung": rung.name,
                "sets": dict(counts),
                "noise_calls": noise_calls,
                "canary_calls": canary_calls,
                "calls": total,
                "model_calls": 0 if free else total,
                "dollars": round(dollars, 2),
            }
        )
    return {
        "rungs": rows,
        "noise_rung": noise_rung.name if noise_rung else None,
        "canary_rung": canary_rung.name if canary_rung else None,
        "model_calls": sum(row["model_calls"] for row in rows),
        "dollars": round(math.fsum(row["dollars"] for row in rows), 2),
    }


@dataclass
class CallCap:
    """Counts model calls (to every rung but the deterministic one) and refuses the one that would pass the cap, before it is made."""

    cap: int | None
    made: int = 0

    def call(self, invoke: Callable[[], Any]) -> Any:
        if self.cap is not None and self.made >= self.cap:
            raise CapReachedError(f"cap {self.cap} reached before call {self.cap + 1}")
        try:
            return invoke()
        finally:
            self.made += 1


def _registration(manifest: Mapping[str, Any]) -> tuple[dict[str, Any] | None, int | None]:
    """The gig's base-run pre-registration and call cap, from its steps' ``release`` gates."""

    evals = manifest.get("evals") or {}
    steps = evals.get("steps") if isinstance(evals, dict) else None
    preregs: list[dict[str, Any]] = []
    caps: list[int] = []
    for step in steps if isinstance(steps, list) else []:
        gate = ((step.get("gates") or {}) if isinstance(step, dict) else {}).get("release")
        if not isinstance(gate, dict):
            continue
        cap = gate.get("max_calls")
        if cap is not None:
            if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
                raise RunRefused("a release gate's max_calls must be an integer >= 0")
            caps.append(cap)
        if "prereg" in gate:
            prereg = gate["prereg"]
            pin = prereg.get("ladder_sha256") if isinstance(prereg, dict) else None
            day = prereg.get("registered_at") if isinstance(prereg, dict) else None
            if not isinstance(pin, str) or re.fullmatch(r"[0-9a-f]{64}", pin) is None or not isinstance(day, str):
                raise RunRefused("a release gate's prereg needs registered_at and a 64-hex ladder_sha256")
            preregs.append({"registered_at": day, "ladder_sha256": pin})
    if any(prereg != preregs[0] for prereg in preregs):
        raise RunRefused("the gig's release gates register different base-run preregs")
    return (preregs[0] if preregs else None), (min(caps) if caps else None)


def _ollama_models(base_url: str) -> list[str] | None:
    """The model names the Ollama server at loopback ``base_url`` lists at ``/api/tags``; ``None`` when it does not answer."""

    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{base_url}/api/tags", timeout=2) as response:
            payload = json.loads(response.read(1_048_576))
    except (OSError, ValueError):
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return None
    return [entry["name"] for entry in models if isinstance(entry, dict) and isinstance(entry.get("name"), str)]


def _unreachable(rung: Rung, config: Any) -> str | None:
    """Why ``rung`` cannot be called on this machine (no target, other model, binary, key or pulled model); ``None`` when it can.

    Runs only when a call will be made: the local Ollama probe is the one request it sends, to 127.0.0.1 only.
    """

    target = next((item for item in config.model_targets if item.name == rung.target), None)
    if target is None:
        return f"no model target {rung.target!r} in config.toml"
    endpoint = next((item for item in config.endpoints if item.name == target.endpoint), None)
    if endpoint is None or endpoint.adapter != rung.adapter:
        return f"model target {rung.target!r} is not a {rung.adapter} target"
    if target.model != rung.model:
        return f"model target {rung.target!r} runs {target.model!r}; the ladder pins {rung.model!r}"
    binary = _BINARIES.get(rung.adapter)
    if binary is not None and shutil.which(binary) is None:
        return f"no {binary} executable on PATH"
    if rung.adapter in ("openrouter_api", "openai_api"):
        credential = next((item for item in config.credentials if item.name == endpoint.credential), None)
        if credential is None:
            return f"endpoint {endpoint.name!r} has no credential"
        if credential.kind == "environment" and not os.environ.get(credential.reference):
            return f"no key in ${credential.reference}"
    if rung.adapter == "ollama_local":
        from gigai.adapters.ollama_local import _checked_endpoint

        try:
            base_url = _checked_endpoint(endpoint.base_url)  # type: ignore[arg-type]
        except ValueError:
            return f"endpoint {endpoint.name!r} is not a loopback http://127.0.0.1:<port> URL"
        names = _ollama_models(base_url)
        if names is None:
            return f"no Ollama server answers at {base_url}"
        if target.model not in names:
            return f"the Ollama server at {base_url} has not pulled {target.model!r}"
    return None


# --- failed calls -----------------------------------------------------------------------------------------------

#: An API answering one of these refused the credential or the account (out of credits): every later call fails too.
_RUN_LEVEL_HTTP = frozenset({401, 402, 403})


def http_status(exc: BaseException) -> int | None:
    """The HTTP status behind a failed call: an httpx response in its cause chain, or ``HTTP NNN`` in a local Ollama error."""

    from gigai.adapters.ollama_local import OllamaLocalAdapterError

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
        current = current.__cause__ or current.__context__
    found = re.search(r"\bHTTP (\d{3})\b", str(exc)) if isinstance(exc, OllamaLocalAdapterError) else None
    return int(found.group(1)) if found else None


def failure_kind(exc: BaseException, *, schema_mode: bool) -> str:
    """What one failed model call means for the run: ``"answer"``, ``"item"`` or ``"run"``.

    ``schema_mode``: the call carried ``output_mode="schema"`` and the item's JSON Schema.

    - ``answer``: the model's wrong answer, recorded as an empty answer and scored like any non-JSON answer; not a
      failed call. A schema-mode reply that is not JSON (``ModelOutputNotJSONError``), and an HTTP 400 to a
      schema-mode call: the provider cannot answer under that schema (API strict ``response_format``, Ollama ``format``).
    - ``item``: the call failed for this one request: any other ``ModelInvocationError`` (a local Ollama server's
      HTTP 4xx/5xx, timeout or malformed reply, an API's HTTP or transport error, a CLI's non-zero exit or timeout) or
      a bounded call that timed out or failed. The run records it and goes on.
    - ``run``: every later call would fail the same way, or the error is unknown, so the run aborts: missing
      authentication, a capability or request-field refusal, a cancelled call, a local model whose identity changed,
      an API answering 401/402/403, a bounded call cancelled or out of budget, and anything else.
    """

    from gigai.adapters.ollama_local import OllamaLocalIdentityError
    from gigai.adapters.port import (
        CapabilityMismatchError,
        ModelAuthenticationRequired,
        ModelInvocationCancelled,
        ModelInvocationError,
        ModelOutputNotJSONError,
        UnsupportedRequestFieldError,
    )
    from gigai.model_call import BoundedCallError

    if isinstance(exc, ModelOutputNotJSONError):
        return "answer"
    if isinstance(exc, BoundedCallError):
        return "item" if exc.reason in ("timed_out", "failed") else "run"
    run_level = (ModelAuthenticationRequired, CapabilityMismatchError, UnsupportedRequestFieldError, ModelInvocationCancelled, OllamaLocalIdentityError)
    if not isinstance(exc, ModelInvocationError) or isinstance(exc, run_level):
        return "run"
    status = http_status(exc)
    if status in _RUN_LEVEL_HTTP:
        return "run"
    return "answer" if schema_mode and status == 400 else "item"


class _ItemFailed(Exception):
    """One item's model call failed at item level; carries the error class and HTTP status, never the error's text."""

    def __init__(self, code: str, status: int | None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status

    def error(self) -> dict[str, Any]:
        """The stored trace error: the class name and HTTP status only."""

        detail = self.code if self.status is None else f"{self.code} (HTTP {self.status})"
        return {"code": self.code, "message": detail, "retryable": False}


# --- the run ----------------------------------------------------------------------------------------------------


@dataclass
class _Context:
    gig: GigContext
    ladder: Ladder
    connection: Any
    deterministic: bool
    source: str
    cap: CallCap
    config: Any = None
    items: dict[str, list[ds.Item]] = field(default_factory=dict)
    canned: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)
    prereg: records.Prereg | None = None
    calls_by_rung: dict[str, int] = field(default_factory=dict)
    #: Each rung's planned model calls (main, noise and canary): the base of its failed-call limit.
    planned_calls: dict[str, int] = field(default_factory=dict)
    #: Each rung's failed calls by error class.
    failures: dict[str, dict[str, int]] = field(default_factory=dict)

    def answer(self, rung: Rung, set_name: str, item: ds.Item, prompt: str) -> tuple[str, records.ModelCall | None]:
        """The rung's answer to ``prompt`` for ``item``: canned for the deterministic rung or run, else one capped call.

        The canned answer is keyed by item id and given only to the item's own prompt; an altered (canary) prompt gets none.
        An item tagged ``output_mode:schema`` is asked in schema mode with its own JSON Schema.
        """

        if self.deterministic or rung.deterministic:
            stored = self.canned[set_name].get(item.id)
            return (stored[1] if stored is not None and stored[0] == prompt else ""), None
        fields = _schema_settings({"tags": list(item.tags), "expected": item.expected}, {}, prompt)
        return self.cap.call(lambda: self._call(rung, prompt, fields))

    def _call(self, rung: Rung, prompt: str, fields: Mapping[str, Any]) -> tuple[str, records.ModelCall | None]:
        from gigai.adapters import factory
        from gigai.model_call import invoke_bounded
        from gigai.steps import _WALL_TIME_MS

        self.calls_by_rung[rung.name] = self.calls_by_rung.get(rung.name, 0) + 1
        number = self.cap.made + 1
        try:
            binding = factory.resolve_model_adapter(self.config, rung.target, home_root=self.gig.home)
        except Exception as exc:
            raise RunRefused(f"rung {rung.name}: cannot build its adapter ({type(exc).__name__})") from exc
        settings = {"temperature": 0.0, "seed": ds.SEED}
        request = dataclasses.replace(
            binding.request(role="reviewer", prompt=prompt), **{key: settings[key] for key in rung.knobs}, **fields
        )
        try:
            result = invoke_bounded(
                binding.port, request, max_wall_time_ms=_WALL_TIME_MS, max_output_tokens=binding.current.target.max_output_tokens
            )
        except Exception as exc:
            kind = failure_kind(exc, schema_mode=request.output_mode == "schema")
            if kind == "answer":
                return "", None
            if kind == "item":
                raise _ItemFailed(type(exc).__name__, http_status(exc)) from exc
            digest = sha256_hex(prompt.encode("utf-8"))[:12]
            raise RunFailed(f"rung {rung.name}: call {number} (input sha256 {digest}) failed: {type(exc).__name__}") from exc
        finally:
            binding.close()
        return result.output_text, records.model_call(binding, request, result)

    def dataset(self, set_name: str) -> records.Dataset:
        items = self.items[set_name]
        return records.Dataset(
            id=set_name,
            version=self.source,
            sha256=dataset_digest(items[0].sha256),
            slice_recipe={"base": True, "items": len(items), "source": self.source},
            seed=ds.SEED,
        )

    def run_record(self, set_name: str | None, tag: str, *, k: int = 1, replay_of: str | None = None, dataset: records.Dataset | None = None) -> records.RunRecord:
        return records.RunRecord(
            run_id=records.new_id("run"),
            gig=self.gig.name,
            manifest_digest=_manifest_digest(self.gig),
            dataset=dataset or self.dataset(set_name),  # type: ignore[arg-type]
            prereg=self.prereg,
            step_id=_step(set_name) if set_name else None,
            suite=self.source,
            tag=tag,
            k=k,
            replay_of=replay_of,
        )

    def trace_and_score(
        self,
        run_id: str,
        rung: Rung,
        set_name: str,
        item: ds.Item,
        prompt: str,
        *,
        repeat: int = 0,
        expected: Any = None,
        scorer_id: str | None = None,
    ) -> float | None:
        try:
            text, model = self.answer(rung, set_name, item, prompt)
        except _ItemFailed as failed:
            self._record_failure(run_id, rung, set_name, item, prompt, repeat, failed)
            return None
        parsed, status = parse_json(text)
        trace = records.TraceRecord(
            trace_id=records.new_id("trace"),
            run_id=run_id,
            step_id=_step(set_name),
            item=records.ItemRef(item.id, self.source, item.cluster_id),
            input=prompt,
            output_raw_text=text,
            repeat_index=repeat,
            output_parsed=parsed,
            parse_status=status,
            model=model,
        )
        records.write_trace(self.connection, trace)
        value = score(set_name, item.expected if expected is None else expected, text)
        if value is not None:
            records.write_score(
                self.connection,
                records.ScoreRecord(records.new_id("score"), trace.trace_id, scorer_id or _scorer(set_name), SCORER_VERSION, value),
            )
        return value

    def _record_failure(self, run_id: str, rung: Rung, set_name: str, item: ds.Item, prompt: str, repeat: int, failed: _ItemFailed) -> None:
        """Record ``item``'s failed call as an unscored trace and abort once the rung's failed calls pass the limit."""

        records.write_trace(
            self.connection,
            records.TraceRecord(
                trace_id=records.new_id("trace"),
                run_id=run_id,
                step_id=_step(set_name),
                item=records.ItemRef(item.id, self.source, item.cluster_id),
                input=prompt,
                output_raw_text="",
                repeat_index=repeat,
                parse_status="not_parsed",
                outcome="failed",
                error=failed.error(),
            ),
        )
        counts = self.failures.setdefault(rung.name, {})
        counts[failed.code] = counts.get(failed.code, 0) + 1
        total, planned = sum(counts.values()), self.planned_calls.get(rung.name, 0)
        if total * 100 > planned * MAX_FAILED_PERCENT:
            raise RunFailed(
                f"rung {rung.name}: {total} of {planned} calls failed (limit {MAX_FAILED_PERCENT}%); last error {failed.code}"
            )


def _step(set_name: str) -> str:
    return f"base-{set_name}"


def _scorer(set_name: str) -> str:
    return f"base:{set_name}"


def _mean(values: Sequence[float | None]) -> float | None:
    scored = [value for value in values if value is not None]
    return math.fsum(scored) / len(scored) if scored else None


def execute_base(
    gig: GigContext,
    *,
    rung_names: Sequence[str] | None = None,
    tier: str = "gate",
    max_calls: int | None = None,
    deterministic: bool = False,
    plan_only: bool = False,
    report_path: Path | None = None,
    ladder_path: Path | None = None,
) -> dict[str, Any]:
    """Plan, check, run and gate a base run; refusals come before any row is written or any call is made."""

    if tier not in TIERS:
        raise RunRefused(f"tier {tier!r} is not one of {list(TIERS)}")
    prereg, manifest_cap = _registration(gig.manifest)
    pin = prereg["ladder_sha256"] if prereg is not None else None
    ladder = shipped_ladder(pin, None if ladder_path is None else (ladder_path,))
    selected = _select(ladder, rung_names)
    counts = dict(ladder.tiers[tier])
    planned = plan(ladder, selected, counts, deterministic)
    head = {
        "gig": gig.name,
        "tier": tier,
        "deterministic": deterministic,
        "ladder_sha256": ladder.sha256,
        "ladder_version": ladder.version,
        "rules": dict(ladder.rules),
        "prereg": prereg,
    }
    if plan_only:
        return {**head, "plan": planned}
    if report_path is not None and not Path(report_path).parent.is_dir():
        raise RunRefused(f"--report: folder {Path(report_path).parent} does not exist")
    caps = [cap for cap in (max_calls, manifest_cap) if cap is not None]
    cap = min(caps) if caps else None
    if planned["model_calls"]:
        if cap is None:
            raise RunRefused(
                f"the base run plans {planned['model_calls']} model calls:"
                " pass --max-calls N (or give a release gate max_calls)"
            )
        if planned["model_calls"] > cap:
            raise RunRefused(
                f"cap {cap} reached before call {cap + 1}"
                f" (the plan needs {planned['model_calls']} model calls; see --plan)"
            )
    skipped: list[dict[str, str]] = []
    config = None
    if not deterministic and any(not rung.deterministic for rung in selected):
        from gigai.config import load_config

        try:
            config = load_config(gig.home)
        except Exception as exc:
            raise RunRefused(f"cannot read config.toml ({type(exc).__name__})") from exc
        reachable = []
        for rung in selected:
            reason = None if rung.deterministic else _unreachable(rung, config)
            if reason is None:
                reachable.append(rung)
            else:
                skipped.append({"rung": rung.name, "reason": reason})
        selected = reachable
    source = "ci" if deterministic else "release"
    items = _load_items(gig, counts, deterministic) if selected else {}
    connection = records.open_evals_db(gig.name, gig.home) if selected else None
    run = _Context(gig, ladder, connection, deterministic, source, CallCap(cap), config=config, items=items)
    run.canned = {name: {item.id: (item.prompt, item.completion or "") for item in rows} for name, rows in items.items()}
    # Re-planned on the reachable rungs: a skipped rung can move the noise or canary passes to another rung.
    run.planned_calls = {row["rung"]: row["model_calls"] for row in plan(ladder, selected, counts, deterministic)["rungs"]}
    run.prereg = records.Prereg(
        primary_metric="suite_score",
        pass_rule="tau_b_min",
        margin=ladder.order["min_tau"],
        n_planned=sum(len(rows) for rows in items.values()),
        registered_at=(prereg or {}).get("registered_at") or ladder.registered_at,
    )
    try:
        report = _run_and_gate(run, selected, skipped, tier)
    finally:
        if connection is not None:
            connection.close()
    report = {**head, "source": source, **report, "plan": planned}
    if report_path is not None:
        Path(report_path).write_text(render_markdown(report), encoding="utf-8")
    return report


def _select(ladder: Ladder, rung_names: Sequence[str] | None) -> list[Rung]:
    if rung_names is None:
        return list(ladder.rungs)
    known = [rung.name for rung in ladder.rungs]
    if not rung_names or any(not name for name in rung_names):
        raise RunRefused("--rungs needs comma-separated rung names")
    if len(set(rung_names)) != len(rung_names):
        raise RunRefused("--rungs names a rung twice")
    unknown = [name for name in rung_names if name not in known]
    if unknown:
        raise RunRefused(f"--rungs: no rung {unknown[0]!r} in the ladder (rungs: {', '.join(known)})")
    return [ladder.rung(name) for name in rung_names]


def _load_items(gig: GigContext, counts: Mapping[str, int], deterministic: bool) -> dict[str, list[ds.Item]]:
    """The first ``counts[set]`` items of each set: the committed ci slices, or the release slices in the gig folder."""

    out = {}
    for set_name, n in counts.items():
        if deterministic:
            path = Path(ds.CI_DIR) / f"{set_name}.jsonl"
            if not path.is_file():
                raise RunRefused(
                    f"--deterministic reads the committed ci slices, which ship only in a gigai source checkout"
                    f" (tests/fixtures/evals/ci/{set_name}.jsonl is not there); run it from a checkout"
                )
        else:
            from gigai.gigs.eval_manifest import data_folder

            folder = data_folder(gig.home, gig.name)
            if folder is None:
                raise RunRefused(f"gig {gig.name!r} has no gig folder")
            path = folder / "datasets" / f"{set_name}-release.jsonl"
            if not path.is_file():
                path = ds.download_release(set_name, folder)
        try:
            rows = ds.read_slice(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RunRefused(f"{set_name}: cannot read its slice {path.name} ({type(exc).__name__})") from exc
        if not rows:
            raise RunRefused(f"{set_name}: its slice {path.name} has no item")
        out[set_name] = rows[:n]
    return out


def saturation(
    ladder: Ladder, selected: Sequence[Rung], sizes: Mapping[str, int], values: Mapping[tuple[str, str], list[float | None]]
) -> list[dict[str, Any]]:
    """The report's ``sets`` rows: per set of ``sizes``, its best counted mean and whether that is at or above the line.

    A saturated set is flagged and left out of the gates. Under the "model" saturation rule the deterministic rung is
    not counted: its canned answers are the gold answer on several sets, so it would saturate them although no model
    does; a set no model rung scored is then not saturated.
    """

    counted = [rung for rung in selected if ladder.rules["saturation"] == "all" or not rung.deterministic]
    out = []
    for set_name, size in sizes.items():
        means = [_mean(values[(rung.name, set_name)]) for rung in counted]
        best = max((mean for mean in means if mean is not None), default=None)
        out.append(
            {
                "set": set_name,
                "k_score": ladder.sets[set_name]["k_score"],
                "items": size,
                "best": None if best is None else 100.0 * best,
                "saturated": best is not None and best >= ladder.order["saturated_at"],
            }
        )
    return out


def _run_and_gate(run: _Context, selected: Sequence[Rung], skipped: list[dict[str, str]], tier: str) -> dict[str, Any]:
    ladder = run.ladder
    rows: list[dict[str, Any]] = []
    values: dict[tuple[str, str], list[float | None]] = {}
    main_runs: dict[tuple[str, str], str] = {}
    for rung in selected:
        for set_name, items in run.items.items():
            record = run.run_record(set_name, f"base:{rung.name}")
            records.write_run(run.connection, record)
            before = run.cap.made
            item_values = [run.trace_and_score(record.run_id, rung, set_name, item, item.prompt) for item in items]
            made = run.cap.made - before
            records.finish_run(run.connection, record.run_id, made)
            values[(rung.name, set_name)] = item_values
            main_runs[(rung.name, set_name)] = record.run_id
            mean = _mean(item_values)
            rows.append(
                {
                    "rung": rung.name,
                    "set": set_name,
                    "run_id": record.run_id,
                    "items": len(items),
                    "scored": sum(value is not None for value in item_values),
                    "score": None if mean is None else 100.0 * mean,
                    "calls_made": made,
                }
            )
    sets_out = saturation(ladder, selected, {name: len(items) for name, items in run.items.items()}, values)
    saturated = {entry["set"] for entry in sets_out if entry["saturated"]}
    gated = [name for name in run.items if name not in saturated]
    suite: dict[str, float | None] = {}
    for rung in selected:
        means = [_mean(values[(rung.name, name)]) for name in gated]
        known = [mean for mean in means if mean is not None]
        suite[rung.name] = 100.0 * math.fsum(known) / len(known) if known and len(known) == len(means) else None
    # Every selected rung's suite score is shown; under the "model" rule gate a ranks the model rungs only.
    ranked = [rung for rung in selected if ladder.rules["order_rungs"] == "all" or not rung.deterministic]
    gate_a = order_gate(
        {rung.name: suite[rung.name] for rung in ranked},
        {rung.name: rung.rank for rung in ranked},
        min_tau=ladder.order["min_tau"],
        min_rungs=ladder.order["min_rungs"],
    )
    gate_a["excluded_sets"] = sorted(saturated)
    gate_a["skipped"] = [entry["rung"] for entry in skipped]
    if ladder.rules["order_rungs"] == "model":
        gate_a["unranked"] = [{"rung": rung.name, "reason": UNRANKED_REASON} for rung in selected if rung not in ranked]
    gate_b = _noise(run, selected, gated, values)
    gate_c = _canaries(run, selected, values, main_runs)
    gate_d = {"status": "not_validated", "reason": "judge agreement with human labels is not checked by the base run"}
    gate_e = _replay(run, main_runs)
    rungs_out = []
    for rung in run.ladder.rungs:
        if rung in selected:
            status, reason = "ran", None
        else:
            found = next((entry for entry in skipped if entry["rung"] == rung.name), None)
            if found is None:
                continue
            status, reason = "skipped", found["reason"]
        made = sum(row["calls_made"] for row in rows if row["rung"] == rung.name)
        failures = dict(sorted(run.failures.get(rung.name, {}).items()))
        rungs_out.append(
            {
                **rung.describe(),
                "status": status,
                "reason": reason,
                "calls_made": run.calls_by_rung.get(rung.name, 0) if status == "ran" else 0,
                "main_calls": made,
                "failed_calls": sum(failures.values()),
                "failures": failures,
                "suite_score": suite.get(rung.name),
            }
        )
    return {
        "rungs": rungs_out,
        "skipped": skipped,
        "sets": sets_out,
        "ifbench": "not available: IFBench is not one of the eval sets; IFEval stands in",
        "runs": rows,
        "gates": {"a": gate_a, "b": gate_b, "c": gate_c, "d": gate_d, "e": gate_e},
        "calls_made": run.cap.made,
    }


def _noise(run: _Context, selected: Sequence[Rung], gated: Sequence[str], values: Mapping[tuple[str, str], list[float | None]]) -> dict[str, Any]:
    """Gate b: the noise rung's main answers are repeat 0; its first ``items`` items are asked ``k - 1`` more times.

    Under the "all" rule the items are taken over every set and the gate reads the gated sets' rows (every set's
    when all are saturated); under the "gated" rule they are taken over the gated sets only, after scoring, so no
    repeat is spent on a set the gate drops. Either way the calls stay within :func:`plan`'s noise budget.
    """

    ladder = run.ladder
    rung = _helper_rung(ladder, ladder.noise["rung"], selected)
    if rung is None or not run.items:
        return {"status": "not_validated", "reason": "no rung was reachable", "sd": None, "flip_rate": None, "n": 0}
    pool = list(gated) if ladder.rules["noise_sets"] == "gated" else list(run.items)
    if not pool:
        return {
            "status": "not_validated",
            "reason": "every set is saturated",
            "sd": None,
            "flip_rate": None,
            "n": 0,
            "rung": rung.name,
            "items_planned": ladder.noise["items"],
            "items_available": 0,
        }
    taken = allocate({name: len(run.items[name]) for name in pool}, ladder.noise["items"])
    sets = [name for name in pool if taken[name]]
    digest = sha256_hex("\n".join(f"{name}:{run.items[name][0].sha256}" for name in sets).encode())
    dataset = records.Dataset("base-noise", run.source, dataset_digest(digest), {"base": True, "items": taken}, ds.SEED)
    record = run.run_record(None, f"base:{rung.name}:noise", k=ladder.noise["k"], dataset=dataset)
    records.write_run(run.connection, record)
    before = run.cap.made
    rows = []
    for name in sets:
        for item, first in zip(run.items[name][: taken[name]], values[(rung.name, name)], strict=False):
            again = [run.trace_and_score(record.run_id, rung, name, item, item.prompt, repeat=r) for r in range(1, ladder.noise["k"])]
            rows.append((name, [first, *again]))
    records.finish_run(run.connection, record.run_id, run.cap.made - before)
    keep = set(gated) or set(sets)
    result = noise_gate(
        [(name, repeats) for name, repeats in rows if name in keep],
        max_sd_points=ladder.noise["max_sd_points"],
        max_flip_rate=ladder.noise["max_flip_rate"],
    )
    return {
        **noise_rule(result, rung, deterministic=run.deterministic),
        "rung": rung.name,
        "run_id": record.run_id,
        "repeat_0": "the rung's main runs",
        "items_planned": ladder.noise["items"],
        "items_available": sum(len(run.items[name]) for name in pool),
    }


def _canaries(
    run: _Context,
    selected: Sequence[Rung],
    values: Mapping[tuple[str, str], list[float | None]],
    main_runs: Mapping[tuple[str, str], str],
) -> list[dict[str, Any]]:
    """Gate c: wrong_label re-scores the main answers; the calling canaries re-ask their ``items`` with altered prompts."""

    ladder = run.ladder
    rung = _helper_rung(ladder, ladder.canaries["rung"], selected)
    taken = canary_items(ladder, {set_name: len(items) for set_name, items in run.items.items()})
    out = []
    for name in CANARIES:
        config = ladder.canaries[name]
        sets = [set_name for set_name in config["sets"] if set_name in run.items and taken.get(name, {}).get(set_name, 1)]
        if rung is None or not sets:
            out.append({"canary": name, "sets": sets, "drop": None, "p": None, "status": "not_validated", "reason": f"canary {name}: no rung or set to run on"})
            continue
        base: list[float] = []
        sabotaged: list[float] = []
        run_id = None
        if name == "wrong_label":
            for set_name in sets:
                traces = run.connection.execute(
                    "SELECT trace_id, item_id, output_raw_text FROM traces WHERE run_id = ? AND outcome = 'succeeded' ORDER BY trace_id",
                    (main_runs[(rung.name, set_name)],),
                ).fetchall()
                by_id = {item.id: item for item in run.items[set_name]}
                for trace_id, item_id, text in traces:
                    item = by_id.get(item_id)
                    if item is None:
                        continue
                    before = score(set_name, item.expected, text)
                    after = score(set_name, wrong_label(set_name, item.expected), text)
                    if before is None or after is None:
                        continue
                    records.write_score(
                        run.connection,
                        records.ScoreRecord(records.new_id("score"), trace_id, f"base:canary:wrong_label:{set_name}", SCORER_VERSION, after),
                    )
                    base.append(before)
                    sabotaged.append(after)
        else:
            dataset = records.Dataset(
                f"base-canary-{name.replace('_', '-')}",
                run.source,
                dataset_digest(sha256_hex("\n".join(f"{s}:{run.items[s][0].sha256}" for s in sets).encode())),
                {"base": True, "canary": name, "sets": sets, "items": taken[name]},
                ds.SEED,
            )
            record = run.run_record(None, f"base:{rung.name}:canary:{name}", dataset=dataset)
            records.write_run(run.connection, record)
            before_calls = run.cap.made
            for set_name in sets:
                for item, original in zip(run.items[set_name][: taken[name][set_name]], values[(rung.name, set_name)], strict=False):
                    prompt = broken_prompt(item.prompt, item.id) if name == "broken_prompt" else truncated_context(item.prompt, config["keep"])
                    after = run.trace_and_score(record.run_id, rung, set_name, item, prompt)
                    if original is None or after is None:
                        continue
                    base.append(original)
                    sabotaged.append(after)
            records.finish_run(run.connection, record.run_id, run.cap.made - before_calls)
            run_id = record.run_id
        verdict = canary_verdict(name, base, sabotaged, min_drop=config["min_drop"], max_p=ladder.canaries["max_p"])
        out.append({**verdict, "sets": sets, "rung": rung.name, "run_id": run_id})
    return out


def replay_verdict(stored: Sequence[float], replayed: Sequence[float], calls: int, *, max_abs_delta: float) -> dict[str, Any]:
    """Gate e's verdict: pass only when the replay made no call and its mean moved less than ``max_abs_delta``.

    It re-scores the same text with the same scorer, so it can catch a nondeterministic scorer or traces lost in
    the copy, and nothing about the model or the data.
    """

    if not stored or not replayed:
        return {"max_abs_delta": None, "status": "not_validated", "reason": "the replayed run has no scored trace"}
    for value in (*stored, *replayed):
        if not math.isfinite(value):
            raise stats.NonFiniteScoreError("replay_gate needs finite scores")
    delta = abs(math.fsum(replayed) / len(replayed) - math.fsum(stored) / len(stored))
    if calls != 0:
        return {"max_abs_delta": delta, "status": "fail", "reason": f"the replay made {calls} call(s)"}
    return {"max_abs_delta": delta, "status": "pass" if delta < max_abs_delta else "fail"}


def replay_gate(
    connection: Any, source_run_id: str, items: Mapping[str, ds.Item], set_name: str, *, max_abs_delta: float, run: records.RunRecord, cap: CallCap
) -> dict[str, Any]:
    """Gate e: copy ``source_run_id``'s traces into ``run`` with no call, score them again, compare the means.

    The source mean is read from its stored score rows, the replay mean from re-scoring the copied outputs; the
    replay's ``calls_made`` row is what ``cap`` counted while it ran.
    """

    stored = [
        value
        for (value,) in connection.execute(
            "SELECT s.value FROM scores s JOIN traces t ON t.trace_id = s.trace_id WHERE t.run_id = ? AND s.scorer_id = ?",
            (source_run_id, _scorer(set_name)),
        ).fetchall()
    ]
    before = cap.made
    copied = records.copy_traces(connection, source_run_id, run, _step(set_name), parse_json)
    replayed = []
    for trace_id, item_id, text in connection.execute(
        "SELECT trace_id, item_id, output_raw_text FROM traces WHERE run_id = ? AND outcome = 'succeeded'", (run.run_id,)
    ).fetchall():
        value = score(set_name, items[item_id].expected, text)
        if value is None:
            continue
        records.write_score(connection, records.ScoreRecord(records.new_id("score"), trace_id, _scorer(set_name), SCORER_VERSION, value))
        replayed.append(value)
    records.finish_run(connection, run.run_id, cap.made - before)
    calls = connection.execute("SELECT calls_made FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()[0]
    out: dict[str, Any] = {"source_run": source_run_id, "run_id": run.run_id, "traces": copied, "calls_made": calls, "max": max_abs_delta}
    return {**out, **replay_verdict(stored, replayed, calls, max_abs_delta=max_abs_delta)}


def _replay(run: _Context, main_runs: Mapping[tuple[str, str], str]) -> dict[str, Any]:
    if not main_runs:
        return {"status": "not_validated", "reason": "no run to replay", "max_abs_delta": None, "calls_made": 0}
    (rung_name, set_name), source = next(iter(main_runs.items()))
    record = run.run_record(set_name, f"base:{rung_name}:replay", replay_of=source)
    items = {item.id: item for item in run.items[set_name]}
    return replay_gate(run.connection, source, items, set_name, max_abs_delta=run.ladder.replay["max_abs_delta"], run=record, cap=run.cap)


# --- output -----------------------------------------------------------------------------------------------------


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def plan_lines(report: Mapping[str, Any]) -> list[str]:
    planned = report["plan"]
    sets = list(planned["rungs"][0]["sets"]) if planned["rungs"] else []
    lines = [f"plan: {report['tier']} tier, {len(planned['rungs'])} rung(s) x {len(sets)} set(s)"]
    for row in planned["rungs"]:
        per_set = ", ".join(f"{name} {n}" for name, n in row["sets"].items())
        lines.append(
            f"  {row['rung']}: {per_set}; noise {row['noise_calls']}; canaries {row['canary_calls']};"
            f" {row['calls']} calls ({row['model_calls']} model calls), ${row['dollars']:.2f}"
        )
    lines.append(f"total: {planned['model_calls']} model calls, ${planned['dollars']:.2f}")
    return lines


def _failed_note(rung: Mapping[str, Any]) -> str:
    """`` (3 failed: OllamaLocalAdapterError 3)`` for a rung whose calls failed; empty when none did."""

    if not rung.get("failed_calls"):
        return ""
    by_class = ", ".join(f"{name} {count}" for name, count in sorted(rung["failures"].items()))
    return f" ({rung['failed_calls']} failed: {by_class})"


_RULE_WORDS = {
    ("saturation", "all"): "saturation over all rungs",
    ("saturation", "model"): "saturation over model rungs",
    ("noise_sets", "all"): "noise from all sets",
    ("noise_sets", "gated"): "noise from gated sets",
    ("order_rungs", "all"): "gate a ranks all rungs",
    ("order_rungs", "model"): "gate a ranks model rungs",
}


def rules_text(rules: Mapping[str, str]) -> str:
    """``v1 rules``, or what each rule does: ``saturation over model rungs, noise from gated sets, ...``."""

    if dict(rules) == V1_RULES:
        return "v1 rules"
    return ", ".join(_RULE_WORDS[(key, rules[key])] for key in RULES)


def _unranked_note(a: Mapping[str, Any], *, reasons: bool) -> str:
    """``; unranked: deterministic (why)`` for gate a under the "model" order rule; empty when it ranks every rung."""

    if not a.get("unranked"):
        return ""
    names = ", ".join(f"{entry['rung']} ({entry['reason']})" if reasons else entry["rung"] for entry in a["unranked"])
    return f"; unranked: {names}"


def report_lines(report: Mapping[str, Any]) -> list[str]:
    gates = report["gates"]
    lines = [f"base run: {report['tier']} tier on the {report['source']} slices, ladder v{report['ladder_version']} {report['ladder_sha256'][:12]}"]
    lines.append(f"rules: {rules_text(report['rules'])}")
    lines.append("rungs (published rank, status, calls made, suite score):")
    for rung in report["rungs"]:
        if rung["status"] == "skipped":
            lines.append(f"  {rung['name']}: rank {rung['rank']}, skipped ({rung['reason']})")
        else:
            lines.append(
                f"  {rung['name']}: rank {rung['rank']}, ran, {rung['calls_made']} calls{_failed_note(rung)},"
                f" suite {_num(rung['suite_score'])}"
            )
    lines.append("sets (K-score, items, best score):")
    for entry in report["sets"]:
        flag = ", saturated: flagged, not gated" if entry["saturated"] else ""
        beside = f"; {IFEVAL_NOTE}; IFBench: not available" if entry["set"] == "ifeval" else ""
        lines.append(f"  {entry['set']}: K{entry['k_score']}, {entry['items']} items, best {_num(entry['best'])}{flag}{beside}")
    a, b, e = gates["a"], gates["b"], gates["e"]
    lines.append("gates:")
    lines.append(
        f"  a order: tau-b {_num(a['tau'], 3)} (min {a['min_tau']:g}): {a['status']}"
        + (f" ({a['reason']})" if a.get("reason") else "")
        + _unranked_note(a, reasons=True)
    )
    if b["status"] == "measured":
        lines.append(
            f"  b noise: sd {_num(b['sd'])} points, flip rate {_num(100 * b['flip_rate'], 1)}% on n={b['n']}: measured,"
            f" smallest tell-apart difference {_num(b['min_detectable_difference'])} points (limits {_noise_limits(b)}; {b['reason']})"
        )
        lines.append(f"    {b['min_detectable_difference_formula']}")
    else:
        lines.append(
            f"  b noise: sd {_num(b.get('sd'))} points, flip rate {_num(None if b.get('flip_rate') is None else 100 * b['flip_rate'], 1)}%"
            f" on n={b.get('n', 0)}: {b['status']}" + (f" ({b['reason']})" if b.get("reason") else "")
        )
    for canary in gates["c"]:
        lines.append(
            f"  c {canary['canary']}: drop {_num(canary.get('drop'))} points (min {canary.get('min_drop', 0):g}),"
            f" p {_num(canary.get('p'), 4)}: {canary['status']}"
        )
    lines.append(f"  d judge: {gates['d']['status']}")
    lines.append(f"  e replay: max |delta| {_num(e.get('max_abs_delta'), 6)}, {e.get('calls_made', 0)} calls: {e['status']}")
    lines.append(f"calls made: {report['calls_made']}")
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    """The base-run report as ``docs/evals/base-run.md`` lays it out, from the run's JSON."""

    gates = report["gates"]
    out = [
        f"# Base run: {report['gig']}",
        "",
        f"- Tier: {report['tier']}, {report['source']} slices{' (deterministic)' if report['deterministic'] else ''}",
        f"- Ladder: v{report['ladder_version']} {report['ladder_sha256'][:12]} (sha256 `{report['ladder_sha256']}`)",
        f"- Rules: {rules_text(report['rules'])}",
        f"- Calls made: {report['calls_made']}",
        "",
        "## Rungs",
        "",
        "| Rung | Adapter | Model | Price in/out ($/M) | Rank (source, date) | Knobs | Status | Calls | Suite |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for rung in report["rungs"]:
        knobs = ", ".join(rung["knobs"]) or (rung.get("knobs_note") or "none")
        status = rung["status"] if rung["status"] == "ran" else f"skipped: {rung['reason']}"
        out.append(
            f"| {rung['name']} | {rung['adapter']} | {rung['model_name']} | {rung['price_in']:g} / {rung['price_out']:g}"
            f" | {rung['rank']} ({rung['rank_source']}, {rung['rank_date']}) | {knobs} | {status} | {rung['calls_made']}{_failed_note(rung)}"
            f" | {_num(rung['suite_score'])} |"
        )
    out += ["", "## Sets", "", "| Set | K-score | Items | Best | Saturated |", "|---|---|---|---|---|"]
    for entry in report["sets"]:
        name = f"{entry['set']} ({IFEVAL_NOTE}; IFBench: not available)" if entry["set"] == "ifeval" else entry["set"]
        out.append(f"| {name} | {entry['k_score']} | {entry['items']} | {_num(entry['best'])} | {'yes, not gated' if entry['saturated'] else 'no'} |")
    out += ["", "## Runs", "", "| Rung | Set | Run | Items | Scored | Score | Calls |", "|---|---|---|---|---|---|---|"]
    for row in report["runs"]:
        out.append(f"| {row['rung']} | {row['set']} | `{row['run_id']}` | {row['items']} | {row['scored']} | {_num(row['score'])} | {row['calls_made']} |")
    a, b, e = gates["a"], gates["b"], gates["e"]
    out += [
        "",
        "## Gates",
        "",
        "| Gate | Measure | Status |",
        "|---|---|---|",
        f"| a order | tau-b {_num(a['tau'], 3)} (min {a['min_tau']:g}){_unranked_note(a, reasons=False)} | {a['status']}{': ' + a['reason'] if a.get('reason') else ''} |",
        _noise_row(b),
    ]
    for canary in gates["c"]:
        out.append(f"| c {canary['canary']} | drop {_num(canary.get('drop'))} points, p {_num(canary.get('p'), 4)} | {canary['status']} |")
    out.append(f"| d judge | {gates['d']['reason']} | {gates['d']['status']} |")
    out.append(f"| e replay | max abs delta {_num(e.get('max_abs_delta'), 6)}, {e.get('calls_made', 0)} calls | {e['status']} |")
    if b["status"] == "measured":
        out += [
            "",
            f"Gate b is measured, not pass/fail: {b['reason']}. Limits {_noise_limits(b)}."
            f" Smallest tell-apart difference: {b['min_detectable_difference_formula']}.",
        ]
    return "\n".join(out) + "\n"


def _noise_limits(b: Mapping[str, Any]) -> str:
    """``sd 2 points and flip rate 10%, over: sd, flip rate``: a measured gate b's limits; the over list is a fact."""

    limits, over = b["limits"], [name.replace("_", " ") for name, above in b["over_limits"].items() if above]
    return f"sd {limits['max_sd_points']:g} points and flip rate {100 * limits['max_flip_rate']:g}%, over: {', '.join(over) or 'none'}"


def _noise_row(b: Mapping[str, Any]) -> str:
    """Gate b's row in the markdown Gates table."""

    flip = _num(None if b.get("flip_rate") is None else 100 * b["flip_rate"], 1)
    if b["status"] == "measured":
        measure = f"sd {_num(b['sd'])} points, flip rate {flip}%, n={b['n']}, smallest tell-apart difference {_num(b['min_detectable_difference'])} points"
        return f"| b noise | {measure} | measured |"
    return f"| b noise | sd {_num(b.get('sd'))} points, flip rate {flip}%, n={b.get('n', 0)} | {b['status']} |"


@click.command("base")
@click.option("--rungs", "rungs", metavar="R,R...", help="Rungs to run (default: the whole ladder).")
@click.option("--tier", type=click.Choice(TIERS), default="gate", show_default=True, help="Which sets: gate, judge or full.")
@click.option("--max-calls", type=click.IntRange(min=0), help="Refuse before model call N+1 (exit 2).")
@click.option("--deterministic", is_flag=True, help="Answer every rung from the ci slices' canned completions; no call.")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
@click.option("--report", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Also write the report as Markdown.")
@click.option("--plan", "plan_only", is_flag=True, help="Print rungs x sets x calls and dollars; run nothing.")
@click.pass_context
def base_command(
    ctx: click.Context,
    rungs: str | None,
    tier: str,
    max_calls: int | None,
    deterministic: bool,
    as_json: bool,
    report_path: Path | None,
    plan_only: bool,
) -> None:
    """Run the shared eval sets on the model ladder and check the measurement (order, noise, canaries, replay)."""

    gig: GigContext = ctx.obj
    if plan_only and report_path is not None:
        raise click.UsageError("--plan runs nothing; drop --report")
    names = None if rungs is None else [name.strip() for name in rungs.split(",")]
    report = guard(
        gig,
        lambda: execute_base(
            gig,
            rung_names=names,
            tier=tier,
            max_calls=max_calls,
            deterministic=deterministic,
            plan_only=plan_only,
            report_path=report_path,
        ),
    )
    if as_json:
        click.echo(json.dumps(report, indent=2, sort_keys=True))
        return
    for line in plan_lines(report) if plan_only else report_lines(report):
        click.echo(line)
