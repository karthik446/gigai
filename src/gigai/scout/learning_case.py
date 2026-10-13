"""0.1.11.11 (learning center M3): the CASE EXERCISE and the ARTIFACT TEMPLATE, the second kind of practice page (pathway step 9).

A tool practice path (``learning_paths``) is a sequence of steps cited into ONE tool's public documentation. Some lessons
have no such tool: product management, engineering management, QA test design, SAP functional consulting,
Salesforce administration, the design exercises of the system design module. Their practice page is written, not
cited. :func:`write_case` runs ONE model call (``data/instructions/learning_case.md``, one retry with the validator's
message) that returns ``{"kind": "case" | "artifact", ...}``:

``case``: ``scenario`` (3 to 6 sentences), ``constraints`` (3 to 8 one-line items), ``deliverable`` (one line),
``reference_answer`` (5 to 9 sections, each ``{title, body}`` of 80 to 250 words), ``rubric`` (6 to 12 one-line checks,
each starting with a verb or "The answer") and ``common_mistakes`` (3 to 6 lines).

``artifact``: ``artifact_name`` (PRD, roadmap, test plan, ADR, runbook, functional spec, cutover plan, ...),
``template`` (6 to 12 sections, each ``{title, prompt}``), ``reference_example`` (``{situation, sections}``: the
same sections filled for a made-up situation, shown as model-written) and ``review_checklist`` (6 to 12 lines).

WHEN. :func:`learning_paths.run_paths_step` asks for one when a lesson's main tool has no verified documentation page
(today's ``path_unverified``: nothing to cite) or when the curriculum marks the lesson ``practice: "case"``.

THE PROMPT is built from the lesson's title, summary, subtopic titles and technologies, the role, and the lesson's
verified source TITLES by index only, all fenced as untrusted, as ``paths_prompt`` does. No resume line is an input
(this module reads none) and no address is ever given or accepted: a case page cites no page.

THE CODE CHECKS (:func:`parse_case_answer`; every refusal is a message the one retry carries): the JSON shape, every
section present, the lengths above, no URL, no code block or backtick, no command line, no email or phone shape
(``learning_course.contact_shapes``), no count of postings or mention of postings (``learning_shipped.POSTING_COUNT``),
no company named in the course's counted examples (``learning_paths._example_companies``), rubric lines that are
checks (a verb first, or "The answer"), and, for an artifact, an example whose sections are the template's. Two
unusable answers are ``case_model_failed``: the lesson has no practice page and is listed as not included.

THE STORED PAGE is ``<work>/paths/<lesson id>.json`` with ``kind`` set (``case`` or ``artifact``); a tool path carries
``kind: "tool"`` (and one written before ``kind`` existed reads as ``tool``). A case page holds no source, so the audit
has no link to check on it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import re
from typing import Any

from .learning_corpus import LearningCorpusError, _error_text, _json_object, _render, _template
from .learning_course import (
    JSON_OPENER,
    LONG_CALL_TIMEOUT_SECONDS,
    CourseModel,
    LearningCourseStopped,
    _clean_tree,
    contact_shapes,
)
from .learning_fetch import clean_text
from .learning_render import KIND_ARTIFACT, KIND_CASE, validate_path
from .untrusted_text import fence_untrusted_posting

CASE_MODEL_FAILED = "case_model_failed"
OUTCOME_CASE = "case_kept"
KINDS = (KIND_CASE, KIND_ARTIFACT)

#: What a case page must hold.
SCENARIO_SENTENCES = (3, 6)
CONSTRAINTS = (3, 8)
ANSWER_SECTIONS = (5, 9)
SECTION_WORDS = (80, 250)
RUBRIC = (6, 12)
MISTAKES = (3, 6)
#: What an artifact page must hold.
TEMPLATE_SECTIONS = (6, 12)
PROMPT_SENTENCES = (1, 2)
EXAMPLE_WORDS = (20, 250)
SITUATION_SENTENCES = (1, 3)
CHECKLIST = (6, 12)
#: Character bounds of a one-line item, a section title, a name.
LINE_CHARS_MAX = 240
TITLE_CHARS_MAX = 80
NAME_CHARS_MAX = 60
PROMPT_CHARS_MAX = 400
BODY_CHARS_MAX = 2500
SOURCES_SHOWN = 12
SOURCE_TITLE_CHARS = 160

_CASE_RESOURCE = ("scout", "data", "instructions", "learning_case.md")
_WORDS = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
#: A mention of postings or a count of them: only code states those (``learning_shipped.POSTING_COUNT`` is the count
#: shape the export's scan refuses; the plain words are refused here so the retry says so before the export does).
_POSTING_WORDS = re.compile(r"(?i)\b(?:job\s+)?postings?\b|\bjob\s+(?:ads?|listings?|descriptions?)\b")

#: The keys a page of each kind holds (besides ``concept`` and ``kind``).
CASE_KEYS = ("scenario", "constraints", "deliverable", "reference_answer", "rubric", "common_mistakes")
ARTIFACT_KEYS = ("artifact_name", "template", "reference_example", "review_checklist")

#: Base forms of the verbs a rubric line may start with ("Names", "States", "Derives"; "name" is the imperative).
_VERBS = frozenset("""
    acknowledge address apply argue ask avoid back bound build calculate catch check choose cite clarify classify commit compare compute confirm
    connect consider convert cost count cover decide defend define derive describe design distinguish draw estimate explain find flag give
    handle identify include justify keep limit link list make map mention modify monitor name note offer order pick plan prioritise prioritize propose
    protect provide quantify rank reach recognise recognize reduce reject report resolve review rule schedule secure select separate
    scope sequence set show simplify size sketch spot specify split state support test trace track use verify weigh write
""".split())


#: A word that follows a NOUN, never a verb in the present tense: "Cost is named", "Order should be clear".
_NOT_AFTER_A_VERB = frozenset("is are was were has have had should would will can may must does do did".split())


class CaseInvalid(ValueError):
    """A model answer the checks cannot use: the reason goes into the one retry."""


def _verb_forms(base: str) -> set[str]:
    forms = {base, base + "s", base + "es"}
    if base.endswith("y"):
        forms.add(base[:-1] + "ies")
    if base.endswith("e"):
        forms.add(base + "d")
    return forms


_VERB_FORMS = frozenset(form for base in _VERBS for form in _verb_forms(base))


def starts_with_a_check(line: str) -> bool:
    """Whether a rubric line is a check: it starts with a verb of :data:`_VERBS` (any form) or with "The answer"."""

    if re.match(r"(?i)the\s+answer\b", line.strip()):
        return True
    found = _WORDS.findall(line.lower())[:2]
    # "Cost is named" is a noun with a verb after it, not a check that starts with one: the word after a verb is never "is".
    return bool(found) and found[0] in _VERB_FORMS and (len(found) < 2 or found[1] not in _NOT_AFTER_A_VERB)


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


def _titles(lesson: Mapping[str, Any]) -> list[str]:
    found = []
    for sub in lesson.get("subtopics") or []:
        title = clean_text(str(sub.get("title", ""))) if isinstance(sub, Mapping) else ""
        if title:
            found.append(title)
    return found


def case_prompt(
    *, role_text: str, lesson: Mapping[str, Any], source_titles: Sequence[str] = (), validation_error: BaseException | str | None = None,
) -> str:
    """The case-exercise prompt of one lesson.

    Of the lesson it reads ``title``, ``summary``, ``subtopics[].title`` and ``technologies``, and nothing else (not a
    marker, not an evidence quote, not a subtopic body); of its sources only the TITLES, numbered, never an address.
    No resume line is an input: this function takes none and this module reads none. Everything an earlier model
    call or a stranger wrote is fenced as untrusted.
    """

    subtopics = "\n".join(f"  - {title}" for title in _titles(lesson)) or "  (none)"
    names = ", ".join(clean_text(str(name)) for name in lesson.get("technologies") or [] if clean_text(str(name))) or "(none)"
    block = (
        f"LESSON: {clean_text(str(lesson.get('title', '')))}\n"
        f"SUMMARY: {clean_text(str(lesson.get('summary', '')))}\n"
        f"SUBTOPICS:\n{subtopics}\n"
        f"TECHNOLOGIES NAMED IN THE LESSON: {names}"
    )
    listed = [clean_text(title)[:SOURCE_TITLE_CHARS] for title in source_titles if clean_text(title)][:SOURCES_SHOWN]
    sources = "\n".join(f"  [{index}] {title}" for index, title in enumerate(listed)) or "  (none)"
    return _render(
        _template(_CASE_RESOURCE),
        {
            "opener": JSON_OPENER, "role": clean_text(role_text), "lesson": fence_untrusted_posting(block),
            "sources": fence_untrusted_posting(sources), "validation_error": _error_text(validation_error),
        },
    )


# ---------------------------------------------------------------------------
# The answer: its shape, its text
# ---------------------------------------------------------------------------


def _problem(text: str, companies: Iterable[str]) -> str | None:
    """Why a string may not stand on a case page; ``None`` when it may. ``text`` is already one line."""

    from .learning_paths import string_problem
    from .learning_shipped import POSTING_COUNT

    found = string_problem(text)
    if found:
        return f"it held {found}"
    if contact_shapes(text):
        return "it held contact data (an email address or a phone number)"
    if _POSTING_WORDS.search(text) or POSTING_COUNT.search(text):
        return "it spoke of job postings or a count of them"
    low = text.casefold()
    for company in companies:
        if company.strip() and re.search(rf"(?<!\w){re.escape(company.casefold())}(?!\w)", low):
            return "it named a real company (invent every name)"
    return None


class _Checker:
    """The text checks of one answer, with the course's example companies."""

    def __init__(self, companies: Iterable[str]) -> None:
        self.companies = [company for company in companies if isinstance(company, str) and company.strip()]

    def text(self, value: object, ctx: str, *, chars: int | None = None) -> str:
        """One string of the page as it is kept: whitespace collapsed to single spaces; ``CaseInvalid`` when it may not stand."""

        if not isinstance(value, str) or not value.strip():
            raise CaseInvalid(f"{ctx} is missing or empty")
        text = " ".join(value.split())
        if chars is not None and len(text) > chars:
            raise CaseInvalid(f"{ctx} is {len(text)} characters; at most {chars} on one line")
        why = _problem(text, self.companies)
        if why:
            raise CaseInvalid(f"{ctx}: {why}")
        return text

    def lines(self, value: object, ctx: str, bounds: tuple[int, int]) -> list[str]:
        if not isinstance(value, list):
            raise CaseInvalid(f"{ctx} must be a list of {bounds[0]} to {bounds[1]} one-line items")
        if not bounds[0] <= len(value) <= bounds[1]:
            raise CaseInvalid(f"{ctx} holds {len(value)} items; {bounds[0]} to {bounds[1]} needed")
        return [self.text(item, f"{ctx} item {number}", chars=LINE_CHARS_MAX) for number, item in enumerate(value, 1)]

    def sections(
        self, value: object, ctx: str, bounds: tuple[int, int], key: str, words: tuple[int, int] | None, sentences: tuple[int, int] | None = None,
    ) -> list[dict[str, str]]:
        if not isinstance(value, list):
            raise CaseInvalid(f"{ctx} must be a list of {bounds[0]} to {bounds[1]} sections")
        if not bounds[0] <= len(value) <= bounds[1]:
            raise CaseInvalid(f"{ctx} holds {len(value)} sections; {bounds[0]} to {bounds[1]} needed")
        kept = []
        for number, item in enumerate(value, 1):
            where = f"{ctx} section {number}"
            if not isinstance(item, Mapping):
                raise CaseInvalid(f"{where} is not an object with a title and a {key}")
            title = self.text(item.get("title"), f"{where} title", chars=TITLE_CHARS_MAX)
            limit = PROMPT_CHARS_MAX if key == "prompt" else BODY_CHARS_MAX
            body = self.text(item.get(key), f"{where} {key}", chars=limit)
            count = len(_WORDS.findall(body))
            if words is not None and not words[0] <= count <= words[1]:
                raise CaseInvalid(f"{where} {key} holds {count} words; {words[0]} to {words[1]} words needed")
            if sentences is not None:
                held = len(_sentences(body))
                if not sentences[0] <= held <= sentences[1]:
                    raise CaseInvalid(f"{where} {key} holds {held} sentences; {sentences[0]} to {sentences[1]} needed")
            kept.append({"title": title, key: body})
        return kept


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_END.split(text.strip()) if part.strip()]


def _in_bounds(text: str, ctx: str, bounds: tuple[int, int]) -> None:
    held = len(_sentences(text))
    if not bounds[0] <= held <= bounds[1]:
        raise CaseInvalid(f"{ctx} holds {held} sentences; {bounds[0]} to {bounds[1]} needed")


def validate_case(decoded: Mapping[str, Any], companies: Iterable[str] = ()) -> dict[str, Any]:
    """The page of a decoded answer (``kind`` and its fields, text collapsed and checked), or ``CaseInvalid`` naming the first thing wrong."""

    check = _Checker(companies)
    kind = decoded.get("kind")
    if kind not in KINDS:
        raise CaseInvalid(f"kind must be one of: {', '.join(KINDS)}")
    if kind == KIND_CASE:
        scenario = check.text(decoded.get("scenario"), "scenario")
        _in_bounds(scenario, "scenario", SCENARIO_SENTENCES)
        rubric = check.lines(decoded.get("rubric"), "rubric", RUBRIC)
        for number, line in enumerate(rubric, 1):
            if not starts_with_a_check(line):
                raise CaseInvalid(f"rubric item {number} must be a check: start it with a verb (Names, States, Derives, ...) or with 'The answer'")
        return {
            "kind": KIND_CASE, "scenario": scenario,
            "constraints": check.lines(decoded.get("constraints"), "constraints", CONSTRAINTS),
            "deliverable": check.text(decoded.get("deliverable"), "deliverable", chars=LINE_CHARS_MAX),
            "reference_answer": check.sections(decoded.get("reference_answer"), "reference_answer", ANSWER_SECTIONS, "body", SECTION_WORDS),
            "rubric": rubric, "common_mistakes": check.lines(decoded.get("common_mistakes"), "common_mistakes", MISTAKES),
        }
    template = check.sections(decoded.get("template"), "template", TEMPLATE_SECTIONS, "prompt", None, PROMPT_SENTENCES)
    example = decoded.get("reference_example")
    if not isinstance(example, Mapping):
        raise CaseInvalid("reference_example must be an object with a situation and sections")
    situation = check.text(example.get("situation"), "reference_example situation")
    _in_bounds(situation, "reference_example situation", SITUATION_SENTENCES)
    filled = check.sections(example.get("sections"), "reference_example", (len(template), len(template)), "body", EXAMPLE_WORDS)
    for number, (asked, given) in enumerate(zip(template, filled), 1):
        if asked["title"].casefold() != given["title"].casefold():
            raise CaseInvalid(f"reference_example section {number} must have the title of template section {number} ({asked['title']!r})")
    return {
        "kind": KIND_ARTIFACT, "artifact_name": check.text(decoded.get("artifact_name"), "artifact_name", chars=NAME_CHARS_MAX),
        "template": template, "reference_example": {"situation": situation, "sections": filled, "written_by": "model"},
        "review_checklist": check.lines(decoded.get("review_checklist"), "review_checklist", CHECKLIST),
    }


def parse_case_answer(raw: str, companies: Iterable[str] = ()) -> dict[str, Any]:
    """The page of a model answer (see :func:`validate_case`), or ``CaseInvalid`` / ``LearningCorpusError`` naming the first thing wrong."""

    decoded = _clean_tree(_json_object(raw, "case_invalid"))
    return validate_case(decoded, companies)


def build_case_path(lesson_id: str, page: Mapping[str, Any]) -> dict[str, Any]:
    """The stored page: the checked answer under the lesson's id."""

    return {"concept": lesson_id, **page}


def check_case_path(data: object, lesson_id: str) -> bool:
    """Whether a stored page is one this step wrote for ``lesson_id`` and the renderer still accepts (skip-if-valid)."""

    if not isinstance(data, Mapping) or data.get("concept") != lesson_id or data.get("kind") not in KINDS:
        return False
    keys = CASE_KEYS if data["kind"] == KIND_CASE else ARTIFACT_KEYS
    body = {"kind": data["kind"], **{key: data[key] for key in keys if key in data}}
    try:
        again = validate_case(body)
    except CaseInvalid:
        return False
    stored = {key: value for key, value in data.items() if key != "concept"}
    return again == stored and not validate_path(dict(data), "path")


# ---------------------------------------------------------------------------
# The step for one lesson
# ---------------------------------------------------------------------------


def write_case(
    model: CourseModel, *, role_text: str, lesson: Mapping[str, Any], source_titles: Sequence[str] = (), companies: Iterable[str] = (),
    cancel: Callable[[], bool] | None = None, log: list[str] | None = None,
) -> dict[str, Any] | None:
    """One call (one more with the validator's message) and the code checks: the stored page of ``lesson``, or ``None`` (``case_model_failed``).

    A model that cannot be reached raises what ``model.ask`` raises (``model_unavailable``): that is not a verdict on
    the lesson. A cancel raises ``LearningCourseStopped``. Each refused answer adds a line to ``log``.
    """

    lines = log if log is not None else []
    lesson_id = str(lesson["id"])
    error: BaseException | None = None
    for attempt in (1, 2):
        if cancel is not None and cancel():
            raise LearningCourseStopped()
        raw = model.ask(
            case_prompt(role_text=role_text, lesson=lesson, source_titles=source_titles, validation_error=error),
            items=1, timeout_seconds=LONG_CALL_TIMEOUT_SECONDS,
        )
        try:
            return build_case_path(lesson_id, parse_case_answer(raw, companies))
        except (CaseInvalid, LearningCorpusError) as exc:
            model.invalid_output()
            error = exc
            lines.append(f"{lesson_id} case attempt {attempt}: {_error_text(exc)}")
    return None


__all__ = [
    "ARTIFACT_KEYS",
    "CASE_KEYS",
    "CASE_MODEL_FAILED",
    "KINDS",
    "OUTCOME_CASE",
    "CaseInvalid",
    "build_case_path",
    "case_prompt",
    "check_case_path",
    "parse_case_answer",
    "starts_with_a_check",
    "validate_case",
    "write_case",
]
