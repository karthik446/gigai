import os
from pathlib import Path
import sys

from ..tool_boundary import (
    failing_tool,
    noisy_tool,
    run_in_process,
    run_subprocess,
)


MODULE = "research.phase0_spike.tool_boundary"


def test_direct_callable_captures_output_and_exception() -> None:
    noisy = run_in_process(noisy_tool, {"value": "x"})
    failed = run_in_process(failing_tool, {})

    assert noisy.status == "ok"
    assert noisy.output == {"value": "x"}
    assert noisy.stdout == "stdout:x\n"
    assert noisy.stderr == "stderr:x\n"
    assert failed.status == "error"
    assert failed.error_type == "ValueError"


def test_subprocess_captures_output_and_exception() -> None:
    noisy = run_subprocess(f"{MODULE}:noisy_tool", {"value": "x"})
    failed = run_subprocess(f"{MODULE}:failing_tool", {})

    assert noisy.status == "ok"
    assert noisy.output == {"value": "x"}
    assert noisy.stdout == "stdout:x\n"
    assert noisy.stderr == "stderr:x\n"
    assert failed.status == "error"
    assert failed.error_type == "ValueError"
    assert "seeded tool failure" in (failed.error_message or "")


def test_subprocess_contains_crash_and_timeout() -> None:
    crashed = run_subprocess(f"{MODULE}:crash_tool", {})
    timed_out = run_subprocess(
        f"{MODULE}:slow_tool",
        {"seconds": 1},
        timeout_seconds=0.05,
    )

    assert crashed.status == "process_exit"
    assert crashed.returncode == 17
    assert timed_out.status == "timeout"


def test_subprocess_controls_cwd_and_environment(tmp_path: Path) -> None:
    environment = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(Path.cwd()),
        "SPIKE_VISIBLE": "yes",
    }
    result = run_subprocess(
        f"{MODULE}:environment_tool",
        {"name": "SPIKE_VISIBLE"},
        cwd=tmp_path,
        environment=environment,
    )

    assert result.status == "ok", result
    assert result.output == {
        "cwd": str(tmp_path),
        "visible": "yes",
        "hidden": None,
    }
    assert result.returncode == 0
    assert sys.executable
