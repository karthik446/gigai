"""0.1.11.3 item 8: the Jobs rows carry the company's H-1B approvals and the posting's sponsorship, as LABELS. Synthetic only.

- a row whose board has a catalog figure says ``h1b`` ``{approvals, fiscal_years}``; a board without one says ``null``
  (never a zero, never a placeholder);
- ``sponsorship`` is null for a posting nothing assessed (the page words it "Sponsorship not stated");
- the figure is looked up in an index built ONCE per request: the Jobs read never reads the catalog per row;
- it is a label: the same rows, order and counts with and without a figure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import posting_search
from gigai.scout.posting_search import _h1b_index as REAL_INDEX
from gigai.scout.find_jobs.company_index import board_list_url, index_stamp, refresh_company

from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, lever_job

WITH_FIGURE = "acme-health"
WITHOUT_FIGURE = "osprey-lane"
FIGURE = {"approvals": 32, "fiscal_years": ["2025", "2026"]}


def _seed(fx: PostingsFixture, slug: str, count: int) -> None:
    fx.watch(slug)
    jobs = [lever_job(slug, n) for n in range(1, count + 1)]
    fx.cache.store("lever", board_list_url("lever", slug), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(fx.index, fx.cache, ats="lever", slug=slug, company=None, observed_at=index_stamp(days_ago(1)))


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _seed(fixture, WITH_FIGURE, 3)
    _seed(fixture, WITHOUT_FIGURE, 2)
    REAL_INDEX.cache_clear()
    yield fixture
    REAL_INDEX.cache_clear()


def _rows(fx: PostingsFixture) -> list[dict]:
    return posting_search.search_postings(fx.home_root, fx.target, now=NOW)["postings"]["rows"]  # type: ignore[index]


def test_a_row_carries_the_company_figure_or_null(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(posting_search, "_h1b_index", lambda: {("lever", WITH_FIGURE): FIGURE})
    rows = _rows(fx)
    assert {row["company_slug"] for row in rows} == {WITH_FIGURE, WITHOUT_FIGURE}
    for row in rows:
        assert row["h1b"] == (FIGURE if row["company_slug"] == WITH_FIGURE else None), row["company_slug"]
        assert row["sponsorship"] is None  # nothing assessed: the page says "Sponsorship not stated"


def test_no_catalog_figure_is_null_for_every_row(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(posting_search, "_h1b_index", lambda: {})
    rows = _rows(fx)
    assert rows and all(row["h1b"] is None and row["sponsorship"] is None for row in rows)


def test_a_figure_is_a_label_only_the_rows_and_their_order_do_not_change(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(posting_search, "_h1b_index", lambda: {})
    bare = _rows(fx)
    monkeypatch.setattr(posting_search, "_h1b_index", lambda: {("lever", WITH_FIGURE): FIGURE, ("lever", WITHOUT_FIGURE): {"approvals": 900, "fiscal_years": []}})
    labelled = _rows(fx)
    strip = lambda rows: [{key: value for key, value in row.items() if key != "h1b"} for row in rows]  # noqa: E731
    assert strip(labelled) == strip(bare)


def test_the_jobs_read_does_not_read_the_catalog_per_row(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.find_jobs import company_catalog

    built = []
    real = company_catalog.CompanyCatalog.h1b_by_board

    def counted(self):  # the one place the figures are read
        built.append(1)
        return real(self)

    monkeypatch.setattr(company_catalog.CompanyCatalog, "h1b_by_board", counted)
    REAL_INDEX.cache_clear()
    first = _rows(fx)
    second = _rows(fx)
    assert len(first) == len(second) == 5
    assert len(built) == 1, "the catalog's index is built once, not per row or per request"

    # And the lookup inside one request is the one index, however many rows: count the calls of the index function.
    calls = []
    index = REAL_INDEX
    monkeypatch.setattr(posting_search, "_h1b_index", lambda: calls.append(1) or index())
    assert len(_rows(fx)) == 5
    assert len(calls) == 1
