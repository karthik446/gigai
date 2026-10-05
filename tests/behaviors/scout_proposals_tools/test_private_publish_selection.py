"""0.1.10.9 M8: a private-record publish captures only the families it consults.

``private_records._publish`` used to take ``writer.snapshot(("records/",
"references/", "run-inputs/"))`` under the writer lock: all of ``records/``,
which also holds families this module never reads (a Scout watchlist is one
file per board: 10,350 files on the operator-sized home). Every private write
(a master revision, a profile's view, a resume, an answer) read and verified
every one of them, about 1.5 s a write there. It now names what it consults,
the selection the read path already uses (``_private_snapshot``):
``references/``, ``run-inputs/``, ``records/operations/`` and each
``records/record_<uuid>/``.

What these tests pin, on a workpad that also holds a large unrelated family
under ``records/``:

* a publish reads no file of that family (the reads are counted);
* every guarantee of a publish still holds: the idempotent retry, the
  equivalent import, ``stale_parent`` and the parent chain, the receipt and
  the recovery after a crash (between the two publishes of a record, after
  the commit, and inside a transition), and two processes writing the same
  record (one wins);
* a file of the UNRELATED family that differs from what is committed no
  longer blocks a private write (the old full capture refused it), is not
  committed by it, and is still refused where that family is read; a file of
  the record being written, of another private record, of a reference or of
  the receipts still refuses the write.

Real journal commits (git subprocesses, ``tmp_path``): the integration lane.
"""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import subprocess
import uuid

import pytest

import gigai.journal as journal
import gigai.private_records as private_records
from gigai.journal import JournalArtifact, JournalConflictError, read_committed_snapshot, reconcile_journal, record_transition
from gigai.lifecycle import create_offline
from gigai.private_records import PrivateRecordError, create_record, import_reference, import_run_input, list_revisions, read_record
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target

OTHER_FAMILY = "records/scout-watchlist/"
#: Enough files that a capture of all of ``records/`` is unmistakable in a count.
OTHER_FILES = 200
ACTOR = {"kind": "operator", "id": "local-user"}


class _Pad:
    def __init__(self, tmp_path: Path, *, other_files: int = OTHER_FILES) -> None:
        self.home, self.target = tmp_path / "home", tmp_path / "target"
        self.target.mkdir()
        run_setup(build_config(home_root=self.home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
        initialize_target(home_root=self.home, requested_target=self.target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
        created = create_offline(home_root=self.home, requested_target=self.target, name="publish-selection", open_editor=False)
        private_records.migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)
        self.workpad, self.project_id, self.gig_id = created.workpad, created.project_id, created.gig_id
        self.tmp_path = tmp_path
        # Another family under records/, published in ONE commit, as a watchlist seed is.
        self.other = [f"{OTHER_FAMILY}scout_watchlist:greenhouse:board{index:06d}.json" for index in range(other_files)]
        if self.other:
            record_transition(
                workpad=self.workpad, project_id=self.project_id, gig_id=self.gig_id, handoff_id=f"handoff_{uuid.uuid4()}",
                transition="private_reference_imported", body="Seeded another family under records/.",
                artifacts=tuple(JournalArtifact(path, json.dumps({"board": index}).encode()) for index, path in enumerate(self.other)),
            )

    @property
    def scope(self) -> dict[str, object]:
        return {"home_root": self.home, "requested_target": self.target, "gig_id": self.gig_id}

    def source(self, text: str, name: str = "resume.md") -> Path:
        path = self.tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def reference(self, text: str, key: str):  # noqa: ANN201 - an ImportResult
        return import_reference(**self.scope, kind="resume", source=self.source(text), operation_key=key)  # type: ignore[arg-type]

    def record(self, content_id: str, key: str, **more: object):  # noqa: ANN201 - a RevisionResult
        return create_record(
            **self.scope, kind="imported_reference", content_family="g45_reference", content_id=content_id, actor=ACTOR, origin="imported",  # type: ignore[arg-type]
            operation_key=key, **more,  # type: ignore[arg-type]
        )

    def revisions(self, record_id: str) -> list[str]:
        resolved = private_records._resolved(home_root=self.home, requested_target=self.target, gig_id=self.gig_id)
        return [str(item["revision_id"]) for item in list_revisions(resolved=resolved, record_id=record_id)]

    def head(self) -> str:
        return subprocess.run(["git", "-C", str(self.workpad), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

    def committed(self, path: str) -> bytes:
        return subprocess.run(["git", "-C", str(self.workpad), "show", f"HEAD:{path}"], capture_output=True, check=True).stdout

    def reconcile(self) -> None:
        reconcile_journal(workpad=self.workpad, project_id=self.project_id, gig_id=self.gig_id)


def _captures(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Every snapshot capture from here on: ``(the prefixes asked for, the paths captured)``."""

    seen: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    real = journal._capture_committed_snapshot

    def capture(root, project_id, gig_id, prefixes, *more):  # noqa: ANN001, ANN002, ANN202
        snapshot = real(root, project_id, gig_id, prefixes, *more)
        seen.append((tuple(prefixes), tuple(snapshot.artifacts)))
        return snapshot

    monkeypatch.setattr(journal, "_capture_committed_snapshot", capture)
    return seen


def _blob_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """How many blobs each bulk read asks git for."""

    counts: list[int] = []
    real = journal._batch_read_blob_objects

    def read(root, refs):  # noqa: ANN001, ANN202
        counts.append(len(refs))
        return real(root, refs)

    monkeypatch.setattr(journal, "_batch_read_blob_objects", read)
    return counts


def test_a_private_write_reads_no_file_of_another_family_under_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pad = _Pad(tmp_path)
    captures, blobs = _captures(monkeypatch), _blob_reads(monkeypatch)

    reference = pad.reference("the first resume\n", "sel-ref-1")
    first = pad.record(reference.item_id, "sel-record-1")
    second_reference = pad.reference("the second resume\n", "sel-ref-2")
    second = pad.record(second_reference.item_id, "sel-record-2", record_id=first.record_id, parent_revision=first.revision_id)
    posting = import_run_input(**pad.scope, data=b"a posting\n", operation_key="sel-posting")  # type: ignore[arg-type]

    assert reference.created and first.created and second.created and posting.created
    assert pad.revisions(first.record_id) == [first.revision_id, second.revision_id]
    assert captures, "the publishes captured nothing: the test no longer observes them"
    read = [path for _prefixes, paths in captures for path in paths]
    outside = [path for path in read if path.startswith(OTHER_FAMILY)]
    assert outside == [], f"a private write read {len(outside)} files of {OTHER_FAMILY}"
    assert all("records/" not in prefixes for prefixes, _paths in captures), "a publish asked for all of records/"
    # Five writes, each one publish (0110-10-16: no projection rebuild after it): none the size of the other family.
    assert max(len(paths) for _prefixes, paths in captures) < 40
    assert max(blobs) < OTHER_FILES // 2, f"one read asked git for {max(blobs)} blobs"
    # What a publish does consult is in what it captured: the receipts, the references, the record's revisions.
    publish = next(paths for prefixes, paths in reversed(captures) if "records/operations/" in prefixes and "run-inputs/" in prefixes)
    assert any(path.startswith("records/operations/") for path in publish)
    assert any(path.startswith(f"references/{reference.item_id}/") for path in publish)
    assert any(path.startswith(f"records/{first.record_id}/revisions/") for path in publish)


def test_the_writer_snapshot_takes_child_prefixes_as_the_lock_free_read_does(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=5)
    reference = pad.reference("a resume\n", "child-ref")
    record = pad.record(reference.item_id, "child-record")
    children = (("records/", private_records.RECORD_DIRECTORY_PATTERN),)

    def under_lock(writer):  # noqa: ANN001, ANN202
        return writer.snapshot(private_records.PUBLISH_PREFIXES, child_prefixes=children), writer.snapshot(("records/",))

    narrow, whole = journal.run_with_journal_writer(workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, operation=under_lock)
    lock_free = read_committed_snapshot(
        workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, prefixes=private_records.PUBLISH_PREFIXES, child_prefixes=children,
    )

    assert narrow.head == whole.head == lock_free.head
    assert narrow.artifacts == lock_free.artifacts  # the same selection, the same bytes, with or without the lock
    assert f"records/{record.record_id}/revisions/{record.revision_id}.json" in narrow.artifacts
    assert not any(path.startswith(OTHER_FAMILY) for path in narrow.artifacts)
    assert sum(path.startswith(OTHER_FAMILY) for path in whole.artifacts) == 5  # without child_prefixes: as before
    # Every records/ file the narrow capture holds is byte for byte what the whole capture holds.
    assert {path: data for path, data in whole.artifacts.items() if path in narrow.artifacts} == {
        path: data for path, data in narrow.artifacts.items() if path.startswith("records/")
    }
    with pytest.raises(JournalConflictError):
        journal.run_with_journal_writer(
            workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id,
            operation=lambda writer: writer.snapshot(("references/",), child_prefixes=(("../records/", ".*"),)),
        )


def test_a_retry_an_equivalent_import_and_a_stale_parent_behave_as_before(tmp_path: Path) -> None:
    pad = _Pad(tmp_path)
    reference = pad.reference("the resume\n", "keep-ref")
    first = pad.record(reference.item_id, "keep-record-1")
    head = pad.head()

    # The same request again (the same key, the same payload): the same receipt, nothing committed.
    again = pad.reference("the resume\n", "keep-ref")
    assert (again.created, again.item_id, again.receipt) == (False, reference.item_id, reference.receipt)
    replay = pad.record(reference.item_id, "keep-record-1")
    assert (replay.created, replay.record_id, replay.revision_id, replay.receipt) == (False, first.record_id, first.revision_id, first.receipt)
    # The same bytes under another key: the equivalent import, found by its content, with its own receipt.
    equivalent = pad.reference("the resume\n", "keep-ref-other-key")
    assert (equivalent.created, equivalent.item_id, equivalent.receipt) == (False, reference.item_id, reference.receipt)
    assert pad.head() == head
    assert len(list((pad.workpad / "references").iterdir())) == 1

    # A key used again with another payload is refused, not answered from the receipt.
    other = pad.reference("the resume, changed\n", "keep-ref-2")
    head = pad.head()
    with pytest.raises(PrivateRecordError) as conflict:
        pad.record(other.item_id, "keep-record-1")
    assert conflict.value.code == "private_operation_conflict"
    assert pad.head() == head and pad.revisions(first.record_id) == [first.revision_id]

    # The parent check: a revision that does not name the current one is refused; the chain is read in parent order.
    with pytest.raises(PrivateRecordError) as stale:
        pad.record(other.item_id, "keep-record-stale", record_id=first.record_id, parent_revision="revision_ffffffff-ffff-4fff-8fff-ffffffffffff")
    assert stale.value.code == "stale_parent"
    with pytest.raises(PrivateRecordError) as rootless:
        pad.record(other.item_id, "keep-record-rootless", record_id=first.record_id)
    assert rootless.value.code == "stale_parent"
    second = pad.record(other.item_id, "keep-record-2", record_id=first.record_id, parent_revision=first.revision_id)
    with pytest.raises(PrivateRecordError) as behind:
        pad.record(reference.item_id, "keep-record-behind", record_id=first.record_id, parent_revision=first.revision_id)
    assert behind.value.code == "stale_parent"
    assert pad.revisions(first.record_id) == [first.revision_id, second.revision_id]
    assert read_record(**pad.scope, record_id=first.record_id, content=True)["content"] == b"the resume, changed\n"  # type: ignore[arg-type]
    assert read_record(**pad.scope, record_id=first.record_id, revision_id=first.revision_id, content=True)["content"] == b"the resume\n"  # type: ignore[arg-type]


def test_a_crash_between_the_steps_of_a_write_is_recovered_by_the_same_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pad = _Pad(tmp_path)

    # 1. The process dies between the two publishes of a record: the reference is committed, the record is not.
    reference = pad.reference("the resume\n", "crash-ref")
    #    The same two calls again: the reference is answered from its receipt, the record is created once.
    again = pad.reference("the resume\n", "crash-ref")
    record = pad.record(again.item_id, "crash-record")
    assert (again.created, again.item_id, record.created) == (False, reference.item_id, True)
    assert pad.revisions(record.record_id) == [record.revision_id]

    # 2. The process dies after the commit, before the projection. Since 0110-10-16 that is every write: a save
    #    never rebuilds the projection. The write is sealed and says the projection is pending; the same call
    #    again returns the same receipt and still rebuilds nothing; the catch-up brings the index to the head,
    #    and the call then says it is not pending.
    private_records.catch_up_scout_projection(resolved=private_records._resolved(**pad.scope))  # type: ignore[arg-type]
    other = pad.reference("the resume, changed\n", "crash-ref-2")
    sealed = pad.record(other.item_id, "crash-record-2", record_id=record.record_id, parent_revision=record.revision_id)
    assert sealed.created is True and sealed.projection_pending is True and sealed.rebuild_action == "rebuild_index"
    context = json.loads((pad.workpad / "indexes" / "context.json").read_bytes())
    assert sealed.revision_id not in json.dumps(context)  # the derived index is behind
    replay = pad.record(other.item_id, "crash-record-2", record_id=record.record_id, parent_revision=record.revision_id)
    assert (replay.created, replay.revision_id, replay.receipt, replay.projection_pending) == (False, sealed.revision_id, sealed.receipt, True)
    assert sealed.revision_id not in (pad.workpad / "indexes" / "context.json").read_text(encoding="utf-8")  # the retry rebuilt nothing
    rebuilt = private_records.catch_up_scout_projection(resolved=private_records._resolved(**pad.scope))  # type: ignore[arg-type]
    assert rebuilt is not None and rebuilt.journal_head == pad.head()
    replay = pad.record(other.item_id, "crash-record-2", record_id=record.record_id, parent_revision=record.revision_id)
    assert (replay.created, replay.revision_id, replay.receipt, replay.projection_pending) == (False, sealed.revision_id, sealed.receipt, False)
    context = json.loads((pad.workpad / "indexes" / "context.json").read_bytes())
    assert [item["revision_id"] for item in context["records"] if item["record_id"] == record.record_id] == [sealed.revision_id]

    # 3. The process dies INSIDE a transition (the files are in place, the commit never happened). The next write
    #    is refused until the journal is reconciled; then the same call is answered from the receipt: one revision.
    head = pad.head()
    third = pad.reference("the resume, changed twice\n", "crash-ref-3")
    with monkeypatch.context() as patched:
        patched.setattr(journal, "_commit_handoff", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated crash before the commit")))
        with pytest.raises(OSError, match="simulated crash"):
            pad.record(third.item_id, "crash-record-3", record_id=record.record_id, parent_revision=sealed.revision_id)
    after_third = pad.head()
    assert after_third != head  # the reference landed; the revision is on disk, uncommitted
    with pytest.raises(PrivateRecordError) as unread:
        pad.revisions(record.record_id)
    assert unread.value.code == "private_record_not_authenticated"  # a read does not skip the half-written revision
    with pytest.raises(PrivateRecordError) as refused:
        pad.record(third.item_id, "crash-record-3", record_id=record.record_id, parent_revision=sealed.revision_id)
    assert refused.value.code == "private_record_not_authenticated"  # the half-written revision is seen, not skipped
    with pytest.raises(PrivateRecordError):
        pad.reference("an unrelated write\n", "crash-ref-4")  # no private write proceeds past a half-written one
    assert pad.head() == after_third
    pad.reconcile()
    recovered = pad.record(third.item_id, "crash-record-3", record_id=record.record_id, parent_revision=sealed.revision_id)
    assert recovered.created is False  # the reconciled transition is the write; it is not made twice
    assert pad.revisions(record.record_id) == [record.revision_id, sealed.revision_id, recovered.revision_id]
    assert read_record(**pad.scope, record_id=record.record_id, content=True)["content"] == b"the resume, changed twice\n"  # type: ignore[arg-type]


def _write_in_a_process(arguments: dict[str, object], queue: object) -> None:
    try:
        result = create_record(**arguments)  # type: ignore[arg-type]
        queue.put(("ok", result.revision_id))  # type: ignore[attr-defined]
    except PrivateRecordError as exc:
        queue.put((exc.code, ""))  # type: ignore[attr-defined]


def test_two_processes_writing_the_same_record_have_one_winner(tmp_path: Path) -> None:
    pad = _Pad(tmp_path)
    reference = pad.reference("the resume\n", "race-ref")
    initial = pad.record(reference.item_id, "race-initial")
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    base = {
        **pad.scope, "kind": "imported_reference", "content_family": "g45_reference", "content_id": reference.item_id, "actor": ACTOR,
        "origin": "imported", "record_id": initial.record_id, "parent_revision": initial.revision_id,
    }
    workers = [context.Process(target=_write_in_a_process, args=({**base, "operation_key": f"race-{number}"}, queue)) for number in (1, 2, 3)]
    for worker in workers:
        worker.start()
    outcomes = [queue.get(timeout=60) for _ in workers]
    for worker in workers:
        worker.join(timeout=60)
        assert worker.exitcode == 0
    # The lock serialises them: the first to hold it writes on the parent it named; the others see a parent that moved.
    assert sorted(code for code, _revision in outcomes) == ["ok", "stale_parent", "stale_parent"]
    winner = next(revision for code, revision in outcomes if code == "ok")
    assert pad.revisions(initial.record_id) == [initial.revision_id, winner]


def test_a_differing_file_blocks_a_private_write_only_where_the_write_consults_it(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=20)
    reference = pad.reference("the resume\n", "corrupt-ref")
    record = pad.record(reference.item_id, "corrupt-record-1")
    other_record = pad.record(reference.item_id, "corrupt-other-record")
    bystander = pad.workpad / pad.other[3]
    committed = pad.committed(pad.other[3])

    # --- outside the selection: a file of the other family that is not what is committed -----------------
    bystander.write_bytes(b'{"board": "changed outside the journal"}')
    second_reference = pad.reference("the resume, changed\n", "corrupt-ref-2")
    second = pad.record(second_reference.item_id, "corrupt-record-2", record_id=record.record_id, parent_revision=record.revision_id)
    assert second.created is True and pad.revisions(record.record_id) == [record.revision_id, second.revision_id]
    # The private write did not commit the changed file, and did not put it back either.
    assert pad.committed(pad.other[3]) == committed
    assert bystander.read_bytes() == b'{"board": "changed outside the journal"}'
    # Where that family IS read, it is refused exactly as before.
    with pytest.raises(JournalConflictError, match="working evidence"):
        read_committed_snapshot(workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, prefixes=(OTHER_FAMILY,))
    # An extra, uncommitted file there does not block a private write either.
    extra = pad.workpad / OTHER_FAMILY / "scout_watchlist:greenhouse:extra.json"
    extra.write_bytes(b"{}")
    assert pad.reference("another resume\n", "corrupt-ref-3").created is True
    extra.unlink()
    bystander.write_bytes(committed)

    # --- inside the selection: every one of these still refuses the write ---------------------------------
    def refused(path: Path, changed: bytes, key: str) -> None:
        original = path.read_bytes()
        head = pad.head()
        path.write_bytes(changed)
        try:
            with pytest.raises(PrivateRecordError) as error:
                pad.record(reference.item_id, key, record_id=record.record_id, parent_revision=second.revision_id)
            assert error.value.code == "private_record_not_authenticated", path
            assert pad.head() == head, path
        finally:
            path.write_bytes(original)

    # the record being written: one of its own revisions
    refused(pad.workpad / "records" / record.record_id / "revisions" / f"{record.revision_id}.json", b'{"schema_version":"1.0"}\n', "corrupt-own")
    # the receipts
    receipt = next(path for path in sorted((pad.workpad / "records" / "operations").iterdir()) if path.name.startswith("record_create-"))
    refused(receipt, b"{}\n", "corrupt-receipt")
    # a reference (the content a record points at)
    refused(pad.workpad / "references" / reference.item_id / "source.txt", b"not what was imported\n", "corrupt-reference")
    # ANOTHER private record: the selection is every records/record_<uuid>/, so this refuses too (as before)
    refused(pad.workpad / "records" / other_record.record_id / "revisions" / f"{other_record.revision_id}.json", b'{"schema_version":"1.0"}\n', "corrupt-other")
    # an extra, uncommitted file among the receipts
    stray = pad.workpad / "records" / "operations" / "record_update-stray.json"
    stray.write_bytes(b"{}\n")
    with pytest.raises(PrivateRecordError) as error:
        pad.record(reference.item_id, "corrupt-stray", record_id=record.record_id, parent_revision=second.revision_id)
    assert error.value.code == "private_record_not_authenticated"
    stray.unlink()

    # With everything as committed again, the write goes through.
    third = pad.record(reference.item_id, "corrupt-record-3", record_id=record.record_id, parent_revision=second.revision_id)
    assert pad.revisions(record.record_id) == [record.revision_id, second.revision_id, third.revision_id]
