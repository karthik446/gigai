"""F1-c: the profiles API (``GET``/``POST /api/profiles``,
``PUT /api/profiles/{profile_id}``, ``POST /api/profiles/{profile_id}/archive``,
``POST /api/profiles/selection``, ``DELETE /api/profiles/{profile_id}``).

0110-047: ``DELETE`` archives a profile as state ``deleted`` (the journal keeps
it): it leaves ``GET /api/profiles``, the switcher, demand tagging and runs;
the default profile and the only active one are a ``409``; deleting the selected
profile selects the default.

Design source: ``orchestrator/docs/v0.1.9/spikes/S25-scout-interested-
profiles.md`` (Q6, Amendment A1, the Packet plan's F1-c row). Calls
``gigai.scout.profile_records`` directly -- this module does NOT add any
method to the ``Backend`` protocol/``ScoutFindJobsBackend``/
``NotWiredBackend`` (F1-b owns those). The gig is resolved from
``self._backend.home_root``/``self._backend.target`` the same way
``ScoutFindJobsBackend._resolved_run`` resolves it for the run routes
(``gig_id=None, allow_semantic_state=True``) -- duck-typed attribute access
on the backend rather than a new Protocol method, since
``ScoutFindJobsBackend`` already carries both as plain attributes
(``server.py``'s own ``__init__``).

Every mutating route (POST/PUT) goes through the Handler's existing
``_check_csrf()`` (enforced by ``do_POST``/``do_PUT`` before dispatch, same
as every other state-changing route in this API -- see ``server.py``).

Error mapping: ``profile_records.ProfileRecordError``'s typed ``code`` is
translated to an HTTP status by ``_PROFILE_ERROR_STATUS`` below; a code this
module doesn't recognize still gets a safe status (409, "a known-but-
unmapped conflict") rather than falling through to a 500 -- the exclusion's
"never a 500 on a known state" rule applies to every code the library can
raise, not just the ones this route currently exercises.

Responses never include resume text: only the sealed ``resume_ref`` triple
(``record_id``/``revision_id``/``content_sha256``) plus a caller-supplied or
resolved ``label`` field is ever returned -- no resume body byte is read by
this module for display.

0110-022: every profile but the default one has its own search settings
(location, work mode, countries, posted window), the nested optional
``search_settings`` object of ``POST``/``PUT``. Left out of a ``POST``, the
new profile is prefilled with the default's; in a ``PUT`` the keys given
replace the profile's own (the others keep their value) and ``null`` goes
back to "same as default". The default profile uses the setup settings:
``search_settings`` for it is a ``409 scout_profile_default_search_settings``.
An unknown nested key is a 400 that lists that object's ``allowed_keys``.
``location`` here is the search area, never the Resume display contact line.
"""

from __future__ import annotations

from http import HTTPStatus

from .... import private_records
from ....canonical import digest_imported_bytes
from ....workpad import resolve_workpad
from ...profile_records import (
    SEARCH_SETTINGS_KEYS,
    ProfileRecord,
    ProfileRecordError,
    ProfileSearchSettings,
    ProfileSelection,
    create_profile,
    default_profile,
    list_profiles,
    selected_profile,
    switch_selected_profile,
    write_profile,
)
from ..contracts import PinnedResume
from .common import reads_committed
from ..effective_config import default_search_settings


def _match_profile_id(path: str, *, suffix: str) -> str | None:
    """Extract ``{profile_id}`` from ``/api/profiles/{profile_id}<suffix>``.

    Mirrors ``common._match_run_id``'s exact shape (same signature, same
    "no embedded slash" rule) -- ``server.py``'s dispatch and
    ``tests/api_e2e/route_inventory.py``'s AST scanner both recognize this
    name the same way they already recognize ``_match_run_id``.
    """

    prefix = "/api/profiles/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix):]
    if suffix:
        if not remainder.endswith(suffix):
            return None
        profile_id = remainder[: -len(suffix)]
    else:
        profile_id = remainder
    if not profile_id or "/" in profile_id:
        return None
    return profile_id


# ProfileRecordError.code -> HTTP status. Every code this module's own
# codepaths can trigger is listed explicitly (see the module docstring for
# the read-time integrity codes' disposition: they're refusals on a known,
# named data-corruption state -- 409, never a silent 500).
_PROFILE_ERROR_STATUS: dict[str, int] = {
    "scout_profile_invalid": HTTPStatus.BAD_REQUEST,
    "scout_profile_unavailable": HTTPStatus.NOT_FOUND,
    "scout_profile_archived": HTTPStatus.CONFLICT,
    "scout_profile_deleted": HTTPStatus.CONFLICT,
    "scout_profile_default_delete": HTTPStatus.CONFLICT,
    "scout_profile_last_active": HTTPStatus.CONFLICT,
    "scout_profile_default_search_settings": HTTPStatus.CONFLICT,
    "profile_archive_requires_replacement": HTTPStatus.CONFLICT,
    "scout_profile_selection_dangling": HTTPStatus.CONFLICT,
    "scout_profile_write_corrupt": HTTPStatus.CONFLICT,
    "scout_profile_write_schema_invalid": HTTPStatus.CONFLICT,
    "scout_profile_write_identity_mismatch": HTTPStatus.CONFLICT,
    "scout_profile_write_duplicate_seq": HTTPStatus.CONFLICT,
    "scout_profile_write_seq_gap": HTTPStatus.CONFLICT,
    "scout_profile_selection_write_corrupt": HTTPStatus.CONFLICT,
    "scout_profile_selection_write_schema_invalid": HTTPStatus.CONFLICT,
    "scout_profile_selection_write_identity_mismatch": HTTPStatus.CONFLICT,
    "scout_profile_selection_write_duplicate_seq": HTTPStatus.CONFLICT,
    "scout_profile_selection_write_seq_gap": HTTPStatus.CONFLICT,
}


def _profile_to_json(record: ProfileRecord, *, default_profile_id: str | None = None) -> dict[str, object]:
    """The profile's public shape: never the resume's own bytes/text.

    0110-022: ``search_settings`` is the profile's OWN settings, ``None``
    when it uses the default's; ``is_default`` marks the profile that uses
    the setup settings.
    """

    return {
        "is_default": record.profile_id == default_profile_id,
        "search_settings": None if record.search_settings is None else record.search_settings.to_json(),
        "profile_id": record.profile_id,
        "revision": record.revision,
        "label": record.label,
        "state": record.state,
        "origin": record.origin,
        "resume_ref": record.resume_ref.to_json(),
        "titles": list(record.titles),
        "titles_to_avoid": list(record.titles_to_avoid),
        "queries": list(record.queries),
        "content_digest": record.content_digest,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _string_list(body: dict[str, object], field: str, errors: dict[str, str]) -> tuple[str, ...] | None:
    if field not in body:
        return None
    value = body[field]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        errors[field] = f"{field} must be an array of non-empty strings"
        return None
    return tuple(value)


# 0.1.11.3 item 11: a new profile with nothing to prefill from searches the US (the default country).
_NO_SETTINGS = ProfileSearchSettings(location=None, work_mode="any", countries=("US",), max_age_days=None)


def _search_settings(
    body: dict[str, object], errors: dict[str, str], *, base: ProfileSearchSettings | None
) -> ProfileSearchSettings | None:
    """The body's ``search_settings`` laid over ``base``; ``None`` for ``null`` or a refused one.

    Only called when the key is present. ``base`` is what the keys left out
    keep: the profile's own settings, else the default's.
    """

    value = body["search_settings"]
    if value is None:
        return None
    allowed = ", ".join(SEARCH_SETTINGS_KEYS)
    if not isinstance(value, dict):
        errors["search_settings"] = f"search_settings must be an object or null (allowed: {allowed})"
        return None
    unknown = sorted(set(value) - set(SEARCH_SETTINGS_KEYS))
    if unknown:
        errors["search_settings"] = f"search_settings contains unknown key(s): {unknown} (allowed: {allowed})"
        return None
    merged = {**(base or _NO_SETTINGS).to_json(), **value}
    try:
        return ProfileSearchSettings.from_json(merged)
    except ProfileRecordError as exc:
        errors["search_settings"] = str(exc)
        return None


def _field_errors_extra(errors: dict[str, str]) -> dict[str, object]:
    """``field_errors``, plus the nested object's ``allowed_keys`` when it had an unknown key."""

    extra: dict[str, object] = {"field_errors": errors}
    if "unknown key(s)" in errors.get("search_settings", ""):
        extra["allowed_keys"] = list(SEARCH_SETTINGS_KEYS)
    return extra


def _label(body: dict[str, object], errors: dict[str, str], *, required: bool) -> str | None:
    if "label" not in body:
        if required:
            errors["label"] = "label is required"
        return None
    value = body["label"]
    if not isinstance(value, str) or not value.strip():
        errors["label"] = "label must be a non-empty string"
        return None
    return value


class ProfilesRoutesMixin:
    """``Handler`` mixin: ``/api/profiles`` and ``/api/profiles/selection``."""

    def _resolve_profiles_gig(self):
        """Resolve the active gig the way ``ScoutFindJobsBackend._resolved_run``
        resolves it for the run routes -- ``gig_id=None,
        allow_semantic_state=True`` -- reading ``home_root``/``target``
        straight off the backend (duck-typed; ``ScoutFindJobsBackend`` carries
        both as plain attributes, see ``server.py``'s own ``__init__``)."""

        backend = self._backend
        target = backend.target
        if target is None:
            raise LookupError("a target path is required")
        return resolve_workpad(
            home_root=backend.home_root,
            requested_target=target,
            gig_id=None,
            allow_semantic_state=True,
        )

    def _default_search_settings(self) -> ProfileSearchSettings | None:
        return default_search_settings(home_root=self._backend.home_root, target=self._backend.target)

    def _profile_response(self, status: HTTPStatus, resolved, record: ProfileRecord) -> None:
        try:
            default = default_profile(list_profiles(resolved))
        except ProfileRecordError:
            default = None
        self._write_json(
            status,
            {
                "schema_version": "scout-profile-response:1",
                "profile": _profile_to_json(record, default_profile_id=None if default is None else default.profile_id),
            },
        )

    def _error_from_profile_error(self, exc: ProfileRecordError) -> None:
        status = _PROFILE_ERROR_STATUS.get(exc.code, HTTPStatus.CONFLICT)
        self._error(status, exc.code, str(exc))

    def _resolve_resume_ref(
        self,
        resolved,
        body: dict[str, object],
        errors: dict[str, str],
        *,
        fallback: PinnedResume | None,
    ) -> PinnedResume | None:
        """The create/edit routes' resume resolution.

        No ``resume_record_id``/``resume_revision_id`` in the body -> the
        given ``fallback`` (the selected profile's own ``resume_ref``, per
        the spec: "resume defaults to the selected profile's resume_ref
        unless a record/revision is given"). Both given -> resolve that
        exact committed record/revision (scoped to this gig, never reading
        the resume's text -- only its digest) into a fresh ``PinnedResume``.
        Exactly one of the pair given, or neither given with no fallback
        available, is a 400 field error.
        """

        record_id = body.get("resume_record_id")
        revision_id = body.get("resume_revision_id")
        if record_id is None and revision_id is None:
            if fallback is None:
                errors["resume_record_id"] = "no default resume is available; provide resume_record_id/resume_revision_id"
            return fallback
        if not isinstance(record_id, str) or not record_id:
            errors["resume_record_id"] = "resume_record_id must be a non-empty string"
            return None
        if not isinstance(revision_id, str) or not revision_id:
            errors["resume_revision_id"] = "resume_revision_id must be a non-empty string"
            return None
        try:
            read = private_records.read_record(
                home_root=self._backend.home_root,
                requested_target=self._backend.target,
                record_id=record_id,
                revision_id=revision_id,
                content=True,
                gig_id=resolved.gig_id,
            )
        except private_records.PrivateRecordError as exc:
            errors["resume_record_id"] = str(exc)
            return None
        content = read.get("content")
        if not isinstance(content, bytes):
            errors["resume_record_id"] = "resume record has no readable content"
            return None
        digest = digest_imported_bytes(content)
        return PinnedResume(record_id=record_id, revision_id=revision_id, content_sha256=digest)

    # -- GET /api/profiles --------------------------------------------------

    @reads_committed
    def _handle_get_profiles(self) -> None:
        try:
            resolved = self._resolve_profiles_gig()
            # selected_profile() runs the F1-a migrate-on-first-read
            # (ensure_default_profile) before reading -- called BEFORE
            # list_profiles() so a gig's very first GET already sees the
            # migrated default profile, not a stale empty list captured
            # before the migration committed.
            selection = selected_profile(resolved, home_root=self._backend.home_root, target=self._backend.target)
            profiles = list_profiles(resolved)
        except ProfileRecordError as exc:
            self._error_from_profile_error(exc)
            return
        default = default_profile(profiles)
        # 0110-047: a deleted profile is gone from every list; its history stays in the journal.
        profiles = tuple(item for item in profiles if item.state != "deleted")
        default_profile_id = None if default is None else default.profile_id
        shared = self._default_search_settings()
        self._write_json(
            HTTPStatus.OK,
            {
                "schema_version": "scout-profiles-response:1",
                "profiles": [_profile_to_json(item, default_profile_id=default_profile_id) for item in profiles],
                "selected_profile_id": selection.profile_id if selection is not None else None,
                # 0110-022: the profile that uses the setup settings, and those settings.
                "default_profile_id": default_profile_id,
                "default_search_settings": None if shared is None else shared.to_json(),
            },
        )

    # -- POST /api/profiles --------------------------------------------------

    def _handle_post_profiles(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return

        errors: dict[str, str] = {}
        known_keys = {"label", "titles", "titles_to_avoid", "queries", "resume_record_id", "resume_revision_id", "search_settings"}
        unknown = set(body) - known_keys
        if unknown:
            errors["_"] = f"unknown field(s): {sorted(unknown)}"

        label = _label(body, errors, required=True)
        titles = _string_list(body, "titles", errors)
        if "titles" not in errors and (titles is None or not titles):
            errors["titles"] = "titles must not be empty"
        titles_to_avoid = _string_list(body, "titles_to_avoid", errors) or ()
        queries = _string_list(body, "queries", errors)
        if queries is None and "queries" not in errors:
            queries = titles  # mirrors _update_find_jobs_config's own "queries defaults to titles" convention

        try:
            resolved = self._resolve_profiles_gig()
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return

        fallback_resume: PinnedResume | None = None
        try:
            current_selection = selected_profile(resolved, home_root=self._backend.home_root, target=self._backend.target)
            if current_selection is not None:
                fallback_resume = current_selection.resume_ref
        except ProfileRecordError:
            fallback_resume = None

        resume_ref = self._resolve_resume_ref(resolved, body, errors, fallback=fallback_resume)

        # 0110-022: a new profile starts with the default's settings; the
        # body's keys replace them, `null` keeps "same as default". The
        # first profile of a gig is the default one and stores none
        # (create_profile).
        search_settings = self._default_search_settings()
        if "search_settings" in body:
            search_settings = _search_settings(body, errors, base=search_settings)

        if errors:
            self._error_with_extra(
                HTTPStatus.BAD_REQUEST, "invalid_value", "invalid profile", _field_errors_extra(errors)
            )
            return
        assert label is not None and titles is not None and resume_ref is not None

        try:
            record = create_profile(
                resolved,
                label=label,
                titles=titles,
                titles_to_avoid=titles_to_avoid,
                queries=queries or titles,
                resume_ref=resume_ref,
                search_settings=search_settings,
            )
        except ProfileRecordError as exc:
            self._error_from_profile_error(exc)
            return
        if "resume_record_id" not in body and "resume_revision_id" not in body:
            self._first_master_selection(resolved, record)
        self._profile_response(HTTPStatus.CREATED, resolved, record)

    def _first_master_selection(self, resolved, record: ProfileRecord):  # noqa: ANN001, ANN202 - the gig's ResolvedWorkpad; the thread, or None
        """0.1.10.9 master P3: with a master resume stored, a new profile that names no resume gets its OWN resume.

        Its first selection of the master, made by code from the postings its
        titles match in the local index (no model, no request), instead of
        the selected profile's resume it was created with. P5: it is made
        AFTER the answer, on a background thread (``master.first_selection_later``):
        on a home with 290,000 postings the index read and the record write
        held this route for 7 to 8 s. The profile is usable at once; its
        resume becomes its own view when the selection lands. Best effort:
        with no master, or when the selection cannot be made, the profile
        stays as created. Returns the thread that makes it (``None``: no master).
        """

        from ...tailor_master import stored_master
        from .master import first_selection_later

        home_root, target = self._backend.home_root, self._backend.target
        if stored_master(home_root, target, resolved=resolved) is None:
            return None
        return first_selection_later(home_root, target, record.profile_id)

    # -- PUT /api/profiles/{profile_id} --------------------------------------

    def _handle_put_profile(self, profile_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return

        errors: dict[str, str] = {}
        known_keys = {"label", "titles", "titles_to_avoid", "queries", "resume_record_id", "resume_revision_id", "search_settings"}
        unknown = set(body) - known_keys
        if unknown:
            errors["_"] = f"unknown field(s): {sorted(unknown)}"

        label = _label(body, errors, required=False)
        titles = _string_list(body, "titles", errors)
        if titles is not None and not titles:
            errors["titles"] = "titles must not be empty"
        titles_to_avoid = _string_list(body, "titles_to_avoid", errors)
        queries = _string_list(body, "queries", errors)

        try:
            resolved = self._resolve_profiles_gig()
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return

        resume_ref: PinnedResume | None = None
        if "resume_record_id" in body or "resume_revision_id" in body:
            resume_ref = self._resolve_resume_ref(resolved, body, errors, fallback=None)

        # 0110-022: the keys given replace the profile's own settings (its
        # own, else the default's, fill the rest); `null` clears them.
        search_settings: ProfileSearchSettings | None = None
        clear_search_settings = False
        if "search_settings" in body:
            try:
                existing = next((item for item in list_profiles(resolved) if item.profile_id == profile_id), None)
            except ProfileRecordError as exc:
                self._error_from_profile_error(exc)
                return
            own = None if existing is None else existing.search_settings
            search_settings = _search_settings(body, errors, base=own or self._default_search_settings())
            clear_search_settings = body["search_settings"] is None

        if errors:
            self._error_with_extra(
                HTTPStatus.BAD_REQUEST, "invalid_value", "invalid profile edit", _field_errors_extra(errors)
            )
            return

        try:
            record = write_profile(
                resolved,
                profile_id=profile_id,
                label=label,
                titles=titles,
                titles_to_avoid=titles_to_avoid,
                queries=queries,
                resume_ref=resume_ref,
                search_settings=search_settings,
                clear_search_settings=clear_search_settings,
            )
        except ProfileRecordError as exc:
            self._error_from_profile_error(exc)
            return
        # 0.1.10.7 PL5: a new resume or other settings re-open this profile's pipeline steps whose inputs changed.
        self._pipeline_profile_changed(self._backend.target, profile_id)
        self._profile_response(HTTPStatus.OK, resolved, record)

    # -- POST /api/profiles/{profile_id}/archive -----------------------------

    def _handle_post_profile_archive(self, profile_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return
        replacement = body.get("replacement_profile_id")
        if replacement is not None and (not isinstance(replacement, str) or not replacement):
            self._error_with_extra(
                HTTPStatus.BAD_REQUEST,
                "invalid_value",
                "invalid archive request",
                {"field_errors": {"replacement_profile_id": "must be a non-empty string when given"}},
            )
            return
        unknown = set(body) - {"replacement_profile_id"}
        if unknown:
            self._error_with_extra(
                HTTPStatus.BAD_REQUEST,
                "invalid_value",
                "invalid archive request",
                {"field_errors": {"_": f"unknown field(s): {sorted(unknown)}"}},
            )
            return

        try:
            resolved = self._resolve_profiles_gig()
            record = write_profile(
                resolved,
                profile_id=profile_id,
                state="archived",
                replacement_profile_id=replacement,
            )
        except ProfileRecordError as exc:
            self._error_from_profile_error(exc)
            return
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return
        self._profile_response(HTTPStatus.OK, resolved, record)

    # -- DELETE /api/profiles/{profile_id} -----------------------------------

    def _handle_delete_profile(self, profile_id: str) -> None:
        try:
            resolved = self._resolve_profiles_gig()
            record = write_profile(resolved, profile_id=profile_id, state="deleted")
            selection = selected_profile(resolved, home_root=self._backend.home_root, target=self._backend.target)
        except ProfileRecordError as exc:
            self._error_from_profile_error(exc)
            return
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "schema_version": "scout-profile-delete-response:1",
                "deleted": record.profile_id,
                "selected_profile_id": selection.profile_id if selection is not None else None,
            },
        )

    # -- POST /api/profiles/selection ----------------------------------------

    def _handle_post_profiles_selection(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return
        profile_id = body.get("profile_id")
        unknown = set(body) - {"profile_id"}
        if unknown or not isinstance(profile_id, str) or not profile_id:
            self._error_with_extra(
                HTTPStatus.BAD_REQUEST,
                "invalid_value",
                "invalid selection request",
                {
                    "field_errors": {
                        **({"_": f"unknown field(s): {sorted(unknown)}"} if unknown else {}),
                        **({} if isinstance(profile_id, str) and profile_id else {"profile_id": "profile_id must be a non-empty string"}),
                    }
                },
            )
            return

        try:
            resolved = self._resolve_profiles_gig()
            selection: ProfileSelection = switch_selected_profile(resolved, profile_id=profile_id)
        except ProfileRecordError as exc:
            self._error_from_profile_error(exc)
            return
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "schema_version": "scout-profile-selection-response:1",
                "selected_profile_id": selection.selected_profile_id,
            },
        )


__all__ = ["ProfilesRoutesMixin", "_match_profile_id"]
