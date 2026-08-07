"""The bounded, deterministic G13/G14 Run lifecycle.

Authority is resolved from committed journal bytes before a Run ID or
directory is allocated. G14 schedules the sealed Graph one automatic
``local_capability`` Goal at a time; workers write only Run-scoped proof
artifacts and never invoke a provider, shell, or target process.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import multiprocessing
import os
from pathlib import Path
import subprocess
import uuid
from typing import Callable

from .canonical import (
    EntityPrefix,
    canonical_json_bytes,
    digest_imported_bytes,
    digest_owned_text,
    generate_entity_id,
    parse_json_bytes,
    parse_json_front_matter,
    validate_entity_id,
)
from .index import read_index
from .journal import JournalArtifact, JournalEntry, record_transition
from .validators import validate_goal_graph, validate_serialized_contract
from .workpad import ResolvedWorkpad, resolve_workpad


class RunError(RuntimeError):
    """A Run cannot truthfully be prepared or observed."""


class _RunInterrupted(RunError):
    """Execution stopped because the sealed target observation diverged."""


class _PreScheduleFailure(RunError):
    """A sealed Graph cannot be scheduled before any Goal starts."""


RunObserver = Callable[[str], None]


@dataclass(frozen=True)
class RunResult:
    run_id: str
    gig_id: str
    gig_version: int
    workpad: Path
    run_path: Path
    status: str
    run_started: JournalEntry
    terminal: JournalEntry | None


_ZERO_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cost": None,
    "currency": None,
    "cost_status": "not_applicable",
}


def launch_run(
    *,
    home_root: Path,
    requested_target: Path | None,
    gig_id: str | None = None,
    version: int | None = None,
    wait: bool = False,
    invocation_argv: tuple[str, ...] = ("gigai", "run"),
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    observer: RunObserver | None = None,
) -> RunResult:
    """Prepare, commit, and execute one deterministic Run."""

    observer = observer or (lambda _step: None)
    resolved = resolve_workpad(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    projection = read_index(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
    )
    authority = _resolve_authority(resolved, projection, version)
    graph = authority["graph"]
    proposal = authority["proposal"]
    _validate_authority(resolved, graph, proposal)
    target_before = _target_observation(resolved)
    run_id = _allocate_run_id(resolved.path, uuid_factory)
    run_path = resolved.path / "runs" / run_id
    run_path.mkdir(parents=True, mode=0o700)
    try:
        prepared = _prepare_records(
            resolved=resolved,
            run_id=run_id,
            gig_version=authority["version"],
            authority_commit=authority["commit"],
            graph=graph,
            proposal=proposal,
            target_before=target_before,
            invocation_argv=invocation_argv,
        )
        observer("after_brief_write")
        observer("after_manifest_seal")
        observer("after_initial_run_details")
        manifest_digest = digest_imported_bytes(
            prepared[f"runs/{run_id}/run-manifest.json"]
        )
        started = record_transition(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            handoff_id=_new_id(EntityPrefix.HANDOFF, uuid_factory),
            transition="run_started",
            body=f"Run {run_id} sealed and ready for deterministic execution.",
            artifacts=tuple(
                JournalArtifact(path, data) for path, data in prepared.items()
            ),
            front_matter={
                "gig_version": authority["version"],
                "run_id": run_id,
                "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
                "source_manifest_sha256": manifest_digest,
                "outcome": "SEALED",
                "actor": {"kind": "operator", "id": "local-user"},
            },
            observer=observer,
        )
        observer("after_run_started_commit")
        process = multiprocessing.get_context("spawn").Process(
            target=_worker_entry,
            args=(
                resolved,
                run_id,
                authority["version"],
                graph,
                target_before,
                started.handoff_id,
                manifest_digest,
            ),
            daemon=False,
        )
        process.start()
        if not wait:
            return RunResult(
                run_id,
                resolved.gig_id,
                authority["version"],
                resolved.path,
                run_path,
                "running",
                started,
                None,
            )
        process.join()
        if process.exitcode != 0:
            terminal = _mark_interrupted(resolved, run_id, authority["version"], graph)
            return RunResult(
                run_id,
                resolved.gig_id,
                authority["version"],
                resolved.path,
                run_path,
                "interrupted",
                started,
                terminal,
            )
        details = parse_json_bytes(
            (run_path / "run-details.json").read_bytes()
        )
        if isinstance(details, dict) and details.get("status") in {
            "failed",
            "blocked",
            "cancelled",
            "interrupted",
        }:
            return RunResult(
                run_id,
                resolved.gig_id,
                authority["version"],
                resolved.path,
                run_path,
                str(details["status"]),
                started,
                _latest_terminal_entry(resolved, run_id),
            )
        return RunResult(
            run_id,
            resolved.gig_id,
            authority["version"],
            resolved.path,
            run_path,
            "succeeded",
            started,
            None,
        )
    except Exception:
        # A failed preparation must not leave an apparently addressable Run.
        started_files = tuple((resolved.path / "handoffs").glob("*-run-started.txt"))
        if run_path.exists() and not started_files:
            _remove_tree(run_path)
        raise


def read_run_details(
    *,
    home_root: Path,
    requested_target: Path | None,
    run_id: str,
    gig_id: str | None = None,
) -> dict[str, object]:
    """Read only the durable terminal or preparation state for one Run."""

    resolved = resolve_workpad(
        home_root=home_root,
        requested_target=requested_target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )
    try:
        validate_entity_id(run_id, expected_prefix=EntityPrefix.RUN)
    except ValueError as exc:
        raise RunError("run_id must be canonical") from exc
    path = resolved.path / "runs" / run_id / "run-details.json"
    if path.is_symlink() or not path.is_file():
        raise RunError("Run details are unavailable")
    payload = parse_json_bytes(path.read_bytes())
    if not isinstance(payload, dict):
        raise RunError("Run details are not an object")
    if payload.get("status") in {"preparing", "running"}:
        target_ref = payload.get("target_before")
        if isinstance(target_ref, dict):
            try:
                if _target_observation(resolved) != _read_artifact_json(resolved.path, target_ref):
                    graph_payload = parse_json_bytes(
                        (resolved.path / "runs" / run_id / "goal-graph.json").read_bytes()
                    )
                    if isinstance(graph_payload, dict):
                        _mark_interrupted(
                            resolved,
                            run_id,
                            int(payload["gig_version"]),
                            graph_payload,
                        )
                        payload = parse_json_bytes(path.read_bytes())
            except (OSError, ValueError, RunError):
                pass
    report = validate_serialized_contract("run-details.schema.json", path.read_bytes())
    if not report.valid:
        raise RunError("Run details failed schema validation")
    return payload


def _resolve_authority(
    resolved: ResolvedWorkpad, projection: object, requested_version: int | None
) -> dict[str, object]:
    active = getattr(projection, "active_version", None)
    if not isinstance(active, dict):
        raise RunError("no active approved Gig version")
    active_version = active.get("active_version")
    if type(active_version) is not int:
        raise RunError("active Gig version is invalid")
    active_report = validate_serialized_contract(
        "active-gig-version.schema.json", canonical_json_bytes(active)
    )
    if not active_report.valid or active.get("gig_id") != resolved.gig_id:
        raise RunError("active-version authority is invalid")
    if requested_version is None:
        version = active_version
        commit = active.get("journal_commit")
    else:
        if type(requested_version) is not int or requested_version < 1:
            raise RunError("--version must be a positive integer")
        version = requested_version
        tag = f"gig-v{version:06d}"
        tag_result = _git(resolved.path, "rev-parse", "--verify", tag, check=False)
        if tag_result.returncode != 0:
            raise RunError("requested Gig version is not approved")
        commit = tag_result.stdout.strip()
    if not isinstance(commit, str) or not commit:
        raise RunError("approved Gig version has no journal commit")
    tag_name = (
        active.get("journal_tag")
        if requested_version is None
        else f"gig-v{version:06d}"
    )
    tag_result = _git(
        resolved.path, "rev-parse", "--verify", str(tag_name), check=False
    )
    if tag_result.returncode != 0 or tag_result.stdout.strip() != commit:
        raise RunError("approved Gig authority is divergent from its immutable tag")
    proposal_bytes = _git_bytes(
        resolved.path, "show", f"{commit}:manifests/gig-proposal.json"
    )
    graph_bytes = _git_bytes(
        resolved.path, "show", f"{commit}:manifests/goal-graph.json"
    )
    proposal = parse_json_bytes(proposal_bytes)
    graph = parse_json_bytes(graph_bytes)
    if not isinstance(proposal, dict) or not isinstance(graph, dict):
        raise RunError("approved Gig authority is malformed")
    return {"version": version, "commit": commit, "proposal": proposal, "graph": graph}


def _validate_authority(
    resolved: ResolvedWorkpad, graph: object, proposal: object
) -> None:
    if not isinstance(graph, dict) or not isinstance(proposal, dict):
        raise RunError("approved authority is malformed")
    graph_report = validate_serialized_contract(
        "goal-graph.schema.json", canonical_json_bytes(graph)
    )
    proposal_report = validate_serialized_contract(
        "gig-proposal.schema.json", canonical_json_bytes(proposal)
    )
    semantic = validate_goal_graph(graph)
    if not graph_report.valid or not proposal_report.valid or not semantic.valid:
        raise RunError("approved Goal Graph failed revalidation")
    if (
        proposal.get("status") != "approved"
        or proposal.get("gig_id") != resolved.gig_id
    ):
        raise RunError("approved proposal authority is inconsistent")
    if proposal.get("goal_graph", {}).get("content_sha256") != digest_imported_bytes(
        canonical_json_bytes(graph)
    ):
        raise RunError("approved proposal does not pin the Goal Graph")
    goals = graph.get("goals", [])
    entries = set(graph.get("entry_goal_ids", []))
    if not isinstance(goals, list) or not entries:
        raise RunError("approved Goal Graph has no ready entry Goal")
    if len(goals) != len({goal.get("goal_id") for goal in goals if isinstance(goal, dict)}):
        raise RunError("approved Goal Graph has duplicate Goals")


def _prepare_records(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    authority_commit: str,
    graph: dict[str, object],
    proposal: dict[str, object],
    target_before: dict[str, object],
    invocation_argv: tuple[str, ...],
) -> dict[str, bytes]:
    run_dir = f"runs/{run_id}"
    goals = [item for item in graph["goals"] if isinstance(item, dict)]
    graph_bytes = canonical_json_bytes(graph)
    capability = canonical_json_bytes(
        {"capability": "gigai.offline", "version": "1", "executor": "local_capability"}
    )
    target_bytes = canonical_json_bytes(target_before)
    now = _now()
    budget = graph["aggregate_budget"]

    def artifact(path: str, media: str, data: bytes) -> dict[str, object]:
        return {
            "path": path,
            "content_sha256": digest_imported_bytes(data),
            "media_type": media,
            "size_bytes": len(data),
        }

    source_ref = artifact(
        f"{run_dir}/sealed/offline-capability.json", "application/json", capability
    )
    target_ref = artifact(
        f"{run_dir}/target-before.json", "application/json", target_bytes
    )
    graph_ref = artifact(f"{run_dir}/goal-graph.json", "application/json", graph_bytes)
    brief_body = f"# Run {run_id}\n\nDeterministic workpad-only execution.\n"
    brief_meta = {
        "schema_version": "1.0",
        "run_id": run_id,
        "gig_id": resolved.gig_id,
        "gig_version": gig_version,
        "created_at": now,
        "invoked_by": {"kind": "operator", "id": "local-user"},
        "invocation_argv": list(invocation_argv),
        "goal_graph": graph_ref,
        "target": {
            "kind": "git" if resolved.target_kind == "git" else "directory",
            "root": "bound-target",
            "git_head": target_before.get("git_head"),
            "status_sha256": target_before.get("status_sha256"),
            "observation_sha256": target_before["observation_sha256"],
        },
        "profile": "default",
        "resolved_models": [],
        "resolved_tools": [],
        "effects": ["write_workpad"],
        "aggregate_budget": budget,
        "input_canonical_sha256": digest_imported_bytes(graph_bytes),
        "body_sha256": digest_owned_text(brief_body),
        "run_manifest_path": f"{run_dir}/run-manifest.json",
    }
    brief = _front_matter(brief_meta, brief_body)
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "gig_id": resolved.gig_id,
        "gig_version": gig_version,
        "authority": "run_invocation",
        "status": "sealed",
        "sealed_at": now,
        "invoked_by": {"kind": "operator", "id": "local-user"},
        "invocation_argv": list(invocation_argv),
        "run_brief": artifact(f"{run_dir}/run-brief.md", "text/markdown", brief),
        "goal_graph": graph_ref,
        "goal_contracts": [
            {
                "goal_id": goal["goal_id"],
                "goal_version": goal["goal_version"],
                "contract": artifact(
                    f"{run_dir}/{goal['contract']['path']}",
                    "text/markdown",
                    _git_bytes(
                        resolved.path,
                        "show",
                        f"{authority_commit}:{goal['contract']['path']}",
                    ),
                ),
            }
            for goal in goals
        ],
        "target_observation": target_ref,
        "profile": "default",
        "resolved_models": [],
        "resolved_tools": [],
        "sealed_sources": [source_ref],
        "effects": ["write_workpad"],
        "aggregate_budget": budget,
        "input_canonical_sha256": digest_imported_bytes(graph_bytes),
    }
    manifest_bytes = canonical_json_bytes(manifest)
    brief_metadata, _brief_body = parse_json_front_matter(brief)
    brief_report = validate_serialized_contract(
        "run-brief-frontmatter.schema.json", canonical_json_bytes(brief_metadata)
    )
    if not brief_report.valid:
        raise RunError(
            "Run Brief failed schema validation: "
            + ",".join(item.code + ":" + item.message for item in brief_report.findings)
        )
    manifest_report = validate_serialized_contract(
        "run-manifest.schema.json", manifest_bytes
    )
    if not manifest_report.valid:
        raise RunError(
            "Run manifest failed schema validation: "
            + ",".join(
                item.code + ":" + item.message for item in manifest_report.findings
            )
        )
    marked_goals = [
        {**goal, "_entry": goal["goal_id"] in set(graph["entry_goal_ids"])}
        for goal in goals
    ]
    initial = _details(
        run_id,
        resolved.gig_id,
        gig_version,
        digest_imported_bytes(graph_bytes),
        marked_goals,
        budget,
        target_ref,
        now,
        status="preparing",
    )
    if not validate_serialized_contract(
        "run-details.schema.json", canonical_json_bytes(initial)
    ).valid:
        raise RunError("initial RunDetails failed schema validation")
    initial["critical_path"] = _critical_path(graph)
    return {
        f"{run_dir}/run-brief.md": brief,
        f"{run_dir}/run-manifest.json": manifest_bytes,
        f"{run_dir}/run-details.json": canonical_json_bytes(initial),
        f"{run_dir}/goal-graph.json": graph_bytes,
        f"{run_dir}/target-before.json": target_bytes,
        f"{run_dir}/sealed/offline-capability.json": capability,
        **{
            f"{run_dir}/{goal['contract']['path']}": _git_bytes(
                resolved.path, "show", f"{authority_commit}:{goal['contract']['path']}"
            )
            for goal in goals
        },
    }


def _execute_deterministic(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    target_before: dict[str, object],
    run_started_handoff_id: str,
    manifest_digest: str,
) -> JournalEntry:
    """Run the sealed graph with one deterministic Goal at a time."""
    run_dir = resolved.path / "runs" / run_id
    details_path = run_dir / "run-details.json"
    details = parse_json_bytes(details_path.read_bytes())
    if not isinstance(details, dict):
        raise _PreScheduleFailure("Run details are unavailable")
    graph_digest = digest_imported_bytes(canonical_json_bytes(graph))
    sealed_graph_bytes = (run_dir / "goal-graph.json").read_bytes()
    if digest_imported_bytes(sealed_graph_bytes) != graph_digest:
        raise _PreScheduleFailure("sealed Run Graph digest diverged before scheduling")
    manifest_bytes = (run_dir / "run-manifest.json").read_bytes()
    if digest_imported_bytes(manifest_bytes) != manifest_digest:
        raise _PreScheduleFailure("sealed Run manifest digest diverged before scheduling")
    manifest = parse_json_bytes(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("goal_graph", {}).get("content_sha256") != graph_digest:
        raise _PreScheduleFailure("Run manifest does not pin the sealed Goal Graph")
    _validate_scheduler_policy(graph)
    goals = {goal["goal_id"]: goal for goal in graph["goals"]}
    goal_details = {goal["goal_id"]: goal for goal in details["goals"]}
    previous_handoff = run_started_handoff_id
    while True:
        ready = _ready_goals(graph, goal_details)
        if not ready:
            incomplete = [
                item for item in goal_details.values()
                if item["status"] not in {"complete", "failed", "blocked", "cancelled"}
            ]
            if incomplete:
                blocked = _blocked_by_terminal_outcome(graph, goal_details)
                if blocked:
                    for goal_id in blocked:
                        previous_handoff = _record_goal_terminal(
                            resolved, run_id, gig_version, graph, details,
                            goals[goal_id], goal_details[goal_id], "blocked",
                            previous_handoff, "blocked_by_predecessor",
                        )
                    continue
                raise _PreScheduleFailure("sealed Goal Graph has no schedulable Goal")
            terminal_status = _terminal_status(goal_details)
            return _finish_run(
                resolved,
                run_id,
                gig_version,
                graph,
                details,
                terminal_status,
                previous_handoff,
                previous_handoff,
            )
        goal_id = ready[0]
        goal = goals[goal_id]
        detail = goal_details[goal_id]
        now = _now()
        detail.update({"status": "running", "started_at": now})
        _refresh_details(details, goal_details, graph, "running")
        started_bytes = canonical_json_bytes(details)
        started = record_transition(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=resolved.gig_id,
            handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
            transition="goal_started",
            body=f"Goal {goal_id} started sequential deterministic execution.",
            artifacts=(JournalArtifact(f"runs/{run_id}/run-details.json", started_bytes),),
            front_matter=_goal_front_matter(
                gig_version, run_id, goal, graph_digest, manifest_digest,
                previous_handoff, "STARTED"
            ),
        )
        previous_handoff = started.handoff_id
        try:
            evidence = _execute_goal(resolved, run_id, goal_id, target_before)
            detail.update({
                "status": "complete",
                "outcome": "COMPLETE",
                "finished_at": _now(),
                "evidence": [evidence],
            })
            _refresh_details(details, goal_details, graph, "running")
            completed_bytes = canonical_json_bytes(details)
            completed = record_transition(
                workpad=resolved.path,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
                transition="goal_completed",
                body=f"Goal {goal_id} completed with outcome COMPLETE.",
                artifacts=(
                    JournalArtifact(f"runs/{run_id}/run-details.json", completed_bytes),
                    JournalArtifact(
                        str(evidence["path"]),
                        (resolved.path / str(evidence["path"])).read_bytes(),
                    ),
                ),
                front_matter=_goal_front_matter(
                    gig_version, run_id, goal, graph_digest, manifest_digest,
                    previous_handoff, "COMPLETE", [evidence]
                ),
            )
            previous_handoff = completed.handoff_id
        except _RunInterrupted:
            raise
        except Exception as exc:
            detail.update({
                "status": "failed",
                "errors": [{"code": "goal_execution_failed", "message": str(exc), "retryable": False, "invocation_id": None}],
                "finished_at": _now(),
            })
            _refresh_details(details, goal_details, graph, "failed")
            failed_bytes = canonical_json_bytes(details)
            failed = record_transition(
                workpad=resolved.path,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
                transition="goal_failed",
                body=f"Goal {goal_id} failed during deterministic execution.",
                artifacts=(JournalArtifact(f"runs/{run_id}/run-details.json", failed_bytes),),
                front_matter=_goal_front_matter(
                    gig_version, run_id, goal, graph_digest, manifest_digest,
                    previous_handoff, "FAILED"
                ),
            )
            terminal = _finish_run(
                resolved,
                run_id,
                gig_version,
                graph,
                details,
                "failed",
                failed.handoff_id,
                previous_handoff,
            )
            return terminal


def _validate_scheduler_policy(graph: dict[str, object]) -> None:
    budget = graph.get("aggregate_budget", {})
    if budget.get("max_parallel_goals") != 1:
        raise _PreScheduleFailure("parallel Goal capacity is unsupported by G14")
    if graph.get("failure_policy") != "fail_gig":
        raise _PreScheduleFailure("failure policy is unsupported by G14")
    if any(edge.get("kind") == "recovery" for edge in graph.get("edges", [])):
        raise _PreScheduleFailure("recovery edges are unsupported by G14")
    if any(
        edge.get("kind") == "dependency" and not edge.get("automatic")
        for edge in graph.get("edges", [])
    ):
        raise _PreScheduleFailure("manual dependency edges are unsupported by G14")
    for goal in graph.get("goals", []):
        if goal.get("activation") != "automatic":
            raise _PreScheduleFailure("operator-gated Goals are unsupported by G14")
        executor = goal.get("executor", {})
        if executor.get("kind") != "local_capability" or executor.get("capability") not in {"gigai.offline", "gigai.deterministic"}:
            raise _PreScheduleFailure("Goal executor is unsupported by G14")
        if goal.get("effects") != ["write_workpad"]:
            raise _PreScheduleFailure("Goal declares an unsafe effect set")


def _ready_goals(graph: dict[str, object], details: dict[str, dict[str, object]]) -> list[str]:
    edges = [edge for edge in graph.get("edges", []) if edge.get("kind") == "dependency"]
    entries = set(graph.get("entry_goal_ids", []))
    ready = []
    for goal in graph.get("goals", []):
        goal_id = goal["goal_id"]
        if details[goal_id]["status"] != "pending" and details[goal_id]["status"] != "ready":
            continue
        incoming = [edge for edge in edges if edge.get("to_goal_id") == goal_id]
        if goal_id not in entries and not incoming:
            continue
        if all(details[edge["from_goal_id"]]["status"] == "complete" and details[edge["from_goal_id"]].get("outcome") in edge.get("on_outcomes", []) for edge in incoming):
            ready.append(goal_id)
    return sorted(ready)


def _terminal_status(details: dict[str, dict[str, object]]) -> str:
    if any(item["status"] == "failed" for item in details.values()):
        return "failed"
    if any(item["status"] == "blocked" for item in details.values()):
        return "blocked"
    return "succeeded"


def _critical_path(graph: dict[str, object]) -> list[str]:
    goals = {goal["goal_id"] for goal in graph.get("goals", [])}
    incoming = {
        goal_id: [] for goal_id in goals
    }
    for edge in graph.get("edges", []):
        if edge.get("kind") == "dependency":
            incoming.setdefault(edge["to_goal_id"], []).append(edge["from_goal_id"])

    def paths(goal_id: str, seen: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
        if goal_id in seen:
            return []
        parents = incoming.get(goal_id, [])
        if not parents:
            return [(goal_id,)]
        candidates = []
        for parent in parents:
            candidates.extend(paths(parent, seen + (goal_id,)))
        return [path + (goal_id,) for path in candidates]

    terminals = graph.get("terminal_goal_ids", []) or sorted(goals)
    candidates = [path for terminal in terminals for path in paths(terminal)]
    if not candidates:
        return []
    return list(min(candidates, key=lambda path: (-len(path), path)))


def _blocked_by_terminal_outcome(graph: dict[str, object], details: dict[str, dict[str, object]]) -> list[str]:
    blocked = []
    for edge in graph.get("edges", []):
        if edge.get("kind") != "dependency":
            continue
        source = details[edge["from_goal_id"]]
        target = details[edge["to_goal_id"]]
        if source["status"] in {"failed", "blocked", "cancelled", "complete"} and target["status"] in {"pending", "ready"}:
            if source["status"] != "complete" or source.get("outcome") not in edge.get("on_outcomes", []):
                blocked.append(edge["to_goal_id"])
    return sorted(set(blocked))


def _execute_goal(resolved: ResolvedWorkpad, run_id: str, goal_id: str, target_before: dict[str, object]) -> dict[str, object]:
    run_dir = resolved.path / "runs" / run_id
    evidence_path = run_dir / "evidence" / f"{goal_id}.txt"
    evidence_path.parent.mkdir(mode=0o700, exist_ok=True)
    evidence = canonicalize_evidence(f"gigai-offline-ok:{goal_id}\n")
    evidence_path.write_bytes(evidence)
    if _target_observation(resolved) != target_before:
        raise _RunInterrupted("target changed during deterministic execution")
    return _artifact_ref(evidence_path.relative_to(resolved.path).as_posix(), "text/plain", evidence)


def _goal_front_matter(gig_version: int, run_id: str, goal: dict[str, object], graph_digest: str, manifest_digest: str | None, parent: str, outcome: str, evidence: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "gig_version": gig_version, "run_id": run_id, "goal_id": goal["goal_id"],
        "goal_version": goal["goal_version"], "goal_graph_sha256": graph_digest,
        "source_manifest_sha256": manifest_digest, "parent_handoff_ids": [parent],
        "outcome": outcome, "evidence": evidence or [], "usage": dict(_ZERO_USAGE),
        "actor": {"kind": "gigai", "id": "deterministic", "model_target": None},
    }


def _refresh_details(details: dict[str, object], goal_details: dict[str, dict[str, object]], graph: dict[str, object], status: str) -> None:
    details["status"] = status
    details["critical_path"] = _critical_path(graph)
    details["goals"] = list(goal_details.values())
    details["goal_sets"] = {key: [] for key in ("pending", "ready", "active", "complete", "failed", "blocked", "gated", "cancelled")}
    for goal_id, goal in goal_details.items():
        state = goal["status"]
        aggregate = "active" if state in {"running", "verifying"} else state
        if aggregate in details["goal_sets"]:
            details["goal_sets"][aggregate].append(goal_id)


def _record_goal_terminal(resolved: ResolvedWorkpad, run_id: str, gig_version: int, graph: dict[str, object], details: dict[str, object], goal: dict[str, object], detail: dict[str, object], state: str, parent: str, outcome: str) -> str:
    detail.update({"status": state, "finished_at": _now(), "errors": [{"code": "blocked_by_predecessor", "message": "predecessor outcome is not accepted", "retryable": False, "invocation_id": None}]})
    goal_details = {item["goal_id"]: item for item in details["goals"]}
    _refresh_details(details, goal_details, graph, "blocked")
    entry = record_transition(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4), transition="goal_blocked", body=f"Goal {goal['goal_id']} was blocked by a predecessor outcome.", artifacts=(JournalArtifact(f"runs/{run_id}/run-details.json", canonical_json_bytes(details)),), front_matter=_goal_front_matter(gig_version, run_id, goal, digest_imported_bytes(canonical_json_bytes(graph)), None, parent, outcome))
    return entry.handoff_id


def _finish_run(resolved: ResolvedWorkpad, run_id: str, gig_version: int, graph: dict[str, object], details: dict[str, object], status: str, parent: str, previous: str) -> JournalEntry:
    target_after = _target_observation(resolved)
    target_before = details["target_before"]
    if target_after != _read_artifact_json(resolved.path, target_before):
        raise _RunInterrupted("target changed before Run terminalization")
    target_bytes = canonical_json_bytes(target_after)
    target_ref = _artifact_ref(f"runs/{run_id}/target-after.json", "application/json", target_bytes)
    terminal_path = f"runs/{run_id}/terminal-handoff.md"
    terminal_bytes = canonicalize_evidence(f"Run {run_id} terminal status: {status}.\n")
    terminal_ref = _artifact_ref(terminal_path, "text/markdown", terminal_bytes)
    details["status"] = status
    details["finished_at"] = _now()
    details["target_after"] = target_ref
    details["terminal_handoff"] = terminal_ref
    details["workpad_commit"] = _git(resolved.path, "rev-parse", "HEAD").stdout.strip()
    details["execution_summary"] = f"Sequential deterministic scheduler completed with status {status}."
    data = canonical_json_bytes(details)
    transition = "run_succeeded" if status == "succeeded" else "run_failed"
    entry = record_transition(workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4), transition=transition, body=f"Run {run_id} terminalized with status {status}.", artifacts=(JournalArtifact(f"runs/{run_id}/target-after.json", target_bytes), JournalArtifact(f"runs/{run_id}/run-details.json", data), JournalArtifact(terminal_path, terminal_bytes)), front_matter={"gig_version": gig_version, "run_id": run_id, "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)), "parent_handoff_ids": [previous], "outcome": "COMPLETE" if status == "succeeded" else "FAILED", "actor": {"kind": "gigai", "id": "scheduler", "model_target": None}})
    return entry


def _read_artifact_json(root: Path, ref: dict[str, object]) -> object:
    path = root / str(ref["path"])
    return parse_json_bytes(path.read_bytes())


def _worker_entry(
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    target_before: dict[str, object],
    run_started_handoff_id: str,
    manifest_digest: str,
) -> None:
    try:
        _execute_deterministic(
            resolved=resolved,
            run_id=run_id,
            gig_version=gig_version,
            graph=graph,
            target_before=target_before,
            run_started_handoff_id=run_started_handoff_id,
            manifest_digest=manifest_digest,
        )
    except _PreScheduleFailure as exc:
        try:
            _record_preschedule_failure(
                resolved, run_id, gig_version, graph, str(exc), run_started_handoff_id
            )
        except BaseException:
            pass
        return
    except _RunInterrupted:
        try:
            _mark_interrupted(resolved, run_id, gig_version, graph)
        except BaseException:
            pass
        return
    except BaseException:
        # Detached launches have no parent waiting on the worker. Make every
        # ordinary worker failure terminal in the child before propagating the
        # failure so wait=False cannot strand a Run at preparing.
        try:
            _mark_interrupted(resolved, run_id, gig_version, graph)
        except BaseException:
            # Preserve the original non-zero worker exit. The parent-side
            # waiter can still reconcile the durable state when it is present.
            pass
        raise


def _record_preschedule_failure(
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    message: str,
    parent: str,
) -> JournalEntry:
    path = resolved.path / "runs" / run_id / "run-details.json"
    details = parse_json_bytes(path.read_bytes())
    if not isinstance(details, dict):
        raise RunError("Run details are unavailable")
    details["status"] = "failed"
    details["finished_at"] = _now()
    details["execution_summary"] = f"Run rejected before Goal scheduling: {message}"
    data = canonical_json_bytes(details)
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition="run_failed",
        body=f"Run {run_id} was rejected before Goal scheduling: {message}",
        artifacts=(JournalArtifact(f"runs/{run_id}/run-details.json", data),),
        front_matter={
            "gig_version": gig_version,
            "run_id": run_id,
            "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
            "parent_handoff_ids": [parent],
            "outcome": "UNSUPPORTED_SCHEDULING_POLICY",
            "actor": {"kind": "gigai", "id": "scheduler", "model_target": None},
        },
    )


def _mark_interrupted(
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
) -> JournalEntry:
    details_path = resolved.path / "runs" / run_id / "run-details.json"
    details = parse_json_bytes(details_path.read_bytes())
    if not isinstance(details, dict):
        raise RunError("interrupted Run details are unavailable")
    if details.get("status") == "interrupted":
        existing = _existing_interruption_entry(resolved, run_id)
        if existing is not None:
            return existing
        raise RunError("interrupted Run handoff is unavailable")
    details["status"] = "interrupted"
    details["finished_at"] = _now()
    details["execution_summary"] = (
        "Worker exited before producing a terminal result; evidence was preserved and no retry was attempted."
    )
    details["goal_sets"] = {
        "pending": [],
        "ready": [],
        "active": [],
        "complete": [],
        "failed": [],
        "blocked": [],
        "gated": [],
        "cancelled": [],
    }
    for goal in details.get("goals", []):
        goal["status"] = "failed"
        goal["errors"] = [
            {
                "code": "worker_interrupted",
                "message": "deterministic worker exited before terminalization",
                "retryable": False,
                "invocation_id": None,
            }
        ]
    data = canonical_json_bytes(details)
    preserved = [
        JournalArtifact(path.relative_to(resolved.path).as_posix(), path.read_bytes())
        for path in (resolved.path / "runs" / run_id).rglob("*")
        if path.is_file() and path != details_path
    ]
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition="run_interrupted",
        body=f"Run {run_id} was interrupted; no automatic retry was performed.",
        artifacts=tuple(
            [JournalArtifact(f"runs/{run_id}/run-details.json", data), *preserved]
        ),
        front_matter={
            "gig_version": gig_version,
            "run_id": run_id,
            "goal_graph_sha256": digest_imported_bytes(canonical_json_bytes(graph)),
            "outcome": "INTERRUPTED",
        },
    )


def _existing_interruption_entry(
    resolved: ResolvedWorkpad, run_id: str
) -> JournalEntry | None:
    for path in sorted((resolved.path / "handoffs").glob("*-run-interrupted.txt")):
        try:
            metadata, _body = parse_json_front_matter(path.read_bytes())
            sequence = int(path.name[:12])
        except (OSError, ValueError, TypeError):
            continue
        if (
            metadata.get("transition") == "run_interrupted"
            and metadata.get("run_id") == run_id
            and isinstance(metadata.get("handoff_id"), str)
        ):
            commit = _git(resolved.path, "rev-parse", "HEAD").stdout.strip()
            return JournalEntry(sequence, metadata["handoff_id"], path, commit)
    return None


def _latest_terminal_entry(
    resolved: ResolvedWorkpad, run_id: str
) -> JournalEntry | None:
    for path in sorted((resolved.path / "handoffs").glob("*-*.txt"), reverse=True):
        try:
            metadata, _body = parse_json_front_matter(path.read_bytes())
            if metadata.get("run_id") != run_id:
                continue
            transition = metadata.get("transition")
            if transition not in {"run_succeeded", "run_failed", "run_interrupted"}:
                continue
            sequence = int(path.name.split("-", 1)[0])
            handoff_id = metadata.get("handoff_id")
            if not isinstance(handoff_id, str):
                continue
            return JournalEntry(
                sequence,
                handoff_id,
                path,
                _git(resolved.path, "rev-parse", "HEAD").stdout.strip(),
            )
        except (OSError, ValueError, TypeError):
            continue
    return None


def _details(
    run_id: str,
    gig_id: str,
    version: int,
    graph_digest: str,
    goals: list[dict[str, object]],
    budget: dict[str, object],
    target_ref: dict[str, object],
    timestamp: str,
    *,
    status: str,
    target_before: dict[str, object] | None = None,
    evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    entry_ids = set()
    # Entry Goals are ready at preparation; all other Goals remain pending.
    # The caller supplies the graph's entry set through the temporary marker.
    for goal in goals:
        if goal.get("_entry") is True:
            entry_ids.add(goal["goal_id"])
    clean_goals = []
    for goal in goals:
        if "_entry" in goal:
            goal = {key: value for key, value in goal.items() if key != "_entry"}
        clean_goals.append(goal)
    empty = {
        "pending": [goal["goal_id"] for goal in clean_goals if goal["goal_id"] not in entry_ids],
        "ready": sorted(entry_ids),
        "active": [],
        "complete": [],
        "failed": [],
        "blocked": [],
        "gated": [],
        "cancelled": [],
    }
    goal_details = [
        {
            "goal_id": goal["goal_id"],
            "goal_version": goal["goal_version"],
            "executor": goal["executor"]["capability"],
            "status": "ready" if goal["goal_id"] in entry_ids else "pending",
            "outcome": None,
            "errors": [],
            "evidence": [],
            "usage": dict(_ZERO_USAGE),
            "started_at": None,
            "finished_at": None,
        }
        for goal in clean_goals
    ]
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "gig_id": gig_id,
        "gig_version": version,
        "goal_graph_sha256": graph_digest,
        "status": status,
        "started_at": timestamp,
        "finished_at": timestamp if status == "succeeded" else None,
        "goal_sets": empty,
        "goals": goal_details,
        "critical_path": [goal["goal_id"] for goal in clean_goals],
        "realized_max_parallel_goals": 1,
        "execution_summary": "Deterministic local capability completed without provider, network, subprocess, or target effects."
        if status == "succeeded"
        else "Run preparation sealed; execution has not started.",
        "tool_errors": [],
        "model_errors": [],
        "aggregate_usage": dict(_ZERO_USAGE),
        "remaining_budget": dict(budget),
        "target_before": target_before or target_ref,
        "target_after": target_ref if status == "succeeded" else None,
        "completion_audit": {"status": "missing", "path": None},
        "terminal_handoff": None,
        "workpad_commit": None,
        "next_actions": [],
    }


def _target_observation(resolved: ResolvedWorkpad) -> dict[str, object]:
    if resolved.target_kind == "git":
        head = (
            _git(resolved.target_root, "rev-parse", "HEAD", check=False).stdout.strip()
            or None
        )
        status = _git(
            resolved.target_root, "status", "--porcelain", check=False
        ).stdout.encode()
        status_digest = digest_imported_bytes(status) if status else None
    else:
        head = None
        status_digest = None
    payload = {
        "schema_version": "1.0",
        "kind": resolved.target_kind,
        "root": "bound-target",
        "git_head": head,
        "status_sha256": status_digest,
    }
    payload["observation_sha256"] = digest_imported_bytes(canonical_json_bytes(payload))
    return payload


def _artifact_ref(path: str, media: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": media,
        "size_bytes": len(data),
    }


def _front_matter(metadata: dict[str, object], body: str) -> bytes:
    from .canonical import render_json_front_matter

    return render_json_front_matter(metadata, body)


def canonicalize_evidence(text: str) -> bytes:
    from .canonical import canonicalize_owned_text

    return canonicalize_owned_text(text)


def _allocate_run_id(workpad: Path, uuid_factory: Callable[[], uuid.UUID]) -> str:
    runs = workpad / "runs"
    return generate_entity_id(
        EntityPrefix.RUN,
        is_persisted=lambda value: (runs / value).exists(),
        uuid_factory=uuid_factory,
    )


def _new_id(prefix: EntityPrefix, uuid_factory: Callable[[], uuid.UUID]) -> str:
    return generate_entity_id(
        prefix, is_persisted=lambda _value: False, uuid_factory=uuid_factory
    )


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        text=True,
        check=check,
        shell=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )


def _git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        check=True,
        shell=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    return result.stdout


__all__ = ["RunError", "RunResult", "launch_run", "read_run_details"]
