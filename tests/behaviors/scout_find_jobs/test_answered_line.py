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
from gigai.scout.resume_gate import HOLD_QUESTION, HOLD_UNMET, SUGGEST, gate
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


def _answered_job(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, *, said: str = SAID) -> tuple[str, str, tuple[str, ...]]:
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
    answered = _cli(fx, "answer", question_id, "--answer-text", said, "--reassess", _URL)
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


_DENY = (
    "I have never used it", "I've not used Kubernetes", "Not professionally, only a weekend course", "Haven't used it", "Unfortunately no",
    "Only Docker, not Kubernetes", "Nein", "No", "Nope", "Never", "I haven't", "No, never in production.",
    # Mine.
    "Nada", "I don't have production experience, only labs", "Nah, not really", "Sadly I can't say I have",
    # Round 2: a denial in the first clause denies, whatever follows.
    "I have no direct experience but I have read the docs", "I have had no exposure",
)
_AFFIRM = (
    "No problem: 5 years running EKS", "No doubt, yes, daily", SAID, "Yes", "story:cut-ci",
    # Mine.
    "Not only Docker: Kubernetes on EKS too", "No worries, I ran it at Acme", "Not a problem, 4 years on GKE", "Built non-trivial clusters on EKS",
    "Ja, drei Jahre bei Acme",
    # Round 2: a yes whose later clause says no about something the requirement does not name.
    "Yes, and it's not hard", "Absolutely, couldn't ship without it", "Yes, not a blocker", "Yes — not a blocker",
)
_BOTH = (
    "No gaps: ran EKS for 3 years", "Yes, but not in production", "Not in production, but 2 years of side projects",
    # Mine: the eval fixture's answer (m12-answered-backend), a no to one alternative and years of the other.
    "No Cassandra in production. MongoDB in production at two employers, Quillon Health and Brassline Pay, for about four years in total.",
    # Round 2: unsure is no yes; a yes taken back by a bare no.
    "I'm not sure", "Not certain", "Yes, no",
)
#: The requirement the table's answers are given to.
_ASKED_ABOUT = "Kubernetes in production on EKS"


@pytest.mark.parametrize(("said", "expected"), [*((text, "denies") for text in _DENY), *((text, "affirms") for text in _AFFIRM), *((text, "ambiguous") for text in _BOTH)])
def test_an_answer_reads_as_a_no_a_yes_or_both(said: str, expected: str) -> None:
    from gigai.scout.assess_strict import answer_denial

    assert answer_denial(said, requirement=_ASKED_ABOUT) == expected


@pytest.mark.parametrize(("said", "requirement", "expected"), [
    ("Yes, but not Kubernetes", "Kubernetes", "ambiguous"),
    ("Yes but never used Helm", "Helm charts", "ambiguous"),
    ("Yes, though not with Terraform", "Terraform", "ambiguous"),
    # The same no beside a yes, about something the requirement does not name: a yes to it.
    ("Yes, but not Kubernetes", "Docker", "affirms"),
    ("Yes but never used Helm", "Kubernetes", "affirms"),
])
def test_a_yes_with_a_no_reads_both_only_when_the_no_is_about_the_requirement(said: str, requirement: str, expected: str) -> None:
    from gigai.scout.assess_strict import answer_denial

    assert answer_denial(said, requirement=requirement) == expected


def test_a_no_the_old_reader_missed_is_a_gap_and_an_answer_saying_both_stays_unclear_and_is_not_asked_again() -> None:
    own = {"question_id": "requirement:req.0a0b0c", "question": ASKED, "requirement": "Kubernetes"}
    for said in ("I've not used Kubernetes", "Haven't used it", "Unfortunately no", "Nein", "Only Docker, not Kubernetes"):
        row = _row()
        kept, dropped, settled = assessment_core.settle_answered_rows([row], [own], {"requirement:req.0a0b0c": said}, {})
        assert (kept, dropped, settled) == ([], [own], [("req-0a0b0c", "unmet")]) and row["status"] == "unmet", said
    # "No problem: ..." is a yes.
    row = _row()
    assert assessment_core.settle_answered_rows([row], [own], {"requirement:req.0a0b0c": "No problem: 5 years running EKS"}, {})[2] == [("req-0a0b0c", "answered")]
    # No and yes at once: the row stays unclear, cites the answer and says why; its question is dropped all the same.
    row = _row()
    kept, dropped, settled = assessment_core.settle_answered_rows([row], [HELM_QUESTION, own], {"requirement:req.0a0b0c": "No gaps: ran EKS for 3 years"}, {})
    assert (kept, dropped, settled) == ([HELM_QUESTION], [own], [])
    assert row["status"] == "unclear" and row["sources"] == ["A requirement:req.0a0b0c"] and row["code_note"] == assessment_core.ANSWER_AMBIGUOUS_NOTE
    # A row the strict read made `answered` (T6) on an answer that says no: a gap.
    row = _row(status="answered", cls="hard", sources=["b-1", "A requirement:req.0a0b0c"])
    assert assessment_core.settle_answered_rows([row], [], {"requirement:req.0a0b0c": "I have never used it"}, {})[2] == [("req-0a0b0c", "unmet")]
    assert row["status"] == "unmet"
    # ... and on a yes it stays answered, untouched.
    row = _row(status="answered", cls="hard", sources=["b-1", "A requirement:req.0a0b0c"])
    assert assessment_core.settle_answered_rows([row], [], {"requirement:req.0a0b0c": SAID}, {})[2] == [] and row["status"] == "answered"


def test_a_row_citing_an_answer_on_file_is_never_raised_as_an_unasked_question() -> None:
    from gigai.scout.assessment_core import Boundary

    row = {**_row(sources=["A requirement:req.0a0b0c"]), "code_note": assessment_core.ANSWER_AMBIGUOUS_NOTE}
    other = _row()
    other["id"] = "req-0d0e0f"
    boundary = Boundary(answers={"requirement:req.0a0b0c": "No gaps: ran EKS for 3 years"})
    raised = assessment_core._unasked([row, other], [], "matched_above_threshold", boundary)  # noqa: SLF001 - the re-ask rule itself
    assert [row_id for row_id, _row_ in raised] == ["req-0d0e0f"]


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


def test_a_years_shortfall_is_not_settled_as_answered_by_the_strict_read() -> None:
    # The line shows Kubernetes but not five years of it (T3 lowers it): an answer cited beside it does not make it `answered`.
    master = strict_master(
        "# Experience\n### Engineer, Acme <!-- id: r-1 -->\nJan 2023 - Present\n- Ran Kubernetes clusters on EKS. <!-- id: b-1 -->\n"
    )
    row = {**_row(status="met", cls="hard", sources=["b-1", "A requirement:req.0a0b0c"]), "requirement": "5+ years of Kubernetes", "alternatives": ["Kubernetes"]}
    changes = REAL_STRICT_ROWS([row], master)
    assert row["status"] != ANSWERED and "T3" in [change.step for change in changes] and "T6" not in [change.step for change in changes]


# --- (c) the drafted line, in code ---------------------------------------------------------------------------------


def test_the_line_is_drafted_from_the_answers_own_words_and_a_bare_yes_from_the_requirement() -> None:
    assert drafted_line.drafted_text("Kubernetes", ["Kubernetes"], SAID) == DRAFT
    # A first sentence that is only "yes" is passed over; words that do not name the tool get it named in front.
    assert drafted_line.drafted_text("Kubernetes", ["Kubernetes"], "Yes. I've run EKS for three years.") == "Worked with Kubernetes: ran EKS for three years."
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


def test_no_line_is_drafted_from_a_no_and_the_words_are_put_in_the_users_own_past_tense() -> None:
    for said in ("I have never used it", "Unfortunately no", "Nein", "I've not used Kubernetes"):
        assert drafted_line.drafted_text("Kubernetes", ["Kubernetes"], said) == "", said
        assert drafted_line.draft_for({"requirement": "Kubernetes"}, None, said, question_id="q:k8s", story_id=None) is None, said
    # A story id alone is a bare yes, never the line's words.
    assert drafted_line.drafted_text("Kubernetes", ["Kubernetes"], "story:cut-ci") == "Worked with Kubernetes."
    # "I've been / I've written": the user's own past tense, not a broken participle.
    assert drafted_line.drafted_text("Kubernetes", [], "Yes, I've been running Kubernetes on EKS since 2021.") == "Was running Kubernetes on EKS since 2021."
    assert drafted_line.drafted_text("Terraform", [], "I've written Terraform modules for every cluster.") == "Wrote Terraform modules for every cluster."
    # A story's words are never read as a no (a story is not an answer to the question).
    assert drafted_line.draft_for({"requirement": "Kubernetes"}, None, "Moved CI to Kubernetes, not Jenkins.", question_id=None, story_id="cut-ci") is not None


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
        line_id = _added(fx, row_id, question_id, done.json(), sg)
        # Done once: the suggestion is resolved, so a second call is refused, says so, names the line and writes nothing.
        written = _master(fx).revision.revision
        again = post(suggestion_id=sg)
        assert (again.status_code, again.json()["error"]["code"]) == (422, "invalid_value")
        assert "already done" in again.json()["error"]["message"] and line_id in again.json()["error"]["message"]
        assert _master(fx).revision.revision == written
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


def _broken_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise job_actions.JobActionError("target_unavailable", "the disk went away")

    monkeypatch.setattr(job_actions, "resolve_suggestion", broken)


def _settled_on(fx: PostingsFixture, row_id: str, sg: str, body: dict, line_id: str) -> None:
    """The job settled on ``line_id``: the row met on it, the history entry once, the suggestion resolved with it."""

    assert body["line"]["id"] == line_id and body["row"] == {"id": row_id, "status": "met"} and body["resolved"] == sg
    stored = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    row = next(item for item in stored.result.matrix if item.id == row_id)
    assert row.status.value == "met" and row.sources[0] == line_id and stored.resume_gate.decision == SUGGEST
    assert [entry.trigger for entry in stored.history].count(f"{job_actions.TRIGGER_MASTER_LINE}:{line_id}") == 1
    resolved = next(item for item in _record(fx).suggestions if item.id == sg)
    assert resolved.status == "done" and resolved.resolved.ref == line_id


def test_a_run_stopped_half_way_is_finished_by_a_retry_in_other_words(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    row_id, _question_id, _listed = _answered_job(fx, monkeypatch)
    monkeypatch.setattr("gigai.scout.assess_strict.strict_rows", REAL_STRICT_ROWS)
    _no_model(fx, monkeypatch)
    sg = _master_line(fx, row_id).id
    real_resolve = job_actions.resolve_suggestion
    _broken_resolve(monkeypatch)
    own = "Ran Kubernetes on EKS at Acme for three years, every cluster in production."
    assert _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL, "--text", own, ok=False)["error"]["code"] == "target_unavailable"
    (line_id,) = [item.id for item in _master(fx).master.items.values() if item.text == own]
    # The retry with the drafted words (not the user's): no second line, the run is finished on the line written first.
    monkeypatch.setattr(job_actions, "resolve_suggestion", real_resolve)
    body = _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL)
    _settled_on(fx, row_id, sg, body, line_id)
    texts = [item.text for item in _master(fx).master.items.values()]
    assert texts.count(own) == 1 and DRAFT not in texts


def test_a_close_line_carrying_the_suggestions_own_answer_is_taken_as_the_earlier_attempt(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import master_edit

    row_id, question_id, _listed = _answered_job(fx, monkeypatch)
    monkeypatch.setattr("gigai.scout.assess_strict.strict_rows", REAL_STRICT_ROWS)
    _no_model(fx, monkeypatch)
    sg = _master_line(fx, row_id).id
    # A line close to the drafted one, backed by the same answer, under an older role (not where the draft goes).
    older = next(entry.id for entry in _master(fx).master.entries_in("experience") if not entry.ongoing)
    close = "Ran Kubernetes clusters on EKS for three years at Acme Corp."
    evidence = master_edit.evidence_for(home_root=fx.home_root, target=fx.target, question_id=question_id)
    written = master_edit.add_line(home_root=fx.home_root, target=fx.target, text=close, entry_id=older, evidence=evidence, force=True)
    (line_id,) = written.ids
    body = _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL)
    _settled_on(fx, row_id, sg, body, line_id)
    assert DRAFT not in [item.text for item in _master(fx).master.items.values()]


def test_a_line_of_the_same_answer_that_does_not_show_the_requirement_is_not_taken_for_the_earlier_attempt(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import master_edit

    row_id, question_id, _listed = _answered_job(fx, monkeypatch)
    monkeypatch.setattr("gigai.scout.assess_strict.strict_rows", REAL_STRICT_ROWS)
    _no_model(fx, monkeypatch)
    sg = _master_line(fx, row_id).id
    # A line the user added by hand under the role the draft goes to, backed by the same answer, that does not name Kubernetes.
    northwind = next(entry.id for entry in _master(fx).master.entries_in("experience") if entry.ongoing)
    evidence = master_edit.evidence_for(home_root=fx.home_root, target=fx.target, question_id=question_id)
    (hand,) = master_edit.add_line(
        home_root=fx.home_root, target=fx.target, text="Mentored four engineers on the platform team.", entry_id=northwind, evidence=evidence, force=True,
    ).ids
    # The drafted line is written beside it, under the same role, and the row is met on the drafted line.
    line_id = _added(fx, row_id, question_id, _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL), sg)
    master = _master(fx).master
    assert line_id != hand and hand in master.items and master.items[line_id].entry_id == northwind


def test_add_line_ref_settles_on_a_line_the_master_has_and_refuses_one_that_does_not_show_it(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import master_edit

    row_id, _question_id, _listed = _answered_job(fx, monkeypatch)
    monkeypatch.setattr("gigai.scout.assess_strict.strict_rows", REAL_STRICT_ROWS)
    _no_model(fx, monkeypatch)
    sg = _master_line(fx, row_id).id
    ids = _ids(fx)
    before = _master(fx).revision.revision
    # Refused, nothing changed: a line the master does not have; a line that does not show the requirement.
    missing = _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL, "--ref", "b-nowhere", ok=False)["error"]
    assert missing["code"] == "invalid_value" and "no line b-nowhere" in missing["message"]
    plain = _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL, "--ref", ids[PLAIN_LINE], ok=False)["error"]
    assert plain["code"] == "invalid_value" and "does not show" in plain["message"]
    assert _master(fx).revision.revision == before and _master_line(fx, row_id).status == "open"
    # A line the user added by hand that shows it: the job settles on it, nothing more is written.
    roles = list(_master(fx).master.entries_in("experience"))
    northwind = next(entry.id for entry in roles if entry.ongoing)
    hand = "Operated Kubernetes clusters on EKS for the payments team."
    (line_id,) = master_edit.add_line(home_root=fx.home_root, target=fx.target, text=hand, entry_id=northwind, force=True).ids
    count = len(_master(fx).master.items)
    body = _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL, "--ref", line_id)
    _settled_on(fx, row_id, sg, body, line_id)
    assert len(_master(fx).master.items) == count and DRAFT not in [item.text for item in _master(fx).master.items.values()]


def test_add_line_on_a_master_with_no_role_names_the_command_that_places_it(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    row_id, _question_id, _listed = _answered_job(fx, monkeypatch)
    _no_model(fx, monkeypatch)
    sg = _master_line(fx, row_id).id
    real_draft = job_actions._row_draft  # noqa: SLF001 - the draft of a master with no role: entry_id None
    monkeypatch.setattr(job_actions, "_row_draft", lambda *args: (lambda draft: draft and replace(draft, entry_id=None))(real_draft(*args)))
    before = _master(fx).revision.revision
    refused = _cli(fx, "suggestions", "add-line", sg, "--job-url", _URL, ok=False)["error"]
    assert refused["code"] == "master_place_invalid"
    assert "master add --from-answer" in refused["message"] and f"add-line {sg} --job-url URL --ref LINE" in refused["message"]
    assert _master(fx).revision.revision == before and _master_line(fx, row_id).status == "open"


def _held_by_0_3_1(fx: PostingsFixture, row_id: str) -> None:
    """What 0.3.1 stored: the answered must-have held the resume (reason `answered`), the verdict pending, no master_line suggestion."""

    from dataclasses import replace

    from gigai.scout import quick_assess
    from gigai.scout import suggestions as store
    from gigai.scout.find_jobs.assess_contracts import GATE_REASON_ANSWERED, GateReason, GateRecord
    from gigai.scout.find_jobs.discovery.storage import atomic_write

    stored = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    held = replace(stored, result=replace(stored.result, verdict="pending_user_answers"), resume_gate=GateRecord(HOLD_QUESTION, (GateReason(GATE_REASON_ANSWERED, row_id),)))
    path = quick_assess.quick_assess_write_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    atomic_write(path, json.dumps(held.to_json(), indent=2, sort_keys=True).encode("utf-8"))
    record = _record(fx)
    gate_031 = {"decision": HOLD_QUESTION, "ready": False, "reasons": [{"code": GATE_REASON_ANSWERED, "requirement": row_id}]}
    store.save_record(replace(record, gate=gate_031, suggestions=tuple(item for item in record.suggestions if item.kind != "master_line")))
    assert job_actions.pick_view(fx.home_root, fx.target, _URL)["gate"]["decision"] == HOLD_QUESTION


def test_a_job_held_by_0_3_1_on_an_answered_row_is_decided_again_by_a_refresh_with_no_model_call(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.pick import TRIGGER_REGATE

    row_id, _question_id, _listed = _answered_job(fx, monkeypatch)
    _no_model(fx, monkeypatch)
    _held_by_0_3_1(fx, row_id)
    # The refresh: today's gate suggests, the resume is picked, the history says so, the line is suggested.
    view = job_actions.pick_action(fx.home_root, fx.target, _URL, job_actions.ACTION_REFRESH)
    assert view["gate"]["decision"] == SUGGEST and view["gate"]["ready"] is True
    after = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    assert after.resume_gate.decision == SUGGEST and after.resume_gate.reasons == () and after.result.verdict == "matched_above_threshold"
    assert after.history[-1].trigger == TRIGGER_REGATE and [entry.trigger for entry in after.history].count(TRIGGER_REGATE) == 1
    assert _master_line(fx, row_id).status == "open" and read_tailored_resume(_resume_path(fx)) is not None
    # Again: nothing more to decide, no second history entry, no second suggestion.
    job_actions.pick_action(fx.home_root, fx.target, _URL, job_actions.ACTION_REFRESH)
    again = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    assert [entry.trigger for entry in again.history].count(TRIGGER_REGATE) == 1 and _master_line(fx, row_id).status == "open"


def test_a_regate_whose_pick_fails_leaves_a_ready_record_by_the_records_own_rule(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    row_id, _question_id, _listed = _answered_job(fx, monkeypatch)
    _no_model(fx, monkeypatch)
    _held_by_0_3_1(fx, row_id)

    def broken(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("the disk went away")

    monkeypatch.setattr("gigai.scout.pick.settle_and_store", broken)
    with pytest.raises(job_actions.JobActionError):
        job_actions.pick_action(fx.home_root, fx.target, _URL, job_actions.ACTION_REFRESH)
    # The record kept its selection, so the regate's gate is ready: a suggest decision with a selection and nothing lost.
    record = _record(fx)
    assert record.selection is not None
    assert record.gate["decision"] == SUGGEST and record.gate["ready"] is True and record.gate["reasons"] == []


def test_a_regate_reads_the_rows_again_on_the_answers_on_file_and_an_answer_changed_to_no_is_a_gap(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import story_bank

    row_id, question_id, _listed = _answered_job(fx, monkeypatch)
    _no_model(fx, monkeypatch)
    _held_by_0_3_1(fx, row_id)
    # The answer on file changed to a no after 0.3.1 settled the row `answered` (no re-assessment since).
    story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id=question_id, answer="I have never used Kubernetes.")
    with pytest.raises(job_actions.JobActionError):
        job_actions.pick_action(fx.home_root, fx.target, _URL, job_actions.ACTION_REFRESH)  # held: an askable must-have unmet
    after = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    row = next(item for item in after.result.matrix if item.id == row_id)
    assert row.status.value == "unmet" and f"A {question_id}" in row.sources
    assert after.resume_gate.decision == HOLD_UNMET and after.history[-1].trigger == "regate"
    record = _record(fx)
    assert record.gate["decision"] == HOLD_UNMET and record.gate["ready"] is False
    assert next(item for item in record.requirements if item.id == row_id).status == "unmet"
    assert not [item for item in record.suggestions if item.kind == "master_line"]
    listed = job_actions.list_suggestions(fx.home_root, fx.target, _URL)["suggestions"]
    assert not [item for item in listed if item["kind"] == "master_line"]


def test_an_answer_that_reads_both_is_not_asked_again_and_holds_on_its_row(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    row_id, question_id, _listed = _answered_job(fx, monkeypatch, said="Yes, but not with Kubernetes itself, I'm not sure.")
    stored = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    row = next(item for item in stored.result.matrix if item.id == row_id)
    # Unclear, citing the answer, with the note that names what settles it; no question on it, the gate holds on the row.
    assert row.status.value == "unclear" and f"A {question_id}" in row.sources and row.code_note == assessment_core.ANSWER_AMBIGUOUS_NOTE
    assert question_id not in [item.question_id for item in stored.result.structured_questions]
    assert stored.resume_gate.decision == HOLD_QUESTION and row_id in [reason.requirement for reason in stored.resume_gate.reasons]
    assert not [item for item in _record(fx).suggestions if item.kind == "master_line" and item.requirement == row_id]


# --- (e) what still holds ---------------------------------------------------------------------------------------------


def test_an_open_question_or_a_must_have_never_asked_about_still_holds() -> None:
    from gigai.scout.find_jobs.contracts import AssessmentQuestion, RequirementMatrixRow

    answered = RequirementMatrixRow.from_json({"id": "req-0a0b0c", "requirement": "Kubernetes", "class": "askable", "status": "answered", "resume_evidence": []})
    unclear = RequirementMatrixRow.from_json({"id": "req-0d0e0f", "requirement": "Terraform", "class": "askable", "status": "unclear", "resume_evidence": []})
    asked = AssessmentQuestion.from_json({"question_id": "requirement:req.0d0e0f", "question": "Terraform?", "requirement": "Terraform"})
    assert gate((answered,), (), "matched_above_threshold").decision == SUGGEST
    assert gate((answered, unclear), (asked,), "pending_user_answers").decision == HOLD_QUESTION
    assert gate((answered, unclear), (), "matched_above_threshold").decision == HOLD_QUESTION
