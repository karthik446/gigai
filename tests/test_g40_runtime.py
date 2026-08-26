from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import uuid

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.invocation import InvocationValidationError, parse_invocation
from gigai.lifecycle import ApprovalResult, approve_offline, create_offline
from gigai.model_discovery import (
    DetectedModel,
    discover_runtime_snapshot,
    hydrate_login_shell_path,
    persist_discovery_snapshot,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


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


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": "field"},
        {"actor": {"kind": "agent", "id": "codex", "session_id": "s", "extra": "x"}},
        {"target": {"home": "/tmp/gigai", "project": "p", "extra": "x"}},
        {"input": {"intent": "x", "unexpected": "field"}},
        {"input": {"proposal": {"summary": "x", "unexpected": "field"}}},
    ],
)
def test_invocation_rejects_unsupported_fields(payload: dict[str, object]) -> None:
    candidate = _invocation()
    for key, value in payload.items():
        if isinstance(value, dict) and isinstance(candidate.get(key), dict):
            candidate[key] = {**candidate[key], **value}  # type: ignore[index]
        else:
            candidate.update(payload)

    with pytest.raises(InvocationValidationError, match="unsupported fields"):
        parse_invocation(candidate)


def test_invocation_rejects_unknown_agent_actor() -> None:
    with pytest.raises(InvocationValidationError, match="unknown agent actor"):
        parse_invocation(
            _invocation(
                actor={"kind": "agent", "id": "unknown", "session_id": "session-1"}
            )
        )


def test_invocation_requires_operator_consent_for_run() -> None:
    with pytest.raises(InvocationValidationError, match="agent-provided run consent"):
        parse_invocation(
            _invocation(
                command="run",
                input={"gig_id": "gig_test", "version": 1, "wait": True},
                consent=[
                    {"action": "run", "actor": {"kind": "operator", "id": "local-user"}}
                ],
            )
        )


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


def test_models_json_redacts_local_runtime_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = discover_runtime_snapshot(
        shell="/bin/sh",
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    snapshot = snapshot.__class__(
        **{
            **snapshot.__dict__,
            "effective_path": "/Users/private/.local/bin",
            "models": (
                DetectedModel(
                    "codex",
                    Path("/Users/private/.local/bin/codex"),
                    "detected",
                    "codex-cli test",
                    "path",
                    "login_shell",
                    None,
                ),
            ),
        }
    )
    monkeypatch.setattr("gigai.cli.discover_runtime_snapshot", lambda **_: snapshot)
    home = tmp_path / "home"
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )

    result = CliRunner().invoke(cli, ["models", "--home", str(home), "--json"])

    assert result.exit_code == 0, result.output
    assert "/Users/private" not in result.output
    payload = json.loads(result.output)
    assert payload["snapshot"]["effective_path"] == "<redacted>"
    assert payload["snapshot"]["models"][0]["executable"] == "<redacted>"
    assert payload["detected"][0]["executable"] == "<redacted>"


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode", "expected"),
    [
        ("noise", "", 0, "shell_sentinel_missing"),
        ("\x1b[2Knoise\x1b[0m", "", 0, "shell_sentinel_missing"),
        ("\x1e__GIGAI_DISCOVERY_PATH__=\x1e\x1e", "", 0, "shell_path_empty"),
        (
            "\x1e__GIGAI_DISCOVERY_PATH__=\x1e/runtime/path\x1e",
            "",
            7,
            "shell_exit:7",
        ),
    ],
)
def test_login_shell_hydration_classifies_bounded_output_failures(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    stderr: str,
    returncode: int,
    expected: str,
) -> None:
    monkeypatch.setattr(
        "gigai.model_discovery.subprocess.run",
        lambda *args, **kwargs: type(
            "Completed", (), {"stdout": stdout, "stderr": stderr, "returncode": returncode}
        )(),
    )

    path, source, status, reason = hydrate_login_shell_path(shell="/bin/sh")

    assert source == ("login_shell" if expected == "shell_exit:7" else "process")
    assert status == "failed"
    assert reason == expected
    assert path is not None


def test_login_shell_hydration_classifies_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired("/bin/sh", 5)

    monkeypatch.setattr("gigai.model_discovery.subprocess.run", timeout)

    path, source, status, reason = hydrate_login_shell_path(shell="/bin/sh")

    assert path is not None
    assert source == "process"
    assert status == "failed"
    assert reason == "shell_timeout"


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


def test_approve_json_reports_sealed_publication_and_pointer_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "gigai.cli.approve_offline",
        lambda **_: ApprovalResult(
            "gig_12345678-1234-4234-9234-123456789abc",
            "gp_12345678-1234-4234-9234-123456789abc",
            1,
            "a" * 40,
            "b" * 40,
            "gig-v000001",
        ),
    )

    result = CliRunner().invoke(cli, ["approve", "gp_test", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sealed_commit"] == "a" * 40
    assert payload["publication_commit"] == "b" * 40
    assert payload["journal_commit"] == "a" * 40
    assert payload["active_pointer"]["publication_commit"] == "b" * 40


def test_run_requires_explicit_consent_before_starting() -> None:
    result = CliRunner().invoke(cli, ["run", "--home", "/tmp/gigai-test", "--json"])

    assert result.exit_code != 0
    assert "run requires direct --confirm operator consent" in result.output


def test_agent_run_envelope_cannot_authorize_mismatched_cli_gig(tmp_path: Path) -> None:
    envelope = tmp_path / "gigai-run-mismatch.json"
    envelope.write_text(
        json.dumps(
            _invocation(
                command="run",
                input={"gig_id": "gig_envelope", "version": 1, "wait": False},
                consent=[],
            )
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli,
        [
                "run",
                "gig_cli",
                "--home",
                "/tmp/gigai",
                "--invocation",
            str(envelope),
            "--confirm",
        ],
    )

    assert result.exit_code != 0
    assert "does not match invocation input.gig_id" in result.output


def test_cli_run_persists_consent_from_agent_envelope(tmp_path: Path) -> None:
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
    binding = initialize_target(home_root=home, requested_target=target)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="consented-run",
        open_editor=False,
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
    )
    envelope = tmp_path / "run-invocation.json"
    envelope.write_text(
        json.dumps(
            _invocation(
                command="run",
                target={"home": str(home), "project": binding.project_id},
                input={"gig_id": created.gig_id, "version": 1, "wait": True},
                consent=[],
            )
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "run",
            created.gig_id,
            "--home",
            str(home),
            "--target",
            str(target),
            "--invocation",
            str(envelope),
            "--confirm",
            "--wait",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "succeeded"
    consent_paths = list((tmp_path / "workpads").rglob("operator-consent.json"))
    assert len(consent_paths) == 1
    consent_path = consent_paths[0]
    consent = json.loads(consent_path.read_text(encoding="utf-8"))
    assert consent["source"] == "direct_cli_confirm"
    assert consent["invocation_id"] == "inv_test-001"
    assert consent["redeemed_before_allocation"] is True
    assert consent["scope"] == {
        "project_id": binding.project_id,
        "gig_id": created.gig_id,
        "gig_version": 1,
        "target_kind": "non-git",
        "target_observation_sha256": consent["scope"]["target_observation_sha256"],
    }


def test_repeated_direct_confirmations_redeem_distinct_run_consent_records(
    tmp_path: Path,
) -> None:
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
    initialize_target(home_root=home, requested_target=target)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="replayed-confirmation",
        open_editor=False,
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
    )
    args = [
        "run",
        created.gig_id,
        "--home",
        str(home),
        "--target",
        str(target),
        "--confirm",
        "--wait",
        "--json",
    ]

    first = CliRunner().invoke(cli, args)
    second = CliRunner().invoke(cli, args)

    assert first.exit_code == second.exit_code == 0
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "workpads").rglob("operator-consent.json")
    ]
    assert len(records) == 2
    assert len({record["confirmation_id"] for record in records}) == 2


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
    initialize_target(home_root=home, requested_target=target)
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
    assert (home / "registry.sqlite").exists()
