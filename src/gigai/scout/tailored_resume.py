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

- STRUCTURE: ``header`` + ``sections[]``; experience/projects/education
  sections hold ``entries[]`` of ``{heading_ref, bullets[]}``.
- COPY-ONLY lines ``{"copy": <resume line n>}``: header lines and entry
  headings (employer, title, dates, location, degree) are inserted by code
  verbatim from the resume; the model only picks the line.  Only bullets,
  the summary (and, optionally, skills lines) are rewritten.
- PROVENANCE: every rewritten line cites 1-4 sources; a resume ref must be
  in ``1..len(resume_lines)``; an answer ref's id, after
  ``normalize_question_id``, must be a ``read_answers`` key.
- NUMERIC guard: every number in a rewritten line (digits or number words,
  ranges, ``5+``, ``$1.2M`` vs ``1.2 million``) appears in a cited source.
- POSTING-TERM guard: a rewritten line may not carry a skill/tool/technology
  term that appears in the posting but in none of its cited sources (terms
  = the stored matrix's requirement names when present, else the posting's
  capitalized/technical tokens; case-insensitive; a small alias table).

The whole answer is rejected on the first violation, with a message naming
the line and the reason (fed back on the single retry, then
``model_output_invalid``).  Storage: ``<home>/scout/<project_id>/resumes/
<profile_id|ephemeral>/<sha256(job_identity)>.json`` + a sibling ``.md``.
The stored JSON carries resume-derived text by design (README privacy
line); the posting text is never echoed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from .experience_answers import read_answers
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
MAX_HEADER_LINES = 8
MAX_ENTRIES_PER_SECTION = 20
MAX_TOTAL_LINES = 120
MAX_TEXT_CHARS = 400
MAX_REFS_PER_LINE = 4
MAX_HEADING_LINES = 4

SECTION_HEADINGS: tuple[str, ...] = ("summary", "experience", "skills", "education", "projects", "other")
ENTRY_SECTIONS: frozenset[str] = frozenset({"experience", "projects", "education"})

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


@dataclass(frozen=True)
class SourceRef:
    """A validated citation: the source kind, its key, and the source text as the validator saw it."""

    kind: str  # "resume" | "answer"
    line: int | None
    question_id: str | None
    text: str

    def label(self) -> str:
        return f"R{self.line}" if self.kind == "resume" else f"A {self.question_id}"

    def to_json(self) -> dict[str, object]:
        if self.kind == "resume":
            return {"kind": "resume", "line": self.line, "text": self.text}
        return {"kind": "answer", "question_id": self.question_id, "text": self.text}

    @classmethod
    def from_json(cls, obj: object) -> "SourceRef":
        if type(obj) is not dict:
            _fail("wrong_type", "ref must be an object")
        kind = obj.get("kind")
        if kind == "resume":
            value = _object_with_optional(obj, ("kind", "line", "text"), (), "ref")
            line = value["line"]
            if type(line) is not int or line < 1:
                _fail("invalid_value", "ref.line must be a positive integer")
            return cls("resume", line, None, _string(value["text"], "ref.text", nonempty=False))
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

    numbered = "\n".join(f"R{index}: {line}" for index, line in enumerate(ctx.resume_lines, 1))
    answers = "\n".join(
        f"A {item.question_id}: {item.answer[:_MAX_PROMPT_ANSWER_TEXT]}" for item in ctx.answers.values()
    )
    matrix = "\n".join(f"M{index}: {row.requirement} [{row.status}]" for index, row in enumerate(ctx.matrix, 1))
    values = {
        "title": job.title or "unspecified",
        "company": job.company or "unspecified",
        "posting_text": job.posting_text[:_MAX_PROMPT_POSTING_TEXT],
        "resume_lines": numbered[:_MAX_PROMPT_RESUME_TEXT],
        "answers": answers,
        "matrix": matrix,
        "validation_error": (validation_error or "")[:_MAX_PROMPT_VALIDATION_ERROR],
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
class TailoredLine(_Contract):
    """One output line: ``copy`` (a resume line verbatim) or ``rewritten`` (the model's wording)."""

    schema_version: ClassVar[str] = "scout-tailored-line:1"
    kind: str  # "copy" | "rewritten"
    text: str
    refs: tuple[SourceRef, ...]

    def to_json(self) -> dict[str, object]:
        return {"kind": self.kind, "text": self.text, "refs": [ref.to_json() for ref in self.refs]}

    @classmethod
    def from_json(cls, obj: object) -> "TailoredLine":
        value = _object_with_optional(obj, ("kind", "text", "refs"), (), "tailored_line")
        kind = _string(value["kind"], "tailored_line.kind")
        if kind not in {"copy", "rewritten"}:
            _fail("bad_enum", "tailored_line.kind must be copy or rewritten")
        if type(value["refs"]) is not list:
            _fail("wrong_type", "tailored_line.refs must be an array")
        return cls(kind, _string(value["text"], "tailored_line.text", nonempty=False), tuple(SourceRef.from_json(item) for item in value["refs"]))


@dataclass(frozen=True)
class TailoredEntry(_Contract):
    """One role/project/degree: copy-only heading lines, then rewritten bullets."""

    schema_version: ClassVar[str] = "scout-tailored-entry:1"
    heading: tuple[TailoredLine, ...]
    bullets: tuple[TailoredLine, ...]

    def to_json(self) -> dict[str, object]:
        return {"heading": [line.to_json() for line in self.heading], "bullets": [line.to_json() for line in self.bullets]}

    @classmethod
    def from_json(cls, obj: object) -> "TailoredEntry":
        value = _object_with_optional(obj, ("heading", "bullets"), (), "tailored_entry")
        for key in ("heading", "bullets"):
            if type(value[key]) is not list:
                _fail("wrong_type", f"tailored_entry.{key} must be an array")
        return cls(
            tuple(TailoredLine.from_json(item) for item in value["heading"]),
            tuple(TailoredLine.from_json(item) for item in value["bullets"]),
        )


@dataclass(frozen=True)
class TailoredSection(_Contract):
    schema_version: ClassVar[str] = "scout-tailored-section:1"
    heading: str
    lines: tuple[TailoredLine, ...] = ()
    entries: tuple[TailoredEntry, ...] = ()

    def to_json(self) -> dict[str, object]:
        if self.heading in ENTRY_SECTIONS:
            return {"heading": self.heading, "entries": [entry.to_json() for entry in self.entries]}
        return {"heading": self.heading, "lines": [line.to_json() for line in self.lines]}

    @classmethod
    def from_json(cls, obj: object) -> "TailoredSection":
        value = _object_with_optional(obj, ("heading",), ("lines", "entries"), "tailored_section")
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
        )

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


def _copy_line(raw: object, where: str, ctx: TailorContext) -> TailoredLine:
    if type(raw) is not dict or set(raw) != {"copy"}:
        _reject(f'{where} must be a copy line ({{"copy": <resume line number>}})')
    number = raw["copy"]
    if isinstance(number, str) and number.isdigit():
        number = int(number)
    if type(number) is not int or isinstance(number, bool):
        _reject(f"{where} copy must be a resume line number")
    if not 1 <= number <= len(ctx.resume_lines):
        _reject(f"{where} copies resume line {number}; the resume has {len(ctx.resume_lines)} lines")
    text = ctx.resume_lines[number - 1]
    return TailoredLine("copy", text, (SourceRef("resume", number, None, text),))


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
            refs.append(SourceRef("resume", number, None, ctx.resume_lines[number - 1]))
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


def _rewritten_line(raw: object, where: str, ctx: TailorContext, terms: Iterable[str]) -> TailoredLine:
    if type(raw) is not dict:
        _reject(f"{where} is not an object")
    if set(raw) == {"copy"}:
        return _copy_line(raw, where, ctx)
    unknown = set(raw) - {"text", "refs"}
    if unknown:
        _reject(f"{where} has unknown key(s) {sorted(unknown)}; a rewritten line is {{\"text\", \"refs\"}}")
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        _reject(f"{where} has no text")
    if len(text) > MAX_TEXT_CHARS:
        _reject(f"{where} text has {len(text)} characters; at most {MAX_TEXT_CHARS} allowed")
    if "\x00" in text:
        _reject(f"{where} text contains a NUL character")
    refs = _refs(raw.get("refs"), where, ctx)
    text = text.strip()
    check_rewritten_line(where, text, refs, terms)
    return TailoredLine("rewritten", text, refs)


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
    """

    if not isinstance(decoded, Mapping):
        _reject("the answer is not a JSON object")
    unknown = set(decoded) - {"header", "sections"}
    if unknown:
        _reject(f"the answer has unknown top-level key(s) {sorted(unknown)}; only header and sections are allowed")
    terms = guard_terms(job, ctx)

    raw_header = decoded.get("header", [])
    if raw_header is None:
        raw_header = []
    if type(raw_header) is not list:
        _reject("header must be a list of copy lines")
    if len(raw_header) > MAX_HEADER_LINES:
        _reject(f"header has {len(raw_header)} copy lines; at most {MAX_HEADER_LINES} allowed")
    header = tuple(_copy_line(item, f"header[{index}]", ctx) for index, item in enumerate(raw_header, 1))

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
                bullets = tuple(
                    _rewritten_line(item, f"{where} bullet {offset}", ctx, terms) for offset, item in enumerate(raw_bullets, 1)
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
            lines = tuple(
                _rewritten_line(item, f"{heading} line {offset}", ctx, terms) for offset, item in enumerate(raw_lines, 1)
            )
            total += len(lines)
            if total > MAX_TOTAL_LINES:
                _reject(f"the sections hold more than {MAX_TOTAL_LINES} lines in total; at most {MAX_TOTAL_LINES} allowed")
            sections.append(TailoredSection(heading, lines, ()))
    return TailoredResume(header, tuple(sections))


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


def _refs_comment(line: TailoredLine) -> str:
    return "<!-- " + ", ".join(ref.label() for ref in line.refs) + " -->"


def render_markdown(result: TailoredResume) -> str:
    """The ``.md`` text, from the validated JSON only; each line keeps its refs
    in a trailing HTML comment (``<!-- R12, A cloud:gcp -->``)."""

    out: list[str] = []
    for index, line in enumerate(result.header):
        shown = _display(line.text)
        out.append((f"# {shown}" if index == 0 else shown) + " " + _refs_comment(line))
    if result.header:
        out.append("")
    for section in result.sections:
        out.append(f"## {section.heading.capitalize()}")
        out.append("")
        if section.heading in ENTRY_SECTIONS:
            for entry in section.entries:
                for index, line in enumerate(entry.heading):
                    shown = _display(line.text)
                    out.append((f"### {shown}" if index == 0 else shown) + " " + _refs_comment(line))
                if entry.bullets:
                    out.append("")
                for line in entry.bullets:
                    out.append(f"- {line.text} {_refs_comment(line)}")
                out.append("")
        else:
            for line in section.lines:
                text = _display(line.text) if line.kind == "copy" else line.text
                out.append(f"- {text} {_refs_comment(line)}")
            out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


# --- the model call -----------------------------------------------------------------------


def tailor_once(binding: object, job: TailorJob, ctx: TailorContext) -> AssessAttempt:
    """One tailoring through the shared loop: render, invoke, extract, validate, retry once.

    ``attempt.parsed`` is a ``TailoredResume`` when ``ok``.  Exactly the
    exception mapping and retry rule ``assess_once`` has (``invoke_json_once``).
    """

    return invoke_json_once(
        binding,
        lambda validation_error: render_tailor_prompt(job, ctx, validation_error),
        lambda decoded: validate_tailored_output(decoded, job, ctx),
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

    Never carries the posting text (``job`` is serialized WITHOUT ``text``).
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
            job = resolve_job(request.job, client=client)
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
    answers: dict[str, AnswerSource] = {}
    try:
        gig_resolved = resolved if resolved is not None else _resolve_workpad(home_root, target)
        stored_answers = read_answers(home_root=home_root, requested_target=target, gig_id=gig_resolved.gig_id)
        answers = {
            key: AnswerSource(question_id=item.question_id, answer=item.answer, revision_id=item.revision_id, prompt=item.prompt)
            for key, item in stored_answers.items()
        }
    except QuickAssessError:
        pass

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
    binding = _resolve_binding(active, model_target, home_root=home_root)
    tailor_job = TailorJob(title=job.title, company=job.company, location=job.location, posting_text=job.text)
    ctx = TailorContext(resume_lines=lines, answers=answers, matrix=matrix)
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
    atomic_write(path, json.dumps(response.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    atomic_write(markdown_path, response.markdown.encode("utf-8"))
    return response


__all__ = [
    "ENTRY_SECTIONS",
    "EPHEMERAL_RESUME_KEY",
    "MAX_HEADER_LINES",
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
    "MatrixRow",
    "NumberMention",
    "SourceRef",
    "TailorContext",
    "TailorError",
    "TailorJob",
    "TailorRequest",
    "TailorResponse",
    "TailorSources",
    "TailorValidationError",
    "TailoredEntry",
    "TailoredLine",
    "TailoredResume",
    "TailoredResumesListResponse",
    "TailoredSection",
    "canonical_term",
    "check_rewritten_line",
    "guard_terms",
    "list_tailored_resumes",
    "load_tailor_instructions",
    "matrix_terms",
    "numeric_values",
    "posting_terms",
    "render_markdown",
    "render_tailor_prompt",
    "resume_lines",
    "run_tailored_resume",
    "tailor_once",
    "tailored_resume_dir",
    "tailored_resume_path",
    "text_terms",
    "unsupported_numbers",
    "unsupported_posting_terms",
    "validate_tailored_output",
]
