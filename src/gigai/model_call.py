"""Bounded, cancel-aware invocation helper for model-backed workflows."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from .adapters.port import InvocationRequest, InvocationResult, ModelInvocationPort


@dataclass(frozen=True)
class BoundedCallError(RuntimeError):
    """A model call stopped at a deterministic G26 boundary."""

    reason: str
    detail: str

    def __str__(self) -> str:
        return self.detail


def invoke_bounded(
    port: ModelInvocationPort,
    request: InvocationRequest,
    *,
    max_wall_time_ms: int,
    max_output_tokens: int,
    cancel_event: threading.Event | None = None,
) -> InvocationResult:
    """Invoke one model call with a hard caller-side wait boundary.

    The adapter call runs on a daemon worker because the transport protocol
    predates cancellation. A timeout/cancellation returns fail-closed without
    waiting for or reusing that result; the caller never writes a draft from a
    late response.
    """

    if max_wall_time_ms <= 0 or max_output_tokens <= 0:
        raise BoundedCallError("budget_exhausted", "builder call budget is non-positive")
    request_output_tokens = getattr(request, "max_output_tokens", max_output_tokens)
    if request_output_tokens > max_output_tokens:
        raise BoundedCallError(
            "budget_exhausted",
            "selected model output limit exceeds the builder token budget",
        )
    if cancel_event is not None and cancel_event.is_set():
        raise BoundedCallError("cancelled", "builder call was cancelled before invocation")
    result: list[InvocationResult] = []
    failure: list[BaseException] = []
    done = threading.Event()

    def worker() -> None:
        try:
            result.append(port.invoke(request))
        except BaseException as exc:  # transport exceptions are normalized by callers
            failure.append(exc)
        finally:
            done.set()

    threading.Thread(target=worker, name="gigai-builder-call", daemon=True).start()
    deadline = time.monotonic() + max_wall_time_ms / 1000
    while not done.is_set():
        if cancel_event is not None and cancel_event.is_set():
            raise BoundedCallError("cancelled", "builder call was cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoundedCallError("timed_out", "builder call exceeded its wall-time budget")
        done.wait(min(remaining, 0.05))
    if failure:
        raise failure[0]
    if not result:
        raise BoundedCallError("failed", "builder call returned no result")
    return result[0]


__all__ = ["BoundedCallError", "invoke_bounded"]
