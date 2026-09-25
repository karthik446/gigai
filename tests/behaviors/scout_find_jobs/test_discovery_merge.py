"""Behavior tests for merge/dedupe/verify/watchlist (S2-A, no live calls)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.discovery.merge import (
    add_usable_boards_to_watchlist,
    evidence_for,
    merge_and_verify,
    normalize_company,
)
from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs
from gigai.scout.find_jobs.discovery.types import Candidate
from gigai.scout.find_jobs.watchlist import list_active
from tests.behaviors.scout_research.test_scout06_research_inputs import _fixture


def _prefs(**overrides: object) -> DiscoveryPrefs:
    values: dict[str, object] = {"roles": ("staff backend", "senior backend"), "countries": ("US",)}
    values.update(overrides)
    return DiscoveryPrefs(**values)


def _candidate(**overrides: object) -> Candidate:
    values: dict[str, object] = {
        "company": "Docker",
        "careers_url": "https://boards.greenhouse.io/docker",
        "ats_provider": "greenhouse",
        "sponsorship": "yes",
        "sponsorship_evidence": "64 LCA filings",
        "source_url": "https://example.test/docker-h1b",
        "found_by": "openai_web_search",
    }
    values.update(overrides)
    return Candidate(**values)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


_GREENHOUSE_JOBS = {
    "jobs": [
        {
            "id": 1,
            "title": "Staff Backend Engineer",
            "absolute_url": "https://boards.greenhouse.io/docker/jobs/1",
            "location": {"name": "Remote - United States"},
            "updated_at": "2026-09-01T00:00:00Z",
            "content": "Backend role.",
        }
    ]
}


def _merge_handler(request: httpx.Request) -> httpx.Response:
    if "boards-api.greenhouse.io" in request.url.host:
        return httpx.Response(200, json=_GREENHOUSE_JOBS)
    if request.method in ("HEAD", "GET") and "example.test" in request.url.host:
        return httpx.Response(200)
    return httpx.Response(404)


def test_normalize_company_ignores_case_and_punctuation() -> None:
    assert normalize_company("Docker, Inc.") == normalize_company("docker inc")


def test_dedupes_by_provider_and_board_token() -> None:
    candidates = [
        _candidate(found_by="openai_web_search"),
        _candidate(found_by="h1b", sponsorship_evidence="3 LCA filings"),
    ]

    with _client(_merge_handler) as client:
        boards, skipped = merge_and_verify(client=client, all_candidates=candidates, prefs=_prefs(), exclusions=set())

    assert len(boards) == 1
    assert set(boards[0].found_by) == {"h1b", "openai_web_search"}
    assert skipped == {}


def test_dedupes_by_normalized_company_across_different_urls() -> None:
    candidates = [
        _candidate(company="Docker Inc", careers_url="https://boards.greenhouse.io/docker"),
        _candidate(company="docker, inc.", careers_url="https://jobs.lever.co/docker"),
    ]

    with _client(_merge_handler) as client:
        boards, _ = merge_and_verify(client=client, all_candidates=candidates, prefs=_prefs(), exclusions=set())

    assert len(boards) == 1


def test_excluded_company_dropped() -> None:
    candidates = [_candidate(company="Docker")]
    exclusions = {normalize_company("Docker")}

    with _client(_merge_handler) as client:
        boards, skipped = merge_and_verify(client=client, all_candidates=candidates, prefs=_prefs(), exclusions=exclusions)

    assert boards == []
    assert skipped.get("excluded") == 1


def test_unparseable_board_url_skipped() -> None:
    candidates = [_candidate(careers_url="https://example.test/not-an-ats-url")]

    with _client(_merge_handler) as client:
        boards, skipped = merge_and_verify(client=client, all_candidates=candidates, prefs=_prefs(), exclusions=set())

    assert boards == []
    assert skipped.get("unparseable_board_url") == 1


def test_board_with_no_matching_us_postings_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "boards-api.greenhouse.io" in request.url.host:
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(200)

    with _client(handler) as client:
        boards, skipped = merge_and_verify(client=client, all_candidates=[_candidate()], prefs=_prefs(), exclusions=set())

    assert boards == []
    assert skipped.get("no_matching_us_postings") == 1


def test_unresolvable_board_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "boards-api.greenhouse.io" in request.url.host:
            return httpx.Response(404)
        return httpx.Response(200)

    with _client(handler) as client:
        boards, skipped = merge_and_verify(client=client, all_candidates=[_candidate()], prefs=_prefs(), exclusions=set())

    assert boards == []
    assert any(reason.startswith("board_unresolvable") for reason in skipped)


def test_dead_evidence_source_url_marks_unverified_and_skips() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "boards-api.greenhouse.io" in request.url.host:
            return httpx.Response(200, json=_GREENHOUSE_JOBS)
        return httpx.Response(404)  # every evidence source URL is dead

    with _client(handler) as client:
        boards, skipped = merge_and_verify(client=client, all_candidates=[_candidate()], prefs=_prefs(), exclusions=set())

    assert boards == []
    assert skipped.get("evidence_unverifiable") == 1


def test_evidence_verified_true_when_source_resolves() -> None:
    with _client(_merge_handler) as client:
        boards, _ = merge_and_verify(client=client, all_candidates=[_candidate()], prefs=_prefs(), exclusions=set())

    assert len(boards) == 1
    assert boards[0].evidence_verified is True
    assert boards[0].matching_us_postings == 1


def test_add_usable_boards_to_watchlist_and_evidence_sidecar(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)

    with _client(_merge_handler) as client:
        boards, _ = merge_and_verify(client=client, all_candidates=[_candidate()], prefs=_prefs(), exclusions=set())

    added = add_usable_boards_to_watchlist(home_root=home, target=target, discovery_id="discovery_test1", boards=boards, gig_id=gig_id)

    assert len(added) == 1
    active = list_active(home, target, gig_id)
    assert len(active) == 1
    assert active[0].company == "Docker"
    assert active[0].provider.value == "greenhouse"
    assert active[0].board_token == "docker"
    # WatchlistEntry/SourceKind are untouched (coordinator decision, Option
    # B): the true discovery origin lives in the sidecar, not the watchlist.
    assert active[0].first_seen.source_kind.value == "ats"

    evidence = evidence_for(home_root=home, target=target)
    key = ("greenhouse", "docker")
    assert key in evidence
    assert evidence[key]["evidence_source_url"] == "https://example.test/docker-h1b"
    assert evidence[key]["evidence_verified"] is True
    assert evidence[key]["discovery_id"] == "discovery_test1"


def test_add_usable_boards_to_watchlist_skips_already_active(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)

    with _client(_merge_handler) as client:
        boards, _ = merge_and_verify(client=client, all_candidates=[_candidate()], prefs=_prefs(), exclusions=set())

    add_usable_boards_to_watchlist(home_root=home, target=target, discovery_id="discovery_test1", boards=boards, gig_id=gig_id)
    second_added = add_usable_boards_to_watchlist(home_root=home, target=target, discovery_id="discovery_test2", boards=boards, gig_id=gig_id)

    assert second_added == []
    active = list_active(home, target, gig_id)
    assert len(active) == 1


def test_watchlist_entries_excluded_from_future_discovery(tmp_path: Path) -> None:
    """The (unexported) exclusion-building step in __init__.py reads list_active; sanity-check the shape here."""

    home, target, gig_id = _fixture(tmp_path)
    with _client(_merge_handler) as client:
        boards, _ = merge_and_verify(client=client, all_candidates=[_candidate()], prefs=_prefs(), exclusions=set())
    add_usable_boards_to_watchlist(home_root=home, target=target, discovery_id="discovery_test1", boards=boards, gig_id=gig_id)

    active = list_active(home, target, gig_id)
    assert {normalize_company(e.company) for e in active} == {normalize_company("Docker")}


# --- held-review-002: an empty/missing evidence source_url is kept, unverified,
# with no HTTP call -- never dropped as "evidence_unverifiable" (that skip is for
# a *cited* URL that turned out dead, S23's live-404 case) and never a crash.
# openai_source's documented operator rule (2026-09-24): when sponsorship isn't
# required the model may answer with no evidence/source at all, and merge only
# verifies a source URL when one is present. ----------------------------------


class _CountingHandler:
    """MockTransport handler that serves the board API and records every other request."""

    def __init__(self, evidence_status: int = 200) -> None:
        self.evidence_requests: list[tuple[str, str]] = []
        self.evidence_status = evidence_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if "boards-api.greenhouse.io" in request.url.host:
            return httpx.Response(200, json=_GREENHOUSE_JOBS)
        self.evidence_requests.append((request.method, str(request.url)))
        return httpx.Response(self.evidence_status)


def test_empty_evidence_source_url_is_kept_unverified_without_http_call() -> None:
    handler = _CountingHandler()
    candidates = [_candidate(source_url="", sponsorship="unknown", sponsorship_evidence="")]

    with _client(handler) as client:
        boards, skipped = merge_and_verify(client=client, all_candidates=candidates, prefs=_prefs(), exclusions=set())

    assert skipped == {}, skipped
    assert len(boards) == 1
    assert boards[0].company == "Docker"
    assert boards[0].evidence_verified is False
    assert boards[0].evidence_source_url == ""
    assert boards[0].matching_us_postings == 1
    assert handler.evidence_requests == []  # nothing to verify -> no HEAD/GET at all


def test_empty_primary_source_url_falls_back_to_a_cited_and_live_alternative() -> None:
    handler = _CountingHandler()
    candidates = [
        _candidate(source_url="", found_by="openai_web_search"),
        _candidate(source_url="https://example.test/docker-h1b", found_by="h1b", sponsorship_evidence="3 LCA filings"),
    ]

    with _client(handler) as client:
        boards, skipped = merge_and_verify(client=client, all_candidates=candidates, prefs=_prefs(), exclusions=set())

    assert skipped == {}
    assert len(boards) == 1
    assert boards[0].evidence_verified is True
    assert boards[0].evidence_source_url == "https://example.test/docker-h1b"
    assert boards[0].sponsorship_evidence == "3 LCA filings"
    assert set(boards[0].found_by) == {"h1b", "openai_web_search"}
    assert [method for method, _ in handler.evidence_requests] == ["HEAD"]


def test_empty_source_url_alongside_a_dead_citation_is_still_kept_unverified() -> None:
    # The uncited member is exactly as good as a solo uncited candidate; the
    # other source's dead citation doesn't make the company disappear.
    handler = _CountingHandler(evidence_status=404)
    candidates = [
        _candidate(source_url="", found_by="openai_web_search"),
        _candidate(source_url="https://example.test/dead", found_by="h1b"),
    ]

    with _client(handler) as client:
        boards, skipped = merge_and_verify(client=client, all_candidates=candidates, prefs=_prefs(), exclusions=set())

    assert skipped == {}
    assert len(boards) == 1
    assert boards[0].evidence_verified is False
    assert boards[0].evidence_source_url == ""
    assert set(boards[0].found_by) == {"h1b", "openai_web_search"}


def test_unverified_empty_source_board_still_reaches_watchlist_and_sidecar(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    with _client(_CountingHandler()) as client:
        boards, _ = merge_and_verify(client=client, all_candidates=[_candidate(source_url="")], prefs=_prefs(), exclusions=set())

    added = add_usable_boards_to_watchlist(home_root=home, target=target, discovery_id="discovery_test1", boards=boards, gig_id=gig_id)

    assert len(added) == 1
    assert len(list_active(home, target, gig_id)) == 1
    evidence = evidence_for(home_root=home, target=target)[("greenhouse", "docker")]
    assert evidence["evidence_source_url"] == ""
    assert evidence["evidence_verified"] is False
