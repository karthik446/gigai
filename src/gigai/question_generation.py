"""G22 model-question generation through the configured G18 model port."""

from __future__ import annotations

import json
from pathlib import Path

from .adapters.factory import AdapterFactoryError, resolve_model_adapter
from .adapters.port import ModelInvocationError
from .canonical import digest_imported_bytes
from .config import GigAIConfig
from .proposal_interview import InterviewSession, ProposalInterviewError, Question, add_questions
from .review import redact_text


DETERMINISTIC_PROMPT = "g22-question-probe"
G26_QUESTION_PROMPTS = ("g26-question-probe-1", "g26-question-probe-2")


class QuestionGenerationError(ProposalInterviewError):
    """Question generation failed or was blocked at the provider boundary."""


def generate_model_questions(
    *,
    config: GigAIConfig,
    model_target: str,
    session: InterviewSession,
    reference_bytes: dict[str, bytes],
    network_allowed: bool = False,
    prompt_name: str = DETERMINISTIC_PROMPT,
) -> InterviewSession:
    """Ask the selected model for typed follow-up questions.

    The built-in deterministic target uses a fixed offline prompt. A remote
    target is refused unless the caller explicitly opts into network access;
    G22 does not infer that permission from configuration presence.
    """

    binding = resolve_model_adapter(config, model_target)
    remote = binding.current.endpoint.adapter != "deterministic"
    if remote and not network_allowed:
        raise QuestionGenerationError("question-generation network permission is denied")
    prompt = prompt_name
    if remote:
        parts = [prompt_name]
        for reference_id in session.selected_reference_ids:
            content = reference_bytes.get(reference_id)
            reference = next(item for item in session.references if item.reference_id == reference_id)
            if content is None or digest_imported_bytes(content) != reference.content_sha256:
                raise QuestionGenerationError("selected reference bytes do not match their digest")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise QuestionGenerationError("selected reference is not UTF-8 text") from exc
            parts.append(f"[{reference_id}]\n{redact_text(text, ())}")
        prompt = "\n".join(parts)
    try:
        result = binding.port.invoke(binding.request(role="proposal-questioner", prompt=prompt))
        payload = json.loads(result.output_text)
    except (AdapterFactoryError, ModelInvocationError, ValueError, json.JSONDecodeError) as exc:
        raise QuestionGenerationError(f"model question generation failed: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise QuestionGenerationError("model question response must contain questions")
    questions: list[Question] = []
    for item in payload["questions"]:
        if not isinstance(item, dict):
            raise QuestionGenerationError("model question entry must be an object")
        try:
            questions.append(
                Question(
                    str(item["question_id"]),
                    str(item["answer_type"]),
                    bool(item["required"]),
                    tuple(str(value) for value in item.get("options", [])),
                    tuple(str(value) for value in item.get("depends_on", [])),
                    str(item["rationale"]),
                    str(item["provenance"]),
                )
            )
        except (KeyError, TypeError, ValueError, ProposalInterviewError) as exc:
            raise QuestionGenerationError("model question entry is invalid") from exc
    return add_questions(session, tuple(questions))


__all__ = [
    "DETERMINISTIC_PROMPT",
    "G26_QUESTION_PROMPTS",
    "QuestionGenerationError",
    "generate_model_questions",
]
