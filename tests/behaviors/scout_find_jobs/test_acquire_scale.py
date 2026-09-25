"""Q2: acquire at scale -- the ATS board fetch pass of ``market_acquisition``.

Direct ``acquire_node`` calls (the same ``NodeContext``/``AcquireInput``
construction ``test_acquire_network.py`` uses) with fakes only:

* bounded concurrency per provider (``AcquireLimits.concurrency_per_provider``)
* the per-provider request pacer (``min_request_interval_seconds``)
* the per-board cache: a second run refetches nothing it already has
  (``304`` on the list, ``updated_at`` marker on Greenhouse details)
* the title prefilter: no detail request for a non-matching title
* the run-time budget: a clean stop, skipped boards recorded per board and
  as one ``time_budget_exceeded`` failure row, user-added boards first
* progress per board (``boards.jsonl`` / ``boards.json``)
* watchlist seeding inside acquire: explicit, idempotent, and skipped
  (recorded) when no prefs are saved yet.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients, BoardCache, BoardFetchResult, BoardFetchStats
from gigai.scout.find_jobs.company_catalog import load_company_catalog
from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    ATSProvider,
    FindJobsConfig,
    NodeContext,
    PostingRow,
    SelectionRule,
    SourceKind,
    SourceToggles,
    WatchlistEntry,
    WatchlistFirstSeen,
)
from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs, save_prefs
from gigai.scout.find_jobs.market_acquisition import (
    BUDGET_EXCEEDED_CODE,
    AcquireAllSourcesFailedError,
    AcquireLimits,
    acquire_node,
)
from gigai.scout.find_jobs.progress import read_progress
from gigai.scout.find_jobs.watchlist import JournalWatchlistClient, list_active
from gigai.workpad import resolve_workpad

from tests.behaviors.scout_research.test_scout06_research_inputs import _fixture
from tests.support.workpad_assertions import assert_managed_workpad_clean


FIXTURES = Path(__file__).parent / "fixtures"


def _context(workpad: Path, key: str = "acquire-scale-1", run_id: str = "run_01") -> NodeContext:
    return NodeContext(
        run_id=run_id, project_id="project_01", gig_id="gig_01",
        graph_id="find-jobs:functional", graph_version=1, goal_slug="acquire",
        manifest_digest="sha256:" + "a" * 64, operation_key=key,
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(workpad), redeemed_consent_ref="consent",
        model_target="ollama_local",
    )


def _managed(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    """(home, requested target, approved Scout workpad, gig id) -- a live run's substrate.

    ``test_scout06_research_inputs._fixture`` (the same one ``test_watchlist.py``
    uses): an approved, v2-layout workpad, so journal ``records/`` are a
    legal top-level entry (``create_offline`` alone yields a v1 layout that
    ``resolve_workpad`` refuses once seeding has written records/). Contexts
    built against it must carry the workpad's REAL gig id (``_real_context``):
    ``acquire_node`` resolves ``(home_root, target, context.gig_id)`` through
    ``resolve_workpad`` exactly as bindings does, and a placeholder is refused.
    """

    home, target, gig_id = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    return home, target, resolved.path, gig_id


def _real_context(home: Path, target: Path, workpad: Path, gig_id: str, *, key: str = "acquire-scale-1", run_id: str = "run_01") -> NodeContext:
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    return replace(_context(workpad, key, run_id), project_id=resolved.project_id, gig_id=resolved.gig_id)


def _config(*, ats: bool = True, exa: bool = False) -> FindJobsConfig:
    payload = json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text())
    config = FindJobsConfig.from_json(payload)
    return replace(config, sources=SourceToggles(exa=exa, ats=ats, hiringcafe=False), published_after=None)


def _input(config: FindJobsConfig | None = None) -> AcquireInput:
    return AcquireInput(config or _config(), "sha256:" + "c" * 64, None, (), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH)


def _limits(*, concurrency: int = 4, interval: float = 0.0, budget: float | None = None) -> AcquireLimits:
    return AcquireLimits(concurrency_per_provider=concurrency, min_request_interval_seconds=interval, time_budget_seconds=budget)


def _board(provider: ATSProvider, token: str, *, catalog: bool = False) -> WatchlistEntry:
    query_key = "catalog:test-rev" if catalog else "software engineer"
    return WatchlistEntry(
        watchlist_id=f"scout_watchlist:{provider.value}:{token}", provider=provider, board_token=token,
        company=token, state="active",
        first_seen=WatchlistFirstSeen(SourceKind.ATS, f"https://example.test/{token}", query_key, "batch-1", "2026-09-22T00:00:00Z"),
    )


def _row(provider: ATSProvider, token: str, job: str, title: str = "Software Engineer") -> PostingRow:
    url = f"https://boards.example/{token}/{job}"
    return PostingRow(
        url=url, normalized_url=url, provider=provider, board_token=token, company=token, title=title,
        location="Denver, CO", published_at="2026-09-20T00:00:00Z", content_sha256="sha256:" + (job * 64)[:64],
        source_kind=SourceKind.ATS, query_key=f"ats:{provider.value}:{token}",
    )


class _Watchlist:
    def __init__(self, boards):
        self.added = list(boards)

    def add_to_watchlist(self, entry):
        return entry

    def active_entries(self):
        return tuple(self.added)


class _Exa:
    def search(self, client, config, *, home_root=None):
        return ()


class _FakeATS:
    """``fetch_board`` fake: sleeps ``delay`` per board, tracks concurrency per provider."""

    def __init__(self, delay: float = 0.0, *, rows_per_board: int = 1, request_client: bool = False):
        self.delay = delay
        self.rows_per_board = rows_per_board
        self.request_client = request_client
        self.lock = threading.Lock()
        self.active: dict[str, int] = {}
        self.max_active: dict[str, int] = {}
        self.calls: list[tuple[str, str, float]] = []

    def fetch_board(self, client, provider, board_token, config, *, cache=None):
        with self.lock:
            self.active[provider] = self.active.get(provider, 0) + 1
            self.max_active[provider] = max(self.max_active.get(provider, 0), self.active[provider])
            self.calls.append((provider, board_token, time.monotonic()))
        try:
            if self.request_client and client is not None:
                client.get(f"https://api.example/{provider}/{board_token}")
            if self.delay:
                time.sleep(self.delay)
        finally:
            with self.lock:
                self.active[provider] -= 1
        rows = tuple(_row(ATSProvider(provider), board_token, str(i)) for i in range(self.rows_per_board))
        return BoardFetchResult(rows, BoardFetchStats(requests=1, cache="miss", listed=self.rows_per_board))


class _TimestampClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.starts: list[float] = []

    def get(self, url, *args, **kwargs):
        with self.lock:
            self.starts.append(time.monotonic())
        return SimpleNamespace(status_code=200, content=b"{}", headers={})


def _patch_import(monkeypatch: pytest.MonkeyPatch) -> None:
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)


# --- concurrency + pacing ----------------------------------------------------


def test_concurrency_is_bounded_per_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_import(monkeypatch)
    boards = [_board(ATSProvider.GREENHOUSE, f"g{i}") for i in range(12)] + [_board(ATSProvider.LEVER, f"l{i}") for i in range(6)]
    ats = _FakeATS(delay=0.03)
    out = acquire_node(
        _context(tmp_path), _input(), http_client=None, exa=_Exa(), ats=ats, watchlist=_Watchlist(boards),
        limits=_limits(concurrency=3),
    )
    assert len(out.rows) == 18
    assert out.failures == ()
    # Never more than the bound within one provider; the two pools run side
    # by side (each provider reaches its own bound independently).
    assert ats.max_active["greenhouse"] <= 3
    assert ats.max_active["lever"] <= 3
    assert ats.max_active["greenhouse"] >= 2, "the pool never actually ran boards in parallel"
    # Rows come back in planned board order (provider, then token -- a plain
    # string sort, so g10 precedes g2), never completion order.
    assert [row.posting.board_token for row in out.rows] == sorted(f"g{i}" for i in range(12)) + sorted(f"l{i}" for i in range(6))


def test_requests_are_paced_per_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_import(monkeypatch)
    boards = [_board(ATSProvider.ASHBY, f"a{i}") for i in range(6)]
    client = _TimestampClient()
    ats = _FakeATS(request_client=True)
    acquire_node(
        _context(tmp_path), _input(), http_client=client, exa=_Exa(), ats=ats, watchlist=_Watchlist(boards),
        limits=_limits(concurrency=4, interval=0.05),
    )
    starts = sorted(client.starts)
    assert len(starts) == 6
    gaps = [later - earlier for earlier, later in zip(starts, starts[1:])]
    assert all(gap >= 0.045 for gap in gaps), gaps


# --- cache hits + title prefilter (real ATSBoardClients over a fake transport) --


class _GreenhouseFixture:
    """Three jobs, two matching; an ETag'd list; details served per job id."""

    def __init__(self):
        self.requests: list[tuple[str, str | None]] = []
        self.jobs = [
            {"id": 11, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
            {"id": 12, "title": "Marketing Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "location": {"name": "Remote"}, "updated_at": "2026-09-20T00:00:00Z"},
            {"id": 13, "title": "Senior Data Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-21T00:00:00Z"},
        ]

    @property
    def etag(self) -> str:
        # A real server's ETag changes with the body: derive it from the jobs.
        return 'W/"acme-%d"' % (sum(hash(job["updated_at"]) for job in self.jobs) & 0xFFFF)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((path, request.headers.get("if-none-match")))
        if path == "/v1/boards/acme/jobs":
            if request.headers.get("if-none-match") == self.etag:
                return httpx.Response(304, headers={"etag": self.etag})
            return httpx.Response(200, json={"jobs": self.jobs}, headers={"etag": self.etag})
        for job in self.jobs:
            if path == f"/v1/boards/acme/jobs/{job['id']}":
                return httpx.Response(200, json={**job, "content": f"&lt;p&gt;Build {job['id']}.&lt;/p&gt;"})
        return httpx.Response(404, json={"error": "no fixture"})


def test_second_run_reuses_the_board_cache_and_prefilters_titles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_import(monkeypatch)
    fixture = _GreenhouseFixture()
    boards = [_board(ATSProvider.GREENHOUSE, "acme")]
    home, target, workpad, gig_id = _managed(tmp_path)
    limits = _limits(concurrency=2)

    with httpx.Client(transport=httpx.MockTransport(fixture.handler)) as client:
        first = acquire_node(
            _real_context(home, target, workpad, gig_id, run_id="run_01"), _input(), http_client=client, exa=_Exa(), ats=ATSBoardClients(),
            watchlist=_Watchlist(boards), home_root=home, target=target, limits=limits,
        )
    assert [row.posting.title for row in first.rows] == ["Software Engineer", "Senior Data Engineer"]
    assert first.rows[0].posting.text == "Build 11."
    # One content-free list + one detail per MATCHING title; the Marketing
    # job (12) never gets a detail request (title prefilter).
    assert [path for path, _ in fixture.requests] == [
        "/v1/boards/acme/jobs",
        "/v1/boards/acme/jobs/11",
        "/v1/boards/acme/jobs/13",
    ]
    assert (home / "cache" / "scout" / "ats-boards" / "greenhouse").is_dir()
    run1 = read_progress(workpad / "runs" / "run_01")
    assert run1.boards["total"] == 1 and run1.boards["fetched"] == 1 and run1.boards["requests"] == 3
    assert run1.boards["prefiltered_out"] == 1 and run1.boards["detail_fetched"] == 2

    fixture.requests.clear()
    with httpx.Client(transport=httpx.MockTransport(fixture.handler)) as client:
        second = acquire_node(
            _real_context(home, target, workpad, gig_id, key="acquire-scale-2", run_id="run_02"), _input(), http_client=client, exa=_Exa(),
            ats=ATSBoardClients(), watchlist=_Watchlist(boards), home_root=home, target=target, limits=limits,
        )
    # Same rows with identical digests (what the reuse rule keys on -- the
    # UNCHANGED outcome itself needs the sealed batch this test patches out),
    # but only ONE request: the conditional list GET answered 304; both
    # details were served from the cache by their unchanged updated_at.
    assert [row.posting.content_sha256 for row in second.rows] == [row.posting.content_sha256 for row in first.rows]
    assert fixture.requests == [("/v1/boards/acme/jobs", fixture.etag)]
    run2 = read_progress(workpad / "runs" / "run_02")
    assert run2.boards["cached"] == 1 and run2.boards["cache_hits"] == 1 and run2.boards["requests"] == 1
    assert run2.boards["detail_cached"] == 2 and run2.boards["detail_fetched"] == 0
    line = next(iter(json.loads(l) for l in (workpad / "runs" / "run_02" / "progress" / "boards.jsonl").read_text().splitlines()))
    assert line["status"] == "cached" and line["cache"] == "hit" and line["matched"] == 2 and line["postings"] == 3
    # The cache lives under the home, never in the workpad (regression-001).
    assert_managed_workpad_clean(workpad)


def test_a_changed_job_is_refetched_but_unchanged_ones_are_not(tmp_path: Path) -> None:
    fixture = _GreenhouseFixture()
    cache = BoardCache(tmp_path / "cache")
    config = _config()
    with httpx.Client(transport=httpx.MockTransport(fixture.handler)) as client:
        ATSBoardClients().fetch_board(client, "greenhouse", "acme", config, cache=cache)
        fixture.requests.clear()
        # Job 13 is edited: new updated_at, so the list changes (no 304) and
        # only 13's detail is refetched; 11's detail comes from the cache.
        fixture.jobs[2]["updated_at"] = "2026-09-24T00:00:00Z"
        result = ATSBoardClients().fetch_board(client, "greenhouse", "acme", config, cache=cache)
    assert [path for path, _ in fixture.requests] == ["/v1/boards/acme/jobs", "/v1/boards/acme/jobs/13"]
    assert result.stats.cache == "miss" and result.stats.detail_cached == 1 and result.stats.detail_fetched == 1
    assert result.stats.prefiltered_out == 1


def test_lever_and_ashby_lists_revalidate_by_digest_and_prefilter_in_list(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "api.lever.co":
            return httpx.Response(200, json=[
                {"id": "1", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/bright/1", "categories": {"location": "Denver"}, "descriptionPlain": "Build."},
                {"id": "2", "text": "Recruiter", "hostedUrl": "https://jobs.lever.co/bright/2", "categories": {"location": "Denver"}, "descriptionPlain": "Hire."},
            ])
        return httpx.Response(200, json={"jobs": [
            {"id": "x", "title": "Data Engineer", "jobUrl": "https://jobs.ashbyhq.com/orbit/x", "location": "Remote", "descriptionPlain": "Pipes."},
        ]})

    cache = BoardCache(tmp_path / "cache")
    config = _config()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        lever1 = ATSBoardClients().fetch_board(client, "lever", "bright", config, cache=cache)
        lever2 = ATSBoardClients().fetch_board(client, "lever", "bright", config, cache=cache)
        ashby1 = ATSBoardClients().fetch_board(client, "ashby", "orbit", config, cache=cache)
    assert [row.title for row in lever1.rows] == ["Software Engineer"]
    assert lever1.stats.cache == "miss" and lever1.stats.listed == 2 and lever1.stats.prefiltered_out == 1 and lever1.stats.requests == 1
    # No ETag from the server: the second call still costs one request, but
    # the unchanged body digest is recognized (a hit for the reuse rule).
    assert lever2.stats.cache == "revalidated" and lever2.stats.requests == 1
    assert [row.title for row in ashby1.rows] == ["Data Engineer"]
    assert ashby1.stats.listed == 1 and ashby1.stats.prefiltered_out == 0
    assert len(calls) == 3


# --- the run-time budget ----------------------------------------------------


def test_budget_stops_cleanly_records_skipped_boards_and_runs_user_boards_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_import(monkeypatch)
    boards = [
        _board(ATSProvider.GREENHOUSE, "cat-a", catalog=True),
        _board(ATSProvider.GREENHOUSE, "cat-b", catalog=True),
        _board(ATSProvider.GREENHOUSE, "mine-z"),
        _board(ATSProvider.GREENHOUSE, "cat-c", catalog=True),
        _board(ATSProvider.GREENHOUSE, "mine-y"),
    ]
    ats = _FakeATS(delay=0.12)
    out = acquire_node(
        _context(tmp_path), _input(), http_client=None, exa=_Exa(), ats=ats, watchlist=_Watchlist(boards),
        limits=_limits(concurrency=1, budget=0.2),
    )
    fetched = [token for _provider, token, _at in ats.calls]
    # The operator's own boards go first regardless of watchlist order; the
    # catalog boards are what the budget cuts.
    assert fetched[:2] == ["mine-y", "mine-z"]
    assert len(fetched) < 5, "the budget never triggered"
    assert {row.posting.board_token for row in out.rows} == set(fetched)
    skipped_rows = [failure for failure in out.failures if failure.code == BUDGET_EXCEEDED_CODE]
    assert len(skipped_rows) == 1
    assert skipped_rows[0].query_key == "ats"
    assert f"{5 - len(fetched)} of 5" in skipped_rows[0].message
    assert out.progress_status.value == "complete"

    snapshot = read_progress(tmp_path / "runs" / "run_01")
    assert snapshot.boards["total"] == 5
    assert snapshot.boards["status"] == "done"
    assert snapshot.boards["fetched"] == len(fetched)
    assert snapshot.boards["skipped"] == 5 - len(fetched)
    assert snapshot.boards["budget_seconds"] == 0.2
    unfetched = {"cat-a", "cat-b", "cat-c", "mine-z", "mine-y"} - set(fetched)
    assert set(snapshot.boards["skipped_boards"]) == {f"greenhouse:{token}" for token in unfetched}
    lines = [json.loads(line) for line in (tmp_path / "runs" / "run_01" / "progress" / "boards.jsonl").read_text().splitlines()]
    assert len(lines) == 5
    assert {line["board_token"] for line in lines if line["status"] == "skipped"} == unfetched
    assert all(line["code"] == BUDGET_EXCEEDED_CODE for line in lines if line["status"] == "skipped")
    assert snapshot.steps["acquire"]["status"] == "done"


def test_a_budget_that_fetches_nothing_fails_the_acquire_like_any_dead_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_import(monkeypatch)
    boards = [_board(ATSProvider.GREENHOUSE, "a"), _board(ATSProvider.LEVER, "b")]
    ats = _FakeATS()
    with pytest.raises(AcquireAllSourcesFailedError) as excinfo:
        acquire_node(
            _context(tmp_path), _input(), http_client=None, exa=_Exa(), ats=ats, watchlist=_Watchlist(boards),
            limits=_limits(budget=0.000001),
        )
    assert BUDGET_EXCEEDED_CODE in str(excinfo.value)
    assert ats.calls == []
    assert read_progress(tmp_path / "runs" / "run_01").boards["skipped"] == 2


def test_a_budget_skip_next_to_real_rows_does_not_fail_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Some boards fetched, some skipped: the batch seals with what it has."""

    _patch_import(monkeypatch)
    boards = [_board(ATSProvider.GREENHOUSE, f"g{i}") for i in range(4)]
    ats = _FakeATS(delay=0.1)
    out = acquire_node(
        _context(tmp_path), _input(), http_client=None, exa=_Exa(), ats=ats, watchlist=_Watchlist(boards),
        limits=_limits(concurrency=1, budget=0.15),
    )
    assert out.rows
    assert any(failure.code == BUDGET_EXCEEDED_CODE for failure in out.failures)


def test_limits_come_from_the_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_ATS_CONCURRENCY", "7")
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0.5")
    monkeypatch.setenv("GIGAI_SCOUT_ACQUIRE_BUDGET_SECONDS", "0")
    limits = AcquireLimits.from_environment()
    assert limits == AcquireLimits(concurrency_per_provider=7, min_request_interval_seconds=0.5, time_budget_seconds=None)
    monkeypatch.setenv("GIGAI_SCOUT_ATS_CONCURRENCY", "garbage")
    monkeypatch.delenv("GIGAI_SCOUT_ACQUIRE_BUDGET_SECONDS")
    assert AcquireLimits.from_environment() == AcquireLimits(concurrency_per_provider=4, min_request_interval_seconds=0.5, time_budget_seconds=1200.0)


# --- seeding inside acquire ---------------------------------------------------


class _JournalWatchlist:
    """``bindings._BoundWatchlist``'s shape over the real journal client.

    Bindings passes no gig id (a live target has an active gig selected);
    the fixture workpad has none selected, so the id is threaded explicitly.
    """

    def __init__(self, home: Path, target: Path, gig_id: str):
        self._home, self._target = home, target
        self._journal = JournalWatchlistClient(home, target, gig_id)

    def add_to_watchlist(self, entry):
        return self._journal.add_to_watchlist(entry)

    def active_entries(self):
        return list_active(self._home, self._target, gig_id=self._journal._resolved.gig_id)


def test_acquire_seeds_the_watchlist_from_the_catalog_once_prefs_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_import(monkeypatch)
    home, target, workpad, gig_id = _managed(tmp_path)
    watchlist = _JournalWatchlist(home, target, gig_id)
    ats = _FakeATS(rows_per_board=0)
    limits = _limits(concurrency=8)

    # No prefs saved yet: nothing is seeded, and the run says so.
    acquire_node(_real_context(home, target, workpad, gig_id, run_id="run_01"), _input(), http_client=None, exa=_Exa(), ats=ats, watchlist=watchlist, home_root=home, target=target, limits=limits)
    before = read_progress(workpad / "runs" / "run_01")
    assert before.watchlist_seed == {"status": "skipped", "reason": "prefs_missing"}
    assert list_active(home, target, gig_id) == ()
    assert before.boards == {}

    save_prefs(home_root=home, target=target, prefs=DiscoveryPrefs(countries=("US",), exclude_companies=("openai",)))
    catalog = load_company_catalog()
    out = acquire_node(_real_context(home, target, workpad, gig_id, key="acquire-scale-2", run_id="run_02"), _input(), http_client=None, exa=_Exa(), ats=ats, watchlist=watchlist, home_root=home, target=target, limits=limits)
    assert out.failures == ()
    seeded = read_progress(workpad / "runs" / "run_02").watchlist_seed
    assert seeded is not None and seeded["status"] == "seeded"
    assert seeded["catalog_revision"] == catalog.revision and seeded["catalog_digest"] == catalog.digest
    assert seeded["added"] == seeded["eligible"] > 0
    assert seeded["excluded_by_company"] >= 1
    assert seeded["already_present"] == 0
    active = list_active(home, target, gig_id)
    assert len(active) == seeded["added"]
    assert all(entry.first_seen.query_key == f"catalog:{catalog.revision}" for entry in active)
    assert "openai" not in {entry.board_token.lower() for entry in active}
    # Every seeded board was then fetched in this same run.
    assert read_progress(workpad / "runs" / "run_02").boards["total"] == seeded["added"]
    assert len(ats.calls) == seeded["added"]

    # Third run: idempotent -- nothing added, everything already present.
    ats.calls.clear()
    acquire_node(_real_context(home, target, workpad, gig_id, key="acquire-scale-3", run_id="run_03"), _input(), http_client=None, exa=_Exa(), ats=ats, watchlist=watchlist, home_root=home, target=target, limits=limits)
    again = read_progress(workpad / "runs" / "run_03").watchlist_seed
    assert again is not None and again["added"] == 0 and again["already_present"] == seeded["added"]
    assert again["receipt_path"] is None
    assert len(list_active(home, target, gig_id)) == seeded["added"]
    assert len(sorted((workpad / "records" / "operations").glob("scout_watchlist_seed-*.json"))) == 1
    assert_managed_workpad_clean(workpad)
