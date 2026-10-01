"""The per-project background settings: one reader for the API and the ONE writer of the file.

``<home>/scout/<project_id>/settings.json`` (``refresh_tick.settings_path``)
holds what the operator chose for Scout's background work. Three readers
already exist, each beside the job it switches:

* ``sources.auto_refresh`` -- ``refresh_tick.auto_refresh_setting`` (0110-025);
* ``tagging.model_enabled`` / ``tagging.backfill_enabled`` /
  ``tagging.tag_backfill_model`` -- ``model_tag.tagging_setting`` (0110-024);
* ``snapshot.enabled`` / ``snapshot.manifest_url`` --
  ``snapshot.snapshot_setting`` (0110-026).

This module adds what was missing: :func:`write_background_settings` (the
API's ``PUT /api/settings/background``), which changes only the keys it is
given, keeps every key it does not know, and replaces the file atomically;
and :func:`background_settings`, the body both routes answer, built from
those three readers so the API can never disagree with the jobs.

Nothing here caches: the refresh thread reads the file again at every look
(``RefreshTicker.step``), so a write is honoured at its next look.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from .model_tag import BACKFILL_MODELS, tagging_setting
from .refresh_tick import SETTINGS_SCHEMA, auto_refresh_setting, settings_path
from .snapshot import snapshot_setting

BACKGROUND_SETTINGS_SCHEMA = "scout-background-settings:1"
MAX_MANIFEST_URL_LENGTH = 2048

#: block -> key -> what a value must be ("bool", "backfill_model" or "manifest_url").
_KEYS: dict[str, dict[str, str]] = {
    "sources": {"auto_refresh": "bool"},
    "tagging": {"model_enabled": "bool", "backfill_enabled": "bool", "tag_backfill_model": "backfill_model"},
    "snapshot": {"enabled": "bool", "manifest_url": "manifest_url"},
}

_WRITE_LOCK = threading.Lock()


class SettingsError(ValueError):
    """A settings body the API refuses (``wrong_type`` / ``unknown_key`` / ``bad_enum`` / ``invalid_value``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SettingsUnreadableError(Exception):
    """The stored file is not a settings file this version can read: it is left as it is, never overwritten."""

    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path


class _Remove:
    """A key to drop from the file, so its default applies again."""


_REMOVE = _Remove()


def _value(block: str, key: str, kind: str, value: object) -> object:
    name = f"{block}.{key}"
    if kind == "bool":
        if type(value) is not bool:
            raise SettingsError("wrong_type", f"{name} must be true or false")
        return value
    if kind == "backfill_model":
        if type(value) is not str:
            raise SettingsError("wrong_type", f"{name} must be a string")
        if value not in BACKFILL_MODELS:
            raise SettingsError("bad_enum", f"{name} must be one of: {', '.join(BACKFILL_MODELS)}")
        return value
    # manifest_url: null puts the default location back.
    if value is None:
        return _REMOVE
    if type(value) is not str:
        raise SettingsError("wrong_type", f"{name} must be a string or null")
    url = value.strip()
    parts = urlsplit(url)
    if not url or len(url) > MAX_MANIFEST_URL_LENGTH or parts.scheme not in ("http", "https") or not parts.netloc:
        raise SettingsError("invalid_value", f"{name} must be an http(s) URL of at most {MAX_MANIFEST_URL_LENGTH} characters, or null for the default")
    return url


def validate_patch(body: object) -> dict[str, dict[str, object]]:
    """The settings a ``PUT`` body names, checked. Raises :class:`SettingsError`; never touches the file."""

    if not isinstance(body, dict):
        raise SettingsError("wrong_type", "request body must be a JSON object")
    unknown = sorted(set(body) - set(_KEYS))
    if unknown:
        raise SettingsError("unknown_key", f"unknown field(s): {unknown}")
    patch: dict[str, dict[str, object]] = {}
    for block, keys in _KEYS.items():
        if block not in body:
            continue
        given = body[block]
        if not isinstance(given, dict):
            raise SettingsError("wrong_type", f"{block} must be an object")
        extra = sorted(set(given) - set(keys))
        if extra:
            raise SettingsError("unknown_key", f"{block} has unknown field(s): {extra} (allowed: {', '.join(sorted(keys))})")
        if given:
            patch[block] = {key: _value(block, key, keys[key], value) for key, value in given.items()}
    if not patch:
        raise SettingsError("invalid_value", "name at least one setting to change")
    return patch


def _read(path: Path) -> dict[str, object]:
    """The stored file (an empty settings object when there is none). Raises :class:`SettingsUnreadableError`."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": SETTINGS_SCHEMA}
    except (OSError, ValueError):
        raise SettingsUnreadableError(path) from None
    if not isinstance(payload, dict) or payload.get("schema_version") != SETTINGS_SCHEMA:
        raise SettingsUnreadableError(path)
    return payload


def write_background_settings(home_root: Path, target: Path, patch: Mapping[str, Mapping[str, object]]) -> Path:
    """Apply a :func:`validate_patch` result to the project's settings file and return its path.

    Only the named keys change. Every other key, known or not, at the top
    level or inside a block, is written back as it was. The file is replaced
    in one step, so a reader sees the old settings or the new ones, never
    half. A stored file that cannot be read is refused
    (:class:`SettingsUnreadableError`): it may hold something the operator
    wrote by hand.
    """

    path = settings_path(Path(home_root), Path(target))
    with _WRITE_LOCK:
        payload = _read(path)
        for block, values in patch.items():
            stored = payload.get(block, {})
            if not isinstance(stored, dict):
                raise SettingsUnreadableError(path)
            merged = dict(stored)
            for key, value in values.items():
                if value is _REMOVE:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            payload[block] = merged
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}-{threading.get_ident()}")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return path


def background_settings(
    home_root: Path,
    target: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """What ``GET``/``PUT /api/settings/background`` answer.

    ``settings`` is what the file says (a key it does not hold shows its
    default), which is what a form edits. ``effective`` is what the
    background jobs act on right now: the same values after the environment
    overrides, each block with the ``source`` that decided it (``default``,
    ``setting``, ``environment`` or ``settings_unreadable``). ``readable`` is
    false when the file exists and cannot be read: every job is then off
    and a write is refused until the file is fixed or removed.
    """

    home = Path(home_root)
    stored_refresh = auto_refresh_setting(home, target, environ={})
    stored_tagging = tagging_setting(home, target, environ={})
    stored_snapshot = snapshot_setting(home, target, environ={})
    refresh = auto_refresh_setting(home, target, environ=environ)
    tagging = tagging_setting(home, target, environ=environ)
    snapshot = snapshot_setting(home, target, environ=environ)
    return {
        "schema_version": BACKGROUND_SETTINGS_SCHEMA,
        "readable": stored_refresh.source != "settings_unreadable",
        "settings": {
            "sources": {"auto_refresh": stored_refresh.enabled},
            "tagging": {
                "model_enabled": stored_tagging.model_enabled,
                "backfill_enabled": stored_tagging.backfill_enabled,
                "tag_backfill_model": stored_tagging.backfill_model,
            },
            "snapshot": {"enabled": stored_snapshot.enabled, "manifest_url": stored_snapshot.manifest_url},
        },
        "effective": {
            "sources": {"auto_refresh": refresh.enabled, "source": refresh.source},
            "tagging": tagging.to_json(),
            "snapshot": snapshot.to_json(),
        },
    }


__all__ = [
    "BACKGROUND_SETTINGS_SCHEMA",
    "MAX_MANIFEST_URL_LENGTH",
    "SettingsError",
    "SettingsUnreadableError",
    "background_settings",
    "validate_patch",
    "write_background_settings",
]
