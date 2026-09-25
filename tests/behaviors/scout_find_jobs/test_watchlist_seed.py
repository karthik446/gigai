"""Q2: seeding the Scout watchlist from the bundled company catalog.

``watchlist.seed_watchlist_from_catalog`` against a real journaled workpad
(the same ``_fixture`` ``test_watchlist.py`` uses): the pref filters
(countries, excluded companies, watched companies), idempotence (a second
call adds nothing and writes nothing), existing entries kept untouched,
and the journal commit (records + one receipt, workpad clean afterwards).
"""

from __future__ import annotations

import json
from pathlib import Path

from gigai.scout.find_jobs.company_catalog import CompanyCatalog, CompanyRecord, load_company_catalog
from gigai.scout.find_jobs.contracts import ATSProvider, WatchlistEntry
from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs
from gigai.scout.find_jobs.watchlist import (
    JournalWatchlistClient,
    catalog_records_for_prefs,
    list_active,
    seed_watchlist_from_catalog,
)
from gigai.workpad import resolve_workpad
from tests.behaviors.scout_research.test_scout06_research_inputs import _fixture
from tests.support.workpad_assertions import assert_managed_workpad_clean


def _record(
    name: str, provider: ATSProvider, token: str, *, hq: str | None = "US", us_postings: int | None = 1,
    domain: str | None = None, suspect: int | None = None,
) -> CompanyRecord:
    return CompanyRecord(
        name=name,
        provider=provider,
        board_token=token,
        board_url=f"https://example.test/{token}",
        hq_country=hq,
        domain=domain,
        us_posting_count=us_postings,
        staffing_suspect=suspect is not None,
        staffing_confidence=suspect,
    )


def _catalog(*records: CompanyRecord, revision: str = "test-rev") -> CompanyCatalog:
    return CompanyCatalog(revision=revision, digest="sha256:" + "d" * 64, size_bytes=1, records=tuple(records), skipped=0)


_RECORDS = (
    _record("Acme", ATSProvider.GREENHOUSE, "acme"),
    _record("Bright Ltd", ATSProvider.LEVER, "bright", hq="GB", us_postings=0),
    _record("Kong", ATSProvider.ASHBY, "kong", domain="konghq.com"),
    _record("Orbit", ATSProvider.ASHBY, "orbit", hq="DE", us_postings=0),
    _record("Nowhere", ATSProvider.GREENHOUSE, "nowhere", hq=None, us_postings=3),
)


def _existing_entry() -> WatchlistEntry:
    raw = json.loads((Path(__file__).parent / "fixtures/fixture-watchlist-v1.json").read_text())
    return WatchlistEntry.from_json(raw["entries"][0])  # scout_watchlist:greenhouse:acme, company "Acme"


# --- the pure filter -------------------------------------------------------


def test_filter_no_countries_pref_keeps_everything_not_excluded() -> None:
    kept, by_country, by_company, _by_staffing = catalog_records_for_prefs(_RECORDS, DiscoveryPrefs())
    assert [r.board_token for r in kept] == ["acme", "bright", "kong", "orbit", "nowhere"]
    assert (by_country, by_company) == (0, 0)


def test_filter_countries_keeps_hq_matches_and_us_postings_for_us() -> None:
    kept, by_country, by_company, _by_staffing = catalog_records_for_prefs(_RECORDS, DiscoveryPrefs(countries=("US",)))
    # "nowhere" has no hq_country but has US postings on record -> kept for US.
    assert [r.board_token for r in kept] == ["acme", "kong", "nowhere"]
    assert (by_country, by_company) == (2, 0)
    kept_gb, by_country_gb, _, _ = catalog_records_for_prefs(_RECORDS, DiscoveryPrefs(countries=("GB",)))
    assert [r.board_token for r in kept_gb] == ["bright"]
    assert by_country_gb == 4


def test_filter_excludes_by_name_slug_or_domain_case_and_punctuation_insensitive() -> None:
    prefs = DiscoveryPrefs(countries=("US",), exclude_companies=("ACME", "Kong HQ"))
    kept, by_country, by_company, _by_staffing = catalog_records_for_prefs(_RECORDS, prefs)
    assert [r.board_token for r in kept] == ["nowhere"]
    assert by_company == 2
    assert by_country == 2


def test_filter_watch_companies_bypass_the_country_filter_but_not_excludes() -> None:
    prefs = DiscoveryPrefs(countries=("US",), watch_companies=("Orbit", "bright"), exclude_companies=("orbit",))
    kept, _by_country, by_company, _by_staffing = catalog_records_for_prefs(_RECORDS, prefs)
    assert "bright" in [r.board_token for r in kept]
    assert "orbit" not in [r.board_token for r in kept]
    assert by_company == 1


# --- the journaled seeding function ---------------------------------------


def test_seeding_adds_filtered_boards_keeps_existing_and_is_idempotent(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    existing = _existing_entry()
    JournalWatchlistClient(home, target, gig_id).add_to_watchlist(existing)
    prefs = DiscoveryPrefs(countries=("US",), exclude_companies=("Nowhere",))

    first = seed_watchlist_from_catalog(home, target, gig_id, prefs=prefs, catalog=_catalog(*_RECORDS), now="2026-09-25T00:00:00Z")
    assert first.catalog_revision == "test-rev"
    assert first.catalog_records == 5
    assert first.eligible == 2  # acme (already present) + kong
    assert first.added == 1
    assert first.already_present == 1
    assert first.excluded_by_country == 2
    assert first.excluded_by_company == 1
    assert first.added_watchlist_ids == ("scout_watchlist:ashby:kong",)
    assert first.receipt_path is not None and first.receipt_path.startswith("records/operations/scout_watchlist_seed-")

    active = {entry.watchlist_id: entry for entry in list_active(home, target, gig_id)}
    assert set(active) == {"scout_watchlist:greenhouse:acme", "scout_watchlist:ashby:kong"}
    # The pre-existing entry is byte-for-byte the one that was there before.
    assert active["scout_watchlist:greenhouse:acme"] == existing
    seeded = active["scout_watchlist:ashby:kong"]
    assert seeded.company == "Kong"
    assert seeded.first_seen.query_key == "catalog:test-rev"
    assert seeded.first_seen.observed_at == "2026-09-25T00:00:00Z"

    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    receipt = json.loads((resolved.path / first.receipt_path).read_text(encoding="utf-8"))
    assert receipt["operation"] == "scout_watchlist_seed"
    assert receipt["catalog_revision"] == "test-rev"
    assert receipt["catalog_digest"] == "sha256:" + "d" * 64
    assert receipt["added"] == 1 and receipt["already_present"] == 1
    assert [ref["path"] for ref in receipt["artifact_refs"]] == ["records/scout-watchlist/scout_watchlist:ashby:kong.json"]
    assert_managed_workpad_clean(resolved.path)

    # Idempotent: nothing to add, nothing written (no new receipt).
    second = seed_watchlist_from_catalog(home, target, gig_id, prefs=prefs, catalog=_catalog(*_RECORDS))
    assert second.added == 0
    assert second.already_present == 2
    assert second.receipt_path is None
    receipts = sorted((resolved.path / "records" / "operations").glob("scout_watchlist_seed-*.json"))
    assert len(receipts) == 1
    assert {entry.watchlist_id for entry in list_active(home, target, gig_id)} == set(active)
    assert_managed_workpad_clean(resolved.path)


def test_a_prefs_change_only_adds_never_removes(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    catalog = _catalog(*_RECORDS)
    seed_watchlist_from_catalog(home, target, gig_id, prefs=DiscoveryPrefs(countries=("US",)), catalog=catalog)
    assert {e.board_token for e in list_active(home, target, gig_id)} == {"acme", "kong", "nowhere"}
    # Now the operator excludes Kong and adds GB: kong STAYS (existing entries
    # are kept), bright is added.
    later = seed_watchlist_from_catalog(
        home, target, gig_id, prefs=DiscoveryPrefs(countries=("US", "GB"), exclude_companies=("kong",)), catalog=catalog
    )
    assert later.added == 1 and later.added_watchlist_ids == ("scout_watchlist:lever:bright",)
    assert {e.board_token for e in list_active(home, target, gig_id)} == {"acme", "kong", "nowhere", "bright"}


def _seeded_ids(workpad: Path) -> set[str]:
    """The seeded watchlist ids, read from the committed record FILENAMES.

    Not ``list_active``: over the full catalog (10,370 seeded records) a journal
    snapshot costs ~6 minutes of CPU today -- ``_capture_committed_snapshot``
    re-parses the seeding handoff (every artifact ref) once PER artifact
    (catalog-repin measurement, 2026-09-25; flagged to the coordinator). The
    per-record round trip is covered by the small-catalog tests above.
    """

    return {path.stem for path in (workpad / "records" / "scout-watchlist").glob("scout_watchlist:*.json")}


def test_seeding_the_shipped_catalog_is_one_commit_and_leaves_the_workpad_clean(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    shipped = load_company_catalog()
    result = seed_watchlist_from_catalog(home, target, gig_id, prefs=DiscoveryPrefs(countries=("US",)))
    assert result.catalog_revision == shipped.revision
    assert result.catalog_digest == shipped.digest
    assert result.added == result.eligible > 0
    assert result.added <= len(shipped.records)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    assert len(_seeded_ids(resolved.path)) == result.added == len(result.added_watchlist_ids)
    assert_managed_workpad_clean(resolved.path)
    assert len(sorted((resolved.path / "records" / "operations").glob("scout_watchlist_seed-*.json"))) == 1


# --- catalog-repin amendment: staffing suspects are not seeded by default ----
# (orchestrator msg_b3ca2e58203a, 2026-09-25: the 48 rev3 ``staffing_suspect``
# records stay in the catalog with the flag; default seeding skips them; the
# operator can still add any such board explicitly.)

_SUSPECT = _record("Mthree Recruiting", ATSProvider.GREENHOUSE, "mthree", suspect=52)


def test_a_staffing_suspect_record_is_not_seeded_and_is_counted() -> None:
    records = (*_RECORDS, _SUSPECT)
    kept, by_country, by_company, by_staffing = catalog_records_for_prefs(records, DiscoveryPrefs(countries=("US",)))
    assert [r.board_token for r in kept] == ["acme", "kong", "nowhere"]
    assert (by_country, by_company, by_staffing) == (2, 0, 1)
    # No country pref: still skipped -- the rule does not depend on the filter.
    kept_all, _, _, by_staffing_all = catalog_records_for_prefs(records, DiscoveryPrefs())
    assert "mthree" not in [r.board_token for r in kept_all] and by_staffing_all == 1
    # An explicit exclude of the same company counts as the exclude, not the flag.
    _, _, by_company_x, by_staffing_x = catalog_records_for_prefs(records, DiscoveryPrefs(exclude_companies=("mthree",)))
    assert (by_company_x, by_staffing_x) == (1, 0)
    # A watched company is kept even when flagged: the user asked for it by name.
    kept_w, _, _, by_staffing_w = catalog_records_for_prefs(records, DiscoveryPrefs(countries=("US",), watch_companies=("Mthree Recruiting",)))
    assert "mthree" in [r.board_token for r in kept_w] and by_staffing_w == 0


def test_seeding_skips_staffing_suspects_and_the_receipt_says_so(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    catalog = _catalog(*_RECORDS, _SUSPECT)
    result = seed_watchlist_from_catalog(home, target, gig_id, prefs=DiscoveryPrefs(countries=("US",)), catalog=catalog, now="2026-09-25T00:00:00Z")
    assert result.catalog_records == 6
    assert result.eligible == result.added == 3
    assert result.excluded_as_staffing_suspect == 1
    assert result.to_json()["excluded_as_staffing_suspect"] == 1
    active = {entry.board_token for entry in list_active(home, target, gig_id)}
    assert active == {"acme", "kong", "nowhere"}
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    receipt = json.loads(next(iter(sorted((resolved.path / "records" / "operations").glob("scout_watchlist_seed-*.json")))).read_text())
    assert receipt["excluded_as_staffing_suspect"] == 1 and receipt["added"] == 3

    # The operator can still add the very same board explicitly (Q1's
    # add-by-URL path), and a later seeding pass leaves it in place.
    from gigai.scout.find_jobs.watchlist import add_company_from_url

    added = add_company_from_url("https://job-boards.greenhouse.io/mthree", home, target, gig_id)
    assert added.board_token == "mthree"
    again = seed_watchlist_from_catalog(home, target, gig_id, prefs=DiscoveryPrefs(countries=("US",)), catalog=catalog)
    assert again.added == 0 and again.excluded_as_staffing_suspect == 1
    assert {entry.board_token for entry in list_active(home, target, gig_id)} == {"acme", "kong", "nowhere", "mthree"}
    assert_managed_workpad_clean(resolved.path)


def test_the_shipped_catalog_seeds_none_of_its_staffing_suspects(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    shipped = load_company_catalog()
    suspects = {record.watchlist_id for record in shipped.staffing_suspects()}
    assert len(suspects) == 48
    result = seed_watchlist_from_catalog(home, target, gig_id, prefs=DiscoveryPrefs(countries=("US",)))
    assert result.excluded_as_staffing_suspect == 48
    assert result.added == result.eligible == len(shipped.records) - 48 == 10370
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    seeded = _seeded_ids(resolved.path)
    assert len(seeded) == 10370 and suspects.isdisjoint(seeded)
    assert set(result.added_watchlist_ids) == seeded
