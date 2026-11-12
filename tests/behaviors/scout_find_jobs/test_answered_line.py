"""0.3.2 gyl: a must-have the user said yes to is never asked again, suggests (no hold) and gets a drafted master line.

THE SYMPTOM (Function Health): a must-have the user had answered was asked again on every re-assessment; the answer
was on file, the model asked anyway, and the gate held the resume on a row only an answer backed ("answered"). The
operator's policy (2026-11-12): an answered question is never asked again; an ``answered`` must-have SUGGESTS (the
resume is made); the job gets a ``master_line`` suggestion whose line is drafted in code from the answer; one call
(``POST /api/jobs/suggestions {action: "add_line"}`` / ``gigai scout suggestions add-line``) adds the line to the
master backed by the answer, reads the row again in code, re-picks and records ``master_line:<line id>``. No model call.

Pinned here, on the synthetic home of ``test_assessment_v9_flow`` (an invented master and posting, a scripted model):

(a) ``assessment_core.settle_answered_rows`` and the widened T6 of ``assess_strict.strict_rows``;
(b) answer once (``gigai scout answer --reassess``), re-assess twice with a model that asks the answered question
    again: the question is dropped, the row reads ``answered``, the gate suggests and the model's pick is kept;
(c) one ``master_line`` suggestion for the answered must-have, its ``draft`` derived on the read (the stored record
    holds ids only); ``drafted_line`` unit cases (a bare "yes" too);
(d) ``add_line`` through the API and through the CLI: the line is backed ``answer:<id>``, the row is ``met``, the
    resume is picked again with the line, the history says ``master_line:<id>``, the suggestion is resolved, and the
    model is never called; the refusals; a run stopped half way is finished by the same call;
(e) an open question, or a must-have never asked about, still holds.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from gigai.scout import assessment_core, drafted_line, job_actions, requirements_list
from gigai.scout.assess_master import strict_master
from gigai.scout.assess_strict import ANSWERED, strict_rows as REAL_STRICT_ROWS  # the real one: `fx` replaces it with a no-op
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.quick_assess import read_quick_assessment
from gigai.scout.resume_gate import HOLD_QUESTION, SUGGEST, gate
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import read_tailored_resume

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import (  # noqa: F401 - `fx` is the fixture
    _JOB, _URL, HELM_QUESTION, KUBERNETES_LINE, OWN_LINE, PYTHON_LINE, REQUIREMENTS, TERRAFORM_LINE, _assess, _edit, _ids, _master,
    _patch_v9_template, _record, _resume_path, fx,
)
from tests.behaviors.scout_find_jobs.test_pick_header_room import _Server
from tests.support.posting_fixtures import PostingsFixture

SAID = "Yes, I ran Kubernetes clusters on EKS for three years at Acme."
DRAFT = "Ran Kubernetes clusters on EKS for three years at Acme."
ASKED = "The posting asks for: Kubernetes. Do you have this? Say where."
#: The fixture's master, edited so no line of it states Kubernetes (else code settles the row as met by the master).
PLAIN_LINE = "Operated PostgreSQL clusters for the payments team."
MODULES_LINE = "Wrote the Terraform modules every cluster is built from."
SKILLS_LINE = "Platform: Python, PostgreSQL, Terraform"


@pytest.fixture(autouse=True)
def _pipeline_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PIPELINE_ENV, "off")


def _cli(fx: PostingsFixture, *args: str, ok: bool = True) -> dict:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert (result.exit_code == 0) is ok, result.output
    return json.loads(result.output)


def _answer(fx: PostingsFixture, *, said: str, questions: list[dict], row_ids: tuple[str, ...] | None = None) -> str:
    """A v9 answer on the edited master: Python and Terraform met on a line, Kubernetes (askable) ``unclear``, Helm one of a list."""

    ids = _ids(fx)
    rows = [
        {"requirement": REQUIREMENTS[0], "class": "hard", "status": "met", "resume_evidence": ["Built Python services for six years"], "sources": [ids[PYTHON_LINE]]},
        {"requirement": REQUIREMENTS[1], "class": "askable", "status": "unclear", "resume_evidence": []},
        {"requirement": REQUIREMENTS[2], "class": "askable", "status": "met", "resume_evidence": ["Terraform modules"], "sources": [ids[MODULES_LINE]]},
        {"requirement": REQUIREMENTS[3], "class": "list_item", "status": "unclear", "resume_evidence": []},
    ]
    for index, row in enumerate(rows):
        row["class_basis"] = f"Requirements: {row['requirement']}"
        if row_ids is not None:
            row["id"] = row_ids[index]
    suggested = [{"kind": "reword", "line": ids[OWN_LINE], "posting_phrase": "own Python inference services", "why": "Lead with what the posting calls inference services."}]
    chosen = {"summary": None, "section_order": ["experience", "projects"], "lines": list(_model_pick(fx))}
    return json.dumps({"verdict": said, "matrix": rows, "questions": questions, "suggestions": suggested, "pick": chosen, "not_a_match_reason": None})


def _model_pick(fx: PostingsFixture) -> tuple[str, ...]:
    ids = _ids(fx)
    return (ids[OWN_LINE], ids[MODULES_LINE], ids[PYTHON_LINE], ids[PLAIN_LINE])


def _asking_again(fx: PostingsFixture, question_id: str, row_ids: tuple[str, ...]) -> str:
    """The model's answer when it asks the answered question again: Kubernetes ``unclear`` with its own question beside Helm's."""

    return _answer(fx, said="pending_user_answers", questions=[HELM_QUESTION, {"question_id": question_id, "question": ASKED, "requirement": "Kubernetes"}], row_ids=row_ids)


def _no_model(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(prompt: str):  # noqa: ANN202 - never returns
        raise AssertionError(f"a model was called: {prompt[:80]}")

    monkeypatch.setattr(fx.base.model, "answer", refuse)


def _answered_job(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, tuple[str, ...]]:
    """Assess (Kubernetes unclear: held, its own question asked), answer it and re-assess: ``(row id, question id, listed row ids)``.

    The prompt shows line ids (the v9 template), so the model's pick is kept on a Matched answer.
    """

    _patch_v9_template(monkeypatch)
    ids = _ids(fx)
    for old, new in ((KUBERNETES_LINE, PLAIN_LINE), (TERRAFORM_LINE, MODULES_LINE), ("Platform: Python, Kubernetes, PostgreSQL, Terraform", SKILLS_LINE)):
        _edit(fx, ids[old], new)
    first, _prompt = _assess(fx, _answer(fx, said="matched_above_threshold", questions=[HELM_QUESTION]))
    row_id = first.result.matrix[1].id
    question_id = assessment_core.row_question_id(row_id)
    assert first.resume_gate.decision == HOLD_QUESTION and question_id in [item.question_id for item in first.result.structured_questions]
    listed = requirements_list.read_list(fx.home_root, fx.target, first.posting_sha256).ids()
    fx.base.model.assessed = _asking_again(fx, question_id, listed)
    answered = _cli(fx, "answer", question_id, "--answer-text", SAID, "--reassess", _URL)
    assert answered["reassessed"]["job"]
    return row_id, question_id, listed


def _master_line(fx: PostingsFixture, row_id: str):  # noqa: ANN202 - the stored Suggestion
    (item,) = [item for item in _record(fx).suggestions if item.kind == "master_line" and item.requirement == row_id]
    return item


# --- (a) in code: the answer on file settles the row ---------------------------------------------------------------


def _row(status: str = "unclear", cls: str = "askable", sources: list[str] | None = None) -> dict:
    return {"id": "req-0a0b0c", "requirement": "Kubernetes", "class": cls, "class_basis": "Requirements: Kubernetes", "status": status, "resume_evidence": [], "sources": sources or []}


def test_an_answer_on_file_drops_its_question_and_settles_its_must_have_row() -> None:
    own = {"question_id": "requirement:req.0a0b0c", "question": ASKED, "requirement": "Kubernetes"}
    row = _row()
    kept, dropped, settled = assessment_core.settle_answered_rows([row], [HELM_QUESTION, own], {"requirement:req.0a0b0c": SAID}, {})
    assert (kept, dropped, settled) == ([HELM_QUESTION], [own], [("req-0a0b0c", "answered")])
    assert row["status"] == "answered" and row["sources"] == ["A requirement:req.0a0b0c"] and "drafted" in row["code_note"]
    # An answer that says no is a gap, not a question to ask again.
    row = _row()
    assert assessment_core.settle_answered_rows([row], [own], {"requirement:req.0a0b0c": "No, never in production."}, {})[2] == [("req-0a0b0c", "unmet")]
    # An answer under another id of the same requirement (a question the model wrote) settles the row too.
    other = {"question_id": "tooling:kubernetes", "question": "Have you run Kubernetes?", "requirement": "kubernetes"}
    row = _row()
    assert assessment_core.settle_answered_rows([row], [other], {"tooling:kubernetes": SAID}, {})[2] == [("req-0a0b0c", "answered")]
    assert row["sources"] == ["A tooling:kubernetes"]
    # An optional row is never settled (it never holds), and with no answer on file nothing changes.
    row = _row(cls="list_item")
    assert assessment_core.settle_answered_rows([row], [own], {"requirement:req.0a0b0c": SAID}, {})[2] == [] and row["status"] == "unclear"
    row = _row()
    assert assessment_core.settle_answered_rows([row], [own], {}, {}) == ([own], [], []) and row["status"] == "unclear"


def test_the_strict_read_settles_a_met_row_it_lowers_that_cites_an_answer_as_answered() -> None:
    # Widened T6 (0.3.2 gyl): a row the model said met on a master line AND an answer; the line does not show it (T1),
    # so before 0.3.2 it went back to `unclear` and the answered question was asked again. Now: answered.
    master = strict_master("# Experience\n### Engineer, Acme <!-- id: r-1 -->\nJan 2020 - Present\n- Built Go services. <!-- id: b-1 -->\n")
    row = {**_row(status="met", cls="hard", sources=["b-1", "A requirement:req.0a0b0c"]), "alternatives": ["Kubernetes"]}
    changes = REAL_STRICT_ROWS([row], master)
    assert row["status"] == ANSWERED and [change.step for change in changes] == ["T6"]
    # The same row with no answer cited: lowered to a question.
    row = {**_row(status="met", cls="hard", sources=["b-1"]), "alternatives": ["Kubernetes"]}
    REAL_STRICT_ROWS([row], master)
    assert row["status"] == "unclear"


# --- (c) the drafted line, in code ---------------------------------------------------------------------------------


def test_the_line_is_drafted_from_the_answers_own_words_and_a_bare_yes_from_the_requirement() -> None:
    assert drafted_line.drafted_text("Kubernetes", ["Kubernetes"], SAID) == DRAFT
    # A first sentence that is only "yes" is passed over; words that do not name the tool get it named in front.
    assert drafted_line.drafted_text("Kubernetes", ["Kubernetes"], "Yes. I've run EKS for three years.") == "Worked with Kubernetes: run EKS for three years."
    assert drafted_line.drafted_text("Kubernetes", [], "Yes. I ran EKS for three years.") == "Ran EKS for three years."
    # A bare yes: the tool, or the words the user said yes to.
    assert drafted_line.drafted_text("Kubernetes", ["Kubernetes"], "Yes") == "Worked with Kubernetes."
    assert drafted_line.drafted_text("Experience in a regulated healthcare setting", [], "yes!") == "Experience in a regulated healthcare setting."
    assert len(drafted_line.drafted_text("Kubernetes", [], "Kubernetes " + "x" * 400)) <= drafted_line.MAX_DRAFT_CHARS + 1
    assert drafted_line.answer_ref(["b-1", "A requirement:req.0a0b0c"]) == ("requirement:req.0a0b0c", None)
    assert drafted_line.answer_ref(["A story:cut-ci"]) == (None, "cut-ci")
    assert drafted_line.draft_for({"requirement": "Kubernetes"}, None, SAID, question_id="q:k8s", story_id=None).to_json() == {
        "text": DRAFT, "entry_id": None, "backed": "answer:q:k8s",
    }
    assert drafted_line.draft_for({"requirement": "Kubernetes"}, None, "Cut CI time on Kubernetes.", question_id=None, story_id="cut-ci").backed == "story:cut-ci"
    assert drafted_line.draft_for({"requirement": "Kubernetes"}, None, None, question_id="q:k8s", story_id=None) is None


# --- (b) + (c): asked once, never again; the gate suggests; a line is drafted -------------------------------------


def test_an_answered_must_have_is_not_asked_again_suggests_keeps_the_models_pick_and_drafts_a_line(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    row_id, question_id, listed_ids = _answered_job(fx, monkeypatch)
    model_pick = _model_pick(fx)

    def settled() -> None:
        stored = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _JOB)
        row = next(item for item in stored.result.matrix if item.id == row_id)
        assert row.status.value == "answered" and f"A {question_id}" in row.sources
        assert [item.question_id for item in stored.result.structured_questions] == [HELM_QUESTION["question_id"]], "the answered question is not asked again"
        assert stored.resume_gate.decision == SUGGEST and stored.resume_gate.reasons == ()
        assert stored.result.pick is not None and stored.result.pick.lines == model_pick
        record = _record(fx)
        assert record.selection is not None and record.selection["picked_by"] == "model"
        assert read_tailored_resume(_resume_path(fx)) is not None, "the resume is made: an answered must-have does not hold it"

    settled()  # after `gigai scout answer --reassess`
    for _again in range(2):  # the model asks the answered question again, twice
        _assess(fx, _asking_again(fx, question_id, listed_ids))
        assert len(fx.base.model.assess_prompts) == 1, "no retry is spent on a question that was answered"
        settled()
    # ONE master_line suggestion for the row; the stored record holds ids only, the read derives the draft.
    item = _master_line(fx, row_id)
    assert item.status == "open" and "draft" not in item.to_json() and DRAFT not in json.dumps(item.to_json())
    listed = job_actions.list_suggestions(fx.home_root, fx.target, _URL)
    (shown,) = [entry for entry in listed["suggestions"] if entry["id"] == item.id]
    roles = list(_master(fx).master.entries_in("experience"))
    northwind = next(entry.id for entry in roles if entry.ongoing)
    assert shown["draft"] == {"text": DRAFT, "entry_id": northwind, "backed": f"answer:{question_id}"}
    assert _cli(fx, "suggestions", "list", "--job-url", _URL)["suggestions"] == listed["suggestions"]


# --- (d) one call adds the line and settles the job, with no model call --------------------------------------------


def _added(fx: PostingsFixture, row_id: str, question_id: str, body: dict, sg: str) -> str:
    """What every add_line leaves: the line in the master backed by the answer, the row met, the history entry, the
    suggestion resolved with the line, the resume picked again with the line in it. The line id."""

    line_id = body["line"]["id"]
    assert body["resolved"] == sg and body["row"] == {"id": row_id, "status": "met"} and body["repick"] is None
    master = _master(fx).master
    line = master.items[line_id]
    assert line.text == DRAFT and f"answer:{question_id}" in line.backed and [item.text for item in master.items.values()].count(DRAFT) == 1
    stored = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    row = next(item for item in stored.result.matrix if item.id == row_id)
    assert row.status.value == "met" and row.sources[0] == line_id
    assert [entry.trigger for entry in stored.history].count(f"{job_actions.TRIGGER_MASTER_LINE}:{line_id}") == 1
    assert stored.resume_gate.decision == SUGGEST
    resolved = next(item for item in _record(fx).suggestions if item.id == sg)
    assert resolved.status == "done" and resolved.resolved.how == "master_line" and resolved.resolved.ref == line_id
    assert DRAFT in read_tailored_resume(_resume_path(fx)).markdown
    return line_id


def test_add_line_through_the_api_adds_the_line_settles_the_row_repicks_and_never_calls_a_model(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    row_id, question_id, _listed = _answered_job(fx, monkeypatch)
    monkeypatch.setattr("gigai.scout.assess_strict.strict_rows", REAL_STRICT_ROWS)
    _no_model(fx, monkeypatch)
    sg = _master_line(fx, row_id).id
    server = _Server(fx, monkeypatch)
    try:
        post = lambda **more: server.client.post("/api/jobs/suggestions", json={"job_url": _URL, "action": "add_line", **more})  # noqa: E731
        # Refused, and nothing written: no such suggestion; a suggestion that is not a master_line.
        before = _master(fx).revision.revision
        missing = post(suggestion_id="sg-99")
        assert (missing.status_code, missing.json()["error"]["code"]) == (404, "suggestion_not_found")
        reword = next(item.id for item in _record(fx).suggestions if item.kind == "reword")
        wrong = post(suggestion_id=reword)
        assert (wrong.status_code, wrong.json()["error"]["code"]) == (422, "invalid_value")
        # A line the master says word for word already: refused, nothing written.
        same = post(suggestion_id=sg, text=PLAIN_LINE)
        assert same.status_code == 409 and same.json()["error"]["code"] in ("master_line_exists", "near_duplicate")
        assert _master(fx).revision.revision == before and _master_line(fx, row_id).status == "open"
        # The call.
        done = post(suggestion_id=sg)
        assert done.status_code == 200, done.text
        _added(fx, row_id, question_id, done.json(), sg)
        # Done once: the suggestion is resolved, so a second call is refused and writes nothing.
        again = post(suggestion_id=sg)
        assert (again.status_code, again.json()["error"]["code"]) == (422, "invalid_value")
    finally:
        server.close()


def test_add_line_through_the_cli_and_a_run_stopped_half_way_is_finished_by_the_same_call(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    row_id, question_id, _listed = _answered_job(fx, monkeypatch)
    monkeypatch.setattr("gigai.scout.assess_strict.strict_rows", REAL_STRICT_ROWS)
    _no_model(fx, monkeypatch)
    sg = _master_line(fx, row_id).id
    # A run that stops after the master and the assessment were written (the resolve fails).
    real_resolve = job_actions.resolve_suggestion

    def broken(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise job_actions.JobActionError("target_unavailable", "the disk went away")

    monkeypatch.setattr(job_actions, "resolve_suggestion", broken)
    failed = _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL, ok=False)
    assert failed["error"]["code"] == "target_unavailable"
    assert [item.text for item in _master(fx).master.items.values()].count(DRAFT) == 1 and _master_line(fx, row_id).status == "open"
    # The same call again finishes it: no second line, no second history entry, the suggestion resolved, the resume picked.
    monkeypatch.setattr(job_actions, "resolve_suggestion", real_resolve)
    _added(fx, row_id, question_id, _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL), sg)
    # A job the call does not know: refused.
    assert _cli(fx, "suggestions", "add-line", sg, "--job-url", "https://jobs.example.invalid/nowhere/9", ok=False)["error"]["code"]


# --- (e) what still holds ---------------------------------------------------------------------------------------------


def test_an_open_question_or_a_must_have_never_asked_about_still_holds() -> None:
    from gigai.scout.find_jobs.contracts import AssessmentQuestion, RequirementMatrixRow

    answered = RequirementMatrixRow.from_json({"id": "req-0a0b0c", "requirement": "Kubernetes", "class": "askable", "status": "answered", "resume_evidence": []})
    unclear = RequirementMatrixRow.from_json({"id": "req-0d0e0f", "requirement": "Terraform", "class": "askable", "status": "unclear", "resume_evidence": []})
    asked = AssessmentQuestion.from_json({"question_id": "requirement:req.0d0e0f", "question": "Terraform?", "requirement": "Terraform"})
    assert gate((answered,), (), "matched_above_threshold").decision == SUGGEST
    assert gate((answered, unclear), (asked,), "pending_user_answers").decision == HOLD_QUESTION
    assert gate((answered, unclear), (), "matched_above_threshold").decision == HOLD_QUESTION
