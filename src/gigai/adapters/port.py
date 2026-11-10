"""Transport-neutral model invocation types owned by the GigAI domain."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import math
import time
from typing import Callable, Mapping, Protocol, Sequence, TypeVar

from ..roles import RoleReference, resolve_role

_T = TypeVar("_T")

#: The request fields added in 0.2 (V16), in recording order. Each defaults to ``None``: unset means
#: "as before", so an old caller's request, argv, payload and request artifact are unchanged.
REQUEST_SETTINGS = (
    "temperature",
    "top_p",
    "seed",
    "output_mode",
    "schema",
    "system",
    "retry_policy",
    "metadata",
    "messages",
    "logprobs",
    "top_logprobs",
    "tools",
    "tool_choice",
)
OUTPUT_MODES = frozenset({"text", "json", "schema"})
#: ``metadata`` is gigai's own (step id, repeat index, ...): every adapter carries it, none sends it.
_ALWAYS_CARRIED = frozenset({"metadata"})
#: Asked of an adapter that cannot carry them, these fail the call before any model call; the
#: sampling knobs are only recorded as ``raw_usage["unsupported"]`` and the prompt runs unchanged.
_REFUSED_WHEN_UNSUPPORTED = frozenset({"messages", "logprobs", "top_logprobs", "tools", "tool_choice"})
#: What a text-only (CLI) adapter carries: ``system`` and the JSON modes go into the prompt text.
TEXT_ONLY_REQUEST_FIELDS = frozenset({"output_mode", "schema", "system"})
_MESSAGE_ROLES = frozenset({"system", "user", "assistant"})
_JSON_SUFFIX = "Answer with one JSON value only, no other text."
_SCHEMA_SUFFIX = "Answer with one JSON value only, no other text, matching this JSON Schema:\n"


class ModelInvocationError(RuntimeError):
    """An adapter could not complete one requested model invocation."""

    code = "model_invocation_failed"


class ModelAuthenticationRequired(ModelInvocationError):
    """The provider transport works but its owned authentication is missing."""

    code = "model_authentication_required"


class CapabilityMismatchError(ModelInvocationError):
    code = "model_capability_mismatch"


class ModelInvocationCancelled(ModelInvocationError):
    """The caller cancelled an invocation before a successful result."""

    code = "model_invocation_cancelled"


class UnsupportedRequestFieldError(ModelInvocationError):
    """The adapter cannot carry a requested field, so it refused before any model call."""

    code = "model_request_field_unsupported"

    def __init__(self, adapter_name: str, fields: Sequence[str]) -> None:
        self.fields = tuple(fields)
        super().__init__(f"{adapter_name} does not support {', '.join(self.fields)}")


class ModelOutputNotJSONError(ModelInvocationError):
    """``output_mode`` asked for JSON and the answer does not parse as JSON."""

    code = "model_output_not_json"


@dataclass(frozen=True)
class RetryPolicy:
    """How often an API adapter tries one call, waiting ``backoff_seconds`` (doubling) between tries."""

    attempts: int = 1
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if type(self.attempts) is not int or not 1 <= self.attempts <= 10:
            raise ValueError("retry_policy attempts must be an integer from 1 to 10")
        if not _is_number(self.backoff_seconds) or not 0 <= self.backoff_seconds <= 60:
            raise ValueError("retry_policy backoff_seconds must be from 0 to 60")


@dataclass(frozen=True)
class InvocationRequest:
    """One configured target invocation, independent of its transport."""

    target_name: str
    endpoint_name: str
    model: str
    role: str
    prompt: str
    target_capabilities: frozenset[str]
    required_capabilities: frozenset[str] = frozenset({"text"})
    max_output_tokens: int = 64
    reasoning_effort: str | None = None
    #: Overrides a CLI adapter's own default (``process.py``'s ``timeout_seconds``) for this one call; ``None`` keeps it.
    timeout_seconds: float | None = None
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    output_mode: str | None = None
    #: A JSON Schema object; required when ``output_mode`` is ``"schema"``.
    schema: Mapping[str, object] | None = None
    system: str | None = None
    retry_policy: RetryPolicy | None = None
    metadata: Mapping[str, object] | None = None
    #: Turns sent before ``prompt``, which stays the last user turn.
    messages: Sequence[Mapping[str, str]] | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = None
    tools: Sequence[Mapping[str, object]] | None = None
    tool_choice: str | Mapping[str, object] | None = None

    @property
    def role_reference(self) -> RoleReference | None:
        """Return the registered role when legacy decoding can prove it."""

        return resolve_role(self.role, namespace="model_invocation").reference

    def __post_init__(self) -> None:
        if not self.target_name or not self.endpoint_name or not self.model or not self.role:
            raise ValueError("invocation target, endpoint, model, and role must be non-empty")
        if not self.prompt or "\0" in self.prompt:
            raise ValueError("invocation prompt must be non-empty and NUL-free")
        if not self.required_capabilities:
            raise ValueError("invocation requires at least one capability")
        if not self.target_capabilities:
            raise ValueError("invocation target must declare at least one capability")
        missing = self.required_capabilities - self.target_capabilities
        if missing:
            raise ValueError(
                f"invocation requirements exceed target capability policy: {sorted(missing)}"
            )
        if self.max_output_tokens <= 0:
            raise ValueError("invocation max_output_tokens must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("invocation timeout_seconds must be positive")
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ValueError("invocation reasoning_effort is unsupported")
        self._check_settings()

    def _check_settings(self) -> None:
        if self.temperature is not None and (not _is_number(self.temperature) or not 0 <= self.temperature <= 2):
            raise ValueError("invocation temperature must be from 0 to 2")
        if self.top_p is not None and (not _is_number(self.top_p) or not 0 < self.top_p <= 1):
            raise ValueError("invocation top_p must be above 0 and at most 1")
        if self.seed is not None and type(self.seed) is not int:
            raise ValueError("invocation seed must be an integer")
        if self.output_mode is not None and self.output_mode not in OUTPUT_MODES:
            raise ValueError("invocation output_mode must be text, json or schema")
        if (self.output_mode == "schema") != (self.schema is not None):
            raise ValueError("invocation schema is required exactly when output_mode is schema")
        if self.schema is not None and not isinstance(self.schema, Mapping):
            raise ValueError("invocation schema must be a JSON Schema object")
        if self.system is not None and (not isinstance(self.system, str) or not self.system or "\0" in self.system):
            raise ValueError("invocation system must be non-empty and NUL-free")
        if self.retry_policy is not None and not isinstance(self.retry_policy, RetryPolicy):
            raise ValueError("invocation retry_policy must be a RetryPolicy")
        if self.metadata is not None and not isinstance(self.metadata, Mapping):
            raise ValueError("invocation metadata must be a mapping")
        if self.messages is not None and (
            not isinstance(self.messages, (list, tuple))
            or not all(
                isinstance(turn, Mapping)
                and turn.get("role") in _MESSAGE_ROLES
                and isinstance(turn.get("content"), str)
                and turn["content"]
                and "\0" not in turn["content"]
                for turn in self.messages
            )
        ):
            raise ValueError("invocation messages must be system/user/assistant turns with text content")
        if self.logprobs is not None and type(self.logprobs) is not bool:
            raise ValueError("invocation logprobs must be a boolean")
        if self.top_logprobs is not None and (
            type(self.top_logprobs) is not int or not 0 <= self.top_logprobs <= 20 or self.logprobs is not True
        ):
            raise ValueError("invocation top_logprobs must be 0 to 20 and needs logprobs=True")
        if self.tools is not None and (
            not isinstance(self.tools, (list, tuple)) or not self.tools or not all(isinstance(tool, Mapping) for tool in self.tools)
        ):
            raise ValueError("invocation tools must be a non-empty list of tool definitions")
        if self.tool_choice is not None and self.tools is None:
            raise ValueError("invocation tool_choice needs tools")


@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class InvocationResult:
    """Normalized result plus unmodified provider usage, never provider secrets."""

    status: str
    output_text: str
    resolved_model: str
    raw_usage: Mapping[str, object]
    normalized_usage: NormalizedUsage
    cost_status: str
    #: What the provider itself said this call cost, in US dollars; ``None`` when it reported none.
    cost_usd: float | None = None
    latency_ms: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    #: The adapter's contract version (``<adapter>:2`` since the 0.2 port widening).
    adapter_version: str | None = None
    retry_count: int | None = None
    last_error_class: str | None = None
    #: The request fields this adapter carries (V12 and BK2 refuse an axis or step it does not list).
    capabilities: frozenset[str] = frozenset()
    logprobs: Sequence[Mapping[str, object]] | None = None
    tool_calls: Sequence[Mapping[str, object]] | None = None

    def __post_init__(self) -> None:
        if self.status != "success":
            raise ValueError("successful model invocation results use status='success'")
        if not self.output_text and not self.tool_calls:
            raise ValueError("successful model invocation result must contain output text or tool calls")
        if not self.resolved_model:
            raise ValueError("successful model invocation result needs resolved_model")
        if self.cost_status not in {"provider_reported", "derived", "unavailable"}:
            raise ValueError("invalid model invocation cost status")
        if self.cost_usd is not None and (
            type(self.cost_usd) not in (int, float) or not 0 <= self.cost_usd < float("inf")
        ):
            raise ValueError("model invocation cost must be a non-negative number")
        if self.latency_ms is not None and (type(self.latency_ms) is not int or self.latency_ms < 0):
            raise ValueError("model invocation latency_ms must be a non-negative integer")


class ModelInvocationPort(Protocol):
    """The sole domain-facing model invocation interface."""

    def invoke(self, request: InvocationRequest) -> InvocationResult: ...


def requested_settings(request: InvocationRequest) -> dict[str, object]:
    """The 0.2 request fields this request sets, in ``REQUEST_SETTINGS`` order."""

    return {name: getattr(request, name) for name in REQUEST_SETTINGS if getattr(request, name) is not None}


def unsupported_settings(
    request: InvocationRequest, adapter_name: str, supported: frozenset[str]
) -> tuple[str, ...]:
    """Raise for a set field the adapter must refuse; return the set fields it only records as unsupported."""

    missing = [name for name in requested_settings(request) if name not in supported | _ALWAYS_CARRIED]
    refused = [name for name in missing if name in _REFUSED_WHEN_UNSUPPORTED]
    if refused:
        raise UnsupportedRequestFieldError(adapter_name, refused)
    return tuple(missing)


def start_clock() -> tuple[str, float]:
    """The wall-clock start (ISO, UTC) and the monotonic start of one call."""

    return _utc_now(), time.perf_counter()


def finish_result(
    result: InvocationResult,
    *,
    clock: tuple[str, float],
    adapter_version: str,
    request_fields: frozenset[str],
    unsupported: Sequence[str] = (),
    retry_count: int | None = None,
    last_error_class: str | None = None,
) -> InvocationResult:
    """Stamp timing, version, capabilities and retry facts; record ``unsupported`` in ``raw_usage`` when any."""

    started_at, started = clock
    latency_ms = max(1, math.ceil((time.perf_counter() - started) * 1000))
    raw_usage = {**result.raw_usage, "unsupported": list(unsupported)} if unsupported else result.raw_usage
    return replace(
        result,
        raw_usage=raw_usage,
        latency_ms=latency_ms,
        started_at=started_at,
        completed_at=_utc_now(),
        adapter_version=adapter_version,
        capabilities=request_fields | _ALWAYS_CARRIED,
        retry_count=retry_count,
        last_error_class=last_error_class,
    )


def call_with_retry(policy: RetryPolicy | None, call: Callable[[], _T]) -> tuple[_T, int, str | None]:
    """Run ``call`` up to ``policy.attempts`` times; return its value, the retries used, the last error class."""

    attempts = policy.attempts if policy is not None else 1
    last_error: str | None = None
    for attempt in range(attempts):
        if attempt and policy is not None:
            time.sleep(policy.backoff_seconds * 2 ** (attempt - 1))
        try:
            return call(), attempt, last_error
        except (ModelInvocationCancelled, ModelAuthenticationRequired):
            raise
        except ModelInvocationError as exc:
            last_error = type(exc).__name__
            if attempt + 1 == attempts:
                raise
    raise AssertionError("unreachable: attempts is at least 1")


def text_only_prompt(request: InvocationRequest) -> str:
    """The prompt a text-only (CLI) adapter sends: ``system``, the prompt, then the JSON-mode instruction."""

    parts = [request.system, request.prompt] if request.system is not None else [request.prompt]
    if request.output_mode == "json":
        parts.append(_JSON_SUFFIX)
    elif request.output_mode == "schema":
        parts.append(_SCHEMA_SUFFIX + json.dumps(request.schema, sort_keys=True))
    return "\n\n".join(parts)


def check_json_output(request: InvocationRequest, text: str) -> None:
    """Raise when ``output_mode`` asked for JSON and ``text`` (one optional code fence) does not parse."""

    if request.output_mode not in {"json", "schema"}:
        return
    body = text.strip()
    if body.startswith("```") and body.endswith("```"):
        body = body[3:-3].removeprefix("json").strip()
    try:
        json.loads(body)
    except ValueError as exc:
        raise ModelOutputNotJSONError("model output is not JSON") from exc


def _is_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)  # type: ignore[arg-type]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "OUTPUT_MODES",
    "REQUEST_SETTINGS",
    "TEXT_ONLY_REQUEST_FIELDS",
    "CapabilityMismatchError",
    "InvocationRequest",
    "InvocationResult",
    "ModelInvocationError",
    "ModelAuthenticationRequired",
    "ModelInvocationCancelled",
    "ModelInvocationPort",
    "ModelOutputNotJSONError",
    "NormalizedUsage",
    "RetryPolicy",
    "UnsupportedRequestFieldError",
    "call_with_retry",
    "check_json_output",
    "finish_result",
    "requested_settings",
    "start_clock",
    "text_only_prompt",
    "unsupported_settings",
]
