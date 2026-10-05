"""0110-10-08: `gigai scout new` on a COLD read model with 500 or more companies to match.

0.1.10.9 crashed there with ``NameError: name 'time' is not defined``: the
progress callback ``new_command`` hands to the build (``build_progress``)
called ``time.monotonic()`` and ``scout_cli.py`` never imported ``time``. The
``total >= 500`` in front of it short-circuits on every small home, so no test
and no gate reached the call.

Two levels, both on the END outcome (the command answers, the progress lines
are on stderr):

* the callback itself, as ``new_command`` builds it, driven with a matching
  total of 500 or more under a fake clock: the first line, the 2 s throttle,
  and the ``done == total`` line;
* ``gigai scout new --no-assess --json`` END TO END in a fresh process on a
  synthetic home with 600 companies and no ``pipeline.sqlite`` at all (no read
  model yet, no server running): the CLI does the build itself.

Synthetic only (``tests/support/operator_home.py``): no request, no real home.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from click.testing import CliRunner
import pytest

from tests.support import operator_home

#: Enough boards for the progress lines (``total >= 500``), few enough postings to build in seconds.
COMPANIES = 600
POSTINGS = 3_000


def _drive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, calls: list[tuple[float, str, int, int]]) -> list[str]:
    """Run ``gigai scout new --no-assess --json`` with a build that reports ``calls`` (clock, phase, done, total); the stderr lines."""

    from gigai.cli import cli
    from gigai.scout import scout_cli, scout_new as scout_new_module

    clock = [0.0]

    def fake_scout_new(_home: Path, _target: Path, *, build_progress: Callable[[str, int, int], None], **_rest: object) -> dict[str, object]:
        for moment, phase, done, total in calls:
            clock[0] = moment
            build_progress(phase, done, total)
        return {"schema_version": "scout-new:1", "status": "listed"}

    monkeypatch.setattr(scout_cli, "_pipeline_target", lambda _target, _home, *, as_json: tmp_path)
    monkeypatch.setattr(scout_new_module, "scout_new", fake_scout_new)
    with monkeypatch.context() as patched:
        patched.setattr(time, "monotonic", lambda: clock[0])
        result = CliRunner().invoke(cli, ["scout", "new", "--no-assess", "--json", "--home", str(tmp_path), "--target", str(tmp_path)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"schema_version": "scout-new:1", "status": "listed"}  # stdout is the response alone
    return result.stderr.splitlines()


def test_a_build_over_500_companies_says_where_it_is_at_most_every_2_seconds_and_when_done(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lines = _drive(monkeypatch, tmp_path, [
        (100.0, "matching", 0, 600),    # the first report: said
        (101.0, "matching", 100, 600),  # 1 s later: not said (the 2 s throttle)
        (101.9, "matching", 200, 600),  # 1.9 s after the last line: not said
        (102.5, "matching", 300, 600),  # 2.5 s after it: said
        (102.6, "matching", 600, 600),  # done == total: said, whatever the clock
    ])

    assert lines == [
        "preparing your postings: 0% (0 of 600 companies)",
        "preparing your postings: 50% (300 of 600 companies)",
        "preparing your postings: 100% (600 of 600 companies)",
    ]


def test_the_done_line_is_said_inside_the_throttle_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lines = _drive(monkeypatch, tmp_path, [(50.0, "matching", 0, 500), (50.1, "matching", 499, 500), (50.2, "matching", 500, 500)])

    assert lines == ["preparing your postings: 0% (0 of 500 companies)", "preparing your postings: 100% (500 of 500 companies)"]


def test_a_small_build_and_the_other_phases_say_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lines = _drive(monkeypatch, tmp_path, [
        (100.0, "matching", 0, 499), (200.0, "matching", 499, 499),  # under 500 companies: quick, silent
        (300.0, "facts", 600, 600), (400.0, "idle", 600, 600),  # only the matching phase is reported
    ])

    assert lines == []


@pytest.fixture(scope="module")
def cold_home(tmp_path_factory: pytest.TempPathFactory) -> operator_home.OperatorHome:
    kept = dict(os.environ)
    try:
        built = operator_home.build(tmp_path_factory.mktemp("cold-home") / "op", postings=POSTINGS, companies=COMPANIES, match_every=20, workers=1, assessed=0)
    finally:
        os.environ.clear()
        os.environ.update(kept)
    return built


def test_scout_new_no_assess_in_a_fresh_process_builds_a_cold_model_of_600_companies(cold_home: operator_home.OperatorHome) -> None:
    from gigai.scout.pipeline.store import pipeline_path

    home_root, target = Path(cold_home.home), Path(cold_home.target)
    store_file = pipeline_path(home_root, target)
    for found in store_file.parent.glob(store_file.name + "*"):
        found.unlink()  # no pipeline.sqlite at all: no read model, no anchor, as before the first `scout new`
    assert not store_file.exists()

    done = subprocess.run(
        [sys.executable, "-c", "from gigai.cli import cli; cli()", "scout", "new", "--no-assess", "--json", "--home", cold_home.home, "--target", cold_home.target],
        capture_output=True, text=True, env=dict(os.environ, **operator_home.SEAM_ENV), check=False, timeout=300,
    )

    assert done.returncode == 0, done.stderr[-2000:]
    assert "Traceback" not in done.stderr and "NameError" not in done.stderr, done.stderr[-2000:]
    response = json.loads(done.stdout)  # stdout is the response alone: the progress lines are on stderr
    assert response["schema_version"] == "scout-new:1"
    said = [line for line in done.stderr.splitlines() if line.startswith("preparing your postings: ")]
    assert said[0] == f"preparing your postings: 0% (0 of {COMPANIES} companies)", done.stderr[-2000:]
    assert said[-1] == f"preparing your postings: 100% ({COMPANIES} of {COMPANIES} companies)", done.stderr[-2000:]
    assert store_file.exists()  # the CLI built the model itself

    # The model is stored: the same command again matches nothing and says nothing.
    again = subprocess.run(
        [sys.executable, "-c", "from gigai.cli import cli; cli()", "scout", "new", "--no-assess", "--json", "--home", cold_home.home, "--target", cold_home.target],
        capture_output=True, text=True, env=dict(os.environ, **operator_home.SEAM_ENV), check=False, timeout=300,
    )
    assert again.returncode == 0, again.stderr[-2000:]
    assert json.loads(again.stdout)["schema_version"] == "scout-new:1"
    assert "preparing your postings" not in again.stderr
