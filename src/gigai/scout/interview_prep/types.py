"""Frozen, JSON-friendly types for one interview prep.

Mirrors ``find_jobs/discovery/types.py``'s convention (frozen dataclasses
with ``to_json``/``from_json`` so a fixture round-trips exactly), scoped to
what S18's minimal first slice needs: company research (web-search,
sourced), role research (posting-text-only), question categories (grounded,
category-level per the coordinator default), and prep notes (resume points
and gaps, reusing assess's requirement matrix when one exists).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceClaim:
    """One company-research claim, always carrying the source URL it came from."""

    claim: str
    source_url: str
    verified: bool

    def to_json(self) -> dict[str, object]:
        return {"claim": self.claim, "source_url": self.source_url, "verified": self.verified}

    @classmethod
    def from_json(cls, obj: object) -> "SourceClaim":
        if type(obj) is not dict:
            raise ValueError("source claim must be an object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        return cls(claim=str(value["claim"]), source_url=str(value["source_url"]), verified=bool(value["verified"]))


@dataclass(frozen=True)
class CompanyResearch:
    """Company-research section; ``skipped`` names the reason a missing key produced no claims."""

    claims: tuple[SourceClaim, ...] = ()
    cost_usd: float = 0.0
    skipped: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "claims": [item.to_json() for item in self.claims],
            "cost_usd": round(self.cost_usd, 6),
            "skipped": self.skipped,
        }

    @classmethod
    def from_json(cls, obj: object) -> "CompanyResearch":
        if type(obj) is not dict:
            raise ValueError("company research must be an object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        claims = value.get("claims", [])
        if not isinstance(claims, list):
            raise ValueError("company research claims must be a list")
        return cls(
            claims=tuple(SourceClaim.from_json(item) for item in claims),
            cost_usd=float(value.get("cost_usd", 0.0)),  # type: ignore[arg-type]
            skipped=value.get("skipped"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class RoleResearch:
    """Role research derived from the posting text alone (no search)."""

    responsibilities: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {"responsibilities": list(self.responsibilities), "requirements": list(self.requirements)}

    @classmethod
    def from_json(cls, obj: object) -> "RoleResearch":
        if type(obj) is not dict:
            raise ValueError("role research must be an object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        return cls(
            responsibilities=tuple(str(item) for item in value.get("responsibilities", [])),  # type: ignore[union-attr]
            requirements=tuple(str(item) for item in value.get("requirements", [])),  # type: ignore[union-attr]
        )


#: Coordinator default (S18 open question #2): category-level prediction
#: only, no per-question probabilities.
QUESTION_CATEGORIES = ("behavioural", "system_design", "coding_in_their_stack", "domain")


@dataclass(frozen=True)
class QuestionCategoryPrediction:
    """One predicted question category with a one-line grounded rationale."""

    category: str
    why: str
    grounded_in: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {"category": self.category, "why": self.why, "grounded_in": list(self.grounded_in)}

    @classmethod
    def from_json(cls, obj: object) -> "QuestionCategoryPrediction":
        if type(obj) is not dict:
            raise ValueError("question category prediction must be an object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        return cls(
            category=str(value["category"]),
            why=str(value["why"]),
            grounded_in=tuple(str(item) for item in value.get("grounded_in", [])),  # type: ignore[union-attr]
        )


@dataclass(frozen=True)
class PrepNotes:
    """Resume points to lead with and gaps to prepare an answer for."""

    resume_points: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    matrix_source: str | None = None  # "assess" | None (no matrix found)

    def to_json(self) -> dict[str, object]:
        return {
            "resume_points": list(self.resume_points),
            "gaps": list(self.gaps),
            "matrix_source": self.matrix_source,
        }

    @classmethod
    def from_json(cls, obj: object) -> "PrepNotes":
        if type(obj) is not dict:
            raise ValueError("prep notes must be an object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        return cls(
            resume_points=tuple(str(item) for item in value.get("resume_points", [])),  # type: ignore[union-attr]
            gaps=tuple(str(item) for item in value.get("gaps", [])),  # type: ignore[union-attr]
            matrix_source=value.get("matrix_source"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class ResumeIdentity:
    """Identifies the resume revision this prep was built against (idempotency key)."""

    reference_id: str
    content_sha256: str

    def to_json(self) -> dict[str, object]:
        return {"reference_id": self.reference_id, "content_sha256": self.content_sha256}

    @classmethod
    def from_json(cls, obj: object) -> "ResumeIdentity":
        if type(obj) is not dict:
            raise ValueError("resume identity must be an object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        return cls(reference_id=str(value["reference_id"]), content_sha256=str(value["content_sha256"]))


@dataclass(frozen=True)
class InterviewPrep:
    """One posting's interview prep. Idempotent per (posting_id, resume revision)."""

    posting_id: str
    company: str
    title: str
    resume: ResumeIdentity | None
    company_research: CompanyResearch
    role_research: RoleResearch
    question_categories: tuple[QuestionCategoryPrediction, ...]
    prep_notes: PrepNotes
    model_target: str | None
    cost_usd: float
    created_at: str
    refreshed_at: str

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": "scout-interview-prep:1",
            "posting_id": self.posting_id,
            "company": self.company,
            "title": self.title,
            "resume": self.resume.to_json() if self.resume is not None else None,
            "company_research": self.company_research.to_json(),
            "role_research": self.role_research.to_json(),
            "question_categories": [item.to_json() for item in self.question_categories],
            "prep_notes": self.prep_notes.to_json(),
            "model_target": self.model_target,
            "cost_usd": round(self.cost_usd, 6),
            "created_at": self.created_at,
            "refreshed_at": self.refreshed_at,
        }

    @classmethod
    def from_json(cls, obj: object) -> "InterviewPrep":
        if type(obj) is not dict:
            raise ValueError("interview prep must be an object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        resume = value.get("resume")
        return cls(
            posting_id=str(value["posting_id"]),
            company=str(value["company"]),
            title=str(value["title"]),
            resume=ResumeIdentity.from_json(resume) if resume is not None else None,
            company_research=CompanyResearch.from_json(value["company_research"]),
            role_research=RoleResearch.from_json(value["role_research"]),
            question_categories=tuple(
                QuestionCategoryPrediction.from_json(item) for item in value.get("question_categories", [])  # type: ignore[union-attr]
            ),
            prep_notes=PrepNotes.from_json(value["prep_notes"]),
            model_target=value.get("model_target"),  # type: ignore[arg-type]
            cost_usd=float(value.get("cost_usd", 0.0)),  # type: ignore[arg-type]
            created_at=str(value["created_at"]),
            refreshed_at=str(value["refreshed_at"]),
        )


__all__ = [
    "QUESTION_CATEGORIES",
    "CompanyResearch",
    "InterviewPrep",
    "PrepNotes",
    "QuestionCategoryPrediction",
    "ResumeIdentity",
    "RoleResearch",
    "SourceClaim",
]
