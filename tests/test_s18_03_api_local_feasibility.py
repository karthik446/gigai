from __future__ import annotations

import ast
from pathlib import Path

from research.s18_03.fixtures import (
    PROTOCOL_DECISIONS,
    parse_anthropic_message,
    parse_anthropic_stream,
    parse_ollama_ndjson,
)


def test_anthropic_content_blocks_usage_and_extensions_are_preserved() -> None:
    result = parse_anthropic_message(
        {
            "type": "message",
            "role": "assistant",
            "model": "claude-fixture",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "tool-fixture", "name": "lookup", "input": {"q": "x"}},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }
    )
    assert result.status == "completed"
    assert result.output_text == "hello"
    assert result.finish_reason == "end_turn"
    assert result.raw_usage == {"input_tokens": 4, "output_tokens": 2}
    assert result.extensions[0]["type"] == "tool_use"


def test_anthropic_stream_reduces_text_and_terminal_usage() -> None:
    result = parse_anthropic_stream(
        [
            {"type": "message_start", "message": {"type": "message", "role": "assistant", "model": "claude-fixture", "content": [], "usage": {"input_tokens": 3}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hel"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"input_tokens": 3, "output_tokens": 2}},
            {"type": "message_stop"},
        ]
    )
    assert result.status == "completed"
    assert result.output_text == "hello"
    assert result.raw_usage["output_tokens"] == 2


def test_anthropic_rate_and_overload_errors_are_not_success() -> None:
    for error_type in ("rate_limit_error", "overloaded_error"):
        result = parse_anthropic_message({"type": "error", "error": {"type": error_type, "message": "fixture"}})
        assert result.status == "failed"
        assert result.error_type == error_type


def test_ollama_ndjson_is_independent_and_preserves_runtime_usage() -> None:
    result = parse_ollama_ndjson(
        [
            '{"model":"llama-fixture","message":{"role":"assistant","content":"hel"},"done":false}',
            '{"model":"llama-fixture","message":{"role":"assistant","content":"lo"},"done":true,"done_reason":"stop","prompt_eval_count":5,"eval_count":2}',
        ],
        runtime_metadata={"runtime": "ollama", "runtime_version": "fixture-1"},
    )
    assert result.family == "local_ollama"
    assert result.status == "completed"
    assert result.output_text == "hello"
    assert result.raw_usage == {"prompt_eval_count": 5, "eval_count": 2}
    assert result.extensions[0]["runtime"] == {"runtime": "ollama", "runtime_version": "fixture-1"}


def test_incomplete_and_malformed_records_fail_closed() -> None:
    malformed_stream = parse_anthropic_stream([{"type": "message_stop"}])
    assert malformed_stream.status == "malformed_response"
    no_stop = parse_ollama_ndjson(['{"model":"llama-fixture","response":"partial","done":false}'], runtime_metadata={"runtime": "ollama", "runtime_version": "fixture-1"})
    assert no_stop.status == "incomplete_stream"
    malformed = parse_ollama_ndjson(["not-json"], runtime_metadata={"runtime": "ollama", "runtime_version": "fixture-1"})
    assert malformed.status == "malformed_response"
    missing_identity = parse_ollama_ndjson(['{"response":"done","done":true}'], runtime_metadata={"runtime": "ollama", "runtime_version": "fixture-1"})
    assert missing_identity.status == "malformed_response"


def test_families_remain_deferred_and_module_has_no_effectful_imports() -> None:
    assert set(PROTOCOL_DECISIONS) == {"anthropic_api", "local_ollama"}
    assert all(value != "supported" for value in PROTOCOL_DECISIONS.values())
    source = Path(__file__).parents[1].joinpath("research/s18_03/fixtures.py").read_text()
    tree = ast.parse(source)
    imported = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not imported.intersection({"socket", "subprocess", "httpx", "urllib", "requests"})
