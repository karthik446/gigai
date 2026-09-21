from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import subprocess
import zipfile

import pytest

import gigai.private_transfer as private_transfer
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.private_transfer import (
    PrivateTransferError,
    backup_private,
    import_definition,
    restore_private,
)
from gigai.default_init import initialize_defaults
from gigai.lifecycle import create_offline
from gigai.native_records import create_native_record, read_native_record
from gigai.scout_materialization import materialize_scout_template_update
from gigai.scout_template import (
    compare_scout_template,
    decide_scout_template_update,
    scout_candidate_inventory,
    scout_source_files,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.private_records import migrate_workpad_layout
from gigai.private_transfer import bind_restored_private
from gigai.portability import read_scout_source_snapshot
from gigai.project_binding import load_project_binding, write_project_binding_atomic


def _manifest(kind: str, rows: list[dict[str, object]], **extra: object) -> bytes:
    value = {
        "schema_version": "1.0",
        "kind": kind,
        "format": "zip",
        "files": rows,
    }
    value.update(extra)
    return canonical_json_bytes(value)


def _row(path: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "size_bytes": len(data),
    }


def test_import_rejects_duplicate_members_before_last_entry_wins(tmp_path: Path) -> None:
    data = b"definition"
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", _manifest("scout_definition_export", [_row("README.md", data)]))
        archive.writestr("README.md", data)
        archive.writestr("README.md", b"attacker")
    with pytest.raises(PrivateTransferError, match="duplicate"):
        import_definition(archive=archive_path, destination=tmp_path / "destination")


def test_restore_rejects_nested_symlink_swap_without_escape_or_unrelated_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    (source / "tools").mkdir(parents=True)
    (source / "tools" / "payload.txt").write_bytes(b"payload")
    archive = backup_private(workpad=source, destination=tmp_path / "private.zip")
    destination = tmp_path / "destination"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"unrelated")

    original_mkdir = private_transfer.os.mkdir
    swapped = False

    def swap_nested_parent(path, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        result = original_mkdir(path, mode, dir_fd=dir_fd)
        if path == "tools" and dir_fd is not None and not swapped:
            safe_copy = destination / "tools-original"
            (destination / "tools").rename(safe_copy)
            (destination / "tools").symlink_to(outside, target_is_directory=True)
            swapped = True
        return result

    monkeypatch.setattr(private_transfer.os, "mkdir", swap_nested_parent)
    with pytest.raises(PrivateTransferError, match="redirects|symlink"):
        restore_private(archive=archive.archive, destination=destination)
    assert swapped
    assert sentinel.read_bytes() == b"unrelated"
    assert not (outside / "payload.txt").exists()
    assert (destination / "tools").is_symlink()
    assert (destination / "tools-original").is_dir()


def test_private_backup_excludes_env_variants_and_machine_authority(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir()
    (tmp_path / "records" / "history.json").write_text("historical prose /Users/old-home is retained")
    (tmp_path / "records" / "operational.json").write_text(json.dumps({"path": "/Users/old-home/workpad"}))
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / ".env.production").write_text("TOKEN=secret")
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / "active-gig-version.json").write_text("{}")
    with pytest.raises(PrivateTransferError, match="absolute source"):
        backup_private(workpad=tmp_path, destination=tmp_path / "bad.zip")
    (tmp_path / "records" / "operational.json").unlink()
    result = backup_private(workpad=tmp_path, destination=tmp_path / "private.zip")
    with zipfile.ZipFile(result.archive) as archive:
        names = set(archive.namelist())
        assert "records/history.json" in names
        assert "tools/.env.production" not in names
        assert "manifests/active-gig-version.json" not in names


def test_private_restore_preserves_selected_history_and_refuses_authority_member(tmp_path: Path) -> None:
    root = tmp_path / "source"
    for path, data in {
        "records/revision.json": b"record",
        "references/source.json": b"reference",
        "docs/interview.md": b"history",
        "ui/template.html": b"<html></html>",
    }.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    archive = backup_private(workpad=root, destination=tmp_path / "private.zip")
    restored = restore_private(archive=archive.archive, destination=tmp_path / "second")
    assert restored.kind == "scout_private_transfer"
    for path in ("records/revision.json", "references/source.json", "docs/interview.md", "ui/template.html"):
        assert (restored.archive / path).read_bytes() == (root / path).read_bytes()
    assert not (restored.archive / "state.sqlite").exists()

    bad = tmp_path / "authority.zip"
    authority = b'{"activation":"none"}'
    with zipfile.ZipFile(bad, "w") as output:
        output.writestr(
            "manifest.json",
            canonical_json_bytes({
                "schema_version": "1.0",
                "kind": "scout_private_transfer",
                "format": "zip",
                "files": [_row("manifests/active-gig-version.json", authority)],
                "disclosure": "explicit operator-selected private Gig history; no credentials or provider configuration",
                "state_sqlite": "rebuildable_omitted",
                "activation": "none",
            }),
        )
        output.writestr("manifests/active-gig-version.json", authority)
    with pytest.raises(PrivateTransferError, match="manifest"):
        restore_private(archive=bad, destination=tmp_path / "unsafe")


def test_template_comparison_distinguishes_upstream_change_from_customization(tmp_path: Path) -> None:
    old = {"README.md": b"old", "tools/check.py": b"old-tool"}
    new = {"README.md": b"new", "tools/check.py": b"new-tool", "ui/template.html": b"new-ui"}
    (tmp_path / "README.md").write_bytes(old["README.md"])
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "check.py").write_bytes(b"local-customization")
    comparison = compare_scout_template(tmp_path, source=new, baseline=old)
    assert comparison.updated == ("README.md",)
    assert comparison.customized == ("tools/check.py",)
    assert "ui/template.html" in comparison.missing
    decision = decide_scout_template_update(comparison, "defer")
    assert decision["automatic_replacement"] is False
    assert (tmp_path / "README.md").read_bytes() == b"old"


def test_explicit_template_adoption_journals_update_and_preserves_customization(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", target], check=True)
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialized = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=scout_candidate_inventory(),
    )
    instance = initialized.instances[0]
    workpad = next((tmp_path / "workpads").glob(f"projects/*/gigs/{instance.gig_id}"))
    source = dict(scout_source_files())
    inventory_path = next(workpad.glob("manifests/software/*/source-inventory.json"))
    inventory_data = inventory_path.read_bytes()
    source_snapshot = read_scout_source_snapshot(
        workpad=workpad,
        project_id=workpad.parent.parent.name,
        gig_id=instance.gig_id,
        inventory_ref={
            "path": inventory_path.relative_to(workpad).as_posix(),
            "content_sha256": digest_imported_bytes(inventory_data),
            "media_type": "application/json",
            "size_bytes": len(inventory_data),
        },
    )
    assert source_snapshot.members["goalgraphs/prepare-interview.md"] == source["goalgraphs/prepare-interview.md"]
    assert source_snapshot.members["gig.py"] == source["gig.py"]
    assert not (workpad / "manifests" / "active-gig-version.json").exists()
    baseline = dict(source)
    (workpad / "README.md").write_bytes(baseline["README.md"])
    (workpad / "gig.py").write_bytes(b"operator customization")
    updated = dict(source)
    updated["README.md"] = b"upstream update"
    comparison = compare_scout_template(workpad, source=updated, baseline=baseline)
    result = materialize_scout_template_update(
        workpad=workpad,
        project_id=workpad.parent.parent.name,
        gig_id=instance.gig_id,
        comparison=comparison,
        decision="adopt",
        source=updated,
        baseline=baseline,
    )
    assert result.decision == "adopt"
    assert (workpad / "README.md").read_bytes() == b"upstream update"
    assert (workpad / "gig.py").read_bytes() == b"operator customization"
    assert result.handoff_id is not None
    scoped_archive = backup_private(
        workpad=workpad,
        destination=tmp_path / "scoped-private.zip",
        project_id=workpad.parent.parent.name,
        gig_id=instance.gig_id,
    )
    with zipfile.ZipFile(scoped_archive.archive) as archive:
        scoped_manifest = json.loads(archive.read("manifest.json"))
    assert isinstance(scoped_manifest["journal_head"], str)
    restored_scoped = restore_private(
        archive=scoped_archive.archive,
        destination=tmp_path / "restored-scoped",
        expected_project_id=workpad.parent.parent.name,
        expected_gig_id=instance.gig_id,
    )
    assert restored_scoped.kind == "scout_private_transfer"
    history = subprocess.run(
        ["git", "-C", str(workpad), "log", "--format=%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "scout source materialized" in history


def test_explicit_second_home_binding_reloads_history_without_selection_or_authority(
    tmp_path: Path,
) -> None:
    source_home, second_home = tmp_path / "source-home", tmp_path / "second-home"
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", target], check=True)
    source_home.mkdir()
    run_setup(
        build_config(
            home_root=source_home,
            workpad_root=tmp_path / "source-workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    source_binding = initialize_target(home_root=source_home, requested_target=target)
    created = create_offline(
        home_root=source_home,
        requested_target=target,
        name="transfer",
        open_editor=False,
    )
    migrate_workpad_layout(
        workpad=created.workpad,
        project_id=created.project_id,
        gig_id=created.gig_id,
    )
    native = create_native_record(
        home_root=source_home,
        requested_target=target,
        gig_id=created.gig_id,
        content={
            "schema_version": "1.0",
            "kind": "experience_qa",
            "scope": {"mode": "saved_default", "task_context_id": None, "base": None},
            "payload": {
                "questions": [{
                    "question_id": "transfer-01",
                    "prompt": "Describe a transfer-safe history.",
                    "state": "missing",
                    "answer": None,
                    "provenance": None,
                }],
            },
        },
        actor={"kind": "operator", "id": "synthetic-user"},
        origin="user_reported",
        operation_key="transfer-native",
    )
    archive = backup_private(
        workpad=created.workpad,
        destination=tmp_path / "second-home.zip",
        project_id=created.project_id,
        gig_id=created.gig_id,
    )
    restored = restore_private(
        archive=archive.archive,
        destination=tmp_path / "restored-tree",
        expected_project_id=created.project_id,
        expected_gig_id=created.gig_id,
    )
    run_setup(
        build_config(
            home_root=second_home,
            workpad_root=tmp_path / "second-workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    rebound = initialize_target(home_root=second_home, requested_target=target)
    assert rebound.project_id == source_binding.project_id == created.project_id
    with pytest.raises(PrivateTransferError, match="already selects"):
        bind_restored_private(
            restored=restored.archive,
            home_root=second_home,
            requested_target=target,
            project_id=created.project_id,
            gig_id=created.gig_id,
        )
    binding = load_project_binding(target)
    write_project_binding_atomic(target, replace(binding, active_gig_id=None))
    bound = bind_restored_private(
        restored=restored.archive,
        home_root=second_home,
        requested_target=target,
        project_id=created.project_id,
        gig_id=created.gig_id,
    )
    assert bound.active_selection is False
    assert not (bound.workpad / "manifests" / "active-gig-version.json").exists()
    assert read_native_record(
        home_root=second_home,
        requested_target=target,
        gig_id=created.gig_id,
        record_id=native.record_id,
        revision_id=native.revision_id,
    )["revision_id"] == native.revision_id
