"""SCOPE-ADD-3 B1: the Claude CLI adapter's lean mode (the model ranker's call mode).

The ranking spike measured that ``--permission-mode plan`` silently ignores
``--model`` and that the default system prompt + settings add ~31.8k input
tokens per call. Lean mode drops plan mode, honours ``--model``, passes
``--effort`` and loads no settings/MCP/slash commands; the default (assess)
argv is pinned unchanged.
"""

from __future__ import annotations

import json

from gigai.adapters.claude_cli import LEAN_SYSTEM_PROMPT, ClaudeCLIAdapter, _parse_claude_json
from gigai.adapters.port import InvocationRequest

_EXE = "/opt/fake/claude"


def _request(model: str = "sonnet", effort: str | None = None) -> InvocationRequest:
    return InvocationRequest(
        target_name="claude-default",
        endpoint_name="claude",
        model=model,
        role="reviewer",
        prompt="Rank these.",
        target_capabilities=frozenset({"text"}),
        reasoning_effort=effort,
    )


def _value(argv: tuple[str, ...], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_lean_argv_honours_model_and_never_uses_plan_mode() -> None:
    argv = ClaudeCLIAdapter(executable=_EXE, lean=True).argv(_request(model="haiku", effort="low"))

    assert "--permission-mode" not in argv and "plan" not in argv
    assert _value(argv, "--model") == "haiku"
    assert _value(argv, "--tools") == ""
    assert _value(argv, "--effort") == "low"
    assert _value(argv, "--system-prompt") == LEAN_SYSTEM_PROMPT
    assert _value(argv, "--setting-sources") == ""
    assert "--strict-mcp-config" in argv and "--disable-slash-commands" in argv
    assert argv[:5] == (_EXE, "-p", "--output-format", "json", "--no-session-persistence")


def test_lean_argv_exact() -> None:
    assert ClaudeCLIAdapter(executable=_EXE, lean=True).argv(_request(model="sonnet", effort="low")) == (
        _EXE, "-p", "--output-format", "json", "--no-session-persistence",
        "--tools", "", "--system-prompt", LEAN_SYSTEM_PROMPT, "--setting-sources", "",
        "--strict-mcp-config", "--disable-slash-commands", "--effort", "low", "--model", "sonnet",
    )


def test_lean_effort_defaults_to_low_and_passes_a_supported_one_through() -> None:
    adapter = ClaudeCLIAdapter(executable=_EXE, lean=True)
    assert _value(adapter.argv(_request(effort=None)), "--effort") == "low"
    assert _value(adapter.argv(_request(effort="none")), "--effort") == "low"
    assert _value(adapter.argv(_request(effort="high")), "--effort") == "high"


def test_lean_default_model_sends_no_model_flag() -> None:
    assert "--model" not in ClaudeCLIAdapter(executable=_EXE, lean=True).argv(_request(model="default"))


def test_default_assess_argv_is_unchanged() -> None:
    adapter = ClaudeCLIAdapter(executable=_EXE)
    assert not adapter.lean
    assert adapter.argv(_request(model="sonnet", effort="low")) == (
        _EXE, "-p", "--output-format", "json", "--no-session-persistence",
        "--permission-mode", "plan", "--tools", "", "--setting-sources", "", "--strict-mcp-config",
        "--model", "sonnet",
    )
    assert adapter.argv(_request(model="default")) == (
        _EXE, "-p", "--output-format", "json", "--no-session-persistence",
        "--permission-mode", "plan", "--tools", "", "--setting-sources", "", "--strict-mcp-config",
    )


def test_lean_copy_keeps_executable_and_switches_mode() -> None:
    adapter = ClaudeCLIAdapter(executable=_EXE, timeout_seconds=45.0)
    lean = adapter.lean_copy()
    assert lean.lean and not adapter.lean
    assert lean.argv(_request())[0] == _EXE


def test_lean_parse_names_the_model_that_ran_from_model_usage() -> None:
    stdout = json.dumps({
        "type": "result", "subtype": "success", "result": "[]",
        "usage": {"input_tokens": 10, "output_tokens": 4},
        "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 10}},
    })
    assert _parse_claude_json(stdout, "sonnet", model_usage_fallback=True)[1] == "claude-sonnet-5-5"
    # the assess path keeps its behaviour: the requested model when no ``model`` field
    assert _parse_claude_json(stdout, "sonnet")[1] == "sonnet"
