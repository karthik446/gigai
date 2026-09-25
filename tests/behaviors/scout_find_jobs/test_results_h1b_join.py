"""Q4b-data: the H-1B join on ``GET /api/runs/{id}/results`` rows.

``rows[].h1b`` (``{"approvals": int, "fiscal_years": [...]}``) is added to
the served JSON when a posting's ``(provider, board_token)`` names a shipped
catalog record whose ``h1b`` is an object; absent (never ``null``) otherwise.
The join is read-only over the bundled catalog and display-only: a catalog
that fails to load leaves every row untouched.
"""

from __future__ import annotations

import json
import threading

import httpx
import pytest

from gigai.scout.find_jobs.api.runs import _catalog_h1b_index, attach_h1b
from gigai.scout.find_jobs.company_catalog import CompanyH1B
from gigai.scout.find_jobs.contracts import ATSProvider, RunResultsResponse
from gigai.scout.find_jobs.present_api import serve

from .conftest import load_fixture
from .test_present_ui import FakeBackend

_INDEX = {
    (ATSProvider.GREENHOUSE, "acme"): CompanyH1B(approvals=7, fiscal_years=("2025", "2026"), denials=2),
    (ATSProvider.LEVER, "orbit"): CompanyH1B(approvals=0, fiscal_years=("2026",)),  # no denials recorded
}


def _row(provider: str, token: str | None, url: str, source_kind: str = "ats") -> dict[str, object]:
    return {
        "posting": {
            "url": url,
            "normalized_url": url,
            "provider": provider,
            "board_token": token,
            "company": token or "Exa Co",
            "title": "Software Engineer",
            "location": "Denver, CO",
            "published_at": None,
            "content_sha256": None,
            "source_kind": source_kind,
            "query_key": f"ats:{provider}:{token}" if token else "exa:software engineer",
        },
        "outcome": "new",
    }


def test_attach_h1b_match_no_match_and_null_h1b() -> None:
    rows = [
        _row("greenhouse", "acme", "https://boards.greenhouse.io/acme/jobs/101"),  # catalog match, h1b object
        _row("greenhouse", "ACME", "https://boards.greenhouse.io/ACME/jobs/102"),  # case-insensitive token
        _row("lever", "orbit", "https://jobs.lever.co/orbit/1"),  # match with zero approvals still joins
        _row("ashby", "acme", "https://jobs.ashbyhq.com/acme/9"),  # same token, other provider: no match
        _row("greenhouse", "nobody", "https://boards.greenhouse.io/nobody/jobs/1"),  # not in the catalog
        _row("greenhouse", None, "https://example.test/jobs/1", source_kind="exa"),  # Exa row: no board
        {"posting": "not an object", "outcome": "new"},
        "not a row",
    ]
    attach_h1b(rows, _INDEX)
    assert rows[0]["h1b"] == {"approvals": 7, "fiscal_years": ["2025", "2026"], "denials": 2}
    assert rows[1]["h1b"] == {"approvals": 7, "fiscal_years": ["2025", "2026"], "denials": 2}
    assert rows[2]["h1b"] == {"approvals": 0, "fiscal_years": ["2026"]}  # denials absent, never null
    for untouched in rows[3:7]:
        assert "h1b" not in untouched, untouched
    assert rows[7] == "not a row"


def test_attach_h1b_with_a_null_h1b_catalog_record_adds_nothing() -> None:
    # A catalog record whose h1b is null never enters the index (company_catalog.h1b_by_board),
    # so the join sees no entry: the row stays exactly as served, no ``"h1b": null``.
    rows = [_row("greenhouse", "acme", "https://boards.greenhouse.io/acme/jobs/101")]
    attach_h1b(rows, {})
    assert rows == [_row("greenhouse", "acme", "https://boards.greenhouse.io/acme/jobs/101")]


def test_catalog_index_comes_from_the_shipped_catalog_and_fails_open() -> None:
    from gigai.scout.find_jobs import company_catalog

    _catalog_h1b_index.cache_clear()
    try:
        index = _catalog_h1b_index()
        assert index == company_catalog.load_company_catalog().h1b_by_board()
        assert index, "the shipped sample has at least one USCIS match"
    finally:
        _catalog_h1b_index.cache_clear()


def test_catalog_index_fails_open_when_the_catalog_cannot_load(monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs import company_catalog

    def _broken():
        raise company_catalog.CompanyCatalogError("catalog_digest_mismatch", "swapped")

    monkeypatch.setattr(company_catalog, "load_company_catalog", _broken)
    _catalog_h1b_index.cache_clear()
    try:
        assert _catalog_h1b_index() == {}
    finally:
        _catalog_h1b_index.cache_clear()


class _RowsBackend(FakeBackend):
    """The in-memory results double, with two acquired rows in its payload."""

    def run_results(self, run_id: str) -> RunResultsResponse:
        if run_id != self.known_run_id:
            raise LookupError(run_id)
        fixture = json.loads(json.dumps(load_fixture("fixture-api-run-results-response-v1.json")))
        fixture["run_id"] = run_id
        fixture["payload"]["run_id"] = run_id
        rows = [
            _row("greenhouse", "acme", "https://boards.greenhouse.io/acme/jobs/101"),
            _row("greenhouse", "nobody", "https://boards.greenhouse.io/nobody/jobs/1"),
        ]
        fixture["payload"]["rows"] = rows
        # T4: every candidate row is assessed or not-assessed; these are over the cap.
        fixture["payload"]["not_assessed"] = [{"posting": row["posting"], "reason": "over_cap"} for row in rows]
        return RunResultsResponse.from_json(fixture)


def test_results_route_serves_h1b_next_to_the_sealed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs.api import runs as runs_module

    monkeypatch.setattr(runs_module, "_catalog_h1b_index", lambda: _INDEX)
    backend = _RowsBackend()
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}") as client:
            response = client.get(f"/api/runs/{backend.known_run_id}/results")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert response.status_code == 200, response.text
    rows = response.json()["payload"]["rows"]
    assert rows[0]["h1b"] == {"approvals": 7, "fiscal_years": ["2025", "2026"], "denials": 2}
    assert "h1b" not in rows[1]
    # The sealed pair is untouched next to the additive key.
    sealed = backend.run_results(backend.known_run_id).to_json()["payload"]["rows"]
    assert {k: v for k, v in rows[0].items() if k != "h1b"} == sealed[0]
    assert rows[1] == sealed[1]
