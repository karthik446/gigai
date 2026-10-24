"""OpenAI-compatible local endpoint implementation of the GigAI model port."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from ..config import CredentialReference
from .capabilities import require_capabilities
from .http import CredentialResolver, HttpModelAdapter, checked_loopback_endpoint
from .normalization import normalize_usage, string_or, usage_object
from .port import InvocationRequest, InvocationResult, ModelInvocationError


class OpenAILocalAdapter(HttpModelAdapter):
    """Adapter for loopback OpenAI-compatible servers (llama.cpp, vLLM, LM Studio...).

    The endpoint must be an explicit numeric loopback URL, and the
    OpenAI-compatible protocol exposes no model digest, so the configured
    model name alone identifies the model (config validation carries the
    bounds; the server owns selection). A key-requiring server
    (``llama.cpp --api-key``, vLLM) is supported via an optional
    credential reference, sent as ``Authorization: Bearer``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        credential: CredentialReference | None = None,
        credential_resolver: CredentialResolver | None = None,
        home_root: Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            base_url=checked_loopback_endpoint(
                base_url, "local OpenAI-compatible endpoint"
            ),
            credential=credential,
            credential_resolver=credential_resolver,
            home_root=home_root,
            client=client,
        )

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)

        payload: dict[str, object] = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_output_tokens,
        }
        body = self._post_json("/v1/chat/completions", payload)
        output = _response_text(body)
        usage = usage_object(body.get("usage"))

        return InvocationResult(
            status="success",
            output_text=output,
            resolved_model=string_or(body.get("model"), request.model),
            raw_usage=usage,
            normalized_usage=normalize_usage(usage),
            cost_status="unavailable",
        )


def _response_text(body: dict[str, Any]) -> str:
    """Extract text from an OpenAI-compatible chat-completions response."""

    choices = body.get("choices")
    if isinstance(choices, list) and len(choices) > 0:
        choice = choices[0]
        if isinstance(choice, dict):
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(choice.get("text"), str):
                return choice["text"]
    raise ModelInvocationError("local OpenAI-compatible response did not contain text content")


__all__ = ["OpenAILocalAdapter"]
