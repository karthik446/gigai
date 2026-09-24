"""Likely question categories, grounded in posting + resume + company research.

Coordinator default (S18 open question #2): category-level ``choice``
prediction only (behavioural / system_design / coding_in_their_stack /
domain), each with a one-line "why" tied to the posting/resume/company
research -- no per-question probabilities, no ``noul``/``score`` primitives
(deferred to a later slice per S18 Sec.5).

Model resolution reuses ``find-jobs.json``'s ``default_model_target`` ->
the same adapter-kind resolution ``assess`` uses
(``_resolve_configured_target_name_for_adapter`` +
``resolve_model_adapter``, both imported from ``proposal_execution.py``,
Scout-internal) -- "no silent provider fallback" per the packet: an
unconfigured/disabled/ambiguous target fails loudly, naming the fix,
exactly as assess's own resolution does.

Privacy: the resume text is included in this prompt (the model call, not
the web-search call) -- matching assess's own privacy line: resume goes
only to the configured assess-equivalent model, never to web search.
"""

from __future__ import annotations

import json
import re
from typing import Mapping

from ...adapters.factory import AdapterFactoryError, resolve_model_adapter
from ...adapters.port import ModelInvocationError
from ...config import GigAIConfig
from ...model_targets import ModelTargetResolutionError
from ..proposal_execution import _resolve_configured_target_name_for_adapter, ScoutProposalExecutionError
from .types import QUESTION_CATEGORIES, QuestionCategoryPrediction

_MAX_PROMPT_TEXT = 12_000


class CategoryPredictionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _prompt(*, title: str, company: str, posting_text: str, resume_text: str, company_claims: tuple[str, ...]) -> str:
    schema = (
        "Return JSON only (no prose, no markdown fences) matching exactly this shape:\n"
        '{"categories": [{"category": "<one of: behavioural, system_design, coding_in_their_stack, domain>", '
        '"why": "<one sentence tying this category to the posting/resume/company research>", '
        '"grounded_in": ["<short quote or paraphrase from the posting, resume, or company research this claim is based on>"]}]}\n'
        "Return between 1 and 4 categories, each used at most once, ordered most-likely first."
    )
    company_block = (
        "COMPANY RESEARCH:\n" + "\n".join(f"- {claim}" for claim in company_claims)
        if company_claims else "COMPANY RESEARCH: none available."
    )
    parts = [
        "You are predicting likely interview question CATEGORIES (not specific questions) for GigAI Scout.",
        schema,
        f"ROLE: {title}\nCOMPANY: {company}",
        "POSTING TEXT (may be truncated):\n" + posting_text[:_MAX_PROMPT_TEXT],
        "CANDIDATE RESUME (may be truncated):\n" + resume_text[:_MAX_PROMPT_TEXT],
        company_block,
        "Ground every category's \"why\" and \"grounded_in\" in the posting, resume, or company research above -- "
        "never invent a claim not supported by one of those three sources.",
    ]
    return "\n\n".join(parts)


def _extract_json_object(raw: str) -> Mapping[str, object]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise CategoryPredictionError("category_prediction_unparsable", "model output contains no JSON object") from None
        decoded = json.loads(text[start : end + 1])
    if not isinstance(decoded, Mapping):
        raise CategoryPredictionError("category_prediction_unparsable", "model output is not a JSON object")
    return decoded


def _parse_categories(decoded: Mapping[str, object]) -> tuple[QuestionCategoryPrediction, ...]:
    raw = decoded.get("categories")
    if not isinstance(raw, list):
        raise CategoryPredictionError("category_prediction_invalid", "model output has no categories list")
    result = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        category = item.get("category")
        why = item.get("why")
        if category not in QUESTION_CATEGORIES or not isinstance(why, str) or not why.strip():
            continue
        grounded_in = item.get("grounded_in")
        grounded = tuple(str(g) for g in grounded_in) if isinstance(grounded_in, list) else ()
        result.append(QuestionCategoryPrediction(category=str(category), why=why, grounded_in=grounded))
    if not result:
        raise CategoryPredictionError("category_prediction_invalid", "model output named no valid category")
    return tuple(result)


def predict_categories(
    *,
    config: GigAIConfig,
    model_target: str,
    title: str,
    company: str,
    posting_text: str,
    resume_text: str,
    company_claims: tuple[str, ...],
) -> tuple[tuple[QuestionCategoryPrediction, ...], str]:
    """Predict question categories; returns (predictions, resolved_target_name).

    Raises :class:`CategoryPredictionError` for a missing/ambiguous/disabled
    target, a denied/unavailable model, or output that doesn't parse -- never
    silently falls back to another provider or returns an empty prediction.
    """

    if model_target not in {"ollama_local", "codex_cli", "openrouter_api"}:
        raise CategoryPredictionError("category_model_target_invalid", f"unsupported model target {model_target!r}")
    try:
        adapter_target = _resolve_configured_target_name_for_adapter(config, model_target)
        binding = resolve_model_adapter(config, adapter_target)
    except (AdapterFactoryError, ModelTargetResolutionError, ScoutProposalExecutionError) as exc:
        raise CategoryPredictionError("category_model_unavailable", str(exc)) from exc
    try:
        prompt = _prompt(title=title, company=company, posting_text=posting_text, resume_text=resume_text, company_claims=company_claims)
        request = binding.request(role="reviewer", prompt=prompt)
        result = binding.port.invoke(request)
    except ModelInvocationError as exc:
        raise CategoryPredictionError(getattr(exc, "code", "category_model_invocation_failed"), str(exc)) from exc
    finally:
        binding.close()
    decoded = _extract_json_object(result.output_text)
    categories = _parse_categories(decoded)
    return categories, adapter_target


__all__ = ["CategoryPredictionError", "predict_categories"]
