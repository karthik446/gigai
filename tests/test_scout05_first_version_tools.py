from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.capabilities import materialize_capability_manifest
from gigai.default_init import initialize_defaults
from gigai.journal import run_with_journal_writer
from gigai.lifecycle import approve_offline
from gigai.project_binding import load_project_binding
from gigai.scout_template import scout_candidate_inventory
from gigai.scout_tools import ScoutToolError, dispatch_native_record_tool
from gigai.setup import build_config, run_setup
from gigai.validators import validate_serialized_contract


_REPO = Path(__file__).parents[1]
_BUNDLED_WRAPPER = _REPO / "src/gigai/data/scout/gig.py"
_CAPABILITY = "cap_00000000-0000-4000-8000-000000000061"
_MANIFEST = "capmanifest_00000000-0000-4000-8000-000000000062"
_GOAL = "goal_00000000-0000-4000-8000-000000000063"


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


def _run(wrapper: Path, home: Path, target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source_root = str(_REPO / "src")
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
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


def _tool_inventory(workpad: Path) -> list[dict[str, object]]:
    root = workpad / "tools" / _CAPABILITY
    root.mkdir(parents=True)
    source = b'''from gigai.scout_tool_adapter import (\
    native_record_archive_operation, native_record_create_operation, native_record_update_operation,\
)\n\n\
def build_native_record_operation(context):\n\
    operation = context["operation"]\n\
    values = context["input"]\n\
    if operation == "record_create":\n\
        return native_record_create_operation(operation_key=context["operation_key"], content=values["content"])\n\
    if operation == "record_update":\n\
        return native_record_update_operation(operation_key=context["operation_key"], record_id=values["record_id"], parent_revision=values["parent_revision"], content=values["content"])\n\
    if operation == "record_archive":\n\
        return native_record_archive_operation(operation_key=context["operation_key"], record_id=values["record_id"], parent_revision=values["parent_revision"])\n\
    raise ValueError("unsupported operation")\n'''
    schema = b'{"type":"object","additionalProperties":false}\n'
    (root / "record_tool.py").write_bytes(source)
    (root / "operation.schema.json").write_bytes(schema)
    return [
        {
            "path": f"tools/{_CAPABILITY}/operation.schema.json",
            "content_sha256": digest_imported_bytes(schema),
            "media_type": "application/schema+json",
            "size_bytes": len(schema),
        },
        {
            "path": f"tools/{_CAPABILITY}/record_tool.py",
            "content_sha256": digest_imported_bytes(source),
            "media_type": "text/x-python",
            "size_bytes": len(source),
        },
    ]


def _manifest(gig_id: str, inventory: list[dict[str, object]]) -> dict[str, object]:
    entry = inventory[1]
    return {
        "schema_version": "1.0",
        "manifest_id": _MANIFEST,
        "manifest_version": 1,
        "gig_id": gig_id,
        "created_at": "2026-09-09T00:00:00Z",
        "created_by": {"kind": "gigai", "id": "first-version-tool-test", "model_target": None},
        "capabilities": [
            {
                "capability_id": _CAPABILITY,
                "goal_ids": [_GOAL],
                "kind": "tool",
                "name": "native-record-tool",
                "requested_version": "1.0",
                "source_constraints": {
                    "allowed_source_kinds": ["installed"],
                    "required_digest": entry["content_sha256"],
                    "required_identity": "record_tool.py",
                },
                "declared_effects": ["write_workpad"],
                "permissions": {
                    "filesystem": "write_isolated",
                    "network": "none",
                    "credentials": "none",
                },
                "credential_requirements": [],
                "network_requirement": "none",
                "availability_state": "available",
                "compatibility": {"status": "compatible", "reason": None},
                "security_review": {"status": "passed", "checks": ["inventory"], "reason": None},
                "alternatives": [],
                "options": [
                    {
                        "option_id": "A",
                        "kind": "use_available",
                        "label": "Use approved inventory",
                        "ordinal": 0,
                        "decision": "pending",
                    }
                ],
                "tool_binding": {
                    "entry_path": entry["path"],
                    "inventory": inventory,
                    "inventory_sha256": digest_imported_bytes(canonical_json_bytes(inventory)),
                    "operations": ["record_archive", "record_create", "record_update"],
                    "effects": ["write_workpad"],
                },
            }
        ],
    }


def _operation_paths(workpad: Path, gig_id: str) -> set[str]:
    layout = parse_json_bytes((workpad / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    snapshot = run_with_journal_writer(
        workpad=workpad,
        project_id=layout["project_id"],
        gig_id=gig_id,
        operation=lambda writer: writer.snapshot(("records/operations/",)),
    )
    return set(snapshot.artifacts)


def _receipt(workpad: Path, gig_id: str) -> dict[str, object]:
    layout = parse_json_bytes((workpad / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    snapshot = run_with_journal_writer(
        workpad=workpad,
        project_id=layout["project_id"],
        gig_id=gig_id,
        operation=lambda writer: writer.snapshot(("records/operations/",)),
    )
    assert len(snapshot.artifacts) == 1
    value = parse_json_bytes(next(iter(snapshot.artifacts.values())))
    assert isinstance(value, dict)
    return value


def _fresh_candidate(tmp_path: Path) -> tuple[Path, Path, str, str, Path, list[dict[str, object]]]:
    home, target = _setup(tmp_path)
    prepared = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=scout_candidate_inventory(),
    )
    instance = prepared.instances[0]
    assert instance.status == "approval_required"
    assert instance.proposal_id is not None
    assert load_project_binding(target).active_gig_id is None
    workpad = next(path for path in (tmp_path / "workpads").glob("projects/*/gigs/*") if path.name == instance.gig_id)
    assert (workpad / "gig.py").read_bytes() == _BUNDLED_WRAPPER.read_bytes()
    inventory = _tool_inventory(workpad)
    materialize_capability_manifest(workpad, _manifest(instance.gig_id, inventory))
    return home, target, instance.gig_id, instance.proposal_id, workpad, inventory


def _approve_first_version(
    home: Path,
    target: Path,
    gig_id: str,
    proposal_id: str,
) -> None:
    approval = approve_offline(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        proposal_id=proposal_id,
        capability_manifest_id=_MANIFEST,
    )
    assert approval.gig_id == gig_id
    assert approval.version == 1
    assert load_project_binding(target).active_gig_id is None


def _create_args(operation_key: str, capability_id: str = _CAPABILITY) -> tuple[str, ...]:
    return (
        "create",
        "--capability-id",
        capability_id,
        "--actor-id",
        "first-version-agent",
        "--operation-key",
        operation_key,
        "--input-json",
        json.dumps({"content": _profile()}),
    )


def test_real_copied_wrapper_requires_explicit_first_version_approval_and_authority(
    tmp_path: Path,
) -> None:
    home, target, gig_id, proposal_id, workpad, _inventory = _fresh_candidate(tmp_path)
    wrapper = workpad / "gig.py"
    pending = _run(wrapper, home, target, *_create_args("before-approval"))
    assert pending.returncode == 2
    assert _payload(pending)["error"]["code"] == "tool_authority_unavailable"  # type: ignore[index]
    assert _operation_paths(workpad, gig_id) == set()

    _approve_first_version(home, target, gig_id, proposal_id)
    wrong = _run(wrapper, home, target, *_create_args("wrong-capability", "cap_00000000-0000-4000-8000-000000000099"))
    assert wrong.returncode == 2
    assert _payload(wrong)["error"]["code"] == "tool_capability_refused"  # type: ignore[index]
    assert _operation_paths(workpad, gig_id) == set()


def test_real_copied_wrapper_commits_first_version_create_update_archive_and_replay(
    tmp_path: Path,
) -> None:
    home, target, gig_id, proposal_id, workpad, _inventory = _fresh_candidate(tmp_path)
    _approve_first_version(home, target, gig_id, proposal_id)
    wrapper = workpad / "gig.py"

    created = _run(wrapper, home, target, *_create_args("first-version-create"))
    assert created.returncode == 0, created.stderr
    first = _payload(created)["result"]["result"]  # type: ignore[index]
    record_id, revision_id = first["record_id"], first["revision_id"]  # type: ignore[index]

    update_args = (
        "update",
        str(record_id),
        "--parent-revision",
        str(revision_id),
        "--capability-id",
        _CAPABILITY,
        "--actor-id",
        "first-version-agent",
        "--operation-key",
        "first-version-update",
        "--input-json",
        json.dumps({"content": _profile()}),
    )
    updated = _run(wrapper, home, target, *update_args)
    assert updated.returncode == 0, updated.stderr
    second = _payload(updated)["result"]["result"]  # type: ignore[index]
    replay = _run(wrapper, home, target, *update_args)
    assert replay.returncode == 0, replay.stderr
    assert _payload(replay)["result"]["result"]["created"] is False  # type: ignore[index]

    archive_args = (
        "archive",
        str(record_id),
        "--parent-revision",
        str(second["revision_id"]),
        "--capability-id",
        _CAPABILITY,
        "--actor-id",
        "first-version-agent",
        "--operation-key",
        "first-version-archive",
    )
    archived = _run(wrapper, home, target, *archive_args)
    assert archived.returncode == 0, archived.stderr
    assert _payload(archived)["result"]["result"]["state"] == "archived"  # type: ignore[index]

    receipts = _operation_paths(workpad, gig_id)
    assert len(receipts) == 3
    layout = parse_json_bytes((workpad / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    snapshot = run_with_journal_writer(
        workpad=workpad,
        project_id=layout["project_id"],
        gig_id=gig_id,
        operation=lambda writer: writer.snapshot(("records/operations/",)),
    )
    for path in receipts:
        assert validate_serialized_contract(
            "scout-operation-receipt.schema.json", snapshot.artifacts[path]
        ).valid


@pytest.mark.parametrize("version", [0, -1])
def test_first_version_receipt_schema_refuses_non_positive_version(
    tmp_path: Path,
    version: int,
) -> None:
    home, target, gig_id, proposal_id, workpad, _inventory = _fresh_candidate(tmp_path)
    _approve_first_version(home, target, gig_id, proposal_id)
    created = _run(workpad / "gig.py", home, target, *_create_args("schema-receipt"))
    assert created.returncode == 0, created.stderr
    receipt = _receipt(workpad, gig_id)
    assert receipt["tool_binding"]["gig_version"] == 1  # type: ignore[index]
    malformed = deepcopy(receipt)
    malformed["tool_binding"]["gig_version"] = version  # type: ignore[index]
    assert not validate_serialized_contract(
        "scout-operation-receipt.schema.json", canonical_json_bytes(malformed)
    ).valid


def test_first_version_tool_refuses_stale_source_and_foreign_gig_without_receipt(
    tmp_path: Path,
) -> None:
    home, target, gig_id, proposal_id, workpad, inventory = _fresh_candidate(tmp_path)
    _approve_first_version(home, target, gig_id, proposal_id)
    before = _operation_paths(workpad, gig_id)
    forged = {
        "gig_id": "gig_00000000-0000-4000-8000-000000000099",
        "gig_version": 1,
        "capability_id": _CAPABILITY,
        "entry_path": inventory[1]["path"],
        "inventory_sha256": _manifest(gig_id, inventory)["capabilities"][0]["tool_binding"]["inventory_sha256"],  # type: ignore[index]
        "operation": "record_create",
        "effects": ["write_workpad"],
        "actor": {"kind": "agent", "id": "first-version-agent"},
        "operation_key": "foreign-gig",
        "origin": "agent_supplied",
        "content": _profile(),
    }
    with pytest.raises(ScoutToolError, match="another Gig"):
        dispatch_native_record_tool(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            invocation=forged,
        )
    assert _operation_paths(workpad, gig_id) == before

    (workpad / "tools" / _CAPABILITY / "operation.schema.json").write_bytes(b'{"type":"array"}\n')
    stale = _run(workpad / "gig.py", home, target, *_create_args("stale-source"))
    assert stale.returncode == 2
    assert _payload(stale)["error"]["code"] == "tool_inventory_changed"  # type: ignore[index]
    assert _operation_paths(workpad, gig_id) == before
