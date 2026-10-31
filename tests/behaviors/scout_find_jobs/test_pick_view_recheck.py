"""COVER-01 part 2 (0.1.11.13, gigai-4f9): the job's stored view checks the record's rows against the stored resume on the read.

The page reads the STORED record (``job_actions.pick_view``, ``GET /api/jobs/suggestions`` with the view). A record
0.1.11.12 wrote read a row that cites a role, a project or a school (an ENTRY id among its sources) as a lost line:
``coverage: lost``, a ``lost_mandatory_evidence`` reason, ``ready: false``. The server's check is mended, but only a
new write (a re-pick, a hand-back, use proposed, a new assessment) reaches that record. So the view re-checks the
rows in memory (``suggestions.recheck_stored``): the rows and the gate it serves are the stored resume's as it is now,
and the file on disk does not change.

The synthetic home of ``test_assessment_v9_flow`` (one profile, a small invented master, one posting, a scripted
model; the pipeline is off).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from gigai.scout import job_actions, suggestions
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.tailored_resume import read_tailored_resume

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import _JOB, _URL, _assess, _record, _resume_path, _v9_answer, fx  # noqa: F401 - `fx` is the fixture
from tests.support.posting_fixtures import PostingsFixture

ABSENT_PROJECT = "p-zzzzzz"


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


def test_a_record_of_0_1_11_12_is_read_against_the_stored_resume_and_the_file_does_not_change(fx: PostingsFixture) -> None:
    _assess(fx, _v9_answer(fx))
    stored = read_tailored_resume(_resume_path(fx))
    assert stored is not None
    shown = suggestions.shown_entry_ids(stored.result)
    assert shown, "the pick's resume names each role it shows by its entry id on the heading"
    # The record as 0.1.11.12 left it: the first (mandatory) row cites a role the resume shows and the second a project it
    # does not; both read `lost`, each with its reason, between two reasons of the gate's own. Not ready.
    record = _record(fx)
    first, second, third, fourth = record.requirements
    old_rows = (
        replace(first, sources=(shown[0],), in_resume=(), coverage="lost"),
        replace(second, sources=(ABSENT_PROJECT,), in_resume=(), coverage="lost"),
        third, fourth,
    )
    others = [{"code": "question_open", "requirement": fourth.id}, {"code": "answered", "requirement": None}]
    lost = [{"code": "lost_mandatory_evidence", "requirement": first.id}, {"code": "lost_mandatory_evidence", "requirement": second.id}]
    old_gate = {"decision": "suggest", "ready": False, "reasons": [others[0], *lost, others[1]]}
    path = suggestions.suggestions_write_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    suggestions.save_record(replace(record, gate=old_gate, requirements=old_rows))
    before = path.read_bytes()

    view = job_actions.pick_view(fx.home_root, fx.target, _URL)
    by_id = {row["id"]: row for row in view["requirements"]}
    assert (by_id[first.id]["coverage"], by_id[first.id]["in_resume"]) == ("kept", [shown[0]])
    assert (by_id[second.id]["coverage"], by_id[second.id]["in_resume"]) == ("lost", [])
    assert by_id[third.id]["coverage"] == third.coverage and by_id[fourth.id]["coverage"] is None
    assert view["gate"] == {"decision": "suggest", "ready": False, "reasons": [*others, lost[1]]}
    assert all("question_id" in row for row in view["requirements"])  # the view's own key on each row stays
    # What the page loads when a job is opened is the same view (`OPEN_KEYS`), and the record on disk is as it was.
    opened = job_actions.list_suggestions(fx.home_root, fx.target, _URL, with_view=True)
    assert opened["requirements"] == view["requirements"] and opened["gate"] == view["gate"]
    assert path.read_bytes() == before
    assert _record(fx).gate == old_gate and _record(fx).requirements == old_rows

    # The absent project is cited no more: nothing is lost, the gate keeps its own reasons, and the resume is ready.
    suggestions.save_record(replace(record, gate=old_gate, requirements=(old_rows[0], third, fourth)))
    view = job_actions.pick_view(fx.home_root, fx.target, _URL)
    assert [row["coverage"] for row in view["requirements"]] == ["kept", third.coverage, None]
    assert view["gate"] == {"decision": "suggest", "ready": True, "reasons": others}
