from __future__ import annotations

import copy
from importlib import resources as importlib_resources
import json
import unittest
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from ..canonical import canonical_json_bytes, sha256_digest
from ..graph_validation import GoalGraphError, validate_goal_graph


EXPECTED_SCHEMA_NAMES = {
    "addressed-artifact.schema.json",
    "adjudication.schema.json",
    "active-gig-version.schema.json",
    "common.schema.json",
    "capability-installation.schema.json",
    "capability-manifest.schema.json",
    "feedback.schema.json",
    "finding.schema.json",
    "gig-proposal.schema.json",
    "goal-graph.schema.json",
    "handoff-frontmatter.schema.json",
    "improvement-manifest.schema.json",
    "learning-record.schema.json",
    "model-exchange.schema.json",
    "model-invocation.schema.json",
    "proposal-interview.schema.json",
    "report.schema.json",
    "review-bundle.schema.json",
    "review-contract.schema.json",
    "run-brief-frontmatter.schema.json",
    "run-details.schema.json",
    "run-manifest.schema.json",
    "review-loop.schema.json",
    "target-effect.schema.json",
    "trace.schema.json",
}

PROJECT_ID = "project_11111111-1111-4111-8111-111111111111"
GIG_ID = "gig_22222222-2222-4222-8222-222222222222"
CAPABILITY_ID = "cap_99999999-9999-4999-8999-999999999999"
CAPABILITY_MANIFEST_ID = "capmanifest_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CAPABILITY_INSTALLATION_ID = "capinstall_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PROPOSAL_ID = "gp_33333333-3333-4333-8333-333333333333"
GRAPH_ID = "graph_44444444-4444-4444-8444-444444444444"
GOAL_A = "goal_55555555-5555-4555-8555-555555555555"
GOAL_B = "goal_66666666-6666-4666-8666-666666666666"
RUN_ID = "run_77777777-7777-4777-8777-777777777777"
HANDOFF_ID = "handoff_88888888-8888-4888-8888-888888888888"
ZERO_DIGEST = "sha256:" + "0" * 64
ONE_DIGEST = "sha256:" + "1" * 64
COMMIT = "a" * 40
NOW = "2026-08-02T12:00:00Z"


def artifact(
    path: str, digest: str = ZERO_DIGEST, media_type: str = "application/json"
) -> dict[str, Any]:
    return {
        "path": path,
        "content_sha256": digest,
        "canonical_sha256": digest,
        "media_type": media_type,
        "size_bytes": 123,
    }


def budget() -> dict[str, Any]:
    return {
        "max_model_calls": 8,
        "max_tool_calls": 24,
        "max_tokens": 100000,
        "max_cost": "12.50",
        "currency": "USD",
        "max_wall_time_ms": 3600000,
        "max_parallel_goals": 2,
    }


def usage() -> dict[str, Any]:
    return {
        "input_tokens": 100,
        "output_tokens": 25,
        "total_tokens": 125,
        "cost": "0.25",
        "currency": "USD",
        "cost_status": "provider_reported",
    }


def goal(
    goal_id: str,
    ordinal: str,
    slug: str,
    outcomes: list[str],
    activation: str = "automatic",
) -> dict[str, Any]:
    return {
        "goal_id": goal_id,
        "goal_version": 1,
        "display_ordinal": ordinal,
        "slug": slug,
        "title": slug.replace("-", " ").title(),
        "required": True,
        "activation": activation,
        "contract": artifact(f"goals/{ordinal}-{slug}.md", media_type="text/markdown"),
        "executor": {
            "kind": "fixed_role_api",
            "capability": "research.execute@1",
            "role": "researcher",
            "resolution": "installed",
            "materialized_by": None,
            "blocking_reason": None,
        },
        "tools": [
            {
                "name": "web.search@1",
                "resolution": "installed",
                "materialized_by": None,
                "blocking_reason": None,
            }
        ],
        "effects": ["read_target", "write_workpad", "network_read"],
        "write_surfaces": [],
        "exclusive_resources": [],
        "budget": budget(),
        "verification": {
            "verifier": "evidence.citation-check@1",
            "acceptance": "Every material claim is supported by captured evidence.",
            "required_evidence": ["source-ledger", "completion-audit"],
        },
        "outcomes": outcomes,
    }


def goal_graph() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "graph_id": GRAPH_ID,
        "gig_id": GIG_ID,
        "graph_version": 1,
        "created_at": NOW,
        "aggregate_budget": budget(),
        "failure_policy": "follow_recovery",
        "goals": [
            goal(GOAL_A, "G00", "collect-evidence", ["COMPLETE", "FAILED"]),
            goal(GOAL_B, "G01", "synthesize", ["COMPLETE"]),
        ],
        "edges": [
            {
                "edge_id": "edge_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
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


def actor() -> dict[str, Any]:
    return {"kind": "operator", "id": "local-user", "model_target": None}


def capability_manifest() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "manifest_id": CAPABILITY_MANIFEST_ID,
        "manifest_version": 1,
        "gig_id": GIG_ID,
        "created_at": NOW,
        "created_by": actor(),
        "capabilities": [
            {
                "capability_id": CAPABILITY_ID,
                "goal_ids": [GOAL_A],
                "kind": "local_capability",
                "name": "fixture-tool",
                "requested_version": "1.0.0",
                "source_constraints": {
                    "allowed_source_kinds": ["local_artifact"],
                    "required_digest": ZERO_DIGEST,
                    "required_identity": "fixture.artifact",
                },
                "declared_effects": ["read_local_metadata"],
                "permissions": {"filesystem": "write_isolated", "network": "none", "credentials": "none"},
                "credential_requirements": [],
                "network_requirement": "none",
                "availability_state": "missing",
                "compatibility": {"status": "compatible", "reason": None},
                "security_review": {"status": "passed", "checks": ["path_containment"], "reason": None},
                "alternatives": [],
                "options": [
                    {"option_id": "A", "kind": "install_local", "label": "Install local artifact", "ordinal": 0, "decision": "pending"},
                    {"option_id": "B", "kind": "continue_without", "label": "Continue without capability", "ordinal": 1, "decision": "pending"},
                ],
            }
        ],
    }


def capability_installation() -> dict[str, Any]:
    snapshot = {
        "root": f"tools/{CAPABILITY_ID}",
        "entries": [],
        "snapshot_sha256": ZERO_DIGEST,
        "source_identity": None,
    }
    return {
        "schema_version": "1.0",
        "installation_id": CAPABILITY_INSTALLATION_ID,
        "installation_version": 1,
        "gig_id": GIG_ID,
        "capability_id": CAPABILITY_ID,
        "manifest_id": CAPABILITY_MANIFEST_ID,
        "created_at": NOW,
        "decision": {"option_id": "A", "status": "approved", "actor": actor(), "recorded_at": NOW, "reason": None},
        "source": {"path": "tools/.sources/fixture.artifact", "content_sha256": ZERO_DIGEST, "size_bytes": 0, "media_type": "application/octet-stream", "identity": "fixture.artifact", "version": "1.0.0"},
        "security_checks": [{"name": "source_digest", "status": "passed", "detail": "fixture"}],
        "before_manifest": snapshot,
        "after_manifest": snapshot,
        "outcome": "already_available",
        "rollback": {"attempted": False, "restored_before": True, "reason": None},
        "provenance": {"source_kind": "local_artifact", "source_sha256": ZERO_DIGEST, "installed_root": f"tools/{CAPABILITY_ID}", "recorded_by": actor()},
        "failure_reason": None,
    }


def valid_instances() -> dict[str, dict[str, Any]]:
    graph_ref = artifact("manifests/goal-graph.json")
    run_brief_ref = artifact("runs/run-777/run-brief.md", media_type="text/markdown")
    target_ref = artifact("runs/run-777/target-before.json")
    resolved_models = [
        {
            "role": "researcher",
            "model_target": "openai-api",
            "endpoint": "responses",
            "configured_selector": "gpt-5",
            "resolved_identity": "gpt-5-2026-07-01",
            "resolution_source": "provider_reported",
            "compatibility_status": "LIVE_VERIFIED",
        }
    ]
    resolved_tools = [
        {
            "name": "web.search",
            "version": "1.0.0",
            "source_sha256": ONE_DIGEST,
            "effects": ["network_read"],
        }
    ]
    brief_body = b"Review this Run Brief before inspecting execution details.\n"
    brief = {
        "schema_version": "1.0",
        "run_id": RUN_ID,
        "gig_id": GIG_ID,
        "gig_version": 1,
        "created_at": NOW,
        "invoked_by": actor(),
        "invocation_argv": ["gigai", "run", GIG_ID],
        "goal_graph": graph_ref,
        "target": {
            "kind": "git",
            "root": "/workspace/project",
            "git_head": COMMIT,
            "status_sha256": ZERO_DIGEST,
            "observation_sha256": ONE_DIGEST,
        },
        "profile": "default",
        "resolved_models": resolved_models,
        "resolved_tools": resolved_tools,
        "effects": ["read_target", "write_workpad", "network_read"],
        "aggregate_budget": budget(),
        "input_canonical_sha256": ZERO_DIGEST,
        "body_sha256": sha256_digest(brief_body),
        "run_manifest_path": "runs/run-777/run-manifest.json",
    }
    proposal = {
        "schema_version": "1.0",
        "proposal_id": PROPOSAL_ID,
        "gig_id": GIG_ID,
        "project_id": PROJECT_ID,
        "name": "research-gigai",
        "status": "proposed",
        "kind": "create",
        "created_at": NOW,
        "created_by": actor(),
        "base_gig_version": None,
        "parent_proposal_id": None,
        "change_request": None,
        "commission": "Research GigAI and produce an evidence-backed report.",
        "gig_document": artifact("proposals/gp-333/gig.md", media_type="text/markdown"),
        "goal_graph": graph_ref,
        "creation_manifest": artifact("proposals/gp-333/creation-manifest.json"),
    }
    active = {
        "schema_version": "1.0",
        "gig_id": GIG_ID,
        "active_version": 1,
        "approved_proposal_id": PROPOSAL_ID,
        "goal_graph": graph_ref,
        "journal_commit": COMMIT,
        "journal_tag": "gig-v000001",
        "approved_at": NOW,
        "approved_by": actor(),
    }
    manifest = {
        "schema_version": "1.0",
        "run_id": RUN_ID,
        "gig_id": GIG_ID,
        "gig_version": 1,
        "authority": "run_invocation",
        "status": "sealed",
        "sealed_at": NOW,
        "invoked_by": actor(),
        "invocation_argv": ["gigai", "run", GIG_ID],
        "run_brief": run_brief_ref,
        "goal_graph": graph_ref,
        "goal_contracts": [
            {
                "goal_id": GOAL_A,
                "goal_version": 1,
                "contract": artifact(
                    "goals/G00-collect-evidence.md", media_type="text/markdown"
                ),
            },
            {
                "goal_id": GOAL_B,
                "goal_version": 1,
                "contract": artifact(
                    "goals/G01-synthesize.md", media_type="text/markdown"
                ),
            },
        ],
        "target_observation": target_ref,
        "profile": "default",
        "resolved_models": resolved_models,
        "resolved_tools": resolved_tools,
        "sealed_sources": [graph_ref, target_ref],
        "effects": ["read_target", "write_workpad", "network_read"],
        "aggregate_budget": budget(),
        "input_canonical_sha256": ZERO_DIGEST,
    }
    goal_sets = {
        "pending": [],
        "ready": [],
        "active": [],
        "complete": [GOAL_A, GOAL_B],
        "failed": [],
        "blocked": [],
        "gated": [],
        "cancelled": [],
    }
    goal_details = []
    for goal_id in [GOAL_A, GOAL_B]:
        goal_details.append(
            {
                "goal_id": goal_id,
                "goal_version": 1,
                "executor": "openai-api/gpt-5-2026-07-01",
                "status": "complete",
                "outcome": "COMPLETE",
                "errors": [],
                "evidence": [artifact(f"evidence/{goal_id}.json")],
                "usage": usage(),
                "started_at": NOW,
                "finished_at": "2026-08-02T12:05:00Z",
            }
        )
    details = {
        "schema_version": "1.0",
        "run_id": RUN_ID,
        "gig_id": GIG_ID,
        "gig_version": 1,
        "goal_graph_sha256": ZERO_DIGEST,
        "status": "succeeded",
        "started_at": NOW,
        "finished_at": "2026-08-02T12:10:00Z",
        "goal_sets": goal_sets,
        "goals": goal_details,
        "critical_path": [GOAL_A, GOAL_B],
        "realized_max_parallel_goals": 1,
        "execution_summary": "Both required Goals completed and verified.",
        "tool_errors": [],
        "model_errors": [],
        "aggregate_usage": usage(),
        "remaining_budget": budget(),
        "target_before": target_ref,
        "target_after": target_ref,
        "completion_audit": {"status": "valid", "path": "reviews/completion-audit.md"},
        "terminal_handoff": artifact(
            "handoffs/000000000006-run-succeeded.txt", media_type="text/plain"
        ),
        "workpad_commit": COMMIT,
        "next_actions": ["Review the completion audit."],
    }
    handoff_body = b"Goal G01 completed with the required evidence.\n"
    handoff = {
        "schema_version": "1.0",
        "handoff_id": HANDOFF_ID,
        "sequence": 6,
        "gig_id": GIG_ID,
        "gig_version": 1,
        "goal_id": GOAL_B,
        "goal_version": 1,
        "run_id": RUN_ID,
        "transition": "goal_completed",
        "timestamp": "2026-08-02T12:05:00Z",
        "actor": {"kind": "gigai", "id": "worker-1", "model_target": None},
        "parent_handoff_ids": [],
        "previous_journal_commit": COMMIT,
        "goal_graph_sha256": ZERO_DIGEST,
        "source_manifest_sha256": ONE_DIGEST,
        "outcome": "COMPLETE",
        "evidence": [artifact("evidence/goal-b.json")],
        "usage": usage(),
        "body_sha256": sha256_digest(handoff_body),
    }
    bundle_id = "bundle_99999999-9999-4999-8999-999999999999"
    reference_id = "ref_99999999-9999-4999-8999-999999999999"
    contract_id = "contract_99999999-9999-4999-8999-999999999999"
    evaluator_id = "evaluator_fixture"
    trace_id = "trace_99999999-9999-4999-8999-999999999999"
    finding_id = "finding_99999999-9999-4999-8999-999999999999"
    bundle = {
        "schema_version": "1.0",
        "bundle_id": bundle_id,
        "bundle_version": 1,
        "created_at": NOW,
        "created_by": actor(),
        "name": "research-fixture",
        "question": "What does the evidence support?",
        "references": [
            {
                "reference_id": reference_id,
                "role": "source",
                "kind": "article",
                "path": "references/source.txt",
                "media_type": "text/plain",
                "content_sha256": ZERO_DIGEST,
                "size_bytes": 1,
                "provenance": {
                    "source_kind": "generated",
                    "locator": "fixture://source",
                    "acquired_at": NOW,
                    "acquisition_method": "fixture",
                    "source_revision": None,
                },
                "sensitivity": "public",
                "redaction_status": "not_required",
            }
        ],
        "tool_requirements": None,
        "redaction_policy": {
            "mode": "local_only",
            "allowed_reference_ids": [reference_id],
            "policy_version": "fixture-1",
            "detector_version": None,
        },
    }
    contract = {
        "schema_version": "1.0",
        "contract_id": contract_id,
        "contract_version": 1,
        "created_at": NOW,
        "created_by": actor(),
        "name": "research-review",
        "question": "What does the evidence support?",
        "reference_roles": ["source"],
        "criteria": [
            {
                "criterion_id": "criterion_support",
                "description": "Claims cite evidence.",
                "severity": "high",
                "required_evidence": ["citation"],
                "citation_requirement": "required",
                "evaluator_ids": [evaluator_id],
            }
        ],
        "severity_model": {
            "levels": ["info", "low", "medium", "high", "critical"],
            "ordering": ["info", "low", "medium", "high", "critical"],
        },
        "evidence_requirements": ["citation"],
        "output_shape": {
            "machine_media_type": "application/json",
            "human_media_type": "text/markdown",
            "required_sections": ["findings"],
        },
        "clarification_policy": "block_run",
        "cycle_cap": 1,
        "escalation_policy": "operator",
        "allowed_effects": ["write_workpad"],
        "evaluator_plan": [
            {
                "evaluator_id": evaluator_id,
                "evaluator_version": "fixture-1",
                "stage": "deterministic",
            }
        ],
        "redaction_policy": {
            "mode": "local_only",
            "policy_version": "fixture-1",
            "detector_version": None,
        },
    }
    evaluator = {
        "evaluator_id": evaluator_id,
        "evaluator_version": "fixture-1",
        "stage": "deterministic",
    }
    finding = {
        "schema_version": "1.0",
        "finding_id": finding_id,
        "finding_version": 1,
        "criterion_id": "criterion_support",
        "status": "open",
        "severity": "high",
        "title": "Missing support",
        "description": "The claim lacks a citation.",
        "evidence": [
            {
                "reference_id": reference_id,
                "content_sha256": ZERO_DIGEST,
                "locator": "bytes:0-1",
                "quote": "source",
            }
        ],
        "evaluator": evaluator,
        "source_evaluators": [evaluator],
        "trace_id": trace_id,
        "confidence": "0.90",
        "disagreement": {"present": False, "peer_finding_ids": [], "summary": None},
        "created_at": NOW,
    }
    feedback = {
        "schema_version": "1.0",
        "feedback_id": "feedback_99999999-9999-4999-8999-999999999999",
        "feedback_version": 1,
        "created_at": NOW,
        "actor": actor(),
        "finding_ids": [finding_id],
        "decision": "deferred",
        "text": "Need more evidence.",
        "rationale": None,
    }
    adjudication = {
        "schema_version": "1.0",
        "adjudication_id": "adjudication_99999999-9999-4999-8999-999999999999",
        "adjudication_version": 1,
        "created_at": NOW,
        "actor": actor(),
        "decisions": [
            {
                "finding_id": finding_id,
                "decision": "deferred",
                "rationale": "Need more evidence.",
            }
        ],
    }
    trace = {
        "schema_version": "1.0",
        "trace_id": trace_id,
        "trace_version": 1,
        "created_at": NOW,
        "bundle_id": bundle_id,
        "contract_id": contract_id,
        "run_id": None,
        "goal_id": None,
        "invocation_id": None,
        "events": [
            {
                "sequence": 1,
                "kind": "deterministic_check",
                "payload_sha256": ZERO_DIGEST,
                "evaluator_id": evaluator_id,
            }
        ],
        "redaction_policy": "fixture-1",
        "variable_fields": ["created_at"],
    }
    report = {
        "schema_version": "1.0",
        "report_id": "report_99999999-9999-4999-8999-999999999999",
        "report_version": 1,
        "created_at": NOW,
        "bundle_id": bundle_id,
        "contract_id": contract_id,
        "trace_ids": [trace_id],
        "finding_ids": [finding_id],
        "feedback_ids": [feedback["feedback_id"]],
        "adjudication_ids": [adjudication["adjudication_id"]],
        "status": "blocked",
        "machine_report_sha256": ZERO_DIGEST,
        "human_report": {
            "path": "reports/report.md",
            "content_sha256": ZERO_DIGEST,
            "media_type": "text/markdown",
            "size_bytes": 123,
        },
    }
    loop = {
        "schema_version": "1.0",
        "loop_id": "loop_99999999-9999-4999-8999-999999999999",
        "loop_version": 1,
        "run_id": RUN_ID,
        "gig_id": GIG_ID,
        "bundle_id": bundle_id,
        "contract_id": contract_id,
        "state": "complete",
        "cycle_cap": 1,
        "cycle_count": 0,
        "stage_sequence": [
            {"state": "reviewing", "sequence": 1},
            {"state": "verifying", "sequence": 2},
            {"state": "feedback_pending", "sequence": 3},
            {"state": "addressing", "sequence": 4},
            {"state": "closing", "sequence": 5},
            {"state": "complete", "sequence": 6},
        ],
        "finding_ids": [finding_id],
        "report_ids": [report["report_id"]],
        "feedback_ids": [feedback["feedback_id"]],
        "adjudication_ids": [adjudication["adjudication_id"]],
        "trace_ids": [trace_id],
        "addressed_artifact_ids": ["addressed_99999999-9999-4999-8999-999999999999"],
        "terminal_decision": {"state": "complete", "reason": "all accepted Findings resolved", "next_action": None},
        "created_at": NOW,
        "updated_at": NOW,
    }
    addressed = {
        "schema_version": "1.0",
        "artifact_id": "addressed_99999999-9999-4999-8999-999999999999",
        "artifact_version": 1,
        "loop_id": loop["loop_id"],
        "bundle_id": bundle_id,
        "contract_id": contract_id,
        "report_id": report["report_id"],
        "source_artifact": artifact("references/source.txt", media_type="text/plain"),
        "content_sha256": ZERO_DIGEST,
        "media_type": "text/plain",
        "size_bytes": 123,
        "accepted_finding_ids": [finding_id],
        "status": "addressed",
        "created_at": NOW,
    }
    invocation_id = "inv_99999999-9999-4999-8999-999999999999"
    invocation = {
        "schema_version": "1.0",
        "record_version": 1,
        "run_id": RUN_ID,
        "goal_id": GOAL_A,
        "invocation_id": invocation_id,
        "role": "researcher",
        "provider_family": "openai_api",
        "configured_selector": "target-a",
        "endpoint_identity": "responses",
        "resolved_model": "model-a",
        "adapter_identity": "adapter/openai@1",
        "request": {
            "selected_references": [{"reference_id": reference_id, "content_sha256": ZERO_DIGEST}],
            "request_artifact": artifact("invocations/inv-999/request.json"),
            "request_sha256": ZERO_DIGEST,
        },
        "outcome": "succeeded",
        "finish": "completed",
        "cancellation": "not_applicable",
        "error": None,
        "usage": usage(),
        "boundary": {
            "redaction": {"policy_version": "fixture-1", "result": "passed"},
            "credential": {"reference": None, "lookup": "not_requested"},
            "network": {"policy": "explicit_permission", "result": "permitted"},
            "check_order_version": "s18-05-1",
        },
        "extensions": [{"namespace": "provider", "name": "finish_reason", "value_type": "string", "value": "stop"}],
        "replay": {"stable_sha256": ZERO_DIGEST, "variable_fields": ["created_at"]},
        "terminal_committed_at": "2026-08-02T12:05:00Z",
    }
    exchange = {
        "schema_version": "1.0",
        "record_version": 1,
        "record_sha256": ZERO_DIGEST,
        "run_id": RUN_ID,
        "edge_id": "edge_99999999-9999-4999-8999-999999999999",
        "source_goal_id": GOAL_A,
        "receiver_goal_id": GOAL_B,
        "kind": "handoff",
        "source_invocation_ids": [invocation_id],
        "source_artifacts": [{"artifact": artifact("outputs/source.json"), "invocation_id": invocation_id, "goal_id": GOAL_A}],
        "handoff": {
            "index": 1,
            "cap": 1,
            "input_artifact": artifact("handoffs/received.json"),
            "parent_artifact": artifact("outputs/source.json"),
            "hidden_context": False,
        },
        "comparison": None,
        "status": "received",
        "automatic_fallback": False,
        "retry_count": 0,
        "created_at": NOW,
    }
    proposal_interview = {
        "schema_version": "1.0",
        "record_version": 1,
        "revision": 1,
        "parent_revision": None,
        "session_id": "session_99999999-9999-4999-8999-999999999999",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "proposal_id": None,
        "request": {
            "kind": "repository-feature",
            "artifact": artifact("review/interviews/request.txt", media_type="text/plain"),
            "content_sha256": ZERO_DIGEST,
        },
        "state": "questions_pending",
        "round": 1,
        "max_rounds": 3,
        "references": [
            {
                "reference_id": "ref_99999999-9999-4999-8999-999999999999",
                "content_sha256": ZERO_DIGEST,
                "decision": "excluded",
            }
        ],
        "selected_reference_ids": [],
        "questions": [
            {
                "question_id": "scope",
                "answer_type": "text",
                "required": True,
                "options": [],
                "depends_on": [],
                "rationale": "Define the requested outcome.",
                "provenance": "g22://scope",
            }
        ],
        "answers": [],
        "boundary": {"privacy": "local_only", "capability": "none", "effect": "read_local"},
        "events": [
            {
                "sequence": 1,
                "event": "session_created",
                "state": "questions_pending",
                "actor": actor(),
                "payload_sha256": ZERO_DIGEST,
                "occurred_at": NOW,
            }
        ],
        "approval": None,
        "terminal_reason": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    target_effect = {
        "schema_version": "1.0",
        "effect_id": "effect_99999999-9999-4999-8999-999999999999",
        "effect_version": 5,
        "state": "applied",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "gig_proposal_id": PROPOSAL_ID,
        "target": {
            "kind": "git",
            "binding_sha256": ZERO_DIGEST,
            "repository_identity_sha256": ZERO_DIGEST,
            "git_head": COMMIT,
        },
        "operator": actor(),
        "effect_kind": "write_target",
        "operation": "replace_file",
        "relative_target_path": "README.md",
        "source_artifact": artifact("addressed/replacement.md", media_type="text/markdown"),
        "expected_before_sha256": ZERO_DIGEST,
        "expected_after_sha256": ONE_DIGEST,
        "expected_file_mode": 420,
        "authorization": {
            "gig_proposal_id": PROPOSAL_ID,
            "operator": actor(),
            "target_binding_sha256": ZERO_DIGEST,
            "relative_target_path": "README.md",
            "source_artifact_sha256": ZERO_DIGEST,
            "expected_before_sha256": ZERO_DIGEST,
            "expected_after_sha256": ONE_DIGEST,
            "authorized_at": NOW,
            "cancellation_policy": "before_exposure_only",
            "commit_policy": "leave_uncommitted",
            "authorization_sha256": ZERO_DIGEST,
        },
        "cancellation_policy": "before_exposure_only",
        "commit_policy": "leave_uncommitted",
        "patch_identity": {
            "relative_target_path": "README.md",
            "source_artifact_sha256": ZERO_DIGEST,
            "expected_before_sha256": ZERO_DIGEST,
            "expected_after_sha256": ONE_DIGEST,
            "expected_file_mode": 420,
            "descriptor_sha256": ZERO_DIGEST,
        },
        "target_before_manifest": artifact("manifests/target-effects/effect-before.json"),
        "target_after_manifest": artifact("manifests/target-effects/effect-after.json"),
        "created_at": NOW,
        "updated_at": NOW,
        "terminal_reason": None,
    }
    learning_record = {
        "schema_version": "1.0",
        "record_version": 1,
        "learning_id": "learning_99999999-9999-4999-8999-999999999999",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "subject": {"kind": "run", "run_id": RUN_ID},
        "active_version": 1,
        "active_pointer_sha256": ZERO_DIGEST,
        "source": {
            "kind": "finding",
            "source_id": "finding_99999999-9999-4999-8999-999999999999",
            "artifact": artifact("evidence/finding.json"),
        },
        "provenance": "observed_outcome",
        "observed_at": NOW,
        "explanation": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    improvement_manifest = {
        "schema_version": "1.0",
        "manifest_version": 1,
        "manifest_id": "improve_manifest_99999999-9999-4999-8999-999999999999",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "base_gig_version": 1,
        "parent_proposal_id": PROPOSAL_ID,
        "learning_record_ids": [learning_record["learning_id"]],
        "change_request": None,
        "changes": [{
            "target": "rubric",
            "path": "rubric.minimum_evidence",
            "operation": "replace",
            "before": artifact("before.json"),
            "after": artifact("after.json"),
        }],
        "evidence_gate": {
            "result": "pass",
            "report": artifact("evidence-gate.json"),
            "supporting_record_ids": [learning_record["learning_id"]],
            "checked_at": NOW,
        },
        "quality_gate": {
            "result": "pass",
            "report": artifact("quality-gate.json"),
            "evaluator_version": "g20-v1",
            "corpus_id": "corpus_g20_v1",
            "baseline_sha256": ZERO_DIGEST,
            "candidate_sha256": ONE_DIGEST,
            "baseline": {"development": {"recall": 1, "false_positive_rate": 0}, "calibration": {"recall": 1, "false_positive_rate": 0}, "final_held_out_acceptance": {"recall": 1, "false_positive_rate": 0}},
            "candidate": {"development": {"recall": 1, "false_positive_rate": 0}, "calibration": {"recall": 1, "false_positive_rate": 0}, "final_held_out_acceptance": {"recall": 1, "false_positive_rate": 0}},
            "minimums": {"recall": 1},
            "maximums": {"false_positive_rate": 1},
            "case_counts": {"development": 4, "calibration": 2, "final_held_out_acceptance": 2},
            "development": {"case_count": 4, "bar_pass": True, "metrics": {"recall": 1, "false_positive_rate": 0}},
            "calibration": {"case_count": 2, "bar_pass": True, "metrics": {"recall": 1, "false_positive_rate": 0}},
            "final_holdout": {"case_count": 2, "bar_pass": True, "metrics": {"recall": 1, "false_positive_rate": 0}},
            "final_holdout_pass": True,
            "no_regression": True,
            "checked_at": NOW,
        },
        "created_at": NOW,
        "updated_at": NOW,
    }
    return {
        "urn:gigai:schema:gig-proposal:1": proposal,
        "urn:gigai:schema:active-gig-version:1": active,
        "urn:gigai:schema:goal-graph:1": goal_graph(),
        "urn:gigai:schema:run-brief-frontmatter:1": brief,
        "urn:gigai:schema:run-manifest:1": manifest,
        "urn:gigai:schema:run-details:1": details,
        "urn:gigai:schema:handoff-frontmatter:1": handoff,
        "urn:gigai:schema:review-bundle:1": bundle,
        "urn:gigai:schema:review-contract:1": contract,
        "urn:gigai:schema:finding:1": finding,
        "urn:gigai:schema:feedback:1": feedback,
        "urn:gigai:schema:adjudication:1": adjudication,
        "urn:gigai:schema:trace:1": trace,
        "urn:gigai:schema:report:1": report,
        "urn:gigai:schema:review-loop:1": loop,
        "urn:gigai:schema:addressed-artifact:1": addressed,
        "urn:gigai:schema:capability-manifest:1": capability_manifest(),
        "urn:gigai:schema:capability-installation:1": capability_installation(),
        "urn:gigai:schema:model-invocation:1": invocation,
        "urn:gigai:schema:model-exchange:1": exchange,
        "urn:gigai:schema:proposal-interview:1": proposal_interview,
        "urn:gigai:schema:target-effect:1": target_effect,
        "urn:gigai:schema:learning-record:1": learning_record,
        "urn:gigai:schema:improvement-manifest:1": improvement_manifest,
    }


class SerializedContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        schema_root = importlib_resources.files("gigai.schemas")
        schema_resources = {
            item.name: item
            for item in schema_root.iterdir()
            if item.name.endswith(".schema.json")
        }
        if set(schema_resources) != EXPECTED_SCHEMA_NAMES:
            missing = sorted(EXPECTED_SCHEMA_NAMES - set(schema_resources))
            additional = sorted(set(schema_resources) - EXPECTED_SCHEMA_NAMES)
            raise AssertionError(
                f"schema resource set mismatch: missing={missing}, "
                f"additional={additional}"
            )
        cls.schemas = {
            schema["$id"]: schema
            for schema in (
                json.loads(schema_resources[name].read_text(encoding="utf-8"))
                for name in sorted(schema_resources)
            )
        }
        resources = [
            (schema_id, Resource.from_contents(schema))
            for schema_id, schema in cls.schemas.items()
        ]
        cls.registry = Registry().with_resources(resources)

    def validator(self, schema_id: str) -> Draft202012Validator:
        return Draft202012Validator(
            self.schemas[schema_id],
            registry=self.registry,
            format_checker=FormatChecker(),
        )

    def test_all_schema_documents_are_valid_draft_2020_12(self) -> None:
        self.assertEqual(len(self.schemas), 25)
        for schema_id, schema in self.schemas.items():
            with self.subTest(schema_id=schema_id):
                Draft202012Validator.check_schema(schema)

    def test_one_golden_instance_for_every_serialized_boundary(self) -> None:
        instances = valid_instances()
        self.assertEqual(
            set(instances), set(self.schemas) - {"urn:gigai:schema:common:1"}
        )
        for schema_id, instance in instances.items():
            with self.subTest(schema_id=schema_id):
                self.validator(schema_id).validate(instance)
                canonical_json_bytes(instance)

    def test_unknown_field_missing_required_field_and_malformed_id_fail(self) -> None:
        instances = valid_instances()
        proposal_id = "urn:gigai:schema:gig-proposal:1"

        unknown = copy.deepcopy(instances[proposal_id])
        unknown["surprise"] = True
        self.assertTrue(list(self.validator(proposal_id).iter_errors(unknown)))

        missing = copy.deepcopy(instances[proposal_id])
        del missing["commission"]
        self.assertTrue(list(self.validator(proposal_id).iter_errors(missing)))

        malformed = copy.deepcopy(instances[proposal_id])
        malformed["gig_id"] = "gig_01-not-a-real-id"
        self.assertTrue(list(self.validator(proposal_id).iter_errors(malformed)))

    def test_goal_graph_semantics_accept_valid_graph(self) -> None:
        validate_goal_graph(goal_graph())

    def test_goal_graph_semantics_reject_cycles(self) -> None:
        graph = goal_graph()
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
        with self.assertRaisesRegex(GoalGraphError, "cycle"):
            validate_goal_graph(graph)

    def test_goal_graph_semantics_reject_unreachable_required_goal(self) -> None:
        graph = goal_graph()
        graph["edges"] = []
        with self.assertRaisesRegex(GoalGraphError, "unreachable"):
            validate_goal_graph(graph)


if __name__ == "__main__":
    unittest.main()
