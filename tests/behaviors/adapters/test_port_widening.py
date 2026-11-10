"""V16: the widened model port (sampling, output mode, system, messages, logprobs, tools, retry, timing).

No live model call: the deterministic adapter, fake ``claude``/``codex`` executables, a loopback
``http.server`` standing in for OpenRouter and OpenAI, and an ``httpx.MockTransport`` Ollama.
"""

from __future__ import annotations

from dataclasses import fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import stat
import threading
import uuid

import httpx
import pytest

from gigai.adapters.claude_cli import ClaudeCLIAdapter
from gigai.adapters.codex_cli import CodexCLIAdapter
from gigai.adapters.deterministic import DeterministicAdapter
from gigai.adapters.factory import ModelAdapterBinding, resolve_model_adapter
from gigai.adapters.openai_api import OpenAIAPIAdapter
from gigai.adapters.openrouter_api import OpenRouterAPIAdapter
from gigai.adapters.port import (
    REQUEST_SETTINGS,
    InvocationRequest,
    InvocationResult,
    ModelInvocationError,
    NormalizedUsage,
    RetryPolicy,
    UnsupportedRequestFieldError,
)
from gigai.canonical import digest_imported_bytes, parse_json_bytes
from gigai.config import CredentialReference, Endpoint, ModelTarget
from gigai.lifecycle import approve_offline, create_offline
from gigai.model_execution import InvocationPolicy, ModelExecutionError, SelectedReference, run_model_invocation
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad
from tests.behaviors.runtime_model_boundary.test_ollama_local_adapter import (
    _adapter as _ollama_adapter,
    _identity_handler as _ollama_handler,
    _request as _ollama_request,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "adapters" / "request_artifact_pre_v16.json"
RUN_ID = "run_00000000-0000-4000-8000-000000000161"
GOAL_ID = "goal_00000000-0000-4000-8000-000000000162"
REFERENCE_ID = "ref_00000000-0000-4000-8000-000000000163"
SCHEMA = {"type": "object", "properties": {"fit": {"type": "string"}}, "required": ["fit"]}
TOOL = {"type": "function", "function": {"name": "lookup_posting", "parameters": {"type": "object"}}}
#: ``claude -p`` argv for a default request, as 0.1.11.13 built it (``claude_cli.py`` ``argv``).
CLAUDE_PLAN_ARGV_TAIL = [
    "-p",
    "--output-format",
    "json",
    "--no-session-persistence",
    "--permission-mode",
    "plan",
    "--tools",
    "",
    "--setting-sources",
    "",
    "--strict-mcp-config",
]


def _request(**settings: object) -> InvocationRequest:
    return InvocationRequest(
        target_name="offline-default",
        endpoint_name="offline",
        model="fixture-v1",
        role="reviewer",
        prompt="Summarise the synthetic source.",
        target_capabilities=frozenset({"text"}),
        **settings,  # type: ignore[arg-type]
    )


# --- G18 request/response artifacts ---------------------------------------------------------------


class _AnyPrompt(dict):
    """The deterministic adapter's response table, answering every prompt."""

    def __missing__(self, key: str) -> str:
        return "canned synthetic answer"


class _PlainPort:
    def invoke(self, request: InvocationRequest) -> InvocationResult:
        return InvocationResult("success", "synthetic answer", request.model, {}, NormalizedUsage(None, None, None), "unavailable")


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: object, settings: dict[str, object] | None = None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    home, target = tmp_path / "home", tmp_path / "target"
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
    created = create_offline(home_root=home, requested_target=target, name="v16-baseline", open_editor=False, uuid_factory=lambda: next(values))
    approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)
    actual = resolve_model_adapter(config, "offline-default")
    monkeypatch.setattr("gigai.model_execution.resolve_model_adapter", lambda _config, _target: ModelAdapterBinding(actual.current, port))
    content = b"synthetic source text\n"
    execution = run_model_invocation(
        resolved=resolved,
        config=config,
        run_id=RUN_ID,
        goal_id=GOAL_ID,
        model_target="offline-default",
        role="reviewer",
        prompt="Summarise the synthetic source.",
        references=(SelectedReference(REFERENCE_ID, "references/source.txt", content, digest_imported_bytes(content)),),
        selected_reference_ids=(REFERENCE_ID,),
        policy=InvocationPolicy(allowed_reference_ids=frozenset({REFERENCE_ID}), offline=True),
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000164"),
        request_settings=settings,
    )
    assert execution.record["outcome"] == "succeeded", execution.record
    directory = resolved.path / "runs" / RUN_ID / "model-invocations" / execution.record["invocation_id"]
    return execution, directory


def _deterministic(monkeypatch: pytest.MonkeyPatch) -> DeterministicAdapter:
    adapter = DeterministicAdapter()
    monkeypatch.setattr(adapter, "_responses", _AnyPrompt())
    return adapter


def test_a_default_request_leaves_every_new_field_unset() -> None:
    request = _request()

    assert all(getattr(request, name) is None for name in REQUEST_SETTINGS)
    assert {field.name for field in fields(InvocationRequest)} >= set(REQUEST_SETTINGS)


def test_a_default_request_artifact_is_byte_identical_to_the_pre_v16_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution, directory = _run(tmp_path, monkeypatch, _PlainPort())

    before = FIXTURE.read_bytes()
    after = (directory / "request.json").read_bytes()
    old, new = parse_json_bytes(before), parse_json_bytes(after)
    # Allowed difference: new keys whose value is null. Today there is none, so the bytes match.
    assert {key: value for key, value in new.items() if key not in old} == {
        key: None for key in new if key not in old
    }
    assert {key: new[key] for key in old} == old
    assert after == before
    assert execution.record["request"]["request_sha256"] == digest_imported_bytes(before)


def test_settings_round_trip_into_the_artifacts_and_the_request_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = {"temperature": 0.3, "seed": 7, "output_mode": "schema", "schema": SCHEMA}

    first, first_dir = _run(tmp_path / "a", monkeypatch, _deterministic(monkeypatch), settings)
    second, _ = _run(tmp_path / "b", monkeypatch, _deterministic(monkeypatch), settings)
    warmer, _ = _run(tmp_path / "c", monkeypatch, _deterministic(monkeypatch), {**settings, "temperature": 0.4})

    request = parse_json_bytes((first_dir / "request.json").read_bytes())
    assert request["settings"] == {
        "temperature": "0.3",
        "seed": 7,
        "output_mode": "schema",
        "schema_sha256": request["settings"]["schema_sha256"],
    }
    assert request["settings"]["schema_sha256"].startswith("sha256:")
    response = parse_json_bytes((first_dir / "response.json").read_bytes())
    assert response["raw_usage"]["echo"] == {"temperature": "0.3", "seed": 7, "output_mode": "schema", "schema": SCHEMA}
    assert response["adapter_version"] == "deterministic:2"
    assert type(response["latency_ms"]) is int and response["latency_ms"] > 0
    sha = first.record["request"]["request_sha256"]
    assert second.record["request"]["request_sha256"] == sha
    assert warmer.record["request"]["request_sha256"] != sha
    assert sha != digest_imported_bytes(FIXTURE.read_bytes())


def test_the_g18_seam_refuses_text_settings_and_unknown_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ModelExecutionError, match="carry text"):
        _run(tmp_path / "a", monkeypatch, _PlainPort(), {"system": "be brief"})
    with pytest.raises(ModelExecutionError, match="unknown request settings: warmth"):
        _run(tmp_path / "b", monkeypatch, _PlainPort(), {"warmth": 1})


def test_deterministic_returns_canned_tool_calls_logprobs_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(
        messages=[{"role": "user", "content": "earlier turn"}, {"role": "assistant", "content": "earlier answer"}],
        tools=[TOOL],
        logprobs=True,
        top_logprobs=2,
    )

    result = _deterministic(monkeypatch).invoke(request)

    assert result.tool_calls == ({"id": "call_offline_1", "type": "function", "function": {"name": "lookup_posting", "arguments": "{}"}},)
    assert result.logprobs == ({"token": "canned synthetic answer", "logprob": 0.0},)
    assert (result.cost_usd, result.cost_status) == (0.0, "provider_reported")
    assert result.capabilities >= set(REQUEST_SETTINGS)
    assert result.started_at.endswith("Z") and result.completed_at.endswith("Z")


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"temperature": 2.5}, "temperature"),
        ({"top_p": 0}, "top_p"),
        ({"seed": True}, "seed"),
        ({"output_mode": "yaml"}, "output_mode"),
        ({"output_mode": "schema"}, "schema"),
        ({"schema": SCHEMA}, "schema"),
        ({"top_logprobs": 3}, "top_logprobs"),
        ({"tool_choice": "auto"}, "tool_choice"),
        ({"messages": [{"role": "tool", "content": "x"}]}, "messages"),
        ({"system": ""}, "system"),
    ],
)
def test_invalid_settings_are_refused_at_construction(settings: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _request(**settings)


def test_retry_policy_bounds() -> None:
    with pytest.raises(ValueError, match="attempts"):
        RetryPolicy(attempts=0)
    with pytest.raises(ValueError, match="backoff"):
        RetryPolicy(attempts=2, backoff_seconds=-1)


# --- claude / codex (text-only CLIs) --------------------------------------------------------------


def _fake_claude(directory: Path) -> tuple[Path, Path]:
    """``directory/claude``: answers the probe, records argv (NUL-separated) and stdin of each call."""

    directory.mkdir(parents=True, exist_ok=True)
    record, stdin = directory / "claude.argv", directory / "claude.stdin"
    answer = json.dumps({"type": "result", "subtype": "success", "result": "fake answer", "usage": {"input_tokens": 3, "output_tokens": 2}, "total_cost_usd": 0.0})
    executable = directory / "claude"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--help" ]; then echo "--setting-sources --strict-mcp-config --tools"; exit 0; fi\n'
        f"for arg in \"$@\"; do printf '%s\\0' \"$arg\"; done >> '{record}'\n"
        f"/bin/cat > '{stdin}'\n"
        f"printf '%s' '{answer}'\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return record, stdin


def _argv_calls(record: Path) -> list[str]:
    return record.read_bytes().decode("utf-8").split("\0")[:-1] if record.exists() else []


def test_claude_keeps_todays_argv_and_records_sampling_as_unsupported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record, stdin = _fake_claude(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    adapter = ClaudeCLIAdapter()

    plain = adapter.invoke(_request())
    plain_argv = _argv_calls(record)
    record.unlink()
    sampled = adapter.invoke(_request(temperature=0.2, top_p=0.9, seed=7))

    assert plain_argv == [*CLAUDE_PLAN_ARGV_TAIL, "--model", "fixture-v1"]
    assert _argv_calls(record) == plain_argv
    assert stdin.read_text(encoding="utf-8") == "Summarise the synthetic source."
    assert "unsupported" not in plain.raw_usage
    assert sampled.raw_usage["unsupported"] == ["temperature", "top_p", "seed"]
    assert sampled.cost_status == "provider_reported"
    assert sampled.adapter_version == "claude_cli:2" and sampled.latency_ms > 0


def test_claude_puts_system_and_json_mode_in_the_prompt_and_checks_the_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, stdin = _fake_claude(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")

    with pytest.raises(ModelInvocationError, match="not JSON") as caught:
        ClaudeCLIAdapter().invoke(_request(system="You grade postings.", output_mode="json"))

    assert caught.value.code == "model_output_not_json"
    sent = stdin.read_text(encoding="utf-8")
    assert sent.startswith("You grade postings.\n\nSummarise the synthetic source.\n\n")
    assert "JSON" in sent


@pytest.mark.parametrize("settings", [{"tools": [TOOL]}, {"logprobs": True}, {"messages": [{"role": "user", "content": "hi"}]}])
def test_text_only_clis_refuse_tools_logprobs_and_messages_before_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: dict[str, object]
) -> None:
    record, _ = _fake_claude(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")

    with pytest.raises(UnsupportedRequestFieldError) as claude_error:
        ClaudeCLIAdapter().invoke(_request(**settings))
    with pytest.raises(UnsupportedRequestFieldError) as codex_error:
        CodexCLIAdapter(executable=str(tmp_path / "no-such-codex")).invoke(_request(**settings))

    assert claude_error.value.code == "model_request_field_unsupported"
    assert claude_error.value.fields == codex_error.value.fields == tuple(settings)
    assert not record.exists()


# --- OpenRouter / OpenAI over a loopback http.server ----------------------------------------------


class _Provider:
    """A loopback stand-in for a provider: records each JSON body, answers from ``replies`` in order."""

    def __init__(self, replies: list[tuple[int, dict[str, object]]]) -> None:
        self.bodies: list[dict[str, object]] = []
        self.paths: list[str] = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's name
                provider.paths.append(self.path)
                provider.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                status, body = replies[min(len(provider.bodies), len(replies)) - 1]
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args: object) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


_CHAT_REPLY = {
    "model": "fake/model-1",
    "choices": [{"message": {"role": "assistant", "content": '{"fit": "strong"}'}, "logprobs": {"content": [{"token": "{", "logprob": -0.1}]}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "cost": 0.0001},
}


def _openrouter(provider: _Provider) -> OpenRouterAPIAdapter:
    return OpenRouterAPIAdapter(
        credential=CredentialReference("openrouter", "environment", "V16_TEST_TOKEN"),
        base_url=provider.base_url,
        credential_resolver=lambda _reference: "test-token",
        client=httpx.Client(trust_env=False),
    )


def _remote_request(**settings: object) -> InvocationRequest:
    return InvocationRequest(
        target_name="remote-default",
        endpoint_name="remote",
        model="fake/model-1",
        role="reviewer",
        prompt="Summarise the synthetic source.",
        target_capabilities=frozenset({"text"}),
        **settings,  # type: ignore[arg-type]
    )


def test_openrouter_receives_the_sampling_and_output_settings() -> None:
    provider = _Provider([(200, _CHAT_REPLY)])
    try:
        result = _openrouter(provider).invoke(
            _remote_request(temperature=0.3, top_p=0.9, seed=7, output_mode="schema", schema=SCHEMA, system="You grade postings.", logprobs=True, top_logprobs=1)
        )
    finally:
        provider.close()

    [body] = provider.bodies
    assert provider.paths == ["/chat/completions"]
    assert (body["temperature"], body["top_p"], body["seed"]) == (0.3, 0.9, 7)
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "gigai_output", "strict": True, "schema": SCHEMA}}
    assert body["messages"] == [{"role": "system", "content": "You grade postings."}, {"role": "user", "content": "Summarise the synthetic source."}]
    assert (body["logprobs"], body["top_logprobs"]) == (True, 1)
    assert result.latency_ms > 0 and result.adapter_version == "openrouter_api:2"
    assert (result.cost_usd, result.cost_status) == (0.0001, "provider_reported")
    assert result.logprobs == ({"token": "{", "logprob": -0.1},)
    assert result.retry_count == 0 and "unsupported" not in result.raw_usage


def test_openrouter_default_payload_is_unchanged() -> None:
    provider = _Provider([(200, _CHAT_REPLY)])
    try:
        _openrouter(provider).invoke(_remote_request())
    finally:
        provider.close()

    assert provider.bodies == [
        {"model": "fake/model-1", "messages": [{"role": "user", "content": "Summarise the synthetic source."}], "max_tokens": 64}
    ]


def test_openrouter_returns_tool_calls_without_text() -> None:
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "lookup_posting", "arguments": "{}"}}
    reply = {"model": "fake/model-1", "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [tool_call]}}], "usage": {}}
    provider = _Provider([(200, reply)])
    try:
        result = _openrouter(provider).invoke(_remote_request(tools=[TOOL], tool_choice="auto"))
    finally:
        provider.close()

    assert provider.bodies[0]["tools"] == [TOOL] and provider.bodies[0]["tool_choice"] == "auto"
    assert result.tool_calls == (tool_call,) and result.output_text == ""


def test_retry_runs_only_when_a_policy_asks_for_it() -> None:
    replies = [(503, {"error": "busy"}), (200, _CHAT_REPLY)]
    once = _Provider(replies)
    try:
        with pytest.raises(ModelInvocationError):
            _openrouter(once).invoke(_remote_request())
    finally:
        once.close()
    retried = _Provider(replies)
    try:
        result = _openrouter(retried).invoke(_remote_request(retry_policy=RetryPolicy(attempts=2)))
    finally:
        retried.close()

    assert len(once.bodies) == 1
    assert len(retried.bodies) == 2
    assert (result.retry_count, result.last_error_class) == (1, "ModelInvocationError")


def test_openai_maps_settings_and_records_seed_as_unsupported() -> None:
    reply = {"model": "gpt-fake", "output_text": '{"fit": "strong"}', "usage": {"input_tokens": 5, "output_tokens": 2}}
    provider = _Provider([(200, reply)])
    try:
        result = OpenAIAPIAdapter(
            credential=CredentialReference("openai", "environment", "V16_TEST_TOKEN"),
            base_url=provider.base_url,
            credential_resolver=lambda _reference: "test-token",
            client=httpx.Client(trust_env=False),
        ).invoke(_remote_request(temperature=0.3, seed=7, output_mode="json", system="You grade postings."))
    finally:
        provider.close()

    [body] = provider.bodies
    assert provider.paths == ["/responses"]
    assert body["temperature"] == 0.3 and "seed" not in body
    assert body["text"] == {"format": {"type": "json_object"}}
    assert body["instructions"] == "You grade postings."
    assert result.raw_usage["unsupported"] == ["seed"]


# --- Ollama (httpx.MockTransport) -----------------------------------------------------------------


def test_ollama_maps_sampling_and_format_and_refuses_tools() -> None:
    calls: list[httpx.Request] = []
    adapter = _ollama_adapter(_ollama_handler(calls))
    base = _ollama_request()
    request = InvocationRequest(
        **{name: getattr(base, name) for name in ("target_name", "endpoint_name", "model", "role", "prompt", "target_capabilities", "max_output_tokens", "reasoning_effort")},
        temperature=0.3,
        seed=7,
        output_mode="json",
        retry_policy=RetryPolicy(attempts=2),
    )
    try:
        result = adapter.invoke(request)
        with pytest.raises(UnsupportedRequestFieldError):
            adapter.invoke(InvocationRequest(**{**request.__dict__, "tools": [TOOL]}))
    finally:
        adapter.close()

    [chat] = [json.loads(call.content) for call in calls if call.url.path == "/api/chat"]
    assert (chat["options"]["temperature"], chat["options"]["seed"], chat["format"]) == (0.3, 7, "json")
    assert result.raw_usage["unsupported"] == ["retry_policy"]
    assert result.adapter_version == "ollama_local:2"
