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


def test_installed_offline_proposal_lifecycle(installed_gigai: InstalledGigAI) -> None:
    python = installed_gigai.command.executable.parent / "python"
    verifier = Path(__file__).parents[1] / "tools" / "verify_installed_g08.py"
    result = subprocess.run(
        [os.fspath(python if python.exists() else Path(sys.executable)), os.fspath(verifier)],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "verified installed GigAI G08 offline proposal lifecycle\n"
