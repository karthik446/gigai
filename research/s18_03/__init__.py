"""Offline S18-03 API and local-runtime feasibility helpers."""

from .fixtures import (
    PROTOCOL_DECISIONS,
    ProtocolResult,
    parse_anthropic_message,
    parse_anthropic_stream,
    parse_ollama_ndjson,
)

__all__ = [
    "PROTOCOL_DECISIONS",
    "ProtocolResult",
    "parse_anthropic_message",
    "parse_anthropic_stream",
    "parse_ollama_ndjson",
]
