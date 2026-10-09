"""SI1 (0.1.11.7): what a search sees when the search index cannot be trusted, synthetic boards only.

Every case of the spike's section 4: a missing, zero-byte, header-damaged,
truncated or noise-damaged file, another schema version, an unfinished build,
a company file that moved on, a reader beside an open write transaction, two
readers beside a writer, a rebuild in place, a deleted or replaced file, a
lock held past the timeout. In every one the read is either the exact rows or
an explicit "unavailable" with no rows: never some rows, never an exception.

Nothing here touches the network or a real home.
"""

from __future__ import annotations

import os
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import sys
import threading

import pytest

from gigai.scout.find_jobs import search_index
from gigai.scout.find_jobs.ats_board_clients import matches_roles
from gigai.scout.find_jobs.company_index import CompanyIndex
from gigai.scout.find_jobs.search_index import IndexQuery

from .test_search_index import BOARDS, make_home, scan, write_board

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

TYPED = ("staff engineer", "data engineer")
QUERY = IndexQuery(titles=TYPED)
UNAVAILABLE = {
    search_index.MISSING, search_index.DAMAGED, search_index.OTHER_SCHEMA, search_index.NOT_BUILT, search_index.STALE,
    search_index.BUSY, search_index.NO_FTS5,
}


def unverify(home: Path) -> None:
    """Forget the writer's ``meta.verified``: the reader then checks the file itself, as it did before SI2."""

    search_index.close(home)
    conn = sqlite3.connect(search_index.search_index_path(home))
    try:
        conn.execute("DELETE FROM meta WHERE key = 'verified'")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        conn.close()


@pytest.fixture
def home(tmp_path: Path):
    """A built home whose index is NOT marked verified: the SI1 cases below are about the reader's own check."""

    path = make_home(tmp_path)
    unverify(path)
    yield path
    search_index.close(path)


def served(home: Path) -> list[tuple[str, str, str, str]] | str:
    """The index's rows through the rule, or the reason it is unavailable (then it gave no row and no count)."""

    found = search_index.candidates(home, QUERY)
    counted = search_index.title_counts(home, QUERY)
    if not found.available:
        assert found.rows == () and found.counts == () and found.reason in UNAVAILABLE, found
        return found.reason
    assert counted.available or counted.reason in UNAVAILABLE
    return [(row.posted, row.url, row.board, row.posting_id) for row in found.rows if matches_roles(row.title, TYPED)]


def truth(home: Path) -> list[tuple[str, str, str, str]]:
    return scan(home, TYPED, False, None)


def settle(home: Path) -> Path:
    """Close this thread's connections and drop the (empty, checkpointed) WAL files: the index is the one file."""

    search_index.close(home)
    path = search_index.search_index_path(home)
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    return path


def assert_falls_back_then_rebuilds(home: Path, reasons: set[str]) -> None:
    expected = truth(home)
    assert served(home) in reasons
    state = search_index.status(home)
    assert not state.available and state.reason in reasons and state.postings == 0
    assert search_index.title_counts(home, QUERY).counts == ()
    assert search_index.is_built(home) in (True, False)  # never raises
    assert search_index.upsert_company(home, "greenhouse:example-ai") in (True, False)  # never raises
    assert search_index.refresh(home) in (True, False)
    rebuilt = search_index.rebuild_from_index(home)
    assert rebuilt.available, rebuilt
    assert served(home) == expected and expected
    assert search_index.is_built(home)


# ---------------------------------------------------------------------------
# A file that cannot be trusted
# ---------------------------------------------------------------------------


def test_missing_file(home: Path) -> None:
    path = settle(home)
    path.unlink()
    assert served(home) == search_index.MISSING
    assert not path.exists() and not search_index.is_built(home)  # a read never creates it
    assert_falls_back_then_rebuilds(home, {search_index.MISSING})


def test_zero_byte_file(home: Path) -> None:
    settle(home).write_bytes(b"")
    assert not search_index.is_built(home)
    assert_falls_back_then_rebuilds(home, {search_index.NOT_BUILT})


def test_first_100_bytes_zeroed(home: Path) -> None:
    path = settle(home)
    data = path.read_bytes()
    path.write_bytes(b"\0" * 100 + data[100:])
    assert not search_index.is_built(home)
    assert_falls_back_then_rebuilds(home, {search_index.DAMAGED})


def test_truncated_file(home: Path) -> None:
    path = settle(home)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    assert_falls_back_then_rebuilds(home, {search_index.DAMAGED})


def test_noise_in_pages_is_found_by_quick_check_even_when_the_query_would_answer(home: Path) -> None:
    """One page of noise at a time. A query alone does not notice damage in a page it does not touch: the plain SQL
    answers with the healthy rows on some of them. The reader's quick_check refuses every damaged file."""

    path = settle(home)
    healthy = path.read_bytes()
    expected = truth(home)
    page = 4096
    assert len(healthy) % page == 0 and len(healthy) // page > 20
    where, params = search_index._sql(QUERY)  # type: ignore[misc]
    noise = random.Random(11707)
    refused = quietly_wrong_without_the_check = 0
    for number in range(1, len(healthy) // page):
        settle(home)
        damaged = bytearray(healthy)
        damaged[number * page : (number + 1) * page] = noise.randbytes(page)
        path.write_bytes(bytes(damaged))
        got = served(home)
        if isinstance(got, str):
            assert got == search_index.DAMAGED, (number, got)
            refused += 1
            settle(home)
            plain = sqlite3.connect(path)
            try:
                rows = plain.execute(f"SELECT p.title {where}", params).fetchall()
                quietly_wrong_without_the_check += len(rows) > 0
            except sqlite3.Error:
                pass
            finally:
                plain.close()
        else:
            assert got == expected, number  # the noise fell on a page nothing uses (a free page): still the exact rows
    print(f"pages {len(healthy) // page}, refused {refused}, answered by the plain SQL although damaged {quietly_wrong_without_the_check}")
    assert refused >= (len(healthy) // page) // 2, refused
    assert quietly_wrong_without_the_check >= 1
    settle(home)
    path.write_bytes(healthy[: 40 * page] + noise.randbytes(4 * page) + healthy[44 * page :])
    assert_falls_back_then_rebuilds(home, {search_index.DAMAGED})


def test_quick_check_runs_once_per_open_and_after_a_stamp_change_never_per_query(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[str] = []
    opened: list[sqlite3.Connection] = []
    real = search_index._Store.reader

    def traced(self: object) -> sqlite3.Connection:
        conn = real(self)  # type: ignore[arg-type]
        if not any(conn is one for one in opened):
            opened.append(conn)
            conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(search_index._Store, "reader", traced)

    def checks() -> int:
        return sum(1 for statement in statements if "quick_check" in statement)

    search_index.close(home)
    for _ in range(5):
        assert isinstance(served(home), list)
    assert search_index.status(home).available
    assert (len(opened), checks()) == (1, 1)
    # The stamp changes (one company re-indexed): checked once more, on the same connection, and it passes there.
    assert search_index.upsert_company(home, write_board(home, 1, round_=3), verify=False)
    for _ in range(3):
        assert served(home) == truth(home)
    assert (len(opened), checks()) == (1, 2)
    search_index.close(home)
    assert served(home) == truth(home)
    assert (len(opened), checks()) == (2, 3)


# ---------------------------------------------------------------------------
# A file that is whole but not this index, or not current
# ---------------------------------------------------------------------------


def _edit(home: Path, *statements: str) -> None:
    conn = sqlite3.connect(search_index.search_index_path(home), isolation_level=None)
    try:
        for statement in statements:
            conn.execute(statement)
    finally:
        conn.close()


def test_other_schema_version(home: Path) -> None:
    _edit(home, f"UPDATE meta SET value = '{search_index.SCHEMA_VERSION + 1}' WHERE key = 'schema_version'")
    assert not search_index.is_built(home)
    assert search_index.upsert_company(home, "greenhouse:example-ai") is False  # a write never touches another layout
    assert_falls_back_then_rebuilds(home, {search_index.OTHER_SCHEMA})


def test_a_file_of_another_layout_is_emptied_by_the_rebuild(home: Path) -> None:
    path = settle(home)
    path.unlink()
    _edit(
        home,
        "PRAGMA journal_mode=WAL",
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "INSERT INTO meta VALUES ('schema_version', '0'), ('built', '1')",
        "CREATE TABLE old_rows (id INTEGER PRIMARY KEY, title TEXT)",
        "CREATE VIRTUAL TABLE old_text USING fts5(title)",
        "CREATE VIEW old_view AS SELECT title FROM old_rows",
        "CREATE TRIGGER old_trigger AFTER INSERT ON old_rows BEGIN INSERT INTO old_text(title) VALUES (new.title); END",
        "INSERT INTO old_rows (title) VALUES ('Staff Engineer')",
    )
    inode = path.stat().st_ino
    assert_falls_back_then_rebuilds(home, {search_index.OTHER_SCHEMA})
    assert path.stat().st_ino == inode  # rebuilt in place
    search_index.close(home)
    conn = sqlite3.connect(path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()
    assert not {name for name in names if name.startswith("old_")} and {"p", "pc", "boards", "meta", "ft"} <= names


def test_built_zero(home: Path) -> None:
    _edit(home, "UPDATE meta SET value = '0' WHERE key = 'built'")
    assert not search_index.is_built(home)
    assert search_index.upsert_company(home, "greenhouse:example-ai") is False
    assert_falls_back_then_rebuilds(home, {search_index.NOT_BUILT})


def test_one_board_mtime_changed(home: Path) -> None:
    expected = truth(home)
    assert served(home) == expected
    board = CompanyIndex.for_home(home).path("lever", "sample-cloud")
    found = board.stat()
    os.utime(board, ns=(found.st_atime_ns, found.st_mtime_ns + 1_000_000))
    # Changed in place, the folder itself did not move: seen at the next scan of the folder (a new process here).
    search_index.close(home)
    assert served(home) == search_index.STALE
    assert search_index.status(home).reason == search_index.STALE
    assert search_index.is_built(home)  # built, not current: only a read says stale
    assert search_index.refresh(home) and served(home) == expected


def test_one_board_changed_size_or_appeared_or_went(home: Path) -> None:
    index = CompanyIndex.for_home(home)
    board = index.path("greenhouse", "example-ai")
    found = board.stat()
    board.write_bytes(board.read_bytes() + b"\n")
    os.utime(board, ns=(found.st_atime_ns, found.st_mtime_ns))  # the same mtime, one byte more
    search_index.close(home)  # an edit in place does not move the folder: the next scan sees it
    assert served(home) == search_index.STALE
    assert search_index.refresh(home) and served(home) == truth(home)
    (index.root / "greenhouse:appeared.json").write_text("{}", encoding="utf-8")
    assert served(home) == search_index.STALE
    assert search_index.refresh(home) and served(home) == truth(home)
    (index.root / "greenhouse:appeared.json").unlink()
    assert served(home) == search_index.STALE
    assert search_index.refresh(home) and served(home) == truth(home)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

_HOLD_A_WRITE = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=5, isolation_level=None)
conn.execute("PRAGMA busy_timeout=5000")
conn.execute("BEGIN IMMEDIATE")
ids = [row[0] for row in conn.execute("SELECT id FROM p WHERE board = ?", (sys.argv[2],))]
conn.execute("DELETE FROM pc WHERE id IN (SELECT id FROM p WHERE board = ?)", (sys.argv[2],))
conn.execute("DELETE FROM p WHERE board = ?", (sys.argv[2],))
print(len(ids), flush=True)
sys.stdin.readline()
conn.execute("COMMIT")
conn.close()
print("committed", flush=True)
"""


def test_a_reader_beside_another_process_open_write_transaction_gets_the_pre_update_rows(home: Path) -> None:
    before = truth(home)
    board = "greenhouse:example-ai"
    assert any(row[2] == board for row in before)
    assert served(home) == before
    writer = subprocess.Popen(
        [sys.executable, "-I", "-c", _HOLD_A_WRITE, str(search_index.search_index_path(home)), board],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert writer.stdout is not None and writer.stdin is not None
        assert int(writer.stdout.readline()) > 0  # the rows are deleted inside the open transaction
        for _ in range(3):
            assert served(home) == before  # served, and the rows are the ones from before the write
        assert search_index.status(home).available
        writer.stdin.write("\n")
        writer.stdin.flush()
        assert writer.stdout.readline().strip() == "committed"
        assert writer.wait(timeout=10) == 0
    finally:
        if writer.poll() is None:
            writer.kill()
            writer.wait(timeout=10)
    after = served(home)
    assert after == [row for row in before if row[2] != board] != before


def test_two_readers_and_one_writer_no_errors_and_only_whole_states(home: Path) -> None:
    state_a = truth(home)
    key = write_board(home, 1, round_=3)
    state_b = truth(home)
    assert state_a != state_b
    assert search_index.upsert_company(home, key) and served(home) == state_b

    rounds = 30
    done = threading.Event()
    failures: list[BaseException] = []
    seen: dict[str, list[object]] = {"reader-1": [], "reader-2": []}
    written: list[bool] = []

    def write() -> None:
        try:
            for number in range(rounds):
                write_board(home, 1, round_=0 if number % 2 == 0 else 3)
                written.append(search_index.upsert_company(home, key))
        except BaseException as error:  # noqa: BLE001 - recorded: the test fails on it below
            failures.append(error)
        finally:
            search_index.close(home)
            done.set()

    def read(name: str) -> None:
        try:
            while not done.is_set() or len(seen[name]) < 20:
                seen[name].append(served(home))
        except BaseException as error:  # noqa: BLE001 - recorded: the test fails on it below
            failures.append(error)
        finally:
            search_index.close(home)

    threads = [threading.Thread(target=write)] + [threading.Thread(target=read, args=(name,)) for name in seen]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads)
    assert failures == []
    assert written == [True] * rounds
    for name, results in seen.items():
        rows = [result for result in results if not isinstance(result, str)]
        reasons = {result for result in results if isinstance(result, str)}
        # Between a company file's write and its re-index the read says stale (the caller scans): nothing else, ever.
        assert reasons <= {search_index.STALE}, (name, reasons)
        assert all(result in (state_a, state_b) for result in rows), name  # a whole state, never a mix of the two
        assert len(rows) >= 5, (name, len(rows), len(results))
    assert served(home) == truth(home)
    wal = Path(f"{search_index.search_index_path(home)}-wal")
    assert not wal.exists() or wal.stat().st_size < 4 * 1024 * 1024  # checkpointed after the updates


def test_rebuild_in_place_keeps_serving(home: Path) -> None:
    before = truth(home)
    path = search_index.search_index_path(home)
    inode = path.stat().st_ino
    assert served(home) == before
    reader_connection = search_index._store(home)._local.reader[0]
    inside, release = threading.Event(), threading.Event()
    results: list[object] = []

    def rebuild() -> None:
        def progress(done: int, _total: int) -> None:
            if done == 3:  # half of the company files are in, inside the one transaction
                inside.set()
                assert release.wait(timeout=60)

        try:
            results.append(search_index.rebuild_from_index(home, progress=progress))
        except BaseException as error:  # noqa: BLE001 - recorded: the test fails on it below
            results.append(error)
        finally:
            search_index.close(home)

    thread = threading.Thread(target=rebuild)
    thread.start()
    try:
        assert inside.wait(timeout=60)
        for _ in range(3):
            assert served(home) == before  # every table is dropped and half refilled in the writer; the reader sees none of it
        assert search_index.status(home).available and search_index.is_built(home)
    finally:
        release.set()
        thread.join(timeout=120)
    assert isinstance(results[0], search_index.IndexStatus) and results[0].available, results
    assert served(home) == before
    assert path.stat().st_ino == inode  # in place: never a temp file + os.replace
    assert search_index._store(home)._local.reader[0] is reader_connection  # the open connection went on serving


def test_deleted_file_is_reopened_not_served_from_the_open_connection(home: Path) -> None:
    expected = truth(home)
    assert served(home) == expected
    path = search_index.search_index_path(home)
    inode = path.stat().st_ino
    held = search_index._store(home)._local.reader[0]
    keep = path.with_name("kept-inode")  # the old inode stays alive, so the new file cannot reuse its number
    os.link(path, keep)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    # The held connection could still read the unlinked file: the reader must notice and say missing instead.
    assert held.execute("SELECT COUNT(*) FROM p").fetchone()[0] > 0
    assert served(home) == search_index.MISSING
    assert search_index.rebuild_from_index(home).available
    assert path.stat().st_ino != inode
    assert served(home) == expected
    assert search_index._store(home)._local.reader[0] is not held


def test_replaced_file_is_reopened(home: Path, tmp_path: Path) -> None:
    expected = truth(home)
    assert served(home) == expected
    path = search_index.search_index_path(home)
    # Another index over the same company files (same stamps), one company's rows taken out, swapped in by os.replace.
    other = tmp_path / "other.sqlite"
    shutil.copyfile(settle(home), other)
    conn = sqlite3.connect(other, isolation_level=None)
    conn.execute("DELETE FROM p WHERE board = 'greenhouse:example-ai'")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    assert served(home) == expected
    held = search_index._store(home)._local.reader[0]
    inode = path.stat().st_ino
    keep = path.with_name("kept-inode")
    os.link(path, keep)
    os.replace(other, path)
    assert path.stat().st_ino != inode
    got = served(home)
    assert got == [row for row in expected if row[2] != "greenhouse:example-ai"] != expected  # the NEW file's rows
    assert search_index._store(home)._local.reader[0] is not held


def test_locked_beyond_the_timeout_is_unavailable_and_the_file_is_kept(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = truth(home)
    monkeypatch.setattr(search_index, "_BUSY_TIMEOUT_MS", 150)
    path = settle(home)
    inode = path.stat().st_ino

    # Another connection holds the write lock: a reader is still served, a writer gives up at the timeout.
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        assert served(home) == expected
        assert search_index.upsert_company(home, "greenhouse:example-ai") is False
        assert search_index.refresh(home) is False
        rebuilt = search_index.rebuild_from_index(home)
        assert (rebuilt.available, rebuilt.reason) == (False, search_index.BUSY)
        assert served(home) == expected
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    # Another connection holds the whole file (exclusive locking mode): a reader gets busy, with no rows.
    search_index.close(home)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("PRAGMA locking_mode=EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        assert served(home) == search_index.BUSY
        assert search_index.status(home).reason == search_index.BUSY
        assert search_index.rebuild_from_index(home).reason == search_index.BUSY
        assert search_index.upsert_company(home, "greenhouse:example-ai") is False
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert path.stat().st_ino == inode  # a busy file is never taken for a damaged one and deleted
    assert served(home) == expected


def test_is_built_reads_unable_to_open_as_not_built(home: Path) -> None:
    path = settle(home)
    assert search_index.is_built(home)  # a read-only open of the lone WAL-mode file, in a folder it may write to
    if os.geteuid() == 0:
        pytest.skip("root ignores the folder's permissions")
    settle(home)
    folder = path.parent
    mode = folder.stat().st_mode
    os.chmod(folder, 0o555)  # the -shm file cannot be created: SQLite cannot open a WAL file read-only
    try:
        assert search_index.is_built(home) is False
    finally:
        os.chmod(folder, mode)
    assert search_index.is_built(home)
    assert len(BOARDS) == search_index.status(home).boards


# ---------------------------------------------------------------------------
# The verified stamp (SI2): the writer runs quick_check, the reader skips it at that stamp
# ---------------------------------------------------------------------------


def trace_checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every reader statement that is a ``quick_check``, from every connection the reader opens from now on."""

    seen: list[str] = []
    real = search_index._Store.reader

    def traced(self: object) -> sqlite3.Connection:
        conn = real(self)  # type: ignore[arg-type]
        if not getattr(conn, "_si2_traced", False):
            conn.set_trace_callback(lambda statement: seen.append(statement) if "quick_check" in statement else None)
            try:
                conn._si2_traced = True  # type: ignore[attr-defined]
            except AttributeError:
                pass
        return conn

    monkeypatch.setattr(search_index._Store, "reader", traced)
    return seen


def meta(home: Path) -> dict[str, str]:
    search_index.close(home)
    conn = sqlite3.connect(search_index.search_index_path(home))
    try:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()


def test_a_build_records_the_stamp_it_verified_and_a_new_reader_skips_quick_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    built = make_home(tmp_path)
    try:
        values = meta(built)
        assert values["verified"] == values["stamp"]
        checks = trace_checks(monkeypatch)
        search_index.close(built)
        assert served(built) == truth(built) and search_index.status(built).available
        assert checks == []
    finally:
        search_index.close(built)


def test_a_changed_stamp_without_a_verify_makes_the_reader_check_and_verify_makes_it_stop(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checks = trace_checks(monkeypatch)
    assert search_index.upsert_company(home, write_board(home, 2, round_=1), verify=False)
    values = meta(home)
    assert values.get("verified") != values["stamp"]
    for _ in range(3):
        assert served(home) == truth(home)
    assert len(checks) == 1  # once for this stamp, never per query
    search_index.close(home)
    assert search_index.verify(home) is True
    assert meta(home)["verified"] == meta(home)["stamp"]
    del checks[:]
    for _ in range(3):
        assert served(home) == truth(home)
    assert checks == []
    # Every writer verifies unless told not to: an upsert, a removal and a refresh leave a verified stamp.
    assert search_index.upsert_company(home, write_board(home, 1, round_=2))
    assert meta(home)["verified"] == meta(home)["stamp"]
    CompanyIndex.for_home(home).delete(*BOARDS[3][1:])
    assert search_index.remove_company(home, f"{BOARDS[3][1]}:{BOARDS[3][2]}")
    assert meta(home)["verified"] == meta(home)["stamp"]
    write_board(home, 0, round_=5)
    assert search_index.refresh(home)
    assert meta(home)["verified"] == meta(home)["stamp"]
    del checks[:]
    assert served(home) == truth(home) and checks == []


def test_verify_never_raises_never_builds_and_does_not_mark_a_state_it_did_not_check(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    assert search_index.verify(empty) is False and not search_index.search_index_path(empty).exists()
    unbuilt = make_home(tmp_path / "x", build=False)
    assert search_index.verify(unbuilt) is False and not search_index.search_index_path(unbuilt).exists()
    built = make_home(tmp_path / "y")
    try:
        path = settle(built)
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
        assert search_index.verify(built) is False
    finally:
        search_index.close(built)


def test_a_noise_damaged_file_with_a_stale_verified_stamp_is_still_refused(home: Path) -> None:
    assert search_index.upsert_company(home, write_board(home, 1, round_=4), verify=False)  # verified is now stale
    path = settle(home)
    healthy = path.read_bytes()
    page = 4096
    noise = random.Random(11708)
    for start in (40, 44, 48, 52):
        settle(home)
        path.write_bytes(healthy[: start * page] + noise.randbytes(2 * page) + healthy[(start + 2) * page :])
        got = served(home)
        assert got == search_index.DAMAGED or got == truth(home), start
    path.write_bytes(healthy[: 40 * page] + noise.randbytes(8 * page) + healthy[48 * page :])
    assert_falls_back_then_rebuilds(home, {search_index.DAMAGED})


def test_damage_after_a_verify_is_still_refused_by_any_query_that_touches_it(home: Path) -> None:
    """The accepted trade-off: no quick_check at a verified stamp, so only a query on damaged pages notices."""

    assert search_index.verify(home)
    path = settle(home)
    data = path.read_bytes()
    path.write_bytes(b"\0" * 100 + data[100:])  # the header: every query touches it
    assert_falls_back_then_rebuilds(home, {search_index.DAMAGED})
    settle(home)
    path.write_bytes(data[: len(data) // 2])  # the tail pages are gone: the query errors
    assert_falls_back_then_rebuilds(home, {search_index.DAMAGED})
    assert meta(home)["verified"] == meta(home)["stamp"]  # a rebuild verifies its own file
