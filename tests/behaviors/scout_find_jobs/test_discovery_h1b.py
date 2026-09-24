"""Behavior tests for the H-1B discovery source (S2-A, no live calls, synthetic LCA sample)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).with_name("fixtures") / "discovery"))
from build_synthetic_lca import build as build_synthetic_lca  # noqa: E402

from gigai.scout.find_jobs.discovery.h1b_source import (
    SOURCE_PAGE_URL,
    ensure_latest_file,
    extract_employers,
    guess_slugs,
    is_likely_staffing_or_outsourcing,
    rank_employers,
    run,
)
from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs


def _prefs(**overrides: object) -> DiscoveryPrefs:
    values: dict[str, object] = {"roles": ("staff backend", "senior backend")}
    values.update(overrides)
    return DiscoveryPrefs(**values)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_extract_employers_filters_visa_class_status_and_soc(tmp_path: Path) -> None:
    xlsx_path = build_synthetic_lca(tmp_path / "sample.xlsx")

    employers = extract_employers(xlsx_path)

    assert "Testcorp Startup Inc" in employers
    assert employers["Testcorp Startup Inc"].certified_case_count == 2
    assert "Acme Widgets LLC" in employers
    # Filtered: wrong visa class, wrong case status, wrong SOC prefix.
    assert "Rejected Sponsor Inc" not in employers
    assert "Wrong Visa Corp" not in employers
    assert "Not Software Corp" not in employers


def test_rank_employers_deprioritizes_staffing_outsourcing_patterns(tmp_path: Path) -> None:
    xlsx_path = build_synthetic_lca(tmp_path / "sample.xlsx")
    employers = extract_employers(xlsx_path)

    ranked_names = [e.employer_name for e in rank_employers(employers, top_n=10)]

    assert "Infosys Limited" not in ranked_names
    assert "Cognizant Technology Solutions" not in ranked_names
    assert "Testcorp Startup Inc" in ranked_names
    assert "Acme Widgets LLC" in ranked_names


def test_is_likely_staffing_or_outsourcing() -> None:
    assert is_likely_staffing_or_outsourcing("Infosys Limited")
    assert is_likely_staffing_or_outsourcing("Cognizant Technology Solutions")
    assert not is_likely_staffing_or_outsourcing("Testcorp Startup Inc")


def test_guess_slugs_strips_legal_suffixes_and_produces_variants() -> None:
    slugs = guess_slugs("Testcorp Startup Inc")
    assert "testcorpstartup" in slugs
    assert "testcorp-startup" in slugs


def test_guess_slugs_case_insensitive_suffix_strip_no_dropped_letters() -> None:
    # Regression per S24 Investigate §4: a case-sensitive strip once dropped
    # a leading uppercase letter ("Amazon.com Services LLC" -> "mazoncomservices").
    slugs = guess_slugs("Amazon.com Services LLC")
    assert any(slug.startswith("amazon") for slug in slugs)


def test_ensure_latest_file_downloads_when_no_cache(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    content = b"fake xlsx bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            if "FY2026_Q3" in request.url.path:
                return httpx.Response(200, headers={"content-length": str(len(content))})
            return httpx.Response(404)
        return httpx.Response(200, content=content)

    with _client(handler) as client:
        path = ensure_latest_file(home_root=home, client=client)

    assert path is not None
    assert path.is_file()
    assert path.read_bytes() == content
    assert path.parent == home / "cache" / "scout" / "h1b"


def test_ensure_latest_file_skips_redownload_when_cached_and_same_size(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    content = b"fake xlsx bytes"
    download_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            if "FY2026_Q3" in request.url.path:
                return httpx.Response(200, headers={"content-length": str(len(content))})
            return httpx.Response(404)
        download_calls["n"] += 1
        return httpx.Response(200, content=content)

    with _client(handler) as client:
        first = ensure_latest_file(home_root=home, client=client)
        second = ensure_latest_file(home_root=home, client=client)

    assert first == second
    assert download_calls["n"] == 1


def test_ensure_latest_file_returns_none_when_nothing_found(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _client(handler) as client:
        path = ensure_latest_file(home_root=home, client=client)

    assert path is None


def test_run_end_to_end_probes_boards_and_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    xlsx_path = build_synthetic_lca(tmp_path / "sample.xlsx")
    xlsx_bytes = xlsx_path.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if "dol.gov" in request.url.host:
            if request.method == "HEAD":
                if "FY2026_Q3" in request.url.path:
                    return httpx.Response(200, headers={"content-length": str(len(xlsx_bytes))})
                return httpx.Response(404)
            return httpx.Response(200, content=xlsx_bytes)
        if "boards-api.greenhouse.io" in request.url.host and "testcorpstartup" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 1,
                            "title": "Staff Backend Engineer",
                            "absolute_url": "https://boards.greenhouse.io/testcorpstartup/jobs/1",
                            "location": {"name": "Remote - United States"},
                            "updated_at": "2026-09-01T00:00:00Z",
                            "content": "Backend role.",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with _client(handler) as client:
        outcome = run(home_root=home, client=client, prefs=_prefs(), top_n=10)

    assert outcome.name == "h1b"
    assert outcome.cost_usd == 0.0
    assert outcome.error is None
    companies = {c.company for c in outcome.candidates}
    assert "Testcorp Startup Inc" in companies
    testcorp = next(c for c in outcome.candidates if c.company == "Testcorp Startup Inc")
    assert testcorp.found_by == "h1b"
    assert testcorp.sponsorship == "yes"
    assert testcorp.source_url == SOURCE_PAGE_URL
    assert testcorp.matching_us_postings_hint == 1


def test_run_records_skip_when_no_file_found(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _client(handler) as client:
        outcome = run(home_root=home, client=client, prefs=_prefs())

    assert outcome.runs == 0
    assert outcome.skip_reason is not None
    assert outcome.candidates == ()
