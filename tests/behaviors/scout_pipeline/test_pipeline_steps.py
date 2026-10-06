"""PL3 + PL4 on the END outcome: one job through ``gigai scout pipeline process``.

Synthetic gig, answer, story and posting (``tests/support/pipeline_fixtures``);
the model is a scripted binding, so every call is counted. What is asserted
is what a user or an agent reads afterwards: the tailored resume, the
assessment against it, the Scout ATS record and the Scout label through
their read paths, the base assessment untouched, the calls made and the
``step_run`` rows written.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from click.testing import CliRunner
import pytest

from gigai.scout import story_bank
from gigai.scout.pipeline import steps
from gigai.scout.pipeline.runner import DRAIN_RAN, WAIT_DAILY_CAP, PipelineRunner, pipeline_status
from gigai.scout.pipeline.settings import PipelineSetting
from gigai.scout.pipeline.store import CAP_PIPELINE_CALLS, STEPS, PipelineStore
from gigai.scout.quick_assess import list_quick_assessments, quick_assess_path, read_quick_assessment, read_tailored_variant
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import list_tailored_resumes

from tests.support.answers_stories_fixtures import config
from tests.support.pipeline_fixtures import (
    ANSWER_CHANGED,
    EMAIL_SHAPE,
    JOB,
    MARKERS,
    PHONE_SHAPE,
    QUESTION_ID,
    PipelineFixture,
    build_pipeline_fixture,
    output_files,
)


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv("GIGAI_SCOUT_PIPELINE", "on")
    return build_pipeline_fixture(tmp_path, monkeypatch)


def _process(fx: PipelineFixture, *extra: str) -> dict[str, object]:
    result = CliRunner().invoke(scout_group, fx.cli("process", JOB, *extra))
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _runs(fx: PipelineFixture):
    store = PipelineStore(fx.db)
    try:
        return store.runs(profile_id=fx.profile_id, job=JOB)
    finally:
        store.close()


def _states(fx: PipelineFixture) -> dict[str, str]:
    store = PipelineStore(fx.db)
    try:
        return {step.name: step.state for step in store.steps(profile_id=fx.profile_id, job=JOB)}
    finally:
        store.close()


def _runner(fx: PipelineFixture, **options) -> PipelineRunner:
    return PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None, **options)


# --- (a) process runs the whole pipeline; every output reads back; the base assessment is untouched ---


def test_process_runs_tailor_then_reassess_and_ats_then_label_and_every_output_reads_back(fx: PipelineFixture) -> None:
    base_path = quick_assess_path(fx.home_root, fx.target, fx.profile_id, JOB)
    base_before = base_path.read_bytes()

    answer = _process(fx)

    assert answer["enqueue"]["result"] == "enqueued"
    assert answer["drain"]["state"] == DRAIN_RAN
    ran = [(step["name"], step["outcome"]) for step in answer["drain"]["steps"]]
    assert ran[0] == ("tailor", "done") and ran[-1] == ("label", "done")
    assert sorted(ran[1:3]) == [("ats", "done"), ("reassess", "done")]  # after tailor, before label
    assert _states(fx) == dict.fromkeys(STEPS, "done")
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (1, 1) and answer["drain"]["model_calls"] == 2

    # The tailored resume, through the tailored-resume store's own read.
    (tailored,) = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)
    assert Path(tailored.markdown_path).read_text(encoding="utf-8") == tailored.markdown
    assert "Two years on GCP." in tailored.markdown and tailored.job.job_identity == JOB
    # Tailored from the resume, the user's answers AND the story that matches the posting.
    assert f"A {QUESTION_ID}: " in fx.model.tailor_prompts[0] and "A story:" in fx.model.tailor_prompts[0]
    assert QUESTION_ID in tailored.sources.answers and any(key.startswith("story:") for key in tailored.sources.answers)

    # The variant: stored beside the base assessment, which is the job's verdict and is byte-for-byte as it was.
    variant = read_tailored_variant(fx.home_root, fx.target, fx.profile_id, JOB)
    assert variant is not None and variant.result.verdict.value == "matched_above_threshold"
    assert Path(variant.stored_path).parent.parent.name == "quick_assess_tailored"
    assert base_path.read_bytes() == base_before
    base = read_quick_assessment(fx.home_root, fx.target, fx.profile_id, JOB)
    assert [item.stored_path for item in list_quick_assessments(fx.home_root, fx.target)] == [base.stored_path]  # the variant is not a second job
    assert "Two years on GCP." in fx.model.assess_prompts[0] and "<!--" not in fx.model.assess_prompts[0]  # assessed against the tailored text

    # The Scout ATS record and the Scout label.
    ats = steps.read_ats(fx.home_root, fx.target, fx.profile_id, JOB)
    assert ats is not None and isinstance(ats["result"]["score"], int) and ats["result"]["line"].startswith("Scout ATS ")
    assert "Python" in ats["keywords"]["must"]
    label = steps.read_label(fx.home_root, fx.target, fx.profile_id, JOB)
    assert label is not None and label["name"] == "Scout label"
    assert (label["label"], label["reasons"]) == ("recommended", [])
    assert "ready" not in json.dumps(label).lower() and "not a prediction" in label["wording"].lower()
    # "50 -> 100 after tailoring": the base assessment's share of requirements met, then the tailored one's.
    assert (label["requirements_met"]["base"]["percent"], label["requirements_met"]["tailored"]["percent"]) == (50, 100)

    # The same, as the CLI status reports it.
    outputs = answer["status"]["outputs"]
    assert outputs["label"]["label"] == "recommended" and outputs["ats"]["score"] == ats["result"]["score"]
    assert outputs["base_assessment"]["requirements_met"]["percent"] == 50
    assert outputs["tailored_assessment"]["requirements_met"]["percent"] == 100
    plain = CliRunner().invoke(scout_group, [*fx.cli("status", "--job", JOB)[:-1]])
    assert plain.exit_code == 0, plain.output
    assert "Requirements met: 50 -> 100 after tailoring" in plain.output and "Scout label: recommended" in plain.output


# --- (h) metrics for every step that called a model ---------------------------------------------------


def test_every_model_step_has_a_step_run_with_metrics_and_a_model_call_row(fx: PipelineFixture) -> None:
    _process(fx)

    runs = {run.name: run for run in _runs(fx)}
    assert set(runs) == set(STEPS) and all(run.outcome == "ok" for run in runs.values())
    for name in ("tailor", "reassess"):
        run = runs[name]
        assert (run.adapter, run.model, run.input_tokens, run.output_tokens) == ("ollama_local", "fixture-model", 10, 20)
        assert run.lane == "ollama" and run.seconds >= 0 and run.cost_status == "unavailable"
    for name in ("ats", "label"):
        assert (runs[name].lane, runs[name].model, runs[name].input_tokens) == ("local", None, None)

    # The same calls through E's CallMeter: one model_call row each, of the kind every other call of it has.
    store = PipelineStore(fx.db)
    try:
        totals = {row.kind: row for row in store.call_totals()}
    finally:
        store.close()
    assert totals["tailor"].calls == 1 and totals["tailor"].input_tokens == 10
    assert totals["assess"].calls == 2  # the base assessment (the fixture's) and the one against the tailored resume


# --- (b) idempotent; a changed answer re-opens tailor and what depends on it ---------------------------


def test_a_second_process_with_nothing_changed_makes_no_call_and_writes_no_step_run(fx: PipelineFixture) -> None:
    _process(fx)
    calls, rows = fx.model.calls, len(_runs(fx))
    files = {path: path.read_bytes() for path in output_files(fx) if path.suffix in (".json", ".md")}

    again = _process(fx)

    assert again["enqueue"]["result"] == "noop_unchanged" and again["drain"]["steps"] == []
    assert fx.model.calls == calls and len(_runs(fx)) == rows
    assert {path: path.read_bytes() for path in files} == files


def test_a_changed_answer_reopens_tailor_and_downstream_and_an_unchanged_resume_short_circuits_ats(fx: PipelineFixture) -> None:
    _process(fx)
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (1, 1)
    ats_before = steps.read_ats(fx.home_root, fx.target, fx.profile_id, JOB)["updated_at"]

    story_bank.save_answer(home_root=fx.home_root, target=fx.target, question_id=QUESTION_ID, answer=ANSWER_CHANGED)
    changed = _process(fx)

    assert changed["enqueue"]["result"] == "enqueued"
    outcomes = {step["name"]: step["outcome"] for step in changed["drain"]["steps"]}
    # Tailor ran again; the re-assessment reads the answers, so it ran too; the markdown came out the same, so the
    # ATS step finished on its digest without running, and so did the label.
    assert outcomes == {"tailor": "done", "reassess": "done", "ats": "unchanged", "label": "unchanged"}
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (2, 2)
    assert ANSWER_CHANGED in fx.model.tailor_prompts[1]
    assert steps.read_ats(fx.home_root, fx.target, fx.profile_id, JOB)["updated_at"] == ats_before
    assert _states(fx) == dict.fromkeys(STEPS, "done")

    # --force tailors again with nothing changed; everything downstream finishes on its digest.
    forced = _process(fx, "--force")
    assert {step["name"]: step["outcome"] for step in forced["drain"]["steps"]} == {
        "tailor": "done", "reassess": "unchanged", "ats": "unchanged", "label": "unchanged",
    }
    assert (len(fx.model.tailor_prompts), len(fx.model.assess_prompts)) == (3, 2)


# --- (f) the daily cap: the call over it waits, it does not fail ---------------------------------------


def test_the_41st_model_call_of_the_day_waits_with_a_clear_status_and_runs_the_next_day(fx: PipelineFixture) -> None:
    day = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
    clock = [1_800_000_000.0]
    steps.enqueue_job(fx.profile_id, JOB, home_root=fx.home_root, target=fx.target)
    store = PipelineStore(fx.db)
    assert store.spend(CAP_PIPELINE_CALLS, "2026-10-02", 40) is True  # 40 calls already made today
    store.close()
    assert PipelineSetting().max_model_calls_per_day == 40

    first = _runner(fx, clock=lambda: clock[0], now=lambda: day).drain()

    assert [(step["name"], step["outcome"], step.get("waiting")) for step in first.steps] == [("tailor", "waiting", WAIT_DAILY_CAP)]
    assert fx.model.calls == 0 and first.model_calls == 0
    assert _runs(fx) == ()  # not an attempt: nothing failed
    status = pipeline_status(fx.home_root, fx.target, profile_id=fx.profile_id, job=JOB, clock=lambda: clock[0], now=lambda: day, busy=lambda: None)
    assert status["calls_today"] == {"day": "2026-10-02", "used": 40, "limit": 40}
    (tailor,) = [step for step in status["steps"] if step["name"] == "tailor"]
    assert (tailor["state"], tailor["waiting"], tailor["attempts"]) == ("ready", "daily_cap_reached", 0) and tailor["retry_at"]
    # Still waiting later the same day; a second drain claims nothing.
    assert _runner(fx, clock=lambda: clock[0], now=lambda: day).drain().steps == []

    clock[0] += 10 * 3600  # past local midnight
    tomorrow = day + timedelta(hours=10)
    second = _runner(fx, clock=lambda: clock[0], now=lambda: tomorrow).drain()

    assert {step["name"]: step["outcome"] for step in second.steps} == dict.fromkeys(STEPS, "done")
    store = PipelineStore(fx.db)
    assert store.used(CAP_PIPELINE_CALLS, "2026-10-03") == 2 and store.used(CAP_PIPELINE_CALLS, "2026-10-02") == 40
    store.close()


def test_a_step_that_fails_before_its_call_gives_its_call_back_and_a_retry_inside_a_step_is_counted(fx: PipelineFixture) -> None:
    day = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
    fx.model.tailored = {"sections": []}  # invalid twice: the tailor step asks, retries once inside, then fails
    steps.enqueue_job(fx.profile_id, JOB, home_root=fx.home_root, target=fx.target)

    result = _runner(fx, now=lambda: day).drain()

    # One attempt, two calls; the queue gives model_output_invalid one more attempt, which the same drain takes.
    assert [(step["name"], step.get("error_code"), step["model_calls"]) for step in result.steps] == [
        ("tailor", "model_output_invalid", 2), ("tailor", "model_output_invalid", 2),
    ]
    store = PipelineStore(fx.db)
    assert store.used(CAP_PIPELINE_CALLS, "2026-10-02") == 4
    assert store.step(fx.profile_id, JOB, "tailor").state == "failed"
    runs = store.runs(profile_id=fx.profile_id, job=JOB)
    assert [(run.outcome, run.error_code, run.input_tokens) for run in runs] == [("error", "model_output_invalid", 20)] * 2
    store.close()


# --- (g) no contact data in any pipeline output or in pipeline.sqlite ----------------------------------


def test_no_contact_data_reaches_a_pipeline_output_the_queue_file_or_the_model(fx: PipelineFixture) -> None:
    _process(fx)

    files = output_files(fx)
    names = {path.parent.parent.name for path in files if path.suffix == ".json"}
    assert {"resumes", "quick_assess_tailored", "ats", "label"} <= names and fx.db in files
    for path in files:
        data = path.read_bytes()
        assert [marker for marker in MARKERS if marker.encode() in data] == [], path
        if path.suffix in (".json", ".md"):
            text = data.decode("utf-8")
            assert EMAIL_SHAPE.search(text) is None and PHONE_SHAPE.search(text) is None, path
    # Every value of every table of the queue file.
    connection = sqlite3.connect(fx.db)
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in connection.execute(f"SELECT * FROM {table}").fetchall():
            for value in row:
                if isinstance(value, str):
                    assert not any(marker in value for marker in MARKERS) and " " not in value, (table, value)
                    assert EMAIL_SHAPE.search(value) is None and PHONE_SHAPE.search(value) is None, (table, value)
    connection.close()
    # And what the model was sent: the resume without its name and contact lines.
    for prompt in (*fx.model.tailor_prompts, *fx.model.assess_prompts):
        assert [marker for marker in MARKERS if marker in prompt] == []


def test_a_tailoring_that_holds_a_contact_shape_fails_its_step_and_nothing_downstream_runs(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import tailored_resume

    real = tailored_resume.render_markdown
    monkeypatch.setattr(tailored_resume, "render_markdown", lambda result: real(result) + "\n- reach me at someone@zq.example.invalid\n")

    answer = _process(fx)

    assert [(step["name"], step.get("error_code")) for step in answer["drain"]["steps"]] == [("tailor", "contact_data_found")]
    assert _states(fx) == {"tailor": "failed", "reassess": "blocked", "ats": "blocked", "label": "blocked"}
    assert steps.read_label(fx.home_root, fx.target, fx.profile_id, JOB) is None


# --- what cannot enter the pipeline; the label rule ----------------------------------------------------


def test_a_job_with_no_assessment_cannot_enter_the_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_pipeline_fixture(tmp_path, monkeypatch, base=False)

    result = CliRunner().invoke(scout_group, fx.cli("process", JOB))

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "assessment_missing"
    assert fx.model.calls == 0 and list((fx.home_root / "scout").rglob("pipeline.sqlite")) == []  # nothing was queued


def test_the_label_needs_attention_when_the_tailored_assessment_still_asks(fx: PipelineFixture) -> None:
    fx.model.assessed = json.dumps(
        {
            "verdict": "pending_user_answers",
            "matrix": [{"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
                       {"requirement": "Terraform", "class": "askable", "status": "unclear", "resume_evidence": []}],
            "suggestions": [],
            "questions": [{"question_id": "tooling:terraform", "question": "Have you used Terraform?", "requirement": "Terraform"}],
            "not_a_match_reason": None,
        }
    )

    _process(fx)

    label = steps.read_label(fx.home_root, fx.target, fx.profile_id, JOB)
    assert label["label"] == "needs_attention"
    assert label["reasons"] == ["tailored_assessment_not_matched", "open_questions"]


def test_a_lone_question_on_a_one_of_a_list_row_does_not_hold_the_label(fx: PipelineFixture) -> None:
    """0110-10-03: the tailored assessment is matched with one minor gap (Helm): asked, counted, and no reason for attention."""

    fx.model.assessed = json.dumps(
        {
            "verdict": "pending_user_answers",  # what a model that learned "any question -> pending" says
            "matrix": [{"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
                       {"requirement": "Docker", "class": "list_item", "status": "met", "resume_evidence": ["Docker"]},
                       {"requirement": "Helm", "class": "list_item", "status": "unclear", "resume_evidence": []}],
            "suggestions": [],
            "questions": [{"question_id": "tooling:helm", "question": "Have you used Helm?", "requirement": "Helm"}],
            "not_a_match_reason": None,
        }
    )

    _process(fx)

    label = steps.read_label(fx.home_root, fx.target, fx.profile_id, JOB)
    assert (label["label"], label["reasons"]) == ("recommended", [])
    assert label["tailored_verdict"] == "matched_above_threshold" and label["open_questions"] == 1  # the question is still there


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"verdict": "matched_above_threshold", "open_questions": 0, "ats": 80, "min_ats": 0, "stale_reason": None}, ("recommended", [])),
        ({"verdict": "matched_above_threshold", "open_questions": 0, "ats": 61, "min_ats": 70, "stale_reason": None}, ("needs_attention", ["ats_below_minimum"])),
        ({"verdict": "not_a_match", "open_questions": 0, "ats": 90, "min_ats": 0, "stale_reason": None}, ("needs_attention", ["tailored_assessment_not_matched"])),
        ({"verdict": "matched_above_threshold", "open_questions": 0, "ats": 90, "min_ats": 0, "stale_reason": "settings_changed"}, ("needs_attention", ["base_assessment_stale"])),
    ],
)
def test_the_scout_label_rule(facts: dict[str, object], expected: tuple[str, list[str]]) -> None:
    assert steps.label_for(**facts) == expected  # type: ignore[arg-type]
