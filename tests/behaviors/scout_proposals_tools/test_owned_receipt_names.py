"""STORE1B: the owner of a receipt is read from its file name, so the name is guarded.

``records/operations/`` is shared by five writers. The projection rebuild and
the equivalent-import lookup decide whether a receipt is theirs to read from
its FILE NAME (``private_records._receipt_kind``), without opening it. That
rule took everything before the LAST dash of the name: a digest holding a dash
(a uuid) would have made an owned receipt look like another module's, and it
would have been skipped in silence: an incomplete rebuild, and an equivalent
import that no longer finds its receipt.

What these tests pin:

* each of the five owned operations, published through the product's own
  writers, commits a receipt whose file name reads back as that operation;
* both writers build the name in one place, and that place refuses, at write
  time and before anything is committed, a name whose digest is not 64 hex;
* a receipt under an owned operation's name is owned whatever follows the
  operation: it is in the rebuild and it answers an equivalent import;
* every name the five writers produce today reads as it did before.

Real journal commits (git subprocesses, ``tmp_path``): the integration lane.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Callable

import pytest

import gigai.native_records as native_records
import gigai.private_records as private_records
from gigai.native_records import archive_native_record, create_native_record, update_native_record
from gigai.private_records import PrivateRecordError, import_run_input, rebuild_scout_projection
from gigai.scout import interview_records
from gigai.scout.find_jobs import watchlist

from .test_private_publish_selection import _Pad
from .test_scout03_native_records import _experience

OPERATOR = {"kind": "operator", "id": "user"}
_HEX = "ab" * 32
#: A digest in the form the old rule could not read: a uuid, with its dashes.
_DASHED = "123e4567-e89b-42d3-a456-426614174000"
_NAME = re.compile(r"records/operations/(?P<operation>[a-z_]+)-(?P<digest>[0-9a-f]{64})\.json")


def _resolved(pad: _Pad):  # noqa: ANN202 - a ResolvedWorkpad
    return private_records._resolved(home_root=pad.home, requested_target=pad.target, gig_id=pad.gig_id)


def _committed_receipts(pad: _Pad) -> dict[str, dict[str, object]]:
    """Every committed file of ``records/operations/`` at the head, by path."""

    listed = subprocess.run(["git", "-C", str(pad.workpad), "ls-tree", "-r", "--name-only", "HEAD", "records/operations/"], capture_output=True, text=True, check=True)
    return {path: json.loads(pad.committed(path)) for path in listed.stdout.split()}


def _writes(pad: _Pad, done: dict[str, object], key: Callable[[str], str]) -> dict[str, tuple[str, Callable[[], object]]]:
    """Every product writer of an owned receipt: ``name -> (operation, the write)``. ``done`` holds the earlier results."""

    return {
        "reference": ("reference_add", lambda: pad.reference("the resume\n", key("reference"))),
        "run input": ("run_input_add", lambda: import_run_input(**pad.scope, data=b"a posting\n", operation_key=key("run input"))),  # type: ignore[arg-type]
        "record create": ("record_create", lambda: pad.record(done["reference"].item_id, key("record create"))),  # type: ignore[attr-defined]
        "record update": ("record_update", lambda: pad.record(
            done["reference"].item_id, key("record update"), record_id=done["record create"].record_id, parent_revision=done["record create"].revision_id,  # type: ignore[attr-defined]
        )),
        "native create": ("record_create", lambda: create_native_record(**pad.scope, content=_experience(), actor=OPERATOR, origin="user_reported", operation_key=key("native create"))),  # type: ignore[arg-type]
        "native update": ("record_update", lambda: update_native_record(
            **pad.scope, record_id=done["native create"].record_id, parent_revision=done["native create"].revision_id,  # type: ignore[arg-type, attr-defined]
            content=_experience(answered=True), actor=OPERATOR, origin="user_reported", operation_key=key("native update"),
        )),
        "native archive": ("record_archive", lambda: archive_native_record(
            **pad.scope, record_id=done["native create"].record_id, parent_revision=done["native update"].revision_id,  # type: ignore[arg-type, attr-defined]
            actor=OPERATOR, operation_key=key("native archive"),
        )),
    }


def test_each_owned_operation_commits_a_receipt_whose_file_name_reads_back_as_it(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=0)
    done: dict[str, object] = {}
    operations: dict[str, str] = {}
    for name, (operation, write) in _writes(pad, done, lambda name: f"named:{name.replace(' ', '-')}").items():
        done[name] = write()
        operations[name] = operation
    committed = _committed_receipts(pad)
    assert len(committed) == len(done) == 7

    for name, result in done.items():
        receipt = result.receipt  # type: ignore[attr-defined]
        assert result.created is True and receipt["operation"] == operations[name], name  # type: ignore[attr-defined]
        (path,) = [path for path, value in committed.items() if value["operation_id"] == receipt["operation_id"]]
        named = _NAME.fullmatch(path)
        assert named is not None, f"{name}: {path}"
        assert private_records._receipt_kind(path) == named["operation"] == operations[name], f"{name}: {path}"
    # All five owned operations were published, and nothing else was.
    assert {private_records._receipt_kind(path) for path in committed} == set(operations.values()) == private_records.OWNED_OPERATIONS

    # The outcome the rule exists for: the rebuild reads every one of them and leaves none to another module.
    rebuilt = rebuild_scout_projection(resolved=_resolved(pad))
    assert (rebuilt.operations, dict(rebuilt.skipped_receipts)) == (7, {})


def test_both_writers_name_a_receipt_the_same_way() -> None:
    for operation in sorted(private_records.OWNED_OPERATIONS):
        path = private_records._receipt_path(operation, "one:key")
        assert path == native_records._receipt_path(operation, "one:key")
        named = _NAME.fullmatch(path)
        assert named is not None and named["operation"] == operation == private_records._receipt_kind(path)


def test_no_owned_operation_can_be_read_as_another() -> None:
    owned = private_records.OWNED_OPERATIONS
    assert not any("-" in operation for operation in owned)
    assert not any(one != other and one.startswith(f"{other}-") for one in owned for other in owned)


@pytest.mark.parametrize("name", ["reference", "run input", "record create", "record update", "native create", "native update", "native archive"])
def test_a_receipt_name_whose_digest_is_not_plain_hex_is_refused_before_anything_is_committed(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pad = _Pad(tmp_path, other_files=0)
    done: dict[str, object] = {}
    # What this write builds on (a reference for a record, a record for its update), written as it is today.
    order = ["reference", "run input", "record create", "record update", "native create", "native update", "native archive"]
    needs = {"record create": ["reference"], "record update": ["reference", "record create"], "native update": ["native create"], "native archive": ["native create", "native update"]}
    earlier = _writes(pad, done, lambda name: f"earlier:{name.replace(' ', '-')}")
    for needed in needs.get(name, []):
        done[needed] = earlier[needed][1]()
    assert name in order
    head, before = pad.head(), _committed_receipts(pad)

    key = "guarded:key"
    operation, write = _writes(pad, done, lambda _name: key)[name]
    with monkeypatch.context() as patched:
        # The digest of THIS operation key comes back in another form (a uuid); every other digest is the real one.
        for module in (private_records, native_records):
            real = module.digest_imported_bytes
            patched.setattr(module, "digest_imported_bytes", lambda data, real=real: f"sha256:{_DASHED}" if data == key.encode() else real(data))
        with pytest.raises(PrivateRecordError) as refused:
            write()
    assert refused.value.code == "operation_receipt_name_invalid", name
    # A hard error at write time: no commit, no receipt under any name.
    assert pad.head() == head and _committed_receipts(pad) == before
    assert not any(_DASHED in path.name for path in (pad.workpad / "records").rglob("*"))

    # With the digest as it is, the same request is an ordinary first write.
    result = write()
    assert result.created is True and result.receipt["operation"] == operation  # type: ignore[attr-defined]


def test_the_name_builder_refuses_an_operation_it_does_not_own() -> None:
    with pytest.raises(PrivateRecordError) as refused:
        private_records._receipt_path("scout_watchlist_seed", "one:key")
    assert refused.value.code == "operation_receipt_name_invalid"
    with pytest.raises(PrivateRecordError) as refused:
        native_records._receipt_path("some_future_operation", "one:key")
    assert refused.value.code == "operation_receipt_name_invalid"


def test_a_receipt_under_an_owned_name_is_owned_whatever_follows_the_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pad = _Pad(tmp_path, other_files=0)
    dashed = f"records/operations/reference_add-{_DASHED}.json"
    # A writer that named its receipt with a dashed digest (no writer of this build does: the test above). The
    # product's own import commits the reference; only the receipt's file name is another's.
    with monkeypatch.context() as patched:
        patched.setattr(private_records, "_receipt_path", lambda operation, key: dashed)
        reference = pad.reference("the resume\n", "dashed-ref")
    assert reference.created is True and list(_committed_receipts(pad)) == [dashed]

    # The rebuild reads it: it is in the projection, not counted as some other module's.
    rebuilt = rebuild_scout_projection(resolved=_resolved(pad))
    assert (rebuilt.operations, dict(rebuilt.skipped_receipts)) == (1, {})
    # And the same file imported again under another key is answered from that receipt, not refused.
    again = pad.reference("the resume\n", "dashed-ref-again")
    assert (again.created, again.item_id, again.receipt) == (False, reference.item_id, reference.receipt)
    assert private_records._receipt_kind(dashed) == "reference_add"


@pytest.mark.parametrize(("path", "kind"), [
    *[(f"records/operations/{operation}-{_HEX}.json", operation) for operation in ("reference_add", "run_input_add", "record_create", "record_update", "record_archive")],
    (watchlist._receipt_path("scout_watchlist_seed", "seed:key"), "scout_watchlist_seed"),
    (watchlist._receipt_path("scout_watchlist_add", "add:key"), "scout_watchlist_add"),
    (f"records/operations/application-record-{_HEX}.json", "application-record"),  # application_events.py
    (interview_records._receipt_path("interview:key"), "interview"),
    (f"records/operations/some_future_operation-{_HEX}.json", "some_future_operation"),
    (f"records/operations/another-kind-{_HEX}.json", "another-kind"),
    ("records/operations/notes.json", "notes"),
])
def test_every_name_written_today_reads_as_it_did(path: str, kind: str) -> None:
    stem = path.removeprefix("records/operations/").removesuffix(".json")
    # The rule before STORE1B, word for word: everything before the last dash.
    assert private_records._receipt_kind(path) == kind == (stem.rpartition("-")[0] or stem)
    assert (kind in private_records.OWNED_OPERATIONS) == (kind in {"reference_add", "run_input_add", "record_create", "record_update", "record_archive"})
