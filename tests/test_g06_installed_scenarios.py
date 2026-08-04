from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.scenarios import InstalledGigAI


@pytest.fixture
def installed_gigai() -> InstalledGigAI:
    return InstalledGigAI.current()


def _python_executable(installed_gigai: InstalledGigAI) -> Path:
    candidate = installed_gigai.command.executable.parent / "python"
    # Keep the venv entrypoint path: resolving it follows its symlink to the
    # base interpreter, which does not carry the installed GigAI distribution.
    return candidate if candidate.exists() else Path(sys.executable)


def test_installed_journal_transition_and_trailer_sequence(
    installed_gigai: InstalledGigAI,
) -> None:
    verifier = Path(__file__).parents[1] / "tools" / "verify_installed_g06.py"
    result = subprocess.run(
        [os.fspath(_python_executable(installed_gigai)), os.fspath(verifier)],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "verified installed GigAI G06 journal first commit and trailer sequencing\n"
