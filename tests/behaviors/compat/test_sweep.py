"""H1: the compatibility sweep and the 0.1.11.13 baseline fixture home.

The sweep runs with OLD = NEW = this checkout's console script, so it proves the
harness itself (every commands.yaml entry answers ``--help``, the journeys pass
on the unpacked fixture home) offline: no uvx, no network. The release lane runs
it against the real 0.1.11.13 (``make compat-sweep``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from tests.support.fixture_homes import BASELINE_COUNTS, BASELINE_TARBALL, HOME_TOKEN, counts, home_0_1_11_13  # noqa: F401 - pytest fixture

ROOT = Path(__file__).resolve().parents[3]
CLI = str(Path(sys.executable).with_name("gigai"))


def test_the_sweep_with_the_same_cli_on_both_sides_is_clean(tmp_path: Path) -> None:
    # OLD == NEW, so every row of the real allow file would be "stale": run with an empty one.
    no_rows = tmp_path.parent / f"{tmp_path.name}-no-allow-rows"
    no_rows.write_text("", encoding="utf-8")
    env = {**os.environ, "TMPDIR": str(tmp_path), "COMPAT_SWEEP_PYTHON": sys.executable, "COMPAT_SWEEP_ALLOW": str(no_rows)}
    result = subprocess.run(
        ["bash", str(ROOT / "tools" / "compat_sweep.sh"), CLI, CLI, str(BASELINE_TARBALL)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=1200, check=False,
    )
    lines = result.stdout.splitlines()
    yaml_commands = json.loads((ROOT / "src/gigai/data/cli/commands.yaml").read_text(encoding="utf-8"))["commands"]  # JSON
    assert len(yaml_commands) >= 187, "0.1.11.13 shipped 187 public commands; none may disappear"
    assert lines[:1] == [f"commands: {len(yaml_commands)}"], result.stdout[-2000:] + result.stderr[-2000:]
    bad = [line for line in lines if line.startswith(("FAIL", "DIFF"))]
    assert bad == [], "\n".join(bad) + "\n" + result.stderr[-2000:]
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert list(tmp_path.iterdir()) == [], "a clean sweep deletes its scratch dir"


def test_the_unpacked_baseline_home_matches_its_counts_and_points_at_itself(home_0_1_11_13: Path) -> None:
    assert os.environ["GIGAI_HOME"] == str(home_0_1_11_13)
    assert counts(home_0_1_11_13) == BASELINE_COUNTS.read_text(encoding="utf-8")
    config = (home_0_1_11_13 / "config.toml").read_text(encoding="utf-8")
    assert f'home_root = "{home_0_1_11_13}"' in config and HOME_TOKEN not in config
    with sqlite3.connect(home_0_1_11_13 / "registry.sqlite") as registry:
        assert registry.execute("PRAGMA user_version").fetchone() == (3,)
        (locator,) = registry.execute("SELECT target_locator FROM projects").fetchone()
    assert locator == str(home_0_1_11_13 / "scout")
