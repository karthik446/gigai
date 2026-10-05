"""0.1.10.11 S9: "Assess these" is answered from the stored rows while a large build of the read model runs.

``GET /api/postings`` has answered from the rows as stored since 0110-9-01;
``POST /api/postings/assess`` waited for the build (8 to 10 s on the
operator-sized home when the build was every board). The postings it is asked
about are the ones the list showed, so it selects them from the same stored
rows. With no stored rows yet (the first build) it waits, as it always did:
only a GET answers ``202``.

It ACTS on rows (model calls), so it only acts on rows of FINISHED builds. A
build writes its rows a few boards at a time and each posting's best profile
only when it ends: a row the running build already wrote is not final. When
the selection holds such a row, or a named posting is not among the stored
rows, the build is waited for, as before. So the assessed set is exactly what
the stored rows of finished builds show.

A real in-process server; the build is held open by the test, so "during the
build" is a state, not a race.
"""

from __future__ import annotations

from pathlib import Path
import threading
import time

import httpx
import pytest

from gigai.scout import postings

from tests.support.posting_fixtures import TITLE_BOTH, days_ago, job_url, lever_job

from .test_postings_preparing_api import ANSWER_SECONDS, BOARDS, PER_BOARD, _hold_builds, _home, _Served, _timed


def test_assess_these_is_answered_from_the_stored_rows_while_a_large_build_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _home(tmp_path, monkeypatch)
    with _Served(fx) as served:
        client = httpx.Client(base_url=str(served.client.base_url), timeout=2 * ANSWER_SECONDS)
        first = client.get("/api/postings")  # a small build: waited for, the rows come back
        assert first.status_code == 200 and first.json()["counts"]["matched"] == BOARDS * PER_BOARD
        monkeypatch.setattr(postings, "SMALL_BUILD_BOARDS", 0)
        release, builds = _hold_builds(monkeypatch)
        fx.seed("pr00", [lever_job("pr00", n, title=TITLE_BOTH) for n in range(PER_BOARD + 2)], seen_at=days_ago(0.5), watch=False)
        assert client.get("/api/postings").status_code == 200  # starts the build of pr00: held open
        assert client.get("/api/postings/status").json()["state"] == "refreshing" and len(builds) == 1

        # The question, about two postings the list shows: answered at once, from the rows as stored.
        asked, seconds = _timed(lambda: client.post("/api/postings/assess", json={"jobs": [job_url("pr01", 0), job_url("pr02", 1)]}))
        assert asked.status_code == 200 and seconds < ANSWER_SECONDS, (asked.status_code, seconds)
        body = asked.json()
        assert body["status"] == "ask" and body["counts"]["selected"] == 2 and body["counts"]["to_assess"] == 2
        assert client.get("/api/postings/status").json()["state"] == "refreshing" and len(builds) == 1
        # A posting that is not among the stored rows (the running build is adding it): the build is waited for, as
        # before, so the answer is never "not found" for a posting the build was about to list.
        answers: list[httpx.Response] = []
        asking = threading.Thread(target=lambda: answers.append(served.client.post("/api/postings/assess", json={"jobs": [job_url("pr00", PER_BOARD + 1)]})))
        asking.start()
        asking.join(1.5)
        assert asking.is_alive() and not answers
        release.set()
        asking.join(60)
        assert not asking.is_alive() and answers[0].status_code == 200 and answers[0].json()["counts"]["selected"] == 1
        client.close()


def _hold_after_the_first_board(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, threading.Event]:
    """Every build writes its first board's rows, then stops until ``release``; ``reached`` is set when it has stopped there."""

    release, reached = threading.Event(), threading.Event()
    real = postings._match

    def held(plan, store, views, facts_of, progress):
        def report(phase: str, done: int, total: int) -> None:
            if progress is not None:
                progress(phase, done, total)
            if done > 0 and not reached.is_set():
                reached.set()
                assert release.wait(60)

        return real(plan, store, views, facts_of, report)

    monkeypatch.setattr(postings, "_match", held)
    monkeypatch.setattr(postings, "CHUNK_BOARDS", 1)  # one board a step
    return release, reached


def _stored(fx, jobs: list[str]) -> dict[str, list[tuple[str, int, str]]]:
    """``job -> [(profile, its place among the posting's profiles, updated_at)]`` as the table holds it now, best profile first."""

    store = postings.open_store(fx.home_root, fx.target)
    try:
        found: dict[str, list[tuple[str, int, str]]] = {}
        for row in store.postings(jobs=jobs):
            found.setdefault(row.job, []).append((row.profile_id, row.match_rank, row.updated_at))
        return found
    finally:
        store.close()


def test_the_assessed_set_is_what_the_finished_builds_stored_never_a_row_of_the_build_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fx = _home(tmp_path, monkeypatch)
    untouched, rewritten, added = job_url("pr02", 0), job_url("pr00", 0), job_url("pr00", PER_BOARD + 1)
    with _Served(fx) as served:
        client = served.client
        assert client.get("/api/postings").json()["counts"]["matched"] == BOARDS * PER_BOARD
        before = _stored(fx, [untouched, rewritten])
        best = {job: rows[0][0] for job, rows in before.items()}  # each posting's best profile, as the finished build stored it
        assert len(before[untouched]) == 2 and len(before[rewritten]) == 2  # both profiles match every posting here

        # A build of two boards is started and stops after it wrote the first one (pr00): half applied.
        monkeypatch.setattr(postings, "SMALL_BUILD_BOARDS", 0)
        release, reached = _hold_after_the_first_board(monkeypatch)
        for slug, more in (("pr00", 2), ("pr01", 1)):
            fx.seed(slug, [lever_job(slug, n, title=TITLE_BOTH) for n in range(PER_BOARD + more)], seen_at=days_ago(0.5), watch=False)
        assert client.get("/api/postings").status_code == 200
        assert reached.wait(30) and client.get("/api/postings/status").json()["state"] == "refreshing"
        during = _stored(fx, [untouched, rewritten, added])
        assert during[untouched] == before[untouched]  # the build has not touched pr02
        assert {stamp for _profile, _rank, stamp in during[rewritten]} != {stamp for _profile, _rank, stamp in before[rewritten]}
        assert added in during  # a row of the running build: in the table, not final

        def assessments() -> set[tuple[str, str]]:
            return {(item["job"]["job_identity"], item["resume"]["profile_id"]) for item in client.get("/api/assessments").json()["items"]}

        answers: dict[str, httpx.Response] = {}

        def approve(name: str, jobs: list[str]) -> threading.Thread:
            thread = threading.Thread(target=lambda: answers.__setitem__(name, client.post("/api/postings/assess", json={"jobs": jobs, "approve": True})))
            thread.start()
            return thread

        # The question about a posting of a board the build did not touch: answered at once, from the stored rows.
        asked, seconds = _timed(lambda: client.post("/api/postings/assess", json={"jobs": [untouched]}))
        assert asked.status_code == 200 and seconds < ANSWER_SECONDS and asked.json()["counts"]["to_assess"] == 1
        # Approved: it is assessed while the build is still held, for the profile the stored rows show, and nothing else is.
        # (Its ANSWER reads the results back from the read model, which waits for the build as it always did.)
        first = approve("untouched", [untouched])
        deadline = time.monotonic() + ANSWER_SECONDS
        while not assessments() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert assessments() == {(untouched, best[untouched])}  # exactly the stored row: one posting, its best profile
        assert client.get("/api/postings/status").json()["state"] == "refreshing"

        # A row the running build wrote (an old posting it rewrote, a new one it added): not acted on, the build is waited for.
        second = approve("of the running build", [rewritten, added])
        second.join(1.5)
        assert second.is_alive() and not answers
        assert assessments() == {(untouched, best[untouched])}

        release.set()
        for thread in (first, second):
            thread.join(60)
        assert not first.is_alive() and not second.is_alive()
        final = _stored(fx, [rewritten, added])
        for name in ("untouched", "of the running build"):
            assert answers[name].status_code == 200 and answers[name].json()["status"] == "assessed", answers[name].text[:300]
        # After the build: each for the best profile of the FINISHED build's rows.
        assert assessments() == {(untouched, best[untouched])} | {(job, final[job][0][0]) for job in (rewritten, added)}


def test_with_no_stored_rows_assess_these_waits_for_the_first_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(postings, "SMALL_BUILD_BOARDS", 0)
    release, builds = _hold_builds(monkeypatch)
    with _Served(fx) as served:
        answers: list[httpx.Response] = []
        asking = threading.Thread(target=lambda: answers.append(served.client.post("/api/postings/assess", json={"jobs": [job_url("pr01", 0)]})))
        asking.start()
        asking.join(1.5)
        assert asking.is_alive() and not answers  # no rows to answer from: it waits, it does not answer 202
        assert served.client.get("/api/postings").status_code == 202
        release.set()
        asking.join(60)
        assert not asking.is_alive() and len(builds) == 1
        assert answers[0].status_code == 200 and answers[0].json()["counts"]["selected"] == 1
