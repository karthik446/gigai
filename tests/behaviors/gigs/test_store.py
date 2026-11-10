"""P4s: a gig's stores live in its gig folder `<home>/gigs/<gig>/`, created on the gig's first run.

`ctx.store.db(gig, name)` opens `<home>/gigs/<gig>/<name>.sqlite` for a store the gig declares under
`[data] stores`; `gigai.sdk.store.migrate` steps the file's own `user_version` and refuses a newer file. Building
the `GigContext` (core does it once the gig's own command line parsed) creates the empty gig folder; `--help`,
completion and `gig list` do not. Gigs are "installed" the way test_loader.py does it; the fixture dists are
`tests/fixtures/gigs/storegig/` and `tests/fixtures/gigs/nostoregig/`.
"""

from __future__ import annotations

import logging
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from gigai.gigs.home import GigNameError, gig_folder, resolve_gig_folder
from gigai.gigs.loader import LazyGig
from gigai.sdk import GigContext
from gigai.sdk.store import GigStore, StoreError, StoreVersionError, declared_stores, migrate, set_version, version
from tests.behaviors.gigs.test_loader import _add_dist, _add_fixture, _run
from tests.support.fixture_homes import counts, home_0_1_11_13  # noqa: F401 - pytest fixture

REPO = Path(__file__).resolve().parents[3]
STORE_MANIFEST = {"gig": {"name": "storegig"}, "data": {"stores": [{"name": "notes", "engine": "sqlite"}]}}
STEPS = (
    lambda db: db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"),
    lambda db: db.execute("ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"),
)


def _context(home: Path, name: str = "storegig", manifest: dict | None = None) -> GigContext:
    return GigContext(home, name, STORE_MANIFEST if manifest is None else manifest, logging.getLogger(f"gigai.gig.{name}"))


def _tree(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def _gigai(tmp_path: Path, site: Path, home: Path, *argv: str) -> subprocess.CompletedProcess:
    return _run(tmp_path, site, *argv, env_extra={"GIGAI_HOME": str(home)})


@pytest.fixture
def site(tmp_path: Path) -> Path:
    directory = tmp_path / "site"
    directory.mkdir()
    _add_fixture(directory, "storegig")
    _add_fixture(directory, "nostoregig")
    return directory


def test_a_declared_store_opens_in_the_gig_folder_and_steps_its_version(tmp_path, site):
    home = tmp_path / "home"
    store = home / "gigs" / "storegig" / "notes.sqlite"
    opened = _gigai(tmp_path, site, home, "storegig", "open")
    assert opened.returncode == 0, opened.stderr
    assert opened.stdout == f"{store}\n"
    assert _tree(home) == ["gigs", "gigs/storegig", "gigs/storegig/notes.sqlite"]
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert version(home, store) == 0
    for known in (1, 2):
        stepped = _gigai(tmp_path, site, home, "storegig", "migrate", "--known", str(known))
        assert stepped.returncode == 0, stepped.stderr
        assert stepped.stdout == f"version {known}\n"
        assert version(home, store) == known
    added = _gigai(tmp_path, site, home, "storegig", "add", "first note")
    assert added.returncode == 0, added.stderr
    with sqlite3.connect(store) as db:
        assert db.execute("SELECT text, pinned FROM notes").fetchall() == [("first note", 0)]
    # An older build of the gig (it knows version 1) refuses the version-2 file and leaves it alone.
    older = _gigai(tmp_path, site, home, "storegig", "migrate", "--known", "1")
    assert older.returncode == 1
    assert "notes.sqlite is at version 2, newer than version 1 this gig knows" in older.stderr
    assert version(home, store) == 2
    assert _tree(home) == ["gigs", "gigs/storegig", "gigs/storegig/notes.sqlite"]


def test_a_gig_that_never_opens_a_store_gets_an_empty_gig_folder_on_its_first_run_and_none_before(tmp_path, site):
    home = tmp_path / "home"
    home.mkdir()
    for argv, code, text in (
        (("--help",), 0, "nostoregig"),
        (("nostoregig", "--help"), 0, "Usage: gigai nostoregig"),
        (("nostoregig",), 2, "Usage: gigai nostoregig"),
        (("gig", "list"), 0, "nostoregig"),
    ):
        completed = _gigai(tmp_path, site, home, *argv)
        assert completed.returncode == code, (argv, completed.stdout, completed.stderr)
        assert text in completed.stdout + completed.stderr, argv
        assert "Traceback" not in completed.stderr, argv
    completed = _run(tmp_path, site, env_extra={
        "GIGAI_HOME": str(home), "_GIGAI_COMPLETE": "bash_complete", "COMP_WORDS": "gigai nostoregig ", "COMP_CWORD": "2",
    })
    assert completed.returncode == 0, completed.stderr
    assert "ping" in completed.stdout
    assert not (home / "gigs").exists()
    ping = _gigai(tmp_path, site, home, "nostoregig", "ping")
    assert ping.returncode == 0, ping.stderr
    assert ping.stdout == "pong\n"
    assert _tree(home) == ["gigs", "gigs/nostoregig"]
    again = _gigai(tmp_path, site, home, "nostoregig", "ping")
    assert again.returncode == 0, again.stderr
    assert _tree(home) == ["gigs", "gigs/nostoregig"]


@pytest.mark.parametrize(("argv", "code", "text"), [
    (("nostoregig", "ping", "--help"), 0, "Usage: gigai nostoregig ping"),
    (("nostoregig", "nosuchverb"), 2, "No such command"),
])
def test_any_run_that_reaches_the_gig_creates_its_gig_folder(tmp_path, site, argv, code, text):
    # Accepted (P4s fix round, F4): once the gig's own group parsed, the run reached the gig and gets its folder,
    # even when the subcommand only prints its help or is unknown.
    home = tmp_path / "home"
    completed = _gigai(tmp_path, site, home, *argv)
    assert completed.returncode == code, completed.stderr
    assert text in completed.stdout + completed.stderr
    assert _tree(home) == ["gigs", "gigs/nostoregig"]


def _executable_gig(tmp_path: Path) -> tuple[Path, Path]:
    site = tmp_path / "ext-site"
    _add_dist(site, "gig-ext", "1.0", {"ext": "gig_ext"}, {
        "gig_ext/__init__.py": "raise AssertionError('must not be imported')\n",
        "gig_ext/gig.toml": '[gig]\nname = "ext"\n[commands]\nexecutable = "gigai-test-ext"\n',
    })
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gigai-test-ext"
    script.write_text('#!/bin/sh\necho "ext got: $*"\n', encoding="utf-8")
    script.chmod(0o755)
    return site, bin_dir


def test_a_gig_folder_that_cannot_be_created_is_a_clean_error(tmp_path, site):
    (tmp_path / "afile").write_text("not a folder", encoding="utf-8")
    home = tmp_path / "afile" / "home"
    in_process = _gigai(tmp_path, site, home, "nostoregig", "ping")
    assert in_process.returncode == 1
    assert in_process.stdout == ""
    assert in_process.stderr.startswith("gigai nostoregig: cannot create its gig folder (")
    assert "Traceback" not in in_process.stderr
    ext_site, bin_dir = _executable_gig(tmp_path)
    forwarded = _run(tmp_path, ext_site, "ext", "one", path=bin_dir, env_extra={"GIGAI_HOME": str(home)})
    assert forwarded.returncode == 1
    assert forwarded.stdout == ""  # the executable is not launched
    assert forwarded.stderr.startswith("gigai ext: cannot create its gig folder (")
    assert "Traceback" not in forwarded.stderr


def test_shell_completion_through_a_gig_creates_no_gig_folder(tmp_path, site):
    home = tmp_path / "home"
    for words, cword in (("gigai storegig ", "2"), ("gigai storegig migrate --", "3")):
        completed = _run(tmp_path, site, env_extra={
            "GIGAI_HOME": str(home), "_GIGAI_COMPLETE": "bash_complete", "COMP_WORDS": words, "COMP_CWORD": cword,
        })
        assert completed.returncode == 0, completed.stderr
    assert "--known" in completed.stdout
    assert not home.exists()


def test_an_executable_gig_gets_no_click_context_object_and_its_gig_folder_on_launch(tmp_path, monkeypatch):
    site, bin_dir = _executable_gig(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("GIGAI_HOME", str(home))
    manifest = {"gig": {"name": "ext"}, "commands": {"executable": "gigai-test-ext"}}
    context = LazyGig("ext", EntryPoint("ext", "gig_ext", "gigai.gigs"), manifest).make_context("ext", ["one"])
    assert context.obj is None
    assert not home.exists()
    forwarded = _run(tmp_path, site, "ext", "one", path=bin_dir, env_extra={"GIGAI_HOME": str(home)})
    assert forwarded.returncode == 0, forwarded.stderr
    assert forwarded.stdout == "ext got: one\n"
    assert _tree(home) == ["gigs", "gigs/ext"]


def test_building_the_context_creates_only_the_empty_gig_folder(tmp_path):
    home = tmp_path / "home"
    context = _context(home)
    assert _tree(home) == ["gigs", "gigs/storegig"]
    assert gig_folder(home, "storegig") == resolve_gig_folder(home, "storegig") == home / "gigs" / "storegig"
    assert context.store == GigStore(home, "storegig", STORE_MANIFEST)


def test_opening_a_store_touches_no_other_file(tmp_path):
    home = tmp_path / "home"
    (home / "registry.sqlite").parent.mkdir(parents=True)
    (home / "registry.sqlite").write_bytes(b"not opened")
    db = _context(home).store.db("storegig", "notes")
    db.close()
    assert _tree(home) == ["gigs", "gigs/storegig", "gigs/storegig/notes.sqlite", "registry.sqlite"]
    assert (home / "registry.sqlite").read_bytes() == b"not opened"


def test_migrate_steps_zero_one_two_and_reruns_as_a_no_op(tmp_path):
    store = _context(tmp_path).store
    store.db("storegig", "notes").close()
    path = store.path("storegig", "notes")
    assert migrate(tmp_path, path, STEPS[:1]) == 1
    assert version(tmp_path, path) == 1
    assert migrate(tmp_path, path, STEPS) == 2
    assert version(tmp_path, path) == 2
    assert migrate(tmp_path, path, STEPS) == 2  # nothing left to run: re-running a step would fail on the existing column
    with sqlite3.connect(path) as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(notes)")] == ["id", "text", "pinned"]


def test_migrate_refuses_a_file_newer_than_the_gig_knows_and_changes_nothing(tmp_path):
    store = _context(tmp_path).store
    store.db("storegig", "notes").close()
    path = store.path("storegig", "notes")
    set_version(tmp_path, path, 3)
    ran = []
    with pytest.raises(StoreVersionError, match="at version 3, newer than version 2 this gig knows"):
        migrate(tmp_path, path, (ran.append, ran.append))
    assert ran == []
    assert version(tmp_path, path) == 3


def test_a_failing_step_rolls_back_with_its_version(tmp_path):
    store = _context(tmp_path).store
    store.db("storegig", "notes").close()
    path = store.path("storegig", "notes")

    def broken(db: sqlite3.Connection) -> None:
        db.execute("CREATE TABLE half (id INTEGER)")
        raise RuntimeError("step 2 failed")

    with pytest.raises(RuntimeError, match="step 2 failed"):
        migrate(tmp_path, path, (STEPS[0], broken))
    assert version(tmp_path, path) == 1
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"notes"}


@pytest.mark.parametrize("name", ["../notes", "a/b", "..", ".", "", "Notes", "notes.sqlite", "a\\b", "café"])
def test_unsafe_store_and_gig_names_are_refused(tmp_path, name):
    home = tmp_path / "home"
    store = _context(home).store
    with pytest.raises(GigNameError):
        store.db("storegig", name)
    with pytest.raises(GigNameError):
        store.db(name, "notes")
    with pytest.raises(GigNameError):
        _context(home, name)
    assert _tree(home) == ["gigs", "gigs/storegig"]


def test_only_the_gigs_own_declared_sqlite_stores_open(tmp_path):
    manifest = {
        "gig": {"name": "storegig"},
        "data": {"stores": [{"name": "notes", "engine": "sqlite"}, {"name": "blobs", "engine": "files"}]},
    }
    store = _context(tmp_path, manifest=manifest).store
    with pytest.raises(StoreError, match="not declared under \\[data\\] stores"):
        store.db("storegig", "undeclared")
    with pytest.raises(StoreError, match="not declared"):
        store.db("storegig", "blobs")
    with pytest.raises(StoreError, match="cannot open a store in the gig folder of 'other'"):
        store.db("other", "notes")
    assert not (tmp_path / "gigs" / "other").exists()
    assert declared_stores(manifest) == {"notes"}
    assert declared_stores({"evals": {}}) == {"evals"}  # the evals store is implied by [evals]
    assert declared_stores({}) == frozenset()


def test_the_version_helper_opens_only_existing_gig_store_files(tmp_path):
    registry = tmp_path / "registry.sqlite"
    sqlite3.connect(registry).close()
    for path in (registry, tmp_path / "gigs" / "registry.sqlite", tmp_path / "gigs" / "x" / "notes.db"):
        with pytest.raises(StoreError, match="not a gig store file"):
            version(tmp_path, path)
        with pytest.raises(StoreError, match="not a gig store file"):
            set_version(tmp_path, path, 1)
    missing = tmp_path / "gigs" / "storegig" / "notes.sqlite"
    with pytest.raises(StoreError, match="no store file"):
        migrate(tmp_path, missing, STEPS)
    assert not missing.exists()
    with pytest.raises(ValueError, match="invalid store version"):
        set_version(tmp_path, missing, -1)


def _sqlite_at(path: Path, user_version: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {user_version}")
    return path


def _helpers_refuse(home: Path, path: Path, match: str) -> None:
    with pytest.raises(StoreError, match=match):
        version(home, path)
    with pytest.raises(StoreError, match=match):
        set_version(home, path, 9)
    with pytest.raises(StoreError, match=match):
        migrate(home, path, STEPS * 5)


def test_a_0_1_11_13_workpad_state_file_and_another_homes_store_are_refused(tmp_path):
    home = tmp_path / "home"
    # Shaped like a gig store (`.../gigs/<a>/<b>.sqlite`), but it is core's and Scout's workpad file.
    state = _sqlite_at(home / "workpads" / "projects" / "p1" / "gigs" / "gig_0a1b2c3d" / "state.sqlite", 3)
    _helpers_refuse(home, state, "not a gig store file")
    other = tmp_path / "other-home"
    other_store = _context(other).store
    other_store.db("storegig", "notes").close()
    _helpers_refuse(home, other_store.path("storegig", "notes"), "not a gig store file")
    assert version(other, other_store.path("storegig", "notes")) == 0
    with sqlite3.connect(state) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)
    assert not (home / "gigs").exists()


def test_symlinks_in_the_gig_folder_are_refused(tmp_path):
    home = tmp_path / "home"
    registry = _sqlite_at(home / "registry.sqlite", 3)
    store = _context(home).store
    # A store file that is a symlink to registry.sqlite.
    link = home / "gigs" / "storegig" / "notes.sqlite"
    link.symlink_to(registry)
    _helpers_refuse(home, link, "refusing a symlink in the gig folder")
    with pytest.raises(StoreError, match="refusing a symlink in the gig folder"):
        store.db("storegig", "notes")
    # A gig folder that is a symlink to the home.
    _sqlite_at(home / "notes.sqlite", 4)
    (home / "gigs" / "viahome").symlink_to(home)
    _helpers_refuse(home, home / "gigs" / "viahome" / "notes.sqlite", "refusing a symlink in the gig folder")
    _helpers_refuse(home, home / "gigs" / "viahome" / "registry.sqlite", "not a gig store file")
    with pytest.raises(StoreError, match="refusing a symlink in the gig folder"):
        _context(home, "viahome").store.db("viahome", "notes")
    # A `gigs` folder that is a symlink.
    elsewhere = tmp_path / "elsewhere"
    _context(elsewhere).store.db("storegig", "notes").close()
    linked_home = tmp_path / "linked-home"
    linked_home.mkdir()
    (linked_home / "gigs").symlink_to(elsewhere / "gigs")
    _helpers_refuse(linked_home, linked_home / "gigs" / "storegig" / "notes.sqlite", "refusing a symlink")
    with pytest.raises(StoreError, match="refusing a symlink"):
        _context(linked_home).store.db("storegig", "notes")
    with sqlite3.connect(registry) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)
    with sqlite3.connect(home / "notes.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone() == (4,)


def test_registry_is_a_reserved_store_name(tmp_path):
    manifest = {"gig": {"name": "storegig"}, "data": {"stores": [{"name": "registry", "engine": "sqlite"}]}}
    store = _context(tmp_path, manifest=manifest).store
    with pytest.raises(StoreError, match="store name 'registry' is reserved for core"):
        store.db("storegig", "registry")
    with pytest.raises(StoreError, match="reserved for core"):
        store.path("storegig", "registry")
    assert _tree(tmp_path) == ["gigs", "gigs/storegig"]


def test_two_runs_at_once_apply_each_step_once(tmp_path):
    store = _context(tmp_path).store
    store.db("storegig", "notes").close()
    path = store.path("storegig", "notes")
    in_step_0, release = threading.Event(), threading.Event()
    ran: list[str] = []
    results: list[object] = []

    def step_0(db: sqlite3.Connection) -> None:
        ran.append("0")
        db.execute("CREATE TABLE notes (text TEXT)")
        db.execute("INSERT INTO notes VALUES ('from step 0')")
        in_step_0.set()
        assert release.wait(10)

    def step_1(db: sqlite3.Connection) -> None:
        ran.append("1")
        db.execute("INSERT INTO notes VALUES ('from step 1')")

    def run() -> None:
        try:
            results.append(migrate(tmp_path, path, (step_0, step_1)))
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            results.append(exc)

    first = threading.Thread(target=run)
    first.start()
    assert in_step_0.wait(10)
    second = threading.Thread(target=run)  # its BEGIN IMMEDIATE waits for the first run's transaction
    second.start()
    time.sleep(0.3)
    release.set()
    first.join(10)
    second.join(10)
    assert results == [2, 2]
    assert sorted(ran) == ["0", "1"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT text FROM notes").fetchall() == [("from step 0",), ("from step 1",)]
    assert version(tmp_path, path) == 2


@pytest.mark.parametrize("step", [
    lambda db: (db.execute("CREATE TABLE t (x)"), db.commit()),
    lambda db: db.executescript("CREATE TABLE t (x);"),
], ids=["commit", "executescript"])
def test_a_step_that_ends_the_transaction_itself_is_an_error(tmp_path, step):
    store = _context(tmp_path).store
    store.db("storegig", "notes").close()
    path = store.path("storegig", "notes")
    with pytest.raises(StoreError, match="a step committed on its own"):
        migrate(tmp_path, path, (step,))
    assert version(tmp_path, path) == 0


def test_a_fixture_gig_run_leaves_the_0_1_11_13_home_as_it_was(tmp_path, site, home_0_1_11_13):
    home = home_0_1_11_13

    def registry_version() -> int:
        with sqlite3.connect(f"{(home / 'registry.sqlite').as_uri()}?mode=ro", uri=True) as db:
            return db.execute("PRAGMA user_version").fetchone()[0]

    before_counts = counts(home)
    assert registry_version() == 3
    # Taken after the counts: their read-only opens of WAL databases leave empty -wal/-shm files behind.
    before_files = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    for argv in (("storegig", "migrate"), ("storegig", "add", "a note"), ("nostoregig", "ping")):
        completed = _gigai(tmp_path, site, home, *argv)
        assert completed.returncode == 0, completed.stderr
    assert registry_version() == 3
    after_files = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    assert {path: data for path, data in after_files.items() if path in before_files} == before_files
    assert sorted(path.relative_to(home).as_posix() for path in after_files.keys() - before_files.keys()) == [
        "gigs/storegig/notes.sqlite"
    ]
    assert sorted(path.relative_to(home).as_posix() for path in (home / "gigs").rglob("*")) == [
        "gigs/nostoregig", "gigs/storegig", "gigs/storegig/notes.sqlite"
    ]
    after_counts = "".join(line for line in counts(home).splitlines(keepends=True) if not line.startswith("gigs/"))
    (tmp_path / "before.counts").write_text(before_counts, encoding="utf-8")
    (tmp_path / "after.counts").write_text(after_counts, encoding="utf-8")
    compared = subprocess.run(
        [sys.executable, str(REPO / "tools" / "migrate_counts.py"), str(tmp_path / "before.counts"), str(tmp_path / "after.counts")],
        capture_output=True, text=True, check=False,
    )
    assert compared.returncode == 0, compared.stdout + compared.stderr
    assert compared.stdout.splitlines()[-1] == "0 differences"
