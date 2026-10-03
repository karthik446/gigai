"""0.1.10.7 PL4: the ``pipeline`` and ``rank`` blocks of the project's settings file, read only.

``<home>/scout/<project_id>/settings.json`` (``refresh_tick.settings_path``),
beside ``sources.auto_refresh`` and ``tagging.*``::

    {"schema_version": "scout-settings:1",
     "pipeline": {"enabled": true,
                  "auto_jobs_per_trigger": 10,
                  "max_model_calls_per_day": 40,
                  "label_min_ats": 0,
                  "models": {"tailor": "claude_cli", "reassess": "codex_cli"}},
     "rank": {"max_calls_per_day": 100, "warn_calls_per_day": 60}}

Every key is optional; a key left out has its default (the values above,
``models`` empty). ``models.<step>`` names the adapter kind a model step runs
with (``find_jobs.contracts.ModelTarget``); a step left out runs with the
project's configured model target (``find-jobs.json``
``default_model_target``), the one every other Scout model call uses.

``enabled`` is ON by default. A settings file that cannot be read, or a
``pipeline`` / ``rank`` block with a value of the wrong kind, turns the
pipeline OFF (DESIGN 7, 12: a background job that spends model calls does not
guess). :data:`PIPELINE_ENV` overrides ``enabled`` either way.

``auto_jobs_per_trigger`` is the per-trigger cap of ``triggers.py`` (PL5: the
jobs over it wait for an approval). ``max_model_calls_per_day`` is enforced
by the runner (``cap_counter``, one count for the whole install). The
``rank`` caps are counted by ``triggers.spend_rank_calls``, for the
background rank lane of a later milestone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path

from .store import MODEL_STEPS

#: ``0``/``false``/``off``/``no`` turns the pipeline off, ``1``/``true``/``on``/``yes`` on, whatever the setting says.
PIPELINE_ENV = "GIGAI_SCOUT_PIPELINE"

DEFAULT_AUTO_JOBS_PER_TRIGGER = 10
DEFAULT_MAX_MODEL_CALLS_PER_DAY = 40
DEFAULT_RANK_MAX_CALLS_PER_DAY = 100
DEFAULT_RANK_WARN_CALLS_PER_DAY = 60
#: The Scout ATS score a job needs for the Scout label. 0: the score is shown, never a gate (the ATS spike's advice).
DEFAULT_LABEL_MIN_ATS = 0

SOURCE_DEFAULT = "default"
SOURCE_SETTING = "setting"
SOURCE_ENVIRONMENT = "environment"
SOURCE_UNREADABLE = "settings_unreadable"

_OFF = frozenset({"0", "false", "off", "no"})
_ON = frozenset({"1", "true", "on", "yes"})


@dataclass(frozen=True)
class PipelineSetting:
    """What the pipeline may do for this project, and what said so."""

    enabled: bool = True
    source: str = SOURCE_DEFAULT
    auto_jobs_per_trigger: int = DEFAULT_AUTO_JOBS_PER_TRIGGER
    max_model_calls_per_day: int = DEFAULT_MAX_MODEL_CALLS_PER_DAY
    label_min_ats: int = DEFAULT_LABEL_MIN_ATS
    #: step -> adapter kind, only for the steps the settings name.
    models: Mapping[str, str] = field(default_factory=dict)
    rank_max_calls_per_day: int = DEFAULT_RANK_MAX_CALLS_PER_DAY
    rank_warn_calls_per_day: int = DEFAULT_RANK_WARN_CALLS_PER_DAY

    def to_json(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "source": self.source,
            "auto_jobs_per_trigger": self.auto_jobs_per_trigger,
            "max_model_calls_per_day": self.max_model_calls_per_day,
            "label_min_ats": self.label_min_ats,
            "models": dict(sorted(self.models.items())),
            "rank": {"max_calls_per_day": self.rank_max_calls_per_day, "warn_calls_per_day": self.rank_warn_calls_per_day},
        }


_SETTING_OFF = PipelineSetting(enabled=False, source=SOURCE_UNREADABLE)


def _count(block: Mapping[str, object], key: str, default: int, *, most: int | None = None) -> int | None:
    """``block[key]`` as a non-negative integer (``default`` when left out); ``None``: not one."""

    if key not in block:
        return default
    value = block[key]
    if type(value) is not int or value < 0 or (most is not None and value > most):
        return None
    return value


def _adapter_kinds() -> frozenset[str]:
    from ..find_jobs.contracts import ModelTarget

    return frozenset(item.value for item in ModelTarget)


def pipeline_setting(
    home_root: Path,
    target: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
    path: Path | None = None,
) -> PipelineSetting:
    """The ``pipeline`` and ``rank`` blocks for this project: the environment, the file, then the defaults.

    ``path`` is the settings file when the caller already knows it (the
    runner, which looks every half minute and resolves the project once).
    """

    from ..find_jobs.refresh_tick import SETTINGS_SCHEMA, settings_path

    def forced(setting: PipelineSetting) -> PipelineSetting:
        raw = ((os.environ if environ is None else environ).get(PIPELINE_ENV) or "").strip().lower()
        if raw in _OFF:
            return replace(setting, enabled=False, source=SOURCE_ENVIRONMENT)
        if raw in _ON and setting.source != SOURCE_UNREADABLE:
            # An unreadable file stays off: its caps and models are unknown, and they are not guessed.
            return replace(setting, enabled=True, source=SOURCE_ENVIRONMENT)
        return setting

    default = PipelineSetting()
    if target is None:
        return forced(default)
    if path is None:
        try:
            path = settings_path(Path(home_root), Path(target))
        except Exception:  # noqa: BLE001 - no bound project yet: there is no settings file to read
            return forced(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return forced(default)
    except (OSError, ValueError):
        return forced(_SETTING_OFF)
    if not isinstance(payload, dict) or payload.get("schema_version") != SETTINGS_SCHEMA:
        return forced(_SETTING_OFF)
    block = payload.get("pipeline", {})
    rank = payload.get("rank", {})
    if not isinstance(block, dict) or not isinstance(rank, dict):
        return forced(_SETTING_OFF)
    enabled = block.get("enabled", True)
    per_trigger = _count(block, "auto_jobs_per_trigger", DEFAULT_AUTO_JOBS_PER_TRIGGER)
    per_day = _count(block, "max_model_calls_per_day", DEFAULT_MAX_MODEL_CALLS_PER_DAY)
    min_ats = _count(block, "label_min_ats", DEFAULT_LABEL_MIN_ATS, most=100)
    rank_max = _count(rank, "max_calls_per_day", DEFAULT_RANK_MAX_CALLS_PER_DAY)
    rank_warn = _count(rank, "warn_calls_per_day", DEFAULT_RANK_WARN_CALLS_PER_DAY)
    models = block.get("models", {})
    if (
        type(enabled) is not bool
        or None in (per_trigger, per_day, min_ats, rank_max, rank_warn)
        or not isinstance(models, dict)
        or any(step not in MODEL_STEPS or type(kind) is not str or kind not in _adapter_kinds() for step, kind in models.items())
    ):
        return forced(_SETTING_OFF)
    assert per_trigger is not None and per_day is not None and min_ats is not None
    assert rank_max is not None and rank_warn is not None
    return forced(
        PipelineSetting(
            enabled=enabled,
            source=SOURCE_SETTING if block or rank else SOURCE_DEFAULT,
            auto_jobs_per_trigger=per_trigger,
            max_model_calls_per_day=per_day,
            label_min_ats=min_ats,
            models=dict(models),
            rank_max_calls_per_day=rank_max,
            rank_warn_calls_per_day=rank_warn,
        )
    )


__all__ = [
    "DEFAULT_AUTO_JOBS_PER_TRIGGER",
    "DEFAULT_LABEL_MIN_ATS",
    "DEFAULT_MAX_MODEL_CALLS_PER_DAY",
    "DEFAULT_RANK_MAX_CALLS_PER_DAY",
    "DEFAULT_RANK_WARN_CALLS_PER_DAY",
    "PIPELINE_ENV",
    "PipelineSetting",
    "pipeline_setting",
]
