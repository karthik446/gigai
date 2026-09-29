"""SCOPE-ADD-3 C1: ``POST /api/runs/{run_id}/rank`` -- a re-rank is a durable ``rank`` run record.

Replaces P6's Jev pass on a daemon thread in a process-local dict. Pinned:

* ``{"start": true}`` makes a record under ``runs/rank_<uuid>/`` whose
  ``run-details.json`` and ``outputs/rank.json`` are committed to the
  journal, and answers at once; ``{}`` reads and never starts a pass;
* single flight per (run, profile, resume revision, prefs digest, prompt
  version): a second start while the pass runs joins it;
* durable across a restart: a record left ``running`` by a process that is
  gone reads ``interrupted``; the next start resumes the SAME record and the
  rows it had scored come from the score cache (never asked again);
* cancellable: ``{"cancel": true}`` stops the pass; the batches that landed
  are kept and the record finishes ``cancelled``;
* a ``complete`` record answers later starts; no Jev call anywhere.

Unit-level against a real gig (``build_gig_with_resume``: a committed resume
and a selected profile) with the parent run's committed acquire output
faked via ``read_committed_artifact`` and a scripted model port behind
``model_rank._resolve``. HTTP validation runs against ``RankRoutesMixin``
with a minimal handler double.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
import subprocess
import sys

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.journal import read_committed_artifact
from gigai.scout.find_jobs import jev_client, jev_rank, model_rank, rank_records, rank_run
from gigai.scout.find_jobs.api import rank as rank_api
from gigai.scout.find_jobs.contracts import (
    ATSProvider,
    AcquireOutput,
    PostingRow,
    PostingRowResult,
    ProgressStatus,
    RowOutcome,
    SourceKind,
    URLSetDiff,
)
from gigai.scout.find_jobs.jev_contracts import RankRequest
from gigai.scout.find_jobs.progress import read_progress
from tests.support.rank_fakes import RankBinding, RankPort
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume, default_find_jobs_config

RUN_ID = "run_1"


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProfileFixtureGig]:
    gig = build_gig_with_resume(tmp_path)
    monkeypatch.setenv(jev_client.JEV_API_KEY_ENV_VAR, "jv_test_key_never_used")

    def refuse(*_args, **_kwargs):
        raise AssertionError("/rank asked Jev")

    monkeypatch.setattr(jev_client.JevClient, "__init__", refuse)
    monkeypatch.setattr(jev_rank, "rank_postings_report", refuse)
    monkeypatch.setattr(rank_api, "_jev_http_client", refuse)
    yield gig
    rank_api.wait_for_rank(timeout=30)


def _row(n: int) -> PostingRow:
    return PostingRow(
        url=f"https://boards.greenhouse.io/acme/jobs/{n}",
        normalized_url=f"https://boards.greenhouse.io/acme/jobs/{n}",
        provider=ATSProvider.GREENHOUSE,
        board_token="acme",
        company=f"Company {n}",
        title="Staff AI Engineer",
        location="Remote",
        published_at=None,
        content_sha256=digest_imported_bytes(f"posting-{n}".encode()),
        source_kind=SourceKind.ATS,
        query_key="q",
        text=f"Requirements\n- Python and AWS for posting {n}.\n",
    )


def _company(line: str) -> int:
    return int(line.split(" @ Company ", 1)[1].split(" |", 1)[0])


def _parent(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch, count: int) -> tuple[PostingRow, ...]:
    """A parent run: its committed acquire output (faked) and its sealed run input (on disk)."""

    rows = tuple(_row(n) for n in range(count))
    output = AcquireOutput(
        batch_id="batch-1",
        batch_ref="records/scout-acquisition/batch-1/input.json",
        progress_ref="records/scout-acquisition/batch-1/progress/0001.json",
        progress_status=ProgressStatus.COMPLETE,
        rows=tuple(PostingRowResult(row, RowOutcome.NEW) for row in rows),
        failures=(),
        url_set_diff=URLSetDiff((), (), (), ()),
        watchlist_refs=(),
        selected_postings=(),
    )

    def fake_read(*, workpad, project_id, gig_id, path, **kwargs):
        assert path == f"runs/{RUN_ID}/outputs/acquire.json"
        return json.dumps(output.to_json()).encode("utf-8"), "fake-commit"

    monkeypatch.setattr(rank_api, "read_committed_artifact", fake_read)
    config = default_find_jobs_config()
    sealed = fx.resolved.path / "runs" / RUN_ID / "sealed"
    sealed.mkdir(parents=True, exist_ok=True)
    (sealed / "find-jobs-run-input.json").write_text(json.dumps({
        "schema_version": "scout-find-jobs-run-input:1",
        "config": config.to_json(),
        "config_digest": config.digest(),
        "selection_cap": 5,
        "selection_rule": "new_or_edited_role_match",
        "model_target": "codex_cli",
        "pinned_resume": {"record_id": fx.resume_record_id, "revision_id": fx.resume_revision_id,
                          "content_sha256": "sha256:" + "d" * 64},
    }))
    return rows


def _install(monkeypatch: pytest.MonkeyPatch, port: RankPort) -> None:
    def resolve(config, adapter_kind, *, home_root):
        return model_rank._ResolvedPort(RankBinding(port), port, "codex-default", "default", "low")

    monkeypatch.setattr(model_rank, "_resolve", resolve)


def _post(fx: ProfileFixtureGig, **flags: bool) -> dict:
    return rank_api.rank_request(home_root=fx.home_root, target=fx.target, run_id=RUN_ID, profile_id=None, **flags)


def _committed(fx: ProfileFixtureGig, path: str) -> dict:
    resolved = fx.resolved
    raw, _commit = read_committed_artifact(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, path=path,
        allow_replaced_run_details=True,
    )
    return json.loads(raw)


# --- a read never starts a pass ------------------------------------------------------------


def test_a_read_starts_nothing_and_says_not_requested(fx, monkeypatch: pytest.MonkeyPatch) -> None:
    _parent(fx, monkeypatch, 3)
    port = RankPort(lambda _line: (80, []))
    _install(monkeypatch, port)

    body = _post(fx)

    assert port.prompts == [] and rank_records.list_records(fx.resolved.path) == []
    assert body["schema_version"] == "scout-jev-rank-response:1" and body["run_id"] == RUN_ID
    assert [item["score"] for item in body["scores"]] == [None, None, None]
    assert body["rank_status"]["status"] == "skipped" and body["rank_status"]["reason"] == "not_requested"
    assert body["rank_record"] is None and body["total_cost_usd"] == "0"


def test_a_run_that_cannot_be_read_is_a_skip(fx, monkeypatch: pytest.MonkeyPatch) -> None:
    body = _post(fx, start=True)  # nothing committed for run_1

    assert body["scores"] == [] and body["rank_status"]["reason"] == "no_run_output"
    assert rank_records.list_records(fx.resolved.path) == []


# --- a start makes a durable record ----------------------------------------------------------


def test_a_start_makes_a_committed_rank_record_and_the_reads_use_it(fx, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _parent(fx, monkeypatch, 3)
    port = RankPort(lambda line: (60 + _company(line), ["onsite"] if _company(line) == 2 else []))
    _install(monkeypatch, port)

    started = _post(fx, start=True)
    assert started["rank_record"]["record_id"].startswith("rank_")
    assert rank_api.wait_for_rank(timeout=30)

    [record] = rank_records.list_records(fx.resolved.path, parent_run_id=RUN_ID)
    details = _committed(fx, f"runs/{record.record_id}/run-details.json")
    assert details["kind"] == "rank" and details["parent_run_id"] == RUN_ID and details["status"] == "complete"
    assert details["key"]["profile_id"] and details["key"]["resume_revision_id"] == fx.resume_revision_id
    assert details["key"]["prompt_version"] == "rank-v1" and details["key_digest"].startswith("sha256:")
    assert details["input"]["postings"] == 3 and details["input"]["acquire_output_digest"].startswith("sha256:")
    assert details["input"]["model_target"] == "codex_cli"
    sealed = _committed(fx, f"runs/{record.record_id}/outputs/rank.json")
    assert sealed["kind"] == "rank" and sealed["parent_run_id"] == RUN_ID and sealed["status"] == "complete"
    # The fake port is no Codex adapter: the effort is asked for and reported as not applied.
    assert (sealed["effort"], sealed["effort_applied"]) == ("low", "provider-default")
    snapshot = read_progress(record.root)
    assert snapshot.rank is not None and snapshot.rank["status"] == "complete" and snapshot.rank["ranked"] == 3

    read = _post(fx)
    assert {item["normalized_url"]: item["score"] for item in read["scores"]} == {
        row.normalized_url: 60 + n for n, row in enumerate(rows)
    }
    assert read["scores"][2]["mismatch_flags"] == ["onsite"]
    assert read["rank_status"]["status"] == "scored" and read["rank_record"]["status"] == "complete"
    assert rank_api.newest_rank_result(
        fx.resolved, RUN_ID, profile_id=details["key"]["profile_id"], resume_revision_id=fx.resume_revision_id
    ) is not None

    # A complete record answers the next start; no new pass, no new record.
    again = _post(fx, start=True)
    rank_api.wait_for_rank(timeout=30)
    assert again["rank_record"]["record_id"] == record.record_id and len(port.prompts) == 1
    assert len(rank_records.list_records(fx.resolved.path)) == 1


def test_a_second_start_joins_the_running_pass(fx, monkeypatch: pytest.MonkeyPatch) -> None:
    _parent(fx, monkeypatch, 60)
    port = RankPort(lambda _line: (70, []), hold_after_first=True)
    _install(monkeypatch, port)
    monkeypatch.setattr(rank_run, "run_concurrency", lambda cpus=None: 1)
    try:
        first = _post(fx, start=True)
        assert port.first_answered.wait(30)
        second = _post(fx, start=True)
        mid = _post(fx)
    finally:
        port.gate.set()
    rank_api.wait_for_rank(timeout=30)

    assert first["rank_status"]["status"] == "running"
    assert second["rank_record"]["record_id"] == first["rank_record"]["record_id"]
    assert second["rank_status"]["status"] == "running"
    assert mid["rank_record"]["status"] == "running" and mid["rank_status"]["status"] == "running"
    assert len(rank_records.list_records(fx.resolved.path)) == 1
    assert len(port.prompts) == 2  # b000 and b001, once each


def test_a_record_left_running_by_a_gone_process_is_resumed_in_place(fx, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _parent(fx, monkeypatch, 80)
    port = RankPort(lambda line: (50 + _company(line) % 40, []))
    _install(monkeypatch, port)
    found = rank_api._resolve(home_root=fx.home_root, target=fx.target, run_id=RUN_ID, profile_id=None)
    assert isinstance(found, rank_api._Resolved)
    source = found.source

    # The first process scored the first 50 rows (the score cache holds them),
    # then died with the record committed as running.
    model_rank.rank_postings(
        source.rows[:50], resume_text=source.resume_text, prefs=source.prefs, model_target="codex_cli",
        home_root=fx.home_root,
    )
    record_id = "rank_00000000-0000-4000-8000-00000000dead"
    details = rank_records._details(record_id, source, status="running", started_at="2026-09-28T00:00:00Z")
    rank_records._commit_details(source.workpad_resolved, record_id, details, operation="scout_rank_started")
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    progress = fx.resolved.path / "runs" / record_id / "progress"
    progress.mkdir(parents=True)
    (progress / "owner.json").write_text(json.dumps({"pid": gone.pid, "token": "an-earlier-process"}))
    port.prompts.clear()

    read = _post(fx)
    assert read["rank_record"]["record_id"] == record_id and read["rank_record"]["status"] == "interrupted"
    assert port.prompts == []

    resumed = _post(fx, start=True)
    assert rank_api.wait_for_rank(timeout=30)

    assert resumed["rank_record"]["record_id"] == record_id
    assert len(rank_records.list_records(fx.resolved.path)) == 1
    assert len(port.asked) == 30  # only the rows the first process had not scored
    final = _committed(fx, f"runs/{record_id}/run-details.json")
    assert final["status"] == "complete" and final["totals"]["cached"] == 50
    assert len([item for item in _post(fx)["scores"] if item["score"] is not None]) == len(rows)


def test_a_cancel_stops_the_pass_and_keeps_the_batches_that_landed(fx, monkeypatch: pytest.MonkeyPatch) -> None:
    _parent(fx, monkeypatch, 120)
    port = RankPort(lambda _line: (75, []), hold_after_first=True)
    _install(monkeypatch, port)
    monkeypatch.setattr(rank_run, "run_concurrency", lambda cpus=None: 1)
    try:
        started = _post(fx, start=True)
        assert port.first_answered.wait(30)
        cancelled = _post(fx, cancel=True)
    finally:
        port.gate.set()
    assert rank_api.wait_for_rank(timeout=30)

    record_id = started["rank_record"]["record_id"]
    assert cancelled["rank_record"]["record_id"] == record_id
    details = _committed(fx, f"runs/{record_id}/run-details.json")
    assert details["status"] == "cancelled"
    sealed = _committed(fx, f"runs/{record_id}/outputs/rank.json")
    scored = [item for item in sealed["postings"] if item["score"] is not None]
    assert len(port.prompts) == 2  # b000, and b001 already running when the cancel came; b002 never asked
    assert len(scored) == 100 and sealed["totals"]["unscored"] == 20
    read = _post(fx)
    assert read["rank_record"]["status"] == "cancelled" and read["rank_status"]["status"] == "scored"

    # After a cancelled record a start makes a NEW record, cache-first.
    port.prompts.clear()
    port.hold_after_first = False
    _post(fx, start=True)
    assert rank_api.wait_for_rank(timeout=30)
    assert len(rank_records.list_records(fx.resolved.path)) == 2
    assert len(port.asked) == 20


# --- HTTP-level request validation (RankRoutesMixin) ----------------------------------------


class _FakeHandler(rank_api.RankRoutesMixin):
    """Minimal double exercising only what ``_handle_post_rank`` needs."""

    def __init__(self, body: object, *, home_root: Path, target: Path) -> None:
        self._body = body
        self.written: tuple[int, dict[str, object]] | None = None
        self.errored: tuple[int, str, str] | None = None

        class _Backend:
            pass

        backend = _Backend()
        backend.home_root = home_root  # type: ignore[attr-defined]
        backend.target = target  # type: ignore[attr-defined]
        self._backend = backend

    def _read_json_body(self) -> object:
        return self._body

    def _error(self, status: int, code: str, message: str) -> None:
        self.errored = (status, code, message)

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        self.written = (status, payload)


def test_run_id_mismatch_between_url_and_body_is_rejected(fx) -> None:
    handler = _FakeHandler({"run_id": "run_other"}, home_root=fx.home_root, target=fx.target)
    handler._handle_post_rank(RUN_ID)
    assert handler.errored is not None and handler.errored[:2] == (422, "invalid_value")


@pytest.mark.parametrize("body", [{"start": "yes"}, {"cancel": 1}])
def test_start_and_cancel_must_be_booleans(fx, body: dict) -> None:
    handler = _FakeHandler(body, home_root=fx.home_root, target=fx.target)
    handler._handle_post_rank(RUN_ID)
    assert handler.errored is not None and handler.errored[:2] == (422, "wrong_type")


def test_missing_run_id_in_body_defaults_to_url_run_id(fx, monkeypatch: pytest.MonkeyPatch) -> None:
    _parent(fx, monkeypatch, 0)
    handler = _FakeHandler({}, home_root=fx.home_root, target=fx.target)
    handler._handle_post_rank(RUN_ID)
    assert handler.errored is None and handler.written is not None
    status, payload = handler.written
    assert status == 200 and payload["run_id"] == RUN_ID and payload["scores"] == []


def test_rank_request_round_trips() -> None:
    request = RankRequest(run_id="run_1", profile_id="p1", cost_cap_usd="0.50")
    assert RankRequest.from_json(request.to_json()) == request
