from __future__ import annotations

from pathlib import Path
import uuid

from gigai.adapters.factory import ModelAdapterBinding, resolve_model_adapter
from gigai.adapters.port import InvocationResult, ModelInvocationCancelled, ModelInvocationError, NormalizedUsage
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.config import CredentialReference, Endpoint, ModelTarget
from gigai.lifecycle import approve_offline, create_offline
from gigai.model_execution import InvocationBudget, InvocationPolicy, SelectedReference, run_model_invocation
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_model_invocation
from gigai.workpad import resolve_workpad


RUN_ID = "run_00000000-0000-4000-8000-000000000101"
GOAL_ID = "goal_00000000-0000-4000-8000-000000000102"
REFERENCE_ID = "ref_00000000-0000-4000-8000-000000000103"


def _fixture(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 30))
    created = create_offline(home_root=home, requested_target=target, name="g18-execution", open_editor=False, uuid_factory=lambda: next(values))
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)
    return config, resolved


class _FakePort:
    def invoke(self, request):
        return InvocationResult(
            status="success",
            output_text=f"reviewed:{request.prompt}",
            resolved_model=request.model,
            raw_usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            normalized_usage=NormalizedUsage(2, 3, 5),
            cost_status="unavailable",
        )


def _reference(content: bytes) -> SelectedReference:
    return SelectedReference(
        reference_id=REFERENCE_ID,
        path="references/source.txt",
        content=content,
        content_sha256=digest_imported_bytes(content),
    )


def test_g18_invocation_runs_boundary_then_persists_terminal_evidence(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    actual = resolve_model_adapter(config, "offline-default")
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda _config, _target: ModelAdapterBinding(actual.current, _FakePort()),
    )
    execution = run_model_invocation(
        resolved=resolved,
        config=config,
        run_id=RUN_ID,
        goal_id=GOAL_ID,
        model_target="offline-default",
        role="reviewer",
        prompt="Review the source.",
        references=(_reference(b"public source bytes\n"),),
        selected_reference_ids=(REFERENCE_ID,),
        policy=InvocationPolicy(allowed_reference_ids=frozenset({REFERENCE_ID}), offline=True),
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000104"),
    )

    assert execution.record["outcome"] == "succeeded"
    assert validate_model_invocation(execution.record).valid
    record_path = resolved.path / "runs" / RUN_ID / "model-invocations" / execution.record["invocation_id"] / "record.json"
    assert parse_json_bytes(record_path.read_bytes()) == execution.record
    response_path = record_path.parent / "response.json"
    assert response_path.is_file()
    assert b"public source bytes" in response_path.read_bytes()
    assert execution.journal_entry.path.is_file()


def test_g18_network_boundary_blocks_before_remote_adapter_call(tmp_path: Path, monkeypatch) -> None:
    base, resolved = _fixture(tmp_path)
    config = build_config(
        home_root=base.home_root,
        workpad_root=base.workpad_root,
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(CredentialReference("provider", "environment", "G18_TEST_TOKEN"),),
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="remote", adapter="openai_api", credential="provider"),
        ),
        model_targets=(
            ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ModelTarget("remote-default", "remote", "gpt-test", ("text",), 8),
        ),
    )
    called = False

    class _ShouldNotRun:
        def invoke(self, _request):
            nonlocal called
            called = True
            raise AssertionError("network boundary allowed an adapter call")

    actual = resolve_model_adapter(config, "remote-default")
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda _config, _target: ModelAdapterBinding(actual.current, _ShouldNotRun()),
    )
    execution = run_model_invocation(
        resolved=resolved,
        config=config,
        run_id=RUN_ID,
        goal_id=GOAL_ID,
        model_target="remote-default",
        role="reviewer",
        prompt="Review the source.",
        references=(_reference(b"public source bytes\n"),),
        selected_reference_ids=(REFERENCE_ID,),
        policy=InvocationPolicy(allowed_reference_ids=frozenset({REFERENCE_ID}), network_allowed=False),
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000105"),
    )

    assert not called
    assert execution.record["outcome"] == "blocked"
    assert execution.record["error"]["code"] == "network_denied"
    assert execution.record["boundary"]["network"]["result"] == "denied"
    assert validate_model_invocation(execution.record).valid


def test_g18_redaction_failure_is_blocked_without_secret_in_evidence(tmp_path: Path) -> None:
    config, resolved = _fixture(tmp_path)
    execution = run_model_invocation(
        resolved=resolved,
        config=config,
        run_id=RUN_ID,
        goal_id=GOAL_ID,
        model_target="offline-default",
        role="reviewer",
        prompt="Review the source.",
        references=(_reference(b"private secret bytes\n"),),
        selected_reference_ids=(REFERENCE_ID,),
        policy=InvocationPolicy(
            allowed_reference_ids=frozenset({REFERENCE_ID}),
            offline=True,
            required_sensitive_values=("secret",),
        ),
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000106"),
    )

    assert execution.record["outcome"] == "blocked"
    assert execution.record["error"]["code"] == "redaction_failed"
    record_path = resolved.path / "runs" / RUN_ID / "model-invocations" / execution.record["invocation_id"] / "record.json"
    assert b"secret" not in record_path.read_bytes()
    request_path = record_path.parent / "request.json"
    assert b"secret" not in request_path.read_bytes()


def test_g18_budget_exhaustion_blocks_before_adapter_call(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    actual = resolve_model_adapter(config, "offline-default")
    called = False

    class _ShouldNotRun:
        def invoke(self, _request):
            nonlocal called
            called = True
            raise AssertionError("budget boundary allowed an adapter call")

    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda _config, _target: ModelAdapterBinding(actual.current, _ShouldNotRun()),
    )
    execution = run_model_invocation(
        resolved=resolved,
        config=config,
        run_id=RUN_ID,
        goal_id=GOAL_ID,
        model_target="offline-default",
        role="reviewer",
        prompt="Review the source.",
        references=(_reference(b"public source bytes\n"),),
        selected_reference_ids=(REFERENCE_ID,),
        policy=InvocationPolicy(allowed_reference_ids=frozenset({REFERENCE_ID}), offline=True),
        budget=InvocationBudget(max_model_calls=0, max_tokens=128),
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000107"),
    )
    assert not called
    assert execution.record["outcome"] == "blocked"
    assert execution.record["error"]["code"] == "budget_exhausted"


def test_g18_missing_remote_credential_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    base, resolved = _fixture(tmp_path)
    config = build_config(
        home_root=base.home_root,
        workpad_root=base.workpad_root,
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(CredentialReference("provider", "environment", "G18_MISSING_TOKEN"),),
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="remote", adapter="openai_api", credential="provider"),
        ),
        model_targets=(
            ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ModelTarget("remote-default", "remote", "gpt-test", ("text",), 8),
        ),
    )
    actual = resolve_model_adapter(config, "remote-default")
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda _config, _target: ModelAdapterBinding(actual.current, _FakePort()),
    )
    execution = run_model_invocation(
        resolved=resolved,
        config=config,
        run_id=RUN_ID,
        goal_id=GOAL_ID,
        model_target="remote-default",
        role="reviewer",
        prompt="Review the source.",
        references=(_reference(b"public source bytes\n"),),
        selected_reference_ids=(REFERENCE_ID,),
        policy=InvocationPolicy(allowed_reference_ids=frozenset({REFERENCE_ID}), network_allowed=True),
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000108"),
    )
    assert execution.record["outcome"] == "unavailable"
    assert execution.record["finish"] == "unavailable"
    assert execution.record["boundary"]["credential"]["lookup"] == "missing"
    assert validate_model_invocation(execution.record).valid


def test_g18_provider_terminal_failures_are_normalized(tmp_path: Path, monkeypatch) -> None:
    cases = (
        (ModelInvocationError("provider response was malformed"), "failed"),
        (ModelInvocationError("provider timed out"), "timeout"),
        (ModelInvocationError("provider unavailable: 503"), "unavailable"),
        (ModelInvocationCancelled("caller cancelled"), "cancelled"),
    )
    for index, (failure, expected) in enumerate(cases, start=110):
        config, resolved = _fixture(tmp_path / str(index))
        actual = resolve_model_adapter(config, "offline-default")

        class _FailingPort:
            def invoke(self, _request):
                raise failure

        monkeypatch.setattr(
            "gigai.model_execution.resolve_model_adapter",
            lambda _config, _target, actual=actual: ModelAdapterBinding(actual.current, _FailingPort()),
        )
        execution = run_model_invocation(
            resolved=resolved,
            config=config,
            run_id=RUN_ID,
            goal_id=GOAL_ID,
            model_target="offline-default",
            role="reviewer",
            prompt="Review the source.",
            references=(_reference(b"public source bytes\n"),),
            selected_reference_ids=(REFERENCE_ID,),
            policy=InvocationPolicy(allowed_reference_ids=frozenset({REFERENCE_ID}), offline=True),
            uuid_factory=lambda: uuid.UUID(f"00000000-0000-4000-8000-{index:012x}"),
        )
        assert execution.record["outcome"] == expected
        assert validate_model_invocation(execution.record).valid
