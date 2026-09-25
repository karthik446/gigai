"""Behavior tests for P6's rank route/service (``find_jobs/api/rank.py``).

Unit-level: ``compute_rank_scores`` is exercised directly against a real
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

    response = rank_api.compute_rank_scores(
        home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None,
    )
    assert len(response.scores) == 2
    assert all(item.fit == "strong" for item in response.scores)
    assert response.unscored == 0
    assert response.capped is False


def test_second_call_hits_cache_and_costs_nothing(monkeypatch: pytest.MonkeyPatch, fx: ProfileFixtureGig) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_live_x")
    _patch_acquire_read(monkeypatch, (_row(1),))
    calls: list[str] = []

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _good_jev_handler(request)

    monkeypatch.setattr(rank_api, "_jev_http_client", lambda: httpx.Client(transport=httpx.MockTransport(counting_handler)))

    rank_api.compute_rank_scores(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)
    assert len(calls) == 1

    rank_api.compute_rank_scores(home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None)
    assert len(calls) == 1  # cache hit, no new call


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

    response = rank_api.compute_rank_scores(
        home_root=fx.home_root, target=fx.target, run_id="run_1", profile_id=None, cost_cap_usd=0.15,
    )
    assert len(response.scores) == 3
    assert response.capped is True
    assert response.unscored == 1


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
