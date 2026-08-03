"""Fixture-backed adapter that never accesses credentials or networks."""

from __future__ import annotations

import json
from typing import Any

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

    def invoke(self, prompt: str) -> str:
        if not isinstance(prompt, str):
            raise TypeError("deterministic adapter prompt must be a string")
        try:
            return self._responses[prompt]
        except KeyError as exc:
            raise OfflineAdapterError(
                f"no deterministic response is registered for {prompt!r}"
            ) from exc


__all__ = ["DeterministicAdapter", "OfflineAdapterError"]
