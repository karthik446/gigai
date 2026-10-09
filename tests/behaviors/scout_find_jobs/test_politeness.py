"""0.1.11.8 review B3, S2, S3: what Scout does when a host pushes back, how fast it may ask, and what it calls itself.

B3: a 429 stops the asking, on a manual update too, for as long as ``Retry-After`` says; a wait longer than the
pass leaves that provider's other boards for the next one. S3: no setting asks a provider faster than its floor.
S2: every client that can reach a board says ``GigAI/<version> (+address)``. Fake hosts only: no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import threading

import httpx
import pytest

from gigai.scout.find_jobs import market_acquisition, sources_update
from gigai.scout.find_jobs.company_index import CompanyIndex
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.sources_update import STATUS_PARTIAL, _BackoffClients, board_cache_for_home, update_sources

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _config, _limits

GREENHOUSE = "boards-api.greenhouse.io"


class _Web:
    """Fake boards: Lever always answers; Greenhouse answers ``refusal`` (a 429) from request ``refuse_from`` on."""

    def __init__(self, refusal: httpx.Response | None, *, refuse_from: int = 1) -> None:
        self.refusal = refusal
        self.refuse_from = refuse_from
        self.lock = threading.Lock()
        self.asked: dict[str, list[str]] = {}
        self.after_first_429: list[str] = []
        self.refused = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        with self.lock:
            seen = self.asked.setdefault(host, [])
            seen.append(str(request.url))
            if host == GREENHOUSE and self.refused:
                self.after_first_429.append(str(request.url))
            if host == GREENHOUSE and self.refusal is not None and len(seen) >= self.refuse_from:
                self.refused += 1
                return httpx.Response(self.refusal.status_code, headers=dict(self.refusal.headers), json={"error": "slow down"})
        return httpx.Response(200, json={"jobs": []} if host == GREENHOUSE else [])

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _boards(greenhouse: int = 6, lever: int = 2) -> list:
    return [_board(ATSProvider.GREENHOUSE, f"g{n}") for n in range(greenhouse)] + [_board(ATSProvider.LEVER, f"l{n}") for n in range(lever)]


def _manual_update(home: Path, web: _Web, *, budget: float | None, concurrency: int = 1) -> dict[str, object]:
    with web.client() as client:
        return update_sources(
            _boards(), cache=board_cache_for_home(home), index=CompanyIndex.for_home(home), client=client, config=_config(),
            limits=_limits(concurrency=concurrency, budget=budget),
        ).snapshot


# --- B3: a 429 storm ----------------------------------------------------------------------------------------------


def test_a_manual_update_stops_asking_a_provider_that_answers_429_with_a_long_retry_after(tmp_path: Path) -> None:
    """The review's case: ``Retry-After: 600`` against a pass with less than that left. Nothing follows the 429."""

    web = _Web(httpx.Response(429, headers={"Retry-After": "600"}))
    snapshot = _manual_update(tmp_path, web, budget=120.0)

    assert snapshot["trigger"] == "manual"
    assert len(web.asked[GREENHOUSE]) == 1 and web.after_first_429 == []  # one request, one 429, then quiet
    assert len(web.asked["api.lever.co"]) == 2  # another provider is not held
    boards = snapshot["boards"]
    assert (boards["failed"], boards["skipped"], boards["fetched"] + boards["cached"]) == (1, 5, 2)  # type: ignore[index]
    assert snapshot["status"] == STATUS_PARTIAL and snapshot["remaining"] == 5  # they lead the next update
    assert snapshot["failures"]["codes"] == {"http_429": 1}  # type: ignore[index]
    assert snapshot["backoff"] == {"greenhouse": {"pauses": 1, "paused_seconds": 0.0, "skipped": 5, "code": "rate_limited"}}
    assert snapshot["cancelled"] is False  # not a stop: a provider asked to be left alone


def test_with_four_workers_only_requests_already_on_the_wire_follow_the_429(tmp_path: Path) -> None:
    web = _Web(httpx.Response(429, headers={"Retry-After": "600"}))
    snapshot = _manual_update(tmp_path, web, budget=120.0, concurrency=4)
    assert len(web.after_first_429) <= 3  # at most the three other workers' requests in flight; never the storm
    assert snapshot["boards"]["skipped"] >= 2 and snapshot["status"] == STATUS_PARTIAL  # type: ignore[index]


def test_a_retry_after_that_fits_the_pass_is_waited_out_then_the_provider_is_asked_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    real = _BackoffClients

    def patient(inner, stop, **kwargs):
        clients = real(inner, stop, **kwargs)
        clients._wait = lambda seconds: (waits.append(round(seconds)), clients._until.clear())  # the wait passes at once
        return clients

    monkeypatch.setattr(sources_update, "_BackoffClients", patient)
    web = _Web(httpx.Response(429, headers={"Retry-After": "45"}))
    web.refuse_from = 10**6
    refused_once = {"done": False}
    plain = web.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == GREENHOUSE and not refused_once["done"]:
            refused_once["done"] = True
            web.asked.setdefault(GREENHOUSE, []).append(str(request.url))
            return httpx.Response(429, headers={"Retry-After": "45"})
        return plain(request)

    web.handler = handler  # type: ignore[method-assign]
    snapshot = _manual_update(tmp_path, web, budget=1200.0)
    assert waits == [45]  # the host's own number, not the 30 s default
    assert len(web.asked[GREENHOUSE]) == 6 and snapshot["boards"]["skipped"] == 0  # type: ignore[index]
    assert snapshot["backoff"] == {"greenhouse": {"pauses": 1, "paused_seconds": 45.0}}


class _Refusing:
    """A board client answering from a script of ``(failure code, Retry-After seconds)``; also the client it is handed."""

    def __init__(self, answers: list[tuple[str | None, float | None]]) -> None:
        self.answers = answers
        self.asked: list[str] = []
        self.failure: str | None = None
        self.retry_after: float | None = None

    def fetch_board(self, client, provider, board_token, config, **kwargs):
        self.asked.append(board_token)
        self.failure, self.retry_after = self.answers.pop(0)
        if self.failure is not None:
            raise RuntimeError("http_error")
        return "ok"

    def last_failure(self) -> str | None:
        return self.failure

    def last_retry_after(self) -> float | None:
        return self.retry_after


def _ask(clients: _BackoffClients, inner: _Refusing, token: str, provider: str = "workable") -> str:
    try:
        return str(clients.fetch_board(inner, provider, token, None))
    except RuntimeError:
        return "refused"
    except market_acquisition._ProviderRateLimited:
        return "rate_limited"


def test_the_pause_is_the_retry_after_capped_at_ten_minutes_and_doubles_only_without_one() -> None:
    now = [0.0]
    waits: list[float] = []

    def wait(seconds: float) -> None:
        waits.append(round(seconds, 3))
        now[0] += seconds

    inner = _Refusing([("http_429", 120.0), ("http_429", 5000.0), ("http_429", None), ("http_429", None), (None, None), ("http_429", 0.0), (None, None)])
    clients = _BackoffClients(inner, None, clock=lambda: now[0], wait=wait)
    for token in "abcdefg":
        _ask(clients, inner, token)
    # 120 s as asked; 5,000 s is read as the 600 s cap; then no header: the streak's doubling (30 * 2**2 = 120, then 240);
    # an answer ends the streak; ``Retry-After: 0`` is no wait at all.
    assert waits == [120.0, 600.0, 120.0, 240.0]
    assert inner.asked == list("abcdefg")
    assert clients.to_json() == {"workable": {"pauses": 5, "paused_seconds": 1080.0}}


def test_a_wait_longer_than_the_pass_has_left_skips_the_providers_other_boards_only() -> None:
    now = [0.0]
    waits: list[float] = []
    inner = _Refusing([(None, None), ("http_429", 600.0), (None, None)])
    clients = _BackoffClients(inner, None, deadline=300.0, clock=lambda: now[0], wait=lambda seconds: waits.append(seconds))

    assert _ask(clients, inner, "a") == "ok"
    assert _ask(clients, inner, "b") == "refused"  # 600 s asked for, 300 s left
    assert [_ask(clients, inner, token) for token in "cde"] == ["rate_limited"] * 3
    assert _ask(clients, inner, "f", provider="lever") == "ok"  # another provider goes on
    assert inner.asked == ["a", "b", "f"] and waits == []  # nothing was sent to the provider, and nobody slept for it
    assert clients.to_json() == {"workable": {"pauses": 1, "paused_seconds": 0.0, "skipped": 3, "code": "rate_limited"}}

    # The same refusal with room in the pass is a wait, not a skip.
    roomy = _Refusing([("http_429", 200.0), (None, None)])
    patient = _BackoffClients(roomy, None, deadline=300.0, clock=lambda: now[0], wait=lambda seconds: now.__setitem__(0, now[0] + seconds))
    assert [_ask(patient, roomy, "a"), _ask(patient, roomy, "b")] == ["refused", "ok"] and now[0] == 200.0


def test_retry_after_is_read_as_seconds_or_as_an_http_date() -> None:
    parse = market_acquisition.parse_retry_after
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    assert parse("600") == 600.0 and parse(" 30 ") == 30.0 and parse("0") == 0.0
    assert parse("86400") == market_acquisition.RETRY_AFTER_MAX_SECONDS == 600.0
    assert parse(format_datetime(now + timedelta(seconds=90), usegmt=True), now=now) == 90.0
    assert parse(format_datetime(now - timedelta(seconds=90), usegmt=True), now=now) == 0.0  # a date already past
    assert parse(format_datetime(now + timedelta(days=2), usegmt=True), now=now) == 600.0
    for nothing in (None, "", "soon", "-5", "1.5e3", 30):
        assert parse(nothing) is None


def test_the_throttled_client_remembers_a_429s_retry_after_per_request() -> None:
    answers = [httpx.Response(429, headers={"Retry-After": "77"}), httpx.Response(429), httpx.Response(200, json=[])]
    client = httpx.Client(transport=httpx.MockTransport(lambda request: answers.pop(0)))
    throttled = market_acquisition._ThrottledClient(client, market_acquisition._RateLimiter(0))
    throttled.get("https://apply.workable.com/api/v1/widget/accounts/acme")
    assert (throttled.last_failure(), throttled.last_retry_after()) == ("http_429", 77.0)
    # The same board asks nothing more of a host that has just refused it (a fallback endpoint, a detail).
    with pytest.raises(httpx.TransportError):
        throttled.get("https://apply.workable.com/api/v1/widget/accounts/acme?details=true")
    assert (throttled.last_failure(), throttled.last_retry_after(), len(answers)) == ("http_429", 77.0, 2)
    throttled.begin_board()
    throttled.get("https://apply.workable.com/api/v1/widget/accounts/globex")
    assert (throttled.last_failure(), throttled.last_retry_after()) == ("http_429", None)
    throttled.begin_board()
    throttled.get("https://apply.workable.com/api/v1/widget/accounts/initech")
    assert (throttled.last_failure(), throttled.last_retry_after()) == (None, None)


# --- S3: the pace never goes under a provider's floor -------------------------------------------------------------


def _paces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, limits=None) -> dict[str, float]:
    """The interval each provider's pool is paced at in one update of a Workable and a Lever board (no request waits)."""

    seen: dict[str, float] = {}
    real = getattr(market_acquisition._RateLimiter, "real", market_acquisition._RateLimiter)

    def recording(min_interval: float, **kwargs):
        seen[["workable", "lever"][len(seen)]] = min_interval
        return real(0, **kwargs)

    recording.real = real  # type: ignore[attr-defined]

    monkeypatch.setattr(market_acquisition, "_RateLimiter", recording)
    boards = [_board(ATSProvider.WORKABLE, "acme"), _board(ATSProvider.LEVER, "acme", catalog=True)]
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"jobs": []} if "workable" in request.url.host else [])))
    update_sources(boards, cache=board_cache_for_home(tmp_path), index=CompanyIndex.for_home(tmp_path), client=client, config=_config(), limits=limits)
    return seen


def test_an_explicit_pace_cannot_ask_workable_faster_than_its_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIGAI_SCOUT_ATS_PROVIDER_FLOORS", raising=False)
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0.125")
    assert _paces(monkeypatch, tmp_path) == {"workable": 2.0, "lever": 0.125}  # max(what the user set, the floor)


def test_a_slower_explicit_pace_is_kept_and_the_test_seam_alone_drops_the_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIGAI_SCOUT_ATS_PROVIDER_FLOORS", raising=False)
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "5")
    assert _paces(monkeypatch, tmp_path / "slow") == {"workable": 5.0, "lever": 5.0}
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("GIGAI_SCOUT_ATS_PROVIDER_FLOORS", "0")
    assert _paces(monkeypatch, tmp_path / "smoke") == {"workable": 0.0, "lever": 0.0}  # the core-flow smoke's fake boards


def test_provider_interval_and_the_floor_table() -> None:
    from gigai.scout.find_jobs import providers

    pace = market_acquisition.provider_interval
    assert providers.spec("workable").min_interval_seconds == 2.0  # half the rate its host refused the seed at
    assert [name for name in providers.provider_names() if providers.spec(name).min_interval_seconds is not None] == ["workable"]
    assert pace("workable", 0.125, {}) == 2.0 and pace("workable", 0.4, {}) == 2.0 and pace("workable", 3.0, {}) == 3.0
    assert pace("greenhouse", 0.125, {}) == 0.125 and pace("page:careers.example.com", 0.125, {}) == 0.125
    assert pace("workable", 0.0, {"GIGAI_SCOUT_ATS_PROVIDER_FLOORS": "0"}) == 0.0
    assert pace("workable", 0.0, {"GIGAI_SCOUT_ATS_PROVIDER_FLOORS": "1"}) == 2.0


def test_the_liveness_check_paces_workable_at_its_floor_too(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from gigai.scout.find_jobs import posting_live

    monkeypatch.delenv("GIGAI_SCOUT_ATS_PROVIDER_FLOORS", raising=False)
    monkeypatch.setenv("GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS", "0")
    posting_live.reset_memory()
    posting_live._paced("workable")
    posting_live._paced("lever")
    assert 1.5 < posting_live._NEXT_REQUEST["workable"] - time.monotonic() <= 2.0
    assert posting_live._NEXT_REQUEST["lever"] - time.monotonic() <= 0.0
    posting_live.reset_memory()


# --- S2: one User-Agent, on every client that can reach a board ---------------------------------------------------


def test_the_user_agent_is_the_name_the_version_and_the_address() -> None:
    import importlib.metadata
    import re

    from gigai.scout.find_jobs import board_headers, robots_guard

    agent = board_headers.user_agent()
    assert agent == f"GigAI/{importlib.metadata.version('gigai')} (+https://github.com/karthik446/gigai)"
    assert re.fullmatch(r"GigAI/\S+ \(\+https://github\.com/karthik446/gigai\)", agent)
    assert board_headers.board_headers() == {"User-Agent": agent}
    assert robots_guard.AGENT_TOKEN == board_headers.PRODUCT_TOKEN == agent.split("/", 1)[0]  # the robots group it reads


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Every request any ``httpx.Client`` built in the test would put on the network, answered 404 instead."""

    sent: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(404, json={})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", raising=False)
    return sent


def _agents(sent: list[httpx.Request]) -> set[str]:
    from gigai.scout.find_jobs.board_headers import user_agent

    assert sent, "the client sent nothing"
    agents = {request.headers.get("user-agent", "") for request in sent}
    assert agents == {user_agent()}, agents
    return agents


def test_the_update_and_find_jobs_client_says_who_it_is(wire: list[httpx.Request]) -> None:
    from gigai.scout.find_jobs import bindings

    with bindings._http_client() as client:
        client.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    with sources_update.default_http_client() as client:
        client.get("https://api.lever.co/v0/postings/acme")
    assert len(wire) == 2 and _agents(wire)


def test_the_liveness_client_says_who_it_is(wire: list[httpx.Request]) -> None:
    from gigai.scout.find_jobs import posting_live

    with posting_live.liveness_client() as client:
        client.get("https://api.ashbyhq.com/posting-api/job-board/acme")
    _agents(wire)


def test_the_description_lookup_client_says_who_it_is(wire: list[httpx.Request]) -> None:
    from gigai.scout.find_jobs import job_input

    with job_input.job_fetch_client() as client:
        client.get("https://apply.workable.com/api/v1/widget/accounts/acme")
    _agents(wire)


def test_the_exa_search_goes_out_under_the_same_name(wire: list[httpx.Request], monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs import bindings
    from gigai.scout.find_jobs.exa_client import EXA_SEARCH_URL, ExaClientError, ExaSearchClient

    monkeypatch.setenv("EXA_API_KEY", "test-key-not-real")
    with bindings._http_client() as client, pytest.raises(ExaClientError):
        ExaSearchClient().search(client, _config(exa=True))
    assert [str(request.url) for request in wire] == [EXA_SEARCH_URL] and _agents(wire)


def test_discoverys_board_clients_say_who_they_are(wire: list[httpx.Request], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs import discovery
    from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs
    from gigai.scout.find_jobs.discovery.types import Candidate, SourceRunOutcome

    candidate = Candidate(
        company="Acme", careers_url="https://boards.greenhouse.io/acme", ats_provider="greenhouse", sponsorship="unknown",
        sponsorship_evidence="", source_url="https://example.test/acme", found_by="h1b",
    )

    def h1b(*, client: httpx.Client, **kwargs: object) -> SourceRunOutcome:
        client.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
        return SourceRunOutcome(name="h1b", runs=1, cost_usd=0.0, candidates=(candidate,))

    def merge(*, client: httpx.Client, **kwargs: object):
        client.get("https://api.lever.co/v0/postings/acme")
        return [], {}

    monkeypatch.setattr(discovery.openai_source, "run", lambda **kwargs: SourceRunOutcome(name="openai_web_search", runs=1, cost_usd=0.0, candidates=()))
    monkeypatch.setattr(discovery.h1b_source, "run", h1b)
    monkeypatch.setattr(discovery, "merge_and_verify", merge)
    monkeypatch.setattr(discovery, "add_usable_boards_to_watchlist", lambda **kwargs: [])
    monkeypatch.setattr(discovery, "_exclusion_set", lambda *args, **kwargs: set())
    monkeypatch.setattr(discovery, "_save_result", lambda *args, **kwargs: None)
    discovery.run_discovery(home_root=tmp_path, target=tmp_path, prefs=DiscoveryPrefs(visa_sponsorship_required=True, budget_usd_per_session=5.0), runs=1)
    assert len(wire) == 2 and _agents(wire)
