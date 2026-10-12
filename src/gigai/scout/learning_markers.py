"""0.1.11.10 shipped lessons, slice 5 (core): THE MARKER CALL, the one small model call of a shipped course.

A shipped course is generic: its lesson pages say nothing about one user. What the user's master resume shows of
it (known / some / new, 'You have', 'Missing') is on ONE locally built page (``learning_pathway_local``), and this
module produces that page's ``markers``. A code match cannot do it (spike section 3: the best matcher agreed with
a real run on 58% of the subtopics and found none of its KNOWN), so one model call compares the shipped CATALOG
with the master's lines, and code checks every answer.

THE RESUME IS ALWAYS THE REFERENCE. There is no switch here and no option: a caller that has a master compares
with it. A master with no lines makes every subtopic NEW without any call.

WHAT THE PROMPT HOLDS (:func:`markers_prompt`, ``data/instructions/learning_markers.md``):

- of the shipped catalog, per lesson: its id, title and technologies, and per subtopic its id and title. No lesson
  body, no posting sentence, no company, no count: nothing a stranger wrote is in this prompt.
- the master's lines as ``id | text``. The master holds no contact line by construction (``master_store`` strips
  at import); every line still goes through ``resume_privacy.guard_private`` in the builder's own body
  (``test_resume_privacy_static.py`` pins that). This module reads no resume: the caller hands the lines in
  (``learning_course.read_master_lines``).

ONE CALL OR ONE PER MODULE (:func:`plan_calls`). The whole course goes into one prompt while that prompt is at
most :data:`PROMPT_CHARS_MAX` characters; a larger course is split by MODULE, each call carrying every master
line. The bound is one and a half times the measured course: the MLOps catalog (35 lessons, 163 subtopics) with a
52-line master is a prompt of 39,849 characters, and that one call took 92 s (21,476 tokens in and 11,842 out as
the meter counted them, the CLI's own preamble and the model's thinking included), well inside
:data:`MARKERS_TIMEOUT_SECONDS`. A module that is over the bound by itself (a very long master) is still one
call: a module is the smallest part.

ZERO INVENTED KNOWLEDGE is the bar, and code holds it (:func:`check_markers`), whatever the model answered:

1. a key that is not a subtopic of the catalog is IGNORED (counted); a lesson or subtopic the model did not
   answer is NEW; no marker is ever made for something the catalog does not hold;
2. KNOWN and SOME go through the generated course's own check, ``learning_course._marker``: the evidence id is a
   line of the master AS SENT, the quote is a verbatim part of that line (whitespace and case folded), it is no
   contact shape; a skills-list line (``s-...``) is SOME at most; SOME carries a ``missing`` sentence. Anything
   else is NEW;
3. the RELATED-LINE check, which a generated course does not have because its call also wrote the lesson: the
   cited master line must share at least one SIGNIFICANT word (:func:`significant_words`: not a stopword, not one
   of the few generic words of :data:`GENERIC_WORDS`) with the subtopic's title or the lesson's technologies; for
   a SOME it is also enough that the model's own ``missing`` sentence names the gap in the subtopic's words
   (adjacent work shares no word with the subtopic by its nature). A KNOWN or SOME that fails is NEW.

Every downgrade is counted (``stats``) and named by its key in ``log``; neither holds a word of the resume.

THE CALL (:func:`run_markers`) is metered as kind ``learning`` (``learning_course.MeteredCourseModel``, the user's
configured target) with ``items`` = the subtopics asked about. A timeout is retried once, at once
(``learning_job``'s rule: waiting does not shorten a long answer); an answer that is not the JSON asked for gets
one retry that names the problem; :data:`CALLS_PER_PART_MAX` calls per part is the cap. Nothing is stored here:
the caller writes the page and its record (``learning_pathway_local.build_local_page``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
from pathlib import Path
import re
from typing import Any

from .learning_corpus import LearningCorpusError, _error_text, _json_object, _render, _template
from .learning_course import (
    LONG_CALL_TIMEOUT_SECONDS, MARKER_KNOWN, MARKER_NEW, MARKER_SOME, CourseModel, LearningCourseStopped, MeteredCourseModel,
    _clean_tree, _flat, _marker, _pairs, guarded_master_lines,
)
from .learning_store import LearningError
from .resume_privacy import guard_private

#: One prompt for the whole course up to this size; above it one call per module (see the module text).
PROMPT_CHARS_MAX = 60_000
#: The marker call answers with up to one entry per subtopic; the lesson-text calls' own long timeout applies.
MARKERS_TIMEOUT_SECONDS = LONG_CALL_TIMEOUT_SECONDS
#: The calls one part may take: the call, one retry of a timeout, one retry of an unusable answer.
CALLS_PER_PART_MAX = 3

#: The estimate's arithmetic (:func:`estimate_markers`), set from the ONE measured call of the module text:
#: characters per token of the prompt, what the meter counts on top of the prompt per call (the CLI's own
#: preamble), the output per subtopic asked about (thinking included) and the pace of the answer.
CHARS_PER_TOKEN = 4
CALL_OVERHEAD_TOKENS_IN = 11_500
OUT_TOKENS_PER_SUBTOPIC = 70
SECONDS_PER_CALL = 10.0
OUT_TOKENS_PER_SECOND = 140.0

MARKERS_INVALID = "markers_invalid"
MODEL_UNAVAILABLE = "model_unavailable"
CAP_REACHED = "cap_reached"

_MARKERS_RESOURCE = ("scout", "data", "instructions", "learning_markers.md")
_NO_RESUME = "(no resume lines: every subtopic is NEW)"
_TOKEN = re.compile(r"[a-z0-9+#]+")

#: Words that carry no subject: a shared one is not a reason to call a master line related to a subtopic.
STOPWORDS = frozenset(
    "a about above across after again against all also am an and any are as at be because been before being below "
    "between both but by can could did do does doing down during each end few for from further had has have having "
    "he her here hers him his how i if in into is it its just many may me more most much must my new no nor not now "
    "of off on once one only onto or other our out over own per same she should so some such than that the their "
    "them then there these they this those through to too two under until up upon us very via was we were what when "
    "where which while who whom why will with within without would you your".split()
)
#: The small generic list: words of any engineering resume and any course title. Role words (ml, ai, cloud) are
#: NOT here on purpose: the list must not know a role.
GENERIC_WORDS = frozenset(
    "application applications approach approaches based basic basics best build building built common company core "
    "create created data design designed develop developed development engineer engineering engineers experience "
    "good high key large led level main make manage managed management multiple overview pattern patterns platform "
    "platforms practice practices process processes production project projects service services software solution "
    "solutions support system systems team teams tool tools use used using work worked working".split()
)


class LearningMarkersError(LearningError):
    """The comparison could not be made; ``code`` is the API/CLI error code. Nothing was written."""


# ---------------------------------------------------------------------------
# The catalog as the call reads it
# ---------------------------------------------------------------------------


def catalog_lessons(catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The catalog's lessons as ``{id, title, module, technologies, subtopics: [{id, title}]}``, each text on one line.

    Refused (``invalid_value``): a catalog without a lesson list, a lesson or subtopic without an id, an id that
    holds a slash or a space (a key is ``<lesson id>/<subtopic id>``), an id used twice.
    """

    def bad(message: str) -> LearningMarkersError:
        return LearningMarkersError("invalid_value", f"the shipped catalog cannot be compared: {message}")

    def identifier(value: object, what: str) -> str:
        if not isinstance(value, str) or not value or value != _flat(value) or "/" in value or " " in value or "|" in value:
            raise bad(f"{what} has no usable id")
        return value

    raw = catalog.get("lessons") if isinstance(catalog, Mapping) else None
    if not isinstance(raw, list):
        raise bad("it holds no lessons")
    lessons: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lesson in raw:
        if not isinstance(lesson, Mapping):
            raise bad("a lesson is not an object")
        lesson_id = identifier(lesson.get("id"), "a lesson")
        if lesson_id in seen:
            raise bad(f"lesson {lesson_id} is listed twice")
        seen.add(lesson_id)
        subtopics: list[dict[str, str]] = []
        used: set[str] = set()
        for sub in lesson.get("subtopics") or []:
            if not isinstance(sub, Mapping):
                raise bad(f"a subtopic of lesson {lesson_id} is not an object")
            sub_id = identifier(sub.get("id"), f"a subtopic of lesson {lesson_id}")
            if sub_id in used:
                raise bad(f"lesson {lesson_id} lists subtopic {sub_id} twice")
            used.add(sub_id)
            subtopics.append({"id": sub_id, "title": _flat(str(sub.get("title") or ""))})
        lessons.append({
            "id": lesson_id, "title": _flat(str(lesson.get("title") or "")), "module": str(lesson.get("module") or ""),
            "technologies": [_flat(name) for name in lesson.get("technologies") or [] if isinstance(name, str) and _flat(name)],
            "subtopics": subtopics,
        })
    return lessons


def _lesson_keys(lesson: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(key, title)`` of what one lesson is asked about: its subtopics, or the lesson itself when it has none."""

    if not lesson["subtopics"]:
        return [(lesson["id"], lesson["title"])]
    return [(f"{lesson['id']}/{sub['id']}", sub["title"]) for sub in lesson["subtopics"]]


def marker_keys(catalog: Mapping[str, Any]) -> list[str]:
    """Every key the markers of this catalog hold, in the catalog's order (``learning_pathway_local``'s format)."""

    return [key for lesson in catalog_lessons(catalog) for key, _title in _lesson_keys(lesson)]


def _lesson_block(lesson: Mapping[str, Any]) -> str:
    technologies = ", ".join(lesson["technologies"]) or "(none named)"
    lines = [f"LESSON {lesson['id']} | {lesson['title']} | technologies: {technologies}"]
    lines.extend(f"  {key} | {title}" for key, title in _lesson_keys(lesson))
    return "\n".join(lines)


def markers_prompt(
    *, role: str, lessons: Sequence[Mapping[str, Any]], master_lines: object, validation_error: BaseException | str | None = None,
) -> str:
    """The marker prompt for ``lessons`` (:func:`catalog_lessons`' shape): the ONE prompt of a shipped course that carries resume lines.

    Every master line goes through ``resume_privacy.guard_private`` here, in this function's own body
    (``test_resume_privacy_static.py`` pins that), and is written as one ``id | text`` line: exactly the lines
    ``learning_course.guarded_master_lines`` returns, which is what a quote is checked against.
    """

    resume = []
    for item_id, text in _pairs(master_lines):
        shown = _flat(guard_private(text))
        if _flat(item_id) and shown:
            resume.append(f"{_flat(item_id)} | {shown}")
    return _render(
        _template(_MARKERS_RESOURCE),
        {
            "role": _flat(role) or "(not named)", "lessons": "\n".join(_lesson_block(lesson) for lesson in lessons),
            "resume_lines": "\n".join(resume) or _NO_RESUME, "validation_error": _error_text(validation_error),
        },
    )


def plan_calls(
    catalog: Mapping[str, Any], master_lines: object, *, prompt_chars_max: int = PROMPT_CHARS_MAX,
) -> list[list[dict[str, Any]]]:
    """The lessons of each call: one part while the whole prompt fits ``prompt_chars_max``, else one part per module.

    Lessons stay in the catalog's order; a module is the lessons that name it. ``[]`` when the catalog holds no
    lesson.
    """

    lessons = catalog_lessons(catalog)
    if not lessons:
        return []
    role = str(catalog.get("role") or "")
    if len(markers_prompt(role=role, lessons=lessons, master_lines=master_lines)) <= prompt_chars_max:
        return [lessons]
    parts: dict[str, list[dict[str, Any]]] = {}
    for lesson in lessons:
        parts.setdefault(lesson["module"], []).append(lesson)
    return list(parts.values())


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def _stem(word: str) -> str:
    """A word without its plural, its verb ending and a final ``e``: 'pipelines' meets 'pipeline', 'deployed' meets 'deploying'.

    Crude on purpose and the same on both sides of a comparison; it never has to be a real word.
    """

    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 5 and word.endswith(("sses", "xes", "ches", "shes")):
        word = word[:-2]
    elif len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for ending in ("ment", "ing", "ed"):
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            word = word[: -len(ending)]
            break
    return word[:-1] if len(word) > 4 and word.endswith("e") else word


def significant_words(text: str) -> frozenset[str]:
    """The words of ``text`` that say what it is about, stemmed: no stopword, no generic word, no bare number.

    A word is letters, digits, ``+`` and ``#`` (``c++``, ``k8s``, ``ci`` and ``cd`` of 'CI/CD'); one character
    is not a word.
    """

    found = set()
    for word in _TOKEN.findall(text.casefold()):
        if len(word) < 2 or word.isdigit() or word in STOPWORDS or word in GENERIC_WORDS:
            continue
        stem = _stem(word)
        if stem not in STOPWORDS and stem not in GENERIC_WORDS:
            found.add(stem)
    return frozenset(found)


def _empty_stats() -> dict[str, int]:
    return {"downgraded": 0, "downgraded_evidence": 0, "downgraded_unrelated": 0, "capped_skills": 0, "ignored": 0}


def _new() -> dict[str, Any]:
    return {"marker": MARKER_NEW, "evidence": None, "missing": None}


def check_markers(
    answer: Mapping[str, Any], lessons: Sequence[Mapping[str, Any]], master_items: Mapping[str, str], *,
    stats: dict[str, int] | None = None, log: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """One answer after the checks: a marker for EVERY key of ``lessons``, in ``learning_pathway_local``'s format.

    ``answer`` is the decoded JSON object, ``master_items`` the master lines as sent (id to text). The three
    checks of the module text apply; ``stats`` counts what they did (``ignored``: keys the catalog does not
    hold; ``downgraded``: KNOWN or SOME answers that are NEW now, of which ``downgraded_evidence`` by the id and
    quote check and ``downgraded_unrelated`` by the related-line check; ``capped_skills``: KNOWN from a skills
    line, kept as SOME). Raises :class:`LearningMarkersError` (``markers_invalid``) when ``answer`` has no
    ``markers`` object.
    """

    counted = stats if stats is not None else _empty_stats()
    notes = log if log is not None else []
    given = answer.get("markers") if isinstance(answer, Mapping) else None
    if not isinstance(given, Mapping):
        raise LearningMarkersError(MARKERS_INVALID, "markers must be an object from KEY to entry")
    topics: dict[str, frozenset[str]] = {}
    for lesson in lessons:
        shared = " ".join(lesson["technologies"])
        for key, title in _lesson_keys(lesson):
            topics[key] = significant_words(f"{title} {shared}")
    checked = {key: _new() for key in topics}
    for key, entry in given.items():
        if key not in topics:
            counted["ignored"] += 1
            notes.append("an answer for a key the catalog does not hold was ignored")
            continue
        asked = entry.get("marker") if isinstance(entry, Mapping) else None
        if asked not in (MARKER_KNOWN, MARKER_SOME):
            continue
        label, evidence, missing = _marker(entry, master_items, key, notes)
        if label == MARKER_NEW:
            counted["downgraded"] += 1
            counted["downgraded_evidence"] += 1
            continue
        assert evidence is not None
        if asked == MARKER_KNOWN and label == MARKER_SOME:
            counted["capped_skills"] += 1
        topic = topics[key]
        related = bool(topic & significant_words(master_items[evidence["id"]]))
        if not related and asked == MARKER_SOME and isinstance(entry.get("missing"), str):
            related = bool(topic & significant_words(entry["missing"]))
        if not related:
            counted["downgraded"] += 1
            counted["downgraded_unrelated"] += 1
            notes.append(f"{key}: {asked} from a line that shares no significant word with the subtopic -> NEW")
            continue
        checked[key] = {"marker": label, "evidence": evidence, "missing": missing}
    return checked


# ---------------------------------------------------------------------------
# The estimate and the call
# ---------------------------------------------------------------------------


def estimate_markers(catalog: Mapping[str, Any], master_lines: object, *, prompt_chars_max: int = PROMPT_CHARS_MAX) -> dict[str, int]:
    """``{calls, tokens_in_estimate, tokens_out_estimate, seconds}`` of the comparison. Makes no call.

    Arithmetic on the prompts that WOULD be sent (:data:`CHARS_PER_TOKEN`, plus :data:`CALL_OVERHEAD_TOKENS_IN`
    per call) and on the subtopic count (:data:`OUT_TOKENS_PER_SUBTOPIC`), with constants from one measured
    call: an estimate, not a history. A master with no lines costs nothing: all four are 0.
    """

    parts = plan_calls(catalog, master_lines, prompt_chars_max=prompt_chars_max) if guarded_master_lines(master_lines) else []
    role = str(catalog.get("role") or "")
    chars = sum(len(markers_prompt(role=role, lessons=part, master_lines=master_lines)) for part in parts)
    tokens_out = sum(len(_lesson_keys(lesson)) for part in parts for lesson in part) * OUT_TOKENS_PER_SUBTOPIC
    return {
        "calls": len(parts), "tokens_in_estimate": math.ceil(chars / CHARS_PER_TOKEN) + len(parts) * CALL_OVERHEAD_TOKENS_IN, "tokens_out_estimate": tokens_out,
        "seconds": math.ceil(len(parts) * SECONDS_PER_CALL + tokens_out / OUT_TOKENS_PER_SECOND),
    }


def _timed_out(exc: BaseException) -> bool:
    from .call_metrics import ERROR_TIMEOUT, error_code_of

    return error_code_of(exc.__cause__ or exc) == ERROR_TIMEOUT


class _Calls:
    """The calls of one comparison against its cap."""

    def __init__(self, cap: int, cancel: Callable[[], bool] | None) -> None:
        self.made, self._cap, self._cancel = 0, cap, cancel

    def take(self) -> None:
        if self._cancel is not None and self._cancel():
            raise LearningCourseStopped()
        if self.made >= self._cap:
            raise LearningMarkersError(CAP_REACHED, f"the comparison stopped at its cap of {self._cap} model calls")
        self.made += 1


def _ask(model: CourseModel, prompt: str, items: int, calls: _Calls) -> str:
    """One answer; a call that timed out is asked once more, at once. Any other failure is the caller's."""

    for attempt in (1, 2):
        calls.take()
        try:
            return model.ask(prompt, items=items, timeout_seconds=MARKERS_TIMEOUT_SECONDS)
        except LearningError as exc:
            if exc.code != MODEL_UNAVAILABLE or attempt == 2 or not _timed_out(exc):
                raise
    raise AssertionError("unreachable")


def run_markers(
    home_root: Path, target: Path, catalog: Mapping[str, Any], master_lines: object, model_port: CourseModel | None = None, *,
    prompt_chars_max: int = PROMPT_CHARS_MAX, max_calls: int | None = None, cancel: Callable[[], bool] | None = None,
    progress: Callable[[str], None] | None = None, log: list[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int | None], dict[str, int]]:
    """Compare a shipped course with the master: ``(markers, usage, stats)``. Writes nothing.

    ``catalog`` is the shipped ``catalog.json``; ``master_lines`` the master's ``(id, text)`` pairs (or a
    mapping), as ``learning_course.read_master_lines`` returns them. ``model_port`` answers the prompts
    (``learning_course.CourseModel``); left out, the user's configured target is used, metered as kind
    ``learning``, and closed afterwards.

    ``markers``: one entry for every subtopic of the catalog (and for a lesson that has no subtopic), key
    ``<lesson id>/<subtopic id>``, value ``{"marker", "evidence": {"id", "line"} | None, "missing"}``: what
    ``learning_pathway_local.build_local_page`` takes. A lesson's own label is left to that page, which derives it
    from the subtopics as a generated course does.
    ``usage``: ``calls`` made, and ``tokens_in`` / ``tokens_out`` as the meter reported them for those calls
    (``None`` when it reported none: a port that is not metered). The calls are read with
    ``call_metrics.capture_calls``, so a caller's own open capture block does not see them: read them here.
    ``stats``: :func:`check_markers`' counts.

    A master with no lines, or a catalog with no lesson, makes no call. Raises :class:`LearningMarkersError`
    ``markers_invalid`` when a part's two answers were both unusable, ``cap_reached`` at ``max_calls`` (default
    :data:`CALLS_PER_PART_MAX` per part); a model failure is the port's own ``LearningError``
    (``model_unavailable``), after the one retry of a timeout; ``learning_course.LearningCourseStopped`` when
    ``cancel`` says stop. In every such case no markers are returned: a partial comparison is never shown.
    """

    from .call_metrics import capture_calls, total_metrics

    say = progress or (lambda _text: None)
    notes = log if log is not None else []
    stats = _empty_stats()
    usage: dict[str, int | None] = {"calls": 0, "tokens_in": None, "tokens_out": None}
    markers = {key: _new() for key in marker_keys(catalog)}
    master_items = dict(guarded_master_lines(master_lines))
    parts = plan_calls(catalog, master_lines, prompt_chars_max=prompt_chars_max) if master_items else []
    if not parts:
        return markers, usage, stats

    role = str(catalog.get("role") or "")
    calls = _Calls(max_calls if max_calls is not None else len(parts) * CALLS_PER_PART_MAX, cancel)
    model = model_port if model_port is not None else MeteredCourseModel(Path(home_root), Path(target))
    try:
        with capture_calls() as captured:
            try:
                for number, part in enumerate(parts, 1):
                    say("Comparing the course with your resume" + (f" (part {number} of {len(parts)})" if len(parts) > 1 else ""))
                    items = sum(len(_lesson_keys(lesson)) for lesson in part)
                    error: BaseException | None = None
                    checked: dict[str, dict[str, Any]] | None = None
                    for _attempt in (1, 2):
                        raw = _ask(model, markers_prompt(role=role, lessons=part, master_lines=master_lines, validation_error=error), items, calls)
                        part_stats: dict[str, int] = _empty_stats()
                        part_log: list[str] = []
                        try:
                            answer = _clean_tree(_json_object(raw, MARKERS_INVALID))
                            checked = check_markers(answer, part, master_items, stats=part_stats, log=part_log)
                        except (LearningCorpusError, LearningMarkersError) as exc:
                            model.invalid_output()
                            error = exc
                            notes.append(f"part {number}: an answer was not usable ({_error_text(exc)})")
                            continue
                        for name, count in part_stats.items():
                            stats[name] += count
                        notes.extend(part_log)
                        break
                    if checked is None:
                        raise LearningMarkersError(MARKERS_INVALID, "the model's answer could not be read as markers; nothing was compared")
                    markers.update(checked)
            finally:
                usage["calls"] = calls.made
                total = total_metrics(captured)
                if total is not None:
                    usage["tokens_in"], usage["tokens_out"] = total.input_tokens, total.output_tokens
    finally:
        if model_port is None:
            model.close()  # type: ignore[attr-defined]
    return markers, usage, stats


__all__ = [
    "CALLS_PER_PART_MAX",
    "GENERIC_WORDS",
    "LearningMarkersError",
    "MARKERS_TIMEOUT_SECONDS",
    "PROMPT_CHARS_MAX",
    "STOPWORDS",
    "catalog_lessons",
    "check_markers",
    "estimate_markers",
    "marker_keys",
    "markers_prompt",
    "plan_calls",
    "run_markers",
    "significant_words",
]
