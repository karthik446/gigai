"""Resolve named model targets without selecting an adapter implementation."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Endpoint, GigAIConfig, ModelTarget


class ModelTargetResolutionError(ValueError):
    code = "model_target_resolution_failed"


@dataclass(frozen=True)
class ResolvedModelTarget:
    target: ModelTarget
    endpoint: Endpoint


def resolve_model_target(config: GigAIConfig, target_name: str) -> ResolvedModelTarget:
    target = next((item for item in config.model_targets if item.name == target_name), None)
    if target is None:
        raise ModelTargetResolutionError(f"unknown model target {target_name!r}")
    endpoint = next((item for item in config.endpoints if item.name == target.endpoint), None)
    if endpoint is None:  # parse_config normally makes this unreachable.
        raise ModelTargetResolutionError(
            f"model target {target_name!r} references missing endpoint {target.endpoint!r}"
        )
    return ResolvedModelTarget(target=target, endpoint=endpoint)


__all__ = [
    "ModelTargetResolutionError",
    "ResolvedModelTarget",
    "resolve_model_target",
]
