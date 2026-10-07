"""0.1.11.5 ASSESS-01 parts 1 and 2: an approved "Assess these" batch runs on, says how far it is, and can be cancelled. The REAL Scout server.

Each model call of the batch is a real child process the test holds open (``tests/support/slow_model_child.py``: its
own session, registered like a `claude -p` call), then the fixture model. Six synthetic postings, two calls at a time.

Pinned:
- ``POST /api/postings/assess`` with ``approve`` and ``background`` answers 202 at once (``status: started``) while the
  batch runs; ``GET /api/postings/assess/status`` says total, assessed, the calls in flight and the postings pending.
- ``POST /api/postings/assess/cancel`` starts no further call. THE CALLS IN FLIGHT FINISH (they are not killed) and
  their assessments are stored; the batch ends ``cancelled`` with the count done, the four never started are not
  failures, no model process is left, the marker is gone, and the next approval takes the rest.
- The status read reads no posting row and writes nothing to the pipeline file; both routes refuse a query / a body key,
  and the cancel refuses another Origin.
- The same call without ``background`` still answers when the batch is done (the CLI's and an agent's contract).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from tests.support.posting_fixtures import build_postings_fixture, job_url, lever_job
from tests.support.slow_model_child import alive, gate_model_calls

NUMBERS = (1, 2, 3, 4, 5, 6)
STATUS = "/api/postings/assess/status"
CANCEL = "/api/postings/assess/cancel"


@pytest.fixture
def served(tmp_path, monkeypatch):
    from gigai.scout.find_jobs import assess_all
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    monkeypatch.setenv("GIGAI_SCOUT_POSTING_LIVENESS", "0")
    monkeypatch.setattr(assess_all, "assess_concurrency", lambda cpus=None: 2)
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    now = datetime.now(UTC)
    fx.seed(
        "cx", [lever_job("cx", n, text=f"Posting {n}: Python services, variant {n}.", created=now - timedelta(hours=n)) for n in NUMBERS],
        seen_at=now - timedelta(minutes=30),
    )
    gate = gate_model_calls(monkeypatch, tmp_path / "gate")
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fx, f"http://127.0.0.1:{server.server_address[1]}", gate
    finally:
        gate.release()
        server.shutdown()
        server.server_close()
        thread.join(10)


def _call(url: str, body: dict | None = None, *, origin: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _until(read, wanted, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    while True:
        found = read()
        if wanted(found):
            return found
        if time.monotonic() > deadline:
            raise AssertionError(f"still {found!r}")
        time.sleep(0.05)


def _assessed(fx) -> set[int]:
    from gigai.scout.quick_assess import read_quick_assessment

    return {n for n in NUMBERS if read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, job_url("cx", n))}


def test_cancel_stops_after_the_calls_in_flight_keeps_what_finished_and_leaves_no_model_call(served, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.adapters.process import live_children
    from gigai.scout.pipeline import busy
    from gigai.scout.pipeline.store import PipelineStore

    fx, url, gate = served
    jobs = [job_url("cx", n) for n in NUMBERS]
    assert _call(url + STATUS) == (200, {"schema_version": "scout-assess-batch:1", "running": False, "batch": None, "last": None})
    assert _call(url + CANCEL, {})[1]["cancel_requested"] == 0  # nothing runs: nothing changes

    status, asked = _call(url + "/api/postings/assess", {"jobs": jobs})
    assert status == 200 and asked["status"] == "ask" and asked["question"]["batch"] == 6

    began = time.monotonic()
    status, started = _call(url + "/api/postings/assess", {**asked["question"]["yes"]["api"]["body"], "background": True})
    assert status == 202 and started["status"] == "started" and started["running"] is True, started
    assert time.monotonic() - began < 20, "the request waited for the batch"
    assert started["batch"]["total"] == 6 and started["batch"]["here"] is True

    first = gate.wait_started(2)
    time.sleep(0.3)
    assert len(gate.started()) == 2, "two at a time"
    # The read the page polls: no posting row is read, nothing is written to the pipeline file.
    row_reads = []
    real_postings = PipelineStore.postings
    monkeypatch.setattr(PipelineStore, "postings", lambda self, *a, **k: row_reads.append(1) or real_postings(self, *a, **k))
    running = _call(url + STATUS)[1]
    monkeypatch.setattr(PipelineStore, "postings", real_postings)
    assert row_reads == []
    batch = running["batch"]
    assert (batch["status"], batch["total"], batch["assessed"], batch["failed"], batch["in_flight"]) == ("running", 6, 0, 0, 2), batch
    assert sorted(batch["pending"]) == sorted(jobs) and batch["profile_id"] == fx.default_profile_id
    assert _call(url + "/api/postings/assess", {"jobs": jobs, "approve": True, "background": True})[0] == 409  # one batch at a time
    assert _call(url + STATUS)[1]["batch"]["id"] == batch["id"]

    status, cancelling = _call(url + CANCEL, {})
    assert status == 200 and cancelling["cancel_requested"] == 1 and cancelling["batch"]["status"] == "cancelling", cancelling
    time.sleep(0.5)
    assert all(alive(pid) for pid in first), "cancel must not kill the calls in flight"
    assert len(gate.started()) == 2 and _assessed(fx) == set()

    gate.release()
    done = _until(lambda: _call(url + STATUS)[1], lambda found: not found["running"])
    last = done["last"]
    assert (last["status"], last["requested"], last["assessed"], last["failed"], last["not_started"]) == ("cancelled", 6, 2, 0, 4), last
    assert last["id"] == batch["id"] and done["batch"] is None
    assert len(_assessed(fx)) == 2, "what finished is kept"
    assert len(gate.started()) == 2, "a cancelled batch started another model call"
    _until(lambda: [pid for pid in first if alive(pid)], lambda left: left == [])
    assert live_children() == ()
    assert not any(busy.live_dir(fx.home_root, fx.target).iterdir()), "the marker or its cancel file was left"

    # The rest: the same approval again, this time waited for (no `background`), as the CLI and an agent do.
    status, rest = _call(url + "/api/postings/assess", {"jobs": jobs, "approve": True})
    assert status == 200 and rest["status"] == "assessed" and rest["assessed"]["requested"] == 4 and rest["assessed"]["stopped"] is None, rest["assessed"]
    assert _assessed(fx) == set(NUMBERS)


def test_a_background_batch_that_is_not_cancelled_ends_done_and_the_rows_are_assessed(served) -> None:
    fx, url, gate = served
    jobs = [job_url("cx", n) for n in NUMBERS[:4]]
    gate.release()  # no call is held
    status, started = _call(url + "/api/postings/assess", {"jobs": jobs, "approve": True, "background": True})
    assert status == 202 and started["batch"]["total"] == 4
    done = _until(lambda: _call(url + STATUS)[1], lambda found: not found["running"] and found["last"] is not None)
    assert (done["last"]["status"], done["last"]["requested"], done["last"]["assessed"], done["last"]["not_started"]) == ("done", 4, 4, 0)
    assert _assessed(fx) == {1, 2, 3, 4}
    rows = {row["job_identity"]: row["state"] for row in _call(url + "/api/postings?limit=50")[1]["postings"]["rows"]}
    assert all(rows[job] != "not_assessed" for job in jobs)
    # Nothing left to assess: the background call answers what the plain one does.
    status, nothing = _call(url + "/api/postings/assess", {"jobs": jobs, "approve": True, "background": True})
    assert status == 200 and nothing["status"] == "nothing_to_assess"
    assert _call(url + STATUS)[1]["last"]["status"] == "done", "a call that assessed nothing must not hide how the last batch ended"


def test_the_two_routes_refuse_keys_and_the_cancel_refuses_another_origin(served) -> None:
    _fx, url, _gate = served
    assert _call(url + STATUS + "?job=1")[1]["error"]["code"] == "unknown_key"
    status, error = _call(url + CANCEL, {"force": True})
    assert status == 422 and error["error"]["code"] == "unknown_key"
    status, error = _call(url + CANCEL, {}, origin="http://evil.example")
    assert status == 403 and error["error"]["code"] == "forbidden_origin"
    status, error = _call(url + "/api/postings/assess", {"jobs": ["https://jobs.lever.co/cx/cx-00001"], "background": "yes"})
    assert status == 422 and error["error"]["code"] == "wrong_type"


def test_the_command_cancels_a_batch_of_another_process_through_its_marker(served) -> None:
    from click.testing import CliRunner

    from gigai.cli import cli

    fx, url, gate = served
    home = ["--home", str(fx.home_root), "--target", str(fx.target)]
    idle = CliRunner().invoke(cli, ["scout", "jobs", "assess", "--cancel", "--json", *home])
    assert idle.exit_code == 0 and json.loads(idle.output)["cancel_requested"] == 0, idle.output

    jobs = [job_url("cx", n) for n in NUMBERS]
    assert _call(url + "/api/postings/assess", {"jobs": jobs, "approve": True, "background": True})[0] == 202
    gate.wait_started(2)
    said = CliRunner().invoke(cli, ["scout", "jobs", "assess", "--cancel", *home])
    assert said.exit_code == 0 and "Cancelling the assess batch: 0 of 6 assessed so far." in said.output, said.output
    assert "what finished is kept" in said.output
    refused = CliRunner().invoke(cli, ["scout", "jobs", "assess", "--cancel", "--yes", *home])
    assert refused.exit_code != 0
    gate.release()
    done = _until(lambda: _call(url + STATUS)[1], lambda found: not found["running"])
    assert (done["last"]["status"], done["last"]["assessed"], done["last"]["not_started"]) == ("cancelled", 2, 4)
