"""uat-bug-028: a posting's work mode, and whether it fits the config's work mode + area.

Pure and I/O-free, like ``filters``. The index search (``index_search``) and
acquire's drop loop (``market_acquisition``) call ``work_mode_fit`` before
ranking and the import cap; the run's results read (``api/run_reads``)
calls it again so a card can say why a posting passed.

A posting's work mode, in this order:

1. the board's own field (Lever/Ashby ``workplaceType``, ``PostingRow.
   work_mode``) -- source ``board``;
2. else the location text -- source ``derived``, never shown as a board
   field (``WorkMode``'s contract: a board field is never inferred):
   "Remote" / "Remote - US" / "Anywhere" -> remote; "Hybrid" -> hybrid;
   "(On-site)" / "Berlin Office" -> onsite; a plain city ("San Jose,
   California") -> ``in_person``: the text names a place and no mode, so
   the posting is hybrid or on-site but not stated as remote;
3. else ``unknown`` (empty text, a bare country or region, a bare state):
   kept under every mode and labelled, never guessed.

A multi-location string ("San Francisco, CA or Remote (U.S.)", "Denver,
CO - Hybrid; New York, NY - Hybrid") is split into parts; a part that
states a mode wins over plain-place parts, and the most open stated mode
wins (remote over hybrid over on-site): the posting offers it.

Real strings this is built against: a read-only sample of the company index
(284,024 live postings, 2026-09-28): 82.7% a plain place, 9.9% Remote*,
0.9% Hybrid*, 0.2% an on-site word, 6.7% multi-location, 18,918 a bare
country/region; see the uat-bug-028 worker notes.

The filter (operator semantics, N37):

* Remote-only: remote postings (and ``unknown``, labelled);
* Hybrid + area: remote postings, plus hybrid, on-site or plain-city
  postings in the area (0110-048; ``in_person_modes``);
* Onsite + area: remote postings, plus hybrid, on-site or plain-city
  postings in the area;
* Any: everything (the country filter still applies, in ``filters``).

Area match: the configured city's metro from ``_METROS`` (a small alias
table: "Denver, CO" also matches Boulder and Aurora), each hit checked
against the state the posting names so Aurora, IL is not Denver. A city
the table does not know matches itself or, as the fallback, any posting in
the same state; a bare state ("Colorado") matches that state. A posting
whose text names no place cannot be judged: kept, labelled "area not
stated".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import FindJobsConfig, PostingRow, WorkMode, WorkModePreference, is_location_placeholder
from .filters import _COUNTRY_ALIASES, _REGION_TOKENS, _US_STATE_SUBDIVISIONS, _fold, location_countries

REMOTE = "remote"
HYBRID = "hybrid"
ONSITE = "onsite"
IN_PERSON = "in_person"
UNKNOWN = "unknown"

SOURCE_BOARD = "board"
SOURCE_DERIVED = "derived"
SOURCE_NONE = "none"

# Most open first: a posting that states several modes offers the first.
_OPENNESS = (REMOTE, HYBRID, ONSITE)

_STATE_BY_NAME: dict[str, str] = {
    subdivision.name.lower(): subdivision.code.split("-", 1)[1] for subdivision in _US_STATE_SUBDIVISIONS
}
_STATE_CODES = frozenset(_STATE_BY_NAME.values())

_COUNTRY_WORDS = frozenset(alias for aliases in _COUNTRY_ALIASES.values() for alias in aliases) | frozenset(
    {"us", "usa", "u.s.", "u.s.a.", "uk", "united states of america", "north america", "europe", "global", "international"}
)

_PART_SPLIT = re.compile(r";|\||/|\s+or\s+|\s+and\s+|\s+&\s+")
_HYBRID_RE = re.compile(r"\bhybrid\b")
_REMOTE_RE = re.compile(r"\bremote(ly)?\b|\banywhere\b|\bwork from home\b|\bwfh\b|\bdistributed\b|\btelecommute\b")
_ONSITE_RE = re.compile(r"\bon-?\s?site\b|\bin[- ]office\b|\boffice\b|\bin[- ]person\b")
_NOISE = " \t()[]-–—,.:*"
# "Washington, DC" is a city, not the state of Washington plus a district.
_WASHINGTON_DC_RE = re.compile(r"\bwashington,?\s*d\.?\s?c\b")

# The small metro alias table: (label, states, folded city names). A hit is
# only a match when the posting names none of other states (Aurora, IL is
# not Denver); an empty states tuple is a non-US metro.
_METROS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("Denver", ("CO",), (
        "denver", "boulder", "aurora", "lakewood", "englewood", "littleton", "centennial", "broomfield",
        "westminster", "arvada", "golden", "louisville", "greenwood village", "thornton", "lone tree",
        "highlands ranch", "longmont",
    )),
    ("San Francisco Bay Area", ("CA",), (
        "san francisco", "sf", "bay area", "sf bay area", "san francisco bay area", "oakland", "berkeley",
        "emeryville", "san jose", "santa clara", "sunnyvale", "mountain view", "palo alto", "menlo park",
        "redwood city", "san mateo", "foster city", "burlingame", "south san francisco", "cupertino",
        "milpitas", "fremont", "san carlos", "san bruno", "los gatos", "campbell", "pleasanton", "hayward",
    )),
    ("New York City", ("NY", "NJ"), (
        "new york", "new york city", "nyc", "manhattan", "brooklyn", "queens", "jersey city", "hoboken",
    )),
    ("Seattle", ("WA",), ("seattle", "bellevue", "redmond", "kirkland", "bothell", "tacoma")),
    ("Los Angeles", ("CA",), (
        "los angeles", "santa monica", "culver city", "pasadena", "burbank", "glendale", "el segundo",
        "playa vista", "long beach", "irvine", "torrance", "marina del rey", "hawthorne",
    )),
    ("Boston", ("MA",), (
        "boston", "cambridge", "somerville", "waltham", "burlington", "lexington", "woburn", "quincy",
        "newton", "needham", "bedford",
    )),
    ("Chicago", ("IL",), ("chicago", "evanston", "oak brook", "schaumburg")),
    ("Austin", ("TX",), ("austin", "round rock", "cedar park")),
    ("Dallas", ("TX",), ("dallas", "fort worth", "plano", "irving", "frisco", "richardson", "addison")),
    ("Washington, DC", ("DC", "VA", "MD"), (
        "washington dc", "washington d.c", "dc", "d.c", "dmv", "arlington", "reston", "mclean", "tysons",
        "herndon", "alexandria", "bethesda", "rockville", "silver spring", "chantilly", "fairfax",
    )),
    ("Atlanta", ("GA",), ("atlanta", "alpharetta", "marietta", "sandy springs")),
    ("Raleigh-Durham", ("NC",), ("raleigh", "durham", "chapel hill", "research triangle", "cary", "morrisville")),
    ("Salt Lake City", ("UT",), ("salt lake city", "lehi", "provo", "draper", "south jordan")),
    ("Phoenix", ("AZ",), ("phoenix", "scottsdale", "tempe", "chandler", "mesa")),
    ("London", (), ("london",)),
    ("Toronto", (), ("toronto", "mississauga", "markham")),
)


def in_person_modes(preference: WorkModePreference | str) -> tuple[str, ...]:
    """The posting modes (besides remote) a preference keeps when the posting is in the area.

    The ONE rule behind ``work_mode_fit`` and the assess prompt's CANDIDATE
    WORK MODE wording (0110-048): Hybrid and Onsite both keep hybrid and
    on-site roles in their own area (a plain city reads as either).
    """

    value = getattr(preference, "value", preference)
    return (HYBRID, ONSITE) if value in (WorkModePreference.HYBRID.value, WorkModePreference.ONSITE.value) else ()


@dataclass(frozen=True)
class PostingWorkMode:
    """A posting's work mode and where it came from (``board``/``derived``/``none``)."""

    mode: str
    source: str


@dataclass(frozen=True)
class Area:
    """The config's area: the city as the operator wrote it, and what matches it."""

    label: str
    cities: tuple[str, ...]
    states: tuple[str, ...]
    state_fallback: str | None


@dataclass(frozen=True)
class WorkModeFit:
    """Whether one posting passes the config's work mode + area, and why."""

    mode: str
    source: str
    preference: str
    area: str | None
    in_area: bool | None
    passes: bool

    def to_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "source": self.source,
            "preference": self.preference,
            "area": self.area,
            "in_area": self.in_area,
            "passes": self.passes,
        }


def _parts(folded: str) -> list[str]:
    return [part.strip(_NOISE) for part in _PART_SPLIT.split(folded) if part.strip(_NOISE)]


def _pieces(part: str) -> list[str]:
    """A part's comma and " - " pieces, trimmed, digits-only pieces (zip, lat/long) dropped."""

    pieces: list[str] = []
    for chunk in part.split(","):
        for piece in chunk.split(" - "):
            piece = re.sub(r"\b\d[\d\s.-]*\b", " ", piece).strip(_NOISE)
            piece = " ".join(piece.split())
            if piece:
                pieces.append(piece)
    return pieces


def _state_of(piece: str) -> str | None:
    if piece in _STATE_BY_NAME:
        return _STATE_BY_NAME[piece]
    upper = piece.upper().replace(".", "")
    return upper if upper in _STATE_CODES else None


def _states(part: str) -> set[str]:
    return {state for state in (_state_of(piece) for piece in _pieces(part)) if state is not None}


def _names_a_city(part: str) -> bool:
    """Whether a part names something finer than a country, region or state."""

    if _WASHINGTON_DC_RE.search(part):
        return True
    for piece in _pieces(part):
        if piece in _COUNTRY_WORDS or piece in _REGION_TOKENS or _state_of(piece) is not None:
            continue
        return True
    return False


def derive_work_mode(location: str | None, board: WorkMode | None = None) -> PostingWorkMode:
    """The posting's work mode: the board's field, else its location text, else unknown."""

    if board is not None:
        return PostingWorkMode(board.value, SOURCE_BOARD)
    stated: set[str] = set()
    city = False
    for part in _parts(_fold(location or "")):
        if _HYBRID_RE.search(part):
            stated.add(HYBRID)
        elif _REMOTE_RE.search(part):
            stated.add(REMOTE)
        elif _ONSITE_RE.search(part):
            stated.add(ONSITE)
        elif _names_a_city(part):
            city = True
    for mode in _OPENNESS:
        if mode in stated:
            return PostingWorkMode(mode, SOURCE_DERIVED)
    if city:
        return PostingWorkMode(IN_PERSON, SOURCE_DERIVED)
    return PostingWorkMode(UNKNOWN, SOURCE_NONE)


def parse_area(location: str | None) -> Area | None:
    """The config's ``location`` as an area; ``None`` for no area (empty or the placeholder)."""

    if location is None or not location.strip() or is_location_placeholder(location):
        return None
    if _WASHINGTON_DC_RE.search(_fold(location)):
        label, states, cities = next(metro for metro in _METROS if metro[0] == "Washington, DC")
        return Area(label=label, cities=cities, states=states, state_fallback=None)
    pieces = [piece.strip() for piece in location.split(",") if piece.strip()]
    folded = [_fold(piece).strip(_NOISE) for piece in pieces]
    state = next((code for code in (_state_of(piece) for piece in folded) if code is not None), None)
    city_pieces = [(raw, low) for raw, low in zip(pieces, folded) if _state_of(low) is None and low not in _COUNTRY_WORDS]
    if not city_pieces:
        if state is None:
            return None
        return Area(label=pieces[0], cities=(), states=(state,), state_fallback=state)
    label, city = city_pieces[0]
    for _label, states, cities in _METROS:
        if city in cities and (state is None or not states or state in states):
            return Area(label=label, cities=cities, states=states, state_fallback=None)
    return Area(label=label, cities=(city,), states=(state,) if state else (), state_fallback=state)


def _mentions(part: str, name: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", part) is not None


def _place_text(part: str) -> str:
    """A part without its work-mode words ("denver, co - hybrid" -> "denver, co")."""

    for pattern in (_HYBRID_RE, _REMOTE_RE, _ONSITE_RE):
        part = pattern.sub(" ", part)
    return " ".join(part.split()).strip(_NOISE)


def in_area(location: str | None, area: Area) -> bool | None:
    """Whether the posting's location is in ``area``; ``None`` when it names no place."""

    placed = False
    for part in (_place_text(part) for part in _parts(_fold(location or ""))):
        if not part:
            continue
        states = _states(part)
        if states or _names_a_city(part):
            placed = True
        if area.states and not states and location_countries(part) - {"US"}:
            continue  # "San Jose, Costa Rica" is not the Bay Area
        if any(_mentions(part, city) for city in area.cities) and (not states or not area.states or states & set(area.states)):
            return True
        if area.state_fallback is not None and area.state_fallback in states:
            return True
    return False if placed else None


def work_mode_fit(posting: PostingRow, config: FindJobsConfig) -> WorkModeFit:
    """Does ``posting`` pass ``config``'s work mode + area? (the N37 semantics above)."""

    preference = config.effective_work_mode
    derived = derive_work_mode(posting.location, posting.work_mode)
    area = parse_area(config.location) if preference in (WorkModePreference.HYBRID, WorkModePreference.ONSITE) else None
    checks_area = area is not None and derived.mode in (HYBRID, ONSITE, IN_PERSON)
    matched = in_area(posting.location, area) if checks_area and area is not None else None
    if preference is WorkModePreference.ANY or derived.mode in (REMOTE, UNKNOWN):
        passes = True
    elif preference is WorkModePreference.REMOTE:
        passes = False
    elif preference is WorkModePreference.HYBRID and derived.mode == ONSITE:
        # On-site stays out unless it is stated to be in the area (no area, or elsewhere: out).
        passes = ONSITE in in_person_modes(preference) and matched is True
    else:
        passes = matched is not False
    return WorkModeFit(
        mode=derived.mode,
        source=derived.source,
        preference=preference.value,
        area=area.label if checks_area and area is not None else None,
        in_area=matched,
        passes=passes,
    )


__all__ = [
    "Area",
    "PostingWorkMode",
    "WorkModeFit",
    "derive_work_mode",
    "in_area",
    "in_person_modes",
    "parse_area",
    "work_mode_fit",
]
