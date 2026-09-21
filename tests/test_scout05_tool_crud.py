from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gigai.canonical import digest_imported_bytes, parse_json_bytes
from gigai.capabilities import materialize_capability_manifest
from gigai.journal import run_with_journal_writer
from gigai.lifecycle import approve_offline, propose_graph_set_offline
from gigai.native_records import read_native_record
from gigai.private_records import migrate_workpad_layout
from gigai.scout_tools import ScoutToolError, dispatch_native_record_tool
from gigai.workpad import provision_workpad
from tests.test_scout02_graph_set_flow import _write_definition
from tests.test_scout03_c3_tools import _CAPABILITY, _MANIFEST, _manifest
from tests.test_scout04_external_recording import _fixture
from tests.test_scout04_input_integration import _profile


_REPO = Path(__file__).parents[1]
_WRAPPER = _REPO / "src/gigai/data/scout/gig.py"


def _copy_wrapper(workpad: Path) -> Path:
    destination = workpad / "gig.py"
    destination.write_bytes(_WRAPPER.read_bytes())
    return destination


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
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


def _crud_inventory(workpad: Path) -> list[dict[str, object]]:
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


def _approved_crud_tool(tmp_path: Path) -> tuple[Path, Path, str, Path, dict[str, object]]:
    home, target, gig_id, workpad, _posting = _fixture(tmp_path)
    inventory = _crud_inventory(workpad)
    manifest = _manifest(gig_id, inventory)
    binding = manifest["capabilities"][0]["tool_binding"]
    assert isinstance(binding, dict)
    binding["operations"] = ["record_archive", "record_create", "record_update"]
    materialize_capability_manifest(workpad, manifest)
    proposal = propose_graph_set_offline(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        definition_path=_write_definition(tmp_path / "crud-definition", workpad, gig_id, suffix="crud"),
    )
    approved = approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=proposal.proposal_id,
        capability_manifest_id=_MANIFEST,
    )
    invocation = {
        "gig_id": gig_id,
        "gig_version": approved.version,
        "capability_id": _CAPABILITY,
        "entry_path": inventory[1]["path"],
        "inventory_sha256": binding["inventory_sha256"],
        "effects": ["write_workpad"],
        "actor": {"kind": "agent", "id": "crud-agent"},
    }
    return home, target, gig_id, workpad, invocation


def _operation_paths(workpad: Path, gig_id: str) -> set[str]:
    layout = parse_json_bytes((workpad / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    snapshot = run_with_journal_writer(
        workpad=workpad,
        project_id=layout["project_id"],
        gig_id=gig_id,
        operation=lambda writer: writer.snapshot(("records/",)),
    )
    return set(snapshot.artifacts)


def _head(workpad: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_fresh_wrapper_executes_approved_create_update_archive_with_cas_and_replay(
    tmp_path: Path,
) -> None:
    home, target, gig_id, workpad, invocation = _approved_crud_tool(tmp_path)
    wrapper = _copy_wrapper(workpad)
    created = _run(
        wrapper, home, target, "create", "--capability-id", str(invocation["capability_id"]),
        "--actor-id", "crud-agent", "--operation-key", "crud-create",
        "--input-json", json.dumps({"content": _profile()}),
    )
    create_payload = _payload(created)
    assert created.returncode == 0, created.stderr
    first = create_payload["result"]["result"]  # type: ignore[index]
    record_id, revision_id = first["record_id"], first["revision_id"]  # type: ignore[index]

    update_args = (
        "update", str(record_id), "--parent-revision", str(revision_id),
        "--capability-id", str(invocation["capability_id"]), "--actor-id", "crud-agent",
        "--operation-key", "crud-update", "--input-json", json.dumps({"content": _profile()}),
    )
    updated = _run(wrapper, home, target, *update_args)
    update_payload = _payload(updated)
    assert updated.returncode == 0, updated.stderr
    second = update_payload["result"]["result"]  # type: ignore[index]
    assert second["record_id"] == record_id  # type: ignore[index]
    assert second["projection_pending"] is False  # type: ignore[index]

    replay = _run(wrapper, home, target, *update_args)
    replay_payload = _payload(replay)
    assert replay.returncode == 0, replay.stderr
    assert replay_payload["result"]["result"]["created"] is False  # type: ignore[index]
    assert replay_payload["result"]["result"]["revision_id"] == second["revision_id"]  # type: ignore[index]

    archive_args = (
        "archive", str(record_id), "--parent-revision", str(second["revision_id"]),
        "--capability-id", str(invocation["capability_id"]), "--actor-id", "crud-agent",
        "--operation-key", "crud-archive",
    )
    archived = _run(wrapper, home, target, *archive_args)
    archive_payload = _payload(archived)
    assert archived.returncode == 0, archived.stderr
    assert archive_payload["result"]["result"]["state"] == "archived"  # type: ignore[index]
    archive_replay = _run(wrapper, home, target, *archive_args)
    assert _payload(archive_replay)["result"]["result"]["created"] is False  # type: ignore[index]

    current = read_native_record(
        home_root=home, requested_target=target, gig_id=gig_id, record_id=str(record_id)
    )
    old = read_native_record(
        home_root=home, requested_target=target, gig_id=gig_id, record_id=str(record_id), revision_id=str(revision_id)
    )
    assert current["state"] == "archived"
    assert old["state"] == "active"
    assert any("record_update-" in path for path in _operation_paths(workpad, gig_id))
    assert any("record_archive-" in path for path in _operation_paths(workpad, gig_id))


def test_tool_crud_refuses_stale_mutated_and_malformed_operations_before_publication(
    tmp_path: Path,
) -> None:
    home, target, gig_id, workpad, invocation = _approved_crud_tool(tmp_path)
    wrapper = _copy_wrapper(workpad)
    create = _payload(_run(
        wrapper, home, target, "create", "--capability-id", str(invocation["capability_id"]),
        "--actor-id", "crud-agent", "--operation-key", "reject-create",
        "--input-json", json.dumps({"content": _profile()}),
    ))
    first = create["result"]["result"]  # type: ignore[index]
    before_head, before_paths = _head(workpad), _operation_paths(workpad, gig_id)
    stale = _run(
        wrapper, home, target, "archive", str(first["record_id"]),
        "--parent-revision", "revision_00000000-0000-4000-8000-000000000099",
        "--capability-id", str(invocation["capability_id"]), "--actor-id", "crud-agent",
        "--operation-key", "stale-archive",
    )
    assert stale.returncode == 2
    assert _payload(stale)["error"]["code"] == "stale_parent"  # type: ignore[index]
    assert _head(workpad) == before_head
    assert _operation_paths(workpad, gig_id) == before_paths

    forged = {
        **invocation,
        "operation": "record_delete",
        "operation_key": "malformed-operation",
        "origin": "agent_supplied",
        "content": _profile(),
    }
    with pytest.raises(ScoutToolError):
        dispatch_native_record_tool(
            home_root=home, requested_target=target, gig_id=gig_id, invocation=forged
        )
    assert _head(workpad) == before_head

    mutated = {
        **invocation,
        "operation": "record_update",
        "operation_key": "mutated-update",
        "origin": "agent_supplied",
        "record_id": first["record_id"],
        "parent_revision": first["revision_id"],
        "content": _profile(),
    }
    schema = workpad / "tools" / _CAPABILITY / "operation.schema.json"
    with pytest.raises(ScoutToolError, match="approved tool source changed"):
        dispatch_native_record_tool(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            invocation=mutated,
            _before_publication=lambda: schema.write_text("{}\n", encoding="utf-8"),
        )
    assert _head(workpad) == before_head
    assert _operation_paths(workpad, gig_id) == before_paths


def test_wrapper_crud_is_bound_to_its_own_gig_not_active_or_foreign_gig(
    tmp_path: Path,
) -> None:
    home, target, gig_a, workpad_a, invocation = _approved_crud_tool(tmp_path)
    layout = parse_json_bytes((workpad_a / "manifests/workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    gig_b = "gig_00000000-0000-4000-8000-000000000077"
    workpad_b = provision_workpad(home_root=home, project_id=layout["project_id"], gig_id=gig_b).path
    migrate_workpad_layout(workpad=workpad_b, project_id=layout["project_id"], gig_id=gig_b)
    wrapper_a, wrapper_b = _copy_wrapper(workpad_a), _copy_wrapper(workpad_b)
    a_create = _run(
        wrapper_a, home, target, "create", "--capability-id", str(invocation["capability_id"]),
        "--actor-id", "crud-agent", "--operation-key", "gig-a-create",
        "--input-json", json.dumps({"content": _profile()}),
    )
    assert a_create.returncode == 0, a_create.stderr
    foreign = _run(
        wrapper_b, home, target, "archive", "record_00000000-0000-4000-8000-000000000001",
        "--parent-revision", "revision_00000000-0000-4000-8000-000000000001",
        "--capability-id", str(invocation["capability_id"]), "--actor-id", "crud-agent",
        "--operation-key", "gig-b-foreign-archive",
    )
    assert foreign.returncode == 2
    assert _payload(foreign)["error"]["code"] in {  # type: ignore[index]
        "tool_authority_unavailable",
        "tool_capability_refused",
    }
    assert _operation_paths(workpad_b, gig_b) == set()
