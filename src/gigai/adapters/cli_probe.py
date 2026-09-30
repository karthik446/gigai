"""Capability probes for the CLI adapters: ask the installed CLI, never compare versions."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from .port import ModelInvocationError
from .process import allowed_environment

_PROBE_TIMEOUT_SECONDS = 20.0
_CACHE: dict[tuple[str, str], None] = {}


def _run_probe(argv: tuple[str, ...]) -> str:
    """Run one no-model probe command; fail closed on any failure."""

    with tempfile.TemporaryDirectory(prefix="gigai-probe-") as directory:
        try:
            done = subprocess.run(
                [*argv],
                cwd=Path(directory),
                env=allowed_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
                shell=False,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModelInvocationError(f"could not check the capabilities of `{' '.join(argv)}`: {exc}") from exc
    if done.returncode != 0:
        raise ModelInvocationError(
            f"could not check the capabilities of `{' '.join(argv)}`: exit code {done.returncode}"
        )
    return done.stdout


def require_codex_capabilities(executable: str) -> None:
    """``codex features list`` must list ``shell_tool`` and ``memories`` (so ``--disable`` can turn them off)."""

    key = ("codex", executable)
    if key in _CACHE:
        return
    output = _run_probe((executable, "features", "list"))
    listed = {line.split()[0] for line in output.splitlines() if line.strip()}
    missing = [name for name in ("shell_tool", "memories") if name not in listed]
    if missing:
        raise ModelInvocationError(
            f"this codex lacks the {' and '.join(missing)} feature Scout needs to lock the model down; "
            "upgrade codex"
        )
    _CACHE[key] = None


def require_claude_capabilities(executable: str) -> None:
    """``claude --help`` must list the flags that turn settings, MCP and tools off."""

    key = ("claude", executable)
    if key in _CACHE:
        return
    output = _run_probe((executable, "--help"))
    missing = [flag for flag in ("--setting-sources", "--strict-mcp-config", "--tools") if flag not in output]
    if missing:
        raise ModelInvocationError(
            f"this claude lacks the {', '.join(missing)} flag(s) Scout needs to lock the model down; "
            "upgrade Claude Code"
        )
    _CACHE[key] = None


def reset_probe_cache() -> None:
    _CACHE.clear()


__all__ = ["require_claude_capabilities", "require_codex_capabilities", "reset_probe_cache"]
