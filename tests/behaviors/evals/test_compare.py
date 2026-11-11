"""``gigai <gig> eval compare A B``: two stored runs paired by item, the paired tests with their closed forms, and the
fingerprint refusals (exit 2) that keep two runs that measured different things from being compared at all.

The runs here are written straight to the eval store (no model call): ``fake_run`` records traces and scores with
chosen values, so each statistic can be checked against its closed form.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import statistics
import uuid
from pathlib import Path

import pytest

from gigai.evals import compare, records, stats
from gigai.evals import run as eval_run
from gigai.sdk import GigContext
from tests.behaviors.evals.test_run import GIG, PACKAGE, calls, gig, home  # noqa: F401 - fixtures
from tests.behaviors.gigs.test_loader import _add_fixture, _run

RELEASE = PACKAGE / "release.jsonl"
SHA = eval_run.dataset_digest(hashlib.sha256(RELEASE.read_bytes()).hexdigest())
ITEMS = [f"r{i:02d}" for i in range(1, 13)]
#: r11 and r12 share the clusters of r01 and r02: 12 items, 10 clusters (the dataset's own cluster_id).
CLUSTERS = {item: json.loads(line)["cluster_id"] for item, line in zip(ITEMS, RELEASE.read_text().splitlines(), strict=True)}
#: The solve step's two scorers (metric names ``match`` and ``answer_marker``), as its release gate names them.
MATCH = "gigai.evals.contracts:match"
MARKER = "gig_eval_fixture.scorers:answer_marker"
#: match with b = 3 (A right, B wrong: r01-r03) and c = 9 (A wrong, B right: r04-r12).
ACC_A = {item: [1.0 if n < 3 else 0.0] for n, item in enumerate(ITEMS)}
ACC_B = {item: [0.0 if n < 3 else 1.0] for n, item in enumerate(ITEMS)}
SCORE_A = {item: [0.5] for item in ITEMS}
SCORE_B = {item: [0.5 + 0.05 * (n % 4)] for n, item in enumerate(ITEMS)}
PREREG = records.Prereg("match", "ci_lower >= 0", 0.0, 12, "2026-01-01T00:00:00Z", "answer_marker")
_STAMP = iter(range(10_000))


def model_call(model: str) -> records.ModelCall:
    return records.ModelCall(model, "deterministic", "local", "DeterministicAdapter", model, model, None, (), records.Settings(), {}, None, None, None)


def fake_run(
    home: Path,
    scores: dict[str, dict[str, list[float]]],
    *,
    clusters: dict[str, str | None] | None = None,
    step: str = "solve",
    suite: str = "release",
    sha: str = SHA,
    versions: dict[str, str] | None = None,
    prereg: records.Prereg | None = PREREG,
    model: str = "deterministic-a",
) -> str:
    """Record a finished run of ``step`` whose scorer ``s`` gave item ``i`` repeat ``r`` the value ``scores[s][i][r]``
    (an item with fewer values than the run's k has no score on its later repeats)."""

    clusters = CLUSTERS if clusters is None else clusters
    stamp = next(_STAMP)
    run_id = f"run_{uuid.uuid4()}"
    k = max(len(repeats) for by_item in scores.values() for repeats in by_item.values())
    connection = records.open_evals_db(GIG, home)
    try:
        dataset = records.Dataset("release.jsonl", "1", sha, {"count": 12})
        created = f"2026-02-01T00:00:{stamp // 100:02d}.{stamp % 100:02d}0000Z"
        records.write_run(connection, records.RunRecord(run_id, GIG, "sha256:m", dataset, prereg, step, suite, None, k, created_at=created))
        items = sorted({item for by_item in scores.values() for item in by_item})
        for item in items:
            for repeat in range(k):
                trace = records.TraceRecord(
                    f"trace_{uuid.uuid4()}", run_id, step, records.ItemRef(item, "1", clusters[item]),
                    "What is the input?", "the output", repeat, model=model_call(model), recorded_at=created,
                )
                records.write_trace(connection, trace)
                for scorer, by_item in scores.items():
                    if item in by_item and repeat < len(by_item[item]):
                        value = by_item[item][repeat]
                        version = (versions or {}).get(scorer, "1")
                        records.write_score(connection, records.ScoreRecord(f"score_{uuid.uuid4()}", trace.trace_id, scorer, version, value, recorded_at=created))
        records.finish_run(connection, run_id, 0)
    finally:
        connection.close()
    return run_id


def pair_of_runs(home: Path, **b_options: object) -> tuple[str, str]:
    a = fake_run(home, {MATCH: ACC_A, MARKER: SCORE_A})
    b = fake_run(home, {MATCH: ACC_B, MARKER: SCORE_B}, **b_options)  # type: ignore[arg-type]
    return a, b


def connor_mde(n: int, discordance: float) -> float:
    """Connor 1987 solved for delta by bisection, independently of ``gigai.evals.stats``."""

    z = statistics.NormalDist()
    z_alpha, z_beta = z.inv_cdf(0.975), z.inv_cdf(0.80)
    low, high = 1e-9, discordance
    for _ in range(200):
        mid = (low + high) / 2
        needed = (z_alpha * math.sqrt(discordance) + z_beta * math.sqrt(discordance - mid**2)) ** 2 / mid**2
        low, high = (mid, high) if needed > n else (low, mid)
    return high


def seeded_diffs(n: int = 48, mean: float = 0.167, sd: float = 0.595) -> list[float]:
    rng = random.Random(7)
    raw = [rng.gauss(0.0, 1.0) for _ in range(n)]
    centre, spread = statistics.fmean(raw), statistics.stdev(raw)
    return [mean + sd * (value - centre) / spread for value in raw]


# --- (c) the closed forms ---------------------------------------------------------------------------------------


def test_binary_metrics_use_exact_mcnemar_and_print_the_mde_at_the_runs_n(gig: GigContext, home: Path) -> None:
    a, b = pair_of_runs(home)
    result = compare.compare(gig, a, b, ["match"])
    (accuracy,) = result["metrics"]

    assert (result["items"], result["clusters"], result["dataset_sha256"]) == (12, 10, SHA)
    assert accuracy["test"] == "mcnemar" and (accuracy["mcnemar"]["b"], accuracy["mcnemar"]["c"]) == (3, 9)
    assert accuracy["mcnemar"]["p"] == pytest.approx(2 * 299 / 4096, abs=1e-12)
    assert accuracy["mcnemar"]["p"] == pytest.approx(0.146, abs=0.001)
    assert accuracy["mde"]["n"] == 12 and accuracy["mde"]["discordance"] == 1.0
    assert accuracy["mde"]["mde"] == pytest.approx(connor_mde(12, 1.0), abs=1e-6)
    assert accuracy["ci"]["lower"] < 0.5 < accuracy["ci"]["upper"] and accuracy["ci"]["level"] == 0.95
    expected = stats.paired_cluster_bootstrap([ACC_A[i][0] for i in ITEMS], [ACC_B[i][0] for i in ITEMS], [CLUSTERS[i] for i in ITEMS])
    assert (accuracy["ci"]["lower"], accuracy["ci"]["upper"]) == (expected["lower"], expected["upper"])
    assert accuracy["noise_bound"] is None  # k = 1 on both sides


def test_continuous_metrics_use_wilcoxon_and_with_k_2_a_noise_bound_of_0_335(tmp_path: Path, home: Path) -> None:
    diffs = seeded_diffs()
    assert (statistics.fmean(diffs), statistics.stdev(diffs)) == (pytest.approx(0.167), pytest.approx(0.595))
    items = [f"r{i:02d}" for i in range(1, 49)]
    alone: dict[str, str | None] = {item: None for item in items}  # no cluster: each item is its own
    a = fake_run(home, {MARKER: {item: [1.0, 1.0] for item in items}}, clusters=alone, prereg=None)
    b = fake_run(home, {MARKER: {item: [1.0 + d, 1.0 + d] for item, d in zip(items, diffs, strict=True)}}, clusters=alone, prereg=None)
    site = tmp_path / "site"
    _add_fixture(site, GIG)

    done = _run(tmp_path, site, GIG, "eval", "compare", a, b, "--metric", "answer_marker", "--json", env_extra={"GIGAI_HOME": str(home)})

    assert done.returncode == 0, done.stderr
    (score,) = json.loads(done.stdout)["metrics"]
    assert score["noise_bound"] == pytest.approx(0.335, abs=0.01)
    assert score["noise_bound"] == pytest.approx(0.167 + 1.959964 * 0.595 / math.sqrt(48), abs=1e-6)
    assert score["test"] == "wilcoxon" and score["wilcoxon"]["n"] == 48
    assert score["mde"] == stats.mde_continuous(48, statistics.stdev(diffs))
    assert json.loads(done.stdout)["clusters"] == 48


def test_the_text_report_prints_the_test_interval_and_mde_but_no_item_text(tmp_path: Path, home: Path) -> None:
    a, b = pair_of_runs(home)
    site = tmp_path / "site"
    _add_fixture(site, GIG)

    done = _run(tmp_path, site, GIG, "eval", "compare", a, b, env_extra={"GIGAI_HOME": str(home)})

    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == f"compare {a} -> {b}: step solve, 12 items, 10 clusters, dataset {SHA}"
    assert lines[1].startswith("  match: 0.2500 -> 0.7500, difference +0.5000, 95% CI ")
    assert "McNemar b=3 c=9 p=0.1460" in lines[1] and "MDE 0." in lines[1] and "at n=12" in lines[1]
    assert lines[2].startswith("  answer_marker: 0.5000 -> 0.5750, difference +0.0750") and "Wilcoxon p=" in lines[2] and "MDE " in lines[2]
    assert "holm p=" in lines[1] and "holm p=" in lines[2]
    rows = RELEASE.read_text()
    assert "What is" not in done.stdout + done.stderr and "the output" not in done.stdout and rows[:20] not in done.stdout


# --- corrections, slices, latest rows ---------------------------------------------------------------------------


def test_holm_corrects_the_preregistered_metrics_and_bh_on_request_others_stay_raw(gig: GigContext, home: Path) -> None:
    extra = {item: [0.0] for item in ITEMS}
    moved = [0.1, 0.2, 0.3, -0.15, 0.25, 0.05, -0.1, 0.35, -0.2, -0.05, 0.4, 0.12]  # Wilcoxon p = 0.1157
    a = fake_run(home, {MATCH: ACC_A, MARKER: {i: [0.0] for i in ITEMS}, "extra": extra})
    b = fake_run(home, {MATCH: ACC_B, MARKER: {i: [d] for i, d in zip(ITEMS, moved, strict=True)}, "extra": {i: [1.0] for i in ITEMS}})

    held = {m["metric"]: m for m in compare.compare(gig, a, b)["metrics"]}
    bh = {m["metric"]: m for m in compare.compare(gig, a, b, correction="bh")["metrics"]}

    raw = [held["match"]["p"], held["answer_marker"]["p"]]
    assert held["answer_marker"]["p"] == pytest.approx(0.11572265625)
    assert [held["match"]["p_adjusted"], held["answer_marker"]["p_adjusted"]] == pytest.approx([0.2314453125, 0.2314453125])
    assert [bh["match"]["p_adjusted"], bh["answer_marker"]["p_adjusted"]] == pytest.approx([2 * 299 / 4096, 2 * 299 / 4096])
    assert [held["match"]["p_adjusted"], held["answer_marker"]["p_adjusted"]] == stats.holm(raw, 0.05)["adjusted"]
    assert held["extra"]["p_adjusted"] is None and held["extra"]["reject"] is None and held["extra"]["p"] < 0.01
    assert compare.compare(gig, a, b)["family"] == [MATCH, MARKER]
    # --metric picks what is shown, not the family: the one shown metric keeps its family-corrected p.
    shown = compare.compare(gig, a, b, ["answer_marker"])
    assert [m["metric"] for m in shown["metrics"]] == ["answer_marker"] and shown["family"] == [MATCH, MARKER]
    assert shown["metrics"][0]["p_adjusted"] == pytest.approx(0.2314453125)
    (alone,) = compare.compare(gig, a, b, ["extra"])["metrics"]
    assert alone["p_adjusted"] is None


def test_without_a_prereg_no_metric_is_corrected(gig: GigContext, home: Path) -> None:
    a = fake_run(home, {MATCH: ACC_A}, prereg=None)
    b = fake_run(home, {MATCH: ACC_B}, prereg=None)

    result = compare.compare(gig, a, b)

    assert result["family"] == [] and result["metrics"][0]["p_adjusted"] is None


def test_a_slice_keeps_its_items_and_a_slice_under_10_clusters_is_refused(gig: GigContext, home: Path) -> None:
    a, b = pair_of_runs(home)

    core = compare.compare(gig, a, b, ["match"], slice_name="core")  # tag core: r01-r10
    assert (core["items"], core["clusters"], core["slice"]) == (10, 10, "core")
    assert (core["metrics"][0]["mcnemar"]["b"], core["metrics"][0]["mcnemar"]["c"]) == (3, 7)
    with pytest.raises(eval_run.RunRefused, match=r"^slice 'easy' has 6 clusters, fewer than the 10 a comparison needs$"):
        compare.compare(gig, a, b, slice_name="easy")


def test_the_last_attempt_and_the_latest_score_row_count(gig: GigContext, home: Path) -> None:
    a, b = pair_of_runs(home)
    connection = records.open_evals_db(GIG, home)
    try:
        (trace,) = connection.execute("SELECT trace_id FROM traces WHERE run_id = ? AND item_id = 'r04'", (b,)).fetchone()
        records.write_score(connection, records.ScoreRecord("score_late", trace, MATCH, "1", 0.0, recorded_at="2026-03-01T00:00:00Z"))
        retry = records.TraceRecord("trace_retry", b, "solve", records.ItemRef("r05", "1", CLUSTERS["r05"]), "i", "o", 0, 1)
        records.write_trace(connection, retry)
        records.write_score(connection, records.ScoreRecord("score_retry", "trace_retry", MATCH, "1", 0.0))
        records.write_score(connection, records.ScoreRecord("score_retry_s", "trace_retry", MARKER, "1", 0.5))
    finally:
        connection.close()

    (accuracy,) = compare.compare(gig, a, b, ["match"])["metrics"]

    assert (accuracy["mcnemar"]["b"], accuracy["mcnemar"]["c"]) == (3, 7)


# --- exit 2: runs that measured different things ----------------------------------------------------------------


def _refusal(gig: GigContext, home: Path, case: str) -> tuple[str, str, list[str]]:
    a = fake_run(home, {MATCH: ACC_A, MARKER: SCORE_A})
    if case == "dataset":
        return a, fake_run(home, {MATCH: ACC_B}, sha="sha256:" + "0" * 64), []
    if case == "step":
        return a, fake_run(home, {MATCH: ACC_B}, step="answer"), []
    if case == "version":
        return a, fake_run(home, {MATCH: ACC_B}, versions={MATCH: "2"}), []
    if case == "family version":  # not asked for, but pre-registered: its version is checked as well
        return a, fake_run(home, {MATCH: ACC_B, MARKER: SCORE_B}, versions={MARKER: "2"}), ["match"]
    if case == "cluster":
        return a, fake_run(home, {MATCH: ACC_B}, clusters={**CLUSTERS, "r12": "c09"}), []
    if case == "items":
        return a, fake_run(home, {MATCH: {i: v for i, v in ACC_B.items() if i != "r12"}}), []
    if case == "k":
        return a, fake_run(home, {MATCH: {i: v * 2 for i, v in ACC_B.items()}}), []
    if case == "repeats":
        return a, fake_run(home, {MATCH: {**ACC_B, "r04": [1.0, 0.0]}}), []
    if case == "duplicate":
        b = fake_run(home, {MATCH: ACC_B})
        connection = records.open_evals_db(GIG, home)
        try:
            twin = records.TraceRecord("trace_twin", b, "solve", records.ItemRef("r03", "1", CLUSTERS["r03"]), "i", "o", 0)
            records.write_trace(connection, twin)
            records.write_score(connection, records.ScoreRecord("score_twin", "trace_twin", MATCH, "1", 1.0))
        finally:
            connection.close()
        return a, b, []
    if case == "metric":
        return a, fake_run(home, {MATCH: ACC_B}), ["answer_marker"]
    if case == "unknown":
        return a, "run_nope", []
    nine = {item: f"c{min(n, 8):02d}" for n, item in enumerate(ITEMS)}
    return fake_run(home, {MATCH: ACC_A}, clusters=nine), fake_run(home, {MATCH: ACC_B}, clusters=nine), []


REFUSALS = {
    "dataset": rf"^cannot compare: dataset sha256 differs \(A {SHA}, B sha256:0{{64}}\)$",
    "step": r"^cannot compare: the runs are of different steps \(A solve, B answer\)$",
    "version": rf"^cannot compare: scorer {MATCH} version differs \(A 1, B 2\)$",
    "family version": rf"^cannot compare: scorer {MARKER} version differs \(A 1, B 2\)$",
    "cluster": r"^cannot compare: the cluster key differs \(1 item\(s\) in a different cluster\)$",
    "items": rf"^cannot compare: the runs scored different items with {MATCH} \(A 12, B 11\)$",
    "k": r"^cannot compare: k differs \(A 1, B 2\)$",
    "repeats": rf"^cannot compare: run run_[0-9a-f-]+ repeats its items unequally with {MATCH} \(1 to 2\)$",
    "duplicate": r"^cannot compare: run run_[0-9a-f-]+ records 2 traces for item r03 repeat 0 attempt 0$",
    "metric": r"^cannot compare: metric 'answer_marker' is not scored in both runs$",
    "unknown": r"no run run_nope",
    "clusters": r"^cannot compare: 9 clusters, fewer than the 10 a paired comparison needs$",
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_runs_that_measured_different_things_are_refused(gig: GigContext, home: Path, case: str) -> None:
    a, b, metrics = _refusal(gig, home, case)

    with pytest.raises(eval_run.RunRefused, match=REFUSALS[case]):
        compare.compare(gig, a, b, metrics)


def test_no_eval_store_is_refused(gig: GigContext, home: Path) -> None:
    with pytest.raises(eval_run.RunRefused, match=r"^gig 'eval-fixture' has no eval store yet: make runs with eval run first$"):
        compare.compare(gig, "run_a", "run_b")
    assert not records.evals_db_path(GIG, home).exists()


def test_the_cli_exits_2_with_one_line_and_prints_nothing_on_stdout(tmp_path: Path, home: Path) -> None:
    a = fake_run(home, {MATCH: ACC_A})
    b = fake_run(home, {MATCH: ACC_B}, versions={MATCH: "2"})
    site = tmp_path / "site"
    _add_fixture(site, GIG)

    done = _run(tmp_path, site, GIG, "eval", "compare", a, b, env_extra={"GIGAI_HOME": str(home)})

    assert done.returncode == 2 and done.stdout == ""
    assert done.stderr.splitlines() == [f"gigai eval-fixture eval: cannot compare: scorer {MATCH} version differs (A 1, B 2)"]


def test_compare_never_writes_the_store(gig: GigContext, home: Path) -> None:
    a, b = pair_of_runs(home)
    path = records.evals_db_path(GIG, home)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    compare.compare(gig, a, b)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone() == (2,)
