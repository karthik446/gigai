"""P3c fix round (M1): Scout runs through the gig contract yet keeps its own paths, as in 0.1.11.13. A Scout verb
touches nothing under `$GIGAI_HOME` (no `gigs/scout/` gig folder), so it also works when that home is read-only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.fixture_homes import home_0_1_11_13  # noqa: F401 - pytest fixture

CLI = "import sys; from gigai.cli import cli; cli(sys.argv[1:], prog_name='gigai')"


def _scout(gigai_home: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIGAI_HOME": str(gigai_home), "COLUMNS": "80"}
    return subprocess.run(
        [sys.executable, "-c", CLI, "scout", *argv], capture_output=True, text=True, env=env, timeout=120, check=False
    )


@pytest.fixture
def read_only_home(tmp_path: Path):
    if os.geteuid() == 0:
        pytest.skip("root writes through mode 0555")
    home = tmp_path / "gigai-home"
    home.mkdir()
    home.chmod(0o555)
    yield home
    home.chmod(0o755)


@pytest.mark.parametrize("argv", [("status", "--help"), ("jobs", "--help")])
def test_a_scout_verbs_help_leaves_gigai_home_untouched_even_read_only(read_only_home, argv):
    completed = _scout(read_only_home, *argv)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith(f"Usage: gigai scout {argv[0]} ")
    assert completed.stderr == ""
    assert list(read_only_home.iterdir()) == []


def test_scout_status_on_its_own_home_leaves_gigai_home_untouched_even_read_only(read_only_home, home_0_1_11_13):
    completed = _scout(read_only_home, "status", "--home", str(home_0_1_11_13), "--json")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert isinstance(json.loads(completed.stdout), dict)
    assert "cannot create its gig folder" not in completed.stderr
    assert list(read_only_home.iterdir()) == []
    assert not (home_0_1_11_13 / "gigs").exists()
