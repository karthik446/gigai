"""0.1.11.6 AN1: a re-assessment by job identity is made for the profile the caller names, never for one the server picks.

THE BUG (released 0.1.11.5): a job held by TWO profiles. `POST /api/answers` with `reassess: {job_identity}` (and
`gigai scout answer --reassess`) carried no profile; the server took the stored assessment it found first by that
identity, which is the NEWEST across every profile, and re-assessed the job for that one. An answer given on one
profile's job page spent a model call on another profile, and the page's own assessment stayed as it was.

Pinned, on a synthetic home (the invented master and posting of `test_pick_header_room.py`, a scripted model, a second
profile on the same resume), with the OTHER profile's assessment always the newer one:

- `reassess.profile_id` (the job page sends its own): the new assessment is that profile's and is stored under it; the
  other profile's file is untouched; one model call;
- a named profile with no assessment of its own is assessed from the address the other holder's names, and that first
  item is the job page's;
- without `profile_id`: one holder -> that profile, as before; TWO holders -> `409 reassess_profile_required`, a plain
  sentence that names the field, and NOTHING is written (no answer, no model call); a deleted profile's old assessment
  does not make a job ambiguous;
- an id that is no profile -> `404 profile_not_found`, nothing written; a `reassess` of another shape -> 422;
- the CLI: `gigai scout answer ... --reassess JOB --profile ID` is the same; without `--profile` on a job two profiles
  hold it is refused before the answer is saved; `--profile` without `--reassess` is refused.
"""

from __future__ import annotations

import json

from click.testing import CliRunner
import pytest

from gigai.scout import profile_records
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.quick_assess import QuickAssessError, list_quick_assessments, run_quick_assessment
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
    """The job assessed on both profiles, each asking; the OTHER profile's is the newer one.

    Returns (the page's profile, the other, the requirement ids of the job). The model's NEXT answer is the settled one.
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
    """The job assessed Matched on both profiles, each with its picked resume; the OTHER profile's is the newer one.

    Returns (the page's profile, the other). The model's next answer is the same Matched one.
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


def _stored(fixture: PostingsFixture) -> dict[str | None, tuple[str, str]]:
    """profile -> (when its assessment of the job was made, its verdict)."""

    return {
        item.resume.profile_id: (item.updated_at, item.result.verdict.value)
        for item in list_quick_assessments(fixture.home_root, fixture.target) if item.job.job_identity == _JOB
    }


def _answers(api: _Server) -> list[str]:
    return [item["question_id"] for item in api.client.get("/api/answers").json()["answers"]]


def _post(api: _Server, reassess: object):
    return api.client.post("/api/answers", json={"question_id": QUESTION["question_id"], "question": QUESTION["question"], "answer": TYPED, "reassess": reassess})


@pytest.mark.parametrize("page_profile", ["default", "second"])
def test_the_named_profile_is_the_one_re_assessed(fx: PostingsFixture, server: _Server, page_profile: str) -> None:  # noqa: F811
    mine, other, _ids = held_by_two(fx, page_profile=page_profile)
    before = _stored(fx)
    assert before[other][0] > before[mine][0], "the other profile's assessment is the newer one"

    answered = _post(server, {"job_identity": _JOB, "profile_id": mine})

    assert answered.status_code == 201, answered.text
    made = answered.json()["reassessed"]
    assert (made["resume"]["profile_id"], made["result"]["verdict"]) == (mine, MATCHED)
    after = _stored(fx)
    assert after[other] == before[other], "the other profile's assessment was made again"
    assert after[mine][1] == MATCHED and after[mine][0] > before[other][0]
    assert len(fx.base.model.assess_prompts) == 1, "one model call"
    assert QUESTION["question_id"] in _answers(server)


def test_a_job_two_profiles_hold_is_refused_without_a_profile_and_nothing_is_written(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    mine, other, _ids = held_by_two(fx, page_profile="default")
    before, answers = _stored(fx), _answers(server)

    refused = _post(server, {"job_identity": _JOB})

    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "reassess_profile_required"
    assert "2 profiles" in error["message"] and "reassess.profile_id" in error["message"] and mine in error["message"] and other in error["message"]
    assert _stored(fx) == before and _answers(server) == answers, "a refused request wrote something"
    assert fx.base.model.assess_prompts == [], "a refused request called the model"


def test_one_holder_needs_no_profile_as_before(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    made = assess(fx, fx.default_profile_id, asking(fx))
    ids = {row.requirement: row.id for row in made.result.matrix}
    second_profile(fx)  # a second profile that never assessed the job
    fx.base.model.assessed = settled(fx, ids)

    answered = _post(server, {"job_identity": _JOB})

    assert answered.status_code == 201, answered.text
    assert answered.json()["reassessed"]["resume"]["profile_id"] == fx.default_profile_id
    assert {profile: verdict for profile, (_at, verdict) in _stored(fx).items()} == {fx.default_profile_id: MATCHED}


def test_a_deleted_profiles_old_assessment_does_not_make_the_job_ambiguous(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    mine, other, _ids = held_by_two(fx, page_profile="default")
    profile_records.write_profile(fx.base.gig.resolved, profile_id=other, state="deleted", uuid_factory=uuids(43))
    before = _stored(fx)

    answered = _post(server, {"job_identity": _JOB})

    assert answered.status_code == 201, answered.text
    assert answered.json()["reassessed"]["resume"]["profile_id"] == mine
    assert _stored(fx)[other] == before[other]


def test_a_named_profile_with_no_assessment_of_its_own_gets_its_first_one(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    made = assess(fx, fx.default_profile_id, asking(fx))
    ids = {row.requirement: row.id for row in made.result.matrix}
    second = second_profile(fx)
    fx.base.model.assessed = settled(fx, ids)
    before = _stored(fx)

    answered = _post(server, {"job_identity": _JOB, "profile_id": second})

    assert answered.status_code == 201, answered.text
    reply = answered.json()["reassessed"]
    assert (reply["resume"]["profile_id"], reply["origin"]) == (second, "job_page")
    after = _stored(fx)
    assert after[fx.default_profile_id] == before[fx.default_profile_id] and after[second][1] == MATCHED


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


def test_the_lookup_reads_the_store_once(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from gigai.scout import quick_assess
    from gigai.scout.quick_assess import reassess_target

    mine, _other, _ids = held_by_two(fx, page_profile="default")
    reads: list[object] = []
    real = quick_assess.list_quick_assessments
    monkeypatch.setattr(quick_assess, "list_quick_assessments", lambda *args, **kwargs: reads.append(args) or real(*args, **kwargs))

    plan = reassess_target(fx.home_root, fx.target, _JOB, profile_id=mine)

    assert (plan.profile_id, plan.own, plan.previous.resume.profile_id) == (mine, True, mine) and len(reads) == 1
    with pytest.raises(QuickAssessError) as refused:
        reassess_target(fx.home_root, fx.target, _JOB)
    assert refused.value.code == "reassess_profile_required"
    nothing = reassess_target(fx.home_root, fx.target, "https://jobs.example.invalid/none")
    assert (nothing.previous, nothing.profile_id, nothing.own) == (None, None, False)


def _cli(fixture: PostingsFixture, *args: str):
    return CliRunner().invoke(
        scout_group,
        ["answer", QUESTION["question_id"], "--answer-text", TYPED, *args, "--home", str(fixture.home_root), "--target", str(fixture.target), "--json"],
    )


def _last(result) -> dict:
    return json.loads(result.output.strip().splitlines()[-1])


def test_the_cli_re_assesses_for_the_profile_it_is_given(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    mine, other, _ids = held_by_two(fx, page_profile="default")
    before, answers = _stored(fx), _answers(server)

    refused = _cli(fx, "--reassess", _URL)
    assert refused.exit_code == 1 and _last(refused)["error"]["code"] == "reassess_profile_required", refused.output
    assert "--profile" in _last(refused)["error"]["message"]
    assert _stored(fx) == before and _answers(server) == answers and fx.base.model.assess_prompts == []

    alone = _cli(fx, "--profile", mine)
    assert alone.exit_code == 1 and "--profile goes with --reassess" in alone.output, alone.output
    assert _answers(server) == answers

    done = _cli(fx, "--reassess", _URL, "--profile", mine)
    assert done.exit_code == 0, done.output
    assert _last(done)["reassessed"]["resume"]["profile_id"] == mine
    after = _stored(fx)
    assert after[other] == before[other] and after[mine][1] == MATCHED and len(fx.base.model.assess_prompts) == 1
