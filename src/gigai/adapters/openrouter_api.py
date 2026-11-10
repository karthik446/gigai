"""OpenRouter chat-completions implementation of the GigAI model port."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import CredentialReference
from .capabilities import require_capabilities
from .http import CredentialResolver, HttpModelAdapter
from .normalization import normalize_usage, string_or, usage_object
from .port import (
    InvocationRequest,
    InvocationResult,
    ModelInvocationError,
    call_with_retry,
    finish_result,
    start_clock,
    unsupported_settings,
)


class OpenRouterAPIAdapter(HttpModelAdapter):
    default_base_url = "https://openrouter.ai/api/v1"
    name = "openrouter_api"
    adapter_version = "openrouter_api:2"
    request_fields = frozenset(
        {
            "temperature",
            "top_p",
            "seed",
            "output_mode",
            "schema",
            "system",
            "retry_policy",
            "messages",
            "logprobs",
            "top_logprobs",
            "tools",
            "tool_choice",
        }
    )

    def __init__(
        self,
        *,
        credential: CredentialReference,
        base_url: str | None = None,
        credential_resolver: CredentialResolver | None = None,
        home_root: Path | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            credential=credential,
            base_url=base_url or self.default_base_url,
            credential_resolver=credential_resolver,
            home_root=home_root,
            client=client,
        )

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        clock = start_clock()
        require_capabilities(("text",), request.required_capabilities, target_name=request.target_name)
        unsupported = unsupported_settings(request, self.name, self.request_fields)
        payload: dict[str, object] = {
            "model": request.model,
            "messages": _chat_messages(request),
            "max_tokens": request.max_output_tokens,
        }
        if request.reasoning_effort is not None:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        payload.update(_chat_settings(request))
        body, retry_count, last_error = call_with_retry(
            request.retry_policy, lambda: self._post_json("/chat/completions", payload)
        )
        message = _chat_message(body)
        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else None
        output = _chat_text(message, allow_empty=bool(tool_calls))
        usage = usage_object(body.get("usage"))
        cost = usage.get("cost")
        reported_cost = float(cost) if type(cost) in (int, float) and cost >= 0 else None
        choice_logprobs = body["choices"][0].get("logprobs")
        logprobs = choice_logprobs.get("content") if isinstance(choice_logprobs, dict) else None
        result = InvocationResult(
            status="success",
            output_text=output,
            resolved_model=string_or(body.get("model"), request.model),
            raw_usage=usage,
            normalized_usage=normalize_usage(usage),
            cost_status="provider_reported" if reported_cost is not None else "unavailable",
            cost_usd=reported_cost,
            logprobs=tuple(logprobs) if isinstance(logprobs, list) else None,
            tool_calls=tuple(tool_calls) if tool_calls else None,
        )
        return finish_result(
            result,
            clock=clock,
            adapter_version=self.adapter_version,
            request_fields=self.request_fields,
            unsupported=unsupported,
            retry_count=retry_count,
            last_error_class=last_error,
        )


def _chat_messages(request: InvocationRequest) -> list[dict[str, str]]:
    system = [{"role": "system", "content": request.system}] if request.system is not None else []
    earlier = [{"role": turn["role"], "content": turn["content"]} for turn in request.messages or ()]
    return [*system, *earlier, {"role": "user", "content": request.prompt}]


def _chat_settings(request: InvocationRequest) -> dict[str, object]:
    settings: dict[str, object] = {}
    for name in ("temperature", "top_p", "seed", "logprobs", "top_logprobs", "tool_choice"):
        if getattr(request, name) is not None:
            settings[name] = getattr(request, name)
    if request.tools is not None:
        settings["tools"] = [dict(tool) for tool in request.tools]
    if request.output_mode == "json":
        settings["response_format"] = {"type": "json_object"}
    elif request.output_mode == "schema":
        settings["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "gigai_output", "strict": True, "schema": dict(request.schema or {})},
        }
    return settings


def _chat_message(body: dict[str, Any]) -> dict[str, Any]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or type(choices[0]) is not dict:
        raise ModelInvocationError("OpenRouter response did not contain choices")
    message = choices[0].get("message")
    if type(message) is not dict:
        raise ModelInvocationError("OpenRouter response did not contain a message")
    return message


def _chat_text(message: dict[str, Any], *, allow_empty: bool) -> str:
    content = message.get("content")
    if allow_empty and content is None:
        return ""
    if not isinstance(content, str) or (not content and not allow_empty):
        raise ModelInvocationError("OpenRouter response did not contain text content")
    return content


__all__ = ["OpenRouterAPIAdapter"]
