"""0.1.11.8 N1 + N2: the two list rules the free search and a profile's Jobs list share. Pure functions.

US ONLY (N1). A switch of its own, by the POSTING'S LOCATION, never by its
board. :func:`place_of` reads a location with the tables every country
filter uses (``filters``: structured countries for Lever and Ashby, the
parsed location text, the regions) and says one of three:

* ``us``: the location clearly names the US ("Austin, TX", "Remote - US",
  "New York, NY; London, UK": ANY of its places in the US is enough);
* ``other``: every place it names is clearly outside the US ("Remote
  Estonia", "Berlin", a region without the US such as "EMEA" or "Europe");
* ``unclear``: Scout cannot tell: "Remote" / "Anywhere" alone, no location,
  a place no table knows, a region that holds the US or may ("Americas",
  "Worldwide").

US only hides ``other`` and nothing else (:func:`in_us`). An ``unclear``
posting is NOT hidden: it is listed and labelled "unclear location". The
default is ON when the shared ``find-jobs.json`` countries hold the US
(:func:`us_only_default`), OFF otherwise; a request can say either.

COPIES (N2). The same job posted once per country is ONE row.
:func:`copy_key` is what two copies share: the board, the company name and
the title, each folded (:func:`fold`: case, spaces and punctuation aside;
``+`` and ``#`` are kept, so "C++ Engineer" is never "C# Engineer"), the SAME
DESCRIPTION, and whether the board still lists the posting. The description
is compared by the digest the company index already keeps for a posting
(``content_sha256``: ``posting_content_digest`` of its title and its plain
text), so nothing is read to compare. Conservative on purpose:

* two descriptions that differ (two teams hiring under one title) are two rows;
* a posting with NO stored description (the digest is ``None``: a Greenhouse
  posting whose detail was never fetched, a feed that carries none) is NEVER
  merged: :func:`copy_key` is ``None`` for it;
* the digest covers the title as written, so a title written with another
  case or punctuation is a row of its own;
* two companies never merge (the board is in the key), nor a removed copy
  with a live one.

Only the location differs between two copies: two cities of ONE country
merge too. The row is ONE canonical job (``canonical_job.pick_canonical``)
and lists every location (:func:`locations_text`: "Remote: Estonia,
Lithuania, Latvia +4"). A request can ask for every posting instead
(``collapse=False``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import lru_cache
import re
import unicodedata

from .filters import DEFAULT_COUNTRY, _fold, _regions, _structured_countries, location_countries

#: What :func:`place_of` says of a location.
PLACE_US, PLACE_UNCLEAR, PLACE_OTHER = "us", "unclear", "other"
UNCLEAR_LABEL = "unclear location"

#: What US only keeps, in one sentence (the API's ``us_only.rule``, the CLI's help, the UI's help line).
US_ONLY_RULE = (
    "US only hides a posting only when every place its location names is clearly outside the US. A posting in the US or "
    'remote in the US stays; "Remote" alone, no location, or a place Scout cannot read also stays and is labelled '
    '"unclear location".'
)
#: How the switch relates to "Show all" (the box's help line).
US_ONLY_WITH_SHOW_ALL = (
    "Show all drops the default work mode, countries and posted window; US only is a switch of its own and still applies "
    "with Show all until you turn it off. With US only off and Show all off, your default countries apply."
)
COPIES_RULE = (
    "The same company, the same title and the same description posted more than once (only the location differs) is one "
    "row that lists every location, also two cities of one country. A posting with another description, or with none "
    "stored, stays a row of its own."
)
CANONICAL_RULE = "The row is one job: its US posting when it has one, else the earliest posted."

#: How many locations a collapsed row names before "+N".
LOCATIONS_SHOWN = 3

_NOT_KEPT = re.compile(r"[^\w+#]+|_+")
_REMOTE_PREFIX = re.compile(r"^\s*remote\b[\s,;:/|()\[\]–—-]*", re.IGNORECASE)


def fold(text: object) -> str:
    """``text`` with case, spaces and punctuation aside: "Sr. MLOps  Engineer" == "sr mlops engineer". ``+`` and ``#`` stay."""

    return _folded(text) if type(text) is str else ""


@lru_cache(maxsize=131072)
def _folded(text: str) -> str:
    # Titles and company names repeat across the postings of a search: each is folded once.
    return " ".join(_NOT_KEPT.sub(" ", unicodedata.normalize("NFKC", text).casefold()).split())


def copy_key(board: str, company: object, title: object, content: str | None, removed: bool) -> tuple[str, str, str, str, bool] | None:
    """What two copies of one job share, or ``None`` for a posting that is never merged (no stored description).

    The board, the folded company name, the folded title, the description's digest (the company index's
    ``content_sha256``), removed or not.
    """

    if type(content) is not str or not content:
        return None
    return (board, fold(company), fold(title), content, bool(removed))


#: Region words that hold the US or may: a location of one of these alone is not "clearly outside the US".
_REGIONS_WITH_THE_US = frozenset({"amer", "americas", "worldwide"})
_WORD = re.compile(r"[a-z0-9]+")


def place_of(location: str | None, structured_countries: tuple[str, ...] | None = None) -> str:
    """Where a posting is, for US only: ``us``, ``other`` or ``unclear`` (see the module docstring).

    Structured countries (Lever, Ashby) decide when they name a country; a structured field that names NONE says
    nothing about where the job is, so the location text is read instead. A region word alone is ``other`` ("EMEA",
    "APAC", "Europe") unless the region holds the US or may ("Americas", "Worldwide": ``unclear``; "North America"
    names the US: ``us``).
    """

    structured = _structured_countries(structured_countries)
    if structured:
        return PLACE_US if DEFAULT_COUNTRY in structured else PLACE_OTHER
    if not location:
        return PLACE_UNCLEAR
    region_named, members = _regions(location)
    found = location_countries(location) | members
    if found:
        return PLACE_US if DEFAULT_COUNTRY in found else PLACE_OTHER
    if region_named:
        return PLACE_UNCLEAR if _REGIONS_WITH_THE_US & set(_WORD.findall(_fold(location))) else PLACE_OTHER
    return PLACE_UNCLEAR  # "Remote" / "Anywhere" alone, or a place no table knows


def in_us(location: str | None, structured_countries: tuple[str, ...] | None = None) -> bool:
    """Whether US only keeps a posting at ``location``: everything but a place clearly outside the US."""

    return place_of(location, structured_countries) != PLACE_OTHER


def us_only_default(countries: Iterable[str] | None) -> bool:
    """ON for a US setup: the configured countries hold the US."""

    return DEFAULT_COUNTRY in {str(code).upper() for code in countries or ()}


def distinct_locations(locations: Iterable[object]) -> list[str]:
    """The locations of a row's copies, each once (by its folded text), in the order given; empty ones are left out."""

    seen: set[str] = set()
    found: list[str] = []
    for location in locations:
        if type(location) is not str or not location.strip():
            continue
        key = fold(location)
        if key not in seen:
            seen.add(key)
            found.append(" ".join(location.split()))
    return found


def locations_text(locations: Sequence[str], *, shown: int = LOCATIONS_SHOWN) -> str:
    """One line for a row's locations: "Remote: Estonia, Lithuania, Latvia +4", "Berlin; Munich", or the one location."""

    places = distinct_locations(locations)
    if len(places) <= 1:
        return places[0] if places else ""
    rest = [_REMOTE_PREFIX.sub("", place).strip(" )]") for place in places]
    remote = all(_REMOTE_PREFIX.match(place) for place in places) and all(rest)
    more = f" +{len(places) - shown}" if len(places) > shown else ""
    if remote:
        return "Remote: " + ", ".join(rest[:shown]) + more
    return "; ".join(places[:shown]) + more


__all__ = [
    "CANONICAL_RULE",
    "COPIES_RULE",
    "PLACE_OTHER",
    "PLACE_UNCLEAR",
    "PLACE_US",
    "UNCLEAR_LABEL",
    "LOCATIONS_SHOWN",
    "US_ONLY_RULE",
    "US_ONLY_WITH_SHOW_ALL",
    "copy_key",
    "distinct_locations",
    "fold",
    "in_us",
    "locations_text",
    "place_of",
    "us_only_default",
]
