"""Read-only discovery and readiness reporting for Gig builder model targets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import uuid
from typing import Callable, Mapping

from .adapters.factory import AdapterFactoryError, resolve_model_adapter
from .adapters.port import ModelAuthenticationRequired, ModelInvocationError
from .adapters.process import allowed_environment
from .canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from .config import GigAIConfig
from .model_targets import ModelTargetResolutionError


DISCOVERY_SCHEMA_VERSION = "1.0"
DISCOVERY_TIMEOUT_SECONDS = 5
DISCOVERY_OUTPUT_LIMIT = 64 * 1024
_PATH_SENTINEL = "__GIGAI_DISCOVERY_PATH__"
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SUPPORTED_COMMANDS = ("codex", "claude")
_BOUNDED_INSTALL_DIRECTORIES = (
    Path("~/.local/bin"),
    Path("~/.asdf/shims"),
    Path("~/Library/pnpm"),
    Path("~/.npm-global/bin"),
)


@dataclass(frozen=True)
class DetectedModel:
    """A locally visible executable; detection never invokes it."""

    name: str
    executable: Path | None
    readiness: str
    version: str | None = None
    resolution: str = "path"
    path_source: str = "process"
    failure_code: str | None = None


@dataclass(frozen=True)
class DiscoverySnapshot:
    """Immutable evidence for one local runtime discovery operation."""

    operation_id: str
    captured_at: str
    runtime_identity: str
    path_source: str
    effective_path: str | None
    hydration_status: str
    hydration_reason: str | None
    refresh_reason: str
    models: tuple[DetectedModel, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DISCOVERY_SCHEMA_VERSION,
            "operation_id": self.operation_id,
            "captured_at": self.captured_at,
            "runtime_identity": self.runtime_identity,
            "path_source": self.path_source,
            "effective_path": self.effective_path,
            "hydration": {
                "status": self.hydration_status,
                "reason": self.hydration_reason,
            },
            "refresh_reason": self.refresh_reason,
            "models": [
                {
                    "name": item.name,
                    "executable": str(item.executable) if item.executable else None,
                    "readiness": item.readiness,
                    "version": item.version,
                    "resolution": item.resolution,
                    "path_source": item.path_source,
                    "failure_code": item.failure_code,
                }
                for item in self.models
            ],
        }

    def to_shareable_dict(self) -> dict[str, object]:
        """Return report-safe evidence without local filesystem locations."""

        payload = self.to_dict()
        payload["effective_path"] = "<redacted>" if self.effective_path else None
        payload["models"] = [
            {
                **item,
                "executable": "<redacted>" if item["executable"] else None,
            }
            for item in payload["models"]
        ]
        return payload


@dataclass(frozen=True)
class ModelReadiness:
    """The durable distinction between configuration and usable invocation."""

    target_name: str
    endpoint_name: str | None
    model: str | None
    adapter: str | None
    readiness: str
    reason: str | None
    states: tuple[str, ...] = ()


def discover_installed_models(
    *,
    which: Callable[[str], str | None] | None = None,
    path: str | None = None,
) -> tuple[DetectedModel, ...]:
    """Detect supported CLIs and collect bounded, read-only version evidence."""

    effective_path = path if path is not None else os.environ.get("PATH", "")
    models: list[DetectedModel] = []
    for name in _SUPPORTED_COMMANDS:
        resolved = (
            which(name)
            if which is not None
            else shutil.which(name, path=effective_path)
        )
        resolution = "path"
        model_path_source = "process"
        if resolved is None and which is None:
            fallback = _resolve_bounded_fallback(name)
            if fallback is not None:
                resolved = str(fallback)
                resolution = "install_directory_fallback"
                model_path_source = "fallback"
        if resolved is None:
            models.append(
                DetectedModel(
                    name,
                    None,
                    "unavailable",
                    None,
                    resolution,
                    model_path_source,
                    "executable_not_found",
                )
            )
            continue
        version = _probe_version(resolved, path=effective_path)
        models.append(
            DetectedModel(
                name,
                Path(resolved),
                "detected",
                version,
                resolution,
                model_path_source,
                None if version is not None else "version_unavailable",
            )
        )
    return tuple(models)


def discover_runtime_snapshot(
    *,
    refresh_reason: str = "command",
    shell: str | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    captured_at: datetime | None = None,
) -> DiscoverySnapshot:
    """Capture one bounded, local runtime snapshot without provider activity."""

    effective_path, path_source, hydration_status, hydration_reason = (
        hydrate_login_shell_path(shell=shell)
    )
    models = discover_installed_models(path=effective_path)
    return DiscoverySnapshot(
        operation_id=f"discovery_{uuid_factory()}",
        captured_at=_timestamp(captured_at),
        runtime_identity=_runtime_identity(),
        path_source=path_source,
        effective_path=effective_path,
        hydration_status=hydration_status,
        hydration_reason=hydration_reason,
        refresh_reason=refresh_reason,
        models=tuple(
            DetectedModel(
                item.name,
                item.executable,
                item.readiness,
                item.version,
                item.resolution,
                path_source if item.path_source == "process" else item.path_source,
                item.failure_code,
            )
            for item in models
        ),
    )


def hydrate_login_shell_path(
    *, shell: str | None = None
) -> tuple[str | None, str, str, str | None]:
    """Resolve a login-shell PATH with fixed command text and bounded output."""

    selected_shell = shell or os.environ.get("SHELL") or "/bin/sh"
    shell_path = selected_shell
    if not Path(shell_path).is_file():
        shell_path = shutil.which(selected_shell) or selected_shell
    command = f"printf '\\036{_PATH_SENTINEL}=\\036%s\\036' \"$PATH\""
    environment = allowed_environment(extra_names=("SHELL",))
    environment["SHELL"] = shell_path
    try:
        result = subprocess.run(
            [shell_path, "-ilc", command],
            cwd=Path.home(),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            timeout=DISCOVERY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return os.environ.get("PATH"), "process", "failed", "shell_timeout"
    except OSError as exc:
        return os.environ.get("PATH"), "process", "failed", f"shell_error:{type(exc).__name__}"
    output = _ANSI_ESCAPE.sub("", f"{result.stdout}\n{result.stderr}")
    if len(output) > DISCOVERY_OUTPUT_LIMIT:
        return os.environ.get("PATH"), "process", "failed", "shell_output_too_large"
    match = re.search(rf"{re.escape(_PATH_SENTINEL)}=\x1e([^\x1e\r\n]*)\x1e", output)
    if match is None:
        return os.environ.get("PATH"), "process", "failed", "shell_sentinel_missing"
    hydrated = match.group(1)
    if not hydrated:
        return os.environ.get("PATH"), "process", "failed", "shell_path_empty"
    if result.returncode != 0:
        return hydrated, "login_shell", "failed", f"shell_exit:{result.returncode}"
    return hydrated, "login_shell", "ready", None


def persist_discovery_snapshot(home_root: Path, snapshot: DiscoverySnapshot) -> Path:
    """Persist one content-addressed local evidence record, never authority."""

    directory = home_root / "snapshots" / "runtime-discovery"
    directory.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(snapshot.to_dict())
    record = {
        **snapshot.to_dict(),
        "content_sha256": digest_imported_bytes(payload),
        "size_bytes": len(payload),
    }
    encoded = canonical_json_bytes(record)
    destination = directory / f"{snapshot.operation_id}.json"
    if destination.exists():
        if destination.read_bytes() != encoded:
            raise ValueError("discovery snapshot identity already contains different evidence")
        return destination
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, destination)
    return destination


def target_configuration_digest(config: GigAIConfig, target_name: str) -> str:
    """Return the exact target/endpoint identity that a readiness proof binds."""

    target = next((item for item in config.model_targets if item.name == target_name), None)
    if target is None:
        raise ModelTargetResolutionError(f"unknown model target {target_name!r}")
    endpoint = next((item for item in config.endpoints if item.name == target.endpoint), None)
    if endpoint is None:
        raise ModelTargetResolutionError(
            f"model target {target_name!r} references missing endpoint {target.endpoint!r}"
        )
    return digest_imported_bytes(canonical_json_bytes({
        "endpoint": {
            "name": endpoint.name,
            "adapter": endpoint.adapter,
            "credential": endpoint.credential,
            "base_url": endpoint.base_url,
        },
        "target": {
            "name": target.name,
            "endpoint": target.endpoint,
            "model": target.model,
            "capabilities": list(target.capabilities),
            "max_output_tokens": target.max_output_tokens,
            "reasoning_effort": target.reasoning_effort,
            "enabled": target.enabled,
        },
    }))


def persist_target_readiness(
    home_root: Path,
    config: GigAIConfig,
    readiness: ModelReadiness,
) -> Path:
    """Persist one private explicit probe result bound to current target config.

    A readiness record is evidence, not configuration authority.  It becomes
    unusable automatically when any sealed target/endpoint field changes.
    """

    target_digest = target_configuration_digest(config, readiness.target_name)
    directory = home_root / "snapshots" / "model-readiness"
    directory.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes({
        "schema_version": "1.0",
        "target_name": readiness.target_name,
        "target_configuration_sha256": target_digest,
        "readiness": readiness.readiness,
        "reason": readiness.reason,
        "recorded_at": _timestamp(None),
    })
    destination = directory / f"{readiness.target_name}-{target_digest.removeprefix('sha256:')}.json"
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return destination


def recorded_target_readiness(
    home_root: Path,
    config: GigAIConfig,
    target_name: str,
) -> ModelReadiness | None:
    """Read a usable explicit probe bound to the current target configuration."""

    try:
        target_digest = target_configuration_digest(config, target_name)
    except ModelTargetResolutionError:
        return None
    path = home_root / "snapshots" / "model-readiness" / f"{target_name}-{target_digest.removeprefix('sha256:')}.json"
    try:
        payload = parse_json_bytes(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    if (
        payload.get("schema_version") != "1.0"
        or payload.get("target_name") != target_name
        or payload.get("target_configuration_sha256") != target_digest
        or payload.get("readiness") != "usable"
    ):
        return None
    return ModelReadiness(
        target_name=target_name,
        endpoint_name=None,
        model=None,
        adapter=None,
        readiness="usable",
        reason=None,
        states=("configured", "explicit_probe", "usable"),
    )


def _resolve_bounded_fallback(name: str) -> Path | None:
    for directory in _BOUNDED_INSTALL_DIRECTORIES:
        candidate = directory.expanduser() / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve(strict=False)
    return None


def _runtime_identity() -> str:
    return f"local:{platform.system().lower()}:{platform.machine().lower()}"


def _timestamp(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _probe_version(executable: str, *, path: str | None = None) -> str | None:
    """Read one CLI version line without invoking a model or inheriting secrets."""

    try:
        with tempfile.TemporaryDirectory(prefix="gigai-version-") as directory:
            result = subprocess.run(
                [executable, "--version"],
                cwd=directory,
                env={**allowed_environment(), **({"PATH": path} if path is not None else {})},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=DISCOVERY_TIMEOUT_SECONDS,
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


def resolve_target_readiness(
    config: GigAIConfig,
    target_name: str,
    *,
    executable_overrides: Mapping[str, str] | None = None,
) -> ModelReadiness:
    """Resolve a configured target without making a provider/model call.

    A constructed adapter proves only that the typed configuration is usable
    by the factory.  It does not prove provider authentication or command
    compatibility; that distinction belongs to :func:`probe_target_readiness`.
    """

    try:
        if executable_overrides is None:
            binding = resolve_model_adapter(config, target_name)
        else:
            binding = resolve_model_adapter(
                config, target_name, executable_overrides=executable_overrides
            )
    except ModelTargetResolutionError as exc:
        return ModelReadiness(target_name, None, None, None, "unavailable", str(exc), ())
    except AdapterFactoryError as exc:
        return ModelReadiness(target_name, None, None, None, "unsupported", str(exc), ())
    except ModelInvocationError as exc:
        return ModelReadiness(target_name, None, None, None, "unavailable", str(exc), ())
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
        states=(
            "configured",
            "compatible",
            "verified",
            "usable",
        )
        if endpoint.adapter == "deterministic"
        else ("configured",),
    )


def probe_target_readiness(
    config: GigAIConfig,
    target_name: str,
    *,
    executable_overrides: Mapping[str, str] | None = None,
) -> ModelReadiness:
    """Run one explicit bounded readiness invocation for a configured target.

    Callers must expose this as an opt-in action.  This function may resolve a
    provider credential and may incur provider cost; ordinary discovery and
    setup rendering must call ``resolve_target_readiness`` instead.
    """

    try:
        if executable_overrides is None:
            binding = resolve_model_adapter(config, target_name)
        else:
            binding = resolve_model_adapter(
                config, target_name, executable_overrides=executable_overrides
            )
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
            states=("configured", "compatible", "authenticated", "verified", "usable"),
        )
    except ModelAuthenticationRequired as exc:
        return ModelReadiness(
            target_name=target_name,
            endpoint_name=endpoint.name,
            model=binding.current.target.model,
            adapter=endpoint.adapter,
            readiness="configured",
            reason=str(exc),
            states=("configured", "compatible"),
        )
    except AdapterFactoryError as exc:
        return ModelReadiness(target_name, None, None, None, "unsupported", str(exc), ())
    except (ModelInvocationError, ModelTargetResolutionError, ValueError) as exc:
        return ModelReadiness(target_name, None, None, None, "unavailable", str(exc), ())


__all__ = [
    "DetectedModel",
    "DiscoverySnapshot",
    "ModelReadiness",
    "discover_runtime_snapshot",
    "discover_installed_models",
    "hydrate_login_shell_path",
    "persist_discovery_snapshot",
    "persist_target_readiness",
    "probe_target_readiness",
    "recorded_target_readiness",
    "resolve_target_readiness",
    "target_configuration_digest",
]
