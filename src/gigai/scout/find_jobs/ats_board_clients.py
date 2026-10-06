"""Public ATS board clients for Greenhouse, Lever, and Ashby (A-3).

Implements ``ATSBoardClient`` from ``scout_find_jobs_contracts`` against the
three providers' public, unauthenticated job-board endpoints. Board-token
extraction is not reimplemented here: callers (and this module's own helpers)
use the frozen ``parse_board_url`` for URL -> ``(provider, token)``.

Assumed public API shapes (no auth, read as of 2026-09; each provider may
change its response shape without notice, so failures are treated as
``bad_json``/``http_error`` rather than asserted forever):

* Greenhouse ``GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true``
  -> ``{"jobs": [{"id", "title", "absolute_url", "location": {"name"},
  "first_published", "updated_at", "content"}]}``.
* Lever ``GET https://api.lever.co/v0/postings/{token}?mode=json``
  -> ``[{"id", "text", "hostedUrl", "categories": {"location"},
  "createdAt" (epoch ms), "descriptionPlain"}]``.
* Ashby ``GET https://api.ashbyhq.com/posting-api/job-board/{token}``
  -> ``{"jobs": [{"id", "title", "location", "jobUrl", "publishedAt",
  "descriptionPlain", "descriptionHtml"}]}`` (the HTML one is read only when
  there is no plain one).

Q2 (acquire at scale) adds a second entry point next to ``list_board``:
``ATSBoardClients.fetch_board`` returns the same ``PostingRow`` tuple plus a
``BoardFetchStats`` and, given a ``BoardCache``, makes conditional GETs
(``If-None-Match``/``If-Modified-Since`` from the cached response; a ``304``
or an unchanged body digest is a cache hit). Greenhouse is fetched in two
phases there -- the list WITHOUT ``?content=true`` (small), a title prefilter
(``matches_roles``), then ``GET /v1/boards/{token}/jobs/{id}`` only for the
matching jobs, and only when the job's ``updated_at`` changed since the
cached detail -- so a 1,000-posting board with two matching titles costs one
small list request (or a 304) plus at most two detail requests. Lever and
Ashby lists already carry the description, so their prefilter is applied
in-list and no detail request exists. ``list_board`` (single ``?content=true``
request, no cache) is unchanged: ``job_input.py``'s one-posting fallback and
the older tests still use it.

A row's ``published_at`` is the day the posting WENT UP (:data:`PUBLISHED_FIELDS`),
never its last change (0110-10-14): Greenhouse's ``first_published`` (on the
list and on the job; the public documentation shows it on the job), Lever's
``createdAt`` (the only date its list carries), Ashby's ``publishedAt``. A
Greenhouse job with no ``first_published`` has no ``published_at``; its
``updated_at`` stays what it is, the change marker (and the index's "updated").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import html as _html_entities
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Callable

import pycountry

from ...canonical import digest_imported_bytes
from .contracts import (
    ATSProvider,
    FindJobsConfig,
    PayPeriod,
    PostingPay,
    PostingRow,
    SourceKind,
    WorkMode,
    content_hash,
    normalize_url,
    parse_board_url,
)
from .filters import sponsorship_from_text

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx


# Block-level tags that should force a line break in the extracted text so
# paragraphs/list items/headings don't get glued together (U25: keep the
# posting text readable, not a wall of words).
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
        "tr", "table", "blockquote", "section", "article", "header", "footer",
    }
)


class _HTMLTextExtractor(HTMLParser):
    """Minimal stdlib HTML -> plain text, no new dependency (U25).

    Greenhouse's ``content`` field is HTML-escaped HTML (entities decoded by
    ``html.parser`` automatically); this collapses tags to line breaks and
    drops ``<script>``/``<style>`` bodies, keeping only visible text.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        # Collapse runs of horizontal whitespace, but keep line structure.
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in joined.splitlines()]
        collapsed = "\n".join(line for line in lines if line)
        return collapsed.strip()


def html_to_text(html: str | None) -> str:
    """Convert an HTML posting body to readable plain text.

    Uses only :mod:`html.parser` from the standard library (no new
    dependency). A malformed fragment degrades gracefully: ``HTMLParser``
    tolerates unclosed/invalid tags rather than raising, so worst case is
    imperfect line breaks, never an exception. Falls through unchanged when
    ``html`` doesn't look like markup at all (Lever/Ashby's ``descriptionPlain``
    is already plain text).

    0.1.8.1 B3 fix: Greenhouse's ``content`` field is *HTML-escaped HTML* --
    real tags encoded as text, e.g. ``"&lt;p&gt;...&lt;/p&gt;"`` rather than
    ``"<p>...</p>"`` (confirmed against a live evidence run's raw payload:
    ``ats_board_clients.py``'s own module docstring already said this, but
    the code never actually decoded that outer layer of escaping). The old
    ``"<" not in html`` check saw no literal ``<`` in that escaped string,
    took the "already plain text" branch, and returned the raw
    entity-escaped soup completely unprocessed -- which broke
    ``sponsorship_from_text``'s phrase matching for every Greenhouse
    posting (0.1.8.1 B3: acquire.json showed 69/69 "sponsorship unknown";
    the Greenhouse share of those rows all had this exact symptom, still
    carrying literal ``"&amp;nbsp;"``/``"&lt;li&gt;"`` in their stored
    ``text``). Decoding entities *before* checking for ``<`` reveals the
    real tags underneath (or, for genuinely plain text -- Lever/Ashby's
    ``descriptionPlain`` -- is a safe no-op/idempotent pass that only
    resolves any literal ``&amp;``-style entities that plain text might
    itself contain, which is the correct display form either way).
    """

    if not html:
        return ""
    decoded = _html_entities.unescape(html)
    if "<" not in decoded:
        # Already plain text (e.g. descriptionPlain) -- nothing to strip.
        return decoded.strip()
    parser = _HTMLTextExtractor()
    parser.feed(decoded)
    parser.close()
    return parser.text()


class ATSBoardClientError(ValueError):
    """A redacted ATS board-listing failure.

    Messages never include response bodies, headers, or credentials -- only
    the provider, board token, and a stable ``code``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
# Q2 two-phase Greenhouse: the content-free list, then one detail per match.
_GREENHOUSE_LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
_GREENHOUSE_JOB_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"
_LEVER_URL = "https://api.lever.co/v0/postings/{token}?mode=json"
# Q4b-data: Ashby's public board API omits ``compensation`` unless asked
# (``includeCompensation=true``); it is the same single request, so the pay
# range costs no extra call. The param is part of the cached URL, so every
# Ashby board misses its per-board cache exactly once after this change.
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


_WORD_RE = re.compile(r"[a-z0-9+#]+")
# Words that carry no role meaning: dropped from both sides.
_FILLER_WORDS = frozenset({"of", "the", "and", "a", "an", "for", "in"})
# Seniority words never decide a prefilter match (ranking judges fit); "sr"
# is the abbreviation of "senior".
_SENIORITY_WORDS = frozenset({"sr", "senior", "associate", "managing"})


#: A role that matches every string title. Internal: lets a caller that does not
#: know the title yet (URL lookup) list a whole board through ``matches_roles``.
MATCH_ANY_TITLE_ROLE = "\x00any-title"

_ALIASES = {"engineering": "engineer", "sr": "senior"}


def _stem(word: str) -> str:
    """Fold engineer/engineers/engineering and plurals to one form.

    Deliberately tiny: whole-word matching must still let the role word
    "engineer" meet "Engineering" in a title, but never match inside an
    unrelated word ("ai" stays "ai", "maintain" stays "maintain").
    """

    if word in _ALIASES:
        return _ALIASES[word]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return _ALIASES.get(word[:-1], word[:-1])
    return word


def _words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD_RE.findall(text.lower()) if w not in _FILLER_WORDS]


# --- the role as a whole (0.1.11.3, packet 14) ----------------------------------------------
#
# A role's words all being somewhere in a title is not that role: "Staff Training Engineer" has the words of "Staff
# Engineer". The rule below reads a title in SEGMENTS and asks for the role's words together.

#: Where a title is cut into segments: , ; : | / ( ) [ ] { } @, a dash of its own (" - ") and an en or em dash.
_SEGMENT_RE = re.compile(r"[,;:|/()\[\]{}@]|\s-+\s|[\u2013\u2014]")


def _stems(words: str) -> frozenset[str]:
    return frozenset(_stem(word) for word in words.split())


#: Level and seniority words, and discipline words (the kinds of the same role: "Staff SOFTWARE Engineer"). They are
#: what the comma-inverted form may hold beside the role's words in its first part: see ``matches_roles``.
_LEVEL_WORDS = _stems(
    "senior sr staff principal lead junior jr mid associate managing distinguished fellow founding member technical tech "
    "i ii iii iv v vi l1 l2 l3 l4 l5 l6 l7 l8 e3 e4 e5 e6 e7 e8 ic"
)
_DISCIPLINE_WORDS = _stems(
    "software backend back end frontend front fullstack full stack platform infrastructure infra cloud devops sre site "
    "reliability production system systems distributed service services api core web mobile ios android "
    "data analytics database storage compute network networking security appsec "
    "ai ml machine learning deep genai llm nlp vision computer applied research science "
    "product developer development dev tools tooling build release automation test qa quality performance "
    "observability kernel os linux hardware firmware embedded robotics mechanical electrical "
    "python java javascript typescript go golang rust c c++ c# ruby scala kotlin swift react node js php elixir sql net"
)
_NEUTRAL_WORDS = _LEVEL_WORDS | _DISCIPLINE_WORDS
#: THE DENY WORDS (operator decision, 0.1.11.3). BETWEEN a role's words one of these makes it another role ("Staff
#: TRAINING Engineer", "Staff SALES Engineer", "Staff APPLICATION Engineer"). Any other word there is the kind of the
#: same role and is kept ("Staff Payments Engineer"): a real job hidden is worse than an odd one shown, and the
#: profile's "titles to avoid" take out the rest. A role that names the word itself is not denied it.
TITLE_DENY_WORDS = (
    "training", "trainer", "sales", "presales", "support", "solutions", "customer", "field", "application", "program",
    "recruiting", "recruiter", "coordinator", "intern",
)
_DENY_WORDS = frozenset(_stem(word) for word in TITLE_DENY_WORDS)
#: Another role's noun. Between the role's words, in front of them, at the end of the segment before them, or after
#: them in their segment, it says the posting is that role ("Staff Engineering MANAGER", "DIRECTOR, Software Engineering").
_ROLE_NOUNS = _stems(
    "manager mgr director head vp svp evp president officer trainer instructor recruiter sourcer coordinator specialist "
    "analyst consultant representative technician advocate evangelist writer intern"
)
#: After the role's words the same nouns, and a trainee's words ("Staff Engineer in Training").
_AFTER_WORDS = _ROLE_NOUNS | _stems("training trainee")
_ENGINEERING_NOUNS = _stems("engineer developer programmer architect")
#: In front of an engineering role these deny words make it a customer-facing or teaching one ("Sales Engineering
#: Manager"). Not "application" and "program" there: "Application Security Engineer" is a "Security Engineer".
_ROLE_CHANGING_WORDS = _DENY_WORDS - _stems("application program")


def _needed(role: str) -> list[str]:
    """The words a title must hold for ``role``: its words, seniority words aside (unless it has no other)."""

    role_words = _words(role)
    return [w for w in role_words if w not in _SENIORITY_WORDS] or role_words


def _title_segments(title: str) -> tuple[list[str], list[int]]:
    """``title``'s words in order, and for each the number of the segment it is in."""

    words: list[str] = []
    segment_of: list[int] = []
    for number, segment in enumerate(_SEGMENT_RE.split(title.lower())):
        for word in _WORD_RE.findall(segment):
            if word not in _FILLER_WORDS:
                words.append(_stem(word))
                segment_of.append(number)
    return words, segment_of


def _role_as_a_whole(words: list[str], segment_of: list[int], needed: frozenset[str]) -> bool:
    """Whether ``needed`` (a role's words, all of them in ``words``) stand together as that role. See ``matches_roles``."""

    between_veto = (_DENY_WORDS | _ROLE_NOUNS) - needed
    front_veto = _ROLE_NOUNS - needed
    if needed & _ENGINEERING_NOUNS and not needed & _ROLE_CHANGING_WORDS:
        front_veto = front_veto | _ROLE_CHANGING_WORDS
    after_veto = _AFTER_WORDS - needed
    count = len(words)
    for start in range(count):
        if words[start] not in needed:
            continue
        seen: set[str] = set()
        end = start
        while end < count:
            word = words[end]
            if word in needed:
                seen.add(word)
                if len(seen) == len(needed):
                    break
            elif word in between_veto:
                break
            end += 1
        if len(seen) != len(needed):
            continue
        first, last = segment_of[start], segment_of[end]
        head = start
        while head > 0 and segment_of[head - 1] == first:
            head -= 1
        if first != last:
            # The comma-inverted form ("Director, Engineering"; "Software Engineer, Staff"): its first part is the role's
            # own words with level and discipline words only ("Staff Accountant, Engineering" is no "Staff Engineer").
            part = [word for word, segment in zip(words[head:end], segment_of[head:end]) if segment == first]
            if any(word not in needed and word not in _NEUTRAL_WORDS and not word.isdigit() for word in part):
                continue
        elif any(word in front_veto for word in words[head:start]):
            continue
        if head > 0 and words[head - 1] in _ROLE_NOUNS and words[head - 1] not in needed:
            continue
        tail = end + 1
        while tail < count and segment_of[tail] == last:
            if words[tail] in after_veto:
                break
            tail += 1
        else:
            return True
    return False


def role_fit(title: str, roles: tuple[str, ...]) -> bool | None:
    """How ``title`` stands to ``roles``: ``True`` a role matches as a whole; ``False`` a role's words are all in the
    title but as another role (a function tag must not bring it back either); ``None`` no role's words are all there.
    """

    if type(title) is not str:
        return None
    words: list[str] | None = None
    segment_of: list[int] = []
    present: frozenset[str] = frozenset()
    found: bool | None = None
    for role in roles:
        if role == MATCH_ANY_TITLE_ROLE:
            return True
        needed = frozenset(_needed(role))
        if not needed:
            continue
        if words is None:
            words, segment_of = _title_segments(title)
            present = frozenset(words)
        if not needed <= present:
            continue
        if _role_as_a_whole(words, segment_of, needed):
            return True
        found = False
    return found


def matches_roles(title: str, roles: tuple[str, ...]) -> bool:
    """Case-insensitive whole-word match of a configured role AS A WHOLE (0.1.11.3).

    Every word of the role must be a word of the title (seniority words of
    the role, Sr. / Senior / Associate / Managing, are not required; filler
    words, of / the / and / a / an / for / in, are ignored on both sides), and
    the words must stand together:

    * between them any word may sit ("Staff Software Engineer", "Staff
      Payments Engineer" are a "Staff Engineer") except a deny word
      (``TITLE_DENY_WORDS``: Training, Trainer, Sales, Presales, Support,
      Solutions, Customer, Field, Application, Program, Recruiting,
      Recruiter, Coordinator, Intern) and another role's noun
      (``_ROLE_NOUNS``: Manager, Director, Trainer, Recruiter, ...). "Staff
      Training Engineer", "Staff Sales Engineer" and "Staff Application
      Engineer" are not a "Staff Engineer". A role that names the word itself
      keeps it ("Sales Engineer" matches "Senior Sales Engineer");
    * their order is free and they may straddle a segment break, so
      "Director of Engineering" is "Director, Engineering" and "Engineering
      Director", and "Staff Software Engineer" is "Software Engineer, Staff".
      That inverted form is read strictly in its FIRST part: beside the
      role's words it holds only level words (Senior, Staff, Principal, Lead,
      II, ...) and discipline words (``_DISCIPLINE_WORDS``), so "Staff
      Accountant, Engineering" and "Chief of Staff, Engineering" are no
      "Staff Engineer";
    * in ONE segment anything may come in front ("Payments Software
      Engineer", "Enterprise Account Executive") except another role's noun
      ("Director Software Engineering") and, for an engineering role that
      names none of them itself, a deny word other than Application and
      Program ("Sales Engineering Manager"; "Application Security Engineer"
      stays a "Security Engineer");
    * the segment before must not end in another role's noun ("Director,
      Software Engineering" is no "Software Engineer");
    * after them, in their segment, no other role's noun and no trainee word
      ("Staff Engineering Manager", "Staff Engineer in Training"). Any other
      word there, and every later segment, names the team or area and is free:
      "Staff Engineer - Payments", "Staff Software Engineer, ML Training
      Infrastructure".

    A title is cut into segments at ``, ; : | / ( ) [ ] { } @``, at a dash
    with a space on both sides and at an en or em dash. This is the list's
    title rule, not only a prefilter: a profile's "titles to avoid"
    (``title_query.title_avoided``) then takes titles out, and ranking judges
    fit. An empty ``roles`` tuple matches nothing (fail closed, not fail open).
    """

    return role_fit(title, roles) is True


def _redacted_fail(code: str, provider: str, board_token: str) -> None:
    raise ATSBoardClientError(code, f"{provider} board {board_token!r} request failed")


def _request(client: "httpx.Client", url: str, provider: str, board_token: str) -> object:
    import httpx

    try:
        response = client.get(url)
    except httpx.HTTPError:
        _redacted_fail("network_error", provider, board_token)
        raise AssertionError("unreachable")
    if response.status_code != 200:
        _redacted_fail("http_error", provider, board_token)
    try:
        return response.json()
    except ValueError:
        _redacted_fail("bad_json", provider, board_token)
        raise AssertionError("unreachable")


def _company_from_token(board_token: str) -> str:
    return board_token


def _text_bytes(*parts: str | None) -> bytes:
    return "\n".join(part for part in parts if part).encode("utf-8")


def posting_content_digest(title: str, text: str | None) -> str:
    """THE posting digest rule (0110-8-06): the title and the plain posting text joined by a newline, through ``gigai.canonical``.

    A board row's ``content_sha256``, an assessment's ``posting_sha256`` and the digest the job-state check recomputes from a stored
    assessment's text are all this function, so they can only disagree when the TEXT does. A posting with no text digests its title
    alone: that is a digest of the listing, never of a description (see ``PostingRow.text``).
    """

    return content_hash(_text_bytes(title, text or None))


#: 0110-10-14: the list field each provider's ``published_at`` is read from: the day the posting went up. A provider
#: added here says so in ``scout_new.PUBLISHED_KINDS`` too (its test compares the two tables).
PUBLISHED_FIELDS = {"greenhouse": "first_published", "lever": "createdAt", "ashby": "publishedAt"}


def _published_at_from_iso(value: object) -> str | None:
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return value


def _normalize_country(value: object) -> str | None:
    """Normalize a provider's own country string/code to ISO alpha-2.

    Only exact matches against :mod:`pycountry`'s alpha-2/alpha-3/name/
    official_name/common_name index (``pycountry.countries.lookup``) are
    accepted -- never ``search_fuzzy``, which fuzzy-matches short/garbage
    tokens like "AMER" to an unrelated country (confirmed against
    ``pycountry`` directly: ``search_fuzzy("AMER")`` returns American Samoa/
    Cameroon/the US) and would silently turn a region code into a false
    country match, exactly the 0.1.8.1 B1 bug. A leading "The " (Ashby's
    ``secondaryLocations`` sometimes sends "The Netherlands") is stripped
    once and retried, since pycountry's own name for that country is just
    "Netherlands". Anything else -- a region token, "Remote", an internal
    label like "z-Test & Templates Only", or an already-ISO alpha-2 code --
    either resolves deterministically or returns ``None`` (never a guess).
    """

    if type(value) is not str or not value.strip():
        return None
    candidate = value.strip()
    try:
        return pycountry.countries.lookup(candidate).alpha_2
    except LookupError:
        pass
    if candidate.lower().startswith("the "):
        try:
            return pycountry.countries.lookup(candidate[4:].strip()).alpha_2
        except LookupError:
            pass
    return None


def _published_at_from_epoch_ms(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        parsed = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return parsed.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Q4b-data: work mode + pay, read ONLY from each provider's structured fields
# (never from the location or description text). Every helper returns None
# when the payload does not state the value, so the row omits the field.
# ---------------------------------------------------------------------------

_WORK_MODE_LABELS = {
    "remote": WorkMode.REMOTE,
    "hybrid": WorkMode.HYBRID,
    "onsite": WorkMode.ONSITE,
}
# Lever ``salaryRange.interval`` -> PayPeriod. Lever also quotes
# ``per-week-salary``/``semi-month-salary``/``bi-week-salary``/``per-day-wage``/
# ``one-time``: an interval the contract cannot express is a STATED interval
# we would misreport as "no period", so such a range is skipped entirely.
_LEVER_INTERVALS = {
    "per-year-salary": PayPeriod.YEAR,
    "per-month-salary": PayPeriod.MONTH,
    "per-hour-wage": PayPeriod.HOUR,
}
# Ashby compensation component ``interval`` -> PayPeriod (same skip rule for
# an interval outside this table, e.g. ``NONE`` on a one-time component).
_ASHBY_INTERVALS = {
    "1 YEAR": PayPeriod.YEAR,
    "1 MONTH": PayPeriod.MONTH,
    "1 HOUR": PayPeriod.HOUR,
}


def work_mode_from_label(value: object) -> WorkMode | None:
    """A provider's workplace-type label -> :class:`WorkMode`, or ``None``.

    Accepts Ashby's ``Remote``/``Hybrid``/``OnSite`` and Lever's
    ``remote``/``hybrid``/``onsite``/``on-site`` spellings (case and
    punctuation ignored). ``unspecified``, ``null``, a free-text label or a
    non-string all mean "not stated" -> ``None``.
    """

    if type(value) is not str:
        return None
    return _WORK_MODE_LABELS.get(re.sub(r"[^a-z]", "", value.lower()))


def _amount(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0:  # NaN / negative
        return None
    return value


def _cents_to_units(value: object) -> int | float | None:
    amount = _amount(value)
    if amount is None:
        return None
    units = amount / 100
    return int(units) if float(units).is_integer() else units


def _pay(minimum: object, maximum: object, currency: object, period: PayPeriod | None) -> PostingPay | None:
    """A :class:`PostingPay` when at least one bound and a currency are stated."""

    low, high = _amount(minimum), _amount(maximum)
    if low is None and high is None:
        return None
    if type(currency) is not str or not currency.strip():
        return None
    if low is not None and high is not None and low > high:
        return None
    return PostingPay(low, high, currency.strip().upper(), period)


def lever_work_mode(job: dict[str, object]) -> WorkMode | None:
    """Lever ``workplaceType`` (``remote``/``hybrid``/``onsite``/``unspecified``)."""

    return work_mode_from_label(job.get("workplaceType"))


def lever_pay(job: dict[str, object]) -> PostingPay | None:
    """Lever ``salaryRange: {min, max, currency, interval}`` -> pay, when stated."""

    salary_range = job.get("salaryRange")
    if type(salary_range) is not dict:
        return None
    interval = salary_range.get("interval")
    period: PayPeriod | None = None
    if type(interval) is str and interval.strip():
        period = _LEVER_INTERVALS.get(interval.strip().lower())
        if period is None:
            return None
    return _pay(salary_range.get("min"), salary_range.get("max"), salary_range.get("currency"), period)


def ashby_work_mode(job: dict[str, object]) -> WorkMode | None:
    """Ashby ``workplaceType`` (``Remote``/``Hybrid``/``OnSite``), else ``isRemote: true``.

    ``isRemote: false`` alone says nothing about hybrid vs. on-site, so it
    never sets a mode.
    """

    mode = work_mode_from_label(job.get("workplaceType"))
    if mode is not None:
        return mode
    return WorkMode.REMOTE if job.get("isRemote") is True else None


def ashby_pay(job: dict[str, object]) -> PostingPay | None:
    """Ashby ``compensation`` -> the first stated ``Salary`` component's range.

    Reads ``compensation.summaryComponents`` (the board API's flattened
    view), falling back to the first tier's ``components``. Only the
    ``Salary`` component is a pay range (``Bonus``/``Commission``/equity
    components are not); its ``interval`` (``1 YEAR``/``1 MONTH``/``1 HOUR``)
    is the period. The compensation object arrives only when the board was
    fetched with ``includeCompensation=true`` (see ``_ASHBY_URL``).
    """

    compensation = job.get("compensation")
    if type(compensation) is not dict:
        return None
    components: object = compensation.get("summaryComponents")
    if type(components) is not list or not components:
        tiers = compensation.get("compensationTiers")
        components = None
        if type(tiers) is list and tiers and type(tiers[0]) is dict:
            components = tiers[0].get("components")
    if type(components) is not list:
        return None
    for component in components:
        if type(component) is not dict:
            continue
        kind = component.get("compensationType")
        if type(kind) is not str or kind.strip().lower() != "salary":
            continue
        interval = component.get("interval")
        period: PayPeriod | None = None
        if type(interval) is str and interval.strip():
            period = _ASHBY_INTERVALS.get(" ".join(interval.upper().split()))
            if period is None:
                continue
        pay = _pay(component.get("minValue"), component.get("maxValue"), component.get("currencyCode"), period)
        if pay is not None:
            return pay
    return None


def greenhouse_pay(job: dict[str, object]) -> PostingPay | None:
    """Greenhouse ``pay_input_ranges[] {min_cents, max_cents, currency_type}`` -> pay.

    Only what the already-fetched list/detail payload carries (no extra
    request): the first usable range, cents converted to units. Greenhouse
    states no interval, so ``period`` is ``None`` -- never inferred
    (coordinator answer, 2026-09-25). Greenhouse has no workplace-type
    field either, so its rows never carry ``work_mode``.
    """

    ranges = job.get("pay_input_ranges")
    if type(ranges) is not list:
        return None
    for item in ranges:
        if type(item) is not dict:
            continue
        pay = _pay(_cents_to_units(item.get("min_cents")), _cents_to_units(item.get("max_cents")), item.get("currency_type"), None)
        if pay is not None:
            return pay
    return None


def list_greenhouse_board(client: "httpx.Client", board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
    url = _GREENHOUSE_URL.format(token=board_token)
    payload = _request(client, url, "greenhouse", board_token)
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        _redacted_fail("bad_json", "greenhouse", board_token)
    rows: list[PostingRow] = []
    for job in payload["jobs"]:  # type: ignore[index]
        if type(job) is not dict:
            continue
        title = job.get("title")
        if type(title) is not str or not matches_roles(title, config.roles):
            continue
        absolute_url = job.get("absolute_url")
        if type(absolute_url) is not str or not absolute_url:
            continue
        content = job.get("content")
        rows.append(_greenhouse_row(job, title, absolute_url, content if type(content) is str else None, board_token))
    return tuple(rows)


def _greenhouse_row(
    job: dict[str, object],
    title: str,
    absolute_url: str,
    content: str | None,
    board_token: str,
    detail: dict[str, object] | None = None,
) -> PostingRow:
    """One Greenhouse job (+ its HTML ``content``, from the list or a detail call) -> ``PostingRow``.

    ``detail`` is the already-fetched detail payload when the two-phase
    fetch made one; its ``pay_input_ranges`` win over the list item's, and
    its ``first_published`` is read when the list item carries none.
    """

    pay = greenhouse_pay(detail) if detail is not None else None
    if pay is None:
        pay = greenhouse_pay(job)
    # 0110-10-14: the day the posting went up. Never ``updated_at``: an edit is not a posting day.
    published_at = _published_at_from_iso(job.get("first_published"))
    if published_at is None and detail is not None:
        published_at = _published_at_from_iso(detail.get("first_published"))
    location = job.get("location")
    location_name = ""
    if type(location) is dict and type(location.get("name")) is str:
        location_name = location["name"]
    # Greenhouse's `content` is HTML-escaped HTML; keep the readable plain
    # text (U25) and hash *that*, not the raw markup, so the digest tracks
    # the posting's actual wording.
    text = html_to_text(content)
    return PostingRow(
        url=absolute_url,
        normalized_url=normalize_url(absolute_url),
        provider=ATSProvider.GREENHOUSE,
        board_token=board_token,
        company=_company_from_token(board_token),
        title=title,
        location=location_name,
        published_at=published_at,
        content_sha256=posting_content_digest(title, text),
        source_kind=SourceKind.ATS,
        query_key=f"ats:greenhouse:{board_token}",
        text=text or None,
        sponsorship=sponsorship_from_text(text),
        pay=pay,
    )


def _lever_lists_text(lists: object) -> str:
    """Flatten Lever's ``lists`` array (structured sections) into text.

    Each item is ``{"text": <section heading>, "content": <HTML>}``
    (Requirements, Responsibilities, Benefits, ...). Not every board uses
    this; a missing/malformed value yields an empty string.
    """

    if type(lists) is not list:
        return ""
    sections: list[str] = []
    for item in lists:
        if type(item) is not dict:
            continue
        heading = item.get("text")
        body = html_to_text(item.get("content") if type(item.get("content")) is str else None)
        section = "\n".join(part for part in (heading if type(heading) is str else None, body) if part)
        if section:
            sections.append(section)
    return "\n\n".join(sections)


#: uat-bug-046: how much longer the HTML ``description`` must be than the plain
#: text before it replaces it (the same 1.5x and 500 chars the operator's
#: board cache was measured with: 1,460 of 68,943 postings).
_LEVER_HTML_MIN_RATIO = 1.5
_LEVER_HTML_MIN_GAIN = 500


def _lever_text(job: dict[str, object]) -> str:
    """A Lever posting's text: ``descriptionPlain`` + ``lists``, or the full HTML body.

    Lever's ``descriptionPlain`` is normally the whole opening and ``lists``
    carries Requirements/Benefits. Some boards put the entire posting in the
    HTML ``description`` instead (``descriptionPlain`` stops after a few
    paragraphs and ``lists`` is empty), and the requirements were lost.
    When ``html_to_text(description)`` is materially longer than the plain
    text + lists, the HTML text is used (with ``lists`` appended only if the
    HTML does not already contain them); otherwise the text is exactly what
    it always was, so every other posting keeps its content hash. With no
    plain text and no lists at all, ``description`` is the fallback.
    """

    plain = job.get("descriptionPlain")
    html = job.get("description")
    plain_text = html_to_text(plain if type(plain) is str else None)
    lists_text = _lever_lists_text(job.get("lists"))
    current = "\n\n".join(part for part in (plain_text, lists_text) if part)
    html_text = html_to_text(html if type(html) is str else None)
    if html_text and (
        not current
        or (len(html_text) >= _LEVER_HTML_MIN_RATIO * len(current) and len(html_text) - len(current) >= _LEVER_HTML_MIN_GAIN)
    ):
        if lists_text and _squash(lists_text) not in _squash(html_text):
            return "\n\n".join((html_text, lists_text))
        return html_text
    return current


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()



def _lever_countries(job: dict[str, object]) -> tuple[str, ...] | None:
    """Lever's structured country signal: ``country`` + ``categories.allLocations``.

    Lever's own docs (``.orchestrator/research/country-data.md`` §2): a plain
    JSON object field ``"country"`` -- an ISO alpha-2 code, or ``null`` for
    "unknown country" -- present on every posting in the exact ``?mode=json``
    list response Scout already requests. When present and non-null, this
    *is* the trusted answer (normalized/validated through
    :func:`_normalize_country` since Lever's value is already ISO-2, so this
    is a validation pass, not a guess). ``categories.allLocations`` is a
    structured array covering multi-location postings the same way
    ``categories.location``'s free text does today; each entry is itself
    free text (no per-location country code), so it's normalized the same
    way as the primary ``location_name`` string would be, via
    :func:`_normalize_country` on each entry, adding to the primary
    ``country`` signal rather than replacing it. Returns ``None`` (no
    structured signal at all) only when neither ``country`` nor any
    ``allLocations`` entry resolves -- callers then fall back to parsing the
    free-text ``location`` string, same as before this packet.
    """

    found: set[str] = set()
    primary = _normalize_country(job.get("country"))
    if primary is not None:
        found.add(primary)
    categories = job.get("categories")
    all_locations = categories.get("allLocations") if type(categories) is dict else None
    if type(all_locations) is list:
        for entry in all_locations:
            code = _normalize_country(entry)
            if code is not None:
                found.add(code)
    if not found:
        return None
    return tuple(sorted(found))


def list_lever_board(client: "httpx.Client", board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
    url = _LEVER_URL.format(token=board_token)
    payload = _request(client, url, "lever", board_token)
    if type(payload) is not list:
        _redacted_fail("bad_json", "lever", board_token)
    return _lever_rows(payload, board_token, config)  # type: ignore[arg-type]


def _lever_rows(payload: list, board_token: str, config: FindJobsConfig, stats: "BoardFetchStats | None" = None) -> tuple[PostingRow, ...]:
    company = _company_from_token(board_token)
    rows: list[PostingRow] = []
    for job in payload:
        if type(job) is not dict:
            continue
        if stats is not None:
            stats.listed += 1
        title = job.get("text")
        if type(title) is not str or not matches_roles(title, config.roles):
            if stats is not None:
                stats.prefiltered_out += 1
            continue
        hosted_url = job.get("hostedUrl")
        if type(hosted_url) is not str or not hosted_url:
            continue
        categories = job.get("categories")
        location_name = ""
        if type(categories) is dict and type(categories.get("location")) is str:
            location_name = categories["location"]
        countries = _lever_countries(job)
        text = _lever_text(job)
        rows.append(
            PostingRow(
                url=hosted_url,
                normalized_url=normalize_url(hosted_url),
                provider=ATSProvider.LEVER,
                board_token=board_token,
                company=company,
                title=title,
                location=location_name,
                published_at=_published_at_from_epoch_ms(job.get("createdAt")),
                content_sha256=posting_content_digest(title, text),
                source_kind=SourceKind.ATS,
                query_key=f"ats:lever:{board_token}",
                text=text or None,
                sponsorship=sponsorship_from_text(text),
                countries=countries,
                work_mode=lever_work_mode(job),
                pay=lever_pay(job),
            )
        )
    return tuple(rows)


def _ashby_address_country(address: object) -> str | None:
    """Normalize one Ashby ``address`` object's ``postalAddress.addressCountry``.

    Ashby's own doc shape (`.orchestrator/research/country-data.md` §2):
    ``address`` -> ``postalAddress`` -> ``addressCountry``, all optionally
    ``null``/missing (confirmed against the evidence run's raw payload: a
    fully remote/region-labelled posting has ``"address": null``; a
    located one sends a country *name* like ``"United States"``, not an
    ISO code -- so this always goes through :func:`_normalize_country`,
    never trusted as already-ISO the way Lever's ``country`` is).
    """

    if type(address) is not dict:
        return None
    postal = address.get("postalAddress")
    if type(postal) is not dict:
        return None
    return _normalize_country(postal.get("addressCountry"))


def _ashby_countries(job: dict[str, object]) -> tuple[str, ...] | None:
    """Ashby's structured country signal: ``address`` + ``secondaryLocations``.

    The primary ``address.postalAddress.addressCountry`` covers the job's
    main location; ``secondaryLocations`` (each with its own optional
    ``address`` of the identical shape, confirmed in the evidence run) covers
    additional locations the same way Lever's ``categories.allLocations``
    does -- structured where present, but each entry can itself have a
    ``null`` address (evidence run: several ``secondaryLocations`` entries
    carry only a free-text ``location`` name with no ``address`` at all), in
    which case that one entry contributes nothing and the row falls back to
    whatever the primary signal (or free-text ``location`` parsing) found.
    Returns ``None`` when nothing structured resolves at all.
    """

    found: set[str] = set()
    primary = _ashby_address_country(job.get("address"))
    if primary is not None:
        found.add(primary)
    secondary = job.get("secondaryLocations")
    if type(secondary) is list:
        for entry in secondary:
            if type(entry) is not dict:
                continue
            code = _ashby_address_country(entry.get("address"))
            if code is not None:
                found.add(code)
    if not found:
        return None
    return tuple(sorted(found))


def list_ashby_board(client: "httpx.Client", board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
    url = _ASHBY_URL.format(token=board_token)
    payload = _request(client, url, "ashby", board_token)
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        _redacted_fail("bad_json", "ashby", board_token)
    return _ashby_rows(payload["jobs"], board_token, config)  # type: ignore[index]


def _ashby_rows(jobs: list, board_token: str, config: FindJobsConfig, stats: "BoardFetchStats | None" = None) -> tuple[PostingRow, ...]:
    company = _company_from_token(board_token)
    rows: list[PostingRow] = []
    for job in jobs:
        if type(job) is not dict:
            continue
        if stats is not None:
            stats.listed += 1
        title = job.get("title")
        if type(title) is not str or not matches_roles(title, config.roles):
            if stats is not None:
                stats.prefiltered_out += 1
            continue
        job_url = job.get("jobUrl")
        if type(job_url) is not str or not job_url:
            continue
        location = job.get("location")
        location_name = location if type(location) is str else ""
        countries = _ashby_countries(job)
        description = job.get("descriptionPlain")
        if type(description) is not str or not description.strip():
            # Some boards send no plain text at all: the whole posting is in descriptionHtml.
            description = job.get("descriptionHtml")
        text = html_to_text(description if type(description) is str else None)
        rows.append(
            PostingRow(
                url=job_url,
                normalized_url=normalize_url(job_url),
                provider=ATSProvider.ASHBY,
                board_token=board_token,
                company=company,
                title=title,
                location=location_name,
                published_at=_published_at_from_iso(job.get("publishedAt")),
                content_sha256=posting_content_digest(title, text),
                source_kind=SourceKind.ATS,
                query_key=f"ats:ashby:{board_token}",
                countries=countries,
                text=text or None,
                sponsorship=sponsorship_from_text(text),
                work_mode=ashby_work_mode(job),
                pay=ashby_pay(job),
            )
        )
    return tuple(rows)


_LISTERS = {
    "greenhouse": list_greenhouse_board,
    "lever": list_lever_board,
    "ashby": list_ashby_board,
}


# ---------------------------------------------------------------------------
# Q2: acquire at scale -- per-board cache, conditional GETs, two-phase
# Greenhouse with a title prefilter before any detail request.
# ---------------------------------------------------------------------------


@dataclass
class BoardFetchStats:
    """What one ``fetch_board`` call cost; summed per run for progress/measurement."""

    requests: int = 0
    #: "miss" (fresh body stored), "hit" (304 / unchanged marker, no body
    #: transferred), "revalidated" (200 with an unchanged digest), or
    #: "bypass" (no cache given).
    cache: str = "bypass"
    listed: int = 0
    prefiltered_out: int = 0
    detail_fetched: int = 0
    detail_cached: int = 0
    detail_failed: int = 0
    #: 1 when this call ran the board's one-time ``?content=true`` fill pass.
    content_filled: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "requests": self.requests,
            "cache": self.cache,
            "listed": self.listed,
            "prefiltered_out": self.prefiltered_out,
            "detail_fetched": self.detail_fetched,
            "detail_cached": self.detail_cached,
            "detail_failed": self.detail_failed,
            "content_filled": self.content_filled,
        }


@dataclass(frozen=True)
class BoardFetchResult:
    rows: tuple[PostingRow, ...]
    stats: BoardFetchStats


@dataclass(frozen=True)
class CachedResponse:
    etag: str | None
    last_modified: str | None
    sha256: str
    marker: str | None
    body: bytes


# acquire-rotation: the per-board "last fetch attempt" index + rotation cursor.
LAST_FETCHED_SCHEMA = "scout-ats-last-fetched:1"
_LAST_FETCHED_FILENAME = "last-fetched.json"


@dataclass(frozen=True)
class BoardFetchIndex:
    """When each watchlist board was last *attempted*, plus the rotation cursor.

    ``boards`` maps ``"<provider>:<board token>"`` to the UTC ISO-8601 stamp
    of the run that last attempted it (fetched, served from the cache by a
    ``304``, or failed -- a dead board must rotate like a live one, or it
    would lead every run forever). ``cycle``/``cycle_started_at`` is the
    rotation cursor: a board stamped at or after ``cycle_started_at`` has
    been covered in the current rotation; when every planned board has,
    the next run starts a new cycle. ``page_sizes`` is how many boards per
    provider the previous run attempted, so the next run can say "full
    rotation every ~K runs" before its own page is measured.

    This is a CACHE, not a record: it lives next to the response cache
    under the GigAI home, never in a workpad or the journal, and losing it
    merely restarts the rotation from "nothing fetched yet".
    """

    boards: dict[str, str] = field(default_factory=dict)
    cycle: int = 1
    cycle_started_at: str | None = None
    page_sizes: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def key(provider: str, board_token: str) -> str:
        return f"{provider}:{board_token}"

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": LAST_FETCHED_SCHEMA,
            "cycle": self.cycle,
            "cycle_started_at": self.cycle_started_at,
            "page_sizes": dict(self.page_sizes),
            "boards": dict(self.boards),
        }

    @classmethod
    def from_json(cls, payload: object) -> "BoardFetchIndex":
        """A missing, corrupt or foreign-schema payload reads as the empty index."""

        if not isinstance(payload, dict) or payload.get("schema_version") != LAST_FETCHED_SCHEMA:
            return cls()
        raw_boards = payload.get("boards")
        boards = (
            {key: value for key, value in raw_boards.items() if isinstance(key, str) and isinstance(value, str)}
            if isinstance(raw_boards, dict)
            else {}
        )
        raw_cycle = payload.get("cycle")
        cycle = raw_cycle if isinstance(raw_cycle, int) and not isinstance(raw_cycle, bool) and raw_cycle >= 1 else 1
        started = payload.get("cycle_started_at")
        raw_pages = payload.get("page_sizes")
        page_sizes = (
            {
                key: value
                for key, value in raw_pages.items()
                if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0
            }
            if isinstance(raw_pages, dict)
            else {}
        )
        return cls(boards=boards, cycle=cycle, cycle_started_at=started if isinstance(started, str) else None, page_sizes=page_sizes)


class BoardCache:
    """A per-URL response cache for the public board endpoints.

    Layout: ``<root>/<provider>/<sha256(url)[:40]>.json`` (etag,
    last-modified, body digest, an optional caller ``marker`` such as a
    Greenhouse job's ``updated_at``, the URL) next to ``...body.gz``. Every
    write is temp-file + ``os.replace``; a missing or corrupt entry reads as
    a miss, never an error. Lives under the GigAI home (``<home>/cache/scout/
    ats-boards``, next to the H-1B cache), never inside a managed workpad,
    so it can be deleted freely and never dirties a journal. Response
    *headers* other than ``ETag``/``Last-Modified`` are never stored.
    """

    def __init__(self, root: Path, validator_source: "Callable[[str, str], tuple[str | None, str | None] | None] | None" = None) -> None:
        self.root = Path(root)
        #: ``(provider, url) -> (etag, last_modified)`` for a board whose body is not cached; default: the
        #: company index next to this cache (``<root>/../companies``), when that directory exists.
        self._validator_source = validator_source

    def indexed_validators(self, provider: str, url: str) -> tuple[str | None, str | None] | None:
        """The company index's ETag/Last-Modified for ``url`` (e.g. from an imported snapshot), or ``None``."""

        source = self._validator_source
        if source is None:
            companies = self.root.parent / "companies"
            if not companies.is_dir():
                return None
            from .company_index import CompanyIndex

            source = CompanyIndex(companies).validators_for_url
            self._validator_source = source
        try:
            return source(provider, url)
        except Exception:  # noqa: BLE001 - a broken index never blocks a board fetch; the request stays unconditional
            return None

    def store_unchanged(self, provider: str, url: str, *, etag: str | None, last_modified: str | None) -> None:
        """Record a ``304`` answered to index validators when no body is cached: validators only, no body file."""

        meta_path, _ = self._paths(provider, url)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "url": url,
            "sha256": None,
            "etag": etag,
            "last_modified": last_modified,
            "marker": None,
            "not_modified": True,
            "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        tmp_meta = meta_path.with_name(meta_path.name + f".tmp{os.getpid()}")
        tmp_meta.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp_meta, meta_path)

    def lookup_unchanged(self, provider: str, url: str) -> tuple[str | None, str | None] | None:
        """``(etag, last_modified)`` of a validator-only ``304`` record for ``url``; ``None`` for anything else."""

        meta_path, body_path = self._paths(provider, url)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict) or meta.get("url") != url or meta.get("not_modified") is not True or body_path.exists():
            return None
        etag, last_modified = meta.get("etag"), meta.get("last_modified")
        return etag if isinstance(etag, str) else None, last_modified if isinstance(last_modified, str) else None

    def _paths(self, provider: str, url: str) -> tuple[Path, Path]:
        name = digest_imported_bytes(url.encode("utf-8")).removeprefix("sha256:")[:40]
        base = self.root / re.sub(r"[^a-z0-9_-]+", "_", provider.lower() or "other")
        return base / f"{name}.json", base / f"{name}.body.gz"

    def lookup(self, provider: str, url: str) -> CachedResponse | None:
        meta_path, body_path = self._paths(provider, url)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = gzip.decompress(body_path.read_bytes())
        except (OSError, ValueError, EOFError):
            return None
        if not isinstance(meta, dict) or meta.get("url") != url:
            return None
        sha = meta.get("sha256")
        if not isinstance(sha, str) or digest_imported_bytes(body) != sha:
            return None
        etag = meta.get("etag")
        last_modified = meta.get("last_modified")
        marker = meta.get("marker")
        return CachedResponse(
            etag if isinstance(etag, str) else None,
            last_modified if isinstance(last_modified, str) else None,
            sha,
            marker if isinstance(marker, str) else None,
            body,
        )

    def store(
        self,
        provider: str,
        url: str,
        *,
        body: bytes,
        etag: str | None,
        last_modified: str | None,
        marker: str | None,
    ) -> None:
        meta_path, body_path = self._paths(provider, url)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "url": url,
            "sha256": digest_imported_bytes(body),
            "etag": etag,
            "last_modified": last_modified,
            "marker": marker,
            "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        pid = os.getpid()
        tmp_body = body_path.with_name(body_path.name + f".tmp{pid}")
        tmp_body.write_bytes(gzip.compress(body, compresslevel=6))
        os.replace(tmp_body, body_path)
        tmp_meta = meta_path.with_name(meta_path.name + f".tmp{pid}")
        tmp_meta.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp_meta, meta_path)

    # -- 0110-026d: the one-time Greenhouse description fill ------------------

    def _filled_path(self, provider: str, board_token: str) -> Path:
        name = digest_imported_bytes(f"{provider}:{board_token}".encode("utf-8")).removeprefix("sha256:")[:40]
        return self.root / "content-filled" / f"{name}.json"

    def content_filled(self, provider: str, board_token: str) -> bool:
        """Whether this board's one-time ``?content=true`` fill pass has completed (a missing/corrupt marker reads ``False``)."""

        try:
            marker = json.loads(self._filled_path(provider, board_token).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(marker, dict) and marker.get("provider") == provider and marker.get("board") == board_token

    def mark_content_filled(self, provider: str, board_token: str) -> None:
        path = self._filled_path(provider, board_token)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        tmp.write_text(json.dumps({"provider": provider, "board": board_token, "filled_at": stamp}, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)

    def clear_content_filled(self, provider: str, board_token: str) -> None:
        """Forget the marker so the next update redoes the fill (a Full refresh)."""

        try:
            self._filled_path(provider, board_token).unlink()
        except OSError:
            pass

    def filled_jobs(self, board_token: str) -> dict[str, dict[str, object]]:
        """``{job id: job}`` from the cached ``?content=true`` body (descriptions included); ``{}`` when there is none."""

        entry = self.lookup("greenhouse", _GREENHOUSE_URL.format(token=board_token))
        if entry is None:
            return {}
        try:
            payload = json.loads(entry.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        found: dict[str, dict[str, object]] = {}
        for job in jobs if isinstance(jobs, list) else ():
            job_id = job.get("id") if isinstance(job, dict) else None
            if isinstance(job_id, (int, str)) and not isinstance(job_id, bool) and str(job_id):
                found[str(job_id)] = job
        return found

    # -- acquire-rotation: the last-fetched index ---------------------------

    @property
    def fetch_index_path(self) -> Path:
        return self.root / _LAST_FETCHED_FILENAME

    def load_fetch_index(self) -> BoardFetchIndex:
        """Read ``<root>/last-fetched.json``; missing or corrupt reads as empty (a fresh rotation)."""

        try:
            payload = json.loads(self.fetch_index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return BoardFetchIndex()
        return BoardFetchIndex.from_json(payload)

    def store_fetch_index(self, index: BoardFetchIndex) -> None:
        """Replace the index atomically (temp file + ``os.replace``); one write per call, never per board."""

        path = self.fetch_index_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(index.to_json(), separators=(",", ":"), sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def conditional_headers(entry: CachedResponse | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if entry is None:
            return headers
        if entry.etag:
            headers["If-None-Match"] = entry.etag
        if entry.last_modified:
            headers["If-Modified-Since"] = entry.last_modified
        return headers


def _cached_request(
    client: "httpx.Client",
    url: str,
    provider: str,
    board_token: str,
    *,
    cache: BoardCache | None,
    stats: BoardFetchStats,
    marker: str | None = None,
) -> tuple[bytes, str]:
    """GET ``url`` through the cache; returns ``(body, cache_status)``.

    ``marker`` (Greenhouse: the job's ``updated_at`` from the list) short-
    circuits without any request when the cached entry carries the same
    marker. Otherwise a conditional GET: ``304`` -> the cached body
    (``hit``); with no cached body but validators in the company index, the request is conditional and a
    ``304`` returns ``(b"", "unchanged")`` (nothing stored but the validators); ``200`` with an unchanged digest -> ``revalidated``; a new
    body -> ``miss`` and the entry is rewritten. Failure codes are exactly
    ``_request``'s (``network_error``/``http_error``), redacted the same way.
    """

    import httpx

    entry = cache.lookup(provider, url) if cache is not None else None
    if entry is not None and marker is not None and entry.marker == marker:
        return entry.body, "hit"
    headers = BoardCache.conditional_headers(entry)
    seeded: tuple[str | None, str | None] | None = None
    if entry is None and cache is not None:
        # No body on disk, but the company index (an imported snapshot, say) holds this board's validators.
        seeded = cache.indexed_validators(provider, url)
        if seeded is not None:
            if seeded[0]:
                headers["If-None-Match"] = seeded[0]
            if seeded[1]:
                headers["If-Modified-Since"] = seeded[1]
    try:
        response = client.get(url, headers=headers) if headers else client.get(url)
    except httpx.HTTPError:
        _redacted_fail("network_error", provider, board_token)
        raise AssertionError("unreachable")
    stats.requests += 1
    if response.status_code == 304 and entry is not None:
        if cache is not None and marker != entry.marker:
            cache.store(provider, url, body=entry.body, etag=entry.etag, last_modified=entry.last_modified, marker=marker)
        return entry.body, "hit"
    if response.status_code == 304 and seeded is not None and cache is not None:
        cache.store_unchanged(
            provider,
            url,
            etag=response.headers.get("etag") or seeded[0],
            last_modified=response.headers.get("last-modified") or seeded[1],
        )
        return b"", "unchanged"
    if response.status_code != 200:
        _redacted_fail("http_error", provider, board_token)
    body = bytes(response.content)
    status = "revalidated" if entry is not None and entry.sha256 == digest_imported_bytes(body) else "miss"
    if cache is not None:
        cache.store(
            provider,
            url,
            body=body,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            marker=marker,
        )
    elif entry is None:
        status = "bypass"
    return body, status


def _decode_json(body: bytes, provider: str, board_token: str) -> object:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _redacted_fail("bad_json", provider, board_token)
        raise AssertionError("unreachable")


def _fill_greenhouse_board(
    client: "httpx.Client",
    board_token: str,
    config: FindJobsConfig,
    cache: BoardCache,
    stats: BoardFetchStats,
) -> BoardFetchResult | None:
    """The one-time ``?content=true`` pass; ``None`` (nothing marked) when it failed, so the plain path runs.

    The full body is cached under its own URL (descriptions for the text
    index and later lookups); the plain list URL gets the same body with the
    ``content`` fields stripped (and the fill's validators), which every later
    plain list request replaces. One request, through the caller's paced client.
    """

    try:
        body, status = _cached_request(client, _GREENHOUSE_URL.format(token=board_token), "greenhouse", board_token, cache=cache, stats=stats)
        payload = _decode_json(body, "greenhouse", board_token)
    except ATSBoardClientError:
        return None
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        return None
    if payload["jobs"] and not any(type(job) is dict and type(job.get("content")) is str for job in payload["jobs"]):
        return None  # the board ignored ?content=true: not a fill, not marked; the plain two-phase path runs
    stats.cache = status
    stats.content_filled = 1
    stripped = {**payload, "jobs": [{k: v for k, v in job.items() if k != "content"} if type(job) is dict else job for job in payload["jobs"]]}
    full = cache.lookup("greenhouse", _GREENHOUSE_URL.format(token=board_token))
    cache.store(
        "greenhouse",
        _GREENHOUSE_LIST_URL.format(token=board_token),
        body=json.dumps(stripped, separators=(",", ":")).encode("utf-8"),
        etag=full.etag if full is not None else None,
        last_modified=full.last_modified if full is not None else None,
        marker=None,
    )
    rows: list[PostingRow] = []
    for job in payload["jobs"]:
        if type(job) is not dict:
            continue
        stats.listed += 1
        title = job.get("title")
        absolute_url = job.get("absolute_url")
        if type(title) is not str or not matches_roles(title, config.roles):
            stats.prefiltered_out += 1
            continue
        if type(absolute_url) is not str or not absolute_url:
            continue
        content = job.get("content")
        rows.append(_greenhouse_row(job, title, absolute_url, content if type(content) is str else None, board_token, job))
    cache.mark_content_filled("greenhouse", board_token)
    return BoardFetchResult(tuple(rows), stats)


def fetch_greenhouse_board(
    client: "httpx.Client",
    board_token: str,
    config: FindJobsConfig,
    *,
    cache: BoardCache | None = None,
    stats: BoardFetchStats | None = None,
    descriptions: bool = False,
    title_filter: "Callable[[str], bool] | None" = None,
) -> BoardFetchResult:
    """Two-phase Greenhouse: content-free list -> title prefilter -> detail per match.

    ``title_filter`` (0110-8-03, the sources update) decides which titles get a detail request in place of the bare
    ``matches_roles(title, config.roles)``: the update passes the union over every active profile, by rule or by function tag.

    A detail request is made only for a job whose title matches the roles
    AND whose ``updated_at`` differs from the cached detail's marker. A
    failed detail call (a 404, a shape change) falls back to the list's
    inline ``content`` when it carries one, else the row ships without text
    (sponsorship unknown) -- one bad posting never fails the board.

    ``descriptions=True`` (the sources update, 0110-026d) adds a one-time
    fill: while the board has no ``content_filled`` marker, ONE
    ``?content=true`` list request brings every posting WITH its
    description (kept in the cache body, never in the shipped snapshot),
    after which the board is marked. From then on lists are plain and a
    detail request is made only for a matching posting the board has no
    description for at all (a NEW one); a posting whose ``updated_at``
    alone changed keeps its cached description (not proven that the text
    changed; a Full refresh re-fills).
    """

    stats = stats if stats is not None else BoardFetchStats()
    if descriptions and cache is not None and not cache.content_filled("greenhouse", board_token):
        filled = _fill_greenhouse_board(client, board_token, config, cache, stats)
        if filled is not None:
            return filled
    list_url = _GREENHOUSE_LIST_URL.format(token=board_token)
    known: dict[str, dict[str, object]] | None = None
    body, status = _cached_request(client, list_url, "greenhouse", board_token, cache=cache, stats=stats)
    stats.cache = "hit" if status == "unchanged" else status
    if status == "unchanged":
        return BoardFetchResult((), stats)
    payload = _decode_json(body, "greenhouse", board_token)
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        _redacted_fail("bad_json", "greenhouse", board_token)
    rows: list[PostingRow] = []
    for job in payload["jobs"]:  # type: ignore[index]
        if type(job) is not dict:
            continue
        stats.listed += 1
        title = job.get("title")
        if type(title) is not str or not (title_filter(title) if title_filter is not None else matches_roles(title, config.roles)):
            stats.prefiltered_out += 1
            continue
        absolute_url = job.get("absolute_url")
        if type(absolute_url) is not str or not absolute_url:
            continue
        inline = job.get("content")
        content: str | None = inline if type(inline) is str else None
        detail: dict[str, object] | None = None
        job_id = job.get("id")
        if isinstance(job_id, (int, str)) and not isinstance(job_id, bool) and str(job_id):
            detail_url = _GREENHOUSE_JOB_URL.format(token=board_token, job_id=job_id)
            updated_at = job.get("updated_at")
            marker = updated_at if type(updated_at) is str and updated_at else None
            before = stats.requests
            have = None
            if descriptions and cache is not None:
                have = cache.lookup("greenhouse", detail_url)
                if have is not None:
                    pass  # updated_at-only change: the cached description stands, no request
                else:
                    if known is None:
                        known = cache.filled_jobs(board_token)
                    filled_job = known.get(str(job_id))
                    if filled_job is not None and type(filled_job.get("content")) is str:
                        stats.detail_cached += 1
                        rows.append(_greenhouse_row(job, title, absolute_url, filled_job["content"], board_token, filled_job))  # type: ignore[arg-type]
                        continue
            try:
                if have is not None:
                    detail_body = have.body
                else:
                    detail_body, _detail_status = _cached_request(
                        client, detail_url, "greenhouse", board_token, cache=cache, stats=stats, marker=marker
                    )
                detail = json.loads(detail_body.decode("utf-8"))
                if type(detail) is not dict or type(detail.get("content")) is not str:
                    detail = None
                    raise ValueError("greenhouse detail has no content")
                content = detail["content"]
                if stats.requests == before:
                    stats.detail_cached += 1
                else:
                    stats.detail_fetched += 1
            except (ATSBoardClientError, ValueError, UnicodeDecodeError):
                stats.detail_failed += 1
        rows.append(_greenhouse_row(job, title, absolute_url, content, board_token, detail))
    return BoardFetchResult(tuple(rows), stats)


def fetch_lever_board(
    client: "httpx.Client",
    board_token: str,
    config: FindJobsConfig,
    *,
    cache: BoardCache | None = None,
    stats: BoardFetchStats | None = None,
) -> BoardFetchResult:
    stats = stats if stats is not None else BoardFetchStats()
    body, status = _cached_request(client, _LEVER_URL.format(token=board_token), "lever", board_token, cache=cache, stats=stats)
    stats.cache = "hit" if status == "unchanged" else status
    if status == "unchanged":
        return BoardFetchResult((), stats)
    payload = _decode_json(body, "lever", board_token)
    if type(payload) is not list:
        _redacted_fail("bad_json", "lever", board_token)
    return BoardFetchResult(_lever_rows(payload, board_token, config, stats), stats)  # type: ignore[arg-type]


def fetch_ashby_board(
    client: "httpx.Client",
    board_token: str,
    config: FindJobsConfig,
    *,
    cache: BoardCache | None = None,
    stats: BoardFetchStats | None = None,
) -> BoardFetchResult:
    stats = stats if stats is not None else BoardFetchStats()
    body, status = _cached_request(client, _ASHBY_URL.format(token=board_token), "ashby", board_token, cache=cache, stats=stats)
    stats.cache = "hit" if status == "unchanged" else status
    if status == "unchanged":
        return BoardFetchResult((), stats)
    payload = _decode_json(body, "ashby", board_token)
    if type(payload) is not dict or type(payload.get("jobs")) is not list:
        _redacted_fail("bad_json", "ashby", board_token)
    return BoardFetchResult(_ashby_rows(payload["jobs"], board_token, config, stats), stats)  # type: ignore[index]


_FETCHERS = {
    "greenhouse": fetch_greenhouse_board,
    "lever": fetch_lever_board,
    "ashby": fetch_ashby_board,
}


class ATSBoardClients:
    """Concrete ``ATSBoardClient`` implementing all three public providers."""

    def list_board(self, client: "httpx.Client", provider: str, board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]:
        lister = _LISTERS.get(provider)
        if lister is None:
            raise ATSBoardClientError("unsupported_provider", f"unsupported ATS provider {provider!r}")
        return lister(client, board_token, config)

    def fetch_board(
        self,
        client: "httpx.Client",
        provider: str,
        board_token: str,
        config: FindJobsConfig,
        *,
        cache: BoardCache | None = None,
        descriptions: bool = False,
        title_filter: "Callable[[str], bool] | None" = None,
    ) -> BoardFetchResult:
        """Q2: the cached, prefiltered path acquire uses (see module docstring).

        ``descriptions`` (the sources update only) turns on Greenhouse's
        one-time description fill; Lever and Ashby already list descriptions.
        ``title_filter`` (the sources update only, 0110-8-03): which Greenhouse titles get a description request.
        """

        fetcher = _FETCHERS.get(provider)
        if fetcher is None:
            raise ATSBoardClientError("unsupported_provider", f"unsupported ATS provider {provider!r}")
        if descriptions and provider == "greenhouse":
            return fetch_greenhouse_board(client, board_token, config, cache=cache, descriptions=True, title_filter=title_filter)
        return fetcher(client, board_token, config, cache=cache)


__all__ = [
    "ATSBoardClientError",
    "ATSBoardClients",
    "BoardCache",
    "BoardFetchIndex",
    "BoardFetchResult",
    "BoardFetchStats",
    "CachedResponse",
    "LAST_FETCHED_SCHEMA",
    "PUBLISHED_FIELDS",
    "ashby_pay",
    "ashby_work_mode",
    "fetch_ashby_board",
    "fetch_greenhouse_board",
    "fetch_lever_board",
    "greenhouse_pay",
    "html_to_text",
    "lever_pay",
    "lever_work_mode",
    "list_ashby_board",
    "list_greenhouse_board",
    "list_lever_board",
    "MATCH_ANY_TITLE_ROLE",
    "TITLE_DENY_WORDS",
    "matches_roles",
    "role_fit",
    "posting_content_digest",
    "parse_board_url",
    "work_mode_from_label",
]
