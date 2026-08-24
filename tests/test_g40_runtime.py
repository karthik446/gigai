from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.invocation import InvocationValidationError, parse_invocation
from gigai.model_discovery import (
    DetectedModel,
    discover_runtime_snapshot,
    persist_discovery_snapshot,
)
from gigai.setup import build_config, run_setup


def _invocation(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol_version": "1",
        "invocation_id": "inv_test-001",
        "trigger": "/gigai",
        "actor": {"kind": "agent", "id": "codex", "session_id": "session-1"},
        "command": "create",
        "target": {"home": "/tmp/gigai", "project": "project-1"},
        "input": {"intent": "review this repository"},
        "requested": {"roles": ["reviewer"], "models": ["codex-default"], "capabilities": []},
        "consent": [],
    }
    payload.update(overrides)
    return payload


def test_invocation_accepts_explicit_agent_envelope_without_authority() -> None:
    invocation = parse_invocation(_invocation())

    assert invocation.command == "create"
    assert invocation.actor["kind"] == "agent"
    assert invocation.consent == ()


@pytest.mark.parametrize("field", ["transcript", "conversation", "credentials", "hidden_prompt"])
def test_invocation_rejects_conversation_and_secret_fields(field: str) -> None:
    payload = _invocation(input={field: "must not cross"})

    with pytest.raises(InvocationValidationError, match="forbidden"):
        parse_invocation(payload)


def test_invocation_rejects_implicit_trigger() -> None:
    with pytest.raises(InvocationValidationError, match="explicit GigAI trigger"):
        parse_invocation(_invocation(trigger="ordinary conversation"))


def test_discovery_snapshot_is_immutable_evidence_and_persisted_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gigai.model_discovery.hydrate_login_shell_path",
        lambda **_: ("/runtime/path", "login_shell", "ready", None),
    )
    monkeypatch.setattr(
        "gigai.model_discovery.discover_installed_models",
        lambda **_: (
            DetectedModel(
                "codex",
                Path("/runtime/path/codex"),
                "detected",
                "codex-cli test",
                "path",
                "login_shell",
                None,
            ),
        ),
    )
    snapshot = discover_runtime_snapshot(
        refresh_reason="explicit_refresh",
        captured_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )

    destination = persist_discovery_snapshot(tmp_path / "home", snapshot)
    record = json.loads(destination.read_text(encoding="utf-8"))

    assert record["operation_id"] == "discovery_12345678-1234-4234-9234-123456789abc"
    assert record["refresh_reason"] == "explicit_refresh"
    assert record["hydration"]["status"] == "ready"
    assert record["models"][0]["version"] == "codex-cli test"
    assert record["content_sha256"].startswith("sha256:")
    assert persist_discovery_snapshot(tmp_path / "home", snapshot) == destination


def test_models_command_persists_runtime_snapshot_without_provider_call(tmp_path: Path) -> None:
    home = tmp_path / "home"
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )

    result = CliRunner().invoke(cli, ["models", "--home", str(home), "--refresh", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["snapshot"]["refresh_reason"] == "explicit_refresh"
    configured = next(
        item for item in payload["configured"] if item["target_name"] == "offline-default"
    )
    assert configured["states"][-1] == "selected"
    assert "usable" in configured["states"]
    assert (home / "snapshots/runtime-discovery").is_dir()


def test_setup_clean_environment_returns_structured_error_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)

    result = CliRunner().invoke(
        cli,
        ["setup", "--non-interactive", "--home", str(tmp_path / "home"), "--json"],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "setup_editor_invalid"
    assert "no editor is configured" in payload["error"]["message"]
    assert "Traceback" not in result.output


def test_approve_help_exposes_capability_manifest_reference() -> None:
    result = CliRunner().invoke(cli, ["approve", "--help"])

    assert result.exit_code == 0, result.output
    assert "--capability-manifest-id" in result.output


def test_create_requires_and_consumes_explicit_agent_envelope(tmp_path: Path) -> None:
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
    envelope = tmp_path / "invocation.json"
    envelope.write_text(
        json.dumps(
            _invocation(
                target={"home": str(home), "project": "project-1"},
                input={
                    "intent": "create a repository review Gig",
                    "proposal": {"summary": "Review repository changes"},
                },
            )
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "create",
            "repository-review",
            "--home",
            str(home),
            "--target",
            str(target),
            "--invocation",
            str(envelope),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "proposed"
    assert payload["authority_created"] is False
    assert (home / "registry.json").exists()
