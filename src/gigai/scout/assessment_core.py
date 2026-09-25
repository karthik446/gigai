"""The shared find-jobs assessment core: one prompt, one model call, one parse.

P1 (v0.1.9 API-first plan) lifts the per-posting model loop out of
``proposal_execution._assess_node_body`` so a later standalone assess (API/CLI,
no run) can call exactly the same code the graph's assess node uses:

    prompt build -> model invoke -> extract JSON -> normalize -> validate ->
    at most ONE retry with the validation error fed back (U22)

``assess_node`` keeps everything around that loop -- candidate selection,
reuse/skip, sealing, journaling and progress -- and this module never touches
any of it.  Two seams stay in the caller by design (plan constraints C1/C2):

- the model adapter is resolved by ``proposal_execution`` through its own
  module attribute ``resolve_model_adapter`` (the production test transport
  and the unit tests patch that exact name); this module only ever receives
  the already-resolved binding;
- the strict parser (``proposals.parse_assessment_proposal``, which
  ``bindings._assess_bound`` swaps at call time) is passed in as ``parse=``,
  never imported here.

The prompt text lives in the packaged resource
``scout/data/instructions/assess.md`` (shipped via ``pyproject.toml``
package-data) as a paragraph template.  ``render_assess_prompt`` splits the
template on blank lines BEFORE substitution, drops the paragraph that carries
``{{validation_error}}`` when there is no error to feed back, substitutes each
``{{name}}`` placeholder in a single pass (substituted text is never rescanned),
and re-joins with blank lines -- byte-identical to the pre-P1 hand-built prompt
for the same inputs (``tests/behaviors/scout_find_jobs/test_assessment_core.py``
pins the golden strings).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import resources
import json
import re

from ..adapters.port import ModelInvocationError, NormalizedUsage
from ..canonical import digest_imported_bytes
from .find_jobs.contracts import FindJobsContractError, NotAssessedReason

_INSTRUCTIONS_RESOURCE = "scout/data/instructions/assess.md"
_ROLE = "reviewer"

_MAX_PROMPT_POSTING_TEXT = 12_000
_MAX_PROMPT_RESUME_TEXT = 12_000
_MAX_PROMPT_VALIDATION_ERROR = 300

_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_VALIDATION_PLACEHOLDER = "{{validation_error}}"

# Exception mapping at the model boundary, exactly as the pre-P1 loop had it:
# a transport/adapter failure whose code is one of these is the operator's own
# policy refusing the call (MODEL_DENIED); every other transport failure is
# MODEL_UNAVAILABLE.  An unknown exception type is only mapped when it carries
# one of the three known codes; otherwise it is re-raised untouched.
_DENIED_INVOCATION_CODES = frozenset({"network_denied", "model_denied", "credential_denied"})
_MAPPED_FOREIGN_CODES = frozenset({"model_denied", "network_denied", "model_unavailable"})


def _instruction_bytes() -> bytes:
    return resources.files("gigai").joinpath(*_INSTRUCTIONS_RESOURCE.split("/")).read_bytes()


def load_assess_instructions() -> str:
    """The packaged assess prompt template, minus the file's single trailing newline."""

    text = _instruction_bytes().decode("utf-8")
    if text.endswith("\n"):
        text = text[:-1]
    return text


INSTRUCTIONS_DIGEST = digest_imported_bytes(_instruction_bytes())
"""Digest of the shipped ``assess.md`` bytes (``digest_imported_bytes``); changes only with the file."""


@dataclass(frozen=True)
class AssessJob:
    """What the prompt needs to know about one posting -- nothing sealed."""

    title: str
    company: str
    location: str
    posting_text: str


@dataclass(frozen=True)
class AssessContext:
    """The candidate side of one assessment."""

    resume_text: str
    visa_sponsorship_required: bool


@dataclass(frozen=True)
class AssessAttempt:
    """The outcome of ``assess_once``: one parsed result, or one reason it has none.

    ``attempts`` counts model invocations that RETURNED an answer (the
    pre-P1 loop's ``model_attempts``); a transport failure before any answer
    leaves it at 0.  ``validation_error`` is the last parse/validation error
    seen, if any (present on a successful retry too).
    """

    ok: bool
    parsed: object | None
    not_assessed_reason: NotAssessedReason | None
    usage: NormalizedUsage | None
    attempts: int
    validation_error: str | None

    def __post_init__(self) -> None:
        if self.ok and (self.parsed is None or self.not_assessed_reason is not None):
            raise ValueError("a successful assess attempt carries a parsed result and no reason")
        if not self.ok and self.not_assessed_reason is None:
            raise ValueError("a failed assess attempt must name its not-assessed reason")


def render_assess_prompt(job: AssessJob, ctx: AssessContext, validation_error: str | None = None) -> str:
    """Render the real find-jobs assessment prompt (U25) from the packaged template.

    Includes the role/title/company/location, the bounded posting text, the
    resume text, the candidate's sponsorship constraint, and a precise JSON
    schema with a short worked example so the model returns a shape that
    parses on the first try.  On a retry (U22), the prior validation error is
    fed back so the model can correct its own output.
    """

    values = {
        "title": job.title,
        "company": job.company,
        "location": job.location or "unspecified",
        "visa_required": "yes" if ctx.visa_sponsorship_required else "no",
        "posting_text": job.posting_text[:_MAX_PROMPT_POSTING_TEXT],
        "resume_text": ctx.resume_text[:_MAX_PROMPT_RESUME_TEXT],
        "validation_error": (validation_error or "")[:_MAX_PROMPT_VALIDATION_ERROR],
    }
    blocks = load_assess_instructions().split("\n\n")
    if not validation_error:
        blocks = [block for block in blocks if _VALIDATION_PLACEHOLDER not in block]

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"assess instructions use an unknown placeholder {{{{{key}}}}}")
        return values[key]

    return "\n\n".join(_PLACEHOLDER.sub(fill, block) for block in blocks)


def assess_once(
    binding: object,
    job: AssessJob,
    ctx: AssessContext,
    *,
    parse: Callable[[dict[str, object]], object],
) -> AssessAttempt:
    """Assess one posting against one resume: invoke, extract, normalize, validate, retry once.

    ``binding`` is an already-resolved ``ModelAdapterBinding`` (``request`` +
    ``port.invoke``); ``parse`` is the strict contract parser applied to the
    normalized payload (the caller adds whatever sealed identity its DTO
    needs before validating).  Never raises for a model-boundary failure it
    can name; re-raises anything it cannot.
    """

    validation_error: str | None = None
    attempts = 0
    for attempt in range(2):
        prompt = render_assess_prompt(job, ctx, validation_error)
        try:
            request = binding.request(role=_ROLE, prompt=prompt)
            result = binding.port.invoke(request)
            attempts += 1
        except (ModelInvocationError, OSError, TimeoutError) as exc:
            reason = (
                NotAssessedReason.MODEL_DENIED
                if getattr(exc, "code", "") in _DENIED_INVOCATION_CODES
                else NotAssessedReason.MODEL_UNAVAILABLE
            )
            return AssessAttempt(False, None, reason, None, attempts, validation_error)
        except Exception as exc:
            code = getattr(exc, "code", "")
            if code in _MAPPED_FOREIGN_CODES:
                reason = (
                    NotAssessedReason.MODEL_DENIED
                    if code == "model_denied"
                    else NotAssessedReason.MODEL_UNAVAILABLE
                )
                return AssessAttempt(False, None, reason, None, attempts, validation_error)
            raise
        try:
            decoded = _extract_json_object(result.output_text)
            normalized = _normalize_assessment_payload(decoded)
            parsed = parse(normalized)
        except (FindJobsContractError, ValueError, TypeError) as exc:
            validation_error = str(exc)
            if attempt == 0:
                # U22: one retry, with the validation error fed back so the
                # model can correct its own shape, before giving up on this
                # single posting.
                continue
            return AssessAttempt(
                False, None, NotAssessedReason.MODEL_OUTPUT_INVALID, None, attempts, validation_error
            )
        return AssessAttempt(True, parsed, None, result.normalized_usage, attempts, validation_error)
    raise AssertionError("assess_once: the retry loop always returns")  # pragma: no cover


def _extract_json_object(raw: object) -> Mapping[str, object]:
    """Extract one JSON object from model output that may be fenced/prose-wrapped.

    Tolerant boundary parsing (U22), applied before normalization and before
    strict contract validation: models sometimes wrap JSON in ``` fences or
    prepend/append prose. This never relaxes the frozen contract itself —
    ``parse_assessment_proposal`` still validates strictly after normalization.
    """
    if isinstance(raw, Mapping):
        return raw
    if not isinstance(raw, str):
        raise ValueError("assessment output is not text or an object")
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("assessment output contains no JSON object") from None
        decoded = json.loads(text[start : end + 1])
    if not isinstance(decoded, Mapping):
        raise ValueError("assessment output is not a JSON object")
    return decoded


_STATUS_SYNONYMS = {
    "met": "met",
    "meets": "met",
    "meet": "met",
    "yes": "met",
    "full": "met",
    "partial": "partial",
    "partially": "partial",
    "partly": "partial",
    "some": "partial",
    "gap": "gap",
    "missing": "gap",
    "no": "gap",
    "none": "gap",
    "not_met": "gap",
    "not met": "gap",
}

_SPONSORSHIP_SYNONYMS = {
    "offered": "offered",
    "offer": "offered",
    "yes": "offered",
    "available": "offered",
    "not_offered": "not_offered",
    "not offered": "not_offered",
    "no": "not_offered",
    "unavailable": "not_offered",
    "unknown": "unknown",
    "unclear": "unknown",
    "n/a": "unknown",
    "na": "unknown",
}


def _normalize_string_list(value: object) -> list[object]:
    """A single string coerces to a one-item list; ``null``/missing to ``[]``."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return list(value)
    return [value]


def _normalize_status(value: object) -> object:
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    return _STATUS_SYNONYMS.get(key, value)


def _normalize_sponsorship(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    return _SPONSORSHIP_SYNONYMS.get(key, value)


def _normalize_assessment_payload(decoded: Mapping[str, object]) -> dict[str, object]:
    """Tolerant normalization at the model boundary, BEFORE strict validation (U22).

    - extracts already happened in ``_extract_json_object``
    - ``resume_evidence`` as a bare string becomes ``[string]``; ``null``/missing becomes ``[]``
    - matrix ``status`` synonyms (yes/partially/no, meets/partial/missing, any case) map to met/partial/gap
    - ``suggestions``/``questions`` as a bare string become ``[string]``; ``null``/missing become ``[]``
    - unknown top-level keys are dropped so the frozen contract's closed-object check still applies cleanly
    - ``sponsorship`` synonyms map to offered/not_offered/unknown; absent stays absent

    This never loosens the frozen contract itself: ``parse_assessment_proposal``
    still runs strict validation immediately after this step.
    """
    matrix = decoded.get("matrix")
    normalized_matrix: list[object] = []
    if isinstance(matrix, list):
        for row in matrix:
            if not isinstance(row, Mapping):
                normalized_matrix.append(row)
                continue
            normalized_row: dict[str, object] = {
                "requirement": row.get("requirement"),
                "resume_evidence": [
                    item for item in _normalize_string_list(row.get("resume_evidence")) if isinstance(item, str)
                ],
                "status": _normalize_status(row.get("status")),
            }
            normalized_matrix.append(normalized_row)
    else:
        normalized_matrix = matrix

    result: dict[str, object] = {
        "matrix": normalized_matrix,
        "suggestions": [item for item in _normalize_string_list(decoded.get("suggestions")) if isinstance(item, str)],
        "questions": [item for item in _normalize_string_list(decoded.get("questions")) if isinstance(item, str)],
    }
    if "sponsorship" in decoded:
        sponsorship = _normalize_sponsorship(decoded.get("sponsorship"))
        if sponsorship is not None:
            result["sponsorship"] = sponsorship
    # Drop any other unknown keys (e.g. a model echoing "posting" back, or
    # inventing extra fields): the frozen contract is a closed object, and
    # normalization's job is to fix shape, not to smuggle new keys through.
    return result


__all__ = [
    "AssessAttempt",
    "AssessContext",
    "AssessJob",
    "INSTRUCTIONS_DIGEST",
    "assess_once",
    "load_assess_instructions",
    "render_assess_prompt",
]
