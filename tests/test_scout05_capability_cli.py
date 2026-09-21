from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.lifecycle import approve_offline
from gigai.project_binding import load_project_binding
from gigai.scout_bundled_tools import SCOUT_CRUD_CAPABILITY_ID, SCOUT_CRUD_ENTRY_PATH, SCOUT_CRUD_MANIFEST_ID
from tests.test_scout05_bundled_tools import _candidate


def _reviewable_candidate(tmp_path: Path):
    home, target, instance, workpad = _candidate(tmp_path)
    assert instance.proposal_id is not None
    approval = approve_offline(
        home_root=home,
        requested_target=target,
        gig_id=instance.gig_id,
        proposal_id=instance.proposal_id,
    )
    assert approval.version == 1
    return home, target, instance, workpad


def _input(path: Path, **overrides: object) -> Path:
    value: dict[str, object] = {
        "reviewer": {"kind": "agent", "id": "local-reviewer"},
        "outcome": "passed",
        "rationale": "The local source was reviewed as inert and bounded to native records.",
        "evidence_refs": ["inventory/source-digest", "review/native-record-boundary"],
    }
    value.update(overrides)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _args(
    *,
    home: Path,
    target: Path,
    instance: object,
    input_path: Path,
    operation_key: str,
    base_version: int = 1,
    base_proposal_id: str | None = None,
    confirm: bool = True,
) -> list[str]:
    proposal_id = base_proposal_id or str(instance.proposal_id)
    args = [
        "capability",
        "review",
        "--gig",
        str(instance.gig_id),
        "--base-version",
        str(base_version),
        "--base-proposal-id",
        proposal_id,
        "--manifest-id",
        SCOUT_CRUD_MANIFEST_ID,
        "--capability-id",
        SCOUT_CRUD_CAPABILITY_ID,
        "--operation-key",
        operation_key,
        "--input",
        str(input_path),
        "--home",
        str(home),
        "--target",
        str(target),
        "--json",
    ]
    if confirm:
        args.append("--confirm")
    return args


def _payload(result) -> dict[str, object]:
    assert result.output, result.exception
    value = json.loads(result.output)
    assert isinstance(value, dict)
    return value


def _review_paths(workpad: Path) -> list[Path]:
    root = workpad / "manifests/capability-reviews"
    return sorted(root.glob("*.json")) if root.exists() else []


def test_cli_passes_actual_reviewer_and_distinct_local_operator_then_replays(tmp_path: Path) -> None:
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    input_path = _input(tmp_path / "review.json")
    runner = CliRunner()
    args = _args(
        home=home,
        target=target,
        instance=instance,
        input_path=input_path,
        operation_key="cli-review-replay",
    )

    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    accepted = _payload(first)
    assert accepted["replayed"] is False
    assert accepted["reviewed_manifest_ref"] is not None
    decision = accepted["decision"]
    assert isinstance(decision, dict)
    assert decision["reviewer"] == {"kind": "agent", "id": "local-reviewer"}
    assert decision["operator_consent"]["actor"] == {"kind": "operator", "id": "local-user"}
    assert accepted["decision_ref"]["path"].startswith("manifests/capability-reviews/")
    assert "does not approve, activate, install, or execute" in accepted["review_note"]
    assert load_project_binding(target).active_gig_id is None
    assert "gigai.scout_tool_adapter" not in sys.modules

    replay = runner.invoke(cli, args)
    assert replay.exit_code == 0, replay.output
    replayed = _payload(replay)
    assert replayed["replayed"] is True
    assert replayed["decision"] == decision
    assert replayed["decision_ref"] == accepted["decision_ref"]
    assert len(_review_paths(workpad)) == 1


def test_cli_records_rejected_review_without_a_reviewed_manifest(tmp_path: Path) -> None:
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    input_path = _input(
        tmp_path / "rejected.json",
        outcome="rejected",
        rationale="The reviewer requires a separate source inspection before passing.",
        evidence_refs=["review/rejected"],
    )
    result = CliRunner().invoke(
        cli,
        _args(
            home=home,
            target=target,
            instance=instance,
            input_path=input_path,
            operation_key="cli-review-reject",
        ),
    )
    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert payload["reviewed_manifest_ref"] is None
    assert payload["decision"]["reviewer_outcome"] == "rejected"
    assert len(_review_paths(workpad)) == 1


def test_cli_refuses_missing_confirm_before_input_or_journal_access(tmp_path: Path) -> None:
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    result = CliRunner().invoke(
        cli,
        _args(
            home=home,
            target=target,
            instance=instance,
            input_path=tmp_path / "does-not-exist.json",
            operation_key="cli-review-no-confirm",
            confirm=False,
        ),
    )
    assert result.exit_code != 0
    payload = _payload(result)
    assert payload["error"]["code"] == "capability_review_operator_consent_required"
    assert not _review_paths(workpad)


@pytest.mark.parametrize("hostile_key", ["operator_actor", "effects", "source_callback"])
def test_cli_refuses_operator_effect_or_source_injection_fields(
    tmp_path: Path, hostile_key: str
) -> None:
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    input_path = _input(tmp_path / f"hostile-{hostile_key}.json", **{hostile_key: "inject"})
    result = CliRunner().invoke(
        cli,
        _args(
            home=home,
            target=target,
            instance=instance,
            input_path=input_path,
            operation_key=f"cli-review-hostile-{hostile_key}",
        ),
    )
    assert result.exit_code != 0
    payload = _payload(result)
    assert payload["error"]["code"] == "capability_review_input_invalid"
    assert not _review_paths(workpad)


@pytest.mark.parametrize(
    ("base_version", "base_proposal_id"),
    [
        (2, None),
        (1, "gp_00000000-0000-4000-8000-000000000099"),
    ],
)
def test_cli_refuses_stale_or_foreign_base_without_publication(
    tmp_path: Path, base_version: int, base_proposal_id: str | None
) -> None:
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    result = CliRunner().invoke(
        cli,
        _args(
            home=home,
            target=target,
            instance=instance,
            input_path=_input(tmp_path / "stale.json"),
            operation_key=f"cli-review-base-{base_version}",
            base_version=base_version,
            base_proposal_id=base_proposal_id,
        ),
    )
    assert result.exit_code != 0
    payload = _payload(result)
    assert payload["error"]["code"] == "capability_review_current_version_conflict"
    assert not _review_paths(workpad)


def test_cli_refuses_unsafe_input_link_and_never_executes_changed_source(tmp_path: Path) -> None:
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    source = _input(tmp_path / "review-source.json")
    redirected = tmp_path / "review-link.json"
    redirected.symlink_to(source)
    runner = CliRunner()
    unsafe = runner.invoke(
        cli,
        _args(
            home=home,
            target=target,
            instance=instance,
            input_path=redirected,
            operation_key="cli-review-input-link",
        ),
    )
    assert unsafe.exit_code != 0
    assert _payload(unsafe)["error"]["code"] == "capability_review_input_unsafe"
    assert not _review_paths(workpad)

    marker = tmp_path / "source-executed"
    entry = workpad / SCOUT_CRUD_ENTRY_PATH
    entry.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    changed = runner.invoke(
        cli,
        _args(
            home=home,
            target=target,
            instance=instance,
            input_path=source,
            operation_key="cli-review-source-changed",
        ),
    )
    assert changed.exit_code != 0
    assert _payload(changed)["error"]["code"] == "capability_review_tool_inventory_changed"
    assert not marker.exists()
    assert not _review_paths(workpad)


@pytest.mark.parametrize(
    "raw_input",
    [
        (
            b'{"reviewer":{"kind":"agent","id":"one"},"outcome":"passed",'
            b'"outcome":"rejected","rationale":"bounded","evidence_refs":["review/ref"]}'
        ),
        (
            b'{"reviewer":{"kind":"agent","id":"one","id":"two"},'
            b'"outcome":"passed","rationale":"bounded","evidence_refs":["review/ref"]}'
        ),
        b'{"reviewer":' + b"[" * 2048 + b"0" + b"]" * 2048 + b"}",
    ],
)
def test_cli_refuses_duplicate_or_excessively_nested_json_without_publication(
    tmp_path: Path, raw_input: bytes
) -> None:
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    input_path = tmp_path / "ambiguous.json"
    input_path.write_bytes(raw_input)
    result = CliRunner().invoke(
        cli,
        _args(
            home=home,
            target=target,
            instance=instance,
            input_path=input_path,
            operation_key="cli-review-ambiguous-json",
        ),
    )
    assert result.exit_code != 0
    assert _payload(result)["error"]["code"] == "capability_review_input_invalid"
    assert not _review_paths(workpad)


def test_cli_refuses_malformed_reviewer_without_publication(tmp_path: Path) -> None:
    home, target, instance, workpad = _reviewable_candidate(tmp_path)
    result = CliRunner().invoke(
        cli,
        _args(
            home=home,
            target=target,
            instance=instance,
            input_path=_input(
                tmp_path / "malformed-reviewer.json",
                reviewer={"kind": "agent", "model_target": None},
            ),
            operation_key="cli-review-malformed-actor",
        ),
    )
    assert result.exit_code != 0
    assert _payload(result)["error"]["code"] == "capability_review_actor_invalid"
    assert not _review_paths(workpad)


def test_cli_help_causes_no_authority_or_selection_change(tmp_path: Path) -> None:
    _home, target, _instance, workpad = _reviewable_candidate(tmp_path)
    before = (workpad / ".git").stat().st_mtime_ns
    result = CliRunner().invoke(cli, ["capability", "review", "--help"])
    assert result.exit_code == 0, result.output
    assert "write_workpad" in result.output
    assert "network is none" in result.output
    assert load_project_binding(target).active_gig_id is None
    assert not _review_paths(workpad)
    assert (workpad / ".git").stat().st_mtime_ns == before


def test_capability_cli_import_is_authority_free(tmp_path: Path) -> None:
    _home, _target, _instance, workpad = _reviewable_candidate(tmp_path)
    before = subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout
    import gigai.capability_cli as capability_cli

    importlib.reload(capability_cli)
    after = subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout
    assert after == before
    assert not _review_paths(workpad)
