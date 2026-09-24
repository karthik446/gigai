from __future__ import annotations

import gzip
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    ATSProvider,
    FindJobsConfig,
    NodeContext,
    PostingRow,
    SelectionRule,
    SourceKind,
    SourceToggles,
    SponsorshipStatus,
    WatchlistEntry,
    WatchlistFirstSeen,
)
from gigai.scout.find_jobs.exa_client import EXA_API_KEY_ENV_VAR, ExaClientError, ExaSearchClient
from gigai.scout.find_jobs.market_acquisition import AcquireAllSourcesFailedError, acquire_node


FIXTURES = Path(__file__).parent / "fixtures"


def _context(tmp_path: Path, key: str = "acquire-001") -> NodeContext:
    return NodeContext(
        run_id="run_01", project_id="project_01", gig_id="gig_01",
        graph_id="find-jobs:functional", graph_version=1, goal_slug="acquire",
        manifest_digest="sha256:" + "a" * 64, operation_key=key,
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(tmp_path), redeemed_consent_ref="consent",
        model_target="ollama_local",
    )


def _config(*, exa: bool = True, ats: bool = True) -> FindJobsConfig:
    payload = json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text())
    config = FindJobsConfig.from_json(payload)
    return replace(config, sources=SourceToggles(exa=exa, ats=ats, hiringcafe=False))


def _input(rows=(), *, config: FindJobsConfig | None = None):
    return AcquireInput(
        config or _config(), "sha256:" + "c" * 64, None,
        tuple(rows), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH,
    )


class _Watchlist:
    def __init__(self):
        self.added = []

    def add_to_watchlist(self, entry):
        # Idempotent by watchlist_id, matching JournalWatchlistClient's real
        # dedupe-by-key behavior (a re-discovered board is a no-op, not a
        # second entry).
        if not any(existing.watchlist_id == entry.watchlist_id for existing in self.added):
            self.added.append(entry)
        return entry

    def active_entries(self):
        return tuple(self.added)


class _Exa:
    def __init__(self, rows):
        self.rows = rows

    def search(self, client, config):
        return tuple(self.rows)


class _ATS:
    def list_board(self, client, provider, board_token, config):
        return ()


class _FailingExa:
    def __init__(self, exc: Exception):
        self.exc = exc

    def search(self, client, config):
        raise self.exc


class _FailingATS:
    def __init__(self, exc: Exception):
        self.exc = exc

    def list_board(self, client, provider, board_token, config):
        raise self.exc


class _ATS_ReturningRow:
    def __init__(self, row: PostingRow):
        self.row = row

    def list_board(self, client, provider, board_token, config):
        return (self.row,)


def test_supplied_rows_select_and_persist(monkeypatch, tmp_path):
    payload = json.loads((FIXTURES / "fixture-acquire-input-v1.json").read_text())
    row = PostingRow.from_json(payload["rows"][0])
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    out = acquire_node(_context(tmp_path), _input([row]), http_client=None,
                       exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist())
    assert out.rows[0].outcome.value == "new"
    assert len(out.selected_postings) == 1


def test_live_exa_auto_adds_watchlist(monkeypatch, tmp_path):
    payload = json.loads((FIXTURES / "fixture-acquire-input-v1.json").read_text())
    row = PostingRow.from_json(payload["rows"][0])
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    watchlist = _Watchlist()
    out = acquire_node(_context(tmp_path, "acquire-002"), _input(), http_client=None,
                       exa=_Exa([row]), ats=_ATS(), watchlist=watchlist)
    assert out.watchlist_refs == ("scout_watchlist:greenhouse:acme",)
    assert watchlist.added[0].first_seen.source_kind.value == "exa"


def test_all_exa_fail_with_only_exa_enabled_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gigai.scout.find_jobs.market_acquisition.import_public_rows",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not write a batch when every source failed")),
    )
    config = _config(exa=True, ats=False)
    with pytest.raises(AcquireAllSourcesFailedError) as exc_info:
        acquire_node(
            _context(tmp_path, "acquire-003"),
            _input(config=config),
            http_client=None,
            exa=_FailingExa(RuntimeError("exa transport exploded")),
            ats=_ATS(),
            watchlist=_Watchlist(),
        )
    assert exc_info.value.code == "acquire_all_sources_failed"
    assert "exa transport exploded" not in str(exc_info.value)


def test_exa_fails_ats_succeeds_returns_rows_and_failure(monkeypatch, tmp_path):
    payload = json.loads((FIXTURES / "fixture-acquire-input-v1.json").read_text())
    row = PostingRow.from_json(payload["rows"][0])
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    watchlist = _Watchlist()
    watchlist.added.append(
        WatchlistEntry(
            watchlist_id="scout_watchlist:greenhouse:acme",
            provider=ATSProvider.GREENHOUSE,
            board_token="acme",
            company="Acme",
            state="active",
            first_seen=WatchlistFirstSeen(SourceKind.ATS, row.url, row.query_key, "acquire-004", "2026-09-22T00:00:00Z"),
        )
    )
    config = _config(exa=True, ats=True)
    out = acquire_node(
        _context(tmp_path, "acquire-004"),
        _input(config=config),
        http_client=None,
        exa=_FailingExa(RuntimeError("exa transport exploded")),
        ats=_ATS_ReturningRow(row),
        watchlist=watchlist,
    )
    assert out.rows and out.rows[0].posting.normalized_url == row.normalized_url
    assert any(failure.source_kind is SourceKind.EXA for failure in out.failures)


# --- U20: Exa vs ATS dedupe, ATS wins ------------------------------------


def test_ats_row_wins_over_exa_row_same_normalized_url(monkeypatch, tmp_path):
    exa_row = PostingRow(
        url="https://job-boards.greenhouse.io/acme/jobs/500",
        normalized_url="https://job-boards.greenhouse.io/acme/jobs/500",
        provider=ATSProvider.GREENHOUSE, board_token="acme", company="acme",
        title="Job Application for Staff Engineer at Acme", location="",
        published_at=None, content_sha256=None, source_kind=SourceKind.EXA,
        query_key="staff engineer",
    )
    ats_row = PostingRow(
        url="https://job-boards.greenhouse.io/acme/jobs/500",
        normalized_url="https://job-boards.greenhouse.io/acme/jobs/500",
        provider=ATSProvider.GREENHOUSE, board_token="acme", company="Acme Corp",
        title="Staff Software Engineer", location="Denver, CO",
        published_at="2026-09-20T00:00:00Z", content_sha256="sha256:" + "d" * 64,
        source_kind=SourceKind.ATS, query_key="ats:greenhouse:acme", text="Full posting body.",
    )
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    watchlist = _Watchlist()
    watchlist.added.append(
        WatchlistEntry(
            watchlist_id="scout_watchlist:greenhouse:acme", provider=ATSProvider.GREENHOUSE,
            board_token="acme", company="acme", state="active",
            first_seen=WatchlistFirstSeen(SourceKind.EXA, exa_row.url, exa_row.query_key, "acquire-dedupe-1", "2026-09-22T00:00:00Z"),
        )
    )
    out = acquire_node(
        _context(tmp_path, "acquire-dedupe-1"), _input(config=_config(exa=True, ats=True)),
        http_client=None, exa=_Exa([exa_row]), ats=_ATS_ReturningRow(ats_row), watchlist=watchlist,
    )
    assert len(out.rows) == 1
    winner = out.rows[0].posting
    assert winner.source_kind is SourceKind.ATS
    assert winner.title == "Staff Software Engineer"
    assert winner.location == "Denver, CO"
    assert winner.text == "Full posting body."


def test_ats_row_wins_over_exa_row_same_job_id_different_domain(monkeypatch, tmp_path):
    # Same Greenhouse job (id 7683977 on the "pinterest" board), reached via
    # two different URLs: Exa found the board-subdomain URL, ATS enriched it
    # via the custom career-site domain that proxies the same board -- a
    # real shape seen in live UAT data (v0.1.8-uat.md U20).
    exa_row = PostingRow(
        url="https://job-boards.greenhouse.io/pinterest/jobs/7683977?gh_src=remote-work.app",
        normalized_url="https://job-boards.greenhouse.io/pinterest/jobs/7683977",
        provider=ATSProvider.GREENHOUSE, board_token="pinterest", company="pinterest",
        title="Job Application for Staff Engineer at Pinterest", location="",
        published_at=None, content_sha256=None, source_kind=SourceKind.EXA,
        query_key="staff engineer",
    )
    ats_row = PostingRow(
        url="https://www.pinterestcareers.com/jobs/?gh_jid=7683977",
        normalized_url="https://www.pinterestcareers.com/jobs?gh_jid=7683977",
        provider=ATSProvider.GREENHOUSE, board_token="pinterest", company="Pinterest",
        title="Staff Software Engineer, Backend", location="San Francisco, CA - US",
        published_at="2026-09-20T00:00:00Z", content_sha256="sha256:" + "e" * 64,
        source_kind=SourceKind.ATS, query_key="ats:greenhouse:pinterest", text="Full posting body.",
    )
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    watchlist = _Watchlist()
    watchlist.added.append(
        WatchlistEntry(
            watchlist_id="scout_watchlist:greenhouse:pinterest", provider=ATSProvider.GREENHOUSE,
            board_token="pinterest", company="pinterest", state="active",
            first_seen=WatchlistFirstSeen(SourceKind.EXA, exa_row.url, exa_row.query_key, "acquire-dedupe-2", "2026-09-22T00:00:00Z"),
        )
    )
    out = acquire_node(
        _context(tmp_path, "acquire-dedupe-2"), _input(config=_config(exa=True, ats=True)),
        http_client=None, exa=_Exa([exa_row]), ats=_ATS_ReturningRow(ats_row), watchlist=watchlist,
    )
    assert len(out.rows) == 1
    winner = out.rows[0].posting
    assert winner.source_kind is SourceKind.ATS
    assert winner.title == "Staff Software Engineer, Backend"


def test_exa_only_row_kept_when_no_matching_ats_row(monkeypatch, tmp_path):
    exa_row = PostingRow(
        url="https://job-boards.greenhouse.io/acme/jobs/999",
        normalized_url="https://job-boards.greenhouse.io/acme/jobs/999",
        provider=ATSProvider.GREENHOUSE, board_token="acme", company="acme",
        title="Job Application for Engineer at Acme", location="",
        published_at=None, content_sha256=None, source_kind=SourceKind.EXA,
        query_key="engineer",
    )
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    out = acquire_node(
        _context(tmp_path, "acquire-dedupe-3"), _input(config=_config(exa=True, ats=False)),
        http_client=None, exa=_Exa([exa_row]), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert len(out.rows) == 1
    assert out.rows[0].posting.source_kind is SourceKind.EXA


# --- U19: country filter ----------------------------------------------------


def _row_at(location: str, *, source_kind: SourceKind = SourceKind.ATS, url: str = "https://job-boards.greenhouse.io/acme/jobs/1") -> PostingRow:
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="acme",
        company="acme", title="Software Engineer", location=location, published_at=None,
        content_sha256=None, source_kind=source_kind, query_key="software engineer",
    )


def test_country_filter_excludes_non_matching_row_from_selection(monkeypatch, tmp_path):
    row = _row_at("Bengaluru, India")
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    config = _config(exa=False, ats=False)
    config = replace(config, countries=("US",))
    out = acquire_node(
        _context(tmp_path, "acquire-country-1"), _input([row], config=config),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    # Filtered out: visible in rows with its real (new) outcome, never selected.
    assert len(out.rows) == 1
    assert out.rows[0].outcome.value == "new"
    assert out.rows[0].posting.location == "Bengaluru, India"
    assert out.selected_postings == ()


def test_country_filter_keeps_matching_row_selected(monkeypatch, tmp_path):
    row = _row_at("Denver, CO")
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    config = replace(_config(exa=False, ats=False), countries=("US",))
    out = acquire_node(
        _context(tmp_path, "acquire-country-2"), _input([row], config=config),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert len(out.selected_postings) == 1


def test_country_filter_keeps_ambiguous_location_selected(monkeypatch, tmp_path):
    # P1b added a bare tech-hub city table, so "Bengaluru" now resolves
    # definitively (non-US) rather than staying ambiguous; "Remote" has no
    # recognized country/city/state signal at all and stays ambiguous.
    row = _row_at("Remote")
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    config = replace(_config(exa=False, ats=False), countries=("US",))
    out = acquire_node(
        _context(tmp_path, "acquire-country-3"), _input([row], config=config),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert len(out.selected_postings) == 1


def test_country_filter_multi_location_string_kept_when_any_matches(monkeypatch, tmp_path):
    row = _row_at("Remote, Canada; Remote, United States")
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    config = replace(_config(exa=False, ats=False), countries=("US",))
    out = acquire_node(
        _context(tmp_path, "acquire-country-4"), _input([row], config=config),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert len(out.selected_postings) == 1


def test_country_filter_us_state_only_location_matches_us(monkeypatch, tmp_path):
    row = _row_at("Atlanta, Georgia")
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    config = replace(_config(exa=False, ats=False), countries=("US",))
    out = acquire_node(
        _context(tmp_path, "acquire-country-5"), _input([row], config=config),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert len(out.selected_postings) == 1


def test_country_filter_off_when_countries_not_configured(monkeypatch, tmp_path):
    row = _row_at("London, United Kingdom")
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    out = acquire_node(
        _context(tmp_path, "acquire-country-6"), _input([row], config=_config(exa=False, ats=False)),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert len(out.selected_postings) == 1


# --- U12: visa sponsorship exclusion ----------------------------------------


def _sponsorship_row(sponsorship: SponsorshipStatus | None) -> PostingRow:
    return PostingRow(
        url="https://job-boards.greenhouse.io/acme/jobs/1", normalized_url="https://job-boards.greenhouse.io/acme/jobs/1",
        provider=ATSProvider.GREENHOUSE, board_token="acme", company="acme", title="Software Engineer",
        location="Denver, CO", published_at=None, content_sha256=None, source_kind=SourceKind.ATS,
        query_key="software engineer", sponsorship=sponsorship,
    )


def test_visa_required_excludes_not_offered_row(monkeypatch, tmp_path):
    row = _sponsorship_row(SponsorshipStatus.NOT_OFFERED)
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    config = replace(_config(exa=False, ats=False), visa_sponsorship_required=True)
    out = acquire_node(
        _context(tmp_path, "acquire-visa-1"), _input([row], config=config),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert len(out.rows) == 1  # still visible
    assert out.selected_postings == ()  # never selected


def test_visa_required_keeps_unknown_sponsorship_row(monkeypatch, tmp_path):
    row = _sponsorship_row(SponsorshipStatus.UNKNOWN)
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    config = replace(_config(exa=False, ats=False), visa_sponsorship_required=True)
    out = acquire_node(
        _context(tmp_path, "acquire-visa-2"), _input([row], config=config),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert len(out.selected_postings) == 1


def test_visa_not_required_keeps_not_offered_row_selected(monkeypatch, tmp_path):
    row = _sponsorship_row(SponsorshipStatus.NOT_OFFERED)
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    out = acquire_node(
        _context(tmp_path, "acquire-visa-3"), _input([row], config=_config(exa=False, ats=False)),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert len(out.selected_postings) == 1


# --- U26: raw response storage -----------------------------------------------


def test_raw_ats_and_exa_responses_are_stored_gzip_with_index(monkeypatch, tmp_path):
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "super-secret-exa-key-abc123")

    def exa_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"url": "https://job-boards.greenhouse.io/acme/jobs/1", "title": "SE"}]})

    def ats_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "location": {"name": "Denver, CO"}, "content": "<p>Build.</p>"}]})

    def handler(request: httpx.Request) -> httpx.Response:
        if "exa.ai" in str(request.url):
            return exa_handler(request)
        return ats_handler(request)

    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    watchlist = _Watchlist()
    watchlist.added.append(
        WatchlistEntry(
            watchlist_id="scout_watchlist:greenhouse:acme", provider=ATSProvider.GREENHOUSE,
            board_token="acme", company="acme", state="active",
            first_seen=WatchlistFirstSeen(SourceKind.ATS, "https://boards.greenhouse.io/acme/jobs/1", "software engineer", "acquire-raw-1", "2026-09-22T00:00:00Z"),
        )
    )
    context = _context(tmp_path, "acquire-raw-1")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        out = acquire_node(
            context, _input(config=_config(exa=True, ats=True)),
            http_client=client, exa=ExaSearchClient(), ats=ATSBoardClients(), watchlist=watchlist,
        )
    assert out  # sanity: the run completed

    raw_root = tmp_path / "runs" / context.run_id / "raw"
    index = json.loads((raw_root / "index.json").read_text())
    assert index["cap_bytes"] == 20 * 1024 * 1024
    assert len(index["entries"]) == 2
    sources = {entry["source"] for entry in index["entries"]}
    assert sources == {"exa", "greenhouse"}
    for entry in index["entries"]:
        assert entry["stored"] is True
        assert "path" in entry
        gz_path = tmp_path / entry["path"]
        assert gz_path.suffix == ".gz"
        raw_bytes = gzip.decompress(gz_path.read_bytes())
        # No key material anywhere in the stored bytes.
        assert b"super-secret-exa-key-abc123" not in raw_bytes
        # sha256/bytes in the index describe the *uncompressed* body.
        assert entry["bytes"] == len(raw_bytes)
        # No request headers/query keys leak into the recorded URL.
        assert "super-secret" not in entry["url"]


def test_raw_payload_cap_stops_storing_further_entries(monkeypatch, tmp_path):
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.RAW_PAYLOAD_CAP_BYTES", 200)
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": [{"id": 1, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "location": {"name": "Denver, CO"}, "content": "x" * 5000}]})

    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    watchlist = _Watchlist()
    watchlist.added.append(
        WatchlistEntry(
            watchlist_id="scout_watchlist:greenhouse:acme", provider=ATSProvider.GREENHOUSE,
            board_token="acme", company="acme", state="active",
            first_seen=WatchlistFirstSeen(SourceKind.ATS, "https://boards.greenhouse.io/acme/jobs/1", "software engineer", "acquire-raw-2", "2026-09-22T00:00:00Z"),
        )
    )
    watchlist.added.append(
        WatchlistEntry(
            watchlist_id="scout_watchlist:greenhouse:beta", provider=ATSProvider.GREENHOUSE,
            board_token="beta", company="beta", state="active",
            first_seen=WatchlistFirstSeen(SourceKind.ATS, "https://boards.greenhouse.io/beta/jobs/1", "software engineer", "acquire-raw-2", "2026-09-22T00:00:00Z"),
        )
    )
    context = _context(tmp_path, "acquire-raw-2")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        acquire_node(
            context, _input(config=_config(exa=False, ats=True)),
            http_client=client, exa=_Exa(()), ats=ATSBoardClients(), watchlist=watchlist,
        )

    raw_root = tmp_path / "runs" / context.run_id / "raw"
    index = json.loads((raw_root / "index.json").read_text())
    assert len(index["entries"]) == 2
    stored = [entry for entry in index["entries"] if entry["stored"]]
    skipped = [entry for entry in index["entries"] if not entry["stored"]]
    assert len(stored) == 1
    assert len(skipped) == 1
    assert skipped[0]["skipped_reason"] == "raw_payload_cap_reached"


def test_missing_exa_api_key_with_only_exa_enabled_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gigai.scout.find_jobs.market_acquisition.import_public_rows",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not write a batch when every source failed")),
    )
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    config = _config(exa=True, ats=False)
    exc = ExaClientError("exa_missing_api_key", "EXA_API_KEY is not set in the environment; Exa discovery cannot run")
    with pytest.raises(AcquireAllSourcesFailedError) as exc_info:
        acquire_node(
            _context(tmp_path, "acquire-005"),
            _input(config=config),
            http_client=None,
            exa=_FailingExa(exc),
            ats=_ATS(),
            watchlist=_Watchlist(),
        )
    assert exc_info.value.code == "acquire_all_sources_failed"
    # The underlying ExaClientError's full message (including the "is not set
    # in the environment" prose) never crosses into the raised node error;
    # only the exception's type name (via FailureRow.code) is surfaced.
    assert "is not set in the environment" not in str(exc_info.value)
    assert "exaclienterror" in str(exc_info.value)
