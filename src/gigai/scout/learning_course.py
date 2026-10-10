"""0.1.11.10 Part B, packet G2: from the counted concepts of a role to its ``course.json`` (pathway steps 6, 7, 8, 10).

G1 (``learning_corpus``) counts what a role's stored postings ask for. This module turns those counts into the
course the renderer (G3, ``learning_render``) draws. Four steps, each a function of plain data to plain data:

6. CURRICULUM (one model call, one retry): the counted concepts become 6 to 9 modules of 3 to 5 lessons (25 to
   35 lessons), each lesson mapped to concept ids or marked ``curriculum`` (field-standard, no posting names
   it), the expectations and technology groups of the front page, and 2 to 5 candidate source URLs per lesson
   (``data/instructions/learning_curriculum.md``; :func:`parse_curriculum` validates the answer).
7. SOURCES (no model unless a lesson is left short): every candidate URL is fetched under the outbound
   policy of ``learning_fetch`` and kept only when it answered 200 with a title and is not a site's home page.
   The page's anchorable headings are kept with it. Lessons left with no source, and lessons whose MAIN TOOL
   (:func:`choose_main_tool`) has fewer than two verified pages of its own, get ONE more model call asking for
   three new URLs each (it names the tool and the URLs that did not answer); a lesson still without any source
   is dropped (``no_verified_source``).
8. LESSON TEXT (one model call per module, one retry): ``data/instructions/learning_lessons.md`` and the
   code-side checks of :func:`fix_module`: sources by index into the verified list only, a diagram that starts
   with a Mermaid diagram word or none, related ids that exist, and the known/some/new markers. A module whose
   two attempts fail is dropped (``module_text_failed``).
10. COURSE JSON (no model): :func:`assemble_course` builds ``course.json`` in the renderer's shape with
    ``dropped[]``, and :func:`course_status` applies the 60 PERCENT rule.

THE RESUME IS ALWAYS THE REFERENCE for known/some/new. It is not an option. The master resume's lines reach
exactly one prompt builder, :func:`lessons_prompt` (step 8), as ``id | text`` lines through
``resume_privacy.guard_private`` (emails, phone numbers and links out, an address line dropped). No other
prompt of this module is given a resume line (steps 6 and 7 do not take the argument). A KNOWN or SOME marker
needs a line id that exists and a quote that is in that line as sent; a skills-list id (``s-...``) is SOME at
most; anything else is NEW. A master with no lines makes every lesson NEW and is not an error.

NO URL A MODEL WROTE IS USED. A candidate URL is a request to fetch; what a lesson links to is the address
that answered, chosen by index. :func:`assemble_course` refuses a course holding any other ``url``.

WHAT REACHES ``course.json`` is cleaned on the way in: invisible Unicode and glyphs the renderer refuses are
taken out of every string a model returned (:func:`clean_glyphs`), and every string except a verified source
``url`` passes the contact check (``story_bank.personal_info_in_answer``): a link, an email address or a phone
number is redacted, and a string that still fails is left out (or fails the module when it is required).

Every step's result can be kept in a work folder (:class:`CourseWork`: ``atomic_write``, and a stored result
is read back only when it still validates), so a run that stopped resumes at the first step without a result
(:func:`run_course_steps`). Every model call is metered as kind ``learning``; a lesson-writing call records
``items`` = the lessons it writes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from .learning_corpus import LearningCorpusError, MeteredRoleModel, _error_text, _json_object, _render, _template
from .learning_fetch import STOP_BUDGET, FetchStopped, clean_text
from .learning_store import LearningError
from .resume_privacy import guard_private, redact_inline
from .untrusted_text import fence_untrusted_posting

SCHEMA_CURRICULUM = "scout-learning-curriculum:1"
SCHEMA_SOURCES = "scout-learning-sources:1"
SCHEMA_MODULE = "scout-learning-module:1"
SCHEMA_COURSE = "scout-learning-course:1"

#: How every prompt that wants JSON back opens (spike 9c: without it a model plans or asks instead of answering).
JSON_OPENER = (
    "Answer now with ONE JSON object. Do not plan, do not explore, do not use tools, do not ask questions. "
    "Your whole reply is the JSON."
)

#: Step 6 bounds.
MODULES_MIN, MODULES_MAX = 6, 9
MODULE_LESSONS_MIN, MODULE_LESSONS_MAX = 3, 5
LESSONS_MIN, LESSONS_MAX = 25, 35
CANDIDATES_MIN, CANDIDATES_MAX = 2, 5
RETRY_CANDIDATES_MAX = 3
TECH_GROUPS_MAX = 12
TITLE_CHARS_MAX = 120
SOURCE_TITLE_CHARS_MAX = 160
URL_CHARS_MAX = 500
ID_CHARS_MAX = 48
SOURCE_KINDS = ("doc", "guide", "tutorial", "paper", "book", "article", "course", "repo", "spec")

#: Step 8 bounds.
SUBTOPICS_MAX = 6
CODE_BLOCKS_MAX = 3
CODE_LINES_MAX = 40
TECHNOLOGIES_MAX = 12
EVIDENCE_CHARS_MAX = 100
EVIDENCE_CHARS_MIN = 3
EXAMPLES_PER_LESSON = 2
EXAMPLE_CHARS_MAX = 160
MERMAID_WORDS = ("flowchart", "graph", "sequenceDiagram")
MARKER_KNOWN, MARKER_SOME, MARKER_NEW = "KNOWN", "SOME", "NEW"
#: The id prefix of a master line that is a skills list (``master_resume._ITEM_PREFIX``).
SKILLS_ID_PREFIX = "s-"

#: Step 10: a course is done when at least this share of the planned lessons passed every check.
DONE_PERCENT = 60
STATUS_DONE, STATUS_FAILED = "done", "failed"
TOO_LITTLE_VERIFIED = "too_little_verified"
NO_VERIFIED_SOURCE = "no_verified_source"
MODULE_TEXT_FAILED = "module_text_failed"

#: G7e: the pages of its own documentation a lesson's main tool needs among the lesson's verified sources.
MAIN_TOOL_PAGES_MIN = 2

#: G7c: the curriculum call (25-35 lessons plus candidate sources, thinking included) and each module's lesson-text
#: call (~140k characters of prompt) legitimately ran 60-120 s in the POC; both outlast the adapter's 120 s default.
LONG_CALL_TIMEOUT_SECONDS = 600.0

_CURRICULUM_RESOURCE = ("scout", "data", "instructions", "learning_curriculum.md")
_LESSONS_RESOURCE = ("scout", "data", "instructions", "learning_lessons.md")
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_LANG = re.compile(r"[A-Za-z0-9+#.-]{1,20}")


class LearningCourseError(LearningError):
    """A course step could not be done; ``code`` is the API/CLI error code."""


class LearningCourseStopped(LearningCourseError):
    """The cancel hook said stop before a model call."""

    def __init__(self) -> None:
        super().__init__("cancelled", "the course was stopped")


class _Invalid(ValueError):
    """A model answer that the checks cannot repair: the reason goes into the one retry."""


class CourseModel(Protocol):
    """What the steps need of a model: an answer to a prompt (``items``: what the call is for), and "that was unusable".

    ``timeout_seconds`` (G7c) overrides the adapter's own default for this one call; ``None`` keeps it.
    """

    def ask(self, prompt: str, *, items: int = 1, timeout_seconds: float | None = None) -> str: ...

    def invalid_output(self) -> None: ...


class MeteredCourseModel(MeteredRoleModel):
    """G1's metered model (the user's configured target, kind ``learning``) with ``items`` on a call's record."""

    def ask(self, prompt: str, *, items: int = 1, timeout_seconds: float | None = None) -> str:  # type: ignore[override]
        from ..adapters.port import ModelInvocationError

        self.calls += 1
        try:
            result = self._meter.invoke(
                self._binding.port,
                self._binding.request(role="reviewer", prompt=prompt, timeout_seconds=timeout_seconds),
                items=max(1, items),
            )
        except ModelInvocationError as exc:
            raise LearningCorpusError("model_unavailable", str(exc)) from exc
        return str(getattr(result, "output_text", "") or "")


# ---------------------------------------------------------------------------
# Text that may reach course.json: glyphs, contact shapes
# ---------------------------------------------------------------------------

#: Symbols a model writes that the renderer's glyph gate refuses, and what stands in for each.
_GLYPHS = {
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>", "≥": ">=", "≤": "<=", "≠": "!=",
    "≈": "~", "−": "-", "‐": "-", "‑": "-", "‒": "-", "―": "-", "•": "-",
    "●": "-", "◦": "-", "✓": "yes", "✔": "yes", "✗": "no", "✘": "no",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", "′": "'", "″": '"',
}
_ALLOWED_EXTRA = frozenset("‘’“”–—…")


def clean_glyphs(text: str) -> str:
    """``text`` as the renderer accepts it: invisible Unicode out, known symbols as ASCII, any other glyph dropped.

    What is left is ASCII, Latin-1 (U+00A0 to U+00FF) and the typographic quotes, dashes and ellipsis, which is
    exactly what ``learning_render.check_glyphs`` lets a page hold. A newline and a tab stay; other control
    characters go.
    """

    from .learning_render import strip_invisible

    kept: list[str] = []
    for char in strip_invisible(text)[0]:
        char = _GLYPHS.get(char, char)
        code = ord(char[0]) if len(char) == 1 else 0
        if len(char) == 1 and not (32 <= code < 127 or char in "\n\t" or 0xA0 <= code <= 0xFF or char in _ALLOWED_EXTRA):
            continue
        kept.append(char)
    return "".join(kept)


def _clean_tree(value: Any) -> Any:
    """Every string (keys too) of a decoded model answer through :func:`clean_glyphs`."""

    if isinstance(value, str):
        return clean_glyphs(value)
    if isinstance(value, list):
        return [_clean_tree(item) for item in value]
    if isinstance(value, Mapping):
        return {clean_glyphs(key) if isinstance(key, str) else key: _clean_tree(item) for key, item in value.items()}
    return value


def contact_shapes(text: str) -> list[str]:
    """What looks like contact data in ``text`` (``story_bank.personal_info_in_answer``); ``[]`` when nothing does."""

    from .story_bank import personal_info_in_answer

    return personal_info_in_answer(text)


def scrub_contact(text: str) -> str | None:
    """``text`` when it holds no contact shape; else with links, emails and phone numbers redacted; ``None`` when that is not enough."""

    if not contact_shapes(text):
        return text
    scrubbed = "\n".join(redact_inline(line) for line in text.split("\n"))
    return scrubbed if scrubbed.strip() and not contact_shapes(scrubbed) else None


def _one_line(value: object, limit: int) -> str:
    """A title or a name: one line, contact shapes redacted; ``""`` when it is not usable."""

    if not isinstance(value, str):
        return ""
    text = scrub_contact(clean_text(clean_glyphs(value)))
    return text if text and len(text) <= limit else ""


def _prose(value: object) -> str:
    """A paragraph: ends trimmed, contact shapes redacted; ``""`` when it is not a string or cannot be made clean."""

    if not isinstance(value, str) or not value.strip():
        return ""
    return (scrub_contact(value.strip()) or "").strip()


# ---------------------------------------------------------------------------
# Step 6: the curriculum
# ---------------------------------------------------------------------------


def _kept(counted_concepts: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [concept for concept in counted_concepts if concept.get("kept", True) and isinstance(concept.get("id"), str)]


#: What a concept line says after the category of a concept that is not technical (G1's ``technical`` flag is false).
BEHAVIOUR_MARK = " (behaviour, not a lesson)"


def _is_technical(concept: Mapping[str, object]) -> bool:
    """Whether a counted concept is something a course can teach: G1's ``technical`` flag (a concept without the flag counts as technical)."""

    return concept.get("technical") is not False


def _concept_line(concept: Mapping[str, object]) -> str:
    examples = concept.get("examples")
    example = ""
    if isinstance(examples, list) and examples and isinstance(examples[0], Mapping):
        example = clean_text(str(examples[0].get("phrase", "")))[:EXAMPLE_CHARS_MAX]
    counts = f"{concept.get('count_in90d', 0)} of {concept.get('n_in90d', 0)} in 90 days, {concept.get('count_all', 0)} of {concept.get('n_all', 0)} any date"
    category = f"{concept.get('category', '')}{'' if _is_technical(concept) else BEHAVIOUR_MARK}"
    return f"{concept['id']} | {clean_text(str(concept.get('display', '')))} | {category} | {counts} | {example}"


def curriculum_prompt(role_text: str, counted_concepts: Sequence[Mapping[str, object]], validation_error: BaseException | str | None = None) -> str:
    """The curriculum prompt. It is built from the role and the counted concepts only: no resume line is an input."""

    lines = "\n".join(_concept_line(concept) for concept in _kept(counted_concepts)) or "(no concept was counted)"
    return _render(
        _template(_CURRICULUM_RESOURCE),
        {
            "plan": "", "retry": None, "role": role_text, "concepts": fence_untrusted_posting(lines), "lessons": None,
            "validation_error": _error_text(validation_error),
        },
    )


def usable_url(url: object) -> str | None:
    """A candidate source URL GigAI may fetch, without its fragment; ``None`` when it may not be asked at all.

    ``https`` at a public-looking host with no port and no login (``outbound_guard.public_looking_host``), ASCII,
    no whitespace, at most 500 characters, and a page under the site, not its home page.
    """

    from .find_jobs.outbound_guard import public_looking_host
    from .learning_render import is_bare_domain_url

    if not isinstance(url, str):
        return None
    url = url.strip().split("#", 1)[0]
    if not url or len(url) > URL_CHARS_MAX or not url.isascii() or re.search(r"[\s\"'<>\\^`{|}]", url):
        return None
    if public_looking_host(url) is None or is_bare_domain_url(url):
        return None
    return url


def _candidates(value: object, limit: int, tried: Iterable[str] = ()) -> list[dict[str, str]]:
    """The usable candidate sources of a model answer, each URL once, at most ``limit``. An unusable one is left out."""

    found: list[dict[str, str]] = []
    seen = set(tried)
    for item in value if isinstance(value, list) else []:
        url = usable_url(item.get("url")) if isinstance(item, Mapping) else None
        if url is None or url in seen:
            continue
        seen.add(url)
        kind = item.get("kind")
        found.append({"title": _one_line(item.get("title"), SOURCE_TITLE_CHARS_MAX), "url": url, "kind": kind if kind in SOURCE_KINDS else "doc"})
        if len(found) >= limit:
            break
    return found


def parse_curriculum(raw: str, role_text: str, counted_concepts: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """The curriculum of a model answer, or ``curriculum_invalid`` naming the first thing wrong.

    Refused: no JSON object; fewer than 6 or more than 9 modules; a module without 3 to 5 lessons; fewer than 25
    or more than 35 lessons; a module or lesson id that is not kebab-case or is used twice; a title that is
    empty, too long or holds contact data; a concept id that was not counted; a lesson with no concept that is
    not marked ``curriculum``; a lesson whose concepts are ALL behaviours (G1's ``technical`` flag is false for
    each: mentoring, ownership, design reviews, documentation): a behaviour is shown under the front page's
    expectations and is never a lesson; a lesson with fewer than 2 usable source URLs; an expectations or
    technologies id that was not counted; a counted ``tool`` concept in no technology group.
    """

    decoded = _clean_tree(_json_object(raw, "curriculum_invalid"))

    def bad(message: str) -> LearningCourseError:
        return LearningCourseError("curriculum_invalid", message)

    known = {str(concept["id"]): concept for concept in _kept(counted_concepts)}

    def ids_of(value: object, what: str) -> list[str]:
        if not isinstance(value, list):
            raise bad(f"{what} must be a list of concept ids")
        found: list[str] = []
        for item in value:
            if not isinstance(item, str) or item not in known:
                raise bad(f"{what}: unknown concept id {str(item)[:ID_CHARS_MAX]!r}")
            if item not in found:
                found.append(item)
        return found

    raw_modules = decoded.get("modules")
    if not isinstance(raw_modules, list) or not MODULES_MIN <= len(raw_modules) <= MODULES_MAX:
        raise bad(f"modules must hold {MODULES_MIN} to {MODULES_MAX} modules")
    modules: list[dict[str, object]] = []
    module_ids: set[str] = set()
    lesson_ids: set[str] = set()
    for raw_module in raw_modules:
        if not isinstance(raw_module, Mapping):
            raise bad("every module is an object")
        module_id = raw_module.get("id")
        if not isinstance(module_id, str) or len(module_id) > ID_CHARS_MAX or not _ID.fullmatch(module_id) or module_id in module_ids:
            raise bad("a module id is lowercase kebab-case and used once")
        module_ids.add(module_id)
        module_title = _one_line(raw_module.get("title"), TITLE_CHARS_MAX)
        if not module_title:
            raise bad(f"the title of module {module_id} must be one line of 1 to {TITLE_CHARS_MAX} characters with no link or contact detail")
        raw_lessons = raw_module.get("lessons")
        if not isinstance(raw_lessons, list) or not MODULE_LESSONS_MIN <= len(raw_lessons) <= MODULE_LESSONS_MAX:
            raise bad(f"module {module_id} must hold {MODULE_LESSONS_MIN} to {MODULE_LESSONS_MAX} lessons")
        lessons: list[dict[str, object]] = []
        for raw_lesson in raw_lessons:
            if not isinstance(raw_lesson, Mapping):
                raise bad("every lesson is an object")
            lesson_id = raw_lesson.get("id")
            if not isinstance(lesson_id, str) or len(lesson_id) > ID_CHARS_MAX or not _ID.fullmatch(lesson_id):
                raise bad(f"a lesson id is lowercase kebab-case, at most {ID_CHARS_MAX} characters")
            if lesson_id in lesson_ids:
                raise bad(f"the lesson id {lesson_id} is used twice")
            lesson_ids.add(lesson_id)
            title = _one_line(raw_lesson.get("title"), TITLE_CHARS_MAX)
            if not title:
                raise bad(f"the title of lesson {lesson_id} must be one line of 1 to {TITLE_CHARS_MAX} characters with no link or contact detail")
            concepts = ids_of(raw_lesson.get("concepts", []), f"concepts of lesson {lesson_id}")
            if not concepts and raw_lesson.get("curriculum") is not True:
                raise bad(f"lesson {lesson_id} must name a concept or be marked curriculum")
            if concepts and not any(_is_technical(known[concept_id]) for concept_id in concepts):
                raise bad(
                    f"lesson {lesson_id} teaches only behaviours ({', '.join(concepts[:5])}): a lesson must teach a technical concept; "
                    "behaviours belong under expectations only"
                )
            candidates = _candidates(raw_lesson.get("sources"), CANDIDATES_MAX)
            if len(candidates) < CANDIDATES_MIN:
                raise bad(f"lesson {lesson_id} needs at least {CANDIDATES_MIN} usable source URLs (https, a page under a site, not its home page)")
            lessons.append({
                "id": lesson_id, "title": title, "concepts": concepts, "source": "postings" if concepts else "curriculum",
                "candidates": candidates,
            })
        modules.append({"id": module_id, "title": module_title, "lessons": lessons})
    if not LESSONS_MIN <= len(lesson_ids) <= LESSONS_MAX:
        raise bad(f"the course must hold {LESSONS_MIN} to {LESSONS_MAX} lessons in all; this one holds {len(lesson_ids)}")

    raw_expectations = decoded.get("expectations")
    if not isinstance(raw_expectations, Mapping):
        raise bad("expectations must be an object with responsibilities and scope")
    expectations = {
        "responsibilities": ids_of(raw_expectations.get("responsibilities", []), "expectations.responsibilities"),
        "scope_at_level": ids_of(raw_expectations.get("scope", []), "expectations.scope"),
    }
    raw_technologies = decoded.get("technologies")
    if not isinstance(raw_technologies, Mapping) or len(raw_technologies) > TECH_GROUPS_MAX:
        raise bad(f"technologies must be an object of at most {TECH_GROUPS_MAX} groups")
    technologies: dict[str, list[str]] = {}
    for raw_name, raw_ids in raw_technologies.items():
        name = _one_line(raw_name, TITLE_CHARS_MAX)
        if not name or name in technologies:
            raise bad("a technologies group title is one line, used once, with no link or contact detail")
        technologies[name] = ids_of(raw_ids, f"technologies group {name!r}")
    grouped = {concept_id for ids in technologies.values() for concept_id in ids}
    uncovered = sorted(concept_id for concept_id, concept in known.items() if concept.get("category") == "tool" and concept_id not in grouped)
    if uncovered:
        raise bad("technologies must cover every tool concept; missing: " + ", ".join(uncovered[:20]))
    return {
        "schema_version": SCHEMA_CURRICULUM, "role": role_text, "modules": modules, "expectations": expectations,
        "technologies": technologies, "planned_lessons": len(lesson_ids),
    }


def plan_curriculum(model: CourseModel, role_text: str, counted_concepts: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Step 6: one call, and one more with the validator's message when the first answer does not validate."""

    error: LearningError | None = None
    for _attempt in range(2):
        raw = model.ask(curriculum_prompt(role_text, counted_concepts, error), items=1, timeout_seconds=LONG_CALL_TIMEOUT_SECONDS)
        try:
            return parse_curriculum(raw, role_text, counted_concepts)
        except (LearningCorpusError, LearningCourseError) as exc:
            model.invalid_output()
            error = exc
    raise LearningCourseError("curriculum_invalid", f"the model's curriculum for this role was not usable, twice ({error})")


def _lessons(curriculum: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [lesson for module in curriculum.get("modules", []) for lesson in module.get("lessons", [])]


def check_curriculum(data: object) -> bool:
    """Whether a stored curriculum is one :func:`parse_curriculum` wrote (the skip-if-valid check of step 6)."""

    if not isinstance(data, Mapping) or data.get("schema_version") != SCHEMA_CURRICULUM or not isinstance(data.get("modules"), list):
        return False
    try:
        lessons = _lessons(data)
        ids = [lesson["id"] for lesson in lessons]
        shaped = all(
            isinstance(lesson["id"], str) and _ID.fullmatch(lesson["id"]) and isinstance(lesson["title"], str)
            and isinstance(lesson["concepts"], list) and isinstance(lesson["candidates"], list)
            for lesson in lessons
        ) and all(isinstance(module["id"], str) and _ID.fullmatch(module["id"]) and isinstance(module["title"], str) for module in data["modules"])
    except (KeyError, TypeError, AttributeError):
        return False
    return bool(shaped) and len(ids) == len(set(ids)) == data.get("planned_lessons") and isinstance(data.get("technologies"), Mapping) and isinstance(data.get("expectations"), Mapping)


# ---------------------------------------------------------------------------
# The main tool of a lesson (steps 7 and 9)
# ---------------------------------------------------------------------------

_NAME_WORDS = re.compile(r"[a-z0-9]+")
#: A vendor's name in front of a product's ("Apache Spark", "Amazon SageMaker"): a title says "Spark", and the
#: vendor's word in a host ("airflow.apache.org") says nothing about which product the page documents.
_VENDOR_WORDS = frozenset({"apache", "amazon", "aws", "google", "microsoft", "azure", "hashicorp", "nvidia"})
_FILLER_WORDS = frozenset({"the", "and", "for", "of", "on", "with"})
#: What is left of "Google Docs" or "GitHub Actions" without the vendor is a common word, not a name.
_COMMON_PRODUCT_WORDS = frozenset({
    "actions", "cloud", "code", "core", "docs", "engine", "functions", "lab", "pipelines", "platform", "server", "sql", "storage", "studio", "tools",
})
#: Hosts that hold anybody's project: the host says nothing about the tool, only the address under it does.
_SHARED_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org", "arxiv.org", "medium.com"})
#: Hosting suffixes: the labels in front of them name the project ("argo-cd.readthedocs.io"), the suffix never does.
_HOSTING_SUFFIXES = (".github.io", ".gitlab.io", ".readthedocs.io", ".readthedocs.org", ".gitbook.io", ".netlify.app", ".pages.dev")
#: A vendor word's OWN root domain, where (unlike "apache.org", a foundation hosting many unrelated third-party
#: projects under their own subdomains) the vendor publishes many of ITS OWN products together: a tool whose
#: name names the vendor ("NVIDIA GPUs", "AWS Bedrock") is documented somewhere under this domain, not identified
#: by a further sub-label of it. A vendor with no entry here (``apache``, ``hashicorp``) stays as before: its
#: word alone never makes a host the tool's.
_VENDOR_OWN_DOMAINS: Mapping[str, str] = {
    "nvidia": "nvidia.com", "aws": "amazon.com", "amazon": "amazon.com", "google": "google.com",
    "microsoft": "microsoft.com", "azure": "microsoft.com",
}


def _vendor_own_host(host: str, name: str) -> bool:
    """Whether ``host`` is a vendor's own multi-product domain AND ``name`` names that same vendor."""

    words = set(_NAME_WORDS.findall(name.lower()))
    for vendor, domain in _VENDOR_OWN_DOMAINS.items():
        if vendor in words and (host == domain or host.endswith("." + domain)):
            return True
    return False


def tool_aliases(name: str) -> list[str]:
    """The names a tool goes by: "Spark / PySpark" is "Spark" and "PySpark", "Model Context Protocol (MCP)" is both, "Amazon SageMaker" is also "SageMaker"."""

    found: list[str] = []
    for part in [name, *re.split(r"[/()]", name)]:
        part = " ".join(part.split())
        words = part.split(" ")
        while len(words) > 1 and words[0].lower() in _VENDOR_WORDS:
            words = words[1:]
        short = " ".join(words)
        for alias in (part, short):
            if alias is short and alias != part and alias.lower() in _COMMON_PRODUCT_WORDS:
                continue
            if _NAME_WORDS.search(alias.lower()) and alias not in found:
                found.append(alias)
    return found


def named_at(text: str, name: str) -> int | None:
    """Where ``text`` first names the tool as whole words (any of :func:`tool_aliases`, case ignored); ``None`` when it does not.

    A name of one or two letters ("Go", "R") is matched in its own case only: "go" is also a verb.
    """

    best: int | None = None
    for alias in tool_aliases(name):
        pattern = r"(?<![A-Za-z0-9])" + r"\s+".join(re.escape(word) for word in alias.split(" ")) + r"(?![A-Za-z0-9+#])"
        match = re.search(pattern, text, 0 if len(alias) <= 2 else re.IGNORECASE)
        if match is not None and (best is None or match.start() < best):
            best = match.start()
    return best


def _url_host(url: object) -> str:
    try:
        return (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


def _holds_words(words: Sequence[str], wanted: Sequence[str]) -> bool:
    return bool(wanted) and any(list(words[start : start + len(wanted)]) == list(wanted) for start in range(len(words) - len(wanted) + 1))


def source_strength(source: Mapping[str, Any], name: str, link: str = "") -> int:
    """How surely a verified source page is a page of the tool ``name``: 2 its documentation host, 1 its address names it or its title opens with it, 0 neither.

    A title that only mentions the tool further on ("Otherboard compared with MLflow") does not make the page the tool's.

    The host is the tool's when it is the host of ``link`` (the course's ``tool_links`` address for the tool),
    or when one of its labels is the first word of the tool's name, or holds it when it has four letters or
    more, or holds the whole name ("airflow.apache.org" for Airflow, "docs.aws.amazon.com" for AWS,
    "argo-cd.readthedocs.io" for Argo CD, "modelcontextprotocol.io" for Model Context Protocol). A vendor's
    word alone does not make a host the tool's ("spark.apache.org" is not Apache Airflow's); the last label
    ("onnx.ai" is not an AI tool's) and a host that holds anybody's project ("github.com", the ".github.io" of
    "google.github.io") never do.
    """

    url = source.get("url")
    host = _url_host(url)
    if not host:
        return 0
    if link and _url_host(link) == host:
        return 2
    if _vendor_own_host(host, name):
        return 2
    own = next((host[: -len(suffix)] for suffix in _HOSTING_SUFFIXES if host.endswith(suffix)), None)
    labels = [] if host in _SHARED_HOSTS else [label for label in (own.split(".") if own is not None else host.split(".")[:-1]) if label]
    flat_labels = [re.sub(r"[^a-z0-9]", "", label) for label in labels]
    weak = False
    try:
        path = urlsplit(str(url)).path.lower()
    except ValueError:
        path = ""
    path_words = _NAME_WORDS.findall(path)
    titles = [_NAME_WORDS.findall(str(source.get(key, "")).lower()) for key in ("title", "page_title")]
    for alias in tool_aliases(name):
        words = _NAME_WORDS.findall(alias.lower())
        telling = [word for word in words if word not in _VENDOR_WORDS and word not in _FILLER_WORDS] or words
        joined = "".join(telling)
        head = telling[0]
        for label, flat in zip(labels, flat_labels):
            if head in re.split(r"[^a-z0-9]+", label) or head == flat or (len(head) >= 4 and head in flat) or (len(joined) >= 4 and joined in flat):
                return 2
        if len(alias) > 2 and (
            _holds_words(path_words, words) or (len(words) > 1 and "".join(words) in path_words) or any(title[: len(words)] == words for title in titles)
        ):
            weak = True  # a name of one or two letters ("Go") is a word of any address: only a host can say it is that tool's
    return 1 if weak else 0


def tool_pages(sources: Iterable[Mapping[str, Any]], name: str, link: str = "") -> list[Mapping[str, Any]]:
    """The verified sources that are pages of the tool ``name`` (:func:`source_strength` above 0), in the order given."""

    return [source for source in sources if isinstance(source, Mapping) and source_strength(source, name, link)]


@dataclass(frozen=True)
class ToolCandidate:
    """A tool a lesson's main tool is chosen from.

    ``count`` is ``(postings of the last 90 days, postings of any date)`` and ``counted`` whether the postings
    count it; ``of_lesson`` whether the lesson itself names it (one of its tool concepts or technologies);
    ``titled`` whether a lesson title may name it (a counted tool concept or a ``tool_links`` name); ``link``
    the course's ``tool_links`` address for it; ``ref`` is the caller's own object for it.
    """

    name: str
    count: tuple[int, int] = (0, 0)
    counted: bool = True
    of_lesson: bool = False
    titled: bool = True
    link: str = ""
    ref: Any = None


#: How a main tool was chosen.
TOOL_BY_TITLE, TOOL_BY_HOSTS, TOOL_BY_COUNT, TOOL_BY_PAGES = "title", "hosts", "count", "pages"


def choose_main_tool(title: str, candidates: Sequence[ToolCandidate], sources: Sequence[Mapping[str, Any]]) -> tuple[ToolCandidate, str] | None:
    """The main tool of a lesson and how it was chosen, or ``None`` when the lesson has no tool to choose.

    In this order:

    1. a tool the lesson TITLE names (whole words, case ignored). When the title names several ("Airflow,
       Prefect and Temporal") the one with most verified pages of its own, then the LONGER name (the more
       specific one: "AWS Bedrock" over the "AWS" it contains, even when AWS has more postings), then most
       postings, then the first named.
    2. the tool whose documentation holds the MAJORITY (more than half) of the lesson's verified sources.
    3. of the lesson's own counted tools, the one most postings name: first among those with a verified page of
       their own, then (none has one) among all of them. Between the two, a tool the lesson names that the
       postings do not count but that has verified pages (most pages wins) is taken before a counted tool
       with none: a path is only ever built from pages of the tool its header names.

    Ties keep the order of ``candidates``.
    """

    pages = [len(tool_pages(sources, item.name, item.link)) for item in candidates]
    order = range(len(candidates))
    titled = [(index, named_at(title, candidates[index].name)) for index in order if candidates[index].titled]
    in_title = [(index, at) for index, at in titled if at is not None]
    if in_title:
        index, _at = min(
            in_title,
            key=lambda found: (
                -pages[found[0]], -len(candidates[found[0]].name),
                tuple(-number for number in candidates[found[0]].count), found[1],
            ),
        )
        return candidates[index], TOOL_BY_TITLE
    majority = [index for index in order if pages[index] * 2 > len(sources)]
    if majority:
        return candidates[max(majority, key=lambda index: (pages[index], candidates[index].count, -index))], TOOL_BY_HOSTS
    own = [index for index in order if candidates[index].of_lesson and candidates[index].counted]
    paged = [index for index in own if pages[index]]
    if paged:
        return candidates[max(paged, key=lambda index: (candidates[index].count, -index))], TOOL_BY_COUNT
    named = [index for index in order if candidates[index].of_lesson and pages[index]]
    if named:
        return candidates[max(named, key=lambda index: (pages[index], -index))], TOOL_BY_PAGES
    if own:
        return candidates[max(own, key=lambda index: (candidates[index].count, -index))], TOOL_BY_COUNT
    return None


def _count(value: object) -> int:
    return value if type(value) is int and value > 0 else 0


def curriculum_tool(lesson: Mapping[str, Any], counted_concepts: Iterable[Mapping[str, object]], sources: Sequence[Mapping[str, Any]]) -> str | None:
    """Step 7's view of a lesson's main tool: :func:`choose_main_tool` over the counted ``tool`` concepts (the lesson's own are its concept ids)."""

    own = set(lesson.get("concepts") or [])
    candidates = []
    for concept in _kept(counted_concepts):
        display = _one_line(concept.get("display"), TITLE_CHARS_MAX)
        if concept.get("category") == "tool" and display:
            candidates.append(ToolCandidate(display, (_count(concept.get("count_in90d")), _count(concept.get("count_all"))), of_lesson=concept["id"] in own))
    chosen = choose_main_tool(str(lesson.get("title", "")), candidates, sources)
    return chosen[0].name if chosen is not None else None


# ---------------------------------------------------------------------------
# Step 7: the sources
# ---------------------------------------------------------------------------


class _PageFetcher(Protocol):
    def fetch_page(self, url: str) -> Any: ...


def sources_retry_prompt(
    role_text: str, lessons: Sequence[Mapping[str, object]], tried: Mapping[str, Sequence[str]], *,
    tools: Mapping[str, str] | None = None, failed: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    """The one follow-up of step 7: three new URLs for the named lessons. No resume line is an input.

    ``tools`` is ``{lesson id: main tool}`` for the lessons whose main tool has fewer than two verified pages:
    their block names the tool, and the new pages are asked from that tool's own documentation. ``failed`` is
    ``{lesson id: {url: why it was not kept}}``: a tried URL that did not answer is marked so.
    """

    blocks = []
    for lesson in lessons:
        lesson_id = str(lesson["id"])
        reasons = (failed or {}).get(lesson_id, {})
        urls = "\n".join(
            f"  already tried: {url}" + (f" (did not answer: {reasons[url]})" if url in reasons else "") for url in tried.get(lesson_id, ())
        ) or "  already tried: (none)"
        tool = clean_text((tools or {}).get(lesson_id, ""))
        named = f"\n  main tool: {tool} (fewer than {MAIN_TOOL_PAGES_MIN} pages of its own documentation answered: give pages of the {tool} documentation)" if tool else ""
        blocks.append(f"LESSON id={lesson['id']} title=\"{lesson['title']}\"{named}\n{urls}")
    return _render(
        _template(_CURRICULUM_RESOURCE),
        {
            "plan": None, "retry": "", "role": role_text, "concepts": None, "lessons": fence_untrusted_posting("\n\n".join(blocks)),
            "validation_error": None,
        },
    )


def parse_source_retry(raw: str, lesson_ids: Iterable[str], tried: Mapping[str, Sequence[str]]) -> dict[str, list[dict[str, str]]]:
    """``{lesson id: new candidates}`` of the follow-up answer (at most three each, never one already tried)."""

    decoded = _clean_tree(_json_object(raw, "sources_invalid"))
    wanted = set(lesson_ids)
    items = decoded.get("lessons")
    if not isinstance(items, list):
        raise LearningCourseError("sources_invalid", "lessons must be a list")
    found: dict[str, list[dict[str, str]]] = {}
    for item in items:
        if isinstance(item, Mapping) and item.get("id") in wanted and item["id"] not in found:
            found[str(item["id"])] = _candidates(item.get("sources"), RETRY_CANDIDATES_MAX, tried.get(str(item["id"]), ()))
    return found


def gather_sources(
    fetcher: _PageFetcher, curriculum: Mapping[str, Any], model: CourseModel | None = None, *,
    progress: Callable[[str], None] | None = None, counted_concepts: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Step 7: the candidate pages that answered, per lesson, with their headings; and the lessons left with none.

    A source is ``{title, url, kind, page_title, requested_url, headings, page_level_citable}``: ``url`` is the
    address that answered 200 (after at most three vetted redirects), never the text a model wrote. A page is
    fetched once however many lessons name it. A lesson with no source gets one model call for three new URLs
    (``model`` ``None``: no call); still none, and it is in ``dropped`` with ``no_verified_source``. A spent
    fetch budget ends the fetching (``stopped``) and no follow-up call is made; a cancel raises ``FetchStopped``.
    No resume line is read, sent or needed here.

    With ``counted_concepts`` (G1's counted concepts) every lesson's MAIN TOOL (:func:`curriculum_tool`) must
    have at least two verified pages of its own (:func:`tool_pages`). A lesson whose tool has fewer is asked
    for in that SAME one call, which names the tool and the URLs that did not answer. Such a lesson keeps the
    sources it has whatever comes back: only a lesson with no source at all is dropped. ``thin_tools`` lists
    the lessons whose main tool still has fewer than two pages (``[{lesson, tool, pages}]``).
    """

    from .learning_render import is_bare_domain_url

    say = progress or (lambda _text: None)
    role_text = str(curriculum.get("role", ""))
    lessons = _lessons(curriculum)
    fetched_pages: dict[str, Any] = {}
    verified: dict[str, list[dict[str, object]]] = {str(lesson["id"]): [] for lesson in lessons}
    tried: dict[str, list[str]] = {lesson_id: [] for lesson_id in verified}
    rejected: list[dict[str, str]] = []
    stopped: str | None = None

    def verify(lesson_id: str, candidate: Mapping[str, str]) -> None:
        nonlocal stopped
        url = candidate["url"]
        tried[lesson_id].append(url)
        if stopped is not None:
            rejected.append({"lesson": lesson_id, "url": url, "reason": stopped})
            return
        if url not in fetched_pages:
            try:
                fetched_pages[url] = fetcher.fetch_page(url)
            except FetchStopped as exc:
                if exc.code != STOP_BUDGET:
                    raise
                stopped = exc.code
                rejected.append({"lesson": lesson_id, "url": url, "reason": stopped})
                return
        fetched = fetched_pages[url]
        reason = fetched.error
        if reason is None and not fetched.page.title:
            reason = "no_title"
        if reason is None and (usable_url(fetched.url) is None or is_bare_domain_url(fetched.url)):
            reason = "home_page"
        if reason is not None:
            rejected.append({"lesson": lesson_id, "url": url, "reason": str(reason)})
            return
        if any(source["url"] == fetched.url for source in verified[lesson_id]):
            return
        page = fetched.page
        verified[lesson_id].append({
            "title": candidate.get("title") or _one_line(page.title, SOURCE_TITLE_CHARS_MAX) or "Source page",
            "url": fetched.url, "kind": candidate.get("kind", "doc"), "page_title": page.title, "requested_url": url,
            "headings": [dict(heading) for heading in page.headings], "page_level_citable": page.page_level_citable,
        })

    for number, lesson in enumerate(lessons, 1):
        say(f"Checking sources for lesson {number} of {len(lessons)}")
        for candidate in lesson.get("candidates", []):
            verify(str(lesson["id"]), candidate)

    def thin_tools() -> dict[str, str]:
        """``{lesson id: main tool}`` of the lessons whose main tool has fewer than two verified pages of its own."""

        if counted_concepts is None:
            return {}
        found: dict[str, str] = {}
        for lesson in lessons:
            tool = curriculum_tool(lesson, counted_concepts, verified[str(lesson["id"])])
            if tool is not None and len(tool_pages(verified[str(lesson["id"])], tool)) < MAIN_TOOL_PAGES_MIN:
                found[str(lesson["id"])] = tool
        return found

    retry_call = "not_needed"
    tools = thin_tools()
    without = [lesson for lesson in lessons if not verified[str(lesson["id"])] or str(lesson["id"]) in tools]
    if without and (model is None or stopped is not None):
        retry_call = "skipped"
    elif without and model is not None:
        say(f"Asking for other sources for {len(without)} lesson{'' if len(without) == 1 else 's'}")
        failed: dict[str, dict[str, str]] = {}
        for item in rejected:
            failed.setdefault(item["lesson"], {})[item["url"]] = item["reason"]
        raw = model.ask(sources_retry_prompt(role_text, without, tried, tools=tools, failed=failed), items=1)
        try:
            fresh = parse_source_retry(raw, (str(lesson["id"]) for lesson in without), tried)
            retry_call = "made"
        except (LearningCorpusError, LearningCourseError):
            model.invalid_output()
            fresh, retry_call = {}, "invalid"
        for lesson in without:
            for candidate in fresh.get(str(lesson["id"]), []):
                verify(str(lesson["id"]), candidate)
        tools = thin_tools()

    dropped = [
        {"what": f"lesson:{lesson['id']}", "title": lesson["title"], "reason": NO_VERIFIED_SOURCE}
        for lesson in lessons if not verified[str(lesson["id"])]
    ]
    return {
        "schema_version": SCHEMA_SOURCES, "lessons": {lesson_id: found for lesson_id, found in verified.items() if found},
        "dropped": dropped, "rejected": rejected, "retry_call": retry_call, "stopped": stopped,
        "pages_fetched": len(fetched_pages),
        "thin_tools": [
            {"lesson": lesson_id, "tool": tool, "pages": len(tool_pages(verified[lesson_id], tool))}
            for lesson_id, tool in tools.items() if verified[lesson_id]
        ],
    }


def check_sources(data: object, curriculum: Mapping[str, Any]) -> bool:
    """Whether stored sources are step 7's answer for THIS curriculum: every lesson has a source or is dropped.

    A result the fetch budget cut short (``stopped``) is not a finished step: it is gathered again on resume.
    """

    if not isinstance(data, Mapping) or data.get("schema_version") != SCHEMA_SOURCES or data.get("stopped") is not None:
        return False
    found, dropped = data.get("lessons"), data.get("dropped")
    if not isinstance(found, Mapping) or not isinstance(dropped, list):
        return False
    gone = {str(item.get("what", "")).partition(":")[2] for item in dropped if isinstance(item, Mapping)}
    for lesson in _lessons(curriculum):
        sources = found.get(lesson["id"])
        if lesson["id"] in gone:
            continue
        if not isinstance(sources, list) or not sources or not all(isinstance(source, Mapping) and usable_url(source.get("url")) for source in sources):
            return False
    return True


# ---------------------------------------------------------------------------
# Step 8: the lesson text
# ---------------------------------------------------------------------------


def _pairs(master_lines: object) -> list[tuple[str, str]]:
    items = master_lines.items() if isinstance(master_lines, Mapping) else (master_lines or ())
    return [(str(item_id), str(text)) for item_id, text in items]  # type: ignore[union-attr]


def _flat(text: str) -> str:
    from .learning_fetch import strip_invisible

    return " ".join(strip_invisible(text).split())


def guarded_master_lines(master_lines: object) -> tuple[tuple[str, str], ...]:
    """The master's ``(id, text)`` lines as a prompt may hold them: through ``guard_private``, each on one line.

    A line the guard empties (an address line, a line that was only a link) is left out. This is the text the
    marker check compares a quote with: a quote is verbatim of what was SENT.
    """

    kept = []
    for item_id, text in _pairs(master_lines):
        shown = _flat(guard_private(text))
        if _flat(item_id) and shown:
            kept.append((_flat(item_id), shown))
    return tuple(kept)


def read_master_lines(home_root: Path, target: Path | None) -> tuple[tuple[str, str], ...]:
    """The stored master resume's lines as ``(id, text)``, in the file's order; ``()`` when there is no master yet.

    The master holds no contact line by construction (``master_store`` strips at import). The lines are for
    :func:`write_lessons` only; no other step of a course takes them.
    """

    from .master_store import load_master

    stored = load_master(home_root=Path(home_root), target=target)
    if stored is None:
        return ()
    return tuple((item.id, item.text) for item in stored.master.items.values())


def _lesson_counts(lesson: Mapping[str, Any], concepts_by_id: Mapping[str, Mapping[str, object]]) -> tuple[int, int]:
    found = [concepts_by_id[concept_id] for concept_id in lesson.get("concepts", []) if concept_id in concepts_by_id]
    return (
        max((int(concept.get("count_in90d", 0) or 0) for concept in found), default=0),  # type: ignore[call-overload]
        max((int(concept.get("count_all", 0) or 0) for concept in found), default=0),  # type: ignore[call-overload]
    )


def _lesson_block(lesson: Mapping[str, Any], sources: Sequence[Mapping[str, object]], concepts_by_id: Mapping[str, Mapping[str, object]]) -> str:
    examples: list[str] = []
    for concept_id in lesson.get("concepts", []):
        for example in (concepts_by_id.get(concept_id, {}).get("examples") or [])[:1]:  # type: ignore[index]
            if isinstance(example, Mapping) and len(examples) < EXAMPLES_PER_LESSON:
                examples.append("- " + clean_text(str(example.get("phrase", "")))[:EXAMPLE_CHARS_MAX])
    listed = "\n".join(f"  [{index}] {source['title']} ({source['kind']}) {source['url']}" for index, source in enumerate(sources))
    return (
        f"LESSON id={lesson['id']} title=\"{lesson['title']}\" postings_naming_it={_lesson_counts(lesson, concepts_by_id)[0]}\n"
        f"SOURCES:\n{listed}\nPOSTING EXAMPLES:\n" + ("\n".join(examples) or "(none)")
    )


def lessons_prompt(
    *, role_text: str, module: Mapping[str, Any], lessons: Sequence[Mapping[str, Any]], course_lesson_ids: Sequence[str],
    sources: Mapping[str, Sequence[Mapping[str, object]]], concepts_by_id: Mapping[str, Mapping[str, object]],
    master_lines: object, validation_error: BaseException | str | None = None,
) -> str:
    """The lesson-writing prompt of one module: the ONE prompt of a course that carries resume lines.

    Every master line goes through ``resume_privacy.guard_private`` here, in this function's own body
    (``test_resume_privacy_static.py`` pins that), and is written as one ``id | text`` line. An empty master is
    an empty block: the rules then make every subtopic NEW.
    """

    resume = []
    for item_id, text in _pairs(master_lines):
        shown = _flat(guard_private(text))
        if _flat(item_id) and shown:
            resume.append(f"{_flat(item_id)} | {shown}")
    blocks = "\n\n".join(_lesson_block(lesson, sources[lesson["id"]], concepts_by_id) for lesson in lessons)
    return _render(
        _template(_LESSONS_RESOURCE),
        {
            "role": role_text, "module_title": str(module["title"]), "lesson_ids": ", ".join(course_lesson_ids),
            "lessons": fence_untrusted_posting(blocks), "resume_lines": "\n".join(resume) or "(no resume lines: every subtopic is NEW)",
            "validation_error": _error_text(validation_error),
        },
    )


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", clean_glyphs(text)).strip().casefold()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:ID_CHARS_MAX].strip("-")


def _sections(value: object) -> list[dict[str, str]]:
    """A subtopic's body as ``[{heading, body}]``; a plain string is one section; a section with no clean text is left out."""

    if isinstance(value, str):
        value = [{"heading": "", "body": value}]
    found = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            item = {"heading": "", "body": item}
        if not isinstance(item, Mapping):
            continue
        body = _prose(item.get("body"))
        if body:
            found.append({"heading": _one_line(item.get("heading"), TITLE_CHARS_MAX), "body": body})
    return found


def _marker(sub: Mapping[str, Any], master_items: Mapping[str, str], where: str, log: list[str]) -> tuple[str, dict[str, str] | None, str | None]:
    """``(marker, evidence, missing)`` of one subtopic after the checks: an unsupported KNOWN or SOME is NEW."""

    label, evidence = sub.get("marker"), sub.get("evidence")
    if label not in (MARKER_KNOWN, MARKER_SOME):
        return MARKER_NEW, None, None
    line_id = evidence.get("id") if isinstance(evidence, Mapping) else None
    quote = " ".join(str(evidence.get("line") or "").split()) if isinstance(evidence, Mapping) and isinstance(evidence.get("line"), str) else ""
    supported = (
        isinstance(line_id, str) and line_id in master_items
        and len(re.sub(r"[^A-Za-z0-9]", "", quote)) >= EVIDENCE_CHARS_MIN
        and _norm(quote) in _norm(master_items[line_id])
        and not contact_shapes(quote)
    )
    if not supported:
        log.append(f"{where}: {label} evidence invalid -> NEW")
        return MARKER_NEW, None, None
    assert isinstance(line_id, str)
    kept = {"id": line_id, "line": quote[:EVIDENCE_CHARS_MAX].strip()}
    if label == MARKER_KNOWN and line_id.startswith(SKILLS_ID_PREFIX):
        log.append(f"{where}: KNOWN from a skills line -> SOME")
        return MARKER_SOME, kept, "Named in the skills list only; no line shows production work on this."
    if label == MARKER_SOME:
        return MARKER_SOME, kept, _one_line(sub.get("missing"), 400) or "Not shown by the cited line."
    return MARKER_KNOWN, kept, None


def _fix_lesson(
    lesson: Mapping[str, Any], out: Mapping[str, Any], *, verified: Sequence[Mapping[str, object]], course_lesson_ids: Iterable[str],
    master_items: Mapping[str, str], concepts_by_id: Mapping[str, Mapping[str, object]], log: list[str],
) -> dict[str, object]:
    lesson_id = str(lesson["id"])
    summary = _prose(out.get("summary"))
    if not summary:
        raise _Invalid(f"{lesson_id}: summary is empty or holds contact data that could not be removed")

    raw_subtopics = out.get("subtopics")
    if not isinstance(raw_subtopics, list) or not raw_subtopics:
        raise _Invalid(f"{lesson_id}: subtopics must hold 1 to {SUBTOPICS_MAX} objects")
    subtopics: list[dict[str, object]] = []
    used: set[str] = set()
    for raw_sub in raw_subtopics[:SUBTOPICS_MAX]:
        title = _one_line(raw_sub.get("title"), TITLE_CHARS_MAX) if isinstance(raw_sub, Mapping) else ""
        if not title:
            raise _Invalid(f"{lesson_id}: subtopics: every subtopic needs a one-line title")
        given = raw_sub.get("id")
        sub_id = given if isinstance(given, str) and len(given) <= ID_CHARS_MAX and _ID.fullmatch(given) else (_slug(title) or "subtopic")
        base, number = sub_id, 2
        while sub_id in used:
            sub_id, number = f"{base}-{number}", number + 1
        used.add(sub_id)
        body = _sections(raw_sub.get("body"))
        if not body:
            raise _Invalid(f"{lesson_id}: subtopics: {sub_id} has no body text")
        label, evidence, missing = _marker(raw_sub, master_items, f"{lesson_id}/{sub_id}", log)
        subtopics.append({"id": sub_id, "title": title, "body": body, "marker": label, "evidence": evidence, "missing": missing})

    indexes: list[int] = []
    for index in out.get("sources") if isinstance(out.get("sources"), list) else []:
        if type(index) is int and 0 <= index < len(verified):
            if index not in indexes:
                indexes.append(index)
        else:
            log.append(f"{lesson_id}: a source that is not an index into the verified list was ignored")
    if not indexes:
        log.append(f"{lesson_id}: no valid source index; the first verified source is used")
        indexes = [0]
    sources = [{key: verified[index][key] for key in ("title", "url", "kind")} for index in indexes]

    diagram = out.get("diagram")
    if diagram is not None:
        source = diagram.get("source") if isinstance(diagram, Mapping) else None
        if isinstance(source, str) and source.strip().startswith(MERMAID_WORDS) and not contact_shapes(source):
            diagram = {"source": source.strip()}
        else:
            log.append(f"{lesson_id}: bad mermaid dropped")
            diagram = None

    code: list[dict[str, str]] = []
    for block in out.get("code") if isinstance(out.get("code"), list) else []:
        text = block.get("code") if isinstance(block, Mapping) else None
        if not isinstance(text, str) or not text.strip() or text.count("\n") >= CODE_LINES_MAX or contact_shapes(text):
            log.append(f"{lesson_id}: a code block was dropped (empty, too long, or it held a link or contact data)")
            continue
        lang = block.get("lang")
        code.append({"lang": lang.lower() if isinstance(lang, str) and _LANG.fullmatch(lang) else "text", "code": text.strip("\n")})
        if len(code) >= CODE_BLOCKS_MAX:
            break

    known_ids = set(course_lesson_ids)
    related: list[str] = []
    for other in out.get("related") if isinstance(out.get("related"), list) else []:
        if isinstance(other, str) and other in known_ids and other != lesson_id:
            if other not in related:
                related.append(other)
        else:
            log.append(f"{lesson_id}: unknown related id dropped")

    technologies: list[str] = []
    for name in out.get("technologies") if isinstance(out.get("technologies"), list) else []:
        shown = _one_line(name, 60)
        if shown and shown not in technologies and len(technologies) < TECHNOLOGIES_MAX:
            technologies.append(shown)

    labels = [sub["marker"] for sub in subtopics]
    label = MARKER_KNOWN if all(item == MARKER_KNOWN for item in labels) else (MARKER_NEW if all(item == MARKER_NEW for item in labels) else MARKER_SOME)
    cited = next((sub for sub in subtopics if sub["marker"] != MARKER_NEW), None)
    partial = next((sub for sub in subtopics if sub["marker"] == MARKER_SOME), None)
    in_90d, any_date = _lesson_counts(lesson, concepts_by_id)
    return {
        "id": lesson_id, "title": lesson["title"], "postings_in_90d": in_90d, "postings_any_date": any_date,
        "source": "postings" if in_90d or any_date else "curriculum",
        "summary": summary, "detail": [], "in_practice": _prose(out.get("in_practice")), "technologies": technologies,
        "diagram": diagram, "code": code, "sources": sources, "related": related,
        "marker": {
            "id": lesson_id, "title": lesson["title"], "marker": label,
            "evidence": cited["evidence"] if label != MARKER_NEW and cited is not None else None,
            "missing": None if label != MARKER_SOME else (partial["missing"] if partial is not None else "Some subtopics are not shown by a resume line."),
        },
        "subtopics": subtopics,
    }


def fix_module(
    module: Mapping[str, Any], lessons: Sequence[Mapping[str, Any]], answer: Mapping[str, Any], *,
    sources: Mapping[str, Sequence[Mapping[str, object]]], course_lesson_ids: Iterable[str], master_items: Mapping[str, str],
    concepts_by_id: Mapping[str, Mapping[str, object]], log: list[str],
) -> dict[str, object]:
    """One module's answer after the code-side checks, in the renderer's shape. Raises on what cannot be repaired.

    Repaired, each with a line in ``log``: a source that is not an index (ignored; with none left the first
    verified source is used), a diagram that does not start with a Mermaid word (dropped), a related id that is
    not a lesson of the course (dropped), a KNOWN or SOME marker without a real line id and a verbatim quote
    (NEW), a KNOWN from a skills line (SOME), a code block that is empty, too long or holds a link (dropped).
    Not repairable (the retry is told): a lesson missing from the answer, an empty summary, no subtopic, a
    subtopic without a title or body text.
    """

    answers = answer.get("lessons")
    if not isinstance(answers, list):
        raise _Invalid("lessons must be a list with one object per LESSON given")
    by_id = {item.get("id"): item for item in answers if isinstance(item, Mapping)}
    course_ids = list(course_lesson_ids)
    written = []
    for lesson in lessons:
        out = by_id.get(lesson["id"])
        if out is None:
            raise _Invalid(f"missing lesson {lesson['id']}")
        written.append(_fix_lesson(
            lesson, out, verified=sources[lesson["id"]], course_lesson_ids=course_ids, master_items=master_items,
            concepts_by_id=concepts_by_id, log=log,
        ))
    return {"schema_version": SCHEMA_MODULE, "id": module["id"], "title": module["title"], "intro": _prose(answer.get("module_intro")), "concepts": written}


def check_module(data: object, lesson_ids: Sequence[str]) -> bool:
    """Whether a stored module is :func:`fix_module`'s answer for exactly these lessons (the skip-if-valid check of step 8)."""

    if not isinstance(data, Mapping) or data.get("schema_version") != SCHEMA_MODULE or not isinstance(data.get("concepts"), list):
        return False
    from .learning_render import validate_course

    try:
        return [concept["id"] for concept in data["concepts"]] == list(lesson_ids) and not [
            error for error in validate_course({"modules": [data]}) if "unknown concept id" not in error
        ]
    except (KeyError, TypeError, AttributeError):
        return False


def write_lessons(
    model: CourseModel, role_text: str, curriculum: Mapping[str, Any], sources: Mapping[str, Any], master_lines: object,
    counted_concepts: Sequence[Mapping[str, object]], *, done: Mapping[str, Mapping[str, Any]] | None = None,
    on_module: Callable[[str, dict[str, object]], object] | None = None, cancel: Callable[[], bool] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Step 8: one call per module (one more with the validator's message), for the lessons that have a verified source.

    ``master_lines`` is the master resume as ``(id, text)`` pairs (or a mapping); this is the only step that
    takes it. ``done`` holds modules a previous run finished (they are not asked for again when they still
    check); ``on_module`` is told each module as it is finished, so the caller can store it. A module with no
    lesson left is not asked for; one whose two answers fail is in ``dropped`` with ``module_text_failed``.
    """

    say = progress or (lambda _text: None)
    verified: Mapping[str, Sequence[Mapping[str, object]]] = sources.get("lessons", {})
    concepts_by_id = {str(concept["id"]): concept for concept in _kept(counted_concepts)}
    master_items = dict(guarded_master_lines(master_lines))
    plan = [(module, [lesson for lesson in module["lessons"] if verified.get(lesson["id"])]) for module in curriculum["modules"]]
    plan = [(module, lessons) for module, lessons in plan if lessons]
    course_lesson_ids = [lesson["id"] for _module, lessons in plan for lesson in lessons]
    modules: dict[str, Mapping[str, Any]] = {}
    dropped: list[dict[str, object]] = []
    log: list[str] = []
    for number, (module, lessons) in enumerate(plan, 1):
        module_id = str(module["id"])
        lesson_ids = [lesson["id"] for lesson in lessons]
        stored = (done or {}).get(module_id)
        if stored is not None and check_module(stored, lesson_ids):
            modules[module_id] = stored
            continue
        say(f"Writing module {number} of {len(plan)}")
        error: BaseException | None = None
        written: dict[str, object] | None = None
        for attempt in (1, 2):
            if cancel is not None and cancel():
                raise LearningCourseStopped()
            prompt = lessons_prompt(
                role_text=role_text, module=module, lessons=lessons, course_lesson_ids=course_lesson_ids, sources=verified,
                concepts_by_id=concepts_by_id, master_lines=master_lines, validation_error=error,
            )
            raw = model.ask(prompt, items=len(lessons), timeout_seconds=LONG_CALL_TIMEOUT_SECONDS)
            try:
                answer = _clean_tree(_json_object(raw, "module_invalid"))
                written = fix_module(
                    module, lessons, answer, sources=verified, course_lesson_ids=course_lesson_ids, master_items=master_items,
                    concepts_by_id=concepts_by_id, log=log,
                )
                break
            except (LearningCorpusError, _Invalid) as exc:
                model.invalid_output()
                error = exc
                log.append(f"{module_id} attempt {attempt} failed: {_error_text(exc)}")
        if written is None:
            dropped.append({"what": f"module:{module_id}", "title": module["title"], "reason": MODULE_TEXT_FAILED, "lessons": lesson_ids})
            continue
        modules[module_id] = written
        if on_module is not None:
            on_module(module_id, written)
    return {"modules": modules, "dropped": dropped, "log": log}


# ---------------------------------------------------------------------------
# Step 10: course.json
# ---------------------------------------------------------------------------


def course_status(planned: int, kept: int) -> tuple[str, str | None]:
    """The 60 PERCENT rule: ``("done", None)`` when ``kept`` lessons are at least 60% of the ``planned`` ones, else ``("failed", "too_little_verified")``."""

    if planned > 0 and kept * 100 >= DONE_PERCENT * planned:
        return STATUS_DONE, None
    return STATUS_FAILED, TOO_LITTLE_VERIFIED


def _percent(concept: Mapping[str, object]) -> object:
    return concept.get("percent_in90d", 0) if concept.get("n_in90d") else concept.get("percent_all", 0)


def _front_item(concept: Mapping[str, object], *, examples: bool) -> dict[str, object] | None:
    display = _one_line(concept.get("display"), TITLE_CHARS_MAX)
    if not display:
        return None
    item: dict[str, object] = {
        "id": concept["id"], "display": display, "percent": _percent(concept), "in_90d": concept.get("count_in90d", 0),
        "any_date": concept.get("count_all", 0),
    }
    if examples:
        shown = []
        for example in (concept.get("examples") or [])[:EXAMPLES_PER_LESSON]:  # type: ignore[index]
            text = _one_line(example.get("phrase"), 200) if isinstance(example, Mapping) else ""
            if text:
                # No posting URL: every URL of a course is a source fetched and verified in this run.
                shown.append({"text": text, "company": _one_line(example.get("company"), 80), "title": _one_line(example.get("title"), TITLE_CHARS_MAX)})
        item["examples"] = shown
    return item


def _scope(corpus: Mapping[str, object]) -> str:
    filters = corpus.get("filters")
    parts: list[str] = []
    if isinstance(filters, Mapping):
        countries = filters.get("countries") or []
        parts.append("US only" if filters.get("us_only") else (", ".join(str(country) for country in countries) or "any country"))  # type: ignore[union-attr]
        if filters.get("work_mode") not in (None, "any"):
            parts.append(str(filters["work_mode"]))
        if filters.get("area"):
            parts.append(str(filters["area"]))
    parts.append("from the job boards stored on this computer")
    if corpus.get("note"):
        parts.append(str(corpus["note"]))
    return _one_line("; ".join(parts), 400)


def course_urls(value: object) -> list[str]:
    """Every ``url`` value anywhere in a course."""

    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                found.append(item)
            else:
                found.extend(course_urls(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(course_urls(item))
    return found


def contact_findings(value: object, where: str = "course") -> list[str]:
    """Where a course holds a string that fails the contact check (paths, never values). A ``url`` value is not text: it is checked against the verified sources instead."""

    found: list[str] = []
    if isinstance(value, str):
        if contact_shapes(value):
            found.append(where)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and contact_shapes(key):
                found.append(f"{where} (a key)")
            if key != "url":
                found.extend(contact_findings(item, f"{where}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(contact_findings(item, f"{where}[{index}]"))
    return found


def assemble_course(
    role_text: str, corpus: Mapping[str, object], counted_concepts: Sequence[Mapping[str, object]], curriculum: Mapping[str, Any],
    sources: Mapping[str, Any], lessons: Mapping[str, Any],
) -> dict[str, object]:
    """Step 10: ``course.json`` in the renderer's shape, with ``dropped``, the lesson counts and the 60% verdict.

    ``corpus`` is G1's corpus numbers (``run_corpus()["corpus"]``), ``sources`` step 7's answer and ``lessons``
    step 8's. The modules stand in the curriculum's order; a related id of a lesson that did not make it is
    taken out. ``status`` is :func:`course_status` of the planned and the kept lessons (``error``:
    ``too_little_verified`` when failed). Raises ``course_invalid`` when the renderer's validator refuses the
    result, ``unverified_url`` when it holds a URL step 7 did not verify, ``contact_data`` when a string fails
    the contact check.
    """

    from .learning_render import validate_course

    concepts_by_id = {str(concept["id"]): concept for concept in _kept(counted_concepts)}
    written: Mapping[str, Mapping[str, Any]] = lessons.get("modules", {})
    kept_modules = [written[module["id"]] for module in curriculum["modules"] if module["id"] in written]
    kept_ids = {concept["id"] for module in kept_modules for concept in module["concepts"]}
    modules = [
        {
            "id": module["id"], "title": module["title"], "intro": module.get("intro", ""),
            "concepts": [{**concept, "related": [other for other in concept.get("related", []) if other in kept_ids]} for concept in module["concepts"]],
        }
        for module in kept_modules
    ]

    def front(ids: Iterable[str], *, examples: bool) -> list[dict[str, object]]:
        items = (_front_item(concepts_by_id[concept_id], examples=examples) for concept_id in ids if concept_id in concepts_by_id)
        return [item for item in items if item is not None]

    expectations = curriculum.get("expectations", {})
    planned = int(curriculum.get("planned_lessons", 0))
    status, error = course_status(planned, len(kept_ids))
    technologies = {}
    for name, ids in curriculum.get("technologies", {}).items():
        group = front(ids, examples=False)
        if group:
            technologies[name] = group
    course: dict[str, object] = _clean_tree({
        "schema_version": SCHEMA_COURSE,
        "role": clean_text(role_text) or "Course",
        "corpus": {
            "postings_90d": corpus.get("with_text_in_window", 0), "postings_any_date": corpus.get("with_text", 0), "scope": _scope(corpus),
        },
        "expectations": {
            "responsibilities": front(expectations.get("responsibilities", []), examples=True),
            "scope_at_staff": front(expectations.get("scope_at_level", []), examples=True),
        },
        "technologies": technologies,
        "modules": modules,
        # Tool names are linked only to pages fetched and verified in this run; this packet verifies lesson sources only.
        "tool_links": {},
        "dropped": [dict(item) for item in [*sources.get("dropped", []), *lessons.get("dropped", [])]],
        "planned_lessons": planned,
        "kept_lessons": len(kept_ids),
        "status": status,
        "error": error,
    })
    if not course["role"]:
        course["role"] = "Course"
    verified_urls = {source["url"] for found in sources.get("lessons", {}).values() for source in found}
    unverified = sorted(set(course_urls(course)) - verified_urls)
    if unverified:
        raise LearningCourseError("unverified_url", f"the course holds {len(unverified)} URL(s) that were not fetched and verified in this run")
    flagged = contact_findings(course)
    if flagged:
        raise LearningCourseError("contact_data", "the course holds text that looks like contact data at: " + ", ".join(flagged[:5]))
    errors = validate_course(course)
    if errors:
        raise LearningCourseError("course_invalid", "; ".join(sorted(errors)[:5]))
    return course


# ---------------------------------------------------------------------------
# The work folder: a result per step, read back only when it still validates
# ---------------------------------------------------------------------------

STEP_CURRICULUM, STEP_SOURCES, STEP_COURSE = "curriculum", "sources", "course"
_STEP_NAME = re.compile(r"(?:curriculum|sources|course|modules/[a-z0-9]+(?:-[a-z0-9]+)*)")


def module_step(module_id: str) -> str:
    return f"modules/{module_id}"


class CourseWork:
    """The step files of one course under its work folder: ``curriculum.json``, ``sources.json``, ``modules/<id>.json``, ``course.json``."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = Path(work_dir)

    def path(self, step: str) -> Path:
        if not _STEP_NAME.fullmatch(step):
            raise LearningCourseError("invalid_value", "not a step of a course")
        return self.work_dir / f"{step}.json"

    def read(self, step: str, check: Callable[[Any], bool] | None = None) -> Any | None:
        """The stored result of ``step``, or ``None`` when there is none, it is not JSON, or ``check`` refuses it (the step then runs again)."""

        try:
            data = json.loads(self.path(step).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if check is None or check(data) else None

    def write(self, step: str, data: object) -> Path:
        from .find_jobs.discovery.storage import atomic_write

        path = self.path(step)
        atomic_write(path, (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
        return path


def run_course_steps(
    work_dir: Path, *, model: CourseModel, fetcher: _PageFetcher, role_text: str, corpus: Mapping[str, object],
    counted_concepts: Sequence[Mapping[str, object]], master_lines: object, cancel: Callable[[], bool] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Steps 6, 7, 8 and 10 over a work folder: a step whose stored result still validates is not run again.

    Returns the course (also written as ``course.json``). ``corpus`` and ``counted_concepts`` are G1's
    (``run_corpus()["corpus"]`` and ``["concepts"]``); ``master_lines`` goes to step 8 and nowhere else.
    """

    work = CourseWork(work_dir)
    say = progress or (lambda _text: None)
    curriculum = work.read(STEP_CURRICULUM, check_curriculum)
    if curriculum is None:
        if cancel is not None and cancel():
            raise LearningCourseStopped()
        say("Planning the modules and lessons...")
        curriculum = plan_curriculum(model, role_text, counted_concepts)
        work.write(STEP_CURRICULUM, curriculum)
    sources = work.read(STEP_SOURCES, lambda data: check_sources(data, curriculum))
    if sources is None:
        sources = gather_sources(fetcher, curriculum, model, progress=progress, counted_concepts=counted_concepts)
        work.write(STEP_SOURCES, sources)
    done = {}
    for module in curriculum["modules"]:
        stored = work.read(module_step(module["id"]))
        if stored is not None:
            done[module["id"]] = stored
    lessons = write_lessons(
        model, role_text, curriculum, sources, master_lines, counted_concepts, done=done,
        on_module=lambda module_id, written: work.write(module_step(module_id), written), cancel=cancel, progress=progress,
    )
    course = assemble_course(role_text, corpus, counted_concepts, curriculum, sources, lessons)
    work.write(STEP_COURSE, course)
    return course


__all__ = [
    "DONE_PERCENT",
    "JSON_OPENER",
    "LESSONS_MAX",
    "LESSONS_MIN",
    "LONG_CALL_TIMEOUT_SECONDS",
    "MAIN_TOOL_PAGES_MIN",
    "MODULE_TEXT_FAILED",
    "NO_VERIFIED_SOURCE",
    "SCHEMA_COURSE",
    "SCHEMA_CURRICULUM",
    "SCHEMA_MODULE",
    "SCHEMA_SOURCES",
    "STATUS_DONE",
    "STATUS_FAILED",
    "TOO_LITTLE_VERIFIED",
    "TOOL_BY_COUNT",
    "TOOL_BY_HOSTS",
    "TOOL_BY_PAGES",
    "TOOL_BY_TITLE",
    "CourseModel",
    "CourseWork",
    "LearningCourseError",
    "LearningCourseStopped",
    "MeteredCourseModel",
    "ToolCandidate",
    "assemble_course",
    "check_curriculum",
    "check_module",
    "check_sources",
    "choose_main_tool",
    "clean_glyphs",
    "contact_findings",
    "contact_shapes",
    "course_status",
    "course_urls",
    "curriculum_prompt",
    "curriculum_tool",
    "fix_module",
    "gather_sources",
    "guarded_master_lines",
    "lessons_prompt",
    "module_step",
    "named_at",
    "parse_curriculum",
    "parse_source_retry",
    "plan_curriculum",
    "read_master_lines",
    "run_course_steps",
    "scrub_contact",
    "source_strength",
    "sources_retry_prompt",
    "tool_aliases",
    "tool_pages",
    "usable_url",
    "write_lessons",
]
