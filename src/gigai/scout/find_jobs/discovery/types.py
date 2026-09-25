"""Shared, source-agnostic types the two discovery sources produce.

Both ``openai_source.py`` and ``h1b_source.py`` emit ``Candidate`` rows;
``merge.py`` unions/dedupes/verifies them into ``DiscoveryResult.new_boards``.
Frozen and JSON-friendly (``to_json``) so a fixture round-trips exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Candidate:
    """One company a source found, before board-check/verification (merge.py).

    Mirrors the S23 strict schema (Investigate §4) minus ``board_token``
    (S23 Recommendation point 1: the agent/model rarely fills it reliably;
    product code derives it from ``careers_url`` via ``parse_board_url``
    instead, in ``merge.py``).
    """

    company: str
    careers_url: str
    ats_provider: str  # "greenhouse" | "lever" | "ashby" | "other" | "unknown"
    sponsorship: str  # "yes" | "no" | "unknown"
    sponsorship_evidence: str
    source_url: str
    found_by: str  # "openai_web_search" | "h1b"
    matching_us_postings_hint: int | None = field(default=None)

    def to_json(self) -> dict[str, object]:
        return {
            "company": self.company,
            "careers_url": self.careers_url,
            "ats_provider": self.ats_provider,
            "sponsorship": self.sponsorship,
            "sponsorship_evidence": self.sponsorship_evidence,
            "source_url": self.source_url,
            "found_by": self.found_by,
            "matching_us_postings_hint": self.matching_us_postings_hint,
        }

    @classmethod
    def from_json(cls, obj: object) -> "Candidate":
        if type(obj) is not dict:
            raise ValueError("candidate must be an object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        return cls(
            company=str(value["company"]),
            careers_url=str(value["careers_url"]),
            ats_provider=str(value["ats_provider"]),
            sponsorship=str(value["sponsorship"]),
            sponsorship_evidence=str(value["sponsorship_evidence"]),
            source_url=str(value["source_url"]),
            found_by=str(value["found_by"]),
            matching_us_postings_hint=value.get("matching_us_postings_hint"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class SourceRunOutcome:
    """One source's outcome for one discovery session (feeds ``DiscoveryResult.sources``)."""

    name: str  # "openai_web_search" | "h1b"
    runs: int
    cost_usd: float
    candidates: tuple[Candidate, ...]
    error: str | None = None
    skip_reason: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "runs": self.runs,
            "cost_usd": round(self.cost_usd, 6),
            "error": self.error,
            "skip_reason": self.skip_reason,
        }


__all__ = ["Candidate", "SourceRunOutcome"]
