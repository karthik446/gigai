"""Request/response DTOs for the standalone ("quick") assessment flow (P4).

Lives next to ``contracts.py`` on purpose: the v0.1.9 plan keeps P4/P5's new
DTOs out of the 2,000-line wave-1a contracts module so three parallel packets
never edit the same file.  The helpers (``_Contract``, ``_fail``, ``_object``,
...) are imported from ``contracts.py`` rather than copied, so every DTO here
fails closed the same way (``FindJobsContractError`` with a stable ``code``).

Nothing in this module fetches, reads a workpad, or invokes a model; the
resolvers live in ``job_input.py`` and ``resume_input.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from .contracts import (
    ELIGIBILITY_ROW_IDS,
    AssessmentQuestion,
    ModelTarget,
    PinnedResume,
    Producer,
    ProfileRef,
    RequirementMatrixRow,
    SponsorshipStatus,
    StoryBankStamp,
    UsageBlock,
    Verdict,
    _Contract,
    _bool,
    _country_codes,
    _digest_value,
    _enum,
    _fail,
    _integer,
    _json_enum,
    _json_strings,
    _object,
    _object_with_optional,
    _optional_string,
    _string,
    _strings,
    is_row_id,
    rows_not_shown_count,
)
from .rank_contracts import RankScore

#: ``ResolvedJob.fetch_kind`` values, in the order ``resolve_job`` tries them.
FETCH_KINDS: tuple[str, ...] = ("pasted", "ats_single", "ats_board", "generic")

_TEXT_IDENTITY_PREFIX = "text:"

#: ``AssessResponse.rank_skip_reason`` values (uat-bug-015): why a quick
#: assessment carries no rank score.  Stored-contract enum, kept so a file
#: written by a Jev-era build still parses; a new assessment never sets one
#: (SCOPE-ADD-3 C2 removed the quick-assess Jev score).
RANK_SKIP_REASONS: tuple[str, ...] = ("no_key", "ephemeral_resume", "no_title_or_company", "cost_cap", "error")

#: ``AssessRequest.origin`` / ``AssessResponse.origin`` values: where the
#: operator started the assessment.  ``quick_assess``: on demand ("+ Assess a
#: job", ``gigai scout assess``, a bare ``POST /api/assess``); ``job_page``:
#: from the job page of a posting a find-jobs run found.  The UI lists only
#: the first kind under Assessments.
ORIGIN_QUICK_ASSESS = "quick_assess"
ORIGIN_JOB_PAGE = "job_page"
ASSESS_ORIGINS: tuple[str, ...] = (ORIGIN_QUICK_ASSESS, ORIGIN_JOB_PAGE)


def _optional_strings(value: object, name: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _strings(value, name, allow_empty=True)


def _optional_bool(value: object, name: str) -> bool | None:
    if value is None:
        return None
    return _bool(value, name)


@dataclass(frozen=True)
class AssessJobInput(_Contract):
    """The job to assess: exactly one of a public URL or pasted text.

    ``title``/``company`` (P5, additive) override what the source carried --
    the plan's ``--title/--company`` flags for pasted text, which otherwise
    has neither.  Both are omitted from JSON when ``None`` so a P4-era
    payload round-trips byte-identically.
    """

    schema_version: ClassVar[str] = "scout-assess-job-input:1"
    job_url: str | None = None
    job_text: str | None = None
    title: str | None = None
    company: str | None = None

    def __post_init__(self) -> None:
        has_url = bool(self.job_url)
        has_text = bool(self.job_text)
        if has_url == has_text:
            _fail("job_input_invalid", "pass exactly one of job_url or job_text")

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"job_url": self.job_url, "job_text": self.job_text}
        if self.title is not None:
            value["title"] = self.title
        if self.company is not None:
            value["company"] = self.company
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessJobInput":
        value = _object_with_optional(obj, (), ("job_url", "job_text", "title", "company"), "assess_job_input")
        return cls(
            job_url=_optional_string(value.get("job_url"), "job_url"),
            job_text=_optional_string(value.get("job_text"), "job_text"),
            title=_optional_string(value.get("title"), "title"),
            company=_optional_string(value.get("company"), "company"),
        )


@dataclass(frozen=True)
class AssessResumeInput(_Contract):
    """Which resume to assess against.

    At most one of ``profile_id`` (a committed scout profile's pinned resume)
    or ``resume_text`` (ephemeral: used for this call only, never imported or
    stored).  Neither means "the gig's selected profile".
    """

    schema_version: ClassVar[str] = "scout-assess-resume-input:1"
    profile_id: str | None = None
    resume_text: str | None = None

    def __post_init__(self) -> None:
        if self.profile_id and self.resume_text:
            _fail("resume_input_invalid", "pass at most one of profile_id or resume_text")

    @property
    def is_ephemeral(self) -> bool:
        return bool(self.resume_text)

    def to_json(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "resume_text": self.resume_text}

    @classmethod
    def from_json(cls, obj: object) -> "AssessResumeInput":
        value = _object_with_optional(obj, (), ("profile_id", "resume_text"), "assess_resume_input")
        return cls(
            profile_id=_optional_string(value.get("profile_id"), "profile_id"),
            resume_text=_optional_string(value.get("resume_text"), "resume_text"),
        )


@dataclass(frozen=True)
class AssessPreferences(_Contract):
    """Per-call preference overrides; ``None`` means "use the default".

    Defaults come from ``find-jobs.json`` (visa, countries) and the resolved
    profile (titles); see ``resume_input.resolve_preferences``.
    """

    schema_version: ClassVar[str] = "scout-assess-preferences:1"
    visa_sponsorship_required: bool | None = None
    titles: tuple[str, ...] | None = None
    countries: tuple[str, ...] | None = None
    # assess-prompt-v2 (v0.1.9): the candidate's own location as they wrote
    # it ("Toronto, ON, Canada"), a per-request override of find-jobs.json's
    # ``location``; ``None`` means "use the config's". Additive: omitted from
    # ``to_json`` when ``None`` so every stored/served preferences object
    # stays byte-identical to before.
    location: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "visa_sponsorship_required": self.visa_sponsorship_required,
            "titles": None if self.titles is None else list(self.titles),
            "countries": None if self.countries is None else list(self.countries),
        }
        if self.location is not None:
            value["location"] = self.location
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessPreferences":
        value = _object_with_optional(
            obj, (), ("visa_sponsorship_required", "titles", "countries", "location"), "assess_preferences"
        )
        countries = value.get("countries")
        location = value.get("location")
        return cls(
            visa_sponsorship_required=_optional_bool(value.get("visa_sponsorship_required"), "visa_sponsorship_required"),
            titles=_optional_strings(value.get("titles"), "titles"),
            countries=None if countries is None else _country_codes(countries, "countries"),
            location=None if location is None else _string(location, "location", nonempty=False),
        )


@dataclass(frozen=True)
class ResolvedJob(_Contract):
    """One job's text plus where it came from; public data only.

    ``job_identity`` is the normalized URL for a fetched job, or
    ``"text:sha256:<hex>"`` for pasted text.  ``fetch_kind`` is one of
    :data:`FETCH_KINDS`.  ``title``/``company``/``location`` are ``""`` when
    the source did not carry them (pasted text; a generic page's company).
    """

    schema_version: ClassVar[str] = "scout-resolved-job:1"
    job_identity: str
    source_url: str | None
    normalized_url: str | None
    fetch_kind: str
    title: str
    company: str
    location: str
    text: str
    text_sha256: str

    def __post_init__(self) -> None:
        if self.fetch_kind not in FETCH_KINDS:
            _fail("bad_enum", "resolved_job.fetch_kind has an unsupported value")
        if self.fetch_kind == "pasted":
            if self.source_url is not None or self.normalized_url is not None:
                _fail("invalid_value", "a pasted job carries no URL")
            if not self.job_identity.startswith(_TEXT_IDENTITY_PREFIX):
                _fail("invalid_value", "a pasted job's identity must be text:<digest>")
        elif self.normalized_url is None or self.job_identity != self.normalized_url:
            _fail("invalid_value", "a fetched job's identity must be its normalized URL")

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "job_identity": self.job_identity,
            "source_url": self.source_url,
            "normalized_url": self.normalized_url,
            "fetch_kind": self.fetch_kind,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "text": self.text,
            "text_sha256": self.text_sha256,
        }

    @classmethod
    def from_json(cls, obj: object) -> "ResolvedJob":
        value = _object_with_optional(
            obj,
            ("schema_version", "job_identity", "source_url", "normalized_url", "fetch_kind", "title", "company", "location", "text", "text_sha256"),
            (),
            "resolved_job",
        )
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "resolved_job.schema_version is unsupported")
        return cls(
            job_identity=_string(value["job_identity"], "job_identity"),
            source_url=_optional_string(value["source_url"], "source_url"),
            normalized_url=_optional_string(value["normalized_url"], "normalized_url"),
            fetch_kind=_string(value["fetch_kind"], "fetch_kind"),
            title=_string(value["title"], "title", nonempty=False),
            company=_string(value["company"], "company", nonempty=False),
            location=_string(value["location"], "location", nonempty=False),
            text=_string(value["text"], "text", nonempty=False),
            text_sha256=_digest_value(value["text_sha256"], "text_sha256"),
        )


@dataclass(frozen=True)
class ResolvedResume(_Contract):
    """The resume identity a quick assessment ran against.

    ``profile_id``/``pinned`` are ``None`` for an ephemeral resume.  The
    resume ``text`` is kept in memory only: it is excluded from ``to_json``,
    from equality and from ``repr``, so serializing or logging this DTO never
    leaks resume content (``from_json`` yields ``text=""``).
    """

    schema_version: ClassVar[str] = "scout-resolved-resume:1"
    profile_id: str | None
    pinned: PinnedResume | None
    content_sha256: str
    text: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.profile_id is None) != (self.pinned is None):
            _fail("invalid_value", "profile_id and pinned must both be set or both be None")
        if self.pinned is not None and self.pinned.content_sha256 != self.content_sha256:
            _fail("invalid_value", "content_sha256 must match the pinned resume digest")

    @property
    def is_ephemeral(self) -> bool:
        return self.pinned is None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "pinned": None if self.pinned is None else self.pinned.to_json(),
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_json(cls, obj: object) -> "ResolvedResume":
        value = _object_with_optional(
            obj, ("schema_version", "profile_id", "pinned", "content_sha256"), (), "resolved_resume"
        )
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "resolved_resume.schema_version is unsupported")
        pinned = value["pinned"]
        return cls(
            profile_id=_optional_string(value["profile_id"], "profile_id"),
            pinned=None if pinned is None else PinnedResume.from_json(pinned),
            content_sha256=_digest_value(value["content_sha256"], "content_sha256"),
        )


def text_identity(text_sha256: str) -> str:
    """The ``job_identity`` for pasted text: ``"text:" + <sha256:hex digest>``."""

    return _TEXT_IDENTITY_PREFIX + _digest_value(text_sha256, "text_sha256")


# --- P5: the assess API/CLI request and response DTOs --------------------------------


# --- 0.1.11 N3 (assessment v9, SPEC 1.2-1.5): the suggestion, the pick, the list reference, the gate -----

#: ``AssessmentSuggestion.kind``: what would make this resume fit the job better. Never a rewritten line.
SUGGESTION_KINDS: tuple[str, ...] = ("reword", "keyword", "order", "gap", "master_line")
#: The kinds kept on a ``pending_user_answers`` assessment: the other three are about a resume that does not exist yet.
SUGGESTION_KINDS_WITHOUT_RESUME: tuple[str, ...] = ("gap", "master_line")
MAX_STRUCTURED_SUGGESTIONS = 8
MAX_SUGGESTION_WHY_CHARS = 300
MAX_SUGGESTION_PHRASE_CHARS = 60
#: A master line id as a suggestion or a pick names it (``master_resume``'s own id shape).
MAX_MASTER_ID_CHARS = 64
#: The two sections the model orders.
PICK_SECTIONS: tuple[str, ...] = ("experience", "projects")
MAX_PICK_LINES = 60

#: ``RequirementsRef.list``: the rows are the posting's STORED requirement list, or this assessment's own
#: (it lost the race of two first assessments, ``requirements_list``).
REQUIREMENTS_LIST_STORED = "stored"
REQUIREMENTS_LIST_OWN = "own"
REQUIREMENTS_LISTS: tuple[str, ...] = (REQUIREMENTS_LIST_STORED, REQUIREMENTS_LIST_OWN)

#: ``GateRecord.decision`` (``resume_gate``).
GATE_SUGGEST = "suggest"
GATE_HOLD_QUESTION = "hold_question"
GATE_HOLD_UNMET = "hold_unmet"
GATE_NOT_A_MATCH = "not_a_match"
GATE_DECISIONS: tuple[str, ...] = (GATE_SUGGEST, GATE_HOLD_QUESTION, GATE_HOLD_UNMET, GATE_NOT_A_MATCH)
#: ``GateReason.code``: why the gate holds.
GATE_REASON_HARD_UNMET = "hard_unmet"
GATE_REASON_QUESTION_OPEN = "question_open"
GATE_REASON_ASKABLE_UNMET = "askable_unmet"
GATE_REASONS: tuple[str, ...] = (GATE_REASON_HARD_UNMET, GATE_REASON_QUESTION_OPEN, GATE_REASON_ASKABLE_UNMET)


def _master_id(value: object, name: str) -> str:
    text = _string(value, name)
    if len(text) > MAX_MASTER_ID_CHARS or any(char.isspace() for char in text):
        _fail("invalid_value", f"{name} must be a master line id")
    return text


@dataclass(frozen=True)
class AssessmentSuggestion(_Contract):
    """One structured suggestion of a v9 assessment: what would help, and why. Never a rewritten line.

    ``line`` (a master line id) and ``requirement`` (a row id) are both
    optional; at least one is given. ``posting_phrase`` is at most
    :data:`MAX_SUGGESTION_PHRASE_CHARS` characters of the posting. Each
    optional key is omitted from JSON when ``None``.
    """

    kind: str
    why: str
    line: str | None = None
    requirement: str | None = None
    posting_phrase: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in SUGGESTION_KINDS:
            _fail("bad_enum", "assessment_suggestion.kind has an unsupported value")
        if not self.why.strip() or len(self.why) > MAX_SUGGESTION_WHY_CHARS:
            _fail("invalid_value", f"assessment_suggestion.why must be 1 to {MAX_SUGGESTION_WHY_CHARS} characters")
        if self.line is None and self.requirement is None:
            _fail("invalid_value", "assessment_suggestion names a line or a requirement")
        if self.requirement is not None and not is_row_id(self.requirement):
            _fail("invalid_value", "assessment_suggestion.requirement must be a requirement row id")
        if self.posting_phrase is not None and len(self.posting_phrase) > MAX_SUGGESTION_PHRASE_CHARS:
            _fail("invalid_value", f"assessment_suggestion.posting_phrase must be at most {MAX_SUGGESTION_PHRASE_CHARS} characters")

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"kind": self.kind, "why": self.why}
        if self.line is not None:
            value["line"] = self.line
        if self.requirement is not None:
            value["requirement"] = self.requirement
        if self.posting_phrase is not None:
            value["posting_phrase"] = self.posting_phrase
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessmentSuggestion":
        value = _object_with_optional(obj, ("kind", "why"), ("line", "requirement", "posting_phrase"), "assessment_suggestion")
        return cls(
            kind=_string(value["kind"], "assessment_suggestion.kind"),
            why=_string(value["why"], "assessment_suggestion.why"),
            line=None if value.get("line") is None else _master_id(value["line"], "assessment_suggestion.line"),
            requirement=_optional_string(value.get("requirement"), "assessment_suggestion.requirement"),
            posting_phrase=_optional_string(value.get("posting_phrase"), "assessment_suggestion.posting_phrase"),
        )


@dataclass(frozen=True)
class AssessmentPick(_Contract):
    """The model's pick exactly as it returned it, ids only, kept for provenance (SPEC 1.2).

    Parsed leniently at the model boundary and validated later (``pick.settle``):
    an id here may name no line of the master. The SELECTION code makes of it
    is not here; it is on the job resume and in the suggestion record.
    """

    summary: str | None
    section_order: tuple[str, ...]
    lines: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.lines) > MAX_PICK_LINES:
            _fail("invalid_value", f"assessment_pick.lines has {len(self.lines)} ids; at most {MAX_PICK_LINES} allowed")
        if any(section not in PICK_SECTIONS for section in self.section_order) or len(set(self.section_order)) != len(self.section_order):
            _fail("invalid_value", "assessment_pick.section_order holds experience and projects, each at most once")

    def to_json(self) -> dict[str, object]:
        return {"summary": self.summary, "section_order": list(self.section_order), "lines": list(self.lines)}

    @classmethod
    def from_json(cls, obj: object) -> "AssessmentPick":
        value = _object(obj, ("summary", "section_order", "lines"), "assessment_pick")
        section_order = _strings(value["section_order"], "assessment_pick.section_order", allow_empty=True)
        if type(value["lines"]) is not list:
            _fail("wrong_type", "assessment_pick.lines must be an array")
        return cls(
            summary=None if value["summary"] is None else _master_id(value["summary"], "assessment_pick.summary"),
            section_order=section_order,
            lines=tuple(_master_id(item, f"assessment_pick.lines[{index}]") for index, item in enumerate(value["lines"])),
        )


@dataclass(frozen=True)
class RequirementsRef(_Contract):
    """Which requirement list one assessment's rows are (SPEC 1.4): digests and a count, never a requirement.

    JSON: ``{posting_sha256, rules_version, digest, rows, list}``. Two
    assessments are compared row by row only when their ``digest`` values
    (``rows_digest`` here) are equal. ``rows`` counts the list's rows (the
    ``elig-`` rows of the assessment are not in a list). ``kind`` (JSON
    ``list``) is one of :data:`REQUIREMENTS_LISTS`.
    """

    posting_sha256: str
    rules_version: str
    rows_digest: str
    rows: int
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in REQUIREMENTS_LISTS:
            _fail("bad_enum", "requirements_ref.list has an unsupported value")

    def to_json(self) -> dict[str, object]:
        return {
            "posting_sha256": self.posting_sha256, "rules_version": self.rules_version, "digest": self.rows_digest,
            "rows": self.rows, "list": self.kind,
        }

    @classmethod
    def from_json(cls, obj: object) -> "RequirementsRef":
        value = _object(obj, ("posting_sha256", "rules_version", "digest", "rows", "list"), "requirements_ref")
        return cls(
            posting_sha256=_digest_value(value["posting_sha256"], "requirements_ref.posting_sha256"),
            rules_version=_string(value["rules_version"], "requirements_ref.rules_version"),
            rows_digest=_digest_value(value["digest"], "requirements_ref.digest"),
            rows=_integer(value["rows"], "requirements_ref.rows", minimum=0),
            kind=_string(value["list"], "requirements_ref.list"),
        )


@dataclass(frozen=True)
class GateReason(_Contract):
    """One row behind a gate decision: a code and the row's id (``None`` for a row without one)."""

    code: str
    requirement: str | None = None

    def __post_init__(self) -> None:
        if self.code not in GATE_REASONS:
            _fail("bad_enum", "resume_gate reason code has an unsupported value")
        if self.requirement is not None and not is_row_id(self.requirement):
            _fail("invalid_value", "resume_gate reason requirement must be a requirement row id")

    def to_json(self) -> dict[str, object]:
        return {"code": self.code, "requirement": self.requirement}

    @classmethod
    def from_json(cls, obj: object) -> "GateReason":
        value = _object(obj, ("code", "requirement"), "resume_gate reason")
        return cls(_string(value["code"], "resume_gate reason code"), _optional_string(value["requirement"], "resume_gate reason requirement"))


@dataclass(frozen=True)
class GateRecord(_Contract):
    """The gate decision stored with a v9 assessment (SPEC 1.5): whether a resume is suggested, and the rows that say no.

    Computed by ``resume_gate.gate`` from the rows, the questions and the
    verdict, the function the verdict itself is settled by. Every reader uses
    this decision; the model's verdict stays stored as it came.
    """

    decision: str
    reasons: tuple[GateReason, ...] = ()

    def __post_init__(self) -> None:
        if self.decision not in GATE_DECISIONS:
            _fail("bad_enum", "resume_gate.decision has an unsupported value")

    def to_json(self) -> dict[str, object]:
        return {"decision": self.decision, "reasons": [reason.to_json() for reason in self.reasons]}

    @classmethod
    def from_json(cls, obj: object) -> "GateRecord":
        value = _object(obj, ("decision", "reasons"), "resume_gate")
        if type(value["reasons"]) is not list:
            _fail("wrong_type", "resume_gate.reasons must be an array")
        return cls(_string(value["decision"], "resume_gate.decision"), tuple(GateReason.from_json(item) for item in value["reasons"]))


@dataclass(frozen=True)
class AssessmentBody(_Contract):
    """``AssessmentResult`` minus the run-bound ``posting``/``proposal_revision_ref``.

    The model's answer for one job/resume pair, with the same field set and
    the same omit-at-default JSON rules as ``AssessmentResult`` (C4).  The
    strict bounds (``proposals.validate_assessment_bounds``) are applied by
    the quick-assess parser BEFORE this ``from_json``, exactly as
    ``parse_assessment_proposal`` does for the run path, so both paths share
    one validator.
    """

    schema_version: ClassVar[str] = "scout-assessment-body:1"
    matrix: tuple[RequirementMatrixRow, ...]
    suggestions: tuple[str, ...]
    questions: tuple[str, ...]
    sponsorship: SponsorshipStatus | None = None
    verdict: Verdict | None = None
    structured_questions: tuple[AssessmentQuestion, ...] = ()
    not_a_match_reason: str | None = None
    # 0110-10-03: as on ``AssessmentResult``: rows past the matrix bound ("+N not shown"); omitted at 0.
    rows_not_shown: int = 0
    # 0.1.11 N3 (assessment v9, additive; each omitted from JSON at its default, so a v8 body is byte for byte
    # what it was). ``structured_suggestions``: the pattern ``structured_questions`` set: the plain
    # ``suggestions`` list above also holds each one's ``why``, so a reader that knows only strings still shows
    # something. ``pick``: the model's pick as returned (:class:`AssessmentPick`); only on a Matched assessment
    # whose prompt showed master line ids.
    structured_suggestions: tuple[AssessmentSuggestion, ...] = ()
    pick: AssessmentPick | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "matrix": [row.to_json() for row in self.matrix],
            "suggestions": _json_strings(self.suggestions),
            "questions": _json_strings(self.questions),
        }
        if self.sponsorship is not None:
            value["sponsorship"] = _json_enum(self.sponsorship)
        if self.verdict is not None:
            value["verdict"] = _json_enum(self.verdict)
        if self.structured_questions:
            value["structured_questions"] = [item.to_json() for item in self.structured_questions]
        if self.not_a_match_reason is not None:
            value["not_a_match_reason"] = self.not_a_match_reason
        if self.rows_not_shown:
            value["rows_not_shown"] = self.rows_not_shown
        if self.structured_suggestions:
            value["structured_suggestions"] = [item.to_json() for item in self.structured_suggestions]
        if self.pick is not None:
            value["pick"] = self.pick.to_json()
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessmentBody":
        value = _object_with_optional(
            obj,
            ("matrix", "suggestions", "questions"),
            ("sponsorship", "verdict", "structured_questions", "not_a_match_reason", "rows_not_shown", "structured_suggestions", "pick"),
            "assessment_body",
        )
        if type(value["matrix"]) is not list:
            _fail("wrong_type", "assessment_body.matrix must be an array")
        sponsorship = None if "sponsorship" not in value else _enum(value["sponsorship"], SponsorshipStatus, "assessment_body.sponsorship")
        verdict = None if "verdict" not in value else _enum(value["verdict"], Verdict, "assessment_body.verdict")
        structured_questions: tuple[AssessmentQuestion, ...] = ()
        if "structured_questions" in value:
            if type(value["structured_questions"]) is not list:
                _fail("wrong_type", "assessment_body.structured_questions must be an array")
            structured_questions = tuple(AssessmentQuestion.from_json(item) for item in value["structured_questions"])
        not_a_match_reason = None if "not_a_match_reason" not in value else _optional_string(value["not_a_match_reason"], "assessment_body.not_a_match_reason")
        structured_suggestions: tuple[AssessmentSuggestion, ...] = ()
        if "structured_suggestions" in value:
            if type(value["structured_suggestions"]) is not list:
                _fail("wrong_type", "assessment_body.structured_suggestions must be an array")
            if len(value["structured_suggestions"]) > MAX_STRUCTURED_SUGGESTIONS:
                _fail("invalid_value", f"assessment_body.structured_suggestions holds at most {MAX_STRUCTURED_SUGGESTIONS}")
            structured_suggestions = tuple(AssessmentSuggestion.from_json(item) for item in value["structured_suggestions"])
        return cls(
            tuple(RequirementMatrixRow.from_json(item) for item in value["matrix"]),
            _strings(value["suggestions"], "suggestions", allow_empty=True),
            _strings(value["questions"], "questions", allow_empty=True),
            sponsorship,
            verdict,
            structured_questions,
            not_a_match_reason,
            rows_not_shown_count(value, "assessment_body"),
            structured_suggestions,
            AssessmentPick.from_json(value["pick"]) if "pick" in value else None,
        )


@dataclass(frozen=True)
class AssessRequest(_Contract):
    """``POST /api/assess`` body.  ``resume`` defaults to "the selected profile";
    ``preferences``/``model_target`` default to the target's ``find-jobs.json``.

    ``origin`` (additive, one of :data:`ASSESS_ORIGINS`) says where the
    operator started this assessment; ``None`` means the caller did not say
    (``quick_assess.run_quick_assessment`` decides what is stored).  Omitted
    from JSON when ``None``.
    """

    schema_version: ClassVar[str] = "scout-assess-request:1"
    job: AssessJobInput
    resume: AssessResumeInput = field(default_factory=AssessResumeInput)
    preferences: AssessPreferences | None = None
    model_target: ModelTarget | None = None
    origin: str | None = None

    def __post_init__(self) -> None:
        if self.origin is not None and self.origin not in ASSESS_ORIGINS:
            _fail("bad_enum", "assess_request.origin has an unsupported value")

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "job": self.job.to_json(),
            "resume": self.resume.to_json(),
            "preferences": None if self.preferences is None else self.preferences.to_json(),
            "model_target": None if self.model_target is None else _json_enum(self.model_target),
        }
        if self.origin is not None:
            value["origin"] = self.origin
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessRequest":
        value = _object_with_optional(
            obj, ("job",), ("schema_version", "resume", "preferences", "model_target", "origin"), "assess_request"
        )
        if "schema_version" in value and value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "assess_request.schema_version is unsupported")
        resume = value.get("resume")
        preferences = value.get("preferences")
        model_target = value.get("model_target")
        origin = value.get("origin")
        return cls(
            job=AssessJobInput.from_json(value["job"]),
            resume=AssessResumeInput() if resume is None else AssessResumeInput.from_json(resume),
            preferences=None if preferences is None else AssessPreferences.from_json(preferences),
            model_target=None if model_target is None else _enum(model_target, ModelTarget, "assess_request.model_target"),
            origin=None if origin is None else _string(origin, "assess_request.origin"),
        )


#: ``ResumeBasis.input``: what the prompt's RESUME was when a master resume is stored (``assess_master``).
RESUME_INPUT_EVIDENCE = "evidence"
RESUME_INPUTS: tuple[str, ...] = (RESUME_INPUT_EVIDENCE,)


@dataclass(frozen=True)
class ResumeBasis(_Contract):
    """0.1.10.9 master P7: what the assessment read INSTEAD of the profile's own resume.

    Present only when the prompt's RESUME was the evidence view of the master
    resume (``assess_master``): ``master_revision_id`` / ``master_revision``
    name the master revision the lines were picked from and
    ``selector_version`` the rule that picked them
    (``master_selection.SELECTOR_VERSION``).  Ids and a version only: no line
    of the master.  ``resume`` on the response still names the profile's
    pinned resume (the resume identity the assessment is stored under).
    """

    input: str
    master_revision_id: str
    master_revision: int
    selector_version: str

    def __post_init__(self) -> None:
        if self.input not in RESUME_INPUTS:
            _fail("bad_enum", "resume_basis.input has an unsupported value")

    def to_json(self) -> dict[str, object]:
        return {
            "input": self.input,
            "master_revision_id": self.master_revision_id,
            "master_revision": self.master_revision,
            "selector_version": self.selector_version,
        }

    @classmethod
    def from_json(cls, obj: object) -> "ResumeBasis":
        value = _object(obj, ("input", "master_revision_id", "master_revision", "selector_version"), "resume_basis")
        return cls(
            input=_string(value["input"], "resume_basis.input"),
            master_revision_id=_string(value["master_revision_id"], "resume_basis.master_revision_id"),
            master_revision=_integer(value["master_revision"], "resume_basis.master_revision", minimum=1),
            selector_version=_string(value["selector_version"], "resume_basis.selector_version"),
        )


@dataclass(frozen=True)
class AssessChecks(_Contract):
    """0.1.11.4 OBS (additive): what the code checks did to one stored v9 assessment, counts and kinds only.

    ``settled_rows`` / ``settled_by_rule``: ``unclear`` rows the master's own lines settled as ``met`` (A2,
    ``stated_check``), by rule. ``questions_dropped``: the questions dropped with them. ``questions_capped``: questions
    dropped past the list-item and must-have caps. ``suggestions_dropped``: structured suggestions the code check
    dropped (A1), by reason. ``citations_cleaned``: evidence items that were not one verbatim line, by what the check
    did. No requirement, master or resume text and no row id; the keys are rule/reason/action names from the code.
    ``questions_removed`` (Q4, omitted when empty): questions dropped by a truth check, by reason
    (``place_not_in_posting``: the question named a city no text the prompt showed names).
    """

    settled_rows: int = 0
    settled_by_rule: tuple[tuple[str, int], ...] = ()
    questions_dropped: int = 0
    questions_capped: int = 0
    suggestions_dropped: tuple[tuple[str, int], ...] = ()
    citations_cleaned: tuple[tuple[str, int], ...] = ()
    questions_removed: tuple[tuple[str, int], ...] = ()

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "settled_rows": self.settled_rows,
            "settled_by_rule": dict(self.settled_by_rule),
            "questions_dropped": self.questions_dropped,
            "questions_capped": self.questions_capped,
            "suggestions_dropped": dict(self.suggestions_dropped),
            "citations_cleaned": dict(self.citations_cleaned),
        }
        if self.questions_removed:
            value["questions_removed"] = dict(self.questions_removed)
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessChecks":
        keys = (
            "settled_rows", "settled_by_rule", "questions_dropped", "questions_capped",
            "suggestions_dropped", "citations_cleaned",
        )
        value = _object_with_optional(obj, keys, ("questions_removed",), "checks")

        def counts(name: str) -> tuple[tuple[str, int], ...]:
            raw = value[name]
            if type(raw) is not dict:
                _fail("wrong_type", f"checks.{name} must be an object")
            return tuple(
                (_string(key, f"checks.{name} key"), _integer(count, f"checks.{name}.{key}", minimum=0))
                for key, count in sorted(raw.items())
            )

        return cls(
            settled_rows=_integer(value["settled_rows"], "checks.settled_rows", minimum=0),
            settled_by_rule=counts("settled_by_rule"),
            questions_dropped=_integer(value["questions_dropped"], "checks.questions_dropped", minimum=0),
            questions_capped=_integer(value["questions_capped"], "checks.questions_capped", minimum=0),
            suggestions_dropped=counts("suggestions_dropped"),
            citations_cleaned=counts("citations_cleaned"),
            questions_removed=counts("questions_removed") if "questions_removed" in value else (),
        )


@dataclass(frozen=True)
class VerdictHistoryEntry(_Contract):
    """Q4a (v0.1.9): one line of a quick assessment's verdict history.

    ``at`` is when that assessment ran (the response's ``updated_at`` at the
    time), ``verdict`` its verdict (``None`` for a result the prompt gave no
    verdict for), and ``trigger`` names what caused it: ``"assess"`` (the
    first assessment of this job), ``"reassess"`` (assessed again with no
    answer in between: the CLI or ``POST /api/assess`` on a known job) or
    ``"answer:<question_id>"`` (``POST /api/answers`` with ``reassess``).
    Operator answer 4: a re-assessment APPENDS here, never rewrites.
    """

    at: str
    verdict: Verdict | None
    trigger: str

    def __post_init__(self) -> None:
        if not self.trigger:
            _fail("invalid_value", "verdict_history_entry.trigger must not be empty")

    def to_json(self) -> dict[str, object]:
        return {
            "at": self.at,
            "verdict": None if self.verdict is None else _json_enum(self.verdict),
            "trigger": self.trigger,
        }

    @classmethod
    def from_json(cls, obj: object) -> "VerdictHistoryEntry":
        value = _object(obj, ("at", "verdict", "trigger"), "verdict_history_entry")
        verdict = value["verdict"]
        return cls(
            at=_string(value["at"], "verdict_history_entry.at"),
            verdict=None if verdict is None else _enum(verdict, Verdict, "verdict_history_entry.verdict"),
            trigger=_string(value["trigger"], "verdict_history_entry.trigger"),
        )


@dataclass(frozen=True)
class AssessResponse(_Contract):
    """One quick assessment: what was assessed (identities only), the effective
    preferences, the model's answer, and where it is stored.

    Never carries resume text, and never PASTED job text: ``job`` is
    serialized WITHOUT its ``text`` field (``text_sha256`` stays), and
    ``ResolvedResume`` never serializes its text at all.  ``from_json``
    therefore yields an empty ``job.text``.  The one posting text it does
    carry is ``posting_text``: the text fetched from a PUBLIC posting URL.
    """

    schema_version: ClassVar[str] = "scout-assess-response:1"
    job: ResolvedJob
    resume: ResolvedResume
    preferences: AssessPreferences
    result: AssessmentBody
    producer: Producer
    usage: UsageBlock | None
    instructions_digest: str
    created_at: str
    stored_path: str
    # P3 (v0.1.9, additive): the last time this job/resume pair was
    # assessed -- distinct from ``created_at`` (kept from the FIRST write,
    # C4-style), so a re-assessment (P3's Q&A loop) can be told apart from
    # the original one. Omitted from JSON when equal to ``created_at`` (the
    # P5-era shape: never re-assessed yet) so a P5 response round-trips
    # byte-identically; ``from_json`` fills it back in from ``created_at``
    # when absent.
    updated_at: str = ""
    # Q4a (v0.1.9, additive): every assessment of this job/resume pair so
    # far, oldest first -- appended on each assess/re-assess by
    # ``quick_assess.run_quick_assessment``. Omitted from JSON when empty so
    # a stored file written before this field still round-trips
    # byte-identically; ``from_json`` reads a missing key as ``()``.
    history: tuple[VerdictHistoryEntry, ...] = ()
    # uat-bug-014 (v0.1.9, additive): the full text fetched from the PUBLIC
    # posting URL (what a run row carries as ``posting.text``), so the job
    # page can show its excerpt. ``None`` for pasted job text (never echoed
    # or stored) and for a file written before this field. Omitted from JSON
    # when ``None``.
    posting_text: str | None = None
    # uat-bug-015 (v0.1.9, additive): this posting's rank score (Jev-era files only; never written now) in the shape
    # run results carry in ``rank_scores`` (``normalized_url`` is the
    # ``job_identity``), or why there is none (:data:`RANK_SKIP_REASONS`).
    # At most one of the two is set; both are ``None`` for a file written
    # before these fields. Each is omitted from JSON when ``None``.
    rank_score: RankScore | None = None
    rank_skip_reason: str | None = None
    # assess-origin-field (v0.1.9, additive): where the operator started
    # this assessment (:data:`ASSESS_ORIGINS`). ``None`` for a file written
    # before this field; the UI then decides from the job's identity and the
    # runs it has loaded. Omitted from JSON when ``None``.
    origin: str | None = None
    # 0110-039 (additive): the BASIS of this assessment, what a find-jobs run
    # seals on its ``AssessOutput``: the assess prompt version it was made
    # with, a digest of the candidate constraints the prompt carried
    # (``assessment_core.constraints_digest``: sponsorship need, eligible
    # countries, own location, work mode) and the story bank the prompt was
    # offered (``None``: no profile to read a bank for). No constraint and no
    # answer text is in any of them. All ``None`` for a file written before
    # these fields; each is omitted from JSON when ``None``.
    # ``assessment_basis.stale_reason`` compares them with what the profile
    # would be assessed with now.
    prompt_version: str | None = None
    constraints_digest: str | None = None
    story_bank: StoryBankStamp | None = None
    # 0.1.10.7 PL2 (additive): the rest of what a run seals, so an assessment
    # made with no run keeps its provenance (DESIGN 10.2). ``profile_ref`` is
    # the profile's ``{profile_id, revision, content_digest}`` (the run seal's
    # shape; ``None`` for a pasted resume, whose identity is ``resume.
    # content_sha256``); ``posting_sha256`` the posting's content digest, the
    # company index's ``content_sha256`` for the same title and text
    # (``assessment_basis.posting_sha256``); ``model`` the model id the
    # adapter answered with (``InvocationResult.resolved_model``). Ids and
    # digests only. All ``None`` for a file written before these fields;
    # each is omitted from JSON when ``None``.
    profile_ref: ProfileRef | None = None
    posting_sha256: str | None = None
    model: str | None = None
    # 0.1.10.9 master P7 (additive): set only when the prompt's RESUME was the
    # evidence view of the master resume, not the profile's own resume
    # (:class:`ResumeBasis`). ``None`` for every other assessment and for a
    # file written before this field; omitted from JSON when ``None``, so
    # such a record's bytes are what they were.
    # ``assessment_basis`` reads it for the ``resume_changed`` stale reason.
    resume_basis: ResumeBasis | None = None
    # 0.1.11 N3 (assessment v9, additive): set only on an assessment whose rows carry v9 fields
    # (``requirement_weights.uses_v9_rules``); ``None`` for every other one and for a file written before these
    # fields, each omitted from JSON when ``None``. ``requirements_ref``: which requirement list the rows are
    # (:class:`RequirementsRef`, ``requirements_list``). ``resume_gate``: whether a resume is suggested
    # (:class:`GateRecord`, ``resume_gate.gate``).
    requirements_ref: RequirementsRef | None = None
    resume_gate: GateRecord | None = None
    # 0.1.11 GUARDFIX (orchestrator #87): the visible note on an assessment whose answer held fewer requirement rows than
    # the incomplete-posting guard accepts, after its one retry ("Only N requirements were read from this posting. Open
    # the posting to check."). Omitted from JSON when ``None``, so every stored file round-trips as it was.
    requirements_note: str | None = None
    # 0.1.11 MODELPIN (additive): the model the call ASKED for (``model`` above is the one that answered) and whether
    # the CLI refused it and the default answered (one fallback call). ``None`` / ``False`` for a file written before
    # these fields and for a target with no evaluated model; each is omitted from JSON then.
    model_asked: str | None = None
    model_fallback: bool = False
    # 0.1.11.4 OBS (additive): counts of what the code checks settled or dropped (:class:`AssessChecks`); ``None`` for a
    # file written before this field and for a non-v9 answer, omitted from JSON then.
    checks: AssessChecks | None = None

    def __post_init__(self) -> None:
        if (
            self.preferences.visa_sponsorship_required is None
            or self.preferences.titles is None
            or self.preferences.countries is None
        ):
            _fail("invalid_value", "assess_response.preferences must carry the effective values, never None")
        if self.posting_text is not None and self.job.fetch_kind == "pasted":
            _fail("invalid_value", "assess_response.posting_text is never set for pasted job text")
        if self.rank_skip_reason is not None and self.rank_skip_reason not in RANK_SKIP_REASONS:
            _fail("bad_enum", "assess_response.rank_skip_reason has an unsupported value")
        if self.rank_score is not None and self.rank_skip_reason is not None:
            _fail("invalid_value", "assess_response carries a rank_score or a rank_skip_reason, never both")
        if self.origin is not None and self.origin not in ASSESS_ORIGINS:
            _fail("bad_enum", "assess_response.origin has an unsupported value")

    def to_json(self) -> dict[str, object]:
        job = self.job.to_json()
        del job["text"]
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "job": job,
            "resume": self.resume.to_json(),
            "preferences": self.preferences.to_json(),
            "result": self.result.to_json(),
            "producer": self.producer.to_json(),
            "usage": None if self.usage is None else self.usage.to_json(),
            "instructions_digest": self.instructions_digest,
            "created_at": self.created_at,
            "stored_path": self.stored_path,
        }
        if self.updated_at and self.updated_at != self.created_at:
            value["updated_at"] = self.updated_at
        if self.history:
            value["history"] = [entry.to_json() for entry in self.history]
        if self.posting_text is not None:
            value["posting_text"] = self.posting_text
        if self.rank_score is not None:
            value["rank_score"] = self.rank_score.to_json()
        if self.rank_skip_reason is not None:
            value["rank_skip_reason"] = self.rank_skip_reason
        if self.origin is not None:
            value["origin"] = self.origin
        if self.prompt_version is not None:
            value["prompt_version"] = self.prompt_version
        if self.constraints_digest is not None:
            value["constraints_digest"] = self.constraints_digest
        if self.story_bank is not None:
            value["story_bank"] = self.story_bank.to_json()
        if self.profile_ref is not None:
            value["profile_ref"] = self.profile_ref.to_json()
        if self.posting_sha256 is not None:
            value["posting_sha256"] = self.posting_sha256
        if self.model is not None:
            value["model"] = self.model
        if self.resume_basis is not None:
            value["resume_basis"] = self.resume_basis.to_json()
        if self.requirements_ref is not None:
            value["requirements_ref"] = self.requirements_ref.to_json()
        if self.resume_gate is not None:
            value["resume_gate"] = self.resume_gate.to_json()
        if self.requirements_note is not None:
            value["requirements_note"] = self.requirements_note
        if self.model_asked is not None:
            value["model_asked"] = self.model_asked
        if self.model_fallback:
            value["model_fallback"] = True
        if self.checks is not None:
            value["checks"] = self.checks.to_json()
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessResponse":
        value = _object_with_optional(
            obj,
            (
                "schema_version", "job", "resume", "preferences", "result", "producer", "usage",
                "instructions_digest", "created_at", "stored_path",
            ),
            (
                "updated_at", "history", "posting_text", "rank_score", "rank_skip_reason", "origin",
                "prompt_version", "constraints_digest", "story_bank", "profile_ref", "posting_sha256", "model", "resume_basis",
                "requirements_ref", "resume_gate", "requirements_note", "model_asked", "model_fallback", "checks",
            ),
            "assess_response",
        )
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "assess_response.schema_version is unsupported")
        job = value["job"]
        if type(job) is not dict:
            _fail("wrong_type", "assess_response.job must be an object")
        if "text" in job:
            _fail("unknown_key", "assess_response.job never carries the job text")
        usage = value["usage"]
        created_at = _string(value["created_at"], "created_at")
        updated_at = _string(value["updated_at"], "updated_at") if "updated_at" in value else created_at
        history: tuple[VerdictHistoryEntry, ...] = ()
        if "history" in value:
            if type(value["history"]) is not list:
                _fail("wrong_type", "assess_response.history must be an array")
            history = tuple(VerdictHistoryEntry.from_json(item) for item in value["history"])
        posting_text = (
            _string(value["posting_text"], "assess_response.posting_text", nonempty=False)
            if "posting_text" in value
            else None
        )
        rank_score = RankScore.from_json(value["rank_score"]) if "rank_score" in value else None
        rank_skip_reason = (
            _string(value["rank_skip_reason"], "assess_response.rank_skip_reason")
            if "rank_skip_reason" in value
            else None
        )
        origin = _string(value["origin"], "assess_response.origin") if "origin" in value else None
        return cls(
            job=ResolvedJob.from_json({**job, "text": ""}),
            resume=ResolvedResume.from_json(value["resume"]),
            preferences=AssessPreferences.from_json(value["preferences"]),
            result=AssessmentBody.from_json(value["result"]),
            producer=Producer.from_json(value["producer"]),
            usage=None if usage is None else UsageBlock.from_json(usage),
            instructions_digest=_digest_value(value["instructions_digest"], "instructions_digest"),
            created_at=created_at,
            stored_path=_string(value["stored_path"], "stored_path"),
            updated_at=updated_at,
            history=history,
            posting_text=posting_text,
            rank_score=rank_score,
            rank_skip_reason=rank_skip_reason,
            origin=origin,
            prompt_version=_string(value["prompt_version"], "assess_response.prompt_version") if "prompt_version" in value else None,
            constraints_digest=(
                _digest_value(value["constraints_digest"], "assess_response.constraints_digest")
                if "constraints_digest" in value
                else None
            ),
            story_bank=StoryBankStamp.from_json(value["story_bank"]) if "story_bank" in value else None,
            profile_ref=ProfileRef.from_json(value["profile_ref"]) if "profile_ref" in value else None,
            posting_sha256=(
                _digest_value(value["posting_sha256"], "assess_response.posting_sha256") if "posting_sha256" in value else None
            ),
            model=_string(value["model"], "assess_response.model") if "model" in value else None,
            resume_basis=ResumeBasis.from_json(value["resume_basis"]) if "resume_basis" in value else None,
            requirements_ref=RequirementsRef.from_json(value["requirements_ref"]) if "requirements_ref" in value else None,
            resume_gate=GateRecord.from_json(value["resume_gate"]) if "resume_gate" in value else None,
            requirements_note=(
                _string(value["requirements_note"], "assess_response.requirements_note") if "requirements_note" in value else None
            ),
            model_asked=_string(value["model_asked"], "assess_response.model_asked") if "model_asked" in value else None,
            model_fallback=_bool(value["model_fallback"], "assess_response.model_fallback") if "model_fallback" in value else False,
            checks=AssessChecks.from_json(value["checks"]) if "checks" in value else None,
        )


@dataclass(frozen=True)
class AssessmentsListResponse(_Contract):
    """``GET /api/assessments``: stored quick assessments, newest first."""

    schema_version: ClassVar[str] = "scout-assessments-response:1"
    items: tuple[AssessResponse, ...]

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "items": [item.to_json() for item in self.items]}

    @classmethod
    def from_json(cls, obj: object) -> "AssessmentsListResponse":
        value = _object_with_optional(obj, ("schema_version", "items"), (), "assessments_list_response")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "assessments_list_response.schema_version is unsupported")
        if type(value["items"]) is not list:
            _fail("wrong_type", "assessments_list_response.items must be an array")
        return cls(tuple(AssessResponse.from_json(item) for item in value["items"]))


__all__ = [
    "ASSESS_ORIGINS",
    "ELIGIBILITY_ROW_IDS",
    "FETCH_KINDS",
    "GATE_DECISIONS",
    "GATE_HOLD_QUESTION",
    "GATE_HOLD_UNMET",
    "GATE_NOT_A_MATCH",
    "GATE_REASONS",
    "GATE_REASON_ASKABLE_UNMET",
    "GATE_REASON_HARD_UNMET",
    "GATE_REASON_QUESTION_OPEN",
    "GATE_SUGGEST",
    "MAX_MASTER_ID_CHARS",
    "MAX_PICK_LINES",
    "MAX_STRUCTURED_SUGGESTIONS",
    "MAX_SUGGESTION_PHRASE_CHARS",
    "MAX_SUGGESTION_WHY_CHARS",
    "PICK_SECTIONS",
    "REQUIREMENTS_LISTS",
    "REQUIREMENTS_LIST_OWN",
    "REQUIREMENTS_LIST_STORED",
    "SUGGESTION_KINDS",
    "SUGGESTION_KINDS_WITHOUT_RESUME",
    "ORIGIN_JOB_PAGE",
    "ORIGIN_QUICK_ASSESS",
    "RANK_SKIP_REASONS",
    "RESUME_INPUTS",
    "RESUME_INPUT_EVIDENCE",
    "AssessJobInput",
    "AssessPreferences",
    "AssessRequest",
    "AssessResponse",
    "AssessResumeInput",
    "AssessmentBody",
    "AssessmentPick",
    "AssessmentSuggestion",
    "AssessmentsListResponse",
    "GateReason",
    "GateRecord",
    "RequirementsRef",
    "ResolvedJob",
    "ResolvedResume",
    "ResumeBasis",
    "VerdictHistoryEntry",
    "text_identity",
]
