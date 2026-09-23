from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.config import Endpoint, ModelTarget, Profile
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _invocation(home: Path) -> dict[str, object]:
    return {
        "protocol_version": "1",
        "invocation_id": "inv_g22-cli-create",
        "trigger": "$gigai",
        "actor": {"kind": "agent", "id": "codex", "session_id": "g22-test"},
        "command": "create",
        "target": {"home": str(home), "project": "g22-project"},
        "input": {
            "intent": "Create a bounded CLI proposal.",
            "proposal": {"summary": "Review repository changes"},
        },
        "requested": {
            "roles": ["gig_creator"],
            "models": ["codex-default"],
            "capabilities": [],
        },
        "consent": [],
    }


def test_cli_create_and_approve_use_explicit_agent_envelope(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
            endpoints=(Endpoint("codex", "codex_cli"),),
            model_targets=(
                ModelTarget("codex-default", "codex", "default", ("text",), 512),
            ),
            profiles=(
                Profile(
                    "default",
                    "codex-default",
                    "codex-default",
                    "codex-default",
                ),
            ),
        )
    )
    initialize_target(home_root=home, requested_target=target)
    envelope = tmp_path / "invocation.json"
    envelope.write_text(json.dumps(_invocation(home)), encoding="utf-8")

    created = CliRunner().invoke(
        cli,
        [
            "create",
            "cli-proof",
            "--home",
            str(home),
            "--target",
            str(target),
            "--invocation",
            str(envelope),
            "--json",
        ],
    )

    assert created.exit_code == 0, created.output
    proposal = json.loads(created.output)
    assert proposal["status"] == "proposed"
    assert proposal["authority_created"] is False

    approved = CliRunner().invoke(
        cli,
        [
            "approve",
            proposal["proposal_id"],
            "--home",
            str(home),
            "--target",
            str(target),
            "--json",
        ],
    )

    assert approved.exit_code == 0, approved.output
    result = json.loads(approved.output)
    assert result["status"] == "approved"
    assert result["version"] == 1
    assert result["tag"] == "gig-v000001"


def test_cli_create_requires_an_explicit_invocation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )

    result = CliRunner().invoke(
        cli,
        [
            "create",
            "implicit-conversation",
            "--home",
            str(home),
            "--target",
            str(target),
        ],
    )

    assert result.exit_code != 0
    assert "requires an explicit --invocation" in result.output
    assert "Traceback" not in result.output
