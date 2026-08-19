"""Read-only discovery and readiness reporting for Gig builder model targets."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from pathlib import Path
import subprocess
import tempfile
from typing import Callable

from .adapters.factory import AdapterFactoryError, resolve_model_adapter
from .adapters.port import ModelInvocationError
from .adapters.process import allowed_environment
from .config import GigAIConfig
from .model_targets import ModelTargetResolutionError


@dataclass(frozen=True)
class DetectedModel:
    """A locally visible executable; detection never invokes it."""

    name: str
    executable: Path | None
    readiness: str
    version: str | None = None


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
    """Detect supported CLIs and collect bounded, read-only version evidence."""

    return tuple(
        DetectedModel(
            name,
            Path(path) if path else None,
            "detected" if path else "unavailable",
            _probe_version(path) if path else None,
        )
        for name in ("codex", "claude")
        for path in (which(name),)
    )


def _probe_version(executable: str) -> str | None:
    """Read one CLI version line without invoking a model or inheriting secrets."""

    try:
        with tempfile.TemporaryDirectory(prefix="gigai-version-") as directory:
            result = subprocess.run(
                [executable, "--version"],
                cwd=directory,
                env=allowed_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=10,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in (*result.stdout.splitlines(), *result.stderr.splitlines()):
        value = line.strip()
        if value and "\0" not in value:
            return value[:200]
    return None


def resolve_target_readiness(config: GigAIConfig, target_name: str) -> ModelReadiness:
    """Resolve a configured target without making a provider/model call.

    A constructed adapter proves only that the typed configuration is usable
    by the factory.  It does not prove provider authentication or command
    compatibility; that distinction belongs to :func:`probe_target_readiness`.
    """

    try:
        binding = resolve_model_adapter(config, target_name)
    except ModelTargetResolutionError as exc:
        return ModelReadiness(target_name, None, None, None, "unavailable", str(exc))
    except AdapterFactoryError as exc:
        return ModelReadiness(target_name, None, None, None, "unsupported", str(exc))
    except ModelInvocationError as exc:
        return ModelReadiness(target_name, None, None, None, "unavailable", str(exc))
    endpoint = binding.current.endpoint
    return ModelReadiness(
        target_name=target_name,
        endpoint_name=endpoint.name,
        model=binding.current.target.model,
        adapter=endpoint.adapter,
        readiness="usable" if endpoint.adapter == "deterministic" else "configured",
        reason=(
            None
            if endpoint.adapter == "deterministic"
            else "explicit readiness probe required before provider invocation"
        ),
    )


def probe_target_readiness(config: GigAIConfig, target_name: str) -> ModelReadiness:
    """Run one explicit bounded readiness invocation for a configured target.

    Callers must expose this as an opt-in action.  This function may resolve a
    provider credential and may incur provider cost; ordinary discovery and
    setup rendering must call ``resolve_target_readiness`` instead.
    """

    try:
        binding = resolve_model_adapter(config, target_name)
        endpoint = binding.current.endpoint
        prompt = (
            "doctor-probe"
            if endpoint.adapter == "deterministic"
            else "Return exactly READY as a readiness check. Do not use tools or modify files."
        )
        result = binding.port.invoke(
            binding.request(role="live-diagnostic", prompt=prompt)
        )
        if result.status != "success" or not result.output_text.strip():
            raise ModelInvocationError("readiness probe returned no successful text")
        return ModelReadiness(
            target_name=target_name,
            endpoint_name=endpoint.name,
            model=binding.current.target.model,
            adapter=endpoint.adapter,
            readiness="usable",
            reason=None,
        )
    except AdapterFactoryError as exc:
        return ModelReadiness(target_name, None, None, None, "unsupported", str(exc))
    except (ModelInvocationError, ModelTargetResolutionError, ValueError) as exc:
        return ModelReadiness(target_name, None, None, None, "unavailable", str(exc))


__all__ = [
    "DetectedModel",
    "ModelReadiness",
    "discover_installed_models",
    "probe_target_readiness",
    "resolve_target_readiness",
]
