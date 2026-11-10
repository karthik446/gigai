"""Fixture-backed adapter that never accesses credentials or networks."""

from __future__ import annotations

import json
from typing import Any

from .capabilities import require_capabilities
from .port import (
    REQUEST_SETTINGS,
    InvocationRequest,
    InvocationResult,
    NormalizedUsage,
    finish_result,
    requested_settings,
    start_clock,
)
from ..standard_pack import pack_bytes


class OfflineAdapterError(ValueError):
    code = "offline_adapter_error"


class DeterministicAdapter:
    name = "offline"
    adapter_version = "deterministic:2"
    #: Carries every 0.2 request field: it echoes them back so tests can see them.
    request_fields = frozenset(REQUEST_SETTINGS)

    def __init__(self) -> None:
        payload: Any = json.loads(pack_bytes())
        responses = payload.get("responses") if isinstance(payload, dict) else None
        if not isinstance(responses, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in responses.items()
        ):
            raise OfflineAdapterError("installed standard pack has invalid responses")
        self._responses = responses

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        clock = start_clock()
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)
        try:
            output = self._responses[request.prompt]
        except KeyError as exc:
            raise OfflineAdapterError(
                f"no deterministic response is registered for {request.prompt!r}"
            ) from exc
        settings = requested_settings(request)
        if not settings:
            result = InvocationResult(
                status="success",
                output_text=output,
                resolved_model=request.model,
                raw_usage={},
                normalized_usage=NormalizedUsage(None, None, None),
                cost_status="unavailable",
            )
        else:
            # Canned answer for the 0.2 fields: the settings echoed into raw_usage (and so into the
            # response artifact), one tool call naming the first tool, one logprob per answer, no cost.
            first_tool = request.tools[0] if request.tools else None
            tool_name = first_tool.get("name") or (first_tool.get("function") or {}).get("name") if first_tool else None
            result = InvocationResult(
                status="success",
                output_text=output,
                resolved_model=request.model,
                raw_usage={"echo": settings},
                normalized_usage=NormalizedUsage(None, None, None),
                cost_status="provider_reported",
                cost_usd=0.0,
                logprobs=({"token": output, "logprob": 0.0},) if request.logprobs else None,
                tool_calls=(
                    ({"id": "call_offline_1", "type": "function", "function": {"name": str(tool_name), "arguments": "{}"}},)
                    if first_tool is not None
                    else None
                ),
            )
        return finish_result(
            result, clock=clock, adapter_version=self.adapter_version, request_fields=self.request_fields
        )


__all__ = ["DeterministicAdapter", "OfflineAdapterError"]
