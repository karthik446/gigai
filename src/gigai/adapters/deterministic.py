"""Fixture-backed adapter that never accesses credentials or networks."""

from __future__ import annotations

import json
from typing import Any

from .capabilities import require_capabilities
from .port import InvocationRequest, InvocationResult, NormalizedUsage
from ..standard_pack import pack_bytes


class OfflineAdapterError(ValueError):
    code = "offline_adapter_error"


class DeterministicAdapter:
    name = "offline"

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
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)
        try:
            output = self._responses[request.prompt]
        except KeyError as exc:
            raise OfflineAdapterError(
                f"no deterministic response is registered for {request.prompt!r}"
            ) from exc
        return InvocationResult(
            status="success",
            output_text=output,
            resolved_model=request.model,
            raw_usage={},
            normalized_usage=NormalizedUsage(None, None, None),
            cost_status="unavailable",
        )


__all__ = ["DeterministicAdapter", "OfflineAdapterError"]
