"""0110-10-15: ``tools/pick_report.py``, the read-only pick report the orchestrator runs on a real home.

On the SYNTHETIC operator-shaped home (``tests/support/operator_home.py``: two profiles, a stored master, assessed
postings; an invented person, a scripted model).  The END outcomes: the report prints counts, ids and requirement
text and never a line of a resume or a path under the home; with ``--baseline`` an older tree's selector is run on
the same inputs and each check gets its own verdict; and every file of the home is byte for byte what it was.

The real-data gate of 0.1.10.11 added: a requirement row is ``in``, ``cited line cut, supported by <ids>`` or a
``REAL LOSS`` (never merged), mandatory coverage is judged on real losses; each posting is selected WITH the rows
its assessment cites; and the read-only proof: every SQLite file is opened ``mode=ro``, ``pipeline.sqlite`` is not
opened at all, and a second connection holding a write on it during the run changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys

import pytest

from tools import pick_report

REPO = Path(__file__).resolve().parents[3]


def _snapshot(root: Path) -> dict[str, str]:
    """Every folder, link and file under ``root``: a file by its bytes and the moment it was last written, a folder
    by the moment something was last made or removed in it (so a temp file made and deleted shows)."""

    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        name = str(path.relative_to(root))
        if path.is_symlink():
            found[name] = f"link:{os.readlink(path)}"
        elif path.is_dir():
            found[name] = f"folder:{path.stat().st_mtime_ns}"
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
        assert out.startswith("Pick report: master revision 1 (") and "selector sel-7" in out
        assert "Nothing was written, no model was called, no request was made." in out
        assert "Each posting was selected WITH the rows its stored assessment cites" in out
        assert out.count("## Profile profile_") == 2, "the newest assessed postings of EACH profile"
        assert "requirement rows cite master lines" in out and "by role: r-" in out and "skills " in out and "conflicts 0" in out
        assert "requirement rows: cited line in " in out and "cited line cut but another shown line supports it " in out and "REAL LOSS 0 (mandatory: in " in out
        assert "## Totals: requirement rows by what the final selection shows for them" in out
        # The roles and projects shown with no line are counted per posting and in the totals (0.1.10.11 PICK v5).
        assert "roles and projects with no line shown: " in out and "## Totals: roles and projects shown with no line" in out
        assert " with no line over " in out and "| the title names " in out
        # How the SQLite files were opened is the last line: read-only every one, and the pipeline's file not at all.
        last = out.rstrip().splitlines()[-1]
        assert last.startswith("SQLite files opened, every one read-only (URI mode=ro): ") and "registry.sqlite x" in last and last.endswith("pipeline.sqlite was not opened.")
        # 0.1.11.5 (c): the selection counts no page ("pages 2/2 fits" until then); the report says what its cap held.
        assert re.search(r"pages not counted, bullets \d+/20 fits \|", out) and "DOES NOT FIT" not in out
        assert "lines picked " in out and "left out " in out
        # Counts, ids and requirement text only: no line of the master, no path under the home.
        assert not [text for text in _master_lines(home) if text in out]
        assert str(home) not in out and str(built) not in out and "/quick_assess/" not in out
    assert "verdict:" not in alone.stdout and "OLD" not in alone.stdout
    # Beside the baseline: the old selector on the same inputs, one verdict per check, the totals and the result line.
    assert "select NEW:" in beside.stdout and "select OLD:" in beside.stdout and "fallback OLD:" in beside.stdout
    assert "against baseline sel-" in beside.stdout
    for check in pick_report.CHECKS:
        assert f"{check} " in beside.stdout
    assert "cited line kept" in pick_report.CHECKS and "## Totals, new against old (postings per verdict)" in beside.stdout
    assert "RESULT: no posting has a new REAL LOSS on a mandatory row, and none lost page fit, against the baseline." in beside.stdout
    # One row per requirement the assessment evidenced with the master: its place, its status, its state, the ids cited.
    rows = [line for line in beside.stdout.splitlines() if line.strip().startswith("r") and ("] in" in line or "] REAL LOSS" in line or "] cited line cut" in line)]
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


def _checked(
    *, covered: tuple[str, ...], skills: int, pages: int = 2, strength: dict[str, int] | None = None, supported: dict[str, tuple[str, ...]] | None = None,
    rows: tuple[str, ...] = ("r1", "r2", "r3"), mandatory: tuple[str, ...] = ("r1", "r2"),
) -> pick_report.Checked:
    supported = supported or {}
    return pick_report.Checked(
        error=None, covered=covered, supported=supported, lost=tuple(row for row in rows if row not in covered and row not in supported),
        mandatory=frozenset(mandatory), strength=strength or {row: 2 for row in covered}, pages=pages, skills=skills, skills_total=20,
    )


def test_each_check_gets_its_own_verdict_and_a_real_loss_on_a_mandatory_row_is_worse_whatever_else_improved() -> None:
    rows = (
        pick_report.Row("r1", "Kubernetes in production", "met", True, ("b-1",), ()),
        pick_report.Row("r2", "Terraform", "met", True, ("b-2",), ()),
        pick_report.Row("r3", "A talk or two", "met", False, ("o-1",), ()),
    )
    posting = pick_report.Posting("p|1", "Staff Engineer", "Acme", "2026-10-01", rows, 0)
    old = _checked(covered=("r1", "r2", "r3"), skills=6)

    same = pick_report.verdicts(posting, old, _checked(covered=("r1", "r2", "r3"), skills=6))
    assert set(same) == set(pick_report.CHECKS) and set(same.values()) == {pick_report.SAME}
    # More skills and stronger evidence, but a mandatory row has NOTHING now (a real loss): WORSE, and nothing offsets it.
    lost = pick_report.verdicts(posting, old, _checked(covered=("r1", "r3"), skills=14, strength={"r1": 3, "r3": 2}))
    assert (lost["mandatory coverage"], lost["cited line kept"], lost["skills kept"], lost["evidence strength"], lost["overall"]) == (
        pick_report.WORSE, pick_report.WORSE, pick_report.BETTER, pick_report.BETTER, pick_report.WORSE)
    # THE TWO THINGS THAT WERE MERGED: the cited line is not shown, but another shown line supports the same row.
    # That is not lost mandatory coverage; the stricter check beside it says the cited line went.
    moved = pick_report.verdicts(posting, old, _checked(covered=("r1", "r3"), supported={"r2": ("b-9",)}, skills=6))
    assert (moved["mandatory coverage"], moved["cited line kept"], moved["overall"]) == (pick_report.SAME, pick_report.WORSE, pick_report.WORSE)
    # ... and the other way: a row that had only another line's support now shows the line the assessment cites.
    back = pick_report.verdicts(posting, _checked(covered=("r1", "r3"), supported={"r2": ("b-9",)}, skills=6), old)
    assert (back["mandatory coverage"], back["cited line kept"], back["overall"]) == (pick_report.SAME, pick_report.BETTER, pick_report.BETTER)
    # A nice-to-have row lost is reported apart, never as mandatory coverage.
    nice = pick_report.verdicts(posting, old, _checked(covered=("r1", "r2"), skills=6))
    assert (nice["mandatory coverage"], nice["cited line kept"], nice["other coverage"], nice["overall"]) == (pick_report.SAME, pick_report.SAME, pick_report.WORSE, pick_report.WORSE)
    better = pick_report.verdicts(posting, _checked(covered=("r1",), skills=6), _checked(covered=("r1", "r2"), skills=6))
    assert (better["mandatory coverage"], better["overall"]) == (pick_report.BETTER, pick_report.BETTER)
    over = pick_report.verdicts(posting, old, _checked(covered=("r1", "r2", "r3"), skills=6, pages=3))
    assert (over["page fit"], over["overall"]) == (pick_report.WORSE, pick_report.WORSE)
    # 0.1.11.5 (c): a tree whose selection counts no page fits when it is within its cap of bullets, whatever a tree
    # that fits to pages measured; past the cap it does not.
    capped = pick_report.Checked(**{**old.__dict__, "pages": None, "bullets": 20, "max_bullets": 20})
    assert capped.fits and pick_report.verdicts(posting, old, capped)["page fit"] == pick_report.SAME
    assert "pages not counted, bullets 20/20 fits" in pick_report._line("select", capped) and "pages 2/2 fits" in pick_report._line("select", old)
    assert not pick_report.Checked(**{**old.__dict__, "pages": None, "bullets": 21, "max_bullets": 20}).fits


def test_a_role_or_project_the_title_names_that_shows_no_line_is_counted_and_has_its_own_verdict() -> None:
    """0.1.10.11 PICK v5: the report says, per posting, which entries the title names and whether each shows a line."""

    from gigai.scout.master_resume import parse_master

    master = parse_master((REPO / "tests" / "evals" / "fixtures" / "pick" / "master.md").read_text(encoding="utf-8"))
    row = pick_report.Row("r1", "Design and operate the agent runtime in production", "met", True, ("hal-01",), ())
    posting = pick_report.Posting("p|1", "Staff Backend Engineer, Agent Runtime", "Acme", "2026-10-01", (row,), 0)

    def final(**entries: list[str]) -> dict[str, object]:
        return {
            "summary": [], "entries": {name.replace("_", "-"): bullets for name, bullets in entries.items()}, "other": [], "skills": [], "pages": 2,
            "empty_entries": [], "date_order": True, "conflicts": [],
        }

    titled = ("p-loom", "p-relay", "p-gone")  # an id the master does not hold is not counted
    dropped = pick_report.check(master, posting, (), final(r_hal=["hal-01"], r_qui=["qui-01"]), titled)
    kept = pick_report.check(master, posting, (), final(r_hal=["hal-01"], p_loom=["loom-01"], p_relay=["relay-02"]), titled)
    assert dropped.titled == kept.titled == ("p-loom", "p-relay")
    assert dropped.titled_dropped == ("p-loom", "p-relay") and kept.titled_dropped == ()
    # Twelve roles and projects hold lines: two show a line in the first selection, three in the second.
    assert (len(dropped.zero_entries), len(kept.zero_entries)) == (10, 9) and "r-qui" in kept.zero_entries
    line = pick_report._line("select", kept)  # noqa: SLF001 - the one printed line of a selection
    assert "roles and projects with no line shown: 9 | the title names: p-loom 1/8, p-relay 1/8" in line
    assert "the title names: p-loom 0/8, p-relay 0/8" in pick_report._line("select", dropped)  # noqa: SLF001
    # The verdict is its own check: better when an entry the title names shows a line it did not, WORSE the other way;
    # it never touches mandatory coverage.
    better = pick_report.verdicts(posting, dropped, kept)
    assert (better["title entry kept"], better["mandatory coverage"], better["overall"]) == (pick_report.BETTER, pick_report.SAME, pick_report.BETTER)
    worse = pick_report.verdicts(posting, kept, dropped)
    assert (worse["title entry kept"], worse["mandatory coverage"], worse["overall"]) == (pick_report.WORSE, pick_report.SAME, pick_report.WORSE)
    assert pick_report.verdicts(posting, kept, kept)["title entry kept"] == pick_report.SAME


def test_a_row_is_in_or_supported_by_another_shown_line_which_is_named_or_a_real_loss() -> None:
    """On the pick eval's synthetic master: the report's three states of a row, by the selector's own word rule."""

    from gigai.scout import master_selection as ms
    from gigai.scout.master_resume import parse_master

    master = parse_master((REPO / "tests" / "evals" / "fixtures" / "pick" / "master.md").read_text(encoding="utf-8"))

    def row(row_id: str, requirement: str, *lines: str, mandatory: bool = True) -> pick_report.Row:
        return pick_report.Row(row_id, requirement, "met", mandatory, tuple(lines), (), ms.word_supporters(master, requirement))

    rows = (
        row("r1", "Experience running an on-call rotation.", "pel-06"),  # hal-06 and pel-10 say "on-call rotation" too
        row("r2", "Hands-on background in distributed systems at scale.", "pel-01"),  # no line shares its words
        row("r3", "Define SLOs and build burn-rate alerting on Prometheus and Grafana.", "qui-03"),
        row("r4", "Operating Kafka clusters.", "pel-11", mandatory=False),
    )
    posting = pick_report.Posting("p|1", "Staff SRE", "Acme", "2026-10-01", rows, 0)

    def final(*shown: str) -> dict[str, object]:
        return {"summary": ["sum-ai"], "entries": {"r-hal": [item for item in shown if item.startswith("hal")], "r-x": [item for item in shown if not item.startswith("hal")]},
                "other": [], "skills": ["Kafka"], "pages": 2, "max_pages": 2, "empty_entries": [], "date_order": True, "conflicts": []}

    # No cited line of r1 or r2 is shown. Another shown line supports r1 by words (it is NAMED); nothing supports r2.
    cut = pick_report.check(master, posting, (), final("hal-06", "qui-03"))
    assert cut.covered == ("r3",) and cut.supported == {"r1": ("hal-06",)} and cut.lost == ("r2", "r4")
    assert (cut.count(pick_report.IN, mandatory=True), cut.count(pick_report.SUPPORTED, mandatory=True), cut.count(pick_report.LOST, mandatory=True)) == (1, 1, 1)
    assert cut.count(pick_report.LOST, mandatory=False) == 1 and cut.count(pick_report.LOST) == 2
    assert [pick_report._state(cut, item) for item in rows] == ["cited line cut, supported by hal-06", "REAL LOSS", "in", "REAL LOSS"]  # noqa: SLF001
    line = pick_report._line("select", cut)  # noqa: SLF001
    assert "requirement rows: cited line in 1, cited line cut but another shown line supports it 1, REAL LOSS 2 (mandatory: in 1, supported 1, REAL LOSS 1)" in line
    # A cited line that the selector left out because a better line says the same is stood for by that line.
    twin = pick_report.check(master, posting, (), {**final("hal-06", "qui-03"), "duplicates": {"pel-06": "hal-06"}})
    assert twin.covered == ("r1", "r3") and twin.shown_per_row["r1"] == ("hal-06",) and not twin.supported
    # With the cited lines shown every row is in.
    kept = pick_report.check(master, posting, (), final("hal-06", "qui-03", "pel-06", "pel-01", "pel-11"))
    assert kept.covered == ("r1", "r2", "r3", "r4") and not kept.supported and not kept.lost
    # Against a selection that showed the cited lines: one mandatory row is a real loss (WORSE), the other only moved.
    found = pick_report.verdicts(posting, kept, cut)
    assert (found["mandatory coverage"], found["cited line kept"], found["other coverage"]) == (pick_report.WORSE, pick_report.WORSE, pick_report.WORSE)
    only_moved = pick_report.verdicts(posting, kept, pick_report.check(master, posting, (), final("hal-06", "qui-03", "pel-01", "pel-11")))
    assert (only_moved["mandatory coverage"], only_moved["cited line kept"]) == (pick_report.SAME, pick_report.WORSE)


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
    assert "REAL LOSS (old: in)" in out and _snapshot(built) == before


def test_no_request_can_be_made_while_the_report_runs_and_cache_writes_and_the_mount_probe_are_held() -> None:
    from gigai import journal, workpad

    with pick_report._no_network():  # noqa: SLF001
        with pytest.raises(OSError, match="makes no request"):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("127.0.0.1", 9))
    real, real_probe = workpad.scratch_cache_path, journal._require_mount_probes  # noqa: SLF001
    with pick_report._hold_cache_writes():  # noqa: SLF001
        assert journal.scratch_cache_path is not real
        assert journal.scratch_cache_path(Path("/nonexistent"), "journal-publishers.sqlite", create=True) is None
        # The journal's mount probe (temp files made and removed in the workpad's scratch folder) is not run.
        assert journal._require_mount_probes is not real_probe and journal._require_mount_probes(Path("/nonexistent")) is None  # noqa: SLF001
    assert journal.scratch_cache_path is real and workpad.scratch_cache_path is real and journal._require_mount_probes is real_probe  # noqa: SLF001


def test_every_sqlite_file_is_opened_read_only_while_the_report_runs(tmp_path: Path) -> None:
    """``_read_only_sqlite``: a plain path, a URI and a write transaction all become read-only; SQLite refuses a write."""

    path = tmp_path / "some.sqlite"
    made = sqlite3.connect(path)
    made.execute("CREATE TABLE kept (n INTEGER)")
    made.execute("INSERT INTO kept VALUES (7)")
    made.commit()
    made.close()
    before = (path.read_bytes(), path.stat().st_mtime_ns, sorted(item.name for item in tmp_path.iterdir()))
    real = sqlite3.connect
    del pick_report.OPENED[:]
    with pick_report._read_only_sqlite():  # noqa: SLF001
        for database, uri in ((path, False), (str(path), False), (f"file:{path}", True), (f"file:{path}?mode=rwc&cache=private", True)):
            connection = sqlite3.connect(database, timeout=1.0, isolation_level=None, uri=uri) if uri else sqlite3.connect(database, timeout=1.0, isolation_level=None)
            # The product's readers begin a write transaction and only read under it: here it is a read transaction.
            connection.execute("BEGIN IMMEDIATE")
            assert connection.execute("SELECT n FROM kept").fetchone() == (7,)
            connection.commit()
            for write in ("INSERT INTO kept VALUES (8)", "CREATE TABLE more (n INTEGER)", "DELETE FROM kept"):
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    connection.execute(write)
            connection.close()
        # A file that does not exist is not created; a database in memory is left alone.
        with pytest.raises(sqlite3.OperationalError):
            sqlite3.connect(tmp_path / "missing.sqlite").execute("SELECT 1")
        memory = sqlite3.connect(":memory:")
        memory.execute("CREATE TABLE fine (n INTEGER)")
        memory.close()
    assert sqlite3.connect is real, "put back after the run"
    assert pick_report.OPENED == [("some.sqlite", "mode=ro")] * 4 + [("missing.sqlite", "mode=ro")]
    assert (path.read_bytes(), path.stat().st_mtime_ns, sorted(item.name for item in tmp_path.iterdir())) == before, "no journal, no -wal, no -shm, no new file"


def test_the_report_opens_no_sqlite_file_for_writing_and_never_opens_the_pipeline_s(built: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """THE PROOF, in this process: every ``sqlite3.connect`` the run makes, and every file opened for writing, is seen."""

    home = built / "home"
    (pipeline,) = home.glob("scout/*/pipeline/pipeline.sqlite")
    assert pipeline.stat().st_size > 0
    connects: list[tuple[str, bool]] = []
    writes: list[str] = []
    real_connect, real_open = sqlite3.connect, os.open

    def connect(database, *args: object, **kwargs: object):  # noqa: ANN001, ANN202
        connects.append((os.fspath(database), bool(kwargs.get("uri", False))))
        return real_connect(database, *args, **kwargs)  # type: ignore[arg-type]

    def os_open(path, flags, *args: object, **kwargs: object):  # noqa: ANN001, ANN202
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) and os.fspath(path).startswith(str(built)):
            writes.append(os.fspath(path))
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sqlite3, "connect", connect)  # under the report's own wrapper: what SQLite is really asked for
    monkeypatch.setattr(os, "open", os_open)
    before = _snapshot(built)
    code = pick_report.main(["--home", str(home), "--limit", "2"])
    out = capsys.readouterr().out
    monkeypatch.undo()

    assert code == 0 and _snapshot(built) == before
    assert writes == [], "no file under the home or its workpads was opened for writing or made"
    files = [(database, uri) for database, uri in connects if database != ":memory:"]
    assert files and all(uri and database.startswith("file:") and database.endswith("mode=ro") for database, uri in files), "every SQLite file: URI mode=ro"
    names = {Path(database.partition("?")[0]).name for database, _uri in files}
    assert "registry.sqlite" in names and names <= {"registry.sqlite", "journal-publishers.sqlite"}
    assert not [database for database, _uri in connects if "pipeline" in database], "pipeline.sqlite is not opened at all"
    assert out.rstrip().endswith("pipeline.sqlite was not opened.")
    assert {name for name, _mode in pick_report.OPENED} == names


def test_a_second_connection_writing_the_pipeline_s_file_during_the_report_neither_fails_it_nor_is_the_file_touched(built: Path) -> None:
    """The orchestrator's run had the operator's server writing ``pipeline.sqlite``: the report must not be what touches it.

    A second connection holds an open WRITE transaction on the home's ``pipeline.sqlite`` (its lock, its ``-wal`` and
    ``-shm`` in place) for the whole run.  The report finishes, and that file, its ``-wal`` and ``-shm`` and every
    other file and folder are, byte for byte and to the modification time, what they were before the report began."""

    home = built / "home"
    (pipeline,) = home.glob("scout/*/pipeline/pipeline.sqlite")
    writer = sqlite3.connect(pipeline, timeout=1.0, isolation_level=None)
    try:
        assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("CREATE TABLE pick_report_second_writer (n INTEGER)")
        writer.execute("INSERT INTO pick_report_second_writer VALUES (1)")
        held = _snapshot(built)
        watched = {name: value for name, value in held.items() if "pipeline.sqlite" in name}
        assert len(watched) == 3, "the file, its -wal and its -shm, while the writer holds them"

        beside = _run("--home", str(home), "--limit", "2")

        after = _snapshot(built)
        assert beside.returncode == 0, beside.stderr[-800:]
        assert {name: value for name, value in after.items() if "pipeline.sqlite" in name} == watched
        assert after == held, sorted(name for name in set(held) | set(after) if held.get(name) != after.get(name))[:10]
        assert beside.stdout.rstrip().endswith("pipeline.sqlite was not opened.") and "RESULT" not in beside.stdout
        # The writer's transaction is still its own: nothing took its lock or ended it.
        assert writer.in_transaction and writer.execute("SELECT n FROM pick_report_second_writer").fetchone() == (1,)
    finally:
        writer.execute("ROLLBACK") if writer.in_transaction else None
        writer.close()


def test_the_report_selects_each_posting_with_the_rows_its_assessment_cites(built: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """One synthetic assessment is made to cite a master line that matching by words leaves out (and put back after).

    With the citations (the default, what a tailoring of that job gets) the final selection shows the line and the
    row is ``in``; with ``--no-citations`` the same row is not in.  The file is restored to the byte and the moment."""

    from gigai.scout import tailor_master as tm
    from gigai.scout.target_resolution import home_scout_target

    home = built / "home"
    path = sorted(home.glob("scout/*/quick_assess/job/*.json"))[0]
    profile_id = next(p.name for p in (home / "scout").glob("*/quick_assess_tailored/profile_*") if p.is_dir())
    original, stat = path.read_bytes(), path.stat()
    stored = tm.stored_master(home, home_scout_target(home))
    assert stored is not None
    master = stored.master
    payloads: list[dict[str, object]] = []
    answers: list[dict[str, object]] = []
    real = pick_report.pick_probe.probe

    def probe(payload: dict[str, object]) -> dict[str, object]:
        payloads.append(payload)
        answers.append(real(payload))
        return answers[-1]

    monkeypatch.setattr(pick_report.pick_probe, "probe", probe)
    args = ["--home", str(home), "--limit", "50", "--profile", profile_id, "--path", "select"]
    try:
        # By words alone: find a bullet the selection of THAT posting does not show.
        assert pick_report.main([*args, "--no-citations"]) == 0
        capsys.readouterr()
        record = json.loads(original)
        text = record["posting_text"]
        case = next(case for case in payloads[-1]["cases"] if case["posting"]["text"] == text)  # type: ignore[index, union-attr]
        final = answers[-1]["results"][case["key"]]["select"]  # type: ignore[index]
        shown = {bullet for bullets in final["entries"].values() for bullet in bullets}
        line = next(item for item in master.items.values() if item.kind == "bullet" and item.id not in shown and len(item.text) > 50 and master.entries[item.entry_id].section == "experience")
        record["result"]["matrix"][0].update({"resume_evidence": [line.text[:50]], "status": "met", "class": "hard"})
        path.write_bytes(json.dumps(record).encode("utf-8"))
        changed = _snapshot(built)

        assert pick_report.main([*args, "--no-citations"]) == 0
        without = capsys.readouterr().out
        assert pick_report.main(args) == 0
        with_citations = capsys.readouterr().out
        assert _snapshot(built) == changed, "the report wrote nothing"
    finally:
        path.write_bytes(original)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.read_bytes() == original and path.stat().st_mtime_ns == stat.st_mtime_ns

    # The posting went to the selector with the row its assessment cites (r1: the first row of the matrix).
    sent = next(case for case in payloads[-1]["cases"] if case["posting"]["text"] == text)["posting"]["cited"]  # type: ignore[index, union-attr]
    assert [(row["id"], row["mandatory"], row["met"], row["lines"]) for row in sent] == [("r1", True, True, [line.id])]
    assert all(case["posting"]["cited"] == [] for case in payloads[-2]["cases"]), "--no-citations sends none"  # type: ignore[index, union-attr]
    assert "selected BY WORDS ALONE (--no-citations)" in without and "selected WITH the rows its stored assessment cites" in with_citations
    # The row, in both reports: not in by words (a real loss, or another shown line supports it); in with the citation.
    row_with = next(item for item in with_citations.splitlines() if item.strip().startswith("r1 [met] ") and f"<- {line.id}" in item)
    row_without = next(item for item in without.splitlines() if item.strip().startswith("r1 [met] ") and f"<- {line.id}" in item)
    assert " in: " in row_with and row_with.rstrip().endswith(f"(shown: {line.id})")
    assert " in: " not in row_without and ("REAL LOSS" in row_without or "cited line cut, supported by " in row_without)
    assert line.text not in with_citations and line.text[:50] not in with_citations, "never a line of the master"


def test_the_probe_answers_with_ids_and_never_a_line_of_text() -> None:
    master = (REPO / "tests" / "evals" / "fixtures" / "pick" / "master.md").read_text(encoding="utf-8")
    posting = {"title": "Staff Engineer", "text": "Requirements:\n- Kubernetes in production.\n- Kafka.\n", "company": "Acme"}
    answer = pick_report.pick_probe.probe({"today": "2026-10-03", "cases": [{"key": "k", "master": master, "profile": {"titles": ["Staff Engineer"], "base_ids": None}, "posting": posting}]})
    text = json.dumps(answer)
    assert set(answer["results"]["k"]) == set(pick_report.pick_probe.PATHS) and all("error" not in final for final in answer["results"]["k"].values())
    lines = [line.split(" <!--")[0][2:] for line in master.splitlines() if line.startswith("- ") and " · " not in line]
    assert len(lines) > 60 and not [line for line in lines if line in text]
    # The roles and projects the selector says the title names, by id (none here: "Staff Engineer" names no subject).
    assert all(final["title_entries"] == {} for final in answer["results"]["k"].values())
    titled = pick_report.pick_probe.probe({"today": "2026-10-03", "paths": ["select", "fallback"], "cases": [{
        "key": "k", "master": master, "profile": {"titles": ["Staff Engineer"], "base_ids": None}, "posting": {**posting, "title": "Staff Backend Engineer, Agent Runtime"},
    }]})["results"]["k"]
    assert all(set(final["title_entries"]) == {"p-loom", "p-relay", "p-trail"} and final["entries"]["p-loom"] for final in titled.values())
