from __future__ import annotations

import os
from pathlib import Path
import sys

from research.s18_02.runner import invoke_fake_cli


FAKE_CLI = Path(__file__).parents[1] / "research/s18_02/fake_cli.py"


def _run(tmp_path: Path, *, scenario: str, **kwargs):
    return invoke_fake_cli(
        (sys.executable, str(FAKE_CLI)),
        scenario=scenario,
        cwd=tmp_path,
        host_environment={
            **os.environ,
            "PATH": os.environ.get("PATH", ""),
            "S18_02_SYNTHETIC_TOKEN": "synthetic-value-never-inherited",
        },
        **kwargs,
    )


def test_structured_output_argv_and_working_directory_are_captured(tmp_path: Path) -> None:
    result = _run(tmp_path, scenario="success")
    assert result.status == "completed"
    assert result.returncode == 0
    assert result.output["model"] == "fixture-cli-model"
    assert result.output["usage"] == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    assert result.output["cwd"] == str(tmp_path)
    assert result.output["argv"] == ["fixture-argument"]


def test_credential_environment_is_not_inherited() -> None:
    result = _run(Path.cwd(), scenario="credential-check")
    assert result.status == "completed"
    assert result.output["credential_present"] is False


def test_nonzero_exit_is_distinct_from_malformed_or_success(tmp_path: Path) -> None:
    result = _run(tmp_path, scenario="exit")
    assert result.status == "failed"
    assert result.returncode == 17
    assert result.output is None
    assert "synthetic cli failure" in result.stderr


def test_timeout_and_cancellation_are_distinct_terminal_outcomes(tmp_path: Path) -> None:
    timed_out = _run(tmp_path, scenario="sleep", timeout_seconds=0.05)
    assert timed_out.status == "timeout"
    cancelled = _run(tmp_path, scenario="sleep", cancel_after_seconds=0.05)
    assert cancelled.status == "cancelled"


def test_malformed_structured_output_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path, scenario="malformed")
    assert result.status == "malformed_response"
    assert result.output is None
