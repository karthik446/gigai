"""Bounded, non-shell process execution for local model adapters."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
from typing import Mapping, Sequence

from .port import ModelInvocationCancelled, ModelInvocationError


@dataclass(frozen=True)
class ProcessOutput:
    """Captured child output without exposing inherited environment values."""

    stdout: str
    stderr: str
    returncode: int


_ENVIRONMENT_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "TERM",
    "TMPDIR",
    "CODEX_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
)


def allowed_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the explicit child environment; credential variables are excluded."""

    source = environment or os.environ
    return {
        name: value
        for name in _ENVIRONMENT_ALLOWLIST
        if isinstance(value := source.get(name), str) and "\0" not in value
    }


def run_json_process(
    argv: Sequence[str],
    *,
    prompt: str,
    cwd: Path,
    timeout_seconds: float,
    environment: Mapping[str, str] | None = None,
) -> ProcessOutput:
    """Run one explicit process and fail closed on timeout or cancellation."""

    if not argv or any(not value or "\0" in value for value in argv):
        raise ModelInvocationError("CLI argv must contain non-empty NUL-free values")
    if not prompt or "\0" in prompt:
        raise ModelInvocationError("CLI prompt must be non-empty and NUL-free")
    if not cwd.is_dir():
        raise ModelInvocationError("CLI working directory is not a directory")
    if timeout_seconds <= 0:
        raise ModelInvocationError("CLI timeout must be positive")

    process = subprocess.Popen(
        tuple(argv),
        cwd=cwd,
        env=allowed_environment(environment),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process, force=True)
        process.communicate()
        raise ModelInvocationError("CLI model invocation timed out") from exc
    except KeyboardInterrupt as exc:
        _terminate_process_group(process, force=False)
        process.communicate()
        raise ModelInvocationCancelled("CLI model invocation cancelled") from exc

    result = ProcessOutput(stdout=stdout, stderr=stderr, returncode=process.returncode)
    if result.returncode != 0:
        raise ModelInvocationError(_safe_failure(result))
    return result


def _terminate_process_group(process: subprocess.Popen[str], *, force: bool) -> None:
    try:
        if process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return


def _safe_failure(output: ProcessOutput) -> str:
    detail = output.stderr.strip().splitlines()[0] if output.stderr.strip() else "no stderr"
    return f"CLI model process failed with exit code {output.returncode}: {detail[:240]}"


__all__ = ["ProcessOutput", "allowed_environment", "run_json_process"]
