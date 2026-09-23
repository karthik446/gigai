from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import copy
import uuid

import httpx
import pytest

from gigai.adapters.factory import resolve_model_adapter
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.config import Endpoint, MalformedConfigurationError, ModelTarget, Profile
from gigai.lifecycle import approve_offline, create_offline
from gigai.model_execution import (
    InvocationPolicy,
    SelectedReference,
    run_model_invocation,
)
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_model_invocation, validate_serialized_contract
from gigai.workpad import resolve_workpad


MODEL = "qwen3.8:8b"
DIGEST = "sha256:" + "a" * 64
REFERENCE_ID = "ref_00000000-0000-4000-8000-000000000201"
RUN_ID = "run_00000000-0000-4000-8000-000000000202"
GOAL_ID = "goal_00000000-0000-4000-8000-000000000203"


class _Transport(httpx.BaseTransport):
    def __init__(self, *, digest: str = DIGEST, response: dict[str, object] | None = None, cancel: bool = False):
        self.digest = digest
        self.response = response or {
            "model": MODEL,
            "done": True,
            "done_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "Fit: strong.\nGaps: none known.\nFocus: verify examples.\nQuestions: none.\nEvidence: [ref_00000000-0000-4000-8000-000000000201]",
            },
            "eval_count": 12,
        }
        self.calls: list[tuple[str, str, bytes]] = []
        self.closed = False
        self.cancel = cancel

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        self.calls.append((request.method, request.url.path, body))
        if self.cancel and request.url.path == "/api/chat":
            raise KeyboardInterrupt
        if request.url.path == "/api/version":
            payload: dict[str, object] = {"version": "0.34.0"}
        elif request.url.path == "/api/tags":
            payload = {"models": [{"name": MODEL, "digest": self.digest}]}
        elif request.url.path == "/api/chat":
            payload = self.response
        else:
            payload = {"error": "unexpected"}
        return httpx.Response(200, json=payload, request=request)

    def close(self) -> None:
        self.closed = True


def _fixture(tmp_path: Path):
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    endpoint = Endpoint(
        name="local-loopback",
        adapter="ollama_local",
        base_url="http://127.0.0.1:11434",
    )
    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(endpoint, Endpoint(name="offline", adapter="deterministic")),
        model_targets=(
            ModelTarget(
                "local-qwen",
                "local-loopback",
                MODEL,
                ("text",),
                96,
                reasoning_effort="none",
                model_digest=DIGEST,
                context_tokens=2048,
                max_response_bytes=65536,
            ),
            ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
        ),
        profiles=(Profile("default", "local-qwen", "local-qwen", "local-qwen"),),
    )
    run_setup(config)
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 30))
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="local-model-run",
        open_editor=False,
        uuid_factory=lambda: next(values),
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=lambda: next(values),
    )
    resolved = resolve_workpad(
        home_root=home,
        requested_target=target,
        gig_id=created.gig_id,
        allow_semantic_state=True,
    )
    return config, resolved


def _reference() -> SelectedReference:
    content = b"Synthetic public posting; salary floor $100k; sponsorship unknown.\n"
    return SelectedReference(
        REFERENCE_ID,
        "references/synthetic-posting.txt",
        content,
        digest_imported_bytes(content),
    )


def _run(config, resolved, transport, monkeypatch, *, local_allowed=True):
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active_config, target_name: resolve_model_adapter(
            active_config,
            target_name,
            transport_overrides={"local-loopback": transport},
        ),
    )
    return run_model_invocation(
        resolved=resolved,
        config=config,
        run_id=RUN_ID,
        goal_id=GOAL_ID,
        model_target="local-qwen",
        role="researcher",
        prompt="Prepare a private fit proposal with gaps, focus, questions, and evidence refs.",
        references=(_reference(),),
        selected_reference_ids=(REFERENCE_ID,),
        policy=InvocationPolicy(
            allowed_reference_ids=frozenset({REFERENCE_ID}),
            local_allowed=local_allowed,
            offline=True,
        ),
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000204"),
    )


def test_config_factory_and_public_invocation_persist_local_private_result(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    transport = _Transport()
    execution = _run(config, resolved, transport, monkeypatch)

    assert execution.record["outcome"] == "succeeded"
    assert execution.record["schema_version"] == "2.0"
    assert execution.record["provider_family"] == "ollama_local"
    assert execution.record["local_identity"]["configured_endpoint"] == "http://127.0.0.1:11434"
    assert execution.record["local_identity"]["configured_model_digest"] == DIGEST
    assert execution.record["local_identity"]["observed"] == {
        "status": "passed",
        "runtime_version": "0.34.0",
        "model_digest": DIGEST,
    }
    assert not validate_serialized_contract(
        "model-invocation.schema.json", canonical_json_bytes(execution.record)
    ).valid
    malformed = copy.deepcopy(execution.record)
    malformed["local_identity"]["unexpected"] = True
    assert not validate_model_invocation(malformed).valid
    assert execution.record["boundary"]["network"] == {
        "policy": "local_loopback",
        "result": "local_permitted",
    }
    assert execution.record["boundary"]["redaction"]["result"] == "not_applicable"
    assert execution.result is not None and "Fit:" in execution.result.output_text
    assert validate_model_invocation(execution.record).valid
    assert [path for _, path, _ in transport.calls] == [
        "/api/version",
        "/api/tags",
        "/api/chat",
        "/api/version",
        "/api/tags",
    ]
    assert transport.closed


def test_local_identity_failure_is_durable_and_closes_without_chat(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    transport = _Transport(digest="sha256:" + "b" * 64)
    execution = _run(config, resolved, transport, monkeypatch)

    assert execution.record["outcome"] == "failed"
    assert execution.record["schema_version"] == "2.0"
    assert execution.record["local_identity"]["observed"]["status"] == "not_observed"
    assert execution.record["error"]["code"] == "provider_invocation_failed"
    assert execution.record["boundary"]["network"]["policy"] == "local_loopback"
    assert "/api/chat" not in [path for _, path, _ in transport.calls]
    assert transport.closed
    assert validate_model_invocation(execution.record).valid


def test_local_permission_denial_does_not_open_prompt_or_leak_redaction_claim(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    transport = _Transport()
    execution = _run(config, resolved, transport, monkeypatch, local_allowed=False)

    assert execution.record["outcome"] == "blocked"
    assert execution.record["error"]["code"] == "local_runtime_denied"
    assert execution.record["boundary"]["network"]["result"] == "local_denied"
    assert execution.record["boundary"]["redaction"]["result"] == "not_started"
    assert transport.calls == []
    assert transport.closed


def test_local_capability_mismatch_closes_binding_before_any_chat(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    target = next(item for item in config.model_targets if item.name == "local-qwen")
    malformed = replace(target, capabilities=("image",))
    config = replace(
        config,
        model_targets=tuple(
            malformed if item.name == "local-qwen" else item for item in config.model_targets
        ),
    )
    transport = _Transport()
    execution = _run(config, resolved, transport, monkeypatch)

    assert execution.record["outcome"] == "failed"
    assert execution.record["error"]["code"] == "provider_invocation_failed"
    assert transport.calls == []
    assert transport.closed
    assert validate_model_invocation(execution.record).valid


def test_local_cancellation_is_failed_attempt_and_closes_binding(tmp_path: Path, monkeypatch) -> None:
    config, resolved = _fixture(tmp_path)
    transport = _Transport(cancel=True)
    execution = _run(config, resolved, transport, monkeypatch)

    assert execution.record["outcome"] == "cancelled"
    assert execution.record["finish"] == "cancelled"
    assert execution.record["error"]["code"] == "model_invocation_cancelled"
    assert "/api/chat" in [path for _, path, _ in transport.calls]
    assert transport.closed
    assert validate_model_invocation(execution.record).valid


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"model_digest": None}, "requires a canonical lowercase model_digest"),
        ({"context_tokens": 0}, "must be a positive integer"),
        ({"max_response_bytes": 4 * 1024 * 1024 + 1}, "must be a positive integer"),
    ],
)
def test_local_config_requires_identity_and_bounded_options(tmp_path: Path, kwargs: dict[str, object], message: str) -> None:
    local_values = {
        "reasoning_effort": "none",
        "model_digest": DIGEST,
        "context_tokens": 2048,
        "max_response_bytes": 65536,
    }
    local_values.update(kwargs)
    with pytest.raises(MalformedConfigurationError, match=message):
        build_config(
            home_root=tmp_path / "home",
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
            endpoints=(
                Endpoint("local-loopback", "ollama_local", base_url="http://127.0.0.1:11434"),
                Endpoint("offline", "deterministic"),
            ),
            model_targets=(
                ModelTarget(
                    "local-qwen", "local-loopback", MODEL, ("text",), 96,
                    **local_values,
                ),
                ModelTarget("offline-default", "offline", "fixture-v1", ("text",), 64),
            ),
            profiles=(Profile("default", "local-qwen", "local-qwen", "local-qwen"),),
        )
