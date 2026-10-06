"""0.1.11.2 THIN (release blocker 3): a match read from too few requirements never reads "Matched · fit 100%".

End outcomes, synthetic fixtures, no model call beyond the scripted one.

(A) FEWER THAN 4 REQUIREMENT ROWS (every matrix row, the "N of M requirements"
    a reader sees) is a LABEL: the row says "thin posting, not enough
    requirements to score", ``thin_posting: true`` and no percentage (``fit`` null); its state, its place in
    the list, the filters and the counts stay a match's. Judged when the row
    is shown, so an assessment already stored is covered.
(B) NO ROW ABOUT THE JOB (an empty matrix, a lone "No stated requirements"
    row) is its own state, ``thin_posting``: never in ``by_state.matched`` or
    the matched filter, no fit number, and ordered below every other assessed
    posting, in Python (``scout_new.order_key``) and in SQL
    (``pipeline.store._POSTING_ORDER``) alike.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import fit, posting_search, run_history, scout_new
from gigai.scout.find_jobs.job_state import AssessmentFact, JobStateSources, derive_job_state
from gigai.scout.pipeline.store import PipelineStore, RunAssessment, pipeline_path
from gigai.scout.quick_assess import quick_assess_path

from tests.support.fit_fixtures import assess_one, five_of_ten, matrix_answer, seed_rank
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

THIN = "thin posting, not enough requirements to score"
NAMES = ("two", "four", "lone", "empty", "unranked_four", "needs")


def _met(count: int) -> str:
    return matrix_answer([(f"Hard topic {n}", "hard", "met") for n in range(1, count + 1)])


def _scene(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Six assessed postings: 2 of 2 (rank 90), 4 of 4 (rank 70), a lone "No stated requirements" row (rank 95), a
    stored match with an EMPTY matrix (rank 99), 4 of 4 not ranked, and one that waits on answers (rank 60)."""

    fx.seed("thin", [lever_job("thin", n) for n in range(1, 7)], seen_at=days_ago(1))
    jobs = {name: job_url("thin", n) for n, name in enumerate(NAMES, start=1)}
    assess_one(fx, jobs["two"], _met(2))
    assess_one(fx, jobs["four"], _met(4))
    assess_one(fx, jobs["lone"], matrix_answer([("No stated requirements", "nice_to_have", "met")]))
    assess_one(fx, jobs["empty"], _met(2))
    assess_one(fx, jobs["unranked_four"], _met(4))
    assess_one(fx, jobs["needs"], five_of_ten())
    # A match stored with no matrix at all (an older file): a new answer like it is refused, so the file is rewritten.
    path = quick_assess_path(fx.home_root, fx.target, fx.default_profile_id, jobs["empty"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored["result"]["matrix"]) == 2
    stored["result"]["matrix"] = []
    fresh = path.with_suffix(".tmp")
    fresh.write_text(json.dumps(stored), encoding="utf-8")
    os.replace(fresh, path)
    seed_rank(fx, monkeypatch, {jobs["two"]: 90, jobs["four"]: 70, jobs["lone"]: 95, jobs["empty"]: 99, jobs["needs"]: 60})
    return jobs


def _search(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return posting_search.search_postings(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _rows(response: dict[str, object]) -> list[dict[str, object]]:
    return response["postings"]["rows"]  # type: ignore[index,return-value]


def test_the_rule_is_one_place() -> None:
    assert (fit.THIN_POSTING, fit.THIN_POSTING_ROWS, fit.THIN_LABEL) == ("thin_posting", 4, THIN)
    assert [fit.is_thin_posting("matched", rows) for rows in (None, 0, 1, 3, 4, 12)] == [True, True, True, True, False, False]
    assert fit.is_thin_posting("thin_posting", 9)
    # Only a match is relabelled: no other state claims a fit to trust.
    assert not any(fit.is_thin_posting(state, 2) for state in ("needs_answers", "has_gap", "not_a_match", "weak_fit", "not_assessed"))
    assert fit.thin_state("matched", 0) == "thin_posting" and fit.thin_state("matched", 2) == "matched"
    assert fit.thin_state("matched", None) == "matched" and fit.thin_state("needs_answers", 0) == "needs_answers"

    def state(**fact: object) -> str:
        return derive_job_state(assessments=(AssessmentFact(at="2026-10-01T00:00:00Z", **fact),)).state  # type: ignore[arg-type]

    matched = "matched_above_threshold"
    assert state(verdict=matched) == "matched" and state(verdict=matched, real_rows=2) == "matched"
    assert state(verdict=matched, real_rows=0) == "thin_posting"
    assert state(verdict="pending_user_answers", real_rows=0) == "needs_answers"
    # The defensive map (RANK-B's report): a matched verdict the gate refused never reads as a match.
    assert state(verdict=matched, gate="not_a_match") == "not_a_match"
    assert state(verdict=matched, gate="hold_unmet") == "has_gap" and state(verdict=matched, gate="suggest") == "matched"


def test_a_match_on_fewer_than_four_requirements_says_thin_posting_and_keeps_its_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = _scene(fx, monkeypatch)
    names = {url: name for name, url in jobs.items()}
    everything = _search(fx)
    by_name = {names[str(row["job_identity"])]: row for row in _rows(everything)}

    # (A) The stored 2-of-2: the thin label, neither "Matched" nor "fit 100%"; the flag; still a match underneath.
    two = by_name["two"]
    assert two["score_text"] == f"{THIN} · 2 of 2 requirements · rank 90"
    assert "Matched" not in str(two["score_text"]) and "fit 100%" not in str(two["score_text"])
    # RANKUI: no percentage anywhere for a thin posting: `fit` null, `score` its rank, `assessment.percent` null.
    assert (two["thin_posting"], two["state"], two["fit"]) == (True, "matched", None)
    assert (two["score"], two["score_kind"], two["assessment"]["percent"]) == (90, "rank", None)
    assert "%" not in json.dumps([row["score_text"] for row in _rows(everything) if row["thin_posting"]])
    # A 4-row assessment is unchanged.
    four = by_name["four"]
    assert four["score_text"] == "Matched · fit 100% · 4 of 4 requirements · rank 70"
    assert (four["thin_posting"], four["state"], four["fit"]) == (False, "matched", 100)
    assert (by_name["needs"]["thin_posting"], by_name["needs"]["state"]) == (False, "needs_answers")

    # (B) No row about the job: its own state, no fit number, no "N of M", never "Matched".
    for name, rank in (("lone", 95), ("empty", 99)):
        row = by_name[name]
        assert row["score_text"] == f"{THIN} · rank {rank}", row["score_text"]
        assert (row["thin_posting"], row["state"], row["fit"], row["score_kind"]) == (True, "thin_posting", None, "rank")

    # The order. The 2-of-2 is where a match of its fit and rank is (label only: first, above the 4-of-4 at rank 70);
    # the thin STATE comes after every other assessed posting, ranked or not, whatever its own rank (95, 99).
    order = [names[str(row["job_identity"])] for row in _rows(everything)]
    assert order == ["two", "four", "unranked_four", "needs", "lone", "empty"]
    # ... and the SQL twin of the order says the same.
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        states = ["matched", "needs_answers", "thin_posting"]
        in_sql = [names[row.job] for row in store.postings_by_score(states=states, profile_id=fx.default_profile_id, limit=50)]
        keys = {names[row.job]: scout_new.order_key(row) for row in store.postings(profile_id=fx.default_profile_id, live=False)}
    finally:
        store.close()
    assert in_sql == order
    assert keys["lone"][2] == keys["empty"][2] == 5 and keys["two"][2] == keys["four"][2] == 0

    # Never counted as a match: the counts by state, and the matched filter.
    counts = everything["counts"]
    assert counts["by_state"] == {"matched": 3, "needs_answers": 1, "thin_posting": 2}  # type: ignore[index]
    matched = _search(fx, states=["matched"])
    assert [names[str(row["job_identity"])] for row in _rows(matched)] == ["two", "four", "unranked_four"]
    assert matched["counts"]["matched"] == 3  # type: ignore[index]
    thin = _search(fx, states=["thin_posting"])
    assert [names[str(row["job_identity"])] for row in _rows(thin)] == ["lone", "empty"]

    # A job's own state (the job page) says the same.
    sources = JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.base.gig.resolved)
    own = {name: sources.state_for(url, profile_id=fx.default_profile_id).state for name, url in jobs.items()}
    assert own == {
        "two": "matched", "four": "matched", "lone": "thin_posting", "empty": "thin_posting", "unranked_four": "matched",
        "needs": "needs_answers",
    }

    # The terminal prints the same words: ``jobs list`` and the ``scout new`` table.
    listed = CliRunner().invoke(cli, ["scout", "jobs", "list", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert listed.exit_code == 0, listed.output
    assert f"{THIN} · 2 of 2 requirements · rank 90" in listed.output and f"{THIN} · rank 95" in listed.output, listed.output
    assert "2 of 2 requirements" in listed.output and "fit 100% · 2 of 2" not in listed.output
    assert listed.output.count("Matched · fit 100%") == 2, listed.output  # the two 4-row matches only
    lines = [line for line in listed.output.splitlines() if " requirements · " in line]
    assert [("thin posting" in line, "Matched" in line) for line in lines[:2]] == [(True, False), (False, True)]  # its place is kept
    grid = scout_new.scout_new(fx.home_root, fx.target, peek=True, assess=False, now=NOW)
    new = {names[str(row["job_identity"])]: row for row in _rows(grid)}
    assert new["two"]["score_text"] == f"{THIN} · 2 of 2 requirements · rank 90" and new["two"]["thin_posting"] is True
    assert new["lone"]["state"] == "thin_posting"
    table = scout_new.render(grid)  # one cell line per part of the score
    assert table.count("| Matched") == 2 and table.count("| fit 100%") == 2 and table.count("| thin posting, not enough") == 3, table  # the cell is narrow: the label wraps


def test_an_old_runs_match_with_few_or_no_requirement_rows_reads_thin_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("ran", [lever_job("ran", n) for n in range(1, 4)], seen_at=days_ago(1))
    jobs = {name: job_url("ran", n) for n, name in enumerate(("none", "three", "five"), start=1)}

    def ran(job: str, met: int) -> RunAssessment:
        return RunAssessment(
            run_id="run_20260930T100000Z", job=job, profile_id=fx.default_profile_id, state="matched",
            assessed_at="2026-09-30T10:00:00.000000Z", reqs_met=met, reqs_total=met, open_questions=0, listing_digest=None,
            prompt_version="assess-prompt-v4", constraints_digest="sha256:" + "c" * 64, bank_digest=None, profile_revision=1,
            profile_digest=None, pinned_record=None, pinned_revision=None, pinned_digest=None, model_target="codex_cli",
            adapter="codex_cli",
        )

    rows = [ran(jobs["none"], 0), ran(jobs["three"], 3), ran(jobs["five"], 5)]
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        store.import_run("run_20260930T100000Z", fx.default_profile_id, rows)
    finally:
        store.close()

    names = {url: name for name, url in jobs.items()}
    listed = {names[str(row["job_identity"])]: row for row in _rows(_search(fx))}
    assert (listed["none"]["state"], listed["none"]["thin_posting"]) == ("thin_posting", True)
    assert listed["none"]["score_text"] == f"{THIN} (old assessment: older prompt) · not ranked yet"
    assert (listed["three"]["state"], listed["three"]["thin_posting"]) == ("matched", True)
    assert listed["three"]["score_text"] == f"{THIN} (old assessment: older prompt) · 3 of 3 requirements · not ranked yet"
    assert (listed["five"]["state"], listed["five"]["thin_posting"]) == ("matched", False)
    assert listed["five"]["score_text"] == "Matched (old assessment: older prompt) · fit 100% · 5 of 5 requirements · not ranked yet"
    assert [names[str(row["job_identity"])] for row in _rows(_search(fx))][-1] == "none"  # below both matches

    history = {item.job: run_history.history_row(item, active=True) for item in rows}
    assert [(history[jobs[name]]["state"], history[jobs[name]]["thin_posting"]) for name in ("none", "three", "five")] == [
        ("thin_posting", True), ("matched", True), ("matched", False),
    ]


def test_no_percentage_for_a_thin_posting_in_any_json_row_api_new_scout_new_and_the_postings_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RANKUI (3): `fit` is null for every thin row (fewer than 4 rows, or the thin state) wherever a row is served:
    GET and POST /api/new, GET /api/postings, `gigai scout new --json` and `gigai scout jobs list --json`."""

    import threading

    import httpx

    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = _scene(fx, monkeypatch)
    thin_jobs, full_jobs = {jobs["two"], jobs["lone"], jobs["empty"]}, {jobs["four"], jobs["unranked_four"]}
    since = "2020-01-01T00:00:00Z"  # every posting of the scene, whatever today is

    def check(rows: list[dict[str, object]], where: str) -> None:
        by_job = {str(row["job_identity"]): row for row in rows}
        assert thin_jobs | full_jobs <= set(by_job), (where, sorted(by_job))
        for job in thin_jobs:
            row = by_job[job]
            assert row["thin_posting"] is True and row["fit"] is None, (where, row["score_text"], row["fit"])
            assert row["score_kind"] != "assessment" and (row["assessment"] or {}).get("percent") is None, (where, row)
            assert "%" not in str(row["score_text"]) and str(row["score_text"]).startswith(THIN), (where, row["score_text"])
        for job in full_jobs:
            assert (by_job[job]["thin_posting"], by_job[job]["fit"]) == (False, 100), (where, by_job[job]["score_text"])

    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=60.0) as client:
            got = client.get(f"/api/new?since={since}")
            assert got.status_code == 200, got.text
            check(got.json()["postings"]["rows"], "GET /api/new")
            posted = client.post("/api/new", json={"assess": False, "peek": True, "since": since})
            assert posted.status_code == 200, posted.text
            check(posted.json()["postings"]["rows"], "POST /api/new")
            listed = client.get("/api/postings?limit=200")
            assert listed.status_code == 200, listed.text
            check(listed.json()["postings"]["rows"], "GET /api/postings")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)

    for args, where in ((["new", "--peek", "--since", since], "scout new --json"), (["jobs", "list", "--limit", "200"], "scout jobs list --json")):
        printed = CliRunner().invoke(cli, ["scout", *args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
        assert printed.exit_code == 0, printed.output
        check(json.loads(printed.stdout)["postings"]["rows"], where)
    # The words a terminal prints: the thin wording, and never a percent on a thin line.
    table = CliRunner().invoke(cli, ["scout", "new", "--peek", "--since", since, "--home", str(fx.home_root), "--target", str(fx.target)])
    assert table.exit_code == 0 and THIN.split(",")[0] in table.output, table.output
    plain = CliRunner().invoke(cli, ["scout", "jobs", "list", "--home", str(fx.home_root), "--target", str(fx.target)])
    thin_lines = [line for line in plain.output.splitlines() if THIN in line]
    assert len(thin_lines) == 3 and not any("%" in line for line in thin_lines), plain.output
