"""0.1.11.8 release check: a host that is known to refuse is left at once, never waited out for nothing.

On the operator's copy a manual update spent ~600 of its 628 s idle: Workable's robots.txt answered 429, the host stayed
``robots_unknown``, and the update slept two pauses before it skipped the 1,258 boards. A provider whose ONE shared host's
robots.txt cannot be read (no ``Retry-After``, a 5xx, a timeout) now has its other boards skipped at once, ``robots_unknown``;
a ``Retry-After`` longer than the pass has left skips them as ``rate_limited``; a short one is waited out and the host is
asked again. A provider whose boards each have their own host fails board by board, as before. Fake hosts, a fake clock
(the wait only records and moves the clock): no network, no real sleep.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs import robots_guard, sources_update
from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
from gigai.scout.find_jobs.company_index import CompanyIndex
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.robots_guard import RobotsGuard
from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _config, _limits

WORKABLE = "apply.workable.com"


class _World:
    """Fake hosts plus a fake clock: ``wait`` records the pause and moves both the pass's clock and the guard's."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, robots) -> None:
        self.waits: list[float] = []
        self.offset = 0.0
        self.robots = robots
        self.asked: list[str] = []
        monkeypatch.setenv(sources_update.ATS_MIN_INTERVAL_ENV, "0")
        monkeypatch.setenv("GIGAI_SCOUT_ATS_PROVIDER_FLOORS", "0")
        real_time = robots_guard.time.time
        monkeypatch.setattr(robots_guard.time, "time", lambda: real_time() + self.offset)
        real = sources_update._BackoffClients

        def with_fake_clock(inner, stop, **kwargs):
            clients = real(inner, stop, **kwargs)
            clients._clock = lambda: self.offset
            clients._deadline = None if kwargs.get("deadline") is None else self._budget

            def wait(seconds: float) -> None:
                self.waits.append(round(seconds, 3))
                self.offset += seconds

            clients._wait = wait
            return clients

        self._budget = 0.0
        monkeypatch.setattr(sources_update, "_BackoffClients", with_fake_clock)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(str(request.url))
        if request.url.path == "/robots.txt":
            return self.robots(request, self)
        return httpx.Response(200, json={"jobs": []} if request.url.host == WORKABLE else [])

    def update(self, home: Path, boards, *, budget: float, robots_guard_: RobotsGuard) -> dict[str, object]:
        self._budget = budget
        with httpx.Client(transport=httpx.MockTransport(self.handler)) as client:
            return update_sources(
                boards, cache=board_cache_for_home(home), index=CompanyIndex.for_home(home), client=client, config=_config(),
                limits=_limits(concurrency=1, budget=budget), ats=ATSBoardClients(robots=robots_guard_),
            ).snapshot


def _workable_and_lever(workable: int = 6) -> list:
    return [_board(ATSProvider.WORKABLE, f"w{n}") for n in range(workable)] + [_board(ATSProvider.LEVER, f"l{n}") for n in range(3)]


def _lever_ok_else(answer):
    return lambda request, world: httpx.Response(404) if request.url.host != WORKABLE else answer(request, world)


@pytest.mark.parametrize("answer", [httpx.Response(503), httpx.Response(429), "timeout"], ids=["5xx", "429-no-retry-after", "timeout"])
def test_an_unreadable_robots_file_on_a_shared_host_skips_its_boards_at_once(answer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def robots(request: httpx.Request, world: _World) -> httpx.Response:
        if answer == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return answer

    world = _World(monkeypatch, _lever_ok_else(robots))
    snapshot = world.update(tmp_path, _workable_and_lever(), budget=1200.0, robots_guard_=RobotsGuard())

    assert world.waits == []  # nobody slept: a robots.txt we cannot read is not fixed by waiting
    assert [url for url in world.asked if WORKABLE in url] == [f"https://{WORKABLE}/robots.txt"]  # one ask, no list
    boards = snapshot["boards"]
    assert (boards["failed"], boards["skipped"], boards["fetched"] + boards["cached"]) == (1, 5, 3)  # type: ignore[index]
    assert snapshot["failures"]["codes"] == {"robots_unknown": 1}  # type: ignore[index]
    assert snapshot["backoff"] == {"workable": {"pauses": 0, "paused_seconds": 0.0, "skipped": 5, "code": "robots_unknown"}}
    assert snapshot["status"] == sources_update.STATUS_PARTIAL


def test_a_retry_after_longer_than_the_pass_has_left_skips_the_boards_as_rate_limited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, _lever_ok_else(lambda request, world: httpx.Response(429, headers={"Retry-After": "600"})))
    snapshot = world.update(tmp_path, _workable_and_lever(), budget=300.0, robots_guard_=RobotsGuard())

    assert world.waits == []
    assert snapshot["boards"]["skipped"] == 5 and snapshot["boards"]["fetched"] == 3  # type: ignore[index]  # Lever went on
    assert snapshot["backoff"] == {"workable": {"pauses": 0, "paused_seconds": 0.0, "skipped": 5, "code": "rate_limited"}}


def test_a_short_retry_after_on_robots_is_waited_out_and_the_host_asked_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def robots(request: httpx.Request, world: _World) -> httpx.Response:
        if sum(url.endswith("/robots.txt") and WORKABLE in url for url in world.asked) == 1:
            return httpx.Response(429, headers={"Retry-After": "20"})
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")

    world = _World(monkeypatch, _lever_ok_else(robots))
    snapshot = world.update(tmp_path, _workable_and_lever(), budget=300.0, robots_guard_=RobotsGuard())

    assert world.waits == [pytest.approx(20.0, abs=0.01)]  # the host's own number
    assert snapshot["boards"]["skipped"] == 0 and snapshot["boards"]["failed"] == 1  # type: ignore[index]  # the board that met the 429
    assert len([url for url in world.asked if WORKABLE in url and "robots" in url]) == 2
    assert len([url for url in world.asked if WORKABLE in url and "robots" not in url]) == 5  # the other five were asked after the wait
    assert snapshot["backoff"] == {"workable": {"pauses": 1, "paused_seconds": 20.0}}


def test_boards_with_their_own_host_fail_one_by_one_and_are_not_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, lambda request, world: httpx.Response(503))
    boards = [_board(ATSProvider.RECRUITEE, f"t{n}") for n in range(4)]
    snapshot = world.update(tmp_path, boards, budget=1200.0, robots_guard_=RobotsGuard())

    assert world.waits == []
    assert snapshot["failures"]["codes"] == {"robots_unknown": 4}  # type: ignore[index]  # one tenant's file says nothing about the next
    assert snapshot["boards"]["skipped"] == 0  # type: ignore[index]
    assert snapshot["backoff"] is None


# --- what `gigai scout sources status` says about it ----------------------------------------------------------------


def _status_lines(home: Path) -> list[str]:
    from click.testing import CliRunner

    from gigai.cli import cli

    result = CliRunner().invoke(cli, ["scout", "sources", "status", "--home", str(home)])
    assert result.exit_code == 0, result.output
    return result.stdout.splitlines()


def test_the_plain_status_names_the_system_whose_boards_are_waiting_and_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, _lever_ok_else(lambda request, world: httpx.Response(503)))
    world.update(tmp_path, _workable_and_lever(workable=1258), budget=1200.0, robots_guard_=RobotsGuard())

    lines = _status_lines(tmp_path)
    assert lines[0].startswith("Last update partial (")
    assert "Workable: 1,257 boards waiting (robots_unknown; asks again at the next update)" in lines
    assert sum(line.startswith("Stored companies:") for line in lines) == 1
    assert not any(line.startswith("Lever:") for line in lines)  # a system that answered has no line


def test_the_plain_status_says_a_rate_limit_and_a_disallowing_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs.company_index import CompanyIndex

    index = CompanyIndex.for_home(tmp_path)
    index.write_update_summary(
        {
            "status": "partial", "finished_at": "2026-10-09T15:02:38.656Z", "summary": "3 companies with new postings: 4 new, 0 changed, 0 removed",
            "backoff": {"workable": {"pauses": 2, "paused_seconds": 600.0, "skipped": 1258, "code": "rate_limited"}, "gem": {"pauses": 1, "paused_seconds": 30.0}},
            "failures": {"total": 50, "codes": {"robots_disallowed": 49, "http_404": 1}, "boards": [], "by_provider": {"gem": {"robots_disallowed": 49, "http_404": 1}}},
        }
    )
    lines = _status_lines(tmp_path)
    assert "Workable: 1,258 boards waiting (rate_limited; asks again at the next update)" in lines
    assert "Gem: 49 boards not asked (robots_disallowed: the host's robots.txt does not allow it)" in lines
    assert len([line for line in lines if ": " in line and line.split(":")[0] in {"Workable", "Gem"}]) == 2  # one line per system

    # A snapshot written before these keys existed prints what it always did.
    index.write_update_summary({"status": "partial", "finished_at": "2026-10-09T15:02:38.656Z", "summary": "x", "backoff": None, "failures": {"total": 0, "codes": {}, "boards": []}})
    lines = _status_lines(tmp_path)
    assert lines[0].startswith("Last update partial (") and lines[1].startswith("Stored companies:")  # no line between the two
