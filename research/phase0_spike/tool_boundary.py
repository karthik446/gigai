"""Compare direct Python tool calls with a one-shot subprocess worker."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
import importlib
import io
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time
import traceback
from typing import Any, Callable


@dataclass(frozen=True)
class ToolResult:
    status: str
    output: Any = None
    stdout: str = ""
    stderr: str = ""
    error_type: str | None = None
    error_message: str | None = None
    returncode: int = 0
    duration_ms: float = 0.0


def echo_tool(payload: dict[str, Any]) -> dict[str, Any]:
    return {"echo": payload}


def noisy_tool(payload: dict[str, Any]) -> dict[str, Any]:
    print(f"stdout:{payload['value']}")
    print(f"stderr:{payload['value']}", file=sys.stderr)
    return {"value": payload["value"]}


def failing_tool(_payload: dict[str, Any]) -> dict[str, Any]:
    raise ValueError("seeded tool failure")


def slow_tool(payload: dict[str, Any]) -> dict[str, Any]:
    time.sleep(float(payload["seconds"]))
    return {"slept": payload["seconds"]}


def crash_tool(_payload: dict[str, Any]) -> dict[str, Any]:
    os._exit(17)


def environment_tool(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "cwd": str(Path.cwd()),
        "visible": os.environ.get(payload["name"]),
        "hidden": os.environ.get("GIGAI_SPIKE_HIDDEN"),
    }


def run_in_process(
    fn: Callable[[dict[str, Any]], Any],
    payload: dict[str, Any],
) -> ToolResult:
    started = time.perf_counter()
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            output = fn(payload)
        return ToolResult(
            status="ok",
            output=output,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    except Exception as exc:
        return ToolResult(
            status="error",
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            error_type=type(exc).__name__,
            error_message=str(exc),
            duration_ms=(time.perf_counter() - started) * 1000,
        )


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_subprocess(
    import_ref: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 5,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> ToolResult:
    started = time.perf_counter()
    command = [
        sys.executable,
        "-m",
        "research.phase0_spike.tool_boundary",
        "--worker",
        import_ref,
    ]
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(
            input=json.dumps(payload),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        _terminate(process)
        return ToolResult(
            status="timeout",
            returncode=process.returncode or -signal.SIGKILL,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    duration_ms = (time.perf_counter() - started) * 1000
    if process.returncode != 0:
        return ToolResult(
            status="process_exit",
            stderr=stderr,
            returncode=process.returncode,
            duration_ms=duration_ms,
        )

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return ToolResult(
            status="protocol_error",
            stdout=stdout,
            stderr=stderr,
            error_type=type(exc).__name__,
            error_message=str(exc),
            returncode=process.returncode,
            duration_ms=duration_ms,
        )
    return ToolResult(
        **envelope,
        returncode=process.returncode,
        duration_ms=duration_ms,
    )


def _load(import_ref: str) -> Callable[[dict[str, Any]], Any]:
    module_name, separator, function_name = import_ref.partition(":")
    if not separator:
        raise ValueError("worker import must be module:function")
    module = importlib.import_module(module_name)
    fn = getattr(module, function_name)
    if not callable(fn):
        raise TypeError(f"{import_ref} is not callable")
    return fn


def _worker(import_ref: str) -> int:
    payload = json.loads(sys.stdin.read())
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        fn = _load(import_ref)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            output = fn(payload)
        envelope = {
            "status": "ok",
            "output": output,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }
    except Exception as exc:
        envelope = {
            "status": "error",
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue() + traceback.format_exc(),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    sys.stdout.write(json.dumps(envelope))
    return 0


def benchmark(iterations: int) -> dict[str, float]:
    direct = [
        run_in_process(echo_tool, {"n": index}).duration_ms
        for index in range(iterations)
    ]
    child = [
        run_subprocess(
            "research.phase0_spike.tool_boundary:echo_tool",
            {"n": index},
        ).duration_ms
        for index in range(iterations)
    ]
    return {
        "iterations": float(iterations),
        "in_process_median_ms": statistics.median(direct),
        "subprocess_median_ms": statistics.median(child),
        "subprocess_p95_ms": sorted(child)[max(0, int(len(child) * 0.95) - 1)],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--benchmark", type=int)
    args = parser.parse_args()
    if args.worker:
        return _worker(args.worker)
    if args.benchmark:
        print(json.dumps(benchmark(args.benchmark), indent=2))
        return 0
    parser.error("--worker or --benchmark required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
