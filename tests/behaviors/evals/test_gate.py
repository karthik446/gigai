"""``gigai <gig> eval gate --suite ci|release|upgrade``: exit 0 pass, 1 fail, 2 cannot compare.

ci replays the committed snapshot with no model call; release and upgrade pair the newest run against the run
``paired_to`` names and apply the primary and guard rules overall and per slice; ``require.private_share`` is
checked only when the manifest sets it.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from gigai.evals import gates, records
from gigai.evals import run as eval_run
from gigai.evals.run import RunRefused, Suite
from gigai.gigs.eval_manifest import EvalDataset
from gigai.sdk import GigContext
from tests.behaviors.evals.test_compare import ACC_A, ACC_B, ITEMS, MARKER, MATCH, SCORE_A, SCORE_B, fake_run
from tests.behaviors.evals.test_run import GIG, PACKAGE, calls, gig, home  # noqa: F401 - fixtures
from tests.behaviors.gigs.test_loader import _add_fixture, _run

SNAPSHOT = "snapshots/answer-ci.json"
EXACT = "gig_eval_fixture.scorers:exact_match"


def regated(gig: GigContext, step: str, name: str, **changes: Any) -> GigContext:
    """``gig`` with ``gates.<name>`` of step ``step`` updated by ``changes`` (a value ``None`` removes the key)."""

    manifest = copy.deepcopy(dict(gig.manifest))
    entry = next(e for e in manifest["evals"]["steps"] if e["id"] == step)
    gate = entry["gates"][name]
    for key, value in changes.items():
        if value is None:
            gate.pop(key, None)
        else:
            gate[key] = value
    return GigContext(gig.home, gig.name, manifest, gig.log)


def package_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the fixture package that ``eval gate`` reads its snapshot and datasets from."""

    target = tmp_path / "package"
    shutil.copytree(PACKAGE, target)
    monkeypatch.setattr(eval_run, "_package_dir", lambda name: target if name == GIG else None)
    return target


def edit_snapshot(package: Path, change: Any) -> None:
    path = package / SNAPSHOT
    snapshot = json.loads(path.read_text())
    change(snapshot)
    path.write_text(json.dumps(snapshot))


def row(snapshot: dict[str, Any], item: str) -> dict[str, Any]:
    return next(r for r in snapshot["rows"] if r["id"] == item)


def apply(gig: GigContext, suite: str, step: str | None = None) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    return gates.apply(gig, suite, step, failures), failures


def release_pair(home: Path, *, suite: str = "release", a: dict | None = None, b: dict | None = None, **b_options: Any) -> tuple[str, str]:
    first = fake_run(home, a or {MATCH: ACC_A, MARKER: SCORE_A}, suite=suite)
    second = fake_run(home, b or {MATCH: ACC_B, MARKER: SCORE_B}, suite=suite, **b_options)
    return first, second


# --- ci: the snapshot replay -----------------------------------------------------------------------------------


def test_the_ci_gate_replays_the_snapshot_with_zero_calls_and_writes_nothing(gig: GigContext, home: Path, calls: list) -> None:
    lines, failures = apply(gig, "ci")

    assert failures == [] and calls == []
    assert lines == [
        f"step answer: ci snapshot {SNAPSHOT}, 6 rows x 2 scorer(s), 0 calls, 0 mismatch(es)",
        f"  {EXACT}: 0.6667",
        "  step:judge: 0.6667",
        "gate ci: PASS",
    ]
    assert not records.evals_db_path(GIG, home).exists()


@pytest.mark.parametrize(
    ("change", "failure"),
    [
        (lambda s: row(s, "q3")["scores"].__setitem__(EXACT, 0.0), f"step answer row q3: {EXACT} scores 1.0000, snapshot 0.0000"),
        (lambda s: row(s, "q3")["scores"].__setitem__(EXACT, 0.99), f"step answer row q3: {EXACT} scores 1.0000, snapshot 0.9900"),
        (lambda s: row(s, "q5")["scores"].__setitem__("step:judge", 1.0), "step answer row q5: step:judge scores 0.0000, snapshot 1.0000"),
        (lambda s: row(s, "q2").__setitem__("output", '{"answer": "9"}'), f"step answer row q2: {EXACT} scores 0.0000, snapshot 1.0000"),
        (lambda s: row(s, "q1")["judge"].__setitem__("judge", "GRADE: I"), "step answer row q1: step:judge scores 0.0000, snapshot 1.0000"),
    ],
)
def test_one_edited_snapshot_value_fails_the_ci_gate_naming_the_row(
    gig: GigContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: list, change: Any, failure: str
) -> None:
    package = package_copy(tmp_path, monkeypatch)
    edit_snapshot(package, change)

    lines, failures = apply(gig, "ci", "answer")

    assert failures == [failure] and calls == []
    assert lines[0].endswith("0 calls, 1 mismatch(es)") and lines[-1] == "gate ci: FAIL"


def test_the_ci_cli_exits_0_on_the_snapshot_and_1_naming_the_row_after_one_edit(tmp_path: Path, home: Path) -> None:
    site = tmp_path / "site"
    _add_fixture(site, GIG)
    env = {"GIGAI_HOME": str(home)}

    passed = _run(tmp_path, site, GIG, "eval", "gate", "--suite", "ci", env_extra=env)
    edit_snapshot(site / "gig_eval_fixture", lambda s: row(s, "q4")["scores"].__setitem__(EXACT, 0.5))
    failed = _run(tmp_path, site, GIG, "eval", "gate", "--suite", "ci", env_extra=env)

    assert passed.returncode == 0, passed.stderr
    assert "0 calls, 0 mismatch(es)" in passed.stdout and passed.stdout.splitlines()[-1] == "gate ci: PASS"
    assert failed.returncode == 1 and failed.stdout.splitlines()[-1] == "gate ci: FAIL"
    assert failed.stderr.splitlines() == [f"gigai eval-fixture eval: gate ci failed: step answer row q4: {EXACT} scores 1.0000, snapshot 0.5000"]
    assert "What is" not in passed.stdout + failed.stdout + failed.stderr


CI_REFUSALS = {
    "no snapshot key": r"^the ci gate of step 'answer' names no snapshot \(gates\.ci\.snapshot\)$",
    "absent file": r"^snapshot snapshots/answer-ci\.json of step 'answer' is not in the gig package$",
    "not json": r"^snapshot snapshots/answer-ci\.json of step 'answer' is not readable JSON$",
    "bad shape": r"^snapshot snapshots/answer-ci\.json of step 'answer' is not a snapshot: ",
    "escapes": r"^snapshot \.\./outside\.json leaves the gig package$",
    "dataset changed": r"^the ci dataset of step 'answer' changed since snapshot snapshots/answer-ci\.json \(sha256:0{64}, now sha256:1900380c",
    "rows differ": r"^snapshot snapshots/answer-ci\.json has 5 rows for the 6 items of the ci dataset$",
    "score missing": rf"^snapshot snapshots/answer-ci\.json row q2 has no {EXACT} score$",
    "reply missing": r"^snapshot snapshots/answer-ci\.json row q6 has no stored reply of judge step 'judge'$",
}


@pytest.mark.parametrize("case", sorted(CI_REFUSALS))
def test_a_ci_gate_that_cannot_be_checked_is_refused(
    gig: GigContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: list, case: str
) -> None:
    package = package_copy(tmp_path, monkeypatch)
    snapshot = package / SNAPSHOT
    if case == "no snapshot key":
        gig = regated(gig, "answer", "ci", snapshot=None)
    elif case == "absent file":
        snapshot.unlink()
    elif case == "not json":
        snapshot.write_text("{not json")
    elif case == "bad shape":
        edit_snapshot(package, lambda s: row(s, "q1").__setitem__("scores", {EXACT: True}))
    elif case == "escapes":
        (tmp_path / "outside.json").write_text(snapshot.read_text())
        gig = regated(gig, "answer", "ci", snapshot="../outside.json")
    elif case == "dataset changed":
        edit_snapshot(package, lambda s: s.__setitem__("dataset_sha256", "sha256:" + "0" * 64))
    elif case == "rows differ":
        edit_snapshot(package, lambda s: s.__setitem__("rows", s["rows"][1:]))
    elif case == "score missing":
        edit_snapshot(package, lambda s: row(s, "q2")["scores"].pop(EXACT))
    else:
        edit_snapshot(package, lambda s: row(s, "q6").pop("judge"))

    with pytest.raises(RunRefused, match=CI_REFUSALS[case]):
        apply(gig, "ci", "answer")
    assert calls == []


# --- release ---------------------------------------------------------------------------------------------------


def test_the_release_gate_passes_overall_and_per_slice_and_reports_small_slices(gig: GigContext, home: Path) -> None:
    a, b = release_pair(home)

    lines, failures = apply(gig, "release")

    assert failures == []
    assert lines[0] == f"step solve: release gate, run {b} paired to last-release run {a}, 12 items, 10 clusters"
    assert lines[1] == "  all items:"
    assert lines[2].startswith("  primary pass match: 0.2500 -> 0.7500, difference +0.5000, 95% CI +0.0909 to +1.0000")
    assert lines[3].startswith("  guard pass answer_marker: 0.5000 -> 0.5750, difference +0.0750")
    assert "  slice easy: 6 items, 6 clusters < 10: reported, not gated" in lines
    assert "  slice hard: 6 items, 6 clusters < 10: reported, not gated" in lines
    core = lines.index("  slice core:")
    assert lines[core + 1].startswith("  primary pass match: 0.3000 -> 0.7000, difference +0.4000, 95% CI -0.2000 to")
    assert lines[-1] == "gate release: PASS"
    assert all("MDE " in line for line in lines if "match:" in line or "answer_marker:" in line)


def test_the_release_gate_fails_a_worse_candidate_on_the_primary_rule(gig: GigContext, home: Path) -> None:
    release_pair(home, a={MATCH: ACC_B, MARKER: SCORE_A}, b={MATCH: ACC_A, MARKER: SCORE_A})

    _, failures = apply(gig, "release", "solve")

    assert failures == [
        "step solve all items: primary match CI lower -1.0000 < min_ci_lower -0.2500",
        "step solve slice core: primary match CI lower -1.0000 < min_ci_lower -0.2500",
    ]


def test_the_guard_fails_on_its_own_when_the_point_difference_drops_past_no_worse(gig: GigContext, home: Path) -> None:
    worse = {item: [0.3] for item in ITEMS}
    release_pair(home, b={MATCH: ACC_B, MARKER: worse})

    _, failures = apply(gig, "release", "solve")

    assert failures == [
        "step solve all items: guard answer_marker difference -0.2000 < no_worse -0.1000",
        "step solve slice core: guard answer_marker difference -0.2000 < no_worse -0.1000",
    ]


def test_a_slice_with_10_clusters_is_gated_on_its_own(gig: GigContext, home: Path) -> None:
    release_pair(home)
    strict = regated(gig, "solve", "release", primary={"metric": "match", "min_ci_lower": 0.0})

    _, failures = apply(strict, "release", "solve")

    assert failures == ["step solve slice core: primary match CI lower -0.2000 < min_ci_lower +0.0000"]


def test_a_declared_slice_with_no_item_is_refused_before_any_verdict(gig: GigContext, home: Path) -> None:
    release_pair(home)
    typo = regated(gig, "solve", "release", slices=["easy", "nope"])

    with pytest.raises(RunRefused, match=r"^slice 'nope' of the release gate of step 'solve' names no item the runs scored$"):
        apply(typo, "release", "solve")


def test_two_real_offline_release_runs_pass_the_release_gate(tmp_path: Path, home: Path) -> None:
    site = tmp_path / "site"
    _add_fixture(site, GIG)
    env = {"GIGAI_HOME": str(home)}

    runs = [_run(tmp_path, site, GIG, "eval", "run", "--step", "solve", "--suite", "release", env_extra=env) for _ in range(2)]
    done = _run(tmp_path, site, GIG, "eval", "gate", "--suite", "release", "--step", "solve", env_extra=env)

    assert [r.returncode for r in runs] == [0, 0], runs[0].stderr
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    # r09 has no ANSWER marker, r10 writes 2,0 and r12 is wrong: match 11/12 (it reads 2,0 as 20), answer_marker 9/12.
    assert lines[2].startswith("  primary pass match: 0.9167 -> 0.9167, difference +0.0000, 95% CI +0.0000 to +0.0000")
    assert lines[3].startswith("  guard pass answer_marker: 0.7500 -> 0.7500, difference +0.0000")
    assert lines[-1] == "gate release: PASS"


def test_the_release_cli_prints_the_verdict_and_exits_0_or_1(tmp_path: Path, home: Path) -> None:
    site = tmp_path / "site"
    _add_fixture(site, GIG)
    env = {"GIGAI_HOME": str(home)}
    release_pair(home)

    passed = _run(tmp_path, site, GIG, "eval", "gate", "--suite", "release", "--step", "solve", env_extra=env)
    fake_run(home, {MATCH: ACC_A, MARKER: SCORE_A})  # the newest run falls back: paired to the last one
    failed = _run(tmp_path, site, GIG, "eval", "gate", "--suite", "release", env_extra=env)

    assert passed.returncode == 0, passed.stderr
    assert passed.stdout.splitlines()[-1] == "gate release: PASS"
    assert failed.returncode == 1 and failed.stdout.splitlines()[-1] == "gate release: FAIL"
    assert failed.stderr.startswith("gigai eval-fixture eval: gate release failed: step solve all items: primary match CI lower ")
    assert len(failed.stderr.splitlines()) == 1


# --- the held-out share ------------------------------------------------------------------------------------------


def test_a_set_private_share_with_no_private_items_is_refused_before_any_run_is_read(gig: GigContext, home: Path) -> None:
    held = regated(gig, "solve", "release", require={"private_share": 0.30})

    with pytest.raises(RunRefused, match=r"^held-out share 0\.00 < 0\.30$"):
        apply(held, "release", "solve")
    assert not records.evals_db_path(GIG, home).exists()


def test_an_unset_private_share_is_not_checked(gig: GigContext, home: Path) -> None:
    release_pair(home)
    assert "require" not in next(e for e in gig.manifest["evals"]["steps"] if e["id"] == "solve")["gates"]["release"]

    _, failures = apply(gig, "release", "solve")

    assert failures == []


def test_the_held_out_share_counts_private_licences_row_first() -> None:
    declared = EvalDataset("release", "rows.jsonl", licence="CC0-1.0")
    rows = {str(i): {"id": str(i), **({"licence": "private"} if i < 3 else {})} for i in range(10)}
    dataset = records.Dataset("rows.jsonl", "1", "sha256:x", {})

    assert gates.held_out_share(Suite(declared, rows, dataset)) == 0.3
    private = EvalDataset("release", "rows.jsonl", licence="private")
    mixed = {**rows, "9": {"id": "9", "licence": "MIT"}}
    assert gates.held_out_share(Suite(private, mixed, dataset)) == 0.9


def test_the_cli_refuses_an_unmet_held_out_share_with_exit_2(tmp_path: Path, home: Path) -> None:
    site = tmp_path / "site"
    _add_fixture(site, GIG)
    manifest = site / "gig_eval_fixture" / "gig.toml"
    text = manifest.read_text()
    marker = 'guard = { metric = "answer_marker", no_worse = -0.10 } }'
    assert marker in text
    manifest.write_text(text.replace(marker, marker[:-2] + ", require = { private_share = 0.30 } }"))

    done = _run(tmp_path, site, GIG, "eval", "gate", "--suite", "release", env_extra={"GIGAI_HOME": str(home)})

    assert done.returncode == 2 and done.stdout == ""
    assert done.stderr.splitlines() == ["gigai eval-fixture eval: held-out share 0.00 < 0.30"]


@pytest.mark.parametrize("require", [["private_share"], {"private_share": 0.1, "min_items": 50}])
def test_a_require_eval_gate_cannot_check_is_refused(gig: GigContext, home: Path, require: object) -> None:
    with pytest.raises(RunRefused, match=r"eval gate checks only private_share$"):
        apply(regated(gig, "solve", "release", require=require), "release", "solve")


MALFORMED = {
    "no metric": ({"primary": {"min_ci_lower": -0.25}}, r" has a malformed primary rule: it takes a metric name, min_ci_lower and no_worse$"),
    "guard not a table": ({"guard": "answer_marker"}, r" has a malformed guard rule: "),
    "unknown key": ({"primary": {"metric": "match", "min_ci_lower": -0.25, "max": 1}}, r" has a malformed primary rule: "),
    "no threshold": ({"primary": {"metric": "match"}}, r": the primary rule sets no threshold \(min_ci_lower or no_worse\)$"),
    "text threshold": ({"guard": {"metric": "answer_marker", "no_worse": "x"}}, r": the guard rule's no_worse is not a number$"),
    "bool threshold": ({"primary": {"metric": "match", "min_ci_lower": True}}, r": the primary rule's min_ci_lower is not a number$"),
    "text share": ({"require": {"private_share": "abc"}}, r": require\.private_share is not a number$"),
    "slices not a list": ({"slices": "core"}, r": slices must be a list of slice or tag names$"),
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_a_malformed_gate_is_refused_before_any_run_is_read(gig: GigContext, home: Path, case: str) -> None:
    changes, message = MALFORMED[case]

    with pytest.raises(RunRefused, match=r"^the release gate of step 'solve'" + message):
        apply(regated(gig, "solve", "release", **changes), "release", "solve")
    assert not records.evals_db_path(GIG, home).exists()


# --- upgrade -----------------------------------------------------------------------------------------------------


def test_the_upgrade_gate_reports_k_paired_to_slices_variance_components_and_mde(gig: GigContext, home: Path) -> None:
    doubled_a = {MATCH: {i: v * 2 for i, v in ACC_A.items()}}
    doubled_b = {MATCH: {i: v * 2 for i, v in ACC_B.items()}}
    a, b = release_pair(home, suite="upgrade", a=doubled_a, b=doubled_b, model="deterministic-b")

    lines, failures = apply(gig, "upgrade", "solve")

    assert failures == []
    assert lines[0] == f"step solve: upgrade gate, run {b} paired to previous-model run {a}, 12 items, 10 clusters"
    assert "  k: previous-model run 2, candidate 2" in lines
    assert f"  variance components (candidate {b}, match): within-item 0.0000, between-item 0.2045, stderr 0.1306" in lines
    # 3 items at 1 and 9 at 0, no spread across the 2 repeats: between-item 9/44 = 0.2045, stderr sqrt(9/44/12) = 0.1306.
    assert f"  variance components (baseline {a}, match): within-item 0.0000, between-item 0.2045, stderr 0.1306" in lines
    assert "  slice easy: 6 items, 6 clusters < 10: reported, not gated" in lines
    assert any("MDE " in line and "at n=12" in line for line in lines)


def test_the_upgrade_gate_needs_a_run_on_another_model(gig: GigContext, home: Path) -> None:
    a, b = release_pair(home, suite="upgrade")

    with pytest.raises(RunRefused, match=rf"^run {b} has no previous-model run of step 'solve' to pair with$"):
        apply(gig, "upgrade", "solve")


# --- exit 2: nothing to compare ----------------------------------------------------------------------------------


def test_gates_that_cannot_compare_are_refused(gig: GigContext, home: Path) -> None:
    with pytest.raises(RunRefused, match=r"^step 'judge' declares no release gate$"):
        apply(gig, "release", "judge")
    with pytest.raises(RunRefused, match=r"^no \[\[evals\.steps\]\] entry has id 'nope'$"):
        apply(gig, "release", "nope")
    with pytest.raises(RunRefused, match=r"^gig 'eval-fixture' has no eval store yet"):
        apply(gig, "release", "solve")
    only = fake_run(home, {MATCH: ACC_A, MARKER: SCORE_A})
    with pytest.raises(RunRefused, match=rf"^run {only} has no last-release run of step 'solve' to pair with$"):
        apply(gig, "release", "solve")
    with pytest.raises(RunRefused, match=r"^paired_to 'yesterday' is not one of \['last-release', 'previous-model'\]$"):
        apply(regated(gig, "solve", "release", paired_to="yesterday"), "release", "solve")
    with pytest.raises(RunRefused, match=r"^the release gate of step 'solve' declares no primary rule$"):
        apply(regated(gig, "solve", "release", primary=None), "release", "solve")
    stale = fake_run(home, {MATCH: ACC_B, MARKER: SCORE_B}, sha="sha256:" + "0" * 64)
    with pytest.raises(RunRefused, match=rf"^run {stale} measured sha256:0{{64}}, not the release dataset now \(sha256:b0a23ac9"):
        apply(gig, "release", "solve")


def test_the_gate_cli_lists_its_suites_in_help(tmp_path: Path, home: Path) -> None:
    site = tmp_path / "site"
    _add_fixture(site, GIG)

    done = _run(tmp_path, site, GIG, "eval", "--help", env_extra={"GIGAI_HOME": str(home)})

    assert done.returncode == 0
    assert "Compare two runs of one step item by item" in done.stdout
    assert "gate            Apply a suite's gate: exit 0 pass, 1 fail, 2 cannot" in done.stdout
    assert "select" not in done.stdout.lower()
