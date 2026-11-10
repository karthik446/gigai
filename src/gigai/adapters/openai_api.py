"""OpenAI Responses API implementation of the GigAI model port."""

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


class OpenAIAPIAdapter(HttpModelAdapter):
    default_base_url = "https://api.openai.com/v1"
    name = "openai_api"
    adapter_version = "openai_api:2"
    #: No ``seed``: the Responses API has no seed parameter, so a seed is recorded as unsupported.
    request_fields = frozenset(
        {
            "temperature",
            "top_p",
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
            "input": _response_input(request),
            "max_output_tokens": request.max_output_tokens,
        }
        if request.reasoning_effort is not None:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        payload.update(_response_settings(request))
        body, retry_count, last_error = call_with_retry(
            request.retry_policy, lambda: self._post_json("/responses", payload)
        )
        tool_calls = _function_calls(body)
        output = _response_text(body, allow_empty=bool(tool_calls))
        usage = usage_object(body.get("usage"))
        logprobs = _output_logprobs(body) if request.logprobs else None
        result = InvocationResult(
            status="success",
            output_text=output,
            resolved_model=string_or(body.get("model"), request.model),
            raw_usage=usage,
            normalized_usage=normalize_usage(usage),
            cost_status="unavailable",
            logprobs=logprobs,
            tool_calls=tool_calls or None,
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


def _response_input(request: InvocationRequest) -> str | list[dict[str, str]]:
    if request.messages is None:
        return request.prompt
    earlier = [{"role": turn["role"], "content": turn["content"]} for turn in request.messages]
    return [*earlier, {"role": "user", "content": request.prompt}]


def _response_settings(request: InvocationRequest) -> dict[str, object]:
    settings: dict[str, object] = {}
    for name in ("temperature", "top_p", "top_logprobs", "tool_choice"):
        if getattr(request, name) is not None:
            settings[name] = getattr(request, name)
    if request.system is not None:
        settings["instructions"] = request.system
    if request.logprobs:
        settings["include"] = ["message.output_text.logprobs"]
    if request.tools is not None:
        settings["tools"] = [dict(tool) for tool in request.tools]
    if request.output_mode == "json":
        settings["text"] = {"format": {"type": "json_object"}}
    elif request.output_mode == "schema":
        settings["text"] = {
            "format": {"type": "json_schema", "name": "gigai_output", "strict": True, "schema": dict(request.schema or {})}
        }
    return settings


def _function_calls(body: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    output = body.get("output")
    if not isinstance(output, list):
        return ()
    return tuple(item for item in output if isinstance(item, dict) and item.get("type") == "function_call")


def _output_logprobs(body: dict[str, Any]) -> tuple[dict[str, Any], ...] | None:
    output = body.get("output")
    if not isinstance(output, list):
        return None
    found = [
        logprob
        for item in output
        if isinstance(item, dict) and isinstance(item.get("content"), list)
        for content in item["content"]
        if isinstance(content, dict) and isinstance(content.get("logprobs"), list)
        for logprob in content["logprobs"]
        if isinstance(logprob, dict)
    ]
    return tuple(found) if found else None


def _response_text(body: dict[str, Any], *, allow_empty: bool = False) -> str:
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
        if allow_empty:
            return ""
        raise ModelInvocationError("OpenAI response did not contain text content")
    return "".join(fragments)


__all__ = ["OpenAIAPIAdapter"]
