"""Verify G07 proposal validation using only an installed GigAI wheel."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import provision_workpad


GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
GOAL_A = "goal_55555555-5555-4555-8555-555555555555"
GOAL_B = "goal_66666666-6666-4666-8666-666666666666"


def _budget(value: int) -> dict[str, object]:
    return {
        "max_model_calls": value,
        "max_tool_calls": value,
        "max_tokens": value * 1_000,
        "max_cost": f"{value}.00",
        "currency": "USD",
        "max_wall_time_ms": value * 1_000,
        "max_parallel_goals": 1,
    }


def _goal(
    goal_id: str, ordinal: str, slug: str, outcomes: list[str]
) -> dict[str, object]:
    return {
        "goal_id": goal_id,
        "goal_version": 1,
        "display_ordinal": ordinal,
        "slug": slug,
        "title": slug.replace("-", " ").title(),
        "required": True,
        "activation": "automatic",
        "contract": {},
        "executor": {
            "kind": "local_capability",
            "capability": "validator.fixture@1",
            "role": None,
            "resolution": "installed",
            "materialized_by": None,
            "blocking_reason": None,
        },
        "tools": [],
        "effects": ["read_target"],
        "write_surfaces": [],
        "exclusive_resources": [],
        "budget": _budget(1),
        "verification": {
            "verifier": "fixture.verify@1",
            "acceptance": "The fixture is valid.",
            "required_evidence": ["completion-audit"],
        },
        "outcomes": outcomes,
    }


def _write_proposal(root: Path, project_id: str) -> None:
    artifacts = {
        "gig.md": b"# Installed G07 proposal\n",
        "goals/README.md": b"# Goals\n",
        "goals/00-collect.md": b"# Collect\n",
        "goals/01-synthesize.md": b"# Synthesize\n",
        "reviews/creation-review.md": b"# Review\n",
        "decisions/creation-decisions.md": b"# Decisions\n",
        "manifests/creation-manifest.json": canonical_json_bytes(
            {"schema_version": "1.0"}
        ),
    }
    for relative, data in artifacts.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    graph = {
        "schema_version": "1.0",
        "graph_id": "graph_44444444-4444-4444-8444-444444444444",
        "gig_id": GIG_ID,
        "graph_version": 1,
        "created_at": "2026-08-04T00:00:00Z",
        "aggregate_budget": _budget(2),
        "failure_policy": "fail_gig",
        "goals": [
            _goal(GOAL_A, "G00", "collect", ["COMPLETE"]),
            _goal(GOAL_B, "G01", "synthesize", ["COMPLETE"]),
        ],
        "edges": [
            {
                "edge_id": "edge_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "from_goal_id": GOAL_A,
                "to_goal_id": GOAL_B,
                "kind": "dependency",
                "on_outcomes": ["COMPLETE"],
                "automatic": True,
            }
        ],
        "entry_goal_ids": [GOAL_A],
        "terminal_goal_ids": [GOAL_B],
        "required_completion_evidence": ["completion-audit"],
    }
    for goal in graph["goals"]:
        relative = f"goals/{int(goal['display_ordinal'][1:]):02d}-{goal['slug']}.md"
        data = artifacts[relative]
        goal["contract"] = {
            "path": relative,
            "content_sha256": digest_imported_bytes(data),
            "media_type": "text/markdown",
            "size_bytes": len(data),
        }
    graph_bytes = canonical_json_bytes(graph)
    (root / "manifests/goal-graph.json").write_bytes(graph_bytes)

    def reference(relative: str, media_type: str) -> dict[str, object]:
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
        "project_id": project_id,
        "name": "installed-g07-proof",
        "status": "proposed",
        "kind": "create",
        "created_at": "2026-08-04T00:00:00Z",
        "created_by": {
            "kind": "operator",
            "id": "wheel-verifier",
            "model_target": None,
        },
        "base_gig_version": None,
        "parent_proposal_id": None,
        "change_request": None,
        "commission": "Verify installed G07 proposal validation.",
        "gig_document": reference("gig.md", "text/markdown"),
        "goal_graph": reference("manifests/goal-graph.json", "application/json"),
        "creation_manifest": reference(
            "manifests/creation-manifest.json", "application/json"
        ),
    }
    (root / "manifests/gig-proposal.json").write_bytes(canonical_json_bytes(proposal))


def _check(
    executable: Path, home: Path, target: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            os.fspath(executable),
            "check",
            GIG_ID,
            "--home",
            os.fspath(home),
            "--target",
            os.fspath(target),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def main() -> None:
    executable = Path(sys.executable).parent / "gigai"
    if not executable.is_file():
        raise SystemExit("installed gigai console script is missing")
    with tempfile.TemporaryDirectory(prefix="gigai-g07-wheel-") as temporary:
        root = Path(temporary)
        home, workpads, target = root / "home", root / "workpads", root / "target"
        target.mkdir()
        run_setup(
            build_config(
                home_root=home,
                workpad_root=workpads,
                editor_argv=("/usr/bin/true",),
                open_with_target=False,
            )
        )
        binding = initialize_target(
            home_root=home,
            requested_target=target,
            uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
        )
        workpad = provision_workpad(
            home_root=home, project_id=binding.project_id, gig_id=GIG_ID
        ).path
        _write_proposal(workpad, binding.project_id)
        valid = _check(executable, home, target)
        if valid.returncode != 0 or json.loads(valid.stdout) != {
            "findings": [],
            "valid": True,
        }:
            raise SystemExit("installed G07 check did not accept a valid proposal")
        (workpad / "goals/01-synthesize.md").write_text(
            "# Tampered\n", encoding="utf-8"
        )
        tampered = _check(executable, home, target)
        if tampered.returncode != 1:
            raise SystemExit("installed G07 check did not reject a digest mismatch")
        codes = {item["code"] for item in json.loads(tampered.stdout)["findings"]}
        if "goal_contract_mismatch" not in codes:
            raise SystemExit(
                "installed G07 check did not report the Goal contract mismatch"
            )
    print("verified installed GigAI G07 proposal validation and digest-pinned check")


if __name__ == "__main__":
    main()
