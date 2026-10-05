"""0.1.10.11 S3: a native-record publish captures only the families it consults.

``native_records._publish`` (every answer and story: ``POST``, ``PUT`` and
``DELETE /api/answers``, ``gigai scout answer``, ``answers save``, ``answers
delete``) took ``writer.snapshot(("records/", "references/", "run-inputs/",
"manifests/capabilities/"))`` under the writer lock: all of ``records/``,
which also holds families this module never reads (a Scout watchlist is one
file per board: 10,350 files on the operator-sized home, 2 to 3 s of every
answer save). It now names what it consults, as ``private_records._publish``
has since 0.1.10.9 (``test_private_publish_selection.py``, the template of
this file): ``records/operations/``, ``references/``, ``run-inputs/``,
``manifests/capabilities/`` and each ``records/record_<uuid>/``.

What these tests pin, on a workpad that also holds a large unrelated family
under ``records/``:

* a native publish reads no file of that family (the reads are counted), and
  the answers' own save, edit and delete read none either;
* EVERY path kind a native sidecar may cite is inside the selection: the five
  kinds the publisher admits are accepted, and nothing under another family
  of ``records/`` (or under ``docs/``, which the content schema also admits)
  can be cited, however it is shaped;
* the one-override-per-task-context check sees every revision it needs: all
  of them are under ``records/record_<uuid>/``;
* every guarantee of a publish still holds: the idempotent retry, a key
  reused with another payload, ``stale_parent``, an archived record, the
  recovery after a crash (between two publishes, after the commit, inside a
  transition), and three processes writing the same record (one wins);
* a file of the UNRELATED family that differs from what is committed no
  longer blocks a native write (the old full capture refused it), is not
  committed by it, and is still refused where that family is read; a file
  INSIDE the selection (a revision, a sidecar, a receipt, a reference,
  another record, a capability manifest) still refuses the write with the
  head unmoved.

The tool binding's revalidation at publication reads ``manifests/capabilities/``
from the same capture; ``test_scout03_c3_tools.py`` holds its tests.

Real journal commits (git subprocesses, ``tmp_path``): the integration lane.
"""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import re
import uuid

import pytest

import gigai.journal as journal
import gigai.native_records as native_records
import gigai.private_records as private_records
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.journal import JournalArtifact, JournalConflictError, read_committed_snapshot, record_transition
from gigai.native_records import (
    archive_native_record,
    create_native_record,
    create_task_override,
    list_native_records,
    read_native_record,
    update_native_record,
)
from gigai.private_records import PrivateRecordError, import_run_input

from .test_private_publish_selection import OTHER_FAMILY, OTHER_FILES, _blob_reads, _captures, _Pad
from .test_scout03_native_records import _experience, _profile

OPERATOR = {"kind": "operator", "id": "user"}
CHILDREN = (("records/", private_records.RECORD_DIRECTORY_PATTERN),)
#: Every family the product keeps under ``records/`` beside the private records themselves (``record_<uuid>``).
#: ``test_every_family_under_records_is_named_here`` fails when the source names one that is not here.
OTHER_FAMILIES = (
    "applications", "external", "operations", "scout-acquisition", "scout-documents", "scout-interviews",
    "scout-profile-selection", "scout-profiles", "scout-proposals", "scout-watchlist", "transfers",
)


def _create(pad: _Pad, key: str, content: dict[str, object] | None = None, **more: object):  # noqa: ANN202 - a NativeRecordResult
    return create_native_record(**pad.scope, content=content or _experience(), actor=OPERATOR, origin="user_reported", operation_key=key, **more)  # type: ignore[arg-type]


def _update(pad: _Pad, record_id: str, parent: str, key: str, content: dict[str, object] | None = None):  # noqa: ANN202
    return update_native_record(
        **pad.scope, record_id=record_id, parent_revision=parent, content=content or _experience(answered=True), actor=OPERATOR,  # type: ignore[arg-type]
        origin="user_reported", operation_key=key,
    )


def _chain(pad: _Pad, record_id: str) -> list[str]:
    resolved = private_records._resolved(home_root=pad.home, requested_target=pad.target, gig_id=pad.gig_id)
    return [str(item["revision_id"]) for item in native_records._chain(resolved, native_records._record_snapshot(resolved), record_id)]


def _cite(pad: _Pad, path: str, media_type: str = "application/json") -> dict[str, object]:
    """A sidecar's reference to ``path`` with the exact committed bytes: the strongest claim a caller can make."""

    data = pad.committed(path)
    return {"path": path, "content_sha256": digest_imported_bytes(data), "media_type": media_type, "size_bytes": len(data)}


def _citing(reference: dict[str, object]) -> dict[str, object]:
    """Native content whose one known fact cites ``reference`` as its evidence."""

    return _profile(employer_ref=reference)


def _commit(pad: _Pad, files: dict[str, bytes]) -> None:
    record_transition(
        workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, handoff_id=f"handoff_{uuid.uuid4()}",
        transition="private_reference_imported", body="Committed files of another family.",
        artifacts=tuple(JournalArtifact(path, data) for path, data in files.items()),
    )


def test_a_native_write_reads_no_file_of_another_family_under_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pad = _Pad(tmp_path)
    reference = pad.reference("the resume\n", "sel-ref")
    captures, blobs = _captures(monkeypatch), _blob_reads(monkeypatch)

    first = _create(pad, "sel-qa")
    second = _update(pad, first.record_id, first.revision_id, "sel-qa-2")
    cited = _create(pad, "sel-profile", _citing(dict(reference.record["snapshot"])))  # type: ignore[arg-type]
    override = create_task_override(**pad.scope, content=_profile(), actor=OPERATOR, origin="user_reported", operation_key="sel-override", base={"record_id": cited.record_id, "revision_id": cited.revision_id})  # type: ignore[arg-type]
    archived = archive_native_record(**pad.scope, record_id=first.record_id, parent_revision=second.revision_id, actor=OPERATOR, operation_key="sel-qa-3")  # type: ignore[arg-type]

    assert first.created and second.created and cited.created and override.created and archived.created
    assert _chain(pad, first.record_id) == [first.revision_id, second.revision_id, archived.revision_id]
    assert captures, "the publishes captured nothing: the test no longer observes them"
    read = [path for _prefixes, paths in captures for path in paths]
    outside = [path for path in read if path.startswith(OTHER_FAMILY)]
    assert outside == [], f"a native write read {len(outside)} files of {OTHER_FAMILY}"
    assert all("records/" not in prefixes for prefixes, _paths in captures), "a publish asked for all of records/"
    # Five writes, each one publish: none of them the size of the other family.
    assert max(len(paths) for _prefixes, paths in captures) < 40
    assert max(blobs) < OTHER_FILES // 2, f"one read asked git for {max(blobs)} blobs"
    # What a publish does consult is in what it asked for and captured: the receipts, the references, the capability
    # manifests (the tool binding), and the record's own revisions and sidecars.
    publishes = [(prefixes, paths) for prefixes, paths in captures if "manifests/capabilities/" in prefixes]
    assert len(publishes) == 5
    prefixes, publish = publishes[-1]
    assert set(native_records.PUBLISH_PREFIXES) == {"records/operations/", "references/", "run-inputs/", "manifests/capabilities/"} <= set(prefixes)
    assert any(path.startswith("records/operations/") for path in publish)
    assert any(path.startswith(f"references/{reference.item_id}/") for path in publish)
    assert f"records/{first.record_id}/revisions/{second.revision_id}.json" in publish
    assert f"records/{first.record_id}/blobs/{second.revision_id}.json" in publish


def test_the_answers_save_edit_and_delete_read_no_watchlist_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The routes' and the commands' own functions (``story_bank``), on a Scout home with a seeded watchlist."""

    from gigai.scout import story_bank
    from tests.behaviors.scout_find_jobs.test_read_routes_lock_free import _gig

    gig = _gig(tmp_path, boards=120)
    captures, blobs = _captures(monkeypatch), _blob_reads(monkeypatch)

    saved = story_bank.save_answer(home_root=gig.home, target=gig.target, question_id="cloud:gcp", question="GCP?", answer="Three years.")  # POST /api/answers, `answer`, `answers save`
    edited = story_bank.edit_answer(home_root=gig.home, target=gig.target, question_id="cloud:gcp", answer="Four years.", expected_revision=saved.revision)  # PUT /api/answers/{id}
    current = story_bank.get_answer(home_root=gig.home, target=gig.target, question_id="cloud:gcp")
    assert current is not None and current.answer == "Four years."
    deleted = story_bank.delete_answer(home_root=gig.home, target=gig.target, question_id="cloud:gcp", expected_revision=edited.revision)  # DELETE /api/answers/{id}, `answers delete`
    assert deleted and story_bank.get_answer(home_root=gig.home, target=gig.target, question_id="cloud:gcp") is None

    publishes = [paths for prefixes, paths in captures if "manifests/capabilities/" in prefixes]
    assert len(publishes) >= 3, "the three writes published nothing through native_records: the test no longer observes them"
    outside = [path for _prefixes, paths in captures for path in paths if path.startswith("records/scout-watchlist/")]
    assert outside == [], f"an answer write read {len(outside)} watchlist files"
    assert all("records/" not in prefixes for prefixes, _paths in captures)
    assert max(blobs) < 60, f"one read asked git for {max(blobs)} blobs on a home with 120 boards"


def test_every_path_kind_a_sidecar_may_cite_is_inside_the_selection(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=3)
    reference = pad.reference("the resume\n", "cite-ref")
    posting = import_run_input(**pad.scope, data=b"a posting\n", operation_key="cite-posting")  # type: ignore[arg-type]
    other = _create(pad, "cite-other")

    # THE LIST: the five path kinds the publisher admits (``_authoritative_media_type``), each cited with its exact bytes.
    admitted = {
        "a reference's record": _cite(pad, f"references/{reference.item_id}/reference.json"),
        "a reference's source": dict(reference.record["snapshot"]),  # type: ignore[arg-type]
        "a Run input's record": _cite(pad, f"run-inputs/{posting.item_id}/input.json"),
        "a Run input's source": dict(posting.record["snapshot"]),  # type: ignore[arg-type]
        "another record's sidecar": _cite(pad, f"records/{other.record_id}/blobs/{other.revision_id}.json"),
    }
    children = native_records.PUBLISH_PREFIXES, CHILDREN
    selection = read_committed_snapshot(workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, prefixes=children[0], child_prefixes=children[1])
    for number, (kind, ref) in enumerate(admitted.items()):
        assert ref["path"] in selection.artifacts, kind
        assert _create(pad, f"cite-ok-{number}", _citing(ref)).created, kind

    # Everything else is refused, exact bytes or not. In the selection but with no owner that vouches for its media:
    receipt = next(path for path in sorted(selection.artifacts) if path.startswith("records/operations/record_create-"))
    unowned = {
        "a receipt": _cite(pad, receipt),
        "a revision file itself": _cite(pad, f"records/{other.record_id}/revisions/{other.revision_id}.json"),
    }
    # Outside the selection: a file of another family, and the docs/ root the content schema also admits.
    _commit(pad, {"docs/interviews/record_x/revision_y/preparation.md": b"# notes\n"})
    outside = {
        "a watchlist board": _cite(pad, pad.other[0]),
        "a file under docs/": _cite(pad, "docs/interviews/record_x/revision_y/preparation.md", "text/markdown"),
    }
    head = pad.head()
    for number, (kind, ref) in enumerate({**unowned, **outside}.items()):
        with pytest.raises(PrivateRecordError) as refused:
            _create(pad, f"cite-refused-{number}", _citing(ref))
        assert refused.value.code == "native_record_scope_refused", kind
    assert pad.head() == head


@pytest.mark.parametrize("family", OTHER_FAMILIES)
def test_nothing_under_another_family_of_records_can_be_cited(tmp_path: Path, family: str) -> None:
    """The most favourable case for a citation outside the private records: a tree shaped EXACTLY like one (a valid
    revision and the sidecar it points at), committed inside another family's directory and cited with its exact
    bytes. It is refused twice over: the owner rule admits a sidecar only in a ``record_<uuid>`` directory, and the
    publish capture holds no other directory of ``records/`` but the receipts'. A family that could be cited would
    pass the publish and fail here."""

    pad = _Pad(tmp_path, other_files=0)
    base = _create(pad, "family-base")
    revision_id, blob = f"revision_{uuid.uuid4()}", canonical_json_bytes(_experience())
    blob_path = f"records/{family}/blobs/{revision_id}.json"
    revision = {
        "schema_version": "1.0", "record_id": base.record_id, "revision_id": revision_id, "parent_revision": None, "project_id": pad.project_id, "gig_id": pad.gig_id,
        "kind": "experience_qa", "privacy_class": "private_sensitive", "origin": "user_reported", "actor": OPERATOR,
        "content": {"family": "jsl_blob", "blob_ref": {"path": blob_path, "content_sha256": digest_imported_bytes(blob), "media_type": "application/json", "size_bytes": len(blob)}, "content_sha256": digest_imported_bytes(blob)},
        "relationships": [], "created_at": "2026-10-05T00:00:00Z", "state": "active",
    }
    _commit(pad, {blob_path: blob, f"records/{family}/revisions/{revision_id}.json": canonical_json_bytes(revision)})
    whole = read_committed_snapshot(workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, prefixes=("records/",))
    selection = read_committed_snapshot(workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, prefixes=native_records.PUBLISH_PREFIXES, child_prefixes=CHILDREN)
    assert blob_path in whole.artifacts

    head = pad.head()
    with pytest.raises(PrivateRecordError) as refused:
        _create(pad, "family-cite", _citing(_cite(pad, blob_path)))
    assert refused.value.code == "native_record_scope_refused" and pad.head() == head
    # The selection: only the receipts' own directory is captured whole, so only there is the file read at all.
    assert (blob_path in selection.artifacts) is (family == "operations")
    # The owner rule, given EVERY file of records/: no owner outside a record_<uuid> directory.
    with pytest.raises(PrivateRecordError, match="no authoritative owner"):
        native_records._authoritative_media_type(whole, blob_path)
    assert re.fullmatch(private_records.RECORD_DIRECTORY_PATTERN, family) is None


def test_every_family_under_records_is_named_here() -> None:
    """The grep behind ``OTHER_FAMILIES``: every literal ``records/<family>`` in the package source."""

    source = Path(native_records.__file__).resolve().parent
    found: dict[str, str] = {}
    for path in sorted(source.rglob("*.py")):
        for name in re.findall(r"""["'f]records/([a-z][a-z0-9-]*)""", path.read_text(encoding="utf-8")):
            found.setdefault(name, str(path.relative_to(source)))
    unknown = {name: where for name, where in found.items() if name not in OTHER_FAMILIES}
    assert not unknown, (
        f"new families under records/: {unknown}. A native sidecar must not be able to cite a path there "
        "(native_records._assert_refs_authentic reads the publish selection only): add the family to OTHER_FAMILIES "
        "so test_nothing_under_another_family_of_records_can_be_cited covers it."
    )
    assert set(OTHER_FAMILIES) == set(found)


def test_the_override_check_sees_every_revision_it_needs(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=5)
    base = _create(pad, "override-base", _profile())
    reference = pad.reference("the resume\n", "override-ref")
    pad.record(reference.item_id, "override-private-record")
    # Other families keep ``/revisions/`` paths too (proposals, interviews, documents), in their own shapes: files
    # committed here at those writers' paths. The whole-``records/`` capture walked them as private-record
    # revisions and refused EVERY override on a workpad that held one ("committed native evidence is invalid").
    _commit(pad, {
        "records/scout-proposals/proposal_1/revisions/revision_1.json": b'{"proposal": "not a private-record revision"}',
        "records/scout-interviews/interview_1/revisions/revision_1.json": b'{"interview": 1}',
        "records/scout-documents/document_1/revisions/revision_1/record.json": b'{"document": 1}',
    })
    context = f"task_context_{uuid.uuid4()}"
    scope = {"mode": "run_override", "task_context_id": context, "base": {"record_id": base.record_id, "revision_id": base.revision_id}}
    arguments = dict(pad.scope, content=_profile(), actor=OPERATOR, origin="user_reported", base=scope["base"])

    first = create_task_override(**arguments, scope=scope, operation_key="override-1")  # type: ignore[arg-type]
    assert first.created and first.task_context_id == context and first.record_id != base.record_id
    # A second, distinct override in the same task context is refused: the walk found the first.
    head = pad.head()
    with pytest.raises(PrivateRecordError, match="distinct override") as refused:
        create_task_override(**arguments, scope=scope, operation_key="override-2")  # type: ignore[arg-type]
    assert refused.value.code == "native_record_scope_refused" and pad.head() == head
    # Another task context is its own override; the same request again is the same override.
    other_scope = dict(scope, task_context_id=f"task_context_{uuid.uuid4()}")
    assert create_task_override(**arguments, scope=other_scope, operation_key="override-3").created  # type: ignore[arg-type]
    replay = create_task_override(**arguments, scope=scope, operation_key="override-1")  # type: ignore[arg-type]
    assert (replay.created, replay.record_id, replay.revision_id) == (False, first.record_id, first.revision_id)

    # Why the narrow capture is enough: under the lock it holds EVERY revision and sidecar of every private and
    # native record that the whole capture holds, and each of its ``/revisions/`` paths is under records/record_<uuid>/.
    def under_lock(writer):  # noqa: ANN001, ANN202
        return writer.snapshot(native_records.PUBLISH_PREFIXES, child_prefixes=CHILDREN), writer.snapshot(("records/", "references/", "run-inputs/", "manifests/capabilities/"))

    narrow, whole = journal.run_with_journal_writer(workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, operation=under_lock)
    revisions = sorted(path for path in narrow.artifacts if "/revisions/" in path)
    assert len(revisions) == 4 and all(re.match(r"records/record_[0-9a-f-]{36}/revisions/", path) for path in revisions)
    assert {path: data for path, data in whole.artifacts.items() if path.startswith("records/record_")} == {
        path: data for path, data in narrow.artifacts.items() if path.startswith("records/record_")
    }
    assert narrow.head == whole.head
    assert sorted(path for path in whole.artifacts if "/revisions/" in path and path not in narrow.artifacts) == [
        "records/scout-documents/document_1/revisions/revision_1/record.json",
        "records/scout-interviews/interview_1/revisions/revision_1.json",
        "records/scout-proposals/proposal_1/revisions/revision_1.json",
    ]


def test_the_writer_capture_is_the_selection_the_lock_free_read_gives(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=5)
    reference = pad.reference("a resume\n", "child-ref")
    record = _create(pad, "child-qa", _citing(dict(reference.record["snapshot"])))  # type: ignore[arg-type]

    narrow = journal.run_with_journal_writer(
        workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id,
        operation=lambda writer: writer.snapshot(native_records.PUBLISH_PREFIXES, child_prefixes=CHILDREN),
    )
    lock_free = read_committed_snapshot(workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, prefixes=native_records.PUBLISH_PREFIXES, child_prefixes=CHILDREN)
    assert narrow.head == lock_free.head == pad.head() and narrow.artifacts == lock_free.artifacts
    assert {f"records/{record.record_id}/revisions/{record.revision_id}.json", f"records/{record.record_id}/blobs/{record.revision_id}.json"} <= set(narrow.artifacts)
    assert not any(path.startswith(OTHER_FAMILY) for path in narrow.artifacts)
    # A native publish captures what a private publish does, and the capability manifests.
    assert set(native_records.PUBLISH_PREFIXES) == {*private_records.PUBLISH_PREFIXES, "manifests/capabilities/"}


def test_a_retry_a_reused_key_a_stale_parent_and_an_archived_record_behave_as_before(tmp_path: Path) -> None:
    pad = _Pad(tmp_path)
    first = _create(pad, "keep-qa")
    second = _update(pad, first.record_id, first.revision_id, "keep-qa-2")
    head = pad.head()

    # The same request again (the same key, the same payload): the same receipt, nothing committed.
    again = _create(pad, "keep-qa")
    assert (again.created, again.record_id, again.revision_id, again.receipt) == (False, first.record_id, first.revision_id, first.receipt)
    replay = _update(pad, first.record_id, first.revision_id, "keep-qa-2")
    assert (replay.created, replay.revision_id, replay.receipt) == (False, second.revision_id, second.receipt)
    assert pad.head() == head and _chain(pad, first.record_id) == [first.revision_id, second.revision_id]

    # A key used again with another payload is refused, not answered from the receipt.
    with pytest.raises(PrivateRecordError) as conflict:
        _create(pad, "keep-qa", _profile())
    assert conflict.value.code == "native_operation_conflict"
    with pytest.raises(PrivateRecordError) as conflict:
        _update(pad, first.record_id, second.revision_id, "keep-qa-2")
    assert conflict.value.code == "native_operation_conflict"

    # The parent check: an update that does not name the current revision is refused, whoever it names.
    for key, parent in (("stale-unknown", "revision_ffffffff-ffff-4fff-8fff-ffffffffffff"), ("stale-behind", first.revision_id)):
        with pytest.raises(PrivateRecordError) as stale:
            _update(pad, first.record_id, parent, key)
        assert stale.value.code == "stale_parent", key
    with pytest.raises(PrivateRecordError) as stale:
        archive_native_record(**pad.scope, record_id=first.record_id, parent_revision=first.revision_id, actor=OPERATOR, operation_key="stale-archive")  # type: ignore[arg-type]
    assert stale.value.code == "stale_parent"
    assert pad.head() == head

    third = _update(pad, first.record_id, second.revision_id, "keep-qa-3", _experience())
    archived = archive_native_record(**pad.scope, record_id=first.record_id, parent_revision=third.revision_id, actor=OPERATOR, operation_key="keep-qa-4")  # type: ignore[arg-type]
    assert _chain(pad, first.record_id) == [first.revision_id, second.revision_id, third.revision_id, archived.revision_id]
    # An archived record takes no further revision, and its history reads as it was written.
    head = pad.head()
    with pytest.raises(PrivateRecordError) as closed:
        _update(pad, first.record_id, archived.revision_id, "keep-qa-5")
    assert closed.value.code == "native_record_archived" and pad.head() == head
    assert b'"answer":"Led a small team."' in read_native_record(**pad.scope, record_id=first.record_id, revision_id=second.revision_id, content=True)["content"]  # type: ignore[arg-type, operator]
    assert list_native_records(**pad.scope) == [] and list_native_records(**pad.scope, include_archived=True)[0]["state"] == "archived"  # type: ignore[arg-type]


def test_a_crash_between_the_steps_of_a_native_write_is_recovered_by_the_same_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pad = _Pad(tmp_path)

    # 1. The process dies between two publishes (the evidence is committed, the record that cites it is not).
    reference = pad.reference("the resume\n", "crash-ref")
    #    The same two calls again: the reference is answered from its receipt, the record is created once.
    again = pad.reference("the resume\n", "crash-ref")
    record = _create(pad, "crash-profile", _citing(dict(again.record["snapshot"])))  # type: ignore[arg-type]
    assert (again.created, again.item_id, record.created) == (False, reference.item_id, True)
    assert _chain(pad, record.record_id) == [record.revision_id]

    # 2. The process dies after the commit, before anything derived from it. The write is sealed and says the
    #    projection is pending; the same call again returns the same receipt and commits nothing.
    qa = _create(pad, "crash-qa")
    sealed = _update(pad, qa.record_id, qa.revision_id, "crash-qa-2")
    assert sealed.created is True and sealed.projection_pending is True and sealed.rebuild_action is None
    head = pad.head()
    replay = _update(pad, qa.record_id, qa.revision_id, "crash-qa-2")
    assert (replay.created, replay.revision_id, replay.receipt) == (False, sealed.revision_id, sealed.receipt) and pad.head() == head
    private_records.catch_up_scout_projection(resolved=private_records._resolved(**pad.scope))  # type: ignore[arg-type]
    context = json.loads((pad.workpad / "indexes" / "context.json").read_bytes())
    assert [item["revision_id"] for item in context["records"] if item["record_id"] == qa.record_id] == [sealed.revision_id]
    assert _update(pad, qa.record_id, qa.revision_id, "crash-qa-2").projection_pending is False

    # 3. The process dies INSIDE a transition (the files are in place, the commit never happened). The next write
    #    is refused until the journal is reconciled; then the same call is answered from the receipt: one revision.
    with monkeypatch.context() as patched:
        patched.setattr(journal, "_commit_handoff", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated crash before the commit")))
        with pytest.raises(OSError, match="simulated crash"):
            _update(pad, qa.record_id, sealed.revision_id, "crash-qa-3", _experience())
    assert pad.head() == head  # the revision and its sidecar are on disk, uncommitted
    with pytest.raises(JournalConflictError):
        _chain(pad, qa.record_id)  # a read does not skip the half-written revision
    with pytest.raises(JournalConflictError):
        _update(pad, qa.record_id, sealed.revision_id, "crash-qa-3", _experience())  # nor does the update's own read of its parent
    with pytest.raises(PrivateRecordError) as refused:
        _create(pad, "crash-unrelated")  # no native write proceeds past a half-written one: the capture under the lock sees it
    assert refused.value.code == "native_record_not_authenticated"
    assert pad.head() == head
    pad.reconcile()
    recovered = _update(pad, qa.record_id, sealed.revision_id, "crash-qa-3", _experience())
    assert recovered.created is False  # the reconciled transition is the write; it is not made twice
    assert _chain(pad, qa.record_id) == [qa.revision_id, sealed.revision_id, recovered.revision_id]
    assert b'"answer":null' in read_native_record(**pad.scope, record_id=qa.record_id, content=True)["content"]  # type: ignore[arg-type, operator]


def _write_in_a_process(arguments: dict[str, object], queue: object) -> None:
    try:
        result = update_native_record(**arguments)  # type: ignore[arg-type]
        queue.put(("ok", result.revision_id))  # type: ignore[attr-defined]
    except PrivateRecordError as exc:
        queue.put((exc.code, ""))  # type: ignore[attr-defined]


def test_three_processes_writing_the_same_native_record_have_one_winner(tmp_path: Path) -> None:
    pad = _Pad(tmp_path)
    initial = _create(pad, "race-initial")
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    base = {**pad.scope, "record_id": initial.record_id, "parent_revision": initial.revision_id, "content": _experience(answered=True), "actor": OPERATOR, "origin": "user_reported"}
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
    assert _chain(pad, initial.record_id) == [initial.revision_id, winner]


def test_a_differing_file_blocks_a_native_write_only_where_the_write_consults_it(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=20)
    reference = pad.reference("the resume\n", "corrupt-ref")
    record = _create(pad, "corrupt-qa")
    other_record = _create(pad, "corrupt-other", _citing(dict(reference.record["snapshot"])))  # type: ignore[arg-type]
    bystander = pad.workpad / pad.other[3]
    committed = pad.committed(pad.other[3])

    # --- outside the selection: a file of the other family that is not what is committed -----------------
    bystander.write_bytes(b'{"board": "changed outside the journal"}')
    second = _update(pad, record.record_id, record.revision_id, "corrupt-qa-2")
    assert second.created is True and _chain(pad, record.record_id) == [record.revision_id, second.revision_id]
    # The native write did not commit the changed file, and did not put it back either.
    assert pad.committed(pad.other[3]) == committed
    assert bystander.read_bytes() == b'{"board": "changed outside the journal"}'
    # Where that family IS read, it is refused exactly as before.
    with pytest.raises(JournalConflictError, match="working evidence"):
        read_committed_snapshot(workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, prefixes=(OTHER_FAMILY,))
    # An extra, uncommitted file there does not block a native write either.
    extra = pad.workpad / OTHER_FAMILY / "scout_watchlist:greenhouse:extra.json"
    extra.write_bytes(b"{}")
    assert _create(pad, "corrupt-while-extra").created is True
    extra.unlink()
    bystander.write_bytes(committed)

    # --- inside the selection: every one of these still refuses the write, the head unmoved ---------------
    def refused(path: Path, changed: bytes | None, key: str) -> None:
        original = path.read_bytes() if path.exists() else None
        made = [folder for folder in (path.parent, path.parent.parent) if not folder.exists()]
        head = pad.head()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(changed if changed is not None else b"{}\n")
        try:
            # A new record has no read before the lock: the capture UNDER the lock is what refuses it.
            with pytest.raises(PrivateRecordError) as error:
                _create(pad, f"{key}-create")
            assert error.value.code == "native_record_not_authenticated", path
            # An update of the record is refused too: by the same capture, or (a file of a record's own folder)
            # already by the read of its parent chain before the lock, which raises the journal's own error.
            with pytest.raises((PrivateRecordError, JournalConflictError)) as error:
                _update(pad, record.record_id, second.revision_id, key, _experience())
            assert getattr(error.value, "code", "") in ("native_record_not_authenticated", "journal_conflict"), path
            assert pad.head() == head, path
        finally:
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)
            for folder in made:
                folder.rmdir()

    own = pad.workpad / "records" / record.record_id
    # the record being written: one of its own revisions, and the sidecar of one
    refused(own / "revisions" / f"{record.revision_id}.json", b'{"schema_version":"1.0"}\n', "corrupt-own-revision")
    refused(own / "blobs" / f"{second.revision_id}.json", b'{"schema_version":"1.0"}\n', "corrupt-own-sidecar")
    # the receipts
    receipt = next(path for path in sorted((pad.workpad / "records" / "operations").iterdir()) if path.name.startswith("record_update-"))
    refused(receipt, b"{}\n", "corrupt-receipt")
    # a reference (evidence a sidecar may cite)
    refused(pad.workpad / "references" / reference.item_id / "source.txt", b"not what was imported\n", "corrupt-reference")
    # ANOTHER record: the selection is every records/record_<uuid>/, so this refuses too (as before)
    refused(pad.workpad / "records" / other_record.record_id / "revisions" / f"{other_record.revision_id}.json", b'{"schema_version":"1.0"}\n', "corrupt-other")
    # extra, uncommitted files: among the receipts, in a record's own folder, among the capability manifests
    refused(pad.workpad / "records" / "operations" / "record_update-stray.json", None, "corrupt-stray-receipt")
    refused(own / "revisions" / "revision_stray.json", None, "corrupt-stray-revision")
    refused(pad.workpad / "manifests" / "capabilities" / "stray.json", None, "corrupt-stray-manifest")

    # With everything as committed again, the write goes through.
    third = _update(pad, record.record_id, second.revision_id, "corrupt-qa-3", _experience())
    assert _chain(pad, record.record_id) == [record.revision_id, second.revision_id, third.revision_id]
