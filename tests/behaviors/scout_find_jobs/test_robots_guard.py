"""0.1.11.8 review S1: the robots guard reads the rules a host really publishes, and every request path asks it.

``*`` and ``$`` with longest-match, a byte-order mark, a file that could not be read (not the same as no file), a
redirect, a large file, one request per host however many workers ask, the DETAIL URL, ``Crawl-delay``, and the
single-posting clients. Every host here is a fake behind ``httpx.MockTransport``: no network.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs import robots_guard
from gigai.scout.find_jobs.ats_board_clients import ATSBoardClientError, ATSBoardClients, BoardCache
from gigai.scout.find_jobs.contracts import ATSProvider, FindJobsConfig, SourceToggles
from gigai.scout.find_jobs.robots_guard import ROBOTS_DISALLOWED, RobotsGuard

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _limits

HOST = "acme.recruitee.com"
FEED = f"https://{HOST}/api/offers/"


def _config() -> FindJobsConfig:
    return FindJobsConfig(
        roles=("software engineer",), merged_queries=("software engineer",), location=None, remote=True,
        published_after=None, sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
    )


def _client(robots, feed=None) -> tuple[httpx.Client, list[str]]:
    """A fake web: ``robots(request)`` answers every ``/robots.txt``, ``feed(request)`` (default: an empty list) the rest."""

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return robots(request) if callable(robots) else robots
        return feed(request) if feed is not None else httpx.Response(200, json=[])

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def _rules(text: str, **kwargs) -> httpx.Response:
    return httpx.Response(200, text=text, **kwargs)


def _allows(text: str, url: str) -> bool:
    client, _calls = _client(_rules(text))
    return RobotsGuard().allows(client, url)


# --- the matcher ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rules", "url", "allowed"),
    [
        # The three shapes the review found allowed (S1.1): a star inside the path, a query pattern, UKG's leading star.
        ("User-agent: *\nDisallow: /api/*/widget/\n", "https://apply.workable.com/api/v1/widget/accounts/acme", False),
        ("User-agent: *\nDisallow: /*?details=\n", "https://apply.workable.com/api/v1/widget/accounts/acme?details=true", False),
        ("User-agent: *\nDisallow: */accounts\n", "https://apply.workable.com/api/v1/widget/accounts/acme", False),
        ("User-agent: *\nDisallow: /api/*/widget/\n", "https://apply.workable.com/api/widget/", True),
        # ``$`` ends the match: only the exact path.
        ("User-agent: *\nDisallow: /postings.json$\n", "https://acme.pinpointhq.com/postings.json", False),
        ("User-agent: *\nDisallow: /postings.json$\n", "https://acme.pinpointhq.com/postings.json?page=2", True),
        ("User-agent: *\nDisallow: /*.json$\n", "https://acme.pinpointhq.com/postings.json", False),
        # The longest matching pattern decides; Allow wins a tie.
        ("User-agent: *\nDisallow: /api/\nAllow: /api/offers/\n", FEED, True),
        ("User-agent: *\nAllow: /api/\nDisallow: /api/offers/\n", FEED, False),
        ("User-agent: *\nDisallow: /api/offers/\nAllow: /api/offers/\n", FEED, True),
        ("User-agent: *\nDisallow: /\nAllow: /api/*/\n", FEED, True),
        # An empty Disallow disallows nothing; a comment and odd spacing are read through.
        ("User-agent: *\nDisallow:\n", FEED, True),
        ("  user-agent :  *   # everyone\n  disallow :  /api/   # the feed\n", FEED, False),
        # Percent-escapes compare in one spelling.
        ("User-agent: *\nDisallow: /a%2fb\n", "https://acme.recruitee.com/a%2Fb", False),
    ],
)
def test_wildcards_end_anchors_and_the_longest_match(rules: str, url: str, allowed: bool) -> None:
    assert _allows(rules, url) is allowed


def test_the_gigai_group_is_read_when_there_is_one_else_the_star_group() -> None:
    both = "User-agent: *\nDisallow: /\n\nUser-agent: GigAI\nAllow: /api/offers/\nDisallow: /api/\n"
    assert _allows(both, FEED) is True  # GigAI's own group, not the star group's "nothing"
    assert _allows(both, f"https://{HOST}/api/other") is False
    assert _allows("User-agent: gigai/1.0\nDisallow: /api/\n\nUser-agent: *\nAllow: /\n", FEED) is False  # the token, any case
    assert _allows("User-agent: SomeBot\nDisallow: /\n\nUser-agent: *\nDisallow: /private/\n", FEED) is True
    # Two agents sharing one group, and one agent named by two groups.
    assert _allows("User-agent: SomeBot\nUser-agent: *\nDisallow: /api/\n", FEED) is False
    assert _allows("User-agent: *\nDisallow: /x/\n\nUser-agent: *\nDisallow: /api/\n", FEED) is False
    assert _allows("User-agent: SomeBot\nDisallow: /\n", FEED) is True  # no group for us: no rules


def test_a_byte_order_mark_does_not_hide_the_first_group() -> None:
    client, _calls = _client(httpx.Response(200, content=b"\xef\xbb\xbfUser-agent: *\nDisallow: /api/\n"))
    assert RobotsGuard().allows(client, FEED) is False


def test_a_html_content_type_and_a_bad_byte_do_not_discard_rules_that_parse() -> None:
    client, _calls = _client(httpx.Response(200, content=b"# caf\xe9\nUser-agent: *\nDisallow: /api/\n", headers={"content-type": "text/html"}))
    assert RobotsGuard().allows(client, FEED) is False


def test_a_large_file_is_read_up_to_the_limit_not_thrown_away() -> None:
    head = "User-agent: *\nDisallow: /api/\n"
    filler = "# padding\n" * (robots_guard.MAX_BODY_BYTES // 10 + 10)
    client, _calls = _client(_rules(head + filler + "Allow: /api/offers/\n"))
    guard = RobotsGuard()
    assert guard.allows(client, FEED) is False  # the rule in the first 512 KiB counts; the Allow past it is not read
    assert len(guard._rules[HOST].body or "") <= robots_guard.MAX_BODY_BYTES


# --- what the host's answer means ---------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", [httpx.Response(503), httpx.Response(500), httpx.Response(429, headers={"retry-after": "600"}), "timeout"])
def test_a_file_that_could_not_be_read_is_not_known_and_the_host_is_left_alone_for_an_hour(answer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"answer": answer}

    def robots(request: httpx.Request) -> httpx.Response:
        if state["answer"] == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return state["answer"]

    client, calls = _client(robots)
    guard = RobotsGuard(tmp_path / "robots")
    assert guard.allows(client, FEED) is False
    with pytest.raises(ATSBoardClientError) as excinfo:
        guard.check(client, FEED, "recruitee", "acme")
    assert excinfo.value.code == robots_guard.ROBOTS_UNKNOWN
    assert calls == [f"https://{HOST}/robots.txt"]  # asked once, the feed never

    # Within the hour nobody asks again, not even a new process (the answer is on disk).
    assert RobotsGuard(tmp_path / "robots").allows(client, FEED) is False
    assert len(calls) == 1
    # After the hour the file is asked for again, and a host that now answers is read.
    state["answer"] = _rules("User-agent: *\nAllow: /\n")
    later = time.time() + robots_guard.UNKNOWN_TTL_SECONDS + 1
    monkeypatch.setattr(robots_guard.time, "time", lambda: later)
    assert guard.allows(client, FEED) is True
    assert calls.count(f"https://{HOST}/robots.txt") == 2


@pytest.mark.parametrize("status", [404, 410, 401, 403])
def test_no_file_and_a_refused_file_are_no_rules_for_a_day(status: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls = _client(httpx.Response(status))
    guard = RobotsGuard(tmp_path / "robots")
    assert guard.allows(client, FEED) is True
    later = time.time() + robots_guard.UNKNOWN_TTL_SECONDS + 60  # past the "not known" hour: still held
    monkeypatch.setattr(robots_guard.time, "time", lambda: later)
    assert guard.allows(client, FEED) is True and calls.count(f"https://{HOST}/robots.txt") == 1
    later = time.time() + robots_guard.TTL_SECONDS + 1
    assert guard.allows(client, FEED) is True and calls.count(f"https://{HOST}/robots.txt") == 2


def test_same_site_redirects_are_followed_and_the_rules_at_the_end_apply() -> None:
    def robots(request: httpx.Request) -> httpx.Response:
        if request.url.host == HOST:
            return httpx.Response(301, headers={"location": "https://www.recruitee.com/robots.txt"})
        return _rules("User-agent: *\nDisallow: /api/\n")

    client, calls = _client(robots)
    guard = RobotsGuard()
    assert guard.allows(client, FEED) is False
    assert calls == [f"https://{HOST}/robots.txt", "https://www.recruitee.com/robots.txt"]
    assert guard.requests == 2  # both hops are requests


def test_a_redirect_off_the_site_or_a_chain_over_five_is_a_host_with_no_rules() -> None:
    away, away_calls = _client(httpx.Response(302, headers={"location": "https://elsewhere.example/robots.txt"}))
    assert RobotsGuard().allows(away, FEED) is True and away_calls == [f"https://{HOST}/robots.txt"]

    def loop(request: httpx.Request) -> httpx.Response:
        hop = int(request.url.params.get("hop", "0"))
        return httpx.Response(302, headers={"location": f"/robots.txt?hop={hop + 1}"})

    looping, loop_calls = _client(loop)
    assert RobotsGuard().allows(looping, FEED) is True
    assert len(loop_calls) == robots_guard.MAX_REDIRECTS + 1  # the first request and five redirects, then it gives up


def test_four_workers_on_a_cold_host_make_one_robots_request() -> None:
    gate = threading.Event()

    def robots(request: httpx.Request) -> httpx.Response:
        gate.wait(2)
        return _rules("User-agent: *\nDisallow: /private/\n")

    client, calls = _client(robots)
    guard = RobotsGuard()
    answers: list[bool] = []
    workers = [threading.Thread(target=lambda: answers.append(guard.allows(client, FEED))) for _ in range(4)]
    for worker in workers:
        worker.start()
    time.sleep(0.05)
    gate.set()
    for worker in workers:
        worker.join(5)
    assert answers == [True] * 4
    assert calls == [f"https://{HOST}/robots.txt"]


# --- every request a board makes ------------------------------------------------------------------------------------

RIPPLING_LIST = {
    "items": [{"id": "job-1", "name": "Software Engineer", "url": "https://ats.rippling.com/acme/jobs/job-1", "locations": [{"name": "Remote", "countryCode": "US"}]}],
    "totalItems": 1,
}


def test_a_disallowed_detail_path_is_never_requested_and_the_list_still_is(tmp_path: Path) -> None:
    def feed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/board/acme/jobs":
            return httpx.Response(200, json=RIPPLING_LIST)
        return httpx.Response(200, json={"uuid": "job-1", "name": "Software Engineer", "description": {"role": "<p>Build.</p>"}})

    client, calls = _client(_rules("User-agent: *\nDisallow: /api/v1/\n"), feed)
    clients = ATSBoardClients(robots=RobotsGuard())
    result = clients.fetch_board(client, "rippling", "acme", _config(), cache=BoardCache(tmp_path / "ats-boards"), descriptions=True, title_filter=lambda _title: True)
    assert calls == ["https://ats.rippling.com/robots.txt", "https://ats.rippling.com/api/v2/board/acme/jobs?page=0&pageSize=1000"]
    assert result.stats.detail_fetched == 0 and result.stats.detail_failed >= 1
    assert clients.robots_tally.value == 1  # the robots request is counted for whoever asked


def test_a_disallowed_greenhouse_detail_is_not_requested(tmp_path: Path) -> None:
    def feed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/boards/acme/jobs":
            return httpx.Response(200, json={"jobs": [{"id": 7, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/7", "updated_at": "2026-10-01T00:00:00Z", "location": {"name": "Remote"}}]})
        return httpx.Response(200, json={"id": 7, "title": "Software Engineer", "content": "<p>Build.</p>"})

    client, calls = _client(_rules("User-agent: *\nDisallow: /v1/boards/*/jobs/\n"), feed)
    try:
        ATSBoardClients(robots=RobotsGuard()).fetch_board(client, "greenhouse", "acme", _config(), cache=BoardCache(tmp_path / "ats-boards"))
    except ATSBoardClientError as exc:
        assert exc.code == ROBOTS_DISALLOWED
    assert [url for url in calls if "/jobs/7" in url] == []  # the detail was never asked
    assert any(url.startswith("https://boards-api.greenhouse.io/v1/boards/acme/jobs?") or url.endswith("/v1/boards/acme/jobs") for url in calls)


def _empty_board(request: httpx.Request) -> httpx.Response:
    """An empty board in the shape its system sends."""

    return httpx.Response(200, json={"offers": []} if request.url.host.endswith(".recruitee.com") else [])


def test_the_update_counts_its_robots_requests(tmp_path: Path) -> None:
    from gigai.scout.find_jobs.company_index import CompanyIndex
    from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources

    client, calls = _client(httpx.Response(404), _empty_board)
    boards = [_board(ATSProvider.LEVER, "acme"), _board(ATSProvider.LEVER, "globex"), _board(ATSProvider.RECRUITEE, "initech")]
    result = update_sources(
        boards, cache=board_cache_for_home(tmp_path), index=CompanyIndex.for_home(tmp_path), client=client,
        limits=_limits(concurrency=2), ats=ATSBoardClients(robots=RobotsGuard(tmp_path / "robots")),
    ).snapshot
    robots = [url for url in calls if url.endswith("/robots.txt")]
    assert sorted(robots) == ["https://api.lever.co/robots.txt", "https://initech.recruitee.com/robots.txt"]  # one per host
    assert result["robots_requests"] == 2
    assert result["requests"] == len(calls) == 5  # three lists and the two robots files


def test_an_unreadable_robots_file_fails_the_board_with_its_own_code_in_the_update(tmp_path: Path) -> None:
    from gigai.scout.find_jobs.company_index import CompanyIndex
    from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources

    def robots(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503) if request.url.host == "api.lever.co" else httpx.Response(404)

    client, calls = _client(robots, _empty_board)
    boards = [_board(ATSProvider.LEVER, "acme"), _board(ATSProvider.LEVER, "globex"), _board(ATSProvider.RECRUITEE, "initech")]
    result = update_sources(
        boards, cache=board_cache_for_home(tmp_path), index=CompanyIndex.for_home(tmp_path), client=client,
        limits=_limits(concurrency=1), ats=ATSBoardClients(robots=RobotsGuard()),
    ).snapshot
    # Lever's boards share one host: the first board meets the unreadable file, the second is left at once for the next
    # update (``robots_unknown``, skipped, not failed). Recruitee's tenants have their own host and fail one by one.
    assert result["failures"]["codes"] == {robots_guard.ROBOTS_UNKNOWN: 1}
    assert result["boards"]["skipped"] == 1
    assert result["backoff"] == {"lever": {"pauses": 0, "paused_seconds": 0.0, "skipped": 1, "code": "robots_unknown"}}
    assert [url for url in calls if "api.lever.co" in url] == ["https://api.lever.co/robots.txt"]  # no Lever list was asked
    assert "https://initech.recruitee.com/api/offers/" in calls


# --- Crawl-delay ------------------------------------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.mark.parametrize(("rules", "expected"), [
    ("User-agent: *\nCrawl-delay: 1\nDisallow: /private/\n", 1.0),
    ("User-agent: *\nCrawl-delay: 60\n", 10.0),  # capped at MAX_CRAWL_DELAY_SECONDS
    ("User-agent: *\nCrawl-delay: 5\n\nUser-agent: GigAI\nCrawl-delay: 2\n", 2.0),  # our group's own
    ("User-agent: *\nCrawl-delay: soon\n", 0.0),
    ("User-agent: *\nDisallow: /private/\n", 0.0),
])
def test_a_crawl_delay_is_the_hosts_pace(rules: str, expected: float) -> None:
    assert robots_guard.MAX_CRAWL_DELAY_SECONDS == 10.0
    clock = _Clock()
    client, calls = _client(_rules(rules))
    guard = RobotsGuard(clock=clock, sleep=clock.sleep)
    guarded = robots_guard.GuardedClient(client, guard, "lever", "acme")
    guarded.get("https://api.lever.co/v0/postings/acme?mode=json")
    guarded.get("https://api.lever.co/v0/postings/globex?mode=json")
    guarded.get("https://api.lever.co/v0/postings/initech?mode=json")
    # The first list waits out the delay after the robots request itself; each later one after the request before it.
    assert round(sum(clock.slept), 3) == expected * 3
    assert guard.crawl_delay("https://api.lever.co/x") == (expected or None)
    assert len(calls) == 4


def test_the_updates_throttled_client_keeps_the_hosts_crawl_delay_once(tmp_path: Path) -> None:
    """Lever's own ``Crawl-delay: 1`` slows the Lever pool to it although the provider interval is 0."""

    from gigai.scout.find_jobs.company_index import CompanyIndex
    from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources

    clock = _Clock()
    client, calls = _client(_rules("User-agent: *\nCrawl-delay: 1\n"), _empty_board)
    guard = RobotsGuard(clock=clock, sleep=clock.sleep)
    boards = [_board(ATSProvider.LEVER, token) for token in ("acme", "globex", "initech")]
    result = update_sources(
        boards, cache=board_cache_for_home(tmp_path), index=CompanyIndex.for_home(tmp_path), client=client,
        limits=_limits(concurrency=1), ats=ATSBoardClients(robots=guard),
    )
    assert result.snapshot["boards"]["fetched"] == 3
    assert round(sum(clock.slept), 3) == 3.0  # one second before each of the three lists, and not twice


# --- the single-posting clients ---------------------------------------------------------------------------------------


def test_an_installed_guard_asks_before_a_board_host_and_never_about_a_company_site() -> None:
    client, calls = _client(_rules("User-agent: *\nDisallow: /v0/\n"))
    assert robots_guard.install(client, None) is client and client.event_hooks["request"] == []
    tally = robots_guard.RequestTally()
    robots_guard.install(client, RobotsGuard(), counted=tally.add)

    with pytest.raises(httpx.RequestError) as excinfo:
        client.get("https://api.lever.co/v0/postings/acme?mode=json")
    assert excinfo.value.code == ROBOTS_DISALLOWED  # type: ignore[attr-defined]
    assert calls == ["https://api.lever.co/robots.txt"] and tally.value == 1
    assert client.get("https://jobs.lever.co/acme/1").status_code == 200  # another host of the provider: its own file, which this fake also disallows /v0/ on
    assert client.get("https://careers.example.com/jobs/1").status_code == 200
    assert "https://careers.example.com/robots.txt" not in calls  # a company's own page is not a board


def test_the_liveness_check_does_not_ask_a_board_that_disallows_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs import posting_live

    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("GIGAI_SCOUT_POSTING_LIVENESS", "1")
    posting_live.reset_memory()
    client, calls = _client(_rules("User-agent: *\nDisallow: /v0/postings/\n"))
    robots_guard.install(client, RobotsGuard())
    answer = posting_live.check_posting_live(tmp_path, url="https://jobs.lever.co/acme/abc", provider="lever", token="acme", posting_id="abc", client=client)
    assert answer.state == posting_live.UNKNOWN  # never "closed" from a request that was not made
    assert calls == ["https://api.lever.co/robots.txt"]


def test_the_shared_guard_keeps_the_process_guard_for_a_caller_without_a_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(robots_guard, "_SHARED", None)
    assert robots_guard.shared_guard(None, {"GIGAI_SCOUT_ROBOTS": "0"}) is None
    on_disk = robots_guard.shared_guard(tmp_path / "ats-boards", {})
    assert robots_guard.shared_guard(None, {}) is on_disk and on_disk.cache_dir == tmp_path / "robots"
    monkeypatch.setattr(robots_guard, "_SHARED", None)
    assert robots_guard.shared_guard(None, {}).cache_dir is None
    monkeypatch.setattr(robots_guard, "_SHARED", None)
