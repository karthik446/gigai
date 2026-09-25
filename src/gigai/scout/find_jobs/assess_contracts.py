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
    AssessmentQuestion,
    ModelTarget,
    PinnedResume,
    Producer,
    RequirementMatrixRow,
    SponsorshipStatus,
    UsageBlock,
    Verdict,
    _Contract,
    _bool,
    _country_codes,
    _digest_value,
    _enum,
    _fail,
    _json_enum,
    _json_strings,
    _object_with_optional,
    _optional_string,
    _string,
    _strings,
)

#: ``ResolvedJob.fetch_kind`` values, in the order ``resolve_job`` tries them.
FETCH_KINDS: tuple[str, ...] = ("pasted", "ats_single", "ats_board", "generic")

_TEXT_IDENTITY_PREFIX = "text:"


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

    def to_json(self) -> dict[str, object]:
        return {
            "visa_sponsorship_required": self.visa_sponsorship_required,
            "titles": None if self.titles is None else list(self.titles),
            "countries": None if self.countries is None else list(self.countries),
        }

    @classmethod
    def from_json(cls, obj: object) -> "AssessPreferences":
        value = _object_with_optional(
            obj, (), ("visa_sponsorship_required", "titles", "countries"), "assess_preferences"
        )
        countries = value.get("countries")
        return cls(
            visa_sponsorship_required=_optional_bool(value.get("visa_sponsorship_required"), "visa_sponsorship_required"),
            titles=_optional_strings(value.get("titles"), "titles"),
            countries=None if countries is None else _country_codes(countries, "countries"),
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
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessmentBody":
        value = _object_with_optional(
            obj,
            ("matrix", "suggestions", "questions"),
            ("sponsorship", "verdict", "structured_questions", "not_a_match_reason"),
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
        return cls(
            tuple(RequirementMatrixRow.from_json(item) for item in value["matrix"]),
            _strings(value["suggestions"], "suggestions", allow_empty=True),
            _strings(value["questions"], "questions", allow_empty=True),
            sponsorship,
            verdict,
            structured_questions,
            not_a_match_reason,
        )


@dataclass(frozen=True)
class AssessRequest(_Contract):
    """``POST /api/assess`` body.  ``resume`` defaults to "the selected profile";
    ``preferences``/``model_target`` default to the target's ``find-jobs.json``."""

    schema_version: ClassVar[str] = "scout-assess-request:1"
    job: AssessJobInput
    resume: AssessResumeInput = field(default_factory=AssessResumeInput)
    preferences: AssessPreferences | None = None
    model_target: ModelTarget | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "job": self.job.to_json(),
            "resume": self.resume.to_json(),
            "preferences": None if self.preferences is None else self.preferences.to_json(),
            "model_target": None if self.model_target is None else _json_enum(self.model_target),
        }

    @classmethod
    def from_json(cls, obj: object) -> "AssessRequest":
        value = _object_with_optional(
            obj, ("job",), ("schema_version", "resume", "preferences", "model_target"), "assess_request"
        )
        if "schema_version" in value and value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "assess_request.schema_version is unsupported")
        resume = value.get("resume")
        preferences = value.get("preferences")
        model_target = value.get("model_target")
        return cls(
            job=AssessJobInput.from_json(value["job"]),
            resume=AssessResumeInput() if resume is None else AssessResumeInput.from_json(resume),
            preferences=None if preferences is None else AssessPreferences.from_json(preferences),
            model_target=None if model_target is None else _enum(model_target, ModelTarget, "assess_request.model_target"),
        )


@dataclass(frozen=True)
class AssessResponse(_Contract):
    """One quick assessment: what was assessed (identities only), the effective
    preferences, the model's answer, and where it is stored.

    Never carries resume text or full job text: ``job`` is serialized WITHOUT
    its ``text`` field (``text_sha256`` stays), and ``ResolvedResume`` never
    serializes its text at all.  ``from_json`` therefore yields an empty
    ``job.text``.
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

    def __post_init__(self) -> None:
        if (
            self.preferences.visa_sponsorship_required is None
            or self.preferences.titles is None
            or self.preferences.countries is None
        ):
            _fail("invalid_value", "assess_response.preferences must carry the effective values, never None")

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
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessResponse":
        value = _object_with_optional(
            obj,
            (
                "schema_version", "job", "resume", "preferences", "result", "producer", "usage",
                "instructions_digest", "created_at", "stored_path",
            ),
            ("updated_at",),
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
    "FETCH_KINDS",
    "AssessJobInput",
    "AssessPreferences",
    "AssessRequest",
    "AssessResponse",
    "AssessResumeInput",
    "AssessmentBody",
    "AssessmentsListResponse",
    "ResolvedJob",
    "ResolvedResume",
    "text_identity",
]
