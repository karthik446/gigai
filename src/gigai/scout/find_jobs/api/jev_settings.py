"""ui-pass (uat-bug-021 decision a); jev-disclosure-fixes (TARGET 4) added the
per-run cap. ``GET`` / ``PUT /api/jev/settings``.

The three Jev settings Settings shows and saves: "Rank with Jev" on/off,
the daily budget, and the per-run cap. All three are the home's
(``jev_budget.settings_path``: ``<home>/local/scout/jev-settings.json``),
read through ``jev_budget``'s own functions, so what this route answers is
what every rank pass obeys.

``GET`` answers::

    {"schema_version": "scout-jev-settings:1",
     "jev_daily_budget_usd": 0.5,       # the stored setting
     "jev_rank_enabled": true,
     "jev_run_cap_usd": 0.25,           # the stored setting
     "daily_budget_env": "0.10" | null, # GIGAI_JEV_DAILY_BUDGET_USD, which wins when set
     "run_cap_env": "0.05" | null,      # GIGAI_JEV_COST_CAP_USD, which wins when set
     "run_cap_in_force": "0.25",        # jev_budget.run_cost_cap_usd, formatted: env > setting > default
     "has_key": true,                   # a Jev key is set (secrets_status.keys_set)
     "usage": {...}}                    # jev_budget.usage: today's spend against the budget in force

``PUT`` takes ``jev_daily_budget_usd`` (a number, 0 or more; 0 means "never
ask Jev"), ``jev_rank_enabled`` (a boolean), and/or ``jev_run_cap_usd`` (a
number, 0 or more; 0 means "never spend on a single pass"). A key the body
leaves out keeps its stored value. Any other key is refused. It answers
what ``GET`` answers.
"""

from __future__ import annotations

from http import HTTPStatus

from .. import jev_budget
from .secrets_status import keys_set

_FIELDS = ("jev_daily_budget_usd", "jev_rank_enabled", "jev_run_cap_usd")


def settings_body(home_root) -> dict[str, object]:
    stored = jev_budget.read_settings(home_root)
    override = jev_budget.budget_env_override()
    run_cap_override = jev_budget.run_cap_env_override()
    return {
        "schema_version": jev_budget.SETTINGS_SCHEMA_VERSION,
        "jev_daily_budget_usd": stored["jev_daily_budget_usd"],
        "jev_rank_enabled": stored["jev_rank_enabled"],
        "jev_run_cap_usd": stored["jev_run_cap_usd"],
        "daily_budget_env": None if override is None else jev_budget.format_usd(override),
        "run_cap_env": None if run_cap_override is None else jev_budget.format_usd(run_cap_override),
        "run_cap_in_force": jev_budget.format_usd(jev_budget.run_cost_cap_usd(home_root)),
        "has_key": bool(keys_set(home_root).get("jev")),
        "usage": jev_budget.usage(home_root),
    }


class JevSettingsRoutesMixin:
    """``Handler`` mixin: ``GET`` / ``PUT /api/jev/settings``."""

    def _handle_get_jev_settings(self) -> None:
        self._write_json(HTTPStatus.OK, settings_body(self._backend.home_root))

    def _handle_put_jev_settings(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if type(body) is not dict:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "the body must be a JSON object")
            return
        unknown = sorted(set(body) - set(_FIELDS))
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_field", f"unknown field: {unknown[0]}")
            return
        try:
            jev_budget.write_settings(
                self._backend.home_root,
                daily_budget_usd=body.get("jev_daily_budget_usd"),
                rank_enabled=body.get("jev_rank_enabled"),
                run_cap_usd=body.get("jev_run_cap_usd"),
            )
        except jev_budget.JevSettingsError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, settings_body(self._backend.home_root))


__all__ = ["JevSettingsRoutesMixin", "settings_body"]
