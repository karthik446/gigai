from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.config import Endpoint, ModelTarget, Profile
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def test_agent_backed_create_stays_proposal_only_until_cli_approval(tmp_path: Path) -> None:
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
    envelope.write_text(
        json.dumps(
            {
                "protocol_version": "1",
                "invocation_id": "inv_g26-cli-builder",
                "trigger": "gigai:",
                "actor": {"kind": "agent", "id": "claude", "session_id": "g26-test"},
                "command": "create",
                "target": {"home": str(home), "project": "g26-project"},
                "input": {
                    "intent": "Review this repository.",
                    "proposal": {
                        "summary": "A bounded repository review proposal",
                        "effect": "read_local",
                    },
                },
                "requested": {
                    "roles": ["gig_creator"],
                    "models": ["codex-default"],
                    "capabilities": ["local_reference_read"],
                },
                "consent": [],
            }
        ),
        encoding="utf-8",
    )

    created = CliRunner().invoke(
        cli,
        [
            "create",
            "builder-proof",
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

    workpad = next((tmp_path / "workpads").rglob("manifests/gig-proposal.json")).parent.parent
    proposal_manifest = json.loads(
        (workpad / "manifests/gig-proposal.json").read_text(encoding="utf-8")
    )
    assert proposal_manifest["status"] in {"drafting", "proposed"}
    assert not (workpad / "manifests/active-gig-version.json").exists()

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
    assert json.loads(approved.output)["status"] == "approved"
    assert (workpad / "manifests/active-gig-version.json").exists()
