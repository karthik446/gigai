"""compat-macos312: the scenario bound is a CI-scalable hang guard, and a
timeout carries enough timing evidence to tell a slow child from a hung one.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

from tests.scenarios import (
    CommandTarget,
    ScenarioHarness,
    ScenarioRoots,
    ScenarioSpec,
    ScenarioViolation,
)
from tests.support.latency import LATENCY_SCALE_ENV


def _git_executable() -> Path:
    discovered = shutil.which("git", path="/usr/bin:/bin")
    assert discovered is not None
    return Path(discovered).resolve()


# Spawns git twice, then outlives any bound this file declares unscaled.
_SPAWN_TWICE_THEN_SLEEP = (
    "import subprocess, time\n"
    f"git = {str(_git_executable())!r}\n"
    "for _ in range(2):\n"
    "    subprocess.run([git, '--version'], capture_output=True, check=True)\n"
    "time.sleep(1.5)\n"
)


def _probe(code: str) -> CommandTarget:
    return CommandTarget(
        executable=Path(sys.executable).resolve(),
        argv_prefix=("-c", code),
        allowed_read_roots=(
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
            Path(__file__).resolve().parents[2],
        ),
    )


def _spec(name: str) -> ScenarioSpec:
    return ScenarioSpec(
        name=name,
        argv=(),
        allowed_subprocesses=(_git_executable(),),
        timeout_seconds=0.5,
    )


def test_timeout_reports_the_bound_cpu_and_spawn_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(LATENCY_SCALE_ENV, raising=False)
    roots = ScenarioRoots.create(tmp_path / "slow")
    harness = ScenarioHarness(_probe(_SPAWN_TWICE_THEN_SLEEP), roots)

    with pytest.raises(ScenarioViolation) as failure:
        harness.run(_spec("slow-child"))

    result = failure.value.result
    assert result.violations == ("process_timeout", "unexpected_exit:124")
    assert result.timeout_seconds == 0.5
    message = str(failure.value)
    assert "timing: elapsed=" in message
    assert "bound=0.50s" in message
    assert "child_cpu=" in message
    assert "last_spawn_at=" in message
    artifact = json.loads(result.artifact.read_text(encoding="utf-8"))
    assert artifact["timed_out"] is True
    assert artifact["timeout_seconds"] == 0.5
    assert artifact["child_cpu_seconds"] >= 0
    timeline = artifact["subprocess_timeline"]
    assert timeline["count"] == len(timeline["tail"])
    assert {event["executable"] for event in timeline["tail"]} <= {"git"}
    assert all(
        0 <= event["offset_seconds"] <= 0.5 + 1.0 for event in timeline["tail"]
    )


def test_latency_scale_widens_the_bound_so_a_slow_child_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LATENCY_SCALE_ENV, "40")
    roots = ScenarioRoots.create(tmp_path / "scaled")
    harness = ScenarioHarness(_probe(_SPAWN_TWICE_THEN_SLEEP), roots)

    result = harness.run(_spec("slow-child-scaled"))

    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.timeout_seconds == 20.0
    assert result.duration_ns >= 1.5e9
    timeline = result.subprocess_timeline
    assert timeline is not None
    assert timeline["count"] == 2
    artifact = json.loads(result.artifact.read_text(encoding="utf-8"))
    assert artifact["subprocess_timeline"]["count"] == 2
    assert [event["executable"] for event in artifact["subprocess_timeline"]["tail"]] == [
        "git",
        "git",
    ]
