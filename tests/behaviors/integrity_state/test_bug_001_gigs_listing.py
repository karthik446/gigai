from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import uuid

from click.testing import CliRunner

from gigai.cli import cli
from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.journal import JournalArtifact, record_transition
from gigai.lifecycle import approve_offline, create_offline
from gigai.target_binding import initialize_target
from tests.behaviors.integrity_state.test_index_projection import _configured, _uuids


def _uuids_from(start: int):
    values = iter(
        __import__("uuid").UUID(f"00000000-0000-4000-8000-{value:012x}")
        for value in range(start, start + 32)
    )
    return lambda: next(values)


def test_gigs_implicit_scope_matches_explicit_target(tmp_path: Path, monkeypatch) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="implicit-listing",
        open_editor=False,
        uuid_factory=_uuids_from(100),
    )
    monkeypatch.chdir(target)

    implicit = CliRunner().invoke(cli, ["gigs", "--home", str(home), "--json"])
    explicit = CliRunner().invoke(
        cli,
        ["gigs", "--target", str(target), "--home", str(home), "--json"],
    )

    assert implicit.exit_code == 0, implicit.output
    assert explicit.exit_code == 0, explicit.output
    assert json.loads(implicit.output) == json.loads(explicit.output)
    assert json.loads(implicit.output)["entries"][0]["gig_id"] == created.gig_id


def test_gigs_rejects_unbound_target_and_scope_conflict(tmp_path: Path) -> None:
    home, _target = _configured(tmp_path)
    unbound = tmp_path / "unbound"
    unbound.mkdir()
    runner = CliRunner()

    refusal = runner.invoke(
        cli,
        ["gigs", "--target", str(unbound), "--home", str(home), "--json"],
    )
    conflict = runner.invoke(
        cli,
        ["gigs", "--all", "--target", str(unbound), "--home", str(home), "--json"],
    )

    assert refusal.exit_code == 1
    assert json.loads(refusal.output)["error"]["code"] == "gigs_target_unbound"
    assert conflict.exit_code == 1
    assert json.loads(conflict.output)["error"]["code"] == "gigs_scope_conflict"


def test_gigs_isolates_malformed_registry_row(tmp_path: Path) -> None:
    home, target = _configured(tmp_path)
    first = create_offline(
        home_root=home,
        requested_target=target,
        name="valid-listing",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    registry_path = home / "registry.sqlite"
    connection = sqlite3.connect(registry_path)
    try:
        connection.execute(
            "INSERT INTO workpads(gig_id, project_id, workpad_locator) VALUES (?, ?, ?)",
            (
                "gig_00000000-0000-4000-8000-000000000002",
                first.project_id,
                "relative/workpad",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    result = CliRunner().invoke(
        cli,
        ["gigs", "--target", str(target), "--home", str(home), "--json"],
    )
    payload = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert [entry["gig_id"] for entry in payload["entries"]] == [first.gig_id]
    assert [item["code"] for item in payload["diagnostics"]] == ["registry_row_invalid"]


def test_gigs_keeps_registry_row_as_degraded_on_project_marker_mismatch(
    tmp_path: Path,
) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="marker-mismatch",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(created.workpad),
            "config",
            "--local",
            "gigai.project-id",
            "project_87654321-4321-4432-a321-cba987654321",
        ],
        check=True,
    )

    result = CliRunner().invoke(
        cli,
        ["gigs", "--target", str(target), "--home", str(home), "--json"],
    )
    payload = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert payload["entries"] == [
        {
            "gig_id": created.gig_id,
            "project_id": created.project_id,
            "title": "N/A Gig",
            "status": "N/A",
            "version": "N/A",
        }
    ]
    assert payload["diagnostics"][0]["code"] == "journal_project_id_mismatch"


def test_gigs_rejects_schema_valid_proposal_for_another_identity(tmp_path: Path) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="proposal-identity",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    proposal_path = created.workpad / "manifests" / "gig-proposal.json"
    proposal = parse_json_bytes(proposal_path.read_bytes())
    assert isinstance(proposal, dict)
    proposal["gig_id"] = "gig_87654321-4321-4432-a321-cba987654321"
    proposal["project_id"] = "project_87654321-4321-4432-a321-cba987654321"
    record_transition(
        workpad=created.workpad,
        project_id=created.project_id,
        gig_id=created.gig_id,
        handoff_id="handoff_87654321-4321-4432-a321-cba987654321",
        transition="gig_proposal_revised",
        body="Test-only schema-valid proposal identity mismatch.",
        artifacts=(
            JournalArtifact("manifests/gig-proposal.json", canonical_json_bytes(proposal)),
        ),
    )

    result = CliRunner().invoke(
        cli,
        ["gigs", "--target", str(target), "--home", str(home), "--json"],
    )
    payload = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert payload["entries"][0]["title"] == "N/A Gig"
    assert payload["entries"][0]["status"] == "N/A"
    assert payload["entries"][0]["version"] == "N/A"
    assert payload["diagnostics"][0]["code"] == "proposal_metadata_invalid"


def test_gigs_does_not_repair_stale_projection(tmp_path: Path) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="read-only-listing",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    state = created.workpad / "state.sqlite"
    state.write_bytes(b"stale projection")
    uncommitted = created.workpad / "review" / "uncommitted-note.txt"
    uncommitted.parent.mkdir()
    uncommitted.write_text("operator note\n", encoding="utf-8")
    before = state.read_bytes()

    result = CliRunner().invoke(
        cli,
        ["gigs", "--target", str(target), "--home", str(home), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert state.read_bytes() == before


def test_gigs_all_uses_safe_project_labels_and_approved_version(tmp_path: Path) -> None:
    home, first_target = _configured(tmp_path)
    second_target = tmp_path / "second-target"
    second_target.mkdir()
    initialize_target(
        home_root=home,
        requested_target=second_target,
        uuid_factory=lambda: uuid.UUID("87654321-4321-4432-a321-cba987654321"),
    )
    first = create_offline(
        home_root=home,
        requested_target=first_target,
        name="approved-listing",
        open_editor=False,
        uuid_factory=_uuids(),
    )
    approved = approve_offline(
        home_root=home,
        requested_target=first_target,
        proposal_id=first.proposal_id,
        uuid_factory=_uuids(),
    )
    second = create_offline(
        home_root=home,
        requested_target=second_target,
        name="other-listing",
        open_editor=False,
        uuid_factory=_uuids_from(100),
    )

    result = CliRunner().invoke(cli, ["gigs", "--all", "--home", str(home), "--json"])
    payload = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert payload["scope"] == {"kind": "all"}
    assert payload["entries"] == [
        {
            "gig_id": second.gig_id,
            "project_id": second.project_id,
            "project_label": "second-target [directory]",
            "title": "other-listing",
            "status": "Proposed",
            "version": "Proposed",
        },
        {
            "gig_id": first.gig_id,
            "project_id": first.project_id,
            "project_label": "target [directory]",
            "title": "approved-listing",
            "status": "Approved",
            "version": "v1",
        },
    ]
    assert approved.version == 1
    assert "/" not in payload["entries"][0]["project_label"]
