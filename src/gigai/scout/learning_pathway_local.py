"""0.1.11.10 shipped lessons, slice 4a: the ONE locally built page of a shipped course, ``pathway.html``.

A shipped course is generic (``learning_shipped.py``): the same lessons and practice pages for everyone. What is
about one user lives on one page that is built on that user's machine and never ships:

1. :func:`count_postings`: how often the user's OWN stored postings ask for each thing the course names. The
   shipped catalog's title phrases, deny words and vocabulary stand in for the two model calls of a generated
   course, and the product's own code counts (``learning_corpus.corpus_filters``, ``build_corpus``,
   ``count_concepts``; the same widening rule, the same percent rule, the same lesson count as a generated
   course). ZERO model calls; this module has no way to reach a model. Every item of the catalog gets a row, an
   item no posting names with 0 (the generated course's keep threshold is not applied: the list is the shipped
   course's, not this machine's).
2. :func:`build_pathway_page`: the page, in the look of the course (the personal course's templates, imported
   from ``learning_render``; ``course.css`` and the scripts at the shared asset base; no new style). It holds the
   role, the corpus line, 'Your path', Expectations and Technologies with percentages and bars, and the lessons.
   It is built from the CATALOG alone: every lesson, subtopic and practice link goes to the public learning center
   (``learning_shipped.Site``, ``target="_blank" rel="noopener"``), the one link that leaves the page; a ``:1``
   catalog has no site, so nothing is linked and the page says so. The audit (:func:`audit_page`) allows exactly
   those addresses and the page's own anchors, and runs before the file is written. The footer prints the
   catalog's site version. The same inputs give the same bytes: no date is on the page.
3. THE RESUME is the reference for known/some/new, always; it is compared by one model call that a later slice
   makes. ``markers`` is that call's checked answer, or ``None`` before it has run: the page then says 'Your
   resume is not compared yet' and shows nothing derived from a resume (no label, no line, no guess by a code
   match). ``markers`` maps a LESSON ID, or ``<lesson id>/<subtopic id>`` for a subtopic, to
   ``{"marker": "KNOWN" | "SOME" | "NEW", "evidence": {"id", "line"} | None, "missing": str | None}``. A
   subtopic with no entry is NEW; a lesson with no entry of its own gets the label a generated course derives
   from its subtopics. This module reads no resume: the caller hands in the markers and says which master
   revision they came from.
4. :func:`write_local_record`: what the page was built from, at ``<home>/scout/<project>/learning/local/<role
   key>.json``: numbers, digests and dates only, never a line of a resume or a posting.
5. :func:`state`: ``not_built``, ``built`` or ``stale`` (the rule is in its text).

The page itself is the user's own file at ``learning_shipped_store.pathway_page_path`` (the project's learning
folder), served by the Scout server at ``/learning/shipped/<role key>/pathway.html``; the only page of a shipped
course the server serves.

No model call, no network. Examples of posting sentences with their company are on the page (it is local and
private) and nowhere else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from .. import canonical as canonical_module
from . import learning_corpus, learning_shipped_store, learning_store
from .find_jobs.discovery.storage import atomic_write
from .learning_course import MARKER_KNOWN, MARKER_NEW, MARKER_SOME, _clean_tree, _front_item, _lesson_counts, _scope
from .learning_render import (
    MAX_HTML_BYTES,
    PAGE_HEAD,
    PAGE_TAIL,
    PATHWAY_PAGE,
    STYLE_LINK,
    CourseError,
    _LinkCollector,
    check_glyphs,
    clean_asset_base,
    concept_marker_line,
    esc,
    postings_label,
    render_expectations_table,
    render_subtopic,
    render_technologies,
    validate_marker,
)
from .learning_shipped import CATALOG_FILENAME, CATALOG_SCHEMAS, Site, catalog_site, library_id
from .learning_shipped_store import PAGE_BUILT, PAGE_NOT_BUILT, PAGE_STALE
from .learning_store import LearningError

COUNTS_SCHEMA = "scout-learning-local-counts:1"
RECORD_SCHEMA = "scout-learning-local-page:1"
LOCAL_DIRNAME = "local"
FOOTER_LOCAL = "built on this computer"
#: What the footer and the page header call the lessons' home.
CENTER_NAME = "From the GigAI learning center"
NOT_LINKED = "The lessons of this course are not linked: this catalog names no learning center. Update GigAI to link them."
#: The page's expectation groups: (the key in the catalog and in the counts, the heading the course prints).
EXPECTATION_GROUPS = (("responsibilities", "Responsibilities"), ("scope_at_staff", "Scope at staff level"))
#: A page is stale once the postings read differ from the recorded count by MORE than this share of it.
POSTINGS_DRIFT_PERCENT = 10
#: Why a page is stale (``state()["reasons"]``).
STALE_VERSION, STALE_CATALOG, STALE_MASTER, STALE_POSTINGS = "gigai_version", "catalog", "master", "postings"
NOT_COMPARED = (
    "Your resume is not compared yet. Until it is, this page shows what your own stored postings ask for and every "
    "lesson of the course, with nothing marked as known or new."
)
_MARKERS = (MARKER_KNOWN, MARKER_SOME, MARKER_NEW)
#: ``state(master=...)`` when the caller did not look at the master: the master is then not compared.
NOT_GIVEN: Any = object()


# --- the catalog -----------------------------------------------------------------------------------------------------


def catalog_digest(catalog: Mapping[str, Any]) -> str:
    """A digest of the shipped catalog as data (sorted keys): the same catalog gives the same digest however it was written."""

    canonical = json.dumps(catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return canonical_module.digest_imported_bytes(canonical)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _role_key(catalog: Mapping[str, Any]) -> str:
    if not isinstance(catalog, Mapping) or catalog.get("schema_version") not in CATALOG_SCHEMAS:
        raise LearningError("invalid_value", f"the catalog is not a {' or '.join(CATALOG_SCHEMAS)}")
    role_key = catalog.get("role_key")
    if not learning_shipped_store.is_role_key(role_key):
        raise LearningError("invalid_value", "the catalog names no role key")
    return str(role_key)


def _lessons(catalog: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [lesson for lesson in catalog.get("lessons") or [] if isinstance(lesson, Mapping) and isinstance(lesson.get("id"), str)]


# --- 1. the counts ---------------------------------------------------------------------------------------------------


def count_postings(home_root: Path, target: Path, catalog: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """What the user's own stored postings ask for, counted with the shipped catalog. No model call.

    The corpus is a generated course's: ``corpus_filters`` (the setup's country, any work mode), the catalog's
    title phrases and deny words, the newest postings, the last 90 days read unless fewer than 25 of them have
    stored text. ``count_concepts`` counts the catalog's vocabulary over it. The result:

    - ``corpus``: the numbers of the corpus and its ``note``, the one plain line that also says when few postings
      match (``Corpus.note``: every date is read below 25 with text; 'counts are directional' below 10);
    - ``expectations`` / ``technologies``: per group the catalog's items as a generated course writes them
      (``id``, ``display``, ``percent``, ``in_90d``, ``any_date``, up to 2 ``examples`` with company and title);
    - ``lessons``: per lesson id ``postings_in_90d`` and ``postings_any_date``, by its concept keys;
    - ``concepts`` / ``concepts_named``: how many concepts the catalog holds and how many a posting names.

    Raises ``LearningCorpusError`` (``config_unavailable``, ``search_index_unavailable``) as the corpus step does.
    """

    role_key = _role_key(catalog)
    titles_found = _mapping(catalog.get("titles"))
    titles = learning_corpus.Titles(
        phrases=tuple(str(phrase) for phrase in titles_found.get("phrases") or ()), deny=tuple(str(word) for word in titles_found.get("deny") or ())
    )
    if not titles.phrases:
        raise LearningError("invalid_value", "the catalog holds no title phrases")
    vocabulary = [concept for concept in catalog.get("vocabulary") or [] if isinstance(concept, Mapping) and isinstance(concept.get("id"), str)]
    concepts = [
        learning_corpus.Concept(
            id=concept["id"], display=str(concept.get("display", "")), category=str(concept.get("category", "")),
            phrases=tuple(str(phrase) for phrase in concept.get("phrases") or ()), technical=bool(concept.get("technical", True)),
        )
        for concept in vocabulary
    ]

    filters = learning_corpus.corpus_filters(Path(home_root), Path(target), now=now)
    corpus = learning_corpus.build_corpus(Path(home_root), titles, filters, now=now)
    counted = {str(item["id"]): item for item in learning_corpus.count_concepts(corpus.postings, [concept for concept in concepts if concept.phrases])}
    # A concept no posting names is left out by ``count_concepts``: it still gets its row, with 0.
    by_id: dict[str, Mapping[str, object]] = {
        concept.id: counted.get(concept.id) or {**concept.to_json(), "count_in90d": 0, "count_all": 0, "percent_in90d": 0.0, "percent_all": 0.0, "examples": []}
        for concept in concepts
    }

    def front(ids: object) -> list[dict[str, object]]:
        items = (_front_item(by_id[concept_id], examples=True) for concept_id in (ids if isinstance(ids, list) else []) if concept_id in by_id)
        return [item for item in items if item is not None]

    expectations = _mapping(catalog.get("expectations"))
    technologies = _mapping(catalog.get("technologies"))
    lessons: dict[str, dict[str, int]] = {}
    for lesson in _lessons(catalog):
        in_90d, any_date = _lesson_counts({"concepts": lesson.get("concept_keys") or []}, by_id)
        lessons[lesson["id"]] = {"postings_in_90d": in_90d, "postings_any_date": any_date}
    return _clean_tree({
        "schema_version": COUNTS_SCHEMA,
        "role_key": role_key,
        "corpus": corpus.to_json(),
        "expectations": {key: front(expectations.get(key)) for key, _heading in EXPECTATION_GROUPS},
        "technologies": {str(name): front(ids) for name, ids in technologies.items()},
        "lessons": lessons,
        "concepts": len(concepts),
        "concepts_named": len(counted),
    })


# --- 2. the page -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PathwayPage:
    """What :func:`build_pathway_page` wrote."""

    path: Path
    size_bytes: int
    #: Links (to the page's own anchors and to the learning center) that the audit checked, all allowed.
    links_checked: int
    #: Whether the page holds the resume's known/some/new (``markers`` was given).
    compared: bool
    #: Subtopics per label when compared, else ``{}``.
    tally: Mapping[str, int]


def _marker_entry(markers: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    found = markers.get(key)
    if found is None:
        return None
    label = found.get("marker") if isinstance(found, Mapping) else None
    if label not in _MARKERS:
        raise CourseError([f"markers[{key}]: marker must be one of KNOWN, SOME, NEW"])
    evidence = found.get("evidence")
    if evidence is not None and not (isinstance(evidence, Mapping) and isinstance(evidence.get("id"), str) and isinstance(evidence.get("line", ""), str)):
        raise CourseError([f"markers[{key}]: evidence must be an id and a line"])
    missing = found.get("missing")
    if missing is not None and not isinstance(missing, str):
        raise CourseError([f"markers[{key}]: missing must be a sentence"])
    entry = {
        "marker": label,
        "evidence": {"id": evidence["id"], "line": evidence.get("line", "")} if evidence else None,
        "missing": (missing or None) if label == MARKER_SOME else None,
    }
    errors = validate_marker(entry, f"markers[{key}]")
    if errors:
        raise CourseError(errors)
    return entry


def _marked_modules(catalog: Mapping[str, Any], counts: Mapping[str, Any], markers: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The catalog's modules as the page reads them: each lesson with its count and, when compared, its markers."""

    lesson_counts = _mapping(counts.get("lessons"))
    known_keys: set[str] = set()
    by_module: dict[str, list[Mapping[str, Any]]] = {}
    for lesson in _lessons(catalog):
        by_module.setdefault(str(lesson.get("module")), []).append(lesson)
    modules: list[dict[str, Any]] = []
    for module in catalog.get("modules") or []:
        if not isinstance(module, Mapping):
            continue
        lessons = []
        for lesson in by_module.get(str(module.get("id")), []):
            lesson_id = lesson["id"]
            found = lesson_counts.get(lesson_id) or {}
            subtopics = [sub for sub in lesson.get("subtopics") or [] if isinstance(sub, Mapping) and isinstance(sub.get("id"), str)]
            shown: dict[str, Any] = {
                "id": lesson_id, "library_id": library_id(lesson), "title": lesson.get("title", lesson_id), "has_practice": bool(lesson.get("has_practice")),
                "postings_in_90d": int(found.get("postings_in_90d", 0) or 0),
                "subtopics": [{"id": sub["id"], "title": sub.get("title", sub["id"])} for sub in subtopics],
            }
            known_keys.add(lesson_id)
            known_keys.update(f"{lesson_id}/{sub['id']}" for sub in shown["subtopics"])
            if markers is not None:
                for sub in shown["subtopics"]:
                    sub.update(_marker_entry(markers, f"{lesson_id}/{sub['id']}") or {"marker": MARKER_NEW, "evidence": None, "missing": None})
                shown["marker"] = _marker_entry(markers, lesson_id) or _lesson_marker(shown["subtopics"])
            lessons.append(shown)
        modules.append({"id": module.get("id", ""), "title": module.get("title", module.get("id", "")), "concepts": lessons})
    if markers is not None:
        stray = sorted(str(key) for key in markers if key not in known_keys)
        if stray:
            raise CourseError([f"markers[{key}]: the catalog holds no such lesson or subtopic" for key in stray[:10]])
    return modules


def _lesson_marker(subtopics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A lesson's label from its subtopics, as a generated course derives it (``learning_course``, the lesson's ``marker``)."""

    labels = [sub["marker"] for sub in subtopics]
    if not labels or all(label == MARKER_NEW for label in labels):
        return {"marker": MARKER_NEW, "evidence": None, "missing": None}
    label = MARKER_KNOWN if all(item == MARKER_KNOWN for item in labels) else MARKER_SOME
    cited = next(sub for sub in subtopics if sub["marker"] != MARKER_NEW)
    partial = next((sub for sub in subtopics if sub["marker"] == MARKER_SOME), None)
    missing = None
    if label == MARKER_SOME:
        missing = (partial or {}).get("missing") or "Some subtopics are not shown by a resume line."
    return {"marker": label, "evidence": cited["evidence"], "missing": missing}


def _subtopic_marker_lines(sub: Mapping[str, Any]) -> list[str]:
    """The personal course's 'Missing' and 'Production evidence' lines of one subtopic, without its heading and body."""

    drawn = render_subtopic({**sub, "body": []}, {}, set()).split("\n")
    return [line for line in drawn[1:] if line.startswith('<p class="')]


class _Links:
    """Where a lesson, a subtopic and a practice path are: the public learning center, or nowhere (a ``:1`` catalog)."""

    def __init__(self, site: Site | None) -> None:
        self.site = site

    def anchor(self, url: str | None, text: str, css: str = "", *, inner: str | None = None) -> str:
        """``text`` (or the escaped ``inner`` html) as a link to ``url`` that opens in a new tab; plain text when there is no site."""

        shown = esc(text) if inner is None else inner
        if url is None:
            return shown if not css else f'<span class="{css}">{shown}</span>'
        cls = f' class="{css}"' if css else ""
        return f'<a href="{esc(url)}"{cls} target="_blank" rel="noopener">{shown}</a>'



    def lesson(self, lesson: Mapping[str, Any], text: str, css: str = "", *, inner: str | None = None) -> str:
        return self.anchor(None if self.site is None else self.site.lesson_url(lesson["library_id"]), text, css, inner=inner)

    def subtopic(self, lesson: Mapping[str, Any], sub: Mapping[str, Any]) -> str:
        return self.anchor(None if self.site is None else self.site.lesson_url(lesson["library_id"], sub["id"]), str(sub["title"]))

    def practice(self, lesson: Mapping[str, Any], css: str = "") -> str:
        if self.site is None or not lesson.get("has_practice"):
            return ""
        return self.anchor(self.site.practice_url(lesson["library_id"]), "Practice", css)


def _render_sidebar(catalog: Mapping[str, Any], modules: Sequence[Mapping[str, Any]], links: _Links) -> str:
    """The course's sidebar from the catalog: this page's own anchors, then every lesson and its practice path on the site."""

    parts = ['<nav class="sidebar">']
    parts.append(f'<div class="sidebar-title"><a href="{PATHWAY_PAGE}" class="current">{esc(catalog.get("role", "Role"))}</a></div>')
    parts.append('<div class="sidebar-search">')
    parts.append('<input type="search" id="sidebar-search-input" placeholder="Search content" autocomplete="off" aria-label="Search content">')
    parts.append("</div>")
    parts.append('<ul class="sidebar-overview">')
    parts.append('<li><a href="#your-path">Your path</a></li>')
    parts.append('<li><a href="#expectations">Expectations</a></li>')
    parts.append('<li><a href="#technologies">Technologies</a></li>')
    parts.append("</ul>")
    for mi, module in enumerate(modules, start=1):
        module_title = str(module.get("title", module.get("id", "")))
        parts.append('<details class="toc-module" open>')
        parts.append(f'<summary data-module-title="{esc(module_title.lower())}">{mi}. {esc(module_title)}</summary>')
        parts.append('<ul class="module-lessons">')
        for ci, lesson in enumerate(module["concepts"], start=1):
            numbered = f"{mi}.{ci} {lesson['title']}"
            practice = links.practice(lesson, "lesson-practice-link")
            parts.append(
                f'<li data-lesson-title="{esc(numbered.lower())}">'
                + links.lesson(lesson, numbered, "lesson-link", inner=f'<span class="lesson-dot"></span><span>{esc(numbered)}</span>')
                + practice + "</li>"
            )
        parts.append("</ul>")
        parts.append("</details>")
    parts.append("</nav>")
    return "\n".join(parts)


def _render_toc(modules: Sequence[Mapping[str, Any]], links: _Links) -> str:
    out = ['<ul class="toc-modules">']
    for mi, module in enumerate(modules, start=1):
        out.append("<li>")
        out.append(f"<h3>{mi}. {esc(module.get('title', module.get('id', '')))}</h3>")
        out.append('<ul class="concepts">')
        for lesson in module["concepts"]:
            out.append(f"<li>{links.lesson(lesson, str(lesson['title']))} {postings_label(lesson)}</li>")
        out.append("</ul>")
        out.append("</li>")
    out.append("</ul>")
    return "\n".join(out)


def _render_your_path(modules: Sequence[Mapping[str, Any]], links: _Links) -> str:
    """The personal course's 'Your path' (``learning_render.render_your_path``) with the lessons linked to the site."""

    out = ['<div class="your-path" id="your-path">', "<h2>Your path</h2>"]
    any_active = False
    for mi, module in enumerate(modules, start=1):
        active_items: list[str] = []
        known_items: list[str] = []
        for ci, lesson in enumerate(module["concepts"], start=1):
            label = (lesson.get("marker") or {}).get("marker")
            line = f'<li><span class="path-number">{mi}.{ci}</span>{links.lesson(lesson, str(lesson["title"]))}</li>'
            if label == MARKER_KNOWN:
                known_items.append(line)
            elif label in (MARKER_SOME, MARKER_NEW):
                active_items.append(line)
        if not active_items and not known_items:
            continue
        out.append('<div class="your-path-module">')
        out.append(f"<h3>{mi}. {esc(module.get('title', module.get('id', '')))}</h3>")
        if active_items:
            any_active = True
            out.append(f'<ul class="path-list">{"".join(active_items)}</ul>')
        if known_items:
            out.append("<details>")
            out.append("<summary>Skim (you have production evidence)</summary>")
            out.append(f'<ul class="path-list">{"".join(known_items)}</ul>')
            out.append("</details>")
        out.append("</div>")
    if not any_active:
        out.insert(2, '<p class="muted">Nothing new or partial &mdash; see the skim lists below.</p>')
    out.append("</div>")
    return "\n".join(out)


def _resume_evidence(modules: Sequence[Mapping[str, Any]], links: _Links) -> str:
    """Per lesson the resume shows in part or whole: 'You have / Missing', then each subtopic with its line and its link."""

    out = ['<h2 id="resume-evidence">What your resume shows</h2>']
    any_lesson = False
    for mi, module in enumerate(modules, start=1):
        blocks = []
        for ci, lesson in enumerate(module["concepts"], start=1):
            if lesson["marker"]["marker"] == MARKER_NEW:
                continue
            block = [f'<p><span class="path-number">{mi}.{ci}</span>{links.lesson(lesson, str(lesson["title"]))}</p>']
            line = concept_marker_line(lesson)
            if line:
                block.append(line)
            block.append("<ul>")
            for sub in lesson["subtopics"]:
                block.append(f"<li>{links.subtopic(lesson, sub)}")
                block.extend(_subtopic_marker_lines(sub))
                block.append("</li>")
            block.append("</ul>")
            blocks.append("\n".join(block))
        if blocks:
            any_lesson = True
            out.append(f"<h3>{mi}. {esc(module['title'])}</h3>")
            out.extend(blocks)
    if not any_lesson:
        out.append('<p class="muted">No line of your resume shows a lesson of this course yet: every lesson is new.</p>')
    return "\n".join(out)


def _corpus_line(corpus: Mapping[str, Any]) -> str:
    """The plain corpus line: how many of the user's stored postings the counts are from, and the corpus's own note."""

    line = (
        f"Based on {int(corpus.get('with_text_in_window', 0) or 0)} of your stored postings in the last "
        f"{int(corpus.get('window_days', learning_corpus.WINDOW_DAYS) or 0)} days ({int(corpus.get('with_text', 0) or 0)} any date)"
    )
    scope = _scope(corpus)
    return f'<p class="muted">{line}{" &middot; " + esc(scope) if scope else ""}</p>'


def render_pathway_body(catalog: Mapping[str, Any], counts: Mapping[str, Any], markers: Mapping[str, Any] | None) -> tuple[str, dict[str, int]]:
    """The page's body and, when compared, the subtopics per label."""

    modules = _marked_modules(catalog, counts, markers)
    links = _Links(catalog_site(catalog))
    corpus = _mapping(counts.get("corpus"))
    body = [f"<h1>Your pathway: {esc(catalog.get('role', 'Role'))}</h1>"]
    body.append(
        '<p class="muted">Built on this computer and kept here: nothing on this page is shipped or sent. '
        "The lessons it links to are on the GigAI learning center and are the same for everyone.</p>"
    )
    if links.site is None:
        body.append(f'<p class="muted">{esc(NOT_LINKED)}</p>')
    body.append(_corpus_line(corpus))

    tally: dict[str, int] = {}
    if markers is None:
        body.append(f'<div class="your-path" id="your-path">\n<h2>Your path</h2>\n<p class="muted">{esc(NOT_COMPARED)}</p>\n</div>')
    else:
        labels = [sub["marker"] for module in modules for lesson in module["concepts"] for sub in lesson["subtopics"]]
        tally = {label: labels.count(label) for label in (MARKER_NEW, MARKER_SOME, MARKER_KNOWN)}
        body.append(_render_your_path(modules, links))
        body.append(_resume_evidence(modules, links))

    expectations = _mapping(counts.get("expectations"))
    body.append('<h2 id="expectations">Expectations</h2>')
    body.append(render_expectations_table({heading: list(expectations.get(key) or []) for key, heading in EXPECTATION_GROUPS}))
    technologies = _mapping(counts.get("technologies"))
    body.append('<h2 id="technologies">Technologies</h2>')
    body.append(render_technologies({str(name): list(items or []) for name, items in technologies.items() if items}))
    body.append('<h2 id="lessons">Lessons</h2>')
    body.append(_render_toc(modules, links))
    return "\n".join(body), tally


class _Audit(_LinkCollector):
    """``_LinkCollector`` that also keeps the ``rel`` of every external link."""

    def __init__(self) -> None:
        super().__init__()
        self.external_rel: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        found = dict(attrs)
        href = found.get("href")
        if tag == "a" and href and href.startswith(("http://", "https://")):
            self.external_rel.setdefault(href, []).append(found.get("rel") or "")


def audit_page(page_html: str, site: Site | None, *, page_name: str = PATHWAY_PAGE) -> tuple[list[str], int]:
    """The link and anchor audit of the pathway page.

    The page may hold exactly these links: an ``#anchor`` that is an id of the page, a link to the page itself, and
    an address on the catalog's learning center (``site.base_url``), each of those with ``target="_blank"`` and
    ``rel="noopener"``. Any other address, a page of any other name, an inline script and an ``on*=`` attribute are
    problems; so is any external link at all when the catalog names no site. Returns the sorted problems and how
    many links were checked.
    """

    own = _Audit()
    own.feed(page_html)
    problems: list[str] = []
    if own.inline_scripts:
        problems.append(f"{page_name}: {own.inline_scripts} inline <script> block(s) are not allowed")
    for attr in sorted(set(own.on_attrs)):
        problems.append(f"{page_name}: an {attr}= attribute is not allowed")
    for url, is_blank in own.external:
        if site is None or not site.owns(url):
            problems.append(f"{page_name}: external link '{url}' is not an address of the learning center")
            continue
        if not is_blank:
            problems.append(f"{page_name}: external link '{url}' has no target=\"_blank\"")
        if any("noopener" not in rel.split() for rel in own.external_rel.get(url, [])):
            problems.append(f"{page_name}: external link '{url}' has no rel=\"noopener\"")
    for path, frag in own.links:
        if path not in ("", page_name):
            problems.append(f"{page_name}: href target '{path}' is not a page this page may link to")
        elif frag and frag not in own.ids:
            problems.append(f"{page_name}: anchor '#{frag}' does not resolve to an id in '{page_name}'")
    return sorted(set(problems)), len(own.links) + len(own.external)


def _footer(site: Site | None) -> str:
    """The footer line: the learning center and the site version the catalog was made for, then 'built on this computer'."""

    named = CENTER_NAME if site is None else f"{CENTER_NAME} &middot; site version {esc(site.site_version)}"
    return f"{named} &middot; {FOOTER_LOCAL}"


def build_pathway_page(
    catalog: Mapping[str, Any], counts: Mapping[str, Any], markers: Mapping[str, Any] | None, out_path: Path, asset_base: str,
) -> PathwayPage:
    """Write the one local page of a shipped course to ``out_path``; the same inputs give the same bytes.

    ``catalog`` is the shipped ``catalog.json``; ``counts`` is :func:`count_postings`'s answer; ``markers`` is
    ``None`` until the resume was compared (see the module text). ``asset_base`` is where the page finds
    ``course.css`` and the scripts (``/learning/_assets``). Nothing but the catalog is read: the lessons are on the
    learning center and every link to them is an address there (:func:`audit_page`).

    Raises ``CourseError`` with every problem found (a marker that is not one, a link the audit does not allow, a
    glyph the course refuses, a page over the size cap); nothing is written then, and a page already at
    ``out_path`` stays as it was.
    """

    role_key = _role_key(catalog)
    if counts.get("role_key") != role_key:
        raise CourseError([f"the catalog is of {role_key}; the counts must be of the same course"])
    base = clean_asset_base(asset_base)
    clean_markers = _clean_tree(dict(markers)) if markers is not None else None
    body, tally = render_pathway_body(catalog, counts, clean_markers)
    site = catalog_site(catalog)
    modules = _marked_modules(catalog, counts, clean_markers)

    role = str(catalog.get("role", "Role"))
    page = (
        PAGE_HEAD.format(
            title=esc(f"Your pathway: {role}"), asset_rel=base, style=STYLE_LINK.format(asset_rel=base), sidebar=_render_sidebar(catalog, modules, _Links(site)),
            breadcrumb=f'<nav class="breadcrumb">{esc(role)}</nav>',
        )
        + body
        + PAGE_TAIL.format(asset_rel=base, footer=_footer(site))
    )
    problems = check_glyphs(page, PATHWAY_PAGE)
    encoded = page.encode("utf-8")
    if len(encoded) >= MAX_HTML_BYTES:
        problems.append(f"{PATHWAY_PAGE}: page is {len(encoded)} bytes, at or over the {MAX_HTML_BYTES}-byte cap")
    link_problems, checked = audit_page(page, site)
    problems.extend(link_problems)
    if problems:
        raise CourseError(sorted(problems))
    atomic_write(Path(out_path), encoded)
    return PathwayPage(path=Path(out_path), size_bytes=len(encoded), links_checked=checked, compared=markers is not None, tally=tally)


# --- 3. the record ---------------------------------------------------------------------------------------------------


def local_record_path(learning: Path, role_key: str) -> Path:
    """Where the record of ``role_key``'s local page is, below the project's learning folder (``learning_store.learning_dir``)."""

    if not learning_shipped_store.is_role_key(role_key):
        raise LearningError("invalid_value", "a role key is lower-case words joined by dashes, at most 64 characters")
    return Path(learning) / LOCAL_DIRNAME / f"{role_key}.json"


def _master_ref(master: Mapping[str, Any] | None) -> dict[str, str | None] | None:
    """The revision and digest of a master, and nothing else of it."""

    if master is None:
        return None
    revision_id, digest = master.get("revision_id"), master.get("content_sha256")
    return {"revision_id": revision_id if isinstance(revision_id, str) else None, "content_sha256": digest if isinstance(digest, str) else None}


def _corpus_numbers(corpus: Mapping[str, Any]) -> dict[str, object]:
    numbers: dict[str, object] = {key: int(corpus.get(key, 0) or 0) for key in ("postings", "with_text", "with_text_in_window", "read")}
    numbers["basis"] = str(corpus.get("basis", ""))
    return numbers


def write_local_record(
    home_root: Path, target: Path, role_key: str, *, catalog: Mapping[str, Any], counts: Mapping[str, Any],
    master: Mapping[str, Any] | None = None, now: datetime | None = None, learning: Path | None = None,
) -> Path:
    """Record what the local page of ``role_key`` was built from; returns the record's path.

    ``master`` is the master revision the page's markers came from (``revision_id`` and ``content_sha256``, as
    the master's own revision gives them), or ``None`` when the resume was not compared. The record holds dates,
    digests and numbers: no line of a resume, no posting sentence, no company.
    """

    if _role_key(catalog) != role_key or counts.get("role_key") != role_key:
        raise LearningError("invalid_value", "the catalog and the counts are not of this course")
    folder = Path(learning) if learning is not None else learning_store.learning_dir(home_root, target)
    lessons = _mapping(counts.get("lessons"))
    moment = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    record = {
        "schema": RECORD_SCHEMA,
        "role_key": role_key,
        "built_at": moment.isoformat().replace("+00:00", "Z"),
        "gigai_version": learning_shipped_store.gigai_version(),
        "catalog_digest": catalog_digest(catalog),
        "master": _master_ref(master),
        "resume_compared": master is not None,
        "corpus": _corpus_numbers(_mapping(counts.get("corpus"))),
        "concepts": int(counts.get("concepts", 0) or 0),
        "concepts_named": int(counts.get("concepts_named", 0) or 0),
        "lessons": len(lessons),
        "lessons_named": sum(1 for found in lessons.values() if isinstance(found, Mapping) and (found.get("postings_in_90d") or found.get("postings_any_date"))),
    }
    path = local_record_path(folder, role_key)
    atomic_write(path, (json.dumps(record, sort_keys=True, indent=1) + "\n").encode("utf-8"))
    return path


def read_local_record(learning: Path, role_key: str) -> dict[str, Any] | None:
    """The record of ``role_key``'s local page, or ``None`` (no record, a link, not a record of this course)."""

    try:
        path = local_record_path(learning, role_key)
    except LearningError:
        return None
    if path.is_symlink() or path.parent.is_symlink():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA or record.get("role_key") != role_key:
        return None
    return record


# --- 4. the state ----------------------------------------------------------------------------------------------------


def stale_reasons(
    record: Mapping[str, Any], *, catalog: Mapping[str, Any] | None = None, master: Any = NOT_GIVEN, corpus: Mapping[str, Any] | None = None,
) -> list[str]:
    """Why a page built as ``record`` says is no longer current; ``[]`` when nothing that was looked at changed (see :func:`state`)."""

    reasons = []
    if record.get("gigai_version") != learning_shipped_store.gigai_version():
        reasons.append(STALE_VERSION)
    if catalog is not None and record.get("catalog_digest") != catalog_digest(catalog):
        reasons.append(STALE_CATALOG)
    if master is not NOT_GIVEN and record.get("master") is not None and record.get("master") != _master_ref(master):
        reasons.append(STALE_MASTER)
    if corpus is not None:
        then = _mapping(record.get("corpus"))
        before, after = int(then.get("read", 0) or 0), int(corpus.get("read", 0) or 0)
        if then.get("basis") != corpus.get("basis") or abs(after - before) * 100 > before * POSTINGS_DRIFT_PERCENT:
            reasons.append(STALE_POSTINGS)
    return reasons


def _shipped_catalog(role_key: str) -> dict[str, Any] | None:
    course = learning_shipped_store.get_course(role_key)
    if course is None:
        return None
    try:
        catalog = json.loads((course.data_dir / CATALOG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return catalog if isinstance(catalog, dict) else None


def state(
    home_root: Path, target: Path, role_key: str, *, catalog: Mapping[str, Any] | None = None, master: Any = NOT_GIVEN,
    corpus: Mapping[str, Any] | None = None, learning: Path | None = None,
) -> dict[str, object]:
    """``{"state", "built_at", "reasons"}`` of the local pathway page of ``role_key``.

    THE RULE.

    - ``not_built``: there is no ``pathway.html`` (``learning_shipped_store.pathway_page``).
    - ``stale``: the page has a record and one of these changed since (``reasons`` names each):
      ``gigai_version``, the installed GigAI is another version than the one that built the page;
      ``catalog``, the shipped catalog's digest differs (``catalog``; when not given, the shipped course of
      ``role_key`` is read, and a course that no longer ships is not a reason);
      ``master``, the page holds a resume comparison and the master is at another revision or digest now, or is
      gone (only when the caller passes ``master``: the current revision's ``revision_id`` and ``content_sha256``,
      or ``None`` for no master; a page built WITHOUT a comparison holds nothing of a resume and is never stale
      for this reason);
      ``postings``, the postings read differ from the recorded count by more than 10 percent of it, or the set
      read changed between the last 90 days and every date (only when the caller passes ``corpus``, the
      ``corpus`` of a fresh :func:`count_postings`; from 0 recorded, any posting is a change).
    - ``built``: otherwise. A page with no record (one this module did not write) is ``built`` with its file
      time: there is nothing to compare it with.

    Without ``master`` and ``corpus`` the answer costs two small file reads: no resume is read and no posting is
    counted. ``built_at`` is the record's, else the file's time.
    """

    try:
        folder = Path(learning) if learning is not None else learning_store.learning_dir(home_root, target)
    except LearningError:
        return {"state": PAGE_NOT_BUILT, "built_at": None, "reasons": []}
    page = learning_shipped_store.pathway_page_in(folder, role_key)
    if page is None:
        return {"state": PAGE_NOT_BUILT, "built_at": None, "reasons": []}
    record = read_local_record(folder, role_key)
    if record is None:
        built = datetime.fromtimestamp(page.stat().st_mtime, tz=UTC).replace(microsecond=0)
        return {"state": PAGE_BUILT, "built_at": built.isoformat().replace("+00:00", "Z"), "reasons": []}
    reasons = stale_reasons(record, catalog=catalog if catalog is not None else _shipped_catalog(role_key), master=master, corpus=corpus)
    return {"state": PAGE_STALE if reasons else PAGE_BUILT, "built_at": record.get("built_at"), "reasons": reasons}


# --- the whole build -------------------------------------------------------------------------------------------------


def build_local_page(
    home_root: Path, target: Path, shipped: learning_shipped_store.ShippedCourse, *, markers: Mapping[str, Any] | None = None,
    master: Mapping[str, Any] | None = None, now: datetime | None = None,
) -> tuple[PathwayPage, dict[str, Any]]:
    """Count, build and record the local page of one shipped course; returns the page and the counts.

    Only the catalog is read. ``markers`` and ``master`` go together: the checked answer of the resume comparison
    and the master revision it was made from, or neither. The record is written last, after the page.
    """

    if (markers is None) != (master is None):
        raise LearningError("invalid_value", "the markers and the master revision they came from are given together")
    catalog = learning_shipped_store.read_catalog(shipped)
    counts = count_postings(home_root, target, catalog, now=now)
    out_path = learning_shipped_store.pathway_page_path(Path(home_root), Path(target), shipped.role_key)
    page = build_pathway_page(catalog, counts, markers, out_path, learning_shipped_store.ASSET_BASE)
    write_local_record(home_root, target, shipped.role_key, catalog=catalog, counts=counts, master=master, now=now)
    return page, counts


__all__ = [
    "COUNTS_SCHEMA",
    "NOT_COMPARED",
    "NOT_GIVEN",
    "POSTINGS_DRIFT_PERCENT",
    "RECORD_SCHEMA",
    "PathwayPage",
    "audit_page",
    "build_local_page",
    "build_pathway_page",
    "catalog_digest",
    "count_postings",
    "local_record_path",
    "read_local_record",
    "render_pathway_body",
    "stale_reasons",
    "state",
    "write_local_record",
]
