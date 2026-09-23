"""Bounded Tailor execution through the existing public model port.

This is a caller service, not a provider adapter: the caller supplies the
already selected local target and an injected ``ModelInvocationPort``. No
networking, target discovery, fallback, journal write, or application action
is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..adapters.port import InvocationRequest, InvocationResult, ModelInvocationPort
from ..canonical import digest_imported_bytes
from .documents import validate_generated_bundle
from .tailor_selection import TailorSelection, TailorSelectionError, build_local_tailor_invocation

_MAX_RESULT_BYTES = 512 * 1024


class ScoutTailorExecutionError(ValueError):
    """Stable refusal for a failed or invalid private Tailor execution."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TailorExecutionResult:
    request: InvocationRequest
    output_bundle: bytes
    output_sha256: str
    resolved_model: str
    raw_usage: Mapping[str, object]
    validation: Mapping[str, object]


def execute_tailor(
    selection: TailorSelection,
    request_bytes: bytes,
    *,
    port: ModelInvocationPort,
    target_name: str,
    endpoint_name: str,
    model: str,
    target_capabilities: frozenset[str],
    local_allowed: bool,
    max_output_tokens: int = 1024,
) -> TailorExecutionResult:
    """Invoke one explicitly authorized local request and strictly validate its bundle."""
    # Protocols are intentionally duck-typed so injected offline transports
    # and the production port share precisely this public seam.
    if not isinstance(selection, TailorSelection) or not hasattr(port, "invoke"):
        raise ScoutTailorExecutionError("tailor_execution_invalid", "execution inputs are invalid")
    if not local_allowed:
        raise ScoutTailorExecutionError("tailor_local_denied", "explicit local permission is required")
    try:
        request = build_local_tailor_invocation(selection, request_bytes, target_name=target_name, endpoint_name=endpoint_name, model=model, target_capabilities=target_capabilities, max_output_tokens=max_output_tokens)
    except TailorSelectionError as exc:
        raise ScoutTailorExecutionError("tailor_request_invalid", "tailoring request is invalid") from exc
    try:
        result = port.invoke(request)
    except Exception as exc:
        raise ScoutTailorExecutionError("tailor_invocation_failed", "local Tailor invocation failed") from exc
    if not isinstance(result, InvocationResult):
        raise ScoutTailorExecutionError("tailor_result_invalid", "model result has invalid type")
    try:
        output = result.output_text.encode("utf-8")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ScoutTailorExecutionError("tailor_result_invalid", "model result text is invalid") from exc
    if not output or len(output) > _MAX_RESULT_BYTES:
        raise ScoutTailorExecutionError("tailor_result_invalid", "model result exceeds the fixed byte limit")
    try:
        validation = validate_generated_bundle(output, selection)
    except Exception as exc:
        raise ScoutTailorExecutionError("tailor_result_invalid", "model result is not a complete document bundle") from exc
    return TailorExecutionResult(request, output, digest_imported_bytes(output), result.resolved_model, dict(result.raw_usage), validation)


__all__ = ["ScoutTailorExecutionError", "TailorExecutionResult", "execute_tailor"]
