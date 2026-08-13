"""G26 model-facilitated Gig definition and proposal drafting."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from .adapters.factory import AdapterFactoryError, resolve_model_adapter
from .adapters.port import ModelInvocationError
from .config import GigAIConfig
from .proposal_interview import InterviewSession, ProposalInterviewError
from .review import redact_text


class GigBuilderError(ProposalInterviewError):
    """The selected model could not safely produce a builder response."""


@dataclass(frozen=True)
class BuilderDraft:
    """Normalized, reviewable model output; it has no approval authority."""

    summary: str
    assumptions: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    citations: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "assumptions": list(self.assumptions),
            "unresolved_questions": list(self.unresolved_questions),
            "citations": [dict(item) for item in self.citations],
        }


def build_model_draft(
    *,
    config: GigAIConfig,
    model_target: str,
    session: InterviewSession,
    reference_bytes: Mapping[str, bytes],
    intent_text: str | None = None,
    network_allowed: bool = False,
) -> tuple[BuilderDraft, dict[str, object]]:
    """Ask one selected target to research and draft a proposal.

    The deterministic target uses an immutable fixture response. Remote targets
    receive only the operator request, answered follow-ups, and selected,
    redacted reference text. The response is parsed and bounded before any
    proposal artifact is materialized.
    """

    try:
        binding = resolve_model_adapter(config, model_target)
    except (AdapterFactoryError, ValueError) as exc:
        raise GigBuilderError(f"model target is not usable: {exc}") from exc
    remote = binding.current.endpoint.adapter != "deterministic"
    if remote and not network_allowed:
        raise GigBuilderError("proposal research requires explicit configured-provider permission")
    prompt = "g26-proposal-build"
    if remote:
        parts = [
            prompt,
            f"Gig name: {session.request_kind}",
            f"Operator intent: {intent_text or session.request_artifact.get('path', '')}",
            "Answered follow-ups:",
        ]
        for answer in session.answers:
            parts.append(f"- {answer.question_id}: {answer.value}")
        for reference in session.references:
            if reference.decision != "selected":
                continue
            content = reference_bytes.get(reference.reference_id)
            if content is None:
                raise GigBuilderError("selected reference bytes are unavailable")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GigBuilderError("selected reference is not UTF-8 text") from exc
            parts.append(f"[{reference.reference_id}]\n{redact_text(text, ())}")
        prompt = "\n".join(parts)
    try:
        result = binding.port.invoke(binding.request(role="gig-builder", prompt=prompt))
        payload = json.loads(result.output_text)
    except (AdapterFactoryError, ModelInvocationError, ValueError, json.JSONDecodeError) as exc:
        raise GigBuilderError(f"model proposal build failed: {exc}") from exc
    draft = _parse_draft(payload)
    selection = {
        "target_name": binding.current.target.name,
        "endpoint_name": binding.current.endpoint.name,
        "model": binding.current.target.model,
        "adapter": binding.current.endpoint.adapter,
    }
    return draft, selection


def _parse_draft(payload: Any) -> BuilderDraft:
    if not isinstance(payload, dict):
        raise GigBuilderError("model proposal response must be an object")
    summary = payload.get("summary")
    assumptions = payload.get("assumptions")
    unresolved = payload.get("unresolved_questions")
    citations = payload.get("citations")
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or not isinstance(assumptions, list)
        or not isinstance(unresolved, list)
        or not isinstance(citations, list)
    ):
        raise GigBuilderError("model proposal response has an invalid draft shape")
    if any(not isinstance(item, str) or not item.strip() for item in assumptions + unresolved):
        raise GigBuilderError("model proposal assumptions and open questions must be text")
    normalized_citations: list[dict[str, object]] = []
    for item in citations:
        if not isinstance(item, dict):
            raise GigBuilderError("model proposal citations must be objects")
        if not isinstance(item.get("claim_id"), str) or not isinstance(item.get("locator"), str):
            raise GigBuilderError("model proposal citations require claim_id and locator")
        normalized_citations.append(
            {
                "claim_id": item["claim_id"],
                "source_kind": item.get("source_kind", "operator_statement"),
                "locator": item["locator"],
                "source_sha256": item.get("source_sha256"),
                "verification": item.get("verification", "unresolved"),
            }
        )
    return BuilderDraft(
        summary=summary.strip(),
        assumptions=tuple(item.strip() for item in assumptions),
        unresolved_questions=tuple(item.strip() for item in unresolved),
        citations=tuple(normalized_citations),
    )


__all__ = ["BuilderDraft", "GigBuilderError", "build_model_draft"]
