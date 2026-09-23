from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import uuid

import pytest

from gigai.lifecycle import create_offline
from gigai.canonical import canonical_json_bytes
from gigai.private_records import (
    PrivateRecordError,
    create_record,
    import_reference,
    import_run_input,
    migrate_workpad_layout,
    read_record,
)
import gigai.private_records as private_records
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _configured(tmp_path: Path) -> tuple[Path, Path]:
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
    return home, target


def _ids():
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{value:012x}") for value in range(1, 100))
    return lambda: next(values)


def _concurrent_update(arguments: dict[str, object], queue: object) -> None:
    try:
        result = create_record(**arguments)  # type: ignore[arg-type]
        queue.put(("ok", result.revision_id))  # type: ignore[attr-defined]
    except PrivateRecordError as exc:
        queue.put((exc.code, ""))  # type: ignore[attr-defined]


def test_private_import_revision_context_and_stale_parent_are_journaled(tmp_path: Path) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(home_root=home, requested_target=target, name="private-records", open_editor=False, uuid_factory=_ids())
    migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id, uuid_factory=_ids())
    source = tmp_path / "resume.md"
    source.write_bytes(b"Jane Example\n")

    reference = import_reference(home_root=home, requested_target=target, gig_id=created.gig_id, kind="resume", source=source, operation_key="ref-1", uuid_factory=_ids())
    assert reference.created is True
    assert source.read_bytes() == b"Jane Example\n"
    replay = import_reference(home_root=home, requested_target=target, gig_id=created.gig_id, kind="resume", source=source, operation_key="ref-2", uuid_factory=_ids())
    assert replay.created is False and replay.item_id == reference.item_id
    posting = import_run_input(home_root=home, requested_target=target, gig_id=created.gig_id, data=b"Job description\n", operation_key="posting-1", uuid_factory=_ids())
    revision = create_record(home_root=home, requested_target=target, gig_id=created.gig_id, kind="imported_reference", content_family="g45_reference", content_id=reference.item_id, actor={"kind":"operator","id":"local-user"}, origin="imported", operation_key="record-1", uuid_factory=_ids())
    assert read_record(home_root=home, requested_target=target, gig_id=created.gig_id, record_id=revision.record_id, content=True)["content"] == b"Jane Example\n"
    metadata = json.loads((created.workpad / "indexes" / "context.json").read_bytes())
    assert b"Jane Example" not in (created.workpad / "indexes" / "context.json").read_bytes()
    assert metadata["records"][0]["revision_id"] == revision.revision_id
    with pytest.raises(PrivateRecordError, match="current parent"):
        create_record(home_root=home, requested_target=target, gig_id=created.gig_id, kind="supplied_source", content_family="g45_run_input", content_id=posting.item_id, actor={"kind":"operator","id":"local-user"}, origin="imported", operation_key="record-2", record_id=revision.record_id, parent_revision="revision_ffffffff-ffff-4fff-8fff-ffffffffffff", uuid_factory=_ids())


def test_v1_workpad_refuses_private_import_until_explicit_migration(tmp_path: Path) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(home_root=home, requested_target=target, name="v1-private", open_editor=False, uuid_factory=_ids())
    source = tmp_path / "resume.txt"
    source.write_text("text\n", encoding="utf-8")
    with pytest.raises(PrivateRecordError, match="migrate"):
        import_reference(home_root=home, requested_target=target, gig_id=created.gig_id, kind="resume", source=source)


def test_committed_bytes_parent_chain_and_projection_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(home_root=home, requested_target=target, name="c1-chain", open_editor=False)
    migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)
    source = tmp_path / "resume.md"
    source.write_bytes(b"committed source\n")
    reference = import_reference(home_root=home, requested_target=target, gig_id=created.gig_id, kind="resume", source=source, operation_key="c1-ref")
    record_id = "record_00000000-0000-4000-8000-000000000010"
    # The revision UUIDs deliberately run in lexical reverse order.  Readers
    # must follow authenticated parents, never sort UUID text.
    values = iter((
        uuid.UUID("ffffffff-ffff-4fff-8fff-fffffffffff1"),
        uuid.UUID("00000000-0000-4000-8000-000000000011"),
        uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
        uuid.UUID("00000000-0000-4000-8000-000000000012"),
        uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"),
        uuid.UUID("00000000-0000-4000-8000-000000000013"),
    ))
    def factory() -> uuid.UUID:
        return next(values)
    first = create_record(home_root=home, requested_target=target, gig_id=created.gig_id, kind="imported_reference", content_family="g45_reference", content_id=reference.item_id, actor={"kind": "operator", "id": "local-user"}, origin="imported", operation_key="c1-record-1", record_id=record_id, uuid_factory=factory)
    second = create_record(home_root=home, requested_target=target, gig_id=created.gig_id, kind="imported_reference", content_family="g45_reference", content_id=reference.item_id, actor={"kind": "operator", "id": "local-user"}, origin="imported", operation_key="c1-record-2", record_id=record_id, parent_revision=first.revision_id, uuid_factory=factory)
    assert [item["revision_id"] for item in private_records.list_revisions(resolved=private_records._resolved(home_root=home, requested_target=target, gig_id=created.gig_id), record_id=record_id)] == [first.revision_id, second.revision_id]
    # A valid-looking uncommitted replacement cannot become selected content.
    record_path = created.workpad / "references" / reference.item_id / "reference.json"
    record_path.write_text('{"schema_version":"1.0"}\n', encoding="utf-8")
    with pytest.raises(PrivateRecordError, match="working evidence differs"):
        read_record(home_root=home, requested_target=target, gig_id=created.gig_id, record_id=record_id, revision_id=first.revision_id, content=True)
    record_path.write_bytes(canonical_json_bytes(reference.record))
    monkeypatch.setattr(private_records, "rebuild_scout_projection", lambda **_kwargs: (_ for _ in ()).throw(OSError("projection unavailable")))
    pending = import_run_input(home_root=home, requested_target=target, gig_id=created.gig_id, data=b"posting", operation_key="c1-pending")
    assert pending.projection_pending is True and pending.receipt is not None


def test_subprocess_same_parent_has_one_winner(tmp_path: Path) -> None:
    home, target = _configured(tmp_path)
    created = create_offline(home_root=home, requested_target=target, name="c1-cas", open_editor=False)
    migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id)
    source = tmp_path / "resume.md"
    source.write_text("source\n", encoding="utf-8")
    reference = import_reference(home_root=home, requested_target=target, gig_id=created.gig_id, kind="resume", source=source, operation_key="cas-ref")
    initial = create_record(home_root=home, requested_target=target, gig_id=created.gig_id, kind="imported_reference", content_family="g45_reference", content_id=reference.item_id, actor={"kind": "operator", "id": "local-user"}, origin="imported", operation_key="cas-initial")
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    base = {"home_root": home, "requested_target": target, "gig_id": created.gig_id, "kind": "imported_reference", "content_family": "g45_reference", "content_id": reference.item_id, "actor": {"kind": "operator", "id": "local-user"}, "origin": "imported", "record_id": initial.record_id, "parent_revision": initial.revision_id}
    workers = [context.Process(target=_concurrent_update, args=({**base, "operation_key": f"cas-{number}"}, queue)) for number in (1, 2)]
    for worker in workers:
        worker.start()
    outcomes = [queue.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0
    assert sorted(value[0] for value in outcomes) == ["ok", "stale_parent"]
