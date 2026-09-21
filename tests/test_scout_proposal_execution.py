from __future__ import annotations

import json
from pathlib import Path
import subprocess
import uuid
from dataclasses import replace

import httpx
import pytest

from gigai.adapters.factory import resolve_model_adapter
from gigai.config import Endpoint, ModelTarget, Profile
from gigai.native_records import create_native_record
from gigai.canonical import canonical_json_bytes, parse_json_bytes, parse_json_front_matter
from gigai.journal import JournalArtifact, read_committed_artifact, record_transition
from gigai.private_records import import_reference
from gigai.run import launch_run, read_run_details
from gigai.scout_proposal_execution import (
    ScoutProposalExecutionError,
    execute_local_proposal,
)
from gigai.setup import build_config

from tests.test_scout07_posting_inputs import _completed_find_jobs
from tests.test_scout03_native_records import _experience, _profile


MODEL = "qwen3.8:latest"
DIGEST = "sha256:" + "a" * 64
def _proposal() -> dict[str, object]:
    def evidence(text: str, handles: list[str]) -> dict[str, object]:
        return {
            "text": text,
            "source_type": "model_assessment",
            "evidence_handles": handles,
        }

    return {
        "schema_version": "1.0",
        "kind": "scout-private-proposal",
        "status": "complete",
        "fit_reasons": [
            evidence(
                "The selected posting and experience overlap.", ["source_1", "source_3"]
            )
        ],
        "hard_blockers": [],
        "unknowns": [],
        "preference_rejection_reason": None,
        "proposed_resume_focus": {
            "state": "focus",
            "items": [evidence("Emphasize the selected experience.", ["source_3"])],
        },
        "focused_experience_questions": {
            "state": "questions",
            "items": [
                {
                    "question": "Which example should be emphasized?",
                    "why": "The selected experience is broad.",
                    "evidence_handles": ["source_3"],
                }
            ],
        },
        "ranking": {
            "ordinal_fit": 3,
            "rationale": evidence("Moderate explainable fit.", ["source_1"]),
            "meaning": "explainable_fit_only_not_hiring_probability",
        },
        "requested_user_actions": ["keep_for_review"],
    }


class _Transport(httpx.BaseTransport):
    def __init__(self, output: dict[str, object] | None = None) -> None:
        self.output = output or _proposal()
        self.calls: list[str] = []
        self.closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path == "/api/version":
            payload: dict[str, object] = {"version": "0.34.0"}
        elif request.url.path == "/api/tags":
            payload = {"models": [{"name": MODEL, "digest": DIGEST}]}
        elif request.url.path == "/api/chat":
            payload = {
                "model": MODEL,
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(self.output)},
                "eval_count": 30,
            }
        else:
            payload = {"error": "unexpected"}
        return httpx.Response(200, json=payload, request=request)

    def close(self) -> None:
        self.closed = True


def _config(tmp_path: Path):
    return build_config(
        home_root=tmp_path / "home",
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(
                "local-loopback", "ollama_local", base_url="http://127.0.0.1:11434"
            ),
        ),
        model_targets=(
            ModelTarget(
                "local-proposal",
                "local-loopback",
                MODEL,
                ("text",),
                1600,
                reasoning_effort="none",
                model_digest=DIGEST,
                context_tokens=4096,
                max_response_bytes=131072,
            ),
        ),
        profiles=(
            Profile(
                "default",
                "local-proposal",
                "local-proposal",
                "local-proposal",
                reviewer="local-proposal",
            ),
        ),
    )


def _setup_inputs(tmp_path: Path):
    resolved, posting_selector, _snapshot, _metadata = _completed_find_jobs(tmp_path)
    home, target = tmp_path / "home", tmp_path / "target"
    preference_path = tmp_path / "preference.txt"
    preference_path.write_text(
        "Minimum salary $120000; remote preferred.\n", encoding="utf-8"
    )
    preference = import_reference(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        kind="resume",
        source=preference_path,
        label="synthetic preference",
    )
    experience = create_native_record(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        content=_experience(answered=True),
        actor={"kind": "operator", "id": "synthetic-user"},
        origin="user_reported",
        operation_key="proposal-experience",
    )
    profile = create_native_record(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        content=_profile(),
        actor={"kind": "operator", "id": "synthetic-user"},
        origin="user_reported",
        operation_key="proposal-profile",
    )
    selectors = (
        {
            "purpose": "experience",
            "selector": {"family": "g45_reference", "id": preference.item_id},
        },
        {"purpose": "experience", "selector": {
            "family": "scout_record",
            "record_id": experience.record_id,
            "revision_id": experience.revision_id,
            "scope": {"mode": "saved_default", "task_context_id": None},
        }},
        {"purpose": "preferences", "selector": {
            "family": "scout_record",
            "record_id": profile.record_id,
            "revision_id": profile.revision_id,
            "scope": {"mode": "saved_default", "task_context_id": None},
        }},
    )
    # The fixture Gig has several approved graph descriptors.  The ordinary
    # launch API intentionally requires a sealed selector; this test-only
    # bridge selects the existing tailor graph while keeping normal Run
    # allocation/goal publication on the real lifecycle path.
    import gigai.run as run_module
    subprocess.run(["git", "-C", str(target), "config", "user.name", "Proposal Test"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "proposal-test@gigai.invalid"], check=True)
    (target / "README.md").write_text("proposal execution fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "--quiet", "-m", "proposal fixture"], check=True)
    original_resolver = run_module.resolve_selected_graph_authority
    def select_existing_tailor_graph(resolved_arg, authority, _selector):
        graph, _descriptor = original_resolver(resolved_arg, authority, "tailor-application")
        return graph, None
    run_module.resolve_selected_graph_authority = select_existing_tailor_graph
    try:
        run = launch_run(
            home_root=home,
            requested_target=target,
            gig_id=resolved.gig_id,
            wait=True,
        )
    finally:
        run_module.resolve_selected_graph_authority = original_resolver
    details = read_run_details(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        run_id=run.run_id,
    )
    graph = parse_json_bytes((run.run_path / "goal-graph.json").read_bytes())
    # Use a still-pending allocated Goal.  The first Goal was terminalized by
    # the genuine deterministic fixture Run; selecting it would make the
    # test-only reactivation contradict committed terminal history.
    goal_id = graph["goals"][-1]["goal_id"]
    # The allocated Run and Goal IDs come from the real scheduler lifecycle;
    # this fixture then publishes a valid active state after the deterministic
    # worker has exited, avoiding a race while exercising the caller.
    details["status"] = "running"
    details["finished_at"] = None
    details["terminal_handoff"] = None
    details["execution_summary"] = "Run is held by the proposal execution fixture."
    for item in details["goals"]:
        active = item["goal_id"] == goal_id
        if active:
            item["status"] = "running"
            item["outcome"] = None
            item["finished_at"] = None
            item["started_at"] = details["started_at"]
    details["goal_sets"] = {
        "active": [goal_id], "blocked": [], "cancelled": [],
        "failed": [], "gated": [], "pending": [
            item["goal_id"] for item in details["goals"]
            if item["goal_id"] != goal_id and item["status"] in {"pending", "ready"}
        ], "ready": [],
        "complete": [
            item["goal_id"] for item in details["goals"] if item["status"] == "complete"
        ],
    }
    record_transition(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        handoff_id=f"handoff_{uuid.uuid4()}",
        transition="goal_started",
        body="Test-only fixture reopened an allocated Run for proposal execution.",
        artifacts=(JournalArtifact(
            f"runs/{run.run_id}/run-details.json", canonical_json_bytes(details)
        ),),
        front_matter={
            "run_id": run.run_id, "goal_id": goal_id, "outcome": "STARTED",
            "actor": {"kind": "operator", "id": "test-fixture"},
        },
    )
    return resolved, posting_selector, selectors, run.run_id, goal_id


def _head(resolved) -> str:
    return subprocess.check_output(
        ["git", "-C", str(resolved.path), "rev-parse", "HEAD"], text=True
    ).strip()


def test_local_proposal_joins_real_discovery_and_private_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors, run_id, goal_id = _setup_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    result = execute_local_proposal(
        resolved=resolved,
        config=config,
        run_id=run_id,
        goal_id=goal_id,
        model_target="local-proposal",
        posting_selector=posting,
        private_selectors=selectors,
        local_allowed=True,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000902"),
    )
    assert result.result["status"] == "complete"
    assert (
        result.result["input_lineage"]["public_source"]["identity"]["run_id"]
        == posting["run_id"]
    )
    assert result.result["proposal"]["kind"] == "scout-private-proposal"
    assert transport.calls == [
        "/api/version",
        "/api/tags",
        "/api/chat",
        "/api/version",
        "/api/tags",
    ]
    assert transport.closed
    result_path = f"runs/{run_id}/scout-proposals/{result.invocation.record['invocation_id']}/result.json"
    raw, _commit = read_committed_artifact(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        path=result_path,
    )
    assert json.loads(raw) == result.result


def test_completed_goal_rejects_sequential_repeat_without_transport_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors, run_id, goal_id = _setup_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    execute_local_proposal(
        resolved=resolved,
        config=config,
        run_id=run_id,
        goal_id=goal_id,
        model_target="local-proposal",
        posting_selector=posting,
        private_selectors=selectors,
        local_allowed=True,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000912"),
    )
    first_calls = list(transport.calls)
    first_head = _head(resolved)
    with pytest.raises(ScoutProposalExecutionError) as refused:
        execute_local_proposal(
            resolved=resolved,
            config=config,
            run_id=run_id,
            goal_id=goal_id,
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
            local_allowed=True,
        )
    assert refused.value.code == "execution_authority_refused"
    assert transport.calls == first_calls
    assert _head(resolved) == first_head
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=run_id,
    )
    selected = next(item for item in details["goals"] if item["goal_id"] == goal_id)
    assert (selected["status"], selected["outcome"]) == ("complete", "COMPLETE")
    assert details["status"] == "running"


def test_failed_goal_rejects_repeat_and_preserves_failed_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors, run_id, goal_id = _setup_inputs(tmp_path)
    transport = _Transport({"not": "a proposal"})
    config = _config(tmp_path)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    result = execute_local_proposal(
        resolved=resolved,
        config=config,
        run_id=run_id,
        goal_id=goal_id,
        model_target="local-proposal",
        posting_selector=posting,
        private_selectors=selectors,
        local_allowed=True,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000913"),
    )
    assert result.result["status"] == "failed"
    first_calls = list(transport.calls)
    first_head = _head(resolved)
    with pytest.raises(ScoutProposalExecutionError) as refused:
        execute_local_proposal(
            resolved=resolved,
            config=config,
            run_id=run_id,
            goal_id=goal_id,
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
            local_allowed=True,
        )
    assert refused.value.code == "execution_authority_refused"
    assert transport.calls == first_calls
    assert _head(resolved) == first_head
    details = read_run_details(
        home_root=tmp_path / "home",
        requested_target=tmp_path / "target",
        gig_id=resolved.gig_id,
        run_id=run_id,
    )
    selected = next(item for item in details["goals"] if item["goal_id"] == goal_id)
    assert (selected["status"], selected["outcome"]) == ("failed", "FAILED")


def test_local_permission_and_hosted_target_are_refused_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors, run_id, goal_id = _setup_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    with pytest.raises(ScoutProposalExecutionError) as denied:
        execute_local_proposal(
            resolved=resolved,
            config=config,
                run_id=run_id,
                goal_id=goal_id,
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=selectors,
            local_allowed=False,
        )
    assert denied.value.code == "local_runtime_denied"
    assert not transport.calls
    hosted = replace(
        config,
        endpoints=(Endpoint("hosted", "openai_api", credential="unused"),),
        model_targets=(ModelTarget("hosted", "hosted", MODEL, ("text",), 100),),
    )
    with pytest.raises(ScoutProposalExecutionError) as wrong:
        execute_local_proposal(
            resolved=resolved,
            config=hosted,
            run_id=run_id,
            goal_id=goal_id,
            model_target="hosted",
            posting_selector=posting,
            private_selectors=selectors,
            local_allowed=True,
        )
    assert wrong.value.code == "local_target_required"


def test_changed_discovery_selector_refuses_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors, run_id, goal_id = _setup_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    changed = dict(posting)
    changed["snapshot_id"] = "snapshot_" + "f" * 32
    with pytest.raises(ScoutProposalExecutionError) as refused:
        execute_local_proposal(
            resolved=resolved,
            config=config,
                run_id=run_id,
                goal_id=goal_id,
            model_target="local-proposal",
            posting_selector=changed,
            private_selectors=selectors,
            local_allowed=True,
        )
    assert refused.value.code == "source_resolution_refused"
    assert not transport.calls


def test_invalid_model_output_is_not_a_complete_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors, run_id, goal_id = _setup_inputs(tmp_path)
    spoofed = _proposal()
    spoofed["input_lineage"] = {"public_source": "model-claimed"}
    transport = _Transport(spoofed)
    config = _config(tmp_path)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    result = execute_local_proposal(
        resolved=resolved,
        config=config,
        run_id=run_id,
        goal_id=goal_id,
        model_target="local-proposal",
        posting_selector=posting,
        private_selectors=selectors,
        local_allowed=True,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000903"),
    )
    assert result.result["status"] == "failed"
    assert result.result["proposal"] is None
    assert result.invocation.record["outcome"] == "succeeded"

    transitions = []
    for handoff in sorted((resolved.path / "handoffs").glob("*.txt")):
        front, _body = parse_json_front_matter(handoff.read_bytes())
        if front.get("run_id") == run_id and front.get("goal_id") == goal_id:
            transitions.append(front.get("transition"))
    assert transitions[-1:] == ["goal_failed"]
    reopened = max(index for index, value in enumerate(transitions) if value == "goal_started")
    assert transitions[reopened + 1 :].count("goal_completed") == 0


def test_authority_refuses_shape_valid_foreign_and_nonmember_ids_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors, run_id, goal_id = _setup_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    for foreign_run, foreign_goal in (
        ("run_00000000-0000-4000-8000-000000000999", goal_id),
        (run_id, "goal_00000000-0000-4000-8000-000000000999"),
    ):
        with pytest.raises(ScoutProposalExecutionError) as refused:
            execute_local_proposal(
                resolved=resolved,
                config=config,
                run_id=foreign_run,
                goal_id=foreign_goal,
                model_target="local-proposal",
                posting_selector=posting,
                private_selectors=selectors,
                local_allowed=True,
            )
        assert refused.value.code == "execution_authority_refused"
    assert not transport.calls


def test_private_source_requires_explicit_compatible_purpose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors, run_id, goal_id = _setup_inputs(tmp_path)
    transport = _Transport()
    config = _config(tmp_path)
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    ambiguous = list(selectors)
    ambiguous[0] = {"selector": ambiguous[0]["selector"], "purpose": "preferences"}
    with pytest.raises(ScoutProposalExecutionError) as refused:
        execute_local_proposal(
            resolved=resolved,
            config=config,
            run_id=run_id,
            goal_id=goal_id,
            model_target="local-proposal",
            posting_selector=posting,
            private_selectors=tuple(ambiguous),
            local_allowed=True,
        )
    assert refused.value.code == "private_source_purpose_mismatch"
    assert not transport.calls
