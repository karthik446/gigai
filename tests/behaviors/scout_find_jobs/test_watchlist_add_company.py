"""Q1 (v0.1.9, SCOPE-ADD-2): "Add company" by board URL.

One function (``watchlist.add_company_from_url``) behind three surfaces --
``gigai scout watchlist add <url>``, ``POST /api/watchlist`` and the UI's
form -- reusing ``contracts.parse_board_url``/``normalize_url`` as the only
host rule. Each surface is exercised here against a real journaled workpad
(the same ``_fixture`` ``test_watchlist.py`` uses) for the library function, and a real
``gigai setup``/``gigai init`` target (``test_scout_cli._setup_and_init``,
which leaves an ACTIVE gig selected -- the CLI/API resolve the gig
themselves, ``gig_id=None``) for the CLI and the API, the latter through a
real ``ScoutFindJobsBackend``/``serve()`` server in-process. No network.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs.contracts import ATSProvider, SourceKind, WatchlistEntry
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.find_jobs.watchlist import (
    OPERATOR_ADDED_BATCH_ID,
    OPERATOR_ADDED_QUERY_KEY,
    WatchlistUrlError,
    add_company_from_url,
    list_active,
    watchlist_entry_from_url,
)
from tests.behaviors.scout_research.test_scout06_research_inputs import _fixture

from .test_scout_cli import _setup_and_init


def _installed(tmp_path: Path) -> tuple[Path, Path]:
    """``gigai setup`` + ``gigai init`` + ``gigai scout install`` -- the
    install is what binds/approves/activates the Scout gig, which the
    CLI/API's own ``gig_id=None`` resolution needs."""

    home, target = _setup_and_init(tmp_path)
    result = CliRunner().invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code == 0, result.output
    return home, target

KONG_JOB = "https://jobs.ashbyhq.com/kong/ea7b507b-1111-2222-3333-444455556666"


# --- the pure URL -> entry rule ----------------------------------------------


@pytest.mark.parametrize(
    ("url", "provider", "token"),
    [
        ("https://boards.greenhouse.io/acme", ATSProvider.GREENHOUSE, "acme"),
        ("https://boards.greenhouse.io/acme/jobs/101", ATSProvider.GREENHOUSE, "acme"),
        ("https://job-boards.greenhouse.io/acme/jobs/101?gh_src=abc", ATSProvider.GREENHOUSE, "acme"),
        ("https://boards.greenhouse.io/embed/job_board?for=acme", ATSProvider.GREENHOUSE, "acme"),
        ("https://jobs.lever.co/beta", ATSProvider.LEVER, "beta"),
        ("https://jobs.lever.co/beta/0f1e2d3c-aaaa-bbbb-cccc-ddddeeeeffff?lever-source=LinkedIn", ATSProvider.LEVER, "beta"),
        ("https://jobs.ashbyhq.com/kong", ATSProvider.ASHBY, "kong"),
        (KONG_JOB, ATSProvider.ASHBY, "kong"),
        ("  https://JOBS.ASHBYHQ.COM/kong/  ", ATSProvider.ASHBY, "kong"),
    ],
)
def test_each_ats_board_or_job_url_maps_to_its_board(url: str, provider: ATSProvider, token: str) -> None:
    entry = watchlist_entry_from_url(url, observed_at="2026-09-25T00:00:00Z")
    assert entry.provider is provider
    assert entry.board_token == token
    assert entry.company == token
    assert entry.state == "active"
    assert entry.watchlist_id == f"scout_watchlist:{provider.value}:{token}"
    assert entry.first_seen.source_kind is SourceKind.ATS
    assert entry.first_seen.query_key == OPERATOR_ADDED_QUERY_KEY
    assert entry.first_seen.batch_id == OPERATOR_ADDED_BATCH_ID
    assert entry.first_seen.observed_at == "2026-09-25T00:00:00Z"
    # The sealed contract round-trips (so the journal record is valid).
    assert WatchlistEntry.from_json(entry.to_json()) == entry


def test_job_url_and_board_url_of_the_same_board_yield_the_same_identity() -> None:
    from_job = watchlist_entry_from_url(KONG_JOB, observed_at="2026-09-25T00:00:00Z")
    from_board = watchlist_entry_from_url("https://jobs.ashbyhq.com/kong", observed_at="2026-09-25T00:00:00Z")
    assert from_job.watchlist_id == from_board.watchlist_id == "scout_watchlist:ashby:kong"
    # Tracking parameters are stripped from the recorded source URL.
    assert watchlist_entry_from_url(KONG_JOB + "?utm_source=li").first_seen.source_url == KONG_JOB


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/careers",
        "https://www.linkedin.com/jobs/view/123",
        "https://greenhouse.io/acme",
        "https://jobs.ashbyhq.com/",
        "https://boards.greenhouse.io/embed/job_board",
    ],
)
def test_other_hosts_are_refused_with_a_typed_error(url: str) -> None:
    with pytest.raises(WatchlistUrlError) as excinfo:
        watchlist_entry_from_url(url)
    assert excinfo.value.code == "unsupported_board_host"


@pytest.mark.parametrize("url", ["", "   ", "not a url", "ftp://jobs.lever.co/beta", "https://user:pw@jobs.lever.co/beta"])
def test_unusable_urls_are_refused_with_invalid_value(url: str) -> None:
    with pytest.raises(WatchlistUrlError) as excinfo:
        watchlist_entry_from_url(url)
    assert excinfo.value.code == "invalid_value"


# --- the journaled add ---------------------------------------------------------


def test_add_company_from_url_is_idempotent_and_lists_active(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    assert list_active(home, target, gig_id) == ()

    first = add_company_from_url(KONG_JOB, home, target, gig_id)
    again = add_company_from_url("https://jobs.ashbyhq.com/kong", home, target, gig_id)
    assert again == first  # the ORIGINAL entry (its own first_seen), never a second record
    assert list_active(home, target, gig_id) == (first,)

    other = add_company_from_url("https://boards.greenhouse.io/acme/jobs/1", home, target, gig_id)
    assert {item.watchlist_id for item in list_active(home, target, gig_id)} == {first.watchlist_id, other.watchlist_id}


def test_add_company_from_url_refuses_before_touching_the_workpad(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    with pytest.raises(WatchlistUrlError):
        add_company_from_url("https://example.com/jobs", home, target, gig_id)
    assert list_active(home, target, gig_id) == ()


# --- CLI: gigai scout watchlist add ------------------------------------------


def test_cli_watchlist_add_prints_company_and_board_and_is_idempotent(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)
    runner = CliRunner()
    args = ["scout", "watchlist", "add", KONG_JOB, "--home", str(home), "--target", str(target)]

    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    assert "Added kong (ashby board 'kong')." in first.output

    second = runner.invoke(cli, [*args, "--json"])
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "created": False,
        "company": "kong",
        "provider": "ashby",
        "board_token": "kong",
        "watchlist_id": "scout_watchlist:ashby:kong",
        "board_url": KONG_JOB,
    }
    assert [item.watchlist_id for item in list_active(home, target)] == ["scout_watchlist:ashby:kong"]


def test_cli_watchlist_add_rejects_other_hosts(tmp_path: Path) -> None:
    home, target = _installed(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["scout", "watchlist", "add", "https://example.com/jobs", "--home", str(home), "--target", str(target), "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "unsupported_board_host"
    assert list_active(home, target) == ()


# --- API: GET/POST /api/watchlist -----------------------------------------


@pytest.fixture
def running_server(tmp_path: Path):
    home, target = _installed(tmp_path)
    backend = ScoutFindJobsBackend(home_root=home, target=target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=30.0) as client:
            yield client, home, target
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_watchlist_add_then_list(running_server) -> None:
    client, home, target = running_server
    empty = client.get("/api/watchlist")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"schema_version": "scout-watchlist-response:1", "entries": []}

    created = client.post("/api/watchlist", json={"url": KONG_JOB})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["schema_version"] == "scout-watchlist-add-response:1"
    assert body["created"] is True
    assert body["entry"]["watchlist_id"] == "scout_watchlist:ashby:kong"
    assert body["entry"]["company"] == "kong"
    assert body["entry"]["provider"] == "ashby"

    again = client.post("/api/watchlist", json={"url": "https://jobs.ashbyhq.com/kong"})
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    assert again.json()["entry"] == body["entry"]

    listed = client.get("/api/watchlist")
    assert listed.status_code == 200
    assert [item["watchlist_id"] for item in listed.json()["entries"]] == ["scout_watchlist:ashby:kong"]
    # The same record the library API reads back.
    assert [item.watchlist_id for item in list_active(home, target)] == ["scout_watchlist:ashby:kong"]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"url": "https://example.com/jobs"}, "unsupported_board_host"),
        ({"url": "not a url"}, "invalid_value"),
        ({"url": ""}, "invalid_value"),
        ({}, "invalid_value"),
        ({"url": KONG_JOB, "company": "Kong"}, "unknown_key"),
        ([KONG_JOB], "wrong_type"),
    ],
)
def test_api_watchlist_add_rejects_bad_bodies_with_422(running_server, body: object, code: str) -> None:
    client, home, target = running_server
    response = client.post("/api/watchlist", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert list_active(home, target) == ()


def test_api_watchlist_add_is_csrf_guarded(running_server) -> None:
    client, home, target = running_server
    evil = client.post("/api/watchlist", json={"url": KONG_JOB}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403, evil.text
    assert evil.json()["error"]["code"] == "forbidden_origin"
    plain = client.post("/api/watchlist", content=json.dumps({"url": KONG_JOB}), headers={"Content-Type": "text/plain"})
    assert plain.status_code == 415, plain.text
    assert list_active(home, target) == ()
