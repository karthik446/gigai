"""Provider-free process runner for the S18-02 fake CLI probe."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Mapping, Sequence


ALLOWED_ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TERM")


@dataclass(frozen=True)
class CLIResult:
    status: str
    returncode: int | None
    output: Mapping[str, object] | None
    stderr: str
    argv: tuple[str, ...]
    cwd: str


def invoke_fake_cli(
    argv: Sequence[str],
    *,
    scenario: str,
    cwd: Path,
    host_environment: Mapping[str, str],
    timeout_seconds: float = 2.0,
    cancel_after_seconds: float | None = None,
) -> CLIResult:
    """Run only a supplied fake command with shell and credential inheritance off."""

    if not cwd.is_dir():
        raise ValueError("CLI working directory must already exist")
    command = tuple(argv) + ("--scenario", scenario, "fixture-argument")
    child_environment = {
        key: host_environment[key]
        for key in ALLOWED_ENVIRONMENT
        if key in host_environment
    }
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=child_environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    try:
        if cancel_after_seconds is not None:
            try:
                stdout, stderr = process.communicate(timeout=cancel_after_seconds)
            except subprocess.TimeoutExpired:
                process.terminate()
                stdout, stderr = process.communicate(timeout=timeout_seconds)
                return CLIResult("cancelled", process.returncode, None, stderr, command, str(cwd))
        else:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        return CLIResult("timeout", process.returncode, None, stderr, command, str(cwd))

    if process.returncode != 0:
        return CLIResult("failed", process.returncode, None, stderr, command, str(cwd))
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return CLIResult("malformed_response", process.returncode, None, stderr, command, str(cwd))
    if not isinstance(payload, dict):
        return CLIResult("malformed_response", process.returncode, None, stderr, command, str(cwd))
    return CLIResult("completed", process.returncode, payload, stderr, command, str(cwd))


__all__ = ["ALLOWED_ENVIRONMENT", "CLIResult", "invoke_fake_cli"]
