"""0.1.11 MODELPIN2: the table carries the result, Codex is never silently "evaluated", the policy lives across a guard retry.

* three states per (target, model): meets the bar (no notice), measured below it (the notice names both counts),
  not measured (the plain notice); the results page's rows come from the same table;
* ``codex exec --json`` with and without a model in its events, through the real adapter and a fake ``codex``;
* a refused Opus plus the guard retry (``posting_requirements_unreadable``) is ONE refused call, then the default model;
* ``modelUsage`` listing two models: the one that wrote the most output is stored.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gigai.adapters.codex_cli import CodexCLIAdapter
from gigai.adapters.factory import resolve_model_adapter
from gigai.adapters.port import InvocationRequest
from gigai.scout import evaluated_models as table
from gigai.scout import quick_assess
from gigai.scout.evaluated_models import BELOW, MEETS, NOT_MEASURED, ModelResult, model_notice, model_state, results_rows
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.contracts import ModelTarget
from gigai.scout.quick_assess import list_quick_assessments, run_quick_assessment
from tests.behaviors.scout_find_jobs.test_assess_model_pin import (
    NOTICE, OPUS, _assess_on_claude, _calls_counted, _config, _fake, _model_flags, _stored,
)
from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import _POSTING, _v8_answer, fx  # noqa: F401 - the fixture
from tests.support.fake_claude import FAKE_CLAUDE_MODEL, write_fake_claude
from tests.support.posting_fixtures import PostingsFixture

SONNET = "claude-sonnet-5-5"


def _scored(monkeypatch: pytest.MonkeyPatch, *, opus: int, sonnet: int, astra: int) -> None:
    monkeypatch.setattr(
        table,
        "RESULTS",
        (
            ModelResult("claude_cli", OPUS, opus),
            ModelResult("claude_cli", SONNET, sonnet),
            ModelResult("codex_cli", "gpt-6-astra", astra),
        ),
    )


# --- the table --------------------------------------------------------------------------------------------------


def test_the_three_states_with_the_counts_filled_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _scored(monkeypatch, opus=14, sonnet=11, astra=14)

    assert model_state("claude_cli", OPUS) == MEETS and model_notice("claude_cli", OPUS) is None
    assert model_state("codex_cli", "gpt-6-astra") == MEETS and model_notice("codex_cli", "gpt-6-astra") is None
    assert model_state("claude_cli", SONNET) == BELOW
    below = model_notice("claude_cli", SONNET)
    assert below is not None
    assert below.text == (
        f"Assessed with {SONNET}. On GigAI's accuracy run it was accurate on 11 of 15 jobs; {OPUS} reached 14 of 15. "
        "This assessment may be less accurate."
    )
    assert below.to_json()["accurate"] == 11 and below.to_json()["bar_accurate"] == 14 and below.to_json()["of"] == 15
    # a model nobody measured keeps the plain notice
    assert model_state("claude_cli", "claude-haiku-4-5") == NOT_MEASURED
    assert model_notice("claude_cli", "claude-haiku-4-5").text == NOTICE.format(used="claude-haiku-4-5", evaluated=OPUS)  # type: ignore[union-attr]


def test_codex_below_the_bar_carries_the_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    _scored(monkeypatch, opus=14, sonnet=14, astra=9)

    notice = model_notice("codex_cli", "gpt-6-astra")
    assert notice is not None and "accurate on 9 of 15 jobs" in notice.text and f"{OPUS} reached 14 of 15" in notice.text


def test_the_results_page_rows_come_from_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    assert [(r["target"], r["model"], r["accurate"], r["state"]) for r in results_rows()] == [
        ("claude_cli", OPUS, None, "not_scored"), ("claude_cli", SONNET, None, "not_scored"), ("codex_cli", "gpt-6-astra", None, "not_scored"),
    ]
    _scored(monkeypatch, opus=14, sonnet=11, astra=14)
    assert [(r["model"], r["accurate"], r["of"], r["state"]) for r in results_rows()] == [
        (OPUS, 14, 15, MEETS), (SONNET, 11, 15, BELOW), ("gpt-6-astra", 14, 15, MEETS),
    ]


# --- codex ------------------------------------------------------------------------------------------------------


def _fake_codex(directory: Path, events: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "codex"
    stream = "\\n".join(json.dumps(event) for event in events)
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.160.0"; exit 0; fi\n'
        'if [ "$1" = "exec" ] && [ "$2" = "--help" ]; then echo "Usage: codex exec"; exit 0; fi\n'
        'if [ "$1" = "features" ]; then exit 0; fi\n'
        "cat >/dev/null\n"
        f"printf '{stream}\\n'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


_ANSWER = {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}
_USAGE = {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}}


def _codex_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[dict]) -> str:
    executable = _fake_codex(tmp_path / "bin", events)
    monkeypatch.setattr("gigai.adapters.codex_cli.codex_lockdown", lambda _exe: _no_lockdown())
    adapter = CodexCLIAdapter(executable=str(executable))
    request = InvocationRequest("codex-default", "codex", "default", "assess", "p", frozenset({"text"}))
    return adapter.invoke(request).resolved_model


def _no_lockdown():
    from gigai.adapters.cli_probe import CodexLockdown

    return CodexLockdown(features=(), mcp_servers=())


def test_codex_that_reports_its_model_is_recorded_and_judged_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = _codex_model(tmp_path, monkeypatch, [{"type": "thread.started", "model": "gpt-5.1-codex"}, _ANSWER, _USAGE])

    assert resolved == "gpt-5.1-codex"
    assert model_notice("codex_cli", resolved).text == NOTICE.format(used="gpt-5.1-codex", evaluated="gpt-6-astra")  # type: ignore[union-attr]


def test_codex_session_header_model_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = _codex_model(tmp_path, monkeypatch, [{"id": "0", "msg": {"type": "session_configured", "model": "gpt-6-astra"}}, _ANSWER, _USAGE])

    assert resolved == "gpt-6-astra" and model_notice("codex_cli", resolved) is None


def test_codex_that_does_not_report_its_model_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = _codex_model(tmp_path, monkeypatch, [{"type": "thread.started", "thread_id": "t"}, _ANSWER, _USAGE])

    assert resolved == "default"
    notice = model_notice("codex_cli", resolved)
    assert notice is not None
    assert notice.text == "Assessed with the Codex CLI's configured model (not reported). GigAI's results for Codex are for gpt-6-astra."
    assert notice.to_json()["kind"] == "unreported"


# --- the guard retry --------------------------------------------------------------------------------------------

_LONG_POSTING = _POSTING + " Harborlight builds careful, well-tested inference services for hospitals." * 20  # past the short-posting allowance
_THIN = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "Remote within the United States", "class": "hard", "status": "met", "resume_evidence": ["Denver"]}],
        "suggestions": [], "questions": [], "not_a_match_reason": None,
    }
)


def test_a_refused_opus_is_asked_once_across_the_guard_retry(fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    record = _fake(tmp_path, monkeypatch, refuse=(OPUS,))
    write_fake_claude(tmp_path / "fake-bin", record=record, refuse=(OPUS,), answers=(_THIN, _v8_answer()), echo_model=True)
    before = len(quick_assess.GUARD_RETRIES)

    response = run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_text=_LONG_POSTING), resume=AssessResumeInput(profile_id=fx.default_profile_id), model_target=ModelTarget.CLAUDE_CLI),
        home_root=fx.home_root, target=fx.target, config=_config(fx.home_root),
    )

    assert len(quick_assess.GUARD_RETRIES) == before + 1  # the thin answer was refused by the guard: a second binding was built
    assert _model_flags(record) == [OPUS, None, None]  # ONE refused call, then the default for the rest (never Opus again)
    assert (response.model, response.model_asked, response.model_fallback) == (FAKE_CLAUDE_MODEL, OPUS, True)
    [stored] = list_quick_assessments(fx.home_root, fx.target)  # read back from the store
    assert (stored.model, stored.model_asked, stored.model_fallback) == (FAKE_CLAUDE_MODEL, OPUS, True)
    assert _calls_counted(fx) == (3, 2)  # the refused call, the thin answer (unused), the good one


# --- two models in modelUsage -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("helper_tokens", "stored_model"), [(5, OPUS), (500, "claude-helper-5")])
def test_the_model_stored_is_the_one_that_wrote_the_most_output(
    fx: PostingsFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper_tokens: int, stored_model: str  # noqa: F811
) -> None:
    record = _fake(tmp_path, monkeypatch)
    write_fake_claude(
        tmp_path / "fake-bin", record=record, answer_text=_v8_answer(), echo_model=True, helper_usage=("claude-helper-5", helper_tokens)
    )

    response = _assess_on_claude(fx)

    assert response.model == stored_model == _stored(fx)["model"]
