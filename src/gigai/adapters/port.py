"""Transport-neutral model invocation types owned by the GigAI domain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class ModelInvocationError(RuntimeError):
    """An adapter could not complete one requested model invocation."""

    code = "model_invocation_failed"


class CapabilityMismatchError(ModelInvocationError):
    code = "model_capability_mismatch"


class ModelInvocationCancelled(ModelInvocationError):
    """The caller cancelled an invocation before a successful result."""

    code = "model_invocation_cancelled"


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
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise ValueError("invocation reasoning_effort is unsupported")


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

    def __post_init__(self) -> None:
        if self.status != "success":
            raise ValueError("successful model invocation results use status='success'")
        if not self.output_text:
            raise ValueError("successful model invocation result must contain output text")
        if not self.resolved_model:
            raise ValueError("successful model invocation result needs resolved_model")
        if self.cost_status not in {"provider_reported", "derived", "unavailable"}:
            raise ValueError("invalid model invocation cost status")


class ModelInvocationPort(Protocol):
    """The sole domain-facing model invocation interface."""

    def invoke(self, request: InvocationRequest) -> InvocationResult: ...


__all__ = [
    "CapabilityMismatchError",
    "InvocationRequest",
    "InvocationResult",
    "ModelInvocationError",
    "ModelInvocationCancelled",
    "ModelInvocationPort",
    "NormalizedUsage",
]
