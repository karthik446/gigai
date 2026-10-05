"""0.1.11 MODELPIN: the assessment asks for the evaluated model, falls back once, and says so when another model answered.

A FAKE ``claude`` on ``PATH`` (``tests/support/fake_claude.py``) and the real ``ClaudeCLIAdapter``, behind the real
``run_quick_assessment``; no live model call:

* Opus answers: asked for ``claude-opus-5-5``, stored with it, no notice, one call;
* the CLI refuses Opus: exactly ONE fallback call on the CLI's default, stored with the model that answered, the
  fallback recorded, the notice present, and the call meter counts both calls;
* a model set in the target's configuration is asked as set (never swapped on failure), and the notice rule is the
  same table's;
* the stored fields are optional: a record without them round-trips byte for byte;
* ``scout jobs assess`` (the batch), the agent brief and ``gigai doctor`` carry the same notice from the same table.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gigai.adapters.factory import resolve_model_adapter
from gigai.adapters.port import InvocationRequest, ModelInvocationError
from gigai.config import Endpoint, write_config_atomic
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.diagnostics import run_doctor
from gigai.scout import evaluated_models, quick_assess
from gigai.scout.assess_model import AssessModelPort
from gigai.scout.assessment_basis import BasisCheck
from gigai.scout.call_metrics import metrics_report
from gigai.scout.evaluated_models import RESULTS_PAGE, model_notice, notice_lines
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResponse, AssessResumeInput
from gigai.scout.find_jobs.contracts import ModelTarget
from gigai.scout.job_brief import YoursInputs, _render_yours, yours_part
from gigai.scout.postings import PostingText
from gigai.scout.quick_assess import QuickAssessError, quick_assess_path, run_quick_assessment
from gigai.scout.scout_new import _assess
from gigai.setup import build_config
from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import _JOB, _URL, _POSTING, _v8_answer, fx  # noqa: F401 - the fixture
from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.fake_claude import FAKE_CLAUDE_MODEL, calls, write_fake_claude
from tests.support.posting_fixtures import PostingsFixture

OPUS = "claude-opus-5-5"
NOTICE = "Assessed with {used}. GigAI's accuracy results are for {evaluated}; this assessment may be less accurate."


def _config(home: Path, model: str = "default"):
    return build_config(
        home_root=home, workpad_root=home.parent / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False,
        endpoints=(Endpoint(name="offline", adapter="deterministic"), Endpoint(name="claude", adapter="claude_cli")),
        model_targets=(
            ConfigModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ConfigModelTarget("claude-default", "claude", model, ("text",), 4096),
        ),
    )


def _fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, refuse: tuple[str, ...] = ()) -> Path:
    bin_dir = tmp_path / "fake-bin"
    record = write_fake_claude(bin_dir, refuse=refuse, answer_text=_v8_answer(), echo_model=True)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    # the pipeline fixture scripts the C1 seam: the real factory (and so the real adapter and the fake CLI) is put back
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve_model_adapter)
    return record


def _assess_on_claude(fx: PostingsFixture, model: str = "default") -> AssessResponse:  # noqa: F811
    return run_quick_assessment(
        AssessRequest(
            job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id), model_target=ModelTarget.CLAUDE_CLI
        ),
        home_root=fx.home_root, target=fx.target, config=_config(fx.home_root, model),
    )


def _stored(fx: PostingsFixture) -> dict:  # noqa: F811
    return json.loads(quick_assess_path(fx.home_root, fx.target, fx.default_profile_id, _JOB).read_text(encoding="utf-8"))


def _served(fx: PostingsFixture, item: AssessResponse) -> dict:  # noqa: F811
    return BasisCheck(home_root=fx.home_root, target=fx.target).served(item)


def _model_flags(record: Path) -> list[str | None]:
    out: list[str | None] = []
    for call in calls(record):
        argv = call["argv"]
        out.append(argv[argv.index("--model") + 1] if "--model" in argv else None)
    return out


def _calls_counted(fx: PostingsFixture) -> tuple[int, int]:  # noqa: F811
    [row] = metrics_report(fx.home_root, fx.target, kind="assess")["comparison"]  # type: ignore[misc]
    return row["calls"], row["errors"]


# --- the table ------------------------------------------------------------------------------------------------


def test_one_table_decides_evaluated_or_not() -> None:
    assert evaluated_models.EVALUATED_MODELS == {"claude_cli": (OPUS,), "codex_cli": ("gpt-6-astra",)}
    assert model_notice("claude_cli", OPUS) is None
    assert model_notice("claude_cli", "claude-sonnet-5-5").text == NOTICE.format(used="claude-sonnet-5-5", evaluated=OPUS)  # type: ignore[union-attr]
    # a model nobody named is not claimed to be anything; a target with no results has no notice
    assert model_notice("claude_cli", None) is None and model_notice("claude_cli", "default") is None
    assert model_notice("ollama_local", "llama3.1:8b") is None


def test_codex_keeps_its_default_and_the_same_rule_applies() -> None:
    # the Codex CLI does not report the model its default resolves to: never claimed to be the evaluated one
    unreported = model_notice("codex_cli", "default")
    assert unreported is not None
    assert unreported.text == "Assessed with the Codex CLI's configured model (not reported). GigAI's results for Codex are for gpt-6-astra."
    assert model_notice("codex_cli", "gpt-6-astra") is None
    other = model_notice("codex_cli", "gpt-5.1-codex")
    assert other is not None
    assert other.text == NOTICE.format(used="gpt-5.1-codex", evaluated="gpt-6-astra")
    assert other.to_json()["link"] == RESULTS_PAGE == "scout/accuracy-0-1-11"


# --- the assessment call ---------------------------------------------------------------------------------------


def test_opus_answers_it_is_asked_for_stored_and_carries_no_notice(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    record = _fake(tmp_path, monkeypatch)

    response = _assess_on_claude(fx)

    assert _model_flags(record) == [OPUS]
    assert (response.model, response.model_asked, response.model_fallback) == (OPUS, OPUS, False)
    stored = _stored(fx)
    assert (stored["model"], stored["model_asked"]) == (OPUS, OPUS) and "model_fallback" not in stored
    assert "model_notice" not in _served(fx, response)
    assert _calls_counted(fx) == (1, 0)


def test_a_refused_opus_costs_exactly_one_fallback_call_and_says_so(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    record = _fake(tmp_path, monkeypatch, refuse=(OPUS,))

    response = _assess_on_claude(fx)

    assert _model_flags(record) == [OPUS, None]  # the refused call, then ONE on the CLI's default; nothing after
    assert (response.model, response.model_asked, response.model_fallback) == (FAKE_CLAUDE_MODEL, OPUS, True)
    stored = _stored(fx)
    assert (stored["model"], stored["model_asked"], stored["model_fallback"]) == (FAKE_CLAUDE_MODEL, OPUS, True)
    notice = _served(fx, response)["model_notice"]
    assert notice["text"] == NOTICE.format(used=FAKE_CLAUDE_MODEL, evaluated=OPUS)
    assert notice["link"] == RESULTS_PAGE
    assert _calls_counted(fx) == (2, 1)  # the call meter counts both


def test_a_second_model_call_of_one_assessment_never_asks_for_opus_again(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """The invalid-output retry after a fallback stays on the default: at most one fallback, no loop."""

    record = _fake(tmp_path, monkeypatch, refuse=(OPUS,))
    bin_dir = tmp_path / "fake-bin"
    write_fake_claude(bin_dir, record=record, refuse=(OPUS,), answer_text="not json", echo_model=True)

    with pytest.raises(QuickAssessError) as caught:
        _assess_on_claude(fx)

    assert caught.value.code == "model_output_invalid"
    assert _model_flags(record) == [OPUS, None, None]  # opus refused, then the default twice (the one retry of a bad answer)


def test_a_model_set_in_the_configuration_is_asked_as_set_and_the_same_table_decides(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    record = _fake(tmp_path, monkeypatch)

    response = _assess_on_claude(fx, "claude-sonnet-5-5")

    assert _model_flags(record) == ["claude-sonnet-5-5"]
    assert (response.model, response.model_asked, response.model_fallback) == ("claude-sonnet-5-5", "claude-sonnet-5-5", False)
    assert _served(fx, response)["model_notice"]["text"] == NOTICE.format(used="claude-sonnet-5-5", evaluated=OPUS)


def test_the_evaluated_model_set_by_hand_carries_no_notice(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _fake(tmp_path, monkeypatch)

    assert "model_notice" not in _served(fx, _assess_on_claude(fx, OPUS))


def test_a_model_the_operator_set_is_never_swapped_when_it_fails(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    record = _fake(tmp_path, monkeypatch, refuse=("claude-sonnet-5-5",))

    with pytest.raises(QuickAssessError) as caught:
        _assess_on_claude(fx, "claude-sonnet-5-5")

    assert caught.value.code == "model_unavailable"
    assert _model_flags(record) == ["claude-sonnet-5-5"]  # no fallback call: an explicit choice fails loudly


def test_a_timeout_is_not_a_refusal_and_asks_nothing_more() -> None:

    seen: list[str] = []

    class Slow:
        def invoke(self, request: object) -> object:
            seen.append(request.model)  # type: ignore[attr-defined]
            raise ModelInvocationError("CLI model invocation timed out")

    port = AssessModelPort(Slow(), "claude_cli")
    request = InvocationRequest("claude-default", "claude", "default", "assess", "p", frozenset({"text"}))
    with pytest.raises(ModelInvocationError):
        port.invoke(request)
    assert seen == [OPUS] and not port.fallback


def test_a_target_without_an_evaluated_model_is_not_touched(fx: PostingsFixture) -> None:  # noqa: F811
    """The fixture's ollama target: no model asked, none recorded, no new key in the stored file."""

    response = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target,
        config=fixture_config(fx.home_root),
    )
    stored = _stored(fx)
    assert response.model_asked is None and "model_asked" not in stored and "model_fallback" not in stored
    assert "model_notice" not in _served(fx, response)


def test_a_record_without_the_new_fields_round_trips_byte_for_byte(fx: PostingsFixture) -> None:  # noqa: F811
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=_URL), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target,
        config=fixture_config(fx.home_root),
    )
    raw = quick_assess_path(fx.home_root, fx.target, fx.default_profile_id, _JOB).read_bytes()
    loaded = AssessResponse.from_json(json.loads(raw))
    assert json.dumps(loaded.to_json(), indent=2, sort_keys=True).encode("utf-8") == raw
    assert AssessResponse.from_json({**json.loads(raw), "model_asked": OPUS, "model_fallback": True}).model_fallback is True


# --- the batch, the brief, the doctor --------------------------------------------------------------------------


def test_the_batch_output_carries_the_notice_in_json_and_in_the_human_lines(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _fake(tmp_path, monkeypatch, refuse=(OPUS,))
    monkeypatch.setattr(quick_assess, "_default_model_target", lambda _target: ModelTarget.CLAUDE_CLI)
    text = PostingText(title="Staff AI Engineer", company="harborlight", location="Remote", url=_URL, text=_POSTING, work_mode="remote", salary=None)

    batch = _assess(
        [(_JOB, fx.default_profile_id)], {_JOB: text}, home_root=fx.home_root, target=fx.target, config=_config(fx.home_root)
    )

    assert batch["assessed"] == 1 and not batch["failed"]
    [notice] = batch["model_notices"]  # type: ignore[misc]
    assert notice["text"] == NOTICE.format(used=FAKE_CLAUDE_MODEL, evaluated=OPUS)
    assert notice_lines(batch) == [f"{notice['text']} Results: {RESULTS_PAGE}"]
    assert notice_lines({"assessed": 1}) == []  # omitted when an evaluated model answered


def test_scout_jobs_assess_prints_the_notice_line_only_when_the_batch_has_one() -> None:
    from gigai.scout import posting_search

    notice = model_notice("claude_cli", "claude-sonnet-5-5")
    assert notice is not None

    def shown(**extra: object) -> str:
        assessed = {"requested": 1, "assessed": 1, "failed": [], "stopped": None, "fetched_on_demand": 0, **extra}
        response = {
            "schema_version": posting_search.ASSESS_SCHEMA_VERSION, "status": "assessed", "profiles": [], "counts": {}, "question": None,
            "model_input_summary": None, "low_rank": None, "not_found": [], "assessed": assessed, "postings": {"rows": []},
        }
        return posting_search.render(response)

    assert f"{notice.text} Results: {RESULTS_PAGE}" in shown(model_notices=[notice.to_json()]).splitlines()
    assert "accuracy results" not in shown()


def test_the_agent_brief_carries_the_notice_once_and_only_when_due() -> None:
    notice = model_notice("claude_cli", "claude-sonnet-5-5")
    assert notice is not None
    due = _render_yours(yours_part(YoursInputs(job_identity="https://example.test/jobs/1", profile_id="profile_x", model_notice=notice.to_json())))
    assert due.count(notice.text) == 1 and RESULTS_PAGE in due
    plain = yours_part(YoursInputs(job_identity="https://example.test/jobs/1", profile_id="profile_x"))
    assert "model_notice" not in plain["state"] and "GigAI's accuracy results" not in _render_yours(plain)  # type: ignore[operator]


def test_doctor_says_which_model_assessments_use(tmp_path: Path) -> None:
    home = tmp_path / "home"
    for model, asks, notice in (("default", OPUS, "no"), ("claude-sonnet-5-5", "claude-sonnet-5-5", "yes")):
        config = _config(home, model)
        home.mkdir(exist_ok=True)
        write_config_atomic(config)
        [check] = [item for item in run_doctor(home).checks if item.id == "assessment.model.claude-default"]
        assert f"asks={asks}" in check.evidence_safe_to_share and f"notice={notice}" in check.evidence_safe_to_share
        assert asks in check.summary
