"""``eval validate-judge``: does a ``step:<judge>`` scorer agree with people (or with a public gold set)?

A judge step grades another step's outputs. Before ``eval run`` trusts it, this verb asks the judge about outputs
whose verdict is known and records the agreement as ``judge_validations`` rows:

- ``--against <labels.jsonl>`` or ``--against labels``: the graded step's stored traces that people labelled
  (``eval labels`` export rows, or the store's own ``labels`` table). The judge gets exactly the prompt ``eval run``
  sends when scoring (:func:`contracts.judge_prompt`), so a passing row unlocks the scorer (:func:`run.check_judge`).
- ``--against judgebench`` / ``--against mtbench_human``: a public pairwise set with gold labels. The judge gets the
  set's own prompt, so this is a calibration report for the model (kappa, true/false positive rates, accuracy per
  source) and never unlocks the scorer; its row records the set's prompt digest.

``--swap`` doubles the judge calls: every unit with a known-better and known-worse output is asked in both orders
(through autoevals' Battle template for labelled traces, by reversing the A/B blocks for a public set), and
``swap_consistency`` is the share of pairs where the same content won in both orders (order invariance, whether or
not the judge was right). Labelled traces also record ``swap_pair_accuracy``: the share of pairs where the met
output won both times.

Pass rule (``details.pass``): kappa >= 0.6 over at least 126 units, and swap consistency >= 0.90 when measured. Fewer
than 126 units, a kappa the data cannot define (one category only) or ``--swap`` with no pair to swap is a clean
fail (exit 1), not a refusal; a row is written only for a metric that has a value.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module, resources
from pathlib import Path
from typing import Any

import click

from ..canonical import digest_imported_bytes
from ..gigs.eval_manifest import data_folder
from ..sdk.context import GigContext
from ..steps import StepTargetError
from . import contracts, records
from . import datasets as ds
from .label import LABELS, LabelRefused, validate_batch
from .run import RunRefused, _entry, _manifest_digest, _redacted, _targets, guard, load_suite
from .stats import cohen_kappa

#: The pass rule of a judge validation.
MIN_KAPPA = 0.6
MIN_UNITS = 126
MIN_SWAP = 0.90
RULE = f"kappa >= {MIN_KAPPA} and n >= {MIN_UNITS} and swap_consistency >= {MIN_SWAP} when measured"

#: Public pairwise sets: the gold key in ``expected``, the value meaning "A wins", the value meaning "B wins", the
#: key of the judge's JSON reply, and the two block markers ``--swap`` exchanges.
PUBLIC_SETS: dict[str, tuple[str, str, str, str, tuple[str, str]]] = {
    "judgebench": ("label", "A>B", "B>A", "verdict", ("RESPONSE A:", "RESPONSE B:")),
    "mtbench_human": ("winner", "A", "B", "winner", ("ASSISTANT A:", "ASSISTANT B:")),
}
#: The store's own labels, as opposed to a labels JSONL path.
STORE_LABELS = "labels"

_TEMPLATES = "gigai.evals._vendor.autoevals"
_OPENEVALS = "gigai.evals._vendor.openevals.prompts"
_JSON_OBJECT = re.compile(r"\{.*?\}", re.DOTALL)


# --- vendored templates ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    """One autoevals classifier template: the mustache-style prompt and the score of each allowed answer."""

    name: str
    prompt: str
    choice_scores: Mapping[str, float]


def load_template(name: str) -> Template:
    """The vendored autoevals template ``name`` (``battle``, ``factuality``, ...), parsed without PyYAML.

    Every template is ``prompt: |-`` (a block scalar) followed by ``choice_scores:`` (quoted keys to numbers);
    anything else is a :class:`ValueError`, so a template edit cannot be misread silently.
    """

    try:
        text = resources.files(_TEMPLATES).joinpath(f"templates/{name}.yaml").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"no vendored autoevals template is named {name!r}") from None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "prompt: |-":
        raise ValueError(f"template {name}: expected 'prompt: |-' on line 1")
    body: list[str] = []
    index = 1
    while index < len(lines) and (lines[index].startswith("  ") or not lines[index].strip()):
        body.append(lines[index][2:])
        index += 1
    if index >= len(lines) or lines[index].strip() != "choice_scores:":
        raise ValueError(f"template {name}: expected 'choice_scores:' after the prompt block")
    scores: dict[str, float] = {}
    for line in lines[index + 1 :]:
        if not line.strip():
            continue
        key, separator, value = line.strip().partition(":")
        if not separator or len(key) < 2 or key[0] != '"' or key[-1] != '"':
            raise ValueError(f"template {name}: cannot read choice line {line.strip()!r}")
        scores[key[1:-1]] = float(value.strip())
    if not scores:
        raise ValueError(f"template {name}: no choice_scores")
    return Template(name, "\n".join(body).rstrip("\n"), scores)


def render_template(template: Template, **fields: str) -> str:
    """``template``'s prompt with every ``{{name}}`` / ``{{{name}}}`` filled from ``fields``, then the answers it
    accepts; a placeholder ``fields`` does not fill is a :class:`ValueError`."""

    text = template.prompt
    for name, value in fields.items():
        text = text.replace("{{{" + name + "}}}", value).replace("{{" + name + "}}", value)
    unfilled = re.findall(r"\{\{\{?([a-zA-Z_]+)\}?\}\}", text)
    if unfilled:
        raise ValueError(f"template {template.name}: no value for {', '.join(sorted(set(unfilled)))}")
    return f"{text}\n\nAnswer with exactly one of: {', '.join(template.choice_scores)}."


def template_choice(template: Template, reply: str) -> str | None:
    """The answer the reply's first non-empty line names (case-insensitive, punctuation ignored); ``None`` when it
    names none of ``template.choice_scores``."""

    for line in reply.splitlines():
        word = line.strip().strip("\"'.()[]*:").strip()
        if not word:
            continue
        lowered = word.lower()
        return next((choice for choice in template.choice_scores if choice.lower() == lowered), None)
    return None


def openevals_prompt(name: str, **fields: str) -> str:
    """The vendored openevals prompt ``hallucination`` or ``correctness``, with its ``{inputs}``, ``{outputs}``,
    ``{reference_outputs}`` (and ``{context}``) placeholders filled; a missing one is a :class:`ValueError`."""

    constants = {"hallucination": "HALLUCINATION_PROMPT", "correctness": "CORRECTNESS_PROMPT"}
    if name not in constants:
        raise ValueError(f"no vendored openevals prompt is named {name!r}")
    text: str = getattr(import_module(f"{_OPENEVALS}.{name}"), constants[name])
    try:
        return text.format_map(fields)
    except KeyError as exc:
        raise ValueError(f"prompt {name}: no value for {exc.args[0]}") from None


# --- agreement -------------------------------------------------------------------------------------------------


def agreement(pairs: Sequence[tuple[int, int]]) -> dict[str, float | int | None]:
    """Kappa with its 95 % interval and the judge's true positive / true negative rates over ``(human, judge)`` pairs
    of 0/1 verdicts; ``None`` for a rate without cases and for kappa when both raters used one category only."""

    table = [[0, 0], [0, 0]]
    for human, judge in pairs:
        table[human][judge] += 1
    positives = table[1][0] + table[1][1]
    negatives = table[0][0] + table[0][1]
    report: dict[str, float | int | None] = {
        "n": len(pairs),
        "kappa": None,
        "ci_low": None,
        "ci_high": None,
        "observed_agreement": (table[0][0] + table[1][1]) / len(pairs) if pairs else None,
        "tpr": table[1][1] / positives if positives else None,
        "tnr": table[0][0] / negatives if negatives else None,
    }
    try:
        stats = cohen_kappa(table)
    except ValueError:
        return report
    report.update(kappa=stats["kappa"], ci_low=stats["lower"], ci_high=stats["upper"])
    return report


def passes(kappa: float | None, n: int, swap_consistency: float | None, swap_pairs: int | None = None) -> bool:
    """The pass rule: ``kappa >= 0.6`` over ``n >= 126`` units and, when measured, ``swap_consistency >= 0.90``."""

    return not failures(kappa, n, swap_consistency, swap_pairs)


def failures(kappa: float | None, n: int, swap_consistency: float | None, swap_pairs: int | None = None) -> list[str]:
    """Why the rule is not met; empty when it is. ``kappa`` ``None`` is undefined (one category only); ``swap_pairs``
    ``0`` means ``--swap`` was asked for but no pair could be swapped."""

    reasons: list[str] = []
    if n < MIN_UNITS:
        reasons.append(f"n < {MIN_UNITS}")
    if kappa is None:
        reasons.append("kappa undefined")
    elif kappa < MIN_KAPPA:
        reasons.append(f"kappa < {MIN_KAPPA}")
    if swap_pairs == 0:
        reasons.append("no swap pairs")
    elif swap_consistency is not None and swap_consistency < MIN_SWAP:
        reasons.append(f"swap_consistency < {MIN_SWAP}")
    return reasons


def swap_blocks(prompt: str, markers: tuple[str, str]) -> str:
    """``prompt`` with the text after ``markers[0]`` and after ``markers[1]`` exchanged (``A:\\n<a>\\n\\nB:\\n<b>``
    becomes ``A:\\n<b>\\n\\nB:\\n<a>``). Each marker is a paragraph of its own (``\\n\\n<marker>\\n``); block A
    starts at the first such paragraph and block B at the last, so a response that quotes a marker inside its own
    text does not move the split. A prompt without both is a :class:`ValueError`."""

    first, second = (f"\n\n{marker}\n" for marker in markers)
    start = prompt.find(first)
    middle = prompt.rfind(second)
    if start < 0 or middle < start + len(first):
        raise ValueError(f"prompt has no {markers[0]!r} ... {markers[1]!r} blocks to swap")
    block_a = prompt[start + len(first) : middle]
    block_b = prompt[middle + len(second) :]
    return f"{prompt[:start]}{first}{block_b}{second}{block_a}"


def json_field(reply: str, key: str) -> Any:
    """``key`` of the first JSON object in ``reply``; ``None`` without one."""

    for match in _JSON_OBJECT.finditer(reply):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value.get(key)
    return None


# --- units -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    """One output with a known verdict: ``human`` is 1 for better/met and 0 for worse/not met."""

    item_id: str
    version: str
    cluster_id: str | None
    input: str
    output: str
    human: int
    reference: Any = None
    run_id: str | None = None
    source: str = "all"


@dataclass
class LabelSet:
    """What the validation ran against and what it found while reading it."""

    name: str
    sha256: str
    kind: str  # "labels" or "public"
    units: list[Unit]
    labels: int = 0
    skipped_unknown: int = 0
    contested: int = 0
    prompt_sha256: str = contracts.JUDGE_PROMPT_SHA256
    markers: tuple[str, str] | None = None
    verdict_key: str | None = None
    a_value: str | None = None
    b_value: str | None = None
    canned: dict[str, tuple[str, str]] = field(default_factory=dict)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _label_rows(gig: GigContext, connection: sqlite3.Connection, graded: str, against: str) -> tuple[str, str, list[dict[str, Any]]]:
    """``(name, sha256, rows)`` of the labels: a JSONL file, or the store's own labels of step ``graded``."""

    if against == STORE_LABELS:
        rows = [
            {"run_id": run_id, "item_id": item_id, "label": json.loads(label)}
            for run_id, item_id, label in connection.execute(
                "SELECT run_id, item_id, label FROM labels WHERE step_id = ? AND run_id IS NOT NULL ORDER BY run_id, item_id, who, at, round",
                (graded,),
            )
        ]
        digest = digest_imported_bytes(json.dumps(rows, sort_keys=True).encode("utf-8"))
        return STORE_LABELS, digest, rows
    path = Path(against)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RunRefused(f"cannot read labels {path}: {exc.strerror or exc}") from exc
    try:
        parsed = validate_batch(text, graded)
    except LabelRefused as exc:
        raise RunRefused(f"labels {path.name}: {exc}") from exc
    rows = [{"run_id": row.run_id, "item_id": row.item_id, "label": row.label} for row in parsed]
    return path.name, digest_imported_bytes(text.encode("utf-8")), rows


def _labelled_units(gig: GigContext, connection: sqlite3.Connection, graded_entry: Mapping[str, Any], against: str) -> LabelSet:
    """The graded step's labelled traces as units: each trace's verdict is the majority of its met / not met labels."""

    graded = str(graded_entry["id"])
    name, digest, rows = _label_rows(gig, connection, graded, against)
    votes: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        votes[(str(row["run_id"]), str(row["item_id"]))][str(row["label"])] += 1
    label_set = LabelSet(name, digest, "labels", [], labels=len(rows))
    suites: dict[str, dict[str, dict[str, Any]]] = {}
    for (run_id, item_id), counter in sorted(votes.items()):
        met, not_met = counter[LABELS[0]], counter[LABELS[1]]
        label_set.skipped_unknown += counter[LABELS[2]]
        if met == not_met:
            label_set.contested += int(met > 0)
            continue
        trace = connection.execute(
            "SELECT input, output_raw_text, item_version, cluster_id FROM traces "
            "WHERE run_id = ? AND step_id = ? AND item_id = ? AND outcome = 'succeeded' ORDER BY repeat_index, attempt_index LIMIT 1",
            (run_id, graded, item_id),
        ).fetchone()
        if trace is None:
            raise RunRefused(f"label for run {run_id} item {item_id} step {graded}: no succeeded trace is stored")
        if run_id not in suites:
            suite = connection.execute("SELECT suite FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if suite is None or suite[0] is None:
                raise RunRefused(f"run {run_id} ran no suite dataset, so its items have no reference to grade against")
            suites[run_id] = load_suite(gig, graded_entry, str(suite[0])).rows
        row = suites[run_id].get(item_id)
        if row is None:
            raise RunRefused(f"run {run_id} item {item_id} is not in the suite dataset the run used")
        label_set.units.append(
            Unit(item_id, str(trace[2]), trace[3], str(trace[0]), str(trace[1] or ""), int(met > not_met), row.get("expected"), run_id)
        )
    return label_set


def _public_units(gig: GigContext, set_name: str, deterministic: bool) -> LabelSet:
    """A public pairwise set as units; the judge is asked the set's own prompt. In a source checkout the committed
    ``ci`` slice is read, else the release slice downloaded to the gig folder."""

    gold_key, a_value, b_value, verdict_key, markers = PUBLIC_SETS[set_name]
    ci_path = ds.CI_DIR / f"{set_name}.jsonl"
    if ci_path.is_file():
        items, where = ds.load_ci(set_name), f"{set_name} ci slice"
    else:
        folder = data_folder(gig.home, gig.name)
        release = (folder / "datasets" / f"{set_name}-release.jsonl") if folder is not None else None
        if release is None or not release.is_file():
            raise RunRefused(
                f"no {set_name} slice is available: committed ci slices ship only in a gigai source checkout, and the "
                f"release slice is not at {release} (gigai <gig> eval datasets download {set_name})"
            )
        items, where = ds.read_slice(release), f"{set_name} release slice"
    if not items:
        raise RunRefused(f"the {where} is empty")
    label_set = LabelSet(
        where,
        f"sha256:{items[0].sha256}",
        "public",
        [],
        labels=len(items),
        prompt_sha256=digest_imported_bytes(getattr(ds.loader(set_name), "ASK", "").encode("utf-8")),
        markers=markers,
        verdict_key=verdict_key,
        a_value=a_value,
        b_value=b_value,
    )
    for item in items:
        gold = item.expected.get(gold_key) if isinstance(item.expected, Mapping) else None
        if gold not in (a_value, b_value):
            raise RunRefused(f"{where} item {item.id} has no {gold_key} of {a_value} or {b_value}")
        source = str(item.source_ids.get("source") or (item.tags[0] if item.tags else "all"))
        label_set.units.append(Unit(item.id, "1", item.cluster_id, item.prompt, "", int(gold == a_value), source=source))
        if deterministic:
            label_set.canned[item.id] = (item.prompt, item.completion or "")
    return label_set


# --- the validation ---------------------------------------------------------------------------------------------


class _Judge:
    """Asks the judge step, tracing every reply under the validation run; a deterministic target answering a public
    set replies from the slice's canned completion with no call."""

    def __init__(self, gig: GigContext, step_id: str, run: records.RunRecord, canned: Mapping[str, tuple[str, str]]) -> None:
        self.gig, self.step_id, self.run, self.canned = gig, step_id, run, canned
        self.calls = 0
        self._repeats: Counter[str] = Counter()

    def ask(self, prompt: str, unit: Unit) -> str:
        canned = self.canned.get(unit.item_id)
        if canned is not None:
            return canned[1] if prompt == canned[0] else ""
        item = records.ItemRef(unit.item_id, unit.version, unit.cluster_id)
        repeat = self._repeats[unit.item_id]
        self._repeats[unit.item_id] += 1
        try:
            result = self.gig.model(self.step_id, prompt, run=self.run, item=item, repeat_index=repeat)
        except (StepTargetError, KeyError) as exc:
            raise RunRefused(f"judge step {self.step_id!r}: {_redacted(exc, prompt, unit.output, unit.input)}") from exc
        except Exception as exc:
            raise records.EvalsError(f"judge call for item {unit.item_id} failed: {_redacted(exc, prompt, unit.output, unit.input)}") from exc
        self.calls += 1
        return result.output_text


def _verdict(label_set: LabelSet, reply: str) -> int | None:
    """The judge's 0/1 verdict in ``reply`` (``None`` when it gave none): CORRECT for labelled traces, "A wins" for
    a public pair."""

    if label_set.kind == "labels":
        value = contracts.judge_score(reply).value
        return None if value == contracts.NOANSWER else int(value == contracts.CORRECT)
    value = json_field(reply, str(label_set.verdict_key))
    return None if value not in (label_set.a_value, label_set.b_value) else int(value == label_set.a_value)


def _swap_labelled(judge: _Judge, label_set: LabelSet) -> tuple[int, int, int]:
    """``(consistent, accurate, pairs)`` over per-item pairs of a met-labelled and a not-met-labelled trace, each
    asked through the Battle template in both orders: consistent when the same output won both times (Yes then No,
    or No then Yes), accurate when that output was the met one."""

    battle = load_template("battle")
    by_item: dict[str, tuple[list[Unit], list[Unit]]] = defaultdict(lambda: ([], []))
    for unit in label_set.units:
        by_item[unit.item_id][unit.human].append(unit)
    consistent = accurate = pairs = 0
    for item_id in sorted(by_item):
        worse, better = by_item[item_id]
        for good, bad in zip(sorted(better, key=lambda u: u.run_id or ""), sorted(worse, key=lambda u: u.run_id or ""), strict=False):
            pairs += 1
            first = template_choice(battle, judge.ask(render_template(battle, instructions=good.input, output=good.output, expected=bad.output), good))
            second = template_choice(battle, judge.ask(render_template(battle, instructions=good.input, output=bad.output, expected=good.output), bad))
            consistent += int((first, second) in (("Yes", "No"), ("No", "Yes")))
            accurate += int((first, second) == ("Yes", "No"))
    return consistent, accurate, pairs


def _swap_public(judge: _Judge, label_set: LabelSet, verdicts: Mapping[str, int | None]) -> tuple[int, int]:
    """``(consistent, pairs)``: every item asked again with its A and B blocks exchanged; consistent when the same
    response won both times (the verdict flipped with the blocks)."""

    consistent = pairs = 0
    for unit in label_set.units:
        pairs += 1
        swapped = _verdict(label_set, judge.ask(swap_blocks(unit.input, label_set.markers or ("", "")), unit))
        original = verdicts.get(unit.item_id)
        consistent += int(original is not None and swapped is not None and original != swapped)
    return consistent, pairs


def validate(gig: GigContext, step_id: str, against: str, swap: bool) -> dict[str, Any]:
    """Run the validation and record it; the report ``eval validate-judge`` prints (``pass`` says whether the rule
    was met). Refusals (:class:`RunRefused`) name what is wrong before any judge call."""

    entry = _entry(gig, step_id)
    graded = entry.get("grades")
    if not isinstance(graded, str) or not graded:
        raise RunRefused(f"step {step_id!r} grades no step ([[evals.steps]] grades), so it is not a judge")
    if not isinstance(entry.get("validation"), Mapping):
        raise RunRefused(f"step {step_id!r} declares no validation table ([[evals.steps]] validation)")
    graded_entry = _entry(gig, graded)
    target = _targets(gig, [step_id])[step_id]
    if against not in PUBLIC_SETS and against != STORE_LABELS and not Path(against).is_file():
        known = ", ".join([STORE_LABELS, *PUBLIC_SETS])
        raise RunRefused(f"--against must be a labels .jsonl file or one of {known}; {against!r} is neither")
    connection = records.open_evals_db(gig.name, gig.home)
    try:
        if against in PUBLIC_SETS:
            label_set = _public_units(gig, against, target.adapter == "deterministic")
        else:
            label_set = _labelled_units(gig, connection, graded_entry, against)
        if not label_set.units:
            raise RunRefused(f"{label_set.name} holds no unit with a met or not met verdict for step {graded!r}")
        run = records.RunRecord(
            run_id=records.new_id("run"),
            gig=gig.name,
            manifest_digest=_manifest_digest(gig),
            dataset=records.Dataset(label_set.name, "1", label_set.sha256, {"validate-judge": against, "count": len(label_set.units)}),
            step_id=step_id,
            tag=f"validate-judge:{against}",
            k=1,
        )
        records.write_run(connection, run)
        judge = _Judge(gig, step_id, run, label_set.canned)
        pairs: list[tuple[int, int]] = []
        verdicts: dict[str, int | None] = {}
        unparsed = 0
        for unit in label_set.units:
            prompt = contracts.judge_prompt(unit.input, unit.output, unit.reference) if label_set.kind == "labels" else unit.input
            verdict = _verdict(label_set, judge.ask(prompt, unit))
            verdicts[unit.item_id] = verdict
            unparsed += int(verdict is None)
            pairs.append((unit.human, verdict or 0))
        report = agreement(pairs)
        swap_consistency = swap_pair_accuracy = swap_pairs = None
        if swap:
            if label_set.kind == "labels":
                consistent, accurate, swap_pairs = _swap_labelled(judge, label_set)
                swap_pair_accuracy = accurate / swap_pairs if swap_pairs else None
            else:
                consistent, swap_pairs = _swap_public(judge, label_set, verdicts)
            swap_consistency = consistent / swap_pairs if swap_pairs else None
        records.finish_run(connection, run.run_id, judge.calls)
        kappa = report["kappa"]
        reasons = failures(None if kappa is None else float(kappa), len(pairs), swap_consistency, swap_pairs)
        details = {
            **report,
            "swap_consistency": swap_consistency,
            "swap_pair_accuracy": swap_pair_accuracy,
            "swap_pairs": swap_pairs,
            "pass": not reasons,
            "reasons": reasons,
            "rule": RULE,
            "label_set": {"name": label_set.name, "sha256": label_set.sha256, "kind": label_set.kind},
            "labels": label_set.labels,
            "skipped_unknown": label_set.skipped_unknown,
            "contested": label_set.contested,
            "unparsed": unparsed,
            "sources": _by_source(label_set.units, pairs),
            "unlocks_scorer": label_set.kind == "labels",
            "run_id": run.run_id,
            "calls": judge.calls,
        }
        validation_id = f"validation_{uuid.uuid4()}"
        recorded_at = _utc_now()
        rows = [(metric, value) for metric, value in (("kappa", kappa), ("swap_consistency", swap_consistency)) if value is not None]
        with records._transaction(connection):
            connection.executemany(
                "INSERT INTO judge_validations (validation_id, scorer_id, scorer_version, judge_model, judge_prompt_sha256, dataset_id, "
                "dataset_sha256, n_items, metric, value, details, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        f"{validation_id}:{metric}",
                        f"step:{step_id}",
                        contracts.LIFTED_VERSION,
                        target.id,
                        label_set.prompt_sha256,
                        label_set.name,
                        label_set.sha256,
                        len(pairs),
                        metric,
                        value,
                        json.dumps(details, sort_keys=True),
                        recorded_at,
                    )
                    for metric, value in rows
                ],
            )
        details["recorded"] = [metric for metric, _ in rows]
    finally:
        connection.close()
    return {"step": step_id, "grades": graded, "judge_model": target.id, "against": against, "validation_id": validation_id, "recorded_at": recorded_at, **details}


def _by_source(units: Sequence[Unit], pairs: Sequence[tuple[int, int]]) -> dict[str, dict[str, int | float]] | None:
    """Accuracy per source of a public set; ``None`` when every unit has the same source."""

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for unit, (human, judge) in zip(units, pairs, strict=True):
        totals[unit.source][0] += int(human == judge)
        totals[unit.source][1] += 1
    if len(totals) < 2:
        return None
    return {source: {"agree": agree, "n": n, "accuracy": agree / n} for source, (agree, n) in sorted(totals.items())}


def _lines(report: Mapping[str, Any]) -> list[str]:
    def number(value: Any, digits: int = 3) -> str:
        return "n/a" if value is None else f"{value:.{digits}f}"

    lines = [
        f"validate-judge: step {report['step']} ({report['judge_model']}) against {report['label_set']['name']}: "
        f"{report['n']} units from {report['labels']} labels ({report['skipped_unknown']} unknown, {report['contested']} contested), "
        f"{report['unparsed']} unparsed, {report['calls']} calls",
        f"kappa {number(report['kappa'])} [{number(report['ci_low'])}, {number(report['ci_high'])}]  tpr {number(report['tpr'])}  tnr {number(report['tnr'])}",
    ]
    if report["swap_pairs"] is not None:
        accuracy = "" if report["swap_pair_accuracy"] is None else f"  pair accuracy {number(report['swap_pair_accuracy'])}"
        lines.append(f"swap consistency {number(report['swap_consistency'])} over {report['swap_pairs']} pairs{accuracy}")
    lines.append("recorded: " + (", ".join(report["recorded"]) if report["recorded"] else "nothing (no metric has a value)"))
    if report["sources"]:
        lines.append("accuracy by source: " + ", ".join(f"{s} {v['accuracy']:.2f} ({v['agree']}/{v['n']})" for s, v in report["sources"].items()))
    if not report["unlocks_scorer"]:
        lines.append(f"note: a public set validates the model, not the scoring prompt; step:{report['step']} scoring still needs a labels validation")
    verdict = "yes" if report["pass"] else "no (" + ", ".join(report["reasons"]) + ")"
    lines.append(f"pass: {verdict}  rule: {report['rule']}")
    return lines


@click.command("validate-judge")
@click.option("--step", "step_id", required=True, help="The judge [[evals.steps]] entry (one with grades = <step>).")
@click.option("--against", required=True, metavar="LABELS", help="A labels .jsonl, 'labels' (the store's own), or a public set: judgebench, mtbench_human.")
@click.option("--swap", is_flag=True, help="Also ask every pair in both orders and record swap_consistency (doubles the calls).")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
@click.pass_context
def validate_judge_command(ctx: click.Context, step_id: str, against: str, swap: bool, as_json: bool) -> None:
    """Measure a judge step's agreement with labels and record it; exit 1 when the pass rule is not met."""

    gig: GigContext = ctx.obj
    report: dict[str, Any] = {}

    def action() -> list[str]:
        report.update(validate(gig, step_id, against, swap))
        return [json.dumps(report, sort_keys=True)] if as_json else _lines(report)

    for line in guard(gig, action):
        click.echo(line)
    if not report["pass"]:
        raise click.exceptions.Exit(1)


__all__ = ["MIN_KAPPA", "MIN_SWAP", "MIN_UNITS", "PUBLIC_SETS", "RULE", "Template", "Unit", "agreement", "failures", "json_field", "load_template", "openevals_prompt", "passes", "render_template", "swap_blocks", "template_choice", "validate", "validate_judge_command"]
