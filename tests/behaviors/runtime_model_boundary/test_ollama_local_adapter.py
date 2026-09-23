"""Offline policy and response tests for the bounded local Ollama adapter."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from gigai.adapters.ollama_local import (
    OllamaLocalAdapter,
    OllamaLocalAdapterError,
    OllamaLocalIdentityError,
    OllamaLocalResponseError,
)
from gigai.adapters.port import InvocationRequest, ModelInvocationCancelled


MODEL = "qwen3.8:latest"
DIGEST = "sha256:" + "a" * 64
DIGEST_BARE = "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
ENDPOINT = "http://127.0.0.1:11434"


def _request(
    *,
    prompt: str = "Synthetic public job; assess fit.",
    model: str = MODEL,
    maximum: int = 64,
) -> InvocationRequest:
    return InvocationRequest(
        target_name="local-qwen",
        endpoint_name="ollama-local",
        model=model,
        role="scout-proposal",
        prompt=prompt,
        target_capabilities=frozenset({"text"}),
        max_output_tokens=maximum,
        reasoning_effort="none",
    )


def _identity_handler(
    calls: list[httpx.Request],
    *,
    tags: object | None = None,
    version: object = "0.34.0",
    chat: object | None = None,
):
    if tags is None:
        tags = [{"name": MODEL, "digest": DIGEST_BARE}]
    if chat is None:
        chat = {
            "model": MODEL,
            "created_at": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant", "content": "fit: strong\ngaps: none"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 9,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": version})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": tags})
        if request.url.path == "/api/chat":
            return httpx.Response(200, json=chat)
        return httpx.Response(404, json={"error": "unknown synthetic path"})

    return handler


def _adapter(handler, **kwargs) -> OllamaLocalAdapter:
    values = {
        "endpoint": ENDPOINT,
        "model": MODEL,
        "model_digest": DIGEST,
        "transport": httpx.MockTransport(handler),
    }
    values.update(kwargs)
    return OllamaLocalAdapter(**values)


class _ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


def test_success_checks_identity_before_and_after_chat_and_bounds_payload() -> None:
    calls: list[httpx.Request] = []
    with _adapter(
        _identity_handler(calls), context_tokens=2048, max_output_tokens=128
    ) as adapter:
        result = adapter.invoke(_request(maximum=32))

    assert [call.url.path for call in calls] == [
        "/api/version",
        "/api/tags",
        "/api/chat",
        "/api/version",
        "/api/tags",
    ]
    chat = json.loads(calls[2].content)
    assert chat == {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Synthetic public job; assess fit."}],
        "stream": False,
        "think": False,
        "options": {"num_ctx": 2048, "num_predict": 32},
    }
    assert "authorization" not in calls[2].headers
    assert result.output_text == "fit: strong\ngaps: none"
    assert result.resolved_model == MODEL
    assert result.normalized_usage.input_tokens == 12
    assert result.normalized_usage.output_tokens == 9
    assert result.normalized_usage.total_tokens == 21
    assert result.raw_usage["runtime_version"] == "0.34.0"
    assert result.raw_usage["model_digest"] == DIGEST


def test_documented_bare_digest_normalizes_to_selected_full_identity() -> None:
    calls: list[httpx.Request] = []
    with _adapter(_identity_handler(calls)) as adapter:
        result = adapter.invoke(_request())
    assert result.raw_usage["model_digest"] == DIGEST
    assert [call.url.path for call in calls].count("/api/chat") == 1


def test_selected_bare_digest_is_normalized_to_full_identity() -> None:
    adapter = OllamaLocalAdapter(
        endpoint=ENDPOINT,
        model=MODEL,
        model_digest=DIGEST_BARE,
        transport=httpx.MockTransport(_identity_handler([])),
    )
    try:
        assert adapter.model_digest == DIGEST
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434",
        "https://127.0.0.1:11434",
        "http://127.0.0.1",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?x=1",
        "http://127.0.0.1:11434#fragment",
        "http://127.0.0.1:0",
        "http://user:pass@127.0.0.1:11434",
        "http://[::1]:11434",
    ],
)
def test_endpoint_must_be_explicit_numeric_loopback(endpoint: str) -> None:
    with pytest.raises(ValueError, match="numeric loopback"):
        OllamaLocalAdapter(endpoint=endpoint, model=MODEL, model_digest=DIGEST)


@pytest.mark.parametrize(
    ("model", "digest"),
    [
        ("qwen3.8-cloud:latest", DIGEST),
        ("gpt-oss:120b-cloud", DIGEST),
        (MODEL, "sha256:" + "A" * 64),
        (MODEL, "sha256:short"),
    ],
)
def test_model_policy_requires_local_tag_and_full_lowercase_digest(
    model: str, digest: str
) -> None:
    with pytest.raises(ValueError):
        OllamaLocalAdapter(endpoint=ENDPOINT, model=model, model_digest=digest)


@pytest.mark.parametrize(
    "tag_digest", [None, 123, "A" * 64, "sha256:" + "A" * 64, "sha256:" + "c" * 63]
)
def test_malformed_tag_digest_refuses_before_prompt(tag_digest: object) -> None:
    calls: list[httpx.Request] = []
    tags = [{"name": MODEL, "digest": tag_digest}]
    with _adapter(_identity_handler(calls, tags=tags)) as adapter:
        with pytest.raises(OllamaLocalIdentityError):
            adapter.invoke(_request())
    assert all(call.url.path != "/api/chat" for call in calls)


def test_constructor_bounds_and_thinking_policy() -> None:
    with pytest.raises(ValueError):
        OllamaLocalAdapter(
            endpoint=ENDPOINT, model=MODEL, model_digest=DIGEST, timeout_seconds=0
        )
    with pytest.raises(ValueError):
        OllamaLocalAdapter(
            endpoint=ENDPOINT, model=MODEL, model_digest=DIGEST, context_tokens=262_145
        )
    with pytest.raises(ValueError):
        OllamaLocalAdapter(
            endpoint=ENDPOINT, model=MODEL, model_digest=DIGEST, max_output_tokens=8193
        )
    with pytest.raises(ValueError, match="think=False"):
        OllamaLocalAdapter(
            endpoint=ENDPOINT, model=MODEL, model_digest=DIGEST, think=True
        )


@pytest.mark.parametrize(
    "tags",
    [
        [],
        [{"name": MODEL, "digest": OTHER_DIGEST}],
        [{"name": MODEL, "digest": DIGEST}, {"name": MODEL, "digest": DIGEST}],
        [{"name": MODEL, "digest": DIGEST, "cloud": True}],
        [{"name": MODEL, "digest": DIGEST, "remote_model": "cloud-qwen"}],
        [{"name": MODEL, "digest": DIGEST, "cloud": []}],
        {"name": MODEL},
    ],
)
def test_identity_refusal_sends_no_prompt(tags: object) -> None:
    calls: list[httpx.Request] = []
    prompt = "SYNTHETIC_CANARY_DO_NOT_LEAK"
    with _adapter(_identity_handler(calls, tags=tags)) as adapter:
        with pytest.raises(OllamaLocalIdentityError) as exc_info:
            adapter.invoke(_request(prompt=prompt))

    assert all(call.url.path != "/api/chat" for call in calls)
    assert prompt not in str(exc_info.value)


def test_malformed_version_and_redirect_fail_before_prompt() -> None:
    calls: list[httpx.Request] = []
    with _adapter(_identity_handler(calls, version=[])) as adapter:
        with pytest.raises(OllamaLocalIdentityError):
            adapter.invoke(_request())
    assert all(call.url.path != "/api/chat" for call in calls)

    calls = []

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1:9/api/version"}
        )

    with _adapter(redirect_handler) as adapter:
        with pytest.raises(OllamaLocalAdapterError, match="HTTP 302"):
            adapter.invoke(_request())
    assert all(call.url.path != "/api/chat" for call in calls)


def test_request_model_and_output_bound_refused_without_http_prompt() -> None:
    calls: list[httpx.Request] = []
    with _adapter(_identity_handler(calls), max_output_tokens=32) as adapter:
        with pytest.raises(OllamaLocalIdentityError):
            adapter.invoke(_request(model="other:latest"))
        with pytest.raises(OllamaLocalAdapterError, match="output bound"):
            adapter.invoke(_request(maximum=33))
    assert calls == []


def test_non_none_reasoning_effort_refused_before_http_prompt() -> None:
    calls: list[httpx.Request] = []
    with _adapter(_identity_handler(calls)) as adapter:
        with pytest.raises(OllamaLocalAdapterError, match="reasoning_effort"):
            adapter.invoke(replace(_request(), reasoning_effort="low"))
    assert calls == []


@pytest.mark.parametrize(
    "chat",
    [
        {"model": MODEL, "done": False, "message": {"content": "partial"}},
        {
            "model": MODEL,
            "done": True,
            "done_reason": "length",
            "message": {"content": "partial"},
        },
        {
            "model": MODEL,
            "done": True,
            "done_reason": "stop",
            "message": {"thinking": "private reasoning"},
        },
        {
            "model": MODEL,
            "done": True,
            "done_reason": "stop",
            "message": {"content": "   "},
        },
        {
            "model": MODEL,
            "done": True,
            "done_reason": "stop",
            "message": {"role": "user", "content": "not assistant"},
        },
        {
            "model": MODEL,
            "done": True,
            "done_reason": "stop",
            "message": {"content": "missing role"},
        },
        {
            "model": MODEL,
            "done": True,
            "done_reason": "stop",
            "message": {"role": 1, "content": "wrong role type"},
        },
        {
            "model": "other:latest",
            "done": True,
            "done_reason": "stop",
            "message": {"content": "wrong"},
        },
    ],
)
def test_incomplete_or_mismatched_response_is_typed_failure(
    chat: dict[str, object],
) -> None:
    calls: list[httpx.Request] = []
    with _adapter(_identity_handler(calls, chat=chat)) as adapter:
        with pytest.raises(OllamaLocalResponseError):
            adapter.invoke(_request())
    assert [call.url.path for call in calls].count("/api/chat") == 1


def test_reported_eval_count_overrun_is_refused_and_missing_count_is_unavailable() -> (
    None
):
    overrun = {
        "model": MODEL,
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": "answer"},
        "eval_count": 2,
    }
    with _adapter(_identity_handler([], chat=overrun)) as adapter:
        with pytest.raises(OllamaLocalResponseError, match="output bound"):
            adapter.invoke(_request(maximum=1))

    missing = {
        "model": MODEL,
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": "answer"},
    }
    with _adapter(_identity_handler([], chat=missing)) as adapter:
        result = adapter.invoke(_request())
    assert result.normalized_usage.output_tokens is None
    assert "completion_tokens" not in result.raw_usage

    bool_usage = {
        "model": MODEL,
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": "answer"},
        "eval_count": True,
    }
    with _adapter(_identity_handler([], chat=bool_usage)) as adapter:
        with pytest.raises(OllamaLocalResponseError, match="malformed usage"):
            adapter.invoke(_request())


def test_oversized_chunked_response_stops_at_byte_bound_and_closes_response() -> None:
    calls: list[httpx.Request] = []
    stream: _ChunkStream | None = None
    base = _identity_handler(calls)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stream
        if request.url.path != "/api/chat":
            return base(request)
        calls.append(request)
        body = json.dumps(
            {
                "model": MODEL,
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": "x" * 400},
            }
        ).encode("utf-8")
        stream = _ChunkStream([body[:32], body[32:]])
        return httpx.Response(200, stream=stream)

    with _adapter(handler, max_response_bytes=128) as adapter:
        with pytest.raises(OllamaLocalAdapterError, match="byte bound"):
            adapter.invoke(_request())
    assert stream is not None and stream.closed
    assert [call.url.path for call in calls].count("/api/chat") == 1


def test_identity_drift_after_response_is_refused() -> None:
    calls: list[httpx.Request] = []
    tag_reads = 0

    def drifting_handler(request: httpx.Request) -> httpx.Response:
        nonlocal tag_reads
        calls.append(request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.34.0"})
        if request.url.path == "/api/tags":
            tag_reads += 1
            digest = DIGEST if tag_reads == 1 else OTHER_DIGEST
            return httpx.Response(
                200, json={"models": [{"name": MODEL, "digest": digest}]}
            )
        return httpx.Response(
            200,
            json={
                "model": MODEL,
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": "answer"},
            },
        )

    with _adapter(drifting_handler) as adapter:
        with pytest.raises(OllamaLocalIdentityError, match="changed"):
            adapter.invoke(_request())
    assert [call.url.path for call in calls].count("/api/chat") == 1


def test_timeout_and_cancellation_are_typed_without_prompt_leak() -> None:
    prompt = "SYNTHETIC_CANARY_DO_NOT_LEAK"

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with _adapter(timeout_handler) as adapter:
        with pytest.raises(OllamaLocalAdapterError, match="timed out") as exc_info:
            adapter.invoke(_request(prompt=prompt))
    assert prompt not in str(exc_info.value)

    def cancelled_handler(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    with _adapter(cancelled_handler) as adapter:
        with pytest.raises(ModelInvocationCancelled):
            adapter.invoke(_request(prompt=prompt))
