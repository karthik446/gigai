from __future__ import annotations

from collections import Counter
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
    DropCount,
    FindJobsConfig,
    NodeContext,
    NotAssessedReason,
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
from gigai.scout.find_jobs.selection import normalize_title


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


def test_b5_exa_fails_and_ats_watchlist_is_empty_still_raises(monkeypatch, tmp_path):
    # B5 (0.1.8.1 live UAT): the real bug wasn't the `not rows` gate itself
    # -- it was that ATS's "no boards in the watchlist yet" case sets
    # `ats_ok = True` vacuously (:431-432, a legitimate "nothing to do" on
    # its own). On a first run, Exa is the *only* source that would ever
    # populate that watchlist; if Exa fails outright, the watchlist stays
    # empty, ATS's vacuous "ok" papered over Exa's real failure, and the old
    # `source_outcomes`-based check reported the run as succeeded with 0
    # postings. Both sources enabled, watchlist genuinely empty, Exa fails:
    # this must still raise, not silently succeed.
    monkeypatch.setattr(
        "gigai.scout.find_jobs.market_acquisition.import_public_rows",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not write a batch when every source failed")),
    )
    config = _config(exa=True, ats=True)
    with pytest.raises(AcquireAllSourcesFailedError) as exc_info:
        acquire_node(
            _context(tmp_path, "acquire-b5-1"),
            _input(config=config),
            http_client=None,
            exa=_FailingExa(RuntimeError("exa transport exploded")),
            ats=_ATS(),  # returns () for any board -- but the watchlist is
            watchlist=_Watchlist(),  # empty, so list_board is never even called.
        )
    assert exc_info.value.code == "acquire_all_sources_failed"
    assert "exa transport exploded" not in str(exc_info.value)
    assert "exa:runtimeerror" in str(exc_info.value)


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
    # Title must match _config()'s default role ("software engineer"): B1
    # now drops a role-mismatched row right after fetch (not just at
    # selection), so a fixture title has to actually satisfy the configured
    # role for this Exa-vs-ATS-dedupe test to still exercise dedupe rather
    # than the (correct, separate) role-drop path.
    exa_row = PostingRow(
        url="https://job-boards.greenhouse.io/acme/jobs/999",
        normalized_url="https://job-boards.greenhouse.io/acme/jobs/999",
        provider=ATSProvider.GREENHOUSE, board_token="acme", company="acme",
        title="Job Application for Software Engineer at Acme", location="",
        published_at=None, content_sha256=None, source_kind=SourceKind.EXA,
        query_key="software engineer",
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
    # B1 (0.1.8.1, operator: "why are we doing filtering on fucking ui?"):
    # a country-non-matching row is now dropped from `results` entirely at
    # acquire, not just left unselected for the UI to filter.
    row = _row_at("Bengaluru, India")
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    config = _config(exa=False, ats=False)
    config = replace(config, countries=("US",))
    out = acquire_node(
        _context(tmp_path, "acquire-country-1"), _input([row], config=config),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert out.rows == ()
    assert out.selected_postings == ()
    assert out.dropped_counts == (DropCount(NotAssessedReason.LOCATION_MISMATCH, 1),)


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
    # B1 (0.1.8.1): dropped from `results` at acquire, same as a
    # country-mismatch row -- not just left unselected.
    row = _sponsorship_row(SponsorshipStatus.NOT_OFFERED)
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    config = replace(_config(exa=False, ats=False), visa_sponsorship_required=True)
    out = acquire_node(
        _context(tmp_path, "acquire-visa-1"), _input([row], config=config),
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )
    assert out.rows == ()
    assert out.selected_postings == ()
    assert out.dropped_counts == (DropCount(NotAssessedReason.SPONSORSHIP_EXCLUDED, 1),)


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


# --- B1 replay: the operator's real evidence run (run_d73cb030-...), small
# shape-only fixtures derived from its raw Ashby/Greenhouse payloads (see
# fixtures/fixture-uat-0181-replay-postings.json's _provenance note). Feeds
# them through acquire_node with countries=["US"] and asserts the exact
# before/after drop the ticket describes: no Bengaluru/Hyderabad/Seoul/
# Singapore/Australia/Canada/AMER-only rows survive; US rows do. -----------


def test_replay_uat_0181_evidence_run_country_filter(monkeypatch, tmp_path):
    payload = json.loads((FIXTURES / "fixture-uat-0181-replay-postings.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "ashbyhq.com" in url:
            return httpx.Response(200, json=payload["ashby"])
        return httpx.Response(200, json=payload["greenhouse"])

    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    watchlist = _Watchlist()
    watchlist.added.append(
        WatchlistEntry(
            watchlist_id="scout_watchlist:ashby:acme", provider=ATSProvider.ASHBY,
            board_token="acme", company="acme", state="active",
            first_seen=WatchlistFirstSeen(SourceKind.ATS, "https://jobs.ashbyhq.com/acme", "software engineer", "acquire-replay-1", "2026-09-22T00:00:00Z"),
        )
    )
    watchlist.added.append(
        WatchlistEntry(
            watchlist_id="scout_watchlist:greenhouse:acme", provider=ATSProvider.GREENHOUSE,
            board_token="acme", company="acme", state="active",
            first_seen=WatchlistFirstSeen(SourceKind.ATS, "https://boards.greenhouse.io/acme", "software engineer", "acquire-replay-1", "2026-09-22T00:00:00Z"),
        )
    )
    config = replace(_config(exa=False, ats=True), countries=("US",))

    # Before: what the same 11 fixture postings look like with country
    # filtering off entirely (the pre-B1 "everything survives to results"
    # baseline this replay is measuring against).
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        before = acquire_node(
            _context(tmp_path, "acquire-replay-before"), _input(config=replace(_config(exa=False, ats=True))),
            http_client=client, exa=_Exa(()), ats=ATSBoardClients(), watchlist=watchlist,
        )
    assert len(before.rows) == 11

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        after = acquire_node(
            _context(tmp_path, "acquire-replay-after"), _input(config=config),
            http_client=client, exa=_Exa(()), ats=ATSBoardClients(), watchlist=watchlist,
        )

    kept_locations = {row.posting.location for row in after.rows}
    survivors = sorted(
        (row.posting.company, row.posting.location, row.posting.countries) for row in after.rows
    )
    print(f"B1 replay: before={len(before.rows)} rows, after={len(after.rows)} rows, dropped={len(before.rows) - len(after.rows)}")
    print("B1 replay survivors (company | location | countries):")
    for company, location, countries in survivors:
        print(f"  {company} | {location} | {countries}")

    # The exact non-US *countries* named in the ticket: none of their rows
    # survive a definite country mismatch.
    assert "India" not in kept_locations  # Bengaluru-sourced (structured)
    assert "Australia" not in kept_locations
    assert "Canada" not in kept_locations
    assert "Singapore" not in kept_locations
    assert "Seoul, South Korea" not in kept_locations

    # 0.1.8.1 r1 (coordinator review): AMER/EMEA/APAC are *region* tokens,
    # not countries. A region-ONLY location is now a definite non-match
    # (dropped), not ambiguous -- the operator's own UAT complaint ("AMER"
    # rows passed a US-only filter). None of them survive.
    assert "AMER" not in kept_locations
    assert "EMEA" not in kept_locations
    assert "APAC" not in kept_locations

    # The internal "z-Test & Templates Only" label carries no region token
    # and no country signal at all -- it has no signal whatsoever, so it
    # stays genuinely ambiguous (kept), unaffected by this fix, same as
    # "Remote" alone always has been.
    assert "z-Test & Templates Only" in kept_locations

    # The US-located rows (structured Ashby address + free-text Greenhouse
    # "Mountain View, USA") also survive.
    assert "United States" in kept_locations
    assert "Mountain View, USA" in kept_locations
    assert len(after.rows) == 3  # US(2, structured+text) + z-Test (ambiguous, kept)

    # Every dropped row is accounted for by a per-reason count: the 5
    # definite non-US-country rows (India/Australia/Canada/Singapore/Seoul)
    # under LOCATION_MISMATCH, and the 3 region-only rows (AMER/EMEA/APAC)
    # under their own REGION_ONLY key (0.1.8.1 r1), distinct and auditable.
    exclusion_dropped = len(before.rows) - len(after.rows)
    assert exclusion_dropped == 8
    by_reason = {item.reason: item.count for item in after.dropped_counts}
    assert by_reason[NotAssessedReason.LOCATION_MISMATCH] == 5
    assert by_reason[NotAssessedReason.REGION_ONLY] == 3
    # B2 wiring (0.1.8.1 UAT): all 3 surviving rows are the same company
    # ("acme", from both Ashby and Greenhouse), so the per-company diversity
    # cap (default 2, well under the un-hit selection_cap of 10) drops the
    # third -- an *additional*, selection-stage drop, folded onto the
    # existing OVER_CAP reason, distinct from the exclusion-stage drops
    # counted above.
    total_dropped = sum(item.count for item in after.dropped_counts)
    assert total_dropped == exclusion_dropped + 1
    assert by_reason[NotAssessedReason.OVER_CAP] == 1


# --- wire-selection: B2's diversity helper wired into acquire's selection loop ---


def _uat_shape_rows() -> list[PostingRow]:
    """36 ClickHouse + 31 Coupang + 2 gen-digital role-matched rows.

    Mirrors ``test_selection_diversity.py``'s fixture shape (the live UAT
    batch B2 fixed), but as real ``PostingRow``s so this test exercises
    ``select_for_assessment`` wired into ``acquire_node`` end-to-end, not
    the pure helper directly. Every title includes "Software Engineer" so
    all 69 rows role-match the fixture config's roles.
    """

    rows: list[PostingRow] = []
    for i in range(36):
        title = "Senior Software Engineer - Cloud Infrastructure" if i < 20 else f"Software Engineer - Backend {i}"
        url = f"https://boards.example/clickhouse/{i}"
        rows.append(PostingRow(
            url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="clickhouse",
            company="ClickHouse", title=title, location="Remote, United States",
            published_at=f"2026-09-{(i % 28) + 1:02d}T00:00:00Z", content_sha256=None,
            source_kind=SourceKind.ATS, query_key="software engineer",
        ))
    for i in range(31):
        url = f"https://boards.example/coupang/{i}"
        rows.append(PostingRow(
            url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="coupang",
            company="Coupang", title=f"Software Engineer {i}", location="Seoul, South Korea",
            published_at=f"2026-09-{(i % 28) + 1:02d}T00:00:00Z", content_sha256=None,
            source_kind=SourceKind.ATS, query_key="software engineer",
        ))
    for i in range(2):
        url = f"https://boards.example/gen-digital/{i}"
        rows.append(PostingRow(
            url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="gen-digital",
            company="Gen Digital", title=f"Software Engineer - Platform {i}", location="Remote, United States",
            published_at=f"2026-09-{(i % 28) + 1:02d}T00:00:00Z", content_sha256=None,
            source_kind=SourceKind.ATS, query_key="software engineer",
        ))
    return rows


def test_uat_shape_selection_is_diverse_and_unselected_rows_are_labelled(monkeypatch, tmp_path):
    # ACCEPTANCE (wire-selection): the live UAT shape (36 ClickHouse + 31
    # Coupang + 2 gen-digital role-matched rows), cap 5 -- acquire's
    # selection loop must call ``select_for_assessment`` instead of the old
    # naive first-N walk, so the selection is diverse and every unselected
    # row is accounted for by a per-reason drop count.
    rows = _uat_shape_rows()
    status = SimpleNamespace(complete=True, input_ref={"path": "input.json"})
    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition.import_public_rows", lambda **_: status)
    input = AcquireInput(_config(exa=False, ats=False), "sha256:" + "c" * 64, None, tuple(rows), 5, SelectionRule.NEW_OR_EDITED_ROLE_MATCH)
    out = acquire_node(
        _context(tmp_path, "acquire-uat-shape-1"), input,
        http_client=None, exa=_Exa(()), ats=_ATS(), watchlist=_Watchlist(),
    )

    assert len(out.selected_postings) == 5
    selected_urls = {sp.normalized_url for sp in out.selected_postings}
    selected_rows = [row.posting for row in out.rows if row.posting.normalized_url in selected_urls]

    counts = Counter(row.company for row in selected_rows)
    assert all(count <= 2 for count in counts.values())
    normalized_titles = [normalize_title(row.title) for row in selected_rows]
    assert len(normalized_titles) == len(set(normalized_titles))

    # Every not-selected new/edited/role-matched row is dropped for
    # DUPLICATE (near-identical postings) or OVER_CAP (didn't fit under the
    # per-company/global cap) -- the two labels B2's helper produces --
    # additively tallied on `dropped_counts`, no new enum value.
    by_reason = {item.reason: item.count for item in out.dropped_counts}
    assert set(by_reason) <= {NotAssessedReason.DUPLICATE, NotAssessedReason.OVER_CAP}
    assert sum(by_reason.values()) == len(rows) - 5
    assert by_reason.get(NotAssessedReason.DUPLICATE, 0) > 0  # the 20 shared-title ClickHouse rows


# Assess's own DUPLICATE/OVER_CAP not-assessed labeling (recomputed from
# the same select_for_assessment helper acquire used) is covered directly
# in tests/behaviors/scout_find_jobs/test_assess_model_policy.py --
# test_candidate_partition_mixes_assessed_over_cap_and_exclusions (OVER_CAP)
# and test_candidate_dropped_as_duplicate_is_labelled_duplicate_not_over_cap
# (DUPLICATE) -- since assess_node's real candidate-resolution path needs a
# full assess fixture (sealed config, resolved workpad, adapter) that lives
# there, not here.
