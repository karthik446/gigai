from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import uuid

import pytest

from gigai.canonical import EntityPrefix, canonical_json_bytes, digest_imported_bytes, generate_entity_id
from gigai.catalog import CatalogEntry
from gigai.default_init import DefaultInitError, initialize_defaults
from gigai.journal import JournalArtifact, record_transition
from gigai.private_records import migrate_workpad_layout
from gigai.registry import open_project_registry
from gigai.setup import build_config, run_setup
from gigai.workpad import provision_workpad


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", target], check=True
    )
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    return home, target


def _entry(name: str, *, version: str = "1.0", text: str = "one") -> CatalogEntry:
    return CatalogEntry(
        catalog_id=name,
        definition_version=version,
        title=name,
        summary="synthetic release-eligible default",
        capabilities=("fixture.prepare",),
        files={"definition/gig.md": f"# {text}\n".encode()},
    )


def _binding_commits(workpad: Path) -> tuple[str, ...]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(workpad),
            "log",
            "--format=%H",
            "--",
            "manifests/template-instance-binding.json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def _workpad(home: Path, gig_id: str) -> Path:
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        record = transaction.find_workpad(gig_id)
    assert record is not None
    return Path(record.workpad_locator)


def test_completed_cache_loss_recovers_same_committed_default_identity(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    inventory = (_entry("one"),)
    first = initialize_defaults(
        home_root=home, requested_target=target, username="owner", inventory=inventory
    )
    gig_id = first.instances[0].gig_id
    root = _workpad(home, gig_id)
    commits = _binding_commits(root)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    connection = sqlite3.connect(home / "registry.sqlite")
    try:
        connection.execute("DELETE FROM template_instances")
        connection.commit()
    finally:
        connection.close()

    recovered = initialize_defaults(
        home_root=home, requested_target=target, username=None, inventory=inventory
    )
    assert recovered.instances[0].gig_id == gig_id
    assert recovered.instances[0].status == "existing"
    assert _binding_commits(root) == commits
    assert subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == head
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        assert transaction.find_template_instance(recovered.package.project_id, "one")
    connection = sqlite3.connect(home / "registry.sqlite")
    try:
        connection.execute(
            "UPDATE template_instances SET binding_sha256 = ? WHERE gig_id = ?",
            ("sha256:" + "0" * 64, gig_id),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DefaultInitError) as corrupt:
        initialize_defaults(
            home_root=home, requested_target=target, username=None, inventory=inventory
        )
    assert corrupt.value.code == "template_reconciliation_required"
    assert _binding_commits(root) == commits


def test_interrupted_batch_cache_loss_keeps_reserved_binding_and_adds_missing_default(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    inventory = (_entry("one"), _entry("two"))

    def interrupt(step: str) -> None:
        if step == "binding_published":
            raise RuntimeError("stop after first binding")

    with pytest.raises(RuntimeError, match="stop after first binding"):
        initialize_defaults(
            home_root=home,
            requested_target=target,
            username="owner",
            inventory=inventory,
            observer=interrupt,
        )
    registry, _ = open_project_registry(home, create=False)
    workpads = registry.workpad_records()
    assert len(workpads) == 1
    first_root = Path(workpads[0].workpad_locator)
    first_id = workpads[0].gig_id
    first_commits = _binding_commits(first_root)

    resumed = initialize_defaults(
        home_root=home, requested_target=target, username=None, inventory=inventory
    )
    ids = {row.template_id: row.gig_id for row in resumed.instances}
    assert ids["one"] == first_id
    assert ids["two"] != first_id
    assert _binding_commits(first_root) == first_commits


def test_ambiguous_binding_or_corrupt_cache_refuses_without_replacement(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    inventory = (_entry("one"),)
    first = initialize_defaults(
        home_root=home, requested_target=target, username="owner", inventory=inventory
    )
    project_id = first.package.project_id
    original_root = _workpad(home, first.instances[0].gig_id)
    original = json.loads(
        (original_root / "manifests" / "template-instance-binding.json").read_text()
    )
    duplicate_id = generate_entity_id(
        EntityPrefix.GIG, is_persisted=lambda _candidate: False, uuid_factory=uuid.uuid4
    )
    duplicate = provision_workpad(
        home_root=home,
        project_id=project_id,
        gig_id=duplicate_id,
        reconcile_existing_journal=True,
    )
    migrate_workpad_layout(
        workpad=duplicate.path,
        project_id=project_id,
        gig_id=duplicate_id,
        uuid_factory=uuid.uuid4,
    )
    original["gig_id"] = duplicate_id
    duplicate_bytes = canonical_json_bytes(original)
    record_transition(
        workpad=duplicate.path,
        project_id=project_id,
        gig_id=duplicate_id,
        handoff_id=generate_entity_id(
            EntityPrefix.HANDOFF,
            is_persisted=lambda _candidate: False,
            uuid_factory=uuid.uuid4,
        ),
        transition="template_instance_bound",
        body="Synthetic duplicate binding for refusal coverage.",
        artifacts=(
            JournalArtifact("manifests/template-instance-binding.json", duplicate_bytes),
        ),
        front_matter={
            "artifact_refs": [
                {
                    "path": "manifests/template-instance-binding.json",
                    "content_sha256": digest_imported_bytes(duplicate_bytes),
                    "media_type": "application/json",
                    "size_bytes": len(duplicate_bytes),
                }
            ]
        },
    )

    with pytest.raises(DefaultInitError) as refused:
        initialize_defaults(
            home_root=home, requested_target=target, username=None, inventory=inventory
        )
    assert refused.value.code == "template_reconciliation_required"
    assert _binding_commits(original_root)
    assert _binding_commits(duplicate.path)

def test_changed_upstream_default_reports_update_without_replacing_history(
    tmp_path: Path,
) -> None:
    home, target = _setup(tmp_path)
    original = _entry("one", version="1.0", text="original")
    updated = _entry("one", version="2.0", text="changed upstream")
    first = initialize_defaults(
        home_root=home, requested_target=target, username="owner", inventory=(original,)
    )
    gig_id = first.instances[0].gig_id
    root = _workpad(home, gig_id)
    commits = _binding_commits(root)

    second = initialize_defaults(
        home_root=home, requested_target=target, username=None, inventory=(updated,)
    )
    assert second.instances[0].gig_id == gig_id
    assert second.instances[0].status == "update_available"
    assert "unchanged" in second.instances[0].next_action
    assert _binding_commits(root) == commits
    registry, _ = open_project_registry(home, create=False)
    with registry.transaction() as transaction:
        cached = transaction.find_template_instance(second.package.project_id, "one")
    assert cached is not None
    assert cached.original_package_digest == original.entry_content_digest

    retry = initialize_defaults(
        home_root=home, requested_target=target, username=None, inventory=(updated,)
    )
    assert retry.instances[0].status == "update_available"
    returned = initialize_defaults(
        home_root=home, requested_target=target, username=None, inventory=(original,)
    )
    assert returned.instances[0].gig_id == gig_id
    assert returned.instances[0].status == "existing"

    extended = initialize_defaults(
        home_root=home,
        requested_target=target,
        username=None,
        inventory=(updated, _entry("two", text="new default")),
    )
    by_template = {row.template_id: row for row in extended.instances}
    assert by_template["one"].gig_id == gig_id
    assert by_template["one"].status == "update_available"
    assert by_template["two"].gig_id != gig_id
