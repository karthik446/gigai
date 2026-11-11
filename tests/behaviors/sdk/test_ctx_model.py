"""BK2: `ctx.model(step_id, input)` calls the model target the step is bound to.

The fixture gig `tests/fixtures/gigs/eval-fixture/` binds `answer` to `deterministic-a` and `judge` to
`deterministic-b` (with `temperature = 0.0`); its responses.json gives each target its own canned answer. Targets
live in a scratch home's config.toml. `claude` is a shell script on PATH and OpenRouter a loopback HTTPS server
answering with a fake key: no network beyond 127.0.0.1, no paid call.
"""

from __future__ import annotations

import importlib
import json
import logging
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import threading
import tomllib
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gigai.config import CredentialReference, Endpoint, ModelTarget, Profile, load_config
from gigai.evals.records import PromptRef, evals_db_path, open_evals_db
from gigai.sdk import GigContext, StepTargetError
from gigai.setup import build_config, run_setup
from gigai.steps import ROLES, StepsFileError, resolve_target, set_override, steps_path
from tests.behaviors.gigs.test_loader import FIXTURES

GIG = "eval-fixture"
PACKAGE = FIXTURES / GIG / "gig_eval_fixture"
MANIFEST = tomllib.loads((PACKAGE / "gig.toml").read_text(encoding="utf-8"))
KEY_ENV = "BK2_FAKE_OPENROUTER_KEY"
FAKE_KEY = "sk-or-v1-bk2-fake-key-do-not-use-0000"
A_TEXT, B_TEXT = "target A says 4", "target B says four"
CLAUDE_TEXT, OPENROUTER_TEXT = "claude fake says 4", "openrouter fake says 4"


def make_home(home: Path, *, openrouter_url: str = "https://127.0.0.1:9/api/v1", profile: Profile | None = None) -> None:
    """A scratch gigai home: deterministic targets A and B, a claude CLI target and an OpenRouter target."""

    def offline(name: str, model: str) -> ModelTarget:
        return ModelTarget(name, "offline", model, ("text",), 64)

    run_setup(
        build_config(
            home_root=home,
            workpad_root=home / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
            credentials=(CredentialReference("openrouter", "environment", KEY_ENV),),
            endpoints=(
                Endpoint("offline", "deterministic"),
                Endpoint("claude", "claude_cli"),
                Endpoint("openrouter", "openrouter_api", credential="openrouter", base_url=openrouter_url),
            ),
            model_targets=(
                offline("offline-default", "fixture-v1"),
                offline("deterministic-a", "fixture-a"),
                offline("deterministic-b", "fixture-b"),
                ModelTarget("claude-cli", "claude", "sonnet", ("text",), 64),
                ModelTarget("openrouter:fake", "openrouter", "fake/model-1", ("text",), 64),
            ),
            profiles=(profile or Profile("default", planner="deterministic-a", critic="deterministic-b", adjudicator="offline-default"),),
        )
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    make_home(tmp_path / "home")
    return tmp_path / "home"


@pytest.fixture
def ctx(home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[GigContext]:
    """The fixture gig's context; its package is importable so `ctx.model` finds responses.json and the prompts."""

    monkeypatch.syspath_prepend(str(FIXTURES / GIG))
    importlib.import_module("gig_eval_fixture")
    yield GigContext(home, GIG, MANIFEST, logging.getLogger("gigai.gig.eval-fixture"))
    sys.modules.pop("gig_eval_fixture", None)


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any socket connect to a non-loopback address fails the test."""

    connect = socket.socket.connect

    def guarded(self: socket.socket, address: object) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            assert address[0] in ("127.0.0.1", "::1"), f"network call to {address!r}"  # type: ignore[index]
        return connect(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded)


def _traces(home: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(evals_db_path(GIG, home))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM traces ORDER BY recorded_at")]
    finally:
        connection.close()


def _fake_claude(directory: Path) -> Path:
    """``directory/claude``: answers the capability probe and records each call's argv, NUL-separated."""

    directory.mkdir(parents=True, exist_ok=True)
    record = directory / "claude.argv"
    answer = json.dumps(
        {"type": "result", "subtype": "success", "result": CLAUDE_TEXT, "usage": {"input_tokens": 3, "output_tokens": 2}, "total_cost_usd": 0.0}
    )
    executable = directory / "claude"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--help" ]; then echo "--setting-sources --strict-mcp-config --tools"; exit 0; fi\n'
        f"for arg in \"$@\"; do printf '%s\\0' \"$arg\"; done >> '{record}'\n"
        "/bin/cat > /dev/null\n"
        f"printf '%s' '{answer}'\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return record


class _OpenRouter:
    """A loopback HTTPS stand-in for OpenRouter (self-signed cert for 127.0.0.1); records each request."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.cert, key = directory / "cert.pem", directory / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(self.cert), "-days", "1",
             "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
            check=True,
            capture_output=True,
        )
        self.requests: list[tuple[str, dict[str, str], dict[str, object]]] = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's name
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                provider.requests.append((self.path, dict(self.headers), body))
                reply = {
                    "model": "fake/model-1",
                    "choices": [{"message": {"role": "assistant", "content": OPENROUTER_TEXT}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
                }
                encoded = json.dumps(reply).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args: object) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, key)
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self.base_url = f"https://127.0.0.1:{self._server.server_address[1]}/api/v1"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def test_each_step_calls_its_own_target(ctx: GigContext, home: Path) -> None:
    answer, judge = ctx.model("answer", "2+2"), ctx.model("judge", "2+2")

    assert (answer.output_text, answer.resolved_model) == (A_TEXT, "fixture-a")
    assert (judge.output_text, judge.resolved_model) == (B_TEXT, "fixture-b")
    # No evals.sqlite: nothing traced, and no gig folder created.
    assert not (home / "gigs" / GIG).exists()


def test_an_undeclared_step_is_a_key_error_naming_the_manifest_field(ctx: GigContext) -> None:
    with pytest.raises(KeyError, match=r"\[steps\]\.summarise"):
        ctx.model("summarise", "2+2")


def test_calls_are_traced_with_prompt_model_tokens_and_latency_once_evals_sqlite_exists(ctx: GigContext, home: Path) -> None:
    open_evals_db(GIG, home).close()

    ctx.model("answer", "2+2")
    ctx.model("judge", "2+2")

    rows = _traces(home)
    assert len(rows) == 2
    answer, judge = sorted(rows, key=lambda row: str(row["step_id"]))
    prompt = PromptRef.of("answer", "1", (PACKAGE / "prompts" / "answer.txt").read_text(encoding="utf-8"))
    assert (answer["prompt_id"], answer["prompt_version"], answer["prompt_sha256"]) == (prompt.id, prompt.version, prompt.sha256)
    assert (answer["model_id"], answer["model_provider"], answer["model_name"]) == ("deterministic-a", "deterministic", "fixture-a")
    assert (answer["output_raw_text"], judge["output_raw_text"]) == (A_TEXT, B_TEXT)
    assert (judge["model_id"], judge["temperature"]) == ("deterministic-b", 0.0)
    assert json.loads(str(answer["argv"])) == []
    assert answer["latency_ms"] is not None and {"input_tokens", "output_tokens"} <= set(json.loads(str(answer["usage"])))


def test_steps_mix_a_claude_cli_target_and_an_openrouter_target_without_a_real_call(
    ctx: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _fake_claude(tmp_path / "bin")
    provider = _OpenRouter(tmp_path / "tls")
    try:
        make_home(home, openrouter_url=provider.base_url)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
        monkeypatch.setenv(KEY_ENV, FAKE_KEY)
        monkeypatch.setenv("SSL_CERT_FILE", str(provider.cert))
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name, raising=False)
        set_override(home, GIG, MANIFEST, "answer", "claude-cli")
        set_override(home, GIG, MANIFEST, "judge", "openrouter:fake")
        open_evals_db(GIG, home).close()

        answer, judge = ctx.model("answer", "2+2"), ctx.model("judge", "2+2")
    finally:
        provider.close()

    assert (answer.output_text, judge.output_text) == (CLAUDE_TEXT, OPENROUTER_TEXT)
    argv = record.read_bytes().decode("utf-8").split("\0")[:-1]
    assert argv[:1] == ["-p"] and argv[-2:] == ["--model", "sonnet"]
    assert len(provider.requests) == 1
    path, headers, body = provider.requests[0]
    assert path == "/api/v1/chat/completions" and body["model"] == "fake/model-1" and body["temperature"] == 0.0
    assert headers["Authorization"] == f"Bearer {FAKE_KEY}"  # the fake is really reached with the fake key

    rows = {row["step_id"]: row for row in _traces(home)}
    assert json.loads(str(rows["answer"]["argv"]))[1:] == argv
    assert (rows["judge"]["model_id"], rows["judge"]["model_provider"]) == ("openrouter:fake", "openrouter_api")
    tokens = {step: json.loads(str(row["usage"])) for step, row in rows.items()}
    assert (tokens["answer"]["input_tokens"], tokens["judge"]["output_tokens"]) == (3, 4)
    for row in rows.values():
        for column, value in row.items():
            for secret in (FAKE_KEY, "Bearer", "Authorization"):
                assert secret not in str(value), f"{secret!r} in traces.{column}"


@pytest.mark.parametrize("setting", [{"temperature": 0.5}, {"tools": ({"type": "function", "function": {"name": "f"}},)}, {"logprobs": True}])
def test_a_cli_target_refuses_a_setting_it_cannot_carry_before_any_call(
    ctx: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting: dict[str, object]
) -> None:
    record = _fake_claude(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    set_override(home, GIG, MANIFEST, "answer", "claude-cli")
    name = next(iter(setting))

    with pytest.raises(StepTargetError, match=rf"step 'answer' sets '{name}'.*claude_cli target 'claude-cli'"):
        ctx.model("answer", "2+2", **setting)
    assert not record.exists()


def test_a_cli_override_of_a_step_with_a_manifest_setting_is_refused_at_set_and_at_call_time(
    ctx: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _fake_claude(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")

    with pytest.raises(StepTargetError, match=r"step 'judge' sets 'temperature'"):
        set_override(home, GIG, MANIFEST, "judge", "claude-cli")
    assert not steps_path(home, GIG).exists()

    # Hand-written steps.toml skips `steps set`; the call still refuses.
    steps_path(home, GIG).parent.mkdir(parents=True)
    steps_path(home, GIG).write_text('[steps]\njudge = "claude-cli"\n', encoding="utf-8")
    steps_path(home, GIG).chmod(0o600)
    with pytest.raises(StepTargetError, match=r"step 'judge' sets 'temperature'"):
        ctx.model("judge", "2+2")
    assert not record.exists()


FALLBACK = {
    "planner": "deterministic-a",
    "critic": "deterministic-b",
    "adjudicator": "offline-default",
    "reviewer": "deterministic-b",
    "verifier": "offline-default",
    "researcher": "deterministic-a",
    "gig_creator": "deterministic-a",
}


@pytest.mark.parametrize("role", ROLES)
def test_every_role_resolves_through_the_default_profile_with_the_fallbacks(home: Path, role: str) -> None:
    manifest = {"gig": {"name": GIG}, "steps": {"draft": {"role": role}}}
    config = load_config(home)

    by_step = resolve_target(home, GIG, manifest, "draft", config)
    by_role = resolve_target(home, GIG, {"gig": {"name": GIG}}, role, config)

    assert (by_step.id, by_step.source) == (FALLBACK[role], f"role:{role}")
    assert (by_role.id, by_role.source) == (FALLBACK[role], f"role:{role}")


def test_a_profile_role_that_is_set_wins_over_its_fallback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    make_home(home, profile=Profile("default", planner="deterministic-a", critic="deterministic-b", adjudicator="offline-default", reviewer="deterministic-a"))

    assert resolve_target(home, GIG, {}, "reviewer", load_config(home)).id == "deterministic-a"


def test_override_then_manifest_then_role(home: Path) -> None:
    config = load_config(home)
    manifest = {"steps": {"draft": {"role": "critic", "target": "deterministic-a"}}}
    assert resolve_target(home, GIG, manifest, "draft", config).source == "manifest"
    set_override(home, GIG, manifest, "draft", "offline-default")
    assert (resolve_target(home, GIG, manifest, "draft", config).id, resolve_target(home, GIG, manifest, "draft", config).source) == (
        "offline-default",
        "override",
    )


def test_a_step_without_target_or_role_names_both_fields(home: Path) -> None:
    with pytest.raises(KeyError, match=r"\[steps\]\.draft\.target or \[steps\]\.draft\.role"):
        resolve_target(home, GIG, {"steps": {"draft": {"prompt": "p.txt"}}}, "draft", load_config(home))


@pytest.mark.parametrize(
    ("content", "mode", "message"),
    [
        ("[steps\n", 0o600, "is not TOML"),
        ('[steps]\nanswer = "deterministic-b"\n[extra]\nx = 1\n', 0o600, "only a \\[steps\\] table"),
        ("[steps]\nanswer = 3\n", 0o600, "only a \\[steps\\] table"),
        ('[steps]\nanswer = "deterministic-b"\n', 0o666, "writable by group or others"),
    ],
)
def test_a_bad_steps_toml_refuses_the_call(ctx: GigContext, home: Path, content: str, mode: int, message: str) -> None:
    path = steps_path(home, GIG)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)

    with pytest.raises(StepsFileError, match=message):
        ctx.model("answer", "2+2")


def test_a_symlinked_steps_toml_or_gig_folder_is_refused(ctx: GigContext, home: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "steps.toml").write_text('[steps]\nanswer = "deterministic-b"\n', encoding="utf-8")
    (elsewhere / "steps.toml").chmod(0o600)
    path = steps_path(home, GIG)
    path.parent.mkdir(parents=True)
    path.symlink_to(elsewhere / "steps.toml")
    with pytest.raises(StepsFileError, match="symlink"):
        ctx.model("answer", "2+2")
    with pytest.raises(StepsFileError, match="symlink"):
        set_override(home, GIG, MANIFEST, "answer", "deterministic-b")

    path.unlink()
    path.parent.rmdir()
    path.parent.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(StepsFileError, match="symlink"):
        set_override(home, GIG, MANIFEST, "answer", "deterministic-b")
    assert (elsewhere / "steps.toml").read_text(encoding="utf-8") == '[steps]\nanswer = "deterministic-b"\n'
