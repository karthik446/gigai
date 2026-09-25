"""Q2: the bundled company + ATS-board catalog (``company_catalog.py``).

Covers the loader (resource present, pinned digest, size budget, every
record parses), its own revision (never the gig catalog's
``CATALOG_REVISION``), the format-agnostic payload parser, the reproducible
builder, and -- under ``make test-wheel`` -- that the INSTALLED wheel ships
the same bytes (the same ``InstalledGigAI`` probe pattern
``test_assessment_core.py`` uses for the shipped instructions).
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import subprocess

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.catalog import CATALOG_REVISION
from gigai.scout.find_jobs.company_catalog import (
    COMPANY_CATALOG_REVISION,
    COMPANY_CATALOG_SHA256,
    COMPANY_CATALOG_SIZE_BUDGET_BYTES,
    CompanyCatalogError,
    CompanyH1B,
    build_catalog_resource,
    decode_catalog_bytes,
    load_company_catalog,
    parse_catalog_payload,
    parse_company_h1b,
    parse_company_record,
    read_catalog_resource_bytes,
)
from gigai.scout.find_jobs.contracts import ATSProvider, SourceKind
from tests.scenarios import InstalledGigAI


def test_shipped_catalog_matches_its_pinned_digest_and_size_budget() -> None:
    raw = read_catalog_resource_bytes()
    assert digest_imported_bytes(raw) == COMPANY_CATALOG_SHA256
    assert len(raw) <= COMPANY_CATALOG_SIZE_BUDGET_BYTES
    assert COMPANY_CATALOG_SIZE_BUDGET_BYTES == 5 * 1024 * 1024


def test_shipped_catalog_loads_with_its_own_revision_and_digest() -> None:
    catalog = load_company_catalog()
    assert catalog.revision == COMPANY_CATALOG_REVISION
    assert catalog.digest == COMPANY_CATALOG_SHA256
    assert catalog.size_bytes <= COMPANY_CATALOG_SIZE_BUDGET_BYTES
    # Its own revision: the gig catalog's revision tracks gig definitions and
    # is bumped per release; the company seed tracks the S26 run that made it.
    assert COMPANY_CATALOG_REVISION != CATALOG_REVISION
    assert COMPANY_CATALOG_REVISION.startswith("s26-")
    assert len(catalog.records) > 0
    # The sample seed carries case-only duplicate Ashby slugs (OpenAI/openai)
    # and two tokens with characters the board APIs can't take; those are
    # folded/skipped at load, counted here, never silently.
    assert catalog.skipped >= 0
    providers = {record.provider for record in catalog.records}
    assert providers <= {ATSProvider.GREENHOUSE, ATSProvider.LEVER, ATSProvider.ASHBY}
    keys = [(record.provider, record.board_token.lower()) for record in catalog.records]
    assert len(keys) == len(set(keys)), "case-insensitive duplicate boards in the shipped catalog"
    assert all(record.board_token == record.board_token.strip() and " " not in record.board_token and "&" not in record.board_token for record in catalog.records)
    for record in catalog.records:
        assert record.name and record.board_token and record.board_url.startswith("https://")
        assert record.watchlist_id == f"scout_watchlist:{record.provider.value}:{record.board_token}"


def test_shipped_catalog_summary_is_json_safe_and_counts_by_provider() -> None:
    catalog = load_company_catalog()
    summary = catalog.summary()
    json.dumps(summary)
    assert summary["records"] == len(catalog.records)
    assert sum(summary["by_provider"].values()) == len(catalog.records)  # type: ignore[union-attr]
    assert summary["revision"] == COMPANY_CATALOG_REVISION


def test_record_to_watchlist_entry_records_the_catalog_revision() -> None:
    record = load_company_catalog().records[0]
    entry = record.to_watchlist_entry(revision="rev-x", observed_at="2026-09-25T00:00:00Z")
    assert entry.watchlist_id == record.watchlist_id
    assert entry.company == record.name
    assert entry.state == "active"
    assert entry.first_seen.source_kind is SourceKind.ATS
    assert entry.first_seen.query_key == "catalog:rev-x"
    assert entry.first_seen.batch_id == "catalog-seed:rev-x"
    assert entry.first_seen.source_url == record.board_url
    # Round-trips through the frozen scout-watchlist:1 contract.
    assert type(entry).from_json(entry.to_json()) == entry


def test_parse_company_record_is_tolerant_and_fails_closed_on_non_boards() -> None:
    assert parse_company_record({"name": "X", "ats": "GreenHouse", "board_slug": "x"}) is not None
    assert parse_company_record({"name": "X", "ats": "workday", "board_slug": "x"}) is None
    assert parse_company_record({"name": "X", "ats": "lever"}) is None
    assert parse_company_record({"name": "X", "ats": "lever", "board_slug": "a/b"}) is None
    assert parse_company_record({"name": "X", "ats": "greenhouse", "board_slug": "harrison&star"}) is None
    assert parse_company_record({"name": "X", "ats": "ashby", "board_slug": "1st Formations"}) is None
    assert parse_company_record({"name": "X", "ats": "ashby", "board_slug": "Agentis-Capital-Advisors"}) is not None
    assert parse_company_record("not a record") is None
    record = parse_company_record({"provider": "ashbyhq", "board_token": "kong", "h1b": {"fiscal_years": ["2026"]}, "unknown_key": 1})
    assert record is not None
    assert record.provider is ATSProvider.ASHBY
    assert record.name == "kong"
    assert record.board_url == "https://jobs.ashbyhq.com/kong"
    assert record.h1b is True


def test_parse_catalog_payload_accepts_an_array_or_a_wrapping_object_and_dedupes() -> None:
    rows = [
        {"name": "A", "ats": "greenhouse", "board_slug": "a"},
        {"name": "A again", "ats": "greenhouse", "board_slug": "a"},
        {"name": "A upper", "ats": "greenhouse", "board_slug": "A"},
        {"name": "B", "ats": "lever", "board_slug": "b"},
        {"name": "bad", "ats": "workday", "board_slug": "z"},
    ]
    from_array, skipped_array = parse_catalog_payload(rows)
    from_object, skipped_object = parse_catalog_payload({"companies": rows})
    assert [r.board_token for r in from_array] == ["a", "b"]
    assert skipped_array == 3
    assert from_object == from_array and skipped_object == skipped_array
    with pytest.raises(CompanyCatalogError) as excinfo:
        parse_catalog_payload({"nope": []})
    assert excinfo.value.code == "bad_shape"
    with pytest.raises(CompanyCatalogError):
        parse_catalog_payload("[]")


def test_decode_catalog_bytes_reports_bad_gzip_and_bad_json() -> None:
    with pytest.raises(CompanyCatalogError) as bad_gzip:
        decode_catalog_bytes(b"not gzip", revision="r")
    assert bad_gzip.value.code == "bad_gzip"
    with pytest.raises(CompanyCatalogError) as bad_json:
        decode_catalog_bytes(gzip.compress(b"{not json"), revision="r")
    assert bad_json.value.code == "bad_json"


def test_build_catalog_resource_is_reproducible_and_size_independent(tmp_path: Path) -> None:
    source = tmp_path / "companies.json"
    rows = [{"name": f"Co {i}", "ats": "greenhouse", "board_slug": f"co{i}", "hq_country": "US"} for i in range(1000)]
    source.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    first = build_catalog_resource(source, tmp_path / "one.json.gz")
    second = build_catalog_resource(source, tmp_path / "two.json.gz")
    assert first == second
    assert (tmp_path / "one.json.gz").read_bytes() == (tmp_path / "two.json.gz").read_bytes()
    catalog = decode_catalog_bytes((tmp_path / "one.json.gz").read_bytes(), revision="test")
    assert len(catalog.records) == 1000
    assert catalog.digest == first
    # The wrapping-object seed shape builds identically.
    source.write_text(json.dumps({"companies": rows}), encoding="utf-8")
    assert build_catalog_resource(source, tmp_path / "three.json.gz") == first
    with pytest.raises(CompanyCatalogError):
        source.write_text("[]", encoding="utf-8")
        build_catalog_resource(source, tmp_path / "empty.json.gz")


def test_installed_interpreter_ships_the_pinned_company_catalog(installed_gigai: InstalledGigAI) -> None:
    """Under ``make test-wheel`` this runs the wheel venv's Python (the resource
    must ship in the wheel via package-data); in the source lane, the dev venv's."""

    python = installed_gigai.command.executable.parent / "python"
    if not python.exists():
        pytest.skip("no interpreter next to the gigai console script")
    probe = (
        "from importlib import resources\n"
        "from gigai.canonical import digest_imported_bytes\n"
        "from gigai.scout.find_jobs.company_catalog import COMPANY_CATALOG_SHA256, COMPANY_CATALOG_SIZE_BUDGET_BYTES, load_company_catalog\n"
        "raw = resources.files('gigai.scout').joinpath('data/companies.json.gz').read_bytes()\n"
        "assert digest_imported_bytes(raw) == COMPANY_CATALOG_SHA256, digest_imported_bytes(raw)\n"
        "assert len(raw) <= COMPANY_CATALOG_SIZE_BUDGET_BYTES\n"
        "catalog = load_company_catalog()\n"
        "assert len(catalog.records) > 0\n"
        "print(catalog.revision, catalog.digest, len(catalog.records))\n"
    )
    result = subprocess.run(
        [os.fspath(python), "-c", probe], capture_output=True, text=True, check=False, shell=False,
        cwd=Path(python).parent,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[:2] == [COMPANY_CATALOG_REVISION, COMPANY_CATALOG_SHA256]


@pytest.fixture
def installed_gigai() -> InstalledGigAI:
    return InstalledGigAI.current()


# --- Q4b-data: the H-1B aggregate next to the bool -------------------------

_FULL_H1B = {
    "fiscal_years": ["2025", "2026"],
    "approvals": 12,
    "denials": 1,
    "naics": "54 - Professional, Scientific, and Technical Services",
    "matched_name": "KONG INC",
    "match_confidence": "normalized",
}


def test_parse_company_h1b_handles_the_full_schema_shape_a_thin_shape_and_no_match() -> None:
    assert parse_company_h1b(_FULL_H1B) == CompanyH1B(approvals=12, fiscal_years=("2025", "2026"), denials=1)
    assert parse_company_h1b(_FULL_H1B).to_json() == {"approvals": 12, "fiscal_years": ["2025", "2026"], "denials": 1}
    # A thinner record (a key missing, or an older sample) still yields an
    # aggregate; a missing ``denials`` stays out of the JSON (never null).
    assert parse_company_h1b({"fiscal_years": ["2026"]}) == CompanyH1B(approvals=0, fiscal_years=("2026",), denials=None)
    assert parse_company_h1b({"fiscal_years": ["2026"]}).to_json() == {"approvals": 0, "fiscal_years": ["2026"]}
    assert parse_company_h1b({"approvals": 3}) == CompanyH1B(approvals=3, fiscal_years=())
    assert parse_company_h1b({"approvals": 3, "denials": 0}).to_json() == {"approvals": 3, "fiscal_years": [], "denials": 0}
    assert parse_company_h1b({"approvals": "3", "fiscal_years": [2026, " 2025 ", ""], "denials": "1"}) == CompanyH1B(approvals=0, fiscal_years=("2025",))
    assert parse_company_h1b({"approvals": -4, "fiscal_years": "2026", "denials": -2}) == CompanyH1B(approvals=0, fiscal_years=(), denials=0)
    # No match recorded: null / absent / the bare bool some rows may carry.
    assert parse_company_h1b(None) is None
    assert parse_company_h1b(False) is None
    assert parse_company_h1b(True) is None
    assert parse_company_h1b("yes") is None


def test_parse_company_record_keeps_the_h1b_bool_and_adds_the_aggregate() -> None:
    full = parse_company_record({"name": "Kong", "ats": "ashby", "board_slug": "kong", "h1b": _FULL_H1B})
    assert full is not None and full.h1b is True
    assert full.h1b_summary == CompanyH1B(approvals=12, fiscal_years=("2025", "2026"), denials=1)
    thin = parse_company_record({"name": "Thin", "ats": "lever", "board_slug": "thin", "h1b": {"fiscal_years": ["2026"], "approvals": 1}})
    assert thin is not None and thin.h1b is True and thin.h1b_summary == CompanyH1B(approvals=1, fiscal_years=("2026",))
    none = parse_company_record({"name": "None", "ats": "greenhouse", "board_slug": "none", "h1b": None})
    assert none is not None and none.h1b is False and none.h1b_summary is None
    flag = parse_company_record({"name": "Flag", "ats": "greenhouse", "board_slug": "flag", "h1b": True})
    assert flag is not None and flag.h1b is True and flag.h1b_summary is None
    absent = parse_company_record({"name": "Absent", "ats": "greenhouse", "board_slug": "absent"})
    assert absent is not None and absent.h1b is False and absent.h1b_summary is None
    # The existing JSON view of a record is unchanged (the aggregate is not added to it).
    assert full.to_json()["h1b"] is True and "h1b_summary" not in full.to_json()


def test_catalog_h1b_by_board_indexes_only_records_with_an_aggregate_case_insensitively() -> None:
    rows = [
        {"name": "Kong", "ats": "ashby", "board_slug": "Kong", "h1b": _FULL_H1B},
        {"name": "None", "ats": "greenhouse", "board_slug": "none", "h1b": None},
        {"name": "Flag", "ats": "lever", "board_slug": "flag", "h1b": True},
    ]
    decoded = decode_catalog_bytes(gzip.compress(json.dumps(rows).encode("utf-8")), revision="t")
    assert [record.h1b for record in decoded.records] == [True, False, True]
    assert decoded.h1b_by_board() == {(ATSProvider.ASHBY, "kong"): CompanyH1B(approvals=12, fiscal_years=("2025", "2026"), denials=1)}


def test_shipped_catalog_carries_h1b_aggregates_for_every_true_flag_with_an_object() -> None:
    catalog = load_company_catalog()
    with_summary = [record for record in catalog.records if record.h1b_summary is not None]
    assert with_summary, "the shipped sample records at least one USCIS match"
    for record in with_summary:
        assert record.h1b is True
        assert record.h1b_summary.approvals >= 0
        assert record.h1b_summary.denials is not None and record.h1b_summary.denials >= 0  # the sample carries denials
        assert all(isinstance(year, str) and year for year in record.h1b_summary.fiscal_years)
    assert len(catalog.h1b_by_board()) == len(with_summary)
