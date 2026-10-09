"""0.1.11.3 item 12: the Jobs rows carry the job's latest application status, as a LABEL, and an "applied" filter lists them. Synthetic only.

- a row says ``application`` ``{status, since}``: applied, then interview, offer, rejected and withdrawn each replace it
  (the latest status wins); a job with no application says ``null``;
- the label never changes a row's ``state`` (the assessment's) or its facts;
- ``state=applied`` lists exactly the jobs with an application status (applied and beyond), with every later status too;
- 0.1.11.5 (packet AP): such a job is LEFT OUT of the list unless ``state=applied`` asks for it, so the rows that
  carry a label are read with that filter here (tests/behaviors/scout_pipeline/test_applied_left_out.py pins the rule);
- the events are read ONCE per request, however many rows: the read does not grow with the page.
"""

from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest

from gigai.application_events import record_application
from gigai.scout import posting_search
from gigai.scout.find_jobs import job_state
from gigai.scout.find_jobs.company_index import board_list_url, index_stamp, refresh_company
from gigai.workpad import committed_read_cache

from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

SLUG = "acme-health"


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.watch(SLUG)
    jobs = [lever_job(SLUG, n) for n in range(1, 6)]
    fixture.cache.store("lever", board_list_url("lever", SLUG), body=json.dumps(jobs).encode("utf-8"), etag=None, last_modified=None, marker=None)
    refresh_company(fixture.index, fixture.cache, ats="lever", slug=SLUG, company=None, observed_at=index_stamp(days_ago(1)))
    return fixture


def _record(fx: PostingsFixture, n: int, kind: str, occurred_at: str) -> None:
    with committed_read_cache():
        result = record_application(
            resolved=fx.base.gig.resolved,
            data={
                "external_ref": job_url(SLUG, n), "event_kind": kind, "occurred_at": occurred_at, "timezone": "UTC",
                "operation_key": f"applied-label-{uuid.uuid4()}",
            },
            confirm=True,
        )
    assert result["status"] == "recorded"


def _search(fx: PostingsFixture, **more: object) -> dict:
    return posting_search.search_postings(fx.home_root, fx.target, now=NOW, **more)


def _by_job(response: dict) -> dict[str, dict]:
    return {row["job_identity"]: row for row in response["postings"]["rows"]}


def test_no_application_is_null_for_every_row(fx: PostingsFixture) -> None:
    rows = _search(fx)["postings"]["rows"]
    assert len(rows) == 5 and all(row["application"] is None for row in rows)


def test_each_status_replaces_the_one_before_it(fx: PostingsFixture) -> None:
    job = job_url(SLUG, 1)
    steps = [
        ("applied", "2026-10-06T10:00:00Z", "applied"),
        ("interview_scheduled", "2026-10-08T10:00:00Z", "interview_scheduled"),
        ("offer_received", "2026-10-12T10:00:00Z", "offer_received"),
        ("rejected", "2026-10-14T10:00:00Z", "rejected"),
    ]
    for kind, when, expected in steps:
        _record(fx, 1, kind, when)
        row = _by_job(_search(fx, states=["applied"]))[job]
        assert row["application"] == {"status": expected, "since": when}, kind
        others = _by_job(_search(fx))
        assert len(others) == 4 and job not in others and all(other["application"] is None for other in others.values())


def test_withdrawn_after_an_interview(fx: PostingsFixture) -> None:
    _record(fx, 2, "applied", "2026-10-06T10:00:00Z")
    _record(fx, 2, "interview_scheduled", "2026-10-08T10:00:00Z")
    _record(fx, 2, "withdrawn", "2026-10-09T10:00:00Z")
    assert _by_job(_search(fx, states=["applied"]))[job_url(SLUG, 2)]["application"] == {"status": "withdrawn", "since": "2026-10-09T10:00:00Z"}


def test_the_label_changes_no_state_or_fact_of_a_row(fx: PostingsFixture) -> None:
    before = _search(fx)
    _record(fx, 3, "applied", "2026-10-06T10:00:00Z")
    _record(fx, 3, "interview_scheduled", "2026-10-08T10:00:00Z")
    strip = lambda rows: [{key: value for key, value in row.items() if key != "application"} for row in rows]  # noqa: E731
    job = job_url(SLUG, 3)
    # The applied row itself, read with its filter: the same row as before, plus its label.
    assert strip(_search(fx, states=["applied"])["postings"]["rows"]) == strip([_by_job(before)[job]])
    # 0.1.11.5: the default list is the other four, each as before, in the same order.
    after = _search(fx)
    assert strip(after["postings"]["rows"]) == strip([row for row in before["postings"]["rows"] if row["job_identity"] != job])
    assert after["counts"] == {**before["counts"], "matched": 4, "shown": 4, "new": 4, "by_state": {"not_assessed": 4}, "applied": 1, "postings": 4}


def test_the_applied_filter_lists_every_job_with_a_status(fx: PostingsFixture) -> None:
    assert _search(fx, states=["applied"])["postings"]["rows"] == []
    _record(fx, 1, "applied", "2026-10-06T10:00:00Z")
    _record(fx, 4, "applied", "2026-10-06T11:00:00Z")
    _record(fx, 4, "rejected", "2026-10-07T11:00:00Z")
    listed = _search(fx, states=["applied"])
    assert sorted(_by_job(listed)) == sorted([job_url(SLUG, 1), job_url(SLUG, 4)])
    assert listed["counts"]["matched"] == 2
    assert {row["application"]["status"] for row in listed["postings"]["rows"]} == {"applied", "rejected"}
    assert len(_search(fx)["postings"]["rows"]) == 3, "0.1.11.5: the list without the filter leaves the two out"


def test_the_events_are_read_once_per_request_not_per_row(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    for n in range(1, 6):
        _record(fx, n, "applied", f"2026-10-0{n}T10:00:00Z")
    reads: list[int] = []
    real = job_state.read_application_events
    monkeypatch.setattr(job_state, "read_application_events", lambda resolved: reads.append(1) or real(resolved))
    response = _search(fx, states=["applied"])
    assert len(response["postings"]["rows"]) == 5 and all(row["application"] for row in response["postings"]["rows"])
    assert len(reads) == 1, "one read of the events for the whole list"
    reads.clear()
    assert len(_search(fx, states=["applied"], limit=2)["postings"]["rows"]) == 2
    assert len(reads) == 1, "and the same one read for a page of two"
    reads.clear()
    assert _search(fx)["postings"]["rows"] == [] and len(reads) == 1, "0.1.11.5: and one for the list that leaves them out"
