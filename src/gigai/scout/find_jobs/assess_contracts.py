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
    PinnedResume,
    _Contract,
    _bool,
    _country_codes,
    _digest_value,
    _fail,
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
    """The job to assess: exactly one of a public URL or pasted text."""

    schema_version: ClassVar[str] = "scout-assess-job-input:1"
    job_url: str | None = None
    job_text: str | None = None

    def __post_init__(self) -> None:
        has_url = bool(self.job_url)
        has_text = bool(self.job_text)
        if has_url == has_text:
            _fail("job_input_invalid", "pass exactly one of job_url or job_text")

    def to_json(self) -> dict[str, object]:
        return {"job_url": self.job_url, "job_text": self.job_text}

    @classmethod
    def from_json(cls, obj: object) -> "AssessJobInput":
        value = _object_with_optional(obj, (), ("job_url", "job_text"), "assess_job_input")
        return cls(
            job_url=_optional_string(value.get("job_url"), "job_url"),
            job_text=_optional_string(value.get("job_text"), "job_text"),
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


__all__ = [
    "FETCH_KINDS",
    "AssessJobInput",
    "AssessPreferences",
    "AssessResumeInput",
    "ResolvedJob",
    "ResolvedResume",
    "text_identity",
]
