"""0.1.10.11 S4: which requests run inside the read scope, and that none of them writes a journal.

``workpad.committed_read_cache()`` lets a request reuse a workpad check that
already passed and a committed read already made. It is for requests that
only READ the journal: a journal writer has to make every check itself.
``api/server.py`` decides which requests are inside it (``read_scope_covers``):
every ``GET /api/...`` but the ones it names, and four ``POST`` routes that
write files in the home and no journal transition.

The rule, held here on a real in-process server:

* the decision is made for every route of the API, and nothing but a ``GET``
  or one of those four ``POST`` routes is ever inside the scope;
* a request inside the scope takes the journal's writer lock NEVER and leaves
  the journal head where it was (every such route is requested here; a route
  added to the API without a request in this file fails);
* the same on a home whose default profile was never migrated, where the
  first profile-aware read writes it: no route this packet put inside the
  scope is such a read, and the ``GET`` left outside the scope is outside for
  that reason (it takes the writer lock there);
* a real journal write (an answer) is made outside the scope, and the next
  read inside it sees what was written.

Not new here, and so not asked of this packet's scope: six ``GET`` routes
(``FIRST_READ_MIGRATES``) have had a read scope of their own around that
first-read migration since 0110-033 and 0110-9-01, and ``POST
/api/applications`` runs its publication inside one since 0110-036.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

import httpx
import pytest

import gigai.journal as journal
from gigai.scout import postings
from gigai.scout.find_jobs.api import openapi, server as server_module
from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve
from gigai.workpad import committed_read_cache_active, workpad_head_without_git

from tests.support.pipeline_fixtures import JOB, QUESTION_ID, assess_base
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

BOARD = "rs00"
LISTED = job_url(BOARD, 0)
PIPELINED = job_url(BOARD, 1)


def _q(value: str) -> str:
    return quote(value, safe="")


#: One request for every ``GET`` route of the API (``openapi.ROUTES``), by its route template.
GET_REQUESTS: dict[str, str] = {
    "/api": "/api",
    "/api/openapi.json": "/api/openapi.json",
    "/llms.txt": "/llms.txt",
    "/api/jobs": f"/api/jobs?url={_q(JOB)}",
    "/api/jobs/suggestions": f"/api/jobs/suggestions?url={_q(JOB)}",
    "/api/jobs/brief": f"/api/jobs/brief?url={_q(JOB)}",
    "/api/health": "/api/health",
    "/api/config": "/api/config",
    "/api/setup": "/api/setup",
    "/api/secrets/status": "/api/secrets/status",
    "/api/runs": "/api/runs",
    "/api/runs/{run_id}": "/api/runs/run_00000000-0000-4000-8000-000000000001",
    "/api/runs/{run_id}/progress": "/api/runs/run_00000000-0000-4000-8000-000000000001/progress",
    "/api/runs/{run_id}/results": "/api/runs/run_00000000-0000-4000-8000-000000000001/results",
    "/api/runs/{run_id}/posting": f"/api/runs/run_00000000-0000-4000-8000-000000000001/posting?url={_q(JOB)}",
    "/api/discover/latest": "/api/discover/latest",
    "/api/profiles": "/api/profiles",
    "/api/assessments": "/api/assessments",
    "/api/answers": "/api/answers",
    "/api/answers/match": f"/api/answers/match?question_id={_q(QUESTION_ID)}&question={_q('Have you run workloads on GCP?')}",
    "/api/answers/{question_id}": f"/api/answers/{_q(QUESTION_ID)}",
    "/api/stories": "/api/stories",
    "/api/stories/prep": "/api/stories/prep",
    "/api/stories/{story_id}": "/api/stories/story_00000000-0000-4000-8000-000000000001",
    "/api/applications": "/api/applications",
    "/api/tailored-resumes": "/api/tailored-resumes",
    "/api/master": "/api/master",
    "/api/master/history": "/api/master/history",
    "/api/master/migration": "/api/master/migration",
    "/api/master/selection": "/api/master/selection",
    "/api/resumes-folder": "/api/resumes-folder",
    "/api/resume-display": "/api/resume-display",
    "/api/privacy/cleanup": "/api/privacy/cleanup",
    "/api/watchlist": "/api/watchlist",
    "/api/sources/update": "/api/sources/update",
    "/api/new": "/api/new?peek=1",
    "/api/new/yours": "/api/new/yours",
    "/api/pipeline": "/api/pipeline",
    "/api/pipeline/approvals": "/api/pipeline/approvals",
    "/api/pipeline/job": f"/api/pipeline/job?job_identity={_q(JOB)}&profile_id=PROFILE",
    "/api/postings": "/api/postings?limit=10",
    "/api/postings/status": "/api/postings/status",
    "/api/metrics": "/api/metrics",
    "/api/settings/background": "/api/settings/background",
}


#: The ``GET`` routes that migrate the default profile on a home's first profile-aware read, inside a read scope of
#: their OWN that predates this packet (``@reads_committed``, ``postings.refresh``, ``scout_new``).
FIRST_READ_MIGRATES = frozenset({"/api/profiles", "/api/config", "/api/setup", "/api/postings", "/api/new", "/api/new/yours"})


class _Spy:
    """Every journal writer-lock acquisition (was the read scope active on that thread?) and every entry of the scope a request made."""

    def __init__(self) -> None:
        self.locks: list[bool] = []
        self.scoped: list[tuple[str, str]] = []

    def clear(self) -> None:
        self.locks.clear()
        self.scoped.clear()

    @property
    def inside(self) -> int:
        return sum(1 for active in self.locks if active)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    found = _Spy()
    real_lock = journal._writer_lock
    real_covers = server_module.read_scope_covers

    @contextmanager
    def spying(path: Path, timeout_seconds: float) -> Iterator[None]:
        found.locks.append(committed_read_cache_active())
        with real_lock(path, timeout_seconds):
            yield

    def covers(method: str, path: str) -> bool:
        covered = real_covers(method, path)
        if covered:
            found.scoped.append((method, path))
        return covered

    monkeypatch.setattr(journal, "_writer_lock", spying)
    monkeypatch.setattr(server_module, "read_scope_covers", covers)
    return found


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """A gig with a resume, two profiles, an answer, a story, one assessed job and one watched board of four postings."""

    fx: PostingsFixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    assess_base(fx.base)
    fx.seed(BOARD, [lever_job(BOARD, n, title=TITLE_BOTH) for n in range(4)], seen_at=days_ago(1))
    postings.refresh(fx.home_root, fx.target, now=NOW)
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    client = httpx.Client(base_url=f"http://{host}:{port}", timeout=120.0)
    try:
        yield SimpleNamespace(fx=fx, client=client, workpad=Path(fx.base.gig.resolved.path))
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(10)


def _head(served: SimpleNamespace) -> str:
    head = workpad_head_without_git(served.workpad)
    assert head is not None
    return head


def _get_path(served: SimpleNamespace, template: str) -> str:
    return GET_REQUESTS[template].replace("PROFILE", served.fx.default_profile_id)


def test_the_scope_is_decided_for_every_route_and_only_reads_and_the_four_posts_are_inside() -> None:
    routes = {(route.method, route.path) for route in openapi.ROUTES}
    assert {path for method, path in routes if method == "GET"} == set(GET_REQUESTS), "a GET route has no request in this file"
    inside = {(method, path) for method, path in routes if server_module.read_scope_covers(method, path)}
    gets = {("GET", path) for method, path in routes if method == "GET" and path.startswith("/api/")}
    outside_gets = {("GET", path) for path in server_module.GET_ROUTES_OUTSIDE_READ_SCOPE}
    posts = {("POST", path) for path in server_module.READ_SCOPE_POST_ROUTES}
    assert outside_gets <= gets and posts <= routes
    assert inside == (gets - outside_gets) | posts
    assert posts == {("POST", "/api/assess"), ("POST", "/api/tailored-resumes"), ("POST", "/api/pipeline/process"), ("POST", "/api/postings/assess")}
    # Never a PUT or a DELETE, and never a POST that is not one of the four.
    assert not {method for method, _path in inside} - {"GET", "POST"}
    for method in ("PUT", "DELETE", "PATCH"):
        assert not server_module.read_scope_covers(method, "/api/assess")
    assert not server_module.read_scope_covers("POST", "/api/answers") and not server_module.read_scope_covers("GET", "/index.html")


def test_no_get_inside_the_read_scope_takes_the_writer_lock_or_moves_the_journal(served: SimpleNamespace, spy: _Spy) -> None:
    client = served.client
    for round_ in ("first", "again"):  # the first read of each route in this server, then a read with everything kept
        for template in GET_REQUESTS:
            path = _get_path(served, template)
            covered = server_module.read_scope_covers("GET", urlsplit(path).path)
            before = _head(served)
            spy.clear()
            response = client.get(path)
            assert response.status_code < 500, (round_, path, response.status_code, response.text[:300])
            assert bool(spy.scoped) == covered, (round_, path, spy.scoped)
            if covered:
                assert spy.locks == [], f"{round_}: GET {path} is inside the read scope and took the journal writer lock"
                assert _head(served) == before, f"{round_}: GET {path} is inside the read scope and moved the journal head"
            else:
                assert not spy.inside, (round_, path)


def test_on_a_home_never_migrated_no_get_this_put_inside_the_scope_writes_and_the_one_left_outside_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spy: _Spy,
) -> None:
    """The first profile-aware read of a home migrates its default profile: one journal write.

    Every ``GET`` this packet put inside the scope is requested on such a home
    before anything migrated it, and none takes the writer lock (so the home
    stays as it was for the next one). The route left outside the scope is
    requested last: it is the read that migrates, which is why it is outside.
    """

    from .test_read_routes_lock_free import _fixture

    home, target, workpad = _fixture(tmp_path)
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")
    server = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    outside = sorted(server_module.GET_ROUTES_OUTSIDE_READ_SCOPE)
    assert outside == ["/api/master/migration"]
    try:
        with httpx.Client(base_url=f"http://{server.server_address[0]}:{server.server_address[1]}", timeout=120.0) as client:
            for template in sorted(set(GET_REQUESTS) - FIRST_READ_MIGRATES - set(outside)):
                path = GET_REQUESTS[template].replace("PROFILE", "profile_00000000-0000-4000-8000-000000000001")
                before = workpad_head_without_git(Path(workpad))
                spy.clear()
                response = client.get(path)
                assert response.status_code < 500, (path, response.status_code, response.text[:300])
                assert spy.locks == [], f"GET {path}, the first read of a home never migrated, took the journal writer lock"
                assert workpad_head_without_git(Path(workpad)) == before, path
            # Nothing above migrated the home, so this is still its first profile-aware read: the one that writes.
            for path in outside:
                spy.clear()
                response = client.get(path)
                assert response.status_code == 200, (path, response.text[:300])
                assert spy.scoped == [] and spy.locks == [False], (path, spy.scoped, spy.locks)  # a writer, in no scope
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


@pytest.mark.skip(reason="needs the pipeline on: re-enable in 0.1.11.1 (pipeline off by default in 0.1.11)")
def test_on_a_home_never_migrated_a_scoped_post_migrates_before_it_enters_the_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spy: _Spy,
) -> None:
    """ "Process now" as the very first request of a home: the default profile is migrated OUTSIDE the scope, once."""

    from .test_read_routes_lock_free import _fixture

    home, target, workpad = _fixture(tmp_path)
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")
    monkeypatch.setattr(server_module, "_settled_first_read", set())
    server = serve(backend=ScoutFindJobsBackend(home_root=home, target=target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://{server.server_address[0]}:{server.server_address[1]}", timeout=120.0) as client:
            before = workpad_head_without_git(Path(workpad))
            spy.clear()  # the fixture's own writes
            # A request the guards refuse (another site's page) migrates nothing: it is refused before anything is done.
            refused = client.post("/api/pipeline/process", json={"job_identity": JOB}, headers={"Origin": "http://evil.example"})
            assert refused.status_code == 403 and spy.locks == [] and workpad_head_without_git(Path(workpad)) == before
            spy.clear()
            first = client.post("/api/pipeline/process", json={"job_identity": JOB})
            assert first.status_code == 404 and first.json()["error"]["code"] == "assessment_missing", first.text[:300]
            assert spy.scoped == [("POST", "/api/pipeline/process")]
            assert spy.locks == [False], spy.locks  # the migration: one journal write, made outside the scope
            migrated = workpad_head_without_git(Path(workpad))
            assert migrated != before
            assert len(client.get("/api/profiles").json()["profiles"]) == 1
            for path in sorted(server_module.READ_SCOPE_POST_ROUTES):  # settled: no later request writes
                spy.clear()
                assert client.post(path, json={"job_identity": JOB}).status_code < 500, path
                assert spy.scoped == [("POST", path)] and spy.locks == [], (path, spy.locks)
            assert workpad_head_without_git(Path(workpad)) == migrated
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def test_the_four_posts_inside_the_scope_write_no_journal(served: SimpleNamespace, spy: _Spy) -> None:
    client, fx = served.client, served.fx
    profile_id = fx.default_profile_id
    requests = [
        # "Assess these": the question, then the batch.
        ("/api/postings/assess", {"jobs": [LISTED]}, 200),
        ("/api/postings/assess", {"jobs": [LISTED, PIPELINED], "approve": True}, 200),
        # The job page's Assess, of a job assessed just now and of one never assessed.
        ("/api/assess", {"job": {"job_url": LISTED}, "resume": {"profile_id": profile_id}, "origin": "job_page"}, 200),
        ("/api/assess", {"job": {"job_url": job_url(BOARD, 2)}, "resume": {"profile_id": profile_id}, "origin": "job_page"}, 200),
        ("/api/tailored-resumes", {"job": {"job_url": LISTED}, "resume": {"profile_id": profile_id}}, 200),
        ("/api/pipeline/process", {"job_identity": PIPELINED, "profile_id": profile_id}, 202),
    ]
    seen: set[str] = set()
    for path, body, expected in requests:
        before = _head(served)
        spy.clear()
        response = client.post(path, json=body)
        assert response.status_code == expected, (path, response.status_code, response.text[:400])
        assert spy.scoped == [("POST", path)], (path, spy.scoped)
        assert spy.locks == [], f"POST {path} is inside the read scope and took the journal writer lock"
        assert _head(served) == before, f"POST {path} is inside the read scope and moved the journal head"
        seen.add(path)
    assert seen == set(server_module.READ_SCOPE_POST_ROUTES)
    # What they wrote is in the home, and the next reads show it.
    assessed = {item["job"]["job_identity"] for item in client.get(f"/api/assessments?profile_id={profile_id}").json()["items"]}
    assert {LISTED, PIPELINED, job_url(BOARD, 2)} <= assessed
    page = client.get(f"/api/jobs?url={_q(LISTED)}").json()
    assert [item["profile_id"] for item in page["assessments"]] == [profile_id] and len(page["tailored_resumes"]) == 1


def test_a_journal_write_is_made_outside_the_scope_and_the_next_scoped_read_sees_it(served: SimpleNamespace, spy: _Spy) -> None:
    client = served.client
    for path in ("/api/applications", "/api/answers", f"/api/jobs?url={_q(LISTED)}"):  # every read below is kept once
        assert client.get(path).status_code == 200, path
    for path, body, key, own_scope in (
        ("/api/answers", {"question_id": "years:python", "question": "Years of Python?", "answer": "Six."}, "answers", False),
        # 0110-036: this route's publication has run inside a scope of its own since then; the server adds none.
        ("/api/applications", {"normalized_url": LISTED, "event_kind": "applied"}, "applications", True),
    ):
        before = _head(served)
        count = len(client.get(path).json()[key])
        spy.clear()
        written = client.post(path, json=body)
        assert written.status_code == 201, (path, written.text[:400])
        assert spy.scoped == [] and spy.locks, (path, spy.locks)  # a writer: the server put it in no scope
        assert bool(spy.inside) == own_scope, (path, spy.locks)
        assert _head(served) != before
        spy.clear()
        assert len(client.get(path).json()[key]) == count + 1  # the read after the write, inside the scope
        assert spy.scoped == [("GET", path)] and spy.locks == []
    events = client.get(f"/api/jobs?url={_q(LISTED)}").json()["application_events"]
    assert [event["event_kind"] for event in events] == ["applied"]


def test_the_spy_sees_a_writer_inside_a_scope(served: SimpleNamespace, spy: _Spy) -> None:
    """The check above can fail: a journal write made inside the scope is counted as one."""

    from gigai.scout import story_bank
    from gigai.workpad import committed_read_cache

    fx = served.fx
    with committed_read_cache():
        story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id="years:go", answer="Two.", question="Years of Go?")
    assert spy.inside >= 1
