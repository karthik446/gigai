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

uat-bug-018 (job state):

- ``GET``: every row carries an additive ``job_state`` ``{state, since,
  next_events}`` -- the row's job's state from its application events alone
  (``job_state.application_state``), or ``null`` when no event puts the job
  in an application state yet (a job that was only ``saved``; its state is
  on its own row of ``GET /api/runs/{id}/results`` / ``GET
  /api/assessments``). Every row of one job carries the same value.
- ``POST`` names the job with exactly one of ``normalized_url`` or
  ``job_identity``. ``job_identity`` is the job's id as the job page has it:
  a pasted posting's ``text:sha256:<64 hex>`` is recorded verbatim as the
  ``external_ref`` (core's ``external_ref`` is an opaque string; no schema
  or record change), anything else is a posting URL and is normalized like
  ``normalized_url``. Both given, or neither, is a 422.
- ``POST`` enforces the pipeline (``job_state.check_transition``): an event
  the job's current state does not accept is a 409
  ``application_transition_refused`` (no interview before applied, nothing
  after rejected or withdrawn, not applied twice), and one dated before the
  job's current state began is a 409 ``application_event_out_of_order``.
  The check and the write run under one in-process lock, so two requests of
  this server cannot both pass the check; the read itself takes no journal
  writer lock. The response carries the job's new ``job_state``.

Every mutating call goes through the Handler's existing loopback + CSRF
guards (``do_POST``, same as every other state-changing route in this API).
Typed errors: ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from http import HTTPStatus

from ....application_events import EVENT_KINDS, ApplicationEventError, record_application
from ....workpad import resolve_workpad
from ..contracts import normalize_url as _find_jobs_normalize_url
from ..job_state import (
    JobStateError,
    application_state,
    check_transition,
    event_identity,
    group_events,
    normalize_job_identity,
    read_application_events,
)
from ...projection import projection_from_snapshot, read_projection_snapshot
from ...report_readers import default_reader_set
from .common import reads_committed

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


# uat-bug-018: one check-then-record at a time in this server process, so a
# double click cannot record the same transition twice.
_TRANSITION_LOCK = threading.Lock()

_ROWS_CACHE_LOCK = threading.Lock()
# 0110-033: (workpad, project, gig) -> (the snapshot's read token, the rows
# ``GET`` answers). The rows are built from the committed snapshot alone, so
# the same artifacts are always the same rows; building them again was 0.4 s
# at 300 events (1.7 s before the schemas were kept). The journal names "the
# same artifacts" with ``JournalSnapshot.read_token``: a recorded event is a
# new artifact, the snapshot is read again and has a new token, and the rows
# are built again. A snapshot with no token is built every time. One entry a
# workpad.
_rows_cache: dict[tuple[str, str, str], tuple[object, list[dict[str, object]]]] = {}


def _status_for(code: str) -> HTTPStatus:
    return _APPLICATION_ERROR_STATUS.get(code, HTTPStatus.CONFLICT)


def _job_state_json(events: list) -> dict[str, object] | None:
    state = application_state(events)
    return None if state is None else state.to_json()


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

    @reads_committed
    def _handle_get_applications(self) -> None:
        try:
            resolved = self._resolve_applications_gig()
        except LookupError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no target is configured")
            return
        if resolved is None:
            return

        snapshot = read_projection_snapshot(resolved)
        cache_key = (str(resolved.path), resolved.project_id, resolved.gig_id)
        with _ROWS_CACHE_LOCK:
            kept = _rows_cache.get(cache_key)
        if kept is not None and snapshot.read_token is not None and kept[0] == snapshot.read_token:
            applications = kept[1]
        else:
            projection = projection_from_snapshot(
                snapshot=snapshot,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                readers=default_reader_set(resolved),
            )
            applications = [_application_to_json(dict(item)) for item in projection.applications]
            # uat-bug-018: the rows just read are every event there is, so the
            # state needs no second read.
            states = {identity: _job_state_json(events) for identity, events in group_events(applications).items()}
            for row in applications:
                row["job_state"] = states.get(event_identity(row) or "")
            if snapshot.read_token is not None:
                with _ROWS_CACHE_LOCK:
                    _rows_cache[cache_key] = (snapshot.read_token, applications)
        self._write_json(
            HTTPStatus.OK,
            {
                "schema_version": "scout-applications-response:1",
                "applications": applications,
            },
        )

    def _handle_post_applications(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict):
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body must be a JSON object")
            return

        known_keys = {"normalized_url", "job_identity", "event_kind", "occurred_at", "notes"}
        unknown = set(body) - known_keys
        if unknown:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_key", f"unknown field(s): {sorted(unknown)}")
            return

        if "normalized_url" in body and "job_identity" in body:
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_value",
                "name the job with exactly one of normalized_url or job_identity",
            )
            return
        if "job_identity" in body:
            raw_identity = body.get("job_identity")
            if not isinstance(raw_identity, str) or not raw_identity.strip():
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "job_identity must be a non-empty string")
                return
            try:
                normalized_url = normalize_job_identity(raw_identity)
            except ValueError as exc:  # normalize_url's FindJobsContractError
                self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_value",
                    f"job_identity must be a posting URL or text:sha256:<64 hex>: {exc}",
                )
                return
        else:
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
        with _TRANSITION_LOCK:
            events = read_application_events(resolved).get(normalized_url, [])
            try:
                check_transition(events=events, event_kind=event_kind, occurred_at=occurred_at)
            except JobStateError as exc:
                self._error(HTTPStatus.CONFLICT, exc.code, str(exc))
                return
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
                "job_state": _job_state_json([*events, event] if isinstance(event, dict) else events),
            },
        )


__all__ = ["ApplicationsRoutesMixin"]
