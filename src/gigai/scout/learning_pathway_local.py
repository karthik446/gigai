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
   role, the corpus line, 'Your path', Expectations and Technologies with percentages and bars, and the lessons
   with their 'N postings' chips. Every link to a shipped page and anchor is checked against the rendered course
   (:func:`audit_page`) before the file is written. The same inputs give the same bytes: no date is on the page.
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
folder, not the page cache), served in the same URL folder as the shipped pages, so its links are plain file names.

No model call, no network. Examples of posting sentences with their company are on the page (it is local and
private) and nowhere else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

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
    concept_page_filename,
    esc,
    render_breadcrumb,
    render_expectations_table,
    render_sidebar,
    render_subtopic,
    render_technologies,
    render_toc,
    render_your_path,
    sanitize_course,
    subtopic_anchor,
    validate_marker,
)
from .learning_shipped import CATALOG_FILENAME, CATALOG_SCHEMA_VERSION, COURSE_FILENAME
from .learning_shipped_store import PAGE_BUILT, PAGE_NOT_BUILT, PAGE_STALE
from .learning_store import LearningError

COUNTS_SCHEMA = "scout-learning-local-counts:1"
RECORD_SCHEMA = "scout-learning-local-page:1"
LOCAL_DIRNAME = "local"
FOOTER_LOCAL = "built on this computer"
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
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _role_key(catalog: Mapping[str, Any]) -> str:
    if not isinstance(catalog, Mapping) or catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise LearningError("invalid_value", f"the catalog is not a {CATALOG_SCHEMA_VERSION}")
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
    #: Links to shipped pages and to anchors that were checked against the rendered course, all found.
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


def _marked_modules(course: Mapping[str, Any], counts: Mapping[str, Any], markers: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The course's modules as the personal templates read them: each lesson with its count and, when compared, its markers."""

    lesson_counts = _mapping(counts.get("lessons"))
    known_keys: set[str] = set()
    modules: list[dict[str, Any]] = []
    for module in course.get("modules") or []:
        lessons = []
        for lesson in module.get("concepts") or []:
            lesson_id = lesson["id"]
            found = lesson_counts.get(lesson_id) or {}
            shown: dict[str, Any] = {
                "id": lesson_id, "title": lesson.get("title", lesson_id), "postings_in_90d": int(found.get("postings_in_90d", 0) or 0),
                "subtopics": [{"id": sub["id"], "title": sub.get("title", sub["id"])} for sub in lesson.get("subtopics") or []],
            }
            known_keys.add(lesson_id)
            known_keys.update(f"{lesson_id}/{sub['id']}" for sub in shown["subtopics"])
            if markers is not None:
                for sub in shown["subtopics"]:
                    sub.update(_marker_entry(markers, f"{lesson_id}/{sub['id']}") or {"marker": MARKER_NEW, "evidence": None, "missing": None})
                shown["marker"] = _marker_entry(markers, lesson_id) or _lesson_marker(shown["subtopics"])
            lessons.append(shown)
        modules.append({"id": module.get("id", ""), "title": module.get("title", module.get("id", "")), "intro": module.get("intro", ""), "concepts": lessons})
    if markers is not None:
        stray = sorted(str(key) for key in markers if key not in known_keys)
        if stray:
            raise CourseError([f"markers[{key}]: the course holds no such lesson or subtopic" for key in stray[:10]])
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


def _resume_evidence(modules: Sequence[Mapping[str, Any]]) -> str:
    """Per lesson the resume shows in part or whole: 'You have / Missing', then each subtopic with its line and its link."""

    out = ['<h2 id="resume-evidence">What your resume shows</h2>']
    any_lesson = False
    for mi, module in enumerate(modules, start=1):
        blocks = []
        for ci, lesson in enumerate(module["concepts"], start=1):
            if lesson["marker"]["marker"] == MARKER_NEW:
                continue
            href = concept_page_filename(lesson["id"])
            block = [f'<p><span class="path-number">{mi}.{ci}</span><a href="{esc(href)}">{esc(lesson["title"])}</a></p>']
            line = concept_marker_line(lesson)
            if line:
                block.append(line)
            block.append("<ul>")
            for sub in lesson["subtopics"]:
                block.append(f'<li><a href="{esc(href)}#{esc(subtopic_anchor(sub["id"]))}">{esc(sub["title"])}</a>')
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


def render_pathway_body(catalog: Mapping[str, Any], course: Mapping[str, Any], counts: Mapping[str, Any], markers: Mapping[str, Any] | None) -> tuple[str, dict[str, int]]:
    """The page's body and, when compared, the subtopics per label."""

    modules = _marked_modules(course, counts, markers)
    corpus = _mapping(counts.get("corpus"))
    body = [f"<h1>Your pathway: {esc(catalog.get('role', 'Role'))}</h1>"]
    body.append(
        '<p class="muted">Built on this computer and kept here: nothing on this page is shipped or sent. '
        "The lessons it links to are the same for everyone.</p>"
    )
    body.append(_corpus_line(corpus))

    tally: dict[str, int] = {}
    if markers is None:
        body.append(f'<div class="your-path" id="your-path">\n<h2>Your path</h2>\n<p class="muted">{esc(NOT_COMPARED)}</p>\n</div>')
    else:
        labels = [sub["marker"] for module in modules for lesson in module["concepts"] for sub in lesson["subtopics"]]
        tally = {label: labels.count(label) for label in (MARKER_NEW, MARKER_SOME, MARKER_KNOWN)}
        body.append(render_your_path(modules))
        body.append(_resume_evidence(modules))

    expectations = _mapping(counts.get("expectations"))
    body.append('<h2 id="expectations">Expectations</h2>')
    body.append(render_expectations_table({heading: list(expectations.get(key) or []) for key, heading in EXPECTATION_GROUPS}))
    technologies = _mapping(counts.get("technologies"))
    body.append('<h2 id="technologies">Technologies</h2>')
    body.append(render_technologies({str(name): list(items or []) for name, items in technologies.items() if items}))
    body.append('<h2 id="lessons">Lessons</h2>')
    body.append(render_toc(modules))
    return "\n".join(body), tally


def audit_page(page_html: str, site_dir: Path, *, page_name: str = PATHWAY_PAGE) -> tuple[list[str], int]:
    """The link and anchor audit of the pathway page against the rendered shipped course in ``site_dir``.

    Every link to a page must name a plain ``.html`` file of that folder; every ``#anchor`` an id of the page it
    points at (this page's own for a bare ``#anchor`` or a link to itself). An inline script, an ``on*=``
    attribute and an external link with no ``target="_blank"`` are problems too, as in the course's own audit.
    Returns the sorted problems and how many links were checked.
    """

    own = _LinkCollector()
    own.feed(page_html)
    problems: list[str] = []
    if own.inline_scripts:
        problems.append(f"{page_name}: {own.inline_scripts} inline <script> block(s) are not allowed")
    for attr in sorted(set(own.on_attrs)):
        problems.append(f"{page_name}: an {attr}= attribute is not allowed")
    for url, is_blank in own.external:
        if not is_blank:
            problems.append(f"{page_name}: external link '{url}' has no target=\"_blank\"")
    ids_by_page: dict[str, set[str] | None] = {"": own.ids, page_name: own.ids}
    for path, frag in own.links:
        if path not in ids_by_page:
            target = Path(site_dir) / path
            plain = "/" not in path and "\\" not in path and path.endswith(".html") and not path.startswith(".")
            if plain and not target.is_symlink() and target.is_file():
                collector = _LinkCollector()
                collector.feed(target.read_text(encoding="utf-8"))
                ids_by_page[path] = collector.ids
            else:
                ids_by_page[path] = None
        ids = ids_by_page[path]
        if ids is None:
            problems.append(f"{page_name}: href target '{path}' does not resolve to a page of the shipped course")
        elif frag and frag not in ids:
            problems.append(f"{page_name}: anchor '#{frag}' does not resolve to an id in '{path or page_name}'")
    return sorted(set(problems)), len(own.links)


def build_pathway_page(
    catalog: Mapping[str, Any], course: Mapping[str, Any], counts: Mapping[str, Any], markers: Mapping[str, Any] | None,
    out_path: Path, asset_base: str, *, site_dir: Path | None = None,
) -> PathwayPage:
    """Write the one local page of a shipped course to ``out_path``; the same inputs give the same bytes.

    ``catalog`` and ``course`` are the shipped ``catalog.json`` and ``course.json``; ``counts`` is
    :func:`count_postings`'s answer; ``markers`` is ``None`` until the resume was compared (see the module text).
    ``asset_base`` is where the page finds ``course.css`` and the scripts (``/learning/_assets``). ``site_dir`` is
    the folder of the RENDERED shipped course the links are checked against (default: ``out_path``'s folder); the
    page is served in the same URL folder as those pages, so every link is a plain file name.

    Raises ``CourseError`` with every problem found (a marker that is not one, a link or an anchor the course does
    not have, a glyph the course refuses, a page over the size cap); nothing is written then, and a page already
    at ``out_path`` stays as it was.
    """

    role_key = _role_key(catalog)
    if course.get("role_key") != role_key or counts.get("role_key") != role_key:
        raise CourseError([f"the catalog is of {role_key}; the course and the counts must be of the same course"])
    base = clean_asset_base(asset_base)
    clean_course, _removed = sanitize_course({"role": catalog.get("role", "Role"), "modules": list(course.get("modules") or [])})
    clean_markers = _clean_tree(dict(markers)) if markers is not None else None
    body, tally = render_pathway_body(catalog, clean_course, counts, clean_markers)

    has_practice = {lesson["id"]: {} for lesson in _lessons(catalog) if lesson.get("has_practice")}
    sidebar = render_sidebar(clean_course, clean_course["modules"], PATHWAY_PAGE, has_practice, shipped=True)
    role = str(clean_course["role"])
    page = (
        PAGE_HEAD.format(
            title=esc(f"Your pathway: {role}"), asset_rel=base, style=STYLE_LINK.format(asset_rel=base), sidebar=sidebar,
            breadcrumb=render_breadcrumb(PATHWAY_PAGE, role),
        )
        + body
        + PAGE_TAIL.format(asset_rel=base, footer=FOOTER_LOCAL)
    )
    problems = check_glyphs(page, PATHWAY_PAGE)
    encoded = page.encode("utf-8")
    if len(encoded) >= MAX_HTML_BYTES:
        problems.append(f"{PATHWAY_PAGE}: page is {len(encoded)} bytes, at or over the {MAX_HTML_BYTES}-byte cap")
    link_problems, checked = audit_page(page, Path(site_dir) if site_dir is not None else Path(out_path).parent)
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

    The shipped pages are rendered first when they are not (``ensure_rendered``), since the page's links are
    checked against them. ``markers`` and ``master`` go together: the checked answer of the resume comparison and
    the master revision it was made from, or neither. The record is written last, after the page.
    """

    if (markers is None) != (master is None):
        raise LearningError("invalid_value", "the markers and the master revision they came from are given together")
    try:
        catalog = json.loads((shipped.data_dir / CATALOG_FILENAME).read_text(encoding="utf-8"))
        course = json.loads((shipped.data_dir / COURSE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LearningError("invalid_value", f"the shipped course {shipped.role_key} cannot be read: {exc}") from exc
    counts = count_postings(home_root, target, catalog, now=now)
    site = learning_shipped_store.ensure_rendered(Path(home_root), shipped)
    out_path = learning_shipped_store.pathway_page_path(Path(home_root), Path(target), shipped.role_key)
    page = build_pathway_page(catalog, course, counts, markers, out_path, learning_shipped_store.ASSET_BASE, site_dir=site)
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
