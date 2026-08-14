"""Read-only discovery and readiness reporting for Gig builder model targets."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from pathlib import Path
from typing import Callable

from .adapters.factory import AdapterFactoryError, resolve_model_adapter
from .config import GigAIConfig
from .model_targets import ModelTargetResolutionError


@dataclass(frozen=True)
class DetectedModel:
    """A locally visible executable; detection never invokes it."""

    name: str
    executable: Path | None
    readiness: str


@dataclass(frozen=True)
class ModelReadiness:
    """The durable distinction between configuration and usable invocation."""

    target_name: str
    endpoint_name: str | None
    model: str | None
    adapter: str | None
    readiness: str
    reason: str | None


def discover_installed_models(
    *, which: Callable[[str], str | None] = shutil.which
) -> tuple[DetectedModel, ...]:
    """Detect supported CLI names without spawning a process or reading secrets."""

    return tuple(
        DetectedModel(name, Path(path) if path else None, "detected" if path else "unavailable")
        for name in ("codex", "claude")
        for path in (which(name),)
    )


def resolve_target_readiness(config: GigAIConfig, target_name: str) -> ModelReadiness:
    """Resolve a configured target and classify it without making a model call."""

    try:
        binding = resolve_model_adapter(config, target_name)
    except ModelTargetResolutionError as exc:
        return ModelReadiness(target_name, None, None, None, "unavailable", str(exc))
    except AdapterFactoryError as exc:
        return ModelReadiness(target_name, None, None, None, "unsupported", str(exc))
    endpoint = binding.current.endpoint
    return ModelReadiness(
        target_name=target_name,
        endpoint_name=endpoint.name,
        model=binding.current.target.model,
        adapter=endpoint.adapter,
        readiness="usable",
        reason=None,
    )


__all__ = [
    "DetectedModel",
    "ModelReadiness",
    "discover_installed_models",
    "resolve_target_readiness",
]
