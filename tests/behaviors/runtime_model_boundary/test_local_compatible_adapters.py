"""Offline wire-contract tests for local OpenAI- and Anthropic-compatible adapters."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gigai.adapters.anthropic_local import AnthropicLocalAdapter
from gigai.adapters.factory import resolve_model_adapter
from gigai.adapters.openai_local import OpenAILocalAdapter
from gigai.adapters.port import InvocationRequest, ModelInvocationError
from gigai.config import CredentialReference, Endpoint, ModelTarget
from gigai.setup import build_config


BASE_URL = "http://127.0.0.1:8000"
TOKEN = "local-test-token"


def _request() -> InvocationRequest:
    return InvocationRequest(
        target_name="local-default",
        endpoint_name="local",
        model="test-model",
        role="scout-proposal",
        prompt="Assess this synthetic job.",
        target_capabilities=frozenset({"text"}),
        max_output_tokens=42,
        reasoning_effort=None,
    )


@pytest.mark.parametrize(
    ("adapter_type", "response", "path", "expected_output", "expected_headers", "usage"),
    (
        (
            OpenAILocalAdapter,
            {
                "model": "served-openai-model",
                "choices": [{"message": {"content": "openai response"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            },
            "/v1/chat/completions",
            "openai response",
            {"authorization": f"Bearer {TOKEN}"},
            (3, 5, 8),
        ),
        (
            AnthropicLocalAdapter,
            {
                "model": "served-anthropic-model",
                "content": [{"type": "text", "text": "anthropic response"}],
                "usage": {"input_tokens": 4, "output_tokens": 6},
            },
            "/v1/messages",
            "anthropic response",
            {"x-api-key": TOKEN, "anthropic-version": "2023-06-01"},
            (4, 6, None),
        ),
    ),
)
def test_local_adapters_send_protocol_auth_and_parse_response(
    adapter_type: type[OpenAILocalAdapter] | type[AnthropicLocalAdapter],
    response: dict[str, object],
    path: str,
    expected_output: str,
    expected_headers: dict[str, str],
    usage: tuple[int, int, int],
) -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json=response, request=request)

    credential = CredentialReference("local-key", "environment", "LOCAL_LLM_API_KEY")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = adapter_type(
            base_url=BASE_URL,
            credential=credential,
            credential_resolver=lambda _: TOKEN,
            client=client,
        )
        result = adapter.invoke(_request())

    assert result.status == "success"
    assert result.output_text == expected_output
    assert result.resolved_model == response["model"]
    assert result.cost_status == "unavailable"
    assert (
        result.normalized_usage.input_tokens,
        result.normalized_usage.output_tokens,
        result.normalized_usage.total_tokens,
    ) == usage
    assert len(observed) == 1
    sent = observed[0]
    assert sent.url == f"{BASE_URL}{path}"
    for name, value in expected_headers.items():
        assert sent.headers[name] == value
    payload = json.loads(sent.content)
    assert payload == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Assess this synthetic job."}],
        "max_tokens": 42,
    }


@pytest.mark.parametrize(
    ("adapter_type", "response", "path"),
    (
        (
            OpenAILocalAdapter,
            {"choices": [{"message": {"content": "ok"}}]},
            "/v1/chat/completions",
        ),
        (
            AnthropicLocalAdapter,
            {"content": [{"type": "text", "text": "ok"}]},
            "/v1/messages",
        ),
    ),
)
def test_local_adapters_omit_auth_without_a_credential(
    adapter_type: type[OpenAILocalAdapter] | type[AnthropicLocalAdapter],
    response: dict[str, object],
    path: str,
) -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json=response, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = adapter_type(base_url=BASE_URL, client=client)
        result = adapter.invoke(_request())

    assert result.output_text == "ok"
    assert observed[0].url.path == path
    assert "authorization" not in observed[0].headers
    assert "x-api-key" not in observed[0].headers
    assert "anthropic-version" not in observed[0].headers


@pytest.mark.parametrize(
    ("adapter_type", "response"),
    (
        (OpenAILocalAdapter, {"choices": [{"message": {"content": None}}]}),
        (AnthropicLocalAdapter, {"content": [{"type": "image", "source": {}}]}),
    ),
)
def test_local_adapters_reject_responses_without_text(
    adapter_type: type[OpenAILocalAdapter] | type[AnthropicLocalAdapter],
    response: dict[str, object],
) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response, request=request)
        )
    ) as client:
        adapter = adapter_type(base_url=BASE_URL, client=client)
        with pytest.raises(ModelInvocationError, match="did not contain text content"):
            adapter.invoke(_request())


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://localhost:8000",
        "https://127.0.0.1:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:8000/v1",
        "http://127.0.0.1:8000?token=x",
        "http://192.0.2.1:8000",
    ),
)
@pytest.mark.parametrize("adapter_type", (OpenAILocalAdapter, AnthropicLocalAdapter))
def test_local_adapters_reject_non_explicit_loopback_urls(
    adapter_type: type[OpenAILocalAdapter] | type[AnthropicLocalAdapter],
    endpoint: str,
) -> None:
    with pytest.raises(ValueError, match="explicit numeric loopback"):
        adapter_type(base_url=endpoint)


@pytest.mark.parametrize(
    ("protocol", "adapter_type", "response"),
    (
        (
            "openai_local",
            OpenAILocalAdapter,
            {"choices": [{"message": {"content": "openai factory"}}]},
        ),
        (
            "anthropic_local",
            AnthropicLocalAdapter,
            {"content": [{"type": "text", "text": "anthropic factory"}]},
        ),
    ),
)
def test_factory_dispatches_local_protocols_to_the_matching_adapter(
    tmp_path: Path,
    protocol: str,
    adapter_type: type[OpenAILocalAdapter] | type[AnthropicLocalAdapter],
    response: dict[str, object],
) -> None:
    config = build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpad",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="local", adapter=protocol, base_url=BASE_URL),
        ),
        model_targets=(
            ModelTarget("offline-default", "offline", "fixture", ("text",), 64),
            ModelTarget("local-default", "local", "test-model", ("text",), 42),
        ),
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response, request=request)
        )
    )
    binding = resolve_model_adapter(
        config,
        "local-default",
        transport_overrides={"local": client},
    )
    try:
        assert isinstance(binding.port, adapter_type)
        assert binding.port.invoke(binding.request(role="test", prompt="hello")).status == "success"
    finally:
        binding.close()
        client.close()
