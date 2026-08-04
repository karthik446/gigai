"""Typed, versioned, canonical machine configuration for GigAI."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib
from typing import Any

from .canonical import canonicalize_owned_text


CONFIG_SCHEMA_VERSION = "2.0"
PREVIOUS_CONFIG_SCHEMA_VERSION = "1.0"
CONFIG_FILENAME = "config.toml"
_CAPABILITY = re.compile(r"[a-z][a-z0-9_-]*\Z")
_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


class ConfigurationError(ValueError):
    """A configuration cannot be read without guessing or migration."""

    code = "configuration_invalid"


class MissingConfigurationError(ConfigurationError):
    code = "configuration_missing"


class MalformedConfigurationError(ConfigurationError):
    code = "configuration_malformed"


class UnsupportedConfigurationVersionError(ConfigurationError):
    code = "configuration_version_unsupported"


class ReadOnlyConfigurationError(ConfigurationError):
    code = "configuration_read_only"


class ConfigurationMigrationError(ConfigurationError):
    code = "configuration_migration_failed"


@dataclass(frozen=True)
class CredentialReference:
    name: str
    kind: str
    reference: str


@dataclass(frozen=True)
class Endpoint:
    name: str
    adapter: str
    credential: str | None = None
    base_url: str | None = None


@dataclass(frozen=True)
class ModelTarget:
    name: str
    endpoint: str
    model: str
    capabilities: tuple[str, ...]
    max_output_tokens: int
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class Profile:
    name: str
    planner: str
    critic: str
    adjudicator: str


@dataclass(frozen=True)
class StandardPack:
    name: str
    version: str
    content_digest: str


@dataclass(frozen=True)
class GigAIConfig:
    schema_version: str
    home_root: Path
    workpad_root: Path
    editor_argv: tuple[str, ...]
    open_with_target: bool
    credentials: tuple[CredentialReference, ...]
    endpoints: tuple[Endpoint, ...]
    model_targets: tuple[ModelTarget, ...]
    profiles: tuple[Profile, ...]
    standard_pack: StandardPack


def config_path(home_root: Path) -> Path:
    return home_root / CONFIG_FILENAME


def render_config(config: GigAIConfig) -> bytes:
    """Render stable TOML bytes. Values are escaped without shell interpretation."""

    lines = [
        f'schema_version = {_toml_string(config.schema_version)}',
        *(('credentials = []',) if not config.credentials else ()),
        "",
        "[paths]",
        f'home_root = {_toml_string(os.fspath(config.home_root))}',
        f'workpad_root = {_toml_string(os.fspath(config.workpad_root))}',
        "",
        "[editor]",
        f"argv = {_toml_array(config.editor_argv)}",
        f"open_with_target = {str(config.open_with_target).lower()}",
    ]
    for credential in sorted(config.credentials, key=lambda item: item.name):
        lines.extend(
            (
                "",
                "[[credentials]]",
                f'name = {_toml_string(credential.name)}',
                f'kind = {_toml_string(credential.kind)}',
                f'reference = {_toml_string(credential.reference)}',
            )
        )
    for endpoint in sorted(config.endpoints, key=lambda item: item.name):
        lines.extend(("", "[[endpoints]]", f'name = {_toml_string(endpoint.name)}'))
        lines.append(f'adapter = {_toml_string(endpoint.adapter)}')
        if endpoint.credential is not None:
            lines.append(f'credential = {_toml_string(endpoint.credential)}')
        if endpoint.base_url is not None:
            lines.append(f'base_url = {_toml_string(endpoint.base_url)}')
    for target in sorted(config.model_targets, key=lambda item: item.name):
        lines.extend(
            (
                "",
                "[[model_targets]]",
                f'name = {_toml_string(target.name)}',
                f'endpoint = {_toml_string(target.endpoint)}',
                f'model = {_toml_string(target.model)}',
                f"capabilities = {_toml_array(target.capabilities)}",
                f"max_output_tokens = {target.max_output_tokens}",
            )
        )
        if target.reasoning_effort is not None:
            lines.append(f'reasoning_effort = {_toml_string(target.reasoning_effort)}')
    for profile in sorted(config.profiles, key=lambda item: item.name):
        lines.extend(
            (
                "",
                "[[profiles]]",
                f'name = {_toml_string(profile.name)}',
                f'planner = {_toml_string(profile.planner)}',
                f'critic = {_toml_string(profile.critic)}',
                f'adjudicator = {_toml_string(profile.adjudicator)}',
            )
        )
    lines.extend(
        (
            "",
            "[standard_pack]",
            f'name = {_toml_string(config.standard_pack.name)}',
            f'version = {_toml_string(config.standard_pack.version)}',
            f'content_digest = {_toml_string(config.standard_pack.content_digest)}',
        )
    )
    return canonicalize_owned_text("\n".join(lines))


def load_config(home_root: Path) -> GigAIConfig:
    path = config_path(home_root)
    if not path.is_file():
        raise MissingConfigurationError(
            f"configuration is missing at {path}; run 'gigai setup'"
        )
    payload = _read_payload(path)
    version = _schema_version(payload, source=path)
    if version == PREVIOUS_CONFIG_SCHEMA_VERSION:
        raise UnsupportedConfigurationVersionError(
            f"configuration schema {version!r} at {path} requires the explicit "
            "v1-to-v2 migration; rerun 'gigai setup'"
        )
    if version != CONFIG_SCHEMA_VERSION:
        raise UnsupportedConfigurationVersionError(
            f"configuration schema {version!r} at {path} is unsupported; expected "
            f"{CONFIG_SCHEMA_VERSION!r}; no migration was attempted"
        )
    return parse_config(payload, source=path)


def migrate_config(home_root: Path) -> tuple[GigAIConfig, bool]:
    """Explicitly migrate the sole supported v1 predecessor to v2."""

    path = config_path(home_root)
    if not path.is_file():
        raise MissingConfigurationError(
            f"configuration is missing at {path}; run 'gigai setup'"
        )
    payload = _read_payload(path)
    version = _schema_version(payload, source=path)
    if version == CONFIG_SCHEMA_VERSION:
        return parse_config(payload, source=path), False
    if version != PREVIOUS_CONFIG_SCHEMA_VERSION:
        raise UnsupportedConfigurationVersionError(
            f"configuration schema {version!r} at {path} is unsupported; expected "
            f"{PREVIOUS_CONFIG_SCHEMA_VERSION!r} or {CONFIG_SCHEMA_VERSION!r}; "
            "no migration was attempted"
        )
    try:
        migrated = _parse_config_v1(payload, source=path)
        changed = write_config_atomic(migrated)
    except (ConfigurationError, OSError, ValueError) as exc:
        raise ConfigurationMigrationError(
            f"configuration v1-to-v2 migration at {path} failed: {exc}"
        ) from exc
    return migrated, changed


def normalize_config(config: GigAIConfig) -> GigAIConfig:
    """Validate a constructed config and return its canonical field ordering."""

    try:
        payload = tomllib.loads(render_config(config).decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise MalformedConfigurationError(
            f"constructed configuration cannot be rendered as canonical TOML: {exc}"
        ) from exc
    return parse_config(payload)


def _parse_config_v1(payload: object, *, source: Path | None = None) -> GigAIConfig:
    where = f" at {source}" if source else ""
    root = _table(payload, "configuration", where)
    version = _string(root, "schema_version", "configuration", where)
    if version != PREVIOUS_CONFIG_SCHEMA_VERSION:
        raise UnsupportedConfigurationVersionError(
            f"configuration schema {version!r}{where} is unsupported; expected "
            f"{PREVIOUS_CONFIG_SCHEMA_VERSION!r}; no migration was attempted"
        )
    _exact_keys(
        root,
        {
            "schema_version",
            "paths",
            "editor",
            "credentials",
            "endpoints",
            "model_targets",
            "profiles",
            "standard_pack",
        },
        "configuration",
        where,
    )
    paths = _table(root.get("paths"), "paths", where)
    _exact_keys(paths, {"home_root", "workpad_root"}, "paths", where)
    editor = _table(root.get("editor"), "editor", where)
    _exact_keys(editor, {"argv", "open_with_target"}, "editor", where)
    editor_argv = _string_array(editor, "argv", "editor", where)
    if not editor_argv:
        raise MalformedConfigurationError(f"editor.argv{where} must not be empty")
    open_with_target = editor.get("open_with_target")
    if type(open_with_target) is not bool:
        raise MalformedConfigurationError(
            f"editor.open_with_target{where} must be a boolean"
        )

    credentials = tuple(
        _credential(item, index, where)
        for index, item in enumerate(_array(root, "credentials", where))
    )
    endpoints = tuple(
        _endpoint_v1(item, index, where)
        for index, item in enumerate(_array(root, "endpoints", where))
    )
    targets = tuple(
        _target_v1(item, index, where)
        for index, item in enumerate(_array(root, "model_targets", where))
    )
    profiles = tuple(
        _profile(item, index, where)
        for index, item in enumerate(_array(root, "profiles", where))
    )
    pack = _pack(root.get("standard_pack"), where)
    _unique_names(credentials, "credentials", where)
    _unique_names(endpoints, "endpoints", where)
    _unique_names(targets, "model_targets", where)
    _unique_names(profiles, "profiles", where)
    endpoint_names = {item.name for item in endpoints}
    target_names = {item.name for item in targets}
    if not endpoints or not targets or not profiles:
        raise MalformedConfigurationError(
            f"endpoints, model_targets, and profiles{where} must not be empty"
        )
    for target in targets:
        if target.endpoint not in endpoint_names:
            raise MalformedConfigurationError(
                f"model target {target.name!r}{where} references unknown endpoint "
                f"{target.endpoint!r}"
            )
    for profile in profiles:
        for role in (profile.planner, profile.critic, profile.adjudicator):
            if role not in target_names:
                raise MalformedConfigurationError(
                    f"profile {profile.name!r}{where} references unknown model target {role!r}"
                )
    home_root = Path(_string(paths, "home_root", "paths", where)).expanduser()
    workpad_root = Path(_string(paths, "workpad_root", "paths", where)).expanduser()
    if not home_root.is_absolute() or not workpad_root.is_absolute():
        raise MalformedConfigurationError(
            f"paths.home_root and paths.workpad_root{where} must be absolute"
        )
    return GigAIConfig(
        schema_version=CONFIG_SCHEMA_VERSION,
        home_root=home_root,
        workpad_root=workpad_root,
        editor_argv=editor_argv,
        open_with_target=open_with_target,
        credentials=credentials,
        endpoints=endpoints,
        model_targets=targets,
        profiles=profiles,
        standard_pack=pack,
    )


def parse_config(payload: object, *, source: Path | None = None) -> GigAIConfig:
    """Parse only the current strict v2 configuration shape."""

    where = f" at {source}" if source else ""
    root = _table(payload, "configuration", where)
    version = _string(root, "schema_version", "configuration", where)
    if version != CONFIG_SCHEMA_VERSION:
        raise UnsupportedConfigurationVersionError(
            f"configuration schema {version!r}{where} is unsupported; expected "
            f"{CONFIG_SCHEMA_VERSION!r}; no migration was attempted"
        )
    _exact_keys(
        root,
        {
            "schema_version",
            "paths",
            "editor",
            "credentials",
            "endpoints",
            "model_targets",
            "profiles",
            "standard_pack",
        },
        "configuration",
        where,
    )
    paths = _table(root.get("paths"), "paths", where)
    _exact_keys(paths, {"home_root", "workpad_root"}, "paths", where)
    editor = _table(root.get("editor"), "editor", where)
    _exact_keys(editor, {"argv", "open_with_target"}, "editor", where)
    editor_argv = _string_array(editor, "argv", "editor", where)
    if not editor_argv:
        raise MalformedConfigurationError(f"editor.argv{where} must not be empty")
    open_with_target = editor.get("open_with_target")
    if type(open_with_target) is not bool:
        raise MalformedConfigurationError(
            f"editor.open_with_target{where} must be a boolean"
        )
    credentials = tuple(
        _credential(item, index, where)
        for index, item in enumerate(_array(root, "credentials", where))
    )
    endpoints = tuple(
        _endpoint(item, index, where)
        for index, item in enumerate(_array(root, "endpoints", where))
    )
    targets = tuple(
        _target(item, index, where)
        for index, item in enumerate(_array(root, "model_targets", where))
    )
    profiles = tuple(
        _profile(item, index, where)
        for index, item in enumerate(_array(root, "profiles", where))
    )
    pack = _pack(root.get("standard_pack"), where)
    _validate_config_relationships(credentials, endpoints, targets, profiles, where)
    home_root = Path(_string(paths, "home_root", "paths", where)).expanduser()
    workpad_root = Path(_string(paths, "workpad_root", "paths", where)).expanduser()
    if not home_root.is_absolute() or not workpad_root.is_absolute():
        raise MalformedConfigurationError(
            f"paths.home_root and paths.workpad_root{where} must be absolute"
        )
    return GigAIConfig(
        schema_version=version,
        home_root=home_root,
        workpad_root=workpad_root,
        editor_argv=editor_argv,
        open_with_target=open_with_target,
        credentials=credentials,
        endpoints=endpoints,
        model_targets=targets,
        profiles=profiles,
        standard_pack=pack,
    )


def write_config_atomic(config: GigAIConfig) -> bool:
    """Atomically create or replace config, returning whether bytes changed."""

    config = normalize_config(config)
    path = config_path(config.home_root)
    rendered = render_config(config)
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o222 == 0:
            raise ReadOnlyConfigurationError(
                f"configuration at {path} is read-only; no changes were made"
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
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _read_payload(path: Path) -> object:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise MalformedConfigurationError(
            f"configuration at {path} is not valid UTF-8 TOML: {exc}"
        ) from exc


def _schema_version(payload: object, *, source: Path) -> str:
    where = f" at {source}"
    root = _table(payload, "configuration", where)
    return _string(root, "schema_version", "configuration", where)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _table(value: object, name: str, where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise MalformedConfigurationError(f"{name}{where} must be a table")
    return value


def _exact_keys(
    value: dict[str, Any], expected: set[str], name: str, where: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise MalformedConfigurationError(
            f"{name}{where} has invalid fields; missing={missing}, unknown={unknown}"
        )


def _allowed_keys(
    value: dict[str, Any],
    required: set[str],
    optional: set[str],
    name: str,
    where: str,
) -> None:
    actual = set(value)
    missing = sorted(required - actual)
    unknown = sorted(actual - required - optional)
    if missing or unknown:
        raise MalformedConfigurationError(
            f"{name}{where} has invalid fields; missing={missing}, unknown={unknown}"
        )


def _string(value: dict[str, Any], key: str, name: str, where: str) -> str:
    result = value.get(key)
    if type(result) is not str or not result or "\0" in result:
        raise MalformedConfigurationError(
            f"{name}.{key}{where} must be a non-empty NUL-free string"
        )
    return result


def _optional_string(
    value: dict[str, Any], key: str, name: str, where: str
) -> str | None:
    if key not in value:
        return None
    return _string(value, key, name, where)


def _positive_integer(value: dict[str, Any], key: str, name: str, where: str) -> int:
    result = value.get(key)
    if type(result) is not int or result <= 0:
        raise MalformedConfigurationError(
            f"{name}.{key}{where} must be a positive integer"
        )
    return result


def _string_array(
    value: dict[str, Any], key: str, name: str, where: str
) -> tuple[str, ...]:
    items = value.get(key)
    if type(items) is not list or any(
        type(item) is not str or not item or "\0" in item for item in items
    ):
        raise MalformedConfigurationError(
            f"{name}.{key}{where} must be an array of non-empty NUL-free strings"
        )
    return tuple(items)


def _array(value: dict[str, Any], key: str, where: str) -> list[object]:
    result = value.get(key)
    if type(result) is not list:
        raise MalformedConfigurationError(f"{key}{where} must be an array of tables")
    return result


def _credential(value: object, index: int, where: str) -> CredentialReference:
    item = _table(value, f"credentials[{index}]", where)
    _exact_keys(item, {"name", "kind", "reference"}, f"credentials[{index}]", where)
    kind = _string(item, "kind", f"credentials[{index}]", where)
    if kind not in {"environment", "secret-manager"}:
        raise MalformedConfigurationError(
            f"credentials[{index}].kind{where} must be 'environment' or 'secret-manager'"
        )
    return CredentialReference(
        name=_string(item, "name", f"credentials[{index}]", where),
        kind=kind,
        reference=_string(item, "reference", f"credentials[{index}]", where),
    )


def _endpoint_v1(value: object, index: int, where: str) -> Endpoint:
    item = _table(value, f"endpoints[{index}]", where)
    _exact_keys(item, {"name", "adapter"}, f"endpoints[{index}]", where)
    return Endpoint(
        name=_string(item, "name", f"endpoints[{index}]", where),
        adapter=_string(item, "adapter", f"endpoints[{index}]", where),
    )


def _target_v1(value: object, index: int, where: str) -> ModelTarget:
    item = _table(value, f"model_targets[{index}]", where)
    _exact_keys(item, {"name", "endpoint", "model"}, f"model_targets[{index}]", where)
    return ModelTarget(
        name=_string(item, "name", f"model_targets[{index}]", where),
        endpoint=_string(item, "endpoint", f"model_targets[{index}]", where),
        model=_string(item, "model", f"model_targets[{index}]", where),
        capabilities=("text",),
        max_output_tokens=64,
        reasoning_effort=None,
    )


def _endpoint(value: object, index: int, where: str) -> Endpoint:
    item = _table(value, f"endpoints[{index}]", where)
    required = {"name", "adapter"}
    optional = {"credential", "base_url"}
    _allowed_keys(item, required, optional, f"endpoints[{index}]", where)
    name = _string(item, "name", f"endpoints[{index}]", where)
    adapter = _string(item, "adapter", f"endpoints[{index}]", where)
    if adapter not in {"deterministic", "openai_api", "openrouter_api"}:
        raise MalformedConfigurationError(
            f"endpoints[{index}].adapter{where} is not a supported G11 adapter"
        )
    credential = _optional_string(item, "credential", f"endpoints[{index}]", where)
    base_url = _optional_string(item, "base_url", f"endpoints[{index}]", where)
    if adapter == "deterministic" and (credential is not None or base_url is not None):
        raise MalformedConfigurationError(
            f"deterministic endpoint {name!r}{where} cannot declare credential or base_url"
        )
    if adapter != "deterministic" and credential is None:
        raise MalformedConfigurationError(
            f"endpoint {name!r}{where} requires a credential reference name"
        )
    if base_url is not None and not base_url.startswith("https://"):
        raise MalformedConfigurationError(
            f"endpoint {name!r}.base_url{where} must use https"
        )
    return Endpoint(name=name, adapter=adapter, credential=credential, base_url=base_url)


def _target(value: object, index: int, where: str) -> ModelTarget:
    item = _table(value, f"model_targets[{index}]", where)
    _allowed_keys(
        item,
        {"name", "endpoint", "model", "capabilities", "max_output_tokens"},
        {"reasoning_effort"},
        f"model_targets[{index}]",
        where,
    )
    capabilities = _string_array(item, "capabilities", f"model_targets[{index}]", where)
    if not capabilities or any(not _CAPABILITY.fullmatch(item) for item in capabilities):
        raise MalformedConfigurationError(
            f"model_targets[{index}].capabilities{where} must be non-empty capability names"
        )
    if len(capabilities) != len(set(capabilities)):
        raise MalformedConfigurationError(
            f"model_targets[{index}].capabilities{where} contains duplicates"
        )
    max_output_tokens = _positive_integer(
        item, "max_output_tokens", f"model_targets[{index}]", where
    )
    reasoning_effort = _optional_string(
        item, "reasoning_effort", f"model_targets[{index}]", where
    )
    if reasoning_effort is not None and reasoning_effort not in _REASONING_EFFORTS:
        raise MalformedConfigurationError(
            f"model_targets[{index}].reasoning_effort{where} must be one of "
            f"{sorted(_REASONING_EFFORTS)}"
        )
    return ModelTarget(
        name=_string(item, "name", f"model_targets[{index}]", where),
        endpoint=_string(item, "endpoint", f"model_targets[{index}]", where),
        model=_string(item, "model", f"model_targets[{index}]", where),
        capabilities=tuple(sorted(capabilities)),
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
    )


def _profile(value: object, index: int, where: str) -> Profile:
    item = _table(value, f"profiles[{index}]", where)
    _exact_keys(
        item,
        {"name", "planner", "critic", "adjudicator"},
        f"profiles[{index}]",
        where,
    )
    return Profile(
        name=_string(item, "name", f"profiles[{index}]", where),
        planner=_string(item, "planner", f"profiles[{index}]", where),
        critic=_string(item, "critic", f"profiles[{index}]", where),
        adjudicator=_string(item, "adjudicator", f"profiles[{index}]", where),
    )


def _pack(value: object, where: str) -> StandardPack:
    item = _table(value, "standard_pack", where)
    _exact_keys(item, {"name", "version", "content_digest"}, "standard_pack", where)
    return StandardPack(
        name=_string(item, "name", "standard_pack", where),
        version=_string(item, "version", "standard_pack", where),
        content_digest=_string(item, "content_digest", "standard_pack", where),
    )


def _unique_names(items: tuple[object, ...], label: str, where: str) -> None:
    names = [getattr(item, "name") for item in items]
    if len(names) != len(set(names)):
        raise MalformedConfigurationError(f"{label}{where} contains duplicate names")


def _validate_config_relationships(
    credentials: tuple[CredentialReference, ...],
    endpoints: tuple[Endpoint, ...],
    targets: tuple[ModelTarget, ...],
    profiles: tuple[Profile, ...],
    where: str,
) -> None:
    _unique_names(credentials, "credentials", where)
    _unique_names(endpoints, "endpoints", where)
    _unique_names(targets, "model_targets", where)
    _unique_names(profiles, "profiles", where)
    if not endpoints or not targets or not profiles:
        raise MalformedConfigurationError(
            f"endpoints, model_targets, and profiles{where} must not be empty"
        )
    credential_names = {item.name for item in credentials}
    endpoint_names = {item.name for item in endpoints}
    target_names = {item.name for item in targets}
    for endpoint in endpoints:
        if endpoint.credential is not None and endpoint.credential not in credential_names:
            raise MalformedConfigurationError(
                f"endpoint {endpoint.name!r}{where} references unknown credential "
                f"{endpoint.credential!r}"
            )
    for target in targets:
        if target.endpoint not in endpoint_names:
            raise MalformedConfigurationError(
                f"model target {target.name!r}{where} references unknown endpoint "
                f"{target.endpoint!r}"
            )
    for profile in profiles:
        for role in (profile.planner, profile.critic, profile.adjudicator):
            if role not in target_names:
                raise MalformedConfigurationError(
                    f"profile {profile.name!r}{where} references unknown model target {role!r}"
                )


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "PREVIOUS_CONFIG_SCHEMA_VERSION",
    "ConfigurationError",
    "ConfigurationMigrationError",
    "CredentialReference",
    "Endpoint",
    "GigAIConfig",
    "MalformedConfigurationError",
    "MissingConfigurationError",
    "ModelTarget",
    "Profile",
    "ReadOnlyConfigurationError",
    "StandardPack",
    "UnsupportedConfigurationVersionError",
    "config_path",
    "load_config",
    "migrate_config",
    "normalize_config",
    "parse_config",
    "render_config",
    "write_config_atomic",
]
