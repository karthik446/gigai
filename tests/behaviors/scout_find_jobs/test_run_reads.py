"""run-reads-fast (uat-bug-022): a run's reads, sized for the page that asks.

After a full-catalog run the Jobs page stayed empty for about a minute:
``GET /api/runs`` built every run's results (16 s), ``.../results``
answered every row with its posting text and scored the rows with Jev on
the way (45 s, 3.9 MB), and ``.../progress`` carried the text again
(3.7 MB). The wall-clock guard at catalog size is
``tests/api_e2e/test_run_reads_fast_journey.py``; what is pinned here, with
counts and spies instead of time:

* ``GET /api/runs`` counts come from the sealed outputs and no results
  payload is built; a finished run is read once; ``?status=&limit=`` reads
  the newest runs only until it has its N;
* one run's committed evidence is ONE journal snapshot, and the
  path-by-path read a busy journal falls back to gives the same evidence;
* a status poll of a run that is still going reads no sealed artifact;
* ``.../results?limit=&offset=``: the page, its order (the grid's), its
  edges, and what it refuses; no posting text; never a Jev client;
* ``.../posting?url=``: one posting, complete;
* ``.../progress?summary=1``: no posting text, no list of skipped boards;
* the no-query ``/results`` and ``/progress`` answer what they did;
* ``runs_list.py`` still has every name another route module imports from
  it (``answers.find_run_posting`` reads a run's profile with
  ``_run_profile_id``: removing it made ``POST /api/answers`` answer 500).

One real offline run (the ``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP``/``_MODEL``
seams, ``test_m1_end_to_end``'s ``_fixture``) is shared by the HTTP tests;
the order and paging rules are checked on rows built here.
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest

import gigai.journal as journal
from gigai import run as run_module
from gigai.scout import projection
from gigai.scout.find_jobs import jev_client, jev_rank
from gigai.scout.find_jobs.api import run_reads, runs_list
from gigai.scout.find_jobs.contracts import (
    AggregateStatus,
    AssessmentResult,
    ATSProvider,
    CarriedForwardAssessment,
    NotAssessedReason,
    NotAssessedRow,
    PostingRow,
    PostingRowResult,
    RowOutcome,
    SelectedPosting,
    SourceKind,
    Verdict,
)
from gigai.scout.find_jobs.jev_contracts import RankScore
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.workpad import resolve_workpad

from .test_m1_end_to_end import _fixture, _run_request

TERMINAL = {"succeeded", "failed", "blocked", "cancelled", "interrupted"}


# --------------------------------------------------------------------------
# One finished run, served in process
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def finished_run(tmp_path_factory: pytest.TempPathFactory):
    tmp_path = tmp_path_factory.mktemp("run-reads")
    home, target, _workpad = _fixture(tmp_path)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("EXA_API_KEY", "run-reads-test-key")
        monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
        monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")
        backend = ScoutFindJobsBackend(home_root=home, target=target)
        server = serve(backend=backend, bind=("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[0], server.server_address[1]
        try:
            with httpx.Client(base_url=f"http://{host}:{port}", timeout=60.0) as client:
                digest = client.get("/api/config").json()["config_digest"]
                started = client.post("/api/run", json=_run_request(digest))
                assert started.status_code == 202, started.text
                run_id = started.json()["run_id"]
                deadline = time.monotonic() + 60.0
                status = "pending"
                while status not in TERMINAL:
                    assert time.monotonic() < deadline, status
                    time.sleep(0.05)
                    status = client.get(f"/api/runs/{run_id}").json()["status"]
                assert status == "succeeded"
                resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
                yield SimpleNamespace(
                    client=client, backend=backend, run_id=run_id, home=home, target=target, resolved=resolved
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@pytest.fixture
def unread(finished_run):
    """Nothing kept in this process: the next read is a first read."""

    with projection._RUN_EVIDENCE_CACHE_LOCK:
        projection._run_evidence_cache.clear()
    with runs_list._RUN_ROW_CACHE_LOCK:
        runs_list._run_row_cache.clear()
    with run_reads._JOINS_CACHE_LOCK:
        run_reads._joins_cache.clear()
    return finished_run


@pytest.fixture
def run_snapshots(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """The prefixes of every committed snapshot that reads a run directory."""

    taken: list[tuple[str, ...]] = []
    real = projection.read_committed_snapshot

    def spying(**kwargs):
        prefixes = tuple(kwargs.get("prefixes", ()))
        if any(prefix.startswith("runs/") for prefix in prefixes):
            taken.append(prefixes)
        return real(**kwargs)

    monkeypatch.setattr(projection, "read_committed_snapshot", spying)
    return taken


@pytest.fixture
def artifact_reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every path read one artifact at a time (``read_committed_artifact``)."""

    paths: list[str] = []
    real = journal.read_committed_artifact

    def spying(**kwargs):
        paths.append(kwargs["path"])
        return real(**kwargs)

    monkeypatch.setattr(projection, "read_committed_artifact", spying)
    return paths


# --------------------------------------------------------------------------
# GET /api/runs
# --------------------------------------------------------------------------


def test_runs_list_counts_are_the_sealed_outputs_and_no_results_are_built(
    unread, monkeypatch: pytest.MonkeyPatch, run_snapshots: list[tuple[str, ...]], artifact_reads: list[str]
) -> None:
    fx = unread
    expected = fx.client.get(f"/api/runs/{fx.run_id}/results").json()["payload"]
    with projection._RUN_EVIDENCE_CACHE_LOCK:
        projection._run_evidence_cache.clear()
    run_snapshots.clear()
    artifact_reads.clear()

    def refuse(*_args, **_kwargs):
        raise AssertionError("GET /api/runs built a run's results")

    monkeypatch.setattr(projection, "build_present_payload", refuse)
    monkeypatch.setattr(projection, "present_payload_from", refuse)
    monkeypatch.setattr(fx.backend, "run_results", refuse)
    monkeypatch.setattr(fx.backend, "run_status", refuse)  # a finished run's status is its committed details
    monkeypatch.setattr(fx.backend, "_payload", refuse)

    listed = fx.client.get("/api/runs")

    assert listed.status_code == 200, listed.text
    [entry] = listed.json()["runs"]
    assert entry["run_id"] == fx.run_id and entry["status"] == "succeeded"
    assert entry["counts"] == {
        "found": len(expected["rows"]),
        "new": sum(1 for row in expected["rows"] if row["outcome"] == "new"),
        "assessed": len(expected["assessments"]),
        "matched": sum(1 for item in expected["assessments"] if item.get("verdict") == "matched_above_threshold"),
    }
    assert entry["counts"]["found"] >= 1 and entry["counts"]["assessed"] >= 1
    # One snapshot of the run's directory, and no artifact read on its own.
    assert run_snapshots == [(f"runs/{fx.run_id}/",)]
    assert artifact_reads == []


def test_a_finished_run_is_read_once(unread, run_snapshots: list[tuple[str, ...]], artifact_reads: list[str]) -> None:
    fx = unread
    first = fx.client.get("/api/runs").json()
    assert len(run_snapshots) == 1

    again = fx.client.get("/api/runs").json()
    filtered = fx.client.get("/api/runs", params={"profile_id": first["runs"][0]["profile_id"]}).json()
    other = fx.client.get("/api/runs", params={"profile_id": "profile_does_not_exist"}).json()

    assert again == first and filtered == first
    assert other["runs"] == []
    assert len(run_snapshots) == 1, "a finished run was read again"
    assert artifact_reads == []


def test_a_run_whose_details_are_replaced_is_read_again(
    unread, monkeypatch: pytest.MonkeyPatch, run_snapshots: list[tuple[str, ...]]
) -> None:
    fx = unread
    first = fx.client.get("/api/runs").json()
    assert len(run_snapshots) == 1
    real = projection.working_run_details_digest
    monkeypatch.setattr(
        projection, "working_run_details_digest", lambda resolved, run_id: "sha256:" + "0" * 64
    )

    again = fx.client.get("/api/runs").json()

    assert again == first
    assert len(run_snapshots) == 2
    # What was read is never filed under a digest the file on disk does not have.
    monkeypatch.setattr(projection, "working_run_details_digest", real)
    assert fx.client.get("/api/runs").json() == first
    assert len(run_snapshots) == 2


def test_a_run_that_is_still_going_takes_its_status_from_run_status(unread, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = unread
    evidence = projection.read_run_evidence(fx.resolved, fx.run_id)
    going = projection.RunEvidence(
        run_id=evidence.run_id,
        details={**evidence.details, "status": "running"},
        run_input=evidence.run_input,
        acquire_output=evidence.acquire_output,
        assess_output=None,
        receipts=evidence.receipts[:1],
    )
    asked: list[str] = []

    def run_status(run_id: str):
        asked.append(run_id)
        return SimpleNamespace(status=AggregateStatus.RUNNING)

    monkeypatch.setattr(projection, "read_runs_evidence", lambda resolved, run_ids: {fx.run_id: going})
    monkeypatch.setattr(fx.backend, "run_status", run_status)

    for _ in range(2):
        [entry] = fx.client.get("/api/runs").json()["runs"]
        assert entry["status"] == "running"
        assert entry["counts"] == {"found": len(evidence.acquire_output.rows), "new": entry["counts"]["new"], "assessed": 0, "matched": 0}

    assert asked == [fx.run_id, fx.run_id], "a run that is still going is never kept"


def test_the_list_filters_by_status_and_keeps_the_newest_n(unread) -> None:
    fx = unread
    everything = fx.client.get("/api/runs").json()
    profile_id = everything["runs"][0]["profile_id"]

    newest = fx.client.get("/api/runs", params={"profile_id": profile_id, "status": "succeeded", "limit": 1})

    assert newest.status_code == 200, newest.text
    assert newest.json() == everything
    assert fx.client.get("/api/runs", params={"status": "failed"}).json()["runs"] == []
    assert fx.client.get("/api/runs", params={"status": "failed", "limit": 1}).json()["runs"] == []
    assert fx.client.get("/api/runs", params={"limit": 500}).json() == everything
    for limit in ("0", "501", "-1", "one", ""):
        refused = fx.client.get(f"/api/runs?limit={limit}")
        assert refused.status_code == 422, (limit, refused.text)
        assert refused.json()["error"]["code"] == "invalid_value"


def test_a_limited_list_reads_the_newest_runs_only_until_it_has_its_n(unread, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = unread
    run_ids = [f"run_{index:02d}" for index in range(10)]  # newest first
    failed = {"run_00", "run_01", "run_02", "run_03", "run_05"}
    read: list[list[str]] = []

    def run_rows(_backend, _resolved, asked: list[str]) -> list[dict[str, object]]:
        read.append(list(asked))
        return [
            {"run_id": run_id, "created_at": None, "profile_id": "profile_1", "status": "failed" if run_id in failed else "succeeded", "counts": {}}
            for run_id in asked
        ]

    monkeypatch.setattr(runs_list, "_run_ids_newest_first", lambda _resolved: list(run_ids))
    monkeypatch.setattr(runs_list, "_run_rows", run_rows)

    def listed(**params: object) -> list[str]:
        read.clear()
        response = fx.client.get("/api/runs", params=params)
        assert response.status_code == 200, response.text
        return [entry["run_id"] for entry in response.json()["runs"]]

    assert listed(limit=1) == ["run_00"] and read == [run_ids[:4]]
    assert listed(limit=6) == run_ids[:6] and read == [run_ids[:6]]
    # The newest four failed: the next four are read, and no more.
    assert listed(status="succeeded", limit=1) == ["run_04"] and read == [run_ids[:4], run_ids[4:8]]
    assert listed(status="succeeded", limit=3) == ["run_04", "run_06", "run_07"] and read == [run_ids[:4], run_ids[4:8]]
    assert listed(status="succeeded", limit=5) == ["run_04", "run_06", "run_07", "run_08", "run_09"]
    assert read == [run_ids[:5], run_ids[5:]]
    assert listed(profile_id="profile_2", limit=1) == [] and read == [run_ids[:4], run_ids[4:8], run_ids[8:]]
    # No limit: every run, in one read.
    assert listed() == run_ids and read == [run_ids]
    assert listed(status="failed") == sorted(failed) and read == [run_ids]


def _names_imported_from_runs_list() -> dict[str, set[str]]:
    """``{importing module: names}`` for every ``from .runs_list import ...`` in the api package."""

    package = Path(runs_list.__file__).resolve().parent
    found: dict[str, set[str]] = {}
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "runs_list":
                found.setdefault(path.name, set()).update(alias.name for alias in node.names)
    return found


def test_runs_list_has_every_name_the_other_routes_import_from_it() -> None:
    imported = _names_imported_from_runs_list()

    # Not vacuous: the scan finds the two importers this packet knows.
    assert imported["server.py"] == {"RunsListRoutesMixin"}
    assert {"_run_ids_newest_first", "_run_profile_id"} <= imported["answers.py"]
    missing = {
        f"{module}: {name}" for module, names in imported.items() for name in names if not hasattr(runs_list, name)
    }
    assert missing == set(), f"runs_list.py no longer has a name another route imports: {sorted(missing)}"


def test_a_run_s_profile_is_read_the_way_the_answers_route_reads_it(unread) -> None:
    fx = unread
    from gigai.scout.find_jobs.api.answers import find_run_posting

    [entry] = fx.client.get("/api/runs").json()["runs"]
    row = fx.client.get(f"/api/runs/{fx.run_id}/results", params={"limit": 1}).json()["payload"]["rows"][0]

    assert runs_list._run_ids_newest_first(fx.resolved) == [fx.run_id]
    assert runs_list._run_profile_id(resolved=fx.resolved, run_id=fx.run_id, default_profile_id=None) == entry["profile_id"]
    # A run with no sealed input (a legacy run, or no such run) is the default's.
    unknown = "run_00000000-0000-4000-8000-000000000000"
    assert runs_list._run_profile_id(resolved=fx.resolved, run_id=unknown, default_profile_id="profile_default") == "profile_default"

    found = find_run_posting(fx.home, fx.target, row["posting"]["normalized_url"])
    assert found is not None
    posting, profile_id = found
    assert posting.normalized_url == row["posting"]["normalized_url"] and posting.text
    assert profile_id == entry["profile_id"]
    assert find_run_posting(fx.home, fx.target, "https://boards.greenhouse.io/nobody/jobs/1") is None


# --------------------------------------------------------------------------
# One run's committed evidence
# --------------------------------------------------------------------------


def test_the_path_by_path_read_gives_the_same_evidence(
    unread, monkeypatch: pytest.MonkeyPatch, artifact_reads: list[str]
) -> None:
    fx = unread
    from_snapshot = projection.read_run_evidence(fx.resolved, fx.run_id)
    assert artifact_reads == []
    with projection._RUN_EVIDENCE_CACHE_LOCK:
        projection._run_evidence_cache.clear()

    # A journal that cannot give a snapshot now: a transition in flight and
    # a writer that holds the lock.
    def busy(**_kwargs):
        raise journal.InterprocessLockUnavailable("writer lock timeout")

    monkeypatch.setattr(projection, "read_committed_snapshot", busy)
    path_by_path = projection.read_run_evidence(fx.resolved, fx.run_id)

    assert path_by_path == from_snapshot
    assert path_by_path.terminal and path_by_path.details_status == "succeeded"
    assert sorted(path.removeprefix(f"runs/{fx.run_id}/") for path in artifact_reads) == [
        "outputs/acquire.json",
        "outputs/assess.json",
        "receipts/acquire.json",
        "receipts/assess.json",
        "receipts/present.json",
        "run-details.json",
        "sealed/find-jobs-run-input.json",
    ]
    assert projection.build_present_payload(
        home_root=fx.home, target=fx.target, run_id=fx.run_id
    ) == projection.present_payload_from(from_snapshot)


def test_a_status_poll_of_a_run_that_is_still_going_reads_no_sealed_artifact(
    unread, monkeypatch: pytest.MonkeyPatch, run_snapshots: list[tuple[str, ...]], artifact_reads: list[str]
) -> None:
    fx = unread
    for status, expected in (("preparing", "pending"), ("running", "running"), ("verifying", "running")):
        monkeypatch.setattr(run_module, "read_run_details", lambda status=status, **_kwargs: {"status": status})
        response = fx.client.get(f"/api/runs/{fx.run_id}")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == expected
        assert response.json()["node_receipts"] == []
    assert run_snapshots == [] and artifact_reads == []


def test_a_finished_run_s_status_carries_its_receipts(unread) -> None:
    fx = unread
    body = fx.client.get(f"/api/runs/{fx.run_id}").json()
    assert body["status"] == "succeeded"
    assert [item["node_slug"] for item in body["node_receipts"]] == ["acquire", "assess", "present"]


# --------------------------------------------------------------------------
# GET /api/runs/{run_id}/results?limit=&offset=
# --------------------------------------------------------------------------


def test_a_results_page_is_the_full_read_without_the_text(unread) -> None:
    fx = unread
    full = fx.client.get(f"/api/runs/{fx.run_id}/results").json()
    page = fx.client.get(f"/api/runs/{fx.run_id}/results", params={"limit": 100}).json()

    assert page["schema_version"] == full["schema_version"] and page["run_id"] == fx.run_id
    assert (page["total"], page["limit"], page["offset"]) == (len(full["payload"]["rows"]), 100, 0)
    assert page["created_at"] == fx.client.get("/api/runs").json()["runs"][0]["created_at"]
    assert page["counts"] == fx.client.get("/api/runs").json()["runs"][0]["counts"]
    # uat-bug-025: the run's own assess cap rides on every page (what "Not assessed: this run assessed its top N" reads).
    assert isinstance(page["assess_cap"], int) and page["assess_cap"] >= 1
    assert set(page["payload"]) == set(full["payload"])
    for key in ("schema_version", "run_id", "config", "pinned_resume", "failures", "assessments", "node_receipts", "status"):
        assert page["payload"][key] == full["payload"][key], key
    assert page["carried_forward_assessments"] == full["carried_forward_assessments"]

    assert len(page["payload"]["rows"]) == len(full["payload"]["rows"]) >= 1
    by_url = {row["posting"]["normalized_url"]: row for row in full["payload"]["rows"]}
    for row in page["payload"]["rows"]:
        whole = by_url[row["posting"]["normalized_url"]]
        assert whole["posting"]["text"], "the full read has the posting's text"
        assert "text" not in row["posting"]
        assert row["posting"] == {key: value for key, value in whole["posting"].items() if key != "text"}
        assert row["outcome"] == whole["outcome"]
        assert row.get("h1b") == whole.get("h1b")
        assert row.get("job_state") == whole.get("job_state")
        assert row["rank_score"] is None  # no Jev key in this project: nothing is stored
    # Left out of a page: see run_reads.py.
    assert {"rank_scores", "resume_label", "resume_created_at"} <= set(full)
    assert not {"rank_scores", "resume_label", "resume_created_at"} & set(page)


def test_results_page_edges(unread) -> None:
    fx = unread
    route = f"/api/runs/{fx.run_id}/results"
    total = fx.client.get(route, params={"limit": 1}).json()["total"]

    past_the_end = fx.client.get(route, params={"limit": 5, "offset": total}).json()
    assert past_the_end["payload"]["rows"] == [] and past_the_end["payload"]["assessments"] == []
    assert (past_the_end["total"], past_the_end["offset"]) == (total, total)
    assert fx.client.get(route, params={"limit": 500}).status_code == 200
    assert fx.client.get(route, params={"limit": 1, "offset": 0}).json()["payload"]["rows"]


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("limit=0", "invalid_value"),
        ("limit=501", "invalid_value"),
        ("limit=-1", "invalid_value"),
        ("limit=ten", "invalid_value"),
        ("limit=", "invalid_value"),
        ("offset=3", "invalid_value"),
        ("limit=5&offset=-1", "invalid_value"),
        ("limit=5&offset=x", "invalid_value"),
        ("page=2", "unknown_key"),
        ("limit=5&text=1", "unknown_key"),
    ],
)
def test_results_page_refuses_an_invalid_query(finished_run, query: str, code: str) -> None:
    response = finished_run.client.get(f"/api/runs/{finished_run.run_id}/results?{query}")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code


def test_the_page_reads_of_an_unknown_run_are_404(finished_run) -> None:
    client = finished_run.client
    unknown = "run_00000000-0000-4000-8000-000000000000"
    for path in (f"/api/runs/{unknown}/results?limit=5", f"/api/runs/{unknown}/progress?summary=1", f"/api/runs/{unknown}/posting?url=x"):
        response = client.get(path)
        assert response.status_code == 404, (path, response.text)
        assert response.json()["error"]["code"] == "not_found"


def test_a_results_page_never_asks_jev(unread, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = unread
    monkeypatch.setenv("JEV_API_KEY", "run-reads-test-jev-key")  # a project WITH a key

    def refuse(*_args, **_kwargs):
        raise AssertionError("a page read built a Jev client")

    monkeypatch.setattr(jev_client.JevClient, "__init__", refuse)
    monkeypatch.setattr(jev_rank, "rank_postings_report", refuse)
    monkeypatch.setattr(jev_rank, "rank_postings", refuse)

    page = fx.client.get(f"/api/runs/{fx.run_id}/results", params={"limit": 100})
    detail_url = page.json()["payload"]["rows"][0]["posting"]["normalized_url"]
    detail = fx.client.get(f"/api/runs/{fx.run_id}/posting", params={"url": detail_url})

    assert page.status_code == 200 and detail.status_code == 200


def test_a_page_carries_the_scores_already_stored(unread, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = unread
    asked: list[dict[str, object]] = []

    def read_cached_scores(rows, **kwargs):
        asked.append(kwargs)
        return tuple(_score(row.normalized_url, 88 if index == 0 else None) for index, row in enumerate(rows))

    # The score cache's page reader (jev_rank.read_cached_scores); a build
    # without one reads the run's sealed scores only.
    monkeypatch.setattr(jev_rank, "read_cached_scores", read_cached_scores, raising=False)

    page = fx.client.get(f"/api/runs/{fx.run_id}/results", params={"limit": 100}).json()

    [first, *rest] = page["payload"]["rows"]
    assert first["rank_score"]["score"] == 88 and first["rank_score"]["fit"] == "strong"
    assert first["rank_score"]["normalized_url"] == first["posting"]["normalized_url"]
    assert all(row["rank_score"] is None for row in rest)
    [call] = asked
    assert call["resume_text"] and call["profile_id"] and call["resume_revision_id"]
    assert (call["home_root"], call["target"]) == (fx.backend.home_root, fx.backend.target)


# --------------------------------------------------------------------------
# GET /api/runs/{run_id}/posting?url=
# --------------------------------------------------------------------------


def test_a_posting_is_read_complete(unread) -> None:
    fx = unread
    full = fx.client.get(f"/api/runs/{fx.run_id}/results").json()
    whole = full["payload"]["rows"][0]
    url = whole["posting"]["normalized_url"]
    assessment = next(item for item in full["payload"]["assessments"] if item["posting"]["normalized_url"] == url)

    response = fx.client.get(f"/api/runs/{fx.run_id}/posting?url={quote(url, safe='')}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"schema_version", "run_id", "row", "assessment", "not_assessed_reason", "carried_forward"}
    assert body["schema_version"] == "scout-find-jobs-run-posting:1" and body["run_id"] == fx.run_id
    assert body["row"]["posting"] == whole["posting"] and body["row"]["posting"]["text"]
    assert body["row"]["outcome"] == whole["outcome"]
    assert body["row"].get("h1b") == whole.get("h1b")
    assert body["row"].get("job_state") == whole.get("job_state")
    assert body["row"]["rank_score"] is None
    assert body["assessment"] == assessment and body["assessment"]["matrix"]
    assert body["not_assessed_reason"] is None and body["carried_forward"] is None


def test_the_full_read_s_scores_are_one_entry_per_posting_in_the_run_s_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, postings = _view()
    handler = run_reads.RunReadsRoutesMixin()
    handler._backend = SimpleNamespace(home_root=tmp_path)
    joins = _joins(resume_text="a resume")
    monkeypatch.setattr(handler, "_run_view", lambda run_id: (None, view, joins), raising=False)
    monkeypatch.setattr(jev_client, "has_api_key", lambda *, home_root: True)

    scores = handler._run_stored_rank_scores("run_1")

    # The run's own order (acquire's), not the grid's; unscored where nothing is stored.
    stored = {"plain-old": 75, "matched-low": 40, "carried": 60, "matched-high": 90}
    assert [(item.normalized_url, item.score, item.fit) for item in scores] == [
        (posting.normalized_url, stored.get(name), "strong" if name in stored else None)
        for name, posting in postings.items()
    ]
    assert len(scores) == 10

    # Nothing to score with or against: the list is empty, as it always was.
    monkeypatch.setattr(jev_client, "has_api_key", lambda *, home_root: False)
    assert handler._run_stored_rank_scores("run_1") == ()
    monkeypatch.setattr(jev_client, "has_api_key", lambda *, home_root: True)
    for without in (_joins(resume_text=None), run_reads.RowJoins(head="head", profile=None, resume_text=None, events=None)):
        monkeypatch.setattr(handler, "_run_view", lambda run_id, without=without: (None, view, without), raising=False)
        assert handler._run_stored_rank_scores("run_1") == ()


def test_a_posting_the_run_does_not_have_is_404_and_a_missing_url_422(finished_run) -> None:
    client, run_id = finished_run.client, finished_run.run_id
    missing = client.get(f"/api/runs/{run_id}/posting", params={"url": "https://boards.greenhouse.io/nobody/jobs/1"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    for query in ("", "url=", "url=x&text=1"):
        response = client.get(f"/api/runs/{run_id}/posting?{query}")
        assert response.status_code == 422, (query, response.text)


# --------------------------------------------------------------------------
# GET /api/runs/{run_id}/progress?summary=1
# --------------------------------------------------------------------------


def test_the_progress_summary_has_no_posting_text(unread) -> None:
    fx = unread
    full = fx.client.get(f"/api/runs/{fx.run_id}/progress").json()
    summary = fx.client.get(f"/api/runs/{fx.run_id}/progress", params={"summary": 1}).json()

    assert set(summary) == set(full)
    assert any(posting.get("text") for posting in full["postings"]), "the full read has the posting's text"
    assert summary["postings"] == [{key: value for key, value in posting.items() if key != "text"} for posting in full["postings"]]
    assert [item["normalized_url"] for item in summary["assessments"]] == [item["normalized_url"] for item in full["assessments"]]
    assert summary["assessments"][0]["assessment"]["matrix"] == full["assessments"][0]["assessment"]["matrix"]
    assert "skipped_boards" not in summary["boards"]
    for key in set(full) - {"postings", "assessments", "boards"}:
        assert summary[key] == full[key], key
    assert {key: value for key, value in full["boards"].items() if key != "skipped_boards"} == summary["boards"]
    assert fx.client.get(f"/api/runs/{fx.run_id}/progress?summary=0").json() == full


@pytest.mark.parametrize("query", ["summary=yes", "summary=", "limit=5", "summary=1&limit=5"])
def test_the_progress_summary_refuses_an_invalid_query(finished_run, query: str) -> None:
    response = finished_run.client.get(f"/api/runs/{finished_run.run_id}/progress?{query}")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] in {"invalid_value", "unknown_key"}


def test_summarise_progress_drops_the_text_wherever_a_posting_is() -> None:
    progress = {
        "schema_version": "scout-find-jobs-progress:1",
        "postings": [{"normalized_url": "https://a.test/1", "title": "Engineer", "text": "long"}, "not a posting"],
        "assessments": [
            {"normalized_url": "https://a.test/1", "status": "assessed", "assessment": {"posting": {"url": "u", "text": "long"}, "matrix": [1]}},
            {"normalized_url": "https://a.test/2", "status": "assessing"},
        ],
        "boards": {"skipped": 2, "skipped_boards": ["greenhouse:a", "greenhouse:b"], "total": 3},
        "cap": 5,
    }
    before = repr(progress)

    summary = run_reads.summarise_progress(progress)

    assert summary == {
        "schema_version": "scout-find-jobs-progress:1",
        "postings": [{"normalized_url": "https://a.test/1", "title": "Engineer"}, "not a posting"],
        "assessments": [
            {"normalized_url": "https://a.test/1", "status": "assessed", "assessment": {"posting": {"url": "u"}, "matrix": [1]}},
            {"normalized_url": "https://a.test/2", "status": "assessing"},
        ],
        "boards": {"skipped": 2, "total": 3},
        "cap": 5,
    }
    assert repr(progress) == before, "the backend's own response is never changed"


# --------------------------------------------------------------------------
# The grid's order, and a page of it
# --------------------------------------------------------------------------


def _posting(name: str, *, published_at: str | None = "2026-09-20T00:00:00Z") -> PostingRow:
    url = f"https://boards.example/acme/{name}"
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="acme", company="Acme",
        title=name, location="Denver, CO", published_at=published_at, content_sha256="sha256:" + "a" * 64,
        source_kind=SourceKind.ATS, query_key="ats:greenhouse:acme", text=f"{name}'s description",
    )


def _assessed(posting: PostingRow, verdict: Verdict | None) -> AssessmentResult:
    selected = SelectedPosting(posting.normalized_url, posting.url, "sha256:" + "a" * 64, True)
    return AssessmentResult(posting=selected, matrix=(), suggestions=(), questions=(), proposal_revision_ref=None, verdict=verdict)


def _score(url: str, score: int | None) -> RankScore:
    return RankScore(
        normalized_url=url, content_sha256="sha256:" + "a" * 64, fit=None if score is None else "strong",
        score=score, reasons=(), mismatch_flags=(), hidden_by_default=False, cost_usd="0", cached=True,
    )


def _view(scores: dict[str, RankScore] | None = None) -> tuple[run_reads.RunView, dict[str, PostingRow]]:
    names = ("plain-old", "no-match", "matched-low", "carried", "needs-answers", "matched-high", "plain-new", "undated", "unverdicted", "refused")
    postings = {name: _posting(name) for name in names}
    postings["plain-new"] = _posting("plain-new", published_at="2026-09-25T00:00:00Z")
    postings["undated"] = _posting("undated", published_at=None)
    acquire = SimpleNamespace(
        rows=tuple(PostingRowResult(posting, RowOutcome.NEW) for posting in postings.values()),
        carried_forward_assessments=(
            CarriedForwardAssessment(postings["carried"].normalized_url, _assessed(postings["carried"], Verdict.MATCHED_ABOVE_THRESHOLD), "2026-09-01T00:00:00Z"),
        ),
        rank_scores=(),
    )
    assess = SimpleNamespace(
        assessments=(
            _assessed(postings["no-match"], Verdict.NOT_A_MATCH),
            _assessed(postings["matched-low"], Verdict.MATCHED_ABOVE_THRESHOLD),
            _assessed(postings["needs-answers"], Verdict.PENDING_USER_ANSWERS),
            _assessed(postings["matched-high"], Verdict.MATCHED_ABOVE_THRESHOLD),
            _assessed(postings["unverdicted"], None),
        ),
        not_assessed=(NotAssessedRow(postings["refused"], NotAssessedReason.MODEL_DENIED),),
    )
    evidence = SimpleNamespace(run_id="run_1", acquire_output=acquire, assess_output=assess)
    if scores is None:
        scores = {
            postings["matched-high"].normalized_url: _score(postings["matched-high"].normalized_url, 90),
            postings["matched-low"].normalized_url: _score(postings["matched-low"].normalized_url, 40),
            postings["carried"].normalized_url: _score(postings["carried"].normalized_url, 60),
            postings["plain-old"].normalized_url: _score(postings["plain-old"].normalized_url, 75),
        }
    return run_reads.RunView(evidence, scores=scores), postings


def test_rows_are_in_the_grid_s_order() -> None:
    view, _postings = _view()
    # jobModel.sortJobs: matched, needs answers, assessed with no verdict,
    # not assessed, not a match; the stored score inside a group (none
    # last); then the newest posting, one with no date last.
    assert [row.posting.title for row in view.rows] == [
        "matched-high",  # matched, 90
        "carried",  # matched (carried forward), 60
        "matched-low",  # matched, 40
        "needs-answers",
        "unverdicted",
        "plain-old",  # not assessed, 75
        "plain-new",  # not assessed, no score, 2026-09-25
        "refused",  # not assessed, no score, 2026-09-20
        "undated",  # not assessed, no score, no date
        "no-match",
    ]


def test_without_scores_the_order_is_the_verdict_then_the_newest() -> None:
    view, _postings = _view(scores={})
    assert [row.posting.title for row in view.rows][:3] == ["matched-low", "carried", "matched-high"]  # acquire's own order
    assert [row.posting.title for row in view.rows][5:9] == ["plain-new", "plain-old", "refused", "undated"]


def test_pages_cover_the_run_once_and_join_only_their_own_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    view, postings = _view()
    payload = SimpleNamespace(
        schema_version="scout-find-jobs-present-payload:1", run_id="run_1",
        config=SimpleNamespace(to_json=lambda: {"roles": []}), pinned_resume=None, failures=(), node_receipts=(),
        status=AggregateStatus.SUCCEEDED,
    )
    monkeypatch.setattr(projection, "present_payload_from", lambda evidence: payload)
    monkeypatch.setattr(run_reads, "run_counts", lambda evidence: {"found": 10, "new": 10, "assessed": 5, "matched": 2})
    view.evidence.started_at = "2026-09-28T00:00:00Z"

    pages = [run_reads.results_page(view, limit=4, offset=offset) for offset in (0, 4, 8, 12)]

    assert [len(page["payload"]["rows"]) for page in pages] == [4, 4, 2, 0]
    assert all((page["total"], page["limit"]) == (10, 4) for page in pages)
    assert [page["offset"] for page in pages] == [0, 4, 8, 12]
    titles = [row["posting"]["title"] for page in pages for row in page["payload"]["rows"]]
    assert titles == [row.posting.title for row in view.rows] and len(set(titles)) == 10
    assert all("text" not in row["posting"] for page in pages for row in page["payload"]["rows"])

    first, second, third, _empty = pages
    urls = lambda page: {row["posting"]["normalized_url"] for row in page["payload"]["rows"]}  # noqa: E731
    for page in pages:
        assert {item["posting"]["normalized_url"] for item in page["payload"]["assessments"]} <= urls(page)
        assert {item["posting"]["normalized_url"] for item in page["payload"]["not_assessed"]} <= urls(page)
        assert {item["normalized_url"] for item in page["carried_forward_assessments"]} <= urls(page)
    assert len(first["payload"]["assessments"]) == 3 and len(first["carried_forward_assessments"]) == 1
    assert [item["posting"]["normalized_url"] for item in second["payload"]["assessments"]] == [postings["unverdicted"].normalized_url]
    assert "verdict" not in second["payload"]["assessments"][0]  # a result from before the verdict existed
    assert [item["reason"] for item in second["payload"]["not_assessed"]] == ["model_denied"]
    assert "text" not in second["payload"]["not_assessed"][0]["posting"]
    assert [item["posting"]["normalized_url"] for item in third["payload"]["assessments"]] == [postings["no-match"].normalized_url]
    assert first["payload"]["rows"][0]["rank_score"]["score"] == 90
    assert second["payload"]["rows"][2]["rank_score"] is None


def test_one_posting_s_rows_are_complete() -> None:
    view, postings = _view()

    refused = run_reads.posting_detail("run_1", run_reads.posting_rows(view, postings["refused"].normalized_url))
    carried = run_reads.posting_detail("run_1", run_reads.posting_rows(view, postings["carried"].normalized_url))

    assert refused["row"]["posting"]["text"] == "refused's description"
    assert (refused["assessment"], refused["not_assessed_reason"], refused["carried_forward"]) == (None, "model_denied", None)
    assert carried["assessment"] is None and carried["not_assessed_reason"] is None
    assert carried["carried_forward"]["from_run_date"] == "2026-09-01T00:00:00Z"
    assert carried["carried_forward"]["result"]["verdict"] == "matched_above_threshold"
    assert carried["row"]["rank_score"]["score"] == 60
    assert run_reads.posting_rows(view, "https://boards.example/acme/nobody") is None


# --------------------------------------------------------------------------
# The scores a page shows
# --------------------------------------------------------------------------


def _sealed(*, profile_id: str | None, revision_id: str, scores: tuple[RankScore, ...]):
    return SimpleNamespace(
        run_input=SimpleNamespace(
            pinned_resume=SimpleNamespace(revision_id=revision_id),
            profile_ref=None if profile_id is None else SimpleNamespace(profile_id=profile_id),
        ),
        acquire_output=SimpleNamespace(rank_scores=scores),
    )


def _joins(*, resume_text: str | None = None, titles: tuple[str, ...] = ("Engineer",)) -> run_reads.RowJoins:
    profile = SimpleNamespace(
        profile_id="profile_1", resume_ref=SimpleNamespace(record_id="record_1", revision_id="revision_1"), titles=titles
    )
    return run_reads.RowJoins(head="head", profile=profile, resume_text=resume_text, events=None)


def test_a_run_s_sealed_scores_count_only_for_the_resume_they_were_made_for(tmp_path: Path) -> None:
    rows = (_posting("a"), _posting("b"), _posting("c"))
    sealed = (_score(rows[0].normalized_url, 70), _score(rows[1].normalized_url, None), _score("https://elsewhere.test/1", 50))

    def stored(evidence, joins=_joins()):
        found = run_reads.stored_rank_scores(rows, evidence=evidence, joins=joins, home_root=tmp_path, target=tmp_path)
        return {url: item.score for url, item in found.items()}

    assert stored(_sealed(profile_id="profile_1", revision_id="revision_1", scores=sealed)) == {rows[0].normalized_url: 70}
    assert stored(_sealed(profile_id=None, revision_id="revision_1", scores=sealed)) == {rows[0].normalized_url: 70}
    assert stored(_sealed(profile_id="profile_2", revision_id="revision_1", scores=sealed)) == {}
    assert stored(_sealed(profile_id="profile_1", revision_id="revision_0", scores=sealed)) == {}
    no_profile = run_reads.RowJoins(head="head", profile=None, resume_text=None, events=None)
    assert stored(_sealed(profile_id="profile_1", revision_id="revision_1", scores=sealed), no_profile) == {}


def test_a_cached_score_wins_over_the_sealed_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (_posting("a"), _posting("b"), _posting("c"))
    sealed = (_score(rows[0].normalized_url, 70), _score(rows[1].normalized_url, 30))
    joins = _joins(resume_text="a resume", titles=("Engineer", "Staff Engineer"))
    asked: list[dict[str, object]] = []

    def read_cached_scores(cached_rows, **kwargs):
        asked.append(kwargs)
        return (_score(rows[0].normalized_url, 95), _score(rows[1].normalized_url, None), _score(rows[2].normalized_url, None))

    monkeypatch.setattr(jev_rank, "read_cached_scores", read_cached_scores, raising=False)

    found = run_reads.stored_rank_scores(
        rows, evidence=_sealed(profile_id="profile_1", revision_id="revision_1", scores=sealed),
        joins=joins, home_root=tmp_path, target=tmp_path,
    )

    assert {url: item.score for url, item in found.items()} == {rows[0].normalized_url: 95, rows[1].normalized_url: 30}
    # The read was actually reached with the reading profile's own titles as
    # the preferences slice (uat-bug-021 decision d's cache key): a stub that
    # ignored prefs would pass for the wrong reason.
    [call] = asked
    assert call["prefs"].target_titles == joins.profile.titles


def test_a_score_cache_that_cannot_be_read_costs_the_scores_not_the_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (_posting("a"),)

    def broken(*_args, **_kwargs):
        raise OSError("the cache is unreadable")

    monkeypatch.setattr(jev_rank, "read_cached_scores", broken, raising=False)
    found = run_reads.stored_rank_scores(
        rows, evidence=_sealed(profile_id="profile_1", revision_id="revision_1", scores=(_score(rows[0].normalized_url, 70),)),
        joins=_joins(resume_text="a resume"), home_root=tmp_path, target=tmp_path,
    )
    assert {url: item.score for url, item in found.items()} == {rows[0].normalized_url: 70}


def test_run_assess_cap_is_the_selection_cap_or_none() -> None:
    assert run_reads.run_assess_cap(SimpleNamespace(assess_output=SimpleNamespace(selection_cap=7))) == 7
    assert run_reads.run_assess_cap(SimpleNamespace(assess_output=None)) is None
    assert run_reads.run_assess_cap(SimpleNamespace()) is None
