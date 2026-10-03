"""0.1.10.7 C: the 0.1.10.5 per-profile story bank becomes user-level answers, once.

A SYNTHETIC 0.1.10.5 state is built by hand: each profile's answers in its own
``experience_qa`` record (as ``AnswerScope`` wrote them) and the per-profile overlay
``story_bank/bank.json`` (``scout-story-bank:1``). Two profiles answered the SAME question:
once with different words (a conflict) and once with the same words (merged).

Covers acceptance (d): every answer is there afterwards, one per question; the newest
write wins a conflict and the other text is kept in the history (and untouched in the
journal); jobs are unioned; revision, dates and writer survive; the counts are reported;
the old file is kept as ``bank.v1.json``; a second run changes nothing and counts 0; an
assessment sealed by 0.1.10.5 is not made stale by migrating.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.native_records import list_native_records
from gigai.scout import story_bank
from gigai.scout.experience_answers import AnswerScope, list_answer_rows, record_answer

from tests.support.answers_stories_fixtures import RESUME, paths, two_profiles
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_OLD = "2026-09-20T10:00:00.000000Z"
_NEW = "2026-09-28T10:00:00.000000Z"
_JOB_A = {"job_identity": "https://jobs.example.invalid/acme/1", "title": "Platform Engineer", "company": "Acme", "url": "https://jobs.example.invalid/acme/1", "kind": "answered", "at": _OLD}
_JOB_B = {"job_identity": "https://jobs.example.invalid/globex/2", "title": "Data Engineer", "company": "Globex", "url": "https://jobs.example.invalid/globex/2", "kind": "reused", "at": _NEW}


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=RESUME)


def _v1_answer(fx: ProfileFixtureGig, owner: str, own_records: set[str], question_id: str, question: str, answer: str) -> str:
    """One answer as 0.1.10.5 wrote it: into a record only ``owner`` appends to. The record id."""

    result = record_answer(
        home_root=fx.home_root, requested_target=fx.target, question_id=question_id, prompt=question, answer=answer,
        scope=AnswerScope(existing_record=None, append_records=frozenset(own_records), owner=owner),
    )
    own_records.add(result.record_id)
    return result.record_id


def _v1_mark(owner: str, record_id: str, question_id: str, revision: int, updated_at: str) -> str:
    """The mark 0.1.10.5 sealed for an entry (``story_bank._mark`` as it was)."""

    return digest_imported_bytes(f"entry\n{owner}\n{record_id}\n{question_id}\n{revision}\n{updated_at}".encode("utf-8"))[len("sha256:"):][:16]


@pytest.fixture
def v1(fx: ProfileFixtureGig) -> dict[str, object]:
    """Two profiles, 0.1.10.5: gcp answered differently by both, python the same by both, aws only by the second."""

    default, other = two_profiles(fx)
    mine: set[str] = set()
    theirs: set[str] = set()
    record_default = _v1_answer(fx, default, mine, "cloud:gcp", "Do you have GCP experience?", "Two years on GCP.")
    _v1_answer(fx, default, mine, "years:python", "How many years of Python?", "Six years.")
    record_other = _v1_answer(fx, other, theirs, "cloud:gcp", "GCP?", "Four years on GCP, GKE and BigQuery.")
    _v1_answer(fx, other, theirs, "years:python", "Python years?", "Six years.")
    _v1_answer(fx, other, theirs, "cloud:aws", "AWS?", "One year on AWS.")
    assert mine.isdisjoint(theirs) and len(mine) == 1 and len(theirs) == 1

    def entry(at: str, revision: int, by: str, postings: list[dict[str, object]], **extra: object) -> dict[str, object]:
        return {
            "tag": None, "first_answered_at": at, "updated_at": at, "revision": revision, "written_by": by,
            "history": [{"at": at, "by": by, "action": "answered"}], "postings": postings, **extra,
        }

    overlay = {
        "schema_version": "scout-story-bank:1",
        "records": {record_default: default, record_other: other},
        "profiles": {
            default: {"share_with": None, "entries": {
                "cloud:gcp": entry(_OLD, 1, "operator", [_JOB_A]),
                "years:python": entry(_NEW, 2, "operator", [_JOB_A], tag="seniority"),
            }},
            other: {"share_with": default, "entries": {
                "cloud:gcp": entry(_NEW, 3, "agent", [_JOB_B]),
                "years:python": entry(_OLD, 1, "operator", [_JOB_B]),
                "cloud:aws": entry(_OLD, 1, "agent", []),
            }},
        },
    }
    old_file = story_bank.v1_path(fx.home_root, fx.target)
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_text(json.dumps(overlay), encoding="utf-8")
    return {"default": default, "other": other, "record_default": record_default, "record_other": record_other, "overlay": overlay}


def test_per_profile_entries_become_user_level_answers_with_nothing_lost(fx: ProfileFixtureGig, v1: dict[str, object]) -> None:
    rows_before = sorted((row.record_id, row.question_id, row.answer) for row in list_answer_rows(home_root=fx.home_root, requested_target=fx.target))
    revisions_before = sorted((row["record_id"], row["revision_id"]) for row in list_native_records(home_root=fx.home_root, requested_target=fx.target))

    report = story_bank.migrate(**paths(fx))

    assert {key: report[key] for key in ("migrated", "answers", "merged", "conflicts", "profiles")} == {
        "migrated": True, "answers": 3, "merged": 1, "conflicts": 1, "profiles": 2,
    }
    answers = {entry.question_id: entry for entry in story_bank.read_bank(**paths(fx))}
    assert sorted(answers) == ["cloud:aws", "cloud:gcp", "years:python"], "one answer per question"

    # The conflict: the newest write is THE answer; the other's words are kept in the history.
    gcp = answers["cloud:gcp"]
    assert gcp.answer == "Four years on GCP, GKE and BigQuery." and gcp.question == "GCP?"
    assert (gcp.revision, gcp.written_by, gcp.updated_at, gcp.created_at) == (3, "agent", _NEW, _OLD)
    kept = [item for item in gcp.history if "answer" in item]
    assert [item["answer"] for item in kept] == ["Two years on GCP."] and kept[0]["at"] == _OLD and "default" in kept[0]["action"]
    assert [(job.job_identity, job.kind) for job in gcp.jobs] == [(_JOB_A["job_identity"], "answered"), (_JOB_B["job_identity"], "reused")], "jobs unioned"

    # The same words in both profiles: one answer, the newer entry's dates and tag, both jobs.
    python = answers["years:python"]
    assert python.answer == "Six years." and (python.revision, python.updated_at, python.tag) == (2, _NEW, "seniority")
    assert not [item for item in python.history if "answer" in item]
    assert {job.job_identity for job in python.jobs} == {_JOB_A["job_identity"], _JOB_B["job_identity"]}

    aws = answers["cloud:aws"]
    assert aws.answer == "One year on AWS." and aws.written_by == "agent"

    # Nothing was written to the journal: both profiles' records are exactly as they were.
    assert sorted((row.record_id, row.question_id, row.answer) for row in list_answer_rows(home_root=fx.home_root, requested_target=fx.target)) == rows_before
    assert sorted((row["record_id"], row["revision_id"]) for row in list_native_records(home_root=fx.home_root, requested_target=fx.target)) == revisions_before
    # The old file is kept beside the new one, byte for byte what it held.
    assert not story_bank.v1_path(fx.home_root, fx.target).exists()
    assert json.loads(story_bank.v1_kept_path(fx.home_root, fx.target).read_text(encoding="utf-8")) == v1["overlay"]


def test_a_second_run_changes_nothing_and_counts_zero(fx: ProfileFixtureGig, v1: dict[str, object]) -> None:
    first = story_bank.migrate(**paths(fx))
    written = story_bank.bank_path(fx.home_root, fx.target).read_bytes()

    again = story_bank.migrate(**paths(fx))

    assert {key: again[key] for key in ("migrated", "answers", "merged", "conflicts")} == {"migrated": False, "answers": 0, "merged": 0, "conflicts": 0}
    assert again["first_run"] == first["first_run"], "the first run's counts stay readable"
    assert story_bank.bank_path(fx.home_root, fx.target).read_bytes() == written
    assert len(story_bank.read_bank(**paths(fx))) == 3


def test_the_first_read_migrates_by_itself(fx: ProfileFixtureGig, v1: dict[str, object]) -> None:
    assert [entry.question_id for entry in story_bank.read_bank(**paths(fx), with_jobs=False)] == ["cloud:aws", "cloud:gcp", "years:python"]
    assert story_bank.migrate(**paths(fx))["migrated"] is False
    assert story_bank.v1_kept_path(fx.home_root, fx.target).is_file()


def test_migrating_marks_no_0_1_10_5_assessment_stale(fx: ProfileFixtureGig, v1: dict[str, object]) -> None:
    """What each profile's 0.1.10.5 assessment sealed for an answer it cited still reads as unchanged."""

    default, other = str(v1["default"]), str(v1["other"])
    cites_python = ["Story bank years:python: Six years."]
    cites_gcp = ["Story bank cloud:gcp: on GCP."]
    sealed_default = {"years:python": _v1_mark(default, str(v1["record_default"]), "years:python", 2, _NEW)}
    sealed_other = {
        "years:python": _v1_mark(other, str(v1["record_other"]), "years:python", 1, _OLD),
        "cloud:gcp": _v1_mark(other, str(v1["record_other"]), "cloud:gcp", 3, _NEW),
    }
    bank = story_bank.assess_bank(**paths(fx))

    # The merged answer: both profiles saw these words.
    assert story_bank.bank_matches(questions=(), evidence=cites_python, sealed_marks=sealed_default, bank=bank) == ()
    assert story_bank.bank_matches(questions=(), evidence=cites_python, sealed_marks=sealed_other, bank=bank) == ()
    # The conflict: the winner's own assessment stands; the profile whose answer was replaced is told.
    assert story_bank.bank_matches(questions=(), evidence=cites_gcp, sealed_marks=sealed_other, bank=bank) == ()
    replaced = {"cloud:gcp": _v1_mark(default, str(v1["record_default"]), "cloud:gcp", 1, _OLD)}
    assert [match.match for match in story_bank.bank_matches(questions=(), evidence=cites_gcp, sealed_marks=replaced, bank=bank)] == ["cited"]

    # A later write is a change, as it always was.
    story_bank.edit_answer(**paths(fx), question_id="years:python", answer="Seven years.", expected_revision=2)
    after = story_bank.assess_bank(**paths(fx))
    assert [match.match for match in story_bank.bank_matches(questions=(), evidence=cites_python, sealed_marks=sealed_default, bank=after)] == ["cited"]


def test_deleting_a_migrated_answer_removes_the_superseded_one_too(fx: ProfileFixtureGig, v1: dict[str, object]) -> None:
    story_bank.migrate(**paths(fx))

    story_bank.delete_answer(**paths(fx), question_id="cloud:gcp", expected_revision=3)

    assert [entry.question_id for entry in story_bank.read_bank(**paths(fx))] == ["cloud:aws", "years:python"], "the other profile's words do not come back"


def test_a_gig_that_never_had_the_0_1_10_5_bank_migrates_its_plain_answers(fx: ProfileFixtureGig) -> None:
    record_answer(home_root=fx.home_root, requested_target=fx.target, question_id="cloud:gcp", prompt="cloud:gcp", answer="Two years on GCP.")

    report = story_bank.migrate(**paths(fx))

    assert (report["migrated"], report["answers"], report["merged"], report["conflicts"]) == (True, 1, 0, 0)
    answer = story_bank.get_answer(**paths(fx), question_id="cloud:gcp")
    assert answer is not None and answer.answer == "Two years on GCP." and answer.revision == 0
    assert not story_bank.v1_kept_path(fx.home_root, fx.target).exists()


def test_a_basis_sealed_with_the_records_revision_before_0110_041_still_compares(fx: ProfileFixtureGig, v1: dict[str, object]) -> None:
    """The oldest stamps hold ``owner + record + the record's revision`` per entry: migrating leaves them current too."""

    default = str(v1["default"])
    record_id = str(v1["record_default"])
    revision_id = next(str(row["revision_id"]) for row in list_native_records(home_root=fx.home_root, requested_target=fx.target) if row["record_id"] == record_id)
    record_mark = digest_imported_bytes(f"{default}\n{record_id}\n{revision_id}".encode("utf-8"))[len("sha256:"):][:16]
    bank = story_bank.assess_bank(**paths(fx))

    assert story_bank.bank_matches(questions=(), evidence=["Story bank years:python: Six years."], sealed_marks={"years:python": record_mark}, bank=bank) == ()
