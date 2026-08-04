from __future__ import annotations

import copy
import json
from pathlib import Path

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.validators import (
    SCHEMA_NAMES,
    validate_goal_graph,
    validate_proposal_workpad,
    validate_serialized_contract,
)
from research.contract_spike.tests.test_schemas import (
    GIG_ID,
    GOAL_A,
    GOAL_B,
    PROJECT_ID,
    budget,
    goal_graph,
)


GOAL_C = "goal_77777777-7777-4777-8777-777777777777"


def _codes(graph: dict) -> set[str]:
    return {finding.code for finding in validate_goal_graph(graph).findings}


def _valid_graph() -> dict:
    graph = copy.deepcopy(goal_graph())
    graph["aggregate_budget"] = {
        **budget(),
        "max_model_calls": 16,
        "max_tool_calls": 48,
        "max_tokens": 200000,
        "max_wall_time_ms": 7200000,
        "max_cost": "25.00",
    }
    for goal in graph["goals"]:
        goal["effects"] = ["read_target"]
        goal["write_surfaces"] = []
    return graph


def test_schema_validator_enumerates_the_packaged_contract_set() -> None:
    assert len(SCHEMA_NAMES) == 8
    report = validate_serialized_contract(
        "goal-graph.schema.json", canonical_json_bytes(_valid_graph())
    )
    assert report.valid


def test_goal_graph_validator_accepts_a_valid_graph() -> None:
    assert validate_goal_graph(_valid_graph()).valid


def test_goal_graph_validator_rejects_materializer_that_is_not_a_predecessor() -> None:
    graph = _valid_graph()
    graph["goals"][0]["executor"].update(
        resolution="materialized", materialized_by=GOAL_B, blocking_reason=None
    )
    assert "unknown_materializer" in _codes(graph)


def test_goal_graph_validator_rejects_write_effect_without_surface() -> None:
    graph = _valid_graph()
    graph["goals"][0]["effects"] = ["write_workpad"]
    assert "missing_write_surface" in _codes(graph)


def test_goal_graph_validator_rejects_incomplete_terminal_evidence() -> None:
    graph = _valid_graph()
    graph["goals"][1]["verification"]["required_evidence"] = ["source-ledger"]
    assert "incomplete_terminal_evidence" in _codes(graph)


def test_goal_graph_validator_rejects_duplicate_and_noncanonical_goal_ids() -> None:
    duplicate = _valid_graph()
    duplicate["goals"].append(copy.deepcopy(duplicate["goals"][0]))
    assert "duplicate_goal_id" in _codes(duplicate)

    noncanonical = _valid_graph()
    noncanonical["goals"][0]["goal_id"] = "goal_NOT-A-CANONICAL-UUID"
    assert "noncanonical_goal_id" in _codes(noncanonical)


def test_goal_graph_validator_rejects_an_invalid_internal_version() -> None:
    graph = _valid_graph()
    graph["goals"][0]["goal_version"] = 0
    assert "invalid_goal_version" in _codes(graph)


def test_goal_graph_validator_rejects_an_undeclared_source_outcome() -> None:
    graph = _valid_graph()
    graph["edges"][0]["on_outcomes"] = ["UNDECLARED"]
    assert "undeclared_outcome" in _codes(graph)


def test_goal_graph_validator_rejects_independent_overlapping_writers() -> None:
    graph = _valid_graph()
    graph["edges"] = []
    graph["entry_goal_ids"] = [GOAL_A, GOAL_B]
    graph["terminal_goal_ids"] = [GOAL_A, GOAL_B]
    for goal in graph["goals"]:
        goal["effects"] = ["write_workpad"]
        goal["write_surfaces"] = ["manifests/goal-graph.json"]
    assert "incompatible_parallel_surfaces" in _codes(graph)


def test_goal_graph_validator_rejects_an_impossible_aggregate_budget() -> None:
    graph = _valid_graph()
    graph["aggregate_budget"]["max_tokens"] = 1
    assert "impossible_budget" in _codes(graph)


def test_goal_graph_validator_rejects_a_blocking_resolution_without_reason() -> None:
    graph = _valid_graph()
    graph["goals"][0]["executor"].update(
        resolution="blocking", materialized_by=None, blocking_reason=None
    )
    assert "invalid_blocking_resolution" in _codes(graph)


def test_goal_graph_validator_rejects_a_write_surface_on_a_read_only_goal() -> None:
    graph = _valid_graph()
    graph["goals"][0]["write_surfaces"] = ["manifests/goal-graph.json"]
    assert "unexpected_write_surface" in _codes(graph)


def test_goal_graph_validator_rejects_an_unreachable_required_goal() -> None:
    graph = _valid_graph()
    unreachable = copy.deepcopy(graph["goals"][1])
    unreachable.update(
        goal_id=GOAL_C,
        display_ordinal="G02",
        slug="unreachable",
        title="Unreachable",
    )
    graph["goals"].append(unreachable)
    assert "unreachable_required_goal" in _codes(graph)


def test_goal_graph_validator_reports_missing_entry_without_suppressing_cycle() -> None:
    graph = _valid_graph()
    graph["entry_goal_ids"] = []
    graph["edges"].append(
        {
            "edge_id": "edge_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "from_goal_id": GOAL_B,
            "to_goal_id": GOAL_A,
            "kind": "recovery",
            "on_outcomes": ["COMPLETE"],
            "automatic": True,
        }
    )
    codes = _codes(graph)
    assert {"missing_entry_path", "graph_cycle"} <= codes


def test_goal_graph_validator_reports_missing_terminal_path() -> None:
    graph = _valid_graph()
    graph["terminal_goal_ids"] = []
    assert "missing_terminal_path" in _codes(graph)


def test_goal_graph_validator_requires_a_typed_automatic_outcome() -> None:
    graph = _valid_graph()
    graph["edges"][0]["on_outcomes"] = []
    assert "missing_automatic_outcome" in _codes(graph)


def _joined_graph() -> dict:
    graph = _valid_graph()
    joined = copy.deepcopy(graph["goals"][1])
    joined.update(goal_id=GOAL_C, display_ordinal="G02", slug="join", title="Join")
    graph["goals"].append(joined)
    graph["aggregate_budget"]["max_model_calls"] = 24
    graph["aggregate_budget"]["max_tool_calls"] = 72
    graph["aggregate_budget"]["max_tokens"] = 300000
    graph["aggregate_budget"]["max_wall_time_ms"] = 10800000
    graph["aggregate_budget"]["max_cost"] = "37.50"
    graph["edges"].extend(
        [
            {
                "edge_id": "edge_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "from_goal_id": GOAL_A,
                "to_goal_id": GOAL_C,
                "kind": "dependency",
                "on_outcomes": ["COMPLETE"],
                "automatic": True,
            },
            {
                "edge_id": "edge_cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "from_goal_id": GOAL_B,
                "to_goal_id": GOAL_C,
                "kind": "dependency",
                "on_outcomes": ["COMPLETE"],
                "automatic": True,
            },
        ]
    )
    graph["terminal_goal_ids"] = [GOAL_C]
    return graph


def test_goal_graph_validator_accepts_a_multi_parent_join() -> None:
    assert validate_goal_graph(_joined_graph()).valid


def test_goal_graph_validator_rejects_duplicated_join_predecessor() -> None:
    graph = _joined_graph()
    graph["edges"][2]["from_goal_id"] = GOAL_A
    assert "invalid_join_predecessors" in _codes(graph)


def _write_valid_proposal(root: Path) -> None:
    contents = {
        "gig.md": b"# Gig\n",
        "goals/README.md": b"# Goals\n",
        "goals/00-collect-evidence.md": b"# Collect\n",
        "goals/01-synthesize.md": b"# Synthesize\n",
        "reviews/creation-review.md": b"# Review\n",
        "decisions/creation-decisions.md": b"# Decisions\n",
        "manifests/creation-manifest.json": canonical_json_bytes(
            {"schema_version": "1.0"}
        ),
    }
    for relative, data in contents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    graph = _valid_graph()
    for goal in graph["goals"]:
        relative = f"goals/{int(goal['display_ordinal'][1:]):02d}-{goal['slug']}.md"
        data = contents[relative]
        goal["contract"] = {
            "path": relative,
            "content_sha256": digest_imported_bytes(data),
            "media_type": "text/markdown",
            "size_bytes": len(data),
        }
    graph_bytes = canonical_json_bytes(graph)
    (root / "manifests/goal-graph.json").write_bytes(graph_bytes)

    def ref(relative: str, media_type: str) -> dict[str, object]:
        data = (root / relative).read_bytes()
        return {
            "path": relative,
            "content_sha256": digest_imported_bytes(data),
            "media_type": media_type,
            "size_bytes": len(data),
        }

    proposal = {
        "schema_version": "1.0",
        "proposal_id": "gp_33333333-3333-4333-8333-333333333333",
        "gig_id": GIG_ID,
        "project_id": PROJECT_ID,
        "name": "validator-proof",
        "status": "proposed",
        "kind": "create",
        "created_at": "2026-08-03T00:00:00Z",
        "created_by": {"kind": "operator", "id": "local-user", "model_target": None},
        "base_gig_version": None,
        "parent_proposal_id": None,
        "change_request": None,
        "commission": "Prove proposal validation.",
        "gig_document": ref("gig.md", "text/markdown"),
        "goal_graph": ref("manifests/goal-graph.json", "application/json"),
        "creation_manifest": ref(
            "manifests/creation-manifest.json", "application/json"
        ),
    }
    (root / "manifests/gig-proposal.json").write_bytes(canonical_json_bytes(proposal))


def test_workpad_validator_proves_digest_pinning_and_markdown_correspondence(
    tmp_path: Path,
) -> None:
    _write_valid_proposal(tmp_path)
    assert validate_proposal_workpad(tmp_path).valid
    (tmp_path / "goals/01-synthesize.md").write_text("# Changed\n", encoding="utf-8")
    codes = {finding.code for finding in validate_proposal_workpad(tmp_path).findings}
    assert "goal_contract_mismatch" in codes


def _proposal_payload(root: Path) -> dict:
    return json.loads((root / "manifests/gig-proposal.json").read_bytes())


def _write_proposal_payload(root: Path, payload: dict) -> None:
    (root / "manifests/gig-proposal.json").write_bytes(canonical_json_bytes(payload))


def test_workpad_validator_rejects_approved_status_before_approval(
    tmp_path: Path,
) -> None:
    _write_valid_proposal(tmp_path)
    proposal = _proposal_payload(tmp_path)
    proposal["status"] = "approved"
    _write_proposal_payload(tmp_path, proposal)
    codes = {finding.code for finding in validate_proposal_workpad(tmp_path).findings}
    assert "proposal_not_pending" in codes


def test_workpad_validator_rejects_a_stale_proposal_artifact_digest(
    tmp_path: Path,
) -> None:
    _write_valid_proposal(tmp_path)
    proposal = _proposal_payload(tmp_path)
    proposal["gig_document"]["content_sha256"] = "sha256:" + "0" * 64
    _write_proposal_payload(tmp_path, proposal)
    codes = {finding.code for finding in validate_proposal_workpad(tmp_path).findings}
    assert "artifact_digest_mismatch" in codes


def test_workpad_validator_rejects_unreferenced_goal_markdown(tmp_path: Path) -> None:
    _write_valid_proposal(tmp_path)
    (tmp_path / "goals/99-orphan.md").write_text("# Orphan\n", encoding="utf-8")
    codes = {finding.code for finding in validate_proposal_workpad(tmp_path).findings}
    assert "unreferenced_goal_markdown" in codes
