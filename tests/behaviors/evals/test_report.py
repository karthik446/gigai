"""``gigai <gig> eval report``: per-run and per-slice means with their cluster-bootstrap interval, the gate verdict of the
run's step and suite, label counts, and a ``--json`` feed with no item text unless ``--include-inputs``.

Runs are written straight to the eval store (no model call) with a prompt and an output longer than the feed's
200-character cap, so a leak of either shows up as a long string, an ``input`` key or a fragment of the text.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from gigai.evals import gates, records, report, stats
from gigai.sdk import GigContext
from tests.behaviors.evals.test_compare import ACC_A, ACC_B, CLUSTERS, ITEMS, MARKER, MATCH, PREREG, SCORE_A, SCORE_B, SHA, model_call
from tests.behaviors.evals.test_run import GIG, PACKAGE, calls, gig, home, shares_a_fragment  # noqa: F401 - fixtures
from tests.behaviors.gigs.test_loader import _add_fixture, _run

#: Each trace's prompt and output: longer than ``report.MAX_FEED_STRING``, and the text that must never leak.
LONG_INPUT = "Private question: " + "how many apples does the operator keep in the second drawer? " * 5
LONG_OUTPUT = "Private answer: " + "the operator keeps seventeen apples in that drawer, said the model. " * 5
ROWS = {json.loads(line)["id"]: json.loads(line) for line in (PACKAGE / "release.jsonl").read_text(encoding="utf-8").splitlines()}
_STAMP = iter(range(10_000))
assert len(LONG_INPUT) > 300 and len(LONG_OUTPUT) > 300


def record_run(
    home: Path,
    scores: dict[str, dict[str, list[float]]],
    *,
    clusters: dict[str, str | None] | None = None,
    step: str = "solve",
    suite: str | None = "release",
    model: str = "deterministic-a",
    calls_made: int | None = 12,
) -> str:
    """A finished run whose scorer ``s`` gave item ``i`` repeat ``r`` the value ``scores[s][i][r]``; every trace holds
    ``LONG_INPUT`` / ``LONG_OUTPUT`` followed by its item id."""

    clusters = CLUSTERS if clusters is None else clusters
    stamp = next(_STAMP)
    run_id = f"run_{uuid.uuid4()}"
    k = max((len(repeats) for by_item in scores.values() for repeats in by_item.values()), default=1)
    created = f"2026-03-01T00:{stamp // 60:02d}:{stamp % 60:02d}.000000Z"
    connection = records.open_evals_db(GIG, home)
    try:
        dataset = records.Dataset("release.jsonl", "1", SHA, {"count": 12})
        records.write_run(
            connection, records.RunRecord(run_id, GIG, "sha256:m", dataset, PREREG if suite else None, step if suite else None, suite, None, k, created_at=created)
        )
        for item in sorted({item for by_item in scores.values() for item in by_item}):
            for repeat in range(k):
                trace = records.TraceRecord(
                    f"trace_{uuid.uuid4()}", run_id, step, records.ItemRef(item, "1", clusters[item]),
                    f"{LONG_INPUT}{item}", f"{LONG_OUTPUT}{item}", repeat, model=model_call(model), recorded_at=created,
                )
                records.write_trace(connection, trace)
                for scorer, by_item in scores.items():
                    if item in by_item and repeat < len(by_item[item]):
                        score = records.ScoreRecord(f"score_{uuid.uuid4()}", trace.trace_id, scorer, "1", by_item[item][repeat], recorded_at=created)
                        records.write_score(connection, score)
        if calls_made is not None:
            records.finish_run(connection, run_id, calls_made)
    finally:
        connection.close()
    return run_id


def invoke(gig: GigContext, *argv: str) -> Result:
    return CliRunner().invoke(report.report_command, list(argv), obj=gig)


def strings(value: Any) -> Iterator[str]:
    """Every string value in ``value`` (jq's ``[.. | strings]``: dict keys are not values)."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys(item)


def leaks(text: str) -> bool:
    return shares_a_fragment(text, [LONG_INPUT, LONG_OUTPUT], n=12)


def one_run(gig: GigContext, run: str) -> dict[str, Any]:
    (entry,) = report.build(gig, run)["runs"]
    return entry


# --- (b) the feed carries no item text ------------------------------------------------------------------------------


def test_the_json_feed_has_no_input_key_no_string_over_200_and_no_fragment_of_the_text(tmp_path: Path, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B, MARKER: SCORE_B})
    site = tmp_path / "site"
    _add_fixture(site, GIG)

    done = _run(tmp_path, site, GIG, "eval", "report", "--json", env_extra={"GIGAI_HOME": str(home)})

    assert done.returncode == 0, done.stderr
    feed = json.loads(done.stdout)
    assert [entry["run"] for entry in feed["runs"]] == [run]
    assert max(len(text) for text in strings(feed)) <= 200
    assert "input" not in set(keys(feed)) and "output" not in set(keys(feed))
    assert not leaks(done.stdout) and done.stderr == ""
    first = feed["runs"][0]["items"][0]
    assert first["item"] == "r01" and first["input_sha256"] == records._sha256(f"{LONG_INPUT}r01")
    assert first["output_sha256"] == records._sha256(f"{LONG_OUTPUT}r01") and first["scores"] == {MATCH: 0.0, MARKER: 0.5}


def test_include_inputs_adds_the_text_uncut_and_warns_on_stderr(tmp_path: Path, home: Path) -> None:
    record_run(home, {MATCH: ACC_B})
    site = tmp_path / "site"
    _add_fixture(site, GIG)

    done = _run(tmp_path, site, GIG, "eval", "report", "--json", "--include-inputs", env_extra={"GIGAI_HOME": str(home)})

    assert done.returncode == 0, done.stderr
    assert done.stderr.splitlines() == [report.INCLUDE_INPUTS_WARNING]
    items = json.loads(done.stdout)["runs"][0]["items"]
    assert [(i["item"], i["input"], i["output"], i["expected"]) for i in items[:2]] == [
        ("r01", f"{LONG_INPUT}r01", f"{LONG_OUTPUT}r01", "2"),
        ("r02", f"{LONG_INPUT}r02", f"{LONG_OUTPUT}r02", "4"),
    ]


def test_the_text_report_prints_no_fragment_of_the_text(gig: GigContext, home: Path) -> None:
    record_run(home, {MATCH: ACC_B, MARKER: SCORE_B})

    done = invoke(gig)

    assert done.exit_code == 0, done.stderr
    assert not leaks(done.output)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("x" * 200, "x" * 200), ("x" * 201, "x" * 199 + "…"), ("é" * 250, "é" * 199 + "…"), ("", "")],
)
def test_clip_cuts_strings_over_200_characters_to_200(value: str, expected: str) -> None:
    assert report.clip(value) == expected
    assert report.clip({"a": [value, {"b": value}], "n": 1.5, "none": None}) == {"a": [expected, {"b": expected}], "n": 1.5, "none": None}


def test_a_gate_line_longer_than_200_characters_is_cut_in_the_feed_only(gig: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record_run(home, {MATCH: ACC_B})
    long_line = "gate release: " + "y" * 300
    monkeypatch.setattr(report, "_verdict", lambda gig, step, suite: {"suite": suite, "verdict": "pass", "reason": None, "lines": [long_line], "failures": []})

    feed = json.loads(invoke(gig, "--json").stdout)
    text = invoke(gig).stdout

    assert feed["runs"][0]["gate"]["lines"] == [long_line[:199] + "…"]
    assert f"  {long_line}" in text.splitlines()


# --- means and intervals ----------------------------------------------------------------------------------------


def test_each_metric_reports_n_clusters_mean_and_the_cluster_bootstrap_interval(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B, MARKER: SCORE_B}, calls_made=12)

    entry = one_run(gig, run)

    assert (entry["n"], entry["clusters"], entry["calls"], entry["models"], entry["k"]) == (12, 10, 12, ["deterministic-a"], 1)
    marker, match = entry["metrics"]  # ordered by scorer id
    assert (match["metric"], match["scorer"], match["n"], match["clusters"]) == ("match", MATCH, 12, 10)
    assert match["mean"] == 0.75
    assert marker["mean"] == pytest.approx(sum(SCORE_B[i][0] for i in ITEMS) / 12, abs=1e-15)
    labels = [CLUSTERS[i] for i in ITEMS]
    expected = stats.paired_cluster_bootstrap([0.0] * 12, [ACC_B[i][0] for i in ITEMS], labels, seed=0)
    assert match["ci"] == {key: expected[key] for key in ("lower", "upper", "level", "stderr", "resamples", "seed")}
    assert match["ci"]["level"] == 0.95 and match["ci"]["lower"] < 0.75 < match["ci"]["upper"]
    alone = stats.paired_cluster_bootstrap([0.0] * 12, [ACC_B[i][0] for i in ITEMS], ITEMS, seed=0)
    assert (alone["lower"], alone["upper"]) != (match["ci"]["lower"], match["ci"]["upper"])  # the clusters are used


def test_a_small_change_in_one_score_moves_the_mean_by_exactly_its_share(gig: GigContext, home: Path) -> None:
    nudged = {**SCORE_B, "r05": [SCORE_B["r05"][0] + 1e-6]}
    before = one_run(gig, record_run(home, {MARKER: SCORE_B}))["metrics"][0]["mean"]
    after = one_run(gig, record_run(home, {MARKER: nudged}))["metrics"][0]["mean"]

    assert after - before == pytest.approx(1e-6 / 12, abs=1e-13)


def test_repeats_are_averaged_per_item_before_the_items_are(gig: GigContext, home: Path) -> None:
    uneven = {item: ([1.0] if item == "r01" else [0.0, 0.0]) for item in ITEMS}  # r01 has one repeat, the rest two

    (metric,) = one_run(gig, record_run(home, {MATCH: uneven}))["metrics"]

    assert metric["mean"] == pytest.approx(1 / 12, abs=1e-15)  # repeat-weighted would be 1/23


def test_no_interval_under_the_minimum_clusters_and_one_at_it(gig: GigContext, home: Path) -> None:
    nine = {item: f"c{min(n, 8):02d}" for n, item in enumerate(ITEMS)}
    ten = {item: f"c{min(n, 9):02d}" for n, item in enumerate(ITEMS)}

    (under,) = one_run(gig, record_run(home, {MATCH: ACC_B}, clusters=nine))["metrics"]
    (at,) = one_run(gig, record_run(home, {MATCH: ACC_B}, clusters=ten))["metrics"]

    assert (under["clusters"], under["ci"]) == (9, None)
    assert at["clusters"] == stats.MIN_CLUSTERS and at["ci"] is not None


def test_each_slice_is_measured_on_its_own_items(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B})

    slices = {part["slice"]: part for part in one_run(gig, run)["slices"]}

    assert sorted(slices) == ["core", "easy", "hard"]
    for name, part in slices.items():
        items = [i for i in ITEMS if ROWS[i]["slice"] == name or name in ROWS[i]["tags"]]
        assert (part["n"], part["clusters"]) == (len(items), len({CLUSTERS[i] for i in items}))
        (metric,) = part["metrics"]
        assert metric["mean"] == pytest.approx(sum(ACC_B[i][0] for i in items) / len(items), abs=1e-15)
    assert (slices["easy"]["n"], slices["easy"]["metrics"][0]["mean"]) == (6, 0.5)
    assert slices["core"]["metrics"][0]["ci"] is not None and slices["easy"]["metrics"][0]["ci"] is None


def test_slices_are_not_read_when_the_dataset_changed_since_the_run(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B})
    connection = sqlite3.connect(records.evals_db_path(GIG, home))
    connection.execute("UPDATE runs SET dataset_sha256 = ? WHERE run_id = ?", ("sha256:" + "0" * 64, run))
    connection.commit()
    connection.close()

    entry = one_run(gig, run)

    assert entry["slices"] is None and entry["slices_note"].startswith("the release dataset changed since run ")
    assert f"  slices: not read ({entry['slices_note']})" in invoke(gig).stdout.splitlines()


# --- gate verdicts ----------------------------------------------------------------------------------------------


def test_the_gate_verdict_is_eval_gates_own_pass(gig: GigContext, home: Path) -> None:
    record_run(home, {MATCH: ACC_A, MARKER: SCORE_A})
    newest = record_run(home, {MATCH: ACC_A, MARKER: SCORE_A})
    failures: list[str] = []
    lines = gates.apply(gig, "release", "solve", failures)

    gate = one_run(gig, newest)["gate"]

    assert gate == {"suite": "release", "verdict": "pass", "reason": None, "lines": lines, "failures": []}
    assert lines[-1] == "gate release: PASS" and f"  {lines[-1]}" in invoke(gig, "--run", newest).stdout.splitlines()


def test_the_gate_verdict_is_eval_gates_own_fail(gig: GigContext, home: Path) -> None:
    record_run(home, {MATCH: ACC_A, MARKER: SCORE_A})
    newest = record_run(home, {MATCH: {i: [0.0] for i in ITEMS}, MARKER: SCORE_A})
    failures: list[str] = []
    lines = gates.apply(gig, "release", "solve", failures)

    gate = one_run(gig, newest)["gate"]

    assert failures and gate == {"suite": "release", "verdict": "fail", "reason": None, "lines": lines, "failures": failures}


def test_a_step_without_a_gate_for_the_suite_prints_no_gate(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B}, step="answer")

    assert one_run(gig, run)["gate"]["verdict"] == "no gate"
    assert "  no gate" in invoke(gig).stdout.splitlines()


def test_a_gate_that_cannot_be_checked_says_why_and_the_report_still_exits_0(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B, MARKER: SCORE_B})  # the only release run: nothing to pair it with
    with pytest.raises(gates.RunRefused) as refused:
        gates.apply(gig, "release", "solve", [])

    gate = one_run(gig, run)["gate"]
    done = invoke(gig)

    assert (gate["verdict"], gate["reason"]) == ("cannot check", str(refused.value))
    assert done.exit_code == 0 and f"  gate release: cannot check: {refused.value}" in done.stdout.splitlines()


# --- labels -----------------------------------------------------------------------------------------------------


def add_label(home: Path, run: str | None, label: str, *, round_: int = 1, step: str = "solve", item: str = "r01") -> None:
    connection = sqlite3.connect(records.evals_db_path(GIG, home))
    connection.execute(
        "INSERT INTO labels (label_id, dataset_id, item_id, item_version, label, source, recorded_at, run_id, step_id, round)"
        " VALUES (?, 'release.jsonl', ?, '1', ?, 'human', '2026-03-02T00:00:00Z', ?, ?, ?)",
        (f"label_{uuid.uuid4()}", item, label, run, step, round_),
    )
    connection.commit()
    connection.close()


def test_label_counts_are_the_runs_first_round_labels_by_value(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B})
    other = record_run(home, {MATCH: ACC_B})
    for value in ['"met"', '"met"', '"met"', '"not met"', '"unknown"', '"maybe"', "not json"]:
        add_label(home, run, value)
    add_label(home, run, '"not met"', round_=2)
    add_label(home, other, '"met"')
    add_label(home, None, '"met"')  # a dataset label: no run
    add_label(home, run, '"met"', step="judge")  # another step of the run

    counts = one_run(gig, run)["labels"]

    assert counts == {"met": 3, "not met": 1, "unknown": 1, "other": 2, "relabels": 1}
    assert "  labels: met 3, not met 1, unknown 1, other 2 (1 relabels)" in invoke(gig, "--run", run).stdout.splitlines()


def test_a_run_without_labels_has_no_label_counts(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B})

    assert one_run(gig, run)["labels"] is None
    assert not any(line.startswith("  labels:") for line in invoke(gig).stdout.splitlines())


# --- which runs, and the refusals (exit 2, one line, nothing on stdout) ------------------------------------------


def test_every_eval_run_is_reported_oldest_first_and_run_picks_one(gig: GigContext, home: Path) -> None:
    first = record_run(home, {MATCH: ACC_A})
    second = record_run(home, {MATCH: ACC_B}, step="answer")

    assert [entry["run"] for entry in report.build(gig)["runs"]] == [first, second]
    assert [entry["run"] for entry in report.build(gig, second)["runs"]] == [second]
    heads = [line for line in invoke(gig).stdout.splitlines() if line.startswith("run ")]
    assert heads == [
        f"run {first}: step solve, suite release, k 1, 12 items, 10 clusters, 12 calls, models deterministic-a",
        f"run {second}: step answer, suite release, k 1, 12 items, 10 clusters, 12 calls, models deterministic-a",
    ]


def refusal(gig: GigContext, home: Path, case: str) -> tuple[list[str], str]:
    if case == "no store":
        return [], f"gig {GIG!r} has no eval store yet: make runs with eval run first"
    if case == "no eval run":
        record_run(home, {MATCH: ACC_B}, suite=None)
        return [], f"gig {GIG!r} has no eval run yet: make one with eval run first"
    if case == "unknown run":
        record_run(home, {MATCH: ACC_B})
        return ["--run", "run_nope"], "no run run_nope in this gig's eval store"
    if case == "not an eval run":
        run = record_run(home, {MATCH: ACC_B}, suite=None)
        return ["--run", run], f"run {run} was not made by eval run"
    if case in ("inf", "-inf"):
        run = record_run(home, {MATCH: {**ACC_B, "r07": [float(case)]}})
        return [], f"cannot report: run {run} stores a non-finite {MATCH} score"
    assert case == "duplicate"
    run = record_run(home, {MATCH: ACC_B})
    connection = records.open_evals_db(GIG, home)
    try:
        twin = records.TraceRecord("trace_twin", run, "solve", records.ItemRef("r03", "1", CLUSTERS["r03"]), f"{LONG_INPUT}r03", "o", 0)
        records.write_trace(connection, twin)
    finally:
        connection.close()
    return [], f"cannot report: run {run} records 2 traces for item r03 repeat 0 attempt 0"


@pytest.mark.parametrize("case", ["no store", "no eval run", "unknown run", "not an eval run", "inf", "-inf", "duplicate"])
@pytest.mark.parametrize("flags", [(), ("--json",), ("--json", "--include-inputs")])
def test_each_refusal_exits_2_with_one_line_and_nothing_on_stdout(gig: GigContext, home: Path, case: str, flags: tuple[str, ...]) -> None:
    argv, message = refusal(gig, home, case)

    done = invoke(gig, *argv, *flags)

    assert (done.exit_code, done.stdout) == (2, "")
    assert done.stderr.splitlines() == [f"gigai {GIG} eval: {message}"]
    assert not leaks(done.stderr)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_summarise_refuses_a_non_finite_value(value: float) -> None:
    with pytest.raises(report.RunRefused, match=r"^cannot report: a non-finite score$"):
        report.summarise({"r01": [1.0], "r02": [value]}, {"r01": "c1", "r02": "c2"})


def test_the_cli_refusal_comes_through_the_eval_group(tmp_path: Path, home: Path) -> None:
    site = tmp_path / "site"
    _add_fixture(site, GIG)

    done = _run(tmp_path, site, GIG, "eval", "report", env_extra={"GIGAI_HOME": str(home)})

    assert (done.returncode, done.stdout) == (2, "")
    assert done.stderr.splitlines() == [f"gigai {GIG} eval: gig {GIG!r} has no eval store yet: make runs with eval run first"]


# --- metric names and home paths --------------------------------------------------------------------------------

OTHER_MATCH = "other.mod:match"


def test_metric_names_use_the_whole_scorer_id_only_when_two_scorers_share_a_name() -> None:
    assert report.metric_names([MATCH, MARKER]) == {MATCH: "match", MARKER: "answer_marker"}
    assert report.metric_names([MATCH, OTHER_MATCH, MARKER]) == {MATCH: MATCH, OTHER_MATCH: OTHER_MATCH, MARKER: "answer_marker"}
    assert report.metric_names([]) == {}


def test_the_text_report_tells_two_scorers_with_one_name_apart(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B, OTHER_MATCH: {item: [0.25] for item in ITEMS}, MARKER: SCORE_B})

    lines = invoke(gig, "--run", run).stdout.splitlines()

    metric_lines = [line.split(": mean ")[0] for line in lines if line.startswith("  ") and not line.startswith("   ") and ": mean " in line]
    assert metric_lines == ["  answer_marker", f"  {MATCH}", f"  {OTHER_MATCH}"]
    assert f"  {OTHER_MATCH}: mean 0.2500 over 12 items" in "\n".join(lines)
    assert [m["metric"] for m in one_run(gig, run)["metrics"]] == ["answer_marker", "match", "match"]


def test_a_dataset_path_in_the_slices_note_names_the_home_as_home(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B}, step="answer")  # its release dataset is private and not there

    entry = one_run(gig, run)
    feed = invoke(gig, "--json").stdout
    text = invoke(gig).stdout

    assert entry["slices"] is None
    assert entry["slices_note"].startswith(f"the release dataset is not there: put it at <home>/gigs/{GIG}/")
    assert str(home) not in feed and str(home.resolve()) not in feed and str(home) not in text
    assert f"  slices: not read ({entry['slices_note']})" in text.splitlines()


def test_a_home_path_in_a_gate_reason_or_line_is_written_home(gig: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record_run(home, {MATCH: ACC_B})

    def refuse(*args: Any) -> list[str]:
        raise report.RunRefused(f"cannot read {home.resolve()}/gigs/{GIG}/evals.sqlite or {home}/x")

    monkeypatch.setattr(gates, "apply", refuse)
    assert report._verdict(gig, "solve", "release")["reason"] == f"cannot read <home>/gigs/{GIG}/evals.sqlite or <home>/x"

    def passes(gig_: GigContext, suite: str, step: str, failures: list[str]) -> list[str]:
        failures.append(f"see {home}/f")
        return [f"gate release: {home}/a", "gate release: PASS"]

    monkeypatch.setattr(gates, "apply", passes)
    verdict = report._verdict(gig, "solve", "release")
    assert (verdict["lines"], verdict["failures"]) == (["gate release: <home>/a", "gate release: PASS"], ["see <home>/f"])
