"""0110-10-15: ``tools/pick_report.py``, the read-only pick report the orchestrator runs on a real home.

On the SYNTHETIC operator-shaped home (``tests/support/operator_home.py``: two profiles, a stored master, assessed
postings; an invented person, a scripted model).  The END outcomes: the report prints counts, ids and requirement
text and never a line of a resume or a path under the home; with ``--baseline`` an older tree's selector is run on
the same inputs and each check gets its own verdict; and every file of the home is byte for byte what it was.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

from tools import pick_report

REPO = Path(__file__).resolve().parents[3]


def _snapshot(root: Path) -> dict[str, str]:
    """Every folder, link and file under ``root``: a file by its bytes and the moment it was last written."""

    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        name = str(path.relative_to(root))
        if path.is_symlink():
            found[name] = f"link:{os.readlink(path)}"
        elif path.is_dir():
            found[name] = "folder"
        else:
            found[name] = f"{hashlib.sha256(path.read_bytes()).hexdigest()}:{path.stat().st_mtime_ns}"
    return found


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A scaled operator home with a stored master, two attached profiles and assessed postings; its root."""

    root = tmp_path_factory.mktemp("pick-report") / "op"
    done = subprocess.run(
        [sys.executable, "-m", "tests.support.operator_home", str(root), "--postings", "3000", "--companies", "100", "--assessed", "6", "--workers", "2",
         "--write-content", "--pipeline-jobs", "1"],
        cwd=REPO, capture_output=True, text=True, timeout=1200, check=False, env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return root


@pytest.fixture(scope="module")
def older_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A second tree to run as ``--baseline``: a ``git archive`` of HEAD (what the command's own help shows)."""

    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("needs a git checkout to archive a tree")
    tree = tmp_path_factory.mktemp("older-tree")
    archive = subprocess.run(["git", "archive", "HEAD", "src/gigai"], cwd=REPO, capture_output=True, check=False)
    assert archive.returncode == 0, archive.stderr.decode()[-500:]
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive.stdout, check=True)
    assert (tree / "src" / "gigai" / "scout" / "master_selection.py").is_file()
    return tree


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(REPO / "tools" / "pick_report.py"), *args], cwd=REPO, capture_output=True, text=True, timeout=900, check=False)


def _master_lines(home: Path) -> list[str]:
    from gigai.scout import tailor_master as tm
    from gigai.scout.target_resolution import home_scout_target

    stored = tm.stored_master(home, home_scout_target(home))
    assert stored is not None
    return [item.text for item in stored.master.items.values() if item.kind != "skills" and len(item.text) > 30]


def test_the_report_reads_a_home_prints_counts_and_ids_only_and_leaves_every_file_as_it_was(built: Path, older_tree: Path) -> None:
    home = built / "home"
    before = _snapshot(built)
    assert any(name.endswith(".json") and "quick_assess" in name for name in before) and len(before) > 500

    alone = _run("--home", str(home), "--limit", "3")
    beside = _run("--home", str(home), "--limit", "3", "--baseline", str(older_tree))

    # NOTHING was written: no file, no folder, not even a cache or a modification time, in the home or its workpads.
    after = _snapshot(built)
    assert after == before, sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))[:10]

    assert alone.returncode == 0 and beside.returncode == 0, (alone.stderr[-800:], beside.stderr[-800:])
    for out in (alone.stdout, beside.stdout):
        assert out.startswith("Pick report: master revision 1 (") and "selector sel-2" in out
        assert "Nothing was written, no model was called, no request was made." in out
        assert out.count("## Profile profile_") == 2, "the newest assessed postings of EACH profile"
        assert "requirement rows cite master lines" in out and "by role: r-" in out and "skills " in out and "conflicts 0" in out
        assert "pages 2/2 fits" in out and "lines picked " in out and "left out " in out
        # Counts, ids and requirement text only: no line of the master, no path under the home.
        assert not [text for text in _master_lines(home) if text in out]
        assert str(home) not in out and str(built) not in out and "/quick_assess/" not in out
    assert "verdict:" not in alone.stdout and "OLD" not in alone.stdout
    # Beside the baseline: the old selector on the same inputs, one verdict per check, the totals and the result line.
    assert "select NEW:" in beside.stdout and "select OLD:" in beside.stdout and "fallback OLD:" in beside.stdout
    assert "against baseline sel-" in beside.stdout
    for check in ("mandatory coverage", "other coverage", "evidence strength", "must-keep", "page fit", "skills kept", "overall"):
        assert f"{check} " in beside.stdout
    assert "## Totals, new against old (postings per verdict)" in beside.stdout
    assert beside.stdout.rstrip().endswith("RESULT: no posting lost mandatory coverage or page fit against the baseline.")
    # One row per requirement the assessment evidenced with the master: its place, its status, in or NOT IN, the ids cited.
    rows = [line for line in beside.stdout.splitlines() if line.strip().startswith("r") and "] in" in line or "] NOT IN" in line]
    assert rows and all(" <- " in line for line in rows)


def test_the_report_refuses_plainly_what_it_cannot_read(tmp_path: Path, built: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    before = _snapshot(empty)
    refused = _run("--home", str(empty))
    assert refused.returncode != 0 and "pick_report:" in refused.stderr and _snapshot(empty) == before, "nothing is created in a folder that is not a home"
    assert _run("--home", str(tmp_path / "missing")).returncode != 0
    assert _run("--home", str(built / "home"), "--limit", "0").returncode != 0
    wrong = _run("--home", str(built / "home"), "--limit", "1", "--baseline", str(tmp_path))
    assert wrong.returncode != 0 and "holds no src/gigai" in wrong.stderr


def _checked(*, covered: tuple[str, ...], skills: int, pages: int = 2, strength: dict[str, int] | None = None) -> pick_report.Checked:
    return pick_report.Checked(error=None, covered=covered, strength=strength or {row: 2 for row in covered}, pages=pages, skills=skills, skills_total=20)


def test_each_check_gets_its_own_verdict_and_lost_mandatory_coverage_is_worse_whatever_else_improved() -> None:
    rows = (
        pick_report.Row("r1", "Kubernetes in production", "met", True, ("b-1",), ()),
        pick_report.Row("r2", "Terraform", "met", True, ("b-2",), ()),
        pick_report.Row("r3", "A talk or two", "met", False, ("o-1",), ()),
    )
    posting = pick_report.Posting("p|1", "Staff Engineer", "Acme", "2026-10-01", rows, 0)
    old = _checked(covered=("r1", "r2", "r3"), skills=6)

    same = pick_report.verdicts(posting, old, _checked(covered=("r1", "r2", "r3"), skills=6))
    assert set(same.values()) == {pick_report.SAME}
    # More skills and stronger evidence, but a mandatory row lost its line: WORSE, and nothing offsets it.
    lost = pick_report.verdicts(posting, old, _checked(covered=("r1", "r3"), skills=14, strength={"r1": 3, "r3": 2}))
    assert (lost["mandatory coverage"], lost["skills kept"], lost["evidence strength"], lost["overall"]) == (pick_report.WORSE, pick_report.BETTER, pick_report.BETTER, pick_report.WORSE)
    # A nice-to-have row lost is reported apart, never as mandatory coverage.
    nice = pick_report.verdicts(posting, old, _checked(covered=("r1", "r2"), skills=6))
    assert (nice["mandatory coverage"], nice["other coverage"], nice["overall"]) == (pick_report.SAME, pick_report.WORSE, pick_report.WORSE)
    better = pick_report.verdicts(posting, _checked(covered=("r1",), skills=6), _checked(covered=("r1", "r2"), skills=6))
    assert (better["mandatory coverage"], better["overall"]) == (pick_report.BETTER, pick_report.BETTER)
    over = pick_report.verdicts(posting, old, _checked(covered=("r1", "r2", "r3"), skills=6, pages=3))
    assert (over["page fit"], over["overall"]) == (pick_report.WORSE, pick_report.WORSE)


def test_a_regression_against_the_baseline_is_the_exit_code(built: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The orchestrator's gate: when the old selector covered a mandatory row the new one does not, the command fails."""

    home = built / "home"
    real = pick_report.pick_probe.probe

    def old_shows_everything(payload: dict[str, object], _tree: Path) -> dict[str, object]:
        return {**real(payload), "selector_version": "sel-old"}

    def new_shows_no_skill_and_no_line(payload: dict[str, object]) -> dict[str, object]:
        answer = real(payload)
        for paths in answer["results"].values():  # type: ignore[union-attr]
            for final in paths.values():
                final.update({"summary": [], "entries": {}, "other": [], "skills": []})
        return answer

    monkeypatch.setattr(pick_report.pick_probe, "run_in_tree", old_shows_everything)
    monkeypatch.setattr(pick_report.pick_probe, "probe", new_shows_no_skill_and_no_line)
    before = _snapshot(built)
    code = pick_report.main(["--home", str(home), "--limit", "2", "--baseline", str(REPO), "--path", "select"])
    out = capsys.readouterr().out
    assert code == 1 and "mandatory coverage WORSE" in out and "RESULT: A REGRESSION" in out
    assert "NOT IN (old: in)" in out and _snapshot(built) == before


def test_no_request_can_be_made_while_the_report_runs_and_cache_writes_are_held() -> None:
    from gigai import journal, workpad

    with pick_report._no_network():  # noqa: SLF001
        with pytest.raises(OSError, match="makes no request"):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("127.0.0.1", 9))
    real = workpad.scratch_cache_path
    with pick_report._hold_cache_writes():  # noqa: SLF001
        assert journal.scratch_cache_path is not real
        assert journal.scratch_cache_path(Path("/nonexistent"), "journal-publishers.sqlite", create=True) is None
    assert journal.scratch_cache_path is real and workpad.scratch_cache_path is real


def test_the_probe_answers_with_ids_and_never_a_line_of_text() -> None:
    master = (REPO / "tests" / "evals" / "fixtures" / "pick" / "master.md").read_text(encoding="utf-8")
    posting = {"title": "Staff Engineer", "text": "Requirements:\n- Kubernetes in production.\n- Kafka.\n", "company": "Acme"}
    answer = pick_report.pick_probe.probe({"today": "2026-10-03", "cases": [{"key": "k", "master": master, "profile": {"titles": ["Staff Engineer"], "base_ids": None}, "posting": posting}]})
    text = json.dumps(answer)
    assert set(answer["results"]["k"]) == set(pick_report.pick_probe.PATHS) and all("error" not in final for final in answer["results"]["k"].values())
    lines = [line.split(" <!--")[0][2:] for line in master.splitlines() if line.startswith("- ") and " · " not in line]
    assert len(lines) > 60 and not [line for line in lines if line in text]
