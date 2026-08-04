"""OpenAI Responses API implementation of the GigAI model port."""

from __future__ import annotations

from typing import Any

from ..config import CredentialReference
from .capabilities import require_capabilities
from .http import CredentialResolver, HttpModelAdapter
from .normalization import normalize_usage, string_or, usage_object
from .port import InvocationRequest, InvocationResult, ModelInvocationError


class OpenAIAPIAdapter(HttpModelAdapter):
    default_base_url = "https://api.openai.com/v1"

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
            "input": request.prompt,
            "max_output_tokens": request.max_output_tokens,
        }
        if request.reasoning_effort is not None:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        body = self._post_json("/responses", payload)
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
    direct = body.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    output = body.get("output")
    if not isinstance(output, list):
        raise ModelInvocationError("OpenAI response did not contain output text")
    fragments: list[str] = []
    for item in output:
        if not isinstance(item, dict) or not isinstance(item.get("content"), list):
            continue
        for content in item["content"]:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                fragments.append(content["text"])
    if not fragments:
        raise ModelInvocationError("OpenAI response did not contain text content")
    return "".join(fragments)


__all__ = ["OpenAIAPIAdapter"]
