"""Executable, provider-free S18-01 compatibility evidence.

This is a research artifact, intentionally outside ``src/gigai``.  It validates
that the proposed common contract has an explicit entry for each candidate
family and that replay separates stable evidence from variable metadata.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping


CANDIDATE_FAMILIES = (
    "openai_api",
    "openrouter_api",
    "codex_cli",
    "claude_cli",
    "anthropic_api",
    "local_ollama",
)

COMMON_FIELDS = (
    "request",
    "identity",
    "output",
    "finish",
    "error",
    "cancellation",
    "usage",
    "cost",
    "replay",
)


def _entry(transport: str, decision: str, **fields: str) -> dict[str, Any]:
    return {
        "transport": transport,
        "decision": decision,
        **{field: value for field, value in fields.items()},
    }


CANDIDATE_MATRIX: dict[str, dict[str, Any]] = {
    "openai_api": _entry(
        "https_json_responses",
        "existing_g11_adapter; candidate common contract",
        request="input, model, max_output_tokens, optional reasoning",
        identity="response model; provider identity is extension data",
        output="output_text or output content blocks",
        finish="response status and provider finish/error details",
        error="HTTP/error envelope; pre-stream and streamed failures remain distinct",
        cancellation="HTTP cancellation is a required probe; not represented by current G11 result",
        usage="raw usage plus normalized input/output/total tokens",
        cost="unavailable in current G11 adapter; extension may preserve provider-reported cost",
        replay="JSON request/response recording with redacted headers and variable-field list",
    ),
    "openrouter_api": _entry(
        "https_json_chat_completions_or_responses",
        "existing_g11_adapter; candidate common contract",
        request="messages or input, model, max_tokens/max_output_tokens, optional reasoning",
        identity="resolved model plus upstream provider as typed extension",
        output="choice message content or Responses output items",
        finish="finish_reason or response status; mid-stream error is not HTTP status",
        error="typed error_type plus HTTP/native code; pre-stream and mid-stream differ",
        cancellation="HTTP cancellation is a required probe; provider fallback is not adopted by inference",
        usage="usage object, normalized token counts, optional provider route extension",
        cost="provider-reported cost is optional; unavailable must not become zero",
        replay="SSE or JSON recording with routing metadata redacted and fallback visible",
    ),
    "codex_cli": _entry(
        "local_process_stdio",
        "deferred_to_s18_02",
        request="argv/stdin shape, working directory, environment allowlist",
        identity="CLI version and resolved model must be captured separately",
        output="stdout structured output or explicit text fallback",
        finish="exit code plus parsed terminal event",
        error="spawn, exit, parse, timeout, and cancellation are separate outcomes",
        cancellation="process-group cancellation requires a dedicated fake-CLI probe",
        usage="unknown until CLI capture probe; never infer from text",
        cost="unknown unless emitted by CLI; default unavailable",
        replay="stdin, argv redaction, stdout/stderr, exit code, and version capture",
    ),
    "claude_cli": _entry(
        "local_process_stdio",
        "deferred_to_s18_02",
        request="argv/stdin shape, working directory, environment allowlist",
        identity="CLI version and model identity must be captured separately",
        output="stdout structured output or explicit text fallback",
        finish="exit code plus parsed terminal event",
        error="spawn, exit, parse, timeout, and cancellation are separate outcomes",
        cancellation="process-group cancellation requires a dedicated fake-CLI probe",
        usage="unknown until CLI capture probe; never infer from text",
        cost="unknown unless emitted by CLI; default unavailable",
        replay="stdin, argv redaction, stdout/stderr, exit code, and version capture",
    ),
    "anthropic_api": _entry(
        "https_json_messages",
        "deferred_to_s18_03",
        request="messages, model, max_tokens, optional system/tools",
        identity="response model plus API version and content-block kinds",
        output="text content blocks; non-text blocks remain typed extensions",
        finish="stop_reason and terminal stream event",
        error="HTTP error envelope and in-stream error event",
        cancellation="request cancellation is a required API probe",
        usage="input/output token usage; normalize without discarding raw fields",
        cost="not assumed from usage; provider pricing is outside the port",
        replay="JSON/SSE recording with request headers and content-block extensions redacted",
    ),
    "local_ollama": _entry(
        "local_http_ndjson",
        "deferred_to_s18_03",
        request="model, messages, stream, options, optional tools/format",
        identity="model name plus runtime version and options",
        output="accumulated content and optional thinking/tool-call extensions",
        finish="done and done_reason terminal fields",
        error="HTTP/NDJSON parse/runtime failure; process availability is separate",
        cancellation="HTTP cancellation and runtime shutdown require a local probe",
        usage="eval/prompt counts and durations are raw extension data; token normalization is optional",
        cost="unavailable unless an external cost policy is supplied",
        replay="NDJSON chunks, runtime/model identity, options, and timing-variable declarations",
    ),
}

REPLAY_STABLE_FIELDS = (
    "request.redacted_payload",
    "target.name",
    "target.endpoint",
    "provider.family",
    "model.identity",
    "result.status",
    "result.output",
    "result.finish",
    "result.raw_usage",
    "result.normalized_usage",
    "result.cost_status",
    "extensions.redacted",
)

REPLAY_VARIABLE_FIELDS = (
    "request_id",
    "trace_id",
    "started_at",
    "completed_at",
    "latency_ms",
    "process_id",
    "network_route_metadata",
)


def validate_matrix(matrix: Mapping[str, Mapping[str, Any]] = CANDIDATE_MATRIX) -> None:
    """Reject incomplete matrices and accidental support claims."""

    if set(matrix) != set(CANDIDATE_FAMILIES):
        raise ValueError("candidate family set changed")
    for family, entry in matrix.items():
        missing = set(COMMON_FIELDS) - set(entry)
        if missing:
            raise ValueError(f"{family} is missing common fields: {sorted(missing)}")
        if entry["decision"] == "supported":
            raise ValueError(f"{family} was advertised as supported by spike evidence")
    if set(REPLAY_STABLE_FIELDS) & set(REPLAY_VARIABLE_FIELDS):
        raise ValueError("replay stable and variable fields overlap")


def build_replay_fixture() -> dict[str, Any]:
    """Return one deterministic redacted replay record for contract tests."""

    record = {
        "request": {"redacted_payload": {"model": "fixture/model", "input": "hello"}},
        "target": {"name": "fixture", "endpoint": "local"},
        "provider": {"family": "local_ollama"},
        "model": {"identity": "fixture/model@runtime-1"},
        "result": {
            "status": "completed",
            "output": "hello back",
            "finish": "stop",
            "raw_usage": {"eval_count": 3},
            "normalized_usage": {"input_tokens": None, "output_tokens": 3, "total_tokens": None},
            "cost_status": "unavailable",
        },
        "extensions": {"redacted": {}},
        "variable": {field: "redacted-or-runtime" for field in REPLAY_VARIABLE_FIELDS},
    }
    record["stable_digest"] = sha256(
        json.dumps(
            {"stable": {field: _get_path(record, field) for field in REPLAY_STABLE_FIELDS}},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return record


def _get_path(record: Mapping[str, Any], path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        current = current[part]
    return current


__all__ = [
    "CANDIDATE_FAMILIES",
    "CANDIDATE_MATRIX",
    "COMMON_FIELDS",
    "REPLAY_STABLE_FIELDS",
    "REPLAY_VARIABLE_FIELDS",
    "build_replay_fixture",
    "validate_matrix",
]
