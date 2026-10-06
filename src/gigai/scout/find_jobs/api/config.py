"""R0: ``GET /api/config``, moved out of ``present_api.py`` verbatim.

``_prefs_prefill_from_config`` lives here (rather than in ``setup.py``)
because it's a pure function of ``FindJobsConfig``, not a route handler, and
this is the module that already imports ``FindJobsConfig``/``SourceToggles``
for its own route; ``setup.py`` imports it back for the ``prefs_missing``
404's pre-fill.
"""

from __future__ import annotations

from http import HTTPStatus

from ..contracts import FindJobsConfig, FindJobsContractError
from .common import reads_committed
from .server import ConfigMissingError


def _prefs_prefill_from_config(config: FindJobsConfig) -> dict[str, object]:
    """Derive a ``PUT /api/setup``-shaped pre-fill from the current find-jobs.json.

    Used for the 404 ``prefs_missing`` response's pre-fill payload (CHANGE
    #1) -- only the fields ``FindJobsConfig`` actually carries are filled;
    the rest default the same way ``_validate_setup_body`` would.
    """

    # uat-bug-028: the stored work mode (or what an older file's `remote`
    # meant); `location` is already None for the starter placeholder.
    # 0.1.11.2 (UAT-006): a fresh onboarding (no stored work_mode and no roles: no config, or the starter) opens on
    # Remote-only; any stored work_mode, and an existing config with roles, keeps what it says.
    fresh = config.work_mode is None and not config.roles
    work_mode = "remote" if fresh else config.effective_work_mode.value
    return {
        "roles": list(config.roles),
        "titles_to_avoid": [],
        # 0.1.11.3 item 11: a fresh onboarding opens on the US (the default country), as the wizard shows it.
        "countries": list(config.countries) or (["US"] if fresh else []),
        "work_mode": work_mode,
        "city": config.location,
        "visa_sponsorship_required": config.visa_sponsorship_required,
        "exclude_companies": [],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        "budget_usd_per_session": 0.50,
    }


class ConfigRoutesMixin:
    """``Handler`` mixin: ``GET /api/config`` and ``PUT /api/config/sources``."""

    def _handle_put_config_sources(self) -> None:
        """uat-bug-033: ``PUT /api/config/sources {"exa": bool}`` -- the optional Exa source on/off.

        Nothing else in ``find-jobs.json`` changes. Turning Exa on without a
        key is allowed and saves (the UI shows the key hint); a run then
        reports Exa as failed, exactly as before.
        """

        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict) or set(body) != {"exa"} or not isinstance(body["exa"], bool):
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "wrong_type",
                'body must be {"exa": true|false}',
            )
            return
        try:
            self._backend.set_exa_source(body["exa"])
        except ConfigMissingError as exc:
            self._error(
                HTTPStatus.NOT_FOUND,
                "config_missing",
                f"{exc.path} does not exist yet. Complete the setup wizard first.",
            )
            return
        except FindJobsContractError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return
        self._write_json(HTTPStatus.OK, {"sources": {"exa": body["exa"]}})


    @reads_committed
    def _handle_get_config(self) -> None:
        try:
            config, _config_bytes = self._backend.read_config()
        except ConfigMissingError as exc:
            self._error(
                HTTPStatus.NOT_FOUND,
                "config_missing",
                f"{exc.path} does not exist yet. Run `gigai scout install` or "
                "`gigai scout run` to write a starter find-jobs.json, then edit it.",
            )
            return
        except FindJobsContractError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
            return
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "config not found")
            return
        # uat-bug-004: additive display fields ("<label> · added <date>")
        # alongside resume_preview's raw ids -- the sealed PinnedResume
        # shape itself never grows display-only fields.
        # uat-bug-008: one resolution instead of resume_preview() +
        # resume_metadata() (each independently replaying committed
        # journal artifacts via run.resolve_newest_resume_details) --
        # see ScoutFindJobsBackend.resume_details.
        resume_result = self._backend.resume_details()
        resume_preview = resume_result.pinned if resume_result is not None else None
        resume_label = resume_result.label if resume_result is not None else None
        resume_created_at = resume_result.created_at if resume_result is not None else None
        config_digest = config.digest()
        payload = {
            "schema_version": "scout-find-jobs-config-response:1",
            "config": config.to_json(),
            "resume_preview": resume_preview.to_json() if resume_preview is not None else None,
            "resume_label": resume_label,
            "resume_created_at": resume_created_at,
            "resume_missing_hint": (
                None if resume_preview is not None else "gigai scout resume add <file>"
            ),
            "config_digest": config_digest,
        }
        self._write_json(HTTPStatus.OK, payload)
