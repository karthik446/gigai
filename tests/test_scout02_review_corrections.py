from __future__ import annotations

from pathlib import Path
import subprocess

from gigai.canonical import canonical_json_bytes, digest_imported_bytes, digest_owned_text, render_json_front_matter
from gigai.graph_set import validate_selection_record


GIG_ID = "gig_00000000-0000-4000-8000-000000000001"


def _ref(path: str, payload: bytes = b"fixture") -> dict[str, object]:
    return {"path": path, "content_sha256": digest_imported_bytes(payload), "media_type": "application/json", "size_bytes": len(payload)}


def _selection(root: Path, payload: bytes) -> tuple[dict[str, object], bytes]:
    selected = _ref("manifests/career.json")
    graph_set = {"graphs": [{"graph_id": "career", "goal_graph": selected}]}
    graph_ref = _ref("manifests/graph-set.json", canonical_json_bytes(graph_set))
    invocation_ref = _ref("agent-invocations/inv_agent-selection.json", payload)
    selection = {
        "schema_version": "1.0", "selection_record_id": "graph_selection_00000000-0000-4000-8000-000000000001",
        "gig_id": GIG_ID, "gig_version": 2, "graph_set": graph_ref, "selected_graph_id": "career", "selected_graph": selected,
        "selection_kind": "agent_explicit", "selector": {"kind": "agent", "actor": {"kind": "agent", "id": "codex", "session_id": "selection-session"}, "rule_id": None, "rule_version": None, "invocation_ref": invocation_ref},
        "selection_reason": "agent explicitly selected one graph", "routing_evidence_refs": [], "created_at": "2026-09-08T00:00:00Z",
    }
    return graph_set, canonical_json_bytes(selection)


def _invocation() -> bytes:
    return canonical_json_bytes({
        "protocol_version": "1", "invocation_id": "inv_agent-selection", "trigger": "gigai:",
        "actor": {"kind": "agent", "id": "codex", "session_id": "selection-session"}, "command": "create",
        "target": {"home": "/tmp/gigai", "project": "fixture"}, "input": {"intent": "select an approved graph"},
        "requested": {"roles": [], "models": [], "capabilities": []}, "consent": [],
    })


def _commit_invocation(root: Path, payload: bytes, *, actor_id: str = "codex") -> None:
    invocation = root / "agent-invocations" / "inv_agent-selection.json"
    invocation.parent.mkdir(parents=True)
    invocation.write_bytes(payload)
    handoff = root / "handoffs" / "000000000001-agent-invocation.txt"
    handoff.parent.mkdir()
    body = "agent invocation persisted\n"
    handoff.write_bytes(render_json_front_matter({"actor": {"kind": "agent", "id": actor_id, "model_target": None}, "body_sha256": digest_owned_text(body)}, body))
    for args in (("init", "-q"), ("add", "."), ("-c", "user.email=fixture@example.test", "-c", "user.name=Fixture", "commit", "-qm", "fixture")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_agent_selection_requires_exact_journaled_invocation_and_actor(tmp_path: Path) -> None:
    payload = _invocation()
    root = tmp_path / "workpad"
    root.mkdir()
    graph_set, selection_data = _selection(root, payload)
    missing = validate_selection_record(selection_data, graph_set=graph_set, gig_id=GIG_ID, gig_version=2, root=root)
    assert {item.code for item in missing.findings} == {"agent_invocation_missing"}

    _commit_invocation(root, payload)
    accepted = validate_selection_record(selection_data, graph_set=graph_set, gig_id=GIG_ID, gig_version=2, root=root)
    assert accepted.valid, accepted.findings

    invocation = root / "agent-invocations" / "inv_agent-selection.json"
    invocation.write_bytes(payload + b"\n")
    tampered = validate_selection_record(selection_data, graph_set=graph_set, gig_id=GIG_ID, gig_version=2, root=root)
    assert {item.code for item in tampered.findings} == {"agent_invocation_mismatch"}

    invocation.write_bytes(payload)
    graph_set, mismatch_data = _selection(root, payload)
    mismatch = validate_selection_record(
        mismatch_data.replace(b'"id":"codex"', b'"id":"claude"', 1), graph_set=graph_set, gig_id=GIG_ID, gig_version=2, root=root
    )
    assert {item.code for item in mismatch.findings} == {"agent_invocation_actor_mismatch"}
