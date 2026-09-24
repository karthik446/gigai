"""Behavior tests for the pure acquire/assess selection filters (P1, U12/U19)."""

from __future__ import annotations

import pytest

from gigai.scout.find_jobs.contracts import (
    ATSProvider,
    FindJobsConfig,
    NotAssessedReason,
    PostingRow,
    SourceKind,
    SourceToggles,
    SponsorshipStatus,
)
from gigai.scout.find_jobs.filters import (
    country_match,
    exclusion_reason,
    location_mismatch_detail,
    sponsorship_from_text,
)


def _config(**overrides: object) -> FindJobsConfig:
    values: dict[str, object] = {
        "roles": ("software engineer",),
        "merged_queries": ("software engineer",),
        "location": "Denver, CO",
        "remote": True,
        "published_after": None,
        "sources": SourceToggles(exa=True, ats=True, hiringcafe=False),
    }
    values.update(overrides)
    return FindJobsConfig(**values)


def _row(**overrides: object) -> PostingRow:
    values: dict[str, object] = {
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "normalized_url": "https://boards.greenhouse.io/acme/jobs/1",
        "provider": ATSProvider.GREENHOUSE,
        "board_token": "acme",
        "company": "acme",
        "title": "Software Engineer",
        "location": "Denver, CO",
        "published_at": None,
        "content_sha256": None,
        "source_kind": SourceKind.ATS,
        "query_key": "software engineer",
    }
    values.update(overrides)
    return PostingRow(**values)


# --- country_match -------------------------------------------------------


@pytest.mark.parametrize(
    "location,expected",
    [
        ("San Francisco, CA - US", True),
        ("Arvada, CO - US", True),
        ("Sunnyvale, CA - US", True),
        ("IND - Pune", False),
        ("USA - Tempe, AZ", True),
        ("CZE - Prague", False),
        ("IND - Chennai", False),
        ("USA - New York, NY", True),
        ("Bengaluru", False),
        ("Seattle, USA", True),
        ("Seoul, South Korea", False),
        ("Mountain View, USA; Seattle, USA; Seoul, South Korea", True),
        ("Bengaluru; Hyderabad", False),
        ("Mountain View, USA", True),
        ("India", False),
        ("Singapore; Singapore, Singapore", False),
        ("Hyderabad", False),
        ("Mountain View, USA; Seattle, USA", True),
        ("Singapore, Singapore", False),
        ("Hyderabad, India", False),
        ("London, United Kingdom - Deliveroo", False),
        ("Remote, Canada; Remote, Poland; Remote, United Kingdom; Remote, United States", True),
        ("Remote, Canada; Remote, United Kingdom; Remote, United States", True),
        ("Remote, Canada; Remote, United States", True),
        ("Bangalore, India", False),
        ("Remote, Canada; Remote, United Kingdom; Remote, US", True),
        ("Remote, United Kingdom", False),
        ("Remote, Poland", False),
        ("Berlin, Berlin, Germany", False),
        ("Toronto, Ontario, Canada", False),
        ("Warszawa, Masovian Voivodeship, Poland", False),
        ("Saarbrücken, Germany", False),
        ("Remote - USA", True),
        ("Remote - Canada", False),
        ("Atlanta, Georgia", True),
        ("Bengaluru, India", False),
        ("Madrid, Spain", False),
        ("London, United Kingdom", False),
        ("", None),
        ("Denver, CO", True),
        ("New York, NY", True),
    ],
)
def test_country_match_against_real_uat_locations(location: str, expected: bool | None) -> None:
    assert country_match(location, ("US",)) is expected


def test_country_match_no_countries_configured_matches_everything() -> None:
    assert country_match("Bengaluru, India", ()) is True
    assert country_match("", ()) is True
    assert country_match(None, ()) is True


def test_country_match_none_location_is_ambiguous() -> None:
    assert country_match(None, ("US",)) is None


def test_country_match_multiple_configured_countries() -> None:
    assert country_match("Bengaluru, India", ("US", "IN")) is True
    assert country_match("London, United Kingdom", ("US", "IN")) is False


def test_country_match_bare_ca_does_not_force_canada_or_california_exclusion() -> None:
    # "CA" alone is ambiguous between the Canada abbreviation and the
    # California state abbreviation; both country filters can legitimately
    # treat it as a candidate match rather than silently excluding it.
    assert country_match("Some City, CA", ("US",)) is True


# --- P1b: the operator's real run-2 numbers (174 postings; US filter gave
# True 101 / False 41 / None 32) showed bare non-US tech-hub city names
# (Bengaluru x18+, Hyderabad, ...) landing in the ambiguous/None bucket
# instead of resolving to a definite non-US match. -----------------------


@pytest.mark.parametrize(
    "location,expected",
    [
        ("Bengaluru", False),
        ("Bengaluru; Hyderabad", False),
        ("IND - Pune", False),
        ("CZE - Prague", False),
        ("Seoul, South Korea", False),
        ("San Francisco, CA - US", True),
        ("USA - Tempe, AZ", True),
        ("Arvada, CO - US", True),
        ("London, United Kingdom - Deliveroo", False),
        ("Remote, Canada; Remote, United States", True),
        ("", None),
    ],
)
def test_country_match_p1b_operator_real_strings(location: str, expected: bool | None) -> None:
    assert country_match(location, ("US",)) is expected


@pytest.mark.parametrize(
    "city,expected_country",
    [
        ("Hyderabad", "IN"), ("Pune", "IN"), ("Chennai", "IN"), ("Mumbai", "IN"),
        ("Gurgaon", "IN"), ("Gurugram", "IN"), ("Noida", "IN"), ("Delhi", "IN"),
        ("London", "GB"), ("Manchester", "GB"), ("Edinburgh", "GB"), ("Dublin", "IE"),
        ("Berlin", "DE"), ("Munich", "DE"), ("Hamburg", "DE"), ("Paris", "FR"),
        ("Amsterdam", "NL"), ("Madrid", "ES"), ("Barcelona", "ES"), ("Lisbon", "PT"),
        ("Warsaw", "PL"), ("Krakow", "PL"), ("Prague", "CZ"), ("Stockholm", "SE"),
        ("Zurich", "CH"), ("Tel Aviv", "IL"), ("Toronto", "CA"), ("Vancouver", "CA"),
        ("Montreal", "CA"), ("Mexico City", "MX"), ("São Paulo", "BR"),
        ("Singapore", "SG"), ("Seoul", "KR"), ("Tokyo", "JP"), ("Sydney", "AU"),
        ("Melbourne", "AU"), ("Bangalore", "IN"), ("Bengaluru", "IN"),
    ],
)
def test_country_match_bare_tech_hub_cities(city: str, expected_country: str) -> None:
    assert country_match(city, (expected_country,)) is True
    if expected_country != "US":
        assert country_match(city, ("US",)) is False


def test_country_match_bare_city_case_insensitive() -> None:
    assert country_match("bengaluru", ("US",)) is False
    assert country_match("HYDERABAD", ("US",)) is False
    assert country_match("ZuRiCh", ("CH",)) is True


def test_country_match_bare_city_diacritic_insensitive() -> None:
    assert country_match("São Paulo", ("US",)) is False
    assert country_match("Sao Paulo", ("BR",)) is True
    assert country_match("Zürich", ("CH",)) is True
    assert country_match("Zurich", ("CH",)) is True


def test_country_match_genuinely_ambiguous_locations_stay_none() -> None:
    assert country_match("Remote", ("US",)) is None
    assert country_match("", ("US",)) is None
    assert country_match(None, ("US",)) is None


# --- B1 (0.1.8.1, r1 coordinator review): region tokens must never count
# as a single-country MATCH, and a location that resolves to ONLY region
# tokens (no country/state/city anywhere in it) is a definite NON-match
# (False), not ambiguous -- the operator's own UAT complaint: "AMER" rows
# passed a US-only filter because it fell into the ambiguous bucket and
# exclusion_reason never excludes ambiguous locations. r0 of this test
# asserted the pre-fix (None/ambiguous) behavior; r1 corrects it to the
# operator's actual rule. -----------------------------------------------


@pytest.mark.parametrize(
    "location",
    ["AMER", "EMEA", "APAC", "LATAM", "Remote - Americas", "Remote, AMER"],
)
def test_country_match_region_only_locations_are_a_definite_non_match(location: str) -> None:
    assert country_match(location, ("US",)) is False


def test_country_match_region_token_with_matching_country_still_matches() -> None:
    # A region token alongside a real country match still matches on the
    # country -- the region token doesn't poison an otherwise-good match.
    assert country_match("AMER; Denver, CO", ("US",)) is True
    assert country_match("Remote - US", ("US",)) is True


def test_country_match_region_token_with_non_matching_country_is_false() -> None:
    assert country_match("AMER; Bengaluru, India", ("US",)) is False


def test_country_match_multiple_region_tokens_still_non_match() -> None:
    assert country_match("EMEA; APAC", ("US",)) is False


def test_country_match_region_token_is_whole_segment_only_not_substring() -> None:
    # "AMER" must not fire as a substring of an unrelated word/company name.
    assert country_match("AMERica Story Inc", ("US",)) is None


def test_country_match_region_token_alongside_unrecognized_token_still_non_match() -> None:
    # A region token plus a genuinely unrecognized token (no country
    # signal at all from either) is still a definite region-only non-match.
    assert country_match("AMER; Remote", ("US",)) is False


def test_country_match_unrecognized_token_without_region_signal_stays_ambiguous() -> None:
    # No region token at all: an unrelated unrecognized token keeps its
    # pre-existing ambiguous (None) behavior, unaffected by this fix.
    assert country_match("Remote", ("US",)) is None
    assert country_match("Some Unknown Place", ("US",)) is None


# --- 0.1.8.1: pycountry-backed table still resolves the same real UAT
# strings (drop-in replacement for the hand-typed alias tables). ----------


def test_country_match_great_britain_demonym_still_folds_to_gb() -> None:
    assert country_match("Great Britain", ("GB",)) is True
    assert country_match("Great Britain", ("US",)) is False


def test_country_match_czechia_and_czech_republic_both_resolve() -> None:
    assert country_match("Czechia", ("CZ",)) is True
    assert country_match("Czech Republic", ("CZ",)) is True


# --- structured_countries: trusted field wins outright, no text fallback --


def test_country_match_structured_field_wins_over_conflicting_text() -> None:
    # Structured says US; free text (deliberately wrong/garbage here) is
    # never even consulted once structured data is supplied.
    assert country_match("Seoul, South Korea", ("US",), structured_countries=("US",)) is True


def test_country_match_structured_field_empty_tuple_is_trusted_not_ambiguous() -> None:
    # An empty structured result (field present, resolved to no recognized
    # country) is a real "no match" -- not "fall back to parsing text".
    assert country_match("", ("US",), structured_countries=()) is False


def test_country_match_structured_none_falls_back_to_text_parsing() -> None:
    assert country_match("Denver, CO", ("US",), structured_countries=None) is True


# --- sponsorship_from_text ------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("We are unable to sponsor work visas for this role.", SponsorshipStatus.NOT_OFFERED),
        ("This role does not offer visa sponsorship.", SponsorshipStatus.NOT_OFFERED),
        ("No visa sponsorship is available for this position.", SponsorshipStatus.NOT_OFFERED),
        ("You must be authorized to work in the US without sponsorship.", SponsorshipStatus.NOT_OFFERED),
        ("We are not able to provide sponsorship for this position.", SponsorshipStatus.NOT_OFFERED),
        ("Visa sponsorship available for qualified candidates.", SponsorshipStatus.OFFERED),
        ("We will sponsor visas for the right candidate.", SponsorshipStatus.OFFERED),
        ("Great benefits and PTO.", SponsorshipStatus.UNKNOWN),
        (None, SponsorshipStatus.UNKNOWN),
        ("", SponsorshipStatus.UNKNOWN),
    ],
)
def test_sponsorship_from_text(text: str | None, expected: SponsorshipStatus) -> None:
    assert sponsorship_from_text(text) is expected


def test_sponsorship_from_text_not_offered_wins_when_both_phrases_present() -> None:
    text = "We will sponsor some roles, but this one: unable to sponsor a visa."
    assert sponsorship_from_text(text) is SponsorshipStatus.NOT_OFFERED


def test_sponsorship_case_insensitive() -> None:
    assert sponsorship_from_text("UNABLE TO SPONSOR a visa.") is SponsorshipStatus.NOT_OFFERED


# --- P1b: additional positive phrasings (the operator's sample sentence
# "Visa sponsorship is available." previously fell through to unknown) and
# confirming the new negatives still win over the new positives. ----------


@pytest.mark.parametrize(
    "text",
    [
        "Visa sponsorship is available.",
        "Sponsorship available for the right candidate.",
        "We sponsor visas for international candidates.",
        "We are able to sponsor work visas.",
        "We will provide sponsorship for this role.",
        "H-1B sponsorship available for qualified candidates.",
        "Open to sponsoring the right candidate.",
    ],
)
def test_sponsorship_p1b_offered_phrases(text: str) -> None:
    assert sponsorship_from_text(text) is SponsorshipStatus.OFFERED


@pytest.mark.parametrize(
    "text",
    [
        "We are not able to sponsor visas for this role.",
        "Unable to sponsor visas at this time.",
        "No sponsorship for this position.",
        "This role comes without sponsorship.",
    ],
)
def test_sponsorship_p1b_not_offered_phrases(text: str) -> None:
    assert sponsorship_from_text(text) is SponsorshipStatus.NOT_OFFERED


def test_sponsorship_p1b_negative_wins_over_new_positive_phrases() -> None:
    text = "We are open to sponsoring in general, but this role: unable to sponsor a visa."
    assert sponsorship_from_text(text) is SponsorshipStatus.NOT_OFFERED


# --- 0.1.8.1 B3: the ticket's required phrase coverage. "will not sponsor"
# was missing outright; the rest were already covered but are asserted here
# together as the ticket's explicit checklist, against real evidence-run
# posting shapes once html_to_text's entity-decoding bug (B3's other half,
# see test_ats_board_clients.py) stopped mangling the text these run
# through in production. --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "We will not sponsor employment visas for this role.",
        "Unable to sponsor work visas at this time.",
        "You must be authorized to work in the US without sponsorship.",
        "Must be authorized to work in the United States without visa sponsorship.",
        "Sponsorship is not available for this position.",
    ],
)
def test_sponsorship_b3_required_not_offered_phrases(text: str) -> None:
    assert sponsorship_from_text(text) is SponsorshipStatus.NOT_OFFERED


@pytest.mark.parametrize(
    "text",
    [
        "Sponsorship is available for the right candidate.",
        "Visa sponsorship available for qualified candidates.",
        "We sponsor international candidates.",
    ],
)
def test_sponsorship_b3_required_offered_phrases(text: str) -> None:
    assert sponsorship_from_text(text) is SponsorshipStatus.OFFERED


def test_sponsorship_b3_visa_required_excludes_not_offered() -> None:
    row = _row(sponsorship=sponsorship_from_text("We will not sponsor employment visas for this role."))
    config = _config(visa_sponsorship_required=True)
    assert exclusion_reason(row, config) is NotAssessedReason.SPONSORSHIP_EXCLUDED


# --- exclusion_reason ------------------------------------------------------


def test_exclusion_reason_none_when_no_filters_configured() -> None:
    row = _row(location="Bengaluru, India")
    assert exclusion_reason(row, _config()) is None


def test_exclusion_reason_location_mismatch() -> None:
    row = _row(location="Bengaluru, India")
    config = _config(countries=("US",))
    assert exclusion_reason(row, config) is NotAssessedReason.LOCATION_MISMATCH


def test_exclusion_reason_ambiguous_location_is_not_excluded() -> None:
    row = _row(location="Remote")  # no recognized country/city/state signal at all
    config = _config(countries=("US",))
    assert exclusion_reason(row, config) is None


def test_exclusion_reason_matching_country_is_not_excluded() -> None:
    row = _row(location="Denver, CO")
    config = _config(countries=("US",))
    assert exclusion_reason(row, config) is None


def test_exclusion_reason_sponsorship_excluded() -> None:
    row = _row(sponsorship=SponsorshipStatus.NOT_OFFERED)
    config = _config(visa_sponsorship_required=True)
    assert exclusion_reason(row, config) is NotAssessedReason.SPONSORSHIP_EXCLUDED


def test_exclusion_reason_sponsorship_not_required_keeps_not_offered_row() -> None:
    row = _row(sponsorship=SponsorshipStatus.NOT_OFFERED)
    config = _config(visa_sponsorship_required=False)
    assert exclusion_reason(row, config) is None


def test_exclusion_reason_unknown_sponsorship_is_not_excluded_even_when_required() -> None:
    row = _row(sponsorship=SponsorshipStatus.UNKNOWN)
    config = _config(visa_sponsorship_required=True)
    assert exclusion_reason(row, config) is None


def test_exclusion_reason_location_checked_before_sponsorship() -> None:
    row = _row(location="Bengaluru, India", sponsorship=SponsorshipStatus.NOT_OFFERED)
    config = _config(countries=("US",), visa_sponsorship_required=True)
    assert exclusion_reason(row, config) is NotAssessedReason.LOCATION_MISMATCH


# --- 0.1.8.1 r1: exclusion_reason on a region-only location -- the
# operator's UAT complaint ("AMER" rows passed a US-only filter). Keeps
# exclusion_reason's stable, coarse LOCATION_MISMATCH contract (unchanged
# for proposal_execution.py); location_mismatch_detail is the finer-grained
# helper acquire's drop-count accounting uses. -----------------------------


def test_exclusion_reason_region_only_location_is_excluded() -> None:
    row = _row(location="AMER")
    config = _config(countries=("US",))
    assert exclusion_reason(row, config) is NotAssessedReason.LOCATION_MISMATCH


def test_exclusion_reason_region_with_matching_country_is_not_excluded() -> None:
    row = _row(location="AMER; Denver, CO")
    config = _config(countries=("US",))
    assert exclusion_reason(row, config) is None


def test_location_mismatch_detail_region_only_returns_region_only() -> None:
    row = _row(location="AMER")
    config = _config(countries=("US",))
    assert location_mismatch_detail(row, config) is NotAssessedReason.REGION_ONLY


def test_location_mismatch_detail_recognized_wrong_country_returns_location_mismatch() -> None:
    row = _row(location="Bengaluru, India")
    config = _config(countries=("US",))
    assert location_mismatch_detail(row, config) is NotAssessedReason.LOCATION_MISMATCH


def test_location_mismatch_detail_none_when_not_excluded() -> None:
    row = _row(location="Denver, CO")
    config = _config(countries=("US",))
    assert location_mismatch_detail(row, config) is None
    ambiguous_row = _row(location="Remote")
    assert location_mismatch_detail(ambiguous_row, config) is None


def test_location_mismatch_detail_structured_countries_never_region_only() -> None:
    # A structured countries=() result (present but unmatched) is always a
    # LOCATION_MISMATCH, never REGION_ONLY -- REGION_ONLY only applies to
    # the free-text fallback path, since a structured field names real
    # countries or nothing, never a region label.
    row = _row(location="AMER", countries=())
    config = _config(countries=("US",))
    assert location_mismatch_detail(row, config) is NotAssessedReason.LOCATION_MISMATCH
