"""N11-C: ``gigai scout sources update`` -- the engine, the CLI and the API routes.

The update refreshes every watchlist board through the existing acquire
rotation (conditional GETs, pacing, budget, the last-fetched index) and
brings the company index in step. Everything here runs over an
``httpx.MockTransport`` (or a fake board client on a fake clock for the
budget cases): no network, and no board request is ever made by the index.

The engine tests hand ``update_sources`` a board list directly; the CLI and
API tests run against a real ``gigai setup``/``init``/``scout install``
target (``test_watchlist_add_company._installed``) and assert the managed
workpad is left clean.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs import sources_update
from gigai.scout.find_jobs.ats_board_clients import BoardFetchResult, BoardFetchStats
from gigai.scout.find_jobs.company_index import CompanyIndex, board_list_url, index_stamp
from gigai.scout.find_jobs.contracts import ATSProvider, content_hash
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.find_jobs.sources_update import (
    SOURCES_UPDATE_STATUS_SCHEMA,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PARTIAL,
    STATUS_SUCCEEDED,
    SourcesUpdateRunningError,
    board_cache_for_home,
    read_status,
    update_sources,
)
from gigai.workpad import resolve_workpad

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _config, _limits
from tests.support.workpad_assertions import assert_managed_workpad_clean

from .test_watchlist_add_company import _installed


class _Boards:
    """Fake Greenhouse (ETag, 304, one detail per job) + Lever (no ETag); ``dead`` tokens 404."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str | None]] = []
        self.lock = threading.Lock()
        self.version = 1
        self.greenhouse = {
            "acme": [
                {"id": 11, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
                {"id": 12, "title": "Marketing Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "location": {"name": "Remote"}, "updated_at": "2026-09-20T00:00:00Z"},
            ],
            "globex": [
                {"id": 21, "title": "Senior Software Engineer", "absolute_url": "https://boards.greenhouse.io/globex/jobs/21", "location": {"name": "Austin, TX"}, "updated_at": "2026-09-21T00:00:00Z"},
            ],
        }
        self.lever = {
            "initech": [
                {"id": "lev-1", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/initech/lev-1", "categories": {"location": "Austin, TX"}, "country": "US", "createdAt": 1758326400000, "descriptionPlain": "Build things."},
                {"id": "lev-2", "text": "Office Manager", "hostedUrl": "https://jobs.lever.co/initech/lev-2", "categories": {"location": "Berlin"}, "country": "DE", "createdAt": 1758412800000, "descriptionPlain": "Run the office."},
            ],
        }
        self.gate: threading.Event | None = None

    def etag(self, token: str) -> str:
        return f'W/"{token}-{self.version}"'

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.gate is not None:
            assert self.gate.wait(timeout=30), "the test never released the board"
        path = request.url.path
        with self.lock:
            self.requests.append((path, request.headers.get("if-none-match")))
        parts = path.strip("/").split("/")
        if request.url.host == "boards-api.greenhouse.io" and len(parts) >= 4 and parts[2] in self.greenhouse:
            token, jobs = parts[2], self.greenhouse[parts[2]]
            if len(parts) == 4:
                if request.headers.get("if-none-match") == self.etag(token):
                    return httpx.Response(304, headers={"etag": self.etag(token)})
                return httpx.Response(200, json={"jobs": jobs}, headers={"etag": self.etag(token)})
            for job in jobs:
                if parts[4] == str(job["id"]):
                    return httpx.Response(200, json={**job, "content": f"&lt;p&gt;Build {job['id']}.&lt;/p&gt;"})
        if request.url.host == "api.lever.co" and parts[-1] in self.lever:
            return httpx.Response(200, json=self.lever[parts[-1]])
        return httpx.Response(404, json={"error": "no such board"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _watchlist() -> list:
    return [
        _board(ATSProvider.GREENHOUSE, "acme"),
        _board(ATSProvider.GREENHOUSE, "globex", catalog=True),
        _board(ATSProvider.LEVER, "initech", catalog=True),
        _board(ATSProvider.LEVER, "dead", catalog=True),
    ]


def _update(home: Path, boards: _Boards, watchlist=None, **kwargs):
    with boards.client() as client:
        return update_sources(
            _watchlist() if watchlist is None else watchlist,
            cache=board_cache_for_home(home),
            index=CompanyIndex.for_home(home),
            client=client,
            config=kwargs.pop("config", _config()),
            limits=kwargs.pop("limits", _limits(concurrency=2)),
            **kwargs,
        )


# --- the engine ----------------------------------------------------------------


def test_an_update_indexes_every_board_and_prints_the_operator_summary(tmp_path: Path) -> None:
    boards = _Boards()
    seen: list[dict] = []

    result = _update(tmp_path, boards, on_progress=seen.append)

    assert result.status == STATUS_SUCCEEDED
    assert result.summary == "3 companies with new postings: 5 new, 0 changed, 0 removed"
    snapshot = result.to_json()
    assert snapshot["update_id"].startswith("sources_update_")
    assert snapshot["boards"] == {"total": 4, "done": 4, "checked": 4, "fetched": 3, "cached": 0, "failed": 1, "skipped": 0, "never_checked": 0, "up_to_date": 0}
    assert snapshot["companies"] == {"checked": 3, "indexed": 3, "updated": 0, "untouched": 0, "unreadable": 0, "with_new": 3, "with_changes": 3}
    assert snapshot["postings"] == {"new": 5, "changed": 0, "removed": 0, "live": 5}
    assert snapshot["remaining"] == 0 and snapshot["error"] is None
    assert snapshot["finished_at"] is not None and snapshot["started_at"] <= snapshot["finished_at"]
    assert snapshot["roles"] == list(_config().roles)
    assert snapshot["rotation"]["total"] == 4 and snapshot["rotation"]["runs_per_rotation"] == 1
    # acme list + detail 11, globex list + detail 21 (the two titles the
    # roles match), one Lever list, one dead board.
    assert snapshot["requests"] == 5 and len(boards.requests) == 6

    index = CompanyIndex.for_home(tmp_path)
    assert sorted(index.keys()) == [("greenhouse", "acme"), ("greenhouse", "globex"), ("lever", "initech")]
    acme = index.read("greenhouse", "acme")
    assert acme is not None and acme.company == "acme" and acme.etag == 'W/"acme-1"'
    # Every listed title is stored, not only the ones the roles match.
    assert sorted(posting.title for posting in acme.postings.values()) == ["Marketing Manager", "Software Engineer"]
    assert acme.postings["11"].content_sha256 == content_hash(b"Software Engineer\nBuild 11.")
    assert acme.postings["12"].content_sha256 is None
    # The snapshot a status poll reads is the one the update returned.
    assert index.read_update_summary() == {**snapshot, "schema_version": "scout-sources-update:1"}
    assert seen[0]["status"] == "running" and seen[-1] == snapshot
    # Before anything is asked the whole watchlist is the backlog; the first
    # board that settles is published at once, so even an update that takes
    # a second shows a bar that moves.
    assert seen[0]["boards"]["checked"] == 0 and seen[0]["boards"]["never_checked"] == 4
    moving = [item for item in seen if item["status"] == "running" and item["boards"]["checked"] >= 1]
    assert moving, [item["boards"] for item in seen]
    assert moving[0]["boards"]["checked"] == 1 and moving[0]["remaining"] == 3
    assert (tmp_path / "cache" / "scout" / "ats-boards" / "last-fetched.json").is_file()


def test_a_second_update_makes_one_conditional_request_per_board_and_changes_nothing(tmp_path: Path) -> None:
    boards = _Boards()
    _update(tmp_path, boards)
    index = CompanyIndex.for_home(tmp_path)
    before = {key: index.read(*key) for key in index.keys()}
    boards.requests.clear()

    result = _update(tmp_path, boards, full_refresh=True)

    assert result.status == STATUS_SUCCEEDED
    assert result.summary == "0 companies with new postings: 0 new, 0 changed, 0 removed"
    snapshot = result.to_json()
    assert snapshot["boards"] == {"total": 4, "done": 4, "checked": 4, "fetched": 0, "cached": 3, "failed": 1, "skipped": 0, "never_checked": 0, "up_to_date": 0}
    assert snapshot["companies"]["untouched"] == 3 and snapshot["companies"]["updated"] == 0
    assert snapshot["postings"]["live"] == 5
    assert sorted(boards.requests) == [
        ("/v0/postings/dead", None),
        ("/v0/postings/initech", None),
        ("/v1/boards/acme/jobs", 'W/"acme-1"'),
        ("/v1/boards/globex/jobs", 'W/"globex-1"'),
    ]
    for key, entry in before.items():
        after = index.read(*key)
        assert after is not None and entry is not None
        assert after.checked_at > entry.checked_at
        assert {**after.to_json(), "checked_at": entry.checked_at} == entry.to_json()


def test_two_updates_in_a_row_check_no_board_the_second_time(tmp_path: Path) -> None:
    boards = _Boards()
    live = [_board(ATSProvider.GREENHOUSE, "acme"), _board(ATSProvider.GREENHOUSE, "globex", catalog=True), _board(ATSProvider.LEVER, "initech", catalog=True)]

    first = _update(tmp_path, boards, watchlist=live).to_json()
    assert first["boards"]["checked"] == 3 and first["boards"]["up_to_date"] == 0
    boards.requests.clear()

    started = time.monotonic()
    second = _update(tmp_path, boards, watchlist=live)
    elapsed = time.monotonic() - started

    # The symptom: the fake board fetcher was never called.
    assert boards.requests == []
    assert second.status == STATUS_SUCCEEDED
    assert second.to_json()["boards"]["checked"] == 0 and second.to_json()["boards"]["up_to_date"] == 3
    assert second.to_json()["requests"] == 0 and second.to_json()["full_refresh"] is False
    assert elapsed < 5.0


def test_a_full_refresh_checks_every_board_even_when_all_are_fresh(tmp_path: Path) -> None:
    boards = _Boards()
    live = [_board(ATSProvider.GREENHOUSE, "acme"), _board(ATSProvider.GREENHOUSE, "globex", catalog=True), _board(ATSProvider.LEVER, "initech", catalog=True)]
    _update(tmp_path, boards, watchlist=live)
    boards.requests.clear()

    full = _update(tmp_path, boards, watchlist=live, full_refresh=True).to_json()

    assert full["boards"]["checked"] == 3 and full["boards"]["up_to_date"] == 0 and full["full_refresh"] is True
    assert len(boards.requests) == 3


def test_boards_checked_longer_ago_than_the_stale_window_are_asked_again(tmp_path: Path) -> None:
    boards = _Boards()
    live = [_board(ATSProvider.GREENHOUSE, "acme"), _board(ATSProvider.LEVER, "initech", catalog=True)]
    _update(tmp_path, boards, watchlist=live)
    boards.requests.clear()

    later = datetime.now(timezone.utc) + timedelta(hours=sources_update.DEFAULT_STALE_AFTER_HOURS + 1)
    result = _update(tmp_path, boards, watchlist=live, now=later).to_json()

    assert result["boards"]["checked"] == 2 and result["boards"]["up_to_date"] == 0
    assert len(boards.requests) == 2


def test_an_update_reports_new_changed_and_removed_postings(tmp_path: Path) -> None:
    boards = _Boards()
    _update(tmp_path, boards)

    boards.version = 2
    boards.greenhouse["acme"] = [
        {**boards.greenhouse["acme"][0], "updated_at": "2026-09-27T00:00:00Z"},
        {"id": 13, "title": "Data Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Remote"}, "updated_at": "2026-09-27T00:00:00Z"},
    ]
    boards.lever["initech"] = boards.lever["initech"][:1]
    result = _update(tmp_path, boards, full_refresh=True)

    assert result.summary == "1 company with new postings: 1 new, 1 changed, 2 removed"
    snapshot = result.to_json()
    # globex answered 200 under a new ETag with the same bytes: untouched.
    assert snapshot["companies"] == {"checked": 3, "indexed": 0, "updated": 2, "untouched": 1, "unreadable": 0, "with_new": 1, "with_changes": 2}
    assert snapshot["postings"] == {"new": 1, "changed": 1, "removed": 2, "live": 4}
    acme = CompanyIndex.for_home(tmp_path).read("greenhouse", "acme")
    assert acme is not None
    assert acme.postings["13"].first_seen == acme.checked_at
    assert acme.postings["11"].changed_at == acme.checked_at
    assert acme.postings["12"].removed_at == acme.checked_at


def test_a_deleted_index_is_rebuilt_by_the_next_update_without_refetching_bodies(tmp_path: Path) -> None:
    boards = _Boards()
    _update(tmp_path, boards)
    index = CompanyIndex.for_home(tmp_path)
    for item in index.root.iterdir():
        item.unlink()
    boards.requests.clear()

    result = _update(tmp_path, boards)  # no company files: nothing counts as up to date

    # The boards answer 304 / the same bytes; the index comes back from the cached bodies.
    assert ("/v1/boards/acme/jobs", 'W/"acme-1"') in boards.requests
    assert result.to_json()["boards"]["cached"] == 3
    assert result.to_json()["companies"]["indexed"] == 3
    assert sorted(index.keys()) == [("greenhouse", "acme"), ("greenhouse", "globex"), ("lever", "initech")]
    acme = index.read("greenhouse", "acme")
    assert acme is not None and acme.postings["11"].content_sha256 == content_hash(b"Software Engineer\nBuild 11.")


def test_a_board_that_does_not_answer_was_checked_and_is_asked_once_per_update(tmp_path: Path) -> None:
    boards = _Boards()

    first = _update(tmp_path, boards).to_json()
    boards.requests.clear()
    second = _update(tmp_path, boards).to_json()

    # A dead board is not backlog: it was asked and it rotates like a live
    # one, one request per update, never more. It has no company file, so
    # an incremental update asks it again (the three live boards are fresh).
    for snapshot in (first, second):
        assert snapshot["boards"]["failed"] == 1 and snapshot["boards"]["never_checked"] == 0
        assert snapshot["remaining"] == 0 and snapshot["status"] == STATUS_SUCCEEDED
    assert second["boards"]["up_to_date"] == 3 and second["boards"]["total"] == 1
    assert [path for path, _etag in boards.requests].count("/v0/postings/dead") == 1


def test_an_update_where_no_board_answers_fails(tmp_path: Path) -> None:
    boards = _Boards()

    result = _update(tmp_path, boards, watchlist=[_board(ATSProvider.LEVER, "dead"), _board(ATSProvider.ASHBY, "gone")])

    assert result.status == STATUS_FAILED
    assert result.to_json()["error"] == {"code": "every_board_failed", "message": "no board could be fetched"}
    assert list(CompanyIndex.for_home(tmp_path).keys()) == []


def test_an_update_of_an_empty_watchlist_succeeds_with_nothing_to_do(tmp_path: Path) -> None:
    result = _update(tmp_path, _Boards(), watchlist=[])

    assert result.status == STATUS_SUCCEEDED
    assert result.to_json()["boards"]["total"] == 0
    assert result.summary == "0 companies with new postings: 0 new, 0 changed, 0 removed"


# --- resumable: the rotation and the budget are the engine ------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.lock = threading.Lock()

    def monotonic(self) -> float:
        with self.lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.now += seconds


class _ClockATS:
    """One board = one clock tick; it leaves a Lever body in the cache like the real client."""

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.calls: list[str] = []

    def fetch_board(self, client, provider, board_token, config, *, cache=None):
        self.calls.append(board_token)
        self.clock.sleep(1.0)
        body = json.dumps([{"id": f"{board_token}-1", "text": "Software Engineer", "hostedUrl": f"https://jobs.lever.co/{board_token}/1", "descriptionPlain": "Build."}]).encode()
        cache.store(provider, board_list_url(provider, board_token), body=body, etag=None, last_modified=None, marker=None)
        return BoardFetchResult((), BoardFetchStats(requests=1, cache="miss", listed=1))


def test_a_budget_stop_is_partial_and_the_next_update_continues_where_it_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.time", clock)
    monkeypatch.setattr(sources_update, "time", clock)
    watchlist = [_board(ATSProvider.LEVER, f"b{n}", catalog=True) for n in range(6)]
    cache, index = board_cache_for_home(tmp_path), CompanyIndex.for_home(tmp_path)

    def run(ats: _ClockATS):
        return update_sources(watchlist, cache=cache, index=index, client=None, ats=ats, config=_config(), limits=_limits(concurrency=1, budget=3.5))

    first_ats = _ClockATS(clock)
    first = run(first_ats)

    assert first.status == STATUS_PARTIAL
    assert first_ats.calls == ["b0", "b1", "b2", "b3"]
    snapshot = first.to_json()
    # `checked` is what was asked; `done` also counts the two the budget
    # skipped. Those two have never been asked by anything: the backlog.
    assert snapshot["boards"] == {"total": 6, "done": 6, "checked": 4, "fetched": 4, "cached": 0, "failed": 0, "skipped": 2, "never_checked": 2, "up_to_date": 0}
    assert snapshot["remaining"] == 2
    assert snapshot["rotation"]["first"] == 1 and snapshot["rotation"]["last"] == 4
    assert sorted(slug for _ats, slug in index.keys()) == ["b0", "b1", "b2", "b3"]

    second_ats = _ClockATS(clock)
    second = run(second_ats)

    # Incremental: the four boards just checked are fresh, so the next
    # update asks only the two the budget left behind.
    assert second_ats.calls == ["b4", "b5"]
    assert second.status == STATUS_SUCCEEDED
    assert second.to_json()["remaining"] == 0 and second.to_json()["boards"]["never_checked"] == 0
    assert second.to_json()["boards"]["checked"] == 2 and second.to_json()["boards"]["up_to_date"] == 4
    assert sorted(slug for _ats, slug in index.keys()) == ["b0", "b1", "b2", "b3", "b4", "b5"]
    assert second.to_json()["postings"]["new"] == 2


# --- one update at a time, and what a reader sees ---------------------------------


def _write_running(home: Path, *, updated_at: str) -> None:
    CompanyIndex.for_home(home).write_update_summary(
        {"update_id": "sources_update_other", "status": "running", "started_at": updated_at, "updated_at": updated_at, "finished_at": None, "summary": "0 companies with new postings: 0 new, 0 changed, 0 removed"}
    )


def test_a_second_update_is_refused_while_one_is_live(tmp_path: Path) -> None:
    boards = _Boards()
    _write_running(tmp_path, updated_at=index_stamp())

    with pytest.raises(SourcesUpdateRunningError) as caught:
        _update(tmp_path, boards)

    assert caught.value.code == "sources_update_running"
    assert boards.requests == []
    assert read_status(tmp_path)["running"] is True

    assert _update(tmp_path, boards, force=True).status == STATUS_SUCCEEDED


def test_a_running_snapshot_whose_process_died_reads_as_interrupted_and_does_not_block(tmp_path: Path) -> None:
    boards = _Boards()
    old = index_stamp(datetime.now(timezone.utc) - timedelta(seconds=sources_update.HEARTBEAT_TIMEOUT_SECONDS + 5))
    _write_running(tmp_path, updated_at=old)

    status = read_status(tmp_path)
    assert status["running"] is False and status["update"]["status"] == STATUS_INTERRUPTED

    assert _update(tmp_path, boards).status == STATUS_SUCCEEDED
    assert read_status(tmp_path)["update"]["status"] == STATUS_SUCCEEDED


def test_status_says_when_a_search_needs_update_sources(tmp_path: Path) -> None:
    empty = read_status(tmp_path)
    assert empty == {
        "schema_version": SOURCES_UPDATE_STATUS_SCHEMA,
        "running": False,
        "update": None,
        "index": {
            "status": "empty",
            "needs_update": True,
            "message": "No company postings are stored on this machine yet. Run Update sources, then search again.",
            "companies_indexed": 0,
            "last_checked_at": None,
            "stale_after_hours": 24.0,
        },
    }

    result = _update(tmp_path, _Boards())
    ready = read_status(tmp_path)
    assert ready["running"] is False
    assert ready["update"]["status"] == STATUS_SUCCEEDED and ready["update"]["summary"] == result.summary
    assert ready["index"] == {
        "status": "ready",
        "needs_update": False,
        "message": None,
        "companies_indexed": 3,
        "last_checked_at": result.to_json()["finished_at"],
        "stale_after_hours": 24.0,
    }

    later = read_status(tmp_path, now=datetime.now(timezone.utc) + timedelta(hours=25))
    assert later["index"]["status"] == "stale" and later["index"]["needs_update"] is True
    assert later["index"]["message"] == "The stored company postings are out of date. Run Update sources, then search again."


# --- CLI ------------------------------------------------------------------------------


def _workpad(home: Path, target: Path) -> Path:
    return resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True).path


def _add(home: Path, target: Path, url: str) -> None:
    result = CliRunner().invoke(cli, ["scout", "watchlist", "add", url, "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code == 0, result.output


def test_cli_sources_update_refreshes_the_watchlist_and_prints_the_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _installed(tmp_path)
    _add(home, target, "https://boards.greenhouse.io/acme")
    _add(home, target, "https://jobs.lever.co/initech")
    boards = _Boards()
    monkeypatch.setattr(sources_update, "default_http_client", boards.client)
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")

    first = CliRunner().invoke(cli, ["scout", "sources", "update", "--home", str(home), "--target", str(target)])

    assert first.exit_code == 0, first.output
    lines = first.stdout.strip().splitlines()
    assert "2 companies with new postings: 4 new, 0 changed, 0 removed" in lines
    assert any(line.startswith("Checked 2 of 2 boards (0 unchanged, 0 did not answer)") for line in lines)
    assert sorted(CompanyIndex.for_home(home).keys()) == [("greenhouse", "acme"), ("lever", "initech")]
    # The index is a cache under the home; the journaled workpad stays clean.
    assert_managed_workpad_clean(_workpad(home, target))
    assert not list(_workpad(home, target).rglob("companies"))

    boards.requests.clear()
    second = CliRunner().invoke(cli, ["scout", "sources", "update", "--home", str(home), "--target", str(target), "--json"])

    assert second.exit_code == 0, second.output
    # stdout is the JSON alone (the seeding note goes to stderr).
    payload = json.loads(second.stdout)
    assert payload["status"] == "succeeded"
    assert payload["summary"] == "0 companies with new postings: 0 new, 0 changed, 0 removed"
    # Incremental: both boards were checked seconds ago, so none is asked.
    assert payload["boards"] == {"total": 0, "done": 0, "checked": 0, "fetched": 0, "cached": 0, "failed": 0, "skipped": 0, "never_checked": 0, "up_to_date": 2}
    assert boards.requests == []
    assert_managed_workpad_clean(_workpad(home, target))

    status = CliRunner().invoke(cli, ["scout", "sources", "status", "--home", str(home)])
    assert status.exit_code == 0, status.output
    assert "Last update succeeded" in status.output and "Stored companies: 2 (ready)." in status.output
    as_json = CliRunner().invoke(cli, ["scout", "sources", "status", "--home", str(home), "--json"])
    assert json.loads(as_json.stdout)["index"]["needs_update"] is False


def test_cli_sources_update_refuses_a_second_live_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _installed(tmp_path)
    boards = _Boards()
    monkeypatch.setattr(sources_update, "default_http_client", boards.client)
    _write_running(home, updated_at=index_stamp())

    refused = CliRunner().invoke(cli, ["scout", "sources", "update", "--home", str(home), "--target", str(target), "--json"])

    assert refused.exit_code == 1, refused.output
    assert json.loads(refused.stdout)["error"]["code"] == "sources_update_running"
    assert boards.requests == []


def test_cli_sources_status_before_any_update(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["scout", "sources", "status", "--home", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "No sources update has run yet." in result.output
    assert "Run Update sources" in result.output


# --- API: POST/GET /api/sources/update ------------------------------------------------


@pytest.fixture
def running_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home, target = _installed(tmp_path)
    boards = _Boards()
    monkeypatch.setattr("gigai.scout.find_jobs.api.sources._board_http_client", boards.client)
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
    backend = ScoutFindJobsBackend(home_root=home, target=target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=30.0) as client:
            yield client, home, target, boards
    finally:
        if boards.gate is not None:
            boards.gate.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _poll(client: httpx.Client, *, seconds: float = 30.0) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        body = client.get("/api/sources/update").json()
        if not body["running"]:
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.05)


def test_api_update_sources_starts_in_the_background_and_reports_status(running_server) -> None:
    client, home, target, boards = running_server
    _add(home, target, "https://boards.greenhouse.io/acme")
    _add(home, target, "https://jobs.lever.co/dead")

    before = client.get("/api/sources/update")
    assert before.status_code == 200, before.text
    assert before.json()["update"] is None and before.json()["index"]["status"] == "empty"
    assert before.json()["index"]["needs_update"] is True

    boards.gate = threading.Event()
    started = client.post("/api/sources/update", json={})
    assert started.status_code == 202, started.text
    body = started.json()
    assert body["schema_version"] == "scout-sources-update-start-response:1"
    assert body["status"] == "running" and body["update_id"].startswith("sources_update_")

    # Live straight after the start, and a second start is refused.
    during = client.get("/api/sources/update").json()
    assert during["running"] is True
    assert during["update"]["update_id"] == body["update_id"] and during["update"]["status"] == "running"
    conflict = client.post("/api/sources/update", json={})
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "sources_update_running"

    boards.gate.set()
    finished = _poll(client)

    assert finished["schema_version"] == SOURCES_UPDATE_STATUS_SCHEMA
    update = finished["update"]
    assert update["update_id"] == body["update_id"] and update["status"] == "succeeded"
    assert update["boards"] == {"total": 2, "done": 2, "checked": 2, "fetched": 1, "cached": 0, "failed": 1, "skipped": 0, "never_checked": 0, "up_to_date": 0}
    assert update["summary"] == "1 company with new postings: 2 new, 0 changed, 0 removed"
    assert update["postings"] == {"new": 2, "changed": 0, "removed": 0, "live": 2}
    assert finished["index"]["status"] == "ready" and finished["index"]["needs_update"] is False
    assert finished["index"]["companies_indexed"] == 1
    assert list(CompanyIndex.for_home(home).keys()) == [("greenhouse", "acme")]
    assert_managed_workpad_clean(_workpad(home, target))

    # Again: incremental, so only the dead board (no company file) is asked.
    boards.requests.clear()
    assert client.post("/api/sources/update", json={}).status_code == 202
    again = _poll(client)["update"]
    assert again["update_id"] != body["update_id"]
    assert again["boards"]["up_to_date"] == 1 and again["boards"]["checked"] == 1 and again["status"] == "succeeded"
    assert ("/v1/boards/acme/jobs", 'W/"acme-1"') not in boards.requests

    # A full refresh asks every board: one conditional request for the live one.
    boards.requests.clear()
    assert client.post("/api/sources/update", json={"full_refresh": True}).status_code == 202
    full = _poll(client)["update"]
    assert full["full_refresh"] is True and full["boards"]["up_to_date"] == 0
    assert full["boards"]["cached"] == 1 and full["postings"]["new"] == 0
    assert ("/v1/boards/acme/jobs", 'W/"acme-1"') in boards.requests


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"force": "yes"}, "wrong_type"),
        ({"full_refresh": "yes"}, "wrong_type"),
        ({"boards": ["acme"]}, "unknown_key"),
        (["force"], "wrong_type"),
    ],
)
def test_api_update_sources_rejects_bad_bodies_with_422(running_server, body: object, code: str) -> None:
    client, home, _target, boards = running_server

    response = client.post("/api/sources/update", json=body)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert boards.requests == [] and CompanyIndex.for_home(home).read_update_summary() is None


def test_api_update_sources_is_csrf_guarded(running_server) -> None:
    client, home, _target, boards = running_server

    evil = client.post("/api/sources/update", json={}, headers={"Origin": "https://evil.example"})

    assert evil.status_code == 403, evil.text
    assert evil.json()["error"]["code"] == "forbidden_origin"
    assert boards.requests == [] and CompanyIndex.for_home(home).read_update_summary() is None

