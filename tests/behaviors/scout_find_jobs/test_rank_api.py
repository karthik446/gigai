"""Behavior tests for P6's rank route/service (``find_jobs/api/rank.py``).

uat-bug-021 (operator decision 2026-09-28): a page that reads a run never
asks Jev. ``compute_rank_scores`` (what ``GET /results`` calls) reads the
score cache only; ``rank_run`` is the pass that asks Jev, waited for; ``POST
/rank`` (``start_or_join_rank``) starts that pass in the background, once,
and answers at once, and only when the request says ``start``. The tests
that read "compute_rank_scores scores the postings" call ``rank_run`` now.

Unit-level: the three are exercised directly against a real
gig (``build_gig_with_resume``, F1-c's own fixture, so ``selected_profile``
resolves a real committed resume) with the run's sealed acquire output
faked via a monkeypatched ``read_committed_artifact`` -- writing a real
journal-committed run is the api-e2e journey's job
(``tests/api_e2e/test_rank_journey.py``), not this focused unit suite.
HTTP-level request validation (missing run_id mismatch, bad cost_cap_usd)
is exercised directly against ``RankRoutesMixin`` with a minimal fake
handler double, mirroring how ``server.py``'s own mixins are unit-tested.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gigai.canonical import digest_imported_bytes
from gigai.scout.find_jobs import jev_rank
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
from gigai.scout.find_jobs.jev_client import JEV_API_KEY_ENV_VAR
from gigai.scout.find_jobs.jev_contracts import RankRequest

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path)


@pytest.fixture(autouse=True)
def _isolate_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jev_rank, "project_id", lambda home_root, target: "proj-test")


def _row(n: int) -> PostingRow:
    return PostingRow(
        url=f"https://boards.greenhouse.io/acme/jobs/{n}",
        normalized_url=f"https://boards.greenhouse.io/acme/jobs/{n}",
        provider=ATSProvider.GREENHOUSE,
        board_token="acme",
        company="Acme",
        title=f"SWE {n}",
        location="Remote",
        published_at=None,
        content_sha256=digest_imported_bytes(f"posting-{n}".encode()),
        source_kind=SourceKind.ATS,
        query_key="q",
        text=f"posting body {n}",
    )


def _fake_acquire_output(rows: tuple[PostingRow, ...]) -> AcquireOutput:
    return AcquireOutput(
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


def _patch_acquire_read(monkeypatch: pytest.MonkeyPatch, rows: tuple[PostingRow, ...]) -> None:
    output = _fake_acquire_output(rows)

    def fake_read(*, workpad, project_id, gig_id, path, **kwargs):
        assert path.endswith("/outputs/acquire.json")
        return json.dumps(output.to_json()).encode("utf-8"), "fake-commit"

    monkeypatch.setattr(rank_api, "read_committed_artifact", fake_read)


def _good_jev_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "jev-1.13.0",
            "answers": {
                "fit": {"choice": "strong"},
                "score": {"score": 8},
                "top_reason": {"choice": "stack_match"},
                "flag_domain": {"noul": 0.0},
                "flag_seniority": {"noul": 0.0},
                "flag_stack": {"noul": 0.0},
                "flag_location": {"noul": 0.0},
                "flag_sponsorship": {"noul": 0.0},
            },
            "usage": {"cost_usd": 0.0005},
        },
        request=request,
    )


def test_no_key_returns_empty_scores(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.delenv(JEV_API_KEY_ENV_VAR, raising=False)
    _patch_acquire_read(monkeypatch, (_row(1), _row(2)))

    response = rank_api.compute_rank_scores(
        home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None,
    )
    assert response.scores == ()
    assert response.total_cost_usd == "0"
    assert response.capped is False
    assert response.unscored == 0


def test_run_with_no_acquire_output_returns_empty(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")

    from gigai.journal import JournalArtifactMissingError

    def fake_read(*, workpad, project_id, gig_id, path, **kwargs):
        raise JournalArtifactMissingError("missing")

    monkeypatch.setattr(rank_api, "read_committed_artifact", fake_read)

    response = rank_api.compute_rank_scores(
        home_root=fx.home_root, target=fx.target, run_id="run_missing", profile_id=None,
    )
    assert response.scores == ()


def test_scores_postings_against_selected_profile(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    _patch_acquire_read(monkeypatch, (_row(1), _row(2)))
    monkeypatch.setattr(rank_api, "_jev_http_client", lambda: httpx.Client(transport=httpx.MockTransport(_good_jev_handler)))

    response, status = rank_api.rank_run(
        home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None,
    )
    assert len(response.scores) == 2
    assert all(item.fit == "strong" for item in response.scores)
    assert response.unscored == 0
    assert response.capped is False
    assert status.text == "scored 2 of 2" and status.line == "Jev: scored 2 of 2 (cost $0.001)"


def test_second_call_hits_cache_and_costs_nothing(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    _patch_acquire_read(monkeypatch, (_row(1),))
    calls: list[str] = []

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _good_jev_handler(request)

    monkeypatch.setattr(rank_api, "_jev_http_client", lambda: httpx.Client(transport=httpx.MockTransport(counting_handler)))

    rank_api.rank_run(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)
    assert len(calls) == 1

    _response, status = rank_api.rank_run(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)
    assert len(calls) == 1  # cache hit, no new call
    assert status.line == "Jev: scored 1 of 1 (cost $0.00)"


def test_unknown_profile_id_returns_empty(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    _patch_acquire_read(monkeypatch, (_row(1),))

    response = rank_api.compute_rank_scores(
        home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id="does-not-exist",
    )
    assert response.scores == ()


def test_cost_cap_override_is_honored(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    _patch_acquire_read(monkeypatch, (_row(1), _row(2), _row(3)))

    def expensive_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "fit": {"choice": "strong"}, "score": {"score": 8}, "top_reason": {"choice": "stack_match"},
                    "flag_domain": {"noul": 0.0}, "flag_seniority": {"noul": 0.0}, "flag_stack": {"noul": 0.0},
                    "flag_location": {"noul": 0.0}, "flag_sponsorship": {"noul": 0.0},
                },
                "usage": {"cost_usd": 0.10},
            },
            request=request,
        )

    monkeypatch.setattr(rank_api, "_jev_http_client", lambda: httpx.Client(transport=httpx.MockTransport(expensive_handler)))

    # One posting at a time, so the cap holds after the second $0.10 call.
    monkeypatch.setattr(rank_api, "RUN_CONCURRENCY", 1)
    response, status = rank_api.rank_run(
        home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None, cost_cap_usd=0.15,
    )
    assert len(response.scores) == 3
    assert response.capped is True
    assert response.unscored == 1
    assert status.line == "Jev: scored 2 of 3 (cost $0.20, cost cap $0.15)"


# ---------------------------------------------------------------------------
# uat-bug-021: a page never spends; POST /rank is one pass, in the background
# ---------------------------------------------------------------------------


class _CountingJev:
    """A fake Jev that counts its calls and can be held until released."""

    def __init__(self, *, hold: bool = False) -> None:
        import threading

        self.calls: list[str] = []
        self.release = threading.Event()
        self.started = threading.Event()
        if not hold:
            self.release.set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(json.loads(request.content)["state"]["posting"]["title"])
        self.started.set()
        assert self.release.wait(30), "the test never released the fake Jev"
        return _good_jev_handler(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _poll_rank(fx: ProfileFixtureGig, *, run_id: str = "run_1"):
    import time

    deadline = time.monotonic() + 30
    while True:
        response, status = rank_api.start_or_join_rank(home_root=fx.home_root, target=fx.target, run_id=run_id, profile_id=None)
        if status.status != "running":
            return response, status
        assert time.monotonic() < deadline, "the ranking pass never ended"
        time.sleep(0.02)


def test_reading_a_runs_scores_never_asks_jev(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    _patch_acquire_read(monkeypatch, (_row(1), _row(2)))
    jev = _CountingJev()
    monkeypatch.setattr(rank_api, "_jev_http_client", jev.client)

    before = rank_api.compute_rank_scores(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)

    assert jev.calls == []
    assert len(before.scores) == 2 and before.unscored == 2 and before.total_cost_usd == "0.000000"

    rank_api.rank_run(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)
    after = rank_api.compute_rank_scores(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)

    assert len(jev.calls) == 2
    assert [(item.score, item.fit, item.cached) for item in after.scores] == [(89, "strong", True)] * 2
    assert after.unscored == 0 and after.total_cost_usd == "0.000000"


def test_post_rank_answers_at_once_and_a_second_request_joins_the_running_pass(
    monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig
) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    _patch_acquire_read(monkeypatch, (_row(1), _row(2), _row(3)))
    jev = _CountingJev(hold=True)
    monkeypatch.setattr(rank_api, "_jev_http_client", jev.client)

    # A page that opens the run asks for nothing: no pass, no call.
    read, read_status = rank_api.start_or_join_rank(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)
    assert read_status.status == "skipped" and read_status.reason == "not_requested"
    assert read_status.line == "Jev: skipped (not asked yet)" and read.unscored == 3
    assert rank_api.wait_for_rank(timeout=1) and jev.calls == []

    try:
        first, first_status = rank_api.start_or_join_rank(
            home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None, start=True
        )
        # Answered while Jev has not answered anything yet.
        assert first_status.status == "running" and first_status.text == "scoring: 0 of 3"
        assert first_status.line == "Jev: scoring, 0 of 3 so far"
        assert len(first.scores) == 3 and first.unscored == 3
        assert jev.started.wait(30)

        # A second click and a page that reads: both join the running pass.
        second, second_status = rank_api.start_or_join_rank(
            home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None, start=True
        )
        third, third_status = rank_api.start_or_join_rank(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)
        assert second_status.status == third_status.status == "running"
        assert second.unscored == third.unscored == 3
    finally:
        jev.release.set()
    assert rank_api.wait_for_rank(timeout=30)

    # One pass: each posting was asked about once, and paid for once.
    assert sorted(jev.calls) == ["SWE 1", "SWE 2", "SWE 3"]
    from gigai.scout.find_jobs import jev_budget

    assert jev_budget.spent_today_usd(fx.home_root) == pytest.approx(3 * 0.0005)

    # A read answers the pass's own result, as often as it is asked ...
    for _ in range(2):
        done, done_status = rank_api.start_or_join_rank(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)
        assert done_status.status == "scored" and done_status.line == "Jev: scored 3 of 3 (cost $0.0015)"
        assert done.total_cost_usd == "0.001500" and done.unscored == 0
    # ... and another click finds everything in the cache: no pass, no call.
    again, again_status = rank_api.start_or_join_rank(
        home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None, start=True
    )
    assert again_status.line == "Jev: scored 3 of 3 (cost $0.00)" and all(item.cached for item in again.scores)
    assert rank_api.wait_for_rank(timeout=1) and len(jev.calls) == 3


def test_the_rank_route_answers_the_status_and_the_days_usage(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    monkeypatch.delenv("GIGAI_JEV_DAILY_BUDGET_USD", raising=False)
    _patch_acquire_read(monkeypatch, (_row(1), _row(2)))
    jev = _CountingJev()
    monkeypatch.setattr(rank_api, "_jev_http_client", jev.client)
    # What a page sends when it opens a run: answered, nothing started.
    reading = _FakeHandler({}, home_root=fx.home_root, target=fx.target)
    reading._handle_post_rank("run_1")
    assert reading.written is not None and reading.written[1]["rank_status"]["reason"] == "not_requested"
    assert rank_api.wait_for_rank(timeout=1) and jev.calls == []
    refused = _FakeHandler({"start": "yes"}, home_root=fx.home_root, target=fx.target)
    refused._handle_post_rank("run_1")
    assert refused.errored is not None and refused.errored[:2] == (422, "wrong_type")

    handler = _FakeHandler({"start": True}, home_root=fx.home_root, target=fx.target)

    handler._handle_post_rank("run_1")

    assert handler.errored is None and handler.written is not None
    status, payload = handler.written
    assert status == 200 and payload["schema_version"] == "scout-jev-rank-response:1"
    assert payload["rank_status"]["status"] == "running" and len(payload["scores"]) == 2
    assert set(payload["usage"]) == {"day", "spent_today_usd", "daily_budget_usd", "remaining_usd", "budget_reached", "line"}

    _response, final = _poll_rank(fx)
    assert final.text == "scored 2 of 2"
    usage_handler = _UsageHandler(home_root=fx.home_root)
    usage_handler._handle_get_jev_usage()
    assert usage_handler.written is not None and usage_handler.written[0] == 200
    assert usage_handler.written[1]["line"] == "Jev: $0.001 of $0.50 today"
    assert usage_handler.written[1]["spent_today_usd"] == "0.001000" and usage_handler.written[1]["budget_reached"] is False


def test_the_daily_budget_holds_for_the_rank_route_too(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    monkeypatch.setenv("GIGAI_JEV_DAILY_BUDGET_USD", "0.001")
    _patch_acquire_read(monkeypatch, tuple(_row(n) for n in range(1, 21)))
    jev = _CountingJev()
    monkeypatch.setattr(rank_api, "_jev_http_client", jev.client)

    _response, status = rank_api.rank_run(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)

    # 8 calls at $0.0005 pass $0.001; the 12 others are not asked about.
    assert len(jev.calls) == 8
    assert status.line == "Jev: scored 8 of 20 (cost $0.004, daily budget $0.001 reached)"
    _again, again = rank_api.rank_run(home_root=fx.home_root, target=fx.target, run_id="run_2", profile_id=None)
    assert len(jev.calls) == 8 and again.line == "Jev: scored 8 of 20 (cost $0.00, daily budget $0.001 reached)"


# ---------------------------------------------------------------------------
# HTTP-level request validation (RankRoutesMixin)
# ---------------------------------------------------------------------------


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


class _UsageHandler(rank_api.JevUsageRoutesMixin):
    def __init__(self, *, home_root: Path) -> None:
        self.written: tuple[int, dict[str, object]] | None = None

        class _Backend:
            pass

        backend = _Backend()
        backend.home_root = home_root  # type: ignore[attr-defined]
        self._backend = backend

    def _write_json(self, status: int, payload: dict[str, object]) -> None:
        self.written = (status, payload)


def test_run_id_mismatch_between_url_and_body_is_rejected(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    handler = _FakeHandler({"run_id": "run_other"}, home_root=fx.home_root, target=fx.target)
    handler._handle_post_rank("run_1")
    assert handler.errored is not None
    assert handler.errored[0] == 422
    assert handler.errored[1] == "invalid_value"


def test_invalid_cost_cap_usd_is_rejected(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    handler = _FakeHandler({"cost_cap_usd": "not-a-number"}, home_root=fx.home_root, target=fx.target)
    handler._handle_post_rank("run_1")
    assert handler.errored is not None
    assert handler.errored[0] == 422
    assert handler.errored[1] == "invalid_value"


def test_missing_run_id_in_body_defaults_to_url_run_id(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.delenv(JEV_API_KEY_ENV_VAR, raising=False)
    _patch_acquire_read(monkeypatch, ())
    handler = _FakeHandler({}, home_root=fx.home_root, target=fx.target)
    handler._handle_post_rank("run_1")
    assert handler.errored is None
    assert handler.written is not None
    status, payload = handler.written
    assert status == 200
    assert payload["run_id"] == "run_1"
    assert payload["scores"] == []


def test_rank_request_round_trips() -> None:
    request = RankRequest(run_id="run_1", profile_id="p1", cost_cap_usd="0.50")
    assert RankRequest.from_json(request.to_json()) == request
