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
    "application-event.schema.json",
    "external-recording-run-v2.schema.json",
    "external-recording-plan-v2.schema.json",
    "external-recording-invocation-v2.schema.json",
    "external-recording-checkpoint-v2.schema.json",
    "external-recording-receipt-v2.schema.json",
    "native-record-content.schema.json",
    "external-recording-invocation.schema.json",
    "external-recording-plan.schema.json",
    "external-recording-run.schema.json",
    "external-recording-checkpoint.schema.json",
    "external-recording-receipt.schema.json",
    "addressed-artifact.schema.json",
    "adjudication.schema.json",
    "active-gig-version.schema.json",
    "active-gig-version-v2.schema.json",
    "common.schema.json",
    "capability-installation.schema.json",
    "capability-manifest.schema.json",
    "capability-review-decision.schema.json",
    "capability-successor-binding.schema.json",
    "feedback.schema.json",
    "finding.schema.json",
    "gig-builder-session.schema.json",
    "gig-comparison.schema.json",
    "gig-discovery-manifest.schema.json",
    "gig-package.schema.json",
    "gig-occurrence.schema.json",
    "gig-proposal.schema.json",
    "gig-proposal-v2.schema.json",
    "gig-graph-set.schema.json",
    "graph-selection-record.schema.json",
    "graph-selection-record-v2.schema.json",
    "goal-graph.schema.json",
    "handoff-frontmatter.schema.json",
    "improvement-manifest.schema.json",
    "learning-record.schema.json",
    "model-exchange.schema.json",
    "model-invocation.schema.json",
    "model-invocation-v2.schema.json",
    "model-invocation-v3.schema.json",
    "proposal-interview.schema.json",
    "proposal-draft-manifest.schema.json",
    "provider-review-closeout-receipt.schema.json",
    "private-record-revision.schema.json",
    "reference-record.schema.json",
    "run-input-record.schema.json",
    "scout-operation-receipt.schema.json",
    "template-instance-binding.schema.json",
    "workpad-layout.schema.json",
    "report.schema.json",
    "runtime-comparison-attempt.schema.json",
    "runtime-comparison-intent.schema.json",
    "runtime-comparison.schema.json",
    "runtime-evaluation-pack.schema.json",
    "requirements-baseline-approval.schema.json",
    "review-bundle.schema.json",
    "review-contract.schema.json",
    "review-input-record.schema.json",
    "run-brief-frontmatter.schema.json",
    "run-details.schema.json",
    "run-manifest.schema.json",
    "run-manifest-v2.schema.json",
    "run-plan.schema.json",
    "run-plan-v2.schema.json",
    "review-loop.schema.json",
    "role-reference.schema.json",
    "target-effect.schema.json",
    "trace.schema.json",
    "verification-record.schema.json",
    "scout-answer-association.schema.json",
    "scout-definition-export-manifest.schema.json",
    "scout-document-revision-v1.schema.json",
    "scout-document-selection-v1.schema.json",
    "scout-document-selection-v2.schema.json",
    "scout-interview-preparation.schema.json",
    "scout-private-transfer-manifest.schema.json",
    "scout-proposal-discovery-job.schema.json",
    "scout-proposal-revision.schema.json",
    "scout-public-import-input.schema.json",
    "scout-public-import-progress.schema.json",
    "scout-tailor-selection-v2.schema.json",
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


def capability_review_decision() -> dict[str, Any]:
    permissions = {
        "filesystem": "write_isolated", "network": "none", "credentials": "none",
    }
    return {
        "schema_version": "1.0",
        "decision_id": "capreview_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "decision_version": 1,
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "base_version": 1,
        "base_proposal_id": PROPOSAL_ID,
        "capability_id": CAPABILITY_ID,
        "operation_key": "review-fixture",
        "request_sha256": ZERO_DIGEST,
        "parent_manifest_ref": artifact("manifests/capabilities/parent.json"),
        "reviewed_manifest_ref": artifact("manifests/capabilities/reviewed.json"),
        "reviewer": {"kind": "agent", "id": "fixture-reviewer"},
        "reviewer_outcome": "passed",
        "reviewer_rationale": "Fixture source review completed.",
        "evidence_refs": ["reviews/fixture-review.md"],
        "operator_consent": {
            "confirmed": True, "actor": {"kind": "operator", "id": "local-user"},
            "effects": ["write_workpad"], "permissions": dict(permissions),
        },
        "source_binding": {
            "inventory_sha256": ZERO_DIGEST, "operations": ["record_create"],
            "effects": ["write_workpad"], "permissions": dict(permissions),
        },
        "created_at": NOW,
    }


def capability_successor_binding() -> dict[str, Any]:
    capability = tool_capability_manifest()["capabilities"][0]
    return {
        "schema_version": "1.0", "proposal_id": PROPOSAL_ID,
        "project_id": PROJECT_ID, "gig_id": GIG_ID,
        "operation_key": "successor-fixture", "input_sha256": ZERO_DIGEST,
        "base_gig_version": 1,
        "base_proposal_id": "gp_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "parent_proposal_id": "gp_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "base_pointer_ref": artifact("manifests/active-gig-version.json"),
        "base_proposal_ref": artifact("manifests/proposals/base.json"),
        "pending_proposal_ref": artifact("manifests/proposals/pending.json"),
        "parent_manifest_ref": artifact("manifests/capabilities/parent.json"),
        "decision_ref": artifact("manifests/capability-reviews/reviewed.json"),
        "reviewed_manifest_ref": artifact("manifests/capabilities/reviewed.json"),
        "source_binding": {
            "capability_id": capability["capability_id"],
            **capability["tool_binding"],
            "permissions": dict(capability["permissions"]),
        },
        "created_at": NOW,
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


def tool_capability_manifest() -> dict[str, Any]:
    manifest = capability_manifest()
    capability = manifest["capabilities"][0]
    entry_path = f"tools/{CAPABILITY_ID}/record_tool.py"
    wrapper = {
        "path": "gig.py", "content_sha256": ZERO_DIGEST,
        "media_type": "text/x-python", "size_bytes": 123,
    }
    inventory = [wrapper, {**wrapper, "path": entry_path}]
    capability.update({
        "kind": "tool",
        "declared_effects": ["write_workpad"],
        "source_constraints": {
            "allowed_source_kinds": ["local_artifact"],
            "required_digest": ZERO_DIGEST,
            "required_identity": "record_tool.py",
        },
        "tool_binding": {
            "entry_path": entry_path, "inventory": inventory,
            "inventory_sha256": sha256_digest(canonical_json_bytes(inventory)),
            "operations": ["record_create"], "effects": ["write_workpad"],
            "wrapper_ref": copy.deepcopy(wrapper),
        },
    })
    return manifest


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
    occurrence = {
        "schema_version": "1.0",
        "occurrence_version": 1,
        "occurrence_id": "occurrence_99999999-9999-4999-8999-999999999999",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "gig_version": 1,
        "cadence": "daily",
        "occurrence_key": "2026-08-02",
        "trigger_actor": actor(),
        "outcome_actor": None,
        "scheduled_for": NOW,
        "snapshot": {
            "bundle_id": "bundle_99999999-9999-4999-8999-999999999999",
            "bundle_version": 1,
            "artifact": artifact("manifests/review-bundles/occurrence.json"),
            "reference_set_sha256": ZERO_DIGEST,
        },
        "prior_occurrence_id": None,
        "run_id": RUN_ID,
        "state": "run_terminal",
        "outcome": "succeeded",
        "comparison": None,
        "reason": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    comparison = {
        "schema_version": "1.0",
        "comparison_version": 1,
        "comparison_id": "comparison_99999999-9999-4999-8999-999999999999",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "current_occurrence_id": occurrence["occurrence_id"],
        "prior_occurrence_id": "occurrence_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "current_run_id": RUN_ID,
        "prior_run_id": "run_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "current_gig_version": 1,
        "prior_gig_version": 1,
        "current_snapshot": artifact("manifests/review-bundles/current.json"),
        "prior_snapshot": artifact("manifests/review-bundles/prior.json"),
        "current_output": artifact("runs/current/target-after.json"),
        "prior_output": artifact("runs/prior/target-after.json"),
        "current_goal_graph": artifact("graphs/current.json"),
        "prior_goal_graph": artifact("graphs/prior.json"),
        "current_review_contracts": [artifact("contracts/current.json")],
        "prior_review_contracts": [artifact("contracts/prior.json")],
        "method_id": "content_digest",
        "method_version": "g21-v1",
        "result": "unchanged",
        "reason": None,
        "evidence": [artifact("comparisons/evidence.json")],
        "selected_winner": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    builder_session = {
        "schema_version": "1.0",
        "record_version": 1,
        "session_id": "session_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "request_kind": "create",
        "state": "clarify",
        "revision": 1,
        "parent_revision": None,
        "round": 1,
        "max_rounds": 8,
        "intent": {"text_artifact": artifact("review/intents/request.txt", media_type="text/plain"), "content_sha256": ZERO_DIGEST, "answered_at": NOW, "actor": {"kind": "operator", "id": "local-user"}},
        "references": [],
        "questions": [{"question_id": "main-drive", "answer_type": "text", "required": True, "options": [], "depends_on": [], "rationale": "Define the Gig drive.", "provenance": "fixture://g26"}],
        "answers": [],
        "model_selection": {"target_name": "offline-default", "endpoint_name": "offline", "model": "fixture-v1", "adapter": "deterministic", "readiness": "usable", "selection_actor": {"kind": "operator", "id": "local-user"}, "selection_digest": ZERO_DIGEST},
        "policy": {"network": "local_only", "credential_reference": None, "budget": budget(), "cancellation": "operator_or_timeout"},
        "accounting": {"model_calls": 0, "input_tokens": None, "output_tokens": None, "elapsed_ms": 0, "cost": None, "cost_currency": None},
        "draft": None,
        "terminal_reason": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    draft_manifest = {
        "schema_version": "1.0",
        "manifest_version": 1,
        "manifest_id": "draft_manifest_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "session_id": builder_session["session_id"],
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "parent_manifest_id": None,
        "model_selection": {"target_name": "offline-default", "endpoint_name": "offline", "model": "fixture-v1", "adapter": "deterministic", "selection_digest": ZERO_DIGEST},
        "build": {"status": "completed", "mode": "deterministic_fixture", "started_at": NOW, "completed_at": NOW, "accounting": {"model_calls": 1, "input_tokens": None, "output_tokens": None, "elapsed_ms": 0, "cost": None, "cost_currency": None}},
        "proposal_artifact": artifact("manifests/gig-proposal.json"),
        "research": {"summary": "A bounded fixture proposal.", "citations": [], "assumptions": ["Operator review is required."], "unresolved_questions": []},
        "boundary": {"reference_ids": [], "network": "local_only", "credential_reference": None, "effects": ["write_workpad"]},
        "created_at": NOW,
        "updated_at": NOW,
    }
    discovery_manifest = {
        "schema_version": "1.0",
        "manifest_version": 1,
        "manifest_id": "discovery_manifest_cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "session_id": builder_session["session_id"],
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "request_kind": "create",
        "parent_manifest_id": None,
        "capabilities": [
            {
                "capability_id": "local_reference_read",
                "status": "usable",
                "status_source": "accepted_contract",
                "network": "local_only",
                "effects": ["read_target"],
            }
        ],
        "research_plan": {
            "status": "not_started",
            "sources": [],
            "network": "local_only",
            "privacy": "local_only",
            "credential_reference": None,
            "budget": budget(),
            "evidence_requirements": [],
        },
        "question_rounds": [
            {
                "round": 1,
                "parent_round": None,
                "status": "pending",
                "model_selection_digest": ZERO_DIGEST,
                "provenance_artifact": artifact("discovery/questions.json"),
                "questions": [builder_session["questions"][0]],
                "answers": [],
                "created_at": NOW,
                "updated_at": NOW,
            }
        ],
        "stable_definition": {
            "artifact": artifact("discovery/stable-definition.json"),
            "fields": ["goals"],
        },
        "run_input_contract": {
            "artifact": artifact("discovery/run-inputs.json"),
            "fields": [],
        },
        "improve_context": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    role_reference = {
        "schema_version": "1.0",
        "namespace": "model_invocation",
        "id": "proposal-questioner",
        "version": 1,
    }
    plan_input = artifact("runs/run-777/review/evidence/sealed-input.json")
    run_plan = {
        "schema_version": "1.0",
        "run_plan_id": "run_plan_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "plan_version": 1,
        "state": "sealed",
        "gig_id": GIG_ID,
        "gig_version": 1,
        "journal_commit": COMMIT,
        "project_id": PROJECT_ID,
        "workpad_locator": f"registry:{PROJECT_ID}",
        "goal_graph": graph_ref,
        "review_contract": artifact("runs/run-777/review-contract.json"),
        "classification": {"task_class": "document_review", "artifact_class": "text", "confidence": "high", "classifier_version": "fixture-1", "considered_inputs": [plan_input], "reason": "fixture input", "override": None},
        "profile": {"profile_id": "focused", "profile_version": 1, "selection": "deterministic", "opt_in": None, "usage_unreported_policy": "block_before_call"},
        "phases": [
            {"phase": "review", "sequence": 1, "required": True, "participant_ids": ["participant_reviewer"], "input_refs": [plan_input], "output_kinds": ["finding"], "stopping_rule": "one pass", "state": "planned", "not_required_reason": None},
            {"phase": "verify", "sequence": 2, "required": True, "participant_ids": ["participant_verifier"], "input_refs": [plan_input], "output_kinds": ["verification-record"], "stopping_rule": "one pass", "state": "planned", "not_required_reason": None},
            {"phase": "adjudicate", "sequence": 3, "required": False, "participant_ids": [], "input_refs": [plan_input], "output_kinds": ["adjudication"], "stopping_rule": "zero loops", "state": "not_required", "not_required_reason": "focused fixture"},
            {"phase": "resolve", "sequence": 4, "required": True, "participant_ids": [], "input_refs": [plan_input], "output_kinds": ["report"], "stopping_rule": "deterministic", "state": "planned", "not_required_reason": None},
        ],
        "participants": [
            {"participant_id": "participant_reviewer", "roles": ["reviewer"], "model_target_id": "offline-default", "target_configuration_ref": artifact("targets/reviewer.json"), "provider_id": "offline", "discovery_ref": artifact("discovery.json"), "independence_group": "REVIEW", "assignment_reason": "fixture", "target_reuse_disclosure": None},
            {"participant_id": "participant_verifier", "roles": ["verifier"], "model_target_id": "offline-default", "target_configuration_ref": artifact("targets/verifier.json"), "provider_id": "offline", "discovery_ref": artifact("discovery.json"), "independence_group": "VERIFY", "assignment_reason": "fixture", "target_reuse_disclosure": "Target reuse disclosed in fixture."},
        ],
        "inputs": [{"input_id": "input_a", "role": "primary", "record_ref": plan_input, "snapshot_ref": plan_input}],
        "capabilities": {"manifest": None, "required_capability_ids": ["gigai.offline"]},
        "effects": ["write_workpad"],
        "budget": {"max_model_calls": 2, "max_tool_calls": 0, "max_tokens": 8000, "max_cost": "0.50", "currency": "USD", "max_wall_time_ms": 300000, "max_parallel_goals": 1},
        "policy_sha256": ZERO_DIGEST,
        "discovery_snapshot_refs": [ZERO_DIGEST],
        "sealed_sources": [plan_input],
        "created_at": NOW,
        "sealed_at": NOW,
        "sealed_by": actor(),
    }
    graph_set_ref = artifact("manifests/graph-sets/fixture.json")
    selected_graph_ref = artifact("manifests/graph-sets/fixture/career.json")
    graph_set = {
        "schema_version": "1.0", "graph_set_id": "graph_set_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "gig_id": GIG_ID,
        "graphs": [{"graph_id": "career", "purpose": "fixture graph", "aliases": ["role"], "routing_summary": "fixture", "goal_graph": selected_graph_ref, "input_contract": artifact("contracts/input.json"), "output_contract": artifact("contracts/output.json"), "permitted_reference_contract": artifact("contracts/references.json"), "effect_policy": ["write_workpad"], "capability_requirements": ["gigai.offline"], "provider_eligibility": {"providers": ["deterministic"]}, "budget": run_plan["budget"], "review_contract": run_plan["review_contract"], "evaluation_contract": artifact("contracts/evaluation.json"), "completion_evidence_contract": artifact("contracts/completion.json")}],
        "shared_policy": {"effects": ["write_workpad"], "required_capability_ids": ["gigai.offline"], "provider_eligibility": {"providers": ["deterministic"]}, "budget": run_plan["budget"]}, "created_at": NOW, "created_by": actor(),
    }
    selection = {"schema_version": "1.0", "selection_record_id": "graph_selection_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "gig_id": GIG_ID, "gig_version": 1, "graph_set": graph_set_ref, "selected_graph_id": "career", "selected_graph": selected_graph_ref, "selection_kind": "operator_explicit", "selector": {"kind": "operator", "actor": actor(), "rule_id": None, "rule_version": None}, "selection_reason": "fixture", "routing_evidence_refs": [], "created_at": NOW}
    selection_v2 = {**selection, "selection_kind": "agent_explicit", "selector": {"kind": "agent", "actor": {"kind": "agent", "id": "codex", "session_id": "fixture-session"}, "rule_id": None, "rule_version": None, "invocation_ref": artifact("agent-invocations/inv_fixture.json")}}
    proposal_v2 = {key: value for key, value in proposal.items() if key != "goal_graph"} | {"graph_set": graph_set_ref}
    active_v2 = {key: value for key, value in active.items() if key != "goal_graph"} | {"graph_set": graph_set_ref}
    run_plan_v2 = dict(run_plan) | {"plan_version": 2, "graph_set": graph_set_ref, "selected_graph_id": "career", "selected_graph": selected_graph_ref, "selection_record": artifact("graph-selections/fixture.json")}
    manifest_v2 = dict(manifest) | {"graph_set": graph_set_ref, "selected_graph_id": "career", "selected_graph": selected_graph_ref, "selection_record": artifact("graph-selections/fixture.json")}
    verification_record = {
        "schema_version": "1.0",
        "verification_id": "verification_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "run_id": RUN_ID,
        "gig_id": GIG_ID,
        "bundle_id": "bundle_cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "contract_id": "contract_dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "verifier_participant_id": "participant_verifier",
        "verifier_target_id": "offline-default",
        "source_finding_ids": ["finding_eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"],
        "outcomes": [{"finding_id": "finding_eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "status": "unverified", "evidence_refs": [plan_input], "reason": "fixture verification"}],
        "created_at": NOW,
    }
    return {
        "urn:gigai:schema:gig-proposal:1": proposal,
        "urn:gigai:schema:gig-proposal-v2:1": proposal_v2,
        "urn:gigai:schema:active-gig-version:1": active,
        "urn:gigai:schema:active-gig-version-v2:1": active_v2,
        "urn:gigai:schema:gig-graph-set:1": graph_set,
        "urn:gigai:schema:graph-selection-record:1": selection,
        "urn:gigai:schema:graph-selection-record-v2:1": selection_v2,
        "urn:gigai:schema:goal-graph:1": goal_graph(),
        "urn:gigai:schema:run-brief-frontmatter:1": brief,
        "urn:gigai:schema:run-manifest:1": manifest,
        "urn:gigai:schema:run-manifest-v2:1": manifest_v2,
        "urn:gigai:schema:run-plan:1": run_plan,
        "urn:gigai:schema:run-plan-v2:1": run_plan_v2,
        "urn:gigai:schema:review-input-record:1": {
            "schema_version": "1.0",
            "review_input_id": "review_input_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "role": "review_subject",
            "snapshot_ref": artifact("review-inputs/subject.md", media_type="text/markdown"),
            "approval_ref": None,
            "re_review_of": None,
        },
        "urn:gigai:schema:requirements-baseline-approval:1": {
            "schema_version": "1.0",
            "approval_id": "requirements_baseline_approval_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "project_id": PROJECT_ID,
            "gig_id": GIG_ID,
            "baseline_snapshot_ref": artifact("review-inputs/baseline.md", media_type="text/markdown"),
            "approved_by": {"kind": "operator", "id": "local-user", "model_target": None},
            "approved_at": NOW,
        },
        "urn:gigai:schema:provider-review-closeout-receipt:1": {
            "schema_version": "1.0",
            "closeout_id": "provider_review_closeout_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "project_id": PROJECT_ID,
            "gig_id": GIG_ID,
            "run_id": RUN_ID,
            "run_plan_id": run_plan["run_plan_id"],
            "run_plan_content_sha256": ZERO_DIGEST,
            "result_ref": artifact("provider-review/result.json"),
            "report_ref": artifact("provider-review/report.json"),
            "review_loop_ref": artifact("provider-review/review-loop.json"),
            "reviewer_participant_ids": ["participant_p1", "participant_p2"],
            "decision": "no_fix_required",
            "confirmed_by": {"kind": "operator", "id": "local-user", "model_target": None},
            "confirmed_at": NOW,
        },
        "urn:gigai:schema:run-details:1": details,
        "urn:gigai:schema:handoff-frontmatter:1": handoff,
        "urn:gigai:schema:review-bundle:1": bundle,
        "urn:gigai:schema:review-contract:1": contract,
        "urn:gigai:schema:finding:1": finding,
        "urn:gigai:schema:feedback:1": feedback,
        "urn:gigai:schema:adjudication:1": adjudication,
        "urn:gigai:schema:trace:1": trace,
        "urn:gigai:schema:verification-record:1": verification_record,
        "urn:gigai:schema:report:1": report,
        "urn:gigai:schema:review-loop:1": loop,
        "urn:gigai:schema:addressed-artifact:1": addressed,
        "urn:gigai:schema:capability-manifest:1": tool_capability_manifest(),
        "urn:gigai:schema:capability-review-decision:1": capability_review_decision(),
        "urn:gigai:schema:capability-successor-binding:1": capability_successor_binding(),
        "urn:gigai:schema:capability-installation:1": capability_installation(),
        "urn:gigai:schema:model-invocation:1": invocation,
        "urn:gigai:schema:model-exchange:1": exchange,
        "urn:gigai:schema:proposal-interview:1": proposal_interview,
        "urn:gigai:schema:target-effect:1": target_effect,
        "urn:gigai:schema:learning-record:1": learning_record,
        "urn:gigai:schema:improvement-manifest:1": improvement_manifest,
        "urn:gigai:schema:gig-occurrence:1": occurrence,
        "urn:gigai:schema:gig-comparison:1": comparison,
        "urn:gigai:schema:gig-builder-session:1": builder_session,
        "urn:gigai:schema:proposal-draft-manifest:1": draft_manifest,
        "urn:gigai:schema:gig-discovery-manifest:1": discovery_manifest,
        "urn:gigai:schema:gig-package:1": {
            "schema_version": "1.0",
            "package_id": "package_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "package_version": 1,
            "project_scope": "repository",
            "content_digest": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            "files": [],
        },
        "urn:gigai:schema:role-reference:1": role_reference,
        **_scout_lane_instances(),
        **_additional_schema_instances(),
        "urn:gigai:schema:template-instance-binding:2": {
            "schema_version": "2.0", "kind": "template_instance_binding",
            "project_id": PROJECT_ID, "gig_id": GIG_ID,
            "owner_id": "owner_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "username": "owner", "template_id": "scout", "instance_name": "default",
            "package_id": "package_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "package_digest": ZERO_DIGEST, "inventory_digest": ZERO_DIGEST,
            "software_inventory": artifact("manifests/software/1/inventory.json"),
            "source_digest": ZERO_DIGEST,
            "proposal_id": PROPOSAL_ID,
            "proposal_ref": artifact("manifests/gig-proposal.json"),
            "approval": {"state": "unapproved", "approved_version": None},
            "customization_parent": None,
        },
        "urn:gigai:schema:workpad-layout:2": {"schema_version":"2.0","layout_version":2,"project_id":PROJECT_ID,"gig_id":GIG_ID,"ignore_sha256":ZERO_DIGEST},
        "urn:gigai:schema:private-reference:1": {"schema_version":"1.0","reference_id":"ref_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","project_id":PROJECT_ID,"state":"sealed","kind":"resume","privacy_class":"private_sensitive","label":"resume","origin":"local_file","media_type":"text/plain","size_bytes":123,"content_sha256":ZERO_DIGEST,"snapshot":artifact("references/ref_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/source.txt", media_type="text/plain"),"created_at":NOW,"created_by":actor()},
        "urn:gigai:schema:run-input-record:1": {"schema_version":"1.0","run_input_id":"input_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","project_id":PROJECT_ID,"state":"sealed","kind":"job_description","privacy_class":"private_sensitive","label":"posting","origin":"operator_paste","media_type":"text/plain","size_bytes":123,"content_sha256":ZERO_DIGEST,"snapshot":artifact("run-inputs/input_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/source.txt", media_type="text/plain"),"created_at":NOW,"created_by":actor()},
        "urn:gigai:schema:private-record-revision:1": {"schema_version":"1.0","record_id":"record_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","revision_id":"revision_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","parent_revision":None,"project_id":PROJECT_ID,"gig_id":GIG_ID,"kind":"imported_reference","privacy_class":"private_sensitive","origin":"imported","actor":{"kind":"operator","id":"local-user"},"content":{"family":"g45_reference","reference_id":"ref_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","record_ref":artifact("references/ref_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/reference.json"),"snapshot_ref":artifact("references/ref_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/source.txt", media_type="text/plain")},"relationships":[],"created_at":NOW,"state":"active"},
        "urn:gigai:schema:scout-operation-receipt:1": {"schema_version":"1.0","operation_id":"operation_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","project_id":PROJECT_ID,"gig_id":GIG_ID,"operation":"reference_add","operation_key":"fixture","payload_sha256":ZERO_DIGEST,"outcome":"committed","artifact_refs":[artifact("references/ref_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/reference.json")],"created_at":NOW},
    }


def _additional_schema_instances() -> dict[str, dict[str, Any]]:
    """Golden values for every additive packaged resource.

    These are deliberately small synthetic records. They exercise the native
    schema boundaries without claiming provider, journal, or live Scout proof.
    """
    def scout_artifact(path: str, media_type: str = "application/json") -> dict[str, Any]:
        return {"path": path, "content_sha256": ZERO_DIGEST, "media_type": media_type, "size_bytes": 1}

    application_event = {
        "schema_version": "1.0",
        "event_id": "event_99999999-9999-4999-8999-999999999999",
        "project_id": PROJECT_ID,
        "gig_id": GIG_ID,
        "opportunity_ref": "opportunity_00000000000000000000000000000001",
        "event_kind": "saved",
        "occurred_at": NOW,
        "timezone": "America/Denver",
        "recorded_at": NOW,
        "document_refs": [],
        "notes": None,
        "supersedes": None,
        "request_evidence": {
            "kind": "direct_event_command", "scope_digest": ZERO_DIGEST,
            "actor": {"kind": "operator", "id": "local-user"},
            "recorded_at": NOW, "command": "gigai application record",
        },
        "requested_event_sha256": ZERO_DIGEST,
        "operation_key": "fixture-event",
        "payload_sha256": ZERO_DIGEST,
        "actor": {"kind": "operator", "id": "local-user"},
    }

    invocation_v2 = {
        "schema_version": "2.0", "record_version": 1, "run_id": RUN_ID,
        "goal_id": GOAL_A, "invocation_id": "inv_99999999-9999-4999-8999-999999999999",
        "role": "researcher", "provider_family": "ollama_local",
        "configured_selector": "fixture-local", "endpoint_identity": "http://127.0.0.1:11434",
        "resolved_model": "fixture-model", "adapter_identity": "ollama_local@1",
        "request": {"selected_references": [{"reference_id": "ref_99999999-9999-4999-8999-999999999999", "content_sha256": ZERO_DIGEST}], "request_artifact": artifact("invocations/inv-999/request.json"), "request_sha256": ZERO_DIGEST},
        "outcome": "succeeded", "finish": "completed", "cancellation": "not_applicable", "error": None,
        "usage": usage(),
        "boundary": {"redaction": {"policy_version": "fixture-1", "result": "passed"}, "credential": {"reference": None, "lookup": "not_requested"}, "network": {"policy": "local_loopback", "result": "local_permitted"}, "check_order_version": "fixture-1"},
        "local_identity": {"configured_endpoint": "http://127.0.0.1:11434", "configured_model": "fixture-model", "configured_model_digest": ZERO_DIGEST, "context_tokens": 128, "max_output_tokens": 64, "max_response_bytes": 4096, "configuration_sha256": ZERO_DIGEST, "observed": {"status": "not_observed", "runtime_version": None, "model_digest": None}},
        "extensions": [], "replay": {"stable_sha256": ZERO_DIGEST, "variable_fields": ["terminal_committed_at"]}, "terminal_committed_at": NOW,
    }
    invocation_v3 = copy.deepcopy(invocation_v2)
    invocation_v3["schema_version"] = "3.0"
    invocation_v3["request"] = {
        **invocation_v3["request"],
        "selected_source_descriptors": [{"source_id": "source_1", "family": "g45_run_input", "purpose": "posting", "content_sha256": ZERO_DIGEST, "identity_sha256": ZERO_DIGEST}],
    }

    comparison_case = {
        "case_id": "fixture_case", "case_version": "1.0", "result": artifact("comparisons/cases/fixture.json"),
        "grade": {}, "wall_time_ms": 1, "retries": 0, "invocation_id": None,
        "usage_unknown": True, "terminal": True,
    }
    comparison_attempt = {
        "setup_id": "fixture_local", "target_name": "fixture-target", "backend_identity": {},
        "run_id": RUN_ID, "status": "succeeded", "cases": [comparison_case],
        "run_ref": artifact("runs/fixture/run.json"),
    }
    comparison_pack = {
        "pack_id": "fixture-pack", "pack_version": "1.0", "content_sha256": ZERO_DIGEST,
        "case_ids": ["fixture_case"], "source_ref": artifact("evaluation/pack.json"),
    }
    comparison_setup = {"setup_id": "fixture_local", "target_name": "fixture-target", "expected_adapter": "ollama_local", "harness_version": "fixture-1", "prompt_version": "fixture-1", "settings": {}}
    comparison_setup_codex = {"setup_id": "fixture_codex", "target_name": "fixture-codex", "expected_adapter": "codex_cli", "harness_version": "fixture-1", "prompt_version": "fixture-1", "settings": {}}
    comparison_authority = {"journal_commit": COMMIT, "selected_graph": {}, "goal_id": GOAL_A, "input_sha256": ZERO_DIGEST, "goal_graph_ref": artifact("graphs/fixture.json"), "graph_set_ref": {}, "selected_graph_ref": None, "consent": {}}
    comparison_intent_authority = {"journal_commit": COMMIT, "graph_id": GRAPH_ID, "goal_id": GOAL_A, "pack_sha256": ZERO_DIGEST, "consent": {}}
    comparison_id = "comparison_99999999-9999-4999-8999-999999999999"

    opportunity = {"opportunity_id": "opportunity_" + "a" * 32, "snapshot_id": "snapshot_" + "b" * 32}
    document = {"document_kind": "resume", "record_id": "record_99999999-9999-4999-8999-999999999999", "revision_id": "revision_99999999-9999-4999-8999-999999999999", "content_sha256": ZERO_DIGEST}
    source_ref = {"family": "scout_record", "kind": "experience", "record_id": document["record_id"], "revision_id": document["revision_id"], "artifact_ref": scout_artifact("records/experience.json"), "owner": {"project_id": PROJECT_ID, "gig_id": GIG_ID}}
    interview_selection = {"mode": "role_only", "role": {"title": "Synthetic role"}, "posting": None, "stage_or_format": {"stage": "screen"}, "research_revisions": [], "candidate_evidence": [source_ref], "prior_feedback": [], "prior_preparation": [], "reuse_prior_research": False}
    interview_content = {"role_expectations": ["fixture"], "technical_topics": ["fixture"], "evidence_backed_stories": [], "hypothetical_examples": [], "practice_questions": ["fixture"], "interviewer_questions": [], "unresolved_gaps": []}

    public_row = {"opportunity_id": opportunity["opportunity_id"], "snapshot_id": opportunity["snapshot_id"], "source_kind": "agent_discovered", "title": "Synthetic role", "employer": "Synthetic employer", "acquisition_state": "considered", "source_snapshot": {"source_kind": "public", "locator": "fixture", "status": "captured", "content_sha256": ZERO_DIGEST, "media_type": "text/html", "size_bytes": 1}}
    progress_outcome = {"index": 0, "opportunity_id": opportunity["opportunity_id"], "snapshot_id": opportunity["snapshot_id"], "source_snapshot": {}, "outcome": "considered", "reason": "fixture"}
    proposal_revision = {
        "schema_version": "scout-proposal-revision:1", "record_id": "record_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "revision_id": "revision_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "project_id": PROJECT_ID, "gig_id": GIG_ID, "state": "active", "sealed_journal_head": COMMIT, "created_at": NOW,
        "opportunity": {"family": "scout_discovery_posting", **opportunity, "run_ref": scout_artifact("runs/discovery.json"), "receipt_ref": scout_artifact("receipts/discovery.json"), "checkpoint_ref": scout_artifact("checkpoints/discovery.json"), "posting_ref": {"artifact_id": "posting-1", "ref": scout_artifact("postings/posting.json")}},
        "assessment": {"status": "complete", "proposal": {}, "content_sha256": ZERO_DIGEST},
        "input_revisions": [{"record_id": source_ref["record_id"], "revision_id": source_ref["revision_id"], "purpose": "experience", "content_sha256": ZERO_DIGEST, "source_ref": scout_artifact("records/experience.json")}],
        "answer_associations": [], "invocation": {"run_id": RUN_ID, "goal_id": GOAL_A, "invocation_id": "inv_99999999-9999-4999-8999-999999999999", "record_sha256": ZERO_DIGEST}, "method": {"kind": "ollama_local", "target": "fixture-local", "configured_digest": ZERO_DIGEST},
    }
    return {
        "urn:gigai:schema:application-event:1": application_event,
        "urn:gigai:schema:model-invocation:2": invocation_v2,
        "urn:gigai:schema:model-invocation:3": invocation_v3,
        "urn:gigai:schema:runtime-comparison-attempt:1": comparison_attempt,
        "urn:gigai:schema:runtime-comparison-intent:1": {"schema_version": "1.0", "kind": "runtime_comparison_intent", "comparison_id": comparison_id, "project_id": PROJECT_ID, "gig_id": GIG_ID, "gig_version": 1, "graph_selector": "career", "authority": comparison_intent_authority, "pack": {"pack_id": "fixture-pack", "pack_version": "1.0", "content_sha256": ZERO_DIGEST}, "setups": [{"setup_id": "fixture_local", "target_name": "fixture-target", "expected_adapter": "ollama_local", "settings": {}, "configured_identity": {"adapter": "ollama_local", "endpoint": "http://127.0.0.1:11434", "model": "fixture-model", "model_digest": ZERO_DIGEST}}, {"setup_id": "fixture_codex", "target_name": "fixture-codex", "expected_adapter": "codex_cli", "settings": {}, "configured_identity": {"adapter": "codex_cli", "endpoint": "local", "model": "fixture-codex", "model_digest": None}}], "attempts": [{"setup_id": "fixture_local", "run_id": RUN_ID}, {"setup_id": "fixture_codex", "run_id": "run_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}], "created_at": NOW},
        "urn:gigai:schema:runtime-comparison:1": {"schema_version": "1.0", "kind": "runtime_comparison", "comparison_version": 1, "comparison_id": comparison_id, "project_id": PROJECT_ID, "gig_id": GIG_ID, "gig_version": 1, "authority": comparison_authority, "pack": comparison_pack, "grader": {"id": "fixture-grader", "version": "1.0", "validation": {}}, "setups": [comparison_setup, comparison_setup_codex], "attempts": [comparison_attempt, {**comparison_attempt, "setup_id": "fixture_codex", "target_name": "fixture-codex", "run_id": "run_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}], "status": "succeeded", "selected_winner": None, "application_state_changed": False, "created_at": NOW, "updated_at": NOW},
        "urn:gigai:schema:runtime-evaluation-pack:1": {"schema_version": "1.0", "kind": "runtime_evaluation_pack", "pack_id": "fixture-pack", "pack_version": "1.0", "owner": GIG_ID, "selected_graph": {"graph_id": GRAPH_ID, "graph_version": 1, "goal": "fixture", "input_contract_version": "1.0", "output_contract_version": "1.0"}, "goal_id": GOAL_A, "grader": {"id": "fixture-grader", "version": "1.0", "method": "deterministic", "self_grading": False}, "cases": [{"case_id": "fixture_case", "case_version": "1.0", "split": "development", "synthetic": True, "prompt": "fixture", "input": {"artifact_kind": "text", "claim": "fixture"}, "evidence": [{"id": "E1", "kind": "fixture", "text": "fixture"}], "expected": {"verdict": "pass", "criteria": {"fixture": "supported"}, "criterion_evidence": {"fixture": {"ids": ["E1"], "kinds": ["fixture"]}}}}, {"case_id": "fixture_case_two", "case_version": "1.0", "split": "final_held_out_acceptance", "synthetic": True, "prompt": "fixture", "input": {"artifact_kind": "text", "claim": "fixture"}, "evidence": [{"id": "E2", "kind": "fixture", "text": "fixture"}], "expected": {"verdict": "fail", "criteria": {"fixture": "unsupported"}, "criterion_evidence": {"fixture": {"ids": ["E2"], "kinds": ["fixture"]}}}}], "grader_validation": {"known_good": {"case_id": "fixture_case", "output": {}}, "known_bad": {"case_id": "fixture_case_two", "output": {}}}},
        "urn:gigai:schema:scout-answer-association:1": {"schema_version": "scout-answer-association:1", "association_id": "association_99999999-9999-4999-8999-999999999999", "record_id": source_ref["record_id"], "revision_id": source_ref["revision_id"], "question_ids": ["question_fixture"], "purpose": "answer", "content_sha256": ZERO_DIGEST},
        "urn:gigai:schema:scout-definition-export-manifest:1": {"schema_version": "1.0", "kind": "scout_definition_export", "format": "zip", "files": [{"path": "gig.py", "content_sha256": ZERO_DIGEST, "size_bytes": 1}]},
        "urn:gigai:scout:document-revision:1": {"revision_version": "scout-document-revision:1", "opportunity": opportunity, "document_kind": "resume", "record_id": document["record_id"], "revision_id": document["revision_id"], "content_sha256": ZERO_DIGEST, "source_lineage": [{"source_id": "posting", "content_sha256": ZERO_DIGEST, "identity": {}}], "checks": {}, "parent_revision_id": None},
        "urn:gigai:scout:document-selection:1": {"selection_version": "scout-document-selection:1", "opportunity": opportunity, "documents": [document], "selected_by": {"kind": "operator", "id": "local-user"}},
        "urn:gigai:scout:document-selection:2": {"selection_version": "scout-document-selection:2", "opportunity": opportunity, "documents": [document], "selected_by": {"kind": "operator", "id": "local-user"}, "source_run": {"authority": "tailor_run", "run_id": RUN_ID, "goal_id": GOAL_A, "invocation_id": "inv_99999999-9999-4999-8999-999999999999", "output_sha256": ZERO_DIGEST}},
        "urn:gigai:schema:scout-interview-preparation:1": {"schema_version": "1.0", "kind": "scout_interview_preparation", "record_id": source_ref["record_id"], "revision_id": source_ref["revision_id"], "parent_revision": None, "project_id": PROJECT_ID, "gig_id": GIG_ID, "state": "active", "selection": interview_selection, "content": interview_content, "feedback": [], "actor": {"kind": "operator", "id": "local-user"}, "created_at": NOW, "provenance": {"kind": "user_selected_inputs", "source_refs": [source_ref]}},
        "urn:gigai:schema:scout-private-transfer-manifest:1": {"schema_version": "1.0", "kind": "scout_private_transfer", "format": "zip", "files": [], "disclosure": "explicit operator-selected private Gig history; no credentials or provider configuration", "state_sqlite": "rebuildable_omitted", "activation": "none"},
        "urn:gigai:schema:scout-proposal-discovery-job:1": {"opportunity_id": opportunity["opportunity_id"], "snapshot_id": opportunity["snapshot_id"], "acquisition_state": "considered", "source_kind": "agent_discovered", "public_source": {}, "provenance": {}},
        "urn:gigai:schema:scout-proposal-revision:1": proposal_revision,
        "urn:gigai:schema:scout-public-import-input:1": {"schema_version": "scout-public-import-input:1", "batch_id": "fixture-batch", "project_id": PROJECT_ID, "gig_id": GIG_ID, "rows": [public_row]},
        "urn:gigai:schema:scout-public-import-progress:1": {"schema_version": "scout-public-import-progress:1", "batch_id": "fixture-batch", "project_id": PROJECT_ID, "gig_id": GIG_ID, "input_sha256": ZERO_DIGEST, "input_ref": artifact("imports/fixture-input.json"), "progress_revision": "revision_99999999-9999-4999-8999-999999999999", "parent_progress_revision": None, "parent_journal_head": None, "processed": 1, "next_index": 1, "total": 1, "deadline_seconds": "10", "stop_reason": "completed", "considered": [progress_outcome], "duplicates": [], "failures": [], "exclusions": []},
        "urn:gigai:scout:tailor-selection:2": {"selector_version": "scout-tailor-selection:2", "opportunity": opportunity, "proposal_ref": None, "answers": [], "requested_outputs": ["resume"], "source_roles": [{"source_id": "posting", "purpose": "posting"}, {"source_id": "candidate", "purpose": "candidate_evidence"}], "requested_by": {"kind": "operator", "id": "local-user"}},
    }


def _scout_lane_instances() -> dict[str, dict[str, Any]]:
    """Representative native/external boundaries, independent of lane test imports.

    These are schema goldens, not claimed journal or provider evidence. Each
    enclosing envelope uses its actual operation's invocation input shape.
    """
    suffix = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    plan_id, run_id = f"run_plan_{suffix}", f"run_{suffix}"
    reference = artifact(f"run-plans/{plan_id}/external-plan.json")
    input_id = f"input_{suffix}"
    plan_input = {
        "graph_selector": "research-role", "gig_version": None,
        "selection_record": None,
        "input_refs": [{"family": "g45_run_input", "id": input_id}],
        "output_kinds": ["role_summary"], "predecessor": None,
    }

    def invocation(operation: str, value: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "1.0", "invocation_id": f"inv_{suffix}",
            "operation": operation, "project_id": PROJECT_ID, "gig_id": GIG_ID,
            "origin": "direct_cli", "actor": {"kind": "operator", "id": "local-user"},
            "input": value, "operation_key": f"fixture-{operation}",
            "payload_sha256": ZERO_DIGEST, "created_at": NOW,
        }

    plan_invocation = invocation("plan", plan_input)
    external_plan = {
        "schema_version": "1.0", "run_plan_id": plan_id,
        "mode": "external_agent_recording", "project_id": PROJECT_ID,
        "gig_id": GIG_ID, "gig_version": 1, "journal_commit": "a" * 40,
        "graph_set": reference, "selected_graph_id": "research-role",
        "goal_graph_id": f"graph_{suffix}", "selected_graph": reference,
        "selection_record": reference, "invocation": plan_invocation,
        "inputs": [{
            "family": "g45_run_input", "run_input_id": input_id,
            "record_ref": artifact(f"run-inputs/{input_id}/input.json"),
            "snapshot_ref": artifact(f"run-inputs/{input_id}/source.txt", media_type="text/plain"),
        }],
        "output_contract": reference, "check_contract": reference,
        "effects": ["write_workpad"], "limits": {
            "max_envelope_bytes": 262144, "max_artifact_bytes": 1048576,
            "max_artifacts_per_operation": 32, "max_total_bytes_per_operation": 4194304,
            "max_checkpoint_questions": 32, "max_checkpoints_per_run": 256,
        },
        "predecessor": None, "state": "sealed", "created_at": NOW, "sealed_at": NOW,
    }
    external_run = {
        "schema_version": "1.0", "run_id": run_id, "run_plan": reference,
        "invocation": invocation("start", {"run_plan_id": plan_id}),
        "mode": "external_agent_recording", "project_id": PROJECT_ID,
        "gig_id": GIG_ID, "gig_version": 1, "status": "active", "started_at": NOW,
    }
    external_checkpoint = {
        "schema_version": "1.0", "checkpoint_id": f"checkpoint_{suffix}",
        "run_id": run_id, "run_plan": reference,
        "invocation": invocation("checkpoint", {
            "run_id": run_id, "parent_checkpoint": None, "questions": [],
            "artifact_refs": [], "reason": "fixture",
        }),
        "sequence": 1, "parent_checkpoint": None, "questions": [],
        "artifacts": [], "reason": "fixture", "created_at": NOW,
    }
    external_receipt = {
        "schema_version": "1.0", "receipt_id": f"receipt_{suffix}",
        "run_id": run_id, "run_plan": reference,
        "invocation": invocation("cancel", {"run_id": run_id, "reason": "fixture"}),
        "operation_key": "fixture-cancel", "payload_sha256": ZERO_DIGEST,
        "outcome": "cancelled", "outputs": [], "checks": [],
        "disclosure": {"execution": "unobserved", "actor_report": "declared"},
        "created_at": NOW,
    }
    native = {
        "schema_version": "1.0", "kind": "experience_qa",
        "scope": {"mode": "saved_default", "task_context_id": None, "base": None},
        "payload": {"questions": [{
            "question_id": "experience-1", "prompt": "Describe relevant experience.",
            "state": "missing", "answer": None, "provenance": None,
        }]},
    }
    role_request = {
        "family": "role_request", "role_title": "Forward Deployed Engineer",
        "role_context": None,
    }
    plan_v2 = copy.deepcopy(external_plan)
    plan_v2["schema_version"] = "2.0"
    plan_v2["inputs"] = [copy.deepcopy(role_request)]
    plan_v2["invocation"]["schema_version"] = "2.0"
    plan_v2["invocation"]["input"]["input_refs"] = [copy.deepcopy(role_request)]
    run_v2 = copy.deepcopy(external_run)
    run_v2["schema_version"] = "2.0"
    run_v2["invocation"]["schema_version"] = "2.0"
    domain_input = {
        "kind": "research", "markdown": "Research fixture\n",
        "sidecar": {
            "document_sha256": ZERO_DIGEST, "output_kind": "research",
            "run_id": run_id, "selected_inputs": copy.deepcopy(plan_v2["inputs"]),
        },
        "domain_sidecar": {
            "schema_id": "urn:gigai:scout:research-packet:2",
            # Domain semantics are validated separately by the fixed validator.
            "value": {"fixture": True},
        },
        "supporting_artifacts": [{
            "artifact_id": "source_one", "media_type": "text/plain",
            "content_base64": "eA==", "content_sha256": ZERO_DIGEST, "size_bytes": 1,
        }],
    }
    checkpoint_v2 = copy.deepcopy(external_checkpoint)
    checkpoint_v2["schema_version"] = "2.0"
    checkpoint_v2["invocation"]["schema_version"] = "2.0"
    checkpoint_v2["invocation"]["input"]["artifact_refs"] = [domain_input]
    output_v2 = {
        "kind": "research",
        "markdown": artifact(f"runs/{run_id}/artifacts/1.md", media_type="text/markdown"),
        "sidecar": artifact(f"runs/{run_id}/artifacts/1.json"),
        "domain_sidecar": artifact(f"runs/{run_id}/artifacts/1.domain.json"),
        "supporting_artifacts": [{
            "artifact_id": "source_one",
            "ref": artifact(f"runs/{run_id}/artifacts/1.supporting/source_one.bin", media_type="text/plain"),
        }],
    }
    checkpoint_v2["artifacts"] = [output_v2]
    receipt_v2 = copy.deepcopy(external_receipt)
    receipt_v2["schema_version"] = "2.0"
    receipt_v2["outcome"] = "succeeded"
    receipt_v2["outputs"] = [copy.deepcopy(output_v2)]
    receipt_v2["checks"] = [artifact(f"runs/{run_id}/artifacts/check.json")]
    receipt_v2["invocation"] = invocation("submit", {
        "run_id": run_id, "parent_checkpoint": checkpoint_v2["checkpoint_id"],
        "output_refs": [copy.deepcopy(output_v2)],
        "check_refs": copy.deepcopy(receipt_v2["checks"]),
        "disclosure": copy.deepcopy(receipt_v2["disclosure"]),
    })
    receipt_v2["invocation"]["schema_version"] = "2.0"
    return {
        "urn:gigai:schema:external-recording-plan:2": plan_v2,
        "urn:gigai:schema:external-recording-run:2": run_v2,
        "urn:gigai:schema:external-recording-invocation:2": checkpoint_v2["invocation"],
        "urn:gigai:schema:external-recording-checkpoint:2": checkpoint_v2,
        "urn:gigai:schema:external-recording-receipt:2": receipt_v2,
        "urn:gigai:schema:native-record-content:1": native,
        "urn:gigai:schema:external-recording-invocation:1": plan_invocation,
        "urn:gigai:schema:external-recording-plan:1": external_plan,
        "urn:gigai:schema:external-recording-run:1": external_run,
        "urn:gigai:schema:external-recording-checkpoint:1": external_checkpoint,
        "urn:gigai:schema:external-recording-receipt:1": external_receipt,
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
        self.assertEqual(len(self.schemas), 82)
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

    def test_additive_schema_goldens_reject_unknown_and_missing_fields(self) -> None:
        instances = valid_instances()
        additive_ids = {
            "urn:gigai:schema:application-event:1",
            "urn:gigai:schema:model-invocation:2",
            "urn:gigai:schema:model-invocation:3",
            "urn:gigai:schema:runtime-comparison-attempt:1",
            "urn:gigai:schema:runtime-comparison-intent:1",
            "urn:gigai:schema:runtime-comparison:1",
            "urn:gigai:schema:runtime-evaluation-pack:1",
            "urn:gigai:schema:scout-answer-association:1",
            "urn:gigai:schema:scout-definition-export-manifest:1",
            "urn:gigai:scout:document-revision:1",
            "urn:gigai:scout:document-selection:1",
            "urn:gigai:scout:document-selection:2",
            "urn:gigai:schema:scout-interview-preparation:1",
            "urn:gigai:schema:scout-private-transfer-manifest:1",
            "urn:gigai:schema:scout-proposal-discovery-job:1",
            "urn:gigai:schema:scout-proposal-revision:1",
            "urn:gigai:schema:scout-public-import-input:1",
            "urn:gigai:schema:scout-public-import-progress:1",
            "urn:gigai:scout:tailor-selection:2",
        }
        nested_paths = {
            "urn:gigai:schema:application-event:1": ("request_evidence",),
            "urn:gigai:schema:model-invocation:2": ("request",),
            "urn:gigai:schema:model-invocation:3": ("request",),
            "urn:gigai:schema:runtime-comparison-attempt:1": ("cases", 0),
            "urn:gigai:schema:runtime-comparison-intent:1": ("authority",),
            "urn:gigai:schema:runtime-comparison:1": ("authority",),
            "urn:gigai:schema:runtime-evaluation-pack:1": ("selected_graph",),
            "urn:gigai:schema:scout-definition-export-manifest:1": ("files", 0),
            "urn:gigai:scout:document-revision:1": ("opportunity",),
            "urn:gigai:scout:document-selection:1": ("opportunity",),
            "urn:gigai:scout:document-selection:2": ("source_run",),
            "urn:gigai:schema:scout-interview-preparation:1": ("selection",),
            "urn:gigai:schema:scout-proposal-revision:1": ("opportunity",),
            "urn:gigai:schema:scout-public-import-input:1": ("rows", 0),
            "urn:gigai:schema:scout-public-import-progress:1": ("considered", 0),
            "urn:gigai:scout:tailor-selection:2": ("opportunity",),
        }
        self.assertEqual(set(additive_ids), set(instances) & additive_ids)
        for schema_id in sorted(additive_ids):
            with self.subTest(schema_id=schema_id):
                validator = self.validator(schema_id)
                base = instances[schema_id]
                unknown = copy.deepcopy(base)
                unknown["unexpected_nested_authority"] = True
                self.assertTrue(list(validator.iter_errors(unknown)))
                missing = copy.deepcopy(base)
                del missing[self.schemas[schema_id]["required"][0]]
                self.assertTrue(list(validator.iter_errors(missing)))
                path = nested_paths.get(schema_id)
                if path:
                    nested = copy.deepcopy(base)
                    value = nested
                    for part in path:
                        value = value[part]
                    value["unexpected_nested_authority"] = True
                    self.assertTrue(list(validator.iter_errors(nested)))

    def test_external_domain_v2_preserves_nested_identity(self) -> None:
        instances = _scout_lane_instances()
        inv_id = "urn:gigai:schema:external-recording-invocation:2"
        for path in (
            ("actor",), ("input",), ("input", "artifact_refs", 0),
            ("input", "artifact_refs", 0, "sidecar"),
            ("input", "artifact_refs", 0, "domain_sidecar"),
            ("input", "artifact_refs", 0, "supporting_artifacts", 0),
        ):
            with self.subTest(path=path):
                candidate = copy.deepcopy(instances[inv_id])
                nested = candidate
                for part in path:
                    nested = nested[part]
                nested["unexpected"] = True
                self.assertTrue(list(self.validator(inv_id).iter_errors(candidate)))
        for field in ("domain_sidecar", "supporting_artifacts"):
            candidate = copy.deepcopy(instances[inv_id])
            del candidate["input"]["artifact_refs"][0][field]
            self.assertTrue(list(self.validator(inv_id).iter_errors(candidate)))
        for field, value in (
            ("artifact_id", "../escape"), ("size_bytes", -1),
            ("content_base64", "not base64"), ("content_sha256", "wrong"),
        ):
            candidate = copy.deepcopy(instances[inv_id])
            candidate["input"]["artifact_refs"][0]["supporting_artifacts"][0][field] = value
            self.assertTrue(list(self.validator(inv_id).iter_errors(candidate)))
        for schema_id, field in (
            ("urn:gigai:schema:external-recording-checkpoint:2", "artifacts"),
            ("urn:gigai:schema:external-recording-receipt:2", "outputs"),
        ):
            for path in ((field, 0), (field, 0, "domain_sidecar"),
                         (field, 0, "supporting_artifacts", 0),
                         (field, 0, "supporting_artifacts", 0, "ref")):
                candidate = copy.deepcopy(instances[schema_id])
                nested = candidate
                for part in path:
                    nested = nested[part]
                nested["unexpected"] = True
                self.assertTrue(list(self.validator(schema_id).iter_errors(candidate)))
        legacy = copy.deepcopy(instances[inv_id])
        legacy["schema_version"] = "1.0"
        self.assertTrue(list(self.validator(
            "urn:gigai:schema:external-recording-invocation:1"
        ).iter_errors(legacy)))

    def test_role_only_plan_v2_requires_one_exact_typed_request(self) -> None:
        schema_id = "urn:gigai:schema:external-recording-plan:2"
        original = _scout_lane_instances()[schema_id]
        validator = self.validator(schema_id)
        for inputs in ([], original["inputs"] * 2,
                       _scout_lane_instances()["urn:gigai:schema:external-recording-plan:1"]["inputs"]):
            candidate = copy.deepcopy(original)
            candidate["inputs"] = inputs
            self.assertTrue(list(validator.iter_errors(candidate)))
        candidate = copy.deepcopy(original)
        candidate["inputs"][0]["confirmed"] = True
        self.assertTrue(list(validator.iter_errors(candidate)))
        legacy = copy.deepcopy(original)
        legacy["schema_version"] = "1.0"
        legacy["invocation"]["schema_version"] = "1.0"
        self.assertTrue(list(self.validator(
            "urn:gigai:schema:external-recording-plan:1"
        ).iter_errors(legacy)))

    def test_v2_checkpoint_carries_research_and_completion_check_together(self) -> None:
        candidate = copy.deepcopy(_scout_lane_instances()[
            "urn:gigai:schema:external-recording-checkpoint:2"
        ])
        check = {
            "kind": "research-role-completion", "markdown": "Checks passed\n",
            "sidecar": {
                "evidence_kind": "research-role-completion",
                "run_id": candidate["run_id"], "output_sha256": ZERO_DIGEST,
                "result": "pass",
            },
        }
        candidate["invocation"]["input"]["artifact_refs"].append(check)
        candidate["artifacts"].append({
            "kind": check["kind"],
            "markdown": artifact("runs/check.md", media_type="text/markdown"),
            "sidecar": artifact("runs/check.json"),
        })
        self.validator("urn:gigai:schema:external-recording-checkpoint:2").validate(candidate)
        candidate["invocation"]["input"]["artifact_refs"][-1]["sidecar"]["unexpected"] = True
        self.assertTrue(list(self.validator(
            "urn:gigai:schema:external-recording-checkpoint:2"
        ).iter_errors(candidate)))

    def test_capability_review_decision_preserves_strict_nested_boundaries(self) -> None:
        validator = self.validator("urn:gigai:schema:capability-review-decision:1")
        for field in ("reviewer", "operator_consent", "source_binding"):
            with self.subTest(field=field):
                candidate = capability_review_decision()
                candidate[field]["unexpected"] = True
                self.assertTrue(list(validator.iter_errors(candidate)))
        candidate = capability_review_decision()
        candidate["operator_consent"]["confirmed"] = False
        self.assertTrue(list(validator.iter_errors(candidate)))
        candidate = capability_review_decision()
        candidate["operator_consent"]["actor"]["kind"] = "agent"
        self.assertTrue(list(validator.iter_errors(candidate)))
        candidate = capability_review_decision()
        candidate["source_binding"]["effects"] = ["write_target"]
        self.assertTrue(list(validator.iter_errors(candidate)))
        candidate = capability_review_decision()
        candidate["source_binding"]["permissions"]["network"] = "unrestricted"
        self.assertTrue(list(validator.iter_errors(candidate)))

    def test_capability_successor_binding_preserves_strict_nested_boundaries(self) -> None:
        validator = self.validator("urn:gigai:schema:capability-successor-binding:1")
        for field in ("decision_ref", "pending_proposal_ref", "source_binding"):
            with self.subTest(field=field):
                candidate = capability_successor_binding()
                candidate[field]["unexpected"] = True
                self.assertTrue(list(validator.iter_errors(candidate)))
        candidate = capability_successor_binding()
        candidate["source_binding"]["inventory"][0]["unexpected"] = True
        self.assertTrue(list(validator.iter_errors(candidate)))
        candidate = capability_successor_binding()
        candidate["source_binding"]["effects"] = ["write_target"]
        self.assertTrue(list(validator.iter_errors(candidate)))
        candidate = capability_successor_binding()
        del candidate["source_binding"]["permissions"]["network"]
        self.assertTrue(list(validator.iter_errors(candidate)))

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

    def test_scout_tool_receipt_accepts_first_version_and_retains_strict_binding(self) -> None:
        schema_id = "urn:gigai:schema:scout-operation-receipt:1"
        receipt = copy.deepcopy(valid_instances()[schema_id])
        capability_id = "cap_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        entry = f"tools/{capability_id}/record_tool.py"
        receipt["operation"] = "record_create"
        receipt["tool_binding"] = {
            "manifest_ref": artifact("manifests/capability-manifest.json"),
            "capability_id": capability_id,
            "gig_version": 1,
            "entry_path": entry,
            "inventory": [{
                "path": entry, "content_sha256": ZERO_DIGEST,
                "media_type": "text/x-python", "size_bytes": 123,
            }],
            "inventory_sha256": ZERO_DIGEST,
            "operation": "record_create",
            "effects": ["write_workpad"],
            "actor": {"kind": "agent", "id": "fixture-agent"},
        }
        validator = self.validator(schema_id)
        for version in (1, 2, 9007199254740991):
            with self.subTest(valid_version=version):
                candidate = copy.deepcopy(receipt)
                candidate["tool_binding"]["gig_version"] = version
                validator.validate(candidate)
        for version in (0, -1, True, "1", 1.5, None, 9007199254740992):
            with self.subTest(invalid_version=version):
                candidate = copy.deepcopy(receipt)
                candidate["tool_binding"]["gig_version"] = version
                self.assertTrue(list(validator.iter_errors(candidate)))
        for field in receipt["tool_binding"]:
            with self.subTest(missing_binding_field=field):
                candidate = copy.deepcopy(receipt)
                del candidate["tool_binding"][field]
                self.assertTrue(list(validator.iter_errors(candidate)))
        candidate = copy.deepcopy(receipt)
        candidate["tool_binding"]["undeclared_authority"] = True
        self.assertTrue(list(validator.iter_errors(candidate)))

    def test_full_manifest_golden_includes_strict_tool_and_wrapper_binding(self) -> None:
        schema_id = "urn:gigai:schema:capability-manifest:1"
        validator = self.validator(schema_id)
        validator.validate(capability_manifest())  # Historical non-tool manifest.
        golden = valid_instances()[schema_id]
        validator.validate(golden)
        binding = golden["capabilities"][0]["tool_binding"]
        for field in ("entry_path", "inventory", "inventory_sha256", "operations", "effects", "wrapper_ref"):
            with self.subTest(missing_binding=field):
                malformed = copy.deepcopy(golden)
                del malformed["capabilities"][0]["tool_binding"][field]
                self.assertTrue(list(validator.iter_errors(malformed)))
        malformed = copy.deepcopy(golden)
        malformed["capabilities"][0]["tool_binding"]["extra"] = True
        self.assertTrue(list(validator.iter_errors(malformed)))
        self.assertEqual(binding["wrapper_ref"], binding["inventory"][0])

    def test_tool_bindings_require_explicit_literal_root_wrapper_reference(self) -> None:
        capability_id = "cap_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        entry = f"tools/{capability_id}/record_tool.py"
        wrapper = {
            "path": "gig.py", "content_sha256": ZERO_DIGEST,
            "media_type": "text/x-python", "size_bytes": 123,
        }
        common = {
            "entry_path": entry,
            "inventory": [{**wrapper, "path": entry}],
            "inventory_sha256": ZERO_DIGEST,
            "effects": ["write_workpad"],
        }
        bindings = {
            "urn:gigai:schema:capability-manifest:1": {
                **common, "operations": ["record_create"],
            },
            "urn:gigai:schema:scout-operation-receipt:1": {
                **common,
                "manifest_ref": artifact("manifests/capability-manifest.json"),
                "capability_id": capability_id, "gig_version": 1,
                "operation": "record_create",
                "actor": {"kind": "agent", "id": "fixture-agent"},
            },
        }
        for schema_id, legacy in bindings.items():
            validator = self.validator(schema_id).evolve(
                schema={"$ref": f"{schema_id}#/$defs/tool_binding"}
            )
            with self.subTest(schema_id=schema_id):
                validator.validate(legacy)
                candidate = copy.deepcopy(legacy)
                candidate["inventory"].insert(0, copy.deepcopy(wrapper))
                self.assertTrue(list(validator.iter_errors(candidate)))
                candidate["wrapper_ref"] = copy.deepcopy(wrapper)
                validator.validate(candidate)
                for path in ("./gig.py", "../gig.py", "/gig.py", "other.py", "ui/gig.py"):
                    with self.subTest(invalid_root=path):
                        malformed = copy.deepcopy(candidate)
                        malformed["inventory"][0]["path"] = path
                        malformed["wrapper_ref"]["path"] = path
                        self.assertTrue(list(validator.iter_errors(malformed)))
                malformed = copy.deepcopy(candidate)
                malformed["entry_path"] = "gig.py"
                self.assertTrue(list(validator.iter_errors(malformed)))
                for field in wrapper:
                    with self.subTest(missing_wrapper_field=field):
                        malformed = copy.deepcopy(candidate)
                        del malformed["wrapper_ref"][field]
                        self.assertTrue(list(validator.iter_errors(malformed)))
                malformed = copy.deepcopy(candidate)
                malformed["wrapper_ref"]["extra"] = True
                self.assertTrue(list(validator.iter_errors(malformed)))

    def test_template_binding_is_strict_and_grants_no_approval(self) -> None:
        schema_id = "urn:gigai:schema:template-instance-binding:2"
        base = valid_instances()[schema_id]
        validator = self.validator(schema_id)
        for username in ("owner", "kar46", "李", "A\u200dB\ue000"):
            with self.subTest(valid_username=username):
                candidate = copy.deepcopy(base)
                candidate["username"] = username
                validator.validate(candidate)
        for username in ("", "x" * 65, "A\0B", "A\x1fB", "A\x7fB", "A\x85B", "A\x9fB", "owner\n"):
            with self.subTest(invalid_username=repr(username)):
                candidate = copy.deepcopy(base)
                candidate["username"] = username
                self.assertTrue(list(validator.iter_errors(candidate)))
        for field, value in (
            ("schema_version", "1.0"),
            ("owner_id", "owner_invalid"),
            ("proposal_id", "gig_proposal_invalid"),
            ("approval", {"state": "approved", "approved_version": 1}),
            ("approval", {"state": "unapproved", "approved_version": None, "grant": True}),
            ("customization_parent", "invented"),
            ("extra", True),
        ):
            with self.subTest(field=field, value=value):
                candidate = copy.deepcopy(base)
                candidate[field] = value
                self.assertTrue(list(validator.iter_errors(candidate)))
        for field in ("proposal_ref", "software_inventory"):
            with self.subTest(missing=field):
                candidate = copy.deepcopy(base)
                del candidate[field]
                self.assertTrue(list(validator.iter_errors(candidate)))
            with self.subTest(nested_unknown=field):
                candidate = copy.deepcopy(base)
                candidate[field]["extra"] = True
                self.assertTrue(list(validator.iter_errors(candidate)))

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
