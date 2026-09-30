"""journal-read-scope: Scout's read routes take no journal writer lock and

read only the record families they need.

UAT (uat-ui-batch2) at catalog size -- a watchlist of 10,370 boards: the
reads a page load sends in parallel took 9-18 s each, and ``GET
/api/profiles`` / ``GET /api/runs`` answered 500 with ``InterprocessLock
Unavailable: writer lock timeout owner=pid=<the server itself>``. Two
causes: every read went through the one exclusive writer lock, so the
server's own threads queued behind each other past the 10 s timeout; and
the answers / applications / resume reads snapshotted all of ``records/``,
so each of them paid for every watchlist file.

These tests pin both halves with counts, never with wall time: a spy on the
writer lock (reads acquire it zero times, alone and in parallel), and a spy
on the snapshot's blob read (a read fetches the same number of blobs whether
the watchlist holds 5 boards or 300).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading
from types import SimpleNamespace

import httpx
import pytest

import gigai.journal as journal
from gigai.native_records import list_native_records, read_native_record
from gigai.private_records import list_imports
from gigai.scout import profile_records
from gigai.scout.experience_answers import read_answers, record_answer
from gigai.scout.find_jobs.api import watchlist as watchlist_api
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.find_jobs.watchlist import list_active, seed_watchlist_from_catalog
from gigai.scout.projection import projection_from_snapshot, read_projection_snapshot, rebuild_projection
from gigai.scout.report_readers import default_reader_set
from gigai.workpad import resolve_workpad

from .test_m1_end_to_end import _fixture
from .test_watchlist_seed import _catalog, _record

_PAGE_LOAD = ("/api/config", "/api/profiles", "/api/runs", "/api/answers", "/api/applications", "/api/watchlist")
_NO_FILTER = SimpleNamespace(countries=(), exclude_companies=(), watch_companies=())


def _seed_boards(home: Path, target: Path, count: int, *, revision: str = "test-rev", already: int = 0) -> None:
    """Seed boards ``0..count-1``; ``already`` of them are on the watchlist from an earlier seed."""

    catalog = _catalog(
        *(_record(f"Company {index}", ATSProvider.GREENHOUSE, f"board{index:05d}") for index in range(count)),
        revision=revision,
    )
    result = seed_watchlist_from_catalog(home, target, prefs=_NO_FILTER, catalog=catalog)
    assert (result.added, result.already_present) == (count - already, already)


def _gig(tmp_path: Path, *, boards: int) -> SimpleNamespace:
    """A gig with a resume, a migrated default profile, one answer and a seeded watchlist."""

    home, target, _workpad = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    record_answer(home_root=home, requested_target=target, question_id="years:python", prompt="Years of Python?", answer="Six.")
    selected = profile_records.selected_profile(resolved, home_root=home, target=target)
    assert selected is not None
    _seed_boards(home, target, boards)
    return SimpleNamespace(home=home, target=target, resolved=resolved, profile=selected)


def _library_reads(gig: SimpleNamespace) -> dict[str, object]:
    """Every read the routes are built from, by name."""

    home, target, resolved = gig.home, gig.target, gig.resolved
    native = list_native_records(home_root=home, requested_target=target)
    return {
        "selected_profile": profile_records.selected_profile(resolved, home_root=home, target=target),
        "ensure_default_profile": profile_records.ensure_default_profile(resolved, home_root=home, target=target),
        "list_profiles": profile_records.list_profiles(resolved),
        "retrieve_profile_revision": profile_records.retrieve_profile_revision(
            resolved, profile_id=gig.profile.profile_id, revision=gig.profile.revision,
            content_digest=gig.profile.content_digest,
        ),
        "read_answers": read_answers(home_root=home, requested_target=target),
        "list_native_records": native,
        "read_native_record": read_native_record(
            home_root=home, requested_target=target, record_id=str(native[0]["record_id"]), content=True,
        ),
        "list_imports": list_imports(home_root=home, requested_target=target, family="reference"),
        "newest_resume": profile_records._resolve_newest_resume_for_gig(resolved, home_root=home, target=target),
        "list_active": list_active(home, target),
        "projection_snapshot": read_projection_snapshot(resolved),
        "rebuild_projection": rebuild_projection(resolved=resolved),
    }


@pytest.fixture
def lock_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every journal writer-lock acquisition in this process, in order."""

    taken: list[str] = []
    real = journal._writer_lock

    @contextmanager
    def spying(path: Path, timeout_seconds: float):
        taken.append(threading.current_thread().name)
        with real(path, timeout_seconds):
            yield

    monkeypatch.setattr(journal, "_writer_lock", spying)
    return taken


@pytest.fixture
def blob_spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """How many blobs each committed snapshot fetched."""

    counts: list[int] = []
    real = journal._batch_read_blobs

    def counting(root: Path, refs: list[str]):
        counts.append(len(refs))
        return real(root, refs)

    monkeypatch.setattr(journal, "_batch_read_blobs", counting)
    return counts


# --------------------------------------------------------------------------
# Reads take no writer lock
# --------------------------------------------------------------------------


def test_no_read_takes_the_writer_lock(tmp_path: Path, lock_spy: list[str]) -> None:
    gig = _gig(tmp_path, boards=12)
    lock_spy.clear()

    values = _library_reads(gig)

    assert lock_spy == []
    assert values["selected_profile"] == gig.profile
    assert values["ensure_default_profile"].created is False  # type: ignore[union-attr]
    assert [item.profile_id for item in values["list_profiles"]] == [gig.profile.profile_id]  # type: ignore[union-attr]
    assert values["retrieve_profile_revision"] == gig.profile
    assert set(values["read_answers"]) == {"years:python"}  # type: ignore[arg-type]
    assert len(values["list_active"]) == 12  # type: ignore[arg-type]
    assert [item["kind"] for item in values["list_imports"]] == ["resume"]  # type: ignore[union-attr]


def test_default_profile_takes_the_writer_lock_only_when_it_is_missing(tmp_path: Path, lock_spy: list[str]) -> None:
    home, target, _workpad = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    lock_spy.clear()

    created = profile_records.ensure_default_profile(resolved, home_root=home, target=target)
    assert created is not None and created.created is True
    # The one acquisition is the create itself; the existence check and the
    # resume lookup before it are reads.
    assert len(lock_spy) == 1

    lock_spy.clear()
    again = profile_records.ensure_default_profile(resolved, home_root=home, target=target)
    selected = profile_records.selected_profile(resolved, home_root=home, target=target)
    assert again is not None and again.created is False and again.profile_id == created.profile_id
    assert selected is not None and selected.profile_id == created.profile_id
    assert lock_spy == []


def test_a_missing_default_profile_is_checked_again_under_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two callers both see "missing" on the lock-free read; only one may create."""

    home, target, _workpad = _fixture(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)
    real = profile_records._read_snapshot
    raced: list[object] = []

    def missing_then_raced(*args: object, **kwargs: object):
        snapshot = real(*args, **kwargs)  # type: ignore[arg-type]
        if not raced:
            # Between this caller's read and its write, another caller migrates.
            monkeypatch.setattr(profile_records, "_read_snapshot", real)
            raced.append(profile_records.ensure_default_profile(resolved, home_root=home, target=target))
        return snapshot

    monkeypatch.setattr(profile_records, "_read_snapshot", missing_then_raced)
    late = profile_records.ensure_default_profile(resolved, home_root=home, target=target)

    winner = raced[0]
    assert winner.created is True  # type: ignore[attr-defined]
    assert late is not None and late.created is False and late.profile_id == winner.profile_id  # type: ignore[attr-defined]
    assert [item.profile_id for item in profile_records.list_profiles(resolved)] == [late.profile_id]


def test_parallel_reads_take_no_writer_lock_and_all_succeed(tmp_path: Path, lock_spy: list[str]) -> None:
    gig = _gig(tmp_path, boards=12)
    expected = _library_reads(gig)
    lock_spy.clear()
    errors: list[BaseException] = []
    results: list[dict[str, object]] = []
    collect = threading.Lock()

    def reader() -> None:
        try:
            values = _library_reads(gig)
            with collect:
                results.append(values)
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=reader, name=f"reader-{index}") for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120.0)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert lock_spy == []
    assert len(results) == 8
    for values in results:
        for name in ("selected_profile", "list_profiles", "read_answers", "list_active", "list_imports", "newest_resume"):
            assert values[name] == expected[name], name


# --------------------------------------------------------------------------
# Reads do not grow with the watchlist
# --------------------------------------------------------------------------


def test_reads_fetch_the_same_blobs_whatever_the_watchlist_holds(tmp_path: Path, blob_spy: list[int]) -> None:
    gig = _gig(tmp_path, boards=5)
    home, target, resolved = gig.home, gig.target, gig.resolved
    reads = {
        "selected_profile": lambda: profile_records.selected_profile(resolved, home_root=home, target=target),
        "list_profiles": lambda: profile_records.list_profiles(resolved),
        "read_answers": lambda: read_answers(home_root=home, requested_target=target),
        "list_native_records": lambda: list_native_records(home_root=home, requested_target=target),
        "list_imports": lambda: list_imports(home_root=home, requested_target=target, family="reference"),
        "newest_resume": lambda: profile_records._resolve_newest_resume_for_gig(resolved, home_root=home, target=target),
        "projection_snapshot": lambda: read_projection_snapshot(resolved),
    }

    def measure() -> dict[str, list[int]]:
        measured: dict[str, list[int]] = {}
        for name, read in reads.items():
            blob_spy.clear()
            read()
            measured[name] = list(blob_spy)
        return measured

    small = measure()
    _seed_boards(home, target, 300, revision="test-rev-2", already=5)
    assert len(list_active(home, target)) == 300
    large = measure()

    assert large == small
    assert all(counts and all(counts) for counts in small.values()), small  # every read did read something
    # The watchlist read itself is the one read that is the watchlist.
    blob_spy.clear()
    list_active(home, target)
    assert sum(blob_spy) >= 300


# --------------------------------------------------------------------------
# The routes, in parallel
# --------------------------------------------------------------------------


@pytest.fixture
def running_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    gig = _gig(tmp_path, boards=25)
    monkeypatch.setenv("EXA_API_KEY", "journal-read-scope-test-key")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")
    with watchlist_api._ACTIVE_ENTRIES_CACHE_LOCK:
        watchlist_api._active_entries_cache.clear()
    backend = ScoutFindJobsBackend(home_root=gig.home, target=gig.target)
    server = serve(backend=backend, bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        yield SimpleNamespace(base_url=f"http://{host}:{port}", gig=gig)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_parallel_page_load_answers_200_without_the_writer_lock(running_server, lock_spy: list[str]) -> None:
    base_url = running_server.base_url
    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        for path in _PAGE_LOAD:  # warm: nothing left to migrate
            assert client.get(path).status_code == 200, path
    lock_spy.clear()
    statuses: dict[str, object] = {}
    collect = threading.Lock()

    def fetch(name: str, path: str) -> None:
        try:
            with httpx.Client(base_url=base_url, timeout=60.0) as client:
                status: object = client.get(path).status_code
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            status = repr(exc)
        with collect:
            statuses[name] = status

    # Two page loads at once: the 12 parallel reads that reproduced the 500.
    threads = [
        threading.Thread(target=fetch, args=(f"{path}#{load}", path))
        for load in range(2)
        for path in _PAGE_LOAD
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120.0)

    assert not any(thread.is_alive() for thread in threads)
    assert statuses == {f"{path}#{load}": 200 for load in range(2) for path in _PAGE_LOAD}
    assert lock_spy == []


def test_the_scoped_projection_equals_the_one_built_from_all_of_records(running_server) -> None:
    """The projection used to read ``records/`` whole. Reading only the
    families it names must build the same projection, and the only files it
    leaves out are the two families no projection reader reads."""

    gig = running_server.gig
    with httpx.Client(base_url=running_server.base_url, timeout=60.0) as client:
        posted = client.post(
            "/api/applications",
            json={"normalized_url": "https://boards.greenhouse.io/nowhere/jobs/999999", "event_kind": "saved"},
        )
        assert posted.status_code == 201, posted.text
        listed = client.get("/api/applications").json()["applications"]
    assert [item["external_ref"] for item in listed] == ["https://boards.greenhouse.io/nowhere/jobs/999999"]

    resolved = gig.resolved
    wide = journal.run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(("records/", "runs/", "run-plans/", "references/", "run-inputs/", "manifests/")),
    )
    scoped = read_projection_snapshot(resolved)

    assert scoped.head == wide.head
    left_out = set(wide.artifacts) - set(scoped.artifacts)
    assert set(scoped.artifacts) <= set(wide.artifacts)
    assert len([path for path in left_out if path.startswith("records/scout-watchlist/")]) == 25
    assert all(path.startswith(("records/scout-watchlist/", "records/operations/")) for path in left_out), sorted(left_out)
    assert all(scoped.artifacts[path] == wide.artifacts[path] for path in scoped.artifacts)

    def build(snapshot):
        return projection_from_snapshot(
            snapshot=snapshot, project_id=resolved.project_id, gig_id=resolved.gig_id,
            readers=default_reader_set(resolved),
        )

    projection = build(scoped)
    assert projection == build(wide)
    assert len(projection.applications) == 1
    assert projection.selected_profile_id == gig.profile.profile_id
    assert len(projection.questions) == 1


# --------------------------------------------------------------------------
# GET /api/watchlist: the full list, a page, a summary
# --------------------------------------------------------------------------


def test_watchlist_summary_and_pages_match_the_full_list(running_server) -> None:
    with httpx.Client(base_url=running_server.base_url, timeout=60.0) as client:
        full = client.get("/api/watchlist")
        assert full.status_code == 200
        assert set(full.json()) == {"schema_version", "entries"}  # the no-query response is unchanged
        entries = full.json()["entries"]
        assert len(entries) == 25

        summary = client.get("/api/watchlist?summary=1")
        assert summary.status_code == 200
        assert summary.json() == {"schema_version": "scout-watchlist-summary:1", "total": 25}

        page = client.get("/api/watchlist?limit=10&offset=20")
        assert page.status_code == 200
        body = page.json()
        assert body["schema_version"] == full.json()["schema_version"]
        assert (body["total"], body["limit"], body["offset"]) == (25, 10, 20)
        assert body["entries"] == entries[20:25]

        first = client.get("/api/watchlist?limit=3").json()
        assert first["offset"] == 0 and first["entries"] == entries[:3]
        assert client.get("/api/watchlist?limit=5&offset=400").json()["entries"] == []


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=501", "limit=-1", "limit=ten", "limit=", "offset=3", "limit=5&offset=-1",
     "summary=yes", "summary=1&limit=5", "page=2"],
)
def test_watchlist_refuses_an_invalid_query(running_server, query: str) -> None:
    with httpx.Client(base_url=running_server.base_url, timeout=60.0) as client:
        response = client.get(f"/api/watchlist?{query}")
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] in {"invalid_value", "unknown_key"}


def test_watchlist_is_read_once_per_journal_head(running_server, monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[int] = []
    real = watchlist_api.list_active

    def counting(*args: object, **kwargs: object):
        entries = real(*args, **kwargs)  # type: ignore[arg-type]
        reads.append(len(entries))
        return entries

    monkeypatch.setattr(watchlist_api, "list_active", counting)
    with httpx.Client(base_url=running_server.base_url, timeout=60.0) as client:
        assert client.get("/api/watchlist?summary=1").json()["total"] == 25
        assert client.get("/api/watchlist?limit=5").json()["total"] == 25
        assert len(client.get("/api/watchlist").json()["entries"]) == 25
        assert reads == [25]

        # A commit moves the head: the next request reads the journal again.
        gig = running_server.gig
        _seed_boards(gig.home, gig.target, 27, revision="test-rev-2", already=25)
        assert client.get("/api/watchlist?summary=1").json()["total"] == 27
        assert client.get("/api/watchlist?summary=1").json()["total"] == 27
        assert reads == [25, 27]
