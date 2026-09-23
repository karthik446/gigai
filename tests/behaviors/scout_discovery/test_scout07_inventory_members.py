"""Focused exact-member validation for Scout software inventory reuse."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from gigai.canonical import parse_json_bytes
from gigai.default_init import initialize_defaults
import gigai.scout.materialization as materialization
from gigai.scout.materialization import _inventory_member_rows, _validate_existing_inventory, ScoutMaterializationError
from gigai.scout.template import scout_candidate_inventory, scout_catalog_candidate
from gigai.setup import build_config, run_setup


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", target], check=True)
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    return home, target


def _materialized(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    home, target = _setup(tmp_path)
    initialize_defaults(home_root=home, requested_target=target, username="owner", inventory=scout_candidate_inventory())
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    inventory_path = next(workpad.glob("manifests/software/*/source-inventory.json"))
    return home, target, workpad, parse_json_bytes(inventory_path.read_bytes())


def _all_materialized_members(workpad: Path) -> dict[str, bytes]:
    inventory_path = next(workpad.glob("manifests/software/*/source-inventory.json"))
    prefix = inventory_path.parent
    return {
        path.relative_to(prefix).as_posix(): path.read_bytes()
        for path in prefix.rglob("*")
        if path.is_file()
        and path.name != "source-inventory.json"
    }


def test_real_materialization_reuse_is_exact_and_idempotent(tmp_path: Path) -> None:
    home, target, _workpad, first_payload = _materialized(tmp_path)
    second = initialize_defaults(home_root=home, requested_target=target, username=None, inventory=scout_candidate_inventory())
    assert second.instances[0].status == "approval_required"
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    inventory_path = next(workpad.glob("manifests/software/*/source-inventory.json"))
    assert parse_json_bytes(inventory_path.read_bytes()) == first_payload
    assert first_payload["members"] == sorted(first_payload["members"], key=lambda item: item["path"])


def test_public_materialization_reuses_historical_ids_time_and_prepared_manifest(tmp_path: Path) -> None:
    home, target, workpad, first_payload = _materialized(tmp_path)
    project_id, gig_id = workpad.parent.parent.name, workpad.name
    first_members = _all_materialized_members(workpad)
    first_proposal = (workpad / "manifests/gig-proposal.json").read_bytes()
    first_head = subprocess.run(["git", "-C", str(workpad), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    result = materialization.materialize_scout_candidate(home_root=home, requested_target=target, workpad=workpad, project_id=project_id, gig_id=gig_id, entry=scout_catalog_candidate())
    second_head = subprocess.run(["git", "-C", str(workpad), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    assert second_head == first_head
    assert result.inventory_ref["content_sha256"] == materialization._artifact_ref(next(workpad.glob("manifests/software/*/source-inventory.json")).relative_to(workpad).as_posix(), "application/json", (workpad / result.inventory_ref["path"]).read_bytes())["content_sha256"]
    assert (workpad / "manifests/gig-proposal.json").read_bytes() == first_proposal
    assert _all_materialized_members(workpad) == first_members
    prepared = [path for path in first_members if path.endswith("compiled/prepared-capability-manifest.json")]
    assert prepared and first_members[prepared[0]] == _all_materialized_members(workpad)[prepared[0]]


@pytest.mark.parametrize("mutation", ["omitted", "extra", "duplicate", "digest", "size"])
def test_public_materialization_refuses_one_journaled_malformed_inventory_before_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    home, target = _setup(tmp_path)
    original = materialization._inventory_member_rows

    def malformed(members):
        rows = original(members)
        if mutation == "omitted":
            return rows[:-1]
        if mutation == "extra":
            return rows + [{"path": "extra/member", "content_sha256": "sha256:" + "0" * 64, "size_bytes": 0}]
        if mutation == "duplicate":
            return rows + [dict(rows[0])]
        if mutation == "digest":
            return [dict(rows[0], content_sha256="sha256:" + "0" * 64)] + rows[1:]
        return [dict(rows[0], size_bytes=rows[0]["size_bytes"] + 1)] + rows[1:]

    monkeypatch.setattr(materialization, "_inventory_member_rows", malformed)
    first = initialize_defaults(home_root=home, requested_target=target, username="owner", inventory=scout_candidate_inventory())
    assert first.instances[0].status == "approval_required"
    workpad = next((tmp_path / "workpads").glob("projects/*/gigs/*"))
    project_id, gig_id = workpad.parent.parent.name, workpad.name
    before_head = subprocess.run(["git", "-C", str(workpad), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(materialization, "_inventory_member_rows", original)
    with pytest.raises(ScoutMaterializationError):
        materialization.materialize_scout_candidate(home_root=home, requested_target=target, workpad=workpad, project_id=project_id, gig_id=gig_id, entry=scout_catalog_candidate())
    after_head = subprocess.run(["git", "-C", str(workpad), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    assert after_head == before_head


@pytest.mark.parametrize("mutation", ["omitted", "extra", "digest", "size", "duplicate"])
def test_inventory_member_corruption_is_not_an_exact_reuse(tmp_path: Path, mutation: str) -> None:
    _home, _target, workpad, payload = _materialized(tmp_path)
    members = payload["members"]
    expected = _inventory_member_rows(_all_materialized_members(workpad))
    assert members == expected
    corrupted = [dict(row) for row in members]
    if mutation == "omitted":
        corrupted.pop()
    elif mutation == "extra":
        corrupted.append(dict(corrupted[0], path="extra/member", content_sha256="sha256:" + "0" * 64, size_bytes=0))
    elif mutation == "digest":
        corrupted[0]["content_sha256"] = "sha256:" + "0" * 64
    elif mutation == "size":
        corrupted[0]["size_bytes"] += 1
    else:
        corrupted.append(dict(corrupted[0]))
    assert corrupted != expected
    inventory_path = next(workpad.glob("manifests/software/*/source-inventory.json"))
    with pytest.raises(ScoutMaterializationError):
        _validate_existing_inventory(
            workpad,
            workpad.parent.parent.name,
            workpad.name,
            inventory_path.relative_to(workpad).as_posix(),
            dict(payload, members=corrupted),
            inventory_path.read_bytes(),
        )
