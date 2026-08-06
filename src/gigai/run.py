"""The bounded, deterministic G13 Run lifecycle.

This module deliberately owns one execution shape: a single ready
``local_capability`` goal which writes a small proof artifact inside the Run
workpad.  Authority is resolved from committed journal bytes before a Run ID
or directory is allocated; the worker never invokes a provider, shell, or
target process.
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
    goals = graph.get("goals", [])
    entries = set(graph.get("entry_goal_ids", []))
    if not isinstance(goals, list) or not entries:
        raise RunError("approved Goal Graph has no ready entry Goal")
    ready = [
        goal
        for goal in goals
        if isinstance(goal, dict) and goal.get("goal_id") in entries
    ]
    if len(ready) != 1:
        raise RunError("deterministic fixture requires exactly one ready Goal")
    executor = ready[0].get("executor")
    if not isinstance(executor, dict) or executor.get("kind") != "local_capability":
        raise RunError("ready Goal is not a local capability")
    if executor.get("capability") not in {"gigai.offline", "gigai.deterministic"}:
        raise RunError("ready Goal has no supported deterministic capability")
    if ready[0].get("effects") != ["write_workpad"]:
        raise RunError("deterministic Goal declares an unsafe effect set")


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
    goal = next(
        item
        for item in graph["goals"]
        if item["goal_id"] in set(graph["entry_goal_ids"])
    )
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
    initial = _details(
        run_id,
        resolved.gig_id,
        gig_version,
        digest_imported_bytes(graph_bytes),
        goal,
        budget,
        target_ref,
        now,
        status="preparing",
    )
    if not validate_serialized_contract(
        "run-details.schema.json", canonical_json_bytes(initial)
    ).valid:
        raise RunError("initial RunDetails failed schema validation")
    return {
        f"{run_dir}/run-brief.md": brief,
        f"{run_dir}/run-manifest.json": manifest_bytes,
        f"{run_dir}/run-details.json": canonical_json_bytes(initial),
        f"{run_dir}/goal-graph.json": graph_bytes,
        f"{run_dir}/target-before.json": target_bytes,
        f"{run_dir}/sealed/offline-capability.json": capability,
        f"{run_dir}/{goal['contract']['path']}": _git_bytes(
            resolved.path, "show", f"{authority_commit}:{goal['contract']['path']}"
        ),
    }


def _execute_deterministic(
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    target_before: dict[str, object],
) -> JournalEntry:
    run_dir = resolved.path / "runs" / run_id
    evidence = canonicalize_evidence("gigai-offline-ok\n")
    (run_dir / "evidence").mkdir(mode=0o700, exist_ok=True)
    (run_dir / "evidence" / "deterministic.txt").write_bytes(evidence)
    target_after = _target_observation(resolved)
    if target_after != target_before:
        raise RunError("target changed during deterministic execution")
    goal = next(
        item
        for item in graph["goals"]
        if item["goal_id"] in set(graph["entry_goal_ids"])
    )
    graph_digest = digest_imported_bytes(canonical_json_bytes(graph))
    goal_id = goal["goal_id"]
    now = _now()
    target_bytes = canonical_json_bytes(target_after)
    target_ref = _artifact_ref(
        f"runs/{run_id}/target-after.json", "application/json", target_bytes
    )
    terminal_body = f"Run {run_id} completed its deterministic local capability.\n"
    terminal_path = f"runs/{run_id}/terminal-handoff.md"
    terminal_bytes = canonicalize_evidence(terminal_body)
    terminal_ref = _artifact_ref(terminal_path, "text/markdown", terminal_bytes)
    evidence_ref = _artifact_ref(
        f"runs/{run_id}/evidence/deterministic.txt", "text/plain", evidence
    )
    details = _details(
        run_id,
        resolved.gig_id,
        gig_version,
        graph_digest,
        goal,
        graph["aggregate_budget"],
        target_ref,
        now,
        status="succeeded",
        target_before=_artifact_ref(
            f"runs/{run_id}/target-before.json",
            "application/json",
            canonical_json_bytes(target_before),
        ),
        evidence=evidence_ref,
    )
    details["finished_at"] = now
    details["terminal_handoff"] = terminal_ref
    details["workpad_commit"] = _git(resolved.path, "rev-parse", "HEAD").stdout.strip()
    details_bytes = canonical_json_bytes(details)
    if not validate_serialized_contract("run-details.schema.json", details_bytes).valid:
        raise RunError("terminal RunDetails failed schema validation")
    terminal_artifacts = (
        JournalArtifact(f"runs/{run_id}/target-after.json", target_bytes),
        JournalArtifact(f"runs/{run_id}/run-details.json", details_bytes),
        JournalArtifact(f"runs/{run_id}/evidence/deterministic.txt", evidence),
        JournalArtifact(terminal_path, terminal_bytes),
    )
    return record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=_new_id(EntityPrefix.HANDOFF, uuid.uuid4),
        transition="run_succeeded",
        body=f"Run {run_id} completed deterministic capability {goal_id}.",
        artifacts=terminal_artifacts,
        front_matter={
            "gig_version": gig_version,
            "run_id": run_id,
            "goal_id": goal_id,
            "goal_version": goal["goal_version"],
            "goal_graph_sha256": graph_digest,
            "outcome": "COMPLETE",
            "evidence": [evidence_ref, terminal_ref],
            "usage": dict(_ZERO_USAGE),
            "actor": {"kind": "gigai", "id": "deterministic", "model_target": None},
        },
    )


def _worker_entry(
    resolved: ResolvedWorkpad,
    run_id: str,
    gig_version: int,
    graph: dict[str, object],
    target_before: dict[str, object],
) -> None:
    try:
        _execute_deterministic(
            resolved=resolved,
            run_id=run_id,
            gig_version=gig_version,
            graph=graph,
            target_before=target_before,
        )
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


def _details(
    run_id: str,
    gig_id: str,
    version: int,
    graph_digest: str,
    goal: dict[str, object],
    budget: dict[str, object],
    target_ref: dict[str, object],
    timestamp: str,
    *,
    status: str,
    target_before: dict[str, object] | None = None,
    evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    goal_id = goal["goal_id"]
    empty = {
        "pending": [],
        "ready": [],
        "active": [],
        "complete": [goal_id] if status == "succeeded" else [],
        "failed": [],
        "blocked": [],
        "gated": [],
        "cancelled": [],
    }
    goal_detail = {
        "goal_id": goal_id,
        "goal_version": goal["goal_version"],
        "executor": goal["executor"]["capability"],
        "status": "complete" if status == "succeeded" else "ready",
        "outcome": "COMPLETE" if status == "succeeded" else None,
        "errors": [],
        "evidence": [evidence] if evidence else [],
        "usage": dict(_ZERO_USAGE),
        "started_at": timestamp if status == "succeeded" else None,
        "finished_at": timestamp if status == "succeeded" else None,
    }
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
        "goals": [goal_detail],
        "critical_path": [goal_id],
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
