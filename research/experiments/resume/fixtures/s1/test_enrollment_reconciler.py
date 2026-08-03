from __future__ import annotations

from datetime import UTC, datetime

from enrollment_reconciler import (
    Application,
    EnrollmentEvent,
    EnrollmentReconciler,
    Program,
)


PARTNER = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"
EVENT = "33333333-3333-4333-8333-333333333333"
APPLICATION = "44444444-4444-4444-8444-444444444444"


class FakeStore:
    def __init__(self) -> None:
        self.processed: set[str] = set()
        self.statuses: list[tuple[str, str]] = []

    def was_processed(self, event_uuid: str) -> bool:
        return event_uuid in self.processed

    def mark_processed(self, event_uuid: str) -> None:
        self.processed.add(event_uuid)

    def application_for_user(self, user_uuid: str) -> Application | None:
        return Application(APPLICATION, PARTNER, user_uuid, "started")

    def program(self, academic_partner_uuid: str, program_code: str) -> Program:
        return Program(academic_partner_uuid, program_code, "2026-07-01T00:00:00+00:00")

    def update_application_status(self, application_uuid: str, status: str) -> None:
        self.statuses.append((application_uuid, status))

    def events_after(self, cursor: str | None, page_size: int) -> list[EnrollmentEvent]:
        return []


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def application_updated(
        self, application_uuid: str, event_uuid: str, status: str
    ) -> None:
        self.events.append((application_uuid, event_uuid, status))


def make_event() -> EnrollmentEvent:
    return EnrollmentEvent(
        event_uuid=EVENT,
        academic_partner_uuid=PARTNER,
        user_uuid=USER,
        program_code="BS-CS",
        occurred_at="2026-07-28T12:00:00+00:00",
        payload={"event_type": "enrollment_created"},
    )


def test_happy_path_updates_and_publishes() -> None:
    store = FakeStore()
    publisher = FakePublisher()
    reconciler = EnrollmentReconciler(
        store,
        publisher,
        clock=lambda: datetime(2026, 7, 28, tzinfo=UTC),
    )

    result = reconciler.reconcile(make_event())

    assert result.outcome == "updated"
    assert store.statuses == [(APPLICATION, "ready_for_enrollment")]
    assert publisher.events == [(APPLICATION, EVENT, "ready_for_enrollment")]


def test_duplicate_is_skipped_after_success() -> None:
    store = FakeStore()
    publisher = FakePublisher()
    reconciler = EnrollmentReconciler(
        store,
        publisher,
        clock=lambda: datetime(2026, 7, 28, tzinfo=UTC),
    )

    reconciler.reconcile(make_event())
    duplicate = reconciler.reconcile(make_event())

    assert duplicate.outcome == "duplicate"
    assert len(publisher.events) == 1
