"""Idempotent local GigAI setup orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import shlex

from .config import (
    CONFIG_SCHEMA_VERSION,
    CredentialReference,
    Endpoint,
    GigAIConfig,
    ModelTarget,
    Profile,
    ReadOnlyConfigurationError,
    StandardPack,
    config_path,
    load_config,
    normalize_config,
    write_config_atomic,
)
from .credentials import validate_reference
from .diagnostics import DiagnosticCheck, run_mount_probes
from .standard_pack import (
    PACK_NAME,
    PACK_VERSION,
    materialize_standard_pack,
    pack_digest,
    pack_path,
    verify_standard_pack,
)


@dataclass(frozen=True)
class SetupResult:
    config: GigAIConfig
    config_changed: bool
    pack_changed: bool
    mount_checks: tuple[DiagnosticCheck, ...]


def default_home_root() -> Path:
    return Path(os.environ.get("GIGAI_HOME", Path.home() / ".gigai")).expanduser()


def default_workpad_root(home_root: Path) -> Path:
    configured = os.environ.get("GIGAI_WORKPAD_ROOT")
    return Path(configured).expanduser() if configured else home_root / "workpads"


def resolve_editor_argv(
    editor: str | None, editor_args: tuple[str, ...] = ()
) -> tuple[str, ...]:
    if editor is not None:
        base_argv = (editor,)
    else:
        configured = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        try:
            base_argv = tuple(shlex.split(configured)) if configured else ()
        except ValueError as exc:
            raise ValueError(f"configured editor environment is malformed: {exc}") from exc
    if not base_argv:
        raise ValueError(
            "no editor is configured; pass --editor or set VISUAL or EDITOR"
        )
    argv = (*base_argv, *editor_args)
    if any(not item or "\0" in item for item in argv):
        raise ValueError("editor argv must contain only non-empty NUL-free values")
    if shutil.which(argv[0]) is None:
        raise ValueError(f"editor executable {argv[0]!r} cannot be resolved")
    return argv


def build_config(
    *,
    home_root: Path,
    workpad_root: Path,
    editor_argv: tuple[str, ...],
    open_with_target: bool,
    credentials: tuple[CredentialReference, ...] = (),
) -> GigAIConfig:
    for reference in credentials:
        validate_reference(reference)
    offline_target = "offline-default"
    return normalize_config(
        GigAIConfig(
            schema_version=CONFIG_SCHEMA_VERSION,
            home_root=home_root.expanduser().resolve(strict=False),
            workpad_root=workpad_root.expanduser().resolve(strict=False),
            editor_argv=editor_argv,
            open_with_target=open_with_target,
            credentials=credentials,
            endpoints=(Endpoint(name="offline", adapter="deterministic"),),
            model_targets=(
                ModelTarget(name=offline_target, endpoint="offline", model="fixture-v1"),
            ),
            profiles=(
                Profile(
                    name="default",
                    planner=offline_target,
                    critic=offline_target,
                    adjudicator=offline_target,
                ),
            ),
            standard_pack=StandardPack(
                name=PACK_NAME,
                version=PACK_VERSION,
                content_digest=pack_digest(),
            ),
        )
    )


def run_setup(config: GigAIConfig) -> SetupResult:
    """Validate first, then materialize one canonical configuration and pack."""

    config = normalize_config(config)
    path = config_path(config.home_root)
    if path.exists():
        existing = load_config(config.home_root)
        if path.stat().st_mode & 0o222 == 0:
            raise ReadOnlyConfigurationError(
                f"configuration at {path} is read-only; no changes were made"
            )
        # Refuse silent reinterpretation of a different configured home.
        if existing.home_root.resolve(strict=False) != config.home_root.resolve(strict=False):
            raise ValueError(
                f"existing configuration owns home {existing.home_root}; no migration was attempted"
            )
    expected_pack = pack_path(config.home_root)
    if expected_pack.exists():
        valid, message = verify_standard_pack(config.home_root)
        if not valid:
            raise ValueError(message)

    created_workpad_paths: list[Path] = []
    try:
        if not config.workpad_root.exists():
            cursor = config.workpad_root
            while not cursor.exists():
                created_workpad_paths.append(cursor)
                if cursor.parent == cursor:
                    break
                cursor = cursor.parent
            config.workpad_root.mkdir(parents=True, exist_ok=False)
        mount_checks = run_mount_probes(config.workpad_root)
        failed = [check for check in mount_checks if check.status == "FAIL"]
        if failed:
            raise ValueError("; ".join(check.summary for check in failed))
    except Exception:
        for created in created_workpad_paths:
            try:
                created.rmdir()
            except OSError:
                break
        raise

    for relative in ("credentials", "catalogs", "capabilities", "learning"):
        (config.home_root / relative).mkdir(parents=True, exist_ok=True)
    _, pack_changed = materialize_standard_pack(config.home_root)
    config_changed = write_config_atomic(config)
    return SetupResult(
        config=config,
        config_changed=config_changed,
        pack_changed=pack_changed,
        mount_checks=mount_checks,
    )


__all__ = [
    "SetupResult",
    "build_config",
    "default_home_root",
    "default_workpad_root",
    "resolve_editor_argv",
    "run_setup",
]
