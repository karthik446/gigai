from __future__ import annotations

from pathlib import Path
import ast
import subprocess
import uuid
from dataclasses import replace

import pytest
import gigai.target_effect as target_effect_module

from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.lifecycle import approve_offline, create_offline
from gigai.review_loop import run_review_loop
from gigai.run import launch_run
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.target_effect import (
    TargetEffectRefusedError,
    apply_target_effect,
    authorize_target_effect,
    cancel_target_effect,
    prepare_target_effect,
    recover_target_effect,
)
from gigai.workpad import resolve_workpad


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _fixture(tmp_path: Path) -> tuple[Path, Path, object, str, str]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init", "--initial-branch=main")
    _git(target, "config", "user.name", "G19 Fixture")
    _git(target, "config", "user.email", "g19-fixture@example.invalid")
    (target / "README.md").write_text("before\n", encoding="utf-8")
    _git(target, "add", "README.md")
    _git(target, "commit", "-m", "fixture baseline")
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(home_root=home, requested_target=target)
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{index:012x}")
        for index in range(1, 200)
    )
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="g19-target-effect",
        open_editor=False,
        uuid_factory=lambda: next(values),
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=lambda: next(values),
    )
    resolved = resolve_workpad(
        home_root=home,
        requested_target=target,
        gig_id=created.gig_id,
        allow_semantic_state=True,
    )
    run_id = launch_run(
        home_root=home,
        requested_target=target,
        gig_id=created.gig_id,
        wait=True,
        uuid_factory=lambda: next(values),
    ).run_id
    review = run_review_loop(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=created.gig_id,
        run_id=run_id,
        profile="repository",
    )
    assert review.state == "complete"
    assert review.addressed_artifact_id is not None
    return home, target, resolved, created.proposal_id, f"addressed/{review.addressed_artifact_id}.json"


def test_g19_applies_exactly_one_file_and_leaves_target_uncommitted(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    before_head = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    assert authorized.record["state"] == "effect_authorized"
    prepared = prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert prepared.record["state"] == "prepared"
    applied = apply_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert applied.record["state"] == "applied"
    assert (target / "README.md").read_bytes() == (resolved.path / source_path).read_bytes()
    assert subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip() == before_head
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain=v1"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert status == [" M README.md"]
    replay = apply_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert replay.record["state"] == "applied"
    assert replay.journal_entries == ()


def test_g19_refuses_dirty_target_before_preparation(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    (target / "README.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(TargetEffectRefusedError, match="not clean"):
        prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    refused = (resolved.path / "manifests/target-effects" / f"{authorized.record['effect_id']}.json").read_bytes()
    assert parse_json_bytes(refused)["state"] == "refused"


def test_g19_rejects_path_traversal_and_symlink_targets(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    with pytest.raises(TargetEffectRefusedError) as traversal:
        authorize_target_effect(
            resolved=resolved,
            proposal_id=proposal_id,
            relative_target_path="../README.md",
            source_artifact_path=source_path,
            operator={"kind": "operator", "id": "test-user"},
        )
    assert traversal.value.code == "unsafe_target_path"

    (target / "README-link.md").symlink_to("README.md")
    _git(target, "add", "README-link.md")
    _git(target, "commit", "-m", "fixture symlink")
    with pytest.raises(TargetEffectRefusedError) as symlink:
        authorize_target_effect(
            resolved=resolved,
            proposal_id=proposal_id,
            relative_target_path="README-link.md",
            source_artifact_path=source_path,
            operator={"kind": "operator", "id": "test-user"},
        )
    assert symlink.value.code == "unsafe_target_path"


def test_g19_rejects_non_git_resolved_targets(tmp_path: Path) -> None:
    _home, _target, resolved, proposal_id, source_path = _fixture(tmp_path)
    non_git = replace(resolved, target_kind="non-git")
    with pytest.raises(TargetEffectRefusedError) as error:
        authorize_target_effect(
            resolved=non_git,
            proposal_id=proposal_id,
            relative_target_path="README.md",
            source_artifact_path=source_path,
            operator={"kind": "operator", "id": "test-user"},
        )
    assert error.value.code == "non_git_target"


def test_g19_refuses_mode_drift_even_when_git_ignores_file_modes(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    _git(target, "config", "core.filemode", "false")
    (target / "README.md").chmod(0o755)
    with pytest.raises(TargetEffectRefusedError) as error:
        apply_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert error.value.code == "mode_mismatch"
    (target / "README.md").chmod(0o644)


def test_g19_refuses_changed_head_before_preparation(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    (target / "README.md").write_text("new committed baseline\n", encoding="utf-8")
    _git(target, "add", "README.md")
    _git(target, "commit", "-m", "changed head")
    with pytest.raises(TargetEffectRefusedError) as error:
        prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert error.value.code == "target_head_changed"


def test_g19_revalidates_active_proposal_before_preparation(tmp_path: Path) -> None:
    _home, _target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    active_path = resolved.path / "manifests/active-gig-version.json"
    active = parse_json_bytes(active_path.read_bytes())
    active["approved_proposal_id"] = "gp_99999999-9999-4999-8999-999999999999"
    active_path.write_bytes(canonical_json_bytes(active))
    with pytest.raises(TargetEffectRefusedError) as error:
        prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert error.value.code == "review_prerequisite_missing"


def test_g19_requires_an_addressed_review_artifact_before_authorization(tmp_path: Path) -> None:
    _home, _target, resolved, proposal_id, _source_path = _fixture(tmp_path)
    loop_path = resolved.path / "manifests/review-loop.json"
    loop = parse_json_bytes(loop_path.read_bytes())
    loop["addressed_artifact_ids"] = []
    loop_path.write_bytes(canonical_json_bytes(loop))
    with pytest.raises(TargetEffectRefusedError) as error:
        authorize_target_effect(
            resolved=resolved,
            proposal_id=proposal_id,
            relative_target_path="README.md",
            source_artifact_path="addressed/missing.md",
            operator={"kind": "operator", "id": "test-user"},
        )
    assert error.value.code == "review_prerequisite_missing"


def test_g19_refuses_tampered_review_artifact_before_preparation(tmp_path: Path) -> None:
    _home, _target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    (resolved.path / source_path).write_bytes(b"tampered source\n")
    with pytest.raises(TargetEffectRefusedError) as error:
        prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert error.value.code == "review_artifacts_invalid"


def test_g19_refuses_tampered_replacement_source_before_preparation(tmp_path: Path) -> None:
    _home, _target, resolved, proposal_id, _source_path = _fixture(tmp_path)
    source_path = "addressed/replacement.md"
    (resolved.path / source_path).write_text("replacement\n", encoding="utf-8")
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    (resolved.path / source_path).write_text("tampered replacement\n", encoding="utf-8")
    with pytest.raises(TargetEffectRefusedError) as error:
        prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert error.value.code == "source_digest_mismatch"


def test_g19_cancellation_before_exposure_leaves_target_unchanged(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    cancelled = cancel_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert cancelled.record["state"] == "cancelled"
    assert (target / "README.md").read_text(encoding="utf-8") == "before\n"


def test_g19_failpoint_before_exposure_leaves_target_unchanged(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))

    def interrupt(step: str) -> None:
        if step == "before_exposure":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        apply_target_effect(
            resolved=resolved,
            effect_id=str(authorized.record["effect_id"]),
            observer=interrupt,
        )
    assert (target / "README.md").read_text(encoding="utf-8") == "before\n"
    recovered = recover_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert recovered.record["state"] == "prepared"


def test_g19_exposed_record_recovers_to_applied_after_interruption(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))

    def interrupt(step: str) -> None:
        if step == "after_exposed_record":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        apply_target_effect(
            resolved=resolved,
            effect_id=str(authorized.record["effect_id"]),
            observer=interrupt,
        )
    recovered = recover_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert recovered.record["state"] == "applied"
    assert (target / "README.md").read_bytes() == (resolved.path / source_path).read_bytes()


def test_g19_cancellation_after_exposure_uses_recovery_policy(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))

    def interrupt(step: str) -> None:
        if step == "after_exposed_record":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        apply_target_effect(
            resolved=resolved,
            effect_id=str(authorized.record["effect_id"]),
            observer=interrupt,
        )
    cancelled = cancel_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert cancelled.record["state"] == "applied"
    assert (target / "README.md").read_bytes() == (resolved.path / source_path).read_bytes()


def test_g19_verified_record_recovers_to_applied_after_interruption(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))

    def interrupt(step: str) -> None:
        if step == "after_verified_record":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        apply_target_effect(
            resolved=resolved,
            effect_id=str(authorized.record["effect_id"]),
            observer=interrupt,
        )
    recovered = recover_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert recovered.record["state"] == "applied"
    assert (target / "README.md").read_bytes() == (resolved.path / source_path).read_bytes()


def test_g19_known_exposed_before_state_recovers_to_rolled_back(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))

    def restore_before(step: str) -> None:
        if step == "after_exposed_record":
            (target / "README.md").write_text("before\n", encoding="utf-8")

    result = apply_target_effect(
        resolved=resolved,
        effect_id=str(authorized.record["effect_id"]),
        observer=restore_before,
    )
    assert result.record["state"] == "rolled_back"
    assert (target / "README.md").read_text(encoding="utf-8") == "before\n"


def test_g19_recovery_blocks_ambiguous_interruption_before_exposed_record(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))

    def interrupt(step: str) -> None:
        if step == "after_exposure":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        apply_target_effect(
            resolved=resolved,
            effect_id=str(authorized.record["effect_id"]),
            observer=interrupt,
        )
    recovered = recover_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert recovered.record["state"] == "blocked"
    assert recovered.record["terminal_reason"] == "ambiguous_state_after_prepared"


def test_g19_after_exposure_digest_drift_blocks_recovery(tmp_path: Path) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))

    def drift(step: str) -> None:
        if step == "after_exposure":
            (target / "README.md").write_text("unexpected concurrent edit\n", encoding="utf-8")

    applied = apply_target_effect(
        resolved=resolved,
        effect_id=str(authorized.record["effect_id"]),
        observer=drift,
    )
    assert applied.record["state"] == "blocked"
    assert applied.record["terminal_reason"] == "ambiguous_exposed_state"


def test_g19_runtime_has_no_effectful_imports_and_uses_atomic_exposure() -> None:
    source_path = Path(target_effect_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        for alias in node.names
        if node.module.split(".")[0] in {"subprocess", "socket", "httpx", "requests", "urllib"}
    )
    assert not imported & {"subprocess", "socket", "httpx", "requests", "urllib"}
    atomic_replace_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == "replace"
    ]
    fsync_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == "fsync"
    ]
    assert atomic_replace_calls
    assert fsync_calls


def test_g19_exposure_uses_atomic_replace_at_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home, target, resolved, proposal_id, source_path = _fixture(tmp_path)
    authorized = authorize_target_effect(
        resolved=resolved,
        proposal_id=proposal_id,
        relative_target_path="README.md",
        source_artifact_path=source_path,
        operator={"kind": "operator", "id": "test-user"},
    )
    prepare_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    replacements: list[tuple[Path, Path]] = []
    original_replace = target_effect_module.os.replace

    def record_replace(source: str | bytes | Path, destination: str | bytes | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr(target_effect_module.os, "replace", record_replace)
    applied = apply_target_effect(resolved=resolved, effect_id=str(authorized.record["effect_id"]))
    assert applied.record["state"] == "applied"
    target_replacements = [destination for _source, destination in replacements if destination == target / "README.md"]
    assert target_replacements == [target / "README.md"]
