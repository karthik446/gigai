"""0.1.11.4 R1: a posting its board no longer lists is found closed when it is about to be acted on. Synthetic only.

The operator's case: a Greenhouse posting whose single-job endpoint answers 404 stayed "Matched" with ``removed_at``
null, and Scout assessed it again from its stored text. A fake transport (``httpx.MockTransport``) stands in for the
public board APIs and counts every request; no request leaves the process. The END outcomes:

- 404 / 410: ``closed``; ``removed_at`` is written to the posting's rows, the default list hides it, the Removed list
  shows it, "assess these" skips it (no model call) and says so;
- 200: ``open``; 500 / 429 / a redirect / a timeout / a body that is no job: ``unknown``, nothing written;
- Lever and Ashby: ``closed`` only on a miss in a list that answered 200;
- one request per posting (and one per Lever/Ashby board), none within the hour, none for a row already removed;
- a posting the board lists again is live again after the next index refresh.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import threading

import httpx
import pytest

from gigai.scout import postings, scout_new
from gigai.scout.posting_search import assess_these, render, search_postings
from gigai.scout.quick_assess import read_quick_assessment

from tests.support.greenhouse_fixtures import gh_detail, gh_job, gh_url, posting_text, seed_greenhouse
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

try:  # the red run (HEAD has no check): the end-outcome tests then run with no fake board and fail on what Scout did
    from gigai.scout.find_jobs import posting_live
except ImportError:  # pragma: no cover - only at a commit before the fix
    posting_live = None  # type: ignore[assignment]

_UPDATED = "2026-10-02T09:00:00Z"
_MASTER = "\n".join([
    "## Summary", "", "Engineer with twelve years building data platforms and inference services.", "",
    "## Experience", "", "### Example Systems", "Staff Engineer | 2022 - 2026", "",
    "- Built the Python inference services on Kubernetes for four regions.", "- Moved the scheduling pipeline to GCP with Terraform.", "",
    "## Education", "", "### Example State University", "BS Computer Science | 2010 - 2014", "", "## Skills", "", "- Python, Kubernetes, Terraform, GCP", "",
])


class _Boards:
    """The public board endpoints, counted. ``greenhouse``: job id -> status (or ``"timeout"``, ``"not_json"``); a job not named answers 200."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.greenhouse: dict[int, object] = {}
        self.lists: dict[str, tuple[int, object]] = {}  # "<host>/<token>" -> (status, body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.url.host}{request.url.path}")
        host, parts = request.url.host, request.url.path.strip("/").split("/")
        if host in ("api.lever.co", "api.ashbyhq.com"):
            status, body = self.lists.get(f"{host}/{parts[-1]}", (200, [] if host == "api.lever.co" else {"jobs": []}))
            return httpx.Response(status, json=body)
        job_id = int(parts[-1])  # /v1/boards/<token>/jobs/<id>
        answer = self.greenhouse.get(job_id, 200)
        if answer == "timeout":
            raise httpx.ReadTimeout("the board did not answer in time", request=request)
        if answer == "not_json":
            return httpx.Response(200, text="<html>Service page</html>")
        if answer in (301, 302):
            return httpx.Response(int(answer), headers={"location": "https://boards.greenhouse.io/acme"})  # type: ignore[call-overload]
        if answer != 200:
            return httpx.Response(int(answer), json={"error": "Job not found"})  # type: ignore[call-overload]
        return httpx.Response(200, json=gh_detail(gh_job("acme", job_id, TITLE_BOTH), posting_text(job_id)))

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "_Boards":
        if posting_live is None:
            return self
        monkeypatch.setenv(posting_live.LIVENESS_ENV, "1")
        monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
        monkeypatch.setattr(posting_live, "liveness_client", lambda: httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=False))
        posting_live.reset_memory()
        return self


def _gh(fx: PostingsFixture, *ids: int) -> None:
    """Greenhouse postings of ``acme`` WITH their stored descriptions (so an assessment needs no request of its own)."""

    seed_greenhouse(
        fx, "acme", [gh_job("acme", n, TITLE_BOTH, updated_at=_UPDATED) for n in ids], seen_at=days_ago(1),
        details={n: (posting_text(n), _UPDATED) for n in ids},
    )
    postings.refresh(fx.home_root, fx.target, now=NOW)


def _rows(fx: PostingsFixture, url: str):
    store = postings.open_store(fx.home_root, fx.target)
    try:
        return store.postings(jobs={url}, live=False)
    finally:
        store.close()


def _listed(fx: PostingsFixture, *, removed: bool = False) -> set[str]:
    found = search_postings(fx.home_root, fx.target, removed=removed, now=NOW)
    return {row["job_identity"] for row in found["postings"]["rows"]}  # type: ignore[index]


# --- the operator's case, on the end outcome ----------------------------------------------------------------


def test_assess_these_skips_a_posting_whose_board_answers_404_makes_no_model_call_for_it_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _gh(fx, 401, 402, 403)
    boards = _Boards().install(monkeypatch)
    boards.greenhouse[402] = 404  # the board no longer lists it: "Job not found"
    dead = gh_url("acme", 402)
    assert dead in _listed(fx) and all(row.removed_at is None for row in _rows(fx, dead))  # listed as live, as the operator saw it

    done = assess_these(fx.home_root, fx.target, jobs=[gh_url("acme", n) for n in (401, 402, 403)], approve=True, now=NOW)

    assessed = done["assessed"]
    assert len(fx.base.model.assess_prompts) == 2, "a model call was made for the closed posting"
    assert not any(posting_text(402) in prompt for prompt in fx.base.model.assess_prompts)
    assert assessed["assessed"] == 2 and assessed["closed_skipped"] == 1  # type: ignore[index]
    assert [(item["job_identity"], item["error_code"]) for item in assessed["failed"]] == [(dead, "posting_closed")]  # type: ignore[index]
    assert "Skipped 1 closed posting: its board no longer lists it." in render(done)
    assert read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, dead) is None
    # removed_at is written for every profile's row: the default list hides it, the Removed list shows it.
    rows = _rows(fx, dead)
    assert rows and all(row.removed_at is not None for row in rows)
    assert dead not in _listed(fx) and dead in _listed(fx, removed=True)
    assert gh_url("acme", 401) in _listed(fx)
    # One request per posting of the batch, and none for the closed one ever again (its row says it).
    assert sorted(boards.requests) == [f"boards-api.greenhouse.io/v1/boards/acme/jobs/{n}" for n in (401, 402, 403)]
    boards.requests.clear()
    again = assess_these(fx.home_root, fx.target, jobs=[dead], approve=True, now=NOW)
    assert again["status"] == "nothing_to_assess" and again["not_found"] == [dead] and boards.requests == []
    assert len(fx.base.model.assess_prompts) == 2


def test_scout_new_yes_skips_a_closed_posting_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _gh(fx, 411, 412)
    boards = _Boards().install(monkeypatch)
    boards.greenhouse[412] = 410

    done = scout_new.scout_new(fx.home_root, fx.target, now=NOW, assess=True)  # type: ignore[arg-type]

    assert done["assessed"]["assessed"] == 1 and done["assessed"]["closed_skipped"] == 1  # type: ignore[index]
    assert len(fx.base.model.assess_prompts) == 1 and posting_text(411) in fx.base.model.assess_prompts[0]
    assert "Skipped 1 closed posting" in scout_new.render(done)
    assert all(row.removed_at is not None for row in _rows(fx, gh_url("acme", 412)))


# --- open | closed | unknown ---------------------------------------------------------------------------------


def test_200_is_open_and_the_answer_is_kept_for_an_hour_in_the_process_and_in_the_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _gh(fx, 421)
    boards = _Boards().install(monkeypatch)
    url = gh_url("acme", 421)

    first = posting_live.job_liveness(fx.home_root, fx.target, url, now=NOW)
    assert (first.state, first.requested, first.closed_at, first.note) == ("open", True, None, None) and first.checked_at is not None
    assert boards.requests == ["boards-api.greenhouse.io/v1/boards/acme/jobs/421"]
    assert all(row.removed_at is None for row in _rows(fx, url)) and url in _listed(fx)

    second = posting_live.job_liveness(fx.home_root, fx.target, url, now=NOW + timedelta(minutes=59))
    assert (second.state, second.requested, second.checked_at) == ("open", False, first.checked_at)
    posting_live.reset_memory()  # a new process: the file under <home>/cache/scout/liveness answers
    third = posting_live.job_liveness(fx.home_root, fx.target, url, now=NOW + timedelta(minutes=59))
    assert (third.state, third.requested) == ("open", False)
    assert len(boards.requests) == 1, "a second request within the hour"
    assert [path.parent.name for path in (fx.home_root / "cache" / "scout" / "liveness").rglob("*.json")] == ["jobs"]

    posting_live.reset_memory()  # past the hour the board is asked again, once
    later = posting_live.job_liveness(fx.home_root, fx.target, url, now=NOW + timedelta(minutes=61))
    assert (later.state, later.requested) == ("open", True) and len(boards.requests) == 2


@pytest.mark.parametrize("answer", [500, 503, 429, 403, 302, "timeout", "not_json"])
def test_anything_but_404_or_410_is_unknown_and_never_closes_a_posting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: object) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _gh(fx, 431)
    boards = _Boards().install(monkeypatch)
    boards.greenhouse[431] = answer
    url = gh_url("acme", 431)

    found = posting_live.job_liveness(fx.home_root, fx.target, url, now=NOW)

    assert (found.state, found.closed_at, found.note) == ("unknown", None, None)
    assert all(row.removed_at is None for row in _rows(fx, url)) and url in _listed(fx), "a board that failed to answer closed a posting"
    assert len(boards.requests) == 1  # one request, never a retry
    assert not list((fx.home_root / "cache" / "scout" / "liveness").rglob("*.json")), "an unknown was written as an answer"
    # It proceeds: the posting is assessed as before.
    done = assess_these(fx.home_root, fx.target, jobs=[url], approve=True, now=NOW)
    assert done["assessed"]["assessed"] == 1 and "closed_skipped" not in done["assessed"]  # type: ignore[index, operator]
    assert len(boards.requests) == 1, "an unknown was asked again within its few minutes"


@pytest.mark.parametrize("status", [404, 410])
def test_404_and_410_are_closed_and_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _gh(fx, 441)
    boards = _Boards().install(monkeypatch)
    boards.greenhouse[441] = status
    url = gh_url("acme", 441)

    found = posting_live.job_liveness(fx.home_root, fx.target, url, now=NOW)

    assert found.state == "closed" and found.closed_at == postings.stamp(NOW) and found.note == "This posting looks closed: check it before you apply"
    assert {row.removed_at for row in _rows(fx, url)} == {postings.stamp(NOW)}
    assert {row.profile_id for row in _rows(fx, url)} == {fx.default_profile_id, fx.second_profile_id}  # every profile's row


def test_the_check_is_off_by_the_environment_and_under_the_fixture_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _gh(fx, 451)
    boards = _Boards().install(monkeypatch)
    boards.greenhouse[451] = 404
    url = gh_url("acme", 451)

    monkeypatch.setenv(posting_live.LIVENESS_ENV, "0")
    assert posting_live.job_liveness(fx.home_root, fx.target, url, now=NOW).state == "unknown"
    monkeypatch.setenv(posting_live.LIVENESS_ENV, "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")  # the fixture transport answers 404 to what it does not know
    assert posting_live.job_liveness(fx.home_root, fx.target, url, now=NOW).state == "unknown"
    assert boards.requests == [] and all(row.removed_at is None for row in _rows(fx, url))


# --- Lever and Ashby: the board list -------------------------------------------------------------------------


def test_a_lever_posting_is_closed_only_when_a_list_that_answered_200_misses_it_one_request_a_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("acme", [lever_job("acme", n) for n in (1, 2, 3)], seen_at=days_ago(1))
    postings.refresh(fx.home_root, fx.target, now=NOW)
    boards = _Boards().install(monkeypatch)

    # The list fails: nothing is concluded.
    boards.lists["api.lever.co/acme"] = (500, {"error": "unavailable"})
    assert posting_live.job_liveness(fx.home_root, fx.target, job_url("acme", 2), now=NOW).state == "unknown"
    assert all(row.removed_at is None for row in _rows(fx, job_url("acme", 2)))

    # The list answers 200 without posting 2: closed. Postings 1 and 3 are open from the SAME list (no second request).
    posting_live.reset_memory()
    boards.requests.clear()
    boards.lists["api.lever.co/acme"] = (200, [lever_job("acme", 1), lever_job("acme", 3)])
    found = posting_live.jobs_liveness(fx.home_root, fx.target, [job_url("acme", n) for n in (1, 2, 3)], now=NOW)
    assert {job.rsplit("-", 1)[-1]: answer.state for job, answer in found.items()} == {"00001": "open", "00002": "closed", "00003": "open"}
    assert boards.requests == ["api.lever.co/v0/postings/acme"], "more than one request for one board"
    assert all(row.removed_at is not None for row in _rows(fx, job_url("acme", 2)))
    assert all(row.removed_at is None for row in _rows(fx, job_url("acme", 1)))
    # The liveness list is kept apart from the sources update's own response cache.
    assert not list((fx.home_root / "cache" / "scout" / "ats-boards").rglob("*.tmp*"))
    assert (fx.home_root / "cache" / "scout" / "liveness" / "boards" / "lever").is_dir()


def test_an_ashby_posting_by_its_link_is_reported_closed_on_a_list_miss_and_nothing_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)  # the job is no row of the read model: assessed by its link
    boards = _Boards().install(monkeypatch)
    here, gone = "https://jobs.ashbyhq.com/nimbus/0d6e1c1e-aaaa-4bbb-8ccc-000000000001", "https://jobs.ashbyhq.com/nimbus/0d6e1c1e-aaaa-4bbb-8ccc-000000000002"
    boards.lists["api.ashbyhq.com/nimbus"] = (200, {"jobs": [{"id": "0d6e1c1e-aaaa-4bbb-8ccc-000000000001", "jobUrl": here, "title": TITLE_BOTH}]})

    found = posting_live.jobs_liveness(fx.home_root, fx.target, [here, gone, here + "/application"], now=NOW)

    assert [found[job].state for job in (here, gone, here + "/application")] == ["open", "closed", "open"]  # the id in the link is the posting's
    assert found[gone].closed_at is not None and boards.requests == ["api.ashbyhq.com/posting-api/job-board/nimbus"]
    # A list that is not a list of jobs concludes nothing; neither does a link on no known board (no request for it).
    posting_live.reset_memory()
    boards.lists["api.ashbyhq.com/nimbus"] = (200, {"message": "maintenance"})
    other = "https://jobs.ashbyhq.com/nimbus/0d6e1c1e-aaaa-4bbb-8ccc-000000000003"
    assert posting_live.job_liveness(fx.home_root, fx.target, other, now=NOW).state == "unknown"
    boards.requests.clear()
    assert posting_live.job_liveness(fx.home_root, fx.target, "https://careers.example.test/jobs/9", now=NOW).state == "unknown"
    assert posting_live.job_liveness(fx.home_root, fx.target, "text:sha256:" + "a" * 64, now=NOW).state == "unknown"
    assert boards.requests == []


# --- the next sources update --------------------------------------------------------------------------------


def test_a_posting_the_board_lists_again_is_live_again_and_one_it_dropped_stays_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _gh(fx, 461, 462)
    boards = _Boards().install(monkeypatch)
    boards.greenhouse[461] = boards.greenhouse[462] = 404
    back, gone = gh_url("acme", 461), gh_url("acme", 462)
    posting_live.jobs_liveness(fx.home_root, fx.target, [back, gone], now=NOW)
    assert all(row.removed_at is not None for row in (*_rows(fx, back), *_rows(fx, gone)))

    # A read with nothing new from the board keeps both removed (the read model is not rebuilt from the index).
    postings.refresh(fx.home_root, fx.target, now=NOW)
    assert all(row.removed_at is not None for row in (*_rows(fx, back), *_rows(fx, gone)))

    # The next sources update: the board lists 461 again (and a new posting), and no longer lists 462.
    later = NOW + timedelta(hours=3)
    seed_greenhouse(
        fx, "acme", [gh_job("acme", n, TITLE_BOTH, updated_at=_UPDATED) for n in (461, 463)], seen_at=later,
        details={n: (posting_text(n), _UPDATED) for n in (461, 463)},
    )
    postings.refresh(fx.home_root, fx.target, now=later)

    assert {row.removed_at for row in _rows(fx, back)} == {None}, "a posting the board lists again stayed removed"
    assert all(row.removed_at is not None for row in _rows(fx, gone))
    assert back in _listed(fx) and gone not in _listed(fx) and gone in _listed(fx, removed=True)


# --- the routes: the job read, Assess, Mark applied ----------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _gh(fx, 471, 472)
    boards = _Boards().install(monkeypatch)
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=120.0)
    try:
        yield fx, boards, client
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(10)


def test_the_job_read_says_open_then_closed_and_never_asks_about_a_row_already_removed(served) -> None:
    fx, boards, client = served
    url = gh_url("acme", 471)

    opened = client.get("/api/jobs", params={"url": url})
    assert opened.status_code == 200, opened.text
    assert opened.json()["liveness"] == {"state": "open", "checked_at": opened.json()["liveness"]["checked_at"], "closed_at": None, "note": None}
    assert opened.json()["posting"]["removed_at"] is None
    assert client.get("/api/jobs", params={"url": url}).json()["liveness"]["state"] == "open"
    assert boards.requests == ["boards-api.greenhouse.io/v1/boards/acme/jobs/471"], "the page asked the board twice within the hour"

    # The board drops the other posting: its page says closed, with the time, and the row is removed in the same read.
    boards.requests.clear()
    boards.greenhouse[472] = 404
    dead = gh_url("acme", 472)
    closed = client.get("/api/jobs", params={"url": dead}).json()
    assert closed["liveness"]["state"] == "closed" and closed["liveness"]["note"] == "This posting looks closed: check it before you apply"
    assert closed["liveness"]["closed_at"] == closed["posting"]["removed_at"] is not None
    assert boards.requests == ["boards-api.greenhouse.io/v1/boards/acme/jobs/472"]
    # An already-removed row: no request, in this process or a new one (nothing kept in memory, the cache file gone).
    posting_live.reset_memory()
    for path in (fx.home_root / "cache" / "scout" / "liveness").rglob("*.json"):
        path.unlink()
    boards.requests.clear()
    again = client.get("/api/jobs", params={"url": dead}).json()
    assert again["liveness"]["state"] == "closed" and again["liveness"]["closed_at"] == closed["liveness"]["closed_at"]
    assert boards.requests == [], "the job read asked the board about a row that is already removed"
    # The list: gone by default, under Removed with its time.
    live = client.get("/api/postings").json()["postings"]["rows"]
    removed = client.get("/api/postings", params={"removed": "1"}).json()["postings"]["rows"]
    assert dead not in {row["job_identity"] for row in live} and {row["job_identity"]: bool(row["removed_at"]) for row in removed} == {dead: True}


def test_assess_of_one_closed_posting_is_refused_with_no_model_call_and_mark_applied_is_never_blocked(served) -> None:
    fx, boards, client = served
    boards.greenhouse[472] = 404
    dead, alive = gh_url("acme", 472), gh_url("acme", 471)

    refused = client.post("/api/assess", json={"job": {"job_url": dead}, "resume": {"profile_id": fx.default_profile_id}})
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "posting_closed" and error["liveness"]["state"] == "closed"
    assert error["message"] == "This posting is closed: its board no longer lists it, so it was not assessed."
    assert fx.base.model.assess_prompts == [] and all(row.removed_at is not None for row in _rows(fx, dead))

    done = client.post("/api/assess", json={"job": {"job_url": alive}, "resume": {"profile_id": fx.default_profile_id}})
    assert done.status_code == 200, done.text
    assert len(fx.base.model.assess_prompts) == 1

    # Mark applied: recorded either way; the closed one carries the plain note, the open one does not.
    applied = client.post("/api/applications", json={"normalized_url": dead, "event_kind": "applied"})
    assert applied.status_code == 201, applied.text
    assert applied.json()["posting_note"] == "This posting looks closed: check it before you apply" and applied.json()["event"]["event_kind"] == "applied"
    fine = client.post("/api/applications", json={"normalized_url": alive, "event_kind": "applied"})
    assert fine.status_code == 201 and "posting_note" not in fine.json()
    # One request per posting across the read, the Assess and Mark applied.
    assert sorted(boards.requests) == [f"boards-api.greenhouse.io/v1/boards/acme/jobs/{n}" for n in (471, 472)]


def test_generate_pdf_of_a_closed_posting_still_makes_the_pdf_and_carries_the_note(served, tmp_path: Path) -> None:
    from click.testing import CliRunner

    from gigai.scout.master_store import import_master
    from gigai.scout.scout_cli import scout_group

    fx, boards, client = served
    url = gh_url("acme", 471)
    # A job resume stored for the posting (an agent's, from the master), as Generate PDF needs one.
    source = tmp_path / "master.md"
    source.write_text(_MASTER, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, gig_id=fx.base.gig.resolved.gig_id).status == "created"
    stored = CliRunner().invoke(scout_group, ["resume", "store", "--in", str(source), "--job-url", url, "--as", "agent", "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert stored.exit_code == 0, stored.output
    key = {"profile_id": fx.default_profile_id, "job_identity": url}

    before = client.post("/api/tailored-resumes/pdf", json=key)
    assert before.status_code == 200 and before.content.startswith(b"%PDF") and "x-gigai-posting-note" not in before.headers

    # The board drops the posting; past the hour the PDF's own check finds it. The PDF is still made.
    boards.greenhouse[471] = 404
    posting_live.reset_memory()
    for path in (fx.home_root / "cache" / "scout" / "liveness").rglob("*.json"):
        path.unlink()
    after = client.post("/api/tailored-resumes/pdf", json=key)
    assert after.status_code == 200 and after.content.startswith(b"%PDF"), "a closed posting's PDF was refused"
    assert after.headers["x-gigai-posting-note"] == "This posting looks closed: check it before you apply"
    assert all(row.removed_at is not None for row in _rows(fx, url))
