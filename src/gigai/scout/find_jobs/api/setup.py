"""R0: the setup interview routes (``GET``/``PUT /api/setup``), moved out of
``present_api.py`` verbatim.
"""

from __future__ import annotations

import re
from http import HTTPStatus

from ..contracts import MAX_AGE_DAYS_MAXIMUM, FindJobsConfig, FindJobsContractError, SourceToggles
from .config import _prefs_prefill_from_config
from .server import (
    ConfigMissingError,
    DiscoveryUnavailableError,
    SetupPrefsMissingError,
    SetupValidationError,
    _logger,
)

_WORK_MODES = ("remote", "hybrid", "onsite", "any")
_COUNTRY_CODE = re.compile(r"\A[A-Z]{2}\Z")


def _setup_field_string_list(body: dict[str, object], field: str, errors: dict[str, str]) -> tuple[str, ...]:
    value = body.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors[field] = f"{field} must be an array of strings"
        return ()
    return tuple(value)


def _setup_field_optional_string(body: dict[str, object], field: str, errors: dict[str, str]) -> str | None:
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        errors[field] = f"{field} must be a non-empty string or null"
        return None
    return value


def _setup_field_bool(body: dict[str, object], field: str, errors: dict[str, str], *, default: bool) -> bool:
    if field not in body:
        return default
    value = body[field]
    if not isinstance(value, bool):
        errors[field] = f"{field} must be a boolean"
        return default
    return value


def _setup_countries(body: dict[str, object], errors: dict[str, str]) -> tuple[str, ...]:
    codes = _setup_field_string_list(body, "countries", errors)
    if "countries" in errors:
        return ()
    for index, code in enumerate(codes):
        if not _COUNTRY_CODE.fullmatch(code):
            errors["countries"] = f"countries[{index}] must be an ISO-3166 alpha-2 code"
            return ()
    return codes


def _setup_work_mode(body: dict[str, object], errors: dict[str, str]) -> str:
    value = body.get("work_mode")
    if value not in _WORK_MODES:
        errors["work_mode"] = f"work_mode must be one of {', '.join(_WORK_MODES)}"
        return "any"
    return value


def _setup_cadence_days(body: dict[str, object], errors: dict[str, str]) -> int:
    if "cadence_days" not in body:
        return 7
    value = body["cadence_days"]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        errors["cadence_days"] = "cadence_days must be a positive integer"
        return 7
    return value


def _setup_budget_usd_per_session(body: dict[str, object], errors: dict[str, str]) -> float:
    if "budget_usd_per_session" not in body:
        return 0.50
    value = body["budget_usd_per_session"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        errors["budget_usd_per_session"] = "budget_usd_per_session must be a positive number"
        return 0.50
    return float(value)


def _setup_max_age_days(body: dict[str, object], errors: dict[str, str]) -> int | None:
    """Q1 (v0.1.9): the optional rolling publication window the wizard sends.

    ``None`` when absent (the save then leaves find-jobs.json's window
    untouched -- see ``ScoutFindJobsBackend._update_find_jobs_config``).
    Never a ``DiscoveryPrefs`` field.
    """

    if "max_age_days" not in body or body["max_age_days"] is None:
        return None
    value = body["max_age_days"]
    if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= MAX_AGE_DAYS_MAXIMUM):
        errors["max_age_days"] = f"max_age_days must be an integer from 1 to {MAX_AGE_DAYS_MAXIMUM}"
        return None
    return value


def _validate_setup_body(body: object) -> dict[str, object]:
    """Validate a ``PUT /api/setup`` body against the 11 S23 interview fields.

    Returns a plain dict shaped exactly like ``DiscoveryPrefs`` fields
    (S2-A's frozen dataclass); the caller constructs the real dataclass so
    this module never has to import it eagerly (see CHANGE #3). Raises
    ``SetupValidationError`` (-> 400 with per-field messages) on any bad
    field; unknown top-level keys are also rejected to fail closed on typos.

    Q1 (v0.1.9): plus one NON-prefs key, ``max_age_days`` (optional int,
    1..365) -- present in the returned dict only when the body set it, and
    stripped again by ``write_setup`` before ``DiscoveryPrefs`` is built.
    """

    if not isinstance(body, dict):
        raise SetupValidationError({"_": "request body must be a JSON object"})
    known_keys = {
        "roles",
        "titles_to_avoid",
        "countries",
        "work_mode",
        "city",
        "visa_sponsorship_required",
        "exclude_companies",
        "watch_companies",
        "company_stage_size",
        "industries_include",
        "industries_exclude",
        "must_have_stack",
        "dealbreaker_stack",
        "cadence_days",
        "budget_usd_per_session",
        "max_age_days",
    }
    errors: dict[str, str] = {}
    unknown = set(body) - known_keys
    if unknown:
        errors["_"] = f"unknown field(s): {sorted(unknown)}"

    roles = _setup_field_string_list(body, "roles", errors)
    if "roles" not in errors and not roles:
        errors["roles"] = "roles must not be empty"
    titles_to_avoid = _setup_field_string_list(body, "titles_to_avoid", errors)
    countries = _setup_countries(body, errors)
    work_mode = _setup_work_mode(body, errors)
    city = _setup_field_optional_string(body, "city", errors)
    visa_sponsorship_required = _setup_field_bool(body, "visa_sponsorship_required", errors, default=False)
    exclude_companies = _setup_field_string_list(body, "exclude_companies", errors)
    watch_companies = _setup_field_string_list(body, "watch_companies", errors)
    company_stage_size = _setup_field_optional_string(body, "company_stage_size", errors)
    industries_include = _setup_field_string_list(body, "industries_include", errors)
    industries_exclude = _setup_field_string_list(body, "industries_exclude", errors)
    must_have_stack = _setup_field_string_list(body, "must_have_stack", errors)
    dealbreaker_stack = _setup_field_string_list(body, "dealbreaker_stack", errors)
    cadence_days = _setup_cadence_days(body, errors)
    budget_usd_per_session = _setup_budget_usd_per_session(body, errors)
    max_age_days = _setup_max_age_days(body, errors)

    if errors:
        raise SetupValidationError(errors)

    fields: dict[str, object] = {
        "roles": roles,
        "titles_to_avoid": titles_to_avoid,
        "countries": countries,
        "work_mode": work_mode,
        "city": city,
        "visa_sponsorship_required": visa_sponsorship_required,
        "exclude_companies": exclude_companies,
        "watch_companies": watch_companies,
        "company_stage_size": company_stage_size,
        "industries_include": industries_include,
        "industries_exclude": industries_exclude,
        "must_have_stack": must_have_stack,
        "dealbreaker_stack": dealbreaker_stack,
        "cadence_days": cadence_days,
        "budget_usd_per_session": budget_usd_per_session,
    }
    if max_age_days is not None:
        fields["max_age_days"] = max_age_days
    return fields


class SetupRoutesMixin:
    """``Handler`` mixin: ``GET``/``PUT /api/setup``."""

    def _handle_get_setup(self) -> None:
        try:
            prefs_json = self._backend.read_setup()
        except DiscoveryUnavailableError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
            return
        if prefs_json is not None:
            self._write_json(
                HTTPStatus.OK,
                {"schema_version": "scout-find-jobs-setup-response:1", "prefs": prefs_json},
            )
            return
        # CHANGE #1: 404 prefs_missing carries a pre-fill derived from the
        # current find-jobs.json where possible, so the UI's first-run
        # interview starts from the operator's existing config instead of
        # a blank form.
        try:
            config, _config_bytes = self._backend.read_config()
            prefill = _prefs_prefill_from_config(config)
        except (ConfigMissingError, FindJobsContractError, LookupError):
            prefill = _prefs_prefill_from_config(
                FindJobsConfig(
                    roles=(),
                    merged_queries=(),
                    location=None,
                    remote=True,
                    published_after=None,
                    sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
                )
            )
        self._error_with_extra(
            HTTPStatus.NOT_FOUND,
            "prefs_missing",
            "no discovery preferences saved yet; complete the setup interview",
            {"prefill": prefill},
        )

    def _handle_put_setup(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            prefs_fields = _validate_setup_body(body)
        except SetupValidationError as exc:
            self._error_with_extra(
                HTTPStatus.BAD_REQUEST,
                exc.code,
                str(exc),
                {"field_errors": exc.field_errors},
            )
            return
        try:
            prefs_json = self._backend.write_setup(prefs_fields)
        except DiscoveryUnavailableError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
            return
        # Field *names* only -- prefs_fields carries the operator's actual
        # roles/companies/etc, which must never reach the log (ticket:
        # "setup saved (which fields changed, not values)").
        _logger.info("setup saved: fields=%s", sorted(prefs_fields))
        self._write_json(
            HTTPStatus.OK,
            {"schema_version": "scout-find-jobs-setup-response:1", "prefs": prefs_json},
        )
