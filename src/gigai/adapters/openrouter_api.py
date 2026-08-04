"""OpenRouter chat-completions implementation of the GigAI model port."""

from __future__ import annotations

from typing import Any

from ..config import CredentialReference
from .capabilities import require_capabilities
from .http import CredentialResolver, HttpModelAdapter
from .normalization import normalize_usage, string_or, usage_object
from .port import InvocationRequest, InvocationResult, ModelInvocationError


class OpenRouterAPIAdapter(HttpModelAdapter):
    default_base_url = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        *,
        credential: CredentialReference,
        base_url: str | None = None,
        credential_resolver: CredentialResolver | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            credential=credential,
            base_url=base_url or self.default_base_url,
            credential_resolver=credential_resolver,
            client=client,
        )

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_output_tokens,
        }
        if request.reasoning_effort is not None:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        body = self._post_json("/chat/completions", payload)
        output = _chat_text(body)
        usage = usage_object(body.get("usage"))
        return InvocationResult(
            status="success",
            output_text=output,
            resolved_model=string_or(body.get("model"), request.model),
            raw_usage=usage,
            normalized_usage=normalize_usage(usage),
            cost_status="unavailable",
        )


def _chat_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or type(choices[0]) is not dict:
        raise ModelInvocationError("OpenRouter response did not contain choices")
    message = choices[0].get("message")
    if type(message) is not dict:
        raise ModelInvocationError("OpenRouter response did not contain a message")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise ModelInvocationError("OpenRouter response did not contain text content")
    return content


__all__ = ["OpenRouterAPIAdapter"]
