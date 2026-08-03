"""Enrollment-event reconciliation used by the S1 review fixture."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any, Protocol


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrollmentEvent:
    event_uuid: str
    academic_partner_uuid: str
    user_uuid: str
    program_code: str
    occurred_at: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Application:
    application_uuid: str
    academic_partner_uuid: str
    user_uuid: str
    status: str


@dataclass(frozen=True)
class Program:
    academic_partner_uuid: str
    code: str
    enrollment_window_opens_at: str


class Store(Protocol):
    def was_processed(self, event_uuid: str) -> bool: ...

    def mark_processed(self, event_uuid: str) -> None: ...

    def application_for_user(self, user_uuid: str) -> Application | None: ...

    def program(self, academic_partner_uuid: str, program_code: str) -> Program: ...

    def update_application_status(self, application_uuid: str, status: str) -> None: ...

    def events_after(
        self, cursor: str | None, page_size: int
    ) -> list[EnrollmentEvent]: ...


class Publisher(Protocol):
    def application_updated(
        self, application_uuid: str, event_uuid: str, status: str
    ) -> None: ...


@dataclass(frozen=True)
class ReconcileResult:
    event_uuid: str
    outcome: str
    detail: str | None = None


class EnrollmentReconciler:
    def __init__(
        self,
        store: Store,
        publisher: Publisher,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._clock = clock
        self._program_cache: dict[str, Program] = {}

    def reconcile(self, event: EnrollmentEvent) -> ReconcileResult:
        if self._store.was_processed(event.event_uuid):
            return ReconcileResult(event.event_uuid, "duplicate")

        self._store.mark_processed(event.event_uuid)
        application = self._store.application_for_user(event.user_uuid)
        if application is None:
            return ReconcileResult(event.event_uuid, "ignored", "application not found")

        program = self._program_cache.get(event.program_code)
        if program is None:
            program = self._store.program(
                event.academic_partner_uuid,
                event.program_code,
            )
            self._program_cache[event.program_code] = program

        window_opens_at = datetime.fromisoformat(
            program.enrollment_window_opens_at
        ).replace(tzinfo=UTC)
        next_status = (
            "ready_for_enrollment"
            if self._clock() >= window_opens_at
            else "awaiting_enrollment_window"
        )

        logger.info(
            "reconciling enrollment event",
            extra={
                "event_uuid": event.event_uuid,
                "user_uuid": event.user_uuid,
                "event_payload": event.payload,
            },
        )
        self._store.update_application_status(
            application.application_uuid,
            next_status,
        )
        self._publisher.application_updated(
            application.application_uuid,
            event.event_uuid,
            next_status,
        )
        return ReconcileResult(event.event_uuid, "updated")

    def reconcile_backlog(self, page_size: int = 100) -> list[ReconcileResult]:
        cursor: str | None = None
        results: list[ReconcileResult] = []
        while True:
            page = self._store.events_after(cursor, page_size)
            if not page:
                break

            eligible = [
                event
                for event in page
                if event.payload.get("event_type") == "enrollment_created"
            ]
            results.extend(self.reconcile(event) for event in eligible)
            cursor = page[-1].event_uuid

            if len(eligible) < page_size:
                break
        return results
