"""0110-004: every Scout CLI call runs with the shell tool, memories and user config off."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from gigai.adapters.claude_cli import ClaudeCLIAdapter
from gigai.adapters.codex_cli import CODEX_MIN_VERSION, CodexCLIAdapter
from gigai.adapters.port import InvocationRequest, ModelInvocationError

_DIR = "/tmp/gigai-codex-x"


def _request(model: str = "default", effort: str | None = None) -> InvocationRequest:
    return InvocationRequest(
        target_name="t",
        endpoint_name="e",
        model=model,
        role="reviewer",
        prompt="Say hi as JSON.",
        target_capabilities=frozenset({"text"}),
        reasoning_effort=effort,
    )


def _pairs(argv: tuple[str, ...], flag: str) -> list[str]:
    return [argv[i + 1] for i, value in enumerate(argv) if value == flag]


@pytest.mark.parametrize("adapter_kind", ["default", "effort"])
@pytest.mark.parametrize("model", ["default", "gpt-x"])
def test_codex_argv_disables_shell_tool_and_memories_in_every_mode(adapter_kind: str, model: str) -> None:
    adapter = CodexCLIAdapter(executable="/opt/fake/codex")
    if adapter_kind == "effort":
        adapter = adapter.effort_copy()
    argv = adapter.argv(_request(model=model, effort="low"), _DIR)

    assert _pairs(argv, "--disable") == ["shell_tool", "memories"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    # --ignore-user-config would skip config.toml (custom providers / default model): never added
    assert "--ignore-user-config" not in argv
    assert argv[-1] == "-"


@pytest.mark.parametrize("lean", [False, True])
def test_claude_argv_loads_no_settings_no_mcp_and_no_tools_in_every_mode(lean: bool) -> None:
    adapter = ClaudeCLIAdapter(executable="/opt/fake/claude", lean=lean)
    argv = adapter.argv(_request(effort="low"))

    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--tools") + 1] == ""


def test_codex_unknown_flag_fails_closed_with_the_minimum_version(tmp_path: Path) -> None:
    exe = tmp_path / "codex"
    exe.write_text("#!/bin/sh\necho \"error: unexpected argument '--disable' found\" >&2\nexit 2\n")
    exe.chmod(0o755)
    with pytest.raises(ModelInvocationError, match=f"upgrade codex to {CODEX_MIN_VERSION}"):
        CodexCLIAdapter(executable=str(exe)).invoke(_request())


def test_claude_unknown_flag_fails_closed_with_the_minimum_version(tmp_path: Path) -> None:
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\necho \"error: unknown option '--strict-mcp-config'\" >&2\nexit 1\n")
    exe.chmod(0o755)
    with pytest.raises(ModelInvocationError, match="upgrade Claude Code to"):
        ClaudeCLIAdapter(executable=str(exe)).invoke(_request())


@pytest.mark.skipif(
    os.environ.get("GIGAI_LIVE_CODEX_HARDENING") != "1" or shutil.which("codex") is None,
    reason="live model call: set GIGAI_LIVE_CODEX_HARDENING=1 with a logged-in codex to run",
)
def test_live_hardened_codex_call_runs_zero_commands(tmp_path: Path) -> None:
    import json

    adapter = CodexCLIAdapter(timeout_seconds=180.0)
    request = _request()
    request = InvocationRequest(**{**request.__dict__, "prompt": "Use a shell command to list files in ~/.codex/memories, then answer with the JSON {\"ok\": true}."})
    argv = adapter.argv(request, str(tmp_path))
    done = subprocess.run(argv, input=request.prompt, capture_output=True, text=True, cwd=tmp_path, timeout=200)
    events = [json.loads(line) for line in done.stdout.splitlines() if line.strip().startswith("{")]
    commands = [e for e in events if (e.get("item") or {}).get("type") == "command_execution"]
    assert done.returncode == 0 and commands == []
