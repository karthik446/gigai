"""0.1.10.9 ticket 0110-10-02: weak fit, sort by real fit, and the assess threshold. End outcomes, synthetic fixtures.

(1) WEAK FIT. A posting that waits on the user's answers while few of its
    requirements are met AND its rank score is low (the operator's case: 1 of
    10 met, rank 39, three questions) is ``weak_fit``: left out of the default
    list and of "needs your answers", listed only when asked for, asking no
    question, and never queued in the pipeline by a saved answer.
(2) SORT. Inside a group the rows come by the fit number (the share of
    requirements met, must-haves counted twice), then the rank score, then the
    newest; each row carries that one number, and so does ``scout new``.
(3) THRESHOLD. ``scout new`` and "Assess these" assess only postings whose
    rank score is at least 50 (a setting); the low-ranked ones are counted and
    are their own question.

No response mixes posting text with the user's own (``data_labels``): every
response here passes ``check_response`` inside the builders.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import posting_search, postings, scout_new, story_bank
from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow
from gigai.scout.find_jobs.job_state import JobStateSources
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.pipeline import triggers
from gigai.scout.pipeline.store import PipelineStore, RunAssessment, pipeline_path

from tests.support.fit_fixtures import TERRAFORM, assess_one, matrix_answer, ranked_new, seed_rank, weak_fixture
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job


# --- helpers --------------------------------------------------------------------------------


def _new(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return scout_new.scout_new(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _search(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return posting_search.search_postings(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _these(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return posting_search.assess_these(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _rows(response: dict[str, object]) -> list[dict[str, object]]:
    return response["postings"]["rows"]  # type: ignore[index,return-value]


def _jobs(response: dict[str, object]) -> list[str]:
    return [str(row["job_identity"]) for row in _rows(response)]


def _write_fit_setting(fx: PostingsFixture, **values: int) -> None:
    path = settings_path(fx.home_root, fx.target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "fit": values}), encoding="utf-8")


def _old_run(fx: PostingsFixture, jobs: list[str]) -> None:
    """``jobs`` as an old find-jobs run assessed them (imported rows: an older prompt)."""

    run_id = "run_20260930T100000Z"
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        store.import_run(run_id, fx.default_profile_id, [
            RunAssessment(
                run_id=run_id, job=job, profile_id=fx.default_profile_id, state="matched", assessed_at="2026-09-30T10:00:00.000000Z",
                reqs_met=3, reqs_total=3, open_questions=0, listing_digest=None, prompt_version="assess-prompt-v4",
                constraints_digest="sha256:" + "c" * 64, bank_digest=None, profile_revision=1, profile_digest=None, pinned_record=None,
                pinned_revision=None, pinned_digest=None, model_target="codex_cli", adapter="codex_cli",
            )
            for job in jobs
        ])
    finally:
        store.close()


# --- the rules, in one place ----------------------------------------------------------------


def test_the_fit_number_counts_a_must_have_twice_and_the_rules_need_known_numbers(tmp_path: Path) -> None:
    from gigai.scout import fit

    def row(kind: RequirementClass | None, status: MatrixStatus) -> RequirementMatrixRow:
        return RequirementMatrixRow("a requirement", (), status, kind)

    # 2 must-haves met (4) of 2 must-haves and 2 nice-to-haves (6): 67%, where the plain share is 50%.
    weighted = [row(RequirementClass.HARD, MatrixStatus.MET), row(RequirementClass.ASKABLE, MatrixStatus.MET),
                row(RequirementClass.NICE_TO_HAVE, MatrixStatus.UNMET), row(RequirementClass.NICE_TO_HAVE, MatrixStatus.UNCLEAR)]
    assert fit.fit_percent(weighted) == 67 and fit.plain_percent(2, 4) == 50
    assert fit.fit_percent([row(None, MatrixStatus.MET)]) == 100  # not classed: a must-have
    assert fit.fit_percent([]) is None and fit.plain_percent(0, 0) is None and fit.plain_percent(None, None) is None

    assert (fit.DEFAULT_WEAK_FIT_BELOW_PERCENT, fit.DEFAULT_WEAK_FIT_BELOW_RANK, fit.DEFAULT_ASSESS_MIN_RANK) == (40, 50, 50)
    assert fit.is_weak_fit("needs_answers", 39, 49)
    assert not fit.is_weak_fit("needs_answers", 40, 49)  # the share is not low
    assert not fit.is_weak_fit("needs_answers", 39, 50)  # the rank is not low
    assert not fit.is_weak_fit("needs_answers", 39, None)  # not ranked yet: nothing says the rank is low
    assert not fit.is_weak_fit("needs_answers", None, 10)
    assert not fit.is_weak_fit("matched", 10, 10) and not fit.is_weak_fit("not_a_match", 10, 10)
    assert fit.shown_state("needs_answers", 10, 39) == "weak_fit" and fit.shown_state("needs_answers", 80, 39) == "needs_answers"
    assert fit.is_low_rank(49) and not fit.is_low_rank(50) and not fit.is_low_rank(None)

    # Tunable in one place: the ``fit`` block of the project's settings file.
    path = tmp_path / "settings.json"
    assert fit.fit_setting(tmp_path, None, path=path) == fit.DEFAULT_SETTING
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "fit": {"assess_min_rank": 60, "weak_fit_below_rank": 30}}), encoding="utf-8")
    tuned = fit.fit_setting(tmp_path, None, path=path)
    assert tuned.to_json() == {"assess_min_rank": 60, "weak_fit_below_percent": 40, "weak_fit_below_rank": 30, "source": "setting"}
    assert fit.is_low_rank(55, tuned) and not fit.is_weak_fit("needs_answers", 10, 39, tuned)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "fit": {"assess_min_rank": 0, "weak_fit_below_percent": 0}}), encoding="utf-8")
    off = fit.fit_setting(tmp_path, None, path=path)
    assert not fit.is_low_rank(0, off) and not fit.is_weak_fit("needs_answers", 0, 0, off)  # 0 switches a rule off
    for bad in ('{"schema_version": "scout-settings:1", "fit": {"assess_min_rank": "50"}}', '{"schema_version": "scout-settings:1", "fit": {"assess_min_rank": 101}}', "{not json"):
        path.write_text(bad, encoding="utf-8")
        assert fit.fit_setting(tmp_path, None, path=path).to_json() == {**fit.DEFAULT_SETTING.to_json(), "source": "settings_unreadable"}


def test_a_stored_posting_table_from_before_gains_the_fit_column_in_place_and_keeps_its_rows(tmp_path: Path) -> None:
    """The upgrade: no schema version bump, so a build without the column still opens the same file."""

    import sqlite3

    from gigai.scout.pipeline import store as pipeline_store

    path = tmp_path / "pipeline.sqlite"
    PipelineStore(path).close()
    digest = "sha256:" + "a" * 64
    stamp = "2026-10-01T00:00:00.000000Z"
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE posting DROP COLUMN fit")  # the table as 0.1.10.9 before this change wrote it
    connection.execute(
        "INSERT INTO posting(job, profile_id, board, first_seen, listing_digest, listing_known, rank_score, match_rank, state, "
        "reqs_met, reqs_total, open_questions, tailored, pinned_digest, settings_digest, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("https://jobs.lever.co/acme/acme-00001", "profile_00000000-0000-4000-8000-000000000001", "lever:acme", stamp, digest, 1, 39, 1,
         "needs_answers", 1, 10, 3, 0, digest, digest, stamp),
    )
    connection.commit()
    assert "fit" not in {row[1] for row in connection.execute("PRAGMA table_info(posting)")}
    connection.close()

    store = PipelineStore(path)
    try:
        (row,) = store.postings(live=False)
        assert (row.state, row.rank_score, row.reqs_met, row.reqs_total, row.fit) == ("needs_answers", 39, 1, 10, None)
        # Until its facts are read again the row has no stored fit number: the plain share stands in, in both orders.
        assert scout_new.fit_of(row) == 10 and store.postings_by_score(states=["needs_answers"]) == (row,)
        assert store.recovered_from is None
    finally:
        store.close()
    connection = sqlite3.connect(path)
    assert "fit" in {row[1] for row in connection.execute("PRAGMA table_info(posting)")}
    assert connection.execute("PRAGMA user_version").fetchone()[0] == pipeline_store.SCHEMA_VERSION == 5
    connection.close()
    PipelineStore(path).close()  # opening again changes nothing


# --- (1) weak fit ---------------------------------------------------------------------------


def test_a_weak_fit_is_not_in_needs_your_answers_has_its_own_state_and_asks_no_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = weak_fixture(fx, monkeypatch)
    names = {url: name for name, url in jobs.items()}

    # The default list and "needs your answers" leave it out; the count the header reads does not hold it.
    everything = _search(fx)
    assert sorted(names[job] for job in _jobs(everything)) == ["half_met", "high_rank", "unranked"]
    assert everything["counts"]["by_state"] == {"needs_answers": 3}  # type: ignore[index]
    assert (everything["counts"]["matched"], everything["counts"]["weak_fit"]) == (3, 1)  # type: ignore[index]
    waiting = _search(fx, states=["needs_answers"])
    assert jobs["weak"] not in _jobs(waiting) and waiting["counts"]["matched"] == 3  # type: ignore[index]
    assert jobs["weak"] not in _jobs(_search(fx, states=["assessed"]))  # hidden unless its own state is asked for

    # Its own state, listed only when asked for; it asks no question, and says its one fit number.
    weak = _search(fx, states=["weak_fit"])
    (row,) = _rows(weak)
    assert (row["job_identity"], row["state"], row["fit"], row["rank_score"]) == (jobs["weak"], "weak_fit", 14, 39)
    assert row["open_questions"] == [] and row["score_text"] == "Weak fit · fit 14% · 1 of 10 requirements · rank 39"
    assert weak["counts"]["by_state"] == {"weak_fit": 1} and weak["counts"]["weak_fit"] == 1  # type: ignore[index]
    assert sorted(names[job] for job in _jobs(_search(fx, states=["needs_answers", "weak_fit"]))) == ["half_met", "high_rank", "unranked", "weak"]

    # The ones the rule keeps still wait on answers and still ask: a high rank, half the requirements met, no rank yet.
    by_name = {names[str(item["job_identity"])]: item for item in _rows(everything)}
    assert {name: item["state"] for name, item in by_name.items()} == {"high_rank": "needs_answers", "half_met": "needs_answers", "unranked": "needs_answers"}
    assert len(by_name["high_rank"]["open_questions"]) == 3 and by_name["high_rank"]["fit"] == 14  # type: ignore[arg-type]
    assert (by_name["half_met"]["fit"], by_name["half_met"]["rank_score"]) == (50, 39)

    # A job's own state (the job page, the Assessments list and its "needs your answers" count) says the same.
    sources = JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.base.gig.resolved)
    own = {name: sources.state_for(url, profile_id=fx.default_profile_id) for name, url in jobs.items()}
    assert {name: state.state for name, state in own.items()} == {
        "weak": "weak_fit", "high_rank": "needs_answers", "half_met": "needs_answers", "unranked": "needs_answers",
    }
    assert own["weak"].next_events == ("applied",) and own["weak"].to_json()["state"] == "weak_fit"

    # ``scout new``: not listed, counted, and how to list them is said; never one that "still needs attention".
    grid = _new(fx, peek=True, assess=False)
    assert jobs["weak"] not in _jobs(grid) and grid["counts"]["weak_fit"] == 1 and grid["counts"]["new"] == 4  # type: ignore[index]
    assert "1 weak fit is not listed" in str(grid["message"]) and "gigai scout jobs list --state weak_fit" in str(grid["message"])
    scout_new.mark_all_seen(fx.home_root, fx.target, now=NOW)
    nothing = _new(fx, peek=True, now=NOW.replace(hour=16))
    assert nothing["status"] == "nothing_new" and jobs["weak"] not in _jobs(nothing) and len(_rows(nothing)) == 3

    # The terminal: the weak fit is listed with --state weak_fit and by name only there.
    listed = CliRunner().invoke(cli, ["scout", "jobs", "list", "--state", "weak_fit", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert listed.exit_code == 0 and "Weak fit · fit 14% · 1 of 10 requirements · rank 39" in listed.output, listed.output
    plain = CliRunner().invoke(cli, ["scout", "jobs", "list", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert plain.exit_code == 0 and jobs["weak"] not in plain.output and "Weak fit" not in plain.output, plain.output


def test_the_weak_fit_numbers_are_a_setting_and_a_change_is_seen_by_the_next_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = weak_fixture(fx, monkeypatch)
    assert _search(fx)["counts"]["weak_fit"] == 1  # type: ignore[index]

    _write_fit_setting(fx, weak_fit_below_rank=30)  # rank 39 is no longer low
    relaxed = _search(fx)
    assert relaxed["counts"]["weak_fit"] == 0 and jobs["weak"] in _jobs(relaxed)  # type: ignore[index]
    assert relaxed["counts"]["by_state"] == {"needs_answers": 4}  # type: ignore[index]

    _write_fit_setting(fx, weak_fit_below_percent=60, weak_fit_below_rank=50)  # 50% met is now a low share too
    stricter = _search(fx)
    assert stricter["counts"]["weak_fit"] == 2 and sorted(_jobs(stricter)) == sorted([jobs["high_rank"], jobs["unranked"]])  # type: ignore[index]


def test_a_saved_answer_never_queues_a_weak_fit_in_the_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = weak_fixture(fx, monkeypatch)
    _search(fx)  # the read model holds the rank scores
    story_bank.save_answer(
        home_root=fx.home_root, target=fx.target, question_id=TERRAFORM[0], answer="Yes, three years of Terraform modules.",
        question=TERRAFORM[1],
    )
    entry = story_bank.get_answer(home_root=fx.home_root, target=fx.target, question_id=TERRAFORM[0])
    assert entry is not None

    pending = triggers.pending_answer(fx.home_root, fx.target, entry)
    assert {pair[1] for pair in pending.pairs} == set(jobs.values())  # all four asked the question
    fired = pending.fire()

    queued = {str(item["job_identity"]) for item in (*fired.enqueued, *fired.awaiting)}
    assert queued == {jobs["high_rank"], jobs["half_met"], jobs["unranked"]}
    assert [(item["job_identity"], item["error_code"]) for item in fired.skipped] == [(jobs["weak"], "weak_fit")]
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        assert store.steps(job=jobs["weak"]) == () and store.steps(job=jobs["high_rank"]) != ()
    finally:
        store.close()


# --- (2) sort by real fit -------------------------------------------------------------------


def test_rows_come_by_fit_then_rank_then_newest_inside_a_group_and_each_says_its_one_fit_number(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("sorta", [lever_job("sorta", n) for n in range(1, 6)], seen_at=days_ago(3))
    fx.seed("sortb", [lever_job("sortb", 7)], seen_at=days_ago(1))  # its own text: the rank cache is keyed by content
    jobs = {name: job_url("sorta", n) for n, name in enumerate(("must_met", "nice_met", "same_fit_low_rank", "older", "not_assessed"), start=1)}
    jobs["newer"] = job_url("sortb", 7)
    # Must-haves met, nice-to-haves not: 2 of 4 plain (50%), 4 of 6 weighted (67%).
    must_met = matrix_answer([("Hard A", "hard", "met"), ("Hard B", "hard", "met"), ("Nice A", "nice_to_have", "unmet"), ("Nice B", "nice_to_have", "unmet")])
    # Nice-to-haves met, a must-have open: 2 of 3 plain (67%), 2 of 4 weighted (50%).
    nice_met = matrix_answer([("Nice A", "nice_to_have", "met"), ("Nice B", "nice_to_have", "met"), ("Askable A", "askable", "unclear")])
    assess_one(fx, jobs["must_met"], must_met)
    assess_one(fx, jobs["nice_met"], nice_met)
    assess_one(fx, jobs["same_fit_low_rank"], must_met)
    assess_one(fx, jobs["older"], must_met)
    assess_one(fx, jobs["newer"], must_met)
    seed_rank(fx, monkeypatch, {
        jobs["must_met"]: 60, jobs["nice_met"]: 95, jobs["same_fit_low_rank"]: 55, jobs["older"]: 52, jobs["newer"]: 52, jobs["not_assessed"]: 99,
    })
    names = {url: name for name, url in jobs.items()}
    # Matched group: fit 67 everywhere, so the rank (60, 55, 52, 52), then the newest of the two at 52. The plain share
    # (50%) would have put the 67%-plain "nice_met" first, and the old order (rank first) too: it has rank 95.
    expected = ["must_met", "same_fit_low_rank", "newer", "older", "nice_met", "not_assessed"]

    listed, grid = _search(fx), _new(fx, peek=True, assess=False)

    for response in (listed, grid):
        rows = _rows(response)
        assert [names[str(row["job_identity"])] for row in rows] == expected
        by_name = {names[str(row["job_identity"])]: row for row in rows}
        assert (by_name["must_met"]["fit"], by_name["must_met"]["score"], by_name["must_met"]["rank_score"]) == (67, 50, 60)
        assert (by_name["nice_met"]["fit"], by_name["nice_met"]["score"], by_name["nice_met"]["state"]) == (50, 67, "matched")
        assert by_name["must_met"]["score_text"] == "Matched · fit 67% · 2 of 4 requirements · rank 60"
        assert by_name["not_assessed"]["fit"] is None and by_name["not_assessed"]["score_text"] == "rank 99 · not assessed"

    # A needs-answers row never climbs above a matched one on its fit number: the verdict group comes first.
    assess_one(fx, jobs["not_assessed"], matrix_answer([("Hard A", "hard", "met"), ("Hard B", "hard", "met"), ("Hard C", "hard", "met"), ("Askable A", "askable", "unclear")], questions=1))
    again = _rows(_search(fx))
    assert [names[str(row["job_identity"])] for row in again] == [*expected[:5], "not_assessed"]
    assert (again[-1]["state"], again[-1]["fit"]) == ("needs_answers", 75)

    # The SQL order ("the 10 that still need attention") is the same key.
    scout_new.mark_all_seen(fx.home_root, fx.target, now=NOW)
    nothing = _new(fx, peek=True, now=NOW.replace(hour=16))
    assert nothing["status"] == "nothing_new"
    assert [names[str(row["job_identity"])] for row in _rows(nothing)] == [*expected[:5], "not_assessed"]

    # The terminal table of ``scout new`` says the fit number in its score column.
    text = CliRunner().invoke(cli, fx.cli("--peek", "--no-assess"))
    assert text.exit_code == 0 and "fit 67%" in text.output and "fit 75%" in text.output, text.output


# --- (3) the assess threshold ---------------------------------------------------------------


def _states(fx: PostingsFixture) -> dict[str, str]:
    return {str(row["job_identity"]): str(row["state"]) for row in _rows(_search(fx, states=["assessed", "not_assessed", "weak_fit"]))}


def test_scout_new_assesses_only_rank_50_or_more_and_asks_about_the_low_ranked_separately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = ranked_new(fx, monkeypatch)
    low = {jobs["r49"], jobs["r20"]}

    ask = _new(fx, peek=True)
    since = str(ask["since"])
    assert (ask["status"], ask["counts"]["new"], ask["counts"]["to_assess"], ask["counts"]["low_rank_skipped"]) == ("ask", 5, 3, 2)  # type: ignore[index]
    assert (ask["question"]["to_assess"], ask["question"]["low_rank_skipped"]) == (3, 2)  # type: ignore[index]
    assert "5 new postings" in ask["question"]["text"] and "Assess 3 of them (2 low-ranked ones are a separate question)?" in ask["question"]["text"]  # type: ignore[index]
    question = ask["low_rank_question"]
    assert (question["kind"], question["skipped"], question["min_rank"], question["estimate"]["calls"]) == ("assess_low_rank", 2, 50, 2)  # type: ignore[index]
    assert str(question["text"]).startswith("2 low-ranked ones are skipped (rank below 50); assess those too? ~2 calls")  # type: ignore[index]
    assert question["yes"] == {  # type: ignore[index]
        "cli": f"gigai scout new --yes --include-low-rank --since {since}",
        "api": {"method": "POST", "path": "/api/new", "body": {"assess": True, "include_low_rank": True, "since": since}},
    }
    assert ask["fit"] == {"assess_min_rank": 50, "weak_fit_below_percent": 40, "weak_fit_below_rank": 50, "source": "default"}
    assert fx.base.model.assess_prompts == []  # a question spends nothing

    # The yes: three model calls, for rank 80, rank 50 and the one not ranked yet. The two low-ranked stay not assessed.
    yes = _new(fx, peek=True, assess=True, since=since)
    assert yes["assessed"]["requested"] == 3 and yes["assessed"]["assessed"] == 3 and len(fx.base.model.assess_prompts) == 3  # type: ignore[index]
    states = _states(fx)
    assert {job for job, state in states.items() if state == "not_assessed"} == low
    assert (yes["status"], yes["question"], yes["counts"]["to_assess"], yes["counts"]["low_rank_skipped"]) == ("new", None, 0, 2)  # type: ignore[index]
    assert yes["low_rank_question"]["skipped"] == 2  # type: ignore[index]
    shown = scout_new.render(yes)
    assert "2 low-ranked ones are skipped (rank below 50); assess those too? ~2 calls" in shown
    assert f"  Yes: gigai scout new --yes --include-low-rank --since {since}" in shown

    # A plain look afterwards does not ask again for them as if they were new work: the separate question stays.
    again = _new(fx, peek=True, since=since)
    assert (again["status"], again["question"]) == ("new", None) and again["low_rank_question"]["skipped"] == 2  # type: ignore[index]

    # Its own yes: the two low-ranked ones, two more calls.
    more = _new(fx, peek=True, assess=True, include_low_rank=True, since=since)
    assert more["assessed"]["requested"] == 2 and len(fx.base.model.assess_prompts) == 5  # type: ignore[index]
    assert more["low_rank_question"] is None and more["counts"]["low_rank_skipped"] == 0  # type: ignore[index]
    assert "not_assessed" not in _states(fx).values()


def test_the_assess_threshold_is_a_setting_and_the_cli_takes_the_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = ranked_new(fx, monkeypatch)

    _write_fit_setting(fx, assess_min_rank=60)
    stricter = _new(fx, peek=True)
    assert (stricter["counts"]["to_assess"], stricter["counts"]["low_rank_skipped"], stricter["low_rank_question"]["min_rank"]) == (2, 3, 60)  # type: ignore[index]
    _write_fit_setting(fx, assess_min_rank=0)
    off = _new(fx, peek=True)
    assert (off["counts"]["to_assess"], off["counts"]["low_rank_skipped"], off["low_rank_question"]) == (5, 0, None)  # type: ignore[index]
    assert off["fit"]["assess_min_rank"] == 0 and off["fit"]["source"] == "setting"  # type: ignore[index]
    settings_path(fx.home_root, fx.target).unlink()

    alone = CliRunner().invoke(cli, fx.cli("--include-low-rank", "--json"))
    assert alone.exit_code != 0 and "--include-low-rank goes with --yes or --reassess-stale" in alone.output
    done = CliRunner().invoke(cli, fx.cli("--yes", "--include-low-rank", "--peek", "--json"))
    assert done.exit_code == 0, done.output
    answer = json.loads(done.stdout)
    assert answer["assessed"]["requested"] == 5 and answer["low_rank_question"] is None and answer["counts"]["low_rank_skipped"] == 0
    assert set(_states(fx)) >= set(jobs.values()) and "not_assessed" not in _states(fx).values()


def test_assess_these_leaves_the_low_ranked_out_by_default_and_reports_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = ranked_new(fx, monkeypatch)
    low = sorted([jobs["r49"], jobs["r20"]])

    ask = _these(fx)
    assert (ask["status"], ask["counts"]["to_assess"], ask["counts"]["low_rank_skipped"]) == ("ask", 3, 2)  # type: ignore[index]
    assert ask["question"]["to_assess"] == 3 and ask["question"]["low_rank_skipped"] == 2  # type: ignore[index]
    assert ask["question"]["text"].startswith("Assess 3 postings")  # type: ignore[index]
    offer = ask["low_rank"]
    assert (offer["kind"], offer["skipped"], offer["min_rank"], offer["estimate"]["calls"]) == ("assess_low_rank", 2, 50, 2)  # type: ignore[index]
    assert str(offer["text"]).startswith("2 low-ranked ones are skipped (rank below 50); assess those too? ~2 calls")  # type: ignore[index]
    assert offer["yes"]["api"]["body"] == {"approve": True, "include_low_rank": True}  # type: ignore[index]
    assert fx.base.model.assess_prompts == []

    done = _these(fx, approve=True)
    assert done["status"] == "assessed" and done["assessed"]["requested"] == 3 and len(fx.base.model.assess_prompts) == 3  # type: ignore[index]
    assert done["approval"]["jobs"] == 3 and done["low_rank"]["skipped"] == 2  # type: ignore[index]
    assert sorted(job for job, state in _states(fx).items() if state == "not_assessed") == low

    # Only low-ranked postings selected (even by name): asked, never assessed by a plain approval.
    named = _these(fx, jobs=[jobs["r20"]])
    assert (named["status"], named["question"]["to_assess"], named["low_rank"]["skipped"]) == ("ask", 0, 1)  # type: ignore[index]
    assert str(named["low_rank"]["text"]).startswith("1 low-ranked one is skipped (rank below 50); assess that too? ~1 call") and "~1 calls" not in str(named["low_rank"]["text"])  # type: ignore[index]
    refused = _these(fx, jobs=[jobs["r20"]], approve=True)
    assert (refused["status"], refused["assessed"], refused["approval"]) == ("nothing_to_assess", None, None)
    assert refused["low_rank"]["skipped"] == 1 and len(fx.base.model.assess_prompts) == 3  # type: ignore[index]
    assert "1 low-ranked one is skipped" in posting_search.render(refused) and "--include-low-rank" in posting_search.render(refused)

    # Its own yes.
    both = _these(fx, approve=True, include_low_rank=True)
    assert both["status"] == "assessed" and both["assessed"]["requested"] == 2 and both["low_rank"] is None  # type: ignore[index]
    assert len(fx.base.model.assess_prompts) == 5 and "not_assessed" not in _states(fx).values()


def test_re_assessing_old_assessments_leaves_the_low_ranked_out_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("oldr", [lever_job("oldr", n) for n in (1, 2)], seen_at=days_ago(20))
    high, low = job_url("oldr", 1), job_url("oldr", 2)
    postings.refresh(fx.home_root, fx.target, now=NOW)
    _old_run(fx, [high, low])
    seed_rank(fx, monkeypatch, {high: 70, low: 30})

    look = _new(fx, peek=True)
    stale = look["stale_question"]
    assert (look["counts"]["only_stale"], stale["to_reassess"], stale["low_rank_skipped"]) == (2, 1, 1)  # type: ignore[index]
    assert stale["text"].startswith("1 has only an old assessment; re-assess? ~1 call") and "~1 calls" not in stale["text"]  # type: ignore[index]
    assert "(1 more is low-ranked, rank below 50, and left out; add --include-low-rank to include them)" in stale["text"]  # type: ignore[index]

    done = _new(fx, peek=True, assess=False, reassess_stale=True)
    assert done["reassessed"]["requested"] == 1 and len(fx.base.model.assess_prompts) == 1  # type: ignore[index]
    left = done["stale_question"]
    assert (left["to_reassess"], left["low_rank_skipped"]) == (0, 1)  # type: ignore[index]
    assert left["text"].startswith("1 low-ranked (rank below 50) has only an old assessment and is left out; re-assess that one too? ~1 call") and "~1 calls" not in left["text"]  # type: ignore[index]
    assert "--reassess-stale --include-low-rank" in left["yes"]["cli"] and left["yes"]["api"]["body"]["include_low_rank"] is True  # type: ignore[index]

    rest = _new(fx, peek=True, assess=False, reassess_stale=True, include_low_rank=True)
    assert rest["reassessed"]["requested"] == 1 and rest["stale_question"] is None and len(fx.base.model.assess_prompts) == 2  # type: ignore[index]


# --- the threshold analysis the operator runs on a real home (read only, numbers only) ------


def test_the_threshold_report_counts_what_a_threshold_would_have_skipped_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("assess_threshold_report", Path(__file__).resolve().parents[3] / "tools" / "assess_threshold_report.py")
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    weak_fixture(fx, monkeypatch)  # needs answers: rank 39 (weak fit), 80, 39 (half met), not ranked
    ranked_new(fx, monkeypatch)  # rank 80, 50, 49, 20, not ranked
    done = _these(fx, approve=True, include_low_rank=True)  # all five come out matched
    assert done["assessed"]["requested"] == 5  # type: ignore[index]
    _search(fx)
    db = pipeline_path(fx.home_root, fx.target)
    assert tool.find_db(fx.home_root) == db
    before = (db.stat().st_mtime_ns, db.read_bytes(), sorted(path.name for path in db.parent.iterdir() if "sqlite" not in path.name))

    rows, opened = tool.read_rows(db)
    result = tool.report(rows, (40, 50, 60))

    assert opened == "read-only"
    assert (result["assessments"], result["ranked"], result["not_ranked"]) == (9, 7, 2)
    assert result["by_state"] == {"matched": 5, "needs_answers": 3, "weak_fit": 1}
    by_threshold = {row["threshold"]: row for row in result["thresholds"]}
    # Below 40: the weak fit and the half-met one (rank 39, both wait on answers) and the rank 20 (matched).
    assert (by_threshold[40]["skipped"], by_threshold[40]["skipped_matched"], by_threshold[40]["skipped_needs_answers"], by_threshold[40]["cost_of_threshold"]) == (3, 1, 2, 3)
    assert (by_threshold[50]["skipped"], by_threshold[50]["skipped_matched"], by_threshold[50]["skipped_needs_answers"], by_threshold[50]["assessed"]) == (4, 2, 2, 5)
    assert (by_threshold[60]["skipped"], by_threshold[60]["skipped_matched"], by_threshold[60]["cost_of_threshold"]) == (5, 3, 5)
    grid = {(row["below_percent"], row["below_rank"]): row["weak_fit"] for row in result["weak_fit_grid"]}
    assert result["needs_answers"] == 4 and grid[(40, 50)] == 1 and grid[(30, 40)] == 1 and grid[(50, 60)] == 1

    # The command: tables of numbers, never a posting's URL, title or company; and the home is exactly as it was.
    # (The window starts at the first day an assessment was made: the store stamps them with the machine's clock, and a
    # run across midnight UTC has two days.)
    assert sum(result["by_day"].values()) == 9
    assert tool.main(["--home", str(fx.home_root), "--from", f"{min(result['by_day'])}T00:00"]) == 0
    printed = capsys.readouterr().out
    assert "       50 |       4 |              5 |                2 |             2" in printed, printed
    assert "http" not in printed and "lever" not in printed and "Staff" not in printed and str(fx.home_root) not in printed
    assert tool.main(["--db", str(db), "--day", "2020-01-01", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["assessments"] == 0
    # The database is byte for byte what it was (SQLite's own -wal / -shm bookkeeping beside it is not data).
    assert (db.stat().st_mtime_ns, db.read_bytes(), sorted(path.name for path in db.parent.iterdir() if "sqlite" not in path.name)) == before
