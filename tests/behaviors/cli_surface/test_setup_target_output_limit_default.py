"""run-status-and-logs (B): setup's auto-created targets need enough
output-token headroom for a real find-jobs assessment.

The pre-0.1.9.x README told operators to raise a target's output limit by
hand via ``--target-output-limit`` because ``gigai setup``'s own
auto-discovered targets (codex/claude CLI detection, and the browser
preview's openai/openrouter defaults) defaulted ``max_output_tokens`` to
512 -- inconsistent with the 4096 an explicit ``--model-target
NAME=ENDPOINT:MODEL`` has always defaulted to (``cli._parse_model_target_spec``).
512 is genuinely too small for a real assessment: the assess prompt's own
JSON schema asks for 5-12 requirement-matrix rows plus suggestions/questions
(``proposal_execution._assess_prompt``), which routinely runs well past a
few hundred tokens -- and ``ollama_local``/``openrouter_api`` both really
enforce a target's ``max_output_tokens`` as a generation cap (``num_predict``
/ ``max_tokens``), so a too-small default silently truncates a real
assessment's JSON output before it can parse.

This test pins the fix: ``gigai setup``'s auto-discovered CLI target now
gets the same 4096-token default as an explicit ``--model-target``, with no
``--target-output-limit`` needed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from gigai.cli import cli
from gigai.config import load_config
from gigai.model_discovery import DetectedModel


def test_setup_auto_discovered_codex_target_defaults_to_4096_output_tokens(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    snapshot = SimpleNamespace(
        models=(
            DetectedModel(
                "codex",
                Path("/runtime/bin/codex"),
                "detected",
                "codex 1.2.3",
                "path",
                "login_shell",
                None,
            ),
            DetectedModel("claude", None, "unavailable", failure_code="executable_not_found"),
        )
    )
    monkeypatch.setattr("gigai.cli.discover_runtime_snapshot", lambda **_: snapshot)
    monkeypatch.setattr("gigai.cli.persist_discovery_snapshot", lambda *_: home / "snapshot.json")

    result = CliRunner().invoke(
        cli,
        ["setup", "--home", str(home), "--editor", "/usr/bin/true"],
        input="\n\n\nn\ny\n",
    )

    assert result.exit_code == 0, result.output
    config = load_config(home)
    target = next(item for item in config.model_targets if item.name == "codex-default")
    assert target.max_output_tokens == 4096, (
        "gigai setup's auto-discovered codex-default target must default to "
        "the same 4096-token allowance an explicit --model-target gets "
        "(cli._parse_model_target_spec), so a real assessment's JSON output "
        "(5-12 requirement-matrix rows plus suggestions/questions) isn't "
        "truncated without the operator ever raising --target-output-limit "
        "by hand"
    )


def test_setup_non_interactive_with_target_output_limit_still_overrides_the_default(
    tmp_path: Path, monkeypatch
) -> None:
    """The escape hatch the pre-0.1.9.x README pointed operators at still
    works: --target-output-limit still overrides whatever the default is,
    raised or not."""

    home = tmp_path / "home"
    snapshot = SimpleNamespace(
        models=(
            DetectedModel(
                "codex",
                Path("/runtime/bin/codex"),
                "detected",
                "codex 1.2.3",
                "path",
                "login_shell",
                None,
            ),
        )
    )
    monkeypatch.setattr("gigai.cli.discover_runtime_snapshot", lambda **_: snapshot)
    monkeypatch.setattr("gigai.cli.persist_discovery_snapshot", lambda *_: home / "snapshot.json")

    result = CliRunner().invoke(
        cli,
        [
            "setup",
            "--home",
            str(home),
            "--editor",
            "/usr/bin/true",
            "--target-output-limit",
            "codex-default=8000",
        ],
        input="\n\n\nn\ny\n",
    )

    assert result.exit_code == 0, result.output
    config = load_config(home)
    target = next(item for item in config.model_targets if item.name == "codex-default")
    assert target.max_output_tokens == 8000
