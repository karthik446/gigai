"""Provider-free parsers for recorded Anthropic and local-runtime fixtures."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Mapping


PROTOCOL_DECISIONS = {
    "anthropic_api": "deferred_to_g18_or_additional_adapter_goal",
    "local_ollama": "deferred_to_g18_or_additional_adapter_goal",
}


@dataclass(frozen=True)
class ProtocolResult:
    family: str
    status: str
    model: str | None
    output_text: str
    finish_reason: str | None
    raw_usage: Mapping[str, object]
    extensions: tuple[Mapping[str, object], ...]
    error_type: str | None = None


def parse_anthropic_message(payload: Mapping[str, Any]) -> ProtocolResult:
    """Parse one recorded Messages response without contacting Anthropic."""

    if payload.get("type") == "error":
        error = payload.get("error")
        error_type = error.get("type") if isinstance(error, Mapping) else None
        return ProtocolResult("anthropic_api", "failed", None, "", None, {}, (), error_type)
    if payload.get("type") != "message" or payload.get("role") != "assistant":
        return _malformed("anthropic_api")
    model = payload.get("model")
    content = payload.get("content")
    usage = payload.get("usage")
    if not isinstance(model, str) or not model or not isinstance(content, list) or not isinstance(usage, Mapping):
        return _malformed("anthropic_api")
    text_parts: list[str] = []
    extensions: list[Mapping[str, object]] = []
    for block in content:
        if not isinstance(block, Mapping):
            return _malformed("anthropic_api")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        else:
            extensions.append(dict(block))
    return ProtocolResult(
        "anthropic_api",
        "completed",
        model,
        "".join(text_parts),
        payload.get("stop_reason") if isinstance(payload.get("stop_reason"), str) else None,
        dict(usage),
        tuple(extensions),
    )


def parse_anthropic_stream(events: Iterable[Mapping[str, Any]]) -> ProtocolResult:
    """Reduce recorded Anthropic SSE-shaped events with explicit terminal checks."""

    model: str | None = None
    usage: Mapping[str, object] = {}
    text_parts: list[str] = []
    extensions: list[Mapping[str, object]] = []
    finish_reason: str | None = None
    saw_start = False
    saw_stop = False
    for event in events:
        event_type = event.get("type")
        if event_type == "message_start":
            saw_start = True
            message = event.get("message")
            if not isinstance(message, Mapping):
                return _malformed("anthropic_api")
            parsed = parse_anthropic_message({**message, "content": message.get("content", [])})
            if parsed.status == "failed":
                return parsed
            if parsed.status == "malformed":
                model = message.get("model") if isinstance(message.get("model"), str) else None
            else:
                model, usage = parsed.model, parsed.raw_usage
        elif event_type == "content_block_start":
            block = event.get("content_block")
            if not isinstance(block, Mapping):
                return _malformed("anthropic_api")
            if block.get("type") != "text":
                extensions.append(dict(block))
        elif event_type == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, Mapping):
                return _malformed("anthropic_api")
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                text_parts.append(delta["text"])
            elif delta.get("type") != "thinking_delta":
                extensions.append(dict(delta))
        elif event_type == "message_delta":
            delta = event.get("delta")
            if not isinstance(delta, Mapping):
                return _malformed("anthropic_api")
            if isinstance(delta.get("stop_reason"), str):
                finish_reason = delta["stop_reason"]
            if isinstance(event.get("usage"), Mapping):
                usage = dict(event["usage"])
        elif event_type == "message_stop":
            if not saw_start:
                return _malformed("anthropic_api")
            saw_stop = True
        elif event_type == "error":
            error = event.get("error")
            error_type = error.get("type") if isinstance(error, Mapping) else None
            return ProtocolResult("anthropic_api", "failed", model, "".join(text_parts), finish_reason, usage, tuple(extensions), error_type)
        else:
            return _malformed("anthropic_api")
    if not saw_start:
        return _malformed("anthropic_api")
    if not saw_stop:
        return ProtocolResult("anthropic_api", "incomplete_stream", model, "".join(text_parts), finish_reason, usage, tuple(extensions))
    return ProtocolResult("anthropic_api", "completed", model, "".join(text_parts), finish_reason, usage, tuple(extensions))


def parse_ollama_ndjson(
    lines: Iterable[str],
    *,
    runtime_metadata: Mapping[str, str],
) -> ProtocolResult:
    """Reduce recorded local-runtime NDJSON without starting a runtime."""

    if not runtime_metadata.get("runtime") or not runtime_metadata.get("runtime_version"):
        return _malformed("local_ollama")
    model: str | None = None
    text_parts: list[str] = []
    extensions: list[Mapping[str, object]] = [{"runtime": dict(runtime_metadata)}]
    usage: Mapping[str, object] = {}
    finish_reason: str | None = None
    saw_done = False
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return _malformed("local_ollama")
        if not isinstance(event, Mapping):
            return _malformed("local_ollama")
        if event.get("error"):
            return ProtocolResult("local_ollama", "failed", model, "".join(text_parts), finish_reason, usage, tuple(extensions), str(event["error"]))
        if isinstance(event.get("model"), str):
            model = event["model"]
        message = event.get("message")
        if isinstance(message, Mapping) and isinstance(message.get("content"), str):
            text_parts.append(message["content"])
        if isinstance(event.get("response"), str):
            text_parts.append(event["response"])
        if isinstance(message, Mapping) and "thinking" in message:
            extensions.append({"thinking": message["thinking"]})
        if event.get("done") is True:
            saw_done = True
            finish_reason = event.get("done_reason") if isinstance(event.get("done_reason"), str) else None
            usage = {key: event[key] for key in ("prompt_eval_count", "eval_count", "prompt_eval_duration", "eval_duration") if key in event}
    if not saw_done:
        return ProtocolResult("local_ollama", "incomplete_stream", model, "".join(text_parts), finish_reason, usage, tuple(extensions))
    if not model:
        return _malformed("local_ollama")
    return ProtocolResult("local_ollama", "completed", model, "".join(text_parts), finish_reason, usage, tuple(extensions))


def _malformed(family: str) -> ProtocolResult:
    return ProtocolResult(family, "malformed_response", None, "", None, {}, ())


__all__ = [
    "PROTOCOL_DECISIONS",
    "ProtocolResult",
    "parse_anthropic_message",
    "parse_anthropic_stream",
    "parse_ollama_ndjson",
]
