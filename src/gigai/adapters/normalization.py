"""Provider-neutral response normalization helpers below concrete adapters."""

from __future__ import annotations

from typing import Any

from .port import NormalizedUsage


def usage_object(value: object) -> dict[str, object]:
    """Preserve provider usage JSON independently from normalized accounting."""

    if type(value) is not dict:
        return {}
    return {str(key): item for key, item in value.items()}


def normalize_usage(usage: dict[str, object]) -> NormalizedUsage:
    def integer(*keys: str) -> int | None:
        for key in keys:
            value = usage.get(key)
            if type(value) is int and value >= 0:
                return value
        return None

    return NormalizedUsage(
        input_tokens=integer("input_tokens", "prompt_tokens"),
        output_tokens=integer("output_tokens", "completion_tokens"),
        total_tokens=integer("total_tokens"),
    )


def string_or(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


__all__ = ["normalize_usage", "string_or", "usage_object"]
