from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.capabilities import (
    CapabilityManifestError,
    materialize_capability_manifest,
    validate_capability_manifest,
)
from gigai.journal import run_with_journal_writer
from gigai.lifecycle import approve_offline, propose_graph_set_offline
from gigai.native_records import create_native_record, create_native_record_from_tool
from gigai.scout.tools import (
    ScoutToolError,
    dispatch_native_record_tool,
)
from tests.behaviors.scout_proposals_tools.test_scout02_graph_set_flow import _write_definition
from tests.behaviors.scout_proposals_tools.test_scout04_external_recording import _fixture
from tests.behaviors.scout_proposals_tools.test_scout04_input_integration import _profile


_CAPABILITY = "cap_00000000-0000-4000-8000-000000000041"
_MANIFEST = "capmanifest_00000000-0000-4000-8000-000000000042"
_GOAL = "goal_00000000-0000-4000-8000-000000000043"


def _inventory(workpad: Path) -> tuple[list[dict[str, object]], bytes, bytes]:
    root = workpad / "tools" / _CAPABILITY
    root.mkdir(parents=True)
    source = (
        b"from gigai.scout.tool_adapter import native_record_create_operation\n\n"
        b"def build_native_record_operation(context):\n"
        b"    return native_record_create_operation(\n"
        b"        operation_key=context['operation_key'],\n"
        b"        content=context['input']['content'],\n"
        b"    )\n"
    )
    schema = b'{"type":"object","additionalProperties":false}\n'
    (root / "record_tool.py").write_bytes(source)
    (root / "operation.schema.json").write_bytes(schema)
    items = [
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
    return items, source, schema


def _manifest(gig_id: str, inventory: list[dict[str, object]]) -> dict[str, object]:
    entry = inventory[1]
    inventory_sha256 = digest_imported_bytes(canonical_json_bytes(inventory))
    return {
        "schema_version": "1.0",
        "manifest_id": _MANIFEST,
        "manifest_version": 1,
        "gig_id": gig_id,
        "created_at": "2026-09-09T00:00:00Z",
        "created_by": {"kind": "gigai", "id": "scout-c3-test", "model_target": None},
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
                "permissions": {"filesystem": "write_isolated", "network": "none", "credentials": "none"},
                "credential_requirements": [],
                "network_requirement": "none",
                "availability_state": "available",
                "compatibility": {"status": "compatible", "reason": None},
                "security_review": {"status": "passed", "checks": ["inventory"], "reason": None},
                "alternatives": [],
                "options": [
                    {"option_id": "A", "kind": "use_available", "label": "Use approved inventory", "ordinal": 0, "decision": "pending"}
                ],
                "tool_binding": {
                    "entry_path": entry["path"],
                    "inventory": inventory,
                    "inventory_sha256": inventory_sha256,
                    "operations": ["record_create"],
                    "effects": ["write_workpad"],
                },
            }
        ],
    }


def _approved_tool(tmp_path: Path) -> tuple[Path, Path, str, Path, dict[str, object]]:
    home, target, gig_id, workpad, _posting = _fixture(tmp_path)
    inventory, _source, _schema = _inventory(workpad)
    entry = inventory[1]
    manifest = _manifest(gig_id, inventory)
    materialize_capability_manifest(workpad, manifest)
    proposal = propose_graph_set_offline(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        definition_path=_write_definition(tmp_path / "tool-definition", workpad, gig_id, suffix="tool"),
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
        "entry_path": entry["path"],
        "inventory_sha256": manifest["capabilities"][0]["tool_binding"]["inventory_sha256"],
        "operation": "record_create",
        "effects": ["write_workpad"],
        "actor": {"kind": "agent", "id": "approved-tool-agent"},
        "operation_key": "approved-tool-create",
        "origin": "agent_supplied",
        "content": _profile(),
    }
    return home, target, gig_id, workpad, invocation


def _receipt(workpad: Path, home: Path, target: Path, gig_id: str) -> dict[str, object]:
    snapshot = run_with_journal_writer(
        workpad=workpad,
        project_id=parse_json_bytes((workpad / "manifests" / "workpad-layout.json").read_bytes())["project_id"],
        gig_id=gig_id,
        operation=lambda writer: writer.snapshot(("records/operations/",)),
    )
    path = next(path for path in snapshot.artifacts if path.startswith("records/operations/record_create-"))
    value = parse_json_bytes(snapshot.artifacts[path])
    assert isinstance(value, dict)
    return value


def test_approved_gig_tool_dispatches_only_through_native_journal_service(tmp_path: Path) -> None:
    home, target, gig_id, workpad, invocation = _approved_tool(tmp_path)
    result = dispatch_native_record_tool(
        home_root=home, requested_target=target, gig_id=gig_id, invocation=invocation
    )
    assert result.created
    receipt = _receipt(workpad, home, target, gig_id)
    assert receipt["operation"] == "record_create"
    assert receipt["tool_binding"]["capability_id"] == _CAPABILITY
    assert receipt["tool_binding"]["actor"] == invocation["actor"]
    replay = dispatch_native_record_tool(
        home_root=home, requested_target=target, gig_id=gig_id, invocation=invocation
    )
    assert not replay.created

    direct = create_native_record(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        content=_profile(),
        actor={"kind": "operator", "id": "local-user"},
        origin="user_reported",
        operation_key="direct-built-in",
    )
    assert direct.created


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("gig_version", 2),
        lambda value: value.__setitem__("gig_id", "gig_00000000-0000-4000-8000-000000000099"),
        lambda value: value.__setitem__("capability_id", "cap_00000000-0000-4000-8000-000000000099"),
        lambda value: value.__setitem__("inventory_sha256", "sha256:" + "0" * 64),
        lambda value: value.__setitem__("effects", ["write_workpad", "network"]),
        lambda value: value.__setitem__("actor", {"kind": "operator", "id": "local-user"}),
    ],
)
def test_tool_refuses_unapproved_identity_effects_and_caller_digest(tmp_path: Path, mutate) -> None:
    home, target, gig_id, _workpad, invocation = _approved_tool(tmp_path)
    forged = deepcopy(invocation)
    mutate(forged)
    with pytest.raises(ScoutToolError):
        dispatch_native_record_tool(home_root=home, requested_target=target, gig_id=gig_id, invocation=forged)


def test_tool_refuses_changed_schema_or_extra_executable_before_publication(tmp_path: Path) -> None:
    home, target, gig_id, workpad, invocation = _approved_tool(tmp_path)
    schema = workpad / "tools" / _CAPABILITY / "operation.schema.json"
    schema.write_bytes(b'{"type":"array"}\n')
    with pytest.raises(ScoutToolError, match="approved tool source changed"):
        dispatch_native_record_tool(home_root=home, requested_target=target, gig_id=gig_id, invocation=invocation)

    schema.write_bytes(b'{"type":"object","additionalProperties":false}\n')
    extra = workpad / "tools" / _CAPABILITY / "unexpected.py"
    extra.write_text("pass\n", encoding="utf-8")
    extra.chmod(extra.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(ScoutToolError):
        dispatch_native_record_tool(home_root=home, requested_target=target, gig_id=gig_id, invocation=invocation)


def _head(workpad: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _record_operation_paths(workpad: Path, gig_id: str) -> set[str]:
    layout = parse_json_bytes((workpad / "manifests" / "workpad-layout.json").read_bytes())
    assert isinstance(layout, dict)
    snapshot = run_with_journal_writer(
        workpad=workpad,
        project_id=layout["project_id"],
        gig_id=gig_id,
        operation=lambda writer: writer.snapshot(("records/operations/",)),
    )
    return set(snapshot.artifacts)


def test_tool_change_after_dispatch_validation_refuses_before_record_publication(tmp_path: Path) -> None:
    home, target, gig_id, workpad, invocation = _approved_tool(tmp_path)
    schema = workpad / "tools" / _CAPABILITY / "operation.schema.json"
    head = _head(workpad)
    before = _record_operation_paths(workpad, gig_id)

    def change_approved_schema() -> None:
        schema.write_bytes(b'{"type":"array"}\n')

    with pytest.raises(ScoutToolError, match="approved tool source changed"):
        dispatch_native_record_tool(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            invocation=invocation,
            _before_publication=change_approved_schema,
        )
    assert _head(workpad) == head
    assert _record_operation_paths(workpad, gig_id) == before


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["capabilities"][0]["tool_binding"].__setitem__("inventory_sha256", "sha256:" + "0" * 64),
        lambda value: value["capabilities"][0]["tool_binding"].__setitem__("inventory", list(reversed(value["capabilities"][0]["tool_binding"]["inventory"]))),
        lambda value: value["capabilities"][0]["tool_binding"]["inventory"].append(deepcopy(value["capabilities"][0]["tool_binding"]["inventory"][0])),
        lambda value: value["capabilities"][0]["tool_binding"].__setitem__("entry_path", f"tools/{_CAPABILITY}/missing.py"),
        lambda value: value["capabilities"][0]["source_constraints"].__setitem__("required_digest", "sha256:" + "f" * 64),
        lambda value: value["capabilities"][0]["source_constraints"].__setitem__("required_identity", "other.py"),
        lambda value: value["capabilities"][0]["tool_binding"].__setitem__("operations", ["record_update", "record_create"]),
        lambda value: value["capabilities"][0]["tool_binding"].__setitem__("effects", ["other_effect"]),
    ],
)
def test_tool_binding_is_rejected_before_manifest_materialization(tmp_path: Path, mutate) -> None:
    inventory = [
        {"path": f"tools/{_CAPABILITY}/operation.schema.json", "content_sha256": "sha256:" + "1" * 64, "media_type": "application/schema+json", "size_bytes": 2},
        {"path": f"tools/{_CAPABILITY}/record_tool.py", "content_sha256": "sha256:" + "2" * 64, "media_type": "text/x-python", "size_bytes": 2},
    ]
    malformed = _manifest("gig_00000000-0000-4000-8000-000000000001", inventory)
    mutate(malformed)
    payload = canonical_json_bytes(malformed)
    assert not validate_capability_manifest(payload).valid
    with pytest.raises(CapabilityManifestError):
        materialize_capability_manifest(tmp_path, malformed)


def test_exposed_tool_publisher_cannot_mint_a_fabricated_binding(tmp_path: Path) -> None:
    home, target, gig_id, workpad, _invocation = _approved_tool(tmp_path)
    head = _head(workpad)
    before = _record_operation_paths(workpad, gig_id)
    fabricated = {
        "manifest_ref": {"path": "manifests/capabilities/fabricated.json"},
        "capability_id": _CAPABILITY,
        "gig_version": 3,
        "entry_path": f"tools/{_CAPABILITY}/record_tool.py",
        "inventory": [],
        "inventory_sha256": "sha256:" + "0" * 64,
        "operation": "record_create",
        "effects": ["write_workpad"],
        "actor": {"kind": "agent", "id": "forged"},
    }
    with pytest.raises(ScoutToolError):
        create_native_record_from_tool(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            content=_profile(),
            actor={"kind": "agent", "id": "forged"},
            origin="agent_supplied",
            operation_key="fabricated-binding",
            tool_binding=fabricated,
        )
    assert _head(workpad) == head
    assert _record_operation_paths(workpad, gig_id) == before


def test_fresh_process_executes_approved_fixture_entry_and_adapter(tmp_path: Path) -> None:
    home, target, gig_id, workpad, invocation = _approved_tool(tmp_path)
    script = """
import json
import sys
from pathlib import Path
from gigai.scout.tools import invoke_approved_tool_entry

result = invoke_approved_tool_entry(
    home_root=Path(sys.argv[1]),
    requested_target=Path(sys.argv[2]),
    gig_id=sys.argv[3],
    capability_id=sys.argv[4],
    actor={\"kind\": \"agent\", \"id\": \"approved-tool-agent\"},
    operation_key=\"fresh-fixture-tool\",
    tool_input={\"content\": json.loads(sys.argv[5])},
)
print(json.dumps({\"record_id\": result.record_id, \"created\": result.created}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(home), str(target), gig_id, _CAPABILITY, json.dumps(_profile())],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[3],
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["created"] is True
    assert _receipt(workpad, home, target, gig_id)["tool_binding"]["entry_path"] == invocation["entry_path"]
