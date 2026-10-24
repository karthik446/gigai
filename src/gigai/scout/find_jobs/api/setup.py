"""R0: the setup interview routes (``GET``/``PUT /api/setup``), moved out of
``present_api.py`` verbatim.

uat-bug-024: ``PUT /api/setup`` takes an optional ``profile_id``. The
profile-scoped answers (``roles`` -> the profile's titles and queries,
``titles_to_avoid``) are written to that profile and to no other; the
selected profile is not touched unless it is the one named. Without
``profile_id`` (or with ``null``) they go to the SELECTED profile, as they
always did: the Preferences page and every older caller send no
``profile_id``. A ``profile_id`` that is not committed in this gig is a
``404 profile_not_found`` and an archived one a ``409
scout_profile_archived``; both are answered before anything is saved.
"""

from __future__ import annotations

import re
from http import HTTPStatus

from ....adapters.http import checked_loopback_endpoint, list_local_models
from ..contracts import (
    MAX_AGE_DAYS_MAXIMUM,
    FindJobsConfig,
    FindJobsContractError,
    LocalLLMProtocol,
    ModelTarget,
    SourceToggles,
    is_location_placeholder,
)
from .common import reads_committed
from .config import _prefs_prefill_from_config
from .server import (
    ConfigMissingError,
    DiscoveryUnavailableError,
    SetupValidationError,
    _logger,
)

_WORK_MODES = ("remote", "hybrid", "onsite", "any")
_COUNTRY_CODE = re.compile(r"\A[A-Z]{2}\Z")
_LOCAL_LLM_PROTOCOLS = tuple(item.value for item in LocalLLMProtocol)


def _setup_local_llm(
    body: dict[str, object], errors: dict[str, str]
) -> tuple[str | None, str | None, str | None, bool, str | None]:
    """The optional local LLM provider keys of a wizard Finish.

    Returns ``(url, protocol, model, key_required, api_key)``, the strings
    ``None`` when the body did not carry them (the save then leaves
    find-jobs.json's stored value as it is).  The URL must be an explicit
    loopback ``http://127.0.0.1:<port>`` -- the same rule the adapters
    apply, so a "local" provider can never point at a remote host --
    protocol is ``openai`` or ``anthropic`` (openai when a URL arrives
    without one), model is a short id string, key_required is the wizard's
    "API key required" checkbox, and api_key is a short secret string (a
    blank value means "keep the stored key"; the value itself never lands in
    find-jobs.json -- it goes to the secrets file).
    """

    url: str | None = None
    raw_url = body.get("local_llm_url")
    if raw_url is not None:
        try:
            url = checked_loopback_endpoint(raw_url if isinstance(raw_url, str) else "", "local_llm_url")
        except ValueError as exc:
            errors["local_llm_url"] = str(exc)
    protocol: str | None = None
    raw_protocol = body.get("local_llm_protocol")
    if raw_protocol is not None:
        if not isinstance(raw_protocol, str) or raw_protocol not in _LOCAL_LLM_PROTOCOLS:
            errors["local_llm_protocol"] = f"local_llm_protocol must be one of {', '.join(_LOCAL_LLM_PROTOCOLS)}"
        else:
            protocol = raw_protocol
    model: str | None = None
    raw_model = body.get("local_llm_model")
    if raw_model is not None:
        if not isinstance(raw_model, str) or not raw_model.strip() or len(raw_model) > 256:
            errors["local_llm_model"] = "local_llm_model must be a non-empty string (256 characters at most) or null"
        else:
            model = raw_model.strip()
    key_required = _setup_field_bool(body, "local_llm_key_required", errors, default=False)
    api_key: str | None = None
    raw_key = body.get("local_llm_api_key")
    if raw_key is not None:
        if not isinstance(raw_key, str) or len(raw_key) > 512:
            errors["local_llm_api_key"] = "local_llm_api_key must be a string (512 characters at most) or null"
        else:
            api_key = raw_key.strip() or None
    if url is not None and protocol is None and "local_llm_protocol" not in errors:
        protocol = LocalLLMProtocol.OPENAI.value
    return url, protocol, model, key_required, api_key


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


def _setup_city(body: dict[str, object], errors: dict[str, str]) -> str | None:
    """uat-bug-028: the optional area; the starter placeholder text is refused."""

    city = _setup_field_optional_string(body, "city", errors)
    if city is not None and is_location_placeholder(city):
        errors["city"] = "city is still the starter placeholder: enter a city or area, or leave it empty"
        return None
    return city.strip() if city is not None and city.strip() else None


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


def _setup_profile_id(body: dict[str, object], errors: dict[str, str]) -> str | None:
    """uat-bug-024: the profile this save's titles go to; ``None`` is the selected one."""

    value = body.get("profile_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        errors["profile_id"] = "profile_id must be a non-empty string or null"
        return None
    return value


def _setup_model_target(body: dict[str, object], errors: dict[str, str]) -> str | None:
    """uat-bug-038: the optional model target Finish saves as ``default_model_target``.

    ``None`` when absent (the save then leaves find-jobs.json's target as it
    is). Never a ``DiscoveryPrefs`` field.
    """

    if "model_target" not in body or body["model_target"] is None:
        return None
    value = body["model_target"]
    if not isinstance(value, str) or value not in {target.value for target in ModelTarget}:
        errors["model_target"] = f"model_target must be one of {', '.join(target.value for target in ModelTarget)}"
        return None
    return value


def _model_keys_from_config(config: FindJobsConfig) -> dict[str, object]:
    """The NON-prefs model keys the wizard's one model choice saved in
    find-jobs.json, in the same additive shape ``PUT /api/setup`` accepts.

    ``GET /api/setup`` carries these beside ``prefs`` (like ``max_age_days``,
    a non-prefs key that is never a ``DiscoveryPrefs`` field), so the Settings
    UI can show the last-saved model target and its local-LLM keys and
    round-trip them on the next save -- the model choice is one key that must
    not be lost when a Finish lands without it.
    """

    keys: dict[str, object] = {"model_target": config.default_model_target.value}
    if config.local_llm_url is not None:
        keys["local_llm_url"] = config.local_llm_url
        keys["local_llm_protocol"] = config.local_llm_protocol.value
        keys["local_llm_model"] = config.local_llm_model
        if config.local_llm_key_required:
            keys["local_llm_key_required"] = True
    return keys


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

    uat-bug-024: plus ``profile_id`` (optional string), also present in the
    returned dict only when the body set it. It is never a prefs field:
    ``_handle_put_setup`` takes it out and hands it to ``write_setup`` as
    its own argument.

    uat-bug-038: plus ``model_target`` (optional ModelTarget value), present
    in the returned dict only when the body set it: ``write_setup`` saves it
    as find-jobs.json's ``default_model_target`` and strips it before
    ``DiscoveryPrefs`` is built.
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
        "profile_id",
        "model_target",
        "local_llm_url",
        "local_llm_protocol",
        "local_llm_model",
        "local_llm_key_required",
        "local_llm_api_key",
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
    city = _setup_city(body, errors)
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
    profile_id = _setup_profile_id(body, errors)
    model_target = _setup_model_target(body, errors)
    local_llm_url, local_llm_protocol, local_llm_model, local_llm_key_required, local_llm_api_key = _setup_local_llm(
        body, errors
    )

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
    if profile_id is not None:
        fields["profile_id"] = profile_id
    if model_target is not None:
        fields["model_target"] = model_target
    if local_llm_url is not None:
        fields["local_llm_url"] = local_llm_url
    if local_llm_protocol is not None:
        fields["local_llm_protocol"] = local_llm_protocol
    if local_llm_model is not None:
        fields["local_llm_model"] = local_llm_model
    if local_llm_url is not None:
        fields["local_llm_key_required"] = local_llm_key_required
    if local_llm_api_key is not None:
        fields["local_llm_api_key"] = local_llm_api_key
    return fields


def _named_profile_refusal(backend: object, profile_id: str) -> tuple[HTTPStatus, str, str] | None:
    """Why ``profile_id`` cannot take a setup save, or ``None`` when it can.

    The gig is resolved the way the profiles routes resolve it
    (``profiles.ProfilesRoutesMixin._resolve_profiles_gig``). Checked before
    ``write_setup`` so a refused save leaves the preferences as they were.
    """

    from ....workpad import WorkpadError, resolve_workpad
    from ...profile_records import ProfileRecordError, list_profiles

    missing = (HTTPStatus.NOT_FOUND, "profile_not_found", "that profile is not committed in this gig")
    home_root = getattr(backend, "home_root", None)
    target = getattr(backend, "target", None)
    if home_root is None or target is None:
        return missing
    try:
        resolved = resolve_workpad(
            home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True
        )
    except (WorkpadError, OSError):
        # No gig is bound to this folder, so no profile is: a typed 404.
        return missing
    try:
        profiles = list_profiles(resolved)
    except ProfileRecordError as exc:
        return (HTTPStatus.CONFLICT, exc.code, str(exc))
    profile = next((item for item in profiles if item.profile_id == profile_id), None)
    if profile is None:
        return missing
    if profile.state == "deleted":
        return missing
    if profile.state == "archived":
        return (HTTPStatus.CONFLICT, "scout_profile_archived", "an archived profile cannot take the setup's titles")
    return None


def _with_selected_profile_settings(backend: object, prefs_json: object) -> object:
    """0110-022: the saved prefs, with the SELECTED profile's own countries / work mode / city.

    The shared prefs hold the default profile's. A selected profile with its
    own search settings shows those in the wizard instead, so saving the
    form again does not overwrite them with the default's. Anything that
    cannot be read leaves the prefs as saved.
    """

    if not isinstance(prefs_json, dict):
        return prefs_json
    try:
        profile = backend._selected_profile()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - no profile to read: the saved prefs
        return prefs_json
    own = getattr(profile, "search_settings", None)
    if own is None:
        return prefs_json
    return {**prefs_json, "countries": list(own.countries), "work_mode": own.work_mode, "city": own.location}


class SetupRoutesMixin:
    """``Handler`` mixin: ``GET``/``PUT /api/setup`` and ``POST /api/setup/local-models``."""

    def _handle_post_setup_local_models(self) -> None:
        """``POST /api/setup/local-models`` -- ids the local server serves.

        Body ``{"url": ..., "protocol": ..., "api_key": ...}`` (protocol and
        api_key optional). Asks the operator's own loopback server (validated
        here before any request leaves) for ``GET <url>/v1/models``; both the
        OpenAI and the Anthropic dialect answer that path with
        ``{"data": [{"id": ...}]}``. POST so an optional server key travels in
        the body rather than a URL. The reply carries the model ids alone --
        never a provider response body, never a credential -- so the wizard's
        model dropdown can be populated without the browser touching the
        local server directly.
        """

        raw = self._read_json_body()
        if raw is None:
            return
        if not isinstance(raw, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "body must be a JSON object")
            return
        url = raw.get("url")
        protocol = raw.get("protocol") or LocalLLMProtocol.OPENAI.value
        api_key = raw.get("api_key")
        if not isinstance(url, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "url must be a string")
            return
        if not isinstance(protocol, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "protocol must be a string")
            return
        if api_key is not None and not isinstance(api_key, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "api_key must be a string or null")
            return
        try:
            base_url = checked_loopback_endpoint(url, "url")
        except ValueError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_loopback_url", str(exc))
            return
        if protocol not in _LOCAL_LLM_PROTOCOLS:
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_value",
                f"protocol must be one of {', '.join(_LOCAL_LLM_PROTOCOLS)}",
            )
            return
        try:
            models = list_local_models(base_url, api_key=api_key, protocol=protocol)
        except Exception as exc:  # noqa: BLE001 - the local server may be down mid-wizard
            # Type name only: the adapter's error text is already operator-
            # safe, but nothing from the provider crosses here either.
            self._error(
                HTTPStatus.BAD_GATEWAY,
                "local_models_unavailable",
                f"could not read models from {base_url} ({type(exc).__name__}). Is the server running at that URL?",
            )
            return
        self._write_json(
            HTTPStatus.OK,
            {"schema_version": "scout-find-jobs-local-models-response:1", "models": list(models)},
        )

    @reads_committed
    def _handle_get_setup(self) -> None:
        try:
            prefs_json = self._backend.read_setup()
        except DiscoveryUnavailableError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
            return
        if prefs_json is not None:
            # uat-bug-028: prefs saved before this fix can hold the starter
            # placeholder as the city (it was pre-filled from find-jobs.json);
            # it reads as no city, like find-jobs.json's own location.
            if isinstance(prefs_json, dict) and is_location_placeholder(prefs_json.get("city")):
                prefs_json = {**prefs_json, "city": None}
            prefs_json = _with_selected_profile_settings(self._backend, prefs_json)
            # uat-bug-038 round-trip: the saved model choice (and its local-LLM
            # keys) rides beside prefs, so the Settings UI shows the last-saved
            # model target and the local-LLM block; a Finish that sends no
            # model_target therefore keeps what the page displayed.
            try:
                config, _config_bytes = self._backend.read_config()
                model_keys = _model_keys_from_config(config)
            except (ConfigMissingError, FindJobsContractError, LookupError):
                model_keys = {}
            self._write_json(
                HTTPStatus.OK,
                {"schema_version": "scout-find-jobs-setup-response:1", "prefs": prefs_json, **model_keys},
            )
            return
        # CHANGE #1: 404 prefs_missing carries a pre-fill derived from the
        # current find-jobs.json where possible, so the UI's first-run
        # interview starts from the operator's existing config instead of
        # a blank form.
        try:
            config, _config_bytes = self._backend.read_config()
            prefill = _prefs_prefill_from_config(config)
            model_keys = _model_keys_from_config(config)
        except (ConfigMissingError, FindJobsContractError, LookupError):
            config = FindJobsConfig(
                roles=(),
                merged_queries=(),
                location=None,
                remote=True,
                published_after=None,
                sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
            )
            prefill = _prefs_prefill_from_config(config)
            model_keys = _model_keys_from_config(config)
        self._error_with_extra(
            HTTPStatus.NOT_FOUND,
            "prefs_missing",
            "no discovery preferences saved yet; complete the setup interview",
            {"prefill": {**prefill, **model_keys}},
        )

    def _handle_put_setup(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        # uat-bug-038: a model_target that is not a ModelTarget value is a
        # 422 (the body is well-formed JSON, the value is unprocessable),
        # answered before anything is saved.
        if isinstance(body, dict):
            target_errors: dict[str, str] = {}
            _setup_model_target(body, target_errors)
            if target_errors:
                self._error_with_extra(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_value",
                    target_errors["model_target"],
                    {"field_errors": target_errors},
                )
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
        # uat-bug-024: the profile the caller named, or None (the selected
        # one). Passed on only when given, so a backend that knows nothing
        # of it is called the way it always was.
        profile_id = prefs_fields.pop("profile_id", None)
        if profile_id is not None:
            refusal = _named_profile_refusal(self._backend, str(profile_id))
            if refusal is not None:
                self._error(*refusal)
                return
        try:
            if profile_id is None:
                prefs_json = self._backend.write_setup(prefs_fields)
            else:
                prefs_json = self._backend.write_setup(prefs_fields, profile_id=str(profile_id))
        except DiscoveryUnavailableError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
            return
        # Field *names* only -- prefs_fields carries the operator's actual
        # roles/companies/etc, which must never reach the log (ticket:
        # "setup saved (which fields changed, not values)").
        _logger.info(
            "setup saved: fields=%s profile=%s", sorted(prefs_fields), "selected" if profile_id is None else "named"
        )
        # 0.1.10.7 PL5: other settings re-open the pipeline steps whose inputs changed. The setup's settings are the
        # default profile's and what the other profiles fall back to, so with no profile named every profile is looked at.
        target = getattr(self._backend, "target", None)
        if target is not None and getattr(self._backend, "home_root", None) is not None:
            self._pipeline_profile_changed(target, None if profile_id is None else str(profile_id))
        self._write_json(
            HTTPStatus.OK,
            {"schema_version": "scout-find-jobs-setup-response:1", "prefs": prefs_json},
        )
