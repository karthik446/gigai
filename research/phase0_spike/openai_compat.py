"""Normalize Chat Completions and Responses through one capability adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Literal

import httpx


Dialect = Literal["chat_completions", "responses"]


class AdapterError(RuntimeError):
    pass


class AdapterHTTPError(AdapterError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    dialect: Dialect
    api_key_env: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    supports_json_schema: bool = False
    timeout_seconds: float = 30


@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    reasoning_tokens: int | None


@dataclass(frozen=True)
class NormalizedResponse:
    text: str
    model: str | None
    response_id: str | None
    usage: NormalizedUsage
    raw: dict[str, Any]


class OpenAICompatibleAdapter:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=config.timeout_seconds)

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", **self.config.extra_headers}
        if self.config.api_key_env:
            api_key = os.environ.get(self.config.api_key_env)
            if not api_key:
                raise AdapterError(
                    f"missing API key environment variable {self.config.api_key_env}"
                )
            headers["authorization"] = f"Bearer {api_key}"
        return headers

    def _request(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        if schema and not self.config.supports_json_schema:
            raise AdapterError("configured endpoint does not support JSON Schema")
        if self.config.dialect == "chat_completions":
            path = "/chat/completions"
            payload: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            }
            if schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "gigai_output",
                        "strict": True,
                        "schema": schema,
                    },
                }
            return path, payload

        path = "/responses"
        payload = {"model": model, "input": prompt}
        if schema:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "gigai_output",
                    "strict": True,
                    "schema": schema,
                }
            }
        return path, payload

    def invoke(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any] | None = None,
    ) -> NormalizedResponse:
        path, payload = self._request(model=model, prompt=prompt, schema=schema)
        response = self.client.post(
            f"{self.config.base_url.rstrip('/')}{path}",
            headers=self._headers(),
            json=payload,
        )
        if response.status_code >= 400:
            raise AdapterHTTPError(response.status_code, response.text)
        try:
            raw = response.json()
        except ValueError as exc:
            raise AdapterError("endpoint returned non-JSON response") from exc
        if not isinstance(raw, dict):
            raise AdapterError("endpoint returned a non-object JSON response")
        if self.config.dialect == "chat_completions":
            return _normalize_chat(raw)
        return _normalize_responses(raw)


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _normalize_chat(raw: dict[str, Any]) -> NormalizedResponse:
    try:
        text = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AdapterError("malformed Chat Completions response") from exc
    if not isinstance(text, str):
        raise AdapterError("Chat Completions content is not text")
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )
    return NormalizedResponse(
        text=text,
        model=raw.get("model") if isinstance(raw.get("model"), str) else None,
        response_id=raw.get("id") if isinstance(raw.get("id"), str) else None,
        usage=NormalizedUsage(
            input_tokens=_optional_int(usage.get("prompt_tokens")),
            output_tokens=_optional_int(usage.get("completion_tokens")),
            total_tokens=_optional_int(usage.get("total_tokens")),
            reasoning_tokens=_optional_int(details.get("reasoning_tokens")),
        ),
        raw=raw,
    )


def _response_text(raw: dict[str, Any]) -> str:
    if isinstance(raw.get("output_text"), str):
        return raw["output_text"]
    chunks: list[str] = []
    output = raw.get("output")
    if not isinstance(output, list):
        raise AdapterError("malformed Responses API response")
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    if not chunks:
        raise AdapterError("Responses API response has no output text")
    return "".join(chunks)


def _normalize_responses(raw: dict[str, Any]) -> NormalizedResponse:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    details = (
        usage.get("output_tokens_details")
        if isinstance(usage.get("output_tokens_details"), dict)
        else {}
    )
    return NormalizedResponse(
        text=_response_text(raw),
        model=raw.get("model") if isinstance(raw.get("model"), str) else None,
        response_id=raw.get("id") if isinstance(raw.get("id"), str) else None,
        usage=NormalizedUsage(
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
            total_tokens=_optional_int(usage.get("total_tokens")),
            reasoning_tokens=_optional_int(details.get("reasoning_tokens")),
        ),
        raw=raw,
    )
