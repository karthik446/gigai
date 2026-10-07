"""Bounded, non-shell process execution for local model adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Mapping, Sequence

from .port import (
    ModelAuthenticationRequired,
    ModelInvocationCancelled,
    ModelInvocationError,
)


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


def allowed_environment(
    environment: Mapping[str, str] | None = None,
    *,
    extra_names: Sequence[str] = (),
) -> dict[str, str]:
    """Build the explicit child environment with narrowly named extras."""

    source = environment or os.environ
    names = (*_ENVIRONMENT_ALLOWLIST, *extra_names)
    return {
        name: value
        for name in names
        if isinstance(value := source.get(name), str) and "\0" not in value
    }


# 0.1.11.5 (ASSESS-01): the model processes THIS process started and has not reaped yet. Each child is the leader of
# its own session (``start_new_session``), so a signal to the parent never reaches it: a server that was stopped left
# its `claude -p` calls running under pid 1. A process that ends ends these itself (:func:`terminate_children`).
# Only a child in this table is ever signalled, and only while it is unreaped (its pid cannot have been reused).
_CHILDREN: dict[int, "subprocess.Popen[str]"] = {}
_CHILDREN_LOCK = threading.Lock()


def live_children() -> tuple[int, ...]:
    """The pids of the model processes this process started that are still running."""

    with _CHILDREN_LOCK:
        return tuple(sorted(pid for pid, process in _CHILDREN.items() if process.returncode is None))


def terminate_children(*, grace_seconds: float = 2.0) -> int:
    """End every model process this process started: SIGTERM to each one's own group, SIGKILL after ``grace_seconds``.

    Returns how many were signalled. Never touches a process this module did not start. Safe from a signal handler:
    it does not reap (the thread that started a child does, in :func:`run_json_process`), it only signals.
    """

    # No lock: this may run in a signal handler, on a thread that holds it. A copy of the table is one atomic step.
    children = [process for process in list(_CHILDREN.values()) if process.returncode is None]
    for process in children:
        _signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline and any(_group_alive(process) for process in children):
        time.sleep(0.05)
    for process in children:
        if _group_alive(process):
            _signal_group(process, signal.SIGKILL)
    return len(children)


def _signal_group(process: "subprocess.Popen[str]", signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError):
        return


def _group_alive(process: "subprocess.Popen[str]") -> bool:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_json_process(
    argv: Sequence[str],
    *,
    prompt: str,
    cwd: Path,
    timeout_seconds: float,
    environment: Mapping[str, str] | None = None,
    extra_environment_names: Sequence[str] = (),
) -> ProcessOutput:
    """Run one explicit process and fail closed on timeout or cancellation."""

    if not argv or not argv[0] or any("\0" in value for value in argv):
        raise ModelInvocationError("CLI argv must contain a non-empty executable and NUL-free values")
    if not prompt or "\0" in prompt:
        raise ModelInvocationError("CLI prompt must be non-empty and NUL-free")
    if not cwd.is_dir():
        raise ModelInvocationError("CLI working directory is not a directory")
    if timeout_seconds <= 0:
        raise ModelInvocationError("CLI timeout must be positive")

    process = subprocess.Popen(
        tuple(argv),
        cwd=cwd,
        env=allowed_environment(environment, extra_names=extra_environment_names),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        start_new_session=True,
    )
    with _CHILDREN_LOCK:
        _CHILDREN[process.pid] = process
    try:
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
    finally:
        with _CHILDREN_LOCK:
            _CHILDREN.pop(process.pid, None)

    result = ProcessOutput(stdout=stdout, stderr=stderr, returncode=process.returncode)
    if result.returncode != 0:
        _raise_structured_authentication_failure(result)
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


def _raise_structured_authentication_failure(output: ProcessOutput) -> None:
    """Preserve a provider-owned auth category when it reports JSON on stdout."""

    for line in reversed(output.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if type(payload) is not dict or payload.get("is_error") is not True:
            continue
        result = payload.get("result")
        if not isinstance(result, str):
            continue
        lowered = result.lower()
        if any(
            marker in lowered
            for marker in ("not logged in", "authentication", "unauthorized", "login")
        ):
            raise ModelAuthenticationRequired(
                "authentication_required: " + result.strip()[:240]
            )


__all__ = ["ProcessOutput", "allowed_environment", "live_children", "run_json_process", "terminate_children"]
