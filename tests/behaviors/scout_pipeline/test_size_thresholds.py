"""0110-10-hf2: every size threshold of the read model, `scout new` and the stores, CROSSED with its real value.

0.1.10.9 crashed on a line behind ``total >= 500``: every fixture had fewer boards, and the tests of the
neighbouring thresholds lowered the constant instead of raising the data (``SMALL_BUILD_BOARDS`` set to 0 or
2, ``FAILED_BOARDS_LISTED`` to 2). A lowered constant runs the branch; it does not show that the branch
works at the size it is for. Each test here leaves the constant alone and gives the code more than it:

* ``postings.SMALL_BUILD_BOARDS`` (500): a build of 501 boards is large (a caller with a wait is answered at
  once, "preparing"), one of 500 is small (the caller waits for it);
* ``postings.TEXT_CACHE_BOARDS`` (4,000): the 4,001st board's texts push the least recently read board out;
* the stores' 500-value SQL chunks: ``PipelineStore.postings(jobs=...)`` and ``replace_board_postings`` with
  more than 500 (and more than 1,000) values, ``TagStore.get_many`` with 1,001 titles;
* ``scout_new.PROGRESS_EVERY_ITEMS`` (20): a batch of 21 is throttled, one of 20 says every result;
* the wording thresholds: 120 minutes ("~2.0 h"), 1,000 tokens ("~1k tokens") in the question `scout new`
  and `scout jobs assess` ask, from enough postings to reach them;
* ``model_tag.LOOKUP_WINDOW`` (4 x 2,000 titles looked for): the lookup forgets and scans again;
* ``rank_run.RUN_MAX_CALLS`` (180): a rank run over 6,000 postings and more is capped there;
* two inputs that are refused over a size (no test had sent one): ``gigai run-input add --stdin`` over 262,144
  bytes, ``gigai scout-answer save`` with an answer over 16,000 characters;
* the pages: ``posting_search.MAX_LIMIT`` (200 rows of "assess these"), ``overview.JOBS_LIMIT`` (200 pipeline
  jobs) and ``overview.ERRORS_LIMIT`` (20 errors): one more than each, counted in full and listed up to it.

The thresholds that already had a crossing test are listed in the worker report (0.1.10.10-hf2.md); the
board-count ones of the build itself (500 for the progress lines, 400 a chunk) are crossed by
``test_scout_new_cold_build_progress.py`` and ``test_operator_sized_home.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import postings, posting_search, scout_new
from gigai.scout.find_jobs import model_tag, rank_run
from gigai.scout.find_jobs.company_catalog import CompanyRecord
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.posting_tags import default_store, normalize_title, tag_new_titles
from gigai.scout.find_jobs.watchlist import seed_watchlist_from_catalog
from gigai.scout.pipeline import overview as pipeline_overview
from gigai.scout.pipeline.store import PipelineStore, PostingRecord, pipeline_path

from tests.behaviors.scout_pipeline.test_posting_model_single_flight import _Matches
from tests.support.operator_home import _Catalog
from tests.support.pipeline_fixtures import assess_base
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago, lever_job

_P = "profile_7f3c"


def _digest(*parts: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _row(job: str, board: str) -> PostingRecord:
    return PostingRecord(
        job=job, profile_id=_P, board=board, first_seen="2026-10-01T08:00:00.000000Z", published_at=None, removed_at=None,
        listing_digest=_digest("c"), listing_known=True, rank_score=None, match_rank=1, state="not_assessed", stale_code=None,
        assessed_at=None, reqs_met=None, reqs_total=None, open_questions=0, tailored=False, label=None, ats_score=None,
        pinned_digest=_digest("r"), settings_digest=_digest("s"), updated_at="2026-10-02T14:02:00.000000Z",
    )


# ---------------------------------------------------------------------------- the read model: a small build and a large one


def _seed_boards(fx: PostingsFixture, count: int) -> None:
    """``count`` watched boards with one matched posting each; the watchlist in ONE journal transition."""

    class _Prefs:
        countries = ()
        exclude_companies = ()
        watch_companies = ()

    records = tuple(
        CompanyRecord(name=f"Threshold Company {n:04d}", provider=ATSProvider.LEVER, board_token=f"th{n:04d}", board_url=f"https://jobs.lever.co/th{n:04d}", hq_country="US")
        for n in range(count)
    )
    assert seed_watchlist_from_catalog(fx.home_root, fx.target, prefs=_Prefs(), catalog=_Catalog(records)).added == count
    for n in range(count):
        fx.seed(f"th{n:04d}", [lever_job(f"th{n:04d}", 0, title=TITLE_BOTH)], seen_at=days_ago(1), watch=False)


def _held_build(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, threading.Thread, list[object]]:
    """A first build started in a thread and held open once it has counted its boards."""

    release = threading.Event()
    matches = _Matches(monkeypatch, hold=release)
    built: list[object] = []

    def build() -> None:
        try:
            built.append(postings.refresh(fx.home_root, fx.target, now=NOW))
        except BaseException as exc:  # noqa: BLE001 - the assertion on ``built`` shows it
            built.append(exc)

    builder = threading.Thread(target=build)
    builder.start()
    assert matches.started.wait(60)
    return release, builder, built


def test_a_build_of_501_boards_is_large_a_caller_with_a_wait_is_answered_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boards = postings.SMALL_BUILD_BOARDS + 1
    assert boards == 501  # the real threshold: nothing lowers it here
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed_boards(fx, boards)
    release, builder, built = _held_build(fx, monkeypatch)
    try:
        started = time.monotonic()
        with pytest.raises(postings.PostingModelPreparing) as caught:
            postings.refresh(fx.home_root, fx.target, now=NOW, wait=30.0)
        waited = time.monotonic() - started
    finally:
        release.set()
        builder.join(120)

    assert waited < 10, waited  # not the 30 s of its wait, and not the build (still held open)
    assert caught.value.progress["boards_total"] == boards and caught.value.progress["state"] == postings.STATE_PREPARING
    (result,) = built
    assert isinstance(result, postings.RefreshResult) and result.rows == 2 * boards  # both profiles match every board's posting
    assert postings._flight(fx.home_root, fx.target).last_boards == boards


def test_a_build_of_500_boards_is_small_a_caller_with_a_wait_waits_for_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boards = postings.SMALL_BUILD_BOARDS
    assert boards == 500
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed_boards(fx, boards)
    release, builder, built = _held_build(fx, monkeypatch)
    answers: list[object] = []

    def call() -> None:
        try:
            answers.append(postings.refresh(fx.home_root, fx.target, now=NOW, wait=120.0))
        except BaseException as exc:  # noqa: BLE001 - the assertion below shows it
            answers.append(exc)

    caller = threading.Thread(target=call)
    try:
        caller.start()
        caller.join(1.5)
        held = caller.is_alive() and not answers  # the build is still held open: a small build is waited for
    finally:
        release.set()
        builder.join(120)
        caller.join(120)

    assert held
    (answer,) = answers
    assert isinstance(answer, postings.RefreshResult) and answer.rows == 2 * boards, answer  # the rows, not "preparing"
    assert isinstance(built[0], postings.RefreshResult)


# ---------------------------------------------------------------------------- the read model: the kept posting texts


def test_the_4001st_board_of_kept_texts_pushes_out_the_least_recently_read_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(postings, "_TEXTS", {})  # this test's own cache; the limit is the real one
    limit = postings.TEXT_CACHE_BOARDS
    assert limit == 4000
    stamp = (1, 2)
    for board in range(limit):
        postings._keep_texts(("home", f"lever:b{board:05d}"), stamp, {f"job-{board}": None})
    assert len(postings._TEXTS) == limit
    # Board 0 is read again (it now leaves last); then one more board than the cache holds is kept.
    assert postings._kept_texts(("home", "lever:b00000"), stamp, {"job-0"}) == {"job-0": None}

    postings._keep_texts(("home", "lever:one-more"), stamp, {"job-x": None})

    assert len(postings._TEXTS) == limit
    assert postings._kept_texts(("home", "lever:b00001"), stamp, {"job-1"}) is None  # the least recently read board left
    assert postings._kept_texts(("home", "lever:b00000"), stamp, {"job-0"}) == {"job-0": None}
    assert postings._kept_texts(("home", "lever:one-more"), stamp, {"job-x"}) == {"job-x": None}


# ---------------------------------------------------------------------------- the stores: more values than one SQL statement takes


def test_the_posting_store_reads_and_drops_more_than_500_and_more_than_1000_at_once(tmp_path: Path) -> None:
    store = PipelineStore(tmp_path / "pipeline.sqlite")
    boards = [f"lever:b{n:04d}" for n in range(1201)]  # three chunks of 500, the last one short
    jobs = [f"https://jobs.example.test/b{n:04d}/1" for n in range(1201)]
    try:
        # Written the way a build writes them: a few hundred boards a call.
        for start in range(0, len(boards), 400):
            part = range(start, min(start + 400, len(boards)))
            store.replace_board_postings(_P, {boards[n]: _digest("stamp", boards[n]) for n in part}, [_row(jobs[n], boards[n]) for n in part])
        assert len(store.posting_board_stamps(_P)) == 1201

        # 1. A read of 1,201 named postings (`jobs=`): every one comes back, once.
        found = store.postings(jobs=jobs)
        assert sorted(row.job for row in found) == sorted(jobs)
        assert sorted(row.job for row in store.postings(jobs=jobs[:501])) == sorted(jobs[:501])  # one over a chunk
        assert sorted(row.job for row in store.postings(jobs=jobs[:500])) == sorted(jobs[:500])  # exactly a chunk

        # 2. One call replaces 501 boards (one over a chunk): each has its new row and its new stamp, the rest are untouched.
        again = {boards[n]: _digest("again", boards[n]) for n in range(501)}
        store.replace_board_postings(_P, again, [_row(jobs[n] + "-new", boards[n]) for n in range(501)])
        stamps = store.posting_board_stamps(_P)
        assert all(stamps[board] == stamp for board, stamp in again.items()) and stamps[boards[501]] == _digest("stamp", boards[501])
        assert sorted(row.job for row in store.postings(live=False)) == sorted([job + "-new" for job in jobs[:501]] + jobs[501:])

        # 3. 1,101 boards are no longer watched: ONE call drops their rows and their stamps (what a build does for them).
        store.replace_board_postings(_P, {board: None for board in boards[:1101]}, [])
        assert sorted(store.posting_board_stamps(_P)) == boards[1101:]
        assert sorted(row.job for row in store.postings(live=False)) == sorted(jobs[1101:])
    finally:
        store.close()


def test_the_tag_store_reads_1001_titles_at_once(tmp_path: Path) -> None:
    titles = [f"Staff Engineer, Team {n:04d}" for n in range(1001)]  # three chunks of 500, the last one of one
    store = default_store(tmp_path)
    try:
        assert tag_new_titles(store, titles).tagged == 1001
        keys = [normalize_title(title) for title in titles]

        found = store.get_many(keys + ["a title that was never tagged"])

        assert sorted(found) == sorted(keys) and all(found[key].level == "staff" for key in keys)
        assert store.count() == 1001
    finally:
        store.close()


# ---------------------------------------------------------------------------- `scout new`: the progress lines of a batch


def _said(total: int, *, average: float | None = None, concurrency: int = 1) -> list[str]:
    lines: list[str] = []
    clock = [0.0]
    progress = scout_new.BatchProgress(total, lines.append, average_seconds=average, concurrency=concurrency, clock=lambda: clock[0])
    progress.start()
    for _ in range(total):
        clock[0] += 1.0  # one result a second: under PROGRESS_EVERY_SECONDS between any two
        progress.done()
    return lines


def test_a_batch_of_20_says_every_result_and_one_of_21_a_line_every_10_seconds() -> None:
    assert (scout_new.PROGRESS_EVERY_ITEMS, scout_new.PROGRESS_EVERY_SECONDS) == (20, 10.0)

    twenty, twenty_one = _said(20), _said(21)

    assert [line.split(" ·")[0] for line in twenty] == ["assessing 20 postings, 1 at a time"] + [f"assessed {n} of 20" for n in range(1, 21)]
    # 21 results, one a second: the first line, then one every 10 seconds, and always the last.
    assert [line.split(" ·")[0] for line in twenty_one] == ["assessing 21 postings, 1 at a time", "assessed 10 of 21", "assessed 20 of 21", "assessed 21 of 21"]
    assert twenty_one[1] == "assessed 10 of 21 · ~1 min left"  # 11 left at the batch's own pace of one a second


def test_time_left_is_said_in_hours_from_120_minutes() -> None:
    assert scout_new._about(119 * 60) == "~119 min" and scout_new._about(120 * 60) == "~2.0 h" and scout_new._about(250 * 60) == "~4.2 h"
    # A real batch that long: 2,000 postings, 30 s a call on record, 4 at a time = 15,000 s.
    lines = _said(2000, average=30.0, concurrency=4)
    assert lines[0] == "assessing 2000 postings, 4 at a time · ~4.2 h"
    assert lines[-1] == "assessed 2000 of 2000" and len(lines) == 1 + 2000 // 10


# ---------------------------------------------------------------------------- the question's cost: 1,000 tokens and more


def test_the_question_says_k_tokens_when_enough_postings_wait_to_reach_1000(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    assess_base(fx.base)  # one assess call on record: 10 in + 20 out tokens, what the estimate is made from
    calls = fx.base.model.calls
    fx.seed("few", [lever_job("few", n) for n in range(33)], seen_at=days_ago(1))

    under = posting_search.assess_these(fx.home_root, fx.target, now=NOW)
    assert under["status"] == "ask" and under["question"]["estimate"]["tokens"] == 33 * 30 == 990  # type: ignore[index]
    assert str(under["question"]["text"]).endswith("? ~33 calls")  # type: ignore[index]  # under 1,000 tokens: no cost is said

    # 54 postings to assess. 0110-10-11: a yes is the newest 50 at a time, so the cost said is the 50's: 1,500 tokens.
    fx.seed("more", [lever_job("more", n) for n in range(21)], seen_at=days_ago(1))
    over = posting_search.assess_these(fx.home_root, fx.target, now=NOW)
    assert (over["question"]["to_assess"], over["question"]["batch"]) == (54, 50)  # type: ignore[index]
    assert over["question"]["estimate"]["tokens"] == 50 * 30 == 1500  # type: ignore[index]
    assert str(over["question"]["text"]).endswith("? ~50 calls, ~2k tokens (4 more after these 50)")  # type: ignore[index]

    asked = scout_new.scout_new(fx.home_root, fx.target, now=NOW)
    assert asked["status"] == "ask" and asked["question"]["estimate"]["tokens"] == 1500  # type: ignore[index]
    assert str(asked["question"]["text"]).endswith(  # type: ignore[index]
        "Assess the top 50 by rank of 54 not assessed yet? ~50 calls, ~2k tokens (4 more after these 50)"
    )
    assert (scout_new._tokens(999), scout_new._tokens(1000)) == (", ~999 tokens", ", ~1k tokens")
    assert fx.base.model.calls == calls  # the questions called no model


# ---------------------------------------------------------------------------- the tag lane's title lookup


def test_the_title_lookup_forgets_after_8000_titles_looked_for_and_scans_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("look", [lever_job("look", 0, title="Staff Engineer, Lookup")], seen_at=days_ago(1))
    listed = normalize_title("Staff Engineer, Lookup")
    window = 4 * model_tag.LOOKUP_WINDOW
    assert window == 8000
    lookup = model_tag.IndexTitles(fx.home_root)

    # A queue that has moved past 8,001 titles (none of them listed any more), then the listed one.
    gone = [f"gone title {n}" for n in range(window + 1)]
    assert lookup.lookup(gone) == {} and len(lookup._looked_for) == window + 1
    found = lookup.lookup([listed])

    assert found == {listed: ("Staff Engineer, Lookup", found[listed][1])}
    assert lookup._looked_for == {listed}  # over the window: what was kept is dropped, only this lookup's titles are remembered
    # Under the window nothing is dropped: the next title is added to what is remembered.
    assert lookup.lookup(["another gone title"]) == {} and lookup._looked_for == {listed, "another gone title"}


def test_a_rank_run_over_6000_postings_is_capped_at_180_calls() -> None:
    assert (rank_run.RUN_MAX_CALLS, rank_run.DEFAULT_BATCH_SIZE) == (180, 50)
    # One call a batch of 50 plus half again: 119 batches are 179 calls, 120 batches (6,000 postings) reach the cap.
    assert rank_run.run_call_cap(5950) == 179 and rank_run.run_call_cap(6000) == 180
    assert rank_run.run_call_cap(290_000) == 180  # every posting of the operator-sized home: still 180
    assert rank_run.run_call_cap(1) == 5 and rank_run.run_call_cap(0) == 4  # the floor: at least 4 beside the batches


# ---------------------------------------------------------------------------- the pages: counted in full, listed up to the limit


def test_assess_these_counts_205_postings_and_lists_the_50_a_yes_would_assess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert posting_search.MAX_LIMIT == 200
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("wide", [lever_job("wide", n) for n in range(205)], seen_at=days_ago(1))

    ask = posting_search.assess_these(fx.home_root, fx.target, now=NOW)

    assert ask["status"] == "ask" and ask["counts"]["to_assess"] == 205 and ask["question"]["to_assess"] == 205  # type: ignore[index]
    # 0110-10-11: all 205 are counted; the approval is the newest 50, and those are the rows listed (it listed 200 before).
    assert (ask["counts"]["batch"], ask["counts"]["more_after"], ask["question"]["batch"]) == (50, 155, 50)  # type: ignore[index]
    rows = ask["postings"]["rows"]  # type: ignore[index]
    assert len(rows) == 50 and len({row["job_identity"] for row in rows}) == 50
    assert fx.base.model.calls == 0


def test_the_pipeline_overview_counts_201_jobs_and_21_errors_and_lists_200_and_20(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (pipeline_overview.JOBS_LIMIT, pipeline_overview.ERRORS_LIMIT) == (200, 20)
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        for n in range(201):
            store.enqueue(
                fx.default_profile_id, f"https://jobs.example.test/acme/{n:04d}", "tailor", input_digest=_digest("in", str(n)),
                trigger="process_now", lane="claude_cli", model_target="claude_cli",
            )
        failed = 0
        while failed < 21:
            claim = store.claim(worker="w1", lanes=["claude_cli"])
            assert claim is not None, failed
            store.fail(claim, "assess_timeout")
            failed += 1
    finally:
        store.close()

    status = pipeline_overview.overview(fx.home_root, fx.target, busy=lambda: None)

    assert status["counts"]["jobs_total"] == 201 and sum(status["counts"]["jobs"].values()) == 201  # type: ignore[index]
    assert len(status["jobs"]) == 200 and len({item["job_identity"] for item in status["jobs"]}) == 200  # type: ignore[index,arg-type]
    errors = status["errors"]
    assert len(errors) == 20 and {error["error_code"] for error in errors} == {"assess_timeout"}  # type: ignore[index,arg-type]


# ---------------------------------------------------------------------------- inputs refused over a size


def test_a_pasted_run_input_of_262145_bytes_is_refused_and_one_of_262144_is_read(tmp_path: Path) -> None:
    base = ["run-input", "add", "--stdin", "--home", str(tmp_path / "home"), "--target", str(tmp_path / "target"), "--json"]

    over = CliRunner().invoke(cli, base, input=b"x" * 262145)

    assert over.exit_code != 0 and json.loads(over.output)["error"]["code"] == "run_input_too_large", over.output
    # One byte less passes the size check: it is refused later, for the home that does not exist, never for its size.
    at_limit = CliRunner().invoke(cli, base, input=b"x" * 262144)
    assert at_limit.exit_code != 0 and json.loads(at_limit.output)["error"]["code"] != "run_input_too_large", at_limit.output


def test_an_answer_of_16001_characters_is_refused_before_anything_is_read(tmp_path: Path) -> None:
    answer = tmp_path / "answer.txt"
    base = [
        "scout-answer", "save", "--record-id", "record_0", "--parent-revision", "revision_0", "--question-id", "cloud:gcp",
        "--answer-file", str(answer), "--operation-key", "answer-0", "--confirm", "--gig", "gig_0", "--home", str(tmp_path / "home"),
        "--target", str(tmp_path / "target"), "--json",
    ]
    answer.write_text("y" * 16_001, encoding="utf-8")

    over = CliRunner().invoke(cli, base)

    assert over.exit_code != 0 and json.loads(over.output)["error"]["code"] == "answer_invalid", over.output
    answer.write_text("y" * 16_000, encoding="utf-8")
    at_limit = CliRunner().invoke(cli, base)
    assert at_limit.exit_code != 0 and json.loads(at_limit.output)["error"]["code"] != "answer_invalid", at_limit.output
