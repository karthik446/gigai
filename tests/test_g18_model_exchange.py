from __future__ import annotations

from pathlib import Path
import uuid

from gigai.adapters.factory import ModelAdapterBinding, resolve_model_adapter
from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.canonical import digest_imported_bytes
from gigai.config import Endpoint, ModelTarget
from gigai.lifecycle import approve_offline, create_offline
from gigai.model_execution import InvocationPolicy, SelectedReference, run_model_invocation
from gigai.model_exchange import compare_model_invocations, prepare_model_handoff
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_model_exchange
from gigai.workpad import resolve_workpad


RUN_ID = "run_00000000-0000-4000-8000-000000000201"
LEFT_GOAL = "goal_00000000-0000-4000-8000-000000000202"
RIGHT_GOAL = "goal_00000000-0000-4000-8000-000000000203"
EDGE_ID = "edge_00000000-0000-4000-8000-000000000204"
REFERENCE_ID = "ref_00000000-0000-4000-8000-000000000205"


class _FakePort:
    def __init__(self, suffix: str):
        self.suffix = suffix

    def invoke(self, request):
        return InvocationResult(
            status="success",
            output_text=f"{self.suffix}:{request.prompt}",
            resolved_model=request.model,
            raw_usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            normalized_usage=NormalizedUsage(1, 1, 2),
            cost_status="unavailable",
        )


def _fixture(tmp_path: Path):
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(),
        endpoints=(Endpoint(name="offline", adapter="deterministic"),),
        model_targets=(ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),),
    )
    run_setup(config)
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 50))
    created = create_offline(home_root=home, requested_target=target, name="g18-exchange", open_editor=False, uuid_factory=lambda: next(values))
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)
    return config, resolved


def _reference() -> SelectedReference:
    content = b"source bytes\n"
    return SelectedReference(REFERENCE_ID, "references/source.txt", content, digest_imported_bytes(content))


def _invoke(config, resolved, goal_id: str, suffix: str, monkeypatch):
    actual = resolve_model_adapter(config, "offline-default")
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda _config, _target: ModelAdapterBinding(actual.current, _FakePort(suffix)),
    )
    return run_model_invocation(
        resolved=resolved,
        config=config,
        run_id=RUN_ID,
        goal_id=goal_id,
        model_target="offline-default",
        role="reviewer",
        prompt="Review.",
        references=(_reference(),),
        selected_reference_ids=(REFERENCE_ID,),
        policy=InvocationPolicy(allowed_reference_ids=frozenset({REFERENCE_ID}), offline=True),
        uuid_factory=iter(
            uuid.UUID(f"00000000-0000-4000-8000-{index:012x}")
            for index in (
                (101, 201) if suffix == "left" else (102, 202)
            )
        ).__next__,
    )


def test_comparison_preserves_disagreement_and_never_selects_winner(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    left = _invoke(config, resolved, LEFT_GOAL, "left", monkeypatch)
    right = _invoke(config, resolved, RIGHT_GOAL, "right", monkeypatch)
    exchange = compare_model_invocations(
        resolved=resolved,
        run_id=RUN_ID,
        edge_id=EDGE_ID,
        source_goal_id=LEFT_GOAL,
        receiver_goal_id=RIGHT_GOAL,
        executions=(left, right),
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000206"),
    )
    assert exchange.record["status"] == "disagreement"
    assert exchange.record["comparison"]["selected_winner"] is None
    assert exchange.record["comparison"]["requires_human_adjudication"] is True
    assert validate_model_exchange(exchange.record).valid


def test_handoff_cap_blocks_without_receiver_invocation(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    source = _invoke(config, resolved, LEFT_GOAL, "left", monkeypatch)
    exchange = prepare_model_handoff(
        resolved=resolved,
        run_id=RUN_ID,
        edge_id=EDGE_ID,
        source_goal_id=LEFT_GOAL,
        receiver_goal_id=RIGHT_GOAL,
        source_execution=source,
        handoff_index=2,
        handoff_cap=1,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000207"),
    )
    assert exchange.record["status"] == "blocked"
    assert exchange.input_text is None
    assert exchange.record["automatic_fallback"] is False
    assert exchange.record["retry_count"] == 0
    assert validate_model_exchange(exchange.record).valid


def test_handoff_exposes_only_explicit_source_output(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    source = _invoke(config, resolved, LEFT_GOAL, "left", monkeypatch)
    exchange = prepare_model_handoff(
        resolved=resolved,
        run_id=RUN_ID,
        edge_id=EDGE_ID,
        source_goal_id=LEFT_GOAL,
        receiver_goal_id=RIGHT_GOAL,
        source_execution=source,
        handoff_index=1,
        handoff_cap=1,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000208"),
    )
    assert exchange.record["status"] == "received"
    assert exchange.input_text == source.result.output_text
    assert exchange.record["handoff"]["hidden_context"] is False
    assert validate_model_exchange(exchange.record).valid
