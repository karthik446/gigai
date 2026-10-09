"""0.1.11.9 PJ2: a job has ONE assessment, and a re-assessment by job identity replaces it, whichever role is named.

HISTORY. 0.1.11.5 kept one assessment per (role, job); `POST /api/answers` with `reassess: {job_identity}` took the
NEWEST across the roles, so an answer given on one role's page re-assessed the job for another. 0.1.11.6 (AN1) made the
caller name the role (`reassess.profile_id`) and refused a job two roles held when none was named
(`409 reassess_profile_required`). 0.1.11.9 removes the cause: the stores are keyed by job. These are the 0.1.11.6
tests, changed to the one-assessment behaviour.

Pinned, on a synthetic home (the invented master and posting of `test_pick_header_room.py`, a scripted model, a second
role on the same resume), the job assessed under one role and then again under the other:

- the store holds ONE assessment of the job, in the job's folder, and no role's folder exists;
- `reassess.profile_id` (the job page sends its own): that ONE assessment is replaced, the named role is recorded on
  it; one model call;
- without `profile_id`: the same, with the selected role recorded; no refusal, whoever assessed the job before; a
  deleted role's name on the stored assessment changes nothing;
- the stored `origin` stays (the job was assessed before, whichever role is named now);
- an id that is no role -> `404 profile_not_found`, nothing written; a `reassess` of another shape -> 422;
- the CLI: `gigai scout answer ... --reassess JOB [--profile ID]` is the same; `--profile` without `--reassess` is refused.
"""

from __future__ import annotations

import json

from click.testing import CliRunner
import pytest

from gigai.scout import profile_records
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.job_store_layout import job_digest
from gigai.scout.quick_assess import QuickAssessError, list_quick_assessments, quick_assess_dir, run_quick_assessment
from gigai.scout.scout_cli import scout_group

from tests.behaviors.scout_find_jobs.test_pick_header_room import _JOB, _URL, _Server, _answer, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.posting_fixtures import PostingsFixture
from tests.support.scout_profile_fixtures import uuids

#: A requirement no line of the invented master speaks to: only an answer settles it.
OPEN_REQUIREMENT = "Own the storefront services"
QUESTION = {"question_id": "scope:storefront", "question": "Have you owned a storefront service end to end?", "requirement": OPEN_REQUIREMENT}
TYPED = "Yes. I owned the storefront services of two teams for three years."
PENDING, MATCHED = "pending_user_answers", "matched_above_threshold"


# --- the home: one job, two profiles --------------------------------------------------------------------------------------


def second_profile(fixture: PostingsFixture) -> str:
    """A second active profile on the same resume, as the operator's home has two. Returns its id."""

    resolved = fixture.base.gig.resolved
    default = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == fixture.default_profile_id)
    made = profile_records.create_profile(
        resolved, label="Staff backend", titles=("staff backend engineer",), titles_to_avoid=(), queries=("staff backend engineer",),
        resume_ref=default.resume_ref, uuid_factory=uuids(40),
    )
    return made.profile_id


def _rows(fixture, ids: dict[str, str] | None) -> dict:
    """The scripted Matched answer plus the open requirement, each row with the id the job's first assessment gave it."""

    body = json.loads(_answer(fixture))
    body["matrix"].append({"requirement": OPEN_REQUIREMENT, "class": "askable", "status": "unclear", "resume_evidence": [], "sources": [], "class_basis": "You will own the storefront services."})
    for row in body["matrix"]:
        if ids is not None:
            row["id"] = ids[row["requirement"]]
    return body


def asking(fixture, ids: dict[str, str] | None = None) -> str:
    """The scripted assessment that ASKS: one requirement is unclear, with one question for the user."""

    return json.dumps({**_rows(fixture, ids), "verdict": PENDING, "questions": [QUESTION]})


def settled(fixture, ids: dict[str, str]) -> str:
    """The scripted assessment made once the answer is on record: the open requirement is met by it, nothing is asked."""

    body = _rows(fixture, ids)
    body["matrix"][-1].update({"status": "met", "resume_evidence": [TYPED]})
    return json.dumps({**body, "verdict": MATCHED, "questions": []})


def assess(fixture, profile_id: str, answer: str):
    fixture.base.model.assessed = answer
    return run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=profile_id)),
        home_root=fixture.home_root, target=fixture.target, config=fixture_config(fixture.home_root),
    )


def held_by_two(fixture, *, page_profile: str) -> tuple[str, str, dict[str, str]]:
    """The job assessed under both roles, each time asking; the OTHER role assessed it last.

    Returns (the page's role, the other, the requirement ids of the job). The model's NEXT answer is the settled one.
    """

    first, second = fixture.default_profile_id, second_profile(fixture)
    mine, other = (first, second) if page_profile == "default" else (second, first)
    made = assess(fixture, mine, asking(fixture))
    assert made.result.verdict.value == PENDING
    ids = {row.requirement: row.id for row in made.result.matrix}
    assert assess(fixture, other, asking(fixture, ids)).result.verdict.value == PENDING
    fixture.base.model.assessed = settled(fixture, ids)
    fixture.base.model.assess_prompts.clear()
    return mine, other, ids


def matched_on_both(fixture: PostingsFixture, *, page_profile: str) -> tuple[str, str]:
    """The job assessed Matched under both roles (its resume picked); the OTHER role assessed it last.

    Returns (the page's role, the other). The model's next answer is the same Matched one. (Used by the browser test.)
    """

    first, second = fixture.default_profile_id, second_profile(fixture)
    mine, other = (first, second) if page_profile == "default" else (second, first)
    made = assess(fixture, mine, _answer(fixture))
    body = json.loads(_answer(fixture))
    for row, stored in zip(body["matrix"], sorted(made.result.matrix, key=lambda item: [entry["requirement"] for entry in body["matrix"]].index(item.requirement))):
        row["id"] = stored.id
    assert assess(fixture, other, json.dumps(body)).result.verdict.value == MATCHED
    fixture.base.model.assess_prompts.clear()
    return mine, other


# --- the tests -----------------------------------------------------------------------------------------------------------


def _stored(fixture: PostingsFixture) -> list[tuple[str | None, str, str]]:
    """Every stored assessment of the job: (the role recorded on it, when it was made, its verdict)."""

    return [
        (item.resume.profile_id, item.updated_at, item.result.verdict.value)
        for item in list_quick_assessments(fixture.home_root, fixture.target) if item.job.job_identity == _JOB
    ]


def _files(fixture: PostingsFixture) -> dict[str, list[str]]:
    """folder -> the assessment files in it, for every folder of the assessment store."""

    root = quick_assess_dir(fixture.home_root, fixture.target)
    return {folder.name: sorted(path.name for path in folder.glob("*.json")) for folder in sorted(root.iterdir()) if folder.is_dir()}


def _one_assessment(fixture: PostingsFixture) -> tuple[str | None, str, str]:
    """THE symptom: the job has exactly one stored assessment, in the job's folder, and no role's folder holds one."""

    assert _files(fixture) == {"job": [f"{job_digest(_JOB)}.json"]}
    (only,) = _stored(fixture)
    return only


def _answers(api: _Server) -> list[str]:
    return [item["question_id"] for item in api.client.get("/api/answers").json()["answers"]]


def _post(api: _Server, reassess: object):
    return api.client.post("/api/answers", json={"question_id": QUESTION["question_id"], "question": QUESTION["question"], "answer": TYPED, "reassess": reassess})


@pytest.mark.parametrize("page_profile", ["default", "second"])
def test_an_answer_re_assesses_the_jobs_one_assessment_whichever_role_is_named(fx: PostingsFixture, server: _Server, page_profile: str) -> None:  # noqa: F811
    mine, other, _ids = held_by_two(fx, page_profile=page_profile)
    before = _one_assessment(fx)
    assert (before[0], before[2]) == (other, PENDING), "the other role assessed the job last"

    answered = _post(server, {"job_identity": _JOB, "profile_id": mine})

    assert answered.status_code == 201, answered.text
    made = answered.json()["reassessed"]
    assert (made["resume"]["profile_id"], made["result"]["verdict"]) == (mine, MATCHED)
    after = _one_assessment(fx)
    assert (after[0], after[2]) == (mine, MATCHED) and after[1] > before[1], "the job's one assessment was not replaced"
    assert len(fx.base.model.assess_prompts) == 1, "one model call"
    assert QUESTION["question_id"] in _answers(server)


def test_a_job_two_roles_assessed_needs_no_role_named(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    """0.1.11.6 refused this (`409 reassess_profile_required`): there were two assessments to choose between."""

    _mine, _other, _ids = held_by_two(fx, page_profile="default")
    before = _one_assessment(fx)

    answered = _post(server, {"job_identity": _JOB})

    assert answered.status_code == 201, answered.text
    assert answered.json()["reassessed"]["resume"]["profile_id"] == fx.default_profile_id  # the selected role
    after = _one_assessment(fx)
    assert (after[0], after[2]) == (fx.default_profile_id, MATCHED) and after[1] > before[1]
    assert len(fx.base.model.assess_prompts) == 1


def test_one_assessor_needs_no_role_as_before(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    made = assess(fx, fx.default_profile_id, asking(fx))
    ids = {row.requirement: row.id for row in made.result.matrix}
    second_profile(fx)  # a second role that never assessed the job
    fx.base.model.assessed = settled(fx, ids)

    answered = _post(server, {"job_identity": _JOB})

    assert answered.status_code == 201, answered.text
    assert answered.json()["reassessed"]["resume"]["profile_id"] == fx.default_profile_id
    assert (_one_assessment(fx)[0], _one_assessment(fx)[2]) == (fx.default_profile_id, MATCHED)


def test_a_deleted_roles_name_on_the_stored_assessment_changes_nothing(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    mine, other, _ids = held_by_two(fx, page_profile="default")
    assert _one_assessment(fx)[0] == other
    profile_records.write_profile(fx.base.gig.resolved, profile_id=other, state="deleted", uuid_factory=uuids(43))

    answered = _post(server, {"job_identity": _JOB})

    assert answered.status_code == 201, answered.text
    assert answered.json()["reassessed"]["resume"]["profile_id"] == mine
    assert (_one_assessment(fx)[0], _one_assessment(fx)[2]) == (mine, MATCHED)


def test_a_role_that_never_assessed_the_job_replaces_the_same_assessment_and_its_origin_stays(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    made = assess(fx, fx.default_profile_id, asking(fx))
    ids = {row.requirement: row.id for row in made.result.matrix}
    second = second_profile(fx)
    fx.base.model.assessed = settled(fx, ids)
    before = _one_assessment(fx)

    answered = _post(server, {"job_identity": _JOB, "profile_id": second})

    assert answered.status_code == 201, answered.text
    reply = answered.json()["reassessed"]
    # Until 0.1.11.9 this was the role's FIRST assessment (a second file, `origin: job_page`). The job was assessed
    # before: its one assessment is replaced, and where it was started from is not changed by an answer.
    assert (reply["resume"]["profile_id"], reply["origin"], reply["created_at"]) == (second, made.origin, made.created_at)
    after = _one_assessment(fx)
    assert (after[0], after[2]) == (second, MATCHED) and after[1] > before[1]


@pytest.mark.parametrize(
    ("reassess", "status", "code"),
    [
        ({"job_identity": _JOB, "profile_id": "profile_00000000-0000-4000-8000-00000000dead"}, 404, "profile_not_found"),
        ({"job_identity": _JOB, "profile_id": 7}, 422, "invalid_value"),
        ({"job_identity": _JOB, "profile": "x"}, 422, "invalid_value"),
        ({"profile_id": "x"}, 422, "invalid_value"),
    ],
)
def test_a_profile_that_is_none_and_a_wrong_shape_are_refused_before_the_answer_is_saved(fx: PostingsFixture, server: _Server, reassess: dict, status: int, code: str) -> None:  # noqa: F811
    held_by_two(fx, page_profile="default")
    before, answers = _stored(fx), _answers(server)

    refused = _post(server, reassess)

    assert (refused.status_code, refused.json()["error"]["code"]) == (status, code), refused.text
    assert _stored(fx) == before and _answers(server) == answers and fx.base.model.assess_prompts == []


def test_the_lookup_reads_the_jobs_files_and_never_lists_the_store(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from gigai.scout import quick_assess
    from gigai.scout.quick_assess import reassess_target

    mine, other, _ids = held_by_two(fx, page_profile="default")

    def listed(*args: object, **kwargs: object) -> None:
        raise AssertionError("the whole store was listed for one job")

    monkeypatch.setattr(quick_assess, "list_quick_assessments", listed)

    plan = reassess_target(fx.home_root, fx.target, _JOB, profile_id=mine)
    assert (plan.profile_id, plan.own, plan.previous.resume.profile_id) == (mine, True, other)
    unnamed = reassess_target(fx.home_root, fx.target, _JOB)  # 0.1.11.6: refused, `reassess_profile_required`
    assert (unnamed.profile_id, unnamed.own, unnamed.previous.stored_path) == (None, True, plan.previous.stored_path)
    with pytest.raises(QuickAssessError) as refused:
        reassess_target(fx.home_root, fx.target, _JOB, profile_id="profile_00000000-0000-4000-8000-00000000dead")
    assert refused.value.code == "profile_not_found"
    nothing = reassess_target(fx.home_root, fx.target, "https://jobs.example.invalid/none")
    assert (nothing.previous, nothing.profile_id, nothing.own) == (None, None, False)


def _cli(fixture: PostingsFixture, *args: str):
    return CliRunner().invoke(
        scout_group,
        ["answer", QUESTION["question_id"], "--answer-text", TYPED, *args, "--home", str(fixture.home_root), "--target", str(fixture.target), "--json"],
    )


def _last(result) -> dict:
    return json.loads(result.output.strip().splitlines()[-1])


def test_the_cli_re_assesses_the_jobs_one_assessment(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    mine, other, ids = held_by_two(fx, page_profile="default")
    answers = _answers(server)

    alone = _cli(fx, "--profile", mine)
    assert alone.exit_code == 1 and "--profile goes with --reassess" in alone.output, alone.output
    assert _answers(server) == answers

    # No role named, on a job two roles assessed: 0.1.11.6 refused it; the job's one assessment is replaced.
    unnamed = _cli(fx, "--reassess", _URL)
    assert unnamed.exit_code == 0, unnamed.output
    assert _last(unnamed)["reassessed"]["resume"]["profile_id"] == fx.default_profile_id
    assert (_one_assessment(fx)[0], _one_assessment(fx)[2]) == (fx.default_profile_id, MATCHED)

    second = other if other != fx.default_profile_id else mine
    fx.base.model.assessed = settled(fx, ids)
    done = _cli(fx, "--reassess", _URL, "--profile", second)
    assert done.exit_code == 0, done.output
    assert _last(done)["reassessed"]["resume"]["profile_id"] == second
    assert (_one_assessment(fx)[0], _one_assessment(fx)[2]) == (second, MATCHED) and len(fx.base.model.assess_prompts) == 2
