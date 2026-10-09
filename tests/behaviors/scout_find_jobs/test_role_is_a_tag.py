"""0.1.11.9 PJ3: a role (a profile) is a TAG. The Jobs list, the job read and the agent's commands work from the JOB.

HISTORY. Until 0.1.11.9 a job two roles found was two things: each role had its own assessment and resume of it, a
list filtered to one role showed that role's own row, and a command that named no role guessed one (the newest
assessment's) or refused (``profile_ambiguous``). 0.1.11.9 PJ2 keyed the stores by job; this packet makes the read
model and the agent contract say so.

Pinned, on synthetic homes (an invented master and posting, a scripted model, no network, never ``~/.gigai``):

- (a) ``GET /api/postings`` lists a job two roles found ONCE, with ``tags`` naming both (id, name, each role's own
  rank score). A role filter keeps the jobs that role found and never changes the row. Assessed from role B's page,
  role A's page reads the SAME assessment, and its own Assess has nothing to do: one model call in all.
- (b) the agent: ``resume brief``, ``resume pick``, ``suggestions list`` and ``GET /api/jobs?url=`` with no role answer
  from the job. ``--profile`` / ``profile_id`` is accepted and changes nothing. No command of the brief names a role,
  no link carries a ``profile_id``, and the job read's ``job_state`` and ``open_questions`` are the job's own
  assessment's, whatever a newer pasted-resume assessment of the same posting says.
- the weak-fit state takes the BEST tag's rank: one role's low score does not hide a job another role ranks well.
- the applied job whose sent PDF cannot be told: the job read says so, from the stores migration's record.
- the pipeline keeps a job's steps under ONE role: "Process now" from a second role's page queues no second set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import job_brief, posting_search, profile_records
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.discovery.storage import project_id
from gigai.scout.find_jobs.job_state import JobStateSources
from gigai.scout.job_store_layout import job_digest
from gigai.scout.job_store_migration import RECORD_SCHEMA, record_path
from gigai.scout.pipeline import triggers
from gigai.scout.pipeline.runner import PipelineRunner
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.pipeline.store import PipelineStore
from gigai.scout.quick_assess import list_quick_assessments, run_quick_assessment

from tests.behaviors.scout_find_jobs.test_pick_header_room import _JOB, _URL, _Server, _answer, _cli, _ok, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.support.answers_stories_fixtures import config as fixture_config, pending, two_profiles
from tests.support.fit_fixtures import assess_one, one_of_ten, seed_rank
from tests.support.pipeline_fixtures import assessment, build_pipeline_fixture, resolved_job
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job
from tests.support.scout_profile_fixtures import uuids

SECOND_ROLE = "Fullstack track"


def two_roles(fixture: PostingsFixture) -> tuple[str, str]:
    """The fixture's role and a SECOND active one whose saved search finds the same posting (the same titles). Their ids."""

    resolved = fixture.base.gig.resolved
    default = next(item for item in profile_records.list_profiles(resolved) if item.profile_id == fixture.default_profile_id)
    made = profile_records.create_profile(
        resolved, label=SECOND_ROLE, titles=default.titles, titles_to_avoid=(), queries=default.queries,
        resume_ref=default.resume_ref, uuid_factory=uuids(40),
    )
    return default.profile_id, made.profile_id


def _origin(api: _Server) -> dict[str, str]:
    return {"Origin": str(api.client.base_url).rstrip("/")}


def _listed(api: _Server, **params: str) -> list[dict]:
    answer = api.client.get("/api/postings", params=params)
    assert answer.status_code == 200, answer.text
    return [row for row in answer.json()["postings"]["rows"] if row["job_identity"] == _JOB]


def _stored_for(api: _Server, profile_id: str) -> dict:
    """The job's assessment as the job page of the role ``profile_id`` reads it (``GET /api/assessments``)."""

    items = api.client.get("/api/assessments", params={"profile_id": profile_id}).json()["items"]
    (item,) = [item for item in items if item["job"]["job_identity"] == _JOB]
    return item


# --- (a) one row, two tags, one assessment ---------------------------------------------------------------------------------


def test_a_job_two_roles_found_is_listed_once_with_both_tags_and_is_assessed_once(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    first, second = two_roles(fx)
    names = {item["profile_id"]: item["label"] for item in server.client.get("/api/postings").json()["profiles"]}
    assert set(names) == {first, second} and names[second] == SECOND_ROLE

    # Listed ONCE, tagged with both roles (id and name), whatever role the list is filtered to.
    for params in ({}, {"profile_id": first}, {"profile_id": second}):
        (row,) = _listed(server, **params)
        assert sorted(tag["profile_id"] for tag in row["tags"]) == sorted([first, second]), params
        assert {tag["profile_id"]: tag["label"] for tag in row["tags"]} == names
        assert row["state"] == "not_assessed" and row["assessment"] is None

    # Assessed from role B's job page (the page names its role; it is recorded on the assessment, it selects nothing).
    fx.base.model.assessed = _answer(fx)
    fx.base.model.assess_prompts.clear()
    made = server.client.post("/api/assess", json={"job": {"job_url": _URL}, "resume": {"profile_id": second}, "origin": "job_page"}, headers=_origin(server))
    assert made.status_code in (200, 201), made.text
    assert len(fx.base.model.assess_prompts) == 1

    # Role A's job page opens on the SAME assessment: the same stored item, byte for byte.
    assert _stored_for(server, first) == _stored_for(server, second)
    assert _stored_for(server, first)["result"]["verdict"] == "matched_above_threshold"
    assert [item.resume.profile_id for item in list_quick_assessments(fx.home_root, fx.target) if item.job.job_identity == _JOB] == [second]

    # And its own "Assess" has nothing to do: asked for role A, and approved, no second model call is made.
    for body in ({"jobs": [_JOB], "profile_id": first}, {"jobs": [_JOB], "profile_id": first, "approve": True}, {"jobs": [_JOB]}):
        answer = server.client.post("/api/postings/assess", json=body, headers=_origin(server))
        assert answer.status_code == 200, answer.text
        assert (answer.json()["status"], answer.json()["counts"]["already_current"], answer.json()["assessed"]) == ("nothing_to_assess", 1, None), body
    assert len(fx.base.model.assess_prompts) == 1, "the job was assessed a second time for its other role"

    # The list: still one row, the job's one verdict under either filter.
    seen = [_listed(server, **params) for params in ({}, {"profile_id": first}, {"profile_id": second})]
    assert [len(rows) for rows in seen] == [1, 1, 1]
    assert {(rows[0]["state"], rows[0]["assessment"]["assessed_at"]) for rows in seen} == {("matched", seen[0][0]["assessment"]["assessed_at"])}


# --- (b) the agent contract: no role is guessed, none is needed ------------------------------------------------------------


def _profile_ids(value: object) -> list[str]:
    """Every ``profile_id`` named anywhere in ``value`` (a link's body, or its path's query)."""

    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "profile_id" and isinstance(item, str):
                found.append(item)
            found.extend(_profile_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_profile_ids(item))
    elif isinstance(value, str) and "profile_id=" in value:
        found.append(value)
    return found


def assessed_with_a_newer_pasted_assessment(fx: PostingsFixture) -> tuple[str, str]:  # noqa: F811
    """The job assessed (Matched, its resume picked) with the SECOND role recorded, then the same posting assessed
    against a PASTED resume, which asks a question. Returns the two roles' ids."""

    first, second = two_roles(fx)
    fx.base.model.assessed = _answer(fx)
    made = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=second)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    picked = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert picked["resume"] is not None
    # A NEWER assessment of the same posting against a pasted resume, which asks a question: it is not the job's.
    ids = {row.requirement: row.id for row in made.result.matrix}
    asking = json.loads(_answer(fx))
    for row in asking["matrix"]:
        row["id"] = ids[row["requirement"]]
    open_row = asking["matrix"][-1]
    open_row.update({"class": "askable", "status": "unclear", "resume_evidence": [], "sources": []})
    question = {"question_id": "tooling:react", "question": "Have you shipped React in production?", "requirement": open_row["requirement"]}
    fx.base.model.assessed = json.dumps({**asking, "verdict": "pending_user_answers", "questions": [question], "pick": None})
    pasted = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(resume_text="## Experience\n\n- Wrote Python services for six years.\n")),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    assert pasted.resume.profile_id is None and pasted.result.verdict.value == "pending_user_answers"

    return first, second


NAMED = (None, "first", "second", "profile_00000000-0000-4000-8000-00000000dead")


def test_brief_pick_and_suggestions_answer_from_the_job_with_no_role_and_name_none(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    first, second = assessed_with_a_newer_pasted_assessment(fx)
    named = tuple({"first": first, "second": second}.get(role, role) if role else None for role in NAMED)
    # The CLI: with no role, with either role, and with an id that is no role at all: the same job, the same answer.
    for command in (("resume", "brief"), ("resume", "brief", "--posting"), ("resume", "pick"), ("suggestions", "list")):
        answers = [_ok(fx, *command, "--job-url", _URL, *(["--profile", role] if role else [])) for role in named]
        assert all(answer == answers[0] for answer in answers), command
        assert answers[0]["job_identity"] == _JOB and answers[0]["profile_id"] == second  # the role RECORDED on the assessment
    brief = _ok(fx, "resume", "brief", "--job-url", _URL)
    assert all("--profile" not in command for command in brief["commands"].values()), brief["commands"]
    assert brief["commands"]["yours"] == f"gigai scout resume brief --job-url {_JOB}"
    for args in (("resume", "brief"), ("resume", "pick"), ("suggestions", "list")):
        assert "Ignored since 0.1.11.9" in _cli(fx, *args, "--help", as_json=False).output

    # The same through the API, as an agent that follows the job read's links does.
    for path in ("/api/jobs/brief", "/api/jobs/suggestions"):
        got = [server.client.get(path, params={"url": _URL, **({"profile_id": role} if role else {})}) for role in named]
        assert [answer.status_code for answer in got] == [200] * len(named), [answer.text for answer in got]
        assert all(answer.json() == got[0].json() for answer in got), path

    # `stored_job`: the job's record for any name; `ephemeral` alone still means the pasted resume's.
    assert {job_brief.stored_job(fx.home_root, fx.target, _URL, role).profile_id for role in named} == {second}
    assert job_brief.stored_job(fx.home_root, fx.target, _URL, "ephemeral").assessment.resume.profile_id is None


def test_the_job_read_is_the_jobs_own_whatever_a_newer_pasted_assessment_says_and_its_links_name_no_role(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    first, second = assessed_with_a_newer_pasted_assessment(fx)
    assert server.client.get("/api/postings").status_code == 200  # the Jobs list was read: the read model holds both roles' rows
    job = server.client.get("/api/jobs", params={"url": _URL}).json()
    # The job's: its one assessment gives the state and the open questions, whatever the newer pasted one asks.
    assert [(item["source"], item["profile_id"]) for item in job["assessments"]] == [("quick", "ephemeral"), ("quick", second)]
    assert job["open_questions"] == [], "the pasted resume's question was served as the job's"
    assert job["job_state"]["state"] == "tailored"
    assert sorted(tag["profile_id"] for tag in job["tags"]) == sorted([first, second])
    assert next(tag["label"] for tag in job["tags"] if tag["profile_id"] == second) == SECOND_ROLE
    assert job["index_posting"]["tags"] == job["tags"]
    assert job["ambiguous_applied_resume"] is None
    # No link names a role; every one of them is answered for the job.
    assert _profile_ids(job["links"]) == [] and _profile_ids([item["links"] for item in job["tailored_resumes"]]) == []
    for name in ("brief", "brief_posting", "suggestions"):
        assert server.client.get(job["links"][name]["path"]).status_code == 200, name
    pick = server.client.post(job["links"]["pick"]["path"], json=job["links"]["pick"]["body"], headers=_origin(server))
    assert pick.status_code == 200 and pick.json()["profile_id"] == second, pick.text
    resume = server.client.get(job["tailored_resumes"][0]["links"]["resume"]["path"])
    assert resume.status_code == 200 and [item["job"]["job_identity"] for item in resume.json()["items"]] == [_JOB]


# --- the weak-fit state takes the best tag's rank ----------------------------------------------------------------------------


def test_the_weak_fit_state_is_judged_at_the_best_rank_among_the_jobs_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    two = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    two.seed("aero", [lever_job("aero", n) for n in (1, 2)], seen_at=days_ago(1))
    kept, weak = job_url("aero", 1), job_url("aero", 2)
    assess_one(two, kept, one_of_ten())  # needs answers, fit 14%
    assess_one(two, weak, one_of_ten())
    seed_rank(two, monkeypatch, {kept: 30, weak: 30})  # the default role ranks both low
    seed_rank(two, monkeypatch, {kept: 80, weak: 20}, two.second_profile_id)  # the other role ranks ONE of them well

    def search(**kwargs: object) -> dict:
        return posting_search.search_postings(two.home_root, two.target, now=NOW, **kwargs)  # type: ignore[arg-type]

    # Whichever role the list is filtered to: the job one role ranks well is listed, as needing answers; the job no
    # role ranks well is the weak fit (left out, counted). Until 0.1.11.9 the default role's filter hid both.
    for role in (None, two.default_profile_id, two.second_profile_id):
        found = search(profile_ids=[role] if role else None)
        assert [(row["job_identity"], row["state"], row["rank_score"]) for row in found["postings"]["rows"]] == [(kept, "needs_answers", 80)], role  # type: ignore[index]
        assert found["counts"]["weak_fit"] == 1  # type: ignore[index]
        (hidden,) = search(profile_ids=[role] if role else None, states=["weak_fit"])["postings"]["rows"]  # type: ignore[index,misc]
        assert (hidden["job_identity"], hidden["state"], hidden["rank_score"]) == (weak, "weak_fit", 30)
    # Each tag keeps its own role's rank score, best first.
    (row,) = search()["postings"]["rows"]  # type: ignore[index,misc]
    assert [(tag["profile_id"], tag["rank_score"]) for tag in row["tags"]] == [(two.second_profile_id, 80), (two.default_profile_id, 30)]

    # The job read's own state (`JobStateSources`): the same, whichever role is named.
    sources = JobStateSources(home_root=two.home_root, target=two.target, resolved=two.base.gig.resolved, events={})
    for role in (two.default_profile_id, two.second_profile_id):
        assert (sources.state_for(kept, profile_id=role).state, sources.state_for(weak, profile_id=role).state) == ("needs_answers", "weak_fit")


# --- the applied job whose sent PDF cannot be told ---------------------------------------------------------------------------


def test_the_job_read_says_when_the_sent_pdf_cannot_be_told(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    fx.base.model.assessed = _answer(fx)
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    assert server.client.get("/api/jobs", params={"url": _URL}).json()["ambiguous_applied_resume"] is None  # no record: nothing is said

    # What `gigai scout migrate-job-stores --apply` records for such a job (synthetic: two invented resume paths).
    entry = {
        "kept_profile_id": "profile_a", "rule": "application", "profiles": ["profile_a", "profile_b"],
        "ambiguous_applied_resume": {"resumes": ["scout/x/resumes/profile_a/1.json", "scout/x/resumes/profile_b/1.json"], "note": "kept"},
    }
    project = project_id(fx.home_root, fx.target)
    record = {"schema_version": RECORD_SCHEMA, "rule_order": [], "projects": {project: {"jobs": {job_digest(_JOB): entry}, "ambiguous_applied_resume": [job_digest(_JOB)]}}}
    path = record_path(fx.home_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    said = server.client.get("/api/jobs", params={"url": _URL}).json()["ambiguous_applied_resume"]
    assert said["resumes"] == 2 and "cannot tell which PDF you sent" in said["text"] and "all of them are kept" in said["text"]
    assert "profile" not in said["text"].lower() and "scout/x" not in json.dumps(said)  # the word is "role"; never a path
    # Another project's record, or one with a single resume, says nothing.
    record["projects"] = {"another-project": record["projects"][project]}
    path.write_text(json.dumps(record), encoding="utf-8")
    assert server.client.get("/api/jobs", params={"url": _URL}).json()["ambiguous_applied_resume"] is None


# --- the pipeline keeps a job's steps under ONE role -------------------------------------------------------------------------


def test_a_job_asked_for_from_two_roles_pages_is_processed_once_under_one_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The steps stay keyed (role, job). Until 0.1.11.9 "Process now" on a second role's job page queued a second set:
    one more tailoring check, re-assessment, ATS score and label for the same job and the same one resume."""

    monkeypatch.setenv(PIPELINE_ENV, "on")
    base = build_pipeline_fixture(tmp_path, monkeypatch, base=False)
    default, second = two_profiles(base.gig)
    job = "https://jobs.example.test/acme/role-01"
    base.model.assessed = pending("tooling:terraform", "Have you used Terraform in production?")
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=job), resume=AssessResumeInput(profile_id=default)),
        home_root=base.home_root, target=base.target, config=fixture_config(base.home_root), resolved_job=resolved_job(job),
    )
    base.model.assessed = assessment(met=2)
    api = _Server(PostingsFixture(base, second_profile_id=second, deleted_profile_id=None), monkeypatch)
    try:
        def process(role: str) -> dict:
            answer = api.client.post("/api/pipeline/process", json={"job_identity": job, "profile_id": role}, headers=_origin(api))
            assert answer.status_code == 202, answer.text
            return answer.json()

        def steps_of(role: str) -> list[dict]:
            answer = api.client.get("/api/pipeline/job", params={"job_identity": job, "profile_id": role})
            assert answer.status_code == 200, answer.text
            return answer.json()["steps"]

        first = process(default)
        assert (first["result"], first["profile_id"]) == ("enqueued", default)
        # Asked again from the OTHER role's page: the job is processed where its steps are, never a second set.
        again = process(second)
        assert again["profile_id"] == default and again["result"] != "enqueued", again
        store = PipelineStore(base.db)
        try:
            assert {step.profile_id for step in store.steps(job=job)} == {default}
            assert triggers.job_role(store, second, job) == default and triggers.job_role(store, default, job) == default
            assert triggers.job_role(store, second, "https://jobs.example.test/acme/role-02") == second  # no step yet: the role that asks
        finally:
            store.close()
        # The other role's job page reads the same steps as the first one's.
        PipelineRunner(home_root=base.home_root, target=base.target, config=fixture_config(base.home_root), busy=lambda: None).drain()
        seen = steps_of(second)
        assert seen == steps_of(default) and [step["state"] for step in seen] == ["done"] * len(seen) and seen
    finally:
        api.close()
