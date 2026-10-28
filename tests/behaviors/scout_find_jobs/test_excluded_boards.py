"""0.1.11.12 (workday-P5): the boards GigAI does not read.

``data/excluded-boards-v1.json`` names the Workday boards whose employer's terms (``terms``) or whose
``robots.txt`` (``robots``) say no. The list is read fail-closed (a malformed file is an error, never an empty
list), seeding from the catalog never adds a listed board (``excluded_as_excluded_board``), and adding one by URL
is refused with ``excluded_board``. The shipped catalog (rev6) ships 49 Workday boards and none of them is listed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from gigai.scout.find_jobs.company_catalog import load_company_catalog
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs
from gigai.scout.find_jobs.excluded_boards import (
    EXCLUDED_BOARDS_SCHEMA,
    ExcludedBoardsError,
    excluded_boards_json,
    find_excluded,
    load_excluded_boards,
    parse_excluded_boards,
    read_excluded_boards_resource_bytes,
)
from gigai.scout.find_jobs.watchlist import (
    WatchlistUrlError,
    add_company_from_url,
    catalog_records_for_prefs,
    list_active,
    seed_watchlist_from_catalog,
)
from tests.behaviors.scout_find_jobs.test_watchlist_seed import _catalog, _record
from tests.behaviors.scout_research.test_scout06_research_inputs import _fixture

ZOOM_BOARD = "https://zoom.wd5.myworkdayjobs.com/Zoom"


def _shipped_payload() -> dict:
    return json.loads(read_excluded_boards_resource_bytes().decode("utf-8"))


# --- the shipped list ------------------------------------------------------------


def test_shipped_list_has_the_eight_reviewed_rows() -> None:
    rows = load_excluded_boards()
    assert len(rows) == 8
    assert {row.provider for row in rows} == {ATSProvider.WORKDAY}
    assert {row.reason for row in rows} == {"terms", "robots"}
    assert sorted(row.token for row in rows if row.reason == "terms") == [
        "amgen.wd1.*", "cvshealth.wd1.*", "mastercard.wd1.*", "relx.wd3.RiskSolutions",
    ]
    assert sorted(row.token for row in rows if row.reason == "robots") == ["asml.wd3.*", "equifax.wd5.*", "equinix.wd1.*", "zoom.wd5.Zoom"]
    assert all(row.company and row.evidence and row.since and row.decided_by for row in rows)
    assert load_excluded_boards() is rows  # read once per process


def test_json_rows_carry_what_status_lines_need() -> None:
    rows = excluded_boards_json()
    assert [sorted(row) for row in rows] == [["company", "decided_by", "evidence", "evidence_short", "provider", "reason", "since", "token"]] * 8
    zoom = next(row for row in rows if row["token"] == "zoom.wd5.Zoom")
    assert zoom["reason"] == "robots" and zoom["company"] == "Zoom Communications, Inc."
    assert zoom["evidence_short"] == "robots.txt on zoom.wd5.myworkdayjobs.com: Disallow: /Zoom/"
    assert all(len(row["evidence_short"]) <= 120 and ";" not in row["evidence_short"] for row in rows)


def test_matching_is_exact_per_site_and_prefix_per_tenant() -> None:
    assert find_excluded(ATSProvider.WORKDAY, "zoom.wd5.Zoom").token == "zoom.wd5.Zoom"
    assert find_excluded(ATSProvider.WORKDAY, "ZOOM.wd5.Zoom").token == "zoom.wd5.Zoom"  # the host is case-insensitive
    assert find_excluded(ATSProvider.WORKDAY, "zoom.wd5.zoom") is None  # the site path is not
    assert find_excluded(ATSProvider.WORKDAY, "zoom.wd5.Other") is None
    assert find_excluded(ATSProvider.WORKDAY, "zoom.wd1.Zoom") is None
    tenant = find_excluded(ATSProvider.WORKDAY, "CVSHealth.wd1.CVS_External")
    assert tenant is not None and tenant.token == "cvshealth.wd1.*" and tenant.tenant_wide
    assert find_excluded(ATSProvider.WORKDAY, "cvshealth.wd1.Anything") is tenant
    assert find_excluded(ATSProvider.WORKDAY, "cvshealthcare.wd1.X") is None
    assert find_excluded(ATSProvider.GREENHOUSE, "zoom.wd5.Zoom") is None


# --- fail closed -----------------------------------------------------------------


def test_parse_accepts_the_shipped_payload() -> None:
    payload = _shipped_payload()
    assert payload["schema_version"] == EXCLUDED_BOARDS_SCHEMA
    assert len(parse_excluded_boards(payload)) == 8


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.update(schema_version="scout-excluded-boards:2"), "bad_schema"),
        (lambda p: p.update(boards="not a list"), "bad_schema"),
        (lambda p: p["boards"].append("zoom.wd5.Zoom"), "malformed_row"),
        (lambda p: p["boards"][0].pop("company"), "malformed_row"),
        (lambda p: p["boards"][0].update(evidence="   "), "malformed_row"),
        (lambda p: p["boards"][0].update(reason="vibes"), "unknown_reason"),
        (lambda p: p["boards"][0].update(provider="taleo"), "unknown_provider"),
    ],
)
def test_a_malformed_list_is_an_error_never_an_empty_list(mutate, code: str) -> None:
    payload = copy.deepcopy(_shipped_payload())
    mutate(payload)
    with pytest.raises(ExcludedBoardsError) as excinfo:
        parse_excluded_boards(payload)
    assert excinfo.value.code == code


# --- seeding ---------------------------------------------------------------------


def test_filter_drops_listed_boards_before_any_other_rule() -> None:
    records = (
        _record("Acme", ATSProvider.GREENHOUSE, "acme"),
        _record("Zoom", ATSProvider.WORKDAY, "zoom.wd5.Zoom", us_postings=None),
        _record("CVS Health", ATSProvider.WORKDAY, "cvshealth.wd1.CVS_External", us_postings=None),
        _record("Workday Inc", ATSProvider.WORKDAY, "workday.wd5.Workday", us_postings=None),
    )
    prefs = DiscoveryPrefs(countries=("US",), watch_companies=("Zoom",))
    kept, by_country, by_company, by_staffing, by_excluded = catalog_records_for_prefs(records, prefs)
    assert [r.board_token for r in kept] == ["acme", "workday.wd5.Workday"]
    assert (by_country, by_company, by_staffing, by_excluded) == (0, 0, 0, 2)  # watched or not, a listed board is never kept


def test_seeding_counts_the_listed_boards_and_never_adds_them(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    catalog = _catalog(
        _record("Acme", ATSProvider.GREENHOUSE, "acme"),
        _record("Zoom", ATSProvider.WORKDAY, "zoom.wd5.Zoom", us_postings=None),
        _record("Workday Inc", ATSProvider.WORKDAY, "workday.wd5.Workday", us_postings=None),
    )
    result = seed_watchlist_from_catalog(home, target, gig_id, prefs=DiscoveryPrefs(countries=("US",)), catalog=catalog)
    assert result.catalog_records == 3
    assert result.excluded_as_excluded_board == 1
    assert result.added == result.eligible == 2
    assert set(result.added_watchlist_ids) == {"scout_watchlist:greenhouse:acme", "scout_watchlist:workday:workday.wd5.Workday"}
    assert result.to_json()["excluded_as_excluded_board"] == 1
    assert {entry.watchlist_id for entry in list_active(home, target, gig_id)} == set(result.added_watchlist_ids)


def test_the_shipped_catalog_seeds_every_workday_board_and_no_listed_one(tmp_path: Path) -> None:
    shipped = load_company_catalog()
    workday = [record for record in shipped.records if record.provider is ATSProvider.WORKDAY]
    assert len(workday) == 49
    assert [record.board_token for record in workday if find_excluded(record.provider, record.board_token)] == []
    kept, _by_country, _by_company, _by_staffing, by_excluded = catalog_records_for_prefs(shipped.records, DiscoveryPrefs(countries=("US",)))
    assert by_excluded == 0
    assert sum(1 for record in kept if record.provider is ATSProvider.WORKDAY) == 49
    home, target, gig_id = _fixture(tmp_path)
    result = seed_watchlist_from_catalog(home, target, gig_id, prefs=DiscoveryPrefs(countries=("US",)), catalog=shipped)
    assert result.excluded_as_excluded_board == 0
    assert sum(1 for item in result.added_watchlist_ids if item.startswith("scout_watchlist:workday:")) == 49


# --- adding by URL ---------------------------------------------------------------


def test_adding_a_listed_board_by_url_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    with pytest.raises(WatchlistUrlError) as excinfo:
        add_company_from_url(ZOOM_BOARD, home, target, gig_id)
    assert excinfo.value.code == "excluded_board"
    assert str(excinfo.value).startswith("Zoom Communications, Inc. is not read: robots (")
    assert list_active(home, target, gig_id) == ()
    with pytest.raises(WatchlistUrlError) as excinfo:
        add_company_from_url("https://cvshealth.wd1.myworkdayjobs.com/CVS_External/job/1", home, target, gig_id)
    assert excinfo.value.code == "excluded_board"
    assert list_active(home, target, gig_id) == ()
