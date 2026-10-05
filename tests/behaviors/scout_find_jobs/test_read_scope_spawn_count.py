"""0.1.10.11 S4 (causes C2, C7): the reads and the journal-free writes of a page, pinned by git process count.

On the operator-sized home the job page spawned 52 git processes a call
(0.42 to 0.82 s), ``GET /api/assessments`` 50, ``GET /api/pipeline`` 215
(1.3 to 2.3 s), ``POST /api/assess`` 135 (1.3 s) and ``POST
/api/pipeline/process`` 99 to 209 (1.0 to 1.2 s). The time was git processes:
each of these routes resolved the workpad 7 to 12 times a request and read the
journal again, because only seven GET handlers ran inside the read scope
(``workpad.committed_read_cache``).

Pinned with counts, not wall time, on a small synthetic home (the same routes
on the operator-sized home are in the worker report):

* a read of an unchanged workpad spawns (almost) nothing, through every route
  here, and so do the POST routes that write no journal (an assess, "Assess
  these" and "Process now" are counted here);
* an approved batch ("Assess these", ``scout new``) assesses each posting
  inside a scope of its own: the batch runs on its own threads, and the scope
  is per thread;
* the job page opens ITS job's stored assessment and tailored resume (one
  file per resume identity), not every stored file.

Before the fix the same calls spawned, on this fixture: 10 to 37 for a read
(15 and 16 the job page, 37 the answers, 23 the pipeline's job, 12 the
assessments), 99 for an assess and 99 for "Process now"; now 0 to 2, 4 and 4.
A batch of two postings spawned 174 (87 a posting); now 8.
The job page read every stored assessment (5 here, 299 on the operator-sized
home); now the one file of its job in each resume identity's folder.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from gigai.scout import quick_assess, tailored_resume

from tests.support.pipeline_fixtures import JOB, QUESTION_ID

from .test_read_scope_routes import BOARD, LISTED, served  # noqa: F401 - the in-process server fixture
from tests.support.posting_fixtures import job_url


def _q(value: str) -> str:
    return quote(value, safe="")


#: What one call may spawn on an unchanged workpad, after one call of the same route. Measured: 0, and 2 for the answers.
READ_SPAWNS_MAX = 3
READS = (
    f"/api/jobs?url={_q(JOB)}",
    f"/api/jobs?url={_q(LISTED)}",
    "/api/assessments",
    "/api/answers",
    f"/api/answers/match?question_id={_q(QUESTION_ID)}&question={_q('Have you run workloads on GCP?')}",
    f"/api/answers/{_q(QUESTION_ID)}",
    "/api/master/selection",
    "/api/pipeline",
    f"/api/pipeline/job?job_identity={_q(JOB)}&profile_id=PROFILE",
    "/api/tailored-resumes",
    "/api/watchlist",
    "/api/stories",
    "/api/settings/background",
    "/api/sources/update",
)
#: The POST routes that write no journal: what the second call of each may spawn. Measured: 4, 0 and 4 (99, 0 and 99
#: before). The fourth, ``POST /api/tailored-resumes``, is requested by ``test_read_scope_routes.py``; its count on the
#: operator-sized home is in the worker report (it reads the stored master, which this fixture does not have).
WRITE_SPAWNS_MAX = {"/api/assess": 8, "/api/postings/assess": 3, "/api/pipeline/process": 8}
#: An approved "Assess these" of two postings, after one such batch. Measured: 8 (174 before: 87 a posting).
BATCH_SPAWNS_MAX = 16


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every subprocess any thread of this process starts (the server's request threads included)."""

    started: list[str] = []
    real = subprocess.run

    def counting(args, *rest, **kwargs):
        started.append(" ".join(str(word) for word in args))
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting)
    return started


def test_a_read_of_an_unchanged_workpad_spawns_almost_nothing(served: SimpleNamespace, spawns: list[str]) -> None:  # noqa: F811
    client = served.client
    paths = [path.replace("PROFILE", served.fx.default_profile_id) for path in READS]
    for path in paths:  # warm: every check passed once, every read made once
        assert client.get(path).status_code == 200, path
    counts: dict[str, int] = {}
    for path in paths:
        spawns.clear()
        assert client.get(path).status_code == 200, path
        counts[path] = len(spawns)
    assert all(count <= READ_SPAWNS_MAX for count in counts.values()), "git processes per read: " + "; ".join(
        f"{path.split('?')[0]} {count}" for path, count in counts.items()
    )


def test_the_journal_free_posts_spawn_almost_nothing(served: SimpleNamespace, spawns: list[str]) -> None:  # noqa: F811
    client, profile_id = served.client, served.fx.default_profile_id
    calls = {
        "/api/assess": [{"job": {"job_url": job_url(BOARD, n)}, "resume": {"profile_id": profile_id}, "origin": "job_page"} for n in (0, 1)],
        "/api/postings/assess": [{"jobs": [job_url(BOARD, 2)]}, {"jobs": [job_url(BOARD, 3)]}],
        "/api/pipeline/process": [{"job_identity": job_url(BOARD, n), "profile_id": profile_id} for n in (0, 1)],
    }
    counts: dict[str, int] = {}
    for path, (first, second) in calls.items():
        assert client.post(path, json=first).status_code in (200, 202), path
        spawns.clear()
        response = client.post(path, json=second)
        assert response.status_code in (200, 202), (path, response.text[:300])
        counts[path] = len(spawns)
    assert all(counts[path] <= WRITE_SPAWNS_MAX[path] for path in calls), counts


def test_an_approved_batch_assesses_each_posting_inside_a_read_scope(served: SimpleNamespace, spawns: list[str]) -> None:  # noqa: F811
    """The batch runs on its own threads and the scope is per thread: each posting's assessment is one, as the job page's Assess."""

    client = served.client
    warm = client.post("/api/postings/assess", json={"jobs": [job_url(BOARD, 0)], "approve": True})
    assert warm.status_code == 200 and warm.json()["status"] == "assessed", warm.text[:300]
    spawns.clear()
    done = client.post("/api/postings/assess", json={"jobs": [job_url(BOARD, 1), job_url(BOARD, 2)], "approve": True})
    assert done.status_code == 200 and done.json()["status"] == "assessed" and done.json()["assessed"]["assessed"] == 2, done.text[:300]
    assert len(spawns) <= BATCH_SPAWNS_MAX, f"{len(spawns)} git processes for a batch of two postings"


def test_the_job_page_reads_its_own_job_not_every_stored_file(served: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    client, fx = served.client, served.fx
    for n in range(4):  # four more assessed jobs (five stored), and a tailored resume for two of them
        body = {"job": {"job_url": job_url(BOARD, n)}, "resume": {"profile_id": fx.default_profile_id}}
        assert client.post("/api/assess", json={**body, "origin": "job_page"}).status_code == 200
        if n < 2:
            tailored = client.post("/api/tailored-resumes", json=body)
            assert tailored.status_code == 200, tailored.text[:300]
    assert len(client.get("/api/assessments").json()["items"]) == 5

    opened: dict[str, list[str]] = {"assessments": [], "resumes": []}
    real_assessment, real_resume = quick_assess._read_stored, tailored_resume._read_stored

    def reading_assessment(path):
        opened["assessments"].append(path.name)
        return real_assessment(path)

    def reading_resume(path):
        opened["resumes"].append(path.name)
        return real_resume(path)

    monkeypatch.setattr(quick_assess, "_read_stored", reading_assessment)
    monkeypatch.setattr(tailored_resume, "_read_stored", reading_resume)
    wanted = job_url(BOARD, 1)
    page = client.get(f"/api/jobs?url={_q(wanted)}")
    assert page.status_code == 200, page.text[:300]
    body = page.json()
    # The page is what it was: this job's assessment and its tailored resume, for the profile that has them.
    assert [item["profile_id"] for item in body["assessments"]] == [fx.default_profile_id]
    assert [item["job_identity"] for item in body["tailored_resumes"]] == [wanted]
    assert body["job_state"]["state"] is not None
    # One file name (the job's key) in each store, however many jobs are stored.
    assert len(set(opened["assessments"])) == 1 and len(set(opened["resumes"])) == 1, opened
    assert len(opened["assessments"]) <= 4 and len(opened["resumes"]) <= 4, opened  # per resume identity (2 profiles), read at most twice
