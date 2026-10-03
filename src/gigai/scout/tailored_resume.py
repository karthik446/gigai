"""Q3 (v0.1.9, SCOPE-ADD-2): one tailored resume (markdown) for one job posting.

``run_tailored_resume`` is what ``POST /api/tailored-resumes`` and ``gigai
scout resume tailor`` both call: resolve the job (P4 ``job_input``), the
resume (P4 ``resume_input``, pinned profile or ephemeral text), the
candidate's answered ``experience_qa`` questions (P3 ``read_answers``) and,
when one is stored, the quick assessment's requirement matrix (context
only); render the packaged ``scout/data/instructions/tailor.md`` with the
sources NUMBERED BY CODE (``R<n>`` resume lines, ``A <question_id>``
answers, ``M<n>`` matrix rows); call the model through the shared
``assessment_core.invoke_json_once`` loop (one retry with the validation
error fed back); validate the answer line by line; render the markdown in
code from the validated JSON; store JSON + ``.md`` under the GigAI home.

The guardrail (the packet's reason to exist): every line of the output
traces to the pinned resume or an answer, enforced in code, never trusted
from the model:

- PRIVACY (0110-003 P1): the prompt carries only the lines
  ``resume_privacy.model_resume`` keeps (no name/contact lines, inline
  contact redacted), under their ORIGINAL ``R<n>`` numbers (gaps are
  fine); a copy or citation of a withheld line is rejected.  The output is
  HEADERLESS: the PDF header comes from the Generate PDF form (0110-046), never
  from the model.  A stray ``header`` key in the answer is accepted and
  discarded (no retry spent); ``TailoredResume.header`` stays in the
  contract (empty for new results) so older stored results still parse.
- STRUCTURE: ``sections[]``; experience/projects/education sections hold
  ``entries[]`` of ``{heading_ref, bullets[]}``.
- COPY-ONLY lines ``{"copy": <resume line n>}``: entry headings (employer,
  title, dates, location, degree) are inserted by code verbatim from the
  resume; the model only picks the line.  Only bullets, the summary (and,
  optionally, skills lines) are rewritten.
- PROVENANCE: every rewritten line cites 1-4 sources; a resume ref must be
  a line the prompt showed (in ``1..len(resume_lines)`` and not withheld);
  an answer ref's id, after
  ``normalize_question_id``, must be a ``read_answers`` key.  A resume ref
  whose line ends mid-sentence is EXPANDED to its continuation lines
  (``resume_continuations``, tailor-r2): the ref's ``text`` is the joined
  span and ``continued_lines`` names the extra lines; the guards, the
  stored refs and every consumer of ``SourceRef.text`` see the span.  Copy
  lines never expand (they are one resume line verbatim).
- CROSS-ENTRY guard (uat-bug-036): a rewritten line may cite several resume
  lines only when the lines that sit in an entry (a role/project/degree
  block, ``resume_entries``) all sit in the same entry; two roles in one
  claim are rejected as ``cross_entry_citation``.  Summary/skills prose
  lines and answers belong to no entry and never conflict.
- NUMERIC guard: every number in a rewritten line (digits or number words,
  ranges, ``5+``, ``$1.2M`` vs ``1.2 million``) appears in a cited source.
- POSTING-TERM guard: a rewritten line may not carry a skill/tool/technology
  term that appears in the posting but in none of its cited sources (terms
  = the stored matrix's requirement names when present, else the posting's
  capitalized/technical tokens; case-insensitive; a small alias table).

The whole answer is rejected on the first violation, with a message naming
the line and the reason (fed back on the single retry, then
``model_output_invalid``).

COPY BY DEFAULT, NO LOSS (0110-006): a copy line may stand for any summary,
bullet, skills or other line (it expands to the lines it wraps onto, like a
cited ref, and a later copy of one of those lines in the same container is
dropped, 0110-015); a rewritten line is the exception and carries a ``reason``
(``surface`` / ``summary`` / ``answer``, anchored to a matrix row ``M<n>``
or a <=60-character phrase the posting contains).  After validation,
``apply_no_loss`` (inside ``tailor_once``'s validate step, so no retry is
spent) replaces every rewrite whose reason is missing or unanchored, that
merges several resume lines, or that drops a fact of its cited lines
(``tailor_no_loss.lost_items``: numbers, named tech/entities, ownership
verbs, scope phrases; skills items on a skills line) with a COPY of its
cited lines (``origin: "fallback"``, the rejected rewrite kept as
``alternative`` with what it ``lost``).  Old roles keep at most
``LENGTH_RULE.old_role_bullets`` bullets, dropped WHOLE, and every resume
bullet a role does not show is recorded on the entry (``dropped``).
Fabrication stays a whole-answer retry; weakening is a per-line fallback.

Storage: ``<home>/scout/<project_id>/resumes/
<profile_id|ephemeral>/<sha256(job_identity)>.json`` + a sibling ``.md``.
The stored JSON carries resume-derived text by design (README privacy
line); never the posting text, at most a 60-character requirement phrase
per rewritten line (a validated ``reason.posting_phrase``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from importlib import resources
import json
import os
from pathlib import Path
import re
from typing import ClassVar

from ..canonical import digest_imported_bytes, parse_json_bytes
from ..config import GigAIConfig, load_config
from .assessment_core import AssessAttempt, invoke_json_once
from .call_metrics import KIND_TAILOR, CallMeter
from .find_jobs.assess_contracts import AssessJobInput, AssessResumeInput, ResolvedJob, ResolvedResume
from .find_jobs.contracts import (
    FindJobsContractError,
    ModelTarget,
    NotAssessedReason,
    Producer,
    UsageBlock,
    _Contract,
    _digest_value,
    _enum,
    _fail,
    _json_enum,
    _object_with_optional,
    _optional_string,
    _string,
)
from .find_jobs.discovery.storage import atomic_write, project_id
from .find_jobs.job_input import job_fetch_client, resolve_job
from .find_jobs.resume_input import resolve_profile, resolve_resume, resume_for_profile
from .question_ids import normalize_question_id
from .resume_pii import detect_contact_details
from .resume_privacy import ModelResume, is_name_line, model_resume, redact_inline
from .untrusted_text import fence_untrusted_posting
from .quick_assess import (
    EPHEMERAL_RESUME_KEY,
    QuickAssessError,
    _apply_job_overrides,
    _default_model_target,
    _read_stored as _read_stored_assessment,
    _resolve_binding,
    _resolve_workpad,
    find_quick_assessment_by_job_identity,
    resume_key,
)

_INSTRUCTIONS_RESOURCE = "scout/data/instructions/tailor.md"

#: The first line of ``tailor.md``.  ``find_jobs/bindings.py`` repeats this
#: literal as ``TEST_MODEL_TAILOR_MARKER`` so the fixture model can tell a
#: tailor prompt from an assess prompt without importing this module
#: (``test_tailored_resume.py`` asserts the two stay identical).
TAILOR_PROMPT_HEADER = "GigAI Scout tailored resume"

_MAX_PROMPT_POSTING_TEXT = 12_000
_MAX_PROMPT_RESUME_TEXT = 16_000
_MAX_PROMPT_VALIDATION_ERROR = 300
_MAX_PROMPT_ANSWER_TEXT = 600

_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_VALIDATION_PLACEHOLDER = "{{validation_error}}"
_ANSWERS_PLACEHOLDER = "{{answers}}"
_MATRIX_PLACEHOLDER = "{{matrix}}"

# --- bounds (orchestrator review, required change 4) ---------------------------------
MAX_SECTIONS = 8
MAX_ENTRIES_PER_SECTION = 20
MAX_TOTAL_LINES = 120
MAX_TEXT_CHARS = 400
MAX_REFS_PER_LINE = 4
MAX_HEADING_LINES = 4

SECTION_HEADINGS: tuple[str, ...] = ("summary", "experience", "skills", "education", "projects", "other")
ENTRY_SECTIONS: frozenset[str] = frozenset({"experience", "projects", "education"})


@dataclass(frozen=True)
class LengthRule:
    """How long a tailored resume may get (0110-006 Q4, the operator's WORKING answer)."""

    #: Pages the whole resume should fit in (the prompt asks for it).
    max_pages: int
    #: A role whose end year is more than this many years back is an OLD role ...
    old_role_years: int
    #: ... and keeps at most this many bullets (the model's first ones: it
    #: orders a role's bullets by posting relevance); the rest are dropped
    #: WHOLE, never shortened, and recorded on the entry.
    old_role_bullets: int


#: The one constant to change when the length answer changes; the prompt
#: (``{{max_pages}}`` ...) and ``apply_no_loss`` both read it.
LENGTH_RULE = LengthRule(max_pages=2, old_role_years=8, old_role_bullets=3)

#: A rewrite's reason: its kinds, and the stored posting phrase's bound (Q5).
REASON_KINDS: frozenset[str] = frozenset({"surface", "summary", "answer"})
MAX_POSTING_PHRASE_CHARS = 60

_PRODUCER_CALLABLE = "scout.tailor"
_PRODUCER_VERSION = "1"
_PRODUCER_ACTOR = "scout-tailor"

_SAFE_RESUME_KEY = re.compile(r"\A[A-Za-z0-9_-]+\Z")


class TailorError(QuickAssessError):
    """A tailored resume could not be produced; ``code`` is the API/CLI error code.

    A ``QuickAssessError`` subclass on purpose: the API mixin shares
    ``api/assess.py``'s error map, and the CLI's ``_fail`` reads ``code``.
    """


class TailorValidationError(ValueError):
    """The model's answer broke a structural or provenance rule (retried once, then 502)."""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- instructions -------------------------------------------------------------------


def _instruction_bytes() -> bytes:
    return resources.files("gigai").joinpath(*_INSTRUCTIONS_RESOURCE.split("/")).read_bytes()


def load_tailor_instructions() -> str:
    """The packaged tailor prompt template, minus the file's single trailing newline."""

    text = _instruction_bytes().decode("utf-8")
    if text.endswith("\n"):
        text = text[:-1]
    return text


TAILOR_INSTRUCTIONS_DIGEST = digest_imported_bytes(_instruction_bytes())
"""Digest of the shipped ``tailor.md`` bytes; changes only with the file."""


# --- sources ----------------------------------------------------------------------------


def resume_lines(resume_text: str) -> tuple[str, ...]:
    """The resume's non-blank lines, stripped, 1-based in every message and ref."""

    return tuple(line.strip() for line in resume_text.splitlines() if line.strip())


#: A line that ends a sentence: terminal punctuation, optionally followed by
#: closing quotes/brackets/emphasis markers (``experience."``, ``(SOC2).``,
#: ``years.**``).
_TERMINATED = re.compile(r"[.!?;:][\"'”’)\]}*_]*\Z")
#: A line that starts a bullet (``-`` ``*`` ``•`` ``–`` ``—`` ``·`` ``>`` or
#: ``1.``/``1)`` then whitespace) -- the markers ``_BULLET_PREFIX`` and
#: ``_LEADING_MARKERS`` already know -- or a heading (see ``_HEADING_LINE``).
_BULLET_LINE = re.compile(r"(?:[-*•–—·>]|\d+[.)])(?:\s|\Z)")
#: A markdown heading (``#``) or a line opening with paired strong emphasis
#: (``**Senior Platform Engineer — Hexa Cloud** (2021–present)``), the shape
#: ``_display`` already treats as a copied entry heading.
_HEADING_LINE = re.compile(r"#|\*\*|__")


def _is_terminated(line: str) -> bool:
    return _TERMINATED.search(line) is not None


def _is_heading_line(line: str) -> bool:
    return _HEADING_LINE.match(line) is not None


def _is_marked_line(line: str) -> bool:
    return _is_heading_line(line) or _BULLET_LINE.match(line) is not None


def resume_continuations(resume_text: str) -> dict[int, tuple[int, ...]]:
    """Which numbered resume lines continue which (tailor-r2): ``{n: (n+1, ...)}``.

    A resume sentence hard-wrapped across lines is one fact split over
    several ``R<n>``; a citation of ``R<n>`` is expanded to the lines it
    runs on into.  ``R<n+1>`` continues ``R<n>`` when ``R<n>`` does not end
    a sentence (``_TERMINATED``: ``. ! ? ; :`` plus closing quotes/brackets),
    ``R<n>`` is not itself a heading, no blank RAW line separates the two
    (``resume_lines`` drops blank lines, so this is decided here on the
    text before numbering) and ``R<n+1>`` is not a bullet or a heading
    (``_is_marked_line``); the chain repeats for ``R<n+2>`` and so on.
    Numbering is exactly ``resume_lines``'s; only lines with at least one
    continuation are keyed.
    """

    numbered: list[str] = []
    blank_after: set[int] = set()  # numbered line n with a blank raw line before n+1
    for raw in resume_text.splitlines():
        line = raw.strip()
        if line:
            numbered.append(line)
        elif numbered:
            blank_after.add(len(numbered))
    continuations: dict[int, tuple[int, ...]] = {}
    for start in range(1, len(numbered) + 1):
        if _is_heading_line(numbered[start - 1]):
            continue
        span: list[int] = []
        tail = start
        while tail < len(numbered) and tail not in blank_after and not _is_terminated(numbered[tail - 1]):
            following = numbered[tail]  # R<tail+1>
            if _is_marked_line(following):
                break
            tail += 1
            span.append(tail)
        if span:
            continuations[start] = tuple(span)
    return continuations


#: A markdown heading of level 1-2 (``# Name``, ``## Experience``) opens a
#: SECTION block; a bold heading or a level 3+ heading opens an ENTRY block.
_SECTION_HEADING = re.compile(r"#{1,2}(?:\s|\Z)")


def resume_entries(resume_text: str) -> dict[int, int]:
    """Which numbered resume lines belong to which entry (uat-bug-036): ``{n: heading line}``.

    The resume is split by its own heading lines (``_is_heading_line``); a
    line belongs to the nearest heading line above it.  A block is an ENTRY
    (a role, project or degree) when its heading is a bold/underscore
    heading (``**Staff ML Engineer -- Northwind** (2021-present)``) or a
    markdown heading of level 3 or deeper; every line of it, the heading
    included, maps to the heading's line number.  A block opened by a level
    1-2 heading (``# Name``, ``## Summary``, ``## Skills``) or by no heading
    at all is section prose and is NOT keyed: it belongs to no entry.
    Numbering is exactly ``resume_lines``'s.  A resume without entry
    headings keys nothing, so the cross-entry guard never fires on it.
    """

    entries: dict[int, int] = {}
    heading = 0  # line number of the entry heading that owns the current block; 0 = none
    for number, line in enumerate(resume_lines(resume_text), 1):
        if _is_heading_line(line):
            heading = 0 if _SECTION_HEADING.match(line) else number
        if heading:
            entries[number] = heading
    return entries


@dataclass(frozen=True)
class AnswerSource:
    """One answered ``experience_qa`` question as a citable source."""

    question_id: str
    answer: str
    revision_id: str
    prompt: str = ""

    @property
    def guard_text(self) -> str:
        """What the guards treat as this source's text: the id's words plus the answer.

        The question PROMPT is deliberately excluded (a prompt like "8+ years
        of Python?" must never let "8 years" through), the id's value tokens
        are included (an affirmative answer to ``cloud:gcp`` supports "GCP").
        """

        return f"{self.question_id.replace(':', ' ').replace('_', ' ')} {self.answer}"


@dataclass(frozen=True)
class MatrixRow:
    requirement: str
    status: str


@dataclass(frozen=True)
class TailorJob:
    title: str
    company: str
    location: str
    posting_text: str


@dataclass(frozen=True)
class TailorContext:
    """The candidate side of one tailoring: the numbered sources and the guard inputs."""

    resume_lines: tuple[str, ...]
    answers: Mapping[str, AnswerSource] = field(default_factory=dict)
    matrix: tuple[MatrixRow, ...] = ()
    #: ``resume_continuations(resume_text)``: which lines a cited line runs on
    #: into.  Empty (the default) means no ref is ever expanded.
    continuations: Mapping[int, tuple[int, ...]] = field(default_factory=dict)
    #: ``resume_entries(resume_text)``: line number -> the heading line of the
    #: entry it belongs to.  Empty (the default) means no line is in an entry,
    #: so the cross-entry guard never fires.
    entries: Mapping[int, int] = field(default_factory=dict)
    #: ``resume_privacy.model_resume(resume_text)``: the lines a model may see
    #: and the withheld line numbers.  ``None`` (the default) derives it from
    #: ``resume_lines``, so a context is never built without the strip;
    #: ``tailor_context`` passes the one made from the raw resume text.
    model: ModelResume | None = None

    def __post_init__(self) -> None:
        if self.model is None:
            object.__setattr__(self, "model", model_resume("\n".join(self.resume_lines)))

    @property
    def withheld(self) -> frozenset[int]:
        assert self.model is not None
        return self.model.withheld


def tailor_context(
    resume_text: str,
    *,
    answers: Mapping[str, AnswerSource] | None = None,
    matrix: tuple[MatrixRow, ...] = (),
) -> TailorContext:
    """The context ``run_tailored_resume`` tailors with: every source derived from the raw resume text."""

    return TailorContext(
        resume_lines=resume_lines(resume_text),
        answers=dict(answers or {}),
        matrix=matrix,
        continuations=resume_continuations(resume_text),
        entries=resume_entries(resume_text),
        model=model_resume(resume_text),
    )


@dataclass(frozen=True)
class SourceRef:
    """A validated citation: the source kind, its key, and the source text as the validator saw it.

    A resume ref cited from a rewritten line may be EXPANDED (tailor-r2):
    ``continued_lines`` are the numbered lines that continue ``line``
    (``resume_continuations``) and ``text`` is then the joined span, one
    space between lines.  ``label()`` stays the cited line (``R4``); the
    stored JSON carries ``continued_lines`` only when it is non-empty, so an
    unexpanded ref serializes exactly as before.
    """

    kind: str  # "resume" | "answer"
    line: int | None
    question_id: str | None
    text: str
    continued_lines: tuple[int, ...] = ()

    def label(self) -> str:
        return f"R{self.line}" if self.kind == "resume" else f"A {self.question_id}"

    def to_json(self) -> dict[str, object]:
        if self.kind == "resume":
            out: dict[str, object] = {"kind": "resume", "line": self.line, "text": self.text}
            if self.continued_lines:
                out["continued_lines"] = list(self.continued_lines)
            return out
        return {"kind": "answer", "question_id": self.question_id, "text": self.text}

    @classmethod
    def from_json(cls, obj: object) -> "SourceRef":
        if type(obj) is not dict:
            _fail("wrong_type", "ref must be an object")
        kind = obj.get("kind")
        if kind == "resume":
            value = _object_with_optional(obj, ("kind", "line", "text"), ("continued_lines",), "ref")
            line = value["line"]
            if type(line) is not int or line < 1:
                _fail("invalid_value", "ref.line must be a positive integer")
            raw_continued = value.get("continued_lines", [])
            if type(raw_continued) is not list or any(type(item) is not int or item <= line for item in raw_continued):
                _fail("invalid_value", "ref.continued_lines must be a list of line numbers after ref.line")
            return cls("resume", line, None, _string(value["text"], "ref.text", nonempty=False), tuple(raw_continued))
        if kind == "answer":
            value = _object_with_optional(obj, ("kind", "question_id", "text"), (), "ref")
            return cls("answer", None, _string(value["question_id"], "ref.question_id"), _string(value["text"], "ref.text", nonempty=False))
        _fail("bad_enum", "ref.kind must be resume or answer")
        raise AssertionError("unreachable")


# --- the prompt -----------------------------------------------------------------------


def render_tailor_prompt(job: TailorJob, ctx: TailorContext, validation_error: str | None = None) -> str:
    """Render ``tailor.md``: sources numbered by code, empty paragraphs dropped.

    Same paragraph-template renderer as ``assessment_core.render_assess_prompt``:
    split on blank lines BEFORE substitution, drop the ``{{answers}}``,
    ``{{matrix}}`` and ``{{validation_error}}`` paragraphs when empty,
    substitute in one pass (substituted text is never rescanned).
    """

    assert ctx.model is not None
    # Only the lines the privacy strip keeps, under their original numbers.
    numbered = "\n".join(f"R{index}: {line}" for index, line in ctx.model.lines)
    answers = "\n".join(
        f"A {item.question_id}: {item.answer[:_MAX_PROMPT_ANSWER_TEXT]}" for item in ctx.answers.values()
    )
    matrix = "\n".join(f"M{index}: {row.requirement} [{row.status}]" for index, row in enumerate(ctx.matrix, 1))
    # 0.1.10.7 P5: the title, the company and the posting text are a stranger's words, and so
    # are the matrix's requirement strings (the assess model copied them from the posting).
    posting = (
        f"ROLE: {job.title or 'unspecified'}\nCOMPANY: {job.company or 'unspecified'}\n"
        f"POSTING TEXT:\n{job.posting_text[:_MAX_PROMPT_POSTING_TEXT]}"
    )
    values = {
        "posting": fence_untrusted_posting(posting),
        "resume_lines": numbered[:_MAX_PROMPT_RESUME_TEXT],
        "answers": answers,
        "matrix": fence_untrusted_posting(matrix) if matrix else "",
        "validation_error": (validation_error or "")[:_MAX_PROMPT_VALIDATION_ERROR],
        "max_pages": str(LENGTH_RULE.max_pages),
        "old_role_years": str(LENGTH_RULE.old_role_years),
        "old_role_bullets": str(LENGTH_RULE.old_role_bullets),
    }
    blocks = load_tailor_instructions().split("\n\n")
    if not validation_error:
        blocks = [block for block in blocks if _VALIDATION_PLACEHOLDER not in block]
    if not ctx.answers:
        blocks = [block for block in blocks if _ANSWERS_PLACEHOLDER not in block]
    if not ctx.matrix:
        blocks = [block for block in blocks if _MATRIX_PLACEHOLDER not in block]

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"tailor instructions use an unknown placeholder {{{{{key}}}}}")
        return values[key]

    return "\n\n".join(_PLACEHOLDER.sub(fill, block) for block in blocks)


# --- the numeric guard ------------------------------------------------------------------

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_SUFFIX_SCALES = {"k": 1_000, "m": 1_000_000, "mm": 1_000_000, "b": 1_000_000_000, "bn": 1_000_000_000}
#: "one" is a pronoun far more often than a number ("one of the first"); it
#: counts only inside a compound ("one hundred", "twenty-one").
_PRONOUN_LIKE = frozenset({"one"})

_NUMBER_TOKEN = re.compile(
    r"(?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<suffix>(?:bn|mm|[kmb])(?![a-z]))?|(?P<word>[A-Za-z]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NumberMention:
    value: Decimal
    span: str


def numeric_values(text: str) -> tuple[NumberMention, ...]:
    """Every number the text states, normalized: digits and number words, ``5+``,
    ``10%``, ``$1.2M``/``1.2 million``/``1,200,000`` all to one value each; a
    range ``2019-2023`` to its two ends; ordinals ``3rd`` to 3."""

    mentions: list[NumberMention] = []
    tokens = list(_NUMBER_TOKEN.finditer(text))
    index = 0
    while index < len(tokens):
        match = tokens[index]
        if match.group("digits"):
            try:
                value = Decimal(match.group("digits").replace(",", ""))
            except InvalidOperation:  # pragma: no cover - the regex only admits decimal digits
                index += 1
                continue
            span = match.group(0)
            suffix = match.group("suffix")
            if suffix:
                value *= _SUFFIX_SCALES[suffix.lower()]
            elif index + 1 < len(tokens) and (tokens[index + 1].group("word") or "").lower() in _SCALES:
                nxt = tokens[index + 1]
                value *= _SCALES[nxt.group("word").lower()]
                span = text[match.start() : nxt.end()]
                index += 1
            mentions.append(NumberMention(value, span))
            index += 1
            continue
        word = match.group("word").lower()
        if word in _UNITS or word in _TENS:
            start = match.start()
            current = Decimal(_UNITS.get(word, _TENS.get(word, 0)))
            end = match.end()
            compound = False
            index += 1
            # "twenty" + "five", then any number of scale words ("two hundred thousand").
            if word in _TENS and index < len(tokens):
                nxt = (tokens[index].group("word") or "").lower()
                if nxt in _UNITS and _UNITS[nxt] < 10 and text[end : tokens[index].start()] in {" ", "-"}:
                    current += _UNITS[nxt]
                    end = tokens[index].end()
                    compound = True
                    index += 1
            while index < len(tokens):
                nxt = (tokens[index].group("word") or "").lower()
                if nxt in _SCALES and text[end : tokens[index].start()].strip() == "":
                    current *= _SCALES[nxt]
                    end = tokens[index].end()
                    compound = True
                    index += 1
                    continue
                break
            if word in _PRONOUN_LIKE and not compound:
                continue
            mentions.append(NumberMention(current, text[start:end]))
            continue
        index += 1
    return tuple(mentions)


def unsupported_numbers(text: str, sources: Iterable[str]) -> tuple[NumberMention, ...]:
    """The numbers ``text`` states that no source states (numerically equal after normalization)."""

    supported = {mention.value for source in sources for mention in numeric_values(source)}
    return tuple(mention for mention in numeric_values(text) if mention.value not in supported)


# --- the posting-term guard ---------------------------------------------------------------

#: Spelling variants that name the same technology (case-insensitive lookup
#: on the lowercased token; canonical spelling on the right).
TERM_ALIASES: dict[str, str] = {
    "postgres": "postgresql", "postgresql": "postgresql", "psql": "postgresql",
    "k8s": "kubernetes", "kube": "kubernetes", "kubernetes": "kubernetes",
    "js": "javascript", "javascript": "javascript",
    "ts": "typescript", "typescript": "typescript",
    "golang": "go", "go": "go",
    "node": "node.js", "nodejs": "node.js", "node.js": "node.js",
    "react": "react", "reactjs": "react", "react.js": "react",
    "vue": "vue", "vuejs": "vue", "vue.js": "vue",
    "angular": "angular", "angularjs": "angular",
    "py": "python", "python": "python", "python3": "python",
    "c++": "c++", "cpp": "c++",
    "c#": "c#", "csharp": "c#",
    ".net": ".net", "dotnet": ".net",
    "ci/cd": "ci/cd", "cicd": "ci/cd", "ci-cd": "ci/cd",
    "mongo": "mongodb", "mongodb": "mongodb",
    "elastic": "elasticsearch", "elasticsearch": "elasticsearch",
    "gql": "graphql", "graphql": "graphql",
    "sklearn": "scikit-learn", "scikit-learn": "scikit-learn",
    "pytorch": "pytorch", "torch": "pytorch",
    "tensorflow": "tensorflow",
    "k8": "kubernetes",
}

#: Capitalized/all-caps words a posting uses that are not skills or tools.
TERM_STOP_WORDS: frozenset[str] = frozenset(
    """
    a an and or of to in for with on at by from as is are be we you our your this that these those it its
    will can must should have has had not no yes all any more most other some such than then there their
    they them what when where which who why how about also into over under per plus us usa uk eu eeo ok i
    ii iii id hr pm am faq ceo cto coo cfo vp na tbd llc inc ltd co corp job jobs role roles team teams
    company companies requirements requirement qualifications qualification responsibilities responsibility
    experience experienced skills skill knowledge ability strong excellent senior junior staff principal
    lead engineer engineers engineering developer developers manager managers director directors remote
    hybrid onsite on-site full-time part-time contract benefits salary equity bonus preferred required
    nice bachelor bachelors bachelor's master masters master's phd degree computer science years year
    months month days day weeks week hours hour monday tuesday wednesday thursday friday saturday sunday
    january february march april may june july august september october november december apply
    application applicants applicant candidates candidate position positions location locations united
    states america american canada canadian europe european north south east west new york san francisco
    london toronto seattle austin boston chicago denver vancouver berlin paris amsterdam dublin remote-first
    what you'll do you'll we're you're what's who we're looking about the why join us equal opportunity
    employer diversity inclusion compensation range base pay paid time off health dental vision insurance
    401k retirement stock options work life balance mission vision values culture people product products
    customer customers business businesses market markets industry growth scale impact fast-paced startup
    series funding backed founded headquartered offices office travel visa sponsorship citizenship
    clearance background check drug reports reporting report responsible own owner ownership build
    building design designing develop developing maintain maintaining deliver delivering collaborate
    collaborating communicate communication written verbal problem solving problem-solving detail
    detail-oriented self-starter independent proactive mentoring mentor leadership leading
    """.split()
)

_TERM_TOKEN = re.compile(r"[A-Za-z.][A-Za-z0-9+#.]*(?:[/-][A-Za-z0-9+#.]+)*")
#: What ends a sentence for the "Capitalized only at a sentence start" rule.
#: List separators (``;`` ``,`` ``:`` ``|`` and dashes) are NOT breaks:
#: "Requirements: Python; Kubernetes; Terraform" names skills.  A bullet start
#: is handled by ``posting_terms`` itself (bare list item vs sentence-like).
_SENTENCE_BREAKS = frozenset(".!?\n(\"'[")


def _clean_token(token: str) -> str:
    token = token.strip(".")
    return token


def canonical_term(token: str) -> str:
    """Case-folded, alias-mapped form of one token (``Postgres`` -> ``postgresql``)."""

    lowered = _clean_token(token).lower()
    if lowered in TERM_ALIASES:
        return TERM_ALIASES[lowered]
    return lowered


def _term_variants(token: str) -> set[str]:
    """The canonical form plus a naive singular so ``containers`` meets ``container``."""

    canonical = canonical_term(token)
    variants = {canonical}
    if len(canonical) > 4 and canonical.endswith("s") and not canonical.endswith("ss"):
        variants.add(canonical[:-1])
    if len(canonical) > 5 and canonical.endswith("es"):
        variants.add(canonical[:-2])
    return variants


def _tokens_with_parts(text: str) -> list[tuple[str, int]]:
    """Every token match with its start offset, plus the parts of slash-joined
    tokens (``Go/Kafka`` -> ``Go``, ``Kafka``) unless the whole is an alias (``CI/CD``)."""

    found: list[tuple[str, int]] = []
    for match in _TERM_TOKEN.finditer(text):
        token = _clean_token(match.group(0))
        if not token:
            continue
        found.append((token, match.start()))
        if "/" in token and token.lower() not in TERM_ALIASES:
            offset = match.start()
            for part in token.split("/"):
                if part:
                    found.append((part, offset))
                offset += len(part) + 1
    return found


def text_terms(text: str) -> set[str]:
    """Every token of ``text`` in every canonical variant (for matching lines and sources)."""

    variants: set[str] = set()
    for token, _offset in _tokens_with_parts(text):
        variants |= _term_variants(token)
    return variants


def _is_technical(token: str) -> bool:
    if any(char.isdigit() or char in "+#" for char in token):
        return True
    if "." in token.strip("."):
        return True
    if len(token) >= 2 and token.isupper():
        return True
    return any(char.isupper() for char in token[1:])


def _at_sentence_start(text: str, offset: int) -> bool:
    before = text[:offset].rstrip(" \t")
    return not before or before[-1] in _SENTENCE_BREAKS


#: A bullet marker (``-`` ``*`` ``•`` ``–`` ``—`` ``·`` or ``1.``/``1)``) and the
#: whitespace around it, when that is all that precedes a token on its line.
_BULLET_PREFIX = re.compile(r"[ \t]*(?:[-*•–—·]|\d+[.)])[ \t]+\Z")
_TRAILING_PARENTHETICAL = re.compile(r"\s*\([^()]*\)\s*\Z")
#: A bullet item with at most this many word tokens (after dropping a trailing
#: parenthetical and punctuation) is a bare list item: ``- Snowflake``,
#: ``- Apache Kafka``, ``- Terraform (3+ years)``.  Longer bullets read as
#: sentences (``- Improve platform reliability through ...``).
_BARE_LIST_ITEM_MAX_TOKENS = 3


def _bullet_item(text: str, offset: int) -> str | None:
    """The rest of the line when the token at ``offset`` is the first word of a
    bullet item, else ``None``."""

    line_start = text.rfind("\n", 0, offset) + 1
    if not _BULLET_PREFIX.fullmatch(text, line_start, offset):
        return None
    line_end = text.find("\n", offset)
    return text[offset:] if line_end == -1 else text[offset:line_end]


def _is_bare_list_item(item: str) -> bool:
    trimmed = _TRAILING_PARENTHETICAL.sub("", item).strip().rstrip(".;:,")
    return len(trimmed.split()) <= _BARE_LIST_ITEM_MAX_TOKENS


def posting_terms(posting_text: str, *, exclude: Iterable[str] = ()) -> frozenset[str]:
    """The skill/tool/technology terms a posting names, canonical and lowercased.

    A token counts when it is technical on its face (a digit, ``+``, ``#``,
    an inner dot, inner capitals, or all caps: ``C++``, ``k8s``, ``Node.js``,
    ``PostgreSQL``, ``AWS``) or when it is a Capitalized word that occurs at
    least once NOT at the start of a sentence (``Python``, ``Kafka``, and a
    list item after ``;``/``,``/``:``; a sentence-initial "Build" never
    qualifies on its own).  The first word of a bullet counts when the bullet
    is a bare list item (``- Snowflake``, ``- Terraform (3+ years)``: at most
    ``_BARE_LIST_ITEM_MAX_TOKENS`` word tokens) or when the word is in
    ``TERM_ALIASES`` (``- Kubernetes and Terraform at scale``); a sentence-like
    bullet's first word is sentence-initial otherwise (``- Improve platform
    reliability ...`` does not make "improve" a term) unless it is Capitalized
    elsewhere mid-sentence.  Stop words and every token of ``exclude`` (the
    posting's own title/company/location) are dropped, as is any token in the
    stop list once lowercased.
    """

    excluded: set[str] = set()
    for item in exclude:
        excluded |= text_terms(item)
    technical: set[str] = set()
    capitalized_mid_sentence: set[str] = set()
    for token, offset in _tokens_with_parts(posting_text):
        lowered = token.lower()
        if lowered in TERM_STOP_WORDS or not any(char.isalpha() for char in token):
            continue
        if _is_technical(token):
            technical.add(token)
        elif token[0].isupper() and len(token) >= 2:
            item = _bullet_item(posting_text, offset)
            if item is not None:
                if _is_bare_list_item(item) or lowered in TERM_ALIASES:
                    capitalized_mid_sentence.add(token)
            elif not _at_sentence_start(posting_text, offset):
                capitalized_mid_sentence.add(token)
    terms: set[str] = set()
    for token in technical | capitalized_mid_sentence:
        canonical = canonical_term(token)
        if canonical in TERM_STOP_WORDS or canonical in excluded or len(canonical) < 2:
            continue
        terms.add(canonical)
    return frozenset(terms)


def matrix_terms(rows: Iterable[MatrixRow], *, exclude: Iterable[str] = ()) -> frozenset[str]:
    """The guard's term list from a stored assessment: the requirement names,
    tokenized like a posting (every token of a requirement name is mid-
    sentence for this purpose: "Python" as a whole requirement name counts)."""

    text = "\n".join(f"requirement: {row.requirement}" for row in rows)
    return posting_terms(text, exclude=exclude)


def source_terms(text: str) -> set[str]:
    """``text_terms`` of a cited source plus the parts of its hyphenated
    compounds: ``Terraform-managed`` supports both ``terraform-managed`` and
    ``terraform`` (an alias such as ``ci-cd`` stays whole).  Source side only:
    a source that states the compound states the tool."""

    variants = text_terms(text)
    for token, _offset in _tokens_with_parts(text):
        if "-" in token and token.lower() not in TERM_ALIASES:
            for part in token.split("-"):
                if part:
                    variants |= _term_variants(part)
    return variants


def unsupported_posting_terms(text: str, sources: Iterable[str], terms: Iterable[str]) -> tuple[str, ...]:
    """The posting terms ``text`` uses that none of ``sources`` mentions (canonical, sorted)."""

    line_terms = text_terms(text)
    supported: set[str] = set()
    for source in sources:
        supported |= source_terms(source)
    return tuple(sorted(term for term in set(terms) if term in line_terms and term not in supported))


# --- the validated result --------------------------------------------------------------


@dataclass(frozen=True)
class LineReason:
    """Why a line was rewritten (0110-006): the kind, and the posting requirement it serves.

    ``requirement`` is a matrix row id (``M3``); ``posting_phrase`` is at
    most ``MAX_POSTING_PHRASE_CHARS`` characters of the posting (Q5: the
    only posting text a stored result may carry).  Stored on a shown
    rewrite as validated; on a rejected rewrite (``alternative``) as the
    model gave it.
    """

    kind: str
    requirement: str | None = None
    posting_phrase: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"kind": self.kind, "requirement": self.requirement, "posting_phrase": self.posting_phrase}

    @classmethod
    def from_json(cls, obj: object) -> "LineReason":
        value = _object_with_optional(obj, ("kind",), ("requirement", "posting_phrase"), "tailored_line.reason")
        kind = _string(value["kind"], "tailored_line.reason.kind")
        if kind not in REASON_KINDS:
            _fail("bad_enum", "tailored_line.reason.kind must be surface, summary or answer")
        phrase = _optional_string(value.get("posting_phrase"), "tailored_line.reason.posting_phrase")
        if phrase is not None and len(phrase) > MAX_POSTING_PHRASE_CHARS:
            _fail("invalid_value", f"tailored_line.reason.posting_phrase is longer than {MAX_POSTING_PHRASE_CHARS} characters")
        return cls(kind, _optional_string(value.get("requirement"), "tailored_line.reason.requirement"), phrase)


#: The ``lost`` rules a fallback can name (``tailor_no_loss.lost_items`` keys).
LOST_RULES: tuple[str, ...] = ("numbers", "entities", "ownership", "scope", "skills")
#: The flags a rejected rewrite can carry besides ``lost``.
FALLBACK_FLAGS: tuple[str, ...] = ("reason_invalid", "dropped_duplicate", "merge")


@dataclass(frozen=True)
class LineAlternative:
    """The other version of a shown line (0110-006), or a line the result does not show.

    On a shown REWRITE: ``kind == "copy"``, the original resume span.  On a
    FALLBACK (the shown line is the original): ``kind == "rewritten"``, the
    model's rejected rewrite with ``reason``, ``lost`` (what it dropped, by
    rule; ``{}`` when only the reason failed) and the flags
    (``reason_invalid``, ``dropped_duplicate``, ``merge``).  In an entry's
    or section's ``dropped`` list: a resume bullet the role does not show
    (``copy``) or a rejected rewrite with no line of its own (``rewritten``).
    A user toggle swaps a line with its alternative and keeps ``lost`` and
    the flags with the pair.
    """

    kind: str  # "copy" | "rewritten"
    text: str
    refs: tuple[SourceRef, ...]
    reason: LineReason | None = None
    lost: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    reason_invalid: bool = False
    dropped_duplicate: bool = False
    merge: bool = False

    def lost_dict(self) -> dict[str, list[str]] | None:
        return None if self.lost is None else {rule: list(items) for rule, items in self.lost}

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"kind": self.kind, "text": self.text, "refs": [ref.to_json() for ref in self.refs]}
        if self.reason is not None:
            out["reason"] = self.reason.to_json()
        if self.lost is not None:
            out["lost"] = self.lost_dict()
        for flag in FALLBACK_FLAGS:
            if getattr(self, flag):
                out[flag] = True
        return out

    @classmethod
    def from_json(cls, obj: object) -> "LineAlternative":
        value = _object_with_optional(obj, ("kind", "text", "refs"), ("reason", "lost", *FALLBACK_FLAGS), "tailored_line.alternative")
        kind = _string(value["kind"], "tailored_line.alternative.kind")
        if kind not in {"copy", "rewritten"}:
            _fail("bad_enum", "tailored_line.alternative.kind must be copy or rewritten")
        if type(value["refs"]) is not list:
            _fail("wrong_type", "tailored_line.alternative.refs must be an array")
        lost = value.get("lost")
        pairs: tuple[tuple[str, tuple[str, ...]], ...] | None = None
        if lost is not None:
            if type(lost) is not dict or any(key not in LOST_RULES for key in lost):
                _fail("invalid_value", f"tailored_line.alternative.lost keys must be among {', '.join(LOST_RULES)}")
            if any(type(items) is not list or any(type(item) is not str for item in items) for items in lost.values()):
                _fail("wrong_type", "tailored_line.alternative.lost values must be arrays of strings")
            pairs = tuple((rule, tuple(lost[rule])) for rule in LOST_RULES if rule in lost)
        flags = {}
        for flag in FALLBACK_FLAGS:
            if flag in value and type(value[flag]) is not bool:
                _fail("wrong_type", f"tailored_line.alternative.{flag} must be a boolean")
            flags[flag] = bool(value.get(flag, False))
        reason = value.get("reason")
        return cls(
            kind,
            _string(value["text"], "tailored_line.alternative.text", nonempty=False),
            tuple(SourceRef.from_json(item) for item in value["refs"]),
            None if reason is None else LineReason.from_json(reason),
            pairs,
            **flags,
        )


#: A line's ``origin``: the model's line, a fallback to the original, or the operator's choice.
LINE_ORIGINS: frozenset[str] = frozenset({"model", "fallback", "user"})


@dataclass(frozen=True)
class TailoredLine(_Contract):
    """One output line: ``copy`` (a resume line verbatim) or ``rewritten`` (the model's wording).

    0110-006 added four OPTIONAL keys (the schema string is unchanged; an
    older stored line without them parses as before): ``id`` (``L<n>``,
    document order, assigned once when the result is settled), ``reason``
    (a shown rewrite's validated reason), ``origin`` (``model`` when absent,
    ``fallback``, ``user``) and ``alternative`` (``LineAlternative``).

    0110-032 added the kind ``custom`` and the OPTIONAL key ``edited_from``:
    a line whose text the operator (or their agent) typed.  It cites nothing
    (``refs`` is empty), has no ``reason`` and no ``alternative`` -- no source
    and no no-loss claim is made for it -- and ``edited_from`` holds the line
    it replaced, whole, so the original or the rewrite can be shown again.
    """

    schema_version: ClassVar[str] = "scout-tailored-line:1"
    kind: str  # "copy" | "rewritten"
    text: str
    refs: tuple[SourceRef, ...]
    id: str | None = None
    reason: LineReason | None = None
    origin: str | None = None
    alternative: LineAlternative | None = None
    edited_from: "TailoredLine | None" = None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"kind": self.kind, "text": self.text, "refs": [ref.to_json() for ref in self.refs]}
        if self.id is not None:
            out["id"] = self.id
        if self.reason is not None:
            out["reason"] = self.reason.to_json()
        if self.origin is not None:
            out["origin"] = self.origin
        if self.alternative is not None:
            out["alternative"] = self.alternative.to_json()
        if self.edited_from is not None:
            out["edited_from"] = self.edited_from.to_json()
        return out

    @classmethod
    def from_json(cls, obj: object) -> "TailoredLine":
        value = _object_with_optional(obj, ("kind", "text", "refs"), ("id", "reason", "origin", "alternative", "edited_from"), "tailored_line")
        kind = _string(value["kind"], "tailored_line.kind")
        if kind not in {"copy", "rewritten", "custom"}:
            _fail("bad_enum", "tailored_line.kind must be copy, rewritten or custom")
        if type(value["refs"]) is not list:
            _fail("wrong_type", "tailored_line.refs must be an array")
        edited_from = None if value.get("edited_from") is None else cls.from_json(value["edited_from"])
        if (kind == "custom") != (edited_from is not None) or (edited_from is not None and edited_from.kind == "custom"):
            _fail("invalid_value", "tailored_line.edited_from is the copy or rewritten line a custom line replaced")
        if kind == "custom" and (value["refs"] or value.get("reason") is not None or value.get("alternative") is not None):
            _fail("invalid_value", "a custom tailored_line carries no refs, reason or alternative")
        origin = _optional_string(value.get("origin"), "tailored_line.origin")
        if origin is not None and origin not in LINE_ORIGINS:
            _fail("bad_enum", "tailored_line.origin must be model, fallback or user")
        reason = value.get("reason")
        alternative = value.get("alternative")
        return cls(
            kind,
            _string(value["text"], "tailored_line.text", nonempty=False),
            tuple(SourceRef.from_json(item) for item in value["refs"]),
            _optional_string(value.get("id"), "tailored_line.id"),
            None if reason is None else LineReason.from_json(reason),
            origin,
            None if alternative is None else LineAlternative.from_json(alternative),
            edited_from,
        )


def _dropped_json(items: tuple[LineAlternative, ...]) -> dict[str, object]:
    return {"dropped": [item.to_json() for item in items]} if items else {}


def _dropped_from_json(value: Mapping[str, object], name: str) -> tuple[LineAlternative, ...]:
    raw = value.get("dropped", [])
    if type(raw) is not list:
        _fail("wrong_type", f"{name}.dropped must be an array")
    return tuple(LineAlternative.from_json(item) for item in raw)


@dataclass(frozen=True)
class TailoredEntry(_Contract):
    """One role/project/degree: copy-only heading lines, then bullets.

    ``dropped`` (optional, 0110-006): the role's resume bullets the result
    does not show and any rejected rewrite that had no line of its own.
    """

    schema_version: ClassVar[str] = "scout-tailored-entry:1"
    heading: tuple[TailoredLine, ...]
    bullets: tuple[TailoredLine, ...]
    dropped: tuple[LineAlternative, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "heading": [line.to_json() for line in self.heading],
            "bullets": [line.to_json() for line in self.bullets],
            **_dropped_json(self.dropped),
        }

    @classmethod
    def from_json(cls, obj: object) -> "TailoredEntry":
        value = _object_with_optional(obj, ("heading", "bullets"), ("dropped",), "tailored_entry")
        for key in ("heading", "bullets"):
            if type(value[key]) is not list:
                _fail("wrong_type", f"tailored_entry.{key} must be an array")
        return cls(
            tuple(TailoredLine.from_json(item) for item in value["heading"]),
            tuple(TailoredLine.from_json(item) for item in value["bullets"]),
            _dropped_from_json(value, "tailored_entry"),
        )


@dataclass(frozen=True)
class TailoredSection(_Contract):
    schema_version: ClassVar[str] = "scout-tailored-section:1"
    heading: str
    lines: tuple[TailoredLine, ...] = ()
    entries: tuple[TailoredEntry, ...] = ()
    #: Optional (0110-006): rejected rewrites of a lines section that had no line of their own.
    dropped: tuple[LineAlternative, ...] = ()

    def to_json(self) -> dict[str, object]:
        if self.heading in ENTRY_SECTIONS:
            return {"heading": self.heading, "entries": [entry.to_json() for entry in self.entries], **_dropped_json(self.dropped)}
        return {"heading": self.heading, "lines": [line.to_json() for line in self.lines], **_dropped_json(self.dropped)}

    @classmethod
    def from_json(cls, obj: object) -> "TailoredSection":
        value = _object_with_optional(obj, ("heading",), ("lines", "entries", "dropped"), "tailored_section")
        heading = _string(value["heading"], "tailored_section.heading")
        if heading not in SECTION_HEADINGS:
            _fail("bad_enum", "tailored_section.heading is not a known section")
        lines = value.get("lines", [])
        entries = value.get("entries", [])
        if type(lines) is not list or type(entries) is not list:
            _fail("wrong_type", "tailored_section.lines/entries must be arrays")
        return cls(
            heading,
            tuple(TailoredLine.from_json(item) for item in lines),
            tuple(TailoredEntry.from_json(item) for item in entries),
            _dropped_from_json(value, "tailored_section"),
        )

    def body_lines(self) -> tuple[TailoredLine, ...]:
        """The lines a reader can choose between: every line but entry headings."""

        return self.lines + tuple(line for entry in self.entries for line in entry.bullets)

    def is_empty(self) -> bool:
        return not self.lines and not self.entries

    def all_lines(self) -> tuple[TailoredLine, ...]:
        out: list[TailoredLine] = list(self.lines)
        for entry in self.entries:
            out.extend(entry.heading)
            out.extend(entry.bullets)
        return tuple(out)


@dataclass(frozen=True)
class TailoredResume(_Contract):
    """The validated structure: ``header`` copy lines + ``sections``."""

    schema_version: ClassVar[str] = "scout-tailored-resume:1"
    header: tuple[TailoredLine, ...]
    sections: tuple[TailoredSection, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "header": [line.to_json() for line in self.header],
            "sections": [section.to_json() for section in self.sections],
        }

    @classmethod
    def from_json(cls, obj: object) -> "TailoredResume":
        value = _object_with_optional(obj, ("schema_version", "header", "sections"), (), "tailored_resume")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "tailored_resume.schema_version is unsupported")
        if type(value["header"]) is not list or type(value["sections"]) is not list:
            _fail("wrong_type", "tailored_resume.header/sections must be arrays")
        return cls(
            tuple(TailoredLine.from_json(item) for item in value["header"]),
            tuple(TailoredSection.from_json(item) for item in value["sections"]),
        )

    def rewritten_lines(self) -> tuple[TailoredLine, ...]:
        return tuple(line for section in self.sections for line in section.all_lines() if line.kind == "rewritten")

    def line_count(self) -> int:
        return sum(len(section.all_lines()) for section in self.sections)


# --- validation (rejects the whole answer; every message names the line) --------------------


def _reject(message: str) -> None:
    raise TailorValidationError(message)


def _copy_line(raw: object, where: str, ctx: TailorContext, *, expand: bool = False) -> TailoredLine:
    """A copy line; ``expand`` (every position but an entry heading, 0110-006)
    copies the line WITH the lines it wraps onto, like a cited ref, so a
    hard-wrapped summary paragraph or bullet is copied whole."""

    if type(raw) is not dict or set(raw) != {"copy"}:
        _reject(f'{where} must be a copy line ({{"copy": <resume line number>}})')
    number = raw["copy"]
    if isinstance(number, str) and number.isdigit():
        number = int(number)
    if type(number) is not int or isinstance(number, bool):
        _reject(f"{where} copy must be a resume line number")
    if not 1 <= number <= len(ctx.resume_lines):
        _reject(f"{where} copies resume line {number}; the resume has {len(ctx.resume_lines)} lines")
    if number in ctx.withheld:
        _reject(f"{where} copies resume line {number}, which is not available (only the listed R lines can be used)")
    if expand:
        return _span_copy(_resume_ref(number, ctx))
    text = ctx.resume_lines[number - 1]
    return TailoredLine("copy", text, (SourceRef("resume", number, None, text),))


def _span_copy(ref: SourceRef, **fields: object) -> TailoredLine:
    """A copy line of one (expanded) resume ref: its text is the span, verbatim."""

    return TailoredLine("copy", ref.text, (ref,), **fields)  # type: ignore[arg-type]


def _resume_ref(number: int, ctx: TailorContext) -> SourceRef:
    """The cited resume line, expanded to its continuation lines (tailor-r2).

    Runs inside validation, before the numeric and posting-term guards, so
    the guards judge the joined span; an entry heading's copy line never
    comes through here (every other copy line does, 0110-006).
    """

    continued = tuple(
        item
        for item in ctx.continuations.get(number, ())
        if number < item <= len(ctx.resume_lines) and item not in ctx.withheld
    )
    text = " ".join(ctx.resume_lines[item - 1] for item in (number, *continued))
    return SourceRef("resume", number, None, text, continued)


def _drop_covered_copies(lines: Sequence[TailoredLine]) -> tuple[TailoredLine, ...]:
    """One container's lines without the copies an earlier copy's wrapped span already holds (0110-015).

    A copy line is stored EXPANDED to its continuation lines (``_resume_ref``),
    so a model that copies every physical line of a hard-wrapped paragraph
    (R46, R47, R48) would store line 1 = R46..R48, line 2 = R47..R48,
    line 3 = R48 and every consumer would print the paragraph's tail again.
    A copy whose resume line is a continuation of an earlier copy in the
    same container adds nothing and is dropped; every other line keeps the
    model's order (ids are assigned later, on the lines that remain).
    """

    out: list[TailoredLine] = []
    continued: set[int] = set()
    for line in lines:
        if line.kind == "copy" and line.refs:
            if line.refs[0].line in continued:
                continue
            continued.update(line.refs[0].continued_lines)
        out.append(line)
    return tuple(out)


def _refs(raw: object, where: str, ctx: TailorContext) -> tuple[SourceRef, ...]:
    if type(raw) is not list or not raw:
        _reject(f"{where} has no refs; every rewritten line cites 1 to {MAX_REFS_PER_LINE} sources")
    if len(raw) > MAX_REFS_PER_LINE:
        _reject(f"{where} has {len(raw)} refs; at most {MAX_REFS_PER_LINE} allowed")
    refs: list[SourceRef] = []
    for item in raw:
        if type(item) is not dict:
            _reject(f"{where} has a ref that is not an object")
        kind = item.get("kind")
        if kind == "resume":
            number = item.get("line")
            if isinstance(number, str) and number.isdigit():
                number = int(number)
            if type(number) is not int or isinstance(number, bool):
                _reject(f"{where} has a resume ref without a line number")
            if not 1 <= number <= len(ctx.resume_lines):
                _reject(f"{where} cites resume line {number}; the resume has {len(ctx.resume_lines)} lines")
            if number in ctx.withheld:
                _reject(f"{where} cites resume line {number}, which is not available (only the listed R lines can be used)")
            refs.append(_resume_ref(number, ctx))
        elif kind == "answer":
            raw_id = item.get("question_id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                _reject(f"{where} has an answer ref without a question_id")
            normalized = normalize_question_id(raw_id)
            answer = ctx.answers.get(normalized)
            if answer is None:
                _reject(f'{where} cites "{raw_id}", which is not an answered question')
            refs.append(SourceRef("answer", None, normalized, answer.guard_text))
        else:
            _reject(f"{where} has a ref whose kind is not resume or answer")
    return tuple(refs)


def check_rewritten_line(where: str, text: str, refs: Sequence[SourceRef], terms: Iterable[str]) -> None:
    """The two content guards for one already-provenance-checked rewritten line."""

    sources = [ref.text for ref in refs]
    cited = ", ".join(ref.label() for ref in refs)
    numbers = unsupported_numbers(text, sources)
    if numbers:
        _reject(f'{where} contains the number "{numbers[0].span}" that appears in none of its cited sources ({cited})')
    borrowed = unsupported_posting_terms(text, sources, terms)
    if borrowed:
        _reject(f'{where} contains the posting term "{borrowed[0]}" that appears in none of its cited sources ({cited})')


def check_single_entry(where: str, refs: Sequence[SourceRef], ctx: TailorContext) -> None:
    """The cross-entry guard (uat-bug-036): one rewritten line, one role or project.

    A line's resume refs may span several lines only when every line that
    sits in an entry (``resume_entries``) sits in the SAME entry.  Lines of
    section prose (summary, skills, header: no entry) and answers never
    conflict; so a summary line may cite a summary line plus one role, but
    not two roles.  Rejects with ``cross_entry_citation``.
    """

    owners: dict[int, int] = {}  # entry heading line -> first cited line in it
    for ref in refs:
        if ref.kind == "resume" and ref.line in ctx.entries:
            owners.setdefault(ctx.entries[ref.line], ref.line)
    if len(owners) > 1:
        parts = ", ".join(f'R{cited} in "{_display(ctx.resume_lines[heading - 1])}"' for heading, cited in owners.items())
        _reject(
            f"{where} is a cross_entry_citation: it combines resume lines from different roles or projects ({parts}); "
            "cite lines of one role or project only"
        )


def _rewritten_line(raw: object, where: str, ctx: TailorContext, terms: Iterable[str]) -> TailoredLine:
    if type(raw) is not dict:
        _reject(f"{where} is not an object")
    if set(raw) == {"copy"}:
        return _copy_line(raw, where, ctx, expand=True)
    unknown = set(raw) - {"text", "refs", "reason"}
    if unknown:
        _reject(f"{where} has unknown key(s) {sorted(unknown)}; a rewritten line is {{\"text\", \"refs\", \"reason\"}}")
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        _reject(f"{where} has no text")
    if len(text) > MAX_TEXT_CHARS:
        _reject(f"{where} text has {len(text)} characters; at most {MAX_TEXT_CHARS} allowed")
    if "\x00" in text:
        _reject(f"{where} text contains a NUL character")
    refs = _refs(raw.get("refs"), where, ctx)
    check_single_entry(where, refs, ctx)
    text = text.strip()
    check_rewritten_line(where, text, refs, terms)
    return TailoredLine("rewritten", text, refs, reason=_parse_reason(raw.get("reason")))


def _parse_reason(raw: object) -> LineReason | None:
    """The model's ``reason``, leniently: a malformed one is ``None`` (the
    line then falls back to its original in ``apply_no_loss``), never a
    rejection of the whole answer."""

    if type(raw) is not dict or raw.get("kind") not in REASON_KINDS:
        return None
    requirement = raw.get("requirement")
    phrase = raw.get("posting_phrase")
    requirement = requirement.strip() if isinstance(requirement, str) and requirement.strip() else None
    phrase = phrase.strip() if isinstance(phrase, str) and phrase.strip() else None
    if phrase is not None and len(phrase) > MAX_POSTING_PHRASE_CHARS:
        phrase = None
    if requirement is not None and len(requirement) > MAX_POSTING_PHRASE_CHARS:
        requirement = None
    return LineReason(raw["kind"], requirement, phrase)


def guard_terms(job: TailorJob, ctx: TailorContext) -> frozenset[str]:
    """The posting-term guard's list: matrix requirement names when a stored
    assessment exists, else the posting's own technical tokens."""

    exclude = (job.title, job.company, job.location)
    if ctx.matrix:
        terms = matrix_terms(ctx.matrix, exclude=exclude)
        if terms:
            return terms
    return posting_terms(job.posting_text, exclude=exclude)


def validate_tailored_output(decoded: Mapping[str, object], job: TailorJob, ctx: TailorContext) -> TailoredResume:
    """Shape, bounds, copy-only rule, provenance, numeric guard, posting-term guard.

    Raises ``TailorValidationError`` naming the first offending line and the
    reason; ``invoke_json_once`` feeds the message back on the one retry.
    The output is headerless (0110-003 P1): a ``header`` key the model sends
    anyway is accepted and discarded, whatever it holds, and never costs the
    retry; the result's ``header`` is always empty.
    """

    if not isinstance(decoded, Mapping):
        _reject("the answer is not a JSON object")
    unknown = set(decoded) - {"header", "sections"}
    if unknown:
        _reject(f"the answer has unknown top-level key(s) {sorted(unknown)}; only sections is allowed")
    terms = guard_terms(job, ctx)

    raw_sections = decoded.get("sections")
    if type(raw_sections) is not list:
        _reject(f"sections must be a list of 1 to {MAX_SECTIONS} sections")
    if not raw_sections:
        _reject("sections has 0 items; at least 1 section is required")
    if len(raw_sections) > MAX_SECTIONS:
        _reject(f"sections has {len(raw_sections)} items; at most {MAX_SECTIONS} allowed")

    sections: list[TailoredSection] = []
    seen: set[str] = set()
    total = 0
    for index, raw in enumerate(raw_sections, 1):
        if type(raw) is not dict:
            _reject(f"sections[{index}] is not an object")
        heading = raw.get("heading")
        if not isinstance(heading, str) or heading.strip().lower() not in SECTION_HEADINGS:
            _reject(f"sections[{index}] heading must be one of {', '.join(SECTION_HEADINGS)}")
        heading = heading.strip().lower()
        if heading in seen:
            _reject(f"sections[{index}] repeats the {heading} heading; each section appears at most once")
        seen.add(heading)
        unknown = set(raw) - {"heading", "lines", "entries"}
        if unknown:
            _reject(f"{heading} section has unknown key(s) {sorted(unknown)}")
        if heading in ENTRY_SECTIONS:
            raw_entries = raw.get("entries")
            if raw_entries is None and "lines" in raw:
                _reject(f"{heading} section must hold entries (heading_ref + bullets), not lines")
            if type(raw_entries) is not list or not raw_entries:
                _reject(f"{heading} section must hold 1 to {MAX_ENTRIES_PER_SECTION} entries")
            if len(raw_entries) > MAX_ENTRIES_PER_SECTION:
                _reject(f"{heading} section has {len(raw_entries)} entries; at most {MAX_ENTRIES_PER_SECTION} allowed")
            entries: list[TailoredEntry] = []
            for position, raw_entry in enumerate(raw_entries, 1):
                where = f"{heading} entry {position}"
                if type(raw_entry) is not dict:
                    _reject(f"{where} is not an object")
                unknown = set(raw_entry) - {"heading_ref", "bullets"}
                if unknown:
                    _reject(f"{where} has unknown key(s) {sorted(unknown)}; an entry is {{\"heading_ref\", \"bullets\"}}")
                raw_heading = raw_entry.get("heading_ref")
                if type(raw_heading) is dict:
                    raw_heading = [raw_heading]
                if type(raw_heading) is not list or not raw_heading:
                    _reject(f"{where} heading_ref must be 1 to {MAX_HEADING_LINES} copy lines")
                if len(raw_heading) > MAX_HEADING_LINES:
                    _reject(f"{where} heading_ref has {len(raw_heading)} lines; at most {MAX_HEADING_LINES} allowed")
                heading_lines = tuple(
                    _copy_line(item, f"{where} heading[{offset}]", ctx) for offset, item in enumerate(raw_heading, 1)
                )
                raw_bullets = raw_entry.get("bullets", [])
                if raw_bullets is None:
                    raw_bullets = []
                if type(raw_bullets) is not list:
                    _reject(f"{where} bullets must be a list")
                bullets = _drop_covered_copies(
                    [_rewritten_line(item, f"{where} bullet {offset}", ctx, terms) for offset, item in enumerate(raw_bullets, 1)]
                )
                total += len(heading_lines) + len(bullets)
                if total > MAX_TOTAL_LINES:
                    _reject(f"the sections hold more than {MAX_TOTAL_LINES} lines in total; at most {MAX_TOTAL_LINES} allowed")
                entries.append(TailoredEntry(heading_lines, bullets))
            sections.append(TailoredSection(heading, (), tuple(entries)))
        else:
            raw_lines = raw.get("lines")
            if raw_lines is None and "entries" in raw:
                _reject(f"{heading} section must hold lines, not entries")
            if type(raw_lines) is not list or not raw_lines:
                _reject(f"{heading} section must hold at least 1 line")
            lines = _drop_covered_copies(
                [_rewritten_line(item, f"{heading} line {offset}", ctx, terms) for offset, item in enumerate(raw_lines, 1)]
            )
            total += len(lines)
            if total > MAX_TOTAL_LINES:
                _reject(f"the sections hold more than {MAX_TOTAL_LINES} lines in total; at most {MAX_TOTAL_LINES} allowed")
            sections.append(TailoredSection(heading, lines, ()))
    return TailoredResume((), tuple(sections))


# --- no loss: a weaker rewrite falls back to the original line (0110-006) -------------------

_REQUIREMENT_ID = re.compile(r"[Mm]?(\d{1,4})")
_WHITESPACE = re.compile(r"\s+")
_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")
_ONGOING = re.compile(r"\b(?:present|current|now|today|ongoing)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _Verdict:
    """What ``apply_no_loss`` decided for one rewritten line."""

    ok: bool
    reason: LineReason | None  # the validated reason when valid, else the model's (for the record)
    reason_invalid: bool
    lost: dict[str, list[str]]
    merge: bool
    spans: tuple[SourceRef, ...]  # the resume spans a fallback copies (summary: the prose ones only)


def _flat(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


def _spans(refs: Iterable[SourceRef]) -> tuple[SourceRef, ...]:
    """The distinct resume spans among ``refs``: a ref whose line another ref's span already covers is folded in."""

    resume = [ref for ref in refs if ref.kind == "resume"]
    covered = {line for ref in resume for line in ref.continued_lines}
    out: list[SourceRef] = []
    seen: set[int] = set()
    for ref in resume:
        if ref.line in covered or ref.line in seen:
            continue
        seen.add(ref.line)  # type: ignore[arg-type]
        out.append(ref)
    return tuple(out)


def _visible_text(ref: SourceRef, visible: Mapping[int, str]) -> str:
    """The span as the MODEL saw it (privacy-redacted): a rewrite is never
    blamed for dropping a name or contact detail it was never shown."""

    numbers = (ref.line, *ref.continued_lines)
    if all(number in visible for number in numbers):
        return " ".join(visible[number] for number in numbers)  # type: ignore[index]
    return ref.text


def _check_reason(line: TailoredLine, job: TailorJob, ctx: TailorContext) -> tuple[LineReason | None, bool]:
    """The deterministic reason check (0110-006 §2b): the reason as stored, and whether it holds.

    Valid when ``requirement`` is an ``M<n>`` of ``ctx.matrix`` OR
    ``posting_phrase`` occurs (case- and whitespace-insensitively) in the
    posting; a ``surface`` reason also needs one of the requirement's or
    phrase's words in both the rewrite and a cited resume line; an
    ``answer`` reason needs an answer ref.  Only the anchors that hold are
    stored on a valid reason.
    """

    from .tailor_no_loss import anchor_terms

    reason = line.reason
    if reason is None:
        return None, False
    requirement: str | None = None
    phrase: str | None = None
    anchors: set[str] = set()
    match = _REQUIREMENT_ID.fullmatch(reason.requirement or "")
    if match and 1 <= int(match.group(1)) <= len(ctx.matrix):
        requirement = f"M{int(match.group(1))}"
        anchors |= anchor_terms(ctx.matrix[int(match.group(1)) - 1].requirement)
    if reason.posting_phrase and _flat(reason.posting_phrase) in _flat(job.posting_text):
        phrase = reason.posting_phrase
        anchors |= anchor_terms(phrase)
    if requirement is None and phrase is None:
        return reason, False
    if reason.kind == "surface":
        sources: set[str] = set()
        for ref in line.refs:
            if ref.kind == "resume":
                sources |= source_terms(ref.text)
        if not anchors & source_terms(line.text) & sources:
            return reason, False
    if reason.kind == "answer" and not any(ref.kind == "answer" for ref in line.refs):
        return reason, False
    return LineReason(reason.kind, requirement, phrase), True


def _judge(line: TailoredLine, position: str, job: TailorJob, ctx: TailorContext, visible: Mapping[int, str]) -> _Verdict:
    from .tailor_no_loss import lost_items

    reason, valid = _check_reason(line, job, ctx)
    spans = _spans(line.refs)
    if position == "summary":
        # A summary sentence owes every item of the summary PROSE it cites;
        # it may borrow a phrase from one role (the cross-entry guard) without
        # owing that role's every fact, and falls back to the prose lines only.
        spans = tuple(ref for ref in spans if ref.line not in ctx.entries)
        merge = False
    else:
        merge = len(spans) > 1  # 0110-006 Q2: no merges in v1; one resume line (span) per rewrite
    lost = lost_items(line.text, [_visible_text(ref, visible) for ref in spans], skills=position == "skills")
    return _Verdict(valid and not lost and not merge, reason, not valid, lost, merge, spans)


def _lost_pairs(lost: Mapping[str, Sequence[str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple((rule, tuple(lost[rule])) for rule in LOST_RULES if rule in lost)


def _settle(lines: Sequence[TailoredLine], position: str, job: TailorJob, ctx: TailorContext, visible: Mapping[int, str]) -> tuple[tuple[TailoredLine, ...], list[LineAlternative]]:
    """One container's lines (an entry's bullets or a lines section) after the reason and no-loss checks.

    A failing rewrite becomes COPIES of its cited spans (the first carries
    the rejected rewrite as ``alternative``); a copy of a span the container
    already shows is not repeated (the rejected rewrite is recorded on the
    retained copy with ``dropped_duplicate``); a rewrite with no span to fall
    back to (answer-only, or a summary citing only role lines) is dropped and
    recorded in the container's ``dropped``.
    """

    later_copies = {line.refs[0].line for line in lines if line.kind == "copy" and line.refs}
    out: list[TailoredLine] = []
    shown: dict[int, int] = {}  # resume line -> index in ``out`` of the copy showing it
    pending: list[tuple[int, LineAlternative]] = []  # duplicates to record on a retained copy
    dropped: list[LineAlternative] = []
    for line in lines:
        if line.kind == "copy":
            shown.setdefault(line.refs[0].line, len(out))  # type: ignore[arg-type]
            out.append(replace(line, origin="model"))
            continue
        verdict = _judge(line, position, job, ctx, visible)
        if verdict.ok:
            original = None
            if verdict.spans:
                text = " ".join(ref.text for ref in verdict.spans)
                original = LineAlternative("copy", text, verdict.spans)
            out.append(replace(line, reason=verdict.reason, origin="model", alternative=original))
            continue
        rejected = LineAlternative(
            "rewritten", line.text, line.refs, verdict.reason, _lost_pairs(verdict.lost),
            reason_invalid=verdict.reason_invalid, merge=verdict.merge,
        )
        placed = False
        for ref in verdict.spans:
            if ref.line in shown or ref.line in later_copies:
                continue
            shown[ref.line] = len(out)  # type: ignore[index]
            out.append(_span_copy(ref, origin="fallback", alternative=None if placed else rejected))
            placed = True
        if placed:
            continue
        if verdict.spans:
            pending.append((verdict.spans[0].line, replace(rejected, dropped_duplicate=True)))  # type: ignore[arg-type]
        else:
            dropped.append(rejected)
    for number, rejected in pending:
        index = shown.get(number)
        if index is not None and out[index].alternative is None:
            out[index] = replace(out[index], origin="fallback", alternative=rejected)
        else:
            dropped.append(rejected)
    return tuple(out), dropped


def _is_old_role(heading: Sequence[TailoredLine], today: date) -> bool:
    """An OLD role (``LENGTH_RULE``): its heading's latest year is more than
    ``old_role_years`` back; "present"/"current" or no year at all is never old."""

    text = " ".join(line.text for line in heading)
    if _ONGOING.search(text):
        return False
    years = [int(year) for year in _YEAR.findall(text)]
    return bool(years) and today.year - max(years) > LENGTH_RULE.old_role_years


def _role_dropped(heading: Sequence[TailoredLine], bullets: Sequence[TailoredLine], trimmed: Sequence[TailoredLine], ctx: TailorContext) -> list[LineAlternative]:
    """The role's resume bullets the result does not show (Q4: recorded for Show changes).

    Candidates: every line of the resume entry the heading copies
    (``ctx.entries``) that is not its heading, not a wrapped tail of another
    line and not withheld, plus the spans of bullets the length rule
    trimmed; minus every line a shown bullet cites or copies.
    """

    covered = {number for line in bullets for ref in line.refs if ref.kind == "resume" for number in (ref.line, *ref.continued_lines)}
    covered |= {ref.line for line in heading for ref in line.refs}
    tails = {number for span in ctx.continuations.values() for number in span}
    owners = {ctx.entries[ref.line] for line in heading for ref in line.refs if ref.line in ctx.entries}
    candidates = {
        number for number, owner in ctx.entries.items()
        if owner in owners and number != owner and number not in tails and number not in ctx.withheld
        and not _is_heading_line(ctx.resume_lines[number - 1])
    }
    out: list[LineAlternative] = []
    for line in trimmed:
        spans = _spans(line.refs)
        if not spans:
            out.append(LineAlternative(line.kind, line.text, line.refs, line.reason))
        candidates |= {ref.line for ref in spans}  # type: ignore[misc]
    for number in sorted(candidates - covered):
        ref = _resume_ref(number, ctx)
        out.append(LineAlternative("copy", ref.text, (ref,)))
    return out


def _with_ids(result: TailoredResume) -> TailoredResume:
    """``L<n>`` on every body line, document order (entry headings included)."""

    counter = 0

    def tag(line: TailoredLine) -> TailoredLine:
        nonlocal counter
        counter += 1
        return replace(line, id=f"L{counter}")

    sections = []
    for section in result.sections:
        entries = tuple(
            replace(entry, heading=tuple(tag(line) for line in entry.heading), bullets=tuple(tag(line) for line in entry.bullets))
            for entry in section.entries
        )
        sections.append(replace(section, lines=tuple(tag(line) for line in section.lines), entries=entries))
    return replace(result, sections=tuple(sections))


def apply_no_loss(result: TailoredResume, job: TailorJob, ctx: TailorContext, *, today: date | None = None) -> TailoredResume:
    """Settle a validated result (0110-006): reasons, no-loss fallbacks, the length rule, ids.

    Runs after ``validate_tailored_output`` inside ``tailor_once``'s
    validate step, so it never spends the retry: fabrication rejects the
    whole answer there; weakening only replaces the weaker line here.
    """

    today = today or datetime.now(UTC).date()
    assert ctx.model is not None
    visible = dict(ctx.model.lines)
    sections: list[TailoredSection] = []
    for section in result.sections:
        if section.heading in ENTRY_SECTIONS:
            entries: list[TailoredEntry] = []
            for entry in section.entries:
                heading = tuple(replace(line, origin="model") for line in entry.heading)
                bullets, dropped = _settle(entry.bullets, "bullet", job, ctx, visible)
                trimmed: tuple[TailoredLine, ...] = ()
                if _is_old_role(heading, today) and len(bullets) > LENGTH_RULE.old_role_bullets:
                    bullets, trimmed = bullets[: LENGTH_RULE.old_role_bullets], bullets[LENGTH_RULE.old_role_bullets :]
                dropped += _role_dropped(heading, bullets, trimmed, ctx)
                entries.append(TailoredEntry(heading, bullets, tuple(dropped)))
            sections.append(TailoredSection(section.heading, (), tuple(entries)))
        else:
            lines, dropped = _settle(section.lines, section.heading, job, ctx, visible)
            sections.append(TailoredSection(section.heading, lines, (), tuple(dropped)))
    return _with_ids(TailoredResume(result.header, tuple(sections)))


# --- the operator's per-line choice and the line counts (0110-006) -----------------------------

LINE_CHOICES: frozenset[str] = frozenset({"original", "rewritten"})
#: ``PUT /api/tailored-resumes/lines``'s ``use``: a choice, or ``custom`` with the caller's ``text`` (0110-032).
LINE_USES: frozenset[str] = LINE_CHOICES | {"custom"}
#: A custom line's length bound: the same as a model line's.
MAX_CUSTOM_TEXT_CHARS = MAX_TEXT_CHARS
_CUSTOM_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CUSTOM_MARKER = re.compile(r"\A[-*•](?:\s+|\Z)")


def personal_info_found(text: str, *, names: Iterable[str] = ()) -> list[str]:
    """What looks personal in one line of text (0110-032): ``[]`` when nothing does.

    The paste path's local check (``resume_pii.detect_contact_details``: email, phone,
    linkedin/github links, a street address), plus anything ``resume_privacy.redact_inline``
    would remove before a model sees the line (other links), plus ``name``: the line holds
    one of ``names`` (a name the caller knows; GigAI stores none, 0110-046) or, when no name is known, is
    strictly name-shaped (``resume_privacy.is_name_line``).  Local and pure: no model, no I/O.
    """

    found = list(detect_contact_details(text))
    if not found and redact_inline(text) != text:
        found.append("links")
    flat = " ".join(text.split()).casefold()
    known = [" ".join(name.split()).casefold() for name in names if isinstance(name, str) and name.strip()]
    if any(name in flat for name in known) or (not known and is_name_line(text)):
        found.append("name")
    return found


def custom_line_text(text: object, *, names: Iterable[str] = ()) -> str:
    """The text a custom line stores, or ``TailorError``: ``invalid_value`` / ``personal_info_refused``.

    One line, at most ``MAX_CUSTOM_TEXT_CHARS`` characters after trimming; a leading
    bullet marker (``- ``) is dropped because the renderers add their own.
    """

    if not isinstance(text, str):
        raise TailorError("invalid_value", "text must be a string")
    if _CUSTOM_CONTROL.search(text):
        raise TailorError("invalid_value", "text must be one line without control characters")
    clean = _CUSTOM_MARKER.sub("", text.strip()).strip()
    if not clean:
        raise TailorError("invalid_value", "text must not be empty")
    if len(clean) > MAX_CUSTOM_TEXT_CHARS:
        raise TailorError("invalid_value", f"text is longer than {MAX_CUSTOM_TEXT_CHARS} characters")
    found = personal_info_found(clean, names=names)
    if found:
        raise TailorError(
            "personal_info_refused",
            f"text looks like a name or contact line ({', '.join(found)}); GigAI stores no name or contact details: "
            "you type them in Scout's Generate PDF form when you make the PDF, never in a resume line",
        )
    return clean


def _map_body_line(stored: "TailorResponse", line_id: str, change: Callable[[TailoredLine], TailoredLine]) -> "TailorResponse":
    """``stored`` with ``change`` applied to body line ``line_id`` (never an entry heading); markdown re-rendered."""

    found = False

    def visit(line: TailoredLine) -> TailoredLine:
        nonlocal found
        if line.id != line_id:
            return line
        found = True
        return change(line)

    sections = tuple(
        replace(
            section,
            lines=tuple(visit(line) for line in section.lines),
            entries=tuple(replace(entry, bullets=tuple(visit(line) for line in entry.bullets)) for entry in section.entries),
        )
        for section in stored.result.sections
    )
    if not found:
        raise TailorError("invalid_value", f"no line {line_id!r} in this tailored resume")
    result = replace(stored.result, sections=sections)
    if result == stored.result:
        return stored
    return replace(stored, result=result, markdown=render_markdown(result))


def apply_line_edit(stored: "TailorResponse", line_id: str, text: object, *, names: Iterable[str] = ()) -> "TailorResponse":
    """Show the caller's own ``text`` on body line ``line_id`` (0110-032).

    The line becomes ``kind: custom``, ``origin: user``, with no refs, reason or
    alternative; ``edited_from`` keeps the line it replaced (an edit of an edit keeps the
    first one's), so ``apply_line_choice`` can show the original or the rewrite again.
    ``text`` goes through ``custom_line_text`` (length, one line, the personal-info check).
    The same text again returns ``stored`` unchanged; ``updated_at`` is unchanged.  Pure.
    """

    clean = custom_line_text(text, names=names)

    def edit(line: TailoredLine) -> TailoredLine:
        if line.kind == "custom" and line.text == clean:
            return line
        return TailoredLine("custom", clean, (), line.id, None, "user", None, line.edited_from if line.kind == "custom" else line)

    return _map_body_line(stored, line_id, edit)


def apply_line_choice(stored: "TailorResponse", line_id: str, use: str) -> "TailorResponse":
    """Show the ``original`` or the ``rewritten`` version of line ``line_id``.

    The chosen version is materialized into ``result`` (the shown line and
    its ``alternative`` swap; ``origin`` becomes ``user``; ``id``, ``lost``
    and the flags stay with the pair) and ``markdown`` is re-rendered;
    ``updated_at`` is unchanged.  A line with no alternative, or a choice
    already shown, returns ``stored`` unchanged (idempotent).  On an EDITED
    line (``kind: custom``, 0110-032) the line it replaced comes back first and
    the choice applies to that; asking for a version that line never had is
    ``invalid_value`` naming the one it has.  Choosing
    ``rewritten`` on a fallback restores a rewrite that already passed every
    fabrication guard.  An unknown id or choice is ``TailorError("invalid_value")``.
    Pure: the caller stores the result (``save_tailor_response``).
    """

    if use not in LINE_CHOICES:
        raise TailorError("invalid_value", "use must be original or rewritten")
    wanted = "copy" if use == "original" else "rewritten"

    def choose(line: TailoredLine) -> TailoredLine:
        # An edited line (0110-032) goes back to the line it replaced first, then the choice applies to that.
        edited = line.kind == "custom" and line.edited_from is not None
        base = replace(line.edited_from, id=line.id) if edited else line  # type: ignore[arg-type]
        alternative = base.alternative
        if base.kind == wanted:
            return base
        if alternative is None or alternative.kind != wanted:
            if edited:
                other = "rewritten" if use == "original" else "original"
                raise TailorError("invalid_value", f"line {line_id!r} has no {use} version; use {other}")
            return line
        return TailoredLine(
            alternative.kind, alternative.text, alternative.refs, base.id,
            alternative.reason if alternative.kind == "rewritten" else None,
            "user",
            replace(alternative, kind=base.kind, text=base.text, refs=base.refs, reason=base.reason),
        )

    return _map_body_line(stored, line_id, choose)


@dataclass(frozen=True)
class TailorLineStats:
    """Line counts from a stored result alone (0110-006 §3e/§5a), shared by the eval, the UI and the tests.

    ``rewritable_lines`` is every body line except entry headings.  A
    model-proposed rewrite is a shown rewrite, a line whose alternative is a
    rewrite (a fallback, or a rewrite the operator set aside), or a rejected
    rewrite in a ``dropped`` list.  A fallback is a line with
    ``origin == "fallback"`` and a rewritten alternative, or a rejected
    rewrite in a ``dropped`` list; ``fallbacks_by_rule`` counts each
    ``lost`` rule and flag once per fallback.
    """

    rewritable_lines: int
    shown_rewritten: int
    copied: int
    answer_only_lines: int
    model_rewrites: int
    fallbacks: int
    fallbacks_by_rule: Mapping[str, int]
    kept_original_by_user: int
    dropped_bullets: int
    #: Lines showing the operator's own text (``kind: custom``, 0110-032): neither copied nor rewritten.
    edited: int = 0

    @property
    def model_rewrite_rate(self) -> float:
        return self.model_rewrites / self.rewritable_lines if self.rewritable_lines else 0.0

    @property
    def final_rewrite_rate(self) -> float:
        return self.shown_rewritten / self.rewritable_lines if self.rewritable_lines else 0.0

    def to_json(self) -> dict[str, object]:
        # ``edited`` is serialized only when a line is edited, so a result without edits reads as before.
        return {
            **({"edited": self.edited} if self.edited else {}),
            "rewritable_lines": self.rewritable_lines,
            "shown_rewritten": self.shown_rewritten,
            "copied": self.copied,
            "answer_only_lines": self.answer_only_lines,
            "model_rewrites": self.model_rewrites,
            "fallbacks": self.fallbacks,
            "fallbacks_by_rule": dict(self.fallbacks_by_rule),
            "kept_original_by_user": self.kept_original_by_user,
            "dropped_bullets": self.dropped_bullets,
            "model_rewrite_rate": self.model_rewrite_rate,
            "final_rewrite_rate": self.final_rewrite_rate,
        }


def tailor_line_stats(result: TailoredResume) -> TailorLineStats:
    """Count a result's lines (see ``TailorLineStats``)."""

    shown = [line for section in result.sections for line in section.body_lines()]
    # What the model proposed is counted on the line an edit replaced; what is shown, on the line itself.
    body = [line.edited_from if line.kind == "custom" and line.edited_from is not None else line for line in shown]
    dropped = [item for section in result.sections for item in (*section.dropped, *(d for entry in section.entries for d in entry.dropped))]
    rules = {rule: 0 for rule in (*LOST_RULES, *FALLBACK_FLAGS)}
    rejected = [line.alternative for line in body if line.origin == "fallback" and line.alternative is not None and line.alternative.kind == "rewritten"]
    rejected += [item for item in dropped if item.kind == "rewritten"]
    for item in rejected:
        for rule, _items in item.lost or ():
            rules[rule] += 1
        for flag in FALLBACK_FLAGS:
            rules[flag] += int(getattr(item, flag))
    shown_rewritten = sum(line.kind == "rewritten" for line in shown)
    return TailorLineStats(
        rewritable_lines=len(body),
        shown_rewritten=shown_rewritten,
        copied=sum(line.kind == "copy" for line in shown),
        answer_only_lines=sum(line.kind == "rewritten" and not any(ref.kind == "resume" for ref in line.refs) for line in shown),
        model_rewrites=sum(line.kind == "rewritten" for line in body)
        + sum(line.kind == "copy" and line.alternative is not None and line.alternative.kind == "rewritten" for line in body)
        + sum(item.kind == "rewritten" for item in dropped),
        fallbacks=len(rejected),
        fallbacks_by_rule=rules,
        kept_original_by_user=sum(line.origin == "user" and line.kind == "copy" for line in shown),
        dropped_bullets=sum(item.kind == "copy" for item in dropped),
        edited=sum(line.kind == "custom" for line in shown),
    )


# --- markdown, rendered by code -----------------------------------------------------------

_LEADING_MARKERS = re.compile(r"\A(?:[#>*\-•–—]+\s*)+")
#: Paired strong-emphasis markers (``**...**``, ``__...__``); both ends go together.
_EMPHASIS_PAIRS = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")


def _display(text: str) -> str:
    """A copied resume line without its own markdown/bullet markers, so the
    renderer can choose the marker for the position it lands in.  A paired
    emphasis (``**Analytics Engineer — Northwind** (2022)``) loses BOTH ends,
    never just the leading one; the stored JSON text stays verbatim."""

    unemphasized = _EMPHASIS_PAIRS.sub(lambda m: m.group(1) or m.group(2), text)
    stripped = _LEADING_MARKERS.sub("", unemphasized).strip()
    return stripped or text.strip()


def shown_text(line: TailoredLine) -> str:
    """A body line as the markdown and the PDF print it: a copy (the model's
    or a fallback) without its own bullet/heading markers, a rewrite as written."""

    return _display(line.text) if line.kind == "copy" else line.text


def _refs_comment(line: TailoredLine) -> str:
    if line.kind == "custom":  # the operator's own text: no source is claimed
        return "<!-- edited -->"
    return "<!-- " + ", ".join(ref.label() for ref in line.refs) + " -->"


def replaced_line(line: TailoredLine) -> TailoredLine:
    """The line an edit replaced (``edited_from``), else the line itself: what the resume-line bookkeeping reads."""

    return line.edited_from if line.kind == "custom" and line.edited_from is not None else line


def _resume_numbers(line: TailoredLine) -> set[int]:
    """The resume line numbers a line cites, wrapped continuations included."""

    return {number for ref in line.refs if ref.kind == "resume" for number in (ref.line, *ref.continued_lines)}  # type: ignore[misc]


def _printed_lines(lines: Sequence[TailoredLine]) -> list[TailoredLine]:
    """One container's lines as printed: a copy whose text the line printed
    before it already holds, citing only resume lines that line cites, is
    skipped (0110-015: an older stored result may carry a hard-wrapped
    paragraph copied line by line, each copy expanded to the paragraph's tail)."""

    out: list[TailoredLine] = []
    for line in lines:
        if line.kind == "copy" and out:
            numbers = _resume_numbers(line)
            previous = replaced_line(out[-1])
            if numbers and numbers <= _resume_numbers(previous) and _flat(line.text) in _flat(previous.text):
                continue
        out.append(line)
    return out


def render_markdown(result: TailoredResume) -> str:
    """The ``.md`` text, from the validated JSON only; each line keeps its refs
    in a trailing HTML comment (``<!-- R12, A cloud:gcp -->``).  No copy line
    repeats text the line above it already printed (``_printed_lines``)."""

    out: list[str] = []
    for index, line in enumerate(result.header):
        shown = _display(line.text)
        out.append((f"# {shown}" if index == 0 else shown) + " " + _refs_comment(line))
    if result.header:
        out.append("")
    for section in result.sections:
        if section.is_empty():  # every line of it fell back to nothing (0110-006)
            continue
        out.append(f"## {section.heading.capitalize()}")
        out.append("")
        if section.heading in ENTRY_SECTIONS:
            for entry in section.entries:
                for index, line in enumerate(entry.heading):
                    shown = _display(line.text)
                    out.append((f"### {shown}" if index == 0 else shown) + " " + _refs_comment(line))
                if entry.bullets:
                    out.append("")
                for line in _printed_lines(entry.bullets):
                    out.append(f"- {shown_text(line)} {_refs_comment(line)}")
                out.append("")
        else:
            for line in _printed_lines(section.lines):
                out.append(f"- {shown_text(line)} {_refs_comment(line)}")
            out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


# --- the model call -----------------------------------------------------------------------


def tailor_once(binding: object, job: TailorJob, ctx: TailorContext) -> AssessAttempt:
    """One tailoring through the shared loop: render, invoke, extract, validate, retry once.

    ``attempt.parsed`` is a ``TailoredResume`` when ``ok``, already settled
    by ``apply_no_loss`` (fallbacks never cost the retry).  Exactly the
    exception mapping and retry rule ``assess_once`` has (``invoke_json_once``).
    """

    return invoke_json_once(
        binding,
        lambda validation_error: render_tailor_prompt(job, ctx, validation_error),
        lambda decoded: apply_no_loss(validate_tailored_output(decoded, job, ctx), job, ctx),
    )


# --- DTOs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class TailorRequest(_Contract):
    """``POST /api/tailored-resumes`` body: the job, which resume, which model."""

    schema_version: ClassVar[str] = "scout-tailor-request:1"
    job: AssessJobInput
    resume: AssessResumeInput = field(default_factory=AssessResumeInput)
    model_target: ModelTarget | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "job": self.job.to_json(),
            "resume": self.resume.to_json(),
            "model_target": None if self.model_target is None else _json_enum(self.model_target),
        }

    @classmethod
    def from_json(cls, obj: object) -> "TailorRequest":
        value = _object_with_optional(obj, ("job",), ("schema_version", "resume", "model_target"), "tailor_request")
        if "schema_version" in value and value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "tailor_request.schema_version is unsupported")
        resume = value.get("resume")
        model_target = value.get("model_target")
        return cls(
            job=AssessJobInput.from_json(value["job"]),
            resume=AssessResumeInput() if resume is None else AssessResumeInput.from_json(resume),
            model_target=None if model_target is None else _enum(model_target, ModelTarget, "tailor_request.model_target"),
        )


@dataclass(frozen=True)
class TailorSources(_Contract):
    """What the tailoring was grounded in: identities and revisions, never the texts."""

    schema_version: ClassVar[str] = "scout-tailor-sources:1"
    resume_content_sha256: str
    resume_line_count: int
    #: normalized question_id -> revision_id.  Serialized as a LIST of
    #: ``{"question_id", "revision_id"}`` objects, never as an object keyed by
    #: the id: ``canonical.parse_json_bytes`` (the stored-file reader) rejects
    #: a member name like ``cloud:gcp``.
    answers: Mapping[str, str]
    assessment_stored_path: str | None

    def to_json(self) -> dict[str, object]:
        return {
            "resume_content_sha256": self.resume_content_sha256,
            "resume_line_count": self.resume_line_count,
            "answers": [
                {"question_id": question_id, "revision_id": revision_id}
                for question_id, revision_id in sorted(self.answers.items())
            ],
            "assessment_stored_path": self.assessment_stored_path,
        }

    @classmethod
    def from_json(cls, obj: object) -> "TailorSources":
        value = _object_with_optional(
            obj, ("resume_content_sha256", "resume_line_count", "answers", "assessment_stored_path"), (), "tailor_sources"
        )
        raw_answers = value["answers"]
        if type(raw_answers) is not list:
            _fail("wrong_type", "tailor_sources.answers must be an array")
        answers: dict[str, str] = {}
        for item in raw_answers:
            entry = _object_with_optional(item, ("question_id", "revision_id"), (), "tailor_sources.answers[]")
            answers[_string(entry["question_id"], "question_id")] = _string(entry["revision_id"], "revision_id")
        count = value["resume_line_count"]
        if type(count) is not int or count < 0:
            _fail("wrong_type", "tailor_sources.resume_line_count must be a non-negative integer")
        return cls(
            _digest_value(value["resume_content_sha256"], "resume_content_sha256"),
            count,
            answers,
            _optional_string(value["assessment_stored_path"], "assessment_stored_path"),
        )


@dataclass(frozen=True)
class TailorResponse(_Contract):
    """One tailored resume: identities, sources, the validated structure, the markdown, storage.

    Never carries the posting text (``job`` is serialized WITHOUT ``text``;
    at most a 60-character ``reason.posting_phrase`` per rewritten line).
    It DOES carry resume-derived text (the copied lines, the rewritten lines
    and every ref's source text) -- that is the product.
    """

    schema_version: ClassVar[str] = "scout-tailor-response:1"
    job: ResolvedJob
    resume: ResolvedResume
    sources: TailorSources
    result: TailoredResume
    markdown: str
    producer: Producer
    usage: UsageBlock | None
    instructions_digest: str
    created_at: str
    updated_at: str
    stored_path: str
    markdown_path: str

    def to_json(self) -> dict[str, object]:
        job = self.job.to_json()
        del job["text"]
        return {
            "schema_version": self.schema_version,
            "job": job,
            "resume": self.resume.to_json(),
            "sources": self.sources.to_json(),
            "result": self.result.to_json(),
            "markdown": self.markdown,
            "producer": self.producer.to_json(),
            "usage": None if self.usage is None else self.usage.to_json(),
            "instructions_digest": self.instructions_digest,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stored_path": self.stored_path,
            "markdown_path": self.markdown_path,
        }

    @classmethod
    def from_json(cls, obj: object) -> "TailorResponse":
        value = _object_with_optional(
            obj,
            (
                "schema_version", "job", "resume", "sources", "result", "markdown", "producer", "usage",
                "instructions_digest", "created_at", "updated_at", "stored_path", "markdown_path",
            ),
            (),
            "tailor_response",
        )
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "tailor_response.schema_version is unsupported")
        job = value["job"]
        if type(job) is not dict:
            _fail("wrong_type", "tailor_response.job must be an object")
        if "text" in job:
            _fail("unknown_key", "tailor_response.job never carries the job text")
        usage = value["usage"]
        return cls(
            job=ResolvedJob.from_json({**job, "text": ""}),
            resume=ResolvedResume.from_json(value["resume"]),
            sources=TailorSources.from_json(value["sources"]),
            result=TailoredResume.from_json(value["result"]),
            markdown=_string(value["markdown"], "markdown"),
            producer=Producer.from_json(value["producer"]),
            usage=None if usage is None else UsageBlock.from_json(usage),
            instructions_digest=_digest_value(value["instructions_digest"], "instructions_digest"),
            created_at=_string(value["created_at"], "created_at"),
            updated_at=_string(value["updated_at"], "updated_at"),
            stored_path=_string(value["stored_path"], "stored_path"),
            markdown_path=_string(value["markdown_path"], "markdown_path"),
        )


@dataclass(frozen=True)
class TailoredResumesListResponse(_Contract):
    """``GET /api/tailored-resumes``: stored tailored resumes, newest first."""

    schema_version: ClassVar[str] = "scout-tailored-resumes-response:1"
    items: tuple[TailorResponse, ...]

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "items": [item.to_json() for item in self.items]}

    @classmethod
    def from_json(cls, obj: object) -> "TailoredResumesListResponse":
        value = _object_with_optional(obj, ("schema_version", "items"), (), "tailored_resumes_list_response")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "tailored_resumes_list_response.schema_version is unsupported")
        if type(value["items"]) is not list:
            _fail("wrong_type", "tailored_resumes_list_response.items must be an array")
        return cls(tuple(TailorResponse.from_json(item) for item in value["items"]))


# --- storage ------------------------------------------------------------------------------


def tailored_resume_dir(home_root: Path, target: Path) -> Path:
    return home_root / "scout" / project_id(home_root, target) / "resumes"


def tailored_resume_path(home_root: Path, target: Path, profile_id: str | None, job_identity: str) -> Path:
    digest = digest_imported_bytes(job_identity.encode("utf-8")).removeprefix("sha256:")
    return tailored_resume_dir(home_root, target) / resume_key(profile_id) / f"{digest}.json"


def _read_stored(path: Path) -> TailorResponse | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return TailorResponse.from_json(parse_json_bytes(path.read_bytes()))
    except Exception:
        return None


def list_tailored_resumes(
    home_root: Path, target: Path, *, profile_id: str | None = None, job_identity: str | None = None
) -> tuple[TailorResponse, ...]:
    """Every stored tailored resume for this project, newest ``updated_at`` first.

    ``profile_id`` narrows to one resume identity (``"ephemeral"`` selects
    the pasted-resume runs); ``job_identity`` narrows to one job.  Files
    that no longer parse are skipped, never raised.
    """

    if profile_id is not None and not _SAFE_RESUME_KEY.fullmatch(profile_id):
        raise TailorError("invalid_value", "profile_id filter is not a profile id")
    try:
        root = tailored_resume_dir(home_root, target)
    except Exception as exc:
        raise TailorError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    if not root.is_dir():
        return ()
    subdirs = [root / profile_id] if profile_id is not None else sorted(p for p in root.iterdir() if p.is_dir())
    items: list[TailorResponse] = []
    for subdir in subdirs:
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.glob("*.json")):
            stored = _read_stored(path)
            if stored is None:
                continue
            if job_identity is not None and stored.job.job_identity != job_identity:
                continue
            items.append(stored)
    items.sort(key=lambda item: (item.updated_at, item.stored_path), reverse=True)
    return tuple(items)


# --- the tailoring ------------------------------------------------------------------------


def _stored_matrix(home_root: Path, target: Path, job_identity: str) -> tuple[tuple[MatrixRow, ...], str | None]:
    """The stored quick assessment's matrix for this job (context only), tolerantly."""

    try:
        stored = find_quick_assessment_by_job_identity(home_root, target, job_identity)
    except Exception:
        return (), None
    if stored is None:
        return (), None
    rows = tuple(MatrixRow(row.requirement, row.status.value) for row in stored.result.matrix)
    return rows, stored.stored_path


def run_tailored_resume(
    request: TailorRequest,
    *,
    home_root: Path,
    target: Path,
    config: GigAIConfig | None = None,
) -> TailorResponse:
    """Tailor ``request.resume`` to ``request.job`` and store the JSON + markdown.

    Raises ``TailorError`` (a ``QuickAssessError``) with one of the quick-
    assess codes -- ``job_input_invalid``, ``resume_input_invalid``,
    ``invalid_value``, ``job_text_unavailable``, ``job_fetch_failed``,
    ``profile_not_found``, ``profile_unavailable``, ``resume_unavailable``,
    ``resume_digest_mismatch``, ``model_target_unavailable``,
    ``model_unavailable``, ``model_denied``, ``model_output_invalid``,
    ``target_unavailable`` -- or ``tailor_timeout``.
    """

    home_root = Path(home_root)
    target = Path(target)

    # 1. Job text (public data; network only for a URL).
    try:
        with job_fetch_client() as client:
            job = resolve_job(request.job, client=client, home_root=home_root)
    except FindJobsContractError as exc:
        raise TailorError(exc.code, str(exc)) from exc
    from .find_jobs.assess_contracts import AssessRequest

    job = _apply_job_overrides(job, AssessRequest(job=request.job, resume=request.resume))

    # 2. Resume identity + text (the pinned profile resume, or ephemeral).
    resolved = None
    try:
        if request.resume.is_ephemeral:
            resume = resolve_resume(request.resume, resolved=None, home_root=home_root, target=target)  # type: ignore[arg-type]
        else:
            resolved = _resolve_workpad(home_root, target)
            profile = resolve_profile(request.resume, resolved=resolved, home_root=home_root, target=target)
            assert profile is not None
            resume = resume_for_profile(profile, resolved=resolved, home_root=home_root, target=target)
    except FindJobsContractError as exc:
        raise TailorError(exc.code, str(exc)) from exc
    except QuickAssessError as exc:
        raise TailorError(exc.code, str(exc)) from exc
    lines = resume_lines(resume.text)
    if not lines:
        raise TailorError("resume_input_invalid", "the resume has no text lines to tailor")

    # 3. Answered questions (citable sources), tolerantly: a pasted-text run
    #    with no bound gig simply has none.
    #    0.1.10.7 C: the user's answers, the same for every profile and for
    #    a pasted resume.
    from . import story_bank

    stored_answers = story_bank.answers_for_reuse(home_root=home_root, target=target)
    answers: dict[str, AnswerSource] = {
        key: AnswerSource(question_id=item.question_id, answer=item.answer, revision_id=item.revision_id, prompt=item.prompt)
        for key, item in stored_answers.items()
    }

    # 4. Storage path first (so the response can name it and ``created_at``
    #    survives a re-run), then the stored matrix (context only).
    try:
        path = tailored_resume_path(home_root, target, resume.profile_id, job.job_identity)
    except Exception as exc:
        raise TailorError("target_unavailable", "this folder is not bound to a GigAI project") from exc
    previous = _read_stored(path)
    matrix, assessment_path = _stored_matrix(home_root, target, job.job_identity)

    # 5. Model target -> adapter (C1/C11), then the shared loop.
    model_target = request.model_target or _default_model_target(target)
    active = config if config is not None else load_config(home_root)
    meter = CallMeter(
        KIND_TAILOR, model_target.value, home_root, target, profile_id=resume.profile_id, job=job.job_identity
    )
    binding = meter.bind(_resolve_binding(active, model_target, home_root=home_root))
    tailor_job = TailorJob(title=job.title, company=job.company, location=job.location, posting_text=job.text)
    ctx = tailor_context(resume.text, answers=answers, matrix=matrix)
    try:
        attempt = tailor_once(binding, tailor_job, ctx)
    finally:
        binding.close()

    if not attempt.ok:
        reason = attempt.not_assessed_reason
        if reason is NotAssessedReason.MODEL_OUTPUT_INVALID:
            detail = attempt.validation_error or "the model's answer did not match the tailored-resume schema"
            raise TailorError("model_output_invalid", f"the model's answer was invalid after one retry: {detail}")
        if reason is NotAssessedReason.MODEL_DENIED:
            raise TailorError("model_denied", "the configured policy refused this model call")
        if binding.port.timed_out:
            raise TailorError("tailor_timeout", "the model call timed out; try again or pick a faster model target")
        raise TailorError("model_unavailable", "the configured model is unavailable right now")

    result = attempt.parsed
    assert isinstance(result, TailoredResume)
    from .proposal_execution import _usage_block

    usage = _usage_block([attempt.usage] if attempt.usage is not None else [], UsageBlock)
    producer = Producer(
        _PRODUCER_CALLABLE, _PRODUCER_VERSION, _PRODUCER_ACTOR, model_target, binding.port.name or model_target.value
    )
    now = _now()
    markdown_path = path.with_suffix(".md")
    response = TailorResponse(
        job=job,
        resume=resume,
        sources=TailorSources(
            resume_content_sha256=resume.content_sha256,
            resume_line_count=len(lines),
            answers={key: item.revision_id for key, item in answers.items()},
            assessment_stored_path=assessment_path,
        ),
        result=result,
        markdown=render_markdown(result),
        producer=producer,
        usage=usage,
        instructions_digest=TAILOR_INSTRUCTIONS_DIGEST,
        created_at=previous.created_at if previous is not None else now,
        updated_at=now,
        stored_path=os.fspath(path),
        markdown_path=os.fspath(markdown_path),
    )
    save_tailor_response(response)
    return response


def save_tailor_response(response: TailorResponse) -> None:
    """Write a response's JSON and its sibling ``.md`` (atomically) at the paths it names."""

    atomic_write(Path(response.stored_path), json.dumps(response.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    atomic_write(Path(response.markdown_path), response.markdown.encode("utf-8"))


__all__ = [
    "ENTRY_SECTIONS",
    "EPHEMERAL_RESUME_KEY",
    "FALLBACK_FLAGS",
    "LENGTH_RULE",
    "LINE_CHOICES",
    "LINE_ORIGINS",
    "LINE_USES",
    "MAX_CUSTOM_TEXT_CHARS",
    "LOST_RULES",
    "MAX_POSTING_PHRASE_CHARS",
    "REASON_KINDS",
    "MAX_REFS_PER_LINE",
    "MAX_SECTIONS",
    "MAX_TEXT_CHARS",
    "MAX_TOTAL_LINES",
    "SECTION_HEADINGS",
    "TAILOR_INSTRUCTIONS_DIGEST",
    "TAILOR_PROMPT_HEADER",
    "TERM_ALIASES",
    "TERM_STOP_WORDS",
    "AnswerSource",
    "LengthRule",
    "LineAlternative",
    "LineReason",
    "MatrixRow",
    "NumberMention",
    "SourceRef",
    "TailorContext",
    "TailorError",
    "TailorJob",
    "TailorLineStats",
    "TailorRequest",
    "TailorResponse",
    "TailorSources",
    "TailorValidationError",
    "TailoredEntry",
    "TailoredLine",
    "TailoredResume",
    "TailoredResumesListResponse",
    "TailoredSection",
    "apply_line_choice",
    "apply_line_edit",
    "custom_line_text",
    "personal_info_found",
    "replaced_line",
    "apply_no_loss",
    "canonical_term",
    "check_rewritten_line",
    "check_single_entry",
    "guard_terms",
    "list_tailored_resumes",
    "load_tailor_instructions",
    "matrix_terms",
    "numeric_values",
    "posting_terms",
    "render_markdown",
    "render_tailor_prompt",
    "resume_continuations",
    "resume_entries",
    "resume_lines",
    "run_tailored_resume",
    "save_tailor_response",
    "shown_text",
    "tailor_context",
    "tailor_line_stats",
    "tailor_once",
    "tailored_resume_dir",
    "tailored_resume_path",
    "text_terms",
    "unsupported_numbers",
    "unsupported_posting_terms",
    "validate_tailored_output",
]
