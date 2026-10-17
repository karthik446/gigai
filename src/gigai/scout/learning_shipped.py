"""0.1.11.10 shipped lessons, slice 1: the SHIPPED form of a course, its export and the scan that guards it.

A shipped course is GENERIC course data. The full folder (``course.json``, ``catalog.json``, ``paths/``) is what the
export writes for the public learning center; only ``catalog.json`` travels inside GigAI as package data
(``gigai/scout/data/learning/shipped/<role-key>/``). The folder holds the concept vocabulary and the model-written
lesson and practice text, and nothing else: nothing of a resume, no posting sentence, no company, no count. Everything
about one user (known/some/new, percentages) is built locally from that user's own master and stored postings.

THE FOLDER, schema ``scout-learning-shipped-course:1`` (every file is minified JSON with sorted keys, UTF-8):

``course.json``::

    {"schema_version": "scout-learning-shipped-course:1", "role_key": "<kebab-case>", "role": "<role title>",
     "expectations": {"responsibilities": [{"id", "display"}], "scope_at_staff": [{"id", "display"}]},
     "technologies": {"<group name>": [{"id", "display"}]},
     "modules": [{"id", "title", "intro", "concepts": [<lesson>]}],
     "tool_links": {"<name>": "<url>"}, "dropped": [{"what", "reason"}]}

    <lesson>: {"id", "title", "summary", "detail", "in_practice", "technologies", "technology_links", "diagram",
               "code", "sources", "related", "concept_keys": ["<vocabulary id>"],
               "subtopics": [{"id", "title", "body"}]}

  Against a generated ``course.json`` (``scout-learning-course:1``) the export takes out: ``corpus``, the lesson
  counts and ``status``/``error``; of an expectation or technology item ``percent``, ``in_90d``, ``any_date`` and
  ``examples`` (posting sentences with their company); of a lesson ``marker``, ``postings_in_90d``,
  ``postings_any_date`` and ``source`` (which says whether stored postings named it); of a subtopic ``marker``,
  ``evidence`` and ``missing``. It adds ``role_key`` and, per lesson, ``concept_keys`` (the curriculum's concept
  ids of the lesson). A key this module does not know is REFUSED, never passed through: a new field has to be
  judged before it ships.

``catalog.json`` (schema ``scout-learning-shipped-catalog:2``), all the local pathway page needs of the course. THE
CATALOG IS WHAT GIGAI SHIPS: the lessons live on the public learning center (``site``) and the page links to them::

    {"schema_version", "role_key", "role", "titles": {"phrases": [...], "deny": [...]},
     "vocabulary": [{"id", "display", "category", "phrases", "technical"}],
     "expectations": {"responsibilities": ["<id>"], "scope_at_staff": ["<id>"]},
     "technologies": {"<group name>": ["<id>"]},
     "modules": [{"id", "title"}],
     "lessons": [{"id", "title", "module", "concept_keys", "technologies", "has_practice",
                  "prerequisites": [], "depth": null, "library_id"?,
                  "subtopics": [{"id", "title"}]}],
     "site": {"base_url", "site_version", "lessons_path", "practice_path", "pathways_path"},
     "pathway_version", "concept_keys_version"}

  ``library_id`` (optional, default the lesson's ``id``) is the lesson's id on the site. A reader accepts
  ``scout-learning-shipped-catalog:1`` too (no ``site`` block: nothing is linked and the page says so).
  ``course.json`` and ``paths/`` are written by the export for the SITE (``--catalog-only`` writes the catalog
  alone, the only file in the wheel) and are never read by GigAI.

``paths/<lesson id>.json``: the practice page as generated. It has a ``kind``: ``tool`` (a path through one tool's
documentation, the default: one written before ``kind`` existed is exported with ``kind: "tool"``), ``case`` (scenario,
constraints, deliverable, reference answer, rubric, common mistakes) or ``artifact`` (artifact name, template, a
model-written reference example, review checklist). A case or artifact page cites nothing and carries no sentence
written from the counts: it is exported unchanged (its keys checked like any other), the catalog's ``has_practice``
is true for it, and the scan reads every string of it. A tool path is exported minus the sentences code wrote from the counts: the
opening of ``why_this_tool`` ("N of M postings ... name <tool>." or "<tool> is named in this lesson; it is not
among the tools counted in your stored postings.") and the "Named in your postings ...: ..." tail of
``alternatives``.

THE SCAN (:func:`scan_shipped`) is a hard gate: :func:`export_shipped` writes to a temporary folder, scans it and
only then moves it into place; on any hit it raises :class:`ShippedScanError` and nothing final is written. A
shipped string (every JSON string value and every key) is a hit when it holds:

- ``master_line``: a whole master line (of at least :data:`MASTER_LINE_MIN_WORDS` words);
- ``master_shingle``: any :data:`SHINGLE_WORDS` consecutive words of a master line;
- ``master_id``: an id of the master (items, entries, what a line is ``backed`` by) or another id handed in;
- ``id_shape``: any token shaped like a master id (:data:`ID_SHAPE`), whatever master was handed in;
- ``master_name``: an employer, school or project name of the master (:func:`master_names`);
- ``posting_shingle``: any :data:`SHINGLE_WORDS` consecutive words of a stored posting sentence (a sentence of
  :data:`SHORT_SENTENCE_MIN_WORDS` words up to that size is compared whole);
- ``posting_company``: the company of a stored posting sentence, in prose (see the two rules below);
- ``posting_count``: a count of postings in words (:data:`POSTING_COUNT`): a number or a number word within a few
  words of "postings", "job ads" or "listings", or one of the generator's own count sentences.

Texts are compared as words: NFKC, case folded, split on anything that is not a letter or a digit. A name that
stands, as whole words, in a vocabulary display or phrase or in a technology name of the course is a term the
course teaches ("AWS", "Snowflake") and is not a name hit; each one is counted in ``Forbidden.counts`` under
``names_that_are_course_terms`` and listed in ``Forbidden.exempt`` by its source, so the exemption is visible.

Two more rules hold for a posting's COMPANY only, never for a line, a shingle, an id or anything of the master:

- it is not searched in a URL field (:func:`is_url_field`: the string under a ``url`` or ``href`` key, or a value
  of ``tool_links`` / ``technology_links``, when the whole string is one http(s) address). Those are public
  documentation addresses the generator verified, and a company named like an ordinary word ("tabs") stands in
  their paths;
- a REVIEWED allow list (``allow_names``, the developer tool's ``--allow-name``): a company whose whole name is, as
  words and whatever the case, an allowed name is not a hit. Each allowed name and every prose place it stands in
  is reported (:class:`Allowed`), so a reader can review what the course says there.

A hit names the file, the field and the SOURCE of what was found (``master line 12``, ``counts.json
concepts[3].examples[1].phrase words 4-11``), never the text.

The posting side of the scan is read from the work folder itself (:data:`POSTING_FILES`): the example sentences
``learning_corpus.count_concepts`` stored (``phrase`` / ``text`` beside a ``company``). It cannot be left out.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
import zlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .learning_store import LearningError

SCHEMA_VERSION = "scout-learning-shipped-course:1"
CATALOG_SCHEMA_VERSION = "scout-learning-shipped-catalog:2"
#: The catalog a reader still accepts: no ``site`` block, so nothing is linked.
CATALOG_SCHEMA_V1 = "scout-learning-shipped-catalog:1"
CATALOG_SCHEMAS = (CATALOG_SCHEMA_V1, CATALOG_SCHEMA_VERSION)
#: What an export of the shipped catalog says about the public learning center (the site repo's ``content/site.json``).
SITE_BASE_URL = "https://scout.edgelab.one/learn"
SITE_VERSION = "2026.10.1"
SITE_LESSONS_PATH, SITE_PRACTICE_PATH, SITE_PATHWAYS_PATH = "lessons", "practice", "pathways"
PATHWAY_VERSION = 1
CONCEPT_KEYS_VERSION = 1
SOURCE_SCHEMA_VERSION = "scout-learning-course:1"

COURSE_FILENAME = "course.json"
CATALOG_FILENAME = "catalog.json"
PATHS_DIRNAME = "paths"

#: How many consecutive words of a master line or a posting sentence make a hit.
SHINGLE_WORDS = 8
#: A posting sentence shorter than a shingle is compared whole from this many words on; a shorter one is not searched.
SHORT_SENTENCE_MIN_WORDS = 5
#: A master line is compared whole from this many words on (a one-word line would match ordinary text).
MASTER_LINE_MIN_WORDS = 3
#: A master name shorter than this is not searched.
MASTER_NAME_MIN_CHARS = 2
#: What ends the name in an entry heading: ``Acme Corp | Remote``, ``Acme Corp - Berlin``, ``Acme Corp (acquired)``.
MASTER_NAME_SEPARATORS = re.compile(r"\s+[|·–—-]\s+|[|·(]")
#: Legal-form words a name is also searched without: ``Acme Robotics L.L.C., Inc.`` is searched as ``acme robotics`` too.
MASTER_NAME_SUFFIXES = frozenset({"inc", "incorporated", "llc", "l l c", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "plc", "pvt", "pte", "ag", "sa", "bv"})
#: A master item or entry id: ``b-1a2b3c``, ``sum-1a2b3c``, ``r-1a2b3c``.
ID_SHAPE = re.compile(r"(?<![a-z0-9-])(?:sum|[a-z]{1,2})-[0-9a-f]{6}(?![a-z0-9])")
_NUMBER = (
    r"(?:\d+(?:[.,]\d+)*%?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|dozens?|half|thirds?|quarters?|fifths?|tenths?)"
)
#: How many words may stand between the number and the postings: "12 of 171 postings", "a third of the postings".
POSTING_COUNT_GAP_WORDS = 3
_GAP = rf"(?:[\w'%-]+,?\s+){{0,{POSTING_COUNT_GAP_WORDS}}}?"
#: A count of POSTINGS in words: a number or a number word before "posting(s)", "job ad(s)" or "listing(s)" with at
#: most :data:`POSTING_COUNT_GAP_WORDS` words between and no sentence end; the same after "postings", "job
#: posting(s)", "job ad(s)" or "job listing(s)" ("postings in the last 90 days"); and the generator's own count
#: sentences ("Named in your postings ...", "... your stored postings"). A number that counts something else is
#: not one, nor is "named in your interview notes".
POSTING_COUNT = re.compile(
    rf"(?i)(?<![\w.,]){_NUMBER}(?![\w])\s+{_GAP}(?:(?:job\s+)?postings?|job\s+ads?|(?:job\s+)?listings?)\b"
    rf"|\b(?:(?:job\s+)?postings|job\s+posting|job\s+ads?|job\s+listings?)\b[\s:(,-]+{_GAP}{_NUMBER}(?![\w])"
    rf"|\bnamed\s+in\s+your\s+{_GAP}postings\b|\byour\s+stored\s+postings\b"
)
#: The keys whose string is an address, and the objects whose values are (name -> address).
URL_KEYS = frozenset({"url", "href"})
URL_MAPS = frozenset({"tool_links", "technology_links"})
#: A string that is one http(s) address and nothing else.
URL = re.compile(r"https?://\S+")
#: The work-folder files whose posting sentences and companies the scan compares against.
POSTING_FILES = ("corpus.json", "counts.json", "course.json")
ROLE_KEY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ROLE_KEY_CHARS_MAX = 64

_WINDOWS = r"(?:postings in the last \d+ days|stored postings)"
_WORD = re.compile(r"[^\W_]+")

_COURSE_KEPT = ("role", "expectations", "technologies", "modules", "tool_links", "dropped")
_COURSE_REMOVED = ("schema_version", "corpus", "planned_lessons", "kept_lessons", "status", "error")
_FRONT_KEPT = ("id", "display")
_FRONT_REMOVED = ("percent", "in_90d", "any_date", "examples")
_MODULE_KEPT = ("id", "title", "intro", "concepts")
_LESSON_KEPT = ("id", "title", "summary", "detail", "in_practice", "technologies", "technology_links", "diagram", "code", "sources", "related", "subtopics")
_LESSON_REMOVED = ("marker", "postings_in_90d", "postings_any_date", "source")
_SUBTOPIC_KEPT = ("id", "title", "body")
_SUBTOPIC_REMOVED = ("marker", "evidence", "missing")
_PATH_KEYS = ("concept", "kind", "tool", "alternatives", "why_this_tool", "setup", "steps", "sources")
#: A case exercise and an artifact template (``learning_case``): written practice, no tool and no citation, and no
#: sentence code wrote from the posting counts, so the export carries them as they are.
_CASE_PATH_KEYS = ("concept", "kind", "scenario", "constraints", "deliverable", "reference_answer", "rubric", "common_mistakes")
_ARTIFACT_PATH_KEYS = ("concept", "kind", "artifact_name", "template", "reference_example", "review_checklist")
_CONCEPT_KEYS = ("id", "display", "category", "phrases", "technical")
#: Keys that may stand nowhere in a shipped folder, at any depth.
FORBIDDEN_KEYS = frozenset({
    "marker", "evidence", "missing", "corpus", "percent", "in_90d", "any_date", "examples", "company", "phrase",
    "postings_in_90d", "postings_any_date", "postings_90d", "count_in90d", "count_all", "percent_in90d", "percent_all", "n_in90d", "n_all",
})

KIND_MASTER_LINE, KIND_MASTER_SHINGLE, KIND_MASTER_ID, KIND_MASTER_NAME = "master_line", "master_shingle", "master_id", "master_name"
KIND_ID_SHAPE, KIND_POSTING_SHINGLE, KIND_POSTING_COMPANY, KIND_POSTING_COUNT = "id_shape", "posting_shingle", "posting_company", "posting_count"
_NAME_KINDS = frozenset({KIND_MASTER_NAME, KIND_POSTING_COMPANY})
HITS_SHOWN = 20


_SITE_SEGMENT = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SITE_URL = re.compile(r"https://[a-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~-]+)*")
_SITE_VERSION_CHARS_MAX = 40


@dataclass(frozen=True)
class Site:
    """Where the public learning center is, as a catalog says (``catalog["site"]``): the one place a link may point to.

    Every link is built here: the stable, unversioned URLs ``<base>/lessons/<lesson>/#<subtopic>``,
    ``<base>/practice/<lesson>/`` and ``<base>/pathways/<role-key>/``. No query string, nothing of the user.
    """

    base_url: str
    site_version: str
    lessons_path: str = SITE_LESSONS_PATH
    practice_path: str = SITE_PRACTICE_PATH
    pathways_path: str = SITE_PATHWAYS_PATH

    @property
    def host(self) -> str:
        return self.base_url.split("/", 3)[2]

    def home_url(self) -> str:
        return f"{self.base_url}/"

    def lesson_url(self, library_id: str, anchor: str | None = None) -> str:
        found = f"{self.base_url}/{self.lessons_path}/{_quote(library_id)}/"
        return f"{found}#{_quote(anchor)}" if anchor else found

    def practice_url(self, library_id: str) -> str:
        return f"{self.base_url}/{self.practice_path}/{_quote(library_id)}/"

    def pathway_url(self, role_key: str) -> str:
        return f"{self.base_url}/{self.pathways_path}/{_quote(role_key)}/"

    def owns(self, url: str) -> bool:
        """Whether ``url`` is an address on the site's base (the only external link a local page may hold)."""

        return url == self.base_url or url.startswith(self.base_url + "/")


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(str(text), safe="-_.~")


def catalog_site(catalog: object) -> Site | None:
    """The :class:`Site` of a catalog, or ``None`` for a ``:1`` catalog or a ``site`` block that is not valid.

    A valid block has an ``https`` ``base_url`` of plain path segments (no query, no fragment, no credentials, no
    trailing slash needed) and a ``site_version`` text; the three paths are plain words. With none, nothing links.
    """

    if not isinstance(catalog, Mapping) or catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        return None
    found = catalog.get("site")
    if not isinstance(found, Mapping):
        return None
    base, version = found.get("base_url"), found.get("site_version")
    if not isinstance(base, str) or not isinstance(version, str) or not version.strip() or len(version) > _SITE_VERSION_CHARS_MAX:
        return None
    base = base.rstrip("/")
    if not _SITE_URL.fullmatch(base):
        return None
    paths = [found.get(name, default) for name, default in (("lessons_path", SITE_LESSONS_PATH), ("practice_path", SITE_PRACTICE_PATH), ("pathways_path", SITE_PATHWAYS_PATH))]
    if not all(isinstance(path, str) and _SITE_SEGMENT.fullmatch(path) for path in paths):
        return None
    return Site(base, version.strip(), *paths)


def library_id(lesson: Mapping[str, Any]) -> str:
    """The id a lesson has on the site: its ``library_id`` when it has one, else its own ``id``."""

    found = lesson.get("library_id")
    return found if isinstance(found, str) and found else str(lesson.get("id", ""))


class ShippedExportError(LearningError):
    """The export was refused before the scan: an input is missing, unfinished or holds something unknown."""


@dataclass(frozen=True)
class Hit:
    """One thing the scan found: where it is and what it came from, never the text itself."""

    kind: str
    file: str
    field: str
    source: str

    def line(self) -> str:
        return f"{self.kind}: {self.file} {self.field} holds {self.source}"


@dataclass(frozen=True)
class Allowed:
    """What the reviewed allow list let through, for a reader to review.

    ``names`` are ``(name, source)``: an allowed name that is the company of a stored posting, and where that
    company is stored. ``places`` are ``(name, file, field)``: every prose string of the shipped folder the name
    stands in. A name of the list that is no stored company (or is a course term or a name of the master, which
    the list never touches) is in neither.
    """

    names: tuple[tuple[str, str], ...] = ()
    places: tuple[tuple[str, str, str], ...] = ()


class ShippedScanError(LearningError):
    """The scan found something that may not ship; ``hits`` says where and from which source."""

    def __init__(self, hits: Sequence[Hit], allowed: Allowed | None = None) -> None:
        self.hits = tuple(hits)
        self.allowed = allowed or Allowed()
        shown = "; ".join(hit.line() for hit in self.hits[:HITS_SHOWN])
        more = f"; and {len(self.hits) - HITS_SHOWN} more" if len(self.hits) > HITS_SHOWN else ""
        super().__init__("shipped_scan_failed", f"the shipped course was refused, {len(self.hits)} hit(s): {shown}{more}")


# ---------------------------------------------------------------------------
# What may not ship: the master's side and the postings' side
# ---------------------------------------------------------------------------


def words(text: str) -> tuple[str, ...]:
    """``text`` as the scan compares it: NFKC, case folded, the runs of letters and digits."""

    return tuple(_WORD.findall(unicodedata.normalize("NFKC", str(text)).casefold()))


def shingles(text: str, size: int = SHINGLE_WORDS) -> list[tuple[int, tuple[str, ...]]]:
    """Every ``size`` consecutive words of ``text`` with the index of the first; none when it is shorter."""

    found = words(text)
    return [(start, found[start:start + size]) for start in range(len(found) - size + 1)]


def master_names(headings: Iterable[str]) -> list[str]:
    """The employer, school and project names of entry headings, as searched.

    Per heading: the whole heading, what stands before the first of :data:`MASTER_NAME_SEPARATORS`, and each of
    the two without trailing :data:`MASTER_NAME_SUFFIXES`. Each once, in the headings' order; a name under
    :data:`MASTER_NAME_MIN_CHARS` characters is left out.
    """

    names: list[str] = []
    for heading in headings:
        whole = str(heading).strip()
        for candidate in (whole, MASTER_NAME_SEPARATORS.split(whole, maxsplit=1)[0]):
            found = list(words(candidate))
            forms = [found]
            trimmed = list(found)
            while trimmed and (trimmed[-1] in MASTER_NAME_SUFFIXES or " ".join(trimmed[-3:]) in MASTER_NAME_SUFFIXES):
                trimmed = trimmed[:-3] if " ".join(trimmed[-3:]) in MASTER_NAME_SUFFIXES else trimmed[:-1]
            forms.append(trimmed)
            for form in forms:
                name = " ".join(form)
                if len(name) >= MASTER_NAME_MIN_CHARS and name not in names:
                    names.append(name)
    return names


@dataclass(frozen=True)
class MasterParts:
    """What of a master the scan searches for: its lines, its ids and its names."""

    lines: tuple[str, ...] = ()
    ids: tuple[str, ...] = ()
    names: tuple[str, ...] = ()


def master_parts(master: Any, *, extra_ids: Iterable[str] = ()) -> MasterParts:
    """The lines, ids and names of a parsed master (``master_resume.Master``); ``extra_ids`` are story ids and the like."""

    items = list(master.items.values())
    entries = list(master.entries.values())
    ids = [item.id for item in items] + [entry.id for entry in entries]
    ids += [ref for item in items for ref in item.backed]
    ids += [str(extra) for extra in extra_ids]
    return MasterParts(
        lines=tuple(item.text for item in items),
        ids=tuple(dict.fromkeys(found for found in ids if found and found.strip())),
        names=tuple(master_names(entry.heading for entry in entries)),
    )


def _walk(value: object, at: str = "") -> Iterator[tuple[str, object]]:
    """Every dict of a JSON tree with its place."""

    if isinstance(value, Mapping):
        yield at, value
        for key, inner in value.items():
            yield from _walk(inner, f"{at}.{key}" if at else str(key))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _walk(inner, f"{at}[{index}]")


def posting_parts(work: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """``(sentences, companies)`` the run of ``work`` stored, each as ``(source, text)``.

    A stored posting sentence is the ``phrase`` or ``text`` of any object that also names a ``company``
    (``learning_corpus.count_concepts`` examples in ``corpus.json`` and ``counts.json``, the expectation examples
    of ``course.json``). ``no_posting_text`` when neither ``corpus.json`` nor ``counts.json`` is there: without
    them the scan would compare against nothing.
    """

    if not any((work / name).is_file() for name in POSTING_FILES[:2]):
        raise ShippedExportError("no_posting_text", f"{work} holds neither corpus.json nor counts.json: the scan has no posting text to compare against")
    sentences: list[tuple[str, str]] = []
    companies: list[tuple[str, str]] = []
    for name in POSTING_FILES:
        if not (work / name).is_file():
            continue
        for at, found in _walk(_read(work / name)):
            if not isinstance(found, Mapping) or not isinstance(found.get("company"), str):
                continue
            companies.append((f"{name} {at}.company", found["company"]))
            for key in ("phrase", "text"):
                if isinstance(found.get(key), str):
                    sentences.append((f"{name} {at}.{key}", found[key]))
    return sentences, companies


@dataclass(frozen=True)
class Forbidden:
    """What may not stand in a shipped folder. Built by :func:`forbidden`; ``counts`` says how much of each kind."""

    #: word count -> words -> (kind, source)
    phrases: Mapping[int, Mapping[tuple[str, ...], tuple[str, str]]] = field(default_factory=dict)
    #: id (case folded) -> source
    ids: Mapping[str, str] = field(default_factory=dict)
    counts: Mapping[str, int] = field(default_factory=dict)
    #: The sources of the names left out because the course teaches them as a term.
    exempt: tuple[str, ...] = ()
    #: words -> (name as allowed, source): the posting companies the reviewed allow list lets through.
    allowed: Mapping[tuple[str, ...], tuple[str, str]] = field(default_factory=dict)


def course_terms(vocabulary: Iterable[Mapping[str, Any]] = (), technologies: Iterable[str] = ()) -> set[tuple[str, ...]]:
    """Every run of whole words of a vocabulary display or phrase and of a technology name: the terms a course teaches."""

    terms: set[tuple[str, ...]] = set()
    texts = [str(name) for name in technologies]
    for concept in vocabulary:
        texts.append(str(concept.get("display", "")))
        texts.extend(str(phrase) for phrase in concept.get("phrases", []))
    for text in texts:
        found = words(text)
        for size in range(1, len(found) + 1):
            for start in range(len(found) - size + 1):
                terms.add(found[start:start + size])
    return terms


def forbidden(
    *,
    master: MasterParts | None = None,
    posting_sentences: Sequence[tuple[str, str]] = (),
    posting_companies: Sequence[tuple[str, str]] = (),
    terms: set[tuple[str, ...]] | frozenset[tuple[str, ...]] = frozenset(),
    allow_names: Iterable[str] = (),
) -> Forbidden:
    """The forbidden set of a master (or none) and stored posting sentences and companies, each ``(source, text)``.

    ``terms`` (:func:`course_terms`) are the terms the course teaches: a master NAME or a posting COMPANY that
    is one of them is not searched, and is listed in ``exempt``. ``allow_names`` is the reviewed allow list: a
    posting COMPANY whose whole name is one of them (as words, whatever the case) is not searched, and is listed
    in ``allowed`` and counted under ``names_allowed``. Lines, shingles, ids and the master's names know no
    exemption: a name of the master, a master line or a posting sentence with the very words of an allowed name
    is searched all the same.
    """

    phrases: dict[int, dict[tuple[str, ...], tuple[str, str]]] = {}
    counts: dict[str, int] = {}
    exempt: list[str] = []
    allow = {words(name): str(name) for name in allow_names if words(name)}
    allowed: dict[tuple[str, ...], tuple[str, str]] = {}

    def add(kind: str, found: tuple[str, ...], source: str) -> None:
        if not found:
            return
        if kind in _NAME_KINDS and found in terms:
            if found not in phrases.get(len(found), {}):
                exempt.append(source)
                counts["names_that_are_course_terms"] = counts.get("names_that_are_course_terms", 0) + 1
                phrases.setdefault(len(found), {})[found] = ("", source)
            return
        if kind == KIND_POSTING_COMPANY and found in allow and found not in phrases.get(len(found), {}):
            if found not in allowed:
                allowed[found] = (allow[found], source)
                counts["names_allowed"] = counts.get("names_allowed", 0) + 1
            return
        if found not in phrases.setdefault(len(found), {}):
            phrases[len(found)][found] = (kind, source)
            counts[kind] = counts.get(kind, 0) + 1

    short: dict[str, set[tuple[str, ...]]] = {"master_lines_too_short": set(), "posting_sentences_too_short": set()}
    ids: dict[str, str] = {}
    master = master or MasterParts()
    # Names go in first: a name that is a course term keeps its exemption whatever else has the same words.
    for index, name in enumerate(master.names):
        add(KIND_MASTER_NAME, words(name), f"master name {index}")
    for source, company in posting_companies:
        found = words(company)
        if len(" ".join(found)) >= MASTER_NAME_MIN_CHARS:
            add(KIND_POSTING_COMPANY, found, source)
    for index, line in enumerate(master.lines):
        found = words(line)
        if len(found) >= MASTER_LINE_MIN_WORDS:
            add(KIND_MASTER_LINE, found, f"master line {index}")
        else:
            short["master_lines_too_short"].add(found)
        for start, shingle in shingles(line):
            add(KIND_MASTER_SHINGLE, shingle, f"master line {index} words {start}-{start + SHINGLE_WORDS - 1}")
    for index, found_id in enumerate(master.ids):
        if found_id.casefold() not in ids:
            ids[found_id.casefold()] = f"master id {index}"
    counts[KIND_MASTER_ID] = len(ids)
    for source, sentence in posting_sentences:
        found = words(sentence)
        if len(found) < SHORT_SENTENCE_MIN_WORDS:
            short["posting_sentences_too_short"].add(found)
        elif len(found) < SHINGLE_WORDS:
            add(KIND_POSTING_SHINGLE, found, f"{source} (whole)")
        for start, shingle in shingles(sentence):
            add(KIND_POSTING_SHINGLE, shingle, f"{source} words {start}-{start + SHINGLE_WORDS - 1}")
    counts.update({what: len(group) for what, group in short.items() if group})
    # A master line or a posting sentence with the very words of an allowed name is searched: nothing was allowed.
    allowed = {found: value for found, value in allowed.items() if found not in phrases.get(len(found), {})}
    counts.pop("names_allowed", None)
    if allowed:
        counts["names_allowed"] = len(allowed)
    # An exempt name stays out of the search.
    kept = {size: {found: value for found, value in group.items() if value[0]} for size, group in phrases.items()}
    return Forbidden(phrases={size: group for size, group in kept.items() if group}, ids=ids, counts=dict(sorted(counts.items())), exempt=tuple(exempt), allowed=allowed)


def build_forbidden(work: Path, master: MasterParts | None = None, *, allow_names: Iterable[str] = ()) -> Forbidden:
    """The forbidden set of a work folder (its stored posting sentences and companies) and of a master, if any.

    The course terms that exempt a name are the work folder's own vocabulary and the technology names of its
    lessons; ``allow_names`` is the reviewed allow list of :func:`forbidden`.
    """

    work = Path(work)
    sentences, companies = posting_parts(work)
    vocabulary = _concepts(_read(work / "vocabulary.json")) if (work / "vocabulary.json").is_file() else []
    technologies: list[str] = []
    if (work / COURSE_FILENAME).is_file():
        for _at, found in _walk(_read(work / COURSE_FILENAME).get("modules", [])):
            if isinstance(found, Mapping) and isinstance(found.get("technologies"), list):
                technologies.extend(str(name) for name in found["technologies"])
    return forbidden(master=master, posting_sentences=sentences, posting_companies=companies, terms=course_terms(vocabulary, technologies), allow_names=allow_names)


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def is_url_field(key: str, parent: str, text: str) -> bool:
    """Whether the string ``text`` under ``key`` (of an object that itself stands under ``parent``) is a URL field.

    It is when the key is one of :data:`URL_KEYS` or the object is one of :data:`URL_MAPS`, AND the whole string
    is one http(s) address: prose under such a key is prose.
    """

    return (key in URL_KEYS or parent in URL_MAPS) and URL.fullmatch(text) is not None


def _strings(value: object, at: str = "", key: str = "", parent: str = "") -> Iterator[tuple[str, str, bool]]:
    """Every string of a JSON tree, keys included, with its place and whether it is a URL field."""

    if isinstance(value, str):
        yield at, value, is_url_field(key, parent, value)
    elif isinstance(value, Mapping):
        for inner_key, inner in value.items():
            yield f"{at}{{key}}", str(inner_key), False
            yield from _strings(inner, f"{at}.{inner_key}" if at else str(inner_key), str(inner_key), key)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _strings(inner, f"{at}[{index}]")


def scan_text(text: str, banned: Forbidden, *, url: bool = False) -> list[tuple[str, str]]:
    """``(kind, source)`` of everything forbidden one string holds, each once.

    ``url`` says the string is a URL field (:func:`is_url_field`): a posting's company is not searched in it.
    Everything else is.
    """

    found: dict[tuple[str, str], None] = {}
    tokens = words(text)
    for size, group in banned.phrases.items():
        for start in range(len(tokens) - size + 1):
            match = group.get(tokens[start:start + size])
            if match is not None and not (url and match[0] == KIND_POSTING_COMPANY):
                found[match] = None
    low = unicodedata.normalize("NFKC", text).casefold()
    for found_id, source in banned.ids.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(found_id)}(?![a-z0-9])", low):
            found[(KIND_MASTER_ID, source)] = None
    if ID_SHAPE.search(low):
        found[(KIND_ID_SHAPE, "a token shaped like a master id")] = None
    if POSTING_COUNT.search(text):
        found[(KIND_POSTING_COUNT, "a count of postings in words")] = None
    return list(found)


def allowed_in(text: str, banned: Forbidden) -> list[str]:
    """The allowed names (``Forbidden.allowed``) one string holds as whole words, each once."""

    found: dict[str, None] = {}
    tokens = words(text)
    for size in {len(name) for name in banned.allowed}:
        for start in range(len(tokens) - size + 1):
            match = banned.allowed.get(tokens[start:start + size])
            if match is not None:
                found[match[0]] = None
    return list(found)


@dataclass(frozen=True)
class ScanReport:
    """What a clean scan read, and what the reviewed allow list let through."""

    files: int
    strings: int
    words: int
    allowed: Allowed = field(default_factory=Allowed)


def scan_shipped(folder: Path, banned: Forbidden) -> ScanReport:
    """Scan every string of every file of a shipped ``folder``; raise :class:`ShippedScanError` on ANY hit.

    A posting's company is not searched in a URL field, and one on the reviewed allow list (``banned.allowed``) is
    not a hit: the report, and the refusal too, name every prose place such a name stands in (:class:`Allowed`).
    The catalog's ``site.base_url`` is not scanned when it is the product's own :data:`SITE_BASE_URL`: it is the
    product's configuration, not course text, and a name the master shares with the site's domain would otherwise
    refuse every export (any other value of that field is scanned like every string).
    Every file must be JSON: another kind of file is refused (``ShippedExportError`` ``unexpected_file``), since
    nothing else belongs in a shipped course and it could not be read string by string.
    """

    folder = Path(folder)
    hits: list[Hit] = []
    places: list[tuple[str, str, str]] = []
    files = strings = total = 0
    for path in sorted(item for item in folder.rglob("*") if item.is_file()):
        name = path.relative_to(folder).as_posix()
        if path.suffix != ".json":
            raise ShippedExportError("unexpected_file", f"{name} is not a JSON file: a shipped course holds JSON only")
        files += 1
        for at, text, url in _strings(_read(path)):
            if name == "catalog.json" and at == "site.base_url" and text == SITE_BASE_URL:
                continue
            strings += 1
            total += len(words(text))
            hits.extend(Hit(kind, name, at, source) for kind, source in scan_text(text, banned, url=url))
            if banned.allowed and not url:
                places.extend((allowed_name, name, at) for allowed_name in allowed_in(text, banned))
    allowed = Allowed(names=tuple(banned.allowed.values()), places=tuple(places))
    if hits:
        raise ShippedScanError(hits, allowed)
    return ScanReport(files, strings, total, allowed)


# ---------------------------------------------------------------------------
# The shipped form
# ---------------------------------------------------------------------------


def _read(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ShippedExportError("unreadable_input", f"{Path(path).name} could not be read as JSON") from exc


def _dump(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _concepts(vocabulary: object) -> list[dict[str, Any]]:
    found = vocabulary.get("concepts") if isinstance(vocabulary, Mapping) else None
    if not isinstance(found, list) or not all(isinstance(concept, Mapping) and isinstance(concept.get("id"), str) for concept in found):
        raise ShippedExportError("invalid_input", "vocabulary.json holds no list of concepts")
    return [dict(concept) for concept in found]


def _only(found: Mapping[str, Any], kept: Sequence[str], removed: Sequence[str], what: str) -> dict[str, Any]:
    """``found`` with its ``kept`` keys; an unknown key is refused."""

    unknown = sorted(set(found) - set(kept) - set(removed))
    if unknown:
        raise ShippedExportError("unknown_field", f"{what} holds a field the shipped schema does not know: {', '.join(unknown)}")
    return {key: found[key] for key in kept if key in found}


def strip_path(path: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    """A practice path without the sentences code wrote from the posting counts, and how many were taken out.

    A case or artifact page (``kind``) has none to take out and is kept whole, its keys checked like a tool path's;
    a tool path with no ``kind`` is written with ``kind: "tool"``.
    """

    kind = path.get("kind")
    if kind in ("case", "artifact"):
        return _only(path, _CASE_PATH_KEYS if kind == "case" else _ARTIFACT_PATH_KEYS, (), f"the {kind} page of {path.get('concept', '?')}"), 0
    kept = _only(path, _PATH_KEYS, (), f"the path of {path.get('concept', '?')}")
    kept["kind"] = "tool"
    tool = re.escape(str(kept.get("tool", "")))
    why, counted = re.subn(rf"^\d+ of \d+ {_WINDOWS} name {tool}(?: \([^)]*\))?\.\s*", "", str(kept.get("why_this_tool", "")))
    why, uncounted = re.subn(rf"^{tool} is named in this lesson; it is not among the tools counted in your stored postings\.\s*", "", why)
    alternatives, others = re.subn(rf"(?:^|\s+)Named in your {_WINDOWS}: .*$", "", str(kept.get("alternatives", "")), flags=re.DOTALL)
    kept["why_this_tool"], kept["alternatives"] = why, alternatives
    return kept, counted + uncounted + others


@dataclass(frozen=True)
class Shipped:
    """The shipped form of one course, in memory: the three kinds of file and what the strip took out."""

    course: dict[str, Any]
    catalog: dict[str, Any]
    paths: dict[str, dict[str, Any]]
    removed: dict[str, int]

    def files(self, *, catalog_only: bool = False) -> dict[str, bytes]:
        """The folder's files by relative name, as written: minified, keys sorted. ``catalog_only``: just ``catalog.json``."""

        found = {COURSE_FILENAME: _dump(self.course), CATALOG_FILENAME: _dump(self.catalog)}
        if catalog_only:
            return {CATALOG_FILENAME: found[CATALOG_FILENAME]}
        for lesson_id in sorted(self.paths):
            found[f"{PATHS_DIRNAME}/{lesson_id}.json"] = _dump(self.paths[lesson_id])
        return found


def check_role_key(role_key: str) -> str:
    if not isinstance(role_key, str) or not ROLE_KEY.fullmatch(role_key) or len(role_key) > ROLE_KEY_CHARS_MAX:
        raise ShippedExportError("invalid_value", "a role key is lower-case words joined by dashes, at most 64 characters")
    return role_key


def shipped_course(
    course: Mapping[str, Any], paths: Mapping[str, Mapping[str, Any]], curriculum: Mapping[str, Any], vocabulary: Mapping[str, Any],
    titles: Mapping[str, Any], *, role_key: str, site_version: str = SITE_VERSION, base_url: str = SITE_BASE_URL,
    pathway_version: int = PATHWAY_VERSION, concept_keys_version: int = CONCEPT_KEYS_VERSION,
) -> Shipped:
    """The shipped form of a finished ``course`` and its ``paths`` (see the module's description of the schema).

    ``curriculum`` gives each lesson's concept keys, ``vocabulary`` and ``titles`` the catalog's counting inputs;
    ``base_url`` and ``site_version`` say where the public lessons are and which site version the catalog was made for.
    Refused (:class:`ShippedExportError`): a course that is not ``done``, a key the schema does not know, a
    concept key or front id the vocabulary does not hold, a path of a lesson the course does not hold.
    """

    check_role_key(role_key)
    if course.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ShippedExportError("invalid_input", f"course.json is not a {SOURCE_SCHEMA_VERSION}")
    if course.get("status") != "done":
        raise ShippedExportError("course_not_done", "only a finished course (status done) is exported")
    removed: dict[str, int] = {}

    def gone(what: str, count: int = 1) -> None:
        if count:
            removed[what] = removed.get(what, 0) + count

    concepts = _concepts(vocabulary)
    known = {concept["id"] for concept in concepts}
    keys: dict[str, list[str]] = {}
    for module in curriculum.get("modules", []) if isinstance(curriculum, Mapping) else []:
        for lesson in module.get("lessons", []):
            keys[str(lesson["id"])] = [str(key) for key in lesson.get("concepts", [])]

    top = _only(course, _COURSE_KEPT, _COURSE_REMOVED, "course.json")
    gone("corpus block", int("corpus" in course))

    def front(groups: object, what: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]]]:
        shown: dict[str, list[dict[str, Any]]] = {}
        for name, items in (groups if isinstance(groups, Mapping) else {}).items():
            shown[str(name)] = []
            for item in items:
                gone(f"{what} counts", int(any(key in item for key in ("percent", "in_90d", "any_date"))))
                gone(f"{what} posting sentences", len(item.get("examples") or []))
                kept = _only(item, _FRONT_KEPT, _FRONT_REMOVED, f"{what} item {item.get('id', '?')}")
                if kept.get("id") not in known:
                    raise ShippedExportError("unknown_concept", f"{what} item {kept.get('id', '?')} is not in the vocabulary")
                shown[str(name)].append(kept)
        return shown, {name: [str(item["id"]) for item in items] for name, items in shown.items()}

    expectations, expectation_ids = front(top.get("expectations"), "expectations")
    technologies, technology_ids = front(top.get("technologies"), "technologies")

    modules: list[dict[str, Any]] = []
    lessons: list[dict[str, Any]] = []
    lesson_ids: set[str] = set()
    for module in top.get("modules", []):
        shown_module = _only(module, _MODULE_KEPT, (), f"module {module.get('id', '?')}")
        shown_lessons = []
        for lesson in module.get("concepts", []):
            lesson_id = str(lesson.get("id", "?"))
            kept = _only(lesson, _LESSON_KEPT, _LESSON_REMOVED, f"lesson {lesson_id}")
            marker = lesson.get("marker") if isinstance(lesson.get("marker"), Mapping) else {}
            gone("lesson markers", int("marker" in lesson))
            gone("lesson evidence quotes", int(bool(marker.get("evidence"))))
            gone("lesson missing sentences", int(bool(marker.get("missing"))))
            gone("lesson posting counts", int("postings_in_90d" in lesson or "postings_any_date" in lesson))
            if lesson_id not in keys:
                raise ShippedExportError("unknown_lesson", f"lesson {lesson_id} is not in curriculum.json: its concept keys are unknown")
            unknown = [key for key in keys[lesson_id] if key not in known]
            if unknown:
                raise ShippedExportError("unknown_concept", f"lesson {lesson_id} names a concept the vocabulary does not hold: {', '.join(unknown)}")
            kept["concept_keys"] = list(keys[lesson_id])
            subtopics = []
            for sub in lesson.get("subtopics", []):
                gone("subtopic markers", int("marker" in sub))
                gone("subtopic evidence quotes", int(bool(sub.get("evidence"))))
                gone("subtopic missing sentences", int(bool(sub.get("missing"))))
                subtopics.append(_only(sub, _SUBTOPIC_KEPT, _SUBTOPIC_REMOVED, f"a subtopic of lesson {lesson_id}"))
            kept["subtopics"] = subtopics
            shown_lessons.append(kept)
            lesson_ids.add(lesson_id)
            lessons.append({
                "id": lesson_id, "title": kept.get("title", ""), "module": shown_module.get("id"), "concept_keys": list(keys[lesson_id]),
                "technologies": list(kept.get("technologies", [])), "has_practice": lesson_id in paths, "prerequisites": [], "depth": None,
                "subtopics": [{"id": sub.get("id"), "title": sub.get("title")} for sub in subtopics],
            })
        shown_module["concepts"] = shown_lessons
        modules.append(shown_module)

    stray = sorted(set(paths) - lesson_ids)
    if stray:
        raise ShippedExportError("unknown_lesson", f"a practice path names a lesson the course does not hold: {', '.join(stray)}")
    shown_paths: dict[str, dict[str, Any]] = {}
    for lesson_id in sorted(paths):
        shown_paths[lesson_id], sentences = strip_path(paths[lesson_id])
        gone("practice count sentences", sentences)

    phrases, deny = titles.get("phrases"), titles.get("deny")
    if not isinstance(phrases, list) or not phrases or not isinstance(deny, list):
        raise ShippedExportError("invalid_input", "titles.json holds no title phrases")
    shipped = {
        "schema_version": SCHEMA_VERSION, "role_key": role_key, "role": top.get("role", ""),
        "expectations": expectations, "technologies": technologies, "modules": modules,
        "tool_links": top.get("tool_links", {}), "dropped": top.get("dropped", []),
    }
    catalog = {
        "schema_version": CATALOG_SCHEMA_VERSION, "role_key": role_key, "role": top.get("role", ""),
        "titles": {"phrases": [str(phrase) for phrase in phrases], "deny": [str(word) for word in deny]},
        "vocabulary": [_only(concept, _CONCEPT_KEYS, (), f"vocabulary concept {concept['id']}") for concept in concepts],
        "expectations": expectation_ids, "technologies": technology_ids,
        "modules": [{"id": module.get("id"), "title": module.get("title")} for module in modules],
        "lessons": lessons,
        "site": {
            "base_url": base_url, "site_version": site_version, "lessons_path": SITE_LESSONS_PATH, "practice_path": SITE_PRACTICE_PATH,
            "pathways_path": SITE_PATHWAYS_PATH,
        },
        "pathway_version": pathway_version, "concept_keys_version": concept_keys_version,
    }
    result = Shipped(shipped, catalog, shown_paths, dict(sorted(removed.items())))
    problems = check_shipped(result)
    if problems:
        raise ShippedExportError("invalid_shipped", "; ".join(problems[:5]))
    return result


def check_shipped(shipped: Shipped) -> list[str]:
    """What is wrong with a shipped course's shape: a forbidden key at any depth, a wrong schema, a lesson without keys."""

    problems: list[str] = []
    if shipped.course.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"course.json is not a {SCHEMA_VERSION}")
    if shipped.catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        problems.append(f"catalog.json is not a {CATALOG_SCHEMA_VERSION}")
    elif catalog_site(shipped.catalog) is None:
        problems.append("catalog.json holds no valid site block")
    trees = {COURSE_FILENAME: shipped.course, CATALOG_FILENAME: shipped.catalog, **{f"{PATHS_DIRNAME}/{name}.json": path for name, path in shipped.paths.items()}}
    for name, tree in trees.items():
        for at, found in _walk(tree):
            banned = sorted(FORBIDDEN_KEYS & set(found)) if isinstance(found, Mapping) else []
            if banned:
                problems.append(f"{name} {at or 'top'} holds {', '.join(banned)}")
    for module in shipped.course.get("modules", []):
        for lesson in module.get("concepts", []):
            if not isinstance(lesson.get("concept_keys"), list):
                problems.append(f"lesson {lesson.get('id', '?')} has no concept_keys")
    return problems


def load_work(work: Path) -> dict[str, Any]:
    """The inputs of an export, read from a finished course's work folder (``learning_job`` wrote them).

    ``course.json``, ``curriculum.json``, ``vocabulary.json`` and ``titles.json`` must be there; ``paths/*.json``
    are the practice paths (none is fine); ``corpus-summary.json`` is read when present, only to check that the
    counting inputs belong to this course's role.
    """

    work = Path(work)
    missing = [name for name in (COURSE_FILENAME, "curriculum.json", "vocabulary.json", "titles.json") if not (work / name).is_file()]
    if missing:
        raise ShippedExportError("missing_input", f"the work folder holds no {', '.join(missing)}")
    found = {name.removesuffix(".json"): _read(work / name) for name in (COURSE_FILENAME, "curriculum.json", "vocabulary.json", "titles.json")}
    found["paths"] = {path.stem: _read(path) for path in sorted((work / PATHS_DIRNAME).glob("*.json"))} if (work / PATHS_DIRNAME).is_dir() else {}
    summary = _read(work / "corpus-summary.json") if (work / "corpus-summary.json").is_file() else None
    roles = {str(part.get("role_text")) for part in (found["titles"], found["vocabulary"], summary) if isinstance(part, Mapping) and part.get("role_text")}
    if len(roles) > 1:
        raise ShippedExportError("invalid_input", "titles.json, vocabulary.json and corpus-summary.json are not of one role")
    return found


def deflated(data: bytes) -> int:
    """The size of ``data`` as a wheel stores it: raw deflate at level 9."""

    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    return len(packer.compress(data) + packer.flush())


@dataclass(frozen=True)
class ExportReport:
    """What an export wrote: where, the files with their raw and deflated sizes, what the strip removed, what the scan read."""

    out: Path
    role_key: str
    lessons: int
    paths: int
    #: ``(relative name, raw bytes, deflated bytes)`` per file, by name.
    files: tuple[tuple[str, int, int], ...]
    removed: Mapping[str, int]
    forbidden: Mapping[str, int]
    exempt: tuple[str, ...]
    scan: ScanReport

    @property
    def raw_bytes(self) -> int:
        return sum(raw for _name, raw, _packed in self.files)

    @property
    def deflated_bytes(self) -> int:
        return sum(packed for _name, _raw, packed in self.files)


def export_shipped(
    work: Path, out: Path, *, role_key: str, master: MasterParts | None, allow_names: Iterable[str] = (), catalog_only: bool = False,
    site_version: str = SITE_VERSION, base_url: str = SITE_BASE_URL,
) -> ExportReport:
    """Write the shipped folder ``out`` of the finished course in ``work``; refuse it when the scan finds anything.

    ``master`` is what of a master the scan searches for (:func:`master_parts`), or ``None`` for no master.
    ``allow_names`` is the reviewed allow list of posting companies (:func:`forbidden`); what it let through is in
    the report's ``scan.allowed``, or in the refusal's ``allowed``. The
    postings' side of the scan always comes from ``work`` itself. The folder is written beside ``out`` under a
    temporary name, scanned there (:func:`scan_shipped`) and moved into place only when clean, so a refused
    export leaves ``out`` as it was. The same inputs give the same bytes. An existing ``out`` is replaced only
    when it is a shipped course folder (``out_not_shipped`` otherwise).

    The scan always reads the FULL folder (course, catalog and paths). ``catalog_only`` then moves into place only
    ``catalog.json``: the form that goes into GigAI's package data (the site gets the full folder).
    """

    work, out = Path(work), Path(out)
    inputs = load_work(work)
    shipped = shipped_course(inputs["course"], inputs["paths"], inputs["curriculum"], inputs["vocabulary"], inputs["titles"], role_key=role_key, site_version=site_version, base_url=base_url)
    banned = build_forbidden(work, master, allow_names=allow_names)
    if out.exists():
        previous, previous_catalog = out / COURSE_FILENAME, out / CATALOG_FILENAME
        was_shipped = (previous.is_file() and _read(previous).get("schema_version") == SCHEMA_VERSION) or (
            previous_catalog.is_file() and _read(previous_catalog).get("schema_version") in CATALOG_SCHEMAS
        )
        if not out.is_dir() or (any(out.iterdir()) and not was_shipped):
            raise ShippedExportError("out_not_shipped", f"{out} exists and is not a shipped course folder")
    files = shipped.files()
    written = shipped.files(catalog_only=catalog_only)
    temporary = out.with_name(f".{out.name}.tmp-{os.getpid()}")
    shutil.rmtree(temporary, ignore_errors=True)
    try:
        for name, data in files.items():
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        scan = scan_shipped(temporary, banned)
        if catalog_only:
            for name in files:
                if name != CATALOG_FILENAME:
                    (temporary / name).unlink()
            shutil.rmtree(temporary / PATHS_DIRNAME, ignore_errors=True)
        if out.exists():
            shutil.rmtree(out)
        os.replace(temporary, out)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return ExportReport(
        out=out, role_key=role_key, lessons=len(shipped.catalog["lessons"]), paths=0 if catalog_only else len(shipped.paths),
        files=tuple((name, len(data), deflated(data)) for name, data in written.items()),
        removed=shipped.removed, forbidden=banned.counts, exempt=banned.exempt, scan=scan,
    )


__all__ = [
    "Allowed",
    "CATALOG_SCHEMAS",
    "CATALOG_SCHEMA_V1",
    "CATALOG_SCHEMA_VERSION",
    "CONCEPT_KEYS_VERSION",
    "ExportReport",
    "FORBIDDEN_KEYS",
    "Forbidden",
    "Hit",
    "ID_SHAPE",
    "MASTER_LINE_MIN_WORDS",
    "MASTER_NAME_MIN_CHARS",
    "MASTER_NAME_SEPARATORS",
    "MASTER_NAME_SUFFIXES",
    "MasterParts",
    "PATHWAY_VERSION",
    "POSTING_COUNT",
    "POSTING_COUNT_GAP_WORDS",
    "POSTING_FILES",
    "SCHEMA_VERSION",
    "SITE_BASE_URL",
    "SITE_VERSION",
    "SHINGLE_WORDS",
    "SHORT_SENTENCE_MIN_WORDS",
    "ScanReport",
    "Shipped",
    "ShippedExportError",
    "ShippedScanError",
    "Site",
    "URL",
    "URL_KEYS",
    "URL_MAPS",
    "allowed_in",
    "build_forbidden",
    "catalog_site",
    "check_role_key",
    "check_shipped",
    "course_terms",
    "deflated",
    "export_shipped",
    "forbidden",
    "is_url_field",
    "library_id",
    "load_work",
    "master_names",
    "master_parts",
    "posting_parts",
    "scan_shipped",
    "scan_text",
    "shingles",
    "shipped_course",
    "strip_path",
    "words",
]
