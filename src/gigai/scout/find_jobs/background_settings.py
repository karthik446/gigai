"""The per-project background settings: one reader for the API and the ONE writer of the file.

``<home>/scout/<project_id>/settings.json`` (``refresh_tick.settings_path``)
holds what the operator chose for Scout's background work. Three readers
already exist, each beside the job it switches:

* ``sources.auto_refresh`` -- ``refresh_tick.auto_refresh_setting`` (0110-025);
* ``sources.check_times`` -- ``refresh_tick.check_schedule_setting`` (0110-029;
  editable here since 0110-033);
* ``tagging.model_enabled`` / ``tagging.backfill_enabled`` /
  ``tagging.tag_backfill_model`` -- ``model_tag.tagging_setting`` (0110-024);
* ``snapshot.enabled`` / ``snapshot.manifest_url`` --
  ``snapshot.snapshot_setting`` (0110-026);
* ``pipeline.*`` and ``rank.*`` -- ``pipeline.settings.pipeline_setting``
  (0.1.10.7 PL5): the background pipeline's switch, its caps
  (``pipeline.auto_jobs_per_trigger``, ``pipeline.max_model_calls_per_day``,
  ``rank.max_calls_per_day``, ``rank.warn_calls_per_day``), the Scout ATS
  minimum of the Scout label (``pipeline.label_min_ats``, 0 to 100) and the
  model target of each model step (``pipeline.models``: ``tailor`` /
  ``reassess``; ``null`` for a step puts the project's model target back).
  ``null`` for a cap puts its default back.

This module adds what was missing: :func:`write_background_settings` (the
API's ``PUT /api/settings/background``), which changes only the keys it is
given, keeps every key it does not know, and replaces the file atomically;
and :func:`background_settings`, the body both routes answer, built from
those three readers so the API can never disagree with the jobs.

``sources.check_times`` is ``{"weekdays": [...], "weekends": [...]}``, each a
list of 1 to :data:`MAX_CHECK_TIMES_PER_DAY` 24-hour ``HH:MM`` local times
(stored sorted, without repeats). A ``PUT`` may name one list or both; a
list left out keeps what is stored, ``null`` for a list puts that list's
default back, and ``"check_times": null`` puts both back.

Nothing here caches: the refresh thread reads the file again at every look
(``RefreshTicker.step``), so a write is honoured at its next look.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

from .model_tag import BACKFILL_MODELS, tagging_setting
from .refresh_plan import DEFAULT_WEEKDAY_TIMES, DEFAULT_WEEKEND_TIMES, CheckSchedule, parse_check_times
from .refresh_tick import (
    SETTINGS_SCHEMA,
    SOURCE_DEFAULT,
    ScheduleSetting,
    auto_refresh_setting,
    check_schedule_setting,
    settings_path,
)
from .snapshot import snapshot_setting
from ..pipeline.settings import pipeline_setting

BACKGROUND_SETTINGS_SCHEMA = "scout-background-settings:1"
MAX_MANIFEST_URL_LENGTH = 2048
#: How many check times one day's list may hold when it is set through the API.
MAX_CHECK_TIMES_PER_DAY = 12
CHECK_TIMES_DAYS = ("weekdays", "weekends")
_HH_MM = re.compile(r"\A\d{2}:\d{2}\Z")

#: block -> key -> what a value must be ("bool", "backfill_model", "manifest_url", "check_times", "count",
#: "percent" or "step_models").
_KEYS: dict[str, dict[str, str]] = {
    "sources": {"auto_refresh": "bool", "check_times": "check_times"},
    "tagging": {"model_enabled": "bool", "backfill_enabled": "bool", "tag_backfill_model": "backfill_model"},
    "snapshot": {"enabled": "bool", "manifest_url": "manifest_url"},
    "pipeline": {
        "enabled": "bool", "auto_jobs_per_trigger": "count", "max_model_calls_per_day": "count",
        "label_min_ats": "percent", "models": "step_models",
    },
    "rank": {"max_calls_per_day": "count", "warn_calls_per_day": "count"},
}
#: The most a cap may be set to through the API: a typo must not buy ten thousand model calls.
MAX_CAP = 1000

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


class _DayLists(dict):
    """``sources.check_times`` in a patch: day -> its times, or ``_REMOVE`` for that day's default.

    Laid over the stored lists by the writer, so naming one day keeps the other.
    ``pipeline.models`` (step -> model target) is laid over the stored steps the same way.
    """


def _check_times(name: str, value: object) -> object:
    """``sources.check_times`` of a body, checked: ``_REMOVE`` (both defaults) or a :class:`_DayLists`."""

    if value is None:
        return _REMOVE
    if not isinstance(value, dict):
        raise SettingsError("wrong_type", f"{name} must be an object {{weekdays, weekends}} or null for the default times")
    extra = sorted(set(value) - set(CHECK_TIMES_DAYS))
    if extra:
        raise SettingsError("unknown_key", f"{name} has unknown field(s): {extra} (allowed: {', '.join(CHECK_TIMES_DAYS)})")
    if not value:
        raise SettingsError("invalid_value", f"{name} must name weekdays, weekends or both")
    days = _DayLists()
    for day in CHECK_TIMES_DAYS:
        if day not in value:
            continue
        times = value[day]
        if times is None:
            days[day] = _REMOVE
            continue
        if not isinstance(times, list):
            raise SettingsError("wrong_type", f"{name}.{day} must be a list of HH:MM times, or null for the default")
        if any(type(item) is not str for item in times):
            raise SettingsError("wrong_type", f"{name}.{day} must hold HH:MM strings")
        problem = f"{name}.{day} must hold 1 to {MAX_CHECK_TIMES_PER_DAY} 24-hour HH:MM times (00:00 to 23:59)"
        if not all(_HH_MM.match(item) for item in times):
            raise SettingsError("invalid_value", problem)
        try:
            parsed = parse_check_times(times)
        except ValueError:
            raise SettingsError("invalid_value", problem) from None
        if not 1 <= len(parsed) <= MAX_CHECK_TIMES_PER_DAY:
            raise SettingsError("invalid_value", problem)
        days[day] = list(parsed)
    return days


def _step_models(name: str, value: object) -> object:
    """``pipeline.models`` of a body, checked: ``_REMOVE`` (no step names a model) or the steps named, laid over the stored ones."""

    from ..pipeline.settings import _adapter_kinds
    from ..pipeline.store import MODEL_STEPS

    if value is None:
        return _REMOVE
    steps = sorted(MODEL_STEPS)
    if not isinstance(value, dict):
        raise SettingsError("wrong_type", f"{name} must be an object {{{', '.join(steps)}}} or null for the project's model target")
    extra = sorted(set(value) - MODEL_STEPS)
    if extra:
        raise SettingsError("unknown_key", f"{name} has unknown field(s): {extra} (allowed: {', '.join(steps)})")
    if not value:
        raise SettingsError("invalid_value", f"{name} must name at least one of: {', '.join(steps)}")
    kinds = sorted(_adapter_kinds())
    models = _DayLists()
    for step, kind in value.items():
        if kind is None:
            models[step] = _REMOVE
            continue
        if type(kind) is not str:
            raise SettingsError("wrong_type", f"{name}.{step} must be a string or null")
        if kind not in kinds:
            raise SettingsError("bad_enum", f"{name}.{step} must be one of: {', '.join(kinds)}")
        models[step] = kind
    return models


def _value(block: str, key: str, kind: str, value: object) -> object:
    name = f"{block}.{key}"
    if kind == "check_times":
        return _check_times(name, value)
    if kind == "step_models":
        return _step_models(name, value)
    if kind in ("count", "percent"):
        # null puts the default back.
        if value is None:
            return _REMOVE
        most = 100 if kind == "percent" else MAX_CAP
        if type(value) is not int:
            raise SettingsError("wrong_type", f"{name} must be a whole number or null for the default")
        if not 0 <= value <= most:
            raise SettingsError("invalid_value", f"{name} must be 0 to {most}, or null for the default")
        return value
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
    rank = patch.get("rank", {})
    most, warn = rank.get("max_calls_per_day"), rank.get("warn_calls_per_day")
    if type(most) is int and type(warn) is int and warn > most:
        raise SettingsError("invalid_value", "rank.warn_calls_per_day must not be above rank.max_calls_per_day")
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
                if isinstance(value, _DayLists):
                    # One day named: the other day's stored list stays. A stored value that
                    # is not an object is one the schedule already ignores; it is replaced.
                    kept = merged.get(key)
                    lists = dict(kept) if isinstance(kept, dict) else {}
                    for day, times in value.items():
                        if times is _REMOVE:
                            lists.pop(day, None)
                        else:
                            lists[day] = times
                    value = lists if lists else _REMOVE
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


def _check_times_setting(home: Path, target: Path | None) -> ScheduleSetting:
    """``check_schedule_setting``; a target with no Scout project has no file, so the defaults (as the other readers say)."""

    if target is not None:
        try:
            settings_path(home, target)
        except Exception:  # noqa: BLE001 - no bound project yet: there is no settings file to read
            return ScheduleSetting(CheckSchedule(), SOURCE_DEFAULT)
    return check_schedule_setting(home, target)


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
    ``setting``, ``environment`` or ``settings_unreadable``).
    ``sources.check_times`` (0110-033) is the two lists of local times the
    checks run at; in ``effective`` it carries its own ``source`` (stored
    times that cannot be used run the defaults, ``settings_unreadable``) and
    ``default``, the times a reset puts back. ``pipeline`` and ``rank``
    (0.1.10.7 PL5) are the background pipeline's switch, caps, Scout ATS
    minimum and per-step model targets; ``effective.pipeline`` is
    ``PipelineSetting.to_json`` (the rank caps inside it). ``readable`` is
    false when the file exists and cannot be read: every job is then off
    (the pipeline too: ``effective.pipeline.enabled`` false, ``source``
    ``settings_unreadable``) and a write is refused until the file is fixed
    or removed.
    """

    home = Path(home_root)
    stored_refresh = auto_refresh_setting(home, target, environ={})
    stored_tagging = tagging_setting(home, target, environ={})
    stored_snapshot = snapshot_setting(home, target, environ={})
    stored_pipeline = pipeline_setting(home, target, environ={})
    pipeline = pipeline_setting(home, target, environ=environ)
    refresh = auto_refresh_setting(home, target, environ=environ)
    tagging = tagging_setting(home, target, environ=environ)
    snapshot = snapshot_setting(home, target, environ=environ)
    # No environment variable changes the times: the file's are the ones in effect.
    schedule = _check_times_setting(home, target)
    check_times = {"weekdays": list(schedule.schedule.weekdays), "weekends": list(schedule.schedule.weekends)}
    return {
        "schema_version": BACKGROUND_SETTINGS_SCHEMA,
        "readable": stored_refresh.source != "settings_unreadable",
        "settings": {
            "sources": {"auto_refresh": stored_refresh.enabled, "check_times": check_times},
            "tagging": {
                "model_enabled": stored_tagging.model_enabled,
                "backfill_enabled": stored_tagging.backfill_enabled,
                "tag_backfill_model": stored_tagging.backfill_model,
            },
            "snapshot": {"enabled": stored_snapshot.enabled, "manifest_url": stored_snapshot.manifest_url},
            "pipeline": {
                "enabled": stored_pipeline.enabled,
                "auto_jobs_per_trigger": stored_pipeline.auto_jobs_per_trigger,
                "max_model_calls_per_day": stored_pipeline.max_model_calls_per_day,
                "label_min_ats": stored_pipeline.label_min_ats,
                "models": dict(sorted(stored_pipeline.models.items())),
            },
            "rank": {
                "max_calls_per_day": stored_pipeline.rank_max_calls_per_day,
                "warn_calls_per_day": stored_pipeline.rank_warn_calls_per_day,
            },
        },
        "effective": {
            "sources": {
                "auto_refresh": refresh.enabled,
                "source": refresh.source,
                "check_times": {
                    **{day: list(times) for day, times in check_times.items()},
                    "source": schedule.source,
                    "default": {"weekdays": list(DEFAULT_WEEKDAY_TIMES), "weekends": list(DEFAULT_WEEKEND_TIMES)},
                },
            },
            "tagging": tagging.to_json(),
            "snapshot": snapshot.to_json(),
            # The pipeline's block carries the rank caps too (``rank``): one reader, one ``source``.
            "pipeline": pipeline.to_json(),
        },
    }


__all__ = [
    "BACKGROUND_SETTINGS_SCHEMA",
    "CHECK_TIMES_DAYS",
    "MAX_CHECK_TIMES_PER_DAY",
    "MAX_MANIFEST_URL_LENGTH",
    "SettingsError",
    "SettingsUnreadableError",
    "background_settings",
    "validate_patch",
    "write_background_settings",
]
