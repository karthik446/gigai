from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gigai.capabilities import validate_capability_manifest
from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.default_init import initialize_defaults
from gigai.lifecycle import approve_offline
from gigai.project_binding import load_project_binding
from gigai.scout.bundled_tools import (
    SCOUT_CRUD_CAPABILITY_ID,
    SCOUT_CRUD_ENTRY_PATH,
    SCOUT_CRUD_MANIFEST_ID,
    SCOUT_CRUD_SCHEMA_PATH,
)
from gigai.scout.template import scout_candidate_inventory, scout_source_files
from gigai.scout.tools import ScoutToolError, _inventory
from gigai.setup import build_config, run_setup


_REPO = Path(__file__).parents[3]


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", target],
        check=True,
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


def _candidate(tmp_path: Path):
    home, target = _setup(tmp_path)
    result = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=scout_candidate_inventory(),
    )
    instance = result.instances[0]
    assert instance.proposal_id is not None
    workpad = next(
        path
        for path in (tmp_path / "workpads").glob("projects/*/gigs/*")
        if path.name == instance.gig_id
    )
    return home, target, instance, workpad


def _run(wrapper: Path, home: Path, target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(_REPO / "src") + os.pathsep + environment.get(
        "PYTHONPATH", ""
    )
    return subprocess.run(
        [sys.executable, str(wrapper), "--home", str(home), "--target", str(target), *args],
        cwd=_REPO,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.stdout.strip(), result.stderr
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def _profile() -> dict[str, object]:
    def fact(value: str, *, state: str = "known", context: str | None = None) -> dict[str, object]:
        return {
            "state": state,
            "value": value if state == "known" else None,
            "context": context,
            "provenance": {"kind": "user_reported", "source_refs": []},
            "conflict_refs": [],
        }

    return {
        "schema_version": "1.0",
        "kind": "profile_preferences",
        "scope": {"mode": "saved_default", "task_context_id": None, "base": None},
        "payload": {
            "hard_constraints": {
                "geography": fact("Denver"),
                "work_mode": fact("remote"),
                "seniority": fact("senior"),
                "employment_type": fact("full_time"),
                "compensation": fact("120000 USD annual"),
            },
            "soft_priorities": {"industry": fact("climate")},
            "sponsorship_need": fact("needs_sponsorship"),
            "employer_sponsorship": fact("", state="unknown"),
            "eligibility": fact("eligible", context="US work authorization"),
        },
    }


def _create_args(operation_key: str) -> tuple[str, ...]:
    return (
        "create",
        "--capability-id",
        SCOUT_CRUD_CAPABILITY_ID,
        "--actor-id",
        "bundled-tools-agent",
        "--operation-key",
        operation_key,
        "--input-json",
        json.dumps({"content": _profile()}),
    )


def _prepared_manifest(workpad: Path) -> dict[str, object]:
    value = parse_json_bytes(
        (workpad / "manifests/capabilities" / f"{SCOUT_CRUD_MANIFEST_ID}.json").read_bytes()
    )
    assert isinstance(value, dict)
    return value


def _compiled_goal_ids(workpad: Path) -> list[str]:
    # Five of the six compiled selectors are single-Goal graphs; the sealed
    # find-jobs-functional graph (Amendment 02 D11/I-1) is a three-Goal
    # acquire/assess/present traversal compiled into the same candidate. The
    # CRUD capability manifest's goal_ids is every compiled graph's Goal IDs,
    # not one per graph, so this collects all of them rather than assuming a
    # fixed count per graph.
    values: list[str] = []
    for path in workpad.glob("manifests/software/*/compiled/*/goal-graph.json"):
        graph = parse_json_bytes(path.read_bytes())
        assert isinstance(graph, dict)
        goals = graph["goals"]
        assert isinstance(goals, list) and len(goals) >= 1
        values.extend(goal["goal_id"] for goal in goals)
    return sorted(values)


def test_candidate_copies_bundled_tool_assets_and_pins_pending_manifest_to_real_goals(
    tmp_path: Path,
) -> None:
    _home, target, instance, workpad = _candidate(tmp_path)
    source = scout_source_files()
    for path in ("gig.py", SCOUT_CRUD_ENTRY_PATH, SCOUT_CRUD_SCHEMA_PATH):
        assert (workpad / path).read_bytes() == source[path]

    manifest = _prepared_manifest(workpad)
    assert validate_capability_manifest(canonical_json_bytes(manifest)).valid
    capability = manifest["capabilities"][0]
    assert capability["goal_ids"] == _compiled_goal_ids(workpad)
    assert capability["availability_state"] == "missing"
    assert capability["security_review"]["status"] == "pending"
    assert capability["options"][0]["decision"] == "pending"
    binding = capability["tool_binding"]
    assert binding["entry_path"] == SCOUT_CRUD_ENTRY_PATH
    assert binding["wrapper_ref"]["path"] == "gig.py"
    assert [member["path"] for member in binding["inventory"]] == sorted(
        member["path"] for member in binding["inventory"]
    )
    assert load_project_binding(target).active_gig_id is None
    assert instance.status == "approval_required"


def test_root_wrapper_inventory_is_closed_and_requires_explicit_binding(tmp_path: Path) -> None:
    _home, _target, _instance, workpad = _candidate(tmp_path)
    manifest = _prepared_manifest(workpad)
    missing_binding = deepcopy(manifest)
    del missing_binding["capabilities"][0]["tool_binding"]["wrapper_ref"]
    assert not validate_capability_manifest(canonical_json_bytes(missing_binding)).valid

    foreign_root = deepcopy(manifest)
    foreign_root["capabilities"][0]["tool_binding"]["inventory"][0]["path"] = "README.md"
    assert not validate_capability_manifest(canonical_json_bytes(foreign_root)).valid

    traversal = deepcopy(manifest)
    traversal["capabilities"][0]["tool_binding"]["inventory"][0]["path"] = "tools/../gig.py"
    assert not validate_capability_manifest(canonical_json_bytes(traversal)).valid


def test_pending_manifest_refuses_copied_wrapper_before_and_after_graph_approval(
    tmp_path: Path,
) -> None:
    home, target, instance, workpad = _candidate(tmp_path)
    before = _run(workpad / "gig.py", home, target, *_create_args("before-approval"))
    assert before.returncode == 2
    assert _payload(before)["error"]["code"] == "tool_authority_unavailable"  # type: ignore[index]

    approval = approve_offline(
        home_root=home,
        requested_target=target,
        gig_id=instance.gig_id,
        proposal_id=str(instance.proposal_id),
        capability_manifest_id=SCOUT_CRUD_MANIFEST_ID,
    )
    assert approval.version == 1
    assert load_project_binding(target).active_gig_id is None
    pending = _run(workpad / "gig.py", home, target, *_create_args("pending-review"))
    assert pending.returncode == 2
    assert _payload(pending)["error"]["code"] == "tool_capability_refused"  # type: ignore[index]
    assert not list((workpad / "records/operations").glob("*.json"))

    active_before = (workpad / "manifests/active-gig-version.json").read_bytes()
    rerun = initialize_defaults(
        home_root=home,
        requested_target=target,
        username=None,
        inventory=scout_candidate_inventory(),
    )
    assert rerun.instances[0].gig_id == instance.gig_id
    assert rerun.instances[0].status == "capability_review_required"
    assert "capability review" in rerun.instances[0].next_action
    assert rerun.scout_status == "capability_review_required"
    assert (workpad / "manifests/active-gig-version.json").read_bytes() == active_before


def test_root_wrapper_member_is_revalidated_against_bundled_pending_inventory(
    tmp_path: Path,
) -> None:
    _home, _target, _instance, workpad = _candidate(tmp_path)
    binding = _prepared_manifest(workpad)["capabilities"][0]["tool_binding"]
    inventory = _inventory(workpad, SCOUT_CRUD_CAPABILITY_ID, binding)
    assert inventory[0]["path"] == "gig.py"
    (workpad / "gig.py").write_bytes(b"# changed source\n")
    with pytest.raises(ScoutToolError, match="approved tool source changed"):
        _inventory(workpad, SCOUT_CRUD_CAPABILITY_ID, binding)


def test_bundled_tool_customization_is_preserved_and_pending_manifest_recovers_from_snapshot(
    tmp_path: Path,
) -> None:
    home, target, instance, workpad = _candidate(tmp_path)
    customized = b"# local Scout customization remains unapproved\n"
    entry = workpad / SCOUT_CRUD_ENTRY_PATH
    entry.write_bytes(customized)
    manifest_path = workpad / "manifests/capabilities" / f"{SCOUT_CRUD_MANIFEST_ID}.json"
    manifest_path.unlink()

    resumed = initialize_defaults(
        home_root=home,
        requested_target=target,
        username=None,
        inventory=scout_candidate_inventory(),
    )
    assert resumed.instances[0].gig_id == instance.gig_id
    assert resumed.instances[0].proposal_id == instance.proposal_id
    assert entry.read_bytes() == customized
    assert _prepared_manifest(workpad)["manifest_id"] == SCOUT_CRUD_MANIFEST_ID


def test_upstream_candidate_change_reports_update_without_repairing_or_adopting_source(
    tmp_path: Path,
) -> None:
    home, target, instance, workpad = _candidate(tmp_path)
    entry = scout_candidate_inventory()[0]
    updated_entry = replace(entry, definition_version="1.1")
    before_inventory = next(workpad.glob("manifests/software/*/source-inventory.json")).read_bytes()
    before_tool = (workpad / SCOUT_CRUD_ENTRY_PATH).read_bytes()

    rerun = initialize_defaults(
        home_root=home,
        requested_target=target,
        username=None,
        inventory=(updated_entry,),
    )
    assert rerun.instances[0].gig_id == instance.gig_id
    assert rerun.instances[0].status == "update_available"
    assert (workpad / SCOUT_CRUD_ENTRY_PATH).read_bytes() == before_tool
    assert next(workpad.glob("manifests/software/*/source-inventory.json")).read_bytes() == before_inventory
