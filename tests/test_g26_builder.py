from __future__ import annotations

import json

import pytest

from gigai.builder import GigBuilderError, build_model_draft
from gigai.config import CredentialReference, Endpoint, ModelTarget, Profile
from gigai.lifecycle import start_interview
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
