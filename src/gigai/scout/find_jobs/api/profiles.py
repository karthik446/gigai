"""F1-c: the profiles API (``GET``/``POST /api/profiles``,
``PUT /api/profiles/{profile_id}``, ``POST /api/profiles/{profile_id}/archive``,
``POST /api/profiles/selection``).

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
"""

from __future__ import annotations

from http import HTTPStatus

from .... import private_records
from ....canonical import digest_imported_bytes
from ....workpad import resolve_workpad
from ...profile_records import (
    ProfileRecord,
    ProfileRecordError,
    ProfileSelection,
    create_profile,
    list_profiles,
    selected_profile,
    switch_selected_profile,
    write_profile,
)
from ..contracts import PinnedResume


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


def _profile_to_json(record: ProfileRecord) -> dict[str, object]:
    """The profile's public shape: never the resume's own bytes/text."""

    return {
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
        self._write_json(
            HTTPStatus.OK,
            {
                "schema_version": "scout-profiles-response:1",
                "profiles": [_profile_to_json(item) for item in profiles],
                "selected_profile_id": selection.profile_id if selection is not None else None,
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
        known_keys = {"label", "titles", "titles_to_avoid", "queries", "resume_record_id", "resume_revision_id"}
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

        if errors:
            self._error_with_extra(
                HTTPStatus.BAD_REQUEST, "invalid_value", "invalid profile", {"field_errors": errors}
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
            )
        except ProfileRecordError as exc:
            self._error_from_profile_error(exc)
            return
        self._write_json(
            HTTPStatus.CREATED,
            {"schema_version": "scout-profile-response:1", "profile": _profile_to_json(record)},
        )

    # -- PUT /api/profiles/{profile_id} --------------------------------------

    def _handle_put_profile(self, profile_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return

        errors: dict[str, str] = {}
        known_keys = {"label", "titles", "titles_to_avoid", "queries", "resume_record_id", "resume_revision_id"}
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

        if errors:
            self._error_with_extra(
                HTTPStatus.BAD_REQUEST, "invalid_value", "invalid profile edit", {"field_errors": errors}
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
            )
        except ProfileRecordError as exc:
            self._error_from_profile_error(exc)
            return
        self._write_json(
            HTTPStatus.OK,
            {"schema_version": "scout-profile-response:1", "profile": _profile_to_json(record)},
        )

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
        self._write_json(
            HTTPStatus.OK,
            {"schema_version": "scout-profile-response:1", "profile": _profile_to_json(record)},
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
