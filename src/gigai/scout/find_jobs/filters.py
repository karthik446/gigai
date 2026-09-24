"""Pure, I/O-free selection filters for the Scout find-jobs acquire node.

Country and visa-sponsorship rules are deliberately isolated here as pure
functions so `market_acquisition.py`'s selection loop and
`proposal_execution.py`'s (P2) not-assessed labeling can both call the same
logic without duplicating it or importing each other's node modules.

Real operator location strings this module is built against (from live
run outputs, see ``../../../../v0.1.8-uat.md`` U19/U20):

* Multi-location, semicolon-separated: "Remote, Canada; Remote, United States",
  "Bengaluru; Hyderabad", "Singapore; Singapore, Singapore".
* "COUNTRY - City" ordering: "IND - Pune", "USA - Tempe, AZ", "CZE - Prague".
* US state abbreviation or name with no country word: "Denver, CO",
  "New York, NY", "Atlanta, Georgia", "San Francisco, CA - US".
* Remote qualified by country: "Remote, United States", "Remote - USA",
  "Remote - Canada".
* Bare city (no country/state at all): "Bengaluru", "Hyderabad", "Berlin,
  Berlin, Germany", "Toronto, Ontario, Canada".
* Region token, never a single-country match (0.1.8.1 B1): "AMER", "EMEA",
  "APAC", "LATAM", "Remote - Americas" -- these carry no *country* signal
  (a region spans many countries). A location that resolves to *only*
  region tokens (no country/state/city signal anywhere in it) is a
  **definite non-match** for a single wanted country (0.1.8.1 r1: the
  operator's own UAT complaint -- "AMER" rows passed a US-only filter,
  which this fixes), not ambiguous. A region token alongside a real country
  match ("AMER; Denver, CO", "Remote - US") still matches on the country.
  An unrelated *unrecognized* token with no region signal at all (an
  unknown city, "Remote" alone) stays ambiguous, as before.
* Empty string: genuinely no location data (Exa rows before ATS enrichment).

P1b (operator's real run-2 numbers: US filter gave True 101 / False 41 /
None 32 over 174 postings; the None bucket was dominated by bare
non-US tech-hub cities -- "Bengaluru", "Hyderabad", "Bengaluru; Hyderabad",
18+ Bengaluru postings alone -- being read as ambiguous instead of
non-US). Added ``_CITY_COUNTRIES``, a bounded table of common tech-hub
city names with no country/state suffix, matched case- and
diacritic-insensitively (``_fold``).

0.1.8.1 (operator: "figure out if there are already libraries that define
this ... we shouldn't make up shit", `.orchestrator/research/country-data.md`):
the country-name/abbreviation/demonym table and the US-state name/
abbreviation tables are now *generated* from :mod:`pycountry` (ISO 3166-1/
ISO 3166-2:US, sourced from Debian's ``iso-codes``) at import time instead
of hand-typed. What stays hand-made, deliberately small, and explicitly
labelled: the UK constituent-country fold (``_UK_FOLD_ALIASES``: "Great
Britain"/"England"/"Scotland"/"Wales" -> GB -- a product choice about how to
bucket UK-constituent-country mentions, not something ISO-3166 encodes), the
bare "ca"/"in"/"de" country-vs-US-state disambiguation policy (still
computed, now generically, from whichever pycountry alpha-2 codes actually
collide with a US state's postal abbreviation), and ``_CITY_COUNTRIES`` (the
45-city list -- kept for now, not replaced by a city gazetteer package; see
the research doc §5 "Optional").
"""

from __future__ import annotations

import re
import unicodedata

import pycountry

from .contracts import FindJobsConfig, NotAssessedReason, PostingRow, SponsorshipStatus

# --- pycountry-derived country alias table -------------------------------
#
# Built once, at import time, from ISO 3166-1 data (pycountry) instead of a
# hand-typed table. For each country: its alpha-2, alpha-3, name,
# official_name (when present), and common_name (when present, e.g. "South
# Korea" for KR) all become case-insensitive alias tokens that resolve to
# that alpha-2 code.
#
# The bare alpha-2 code (a 2-letter token, e.g. "fr", "es") is included as
# an alias UNLESS it collides with a US state's postal abbreviation (e.g.
# "ca" collides with California, "in" with Indiana, "de" with Delaware) --
# in that case the bare code is deliberately dropped from the alias set so a
# state-abbreviation-only segment doesn't get misread as the foreign
# country. The longer forms (alpha-3, name, official/common name) are never
# dropped, since ATS postings write those out in full rather than using the
# bare 2-letter form for the country (comment preserved from the original
# hand-typed table).
_US_STATE_SUBDIVISIONS = tuple(
    subdivision
    for subdivision in pycountry.subdivisions.get(country_code="US")
    if subdivision.type in ("State", "District")
)
_US_STATE_ABBREVIATIONS = frozenset(
    subdivision.code.split("-", 1)[1].lower() for subdivision in _US_STATE_SUBDIVISIONS
)
_US_STATE_NAMES = frozenset(subdivision.name.lower() for subdivision in _US_STATE_SUBDIVISIONS)
_US_STATE_ABBREVIATIONS_UPPER = frozenset(code.upper() for code in _US_STATE_ABBREVIATIONS)


def _build_country_aliases() -> dict[str, tuple[str, ...]]:
    """Case-insensitive, multi-character name/demonym aliases (never bare codes).

    PR #37 review P0-1: the bare alpha-2/alpha-3 code used to live in this
    same case-insensitive table, so it matched ordinary lowercase English
    words that happen to collide with a code ("and" -> AD/Andorra, "per" ->
    PE/Peru, "est" -> EE/Estonia's alpha-3). Bare codes are now handled
    separately by :data:`_CODE_ALIASES` / :func:`_code_countries_in_original`,
    matched only as an uppercase, delimited token in the *original*
    (unfolded) string. This table keeps only names/official names/common
    names, which ATS postings always write out in full and which are safe to
    fold case-insensitively (nobody writes "GERMANY" meaning the letters
    G-E-R-M-A-N-Y as an unrelated word).
    """

    table: dict[str, tuple[str, ...]] = {}
    for country in pycountry.countries:
        code = country.alpha_2
        aliases: set[str] = {country.name.lower()}
        official_name = getattr(country, "official_name", None)
        if official_name:
            aliases.add(official_name.lower())
        common_name = getattr(country, "common_name", None)
        if common_name:
            aliases.add(common_name.lower())
        table[code] = tuple(sorted(aliases))
    return table


def _build_code_aliases() -> dict[str, tuple[str, ...]]:
    """Bare alpha-2/alpha-3 codes, matched uppercase-only in the original string.

    The alpha-2 code is dropped when it collides with a US state's postal
    abbreviation (e.g. "ca" collides with California, "in" with Indiana, "de"
    with Delaware) -- comment preserved from the original hand-typed table --
    so a state-abbreviation-only segment doesn't get misread as the foreign
    country. Unlike the old table, collision is checked against the
    *uppercase* code (states are matched case-insensitively via
    ``_US_STATE_ABBREVIATIONS`` on folded text, but this table's codes are
    only ever compared uppercase-to-uppercase against the original string --
    see :func:`_code_countries_in_original`), so the comparison is still
    correct: an uppercase "CA"/"IN"/"DE" token is ambiguous the same way.
    """

    table: dict[str, tuple[str, ...]] = {}
    for country in pycountry.countries:
        code = country.alpha_2
        codes: set[str] = {country.alpha_3.upper()}
        if code.upper() not in _US_STATE_ABBREVIATIONS_UPPER:
            codes.add(code.upper())
        table[code] = tuple(sorted(codes))
    return table


_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = _build_country_aliases()

# "US" additionally carries the common colloquial forms ATS postings use
# that ISO-3166 data itself doesn't encode ("U.S.", "U.S.A."; "US"/"USA"
# moved to _CODE_ALIASES/_US_EXTRA_CODES below since they're bare codes).
_COUNTRY_ALIASES["US"] = tuple(sorted(set(_COUNTRY_ALIASES["US"]) | {"u.s.", "u.s.a."}))

# UK constituent-country fold: a product decision about how to bucket
# mentions of England/Scotland/Wales (and the "Great Britain" demonym) under
# the GB (United Kingdom) code. Not something ISO-3166 alone encodes -- kept
# as a small, explicitly-labelled policy list on top of the generated table
# (research doc §5 "What would stay hand-made, and why").
_UK_FOLD_ALIASES: tuple[str, ...] = ("uk", "u.k.", "great britain", "england", "scotland", "wales")
_COUNTRY_ALIASES["GB"] = tuple(sorted(set(_COUNTRY_ALIASES["GB"]) | set(_UK_FOLD_ALIASES)))

_CODE_ALIASES: dict[str, tuple[str, ...]] = _build_code_aliases()

# US additionally carries "US"/"USA" as bare uppercase codes (PR #37 P0:
# these used to be case-insensitive aliases; moved here so lowercase "us"/
# "usa" inside an ordinary word/phrase is never misread as a country).
_CODE_ALIASES["US"] = tuple(sorted(set(_CODE_ALIASES["US"]) | {"US", "USA"}))

# North American and other timezone abbreviations that must never be read
# as a country code even though they're uppercase and delimited (PR #37
# review P0-1: "Remote - EST timezone" was misread as Estonia via EST's
# alpha-3 collision; ET/PT also collide with real alpha-2 codes,
# Ethiopia/Portugal). Deliberately NOT treated as a positive US signal
# either (coordinator review): EST/ET/CST/PST etc. are shared with Canada
# (Ontario/Quebec observes ET/EST) and other countries, so reading them as
# US would create a new false positive for non-US remote roles that also
# mention a North American-overlapping timezone. A location carrying only a
# timezone token, with no other country/state/city signal, stays whatever
# it already resolved to -- ambiguous (None), same as "Remote" alone --
# never a definite match either way.
_TIMEZONE_TOKENS: frozenset[str] = frozenset(
    {
        "EST", "EDT", "CST", "CDT", "MST", "MDT", "PST", "PDT",
        "ET", "CT", "MT", "PT", "GMT", "UTC", "CET", "CEST",
        "AEST", "AEDT", "BST",
    }
)


# Region tokens: named explicitly in the operator's UAT ticket (B1) as
# strings that must never count as a match for a single wanted country --
# "AMER" is not "US", it's a multi-country sales/eng region label. Matched
# the same whole-segment way `_CITY_COUNTRIES` is (after folding), and only
# after the segment has already failed to resolve to any real
# country/state/city -- a segment can't be both "Denver" (a US-state-bearing
# city) and a region token, so there's no ordering conflict with the checks
# above it in `_segment_countries`. 0.1.8.1 r1 (coordinator review):
# unlike an unrecognized token (a genuinely unknown city, "Remote" alone),
# a region token is a *known, named* multi-country label -- it is
# deliberately tracked separately from "no signal at all" so a
# region-token-*only* location can be treated as a definite non-match
# (see `_LocationSignal`/`location_countries` below) rather than ambiguous.
_REGION_TOKENS: frozenset[str] = frozenset({"amer", "americas", "emea", "apac", "latam"})


# Bare tech-hub city names with no country/state suffix at all (P1b:
# the operator's #1 complaint -- ~50 real postings in "Bengaluru",
# "Hyderabad", "Bengaluru; Hyderabad" etc. shape with zero country signal
# were falling into the ambiguous/kept bucket instead of being read as
# non-US). Matched against a whole comma/semicolon segment (see
# ``_segments``), diacritic- and case-insensitive (``_fold``), after the
# country-alias/US-state checks in ``_segment_countries`` have already run
# -- a segment like "hyderabad, india" is already resolved to IN by the
# "india" alias before this table is even consulted; this table only fires
# for a segment that is *just* the bare city.
#
# 0.1.8.1: kept as-is, deliberately hand-made and small (operator: keep for
# now, relabelled as a deliberate short list) -- a city gazetteer package
# (``geonamescache``, ~35 MB) would replace it but wasn't adopted; see
# research doc §5 "Optional".
_CITY_COUNTRIES: dict[str, str] = {
    # India
    "bengaluru": "IN", "bangalore": "IN", "hyderabad": "IN", "pune": "IN",
    "chennai": "IN", "mumbai": "IN", "gurgaon": "IN", "gurugram": "IN",
    "noida": "IN", "delhi": "IN", "new delhi": "IN",
    # UK / Ireland
    "london": "GB", "manchester": "GB", "edinburgh": "GB", "dublin": "IE",
    # Germany / France / Netherlands
    "berlin": "DE", "munich": "DE", "hamburg": "DE", "paris": "FR",
    "amsterdam": "NL",
    # Iberia
    "madrid": "ES", "barcelona": "ES", "lisbon": "PT",
    # Central/Northern Europe
    "warsaw": "PL", "krakow": "PL", "cracow": "PL", "prague": "CZ",
    "stockholm": "SE", "zurich": "CH",
    # Middle East
    "tel aviv": "IL",
    # Americas
    "toronto": "CA", "vancouver": "CA", "montreal": "CA",
    "mexico city": "MX", "sao paulo": "BR",
    # Asia-Pacific
    "singapore": "SG", "seoul": "KR", "tokyo": "JP",
    "sydney": "AU", "melbourne": "AU",
}

# Common US colloquial location forms ATS postings use that the pycountry
# name/official-name/common-name table doesn't cover (PR #37 review P0-1's
# required additions): "New York City"/"NYC" name the city, not the country,
# so they're never in ISO-3166 data at all; matched the same substring/
# whole-segment way ``_CITY_COUNTRIES`` is (see ``_segment_countries``).
_US_CITY_ALIASES: dict[str, str] = {"new york city": "US", "nyc": "US"}

_NOT_OFFERED_PHRASES: tuple[str, ...] = (
    "unable to sponsor",
    "not able to sponsor",
    "no visa sponsorship",
    "not able to provide sponsorship",
    "without sponsorship",
    "without visa sponsorship",
    "does not offer sponsorship",
    "does not offer visa sponsorship",
    "does not provide sponsorship",
    "does not provide visa sponsorship",
    "cannot sponsor",
    "can not sponsor",
    # 0.1.8.1 B3: "will not sponsor" is explicitly named in the ticket's
    # required phrase coverage and was missing -- confirmed against real
    # Greenhouse posting text in the evidence run once ``html_to_text``'s
    # entity-decoding bug (the other half of B3) was fixed.
    "will not sponsor",
    "no sponsorship is available",
    "sponsorship is not available",
    "visa sponsorship is not available",
    "not provide visa sponsorship",
    "not offer visa sponsorship",
    "no sponsorship",
)

_OFFERED_PHRASES: tuple[str, ...] = (
    "visa sponsorship available",
    "visa sponsorship is available",
    "will sponsor",
    "we sponsor",
    "we sponsor visas",
    "sponsorship available",
    "sponsorship is available",
    "able to sponsor",
    "offers visa sponsorship",
    "provides visa sponsorship",
    "will provide sponsorship",
    "h-1b sponsorship",
    "h1b sponsorship",
    "open to sponsoring",
)


def _fold(value: str) -> str:
    """Lowercase and strip diacritics (``"São Paulo"`` -> ``"sao paulo"``).

    Real postings mix accented and unaccented spellings of the same city
    (``"Zürich"``/``"Zurich"``, ``"São Paulo"``/``"Sao Paulo"``); both must
    hit the same ``_CITY_COUNTRIES``/``_COUNTRY_ALIASES`` entry.
    """

    decomposed = unicodedata.normalize("NFKD", value.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _segments(location: str) -> list[str]:
    """Split a free-text location into comma/semicolon segments, folded."""

    parts: list[str] = []
    for chunk in location.split(";"):
        for piece in chunk.split(","):
            piece = _fold(piece).strip()
            if piece:
                parts.append(piece)
    return parts


# A code token is delimited by start/end of string, whitespace, or common
# ATS punctuation (comma, semicolon, hyphen, slash, parentheses). Matched
# against the *original*, unfolded location string -- see
# ``_code_countries_in_original`` -- so only an actually-uppercase token
# ("US", "DE", "CZE") can match, never a lowercase word that happens to
# spell the same letters ("and", "per", "de facto").
_CODE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,3})(?![A-Za-z0-9])")


def _code_countries_in_original(location: str) -> set[str]:
    """ISO codes from bare alpha-2/alpha-3 codes in the *original* string.

    PR #37 review P0-1: a bare code only counts when it appears as an
    actually-uppercase token, delimited by punctuation/whitespace/
    parentheses/start/end -- never as a lowercase (or mixed-case) word, and
    never as a substring of a longer word. This is checked against
    ``location`` before any lowercasing (``_fold``/``_segments`` fold the
    string for the name/city/state checks in ``_segment_countries``, which
    would destroy the case signal a bare code needs). A timezone
    abbreviation (EST/PST/ET/PT/GMT/...) is explicitly excluded even though
    some collide with a real alpha-2/alpha-3 code (EST/Estonia, ET/Ethiopia,
    PT/Portugal) -- see ``_TIMEZONE_TOKENS``.
    """

    found: set[str] = set()
    for match in _CODE_TOKEN_RE.finditer(location):
        token = match.group(1)
        if token in _TIMEZONE_TOKENS:
            continue
        for code, codes in _CODE_ALIASES.items():
            if token in codes:
                found.add(code)
    return found


def _segment_countries(segment: str) -> set[str]:
    """ISO codes this one folded comma/semicolon segment identifies, if any.

    ``segment`` is already comma/semicolon-split and folded (see
    ``_segments``), so a location like "Denver, CO" arrives here as two
    segments: "denver" and "co". A whole-segment match against a US state
    name/abbreviation (the "co" case) is what lets state-only segments
    signal US. Checked in order: country-name aliases, then US
    states, then the bare tech-hub city table (P1b) -- a segment like
    "hyderabad, india" is already resolved via the "india" alias before the
    city table would even be reached for the "hyderabad" segment, so the
    city table only matters for genuinely bare city names.
    """

    found: set[str] = set()
    tokens = segment.split()
    for code, aliases in _COUNTRY_ALIASES.items():
        for alias in aliases:
            if " " in alias:
                if alias in segment:
                    found.add(code)
                    break
            elif alias in tokens:
                found.add(code)
                break
    if segment in _US_STATE_NAMES or segment in _US_STATE_ABBREVIATIONS:
        found.add("US")
    city_country = _CITY_COUNTRIES.get(segment) or _US_CITY_ALIASES.get(segment)
    if city_country:
        found.add(city_country)
    else:
        # A multi-word city name ("new york city") can appear inside a
        # longer, non-comma-delimited phrase ("new york city and remote"),
        # the same way a multi-word country alias is substring-matched
        # above -- a single-word city stays a whole-token/whole-segment
        # match only (avoids a short city name matching inside an
        # unrelated longer word).
        for city, code in {**_CITY_COUNTRIES, **_US_CITY_ALIASES}.items():
            if " " in city and city in segment:
                found.add(code)
                break
    return found


def location_countries(location: str) -> set[str]:
    """All ISO-3166 alpha-2 codes this free-text location string identifies.

    Returns an empty set when no known country/US-state signal is found
    (ambiguous, e.g. a bare city name never seen in ``_COUNTRY_ALIASES``, a
    region token like "AMER"/"EMEA"/"APAC"/"LATAM", or the empty string).

    Bare alpha-2/alpha-3 codes (PR #37 review P0-1) are checked separately,
    against the original unfolded string via ``_code_countries_in_original``
    -- everything else (names, US states, the bare tech-hub city table) is
    checked case-insensitively via the folded ``_segments``/
    ``_segment_countries`` path, unchanged.
    """

    if not location:
        return set()
    found: set[str] = _code_countries_in_original(location)
    for segment in _segments(location):
        found |= _segment_countries(segment)
        # Also check whole-segment "usa - tempe" style joins where a country
        # alias and a city share one comma-free segment separated by " - ".
        for sub in segment.split(" - "):
            sub = sub.strip()
            if sub:
                found |= _segment_countries(sub)
    return found


def _is_region_only(location: str) -> bool:
    """True when every signal-bearing segment is a bare region token.

    0.1.8.1 r1 (coordinator review of B1): the operator's UAT complaint was
    that a region string like "AMER" passed a US-only filter -- it resolved
    ambiguous (``location_countries`` returns an empty set) and
    ``exclusion_reason`` never excludes ambiguous locations. A region token
    is not the same kind of "no signal" as a genuinely unrecognized token
    (an unknown city, "Remote" alone): it *is* a known, named multi-country
    label, so a location that resolves to ONLY region tokens -- no country,
    no US state, no known city anywhere in it -- is treated as a definite
    non-match, not ambiguous.

    Split the same way ``location_countries`` walks segments (comma/
    semicolon, then " - " sub-joins) so "Remote - Americas" (one
    comma-segment, "remote - americas", split into "remote"/"americas" only
    here) is recognized the same way "AMER; Denver, CO" (already three
    comma/semicolon segments) is. Returns ``False`` (not region-only) for
    the empty string and for a location with no region token at all --
    those stay however ``location_countries``/``country_match`` already
    resolve them (ambiguous, or a real country match).
    """

    if not location:
        return False
    saw_region = False
    for segment in _segments(location):
        subsegments = [segment] + [sub.strip() for sub in segment.split(" - ") if sub.strip()]
        for sub in subsegments:
            if sub == segment and " - " in segment:
                continue  # the whole joined segment itself, not a real token
            if _segment_countries(sub):
                return False  # a real country/state/city signal -- not region-only
            if sub in _REGION_TOKENS:
                saw_region = True
    return saw_region


def _structured_countries(countries: tuple[str, ...] | None) -> set[str] | None:
    """Normalize a :attr:`PostingRow.countries` structured value, if present.

    Returns ``None`` when there is no structured data at all (the caller
    should fall back to free-text parsing), or the (possibly empty) set of
    ISO alpha-2 codes the structured field names. An empty set here is a
    genuine, trusted "no country" signal from the structured field itself
    (e.g. Lever's ``country`` was present but null) -- callers should NOT
    fall back to text parsing in that case; see ``country_match``.
    """

    if countries is None:
        return None
    return {code.upper() for code in countries}


def country_match(
    location: str | None,
    countries: tuple[str, ...],
    *,
    structured_countries: tuple[str, ...] | None = None,
) -> bool | None:
    """Whether ``location`` matches one of the configured ISO country codes.

    When ``structured_countries`` is given (a :attr:`PostingRow.countries`
    value, i.e. trusted structured data from Lever/Ashby rather than
    free-text parsing), it is checked first and wins outright -- no
    free-text fallback -- since it's a deterministic signal with no
    ambiguity risk (0.1.8.1, `.orchestrator/research/country-data.md` §5).
    Structured data that resolves to an empty set (present but unmatched) is
    still authoritative: it means "recognized, not in the wanted set", not
    ambiguous.

    Falls back to ``location``'s free-text parsing only when
    ``structured_countries`` is ``None`` (absent/not supplied).

    Returns ``True`` when a recognized country is found and it's in
    ``countries``, ``False`` when recognized countries are found and none of
    them are in ``countries``, and ``None`` when the location is empty or no
    country could be identified in it (ambiguous -- callers keep, not drop,
    an ambiguous row). When ``countries`` is empty, every location matches
    (``True``): the country filter is off.

    0.1.8.1 r1: a location that resolves to *only* region tokens (no
    country/state/city anywhere in it -- "AMER", "EMEA", "APAC", "LATAM",
    "Remote - Americas") is also ``False``, a definite non-match, not
    ``None`` -- see ``_is_region_only``. A region token alongside a real
    country match ("AMER; Denver, CO") still matches on the country, since
    ``location_countries`` finds a non-empty set first and this branch is
    never reached.
    """

    if not countries:
        return True
    wanted = {code.upper() for code in countries}
    structured = _structured_countries(structured_countries)
    if structured is not None:
        return bool(structured & wanted)
    if not location:
        return None
    found = location_countries(location)
    if not found:
        if _is_region_only(location):
            return False
        return None
    return bool(found & wanted)


def sponsorship_from_text(text: str | None) -> SponsorshipStatus:
    """Derive a posting's visa-sponsorship stance from its plain-text body.

    Checked in this order: an explicit "not offered" phrase wins over an
    "offered" phrase when both somehow appear (fail toward the stricter,
    safer read for a candidate who needs sponsorship); otherwise whichever
    phrase set matches; otherwise ``unknown``.
    """

    if not text:
        return SponsorshipStatus.UNKNOWN
    lowered = text.lower()
    if any(phrase in lowered for phrase in _NOT_OFFERED_PHRASES):
        return SponsorshipStatus.NOT_OFFERED
    if any(phrase in lowered for phrase in _OFFERED_PHRASES):
        return SponsorshipStatus.OFFERED
    return SponsorshipStatus.UNKNOWN


def location_mismatch_detail(posting: PostingRow, config: FindJobsConfig) -> NotAssessedReason | None:
    """Finer-grained detail behind a ``LOCATION_MISMATCH`` exclusion.

    0.1.8.1 r1: ``exclusion_reason`` itself keeps returning the coarse
    ``LOCATION_MISMATCH`` (its stable, existing contract -- see that
    function's docstring). This helper is for callers that want to
    distinguish *why* -- specifically, ``market_acquisition.py``'s
    ``dropped_counts`` accounting, so a coordinator/operator can tell "wrong
    country" (``LOCATION_MISMATCH``) apart from "region label only, no
    country at all" (``REGION_ONLY``) without changing what
    ``exclusion_reason``/``proposal_execution.py`` see. Returns ``None``
    when the posting isn't location-excluded at all (matches, or
    ambiguous). Structured ``countries`` data is never region-only by
    definition (a structured field names real countries or nothing), so
    this only ever returns ``REGION_ONLY`` for the free-text fallback path.
    """

    if not config.countries:
        return None
    match = country_match(posting.location, config.countries, structured_countries=posting.countries)
    if match is not False:
        return None
    if posting.countries is None and _is_region_only(posting.location or ""):
        return NotAssessedReason.REGION_ONLY
    return NotAssessedReason.LOCATION_MISMATCH


def exclusion_reason(posting: PostingRow, config: FindJobsConfig) -> NotAssessedReason | None:
    """Why ``posting`` would be excluded from selection under ``config``.

    Pure and side-effect-free so both the acquire selection loop and
    assess's not-assessed labeling call the identical rule. Returns ``None``
    when the posting is not excluded by either rule. Ambiguous locations
    (``country_match`` returns ``None``) are never excluded -- only a
    definite non-match is. Always returns the coarse ``LOCATION_MISMATCH``
    for any location exclusion (including a region-only location, 0.1.8.1
    r1) -- see ``location_mismatch_detail`` for the finer-grained reason
    used by acquire's drop-count accounting.
    """

    if config.countries:
        match = country_match(posting.location, config.countries, structured_countries=posting.countries)
        if match is False:
            return NotAssessedReason.LOCATION_MISMATCH
    if config.visa_sponsorship_required and posting.sponsorship is SponsorshipStatus.NOT_OFFERED:
        return NotAssessedReason.SPONSORSHIP_EXCLUDED
    return None


__all__ = [
    "country_match",
    "exclusion_reason",
    "location_countries",
    "location_mismatch_detail",
    "sponsorship_from_text",
]
