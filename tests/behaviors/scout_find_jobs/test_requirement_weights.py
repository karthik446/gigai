"""0110-10-03 (a) and (b): a requirement weighs what the posting gave it, and no stated requirement is dropped in silence.

The END outcomes, on a synthetic home, through the product's own assessment
path (``run_quick_assessment``, the scripted fixture model) and its own reads
(``GET /api/jobs``'s aggregate and the Jobs grid's row):

(a) A posting names "Docker, Helm, and Kubernetes" in one sentence and the
    resume shows everything but Helm. Before: the job waited at "needs your
    answers" on Helm alone. After: the job is MATCHED, it says "1 minor gap:
    Helm", and the Helm question is still offered. Two unknown tools of a
    list, or one unknown must-have, still wait.
(b) A posting states 16 requirements. Before: an answer with more than 12
    rows was refused (and the prompt told the model to drop rows to fit).
    After: all 16 are kept, must-haves first; an answer past the bound keeps
    the first 40 and says "+N not shown".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout import posting_search, postings, requirement_weights as weights
from gigai.scout.assessment_core import AssessContext, AssessJob, load_assess_instructions, render_assess_prompt
from gigai.scout.find_jobs.api.agent_routes import AgentRoutesMixin
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessmentBody, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import RequirementClass, normalize_url
from gigai.scout.quick_assess import run_quick_assessment

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

_SLUG = "harborlight"
_URL = job_url(_SLUG, 1)
_POSTING = (
    "Harborlight is hiring a Staff AI Engineer. The ideal candidate will have: 8+ years of experience in infrastructure "
    "engineering; strong programming skills in Python or Go; experience with AWS, GCP, or similar cloud platforms, as well "
    "as Docker, Helm, and Kubernetes. Remote within the United States."
)


class _Routes(AgentRoutesMixin):
    """The one-job route's own aggregate, with no server around it."""


def _row(requirement: str, klass: str, status: str = "met") -> dict[str, object]:
    return {"requirement": requirement, "class": klass, "status": status, "resume_evidence": ["ten years on it"] if status == "met" else []}


def _ask(tool: str) -> dict[str, object]:
    return {"question_id": f"tool:{tool.lower()}", "question": f"Have you used {tool}?", "requirement": tool}


def _answer(verdict: str, rows: list[dict[str, object]], questions: list[dict[str, object]] | None = None) -> str:
    return json.dumps({"verdict": verdict, "matrix": rows, "questions": questions or [], "not_a_match_reason": None})


_BASE = [_row("8+ years of experience in infrastructure engineering", "hard"), _row("Python or Go", "askable"), _row("Cloud platform (AWS, GCP, or similar)", "askable")]


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    fx.seed(_SLUG, [lever_job(_SLUG, 1, text=_POSTING)], seen_at=days_ago(1))
    postings.refresh(fx.home_root, fx.target, now=NOW)
    return fx


def _assess(fx: PostingsFixture, answer: str):
    """The job assessed by its URL with the model answering ``answer``; ``(the stored assessment, GET /api/jobs)``."""

    fx.base.model.assessed = answer
    stored = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root),
    )
    postings.refresh(fx.home_root, fx.target, now=NOW)
    job = _Routes()._job_aggregate(normalize_url(_URL), home_root=fx.home_root, target=fx.target)
    assert job is not None
    return stored, job


# --- (a) weights: the END outcome ---------------------------------------------------------------------------


def test_one_unknown_tool_of_a_list_is_a_minor_gap_on_a_matched_job_and_its_question_is_still_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    rows = [*_BASE, _row("Docker", "list_item"), _row("Helm", "list_item", "unclear"), _row("Kubernetes", "list_item")]

    # The model asks about Helm and, as every earlier prompt taught, calls the job pending.
    stored, job = _assess(fx, _answer("pending_user_answers", rows, [_ask("Helm")]))

    assert stored.result.verdict.value == "matched_above_threshold"
    assert job["job_state"]["state"] == "matched"
    assessment = job["assessments"][0]
    assert (assessment["verdict"], assessment["minor_gaps"], assessment["minor_gap_text"]) == ("matched_above_threshold", ["Helm"], "1 minor gap: Helm")
    # The question is still offered: on the assessment, in the job's open questions and on the grid's row.
    assert [question["requirement"] for question in job["open_questions"]] == ["Helm"]
    grid = job["index_posting"]
    assert (grid["state"], grid["minor_gaps"], grid["minor_gap_text"]) == ("matched", ["Helm"], "1 minor gap: Helm")
    assert [question["question"] for question in grid["open_questions"]] == ["Have you used Helm?"]
    assert grid["score_text"].startswith("Matched")
    # The grid's text output says it in one line.
    listed = posting_search.render(posting_search.search_postings(fx.home_root, fx.target, now=NOW))
    assert "Matched" in listed and "1 minor gap: Helm" in listed


def test_two_unknown_tools_of_a_list_still_wait_for_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    rows = [*_BASE, _row("Docker", "list_item"), _row("Helm", "list_item", "unclear"), _row("Istio", "list_item", "unclear")]

    # Even when the model calls it matched: more than one unknown is not minor.
    stored, job = _assess(fx, _answer("matched_above_threshold", rows, [_ask("Helm"), _ask("Istio")]))

    assert stored.result.verdict.value == "pending_user_answers"
    assert job["job_state"]["state"] == "needs_answers"
    assert job["assessments"][0]["minor_gap_text"] == "2 minor gaps: Helm, Istio"
    assert len(job["open_questions"]) == 2


def test_an_unknown_must_have_still_waits_whatever_else_is_minor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    rows = [*_BASE, _row("Apache Kafka", "askable", "unclear"), _row("Helm", "list_item", "unclear"), _row("Rust", "nice_to_have", "unclear")]

    stored, job = _assess(fx, _answer("matched_above_threshold", rows, [_ask("Apache Kafka"), _ask("Helm")]))

    assert stored.result.verdict.value == "pending_user_answers"
    assert job["job_state"]["state"] == "needs_answers"
    # The must-have is no minor gap; the list item and the bonus are.
    assert job["assessments"][0]["minor_gaps"] == ["Helm", "Rust"]


def test_a_tool_the_candidate_says_they_lack_stays_a_minor_gap_on_a_matched_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    rows = [*_BASE, _row("Docker", "list_item"), _row("Helm", "list_item", "unmet"), _row("Kubernetes", "list_item")]

    stored, job = _assess(fx, _answer("matched_above_threshold", rows))

    assert stored.result.verdict.value == "matched_above_threshold" and job["job_state"]["state"] == "matched"
    assert job["open_questions"] == [] and job["assessments"][0]["minor_gap_text"] == "1 minor gap: Helm"


# --- (b) rows: the END outcome ------------------------------------------------------------------------------


def test_every_stated_requirement_is_kept_must_haves_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    # 16 rows, as a model writes them down the posting: bonuses and list items in between.
    rows = [
        _row("Experience with eBPF", "nice_to_have", "unclear"),
        *[_row(f"Qualification {number}", "askable") for number in range(1, 11)],
        _row("Docker", "list_item"),
        _row("Experience in an enterprise SaaS or cybersecurity software company", "nice_to_have"),
        _row("8+ years of experience", "hard"),
        _row("Mentor fellow engineers and influence technical direction", "askable"),
        _row("Kubernetes", "list_item"),
    ]

    stored, job = _assess(fx, _answer("matched_above_threshold", rows))

    matrix = stored.result.matrix
    assert len(matrix) == 16 and stored.result.rows_not_shown == 0
    assert [row.requirement_class.value for row in matrix] == ["hard", *["askable"] * 11, "list_item", "list_item", "nice_to_have", "nice_to_have"]
    assert {"Mentor fellow engineers and influence technical direction", "Experience in an enterprise SaaS or cybersecurity software company"} <= {row.requirement for row in matrix}
    assert job["job_state"]["state"] == "matched"
    assert job["assessments"][0]["rows_not_shown"] == 0 and job["index_posting"]["assessment"]["requirements"] == 16


def test_rows_past_the_bound_are_counted_and_said_never_dropped_in_silence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = _fixture(tmp_path, monkeypatch)
    rows = [*[_row(f"Bonus {number}", "nice_to_have") for number in range(1, 31)], *[_row(f"Qualification {number}", "askable") for number in range(1, 16)]]

    stored, job = _assess(fx, _answer("matched_above_threshold", rows))

    kept = stored.result.matrix
    assert len(kept) == weights.MAX_MATRIX_ROWS == 40 and stored.result.rows_not_shown == 5
    # Must-haves first: every one of the 15 is kept; what is not shown are the last bonuses.
    assert [row.requirement for row in kept[:15]] == [f"Qualification {number}" for number in range(1, 16)]
    assert kept[-1].requirement == "Bonus 25"
    assert job["assessments"][0]["rows_not_shown"] == 5 and job["index_posting"]["rows_not_shown"] == 5
    # The stored record round-trips with the count, and the CLI says it.
    assert AssessmentBody.from_json(stored.result.to_json()).rows_not_shown == 5
    shown = CliRunner().invoke(cli, ["scout", "assess", "--job-url", _URL, "--home", str(fx.home_root), "--target", str(fx.target)])
    assert shown.exit_code == 0, shown.output
    assert "+5 not shown" in shown.output


# --- the rule itself ------------------------------------------------------------------------------------------


def test_question_weights_and_the_settled_verdict() -> None:
    rows = [_row("8+ years", "hard"), _row("Kafka", "askable", "unclear"), _row("Helm", "list_item", "unclear"), _row("Istio", "list_item", "unclear")]
    helm, istio, kafka = _ask("Helm"), _ask("Istio"), _ask("Kafka")

    assert weights.question_weights(rows, [helm, kafka]) == (1, 1)
    assert weights.blocking_question_count(rows, [helm]) == 0
    assert weights.blocking_question_count(rows, [helm, istio]) == 2
    assert weights.blocking_question_count(rows, [kafka, helm]) == 2
    # The row is found by its requirement whatever the case and spacing; a question naming no row is a must-have's.
    assert weights.blocking_question_count(rows, [{"requirement": "  helm "}]) == 0
    assert weights.blocking_question_count(rows, [{"requirement": "Something else"}]) == 1
    assert weights.blocking_question_count(rows, [{"requirement": None}]) == 1

    matched, pending = weights.MATCHED, weights.PENDING
    assert weights.settled_verdict(pending, rows, [helm]) == matched
    assert weights.settled_verdict(matched, rows, [helm, istio]) == pending
    assert weights.settled_verdict(matched, rows, [kafka]) == pending
    # Nothing to settle: no question, another verdict, or a hard gap (the validator's to refuse).
    assert weights.settled_verdict(pending, rows, []) == pending
    assert weights.settled_verdict("not_a_match", rows, [helm]) == "not_a_match"
    assert weights.settled_verdict(pending, [_row("8+ years", "hard", "unmet"), *rows[1:]], [helm]) == pending
    # A row with no class is an old one: a must-have.
    assert weights.settled_verdict(matched, [{"requirement": "Helm", "status": "unclear", "resume_evidence": []}], [helm]) == pending


def test_minor_gaps_are_named_and_counted_in_one_line() -> None:
    rows = [_row("Kafka", "askable", "unclear"), _row("Helm", "list_item", "unclear"), _row("Istio", "list_item", "unmet"), _row("Rust", "nice_to_have", "unclear"), _row("Go", "nice_to_have", "unmet"), _row("Docker", "list_item")]

    assert weights.minor_gaps(rows) == ["Helm", "Istio", "Rust", "Go"]
    assert weights.minor_gap_text(["Helm"]) == "1 minor gap: Helm"
    assert weights.minor_gap_text(weights.minor_gaps(rows)) == "4 minor gaps: Helm, Istio, Rust +1 more"
    assert weights.minor_gap_text([]) is None
    # The same over stored rows (the contract's objects).
    stored = AssessmentBody.from_json({"matrix": rows, "suggestions": [], "questions": []}).matrix
    assert weights.minor_gaps(stored) == ["Helm", "Istio", "Rust", "Go"]
    assert [row.requirement for row in weights.must_haves_first(stored)] == ["Kafka", "Helm", "Istio", "Docker", "Rust", "Go"]


def test_an_old_result_reads_as_it_always_did() -> None:
    old = {"matrix": [{"requirement": "Python", "resume_evidence": [], "status": "met"}], "suggestions": [], "questions": []}
    parsed = AssessmentBody.from_json(old)

    assert parsed.rows_not_shown == 0 and parsed.to_json() == old  # byte for byte: nothing added at the defaults
    assert RequirementClass("list_item") is RequirementClass.LIST_ITEM
    with pytest.raises(Exception, match="rows_not_shown"):
        AssessmentBody.from_json({**old, "rows_not_shown": 0})


# --- the prompt says the same rule ----------------------------------------------------------------------------


def test_the_prompt_teaches_the_list_item_class_and_has_no_row_cap() -> None:
    prompt = render_assess_prompt(AssessJob("Staff AI Engineer", "Harborlight", "Remote", _POSTING), AssessContext(resume_text="ten years", visa_sponsorship_required=False))

    assert '"class": "hard|askable|list_item|nice_to_have"' in prompt
    assert "LIST_ITEM: one tool of a list of three or more" in prompt
    assert "or questions on two or more LIST_ITEM rows -> \"pending_user_answers\"" in prompt
    assert "one row for every requirement the posting states" in prompt
    assert "1 to 12 rows" not in prompt and "drop NICE_TO_HAVE rows first" not in load_assess_instructions()
    retry = render_assess_prompt(AssessJob("Staff AI Engineer", "Harborlight", "Remote", _POSTING), AssessContext(resume_text="ten years", visa_sponsorship_required=False), "questions has 44 items; at most 40 allowed")
    assert f'"at most {weights.MAX_MATRIX_ROWS} allowed" means the questions list broke OUTPUT BOUNDS' in retry
