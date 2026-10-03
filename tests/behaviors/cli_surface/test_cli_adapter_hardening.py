"""0110-004: every Scout CLI call runs with the shell tool, memories and user config off.

0110-8-07 adds web search, MCP servers and every other tool feature: ``test_tool_surface_off.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from gigai.adapters.claude_cli import ClaudeCLIAdapter
from gigai.adapters.cli_probe import CodexLockdown, codex_lockdown, reset_probe_cache
from gigai.adapters.codex_cli import CodexCLIAdapter
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
    argv = adapter.argv(_request(model=model, effort="low"), _DIR, CodexLockdown())

    assert _pairs(argv, "--disable") == ["shell_tool", "memories"]
    assert 'web_search="disabled"' in _pairs(argv, "-c")  # 0110-8-07: the default is "cached", which is ON
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


@pytest.fixture(autouse=True)
def _canned_cli_probe() -> None:
    reset_probe_cache()


_CODEX_FEATURES = "shell_tool                              stable             true\nmemories                                stable             false\n"
_CLAUDE_HELP = "  --setting-sources <s>\n  --strict-mcp-config\n  --tools <tools...>\n"


def _fake(tmp_path: Path, name: str, probe_args: str, probe_out: str, probe_exit: int = 0, rest: str = "") -> Path:
    """A fake CLI: answers the probe from canned text, otherwise runs ``rest``. No model."""

    exe = tmp_path / name
    log = tmp_path / f"{name}.calls"
    exe.write_text(
        f"#!/bin/sh\necho \"$@\" >> {log}\n"
        f"if [ \"$*\" = \"{probe_args}\" ]; then printf '%s' '{probe_out}'; exit {probe_exit}; fi\n"
        # 0110-8-07: codex is asked for its MCP servers before every call; this one has none
        f"if [ \"$1 $2\" = \"mcp list\" ]; then echo '[]'; exit 0; fi\n"
        f"if [ \"$*\" = \"debug models\" ]; then echo '{{\"models\": []}}'; exit 0; fi\n{rest}\n"
    )
    exe.chmod(0o755)
    return exe


_CODEX_OK = '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}'


def test_codex_probe_passes_and_runs_once_per_process(tmp_path: Path) -> None:
    exe = _fake(tmp_path, "codex", "features list", _CODEX_FEATURES, rest=f"cat >/dev/null; echo '{_CODEX_OK}'")
    adapter = CodexCLIAdapter(executable=str(exe))
    adapter.invoke(_request())
    adapter.invoke(_request())
    calls = (tmp_path / "codex.calls").read_text().splitlines()
    # the feature list is read once per process; the MCP list and the model catalog before EVERY call (0110-8-07)
    assert calls.count("features list") == 1 and calls.count("debug models") == 2 and len(calls) == 7
    assert [call for call in calls if call.startswith("mcp list")] == ["mcp list --json --disable shell_tool --disable memories"] * 2


def test_codex_missing_memories_fails_closed_naming_it(tmp_path: Path) -> None:
    exe = _fake(tmp_path, "codex", "features list", "shell_tool stable true\n")
    with pytest.raises(ModelInvocationError, match="memories"):
        CodexCLIAdapter(executable=str(exe)).invoke(_request())
    assert (tmp_path / "codex.calls").read_text().splitlines() == ["features list"]


def test_codex_probe_command_failing_fails_closed(tmp_path: Path) -> None:
    exe = _fake(tmp_path, "codex", "features list", "", probe_exit=2)
    with pytest.raises(ModelInvocationError, match="could not check"):
        CodexCLIAdapter(executable=str(exe)).invoke(_request())
    assert (tmp_path / "codex.calls").read_text().splitlines() == ["features list"]


def test_claude_probe_passes_and_runs_once_per_process(tmp_path: Path) -> None:
    ok = '{"result":"ok","subtype":"success"}'
    exe = _fake(tmp_path, "claude", "--help", _CLAUDE_HELP, rest=f"cat >/dev/null; echo '{ok}'")
    adapter = ClaudeCLIAdapter(executable=str(exe))
    adapter.invoke(_request())
    adapter.invoke(_request())
    calls = (tmp_path / "claude.calls").read_text().splitlines()
    assert calls.count("--help") == 1 and len(calls) == 3


@pytest.mark.parametrize("flag", ["--setting-sources", "--strict-mcp-config", "--tools"])
def test_claude_missing_flag_fails_closed_naming_it(tmp_path: Path, flag: str) -> None:
    exe = _fake(tmp_path, "claude", "--help", _CLAUDE_HELP.replace(flag, "--other"))
    with pytest.raises(ModelInvocationError, match=flag):
        ClaudeCLIAdapter(executable=str(exe)).invoke(_request())
    assert (tmp_path / "claude.calls").read_text().splitlines() == ["--help"]


def test_claude_probe_command_failing_fails_closed(tmp_path: Path) -> None:
    exe = _fake(tmp_path, "claude", "--help", "", probe_exit=1)
    with pytest.raises(ModelInvocationError, match="could not check"):
        ClaudeCLIAdapter(executable=str(exe)).invoke(_request())


def test_codex_unknown_flag_at_call_time_still_fails_closed(tmp_path: Path) -> None:
    exe = _fake(tmp_path, "codex", "features list", _CODEX_FEATURES, rest="echo \"error: unexpected argument '--disable' found\" >&2; exit 2")
    with pytest.raises(ModelInvocationError, match="upgrade codex"):
        CodexCLIAdapter(executable=str(exe)).invoke(_request())


def test_claude_unknown_flag_at_call_time_still_fails_closed(tmp_path: Path) -> None:
    exe = _fake(tmp_path, "claude", "--help", _CLAUDE_HELP, rest="echo \"error: unknown option '--strict-mcp-config'\" >&2; exit 1")
    with pytest.raises(ModelInvocationError, match="upgrade Claude Code"):
        ClaudeCLIAdapter(executable=str(exe)).invoke(_request())


def _live_summary(name: str, events: list[dict]) -> None:
    """Print a secret-free event summary (types only, plus the reply text) for the evidence file."""

    kinds = [(e.get("type"), (e.get("item") or {}).get("type")) for e in events]
    print(f"\nLIVE-{name}-EVENTS {kinds}")


@pytest.mark.skipif(
    os.environ.get("GIGAI_LIVE_CODEX_HARDENING") != "1" or shutil.which("codex") is None,
    reason="live model call: set GIGAI_LIVE_CODEX_HARDENING=1 with a logged-in codex to run",
)
def test_live_hardened_codex_call_runs_zero_commands(tmp_path: Path) -> None:
    import json

    adapter = CodexCLIAdapter(timeout_seconds=180.0)
    request = InvocationRequest(**{**_request().__dict__, "prompt": "Reply with the word ok"})
    from gigai.adapters.cli_probe import require_codex_capabilities

    reset_probe_cache()
    require_codex_capabilities(adapter._executable)  # the real probe, no model
    argv = adapter.argv(request, str(tmp_path), codex_lockdown(adapter._executable))
    done = subprocess.run(argv, input=request.prompt, capture_output=True, text=True, cwd=tmp_path, timeout=200)
    events = [json.loads(line) for line in done.stdout.splitlines() if line.strip().startswith("{")]
    _live_summary("CODEX", events)
    commands = [e for e in events if (e.get("item") or {}).get("type") in {"command_execution", "mcp_tool_call", "web_search", "collab_tool_call"}]
    assert done.returncode == 0 and commands == []


@pytest.mark.skipif(
    os.environ.get("GIGAI_LIVE_CLAUDE_HARDENING") != "1" or shutil.which("claude") is None,
    reason="live model call: set GIGAI_LIVE_CLAUDE_HARDENING=1 with a logged-in claude to run",
)
def test_live_hardened_claude_call_uses_no_tools(tmp_path: Path) -> None:
    import json

    from gigai.adapters.cli_probe import require_claude_capabilities

    adapter = ClaudeCLIAdapter(timeout_seconds=180.0)
    request = InvocationRequest(**{**_request().__dict__, "prompt": "Reply with the word ok"})
    reset_probe_cache()
    require_claude_capabilities(adapter._executable)  # the real probe, no model
    argv = adapter.argv(request)
    done = subprocess.run(
        argv, input=request.prompt, capture_output=True, text=True, cwd=tmp_path, timeout=200,
        env={k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "USER", "TMPDIR", "LANG")},
    )
    payload = json.loads(done.stdout)
    print(f"\nLIVE-CLAUDE-KEYS {sorted(payload)} subtype={payload.get('subtype')} is_error={payload.get('is_error')} "
          f"num_turns={payload.get('num_turns')} result={payload.get('result')!r} "
          f"permission_denials={payload.get('permission_denials')} modelUsage={sorted(payload.get('modelUsage') or {})}")
    assert done.returncode == 0 and payload.get("is_error") is False
    assert payload.get("permission_denials") in ([], None) and payload.get("num_turns") == 1
