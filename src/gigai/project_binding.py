"""Path-free authoritative binding stored inside a Git target."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import tempfile
import tomllib

from .canonical import (
    EntityPrefix,
    InvalidIdentifierError,
    canonicalize_owned_text,
    validate_entity_id,
)


BINDING_SCHEMA_VERSION = "1.0"
BINDING_DIRECTORY = ".gigai"
BINDING_FILENAME = "project.toml"


class ProjectBindingError(ValueError):
    code = "project_binding_invalid"


class MissingProjectBindingError(ProjectBindingError):
    code = "project_binding_missing"


class MalformedProjectBindingError(ProjectBindingError):
    code = "project_binding_malformed"


class UnsupportedProjectBindingVersionError(ProjectBindingError):
    code = "project_binding_version_unsupported"


class ReadOnlyProjectBindingError(ProjectBindingError):
    code = "project_binding_read_only"


@dataclass(frozen=True)
class ProjectBinding:
    schema_version: str
    project_id: str
    workpad_locator: str
    active_gig_id: str | None = None


def binding_path(target_root: Path) -> Path:
    return target_root / BINDING_DIRECTORY / BINDING_FILENAME


def new_project_binding(project_id: str) -> ProjectBinding:
    return normalize_project_binding(
        ProjectBinding(
            schema_version=BINDING_SCHEMA_VERSION,
            project_id=project_id,
            workpad_locator=f"registry:{project_id}",
        )
    )


def render_project_binding(binding: ProjectBinding) -> bytes:
    lines = [
        f'schema_version = {_toml_string(binding.schema_version)}',
        f'project_id = {_toml_string(binding.project_id)}',
    ]
    if binding.active_gig_id is not None:
        lines.append(f'active_gig_id = {_toml_string(binding.active_gig_id)}')
    lines.append(f'workpad_locator = {_toml_string(binding.workpad_locator)}')
    return canonicalize_owned_text("\n".join(lines))


def load_project_binding(target_root: Path) -> ProjectBinding:
    path = binding_path(target_root)
    if not path.is_file():
        raise MissingProjectBindingError(f"project binding is missing at {path}")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise MalformedProjectBindingError(
            f"project binding at {path} is not valid UTF-8 TOML: {exc}"
        ) from exc
    return parse_project_binding(payload, source=path)


def parse_project_binding(
    payload: object, *, source: Path | None = None
) -> ProjectBinding:
    where = f" at {source}" if source else ""
    if type(payload) is not dict:
        raise MalformedProjectBindingError(f"project binding{where} must be a table")
    required = {"schema_version", "project_id", "workpad_locator"}
    allowed = required | {"active_gig_id"}
    actual = set(payload)
    if not required <= actual or not actual <= allowed:
        raise MalformedProjectBindingError(
            f"project binding{where} has invalid fields; "
            f"missing={sorted(required - actual)}, unknown={sorted(actual - allowed)}"
        )
    schema_version = _string(payload, "schema_version", where)
    if schema_version != BINDING_SCHEMA_VERSION:
        raise UnsupportedProjectBindingVersionError(
            f"project binding schema {schema_version!r}{where} is unsupported; "
            f"expected {BINDING_SCHEMA_VERSION!r}; no migration was attempted"
        )
    project_id = _string(payload, "project_id", where)
    try:
        validate_entity_id(project_id, expected_prefix=EntityPrefix.PROJECT)
    except InvalidIdentifierError as exc:
        raise MalformedProjectBindingError(
            f"project_id{where} is not a canonical project UUIDv4"
        ) from exc
    workpad_locator = _string(payload, "workpad_locator", where)
    if workpad_locator != f"registry:{project_id}":
        raise MalformedProjectBindingError(
            f"workpad_locator{where} must be registry:{project_id}"
        )
    active_gig_id: str | None
    if "active_gig_id" not in payload:
        active_gig_id = None
    elif type(payload["active_gig_id"]) is str:
        try:
            active_gig_id = validate_entity_id(
                payload["active_gig_id"], expected_prefix=EntityPrefix.GIG
            )
        except InvalidIdentifierError as exc:
            raise MalformedProjectBindingError(
                f"active_gig_id{where} is not a canonical Gig UUIDv4"
            ) from exc
    else:
        raise MalformedProjectBindingError(
            f"active_gig_id{where} must be a string when present"
        )
    return ProjectBinding(
        schema_version=schema_version,
        project_id=project_id,
        workpad_locator=workpad_locator,
        active_gig_id=active_gig_id,
    )


def normalize_project_binding(binding: ProjectBinding) -> ProjectBinding:
    try:
        payload = tomllib.loads(render_project_binding(binding).decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise MalformedProjectBindingError(
            f"constructed project binding cannot be rendered: {exc}"
        ) from exc
    return parse_project_binding(payload)


def write_project_binding_atomic(target_root: Path, binding: ProjectBinding) -> bool:
    """Atomically create or replace a validated binding if exact bytes differ."""

    binding = normalize_project_binding(binding)
    path = binding_path(target_root)
    rendered = render_project_binding(binding)
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o222 == 0:
            if path.read_bytes() == rendered:
                return False
            raise ReadOnlyProjectBindingError(
                f"project binding at {path} is read-only; no replacement occurred"
            )
        if path.read_bytes() == rendered:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _string(payload: dict[str, object], name: str, where: str) -> str:
    value = payload.get(name)
    if type(value) is not str or not value or "\0" in value:
        raise MalformedProjectBindingError(
            f"{name}{where} must be a non-empty NUL-free string"
        )
    return value


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


__all__ = [
    "BINDING_DIRECTORY",
    "BINDING_FILENAME",
    "BINDING_SCHEMA_VERSION",
    "MalformedProjectBindingError",
    "MissingProjectBindingError",
    "ProjectBinding",
    "ProjectBindingError",
    "ReadOnlyProjectBindingError",
    "UnsupportedProjectBindingVersionError",
    "binding_path",
    "load_project_binding",
    "new_project_binding",
    "normalize_project_binding",
    "parse_project_binding",
    "render_project_binding",
    "write_project_binding_atomic",
]
