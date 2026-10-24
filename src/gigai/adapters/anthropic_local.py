"""Anthropic-compatible local endpoint implementation of the GigAI model port."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from ..config import CredentialReference
from .capabilities import require_capabilities
from .http import CredentialResolver, HttpModelAdapter, checked_loopback_endpoint
from .normalization import normalize_usage, string_or, usage_object
from .port import InvocationRequest, InvocationResult, ModelInvocationError


class AnthropicLocalAdapter(HttpModelAdapter):
    """Adapter for loopback Anthropic-compatible servers (claude-code-router, LM Studio...).

    The endpoint must be an explicit numeric loopback URL, and the
    Anthropic-compatible protocol exposes no model digest, so the
    configured model name alone identifies the model (config validation
    carries the bounds; the server owns selection). A key-requiring
    server is supported via an optional credential reference, sent under
    the dialect's ``x-api-key`` + ``anthropic-version`` headers.
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
                base_url, "local Anthropic-compatible endpoint"
            ),
            credential=credential,
            credential_resolver=credential_resolver,
            home_root=home_root,
            client=client,
            auth_style="x_api_key",
        )

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)

        payload: dict[str, object] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        body = self._post_json("/v1/messages", payload)
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
    """Extract text from an Anthropic Messages-format response."""

    content = body.get("content")
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        text = "".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
        if text:
            return text
    raise ModelInvocationError("local Anthropic-compatible response did not contain text content")


__all__ = ["AnthropicLocalAdapter"]
