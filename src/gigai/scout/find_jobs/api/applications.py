"""P9c: ``GET``/``POST /api/applications`` -- the application pipeline +
needs-action panels, and the posting card's "Mark applied" action.

``GET`` reads the Scout projection's own ``applications`` rows
(``gigai.scout.projection.projection_from_snapshot``, built with the same
``default_reader_set`` ``report.py``'s CLI report/``rebuild_projection``
use) -- never a bespoke re-read of ``records/applications/events/`` -- so
this route gets the A1 ``linked_posting`` join
(``projection._link_external_refs``) for free and reads the exact same
rows the CLI report already proves correct. Unlike ``rebuild_projection``,
this calls ``projection_from_snapshot`` directly rather than also writing
the disposable ``state.sqlite`` report cache: a GET is read-only and should
not commit a cache write as a side effect of every poll. Each row is the
projection's own dict, trimmed of internal-only bookkeeping fields
(``event_path``, ``journal_sequence``) that name no committed contract.

``POST`` accepts ``{normalized_url, event_kind, occurred_at?, notes?}`` and
records an ``external_ref`` application event through the CORE record path
(``gigai.application_events.record_application`` -- never reimplemented
here, per the task spec). ``normalized_url`` is re-normalized server-side
with find-jobs' own ``normalize_url`` (``contracts.py``) so a raw posting
URL from a card's "Mark applied" button joins the SAME way Scout's own
projection join does (A1: exact string match against ``PostingRow.
normalized_url``) -- an already-normalized URL round-trips unchanged.
``occurred_at`` defaults to now (UTC); ``event_id``/``operation_key`` are
generated fresh per call (this route records a new event every time it's
called -- there is no natural idempotency key a "Mark applied" click body
carries, unlike the CLI's ``--input`` file flow). An unknown URL (no
posting has ever been acquired with it) still records -- the event comes
back with ``linked_posting: None`` (A1 (5): unlinked, never dropped, never
an error).

Every mutating call goes through the Handler's existing loopback + CSRF
guards (``do_POST``, same as every other state-changing route in this API).
Typed errors: ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from http import HTTPStatus

from ....application_events import EVENT_KINDS, ApplicationEventError, record_application
from ....journal import run_with_journal_writer
from ....workpad import resolve_workpad
from ..contracts import normalize_url as _find_jobs_normalize_url
from ...projection import projection_from_snapshot
from ...report_readers import default_reader_set

_APPLICATION_ERROR_STATUS: dict[str, HTTPStatus] = {
    "application_event_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_ref_conflict": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_ref_missing": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_external_ref_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_opportunity_ref_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_date_required": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_document_ref_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_request_evidence_required": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_confirmation_required": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_operation_required": HTTPStatus.UNPROCESSABLE_ENTITY,
    "application_event_identity_invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
}


def _status_for(code: str) -> HTTPStatus:
    return _APPLICATION_ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


def _application_to_json(event: dict[str, object]) -> dict[str, object]:
    """The projection's own application row, minus internal bookkeeping
    (``event_path``, ``journal_sequence``) that names no committed field."""

    return {key: value for key, value in event.items() if key not in {"event_path", "journal_sequence"}}


class ApplicationsRoutesMixin:
    """``Handler`` mixin: ``GET``/``POST /api/applications``."""

    def _applications_target(self):
        backend = self._backend
        target = getattr(backend, "target", None)
        if target is None:
            self._error(HTTPStatus.NOT_FOUND, "target_unavailable", "a target path is required")
            return None
        return target

    def _resolve_applications_gig(self):
        backend = self._backend
        target = self._applications_target()
        if target is None:
            return None
        return resolve_workpad(home_root=backend.home_root, requested_target=target, gig_id=None, allow_semantic_state=True)

    def _handle_get_applications(self) -> None:
        try:
            resolved = self._resolve_applications_gig()
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return
        if resolved is None:
            return

        def read(writer):
            snapshot = writer.snapshot(("records/", "runs/", "run-plans/", "references/", "run-inputs/", "manifests/"))
            return projection_from_snapshot(
                snapshot=snapshot,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                readers=default_reader_set(resolved),
            )

        projection = run_with_journal_writer(
            workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=read,
        )
        self._write_json(
            HTTPStatus.OK,
            {
                "schema_version": "scout-applications-response:1",
                "applications": [_application_to_json(dict(item)) for item in projection.applications],
            },
        )

    def _handle_post_applications(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return

        known_keys = {"normalized_url", "event_kind", "occurred_at", "notes"}
        unknown = set(body) - known_keys
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown field(s): {sorted(unknown)}")
            return

        raw_url = body.get("normalized_url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "normalized_url must be a non-empty string")
            return
        try:
            normalized_url = _find_jobs_normalize_url(raw_url)
        except Exception as exc:  # noqa: BLE001 - find_jobs' own normalize_url raises FindJobsContractError (ValueError)
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", f"normalized_url is invalid: {exc}")
            return

        event_kind = body.get("event_kind")
        if not isinstance(event_kind, str) or event_kind not in EVENT_KINDS:
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_value",
                f"event_kind must be one of {sorted(EVENT_KINDS)}",
            )
            return

        occurred_at = body.get("occurred_at")
        if occurred_at is None:
            occurred_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        elif not isinstance(occurred_at, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "occurred_at must be a string when given")
            return

        notes = body.get("notes")
        if notes is not None and not isinstance(notes, str):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "notes must be a string when given")
            return

        try:
            resolved = self._resolve_applications_gig()
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return
        if resolved is None:
            return

        # No natural idempotency key exists for a UI button click (unlike the
        # CLI's --input file, which the operator can safely re-submit) -- a
        # fresh operation_key/event_id per call means every POST records a
        # new event, matching "Mark applied" being a one-shot action.
        operation_key = f"scout-application-record-ui-{uuid.uuid4()}"
        data: dict[str, object] = {
            "operation_key": operation_key,
            "external_ref": normalized_url,
            "event_kind": event_kind,
            "occurred_at": occurred_at,
            "timezone": "UTC",
            "notes": notes,
        }
        try:
            result = record_application(resolved=resolved, data=data, confirm=True)
        except ApplicationEventError as exc:
            self._error(_status_for(exc.code), exc.code, str(exc))
            return
        event = result.get("event")
        self._write_json(
            HTTPStatus.CREATED,
            {
                "schema_version": "scout-application-response:1",
                "status": result.get("status"),
                "event": event,
            },
        )


__all__ = ["ApplicationsRoutesMixin"]
