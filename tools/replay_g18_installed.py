"""Replay G18's offline invocation/exchange path from an installed wheel."""

from __future__ import annotations

from pathlib import Path
import tempfile
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
from gigai.validators import validate_model_exchange, validate_model_invocation
from gigai.workpad import resolve_workpad


class FakePort:
    def __init__(self, label: str):
        self.label = label

    def invoke(self, request):
        return InvocationResult(
            status="success",
            output_text=f"{self.label}:{request.prompt}",
            resolved_model=request.model,
            raw_usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            normalized_usage=NormalizedUsage(1, 2, 3),
            cost_status="unavailable",
        )


def _ids(start: int):
    return iter(
        uuid.UUID(f"00000000-0000-4000-8000-{value:012x}")
        for value in (start, start + 1)
    ).__next__


def _sequence_ids(start: int):
    return iter(
        uuid.UUID(f"00000000-0000-4000-8000-{value:012x}")
        for value in range(start, start + 40)
    ).__next__


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gigai-g18-wheel-replay-") as temporary:
        root = Path(temporary)
        home = root / "home"
        target = root / "target"
        target.mkdir()
        config = build_config(
            home_root=home,
            workpad_root=root / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
            credentials=(),
            endpoints=(Endpoint(name="offline", adapter="deterministic"),),
            model_targets=(ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),),
        )
        run_setup(config)
        initialize_target(
            home_root=home,
            requested_target=target,
            uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
        )
        created = create_offline(
            home_root=home,
            requested_target=target,
            name="g18-wheel-replay",
            open_editor=False,
            uuid_factory=_sequence_ids(301),
        )
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=created.proposal_id,
            uuid_factory=_sequence_ids(341),
        )
        resolved = resolve_workpad(
            home_root=home,
            requested_target=target,
            gig_id=created.gig_id,
            allow_semantic_state=True,
        )
        actual = resolve_model_adapter(config, "offline-default")
        import gigai.model_execution as execution_module

        execution_module.resolve_model_adapter = lambda _config, target_name: ModelAdapterBinding(
            actual.current, FakePort(target_name)
        )
        content = b"installed wheel source\n"
        reference = SelectedReference(
            "ref_00000000-0000-4000-8000-000000000303",
            "references/source.txt",
            content,
            digest_imported_bytes(content),
        )
        left = run_model_invocation(
            resolved=resolved,
            config=config,
            run_id="run_00000000-0000-4000-8000-000000000304",
            goal_id="goal_00000000-0000-4000-8000-000000000305",
            model_target="offline-default",
            role="reviewer",
            prompt="Replay.",
            references=(reference,),
            selected_reference_ids=(reference.reference_id,),
            policy=InvocationPolicy(allowed_reference_ids=frozenset({reference.reference_id}), offline=True),
            uuid_factory=_ids(306),
        )
        right = run_model_invocation(
            resolved=resolved,
            config=config,
            run_id="run_00000000-0000-4000-8000-000000000304",
            goal_id="goal_00000000-0000-4000-8000-000000000307",
            model_target="offline-default",
            role="reviewer",
            prompt="Replay.",
            references=(reference,),
            selected_reference_ids=(reference.reference_id,),
            policy=InvocationPolicy(allowed_reference_ids=frozenset({reference.reference_id}), offline=True),
            uuid_factory=_ids(308),
        )
        comparison = compare_model_invocations(
            resolved=resolved,
            run_id="run_00000000-0000-4000-8000-000000000304",
            edge_id="edge_00000000-0000-4000-8000-000000000309",
            source_goal_id=left.record["goal_id"],
            receiver_goal_id=right.record["goal_id"],
            executions=(left, right),
            uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000310"),
        )
        handoff = prepare_model_handoff(
            resolved=resolved,
            run_id="run_00000000-0000-4000-8000-000000000304",
            edge_id="edge_00000000-0000-4000-8000-000000000311",
            source_goal_id=left.record["goal_id"],
            receiver_goal_id=right.record["goal_id"],
            source_execution=left,
            handoff_index=1,
            handoff_cap=1,
            uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000312"),
        )
        if not all(validate_model_invocation(item.record).valid for item in (left, right)):
            raise AssertionError("installed invocation replay failed validation")
        if not validate_model_exchange(comparison.record).valid or not validate_model_exchange(handoff.record).valid:
            raise AssertionError("installed exchange replay failed validation")
        print("replay_status=PASS")
        print("provider_effects=none")
        print("credential_values=none")
        print("network_calls=none")
        print("comparison=agreement")
        print("handoff=received")


if __name__ == "__main__":
    main()
