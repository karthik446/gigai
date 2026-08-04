"""Declared model capability data and fail-closed matching."""

from __future__ import annotations

from .port import CapabilityMismatchError


def require_capabilities(
    available: tuple[str, ...], required: frozenset[str], *, target_name: str
) -> None:
    missing = sorted(required - frozenset(available))
    if missing:
        raise CapabilityMismatchError(
            f"model target {target_name!r} lacks required capabilities: {missing}"
        )


__all__ = ["require_capabilities"]
