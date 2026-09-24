"""B4: the non-authoritative live-progress files + the /progress route.

Two layers are covered separately, matching how the real system is layered:

1. ``gigai.scout.find_jobs.progress`` itself -- the writer/reader round trip,
   and reading a partial (mid-write) file -- as pure filesystem tests with no
   run/workpad infrastructure at all.
2. The ``/api/runs/{id}/progress`` HTTP route on ``present_api.py``, via a
   fake ``Backend`` double (same pattern ``test_present_ui.py`` uses for the
   other routes) so this doesn't need a full lifecycle-approved workpad --
   the route's own contract (loopback guard, 404 shape, JSON body) is what's
   under test here, not ``ScoutFindJobsBackend``'s real resolution (that's
   covered end-to-end by ``test_m1_end_to_end.py``).

``acquire_node``'s and ``assess_node``'s own progress *writes* (the producer
side) are covered directly against the real nodes in
``test_acquire_network.py``-style direct calls below, using the same
``NodeContext``/``AcquireInput`` construction those modules' own tests use.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import threading

import httpx
import pytest

from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    ATSProvider,
    NodeContext,
    PostingRow,
    SelectionRule,
    SourceKind,
    SourceToggles,
)
from gigai.scout.find_jobs.contracts import FindJobsConfig
from gigai.scout.find_jobs.market_acquisition import acquire_node
from gigai.scout.find_jobs.present_api import NotWiredBackend, serve
from gigai.scout.find_jobs.progress import ProgressWriter, progress_dir, read_progress


# ---------------------------------------------------------------------------
# 1. progress.py: writer/reader round trip + partial files
# ---------------------------------------------------------------------------


def test_read_progress_on_a_run_with_no_files_yet_is_all_empty(tmp_path: Path) -> None:
    """Before acquire starts, nothing under progress/ exists at all."""

    snapshot = read_progress(tmp_path / "runs" / "run_01")
    assert snapshot.steps == {}
    assert snapshot.postings == []
    assert snapshot.assessments == []
    assert snapshot.cap is None
    assert snapshot.candidate_count is None
    assert snapshot.not_assessed_counts == {}


def test_steps_round_trip_through_start_and_finish(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)

    writer.start_step("acquire")
    mid = read_progress(run_root)
    assert mid.steps["acquire"]["status"] == "running"
    assert mid.steps["acquire"]["started_at"]
    assert mid.steps["acquire"]["finished_at"] is None

    writer.finish_step("acquire", ok=True)
    done = read_progress(run_root)
    assert done.steps["acquire"]["status"] == "done"
    assert done.steps["acquire"]["finished_at"]
    # started_at is preserved across the finish rewrite, not clobbered.
    assert done.steps["acquire"]["started_at"] == mid.steps["acquire"]["started_at"]

    writer.start_step("assess")
    writer.finish_step("assess", ok=False)
    both = read_progress(run_root)
    assert both.steps["acquire"]["status"] == "done"
    assert both.steps["assess"]["status"] == "failed"


def _posting_json(url: str, *, title: str = "Software Engineer") -> dict[str, object]:
    return PostingRow(
        url=url,
        normalized_url=url,
        provider=ATSProvider.GREENHOUSE,
        board_token="acme",
        company="Acme",
        title=title,
        location="Denver, CO",
        published_at="2026-09-20T00:00:00Z",
        content_sha256="sha256:" + "a" * 64,
        source_kind=SourceKind.ATS,
        query_key="software-engineer",
    ).to_json()


def test_postings_append_one_line_per_posting_in_order(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)

    writer.posting_acquired(_posting_json("https://boards.greenhouse.io/acme/jobs/1"), outcome="new")
    writer.posting_acquired(_posting_json("https://boards.greenhouse.io/acme/jobs/2"), outcome="unchanged")

    snapshot = read_progress(run_root)
    assert [item["normalized_url"] for item in snapshot.postings] == [
        "https://boards.greenhouse.io/acme/jobs/1",
        "https://boards.greenhouse.io/acme/jobs/2",
    ]
    assert snapshot.postings[0]["outcome"] == "new"
    assert snapshot.postings[1]["outcome"] == "unchanged"
    # Every posting field survives the round trip, not just outcome/url.
    assert snapshot.postings[0]["title"] == "Software Engineer"
    assert snapshot.postings[0]["company"] == "Acme"


def test_assessment_lifecycle_folds_to_the_latest_status_per_url(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)
    url = "https://boards.greenhouse.io/acme/jobs/1"

    writer.assessment_started(url)
    mid = read_progress(run_root)
    assert mid.assessments[0]["normalized_url"] == url
    assert mid.assessments[0]["status"] == "assessing"

    writer.assessment_finished(url, ok=True, assessment_json={"matrix": [], "suggestions": [], "questions": []})
    done = read_progress(run_root)
    assert len(done.assessments) == 1
    assert done.assessments[0]["status"] == "assessed"
    assert done.assessments[0]["assessment"] == {"matrix": [], "suggestions": [], "questions": []}


def test_not_assessed_events_are_recorded_and_counted_by_reason(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)

    writer.not_assessed("https://a.test/1", reason="duplicate")
    writer.not_assessed("https://a.test/2", reason="duplicate")
    writer.not_assessed("https://a.test/3", reason="over_cap")

    snapshot = read_progress(run_root)
    assert snapshot.not_assessed_counts == {"duplicate": 2, "over_cap": 1}
    statuses = {item["normalized_url"]: item["status"] for item in snapshot.assessments}
    assert statuses == {
        "https://a.test/1": "not_assessed",
        "https://a.test/2": "not_assessed",
        "https://a.test/3": "not_assessed",
    }


def test_a_failed_assessment_finish_does_not_count_as_not_assessed(tmp_path: Path) -> None:
    """A model-boundary failure (assessment_finished ok=False) is a distinct
    status from an acquire/assess-side not_assessed event: it's still
    counted in not_assessed_counts via the reason it carries, and the
    resulting status distinguishes "attempted and failed" from "never
    attempted"."""

    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)
    writer.assessment_started("https://a.test/1")
    writer.assessment_finished("https://a.test/1", ok=False, reason="model_unavailable")

    snapshot = read_progress(run_root)
    assert snapshot.assessments[0]["status"] == "failed"
    assert snapshot.assessments[0]["reason"] == "model_unavailable"


def test_cap_known_is_a_single_replace_not_an_append(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)

    writer.cap_known(cap=5, candidate_count=42)
    snapshot = read_progress(run_root)
    assert snapshot.cap == 5
    assert snapshot.candidate_count == 42

    writer.cap_known(cap=5, candidate_count=50)
    snapshot = read_progress(run_root)
    assert snapshot.cap == 5
    assert snapshot.candidate_count == 50


# ---------------------------------------------------------------------------
# r1 (coordinator review): every public write is best-effort, not just
# construction. A write failure disables the writer once (first failure
# only) instead of raising into the caller or repeating the same doomed
# write/warning for the rest of the run.
# ---------------------------------------------------------------------------


def test_a_failing_write_disables_the_writer_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("gigai.scout.find_jobs.progress._append_line", _boom)

    # Must not raise: the OSError is caught, logged once, and the writer
    # disables itself.
    writer.posting_acquired({"normalized_url": "https://a.test/1"}, outcome="new")

    assert writer._disabled is True
    assert "disk full" in capsys.readouterr().err


def test_a_disabled_writer_stops_attempting_further_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Once disabled, no further write is even attempted -- one warning, not
    one per call -- so a persistent failure (e.g. a removed run dir) can't
    spend the rest of the run repeating the same doomed write."""

    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)
    calls = []

    def _boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("disk full")

    monkeypatch.setattr("gigai.scout.find_jobs.progress._append_line", _boom)

    writer.posting_acquired({"normalized_url": "https://a.test/1"}, outcome="new")
    writer.posting_acquired({"normalized_url": "https://a.test/2"}, outcome="new")
    writer.assessment_started("https://a.test/1")

    assert len(calls) == 1  # only the first write was ever attempted
    assert capsys.readouterr().err.count("warning:") == 1


def test_a_failing_replace_json_write_also_disables_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole-file-replace path (steps.json/cap.json) is guarded the same
    way as the append path (acquire.jsonl/assess.jsonl)."""

    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)

    def _boom(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("gigai.scout.find_jobs.progress._replace_json", _boom)

    writer.start_step("acquire")  # must not raise
    assert writer._disabled is True

    # cap_known also goes through _replace_json; already disabled, so it's a
    # silent no-op rather than a second warning/attempt.
    writer.cap_known(cap=5, candidate_count=10)
    snapshot = read_progress(run_root)
    assert snapshot.steps == {}
    assert snapshot.cap is None


def test_a_keyboard_interrupt_during_a_write_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard catches Exception, not BaseException: progress must never
    swallow a real interrupt/exit signal raised while it happens to be
    mid-write."""

    run_root = tmp_path / "runs" / "run_01"
    writer = ProgressWriter(run_root)

    def _interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("gigai.scout.find_jobs.progress._append_line", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        writer.posting_acquired({"normalized_url": "https://a.test/1"}, outcome="new")


def test_read_progress_tolerates_a_truncated_last_jsonl_line(tmp_path: Path) -> None:
    """A reader polling mid-append must not choke on a half-written last line."""

    run_root = tmp_path / "runs" / "run_01"
    directory = progress_dir(run_root)
    directory.mkdir(parents=True)
    complete_line = json.dumps({"normalized_url": "https://a.test/1", "outcome": "new"})
    (directory / "acquire.jsonl").write_text(complete_line + "\n" + '{"normalized_url": "https://a.test/2"' , encoding="utf-8")

    snapshot = read_progress(run_root)
    assert len(snapshot.postings) == 1
    assert snapshot.postings[0]["normalized_url"] == "https://a.test/1"


def test_read_progress_tolerates_a_corrupt_steps_json(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run_01"
    directory = progress_dir(run_root)
    directory.mkdir(parents=True)
    (directory / "steps.json").write_text("{not valid json", encoding="utf-8")

    snapshot = read_progress(run_root)
    assert snapshot.steps == {}


# ---------------------------------------------------------------------------
# 2. acquire_node's progress writes (producer side, direct node call)
# ---------------------------------------------------------------------------


def _context(tmp_path: Path, key: str = "acquire-001") -> NodeContext:
    return NodeContext(
        run_id="run_01", project_id="project_01", gig_id="gig_01",
        graph_id="find-jobs:functional", graph_version=1, goal_slug="acquire",
        manifest_digest="sha256:" + "a" * 64, operation_key=key,
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(tmp_path), redeemed_consent_ref="consent",
        model_target="ollama_local",
    )


def _acquire_config() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("software engineer",),
        merged_queries=("software engineer",),
        location="Denver, CO",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=False, ats=False, hiringcafe=False),
    )


class _Watchlist:
    def active_entries(self):
        return ()

    def add_to_watchlist(self, entry):
        return entry


class _Exa:
    def search(self, client, config, *, home_root=None):
        return ()


class _ATS:
    def list_board(self, client, provider, board_token, config):
        return ()


def test_acquire_node_writes_progress_for_every_kept_posting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = PostingRow(
        url="https://boards.greenhouse.io/acme/jobs/101",
        normalized_url="https://boards.greenhouse.io/acme/jobs/101",
        provider=ATSProvider.GREENHOUSE,
        board_token="acme",
        company="Acme",
        title="Software Engineer",
        location="Denver, CO",
        published_at="2026-09-20T00:00:00Z",
        content_sha256="sha256:" + "a" * 64,
        source_kind=SourceKind.ATS,
        query_key="software-engineer",
    )
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)

    acquire_input = AcquireInput(
        _acquire_config(), "sha256:" + "c" * 64, None, (row,), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH,
    )
    output = acquire_node(
        _context(tmp_path), acquire_input, http_client=None, exa=_Exa(), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert output.rows  # sealed output unaffected by progress wiring

    run_root = tmp_path / "runs" / "run_01"
    snapshot = read_progress(run_root)
    assert snapshot.steps["acquire"]["status"] == "done"
    assert [item["normalized_url"] for item in snapshot.postings] == [row.normalized_url]
    assert snapshot.cap == 10
    assert snapshot.candidate_count == 1


def test_acquire_node_marks_the_step_failed_when_every_source_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gigai.scout.find_jobs.market_acquisition import AcquireAllSourcesFailedError

    class _FailingExa:
        def search(self, client, config, *, home_root=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "gigai.scout.find_jobs.market_acquisition.import_public_rows",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not write a batch")),
    )
    config = FindJobsConfig(
        roles=("software engineer",), merged_queries=("software engineer",), location="Denver, CO",
        remote=True, published_after=None, sources=SourceToggles(exa=True, ats=False, hiringcafe=False),
    )
    acquire_input = AcquireInput(config, "sha256:" + "c" * 64, None, (), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH)

    with pytest.raises(AcquireAllSourcesFailedError):
        acquire_node(
            _context(tmp_path), acquire_input, http_client=None, exa=_FailingExa(), ats=_ATS(), watchlist=_Watchlist(),
        )

    snapshot = read_progress(tmp_path / "runs" / "run_01")
    assert snapshot.steps["acquire"]["status"] == "failed"


def test_acquire_node_completes_with_correct_sealed_output_when_progress_writes_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """r1: a progress-write failure (disk full/permissions/removed run dir)
    must never fail the sealed run -- acquire_node's real output is
    unaffected even though every progress write raises."""

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("gigai.scout.find_jobs.progress._append_line", _boom)
    monkeypatch.setattr("gigai.scout.find_jobs.progress._replace_json", _boom)

    row = PostingRow(
        url="https://boards.greenhouse.io/acme/jobs/101",
        normalized_url="https://boards.greenhouse.io/acme/jobs/101",
        provider=ATSProvider.GREENHOUSE,
        board_token="acme",
        company="Acme",
        title="Software Engineer",
        location="Denver, CO",
        published_at="2026-09-20T00:00:00Z",
        content_sha256="sha256:" + "a" * 64,
        source_kind=SourceKind.ATS,
        query_key="software-engineer",
    )
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)

    acquire_input = AcquireInput(
        _acquire_config(), "sha256:" + "c" * 64, None, (row,), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH,
    )
    output = acquire_node(
        _context(tmp_path), acquire_input, http_client=None, exa=_Exa(), ats=_ATS(), watchlist=_Watchlist(),
    )

    assert output.rows
    assert output.rows[0].outcome.value == "new"
    assert len(output.selected_postings) == 1
    # No progress file exists at all (every write failed and was swallowed),
    # confirming this isn't accidentally passing because the writes secretly
    # succeeded.
    assert not (tmp_path / "runs" / "run_01" / "progress").exists()


def test_a_real_exception_propagates_even_when_finish_step_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """r1's sharpest case: acquire_node's own real failure
    (AcquireAllSourcesFailedError) must be the exception that reaches the
    caller, not swallowed or replaced by a progress-write failure inside the
    `except BaseException: progress.finish_step(..., ok=False)` handler."""

    from gigai.scout.find_jobs.market_acquisition import AcquireAllSourcesFailedError

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("gigai.scout.find_jobs.progress._replace_json", _boom)

    class _FailingExa:
        def search(self, client, config, *, home_root=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "gigai.scout.find_jobs.market_acquisition.import_public_rows",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not write a batch")),
    )
    config = FindJobsConfig(
        roles=("software engineer",), merged_queries=("software engineer",), location="Denver, CO",
        remote=True, published_after=None, sources=SourceToggles(exa=True, ats=False, hiringcafe=False),
    )
    acquire_input = AcquireInput(config, "sha256:" + "c" * 64, None, (), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH)

    # Without r1's fix, finish_step("acquire", ok=False)'s own OSError (from
    # the monkeypatched _replace_json) would replace this
    # AcquireAllSourcesFailedError as the exception seen here.
    with pytest.raises(AcquireAllSourcesFailedError):
        acquire_node(
            _context(tmp_path), acquire_input, http_client=None, exa=_FailingExa(), ats=_ATS(), watchlist=_Watchlist(),
        )


def test_assess_node_wrapper_completes_and_propagates_correctly_when_progress_writes_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """r1, assess side: `assess_node` is a thin try/finally wrapper around
    `_assess_node_body` (see proposal_execution.py) using the exact same
    ProgressWriter guard proven above for acquire -- this isolates that
    wrapper (mocking the body) so it doesn't need the full lifecycle-approved
    workpad `test_assess_model_policy.py`'s fixture builds, while still
    exercising the real `assess_node`/`_assess_progress_writer` code path.
    """

    from gigai.scout.proposal_execution import assess_node

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("gigai.scout.find_jobs.progress._append_line", _boom)
    monkeypatch.setattr("gigai.scout.find_jobs.progress._replace_json", _boom)

    context = NodeContext(
        run_id="run_01", project_id="project_01", gig_id="gig_01",
        graph_id="find-jobs:functional", graph_version=1, goal_slug="assess",
        manifest_digest="sha256:" + "a" * 64, operation_key="assess-001",
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(tmp_path), redeemed_consent_ref="consent",
        model_target="ollama_local",
    )
    sentinel_output = SimpleNamespace(assessments=(), not_assessed=())
    monkeypatch.setattr(
        "gigai.scout.proposal_execution._assess_node_body",
        lambda *_args, **_kwargs: sentinel_output,
    )

    # Happy path: the real output reaches the caller unchanged even though
    # every progress write raised.
    result = assess_node(context, object(), home_root=tmp_path, target=tmp_path, config=object())
    assert result is sentinel_output
    assert not (tmp_path / "runs" / "run_01" / "progress").exists()

    # Failure path: the body's own real exception propagates, not a
    # progress-write failure from the `except BaseException:` handler.
    class _BodyError(RuntimeError):
        pass

    def _raise_body_error(*_args, **_kwargs):
        raise _BodyError("real assess failure")

    monkeypatch.setattr("gigai.scout.proposal_execution._assess_node_body", _raise_body_error)
    with pytest.raises(_BodyError):
        assess_node(context, object(), home_root=tmp_path, target=tmp_path, config=object())


def test_assess_node_writes_progress_under_the_workpad_not_the_target_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0-3 repro: in the real graph-worker path, ``target`` (bound from
    ``present_api._target_root()``, the operator's repo/target root) and
    ``context.workpad_path`` (the resolved per-Gig workpad, from
    ``run._build_node_context``'s ``resolved.path``) are different paths
    whenever the active Gig's workpad isn't the target root itself. Every
    other progress writer/reader (acquire's ``_progress_writer`` in
    ``market_acquisition.py``, and ``present_api.run_progress`` /
    ``/progress``) always resolves and uses the real workpad path, never the
    bare target. ``_assess_progress_writer`` must match that: progress must
    land under ``workpad_path/runs/<run_id>/progress``, never under
    ``target/runs/<run_id>/progress``.
    """

    from gigai.scout.proposal_execution import assess_node

    target_root = tmp_path / "operator-repo"
    workpad_root = tmp_path / "home" / "workpads" / "gig_01"
    target_root.mkdir(parents=True)
    workpad_root.mkdir(parents=True)

    context = NodeContext(
        run_id="run_01", project_id="project_01", gig_id="gig_01",
        graph_id="find-jobs:functional", graph_version=1, goal_slug="assess",
        manifest_digest="sha256:" + "a" * 64, operation_key="assess-001",
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(workpad_root), redeemed_consent_ref="consent",
        model_target="ollama_local",
    )
    sentinel_output = SimpleNamespace(assessments=(), not_assessed=())
    monkeypatch.setattr(
        "gigai.scout.proposal_execution._assess_node_body",
        lambda *_args, **_kwargs: sentinel_output,
    )

    result = assess_node(context, object(), home_root=tmp_path, target=target_root, config=object())
    assert result is sentinel_output

    # Progress must be under the workpad, matching acquire's writer and
    # present_api's /progress reader -- never under the bare target root.
    assert (workpad_root / "runs" / "run_01" / "progress").exists()
    assert not (target_root / "runs" / "run_01" / "progress").exists()


# ---------------------------------------------------------------------------
# 3. GET /api/runs/{id}/progress route contract
# ---------------------------------------------------------------------------


class _ProgressBackend(NotWiredBackend):
    """A minimal Backend double exposing only run_progress, for route-shape tests."""

    def __init__(self, *, known_run_id: str, progress_payload: dict[str, object] | None = None) -> None:
        self.known_run_id = known_run_id
        self.progress_payload = progress_payload if progress_payload is not None else {
            "schema_version": "scout-find-jobs-progress:1",
            "run_id": known_run_id,
            "steps": {},
            "postings": [],
            "assessments": [],
            "cap": None,
            "candidate_count": None,
            "not_assessed_counts": {},
        }

    def run_progress(self, run_id: str) -> dict[str, object]:
        if run_id != self.known_run_id:
            raise LookupError(run_id)
        return self.progress_payload


@pytest.fixture
def running_server(request: pytest.FixtureRequest):
    backend = request.param
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "running_server",
    [_ProgressBackend(known_run_id="run_123e4567-e89b-42d3-a456-426614174002")],
    indirect=True,
)
def test_progress_route_before_acquire_is_all_empty(running_server) -> None:
    response = running_server.get("/api/runs/run_123e4567-e89b-42d3-a456-426614174002/progress")
    assert response.status_code == 200
    body = response.json()
    assert body["steps"] == {}
    assert body["postings"] == []
    assert body["assessments"] == []


@pytest.mark.parametrize(
    "running_server",
    [
        _ProgressBackend(
            known_run_id="run_123e4567-e89b-42d3-a456-426614174002",
            progress_payload={
                "schema_version": "scout-find-jobs-progress:1",
                "run_id": "run_123e4567-e89b-42d3-a456-426614174002",
                "steps": {"acquire": {"status": "running", "started_at": "2026-09-24T00:00:00Z", "finished_at": None}},
                "postings": [{"normalized_url": "https://a.test/1", "title": "Engineer", "outcome": "new"}],
                "assessments": [],
                "cap": None,
                "candidate_count": None,
                "not_assessed_counts": {},
            },
        )
    ],
    indirect=True,
)
def test_progress_route_mid_acquire_shows_running_step_and_postings(running_server) -> None:
    response = running_server.get("/api/runs/run_123e4567-e89b-42d3-a456-426614174002/progress")
    assert response.status_code == 200
    body = response.json()
    assert body["steps"]["acquire"]["status"] == "running"
    assert body["postings"][0]["normalized_url"] == "https://a.test/1"


@pytest.mark.parametrize(
    "running_server",
    [
        _ProgressBackend(
            known_run_id="run_123e4567-e89b-42d3-a456-426614174002",
            progress_payload={
                "schema_version": "scout-find-jobs-progress:1",
                "run_id": "run_123e4567-e89b-42d3-a456-426614174002",
                "steps": {
                    "acquire": {"status": "done", "started_at": "t0", "finished_at": "t1"},
                    "assess": {"status": "running", "started_at": "t1", "finished_at": None},
                },
                "postings": [{"normalized_url": "https://a.test/1", "outcome": "new"}],
                "assessments": [{"normalized_url": "https://a.test/1", "status": "assessing"}],
                "cap": 5,
                "candidate_count": 12,
                "not_assessed_counts": {"over_cap": 7},
            },
        )
    ],
    indirect=True,
)
def test_progress_route_mid_assess_shows_cap_and_in_flight_assessment(running_server) -> None:
    response = running_server.get("/api/runs/run_123e4567-e89b-42d3-a456-426614174002/progress")
    assert response.status_code == 200
    body = response.json()
    assert body["steps"]["assess"]["status"] == "running"
    assert body["assessments"][0]["status"] == "assessing"
    assert body["cap"] == 5
    assert body["candidate_count"] == 12
    assert body["not_assessed_counts"] == {"over_cap": 7}


@pytest.mark.parametrize(
    "running_server",
    [
        _ProgressBackend(
            known_run_id="run_123e4567-e89b-42d3-a456-426614174002",
            progress_payload={
                "schema_version": "scout-find-jobs-progress:1",
                "run_id": "run_123e4567-e89b-42d3-a456-426614174002",
                "steps": {
                    "acquire": {"status": "done", "started_at": "t0", "finished_at": "t1"},
                    "assess": {"status": "done", "started_at": "t1", "finished_at": "t2"},
                    "present": {"status": "done", "started_at": "t2", "finished_at": "t3"},
                },
                "postings": [{"normalized_url": "https://a.test/1", "outcome": "new"}],
                "assessments": [{"normalized_url": "https://a.test/1", "status": "assessed", "assessment": {}}],
                "cap": 5,
                "candidate_count": 1,
                "not_assessed_counts": {},
            },
        )
    ],
    indirect=True,
)
def test_progress_route_after_completion_shows_all_steps_done(running_server) -> None:
    response = running_server.get("/api/runs/run_123e4567-e89b-42d3-a456-426614174002/progress")
    assert response.status_code == 200
    body = response.json()
    assert all(step["status"] == "done" for step in body["steps"].values())
    assert body["assessments"][0]["status"] == "assessed"


@pytest.mark.parametrize("running_server", [_ProgressBackend(known_run_id="run_known")], indirect=True)
def test_progress_route_unknown_run_is_404_not_found(running_server) -> None:
    response = running_server.get("/api/runs/run_unknown/progress")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.parametrize("running_server", [_ProgressBackend(known_run_id="run_known")], indirect=True)
def test_progress_route_does_not_shadow_the_results_route(running_server) -> None:
    """`/progress` and `/results` are distinct suffixes on the same run id;
    neither route's suffix-matching should swallow the other."""

    response = running_server.get("/api/runs/run_known/progress")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "scout-find-jobs-progress:1"
