import json

import httpx
import pytest

from ..openai_compat import (
    AdapterError,
    AdapterHTTPError,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


def test_chat_completions_normalizes_without_provider_branch() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chat-1",
                "model": "provider/model",
                "choices": [
                    {"message": {"content": '{"answer":"ok"}'}}
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            },
        )

    adapter = OpenAICompatibleAdapter(
        OpenAICompatibleConfig(
            base_url="https://example.test/v1",
            dialect="chat_completions",
            supports_json_schema=True,
            extra_headers={"x-title": "GigAI spike"},
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = adapter.invoke(model="provider/model", prompt="answer", schema=SCHEMA)

    assert captured["path"] == "/v1/chat/completions"
    assert captured["body"]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert result.text == '{"answer":"ok"}'
    assert result.usage.input_tokens == 10
    assert result.usage.reasoning_tokens == 2


def test_responses_normalizes_nested_output_and_missing_usage() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp-1",
                "model": "custom-model",
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": '{"answer":"ok"}'}
                        ]
                    }
                ],
            },
        )

    adapter = OpenAICompatibleAdapter(
        OpenAICompatibleConfig(
            base_url="http://localhost:8000/v1/",
            dialect="responses",
            supports_json_schema=True,
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = adapter.invoke(model="custom-model", prompt="answer", schema=SCHEMA)

    assert captured["path"] == "/v1/responses"
    assert captured["body"]["text"]["format"]["schema"] == SCHEMA
    assert result.text == '{"answer":"ok"}'
    assert result.usage.input_tokens is None
    assert result.usage.total_tokens is None


def test_capability_flags_replace_provider_name_checks() -> None:
    adapter = OpenAICompatibleAdapter(
        OpenAICompatibleConfig(
            base_url="http://localhost:8000/v1",
            dialect="chat_completions",
            supports_json_schema=False,
        ),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: None)),
    )

    with pytest.raises(AdapterError, match="does not support JSON Schema"):
        adapter.invoke(model="local", prompt="answer", schema=SCHEMA)


def test_http_and_malformed_responses_are_classified() -> None:
    failing = OpenAICompatibleAdapter(
        OpenAICompatibleConfig(
            base_url="https://example.test/v1",
            dialect="chat_completions",
        ),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(429, text="rate limited")
            )
        ),
    )
    malformed = OpenAICompatibleAdapter(
        OpenAICompatibleConfig(
            base_url="https://example.test/v1",
            dialect="chat_completions",
        ),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"choices": []})
            )
        ),
    )

    with pytest.raises(AdapterHTTPError) as error:
        failing.invoke(model="x", prompt="answer")
    assert error.value.status_code == 429
    with pytest.raises(AdapterError, match="malformed Chat Completions"):
        malformed.invoke(model="x", prompt="answer")
