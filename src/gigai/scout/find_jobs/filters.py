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
* Empty string: genuinely no location data (Exa rows before ATS enrichment).

P1b (operator's real run-2 numbers: US filter gave True 101 / False 41 /
None 32 over 174 postings; the None bucket was dominated by bare
non-US tech-hub cities -- "Bengaluru", "Hyderabad", "Bengaluru; Hyderabad",
18+ Bengaluru postings alone -- being read as ambiguous instead of
non-US). Added ``_CITY_COUNTRIES``, a bounded table of common tech-hub
city names with no country/state suffix, matched case- and
diacritic-insensitively (``_fold``).
"""

from __future__ import annotations

import unicodedata

from .contracts import FindJobsConfig, NotAssessedReason, PostingRow, SponsorshipStatus

# ISO-3166 alpha-2 -> the country-name/demonym tokens seen in the wild that
# identify it in a free-text location string.  Matching is per comma/semicolon
# segment, case-insensitive, whole-word.  This is intentionally small (US +
# the countries that showed up in live UAT data plus their common neighbors)
# rather than an exhaustive gazetteer: an unrecognized country name is treated
# as ambiguous (kept), never as a silent non-match.
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "US": ("us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"),
    # "ca" alone is deliberately excluded: it collides with the US state
    # abbreviation for California, and ATS postings write out "Canada" or
    # "CAN" rather than bare "CA" for the country.
    "CA": ("can", "canada"),
    "GB": ("uk", "u.k.", "gbr", "united kingdom", "great britain", "england", "scotland", "wales"),
    # Bare "in"/"de" are deliberately excluded: they collide with the US
    # state abbreviations for Indiana/Delaware. ATS postings write "India"/
    # "IND" or "Germany"/"DEU" for the country instead of the bare 2-letter
    # form.
    "IN": ("ind", "india"),
    "DE": ("deu", "germany", "deutschland"),
    "FR": ("fr", "fra", "france"),
    "ES": ("es", "esp", "spain"),
    "PL": ("pl", "pol", "poland"),
    "CZ": ("cz", "cze", "czech republic", "czechia"),
    "KR": ("kr", "kor", "south korea", "korea"),
    "SG": ("sg", "sgp", "singapore"),
    "JP": ("jp", "jpn", "japan"),
    "AU": ("au", "aus", "australia"),
    "NL": ("nl", "nld", "netherlands"),
    "IE": ("ie", "irl", "ireland"),
    "BR": ("br", "bra", "brazil"),
    "MX": ("mx", "mex", "mexico"),
}

# US state names and abbreviations: any of these appearing as a segment token
# means "US" even when the word "United States"/"USA" is absent (e.g. "Denver,
# CO", "Atlanta, Georgia", "New York, NY").
_US_STATE_ABBREVIATIONS = frozenset(
    {
        "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
        "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
        "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
        "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
        "wi", "wy", "dc",
    }
)

_US_STATE_NAMES = frozenset(
    {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
        "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york",
        "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhode island", "south carolina", "south dakota",
        "tennessee", "texas", "utah", "vermont", "virginia", "washington",
        "west virginia", "wisconsin", "wyoming",
    }
)

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

# Countries newly reachable only via ``_CITY_COUNTRIES`` (not already in
# ``_COUNTRY_ALIASES``) need their own alias entries too, so a spelled-out
# form of the country name still resolves (e.g. "Lisbon, Portugal").
_COUNTRY_ALIASES_EXTRA: dict[str, tuple[str, ...]] = {
    "PT": ("pt", "prt", "portugal"),
    "SE": ("se", "swe", "sweden"),
    "CH": ("ch", "che", "switzerland"),
    "IL": ("il", "isr", "israel"),
}

_NOT_OFFERED_PHRASES: tuple[str, ...] = (
    "unable to sponsor",
    "not able to sponsor",
    "no visa sponsorship",
    "not able to provide sponsorship",
    "without sponsorship",
    "does not offer sponsorship",
    "does not offer visa sponsorship",
    "does not provide sponsorship",
    "does not provide visa sponsorship",
    "cannot sponsor",
    "can not sponsor",
    "no sponsorship is available",
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


_ALL_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    **_COUNTRY_ALIASES,
    **{code: aliases for code, aliases in _COUNTRY_ALIASES_EXTRA.items() if code not in _COUNTRY_ALIASES},
}


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
    for code, aliases in _ALL_COUNTRY_ALIASES.items():
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
    city_country = _CITY_COUNTRIES.get(segment)
    if city_country:
        found.add(city_country)
    return found


def location_countries(location: str) -> set[str]:
    """All ISO-3166 alpha-2 codes this free-text location string identifies.

    Returns an empty set when no known country/US-state signal is found
    (ambiguous, e.g. a bare city name never seen in ``_COUNTRY_ALIASES``, or
    the empty string).
    """

    if not location:
        return set()
    found: set[str] = set()
    for segment in _segments(location):
        found |= _segment_countries(segment)
        # Also check whole-segment "usa - tempe" style joins where a country
        # alias and a city share one comma-free segment separated by " - ".
        for sub in segment.split(" - "):
            sub = sub.strip()
            if sub:
                found |= _segment_countries(sub)
    return found


def country_match(location: str | None, countries: tuple[str, ...]) -> bool | None:
    """Whether ``location`` matches one of the configured ISO country codes.

    Returns ``True`` when a recognized country is found and it's in
    ``countries``, ``False`` when recognized countries are found and none of
    them are in ``countries``, and ``None`` when the location is empty or no
    country could be identified in it (ambiguous -- callers keep, not drop,
    an ambiguous row). When ``countries`` is empty, every location matches
    (``True``): the country filter is off.
    """

    if not countries:
        return True
    if not location:
        return None
    found = location_countries(location)
    if not found:
        return None
    wanted = {code.upper() for code in countries}
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


def exclusion_reason(posting: PostingRow, config: FindJobsConfig) -> NotAssessedReason | None:
    """Why ``posting`` would be excluded from selection under ``config``.

    Pure and side-effect-free so both the acquire selection loop and
    assess's not-assessed labeling call the identical rule. Returns ``None``
    when the posting is not excluded by either rule. Ambiguous locations
    (``country_match`` returns ``None``) are never excluded -- only a
    definite non-match is.
    """

    if config.countries and country_match(posting.location, config.countries) is False:
        return NotAssessedReason.LOCATION_MISMATCH
    if config.visa_sponsorship_required and posting.sponsorship is SponsorshipStatus.NOT_OFFERED:
        return NotAssessedReason.SPONSORSHIP_EXCLUDED
    return None


__all__ = [
    "country_match",
    "exclusion_reason",
    "location_countries",
    "sponsorship_from_text",
]
