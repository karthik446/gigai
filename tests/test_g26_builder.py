from __future__ import annotations

import json
from types import SimpleNamespace
import time

import pytest

from gigai.builder import GigBuilderError, build_model_draft
from gigai.canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from gigai.config import CredentialReference, Endpoint, ModelTarget, Profile
from gigai.lifecycle import (
    LifecycleError,
    _builder_selection,
    _builder_session_record,
    build_interview_proposal,
    recover_builder_session,
    start_interview,
)
from gigai.proposal_interview import answer_question, attach_reference_choices
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _start(tmp_path):
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
    )
    run_setup(config)
    initialize_target(home_root=home, requested_target=target)
    start = start_interview(
        home_root=home,
        requested_target=target,
        name="builder-test",
        request="Review this repository.",
        reference_paths=(),
    )
    return config, start


def test_deterministic_builder_returns_bounded_draft(tmp_path) -> None:
    config, start = _start(tmp_path)
    draft, selection = build_model_draft(
        config=config,
        model_target="offline-default",
        session=start.session,
        reference_bytes={},
    )
    assert draft.summary.startswith("A local Gig proposal")
    assert selection["adapter"] == "deterministic"


def test_remote_builder_requires_explicit_network_permission(tmp_path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        credentials=(CredentialReference("remote-key", "environment", "GIGAI_TEST_KEY"),),
        endpoints=(Endpoint("remote", "openai_api", credential="remote-key"),),
        model_targets=(ModelTarget("remote-model", "remote", "gpt-test", ("text",), 128),),
        profiles=(Profile("default", "remote-model", "remote-model", "remote-model"),),
    )
    run_setup(config)
    initialize_target(home_root=home, requested_target=target)
    start = start_interview(
        home_root=home,
        requested_target=target,
        name="builder-test",
        request="Review this repository.",
        reference_paths=(),
    )
    with pytest.raises(GigBuilderError, match="explicit configured-provider permission"):
        build_model_draft(
            config=config,
            model_target="remote-model",
            session=start.session,
            reference_bytes={},
        )


def test_malformed_model_output_fails_closed(monkeypatch, tmp_path) -> None:
    config, start = _start(tmp_path)

    class FakePort:
        def invoke(self, request):
            from gigai.adapters.port import InvocationResult, NormalizedUsage

            return InvocationResult(
                status="success",
                output_text=json.dumps({"summary": "missing fields"}),
                resolved_model="fake",
                raw_usage={},
                normalized_usage=NormalizedUsage(None, None, None),
                cost_status="unavailable",
            )

    from gigai import builder

    monkeypatch.setattr(
        builder,
        "resolve_model_adapter",
        lambda config, target: type(
            "Binding",
            (),
            {
                "current": type(
                    "Current",
                    (),
                    {
                        "endpoint": type("Endpoint", (), {"adapter": "deterministic", "name": "offline"})(),
                        "target": type("Target", (), {"name": "offline-default", "model": "fixture-v1"})(),
                    },
                )(),
                "port": FakePort(),
                "request": lambda self, **kwargs: kwargs,
            },
        )(),
    )
    with pytest.raises(GigBuilderError, match="invalid draft shape") as error:
        build_model_draft(
            config=config,
            model_target="offline-default",
            session=start.session,
            reference_bytes={},
        )
    assert error.value.reason == "malformed"


def test_unavailable_builder_target_writes_terminal_session(tmp_path) -> None:
    config, start = _start(tmp_path)
    session = answer_question(start.session, "scope", "Review this repository.")

    with pytest.raises(LifecycleError, match="unknown model target"):
        build_interview_proposal(
            home_root=config.home_root,
            requested_target=tmp_path / "target",
            start=start,
            session=session,
            model_target="missing-model",
            reference_bytes={},
        )

    snapshot = parse_json_bytes(
        (start.workpad / "manifests/gig-builder-session.json").read_bytes()
    )
    assert snapshot["state"] == "unavailable"
    assert snapshot["terminal_reason"] == "unavailable"


def test_remote_builder_receives_only_selected_reference_content(monkeypatch, tmp_path) -> None:
    config, start = _start(tmp_path)
    selected = b"selected context"
    excluded = b"unselected context and synthetic-secret"
    from gigai.proposal_interview import ReferenceDecision

    references = (
        ReferenceDecision("ref_00000000-0000-4000-8000-000000000005", digest_imported_bytes(selected)),
        ReferenceDecision("ref_00000000-0000-4000-8000-000000000006", digest_imported_bytes(excluded)),
    )
    session = attach_reference_choices(start.session, references)
    session = answer_question(session, "references", [references[0].reference_id])
    prompts: list[str] = []

    class FakePort:
        def invoke(self, request):
            prompts.append(request.prompt)
            from gigai.adapters.port import InvocationResult, NormalizedUsage

            return InvocationResult(
                status="success",
                output_text=json.dumps(
                    {
                        "summary": "Remote bounded draft",
                        "assumptions": [],
                        "unresolved_questions": [],
                        "citations": [],
                    }
                ),
                resolved_model="fake",
                raw_usage={},
                normalized_usage=NormalizedUsage(None, None, None),
                cost_status="unavailable",
            )

    binding = SimpleNamespace(
        current=SimpleNamespace(
            endpoint=SimpleNamespace(adapter="openai_api", name="remote", credential="remote-key"),
            target=SimpleNamespace(name="remote-model", model="gpt-test"),
        ),
        port=FakePort(),
        request=lambda **kwargs: SimpleNamespace(max_output_tokens=64, **kwargs),
    )
    from gigai import builder

    monkeypatch.setattr(builder, "resolve_model_adapter", lambda *_args: binding)
    draft, _selection = builder.build_model_draft(
        config=config,
        model_target="remote-model",
        session=session,
        reference_bytes={references[0].reference_id: selected, references[1].reference_id: excluded},
        network_allowed=True,
    )
    assert draft.summary == "Remote bounded draft"
    assert len(prompts) == 1
    assert "selected context" in prompts[0]
    assert "unselected context" not in prompts[0]
    assert "synthetic-secret" not in prompts[0]
    assert "remote-key" not in prompts[0]


def test_builder_timeout_is_classified_without_a_draft(monkeypatch, tmp_path) -> None:
    config, start = _start(tmp_path)

    class SlowPort:
        def invoke(self, request):
            time.sleep(0.05)
            raise AssertionError("late result must not be consumed")

    binding = SimpleNamespace(
        current=SimpleNamespace(
            endpoint=SimpleNamespace(adapter="deterministic", name="offline"),
            target=SimpleNamespace(name="offline-default", model="fixture-v1"),
        ),
        port=SlowPort(),
        request=lambda **kwargs: SimpleNamespace(max_output_tokens=64, **kwargs),
    )
    from gigai import builder

    monkeypatch.setattr(builder, "resolve_model_adapter", lambda *_args: binding)
    with pytest.raises(GigBuilderError) as error:
        builder.build_model_draft(
            config=config,
            model_target="offline-default",
            session=start.session,
            reference_bytes={},
            max_wall_time_ms=5,
        )
    assert error.value.reason == "timed_out"


def test_interrupted_builder_recovery_terminalizes_without_retry(tmp_path) -> None:
    config, start = _start(tmp_path)
    session = answer_question(start.session, "scope", "Review this repository.")
    payload = _builder_session_record(
        session=session,
        start=start,
        selection=_builder_selection(config, "offline-default"),
        state="researching",
        draft_ref=None,
        terminal_reason=None,
    )
    builder_path = start.workpad / "manifests/gig-builder-session.json"
    builder_path.write_bytes(canonical_json_bytes(payload))

    recovered = recover_builder_session(start=start)

    assert recovered.proposal_id is None
    assert not recovered.builder_ready
    snapshot = parse_json_bytes(builder_path.read_bytes())
    assert snapshot["state"] == "failed"
    assert snapshot["terminal_reason"] == "interrupted_build_recovery"


def test_completed_builder_cannot_be_run_again(tmp_path) -> None:
    config, start = _start(tmp_path)
    session = answer_question(start.session, "scope", "Review this repository.")
    build_interview_proposal(
        home_root=config.home_root,
        requested_target=tmp_path / "target",
        start=start,
        session=session,
        model_target="offline-default",
        reference_bytes={},
    )
    recovered = recover_builder_session(start=start)
    assert recovered.proposal_id is not None
    assert recovered.review["summary"]
    assert recovered.builder_ready

    with pytest.raises(LifecycleError, match="reviewable"):
        build_interview_proposal(
            home_root=config.home_root,
            requested_target=tmp_path / "target",
            start=start,
            session=session,
            model_target="offline-default",
            reference_bytes={},
        )
