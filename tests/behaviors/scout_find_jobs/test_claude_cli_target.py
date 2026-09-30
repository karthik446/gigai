"""uat-bug-035 (operator decision): ``claude_cli`` is a selectable Scout model target.

Pinned end to end, with a fake ``claude`` on ``PATH`` (``tests/support/
fake_claude.py``) and the real ``ClaudeCLIAdapter``; no live model call:

* the sealed ``ModelTarget`` enum takes ``claude_cli``: config, run input
  and the assess request round-trip it; an unknown value is still refused;
* ``claude_cli`` maps to the configured target whose endpoint uses the
  ``claude_cli`` adapter (``gigai setup`` names it ``claude-default``);
* availability is ``claude`` on ``PATH``, found the way ``codex`` is: with
  no ``claude`` the rank pass is skipped with the same
  ``model_target_unavailable: <adapter message>`` shape codex gets;
* a rank pass through the real run step (``acquire_node``) calls the fake
  CLI in LEAN mode, and an assessment through the real ``assess_node`` calls
  it in the adapter's default PLAN mode: both argvs are pinned.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from gigai.adapters.claude_cli import LEAN_SYSTEM_PROMPT
from gigai.adapters.factory import resolve_model_adapter
from gigai.adapters.port import ModelInvocationError
from gigai.config import Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.journal import read_committed_artifact
from gigai.model_discovery import discover_installed_models
from gigai.scout.find_jobs import model_rank
from gigai.scout.find_jobs.contracts import (
    AssessInput,
    FindJobsConfig,
    FindJobsContractError,
    FindJobsRunInput,
    ModelTarget,
    NodeContext,
    RunRequest,
    SelectedPosting,
    SelectionReason,
    SelectionReasonCode,
)
from gigai.scout.proposal_execution import _resolve_configured_target_name_for_adapter, assess_node
from tests.behaviors.scout_find_jobs.test_assess_model_policy import _assess_fixture, _posting as _assess_posting
from tests.behaviors.scout_find_jobs.test_run_rank_step import RUN_ID, _acquire, _posting
from tests.support.fake_claude import FAKE_CLAUDE_MODEL, calls, write_fake_claude

FIXTURES = Path(__file__).parent / "fixtures"

CLAUDE_ENDPOINT = Endpoint(name="claude", adapter="claude_cli")
CLAUDE_TARGET = ConfigModelTarget("claude-default", "claude", "default", ("text",), 4096)

# The argvs after the executable, spelled out: any change is deliberate.
PLAN_ARGV = ["-p", "--output-format", "json", "--no-session-persistence", "--permission-mode", "plan", "--tools", "", "--setting-sources", "", "--strict-mcp-config"]
LEAN_ARGV = [
    "-p", "--output-format", "json", "--no-session-persistence",
    "--tools", "", "--system-prompt", LEAN_SYSTEM_PROMPT, "--setting-sources", "", "--strict-mcp-config",
    "--disable-slash-commands", "--effort", "low",
]


def _config_json() -> dict:
    return json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text())


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``claude`` first on PATH; returns its call record."""

    bin_dir = tmp_path / "fake-bin"
    record = write_fake_claude(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return record


@pytest.fixture
def no_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PATH with no ``claude`` on it (a developer box may have the real one)."""

    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


@pytest.fixture
def substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    fixture, target = _assess_fixture(
        tmp_path, extra_endpoints=(CLAUDE_ENDPOINT,), extra_model_targets=(CLAUDE_TARGET,)
    )
    monkeypatch.setenv("JEV_API_KEY", "jv_test_key_never_used")
    return {**fixture, "target": target}


# --- the sealed enum and the config ---------------------------------------------------------


def test_claude_cli_round_trips_through_config_and_the_sealed_run_input() -> None:
    assert ModelTarget("claude_cli") is ModelTarget.CLAUDE_CLI
    assert {item.value for item in ModelTarget} == {"ollama_local", "codex_cli", "openrouter_api", "claude_cli"}

    config = FindJobsConfig.from_json({**_config_json(), "default_model_target": "claude_cli"})
    assert config.default_model_target is ModelTarget.CLAUDE_CLI
    assert FindJobsConfig.from_json(config.to_json()) == config
    assert config.to_json()["default_model_target"] == "claude_cli"

    run_input = {
        "schema_version": "scout-find-jobs-run-input:1",
        "config": config.to_json(),
        "config_digest": config.digest(),
        "selection_cap": 10,
        "selection_rule": "new_or_edited_role_match",
        "model_target": "claude_cli",
        "pinned_resume": {
            "record_id": "record_" + "1" * 32,
            "revision_id": "revision_" + "2" * 32,
            "content_sha256": "sha256:" + "3" * 64,
        },
    }
    sealed = FindJobsRunInput.from_json(run_input)
    assert sealed.model_target is ModelTarget.CLAUDE_CLI
    assert FindJobsRunInput.from_json(sealed.to_json()) == sealed


def test_a_run_request_takes_claude_cli() -> None:
    """What the run dialog sends (``api.buildRunRequest``) for Claude."""

    body = {**json.loads((FIXTURES / "fixture-api-run-request-v1.json").read_text()), "model_target": "claude_cli"}
    request = RunRequest.from_json(body)
    assert request.model_target is ModelTarget.CLAUDE_CLI
    assert RunRequest.from_json(request.to_json()) == request


def test_an_unknown_model_target_is_still_refused() -> None:
    with pytest.raises(FindJobsContractError):
        FindJobsConfig.from_json({**_config_json(), "default_model_target": "claude"})


def test_claude_cli_maps_to_the_target_setup_names_claude_default(substrate: dict) -> None:
    config = substrate["config"]

    assert _resolve_configured_target_name_for_adapter(config, "claude_cli") == "claude-default"
    # Adding claude leaves the other kinds where they were.
    assert _resolve_configured_target_name_for_adapter(config, "ollama_local") == "ollama-default"


# --- availability: `claude` on PATH, like `codex` ------------------------------------------------


def test_availability_is_claude_on_path(tmp_path: Path, substrate: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    config = substrate["config"]
    bin_dir = tmp_path / "fake-bin"
    write_fake_claude(bin_dir)

    monkeypatch.setenv("PATH", str(tmp_path))  # no claude here
    with pytest.raises(ModelInvocationError, match="claude executable is not available on PATH"):
        resolve_model_adapter(config, "claude-default")
    [missing] = [item for item in discover_installed_models(path=str(tmp_path), which=lambda _name: None) if item.name == "claude"]
    assert missing.readiness == "unavailable" and missing.failure_code == "executable_not_found"

    monkeypatch.setenv("PATH", str(bin_dir))
    binding = resolve_model_adapter(config, "claude-default")
    assert binding.port.adapter_name == "claude_cli"
    [found] = [item for item in discover_installed_models(path=str(bin_dir)) if item.name == "claude"]
    assert found.executable == bin_dir / "claude" and found.readiness != "unavailable"


def test_without_claude_the_rank_pass_is_skipped_with_the_codex_message_shape(substrate: dict, no_claude: None) -> None:
    result = model_rank.rank_postings(
        [_posting(0)],
        resume_text="Python engineer.",
        prefs=model_rank.CandidatePrefs(("Software Engineer",), ("US",), False, "", True),
        model_target="claude_cli",
        home_root=substrate["home"],
        config=substrate["config"],
    )

    assert result.status == "skipped"
    assert result.fail_open_reason == "model_target_unavailable: claude executable is not available on PATH"


# --- the real run path with a fake claude ----------------------------------------------------


def test_a_run_ranks_with_claude_in_lean_mode(substrate: dict, fake_claude: Path) -> None:
    rows = [_posting(n) for n in range(5)]

    output = _acquire(substrate, rows, cap=3, model_target=ModelTarget.CLAUDE_CLI)

    [call] = calls(fake_claude)  # 5 postings: one batch
    assert call["kind"] == "rank"
    assert call["argv"] == LEAN_ARGV  # the configured model is "default": no --model
    assert {item.normalized_url for item in output.rank_scores} == {row.normalized_url for row in rows}
    assert all(item.score is not None for item in output.rank_scores)

    resolved = substrate["resolved"]
    raw, _commit = read_committed_artifact(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        path=f"runs/{RUN_ID}/outputs/rank.json",
    )
    rank = json.loads(raw)
    assert rank["model_target"] == "claude_cli" and rank["configured_target"] == "claude-default"
    assert rank["status"] == "complete" and rank["resolved_model"] == FAKE_CLAUDE_MODEL
    assert rank["effort"] == "low" and rank["effort_applied"] == "low"


def _assess_with_claude(substrate: dict, run_id: str) -> object:
    target: Path = substrate["target"]
    posting = _assess_posting(normalized_url="https://boards.greenhouse.io/acme/jobs/35", text="We need Python and GCP.")
    run_dir = target / "runs" / run_id
    (run_dir / "outputs").mkdir(parents=True)
    (run_dir / "outputs" / "acquire.json").write_text(
        json.dumps({"rows": [{"posting": posting.to_json(), "outcome": "new"}]})
    )
    context = NodeContext(
        run_id=run_id, project_id=substrate["resolved"].project_id, gig_id=substrate["gig_id"],
        graph_id="graph_find_jobs_test", graph_version=1, goal_slug="assess",
        manifest_digest="sha256:" + "0" * 64, operation_key="assess-claude",
        target_observation_digest="sha256:" + "0" * 64, workpad_path=str(substrate["resolved"].path),
        redeemed_consent_ref="none", model_target=ModelTarget.CLAUDE_CLI,
    )
    assess_input = AssessInput(
        acquire_batch_ref=str(run_dir / "outputs" / "acquire.json"),
        acquire_output_digest="sha256:" + "0" * 64,
        selected_postings=(SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True),),
        selection_cap=10,
        selection_reasons=(SelectionReason(posting.normalized_url, SelectionReasonCode.NEW),),
        pinned_resume=substrate["pinned"],
        target=str(target),
        model_target=ModelTarget.CLAUDE_CLI,
        answer_association_version="scout-answer-association:1",
    )
    return assess_node(context, assess_input, home_root=substrate["home"], target=target, config=substrate["config"])


def test_a_run_assesses_with_claude_in_plan_mode(substrate: dict, fake_claude: Path) -> None:
    output = _assess_with_claude(substrate, "run_00000000-0000-4000-8000-000000000035")

    [call] = calls(fake_claude)
    assert call["kind"] == "assess"
    # The adapter's default mode, unchanged: plan mode. Plan mode ignores
    # --model (ranking spike), so an assessment runs Claude Code's default
    # model; the configured model "default" passes no --model at all.
    assert call["argv"] == PLAN_ARGV
    assert output.model_target is ModelTarget.CLAUDE_CLI
    assert output.producer.model_target is ModelTarget.CLAUDE_CLI and output.producer.adapter == "claude_cli"
    [assessed] = output.assessments
    assert assessed.proposal_revision_ref and assessed.matrix


def test_a_configured_model_is_passed_in_both_modes(substrate: dict, fake_claude: Path) -> None:
    """``--model`` goes on both argvs; only lean mode honours it (plan mode ignores it)."""

    config = substrate["config"]
    named = replace(config, model_targets=tuple(
        replace(item, model="sonnet") if item.name == "claude-default" else item for item in config.model_targets
    ))
    substrate = {**substrate, "config": named}

    _assess_with_claude(substrate, "run_00000000-0000-4000-8000-000000000036")
    model_rank.rank_postings(
        [_posting(0)],
        resume_text="Python engineer.",
        prefs=model_rank.CandidatePrefs(("Software Engineer",), ("US",), False, "", True),
        model_target=ModelTarget.CLAUDE_CLI,
        home_root=substrate["home"],
        config=named,
    )

    assess, rank = calls(fake_claude)
    assert assess["argv"] == [*PLAN_ARGV, "--model", "sonnet"]
    assert rank["argv"] == [*LEAN_ARGV, "--model", "sonnet"]



# --- a missing CLI is "model target unavailable" on every path, not a crash ----------------------


def test_without_claude_extraction_quick_assess_and_assess_say_the_target_is_unavailable(
    substrate: dict, no_claude: None
) -> None:
    """Before uat-bug-035 only the ranker mapped a missing CLI (codex too): the
    adapter's ``ModelInvocationError`` escaped extraction (a 500), quick assess
    and the assess node. Each now says ``model_target_unavailable`` with the
    adapter's own message, the shape the rank pass already used."""

    from gigai.scout.find_jobs.api.extract import ResumeExtractError, extract_with_model
    from gigai.scout.proposal_execution import ScoutProposalExecutionError
    from gigai.scout.quick_assess import QuickAssessError, _resolve_binding

    config = substrate["config"]
    with pytest.raises(ResumeExtractError) as extract_error:
        extract_with_model("Python engineer.", config=config, model_target=ModelTarget.CLAUDE_CLI, home_root=substrate["home"])
    assert extract_error.value.code == "model_target_unavailable"
    assert str(extract_error.value) == "claude executable is not available on PATH"

    with pytest.raises(QuickAssessError) as quick_error:
        _resolve_binding(config, ModelTarget.CLAUDE_CLI, home_root=substrate["home"])
    assert quick_error.value.code == "model_target_unavailable"
    assert str(quick_error.value) == "claude executable is not available on PATH"

    with pytest.raises(ScoutProposalExecutionError) as assess_error:
        _assess_with_claude(substrate, "run_00000000-0000-4000-8000-000000000037")
    assert assess_error.value.code == "model_target_unavailable"
