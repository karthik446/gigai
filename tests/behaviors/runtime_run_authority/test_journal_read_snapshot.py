"""journal-read-scope: ``journal.read_committed_snapshot`` -- a committed

snapshot read that does not take the journal writer lock.

Before it, every read reached ``JournalWriter.snapshot`` through
``run_with_journal_writer``, i.e. through the one EXCLUSIVE per-workpad
lock. A threaded API server therefore ran its parallel reads one at a time,
and a read that waited past the 10 s lock timeout failed with
``InterprocessLockUnavailable`` (a 500 on ``GET /api/profiles``).

Covered here: parity with the locked snapshot at the same head, no lock on
the happy path, the single retry under the lock, a reader that overlaps a
writer's transition, many parallel readers against a writer committing in a
loop (every read is exactly one committed head), selection of a record
family by its directory-name pattern, reads that do not grow with an
unrelated family, and the Git-maintenance dependency the read relies on.

Lane: real journal commits (git subprocesses, ``tmp_path``), so
``tests/conftest.py`` classifies these ``integration``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import subprocess
import threading

import pytest

import gigai.journal as journal
from gigai.journal import (
    JournalArtifact,
    JournalConflictError,
    JournalSnapshot,
    read_committed_artifact,
    read_committed_snapshot,
    run_with_journal_writer,
)
from tests.behaviors.runtime_run_authority.test_journal_snapshot_equivalence import (
    GIG_ID,
    PROJECT_ID,
    _workpad,
    _write,
)

_WATCHLIST = "records/scout-watchlist/"
_RECORD_PATTERN = r"record_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_RECORD_A = "record_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_RECORD_B = "record_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _board(index: int) -> JournalArtifact:
    return JournalArtifact(f"{_WATCHLIST}board{index:05d}.json", f'{{"board":{index}}}'.encode())


def _record(record_id: str, name: str = "revision_1") -> JournalArtifact:
    return JournalArtifact(f"records/{record_id}/revisions/{name}.json", f'{{"id":"{record_id}/{name}"}}'.encode())


def _read(workpad: Path, **kwargs: object) -> JournalSnapshot:
    return read_committed_snapshot(workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID, **kwargs)  # type: ignore[arg-type]


def _locked(workpad: Path, prefixes: tuple[str, ...]) -> JournalSnapshot:
    return run_with_journal_writer(
        workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID,
        operation=lambda writer: writer.snapshot(prefixes),
    )


@pytest.fixture
def lock_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every writer-lock acquisition in this process, in order."""

    taken: list[str] = []
    real = journal._writer_lock

    @contextmanager
    def spying(path: Path, timeout_seconds: float):
        taken.append(threading.current_thread().name)
        with real(path, timeout_seconds):
            yield

    monkeypatch.setattr(journal, "_writer_lock", spying)
    return taken


# --------------------------------------------------------------------------
# Parity and the happy path
# --------------------------------------------------------------------------


def test_read_equals_the_locked_snapshot_at_the_same_head(tmp_path: Path) -> None:
    workpad = _workpad(tmp_path)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=tuple(_board(i) for i in range(5)))
    _write(workpad, "record", artifacts=(_record(_RECORD_A),))

    for prefixes in ((_WATCHLIST,), ("records/",), (_WATCHLIST, f"records/{_RECORD_A}/")):
        read, locked = _read(workpad, prefixes=prefixes), _locked(workpad, prefixes)
        assert read.head == locked.head
        assert read.artifacts == locked.artifacts


def test_a_read_never_takes_the_writer_lock(tmp_path: Path, lock_spy: list[str]) -> None:
    workpad = _workpad(tmp_path)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=tuple(_board(i) for i in range(3)))
    lock_spy.clear()

    snapshot = _read(workpad, prefixes=(_WATCHLIST,))

    assert len(snapshot.artifacts) == 3
    assert lock_spy == []


def test_an_empty_family_reads_as_an_empty_snapshot_at_the_head(tmp_path: Path, lock_spy: list[str]) -> None:
    workpad = _workpad(tmp_path)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=(_board(0),))
    lock_spy.clear()

    snapshot = _read(workpad, child_prefixes=(("records/", _RECORD_PATTERN),))

    assert lock_spy == []
    assert snapshot.artifacts == {}
    assert snapshot.head == _locked(workpad, (_WATCHLIST,)).head


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"prefixes": ("records",)},
        {"prefixes": ("/records/",)},
        {"prefixes": ("records/../",)},
        {"prefixes": ["records/"]},
        {"child_prefixes": (("records", _RECORD_PATTERN),)},
        {"child_prefixes": (("records/", "("),)},
        {"child_prefixes": (("records/",),)},
    ],
)
def test_invalid_prefixes_are_refused(tmp_path: Path, kwargs: dict[str, object]) -> None:
    workpad = _workpad(tmp_path)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=(_board(0),))
    with pytest.raises(JournalConflictError):
        _read(workpad, **kwargs)


# --------------------------------------------------------------------------
# A family selected by its directory-name pattern
# --------------------------------------------------------------------------


def test_child_prefixes_select_only_the_matching_directories(tmp_path: Path) -> None:
    workpad = _workpad(tmp_path)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=tuple(_board(i) for i in range(4)))
    _write(workpad, "a", artifacts=(_record(_RECORD_A),))
    _write(workpad, "b", artifacts=(_record(_RECORD_B), _record(_RECORD_B, "revision_2")))
    # Same shape one level too deep, and a near-miss name: neither is a record.
    _write(workpad, "deep", artifacts=(JournalArtifact(f"records/nested/{_RECORD_A}/revisions/x.json", b"{}"),))
    _write(workpad, "near", artifacts=(JournalArtifact(f"records/{_RECORD_A}-copy/revisions/x.json", b"{}"),))

    snapshot = _read(workpad, child_prefixes=(("records/", _RECORD_PATTERN),))

    assert set(snapshot.artifacts) == {
        f"records/{_RECORD_A}/revisions/revision_1.json",
        f"records/{_RECORD_B}/revisions/revision_1.json",
        f"records/{_RECORD_B}/revisions/revision_2.json",
    }
    explicit = _locked(workpad, (f"records/{_RECORD_A}/", f"records/{_RECORD_B}/"))
    assert snapshot.head == explicit.head
    assert snapshot.artifacts == explicit.artifacts


def test_a_scoped_read_does_not_grow_with_an_unrelated_family(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cost of reading the records is the records, however many boards
    share ``records/``: the same blobs are read and the same working files
    are visited at 20 boards and at 600."""

    blob_counts: list[int] = []
    real_blobs = journal._batch_read_blobs

    def counting_blobs(root: Path, refs: list[str]):
        blob_counts.append(len(refs))
        return real_blobs(root, refs)

    monkeypatch.setattr(journal, "_batch_read_blobs", counting_blobs)

    observed: dict[int, tuple[int, int]] = {}
    for boards in (20, 600):
        root = tmp_path / f"boards-{boards}"
        root.mkdir()
        workpad = _workpad(root)
        _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=tuple(_board(i) for i in range(boards)))
        _write(workpad, "a", artifacts=(_record(_RECORD_A),))
        _write(workpad, "b", artifacts=(_record(_RECORD_B),))
        blob_counts.clear()
        snapshot = _read(workpad, child_prefixes=(("records/", _RECORD_PATTERN),))
        assert len(blob_counts) == 1
        observed[boards] = (len(snapshot.artifacts), blob_counts[0])

    assert observed[20] == observed[600]
    assert observed[20][0] == 2


# --------------------------------------------------------------------------
# The retry under the writer lock
# --------------------------------------------------------------------------


def test_a_conflict_is_retried_once_under_the_writer_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_spy: list[str]
) -> None:
    workpad = _workpad(tmp_path)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=tuple(_board(i) for i in range(3)))
    lock_spy.clear()
    real = journal._capture_committed_snapshot
    locks_held_at_each_capture: list[int] = []

    def flaky(*args: object, **kwargs: object):
        locks_held_at_each_capture.append(len(lock_spy))
        if len(locks_held_at_each_capture) == 1:
            raise JournalConflictError("journal working evidence is extra or redirected")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(journal, "_capture_committed_snapshot", flaky)

    snapshot = _read(workpad, prefixes=(_WATCHLIST,))

    assert len(snapshot.artifacts) == 3
    # First capture with no lock, second capture inside the one lock taken.
    assert locks_held_at_each_capture == [0, 1]
    assert len(lock_spy) == 1


def test_a_lasting_conflict_is_reported_from_the_locked_read(tmp_path: Path, lock_spy: list[str]) -> None:
    workpad = _workpad(tmp_path)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=(_board(0),))
    (workpad / _WATCHLIST / "stray.json").write_bytes(b"{}")  # never committed
    lock_spy.clear()

    with pytest.raises(JournalConflictError, match="extra or redirected"):
        _read(workpad, prefixes=(_WATCHLIST,))
    assert len(lock_spy) == 1

    # The locked snapshot refuses the same state with the same error.
    with pytest.raises(JournalConflictError, match="extra or redirected"):
        _locked(workpad, (_WATCHLIST,))


def test_a_tampered_working_file_is_still_refused(tmp_path: Path) -> None:
    workpad = _workpad(tmp_path)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=(_board(0),))
    (workpad / _board(0).path).write_bytes(b'{"board":"forged"}')

    with pytest.raises(JournalConflictError, match="differs from committed bytes"):
        _read(workpad, prefixes=(_WATCHLIST,))


# --------------------------------------------------------------------------
# Readers against a writer
# --------------------------------------------------------------------------


def test_a_read_that_overlaps_a_transition_waits_and_reads_the_new_head(tmp_path: Path, lock_spy: list[str]) -> None:
    """The writer has replaced its working files but not committed yet --
    the exact half-written state. The reader must not return it: it falls
    back to the lock, waits for the commit, and reads the new head."""

    workpad = _workpad(tmp_path)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=(_board(0),))
    before = _read(workpad, prefixes=(_WATCHLIST,))
    result: dict[str, object] = {}

    def reader() -> None:
        try:
            result["snapshot"] = _read(workpad, prefixes=(_WATCHLIST,))
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            result["error"] = exc

    thread = threading.Thread(target=reader, name="overlapping-reader")
    returned_while_writer_held_the_lock: list[bool] = []

    def observer(step: str) -> None:
        if step != "after_artifact_replace":
            return
        lock_spy.clear()
        thread.start()
        thread.join(timeout=1.0)
        returned_while_writer_held_the_lock.append(not thread.is_alive())

    written = _write(
        workpad, "second", transition="scout_public_acquisition_progress",
        artifacts=(_board(1),), observer=observer,
    )
    thread.join(timeout=30.0)

    assert returned_while_writer_held_the_lock == [False]
    assert "error" not in result, result.get("error")
    snapshot = result["snapshot"]
    assert isinstance(snapshot, JournalSnapshot)
    assert snapshot.head == written.commit != before.head
    assert set(snapshot.artifacts) == {_board(0).path, _board(1).path}
    assert lock_spy == ["overlapping-reader"]


def test_parallel_readers_with_a_committing_writer_each_read_one_committed_head(tmp_path: Path) -> None:
    workpad = _workpad(tmp_path)
    first = _write(workpad, "seed", transition="scout_public_acquisition_progress", artifacts=tuple(_board(i) for i in range(20)))
    # head -> exactly the artifacts committed at that head.
    committed: dict[str, dict[str, bytes]] = {first.commit: {_board(i).path: _board(i).content for i in range(20)}}
    committed_lock = threading.Lock()
    writer_done = threading.Event()
    errors: list[BaseException] = []
    reads: list[JournalSnapshot] = []
    reads_lock = threading.Lock()

    def writer() -> None:
        try:
            current = dict(committed[first.commit])
            for index in range(20, 32):
                artifact = _board(index)
                entry = _write(workpad, f"add-{index}", transition="scout_public_acquisition_progress", artifacts=(artifact,))
                current = {**current, artifact.path: artifact.content}
                with committed_lock:
                    committed[entry.commit] = current
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            writer_done.set()

    def reader() -> None:
        try:
            while True:
                finished = writer_done.is_set()
                snapshot = _read(workpad, prefixes=(_WATCHLIST,))
                with reads_lock:
                    reads.append(snapshot)
                if finished:
                    return
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    readers = [threading.Thread(target=reader, name=f"reader-{index}") for index in range(8)]
    writing = threading.Thread(target=writer, name="writer")
    for thread in readers:
        thread.start()
    writing.start()
    writing.join(timeout=120.0)
    for thread in readers:
        thread.join(timeout=120.0)

    assert not writing.is_alive() and not any(thread.is_alive() for thread in readers)
    assert errors == []
    assert len(committed) == 13
    assert len(reads) >= len(readers)
    for snapshot in reads:
        # Never torn: a read is exactly one committed head, every file of it
        # and no file of any other.
        assert snapshot.head in committed
        assert snapshot.artifacts == committed[snapshot.head]
    # Every reader's last read started after the writer finished.
    final = max(committed.values(), key=len)
    assert sum(1 for snapshot in reads if snapshot.artifacts == final) >= len(readers)


# --------------------------------------------------------------------------
# The Git-maintenance dependency
# --------------------------------------------------------------------------


def test_journal_git_writes_never_start_background_maintenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lock-free reader pins a head and then reads its objects. No journal
    Git call may start a repack or prune behind it."""

    assert journal._GIT_NO_AUTO_MAINTENANCE == ("-c", "maintenance.auto=false", "-c", "gc.auto=0")

    workpad = _workpad(tmp_path)
    commands: list[list[str]] = []
    real_run = subprocess.run

    def recording(argv, *args: object, **kwargs: object):
        commands.append([str(item) for item in argv])
        return real_run(argv, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(journal.subprocess, "run", recording)
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=(_board(0),))

    writes = [argv for argv in commands if any(verb in argv for verb in ("commit", "add"))]
    assert any("commit" in argv for argv in writes)
    for argv in writes:
        assert "maintenance.auto=false" in argv and "gc.auto=0" in argv, argv


def test_an_old_head_stays_readable_after_later_commits(tmp_path: Path) -> None:
    workpad = _workpad(tmp_path)
    mutable = "runs/run_1/run-details.json"
    first = _write(workpad, "details-1", artifacts=(JournalArtifact(mutable, b'{"state":"first"}'),))
    old = _read(workpad, prefixes=("runs/",))
    assert old.head == first.commit

    for index in range(2, 8):
        _write(workpad, f"details-{index}", artifacts=(JournalArtifact(mutable, f'{{"state":"{index}"}}'.encode()),))
    _write(workpad, "boards", transition="scout_public_acquisition_progress", artifacts=tuple(_board(i) for i in range(30)))

    data, commit = read_committed_artifact(
        workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID, path=mutable, head=old.head,
    )
    assert data == b'{"state":"first"}' == old.artifacts[mutable]
    assert commit == first.commit
