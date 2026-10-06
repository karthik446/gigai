"""0.1.11.2 THIN (release blocker 3): a match read from too few requirements never reads "Matched · fit 100%".

End outcomes, synthetic fixtures, no model call beyond the scripted one.

(A) FEWER THAN 4 REQUIREMENT ROWS (every matrix row, the "N of M requirements"
    a reader sees) is a LABEL: the row says "thin posting: too few
    requirements to judge" and ``thin_posting: true``; its state, the filters
    and the counts stay a match's. Judged when the row is shown, so an
    assessment already stored is covered. It is LISTED LAST (RANKFIX, the
    local release check: a 2-of-2 "fit 100%" was listed first): after every
    other assessed posting and every posting not assessed yet.
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

THIN = "thin posting: too few requirements to judge"
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


def test_a_match_on_fewer_than_four_requirements_says_thin_posting_and_is_listed_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = _scene(fx, monkeypatch)
    names = {url: name for name, url in jobs.items()}
    everything = _search(fx)
    by_name = {names[str(row["job_identity"])]: row for row in _rows(everything)}

    # (A) The stored 2-of-2: the thin label, neither "Matched" nor "fit 100%"; the flag; still a match underneath.
    two = by_name["two"]
    assert two["score_text"] == f"{THIN} · 2 of 2 requirements · rank 90"
    assert "Matched" not in str(two["score_text"]) and "fit 100%" not in str(two["score_text"])
    assert (two["thin_posting"], two["state"], two["fit"]) == (True, "matched", 100)
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

    # The order. The 2-of-2 comes after every posting that is not thin, whatever its fit (100) and rank (90); the thin
    # STATE comes last of all, whatever its own rank (95, 99).
    order = [names[str(row["job_identity"])] for row in _rows(everything)]
    assert order == ["four", "unranked_four", "needs", "two", "lone", "empty"]
    # ... and the SQL twin of the order says the same.
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        states = ["matched", "needs_answers", "thin_posting"]
        in_sql = [names[row.job] for row in store.postings_by_score(states=states, profile_id=fx.default_profile_id, limit=50)]
        keys = {names[row.job]: scout_new.order_key(row) for row in store.postings(profile_id=fx.default_profile_id, live=False)}
    finally:
        store.close()
    assert in_sql == order
    assert keys["lone"][3] == keys["empty"][3] == 5 and keys["two"][3] == keys["four"][3] == 0
    assert [keys[name][0] for name in ("four", "needs", "two", "lone", "empty")] == [0, 0, 1, 1, 1]  # thin: after the rest

    # Never counted as a match: the counts by state, and the matched filter.
    counts = everything["counts"]
    assert counts["by_state"] == {"matched": 3, "needs_answers": 1, "thin_posting": 2}  # type: ignore[index]
    matched = _search(fx, states=["matched"])
    assert [names[str(row["job_identity"])] for row in _rows(matched)] == ["four", "unranked_four", "two"]
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
    assert [("thin posting" in line, "Matched" in line) for line in lines[:4]] == [(False, True), (False, True), (False, False), (True, False)]  # after the matches and the one that waits on answers
    grid = scout_new.scout_new(fx.home_root, fx.target, peek=True, assess=False, now=NOW)
    new = {names[str(row["job_identity"])]: row for row in _rows(grid)}
    assert new["two"]["score_text"] == f"{THIN} · 2 of 2 requirements · rank 90" and new["two"]["thin_posting"] is True
    assert new["lone"]["state"] == "thin_posting"
    table = scout_new.render(grid)  # one cell line per part of the score
    assert table.count("| Matched") == 2 and table.count("| fit 100%") == 2 and table.count("thin posting: too few requirements") == 3, table


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


# --- 0.1.11.2 RANKFIX (2): a thin posting is listed after the real matches and the ranked postings ----------


def test_a_thin_posting_is_listed_after_every_real_match_and_every_ranked_posting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    from urllib.request import urlopen

    from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("order", [lever_job("order", n) for n in range(1, 5)], seen_at=days_ago(1))
    jobs = {name: job_url("order", n) for n, name in enumerate(("thin", "real", "ranked", "unranked"), start=1)}
    names = {url: name for name, url in jobs.items()}
    # The operator's case: 2 of 2 requirements, fit 100%, the best rank of all. And a real match: 6 rows, the 4 must-haves met, fit 80%.
    assess_one(fx, jobs["thin"], _met(2))
    assess_one(fx, jobs["real"], matrix_answer([(f"Hard topic {n}", "hard", "met") for n in range(1, 5)] + [(f"Bonus topic {n}", "nice_to_have", "unmet") for n in (1, 2)]))
    seed_rank(fx, monkeypatch, {jobs["thin"]: 99, jobs["real"]: 60, jobs["ranked"]: 70})
    expected = ["real", "ranked", "unranked", "thin"]

    def order(rows: list[dict[str, object]]) -> list[str]:
        return [names[str(row["job_identity"])] for row in rows]

    # The Jobs list, as GET /api/postings serves it.
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        with urlopen(f"http://{host}:{port}/api/postings?limit=50", timeout=30) as response:  # noqa: S310 - the test's own server
            served = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
    by_name = {names[str(row["job_identity"])]: row for row in _rows(served)}
    # The thin one is still a match underneath with the best numbers, and still says the thin label ...
    assert (by_name["thin"]["state"], by_name["thin"]["fit"], by_name["thin"]["rank_score"], by_name["thin"]["thin_posting"]) == ("matched", 100, 99, True)
    assert by_name["thin"]["score_text"] == f"{THIN} · 2 of 2 requirements · rank 99"
    assert (by_name["real"]["thin_posting"], by_name["real"]["fit"], by_name["real"]["rank_score"]) == (False, 80, 60)
    assert by_name["real"]["score_text"].endswith("fit 80% · 4 of 6 requirements · rank 60"), by_name["real"]["score_text"]
    assert (by_name["ranked"]["state"], by_name["ranked"]["rank_score"]) == ("not_assessed", 70)
    assert (by_name["unranked"]["state"], by_name["unranked"]["rank_score"]) == ("not_assessed", None)
    # ... and THE OUTCOME: it is listed after the real match and after the ranked posting.
    assert order(_rows(served)) == expected

    # `gigai scout new` (the grid and its table) and `gigai scout jobs list` say the same order.
    grid = scout_new.scout_new(fx.home_root, fx.target, peek=True, assess=False, now=NOW)
    assert order(_rows(grid)) == expected
    table = scout_new.render(grid)
    assert table.index("4 of 6 requirements") < table.index("rank 70") < table.index("thin posting: too few requirements"), table
    listed = CliRunner().invoke(cli, ["scout", "jobs", "list", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert listed.exit_code == 0, listed.output
    assert listed.output.index("4 of 6 requirements") < listed.output.index("rank 70 · not assessed") < listed.output.index(THIN), listed.output

    # The SQL twin (``pipeline.store._POSTING_ORDER``) and ``scout_new.order_key`` agree on it.
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        states = ["matched", "has_gap", "not_assessed"]
        in_sql = [names[row.job] for row in store.postings_by_score(states=states, profile_id=fx.default_profile_id, limit=50)]
        rows = store.postings(profile_id=fx.default_profile_id, live=False)
    finally:
        store.close()
    assert in_sql == expected
    assert [names[row.job] for _tags, row in scout_new.in_order(((), row) for row in rows)] == expected
    assert [scout_new.order_key(row)[0] for _tags, row in scout_new.in_order(((), row) for row in rows)] == [0, 0, 0, 1]
