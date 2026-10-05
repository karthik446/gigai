"""0110-10-16: the private-record projection is rebuilt outside a save, and can be.

``rebuild_scout_projection`` validated EVERY file of ``records/operations/``
against the receipt schema of the five private operations. Other modules keep
their receipts in the same folder, in their own shapes: the watchlist (a
seed's receipt has one artifact ref per board), the application events, the
interview records. On a home with any of them the rebuild raised, the save
swallowed it and answered ``projection_pending``, and every later save paid
for a rebuild that wrote nothing.

What these tests pin:

* a rebuild reads the receipts of the operations it owns and no other: a home
  with a watchlist seed (and an add, an application receipt, an interview
  receipt, a kind nobody knows) rebuilds to the journal head, the others
  counted;
* a receipt of an OWNED kind that does not meet the contract still refuses
  the rebuild, and nothing of the projection is rewritten;
* a save and its retry make no rebuild attempt and capture once; the result
  says the projection is pending, and after a catch-up the retry says it is
  not;
* a rebuild leaves Scout's own row in ``scout_meta`` as it was;
* a home that is behind catches up in one background thread that no save
  waits for, and the server starts that thread once.

Real journal commits (git subprocesses, ``tmp_path``): the integration lane.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from types import SimpleNamespace
import uuid

from click.testing import CliRunner
import pytest

import gigai.private_records as private_records
from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.index import database_lock
from gigai.journal import JournalArtifact, record_transition
from gigai.native_records import archive_native_record, create_native_record, update_native_record
from gigai.private_records import PrivateRecordError, ProjectionRebuild, catch_up_scout_projection, import_run_input, rebuild_scout_projection
from gigai.scout.find_jobs.company_catalog import CompanyCatalog, CompanyRecord
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.watchlist import JournalWatchlistClient, seed_watchlist_from_catalog, watchlist_entry_from_url
from tests.support.latency import latency_bound

from .test_private_publish_selection import _captures, _Pad
from .test_scout03_native_records import _experience

OPERATOR = {"kind": "operator", "id": "user"}
BOARDS = 30
_NO_FILTER = SimpleNamespace(countries=(), exclude_companies=(), watch_companies=())
_DIGEST = "ab" * 32


def _resolved(pad: _Pad):  # noqa: ANN202 - a ResolvedWorkpad
    return private_records._resolved(home_root=pad.home, requested_target=pad.target, gig_id=pad.gig_id)


def _commit(pad: _Pad, path: str, data: bytes) -> None:
    """One file committed through the journal, as its own module would commit it."""

    record_transition(
        workpad=pad.workpad, project_id=pad.project_id, gig_id=pad.gig_id, handoff_id=f"handoff_{uuid.uuid4()}",
        transition="private_reference_imported", body="Committed a receipt of another module.", artifacts=(JournalArtifact(path, data),),
    )


def _seed_watchlist(pad: _Pad, boards: int = BOARDS) -> None:
    """The product's own seed: one ``scout_watchlist_seed`` receipt with one artifact ref per board."""

    catalog = CompanyCatalog(
        revision="projection-test", digest="sha256:" + "d" * 64, size_bytes=1, skipped=0,
        records=tuple(
            CompanyRecord(name=f"Company {index}", provider=ATSProvider.GREENHOUSE, board_token=f"board{index:05d}", board_url=f"https://example.test/board{index:05d}", hq_country="US")
            for index in range(boards)
        ),
    )
    seeded = seed_watchlist_from_catalog(pad.home, pad.target, pad.gig_id, prefs=_NO_FILTER, catalog=catalog)
    assert seeded.added == boards


def _tables(pad: _Pad) -> dict[str, dict[str, bytes]]:
    connection = sqlite3.connect(f"file:{pad.workpad / 'state.sqlite'}?mode=ro", uri=True)
    try:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        return {
            name: {key: bytes(payload) for key, payload in connection.execute(f"SELECT key, payload FROM {name}")}
            for name in ("scout_records", "scout_operations", "scout_meta") if name in names
        }
    finally:
        connection.close()


def _cursor(pad: _Pad) -> str | None:
    return private_records._projection_cursor(pad.workpad)


def _receipts(pad: _Pad) -> list[str]:
    return sorted(f"records/operations/{path.name}" for path in (pad.workpad / "records" / "operations").iterdir())


def _owned(paths: list[str]) -> list[str]:
    return [path for path in paths if path.split("/")[-1].rsplit("-", 1)[0] in private_records.OWNED_OPERATIONS]


def _rebuild_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every call of the rebuild from here on (the real one still runs)."""

    calls: list[int] = []
    real = private_records.rebuild_scout_projection

    def counted(**kwargs: object) -> ProjectionRebuild:
        calls.append(1)
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(private_records, "rebuild_scout_projection", counted)
    return calls


def test_a_home_with_a_watchlist_seed_receipt_rebuilds_to_the_journal_head(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=0)
    reference = pad.reference("the resume\n", "seeded-ref")
    record = pad.record(reference.item_id, "seeded-record")
    answers = create_native_record(**pad.scope, content=_experience(), actor=OPERATOR, origin="user_reported", operation_key="seeded-qa")  # type: ignore[arg-type]
    # The receipts of the other modules that share records/operations/. The two watchlist ones are written by the
    # product; the application and interview ones are committed under their writers' file names with the keys those
    # writers give them (application_events.py, scout/interview_records.py): the rebuild decides by the file name and
    # reads neither.
    _seed_watchlist(pad)
    JournalWatchlistClient(pad.home, pad.target, pad.gig_id).add_to_watchlist(watchlist_entry_from_url("https://boards.greenhouse.io/acmeadded"))
    _commit(pad, f"records/operations/application-record-{_DIGEST}.json", canonical_json_bytes({
        "schema_version": "1.0", "operation_key": "application:1", "requested_event_sha256": "sha256:" + "1" * 64, "payload_sha256": "sha256:" + "2" * 64, "event": {"event_id": "event_1"},
    }))
    _commit(pad, f"records/operations/interview-{_DIGEST}.json", canonical_json_bytes({
        "schema_version": "1.0", "operation": "scout_interview", "operation_key": "interview:1", "payload_sha256": "sha256:" + "3" * 64, "outcome": "committed",
        "record_id": "interview_1", "revision_id": "revision_1", "created_at": "2026-10-05T00:00:00Z",
    }))
    # Private writes AFTER the seed: the commits that never reached the projection.
    later = pad.reference("the resume, changed\n", "seeded-ref-2")
    second = pad.record(later.item_id, "seeded-record-2", record_id=record.record_id, parent_revision=record.revision_id)
    answered = update_native_record(**pad.scope, record_id=answers.record_id, parent_revision=answers.revision_id, content=_experience(answered=True), actor=OPERATOR, origin="user_reported", operation_key="seeded-qa-2")  # type: ignore[arg-type]
    head, receipts = pad.head(), _receipts(pad)
    seed_receipt = next(path for path in receipts if "/scout_watchlist_seed-" in path)
    assert len(json.loads((pad.workpad / seed_receipt).read_bytes())["artifact_refs"]) == BOARDS  # over the receipt schema's 4
    assert _cursor(pad) != head  # behind: no save rebuilt it

    rebuilt = rebuild_scout_projection(resolved=_resolved(pad))

    assert rebuilt.journal_head == head == _cursor(pad)
    assert dict(rebuilt.skipped_receipts) == {"application-record": 1, "interview": 1, "scout_watchlist_add": 1, "scout_watchlist_seed": 1}
    tables = _tables(pad)
    # Every receipt of an owned kind is in the projection, and no other.
    assert sorted(tables["scout_operations"]) == _owned(receipts) and len(_owned(receipts)) == rebuilt.operations == 6
    assert not any("watchlist" in key or "application" in key or "interview" in key for key in tables["scout_operations"])
    # Each record at its CURRENT revision: the writes made after the seed are there.
    assert {key: json.loads(payload)["revision_id"] for key, payload in tables["scout_records"].items()} == {
        record.record_id: second.revision_id, answers.record_id: answered.revision_id,
    }
    assert rebuilt.records == 2
    assert json.loads(tables["scout_meta"]["cursor"]) == {"schema_version": "1.0", "journal_head": head}
    context = json.loads((pad.workpad / "indexes" / "context.json").read_bytes())
    assert context["journal_head"] == head
    assert {item["record_id"]: item["revision_id"] for item in context["records"]} == {record.record_id: second.revision_id, answers.record_id: answered.revision_id}
    assert b"the resume" not in (pad.workpad / "indexes" / "context.json").read_bytes()


def test_a_receipt_of_an_unknown_kind_is_skipped_and_counted_whatever_it_holds(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=0)
    reference = pad.reference("the resume\n", "unknown-ref")
    # A kind no module of this build writes, three ways: not a receipt, not an object, not JSON; and a file with no kind.
    _commit(pad, f"records/operations/some_future_operation-{_DIGEST}.json", b'{"operation":"some_future_operation","anything":[1,2,3]}')
    _commit(pad, f"records/operations/some_future_operation-{'cd' * 32}.json", b"[]")
    _commit(pad, f"records/operations/another-kind-{_DIGEST}.json", b"this is not JSON")
    _commit(pad, "records/operations/notes.json", b"{}")

    rebuilt = rebuild_scout_projection(resolved=_resolved(pad))

    assert dict(rebuilt.skipped_receipts) == {"another-kind": 1, "notes": 1, "some_future_operation": 2}
    assert rebuilt.journal_head == pad.head() == _cursor(pad)
    operations = _tables(pad)["scout_operations"]
    assert sorted(operations) == _owned(_receipts(pad)) and len(operations) == 1
    assert json.loads(next(iter(operations.values())))["operation_id"] == reference.receipt["operation_id"]  # type: ignore[index]


def test_a_corrupt_receipt_of_an_owned_kind_refuses_the_rebuild(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    pad = _Pad(tmp_path, other_files=0)
    reference = pad.reference("the resume\n", "corrupt-ref")
    pad.record(reference.item_id, "corrupt-record")
    rebuild_scout_projection(resolved=_resolved(pad))
    before, cursor = _tables(pad), _cursor(pad)
    assert cursor == pad.head()

    # A committed file under an owned operation's name that is not a receipt.
    _commit(pad, f"records/operations/record_create-{_DIGEST}.json", b"{}\n")
    with pytest.raises(PrivateRecordError) as refused:
        rebuild_scout_projection(resolved=_resolved(pad))
    assert refused.value.code == "private_operation_conflict"
    with pytest.raises(PrivateRecordError):
        catch_up_scout_projection(resolved=_resolved(pad))
    # Nothing of the projection was rewritten: it is where it was, behind the head.
    assert _tables(pad) == before and _cursor(pad) == cursor != pad.head()
    # The background catch-up says so in one line (the error's type and code, no record text) and raises nothing.
    with caplog.at_level(logging.INFO, logger=private_records.__name__):
        thread = private_records.start_scout_projection_catch_up(home_root=pad.home, requested_target=pad.target, gig_id=pad.gig_id)
        thread.join(timeout=latency_bound(60))
    assert not thread.is_alive() and thread.daemon and thread.name == "scout-projection-catch-up"
    assert [(record.levelname, record.getMessage()) for record in caplog.records if record.name == private_records.__name__] == [
        ("WARNING", "scout projection catch-up did not finish: PrivateRecordError private_operation_conflict")
    ]
    assert _tables(pad) == before


def test_the_catch_up_on_a_home_with_no_workpad_says_so_without_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "target").mkdir()
    with caplog.at_level(logging.INFO, logger=private_records.__name__):
        private_records.start_scout_projection_catch_up(home_root=tmp_path / "home", requested_target=tmp_path / "target").join(timeout=latency_bound(60))
    assert [(record.levelname, record.getMessage()) for record in caplog.records if record.name == private_records.__name__] == [
        ("INFO", "scout projection catch-up did not finish: WorkpadUnavailableError workpad_unavailable")
    ]
    assert not (tmp_path / "home").exists()  # and it created nothing


def test_a_receipt_that_names_another_operation_than_its_file_refuses_the_rebuild(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=0)
    reference = pad.reference("the resume\n", "identity-ref")
    # A VALID receipt (the reference's own), committed again under another owned operation's name.
    _commit(pad, f"records/operations/record_update-{_DIGEST}.json", canonical_json_bytes(reference.receipt))
    with pytest.raises(PrivateRecordError, match="identity differs") as refused:
        rebuild_scout_projection(resolved=_resolved(pad))
    assert refused.value.code == "private_operation_conflict"


def test_a_save_and_its_retry_make_no_rebuild_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pad = _Pad(tmp_path, other_files=0)
    calls, captures = _rebuild_calls(monkeypatch), _captures(monkeypatch)
    snapshots: list[bool] = []
    real_private_snapshot = private_records._private_snapshot
    monkeypatch.setattr(private_records, "_private_snapshot", lambda resolved, *, operations=False: (snapshots.append(operations), real_private_snapshot(resolved, operations=operations))[1])

    # Every private and native write, each made twice (the second time is the retry of a committed request).
    results: dict[str, object] = {}
    writes = {
        "reference": lambda: pad.reference("the resume\n", "quiet-ref"),
        "run input": lambda: import_run_input(**pad.scope, data=b"a posting\n", operation_key="quiet-posting"),  # type: ignore[arg-type]
        "record": lambda: pad.record(results["reference"].item_id, "quiet-record"),  # type: ignore[attr-defined]
        "native create": lambda: create_native_record(**pad.scope, content=_experience(), actor=OPERATOR, origin="user_reported", operation_key="quiet-qa"),  # type: ignore[arg-type]
        "native update": lambda: update_native_record(
            **pad.scope, record_id=results["native create"].record_id, parent_revision=results["native create"].revision_id,  # type: ignore[arg-type, attr-defined]
            content=_experience(answered=True), actor=OPERATOR, origin="user_reported", operation_key="quiet-qa-2",
        ),
        "native archive": lambda: archive_native_record(
            **pad.scope, record_id=results["native create"].record_id, parent_revision=results["native update"].revision_id,  # type: ignore[arg-type, attr-defined]
            actor=OPERATOR, operation_key="quiet-qa-3",
        ),
    }
    for name, write in writes.items():
        results[name] = write()
    assert calls == [], f"{len(calls)} rebuild attempts inside {len(results)} saves"
    assert len([1 for prefixes, _paths in captures if "records/operations/" in prefixes or "records/" in prefixes]) == len(results)  # one capture a save

    for name, result in results.items():
        assert (result.created, result.projection_pending, result.rebuild_action) == (True, True, None), name  # type: ignore[attr-defined]
    head = pad.head()
    retries = {name: write() for name, write in writes.items()}
    for name, retry in retries.items():
        # The retry answers from the receipt, commits nothing, and still says the truth: the projection is behind.
        assert (retry.created, retry.receipt, retry.projection_pending, retry.rebuild_action) == (False, results[name].receipt, True, None), name  # type: ignore[attr-defined]
    assert pad.head() == head

    assert calls == [], f"{len(calls)} rebuild attempts inside {len(results) + len(retries)} saves"
    assert True not in snapshots  # nothing read the projection's selection (the receipts with every record) either
    assert private_records.scout_projection_behind(_resolved(pad), journal_head=head) is True
    # One capture under the writer lock per save, and none for a rebuild after it.
    publishes = [prefixes for prefixes, _paths in captures if "records/operations/" in prefixes or "records/" in prefixes]
    assert len(publishes) == len(results) + len(retries) == 12

    # The catch-up is the one rebuild; then the same retries say the projection is NOT pending; then there is nothing to do.
    rebuilt = catch_up_scout_projection(resolved=_resolved(pad))
    assert rebuilt is not None and rebuilt.journal_head == head == _cursor(pad) and (rebuilt.records, rebuilt.operations) == (2, 6)
    for name, write in writes.items():
        retry = write()
        assert (retry.created, retry.receipt, retry.projection_pending, retry.rebuild_action) == (False, results[name].receipt, False, None), name  # type: ignore[attr-defined]
    assert catch_up_scout_projection(resolved=_resolved(pad)) is None
    assert calls == [1]


def test_a_write_says_the_index_is_pending_and_names_no_action(tmp_path: Path) -> None:
    """STORE1B: every write said ``rebuild_action: rebuild_index``, and no command of that name exists.

    The index is pending (that is true) and needs no action: it follows when Scout next starts.
    """
    pad = _Pad(tmp_path, other_files=0)
    source = tmp_path / "experience.json"
    source.write_text(json.dumps(_experience()), encoding="utf-8")
    scope = ["--home", str(pad.home), "--target", str(pad.target), "--gig", pad.gig_id, "--json"]
    create = ["record", "native", "create", "--content-file", str(source), "--operation-key", "cli-pending", *scope]
    runner = CliRunner()

    created = runner.invoke(cli, create)
    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)
    assert (payload["created"], payload["projection_pending"]) == (True, True)
    assert "rebuild_action" in payload and payload["rebuild_action"] is None  # the key stays; it names nothing
    override = runner.invoke(cli, [
        "record", "native", "override", "--content-file", str(source), "--base-record", payload["record_id"], "--base-revision", payload["revision_id"],
        "--operation-key", "cli-pending-override", *scope,
    ])
    assert override.exit_code == 0, override.output
    assert json.loads(override.output)["projection_pending"] is True and json.loads(override.output).get("rebuild_action") is None

    # Nothing a result names is something to run: after the catch-up (Scout's start) the same request says "not pending".
    assert catch_up_scout_projection(resolved=_resolved(pad)) is not None
    retried = json.loads(runner.invoke(cli, create).output)
    assert (retried["created"], retried["record_id"], retried["projection_pending"], retried["rebuild_action"]) == (False, payload["record_id"], False, None)


def test_a_rebuild_keeps_scouts_own_read_model_row(tmp_path: Path) -> None:
    pad = _Pad(tmp_path, other_files=0)
    reference = pad.reference("the resume\n", "meta-ref")
    pad.record(reference.item_id, "meta-record")
    rebuild_scout_projection(resolved=_resolved(pad))
    # Scout's read model keeps its row in the same table, written the way scout/projection.py::rebuild_projection writes it.
    report = canonical_json_bytes({"schema_version": "1.0", "journal_head": pad.head(), "opportunities": [], "proposals": []})
    with database_lock(pad.workpad):
        connection = sqlite3.connect(pad.workpad / "state.sqlite")
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS scout_meta (key TEXT PRIMARY KEY, payload BLOB NOT NULL)")
            connection.execute("INSERT OR REPLACE INTO scout_meta(key,payload) VALUES (?,?)", ("report_projection", report))
            connection.commit()
        finally:
            connection.close()

    pad.reference("the resume, changed\n", "meta-ref-2")
    rebuilt = rebuild_scout_projection(resolved=_resolved(pad))

    meta = _tables(pad)["scout_meta"]
    assert meta["report_projection"] == report  # byte for byte: not deleted, not rewritten
    assert sorted(meta) == ["context", "cursor", "report_projection"]
    assert json.loads(meta["cursor"])["journal_head"] == rebuilt.journal_head == pad.head()
    assert json.loads(meta["context"])["journal_head"] == pad.head()
    # And again: the two rows of this projection are replaced, not duplicated; Scout's row is still there.
    rebuild_scout_projection(resolved=_resolved(pad))
    assert _tables(pad)["scout_meta"]["report_projection"] == report and len(_tables(pad)["scout_meta"]) == 3


def test_a_home_that_is_behind_catches_up_in_the_background_and_no_save_waits_for_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    pad = _Pad(tmp_path, other_files=0)
    reference = pad.reference("the resume\n", "behind-ref")
    record = pad.record(reference.item_id, "behind-record")
    _seed_watchlist(pad)
    later = pad.reference("the resume, changed\n", "behind-ref-2")
    behind_head = pad.head()
    assert _cursor(pad) != behind_head
    calls = _rebuild_calls(monkeypatch)

    # Hold the catch-up INSIDE the state database's lock (the only lock it takes), and save while it is held.
    entered, release = threading.Event(), threading.Event()
    real_validate = private_records.validate_state_database

    def held(path: Path) -> None:
        entered.set()
        assert release.wait(latency_bound(120))
        real_validate(path)

    monkeypatch.setattr(private_records, "validate_state_database", held)
    with caplog.at_level(logging.INFO, logger=private_records.__name__):
        thread = private_records.start_scout_projection_catch_up(home_root=pad.home, requested_target=pad.target, gig_id=pad.gig_id)
        try:
            assert entered.wait(latency_bound(60)), "the catch-up never reached the state database"
            started = time.monotonic()
            second = pad.record(later.item_id, "behind-record-2", record_id=record.record_id, parent_revision=record.revision_id)
            answers = create_native_record(**pad.scope, content=_experience(), actor=OPERATOR, origin="user_reported", operation_key="behind-qa")  # type: ignore[arg-type]
            saved_in = time.monotonic() - started
            assert second.created and answers.created and thread.is_alive()  # two saves went through while the catch-up held its lock
            assert _cursor(pad) != behind_head  # and it has written nothing yet
        finally:
            release.set()
            thread.join(timeout=latency_bound(60))
    assert not thread.is_alive() and saved_in < latency_bound(60)
    # It rebuilt to the head it read when it started: the commits behind it are in, the two saves made meanwhile are not.
    assert _cursor(pad) == behind_head and calls == [1]
    assert [record.getMessage() for record in caplog.records if record.name == private_records.__name__] == [
        f"scout projection caught up: head={behind_head[:12]} records=1 operations=3 receipts_of_other_kinds_skipped=1"
    ]
    # The next catch-up brings in the two saves; the one after it finds nothing to do.
    monkeypatch.setattr(private_records, "validate_state_database", real_validate)
    private_records.start_scout_projection_catch_up(home_root=pad.home, requested_target=pad.target, gig_id=pad.gig_id).join(timeout=latency_bound(60))
    assert _cursor(pad) == pad.head() and calls == [1, 1]
    private_records.start_scout_projection_catch_up(home_root=pad.home, requested_target=pad.target, gig_id=pad.gig_id).join(timeout=latency_bound(60))
    assert calls == [1, 1]
    assert {key: json.loads(payload)["revision_id"] for key, payload in _tables(pad)["scout_records"].items()} == {
        record.record_id: second.revision_id, answers.record_id: answers.revision_id,
    }


def test_the_server_starts_one_catch_up_and_a_seeded_home_reaches_the_journal_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.experience_answers import record_answer
    from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
    from tests.behaviors.scout_find_jobs.test_m1_end_to_end import _fixture

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    home, target, workpad = _fixture(tmp_path)
    pad = SimpleNamespace(home=home, target=target, gig_id=None, workpad=Path(workpad))
    _seed_watchlist(pad)  # type: ignore[arg-type]
    record_answer(home_root=home, requested_target=target, question_id="years:python", prompt="Years of Python?", answer="Six.")
    head = subprocess.run(["git", "-C", str(workpad), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert private_records._projection_cursor(Path(workpad)) != head  # a seeded home after a save: behind

    started: list[dict[str, object]] = []
    real_start = private_records.start_scout_projection_catch_up
    monkeypatch.setattr(private_records, "start_scout_projection_catch_up", lambda **kwargs: (started.append(kwargs), real_start(**kwargs))[1])
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    plain = serve(backend=backend, bind=("127.0.0.1", 0))  # a server built by a test or a one-shot caller: no background work
    plain.server_close()
    assert started == []

    server = serve(backend=backend, bind=("127.0.0.1", 0), background_refresh=True)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        assert [(call["home_root"], call["requested_target"]) for call in started] == [(backend.home_root, backend.target)]
        deadline = time.monotonic() + latency_bound(60)
        while private_records._projection_cursor(Path(workpad)) != head:
            assert time.monotonic() < deadline, "the server's catch-up did not bring the projection to the journal head"
            time.sleep(0.05)
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
    assert len(started) == 1


# --- found beyond 0110-10-16: the equivalent-import lookup read every receipt of the folder too -----------------


def test_an_equivalent_import_is_answered_on_a_home_with_receipts_of_other_modules(tmp_path: Path) -> None:
    """``_receipt_for_artifact`` (the same bytes imported under another key) validated every file of
    ``records/operations/`` in name order as a private receipt. ``application-record-...`` and ``interview-...``
    sort before every owned kind, so one "Mark applied" refused every equivalent import of that home."""

    pad = _Pad(tmp_path, other_files=0)
    reference = pad.reference("the resume\n", "equal-ref")
    posting = import_run_input(**pad.scope, data=b"a posting\n", operation_key="equal-posting")  # type: ignore[arg-type]
    _seed_watchlist(pad, boards=5)
    _commit(pad, f"records/operations/application-record-{_DIGEST}.json", canonical_json_bytes({
        "schema_version": "1.0", "operation_key": "application:1", "requested_event_sha256": "sha256:" + "1" * 64, "payload_sha256": "sha256:" + "2" * 64, "event": {"event_id": "event_1"},
    }))
    _commit(pad, f"records/operations/interview-{_DIGEST}.json", canonical_json_bytes({"schema_version": "1.0", "operation": "scout_interview", "operation_key": "interview:1"}))
    head = pad.head()

    # The same bytes under another key: the first import and its receipt, nothing committed.
    again = pad.reference("the resume\n", "equal-ref-other-key")
    assert (again.created, again.item_id, again.receipt) == (False, reference.item_id, reference.receipt)
    same_posting = import_run_input(**pad.scope, data=b"a posting\n", operation_key="equal-posting-other-key")  # type: ignore[arg-type]
    assert (same_posting.created, same_posting.item_id, same_posting.receipt) == (False, posting.item_id, posting.receipt)
    assert pad.head() == head and len(list((pad.workpad / "references").iterdir())) == 1

    # A receipt of an OWNED kind that is not a receipt still refuses the lookup, whichever owned operation it names.
    _commit(pad, f"records/operations/record_create-{_DIGEST}.json", b"{}\n")
    head = pad.head()
    with pytest.raises(PrivateRecordError) as refused:
        pad.reference("the resume\n", "equal-ref-third-key")
    assert refused.value.code == "private_operation_conflict" and pad.head() == head
