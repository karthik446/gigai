"""0.1.11 N3 (SPEC 1.7 steps 1 to 4): what ``run_quick_assessment`` does after the model answers, end to end.

A FAKE model (the pipeline fixture's scripted one) on a synthetic home with a stored master.  The template is the
shipped text with its v9 placeholders neutralized (the ``fx`` fixture), so the v8-shaped cases below still render the
prompt they always did; the shipped v9 text itself is pinned in ``test_assess_prompt_v92.py`` and
``test_assessment_v9_boundary.py``.  So:

- INERT: a v8-shaped answer leaves exactly what it left before: no v9 key in the stored assessment (which
  re-serializes byte for byte), no requirement list, no suggestion record, no job resume, no id in the prompt;
- a v9-shaped answer (its rows carry ``class_basis``) gets its row ids, its stored gate, the posting's requirement
  list, a selection and a suggestion record, all without a second call;
- with a template that carries the v9 placeholders (patched in here; step 3 writes the real text) the RESUME block
  shows ids, the model's pick is settled, the next assessment must return exactly the listed ids, and a stored
  assessment is stale for its resume by its SOURCES, not by shared words;
- OD1: a must-have confirmed unmet holds the resume and the job reads ``has_gap`` (job state and posting read model);
- N3b (decision #11): a must-have the model leaves ``unclear`` and never asks about is stored pending, with the row's
  own question (asked by code after the one retry), and the job reads "needs your answers";
- N3b (orchestrator #14): a first assessment's ``gap`` suggestion that names its row by words is stored with the row's id;
- A6: a job resume the user changed is never replaced; the new selection waits as ``proposed``;
- opening a job (the stale list) writes nothing.
"""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.canonical import parse_json_bytes
from gigai.scout import assessment_core, postings, requirements_list, suggestions
from gigai.scout.assessment_basis import BasisCheck
from gigai.scout.experience_answers import read_answers, record_answer
from gigai.scout.find_jobs import job_state
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResponse, AssessResumeInput
from gigai.scout.find_jobs.contracts import normalize_url
from gigai.scout.master_store import import_master, load_master
from gigai.scout.quick_assess import QuickAssessError, quick_assess_path, run_quick_assessment
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import TailorEdit, read_tailored_resume, save_tailor_response, tailored_resume_path

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import build_pipeline_fixture
from tests.support.posting_fixtures import NOW, PostingsFixture, days_ago, job_url, lever_job

_SLUG = "harborlight"
_URL = job_url(_SLUG, 1)
_JOB = normalize_url(_URL)
_POSTING = (
    "Harborlight is hiring a Staff AI Engineer to own Python inference services. Requirements: 5+ years of Python in "
    "production; Kubernetes; Terraform modules for every cluster; Helm charts for every service. Remote within the United States."
)
RESUME = """## Experience

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.
- Operated Kubernetes clusters backed by PostgreSQL.

## Skills

- Python, Kubernetes, PostgreSQL
"""
MASTER = """## Summary

- Engineer with nine years on Python inference services.

## Experience

### Northwind Labs
Staff Engineer | 2023 - Present

- Own the Python inference services behind 40 product teams.
- Wrote the Terraform modules every Kubernetes cluster is built from.

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.
- Operated Kubernetes clusters backed by PostgreSQL.

## Skills

- Platform: Python, Kubernetes, PostgreSQL, Terraform
"""
OWN_LINE = "Own the Python inference services behind 40 product teams."
TERRAFORM_LINE = "Wrote the Terraform modules every Kubernetes cluster is built from."
PYTHON_LINE = "Built Python services for six years; cut p99 latency by 40%."
KUBERNETES_LINE = "Operated Kubernetes clusters backed by PostgreSQL."
REQUIREMENTS = ("5+ years of Python in production", "Kubernetes", "Terraform modules for every cluster", "Helm charts for every service")
HELM_QUESTION = {"question_id": "tooling:helm", "question": "Have you written Helm charts?", "requirement": REQUIREMENTS[3]}
V9_PARAGRAPHS = (
    "\n\nIDS: each RESUME line ends with its id, like {{id_example}}. For each met row, sources lists the ids it relied on."
    "\n\nPICK: return {{pick_lines}} line ids, best first."
    "\n\n{{requirements}}"
)


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> PostingsFixture:
    shipped = assessment_core.load_assess_instructions()
    for name in ("id_example", "note_example", "pick_lines", "requirements"):
        shipped = shipped.replace("{{" + name + "}}", "x")
    monkeypatch.setattr(assessment_core, "load_assess_instructions", lambda: shipped)
    base = build_pipeline_fixture(tmp_path, monkeypatch, base=False, resume=RESUME)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fixture = PostingsFixture(base, second_profile_id="", deleted_profile_id=None)
    fixture.seed(_SLUG, [lever_job(_SLUG, 1, text=_POSTING)], seen_at=days_ago(1))
    postings.refresh(fixture.home_root, fixture.target, now=NOW)
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fixture.home_root, target=fixture.target, source=source, gig_id=base.gig.resolved.gig_id).status == "created"
    caplog.set_level(logging.WARNING, logger="gigai.scout.server")
    return fixture


def _master(fx: PostingsFixture):
    stored = load_master(home_root=fx.home_root, target=fx.target, gig_id=fx.base.gig.resolved.gig_id)
    assert stored is not None
    return stored


def _ids(fx: PostingsFixture) -> dict[str, str]:
    return {item.text: item.id for item in _master(fx).master.items.values()}


def _edit(fx: PostingsFixture, item_id: str, text: str) -> None:
    result = CliRunner().invoke(scout_group, [
        "resume", "master", "edit", item_id, "--text", text, "--revision", str(_master(fx).revision.revision),
        "--home", str(fx.home_root), "--target", str(fx.target), "--json",
    ])
    assert result.exit_code == 0, result.output


def _v8_answer() -> str:
    rows = [
        {"requirement": REQUIREMENTS[0], "class": "hard", "status": "met", "resume_evidence": ["Built Python services for six years"]},
        {"requirement": REQUIREMENTS[1], "class": "askable", "status": "met", "resume_evidence": ["Operated Kubernetes clusters"]},
        {"requirement": REQUIREMENTS[2], "class": "askable", "status": "met", "resume_evidence": ["Wrote the Terraform modules"]},
        {"requirement": REQUIREMENTS[3], "class": "list_item", "status": "unclear", "resume_evidence": []},
    ]
    return json.dumps({"verdict": "matched_above_threshold", "matrix": rows, "suggestions": ["Lead with Python."], "questions": [HELM_QUESTION], "not_a_match_reason": None})


def _v9_answer(fx: PostingsFixture, *, kubernetes: str = "met", row_ids: tuple[str, ...] | None = None, said: str = "pending_user_answers") -> str:
    """A v9-shaped answer: every row with the posting wording behind its class and the lines it relied on, suggestions, a pick.

    The model says ``pending`` for the one open Helm question, as every earlier prompt taught; Helm is one of a list.
    """

    ids = _ids(fx)
    rows = [
        {"requirement": REQUIREMENTS[0], "class": "hard", "status": "met", "resume_evidence": ["Built Python services for six years"], "sources": [ids[PYTHON_LINE]]},
        {"requirement": REQUIREMENTS[1], "class": "askable", "status": kubernetes, "resume_evidence": ["Runs clusters"] if kubernetes == "met" else [],
         "sources": [ids[KUBERNETES_LINE]] if kubernetes == "met" else []},
        {"requirement": REQUIREMENTS[2], "class": "askable", "status": "met", "resume_evidence": ["Infrastructure as code"], "sources": [ids[TERRAFORM_LINE], "b-zzzzzz"]},
        {"requirement": REQUIREMENTS[3], "class": "list_item", "status": "unclear", "resume_evidence": []},
    ]
    for index, row in enumerate(rows):
        row["class_basis"] = f"Requirements: {row['requirement']}"
        if row_ids is not None:
            row["id"] = row_ids[index]
    suggested = [
        {"kind": "reword", "line": ids[OWN_LINE], "posting_phrase": "own Python inference services", "why": "Lead with what the posting calls inference services."},
        {"kind": "gap", "requirement": "elig-location", "why": "Only an answer can say where you may work."},
    ]
    chosen = {"summary": None, "section_order": ["experience", "projects"], "lines": [ids[OWN_LINE], ids[TERRAFORM_LINE], ids[PYTHON_LINE], ids[KUBERNETES_LINE]]}
    return json.dumps({"verdict": said, "matrix": rows, "questions": [HELM_QUESTION], "suggestions": suggested, "pick": chosen, "not_a_match_reason": None})


def _assess(fx: PostingsFixture, answer: str) -> tuple[AssessResponse, str]:
    fx.base.model.assessed = answer
    fx.base.model.assess_prompts.clear()
    stored = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    return stored, fx.base.model.assess_prompts[0]


def _assessment_path(fx: PostingsFixture) -> Path:
    return quick_assess_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)


def _resume_path(fx: PostingsFixture) -> Path:
    return tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)


def _record(fx: PostingsFixture) -> suggestions.SuggestionRecord:
    found = suggestions.read_suggestions(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    assert found is not None
    return found


def _scout_files(fx: PostingsFixture) -> dict[str, bytes]:
    root = _assessment_path(fx).parents[1]
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file() and "pipeline" not in path.name}


def _patch_v9_template(monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = assessment_core.load_assess_instructions()
    monkeypatch.setattr(assessment_core, "load_assess_instructions", lambda: shipped + V9_PARAGRAPHS)


# --- inert: a v8 answer of the shipped prompt -------------------------------------------------------------------------


def test_a_v8_answer_is_stored_as_it_always_was_and_nothing_of_v9_is_written(fx: PostingsFixture, caplog: pytest.LogCaptureFixture) -> None:
    stored, prompt = _assess(fx, _v8_answer())
    root = _assessment_path(fx).parents[1]
    assert "<!-- id:" not in prompt and "REQUIREMENTS (" not in prompt
    assert stored.requirements_ref is None and stored.resume_gate is None and stored.result.pick is None and stored.result.structured_suggestions == ()
    assert all(set(row.to_json()) == {"requirement", "resume_evidence", "status", "class"} for row in stored.result.matrix)
    written = _assessment_path(fx).read_bytes()
    value = parse_json_bytes(written)
    assert not {"requirements_ref", "resume_gate"} & set(value) and not {"pick", "structured_suggestions"} & set(value["result"])
    assert json.dumps(AssessResponse.from_json(value).to_json(), indent=2, sort_keys=True).encode("utf-8") == written
    assert not (root / "requirements").exists() and not (root / "suggestions").exists() and not _resume_path(fx).exists()
    assert not caplog.records, caplog.text
    # The job reads matched (one open one-of-a-list question holds nothing, as before).
    sources = job_state.JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.base.gig.resolved)
    assert sources.state_for(_JOB, profile_id=fx.default_profile_id).state == "matched"


# --- a v9-shaped answer: ids, the gate, the list, the selection, the record -------------------------------------------


def test_a_v9_answer_gets_its_ids_its_gate_the_requirement_list_a_selection_and_a_record(fx: PostingsFixture, caplog: pytest.LogCaptureFixture) -> None:
    stored, prompt = _assess(fx, _v9_answer(fx))
    assert not caplog.records, caplog.text
    assert len(fx.base.model.assess_prompts) == 1 and "<!-- id:" not in prompt  # one call; the v8 prompt shows no ids
    # 1. The assessment: the verdict settled by the gate (Helm is optional), every row with its id; no ids were offered, so no sources and no pick.
    assert stored.result.verdict.value == "matched_above_threshold" and stored.resume_gate is not None and stored.resume_gate.to_json() == {"decision": "suggest", "reasons": []}
    listed = requirements_list.read_list(fx.home_root, fx.target, stored.posting_sha256)
    assert listed is not None and [row.requirement for row in listed.rows] == list(REQUIREMENTS)
    assert [row.id for row in stored.result.matrix] == list(listed.ids()) == list(requirements_list.assign_ids(stored.posting_sha256, REQUIREMENTS))
    assert [row.class_basis for row in stored.result.matrix] == [f"Requirements: {text}" for text in REQUIREMENTS]
    assert all(row.sources == () for row in stored.result.matrix) and stored.result.pick is None
    # 2. The list, and which list the rows are.
    assert stored.requirements_ref is not None and stored.requirements_ref.to_json() == {
        "posting_sha256": stored.posting_sha256, "rules_version": "req-rules:1", "digest": listed.digest, "rows": 4, "list": "stored",
    }
    assert listed.extracted_by.profile_id == fx.default_profile_id and listed.extracted_by.model == "fixture-model"
    reread = AssessResponse.from_json(parse_json_bytes(_assessment_path(fx).read_bytes()))
    assert reread.requirements_ref == stored.requirements_ref and reread.resume_gate == stored.resume_gate
    # The structured suggestions beside the plain strings a reader of 0.1.10 shows.
    assert [item.kind for item in stored.result.structured_suggestions] == ["reword", "gap"]
    assert stored.result.suggestions == tuple(item.why for item in stored.result.structured_suggestions)
    # 4. No usable pick: the code selector's selection is the job resume, in the tailored-resume store's own format.
    resume = read_tailored_resume(_resume_path(fx))
    assert resume is not None and resume.producer.callable == "scout.pick" and resume.usage is None and resume.edited is None
    assert resume.instructions_digest == assessment_core.INSTRUCTIONS_DIGEST and resume.sources.assessment_stored_path == stored.stored_path
    assert resume.sources.master is not None and resume.sources.master.revision_id == _master(fx).revision.revision_id
    assert resume.selection is not None and (resume.selection.picked_by, resume.selection.fallback, resume.selection.candidates) == ("code", "no_pick", "view")
    assert PYTHON_LINE in resume.markdown and _resume_path(fx).with_suffix(".md").read_text(encoding="utf-8") == resume.markdown
    # The record: ids, codes and the assessment's own sentences. Never a line of the resume, never an answer.
    record = _record(fx)
    text = json.dumps(record.to_json())
    assert all(line not in text for line in (PYTHON_LINE, TERRAFORM_LINE, KUBERNETES_LINE, OWN_LINE, "Two years on GCP"))
    assert record.gate == {"decision": "suggest", "ready": True, "reasons": []}
    assert [(row.id, row.status, row.coverage) for row in record.requirements] == [
        (listed.rows[0].id, "met", "none"), (listed.rows[1].id, "met", "none"), (listed.rows[2].id, "met", "none"), (listed.rows[3].id, "unclear", None),
    ]
    selection = record.selection
    assert selection is not None and (selection["picked_by"], selection["fallback"], selection["draft"], selection["model_pick"]) == ("code", "no_pick", False, None)
    assert selection["made_from"] == {"result_digest": suggestions.result_digest(stored), "master_revision_id": _master(fx).revision.revision_id}
    assert selection["resume"]["stored_path"] == str(_resume_path(fx)) and selection["resume"]["origin"] == "pick"
    assert record.basis["requirements"] == {"rules_version": "req-rules:1", "digest": listed.digest, "list": "stored"}
    assert record.basis["assessment"]["result_digest"] == suggestions.result_digest(stored) and record.proposed is None
    assert [(item.id, item.kind, item.source, item.status) for item in record.suggestions] == [("sg-1", "reword", "assessment", "open"), ("sg-2", "gap", "assessment", "open")]
    assert record.printed() and set(record.printed()) <= set(_master(fx).master.items)


def test_opening_a_job_derives_the_stale_list_and_writes_nothing(fx: PostingsFixture) -> None:
    _assess(fx, _v9_answer(fx))
    resolved = fx.base.gig.resolved
    assert suggestions.stale_for(fx.home_root, fx.target, fx.default_profile_id, _JOB, resolved=resolved) == ()
    before = _scout_files(fx)
    assert suggestions.stale_for(fx.home_root, fx.target, fx.default_profile_id, _JOB, resolved=resolved) == ()
    assert suggestions.stale_for(fx.home_root, fx.target, fx.default_profile_id, _JOB, assessment_stale="older_prompt", resolved=resolved) == ("assessment_stale:older_prompt",)
    assert _scout_files(fx) == before
    # A printed line is edited in the master: the stored resume still prints the old words; the label says so. Nothing is re-picked.
    printed = set(_record(fx).printed())
    ids = _ids(fx)
    assert ids[PYTHON_LINE] in printed
    _edit(fx, ids[PYTHON_LINE], "Built Python services for seven years; cut p99 latency by 40%.")
    assert suggestions.stale_for(fx.home_root, fx.target, fx.default_profile_id, _JOB) == ("picked_line_changed",)
    assert _scout_files(fx) == before
    assert PYTHON_LINE in read_tailored_resume(_resume_path(fx)).markdown


# --- OD1: a must-have confirmed unmet --------------------------------------------------------------------------------------


def test_an_unmet_askable_row_holds_the_resume_and_the_job_reads_has_gap(fx: PostingsFixture, caplog: pytest.LogCaptureFixture) -> None:
    stored, _prompt = _assess(fx, _v9_answer(fx, kubernetes="unmet", said="matched_above_threshold"))
    assert not caplog.records, caplog.text
    kubernetes = stored.result.matrix[1]
    assert kubernetes.requirement == "Kubernetes" and kubernetes.status.value == "unmet"
    # The model's verdict is stored as it came; the gate is what every reader uses.
    assert stored.result.verdict.value == "matched_above_threshold" and stored.result.pick is None
    assert stored.resume_gate.to_json() == {"decision": "hold_unmet", "reasons": [{"code": "askable_unmet", "requirement": kubernetes.id}]}
    assert not _resume_path(fx).exists()  # no resume until the user asks for a draft
    record = _record(fx)
    assert record.selection is None and record.proposed is None and record.selection_error is None
    assert record.gate == {"decision": "hold_unmet", "ready": False, "reasons": [{"code": "askable_unmet", "requirement": kubernetes.id}]}
    assert [item.kind for item in record.suggestions] == ["reword", "gap"]
    # The job state, and the posting read model's row.
    sources = job_state.JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.base.gig.resolved)
    state = sources.state_for(_JOB, profile_id=fx.default_profile_id)
    assert state.state == "has_gap" and state.next_events == ("applied",)
    postings.refresh(fx.home_root, fx.target, now=NOW)
    store = postings.open_store(fx.home_root, fx.target)
    try:
        assert [row.state for row in store.postings(live=False) if row.profile_id == fx.default_profile_id and row.job == _JOB] == ["has_gap"]
    finally:
        store.close()


# --- N3b (decision #11): an unresolved must-have holds and asks ---------------------------------------------------------------


def test_an_unclear_must_have_the_model_never_asks_about_is_stored_pending_with_the_rows_own_question(fx: PostingsFixture, caplog: pytest.LogCaptureFixture) -> None:
    # Kubernetes (askable) is unclear and the only question is Helm's (one of a list); the model says matched, both times.
    stored, _prompt = _assess(fx, _v9_answer(fx, kubernetes="unclear", said="matched_above_threshold"))
    assert not caplog.records, caplog.text
    kubernetes = stored.result.matrix[1]
    assert kubernetes.requirement == "Kubernetes" and kubernetes.status.value == "unclear" and kubernetes.requirement_class.value == "askable"
    # Stored: pending, the gate holds for the answer, and the row's own question is there, as a structured question.
    question_id = f"requirement:{kubernetes.id.replace('-', '.')}"
    asked = "The posting asks for: Kubernetes. Do you have this? Say where."
    assert (stored.result.verdict.value, stored.resume_gate.decision) == ("pending_user_answers", "hold_question")
    assert stored.resume_gate.to_json() == {"decision": "hold_question", "reasons": [{"code": "question_open", "requirement": kubernetes.id}]}
    assert stored.result.pick is None
    # The one retry was spent, naming the row by the id its stored row carries; the assessment is NOT failed.
    prompts = fx.base.model.assess_prompts
    assert len(prompts) == 2 and f"unclear mandatory row {kubernetes.id} has no question" in prompts[1] and "has no question" not in prompts[0]
    assert [question.to_json() for question in stored.result.structured_questions] == [
        HELM_QUESTION, {"question_id": question_id, "question": asked, "requirement": "Kubernetes"},
    ]
    assert stored.result.questions == (HELM_QUESTION["question"], asked)
    reread = AssessResponse.from_json(parse_json_bytes(_assessment_path(fx).read_bytes()))
    assert reread.result == stored.result and reread.resume_gate == stored.resume_gate
    # No resume is made under the hold; the record says why; the job reads "needs your answers".
    assert not _resume_path(fx).exists()
    record = _record(fx)
    assert record.selection is None and record.gate == {"decision": "hold_question", "ready": False, "reasons": [{"code": "question_open", "requirement": kubernetes.id}]}
    sources = job_state.JobStateSources(home_root=fx.home_root, target=fx.target, resolved=fx.base.gig.resolved)
    assert sources.state_for(_JOB, profile_id=fx.default_profile_id).state == "needs_answers"
    # The answers store takes the id as it is, and the answer is found under it again.
    record_answer(home_root=fx.home_root, requested_target=fx.target, question_id=question_id, prompt=asked, answer="Three years running EKS clusters at Acme.")
    assert read_answers(home_root=fx.home_root, requested_target=fx.target)[question_id].answer == "Three years running EKS clusters at Acme."
    # Assessed again: the same row, the same question id.
    again, _prompt = _assess(fx, _v9_answer(fx, kubernetes="unclear", said="matched_above_threshold"))
    assert again.result.matrix[1].id == kubernetes.id and [question.question_id for question in again.result.structured_questions] == ["tooling:helm", question_id]
    # An optional row left unclear with no question (Helm, were its question dropped) is never asked about and never holds: test_resume_gate.


def test_a_first_assessments_gap_suggestion_that_names_its_row_by_words_is_stored_with_the_rows_id(fx: PostingsFixture, caplog: pytest.LogCaptureFixture) -> None:
    # Orchestrator #14: the rows of a first assessment have no id when the model answers; it names the row by its words.
    answer = json.loads(_v9_answer(fx))
    answer["suggestions"].append({"kind": "gap", "requirement": REQUIREMENTS[3], "why": "No line says you wrote Helm charts."})
    stored, _prompt = _assess(fx, json.dumps(answer))
    assert not caplog.records, caplog.text
    helm = stored.result.matrix[3]
    assert helm.requirement == REQUIREMENTS[3] and helm.id is not None
    # Kept (it names no line), with the id the stored row carries; in the assessment and in the job's suggestion record.
    assert [(item.kind, item.line, item.requirement) for item in stored.result.structured_suggestions][-1] == ("gap", None, helm.id)
    assert [(item.kind, item.requirement, item.why) for item in _record(fx).suggestions][-1] == ("gap", helm.id, "No line says you wrote Helm charts.")
    assert stored.result.suggestions[-1] == "No line says you wrote Helm charts."


# --- A6: the user's resume is never replaced ---------------------------------------------------------------------------------


def test_the_picks_own_resume_is_replaced_and_a_resume_the_user_changed_waits_beside_a_proposal(fx: PostingsFixture, caplog: pytest.LogCaptureFixture) -> None:
    _assess(fx, _v9_answer(fx))
    first = _record(fx)
    # Assessed again with nothing touched: the pick's own resume is replaced by the new selection.
    _assess(fx, _v9_answer(fx))
    second = _record(fx)
    assert second.proposed is None and second.selection is not None and second.selection["made_at"] >= first.selection["made_at"]
    assert second.created_at == first.created_at and not suggestions.proposed_resume_path(Path(second.stored_path)).exists()
    assert [item.id for item in second.suggestions] == ["sg-3", "sg-4"]  # the assessment's open ones are replaced; an id is never used twice
    # The user (or their agent) changes the job resume.
    mine = replace(read_tailored_resume(_resume_path(fx)), edited=TailorEdit("operator", "2026-10-05T12:00:00Z"))
    save_tailor_response(mine, home_root=fx.home_root)
    kept = _resume_path(fx).read_bytes()
    _assess(fx, _v9_answer(fx))
    assert not caplog.records, caplog.text
    third = _record(fx)
    sibling = suggestions.proposed_resume_path(Path(third.stored_path))
    assert _resume_path(fx).read_bytes() == kept  # never replaced automatically
    assert third.selection == second.selection and third.proposed is not None and third.proposed["resume"]["stored_path"] == str(sibling)
    assert sibling.is_file() and third.proposed["picked_by"] == "code" and third.gate["decision"] == "suggest"
    # Dismissed: the proposal goes, the user's resume stays.
    dismissed = suggestions.dismiss_proposed(fx.home_root, fx.target, fx.default_profile_id, _JOB, now="2026-10-05T13:00:00Z")
    assert dismissed is not None and dismissed.proposed is None and not sibling.exists() and _resume_path(fx).read_bytes() == kept
    with pytest.raises(suggestions.SuggestionError) as nothing:
        suggestions.use_proposed(fx.home_root, fx.target, fx.default_profile_id, _JOB, now="2026-10-05T13:00:00Z")
    assert nothing.value.code == "no_proposed_resume"
    # Proposed again, and taken: the one explicit step that replaces the job resume.
    _assess(fx, _v9_answer(fx))
    taken = suggestions.use_proposed(fx.home_root, fx.target, fx.default_profile_id, _JOB, now="2026-10-05T14:00:00Z")
    resume = read_tailored_resume(_resume_path(fx))
    assert resume.edited is None and resume.producer.callable == "scout.pick" and resume.updated_at == "2026-10-05T14:00:00Z"
    assert taken.proposed is None and not sibling.exists() and taken.selection["resume"]["stored_path"] == str(_resume_path(fx))
    assert suggestions.is_replaceable(resume, taken) and not suggestions.is_replaceable(mine, taken)


# --- with a template that explains ids (step 3 writes the real one) -----------------------------------------------------------


def test_with_ids_in_the_prompt_the_models_pick_is_settled_and_the_next_assessment_answers_the_listed_rows(
    fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_v9_template(monkeypatch)
    ids = _ids(fx)
    stored, prompt = _assess(fx, _v9_answer(fx))
    assert not caplog.records, caplog.text
    # The RESUME block shows the ids, the prompt asks for a pick of a size, and (a first assessment) carries no list.
    assert all(f"{text} <!-- id:{ids[text]} -->" in prompt for text in (PYTHON_LINE, TERRAFORM_LINE, KUBERNETES_LINE, OWN_LINE))
    assert "PICK: return " in prompt and "REQUIREMENTS (" not in prompt
    # The rows say which lines they relied on; a source the prompt did not offer was dropped.
    assert [row.sources for row in stored.result.matrix] == [(ids[PYTHON_LINE],), (ids[KUBERNETES_LINE],), (ids[TERRAFORM_LINE],), ()]
    assert stored.result.pick is not None and stored.result.pick.lines == (ids[OWN_LINE], ids[TERRAFORM_LINE], ids[PYTHON_LINE], ids[KUBERNETES_LINE])
    record = _record(fx)
    selection = record.selection
    assert selection is not None and (selection["picked_by"], selection["fallback"]) == ("model", None)
    assert selection["model_pick"]["lines"] == list(stored.result.pick.lines) and selection["problems"] == [{"code": "summary_by_code", "id": None}]
    assert {row.id: (row.coverage, row.in_resume) for row in record.requirements if row.status == "met"} == {
        stored.result.matrix[0].id: ("kept", (ids[PYTHON_LINE],)), stored.result.matrix[1].id: ("kept", (ids[KUBERNETES_LINE],)),
        stored.result.matrix[2].id: ("kept", (ids[TERRAFORM_LINE],)),
    }
    assert record.gate["ready"] is True
    resume = read_tailored_resume(_resume_path(fx))
    assert resume.selection.picked_by == "model" and resume.selection.candidates == "evidence" and all(text in resume.markdown for text in (PYTHON_LINE, OWN_LINE))
    # A LATER assessment of the same posting text gets the list and must return exactly its ids.
    listed = requirements_list.read_list(fx.home_root, fx.target, stored.posting_sha256)
    before = _assessment_path(fx).read_bytes()
    with pytest.raises(QuickAssessError) as refused:
        _assess(fx, _v9_answer(fx))  # no ids on its rows
    assert refused.value.code == "model_output_invalid" and "matrix must hold exactly the 4 listed requirement ids" in str(refused.value)
    assert len(fx.base.model.assess_prompts) == 2 and _assessment_path(fx).read_bytes() == before  # the one retry, then nothing is stored
    again, prompt = _assess(fx, _v9_answer(fx, row_ids=listed.ids()))
    block = prompt.split(assessment_core.REQUIREMENTS_BLOCK_HEADER, 1)[1]
    assert all(f"{row.id} | {row.requirement_class} | {row.requirement} | Requirements: {row.requirement}" in block for row in listed.rows)
    assert again.requirements_ref == stored.requirements_ref and requirements_list.comparable(again.requirements_ref, stored.requirements_ref)
    assert [row.id for row in again.result.matrix] == list(listed.ids()) and requirements_list.read_list(fx.home_root, fx.target, stored.posting_sha256) == listed


def test_a_v9_assessment_is_stale_for_its_resume_by_its_sources_not_by_shared_words(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_v9_template(monkeypatch)
    ids = _ids(fx)
    stored, _prompt = _assess(fx, _v9_answer(fx))

    def served() -> dict[str, object]:
        return BasisCheck(home_root=fx.home_root, target=fx.target).served(stored)

    assert served() == {"basis_stale": False}
    # A line no row names is edited, in words the Python row's quote shares: the assessment still stands.
    _edit(fx, ids[OWN_LINE], "Built Python services for six years behind 40 product teams.")
    assert served() == {"basis_stale": False}
    # The line the Terraform row names is edited. Its quote ("Infrastructure as code") shares no word with it: only the source says so.
    _edit(fx, ids[TERRAFORM_LINE], "Wrote the Terraform modules every Kubernetes cluster and database is built from.")
    assert served() == {
        "basis_stale": True, "basis_stale_reason": "resume_changed",
        "basis_stale_resume": [{"change": "line_changed", "requirement": REQUIREMENTS[2]}],
    }
