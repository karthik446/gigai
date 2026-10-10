"""0.1.11.10 Part B, packet G4: the practice path of each lesson of a course (pathway step 9).

A practice path takes the learner from zero to confident with a lesson's MAIN TOOL: two to five setup items and
eight to twelve steps, each one line of what to do, why, the ONE documentation section that explains how, and what
"done" looks like. :func:`run_paths_step` is a function of a work folder: it reads the course (G2's
``course.json``) and the verified sources (G2's step 7) and writes ``<work>/paths/<lesson id>.json``, the folder
``learning_render.load_paths`` reads.

Per lesson (the method the research spike ``spike9c`` proved on three lessons):

1. THE MAIN TOOL is picked by CODE (:func:`pick_tool`, ``learning_course.choose_main_tool``), in this order: a
   tool the lesson's TITLE names (a counted technology of the course or a ``tool_links`` name); else the tool
   whose documentation holds the majority of the lesson's verified sources; else, of the technologies the
   lesson names, the one counted in most postings. The sentence "N of M postings name it" is written by code
   from the course's counts; a company is named only when the course's counted examples name it for that tool.
2. FETCH: the lesson's verified source pages OF THE MAIN TOOL (``learning_course.tool_pages``) on its
   documentation host are the start pages of a bounded same-host crawl (``learning_fetch.crawl_lesson``, at
   most 24 pages, under the outbound policy of the fetcher it is handed). Of each page only the title and the
   headings that carry an ``id`` are kept. A main tool with no verified page of its own has no practice page
   (``path_unverified``): a path is never built from another tool's documentation.
3. THE ARC CHECKLIST is computed by code from the fetched pages (``learning_fetch.compute_arc_checklist``).
4. ONE MODEL CALL (``data/instructions/learning_paths.md``): it opens with ``learning_course.JSON_OPENER``, holds
   the lesson's title, summary, subtopic titles and technologies, the checklist and the numbered pages and
   headings (fenced as untrusted). A citation is a ``(page number, heading id)`` pair from that list, or a page
   marked ``(page, no sections)`` with an empty heading. The model never writes a URL.
5. CODE CHECKS: every citation is resolved to ``url#anchor`` (a page-level one to the bare URL) and RE-FETCHED
   through the fetcher: the page must answer 200, not be a site's home page, still hold the anchor (or, for a
   page-level citation, still be a deep page with real text and the same title). An item that fails, or whose
   text holds a code block, a command line or a URL, is dropped. Stage coverage is checked on what is left.
6. ONE RETRY when the answer was not usable (the validator's message), a listed stage got no item, too few items
   survived, too few steps were hands-on, or more than a third of the kept items cite a page that is not on one
   of the main tool's verified source hosts (the retry names what was wrong; for the last it names the tool and
   its hosts). Then at least 8 steps and 2 setup items must be left and no more than a third of them off the
   tool's hosts, else the lesson has no practice page (``path_unverified``); two unusable answers are
   ``path_model_failed``.

THE RESUME IS NOT AN INPUT. :func:`paths_prompt` is built from a lesson's ``title``, ``summary``,
``subtopics[].title`` and ``technologies`` only (design section 6): no marker, no evidence quote and no resume
line can reach it, and this module reads no resume.

A lesson that is done is not done again: a stored path that still validates is kept without a fetch or a call,
a lesson that was dropped stays dropped, and a crawl that finished is not fetched again
(``<work>/paths-work/<lesson id>.json`` keeps the crawl and the outcome, keyed by the lesson's inputs).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import datetime
import json
from pathlib import Path
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from ..canonical import digest_imported_bytes
from .learning_corpus import LearningCorpusError, _error_text, _json_object, _render, _template
from .learning_course import (
    JSON_OPENER,
    LONG_CALL_TIMEOUT_SECONDS,
    CourseModel,
    LearningCourseStopped,
    ToolCandidate,
    _clean_tree,
    _one_line,
    choose_main_tool,
    contact_findings,
    source_strength,
    tool_aliases,
    tool_pages,
    usable_url,
)
from .learning_fetch import (
    MAX_PAGES_PER_LESSON,
    STOP_BUDGET,
    FetchStopped,
    clean_text,
    compute_arc_checklist,
    crawl_lesson,
    is_deep_path,
    lesson_words,
)
from .untrusted_text import fence_untrusted_posting, neutralise_fence_markers

SCHEMA_PATH_WORK = "scout-learning-path-work:1"

PATH_UNVERIFIED = "path_unverified"
PATH_MODEL_FAILED = "path_model_failed"
OUTCOME_KEPT = "kept"

#: What a path must still hold after the checks.
STEPS_MIN, SETUP_MIN = 8, 2
#: What the prompt asks for.
STEPS_ASKED_MAX, SETUP_ASKED_MAX = 12, 5
#: What the validator lets an answer hold (a 13th step is not a reason to spend the one retry).
STEPS_ACCEPTED_MAX, SETUP_ACCEPTED_MAX = 15, 6
#: Headings of one page a prompt lists; a heading that is not listed cannot be cited.
HEADINGS_PER_PAGE = 40
HEADING_TEXT_CHARS = 100
HEADING_ID_CHARS = 120
STAGE_PAGES_SHOWN = 6
DO_CHARS_MAX, WHY_CHARS_MAX, DONE_CHARS_MAX = 400, 700, 400
LINE_CHARS_MAX = 600
TITLE_CHARS_MAX = 160
SECTION_CHARS_MAX = 200
TOOL_CHARS_MAX = 60
COMPANIES_SHOWN = 3
#: At least this share of the steps is hands-on (their "do" does not start with a reading verb).
HANDS_ON_SHARE = (2, 3)
#: At most this share of the kept items may cite a page that is not on one of the main tool's verified source hosts.
OFF_TOOL_SHARE = (1, 3)
READING_VERBS = frozenset({
    "read", "review", "skim", "study", "learn", "understand", "watch", "browse", "look", "familiarize", "familiarise", "note",
})

_PATHS_RESOURCE = ("scout", "data", "instructions", "learning_paths.md")
_LESSON_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
#: A heading id that can stand in a URL fragment as it is.
_ANCHOR = re.compile(r"[A-Za-z0-9._~%:+-]+")
_WORDS = re.compile(r"[a-z0-9]+")

#: A code block or inline code; a command line (a prompt, a pipe, a chain, a substitution, a redirect); a URL.
#: Naming a command in a sentence ("kubectl get pods shows ...") is what the approved paths do and is let through.
_CODE_RE = re.compile(r"`")
_COMMAND_LINE_RE = re.compile(r"(?m)^\s*\$\s+\S|&&|\|\||\s\|\s|\$\(|\$\{|\s>>\s|2>&1|\s2>\s*/")
_URL_RE = re.compile(r"(?i)\b(?:https?://|ftp://|www\.)\S")
#: The scheme of an address on the learner's own machine ("the UI answers at http://localhost:5000"): that is where a
#: step's result shows, not a link to anywhere, so it stays as ``localhost:5000``.
_LOCAL_SCHEME_RE = re.compile(r"(?i)\bhttps?://(?=(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?![\w.-]))")
#: A sentence that states a posting count or a share: only code states those.
_COUNT_CLAIM_RE = re.compile(r"(?i)\b\d+\s*(?:of|out of|/)\s*\d+\b|\d\s*%|\bpercent\b|\bpostings?\b|\bjob (?:ads?|listings?|descriptions?)\b")
_SENTENCE_END = re.compile(r"(?<=[.;!?])\s+")


class _PathInvalid(ValueError):
    """A model answer the checks cannot use: the reason goes into the one retry."""


class _PageFetcher(Protocol):
    def fetch_page(self, url: str) -> Any: ...


def _today() -> str:
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# The course data a path is built from
# ---------------------------------------------------------------------------


def _course_lessons(course: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    found = []
    for module in course.get("modules") or []:
        for lesson in (module.get("concepts") or []) if isinstance(module, Mapping) else []:
            if isinstance(lesson, Mapping) and isinstance(lesson.get("id"), str):
                found.append(lesson)
    return found


def _source_lists(sources: Mapping[str, Any]) -> Mapping[str, Any]:
    """``{lesson id: [verified source]}`` of step 7's answer (or that mapping itself)."""

    found = sources.get("lessons") if isinstance(sources, Mapping) else None
    return found if isinstance(found, Mapping) else (sources if isinstance(sources, Mapping) else {})


def _names(lesson: Mapping[str, Any]) -> list[str]:
    """The technologies a lesson names, each one line, each once."""

    found: list[str] = []
    for name in lesson.get("technologies") or []:
        shown = _one_line(name, TOOL_CHARS_MAX)
        if shown and shown not in found:
            found.append(shown)
    return found


def _subtopic_titles(lesson: Mapping[str, Any]) -> list[str]:
    found = []
    for sub in lesson.get("subtopics") or []:
        title = _one_line(sub.get("title"), TITLE_CHARS_MAX) if isinstance(sub, Mapping) else ""
        if title:
            found.append(title)
    return found


def _counted(course: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The counted technology concepts of a course's front page (``{id, display, in_90d, any_date}``), each once."""

    found: dict[str, Mapping[str, Any]] = {}
    groups = course.get("technologies")
    for items in groups.values() if isinstance(groups, Mapping) else []:
        for item in items if isinstance(items, list) else []:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str) and isinstance(item.get("display"), str):
                found.setdefault(item["id"], item)
    return list(found.values())


def _example_companies(course: Mapping[str, Any]) -> dict[str, list[str]]:
    """``{concept id: [company]}`` of the counted examples a course carries (its expectations and technologies)."""

    found: dict[str, list[str]] = {}
    for part in ("expectations", "technologies"):
        groups = course.get(part)
        for items in groups.values() if isinstance(groups, Mapping) else []:
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                    continue
                for example in item.get("examples") or []:
                    company = _one_line(example.get("company"), 80) if isinstance(example, Mapping) else ""
                    if company and company not in found.setdefault(item["id"], []):
                        found[item["id"]].append(company)
    return found


def _int(value: object) -> int:
    return value if type(value) is int and value > 0 else 0


def _names_it(name: str, display: str) -> bool:
    """Whether a technology a lesson names is the counted concept ``display``.

    "MLflow Model Registry" names "MLflow"; "Apache Spark" names "Spark / PySpark" (any of the concept's names,
    ``learning_course.tool_aliases``).
    """

    words = _WORDS.findall(name.lower())
    for alias in tool_aliases(display):
        wanted = _WORDS.findall(alias.lower())
        if wanted and any(words[start : start + len(wanted)] == wanted for start in range(len(words) - len(wanted) + 1)):
            return True
    return False


@dataclass(frozen=True)
class Tool:
    """The main tool of a lesson. ``concept`` is its counted concept (``None``: the postings do not count it)."""

    name: str
    concept: Mapping[str, Any] | None = None
    #: The lesson's other counted technologies: what the alternatives line may be given counts for.
    others: tuple[Mapping[str, Any], ...] = ()
    #: The hosts of the lesson's verified sources that are pages of this tool: what a kept item may cite.
    hosts: tuple[str, ...] = ()
    #: The course's ``tool_links`` address for the tool (``""``: none).
    link: str = ""
    #: How it was chosen: ``title``, ``hosts``, ``count``, ``pages``, or ``""`` (nothing to choose from).
    how: str = ""


def _tool_links(course: Mapping[str, Any]) -> dict[str, str]:
    links = course.get("tool_links")
    return {name: url for name, url in links.items() if isinstance(name, str) and isinstance(url, str) and usable_url(url)} if isinstance(links, Mapping) else {}


def pick_tool(lesson: Mapping[str, Any], course: Mapping[str, Any], lesson_sources: Sequence[Mapping[str, Any]] = ()) -> Tool:
    """The main tool of a lesson (``learning_course.choose_main_tool``), in this order:

    1. a tool the lesson's TITLE names, whole words and any case: one of the course's counted technologies or a
       ``tool_links`` name;
    2. the tool whose documentation holds the majority of ``lesson_sources`` (the lesson's verified sources): a
       host is a tool's when ``tool_links`` says so or its name is in the host (``learning_course.source_strength``);
    3. of the technologies the lesson names, the counted one most postings name (90 days, then any date), one
       with a verified page of its own before one without.

    A tie keeps the lesson's order. A lesson that names no counted technology is practised with the technology
    it names that has most verified pages, else the first it names (no count is claimed for it), and one that
    names none at all with its own title.
    """

    names = _names(lesson)
    counted = _counted(course)
    links = _tool_links(course)
    matched: list[Mapping[str, Any]] = []
    for name in names:
        for concept in counted:
            if _names_it(name, concept["display"]) and concept not in matched:
                matched.append(concept)

    def link_of(display: str) -> str:
        aliases = {alias.casefold() for alias in tool_aliases(display)}
        return next((url for name, url in links.items() if name.casefold() in aliases), "")

    candidates: list[ToolCandidate] = []
    for concept in [*matched, *(concept for concept in counted if concept not in matched)]:
        display = _one_line(concept["display"], TOOL_CHARS_MAX)
        if display:
            candidates.append(ToolCandidate(
                display, (_int(concept.get("in_90d")), _int(concept.get("any_date"))), of_lesson=concept in matched, link=link_of(display), ref=concept,
            ))
    taken = {alias.casefold() for item in candidates for alias in tool_aliases(item.name)}
    for name in names:  # a technology the lesson names that no posting counts: the title's tool only as a tool_links name, never given a count
        if name.casefold() not in taken and not any(_names_it(name, concept["display"]) for concept in matched):
            taken.add(name.casefold())
            linked = next((url for known, url in links.items() if known.casefold() == name.casefold()), "")
            candidates.append(ToolCandidate(name, counted=False, of_lesson=True, titled=bool(linked), link=linked))
    for name, url in links.items():
        shown = _one_line(name, TOOL_CHARS_MAX)
        if shown and shown.casefold() not in taken:
            taken.add(shown.casefold())
            candidates.append(ToolCandidate(shown, counted=False, link=url))

    chosen = choose_main_tool(clean_text(str(lesson.get("title", ""))), candidates, lesson_sources)
    if chosen is None:
        return Tool(names[0] if names else (_one_line(lesson.get("title"), TITLE_CHARS_MAX) or str(lesson["id"])))
    picked, how = chosen
    hosts = tuple(dict.fromkeys(_host(str(source.get("url"))) for source in tool_pages(lesson_sources, picked.name, picked.link)))
    return Tool(picked.name, picked.ref, tuple(item for item in matched if item is not picked.ref), hosts, picked.link, how)


def _count_of(concept: Mapping[str, Any], course: Mapping[str, Any]) -> tuple[int, int, str] | None:
    """``(n, m, window words)`` of a counted concept: the 90-day numbers, or the any-date ones when no posting is that new."""

    corpus = course.get("corpus")
    if not isinstance(corpus, Mapping):
        corpus = {}
    recent, stored = _int(corpus.get("postings_90d")), _int(corpus.get("postings_any_date"))
    if recent:
        return _int(concept.get("in_90d")), recent, "postings in the last 90 days"
    if stored:
        return _int(concept.get("any_date")), stored, "stored postings"
    return None


def count_sentence(tool: Tool, course: Mapping[str, Any]) -> str:
    """How often the stored postings name the main tool, from the course's own counts. No model writes this."""

    counted = _count_of(tool.concept, course) if tool.concept is not None else None
    if tool.concept is None or counted is None:
        return f"{tool.name} is named in this lesson; it is not among the tools counted in your stored postings."
    number, total, window = counted
    companies = _example_companies(course).get(str(tool.concept["id"]), [])[:COMPANIES_SHOWN]
    named = f" ({', '.join(companies)})" if companies and number else ""
    return f"{number} of {total} {window} name {tool.name}{named}."


def others_sentence(tool: Tool, course: Mapping[str, Any]) -> str:
    """The posting counts of the lesson's other counted technologies; ``""`` when it names none."""

    parts = []
    for concept in tool.others:
        counted = _count_of(concept, course)
        display = _one_line(concept.get("display"), TOOL_CHARS_MAX)
        if counted is not None and display:
            parts.append(f"{display} {counted[0]} of {counted[1]}")
    if not parts:
        return ""
    window = _count_of(tool.others[0], course)[2]  # type: ignore[index]
    return f"Named in your {window}: {', '.join(parts)}."


# ---------------------------------------------------------------------------
# The pages of a lesson: where the crawl starts, what a prompt lists
# ---------------------------------------------------------------------------


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def start_pages(lesson_sources: Sequence[Mapping[str, Any]], tool_name: str, link: str = "") -> list[str]:
    """The lesson's verified source pages OF THE MAIN TOOL on ONE host, its documentation host.

    Only a page of the tool is a start page (``learning_course.source_strength``: its host is the tool's, or
    its address names the tool, or its title opens with it). A page is worth more when its host is the tool's
    (4) than when only its address or title names it (2), and when it is documentation rather than a paper or an article (1). The
    host of the best page wins; its pages are the start pages, best first. Pages on any other host are not
    asked. ``[]`` when no verified source is a page of the tool: there is nothing of that tool to practise from.
    """

    scored: list[tuple[int, int, str]] = []
    for index, source in enumerate(lesson_sources):
        url = usable_url(source.get("url")) if isinstance(source, Mapping) else None
        strength = source_strength(source, tool_name, link) if url is not None else 0
        if url is None or not strength:
            continue
        score = 2 * strength + (1 if source.get("kind") in ("doc", "guide", "tutorial") else 0)
        scored.append((-score, index, url))
    scored.sort()
    if not scored:
        return []
    host = _host(scored[0][2])
    return list(dict.fromkeys(url for _score, _index, url in scored if _host(url) == host))


def listed_pages(crawl_pages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The fetched pages as a prompt numbers them: ``{url, title, headings: [{id, text}], page_level, citable}``.

    The number of a page is its place among the pages that answered (the number ``compute_arc_checklist`` uses).
    ``headings`` are the ones a prompt lists: an id that can stand in a URL fragment, at most 40 a page. A site's
    home page is numbered and never listed.
    """

    pages = []
    for page in crawl_pages:
        if "headings" not in page:
            continue
        headings = [
            {"id": str(heading.get("id", "")), "text": clean_text(str(heading.get("text", "")))}
            for heading in page.get("headings") or []
            if isinstance(heading, Mapping) and len(str(heading.get("id", ""))) <= HEADING_ID_CHARS and _ANCHOR.fullmatch(str(heading.get("id", "")))
        ][:HEADINGS_PER_PAGE]
        url = str(page.get("url", ""))
        deep = usable_url(url) is not None  # a site's home page is never a citation, whatever headings it has
        page_level = deep and bool(page.get("page_level_citable")) and not page.get("headings")
        pages.append({
            "url": url, "title": clean_text(str(page.get("title", ""))), "headings": headings if deep else [],
            "page_level": page_level, "citable": deep and (bool(headings) or page_level),
        })
    return pages


def arc_checklist(crawl_pages: Sequence[Mapping[str, Any]]) -> dict[str, list[int]]:
    """``{stage: [page number]}`` for the stages some CITABLE fetched page matches, in the arc's order."""

    pages = listed_pages(crawl_pages)
    found: dict[str, list[int]] = {}
    for stage, hits in compute_arc_checklist(crawl_pages).items():
        numbers = sorted({int(hit["page_index"]) for hit in hits if pages[int(hit["page_index"])]["citable"]})  # type: ignore[call-overload]
        if numbers:
            found[stage] = numbers
    return found


def _pages_shown(numbers: Sequence[int]) -> str:
    return ", ".join(f"PAGE {number}" for number in numbers[:STAGE_PAGES_SHOWN])


def arc_block(checklist: Mapping[str, Sequence[int]]) -> str:
    if not checklist:
        return "ARC CHECKLIST (computed by GigAI from the fetched pages, not by you): no stage has a page in the list; follow the arc as far as the listed pages allow."
    lines = [
        "ARC CHECKLIST (computed by GigAI from the fetched pages, not by you). Each stage below HAS pages in the list: the path "
        "MUST include at least one item citing one of that stage's pages. Do not add a step for a stage not listed here.",
    ]
    lines.extend(f"  - {stage}: {_pages_shown(numbers)}" for stage, numbers in checklist.items())
    if "retention" in checklist and "compaction" in checklist:
        lines.append(
            "  NOTE: both retention and compaction have pages: include one step that explicitly COMPARES time-based retention "
            "with log compaction as a decision, not just a step for each in isolation."
        )
    return "\n".join(lines)


def headings_block(pages: Sequence[Mapping[str, Any]]) -> str:
    lines = []
    for number, page in enumerate(pages):
        if page["page_level"]:
            lines.append(f"PAGE {number} (page, no sections): {page['title']}")
        elif page["headings"]:
            lines.append(f"PAGE {number}: {page['title']}")
            lines.extend(f"  - id=\"{heading['id']}\" text=\"{heading['text'][:HEADING_TEXT_CHARS]}\"" for heading in page["headings"])
    return "\n".join(lines) or "(no page with a section to cite was fetched)"


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


def paths_prompt(
    *, role_text: str, lesson: Mapping[str, Any], tool_name: str, pages: Sequence[Mapping[str, Any]],
    checklist: Mapping[str, Sequence[int]], retry_notice: str | None = None, validation_error: BaseException | str | None = None,
) -> str:
    """The practice-path prompt of one lesson.

    Of the lesson it reads ``title``, ``summary``, ``subtopics[].title`` and ``technologies``, and nothing else:
    not a marker, not an evidence quote, not a subtopic body. No resume line is an input (this function takes
    none and this module reads none). Everything a stranger or an earlier model call wrote is fenced.
    """

    subtopics = "\n".join(f"  - {title}" for title in _subtopic_titles(lesson)) or "  (none)"
    block = (
        f"LESSON: {clean_text(str(lesson.get('title', '')))}\n"
        f"SUMMARY: {clean_text(str(lesson.get('summary', '')))}\n"
        f"SUBTOPICS:\n{subtopics}\n"
        f"TECHNOLOGIES NAMED IN THE LESSON: {', '.join(_names(lesson)) or '(none)'}\n"
        f"MAIN TOOL: {tool_name}"
    )
    return _render(
        _template(_PATHS_RESOURCE),
        {
            "opener": JSON_OPENER, "role": clean_text(role_text), "lesson": fence_untrusted_posting(block),
            "arc_checklist": arc_block(checklist), "headings": fence_untrusted_posting(headings_block(pages)),
            "retry_notice": retry_notice, "validation_error": _error_text(validation_error),
        },
    )


# ---------------------------------------------------------------------------
# The answer: its shape, its text, its citations
# ---------------------------------------------------------------------------


def parse_path_answer(raw: str) -> dict[str, Any]:
    """The decoded answer when it has the shape asked for; else ``path_invalid`` or a reason for the retry.

    Refused: no JSON object; ``setup`` without 2 to 6 objects or ``steps`` without 8 to 15; an item without a
    ``do``, a ``why`` or a ``done_when``; a ``source`` that is not ``{"page": integer, "heading": string}``.
    Every string is glyph-cleaned on the way in (``learning_course.clean_glyphs``).
    """

    decoded = _clean_tree(_json_object(raw, "path_invalid"))
    for part, low, high in (("setup", SETUP_MIN, SETUP_ACCEPTED_MAX), ("steps", STEPS_MIN, STEPS_ACCEPTED_MAX)):
        asked = SETUP_ASKED_MAX if part == "setup" else STEPS_ASKED_MAX
        items = decoded.get(part)
        if not isinstance(items, list) or not low <= len(items) <= high:
            held = len(items) if isinstance(items, list) else 0
            raise _PathInvalid(f"{part} must hold {low} to {asked} items; this answer held {held}")
        for index, item in enumerate(items, 1):
            if not isinstance(item, Mapping):
                raise _PathInvalid(f"{part} item {index} is not an object")
            for key in ("do", "why", "done_when"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise _PathInvalid(f"{part} item {index}: {key} is missing")
            source = item.get("source")
            page = source.get("page") if isinstance(source, Mapping) else None
            heading = source.get("heading") if isinstance(source, Mapping) else None
            if type(page) is not int or not isinstance(heading if heading is not None else "", str):
                raise _PathInvalid(f"{part} item {index}: source must be an object with an integer page and a string heading")
    return dict(decoded)


def string_problem(text: str) -> str | None:
    """Why a model string may not stand in a path: a code block, a command line or a web URL; ``None`` when it may."""

    if _CODE_RE.search(text):
        return "a code block or a backtick"
    if "\n" in text.strip():
        return "more than one line"
    if _COMMAND_LINE_RE.search(text):
        return "a command line"
    if _URL_RE.search(text):
        return "a URL"
    return None


def _item_text(value: object, limit: int) -> tuple[str, str | None]:
    """``(text, None)`` of one item string as a path holds it, or ``("", why not)``."""

    if not isinstance(value, str):
        return "", "no text"
    value = _LOCAL_SCHEME_RE.sub("", value)
    problem = string_problem(value)
    if problem:
        return "", f"it held {problem}"
    text = _one_line(value, limit)
    return (text, None) if text else ("", "the text was too long or held contact data")


def free_line(value: object, *, companies: Iterable[str] = ()) -> str:
    """A model's ``alternatives`` or ``why_this_tool`` line as a path may hold it; ``""`` when nothing of it may stand.

    Left out: the whole line when it holds a code block, a command line, a URL or contact data; any sentence
    that states a count, a share or what postings say (code writes those from the course's counts); any sentence
    that names one of ``companies`` (the employers of the course's posting examples).
    """

    if not isinstance(value, str) or string_problem(_LOCAL_SCHEME_RE.sub("", value)):
        return ""
    text = _one_line(_LOCAL_SCHEME_RE.sub("", value), LINE_CHARS_MAX)
    known = [company.casefold() for company in companies if company.strip()]
    kept = []
    for sentence in _SENTENCE_END.split(text):
        low = sentence.casefold()
        if _COUNT_CLAIM_RE.search(sentence) or any(re.search(rf"(?<!\w){re.escape(company)}(?!\w)", low) for company in known):
            continue
        kept.append(sentence)
    return " ".join(kept).strip()


@dataclass(frozen=True)
class Citation:
    """One resolved ``(page, heading)`` pair: the page as fetched, and the section on it (``anchor`` ``None``: the page itself)."""

    page: int
    url: str
    anchor: str | None
    section: str
    title: str


def resolve_citation(source: Mapping[str, Any], pages: Sequence[Mapping[str, Any]]) -> tuple[Citation | None, str | None]:
    """``(citation, None)`` when ``source`` names a listed page and a heading listed under it, else ``(None, why not)``.

    A heading is matched by its id, else by its exact text, else as the one listed heading whose text holds the
    words given. An empty heading is a page-level citation and stands only for a page listed ``(page, no sections)``.
    """

    number = source.get("page")
    wanted = clean_text(str(source.get("heading") or ""))
    if type(number) is not int or not 0 <= number < len(pages) or not pages[number]["citable"]:
        return None, f"page {number} is not in the list"
    page = pages[number]
    if not wanted:
        if page["page_level"]:
            return Citation(number, page["url"], None, page["title"], page["title"]), None
        return None, f"an empty heading on page {number}, which is not listed as (page, no sections)"
    headings = page["headings"]
    match = next((heading for heading in headings if heading["id"] == wanted), None)
    if match is None:
        match = next((heading for heading in headings if heading["text"].casefold() == wanted.casefold()), None)
    if match is None and len(wanted) > 4:
        holding = [heading for heading in headings if wanted.casefold() in heading["text"].casefold()]
        match = holding[0] if len(holding) == 1 else None
    if match is None:
        return None, f"the heading '{neutralise_fence_markers(wanted)[:80]}' is not listed under page {number}"
    return Citation(number, page["url"], match["id"], match["text"], page["title"]), None


class Verifier:
    """The live re-fetch of a lesson's citations, one request per cited page (``fetcher`` is the outbound policy)."""

    def __init__(self, fetcher: _PageFetcher, host: str) -> None:
        self._fetcher = fetcher
        self._host = host
        self._fetched: dict[str, Any] = {}
        self.fetches = 0

    def check(self, citation: Citation) -> tuple[str | None, str | None]:
        """``(url, None)`` when the citation verifies, ``url`` being ``page#anchor`` as it answered now; else ``(None, why not)``."""

        if citation.url not in self._fetched:
            self._fetched[citation.url] = self._fetcher.fetch_page(citation.url)
            self.fetches += 1
        fetched = self._fetched[citation.url]
        if not fetched.ok or fetched.page is None:
            return None, f"the page did not answer on the re-fetch ({fetched.error or 'no page'})"
        url = usable_url(fetched.url)
        if url is None:
            return None, "the page is a home page (no deep path)"
        if _host(url) != self._host:
            return None, "the page moved to another host"
        page = fetched.page
        if citation.anchor is None:
            if not is_deep_path(url) or not page.page_level_citable:
                return None, "a page-level citation needs a deep page with real text and no sections"
            if clean_text(page.title).casefold() != citation.title.casefold():
                return None, "the page's title changed on the re-fetch"
            return url, None
        ids = [str(heading.get("id", "")) for heading in page.headings]
        if citation.anchor in ids:
            return f"{url}#{citation.anchor}", None
        moved = next((heading for heading in page.headings if clean_text(str(heading.get("text", ""))).casefold() == citation.section.casefold()), None)
        if moved is not None and _ANCHOR.fullmatch(str(moved.get("id", ""))):
            return f"{url}#{moved['id']}", None
        return None, f"the anchor #{citation.anchor} is not on the page on the re-fetch"


@dataclass
class Attempt:
    """One answer after the code checks."""

    setup: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    drops: list[dict[str, Any]] = field(default_factory=list)
    missing_stages: list[str] = field(default_factory=list)
    hands_on: bool = True
    #: How many kept items cite a page that is not on one of the main tool's verified source hosts.
    off_tool: int = 0
    alternatives: str = ""
    why: str = ""
    #: ``{page url: {title, sections}}`` of the pages the kept items cite, in the order they are first cited.
    cited: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def enough(self) -> bool:
        return len(self.steps) >= STEPS_MIN and len(self.setup) >= SETUP_MIN

    @property
    def on_tool(self) -> bool:
        """No more than a third of the kept items cite a page off the main tool's hosts."""

        return self.off_tool * OFF_TOOL_SHARE[1] <= (len(self.steps) + len(self.setup)) * OFF_TOOL_SHARE[0]

    @property
    def wants_retry(self) -> bool:
        return not self.enough or bool(self.missing_stages) or not self.hands_on or not self.on_tool

    @property
    def rank(self) -> tuple[int, int, int, int, int]:
        return (int(self.on_tool), int(self.enough), -len(self.missing_stages), int(self.hands_on), len(self.steps) + len(self.setup))


def _hands_on(steps: Sequence[Mapping[str, Any]]) -> bool:
    reading = sum(1 for step in steps if (_WORDS.findall(str(step["do"]).lower()) or [""])[0] in READING_VERBS)
    return (len(steps) - reading) * HANDS_ON_SHARE[1] >= len(steps) * HANDS_ON_SHARE[0]


def check_answer(
    answer: Mapping[str, Any], pages: Sequence[Mapping[str, Any]], checklist: Mapping[str, Sequence[int]], verifier: Verifier, *,
    companies: Iterable[str] = (), today: str | None = None, tool_hosts: Iterable[str] | None = None,
) -> Attempt:
    """The code checks of one answer: text, citation, live re-fetch per item; then stage coverage, the hands-on share and the tool's hosts.

    ``tool_hosts`` are the hosts of the lesson's verified sources that are pages of the main tool: a kept item
    whose page is on another host is counted in ``off_tool`` (``None``: not checked).
    """

    today = today or _today()
    attempt = Attempt()
    cited_pages: set[int] = set()
    for part, kept in (("setup", attempt.setup), ("steps", attempt.steps)):
        for index, item in enumerate(answer.get(part) or [], 1):
            texts: dict[str, str] = {}
            problem: str | None = None
            for key, limit in (("do", DO_CHARS_MAX), ("why", WHY_CHARS_MAX), ("done_when", DONE_CHARS_MAX)):
                texts[key], why_not = _item_text(item.get(key), limit)
                if why_not and problem is None:
                    problem = f"its {key} was not usable: {why_not}"
            citation: Citation | None = None
            url: str | None = None
            if problem is None:
                citation, problem = resolve_citation(item.get("source") or {}, pages)
            if problem is None and citation is not None:
                url, problem = verifier.check(citation)
            if problem is not None or citation is None or url is None:
                attempt.drops.append({"part": part, "n": index, "reason": problem or "not verified"})
                continue
            title = _one_line(citation.title, TITLE_CHARS_MAX) or _one_line(citation.section, TITLE_CHARS_MAX) or "Documentation page"
            section = _one_line(citation.section, SECTION_CHARS_MAX) or title
            kept.append({
                "n": len(kept) + 1, "do": texts["do"], "why": texts["why"],
                "source": {"title": title, "url": url, "verified": {"http": 200, "section_found": section, "checked": today}},
                "done_when": texts["done_when"],
            })
            cited_pages.add(citation.page)
            entry = attempt.cited.setdefault(url.split("#", 1)[0], {"title": title, "sections": []})
            if section not in entry["sections"]:
                entry["sections"].append(section)
    attempt.missing_stages = [stage for stage, numbers in checklist.items() if not cited_pages & set(numbers)]
    attempt.hands_on = _hands_on(attempt.steps)
    if tool_hosts is not None:
        allowed = {host.lower() for host in tool_hosts}
        attempt.off_tool = sum(1 for item in [*attempt.setup, *attempt.steps] if _host(item["source"]["url"]) not in allowed)
    attempt.alternatives = free_line(answer.get("alternatives"), companies=companies)
    attempt.why = free_line(answer.get("why_this_tool"), companies=companies)
    return attempt


def retry_notice(attempt: Attempt, checklist: Mapping[str, Sequence[int]], tool: Tool | None = None) -> str:
    """What the one retry is told: the stages with no item and their pages, the items removed and why, what was too little.

    With ``tool``, and more than a third of the kept items off its hosts, the notice names the main tool and
    its hosts first.
    """

    lines = []
    if tool is not None and not attempt.on_tool:
        name = neutralise_fence_markers(clean_text(tool.name))
        lines.append(
            f"- the MAIN TOOL of this path is {name}, and {attempt.off_tool} of the {len(attempt.steps) + len(attempt.setup)} kept items cited a page "
            f"that is not {name} documentation ({name} documentation is at: {', '.join(tool.hosts) or 'no fetched host'}): every setup and steps "
            f"item must practise {name} and cite a listed page of {name}'s own documentation, never another tool's"
        )
    lines += [f"- no kept item cites a page of the stage {stage}: cite one of {_pages_shown(checklist[stage])}" for stage in attempt.missing_stages]
    lines.extend(f"- {drop['part']} item {drop['n']} was removed: {drop['reason']}" for drop in attempt.drops[:12])
    if not attempt.enough:
        lines.append(
            f"- only {len(attempt.steps)} steps and {len(attempt.setup)} setup items were left after the checks; at least {STEPS_MIN} steps and "
            f"{SETUP_MIN} setup items must each cite a listed page and one of its listed heading ids, copied exactly"
        )
    if not attempt.hands_on:
        lines.append("- fewer than two thirds of the steps were hands-on: start a step with a reading verb only where no hands-on page is listed")
    return "\n".join(lines)


def build_path(lesson_id: str, tool: Tool, course: Mapping[str, Any], attempt: Attempt, lesson_sources: Sequence[Mapping[str, Any]], *, today: str | None = None) -> dict[str, Any]:
    """The path of a lesson in the renderer's shape (the v5 schema). ``sources`` are the deep pages its items cite."""

    today = today or _today()
    kinds = {str(source.get("url")): str(source.get("kind") or "doc") for source in lesson_sources if isinstance(source, Mapping)}
    why = " ".join(part for part in (count_sentence(tool, course), attempt.why) if part)
    alternatives = " ".join(part for part in (attempt.alternatives, others_sentence(tool, course)) if part)
    return {
        "concept": lesson_id, "tool": tool.name, "alternatives": alternatives, "why_this_tool": why,
        "setup": attempt.setup, "steps": attempt.steps,
        "sources": [
            {
                "title": entry["title"], "url": url, "kind": kinds.get(url, "doc"),
                "verified": {"http": 200, "section_found": " / ".join(entry["sections"])[:SECTION_CHARS_MAX], "checked": today},
            }
            for url, entry in attempt.cited.items()
        ],
    }


def check_path(data: object, lesson_id: str) -> bool:
    """Whether a stored path is one this step wrote for ``lesson_id`` and the renderer still accepts (skip-if-valid)."""

    from .learning_render import validate_path

    if not isinstance(data, Mapping) or data.get("concept") != lesson_id or not isinstance(data.get("tool"), str) or not data["tool"]:
        return False
    setup, steps, sources = data.get("setup"), data.get("steps"), data.get("sources")
    if not isinstance(setup, list) or not isinstance(steps, list) or not isinstance(sources, list) or len(steps) < STEPS_MIN or len(setup) < SETUP_MIN:
        return False
    for item in [*setup, *steps]:
        source = item.get("source") if isinstance(item, Mapping) else None
        if not isinstance(source, Mapping) or usable_url(source.get("url")) is None:
            return False
        verified = source.get("verified")
        if not isinstance(verified, Mapping) or verified.get("http") != 200:
            return False
        if type(item.get("n")) is not int or not all(isinstance(item.get(key), str) and item[key] for key in ("do", "why", "done_when")):
            return False
    if not all(isinstance(source, Mapping) and usable_url(source.get("url")) for source in sources):
        return False
    try:
        return not validate_path(dict(data), "path") and not contact_findings(dict(data))
    except (TypeError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# The step over a work folder
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: object) -> None:
    from .find_jobs.discovery.storage import atomic_write

    atomic_write(path, (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))


def _key(lesson: Mapping[str, Any], seeds: Sequence[str], tool: Tool) -> str:
    """What a lesson's path was built from: when it changes, a stored outcome is not this lesson's any more."""

    inputs = [str(lesson.get("title", "")), str(lesson.get("summary", "")), _subtopic_titles(lesson), _names(lesson), list(seeds), tool.name]
    digest = json.dumps(inputs, ensure_ascii=False).encode("utf-8")
    return digest_imported_bytes(digest).removeprefix("sha256:")[:24]


def run_paths_step(
    work_dir: Path, *, model: CourseModel, fetcher: _PageFetcher, course: Mapping[str, object], sources: Mapping[str, object],
    cancel: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Step 9 over a work folder: the practice path of every lesson of ``course``, or why it has none.

    ``course`` is G2's ``course.json`` and ``sources`` its step 7 answer (``{"lessons": {lesson id: [source]}}``);
    ``fetcher`` is the course's ``learning_fetch.PageFetcher`` (the crawl and every re-fetch go through it) and
    ``model`` its metered model (kind ``learning``, one call per attempt, ``items`` 1: one lesson).

    Returns ``{"paths": {lesson id: path}, "dropped": [{"what": lesson id, "title", "reason"}], "stopped", "log"}``.
    ``reason`` is ``path_unverified`` (fewer than 8 steps or 2 setup items survived the checks, or nothing could
    be fetched to cite) or ``path_model_failed`` (two answers, neither usable). Each kept path is written to
    ``<work_dir>/paths/<lesson id>.json``. ``stopped`` is ``budget_exhausted`` when the course's fetch budget
    ended the step early (the lessons not reached are neither kept nor dropped, and a resume takes them up), else
    ``None``. A cancel, from ``cancel`` or from the fetcher, raises ``LearningCourseStopped``. A model that
    cannot be reached raises what ``model.ask`` raises (``model_unavailable``): that is not a verdict on a lesson.
    """

    work = Path(work_dir)
    say = progress or (lambda _text: None)
    lessons = _course_lessons(course)
    by_lesson = _source_lists(sources)
    companies = sorted({company for found in _example_companies(course).values() for company in found})
    role_text = str(course.get("role", ""))
    paths: dict[str, dict[str, Any]] = {}
    dropped: list[dict[str, object]] = []
    log: list[str] = []
    stopped: str | None = None

    def stop_if_cancelled() -> None:
        if cancel is not None and cancel():
            raise LearningCourseStopped()

    for number, lesson in enumerate(lessons, 1):
        lesson_id = str(lesson["id"])
        title = str(lesson.get("title", lesson_id))
        stop_if_cancelled()

        def drop(reason: str) -> None:
            dropped.append({"what": lesson_id, "title": title, "reason": reason})

        if not _LESSON_ID.fullmatch(lesson_id):
            log.append(f"{lesson_id}: not a lesson id a path file can be named by")
            drop(PATH_UNVERIFIED)
            continue
        found = by_lesson.get(lesson_id)
        lesson_sources = [source for source in found if isinstance(source, Mapping)] if isinstance(found, list) else []
        tool = pick_tool(lesson, course, lesson_sources)
        seeds = start_pages(lesson_sources, tool.name, tool.link)
        key = _key(lesson, seeds, tool)
        path_file = work / "paths" / f"{lesson_id}.json"
        state_file = work / "paths-work" / f"{lesson_id}.json"
        state = _read_json(state_file)
        if not isinstance(state, Mapping) or state.get("schema_version") != SCHEMA_PATH_WORK:
            state = None
        current = state is not None and state.get("key") == key

        stored = _read_json(path_file)
        if (state is None or current) and isinstance(stored, dict) and check_path(stored, lesson_id):
            paths[lesson_id] = stored
            continue
        if current and state is not None and state.get("outcome") in (PATH_UNVERIFIED, PATH_MODEL_FAILED):
            drop(str(state["outcome"]))
            continue

        def finish(outcome: str, crawl_pages: Sequence[Mapping[str, Any]], host: str, attempt: Attempt | None = None, calls: int = 0) -> None:
            _write_json(state_file, {
                "schema_version": SCHEMA_PATH_WORK, "key": key, "host": host, "pages": [dict(page) for page in crawl_pages], "outcome": outcome,
                "calls": calls, "item_drops": attempt.drops if attempt else [], "missing_stages": attempt.missing_stages if attempt else [],
                "tool": tool.name, "tool_chosen_by": tool.how, "tool_hosts": list(tool.hosts), "off_tool": attempt.off_tool if attempt else 0,
            })

        try:
            # -- the pages: a crawl that finished is not fetched again -------------------------------------
            crawl_pages = state.get("pages") if current and state is not None and isinstance(state.get("pages"), list) else None
            host = str(state.get("host", "")) if crawl_pages is not None and state is not None else _host(seeds[0]) if seeds else ""
            if crawl_pages is None:
                if not seeds:
                    log.append(f"{lesson_id}: no verified source page of the main tool {tool.name} to start from")
                    finish(PATH_UNVERIFIED, [], host)
                    drop(PATH_UNVERIFIED)
                    continue
                say(f"Reading the documentation for lesson {number} of {len(lessons)}")
                crawl = crawl_lesson(fetcher, seeds, lesson_words([*_subtopic_titles(lesson), *_names(lesson)]), max_pages=MAX_PAGES_PER_LESSON)  # type: ignore[arg-type]
                if crawl.stopped is not None:
                    raise FetchStopped(crawl.stopped)
                crawl_pages, host = [dict(page) for page in crawl.pages], crawl.host
                _write_json(state_file, {"schema_version": SCHEMA_PATH_WORK, "key": key, "host": host, "pages": crawl_pages, "outcome": None})
            pages = listed_pages(crawl_pages)
            if not any(page["citable"] for page in pages):
                log.append(f"{lesson_id}: no fetched page has a section to cite")
                finish(PATH_UNVERIFIED, crawl_pages, host)
                drop(PATH_UNVERIFIED)
                continue
            checklist = arc_checklist(crawl_pages)

            # -- one call, one retry, the code checks ---------------------------------------------------
            verifier = Verifier(fetcher, host)
            attempts: list[Attempt] = []
            notice: str | None = None
            error: BaseException | None = None
            calls = 0
            for _try in (1, 2):
                stop_if_cancelled()
                say(f"Writing the practice path for lesson {number} of {len(lessons)}")
                raw = model.ask(
                    paths_prompt(role_text=role_text, lesson=lesson, tool_name=tool.name, pages=pages, checklist=checklist, retry_notice=notice, validation_error=error),
                    items=1,
                    timeout_seconds=LONG_CALL_TIMEOUT_SECONDS,
                )
                calls += 1
                try:
                    answer = parse_path_answer(raw)
                except (LearningCorpusError, _PathInvalid) as exc:
                    model.invalid_output()
                    log.append(f"{lesson_id} attempt {calls}: {_error_text(exc)}")
                    notice, error = None, exc
                    continue
                say(f"Checking links for lesson {number} of {len(lessons)} ({len(dropped)} dropped so far)")
                attempt = check_answer(answer, pages, checklist, verifier, companies=companies, tool_hosts=tool.hosts)
                attempts.append(attempt)
                log.extend(f"{lesson_id} attempt {calls}: {item['part']} item {item['n']} dropped: {item['reason']}" for item in attempt.drops)
                if not attempt.wants_retry:
                    break
                if not attempt.on_tool:
                    log.append(f"{lesson_id} attempt {calls}: {attempt.off_tool} kept items cite a page off the hosts of the main tool {tool.name}")
                notice, error = retry_notice(attempt, checklist, tool), None
        except FetchStopped as exc:
            if exc.code != STOP_BUDGET:
                raise LearningCourseStopped() from exc
            stopped = exc.code
            break

        if not attempts:
            finish(PATH_MODEL_FAILED, crawl_pages, host, calls=calls)
            drop(PATH_MODEL_FAILED)
            continue
        best = max(attempts, key=lambda item: item.rank)
        path = _clean_tree(build_path(lesson_id, tool, course, best, lesson_sources)) if best.enough and best.on_tool else None
        if path is None or not check_path(path, lesson_id):
            log.append(
                f"{lesson_id}: {len(best.steps)} steps and {len(best.setup)} setup items were left after the checks"
                + ("" if best.on_tool else f", {best.off_tool} of them off the hosts of the main tool {tool.name}")
            )
            finish(PATH_UNVERIFIED, crawl_pages, host, best, calls)
            drop(PATH_UNVERIFIED)
            continue
        if best.missing_stages:
            log.append(f"{lesson_id}: kept without an item for " + ", ".join(best.missing_stages))
        _write_json(path_file, path)
        finish(OUTCOME_KEPT, crawl_pages, host, best, calls)
        paths[lesson_id] = path

    return {"paths": paths, "dropped": dropped, "stopped": stopped, "log": log}


__all__ = [
    "OUTCOME_KEPT",
    "PATH_MODEL_FAILED",
    "PATH_UNVERIFIED",
    "SCHEMA_PATH_WORK",
    "SETUP_MIN",
    "STEPS_MIN",
    "Attempt",
    "Citation",
    "Tool",
    "Verifier",
    "arc_block",
    "arc_checklist",
    "build_path",
    "check_answer",
    "check_path",
    "count_sentence",
    "free_line",
    "headings_block",
    "listed_pages",
    "others_sentence",
    "parse_path_answer",
    "paths_prompt",
    "pick_tool",
    "resolve_citation",
    "retry_notice",
    "run_paths_step",
    "start_pages",
    "string_problem",
]
