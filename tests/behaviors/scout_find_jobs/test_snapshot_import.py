"""0110-026e: snapshot import (S3).

A fake release server only (``httpx.MockTransport`` serving an export's
output directory; one test uses a loopback ``http.server``). Synthetic cache
directories; no network, no real data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading

from click.testing import CliRunner
import httpx
import pytest

from gigai.scout.find_jobs import snapshot
from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
from gigai.scout.find_jobs.company_index import (
    STATUS_UNTOUCHED,
    CompanyIndex,
    CompanyIndexEntry,
    IndexedPosting,
    board_list_url,
    index_stamp,
    refresh_company,
)
from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles
from gigai.scout.find_jobs.posting_tags import TAGGER_VERSION, normalize_title, tag_new_titles, tag_title
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.find_jobs.snapshot import (
    FORMAT_NAME,
    FORMAT_VERSION,
    MANIFEST_URL_ENV,
    SNAPSHOT_ENV,
    export_snapshot,
    file_name,
    import_snapshot,
    maybe_import_snapshot,
    snapshot_dir,
    snapshot_setting,
    snapshot_status,
)
from gigai.scout.find_jobs.sources_update import board_cache_for_home
from gigai.scout.find_jobs.tag_store import TagStore
from gigai.scout.scout_cli import scout_group

from .test_refresh_core import _project

URL = "https://releases.example/download/scout-snapshot/manifest.json"
T0 = "2026-09-01T10:00:00.000Z"  # long before the snapshot
T1 = "2026-09-30T10:00:00.000Z"  # the builder's check, day 1
T2 = "2026-10-01T10:00:00.000Z"  # the builder's check, day 2
LATER = "2026-10-05T10:00:00.000Z"  # after both snapshots
DAY1 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
DAY2 = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
#: A title no rule gives a function: the tag waits for a model.
ODD_TITLE = "Zorblax Wrangler"


def _posting(pid: str, title: str, *, seen: str = T1, url: str | None = None) -> IndexedPosting:
    return IndexedPosting(
        posting_id=pid, title=title, location="Denver, CO", url=url or f"https://boards.example/jobs/{pid}",
        updated_at="2026-09-20T00:00:00Z", content_sha256="ab" * 32, first_seen=seen, last_seen=seen,
        published_at="2026-09-19T00:00:00Z", countries=("US",),
    )


def _board(ats: str, slug: str, ids: dict[str, str], *, etag: str | None = None, checked_at: str = T1) -> CompanyIndexEntry:
    return CompanyIndexEntry(
        company=slug.title(), ats=ats, slug=slug, checked_at=checked_at, etag=etag or f"etag-{slug}", body_sha256="cd" * 32,
        postings={pid: _posting(pid, title, seen=checked_at) for pid, title in ids.items()}, last_modified="Mon, 01 Sep 2026 00:00:00 GMT",
    )


class _Release:
    """The fake release server: serves the files of one directory and records every request."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.requests: list[str] = []
        self.urls: list[str] = []
        self.offline = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        self.requests.append(name)
        self.urls.append(str(request.url))
        if self.offline:
            raise httpx.ConnectError("no route to host", request=request)
        path = self.directory / name
        if not path.is_file():
            return httpx.Response(404)
        return httpx.Response(200, content=path.read_bytes())

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def run(self, home: Path, **kwargs) -> snapshot.ImportResult:
        kwargs.setdefault("environ", {})
        kwargs.setdefault("now", NOW)
        with self.client() as client:
            return import_snapshot(home, kwargs.pop("source", URL), client=client, **kwargs)


@pytest.fixture
def builder(tmp_path: Path) -> Path:
    """The operator's machine: three boards, their title tags, one function a model supplied."""

    root = tmp_path / "builder"
    index = CompanyIndex.for_home(root)
    index.write(_board("greenhouse", "acme", {"1": "Senior Software Engineer", "2": "Product Designer"}))
    index.write(_board("lever", "globex", {"a": "Staff Data Scientist"}))
    index.write(_board("ashby", "initech", {"x": "Engineering Manager", "y": ODD_TITLE}))
    store = TagStore.for_home(root, tagger_version=TAGGER_VERSION)
    tag_new_titles(store, ["Senior Software Engineer", "Product Designer", "Staff Data Scientist", "Engineering Manager", ODD_TITLE])
    assert tag_title(ODD_TITLE).function is None
    assert store.set_model_function(normalize_title(ODD_TITLE), "operations", model="builder-model", prompt_version="tag-v1")
    store.close()
    return root


@pytest.fixture
def day1(builder: Path, tmp_path: Path) -> _Release:
    out = tmp_path / "release-day1"
    export_snapshot(builder, out, as_of=DAY1)
    return _Release(out)


def _day2(builder: Path, day1: _Release, tmp_path: Path) -> _Release:
    """The next day's export: globex is gone, acme lost posting 2 and its ETag moved, initech has a new posting."""

    index = CompanyIndex.for_home(builder)
    assert index.delete("lever", "globex")
    index.write(_board("greenhouse", "acme", {"1": "Senior Software Engineer"}, etag="etag-acme-2", checked_at=T2))
    index.write(_board("ashby", "initech", {"x": "Engineering Manager", "y": ODD_TITLE, "z": "Site Reliability Engineer"}, checked_at=T2))
    store = TagStore.for_home(builder, tagger_version=TAGGER_VERSION)
    tag_new_titles(store, ["Site Reliability Engineer"])
    store.close()
    out = tmp_path / "release-day2"
    export_snapshot(builder, out, base_manifest=day1.directory / "manifest.json", as_of=DAY2)
    return _Release(out)


def _index_files(home: Path) -> dict[str, bytes]:
    root = CompanyIndex.for_home(home).root
    return {path.name: path.read_bytes() for path in sorted(root.iterdir())} if root.is_dir() else {}


def _tag_rows(home: Path) -> list[tuple]:
    path = home / "cache" / "scout" / "tags.sqlite"
    if not path.is_file():
        return []
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT title_key, level, function, function_source FROM title_tags ORDER BY title_key").fetchall()
    finally:
        conn.close()


def _local_data(home: Path) -> tuple[dict[str, bytes], list[tuple]]:
    return _index_files(home), _tag_rows(home)


def _live(home: Path, ats: str, slug: str) -> set[str]:
    entry = CompanyIndex.for_home(home).read(ats, slug)
    assert entry is not None
    return {posting.posting_id for posting in entry.live()}


# --- first run ---------------------------------------------------------------------


def test_first_run_takes_the_full_files_and_fills_index_tags_and_validators(builder: Path, day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"

    result = day1.run(home)

    assert (result.status, result.reason, result.kind, result.as_of) == ("imported", None, "full", "2026-10-01T06:00:00Z")
    assert result.counts == {"boards": 3, "postings": 5, "tags": 5, "boards_kept_local": 0, "boards_removed": 0, "postings_removed": 0}
    # the manifest, then exactly the three full files
    assert day1.requests[0] == "manifest.json"
    assert sorted(day1.requests[1:]) == sorted(file_name(role, "2026-10-01") for role in ("index", "tags", "boards"))

    theirs, mine = CompanyIndex.for_home(builder), CompanyIndex.for_home(home)
    assert list(mine.keys()) == list(theirs.keys())
    for ats, slug in theirs.keys():
        built, got = theirs.read(ats, slug), mine.read(ats, slug)
        assert (got.etag, got.last_modified, got.body_sha256, got.checked_at, got.company) == (
            built.etag, built.last_modified, built.body_sha256, built.checked_at, built.company,
        )
        assert got.postings == built.postings

    status = snapshot_status(home, environ={})
    assert (status["enabled"], status["setting_source"], status["as_of"], status["source"], status["kind"]) == (
        True, "default", "2026-10-01T06:00:00Z", URL, "full",
    )
    assert (status["last_attempt_at"], status["last_result"], status["last_reason"]) == (index_stamp(NOW), "imported", None)
    assert status["counts"] == dict(result.counts)
    # nothing is left behind but the state and the ledger
    assert sorted(path.name for path in snapshot_dir(home).iterdir()) == ["boards.json", "state.json"]


def test_tags_land_in_the_tag_store_with_the_model_function(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    day1.run(home)

    store = TagStore.for_home(home, tagger_version=TAGGER_VERSION)
    try:
        keys = [normalize_title(title) for title in ("Senior Software Engineer", "Product Designer", "Staff Data Scientist", "Engineering Manager", ODD_TITLE)]
        tags = store.get_many(keys)
        assert set(tags) == set(keys)
        assert tags[normalize_title("Senior Software Engineer")].level == "senior"
        odd = tags[normalize_title(ODD_TITLE)]
        assert (odd.function, odd.function_source) == ("operations", "model")
        # a title a model already answered is not asked again on this machine
        assert normalize_title(ODD_TITLE) not in store.titles_awaiting_model()
    finally:
        store.close()


def test_a_stored_tag_stays_and_one_waiting_for_a_model_takes_the_snapshots_answer(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    store = TagStore.for_home(home, tagger_version=TAGGER_VERSION)
    tag_new_titles(store, [ODD_TITLE, "Engineering Manager"])
    manager = normalize_title("Engineering Manager")
    assert store.set_model_function(manager, "research", model="local-model", prompt_version="tag-v1")
    assert store.titles_awaiting_model() == [normalize_title(ODD_TITLE)]
    store.close()

    result = day1.run(home)

    assert result.counts["tags"] == 4  # three new titles, one function filled in
    tags = TagStore.for_home(home, tagger_version=TAGGER_VERSION)
    try:
        assert tags.get(normalize_title(ODD_TITLE)).function == "operations"
        assert (tags.get(manager).function, tags.get(manager).model) == ("research", "local-model")  # local wins
    finally:
        tags.close()


def test_the_same_manifest_again_is_up_to_date_after_one_request(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    day1.run(home)
    before = _local_data(home)
    day1.requests.clear()

    result = day1.run(home)

    assert (result.status, day1.requests) == ("up_to_date", ["manifest.json"])
    assert _local_data(home) == before
    assert snapshot_status(home, environ={})["as_of"] == "2026-10-01T06:00:00Z"


def test_a_real_http_server_and_the_default_client(day1: _Release, tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(day1.directory)))
    server.RequestHandlerClass.log_message = lambda *args, **kwargs: None  # type: ignore[method-assign]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        home = tmp_path / "home"
        result = import_snapshot(home, f"http://127.0.0.1:{server.server_address[1]}/manifest.json", environ={}, now=NOW)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert (result.status, result.kind, result.counts["postings"]) == ("imported", "full", 5)
    assert _live(home, "greenhouse", "acme") == {"1", "2"}


# --- delta and the removal list ----------------------------------------------------


def test_delta_import_downloads_only_the_delta_and_honours_the_removal_list(builder: Path, day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    day1.run(home)
    day2 = _day2(builder, day1, tmp_path)

    result = day2.run(home, now=NOW + timedelta(days=1))

    assert (result.status, result.kind, result.as_of) == ("imported", "delta", "2026-10-02T06:00:00Z")
    assert day2.requests == ["manifest.json", file_name("delta", "2026-10-02")]
    index = CompanyIndex.for_home(home)
    # the removal list: a board the snapshot added, and never checked here, is deleted
    assert index.read("lever", "globex") is None and not index.path("lever", "globex").exists()
    # a removed posting is no longer live; the board's validator moved with the snapshot
    acme = index.read("greenhouse", "acme")
    assert _live(home, "greenhouse", "acme") == {"1"} and acme.postings["2"].removed_at == T2
    assert (acme.etag, acme.checked_at) == ("etag-acme-2", T2)
    assert _live(home, "ashby", "initech") == {"x", "y", "z"}
    assert result.counts["boards_removed"] == 1 and result.counts["postings_removed"] == 2  # globex's posting and acme's
    tags = TagStore.for_home(home, tagger_version=TAGGER_VERSION)
    try:
        assert tags.get(normalize_title("Site Reliability Engineer")) is not None
    finally:
        tags.close()
    # what the delta left equals what a first run on day 2 gets
    fresh = tmp_path / "fresh"
    assert _day_two_live(day2, fresh) == {key: _live(home, *key) for key in CompanyIndex.for_home(home).keys()}


def _day_two_live(day2: _Release, fresh: Path) -> dict[tuple[str, str], set[str]]:
    assert day2.run(fresh).kind == "full"
    return {key: _live(fresh, *key) for key in CompanyIndex.for_home(fresh).keys()}


def test_the_removal_list_spares_a_board_this_machine_checked_itself(builder: Path, day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    day1.run(home)
    index = CompanyIndex.for_home(home)
    index.write(_board("lever", "globex", {"a": "Staff Data Scientist", "local": "Data Engineer"}, checked_at=LATER))  # a local refresh
    kept = index.path("lever", "globex").read_bytes()
    day2 = _day2(builder, day1, tmp_path)

    result = day2.run(home)

    assert result.kind == "delta" and result.counts["boards_removed"] == 0
    assert index.path("lever", "globex").read_bytes() == kept


def test_a_delta_whose_base_board_is_gone_locally_falls_back_to_the_full_files(builder: Path, day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    day1.run(home)
    CompanyIndex.for_home(home).delete("ashby", "initech")  # the cache lost a file the delta only patches
    day2 = _day2(builder, day1, tmp_path)

    result = day2.run(home)

    assert (result.status, result.kind) == ("imported", "full")
    assert _live(home, "ashby", "initech") == {"x", "y", "z"}


def test_a_stale_local_snapshot_takes_the_full_files_not_the_delta(builder: Path, day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    day2 = _day2(builder, day1, tmp_path)  # this machine never had day 1

    result = day2.run(home)

    assert result.kind == "full" and file_name("delta", "2026-10-02") not in day2.requests
    assert not CompanyIndex.for_home(home).path("lever", "globex").exists()


# --- local data wins ---------------------------------------------------------------


def test_a_fresher_local_board_survives_and_an_older_one_keeps_its_own_postings(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    index = CompanyIndex.for_home(home)
    # checked here AFTER the snapshot's builder checked it: the whole board is the machine's own
    index.write(_board("greenhouse", "acme", {"1": "Senior Software Engineer (edited)", "9": "Local Only"}, etag="local-etag", checked_at=LATER))
    # checked here long BEFORE: the snapshot's postings are added, the machine's own stay
    index.write(_board("ashby", "initech", {"x": "Engineering Manager", "q": "Local Only Too"}, etag="old-etag", checked_at=T0))
    fresher = index.path("greenhouse", "acme").read_bytes()

    result = day1.run(home)

    assert result.status == "imported" and result.counts["boards_kept_local"] == 1
    assert index.path("greenhouse", "acme").read_bytes() == fresher
    initech = index.read("ashby", "initech")
    assert _live(home, "ashby", "initech") == {"x", "y", "q"}  # nothing local was deleted or marked removed
    assert initech.postings["x"].first_seen == T0  # the earlier sighting is kept
    # the stored postings are no longer what any one ETag describes: the next refresh reads the board in full
    assert (initech.etag, initech.last_modified, initech.body_sha256, initech.checked_at) == (None, None, None, T0)
    assert _live(home, "lever", "globex") == {"a"}

    # The delta the next day treats both the same way: neither board was created by the snapshot.
    assert index.validators_for_url("ashby", board_list_url("ashby", "initech")) is None


def test_an_older_local_board_that_matches_the_snapshot_takes_its_validators(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    index = CompanyIndex.for_home(home)
    index.write(_board("lever", "globex", {"a": "Staff Data Scientist"}, etag="old-etag", checked_at=T0))

    day1.run(home)

    entry = index.read("lever", "globex")
    assert (entry.etag, entry.checked_at) == ("etag-globex", T1)
    assert entry.postings["a"].first_seen == T0


# --- a snapshot that is not trusted ------------------------------------------------


@pytest.mark.parametrize("role", ["index", "tags", "boards"])
def test_a_corrupt_file_is_refused_before_anything_is_written(day1: _Release, tmp_path: Path, role: str) -> None:
    home = tmp_path / "home"
    CompanyIndex.for_home(home).write(_board("ashby", "initech", {"x": "Engineering Manager"}, checked_at=T0))
    store = TagStore.for_home(home, tagger_version=TAGGER_VERSION)
    tag_new_titles(store, ["Engineering Manager"])
    store.close()
    before = _local_data(home)
    victim = day1.directory / file_name(role, "2026-10-01")
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0xFF
    victim.write_bytes(bytes(data))

    result = day1.run(home)

    assert (result.status, result.reason) == ("refused", "digest_mismatch")
    assert _local_data(home) == before
    status = snapshot_status(home, environ={})
    assert (status["as_of"], status["last_result"], status["last_reason"]) == (None, "refused", "digest_mismatch")
    assert not (snapshot_dir(home) / "boards.json").exists()
    assert not list(snapshot_dir(home).glob("download-*"))


def test_a_truncated_download_and_a_foreign_manifest_are_refused(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    victim = day1.directory / file_name("index", "2026-10-01")
    whole = victim.read_bytes()
    victim.write_bytes(whole[:-7])
    assert day1.run(home).reason == "size_mismatch"
    victim.write_bytes(whole + b"padding")
    assert day1.run(home).reason == "size_mismatch"
    victim.write_bytes(whole)

    manifest = day1.directory / "manifest.json"
    document = json.loads(manifest.read_text())
    for change, reason in (
        ({"format_version": 99}, "manifest_version"),
        ({"format": "something-else"}, "manifest_foreign"),
        ({"contents": "with-descriptions"}, "not_metadata_only"),
    ):
        manifest.write_text(json.dumps({**document, **change}))
        result = day1.run(home)
        assert (result.status, result.reason) == ("refused", reason)
    manifest.write_text("<html>captive portal</html>")
    assert day1.run(home).reason == "manifest_unreadable"
    assert _index_files(home) == {}


def test_a_plain_http_address_is_refused_without_a_request(day1: _Release, tmp_path: Path) -> None:
    result = day1.run(tmp_path / "home", source="http://releases.example/manifest.json")
    assert (result.status, result.reason, day1.requests) == ("refused", "insecure_url", [])


def _craft(directory: Path, *, index_rows: list[dict], board_rows: list[dict], date: str = "2026-10-03") -> _Release:
    """A hand-made release: what a snapshot built by something other than the exporter could contain."""

    directory.mkdir()
    files = {}
    for role, rows in (("index", index_rows), ("tags", []), ("boards", board_rows)):
        data, count = snapshot._compress(rows)
        (directory / file_name(role, date)).write_bytes(data)
        files[file_name(role, date)] = {"role": role, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "rows": count}
    manifest = {
        "format": FORMAT_NAME, "format_version": FORMAT_VERSION, "as_of": f"{date}T06:00:00Z", "tagger_version": TAGGER_VERSION,
        "contents": "metadata-only", "counts": {"boards": len(board_rows), "postings": len(index_rows), "tags": 0}, "files": files,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return _Release(directory)


_BOARD_ROW = {"ats": "greenhouse", "slug": "acme", "company": "Acme", "etag": "e", "last_modified": None, "body_sha256": "s", "checked_at": T2, "changed_at": None}
_INDEX_ROW = {
    "ats": "greenhouse", "slug": "acme", "company": "Acme", "id": "1", "title": "Engineer", "location": "Remote", "url": "https://boards.example/jobs/1",
    "updated_at": None, "content_sha256": None, "first_seen": T1, "changed_at": None, "published_at": None, "countries": ["US"],
}


def test_no_description_text_is_ever_in_the_imported_data(day1: _Release, tmp_path: Path) -> None:
    # 1. a snapshot that carries a description field is refused outright
    home = tmp_path / "home"
    poisoned = _craft(tmp_path / "poisoned", index_rows=[{**_INDEX_ROW, "description": "SECRET-DESCRIPTION-TEXT"}], board_rows=[_BOARD_ROW])
    result = poisoned.run(home)
    assert (result.status, result.reason) == ("refused", "forbidden_field")
    assert _index_files(home) == {}

    # 2. text riding in under a field name the scan does not know is not copied: only the listed fields are
    smuggled = _craft(tmp_path / "smuggled", index_rows=[{**_INDEX_ROW, "summary_blob": "SECRET-DESCRIPTION-TEXT"}], board_rows=[{**_BOARD_ROW, "notes": "SECRET-DESCRIPTION-TEXT"}])
    assert smuggled.run(home).status == "imported"

    # 3. and an ordinary import: no description-like key or text anywhere under the home
    other = tmp_path / "other"
    day1.run(other)
    for root in (home, other):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            raw = path.read_bytes()
            assert b"SECRET-DESCRIPTION-TEXT" not in raw, path
            if path.suffix == ".json":
                for forbidden in (b'"description', b'"body"', b'"content"', b'"html"', b'"text"'):
                    assert forbidden not in raw, path
    assert set(json.loads(CompanyIndex.for_home(home).path("greenhouse", "acme").read_text())["postings"]["1"]) <= {
        "title", "location", "url", "updated_at", "content_sha256", "first_seen", "last_seen", "changed_at", "published_at", "countries",
    }


def test_rows_this_version_cannot_store_refuse_the_whole_snapshot(tmp_path: Path) -> None:
    home = tmp_path / "home"
    for number, (index_row, board_row) in enumerate(
        (
            ({**_INDEX_ROW, "url": "javascript:alert(1)"}, _BOARD_ROW),
            ({**_INDEX_ROW, "title": ""}, _BOARD_ROW),
            ({**_INDEX_ROW, "slug": "elsewhere"}, _BOARD_ROW),  # a posting of a board the snapshot does not list
            (_INDEX_ROW, {**_BOARD_ROW, "ats": "workday"}),
        )
    ):
        release = _craft(tmp_path / f"bad-{number}", index_rows=[index_row], board_rows=[board_row])
        result = release.run(home)
        assert (result.status, result.reason) == ("refused", "bad_row"), number
    assert _index_files(home) == {}


# --- unreachable, turned off -------------------------------------------------------


def test_offline_or_not_published_is_a_quiet_no_op_with_a_reason(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    CompanyIndex.for_home(home).write(_board("ashby", "initech", {"x": "Engineering Manager"}, checked_at=T0))
    before = _local_data(home)

    day1.offline = True
    result = day1.run(home)

    assert (result.status, result.reason) == ("skipped", "offline")
    assert _local_data(home) == before
    status = snapshot_status(home, environ={})
    assert (status["as_of"], status["last_attempt_at"], status["last_result"], status["last_reason"]) == (None, index_stamp(NOW), "skipped", "offline")

    # online again, but a file stops half way through the list: still a no-op
    day1.offline = False
    (day1.directory / file_name("tags", "2026-10-01")).unlink()
    assert day1.run(home).reason == "unavailable"
    assert _local_data(home) == before

    empty = _Release(tmp_path / "nothing-published")
    assert (empty.run(home).status, empty.run(home).reason) == ("skipped", "not_published")
    assert _local_data(home) == before


def test_opt_out_makes_no_request_and_writes_nothing(day1: _Release, tmp_path: Path) -> None:
    home, target = _project(tmp_path)

    by_environment = day1.run(home, environ={SNAPSHOT_ENV: "0"})
    assert (by_environment.status, by_environment.reason) == ("skipped", "disabled")

    path = settings_path(home, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "snapshot": {"enabled": False}}), encoding="utf-8")
    assert snapshot_setting(home, target, environ={}).to_json() == {"enabled": False, "manifest_url": snapshot.DEFAULT_MANIFEST_URL, "source": "setting"}
    by_setting = day1.run(home, target=target)
    with day1.client() as client:
        by_trigger = maybe_import_snapshot(home, NOW, target=target, client=client, environ={})
    assert (by_setting.reason, by_trigger.reason) == ("disabled", "disabled")

    assert day1.requests == []
    assert not snapshot_dir(home).exists() and _index_files(home) == {}
    assert snapshot_status(home, target, environ={})["enabled"] is False

    # the environment overrides the file either way; a file that cannot be read is OFF
    assert snapshot_setting(home, target, environ={SNAPSHOT_ENV: "on"}).enabled is True
    path.write_text("{not json", encoding="utf-8")
    assert snapshot_setting(home, target, environ={}).to_json()["source"] == "settings_unreadable"
    assert day1.run(home, target=target).reason == "disabled" and day1.requests == []


def test_the_manifest_address_is_a_setting(day1: _Release, tmp_path: Path) -> None:
    home, target = _project(tmp_path)
    assert snapshot_setting(home, target, environ={}).to_json() == {"enabled": True, "manifest_url": snapshot.DEFAULT_MANIFEST_URL, "source": "default"}
    assert snapshot.DEFAULT_MANIFEST_URL == "https://github.com/karthik446/gigai/releases/download/scout-snapshot/manifest.json"
    path = settings_path(home, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    moved = "https://data.example/scout/manifest.json"
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "snapshot": {"manifest_url": moved}}), encoding="utf-8")

    with day1.client() as client:
        result = import_snapshot(home, target=target, client=client, environ={}, now=NOW)

    assert result.status == "imported" and result.source == moved
    assert day1.urls[0] == moved and all(url.startswith("https://data.example/scout/") for url in day1.urls)
    assert snapshot_setting(home, target, environ={MANIFEST_URL_ENV: URL}).manifest_url == URL


# --- atomic ------------------------------------------------------------------------


def _half_filled(home: Path) -> None:
    index = CompanyIndex.for_home(home)
    index.write(_board("ashby", "initech", {"x": "Engineering Manager", "q": "Local Only"}, checked_at=T0))
    index.write(_board("greenhouse", "acme", {"1": "Senior Software Engineer"}, checked_at=T0))


def test_a_failure_while_writing_leaves_the_local_data_exactly_as_before(day1: _Release, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _half_filled(home)
    before = _local_data(home)
    real_write, calls = CompanyIndex.write, []

    def failing_write(self: CompanyIndex, entry: CompanyIndexEntry):
        calls.append(entry.key)
        if len(calls) == 3:
            raise OSError("disk full")
        return real_write(self, entry)

    monkeypatch.setattr(CompanyIndex, "write", failing_write)
    result = day1.run(home)
    monkeypatch.undo()

    assert len(calls) == 3  # two company files had already been replaced
    assert (result.status, result.reason) == ("failed", "apply_failed")
    assert _local_data(home)[0] == before[0]
    assert not (snapshot_dir(home) / "rollback").exists() and not (snapshot_dir(home) / "boards.json").exists()
    status = snapshot_status(home, environ={})
    assert (status["as_of"], status["last_result"]) == (None, "failed")
    # and the same import, undisturbed, goes through
    assert day1.run(home).status == "imported"


def test_a_failure_in_the_tag_store_puts_the_index_back(day1: _Release, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _half_filled(home)
    before = _local_data(home)

    def no_tags(self: TagStore, tags):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(TagStore, "write_rules", no_tags)
    result = day1.run(home)

    assert (result.status, result.reason) == ("failed", "apply_failed")
    assert _local_data(home) == before


def test_an_import_cut_off_mid_write_is_undone_by_the_next_attempt(day1: _Release, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _half_filled(home)
    before = _local_data(home)
    real_write, calls = CompanyIndex.write, []

    def dying_write(self: CompanyIndex, entry: CompanyIndexEntry):
        calls.append(entry.key)
        if len(calls) == 3:
            raise OSError("power cut")
        return real_write(self, entry)

    monkeypatch.setattr(CompanyIndex, "write", dying_write)
    monkeypatch.setattr(snapshot._Journal, "restore", lambda self: None)  # the process died: nothing was put back
    day1.run(home)
    monkeypatch.undo()
    assert _local_data(home)[0] != before[0] and (snapshot_dir(home) / "rollback" / "plan.json").is_file()

    day1.offline = True  # the next attempt cannot even reach the snapshot: the undo comes first
    assert day1.run(home).reason == "offline"

    assert _local_data(home)[0] == before[0]
    assert not (snapshot_dir(home) / "rollback").exists()


def test_a_second_import_at_the_same_time_steps_aside(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    snapshot_dir(home).mkdir(parents=True)
    (snapshot_dir(home) / "import.lock").write_text("")

    result = day1.run(home)

    assert (result.status, result.reason, day1.requests) == ("skipped", "import_running", [])


def test_an_import_steps_aside_while_update_sources_is_running(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    index = CompanyIndex.for_home(home)
    index.write_update_summary({"status": "running", "started_at": index_stamp(NOW), "updated_at": index_stamp(NOW), "pid": os.getpid()})

    result = day1.run(home)

    assert (result.status, result.reason, day1.requests) == ("skipped", "update_running", [])
    assert not snapshot_dir(home).exists()
    # the update's own trigger is not held up by the update it runs in
    with day1.client() as client:
        assert maybe_import_snapshot(home, NOW, client=client, environ={MANIFEST_URL_ENV: URL}).status == "imported"


# --- the validators make the next request conditional (S2) -------------------------


def test_an_imported_validator_makes_the_next_board_request_conditional(day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    day1.run(home)
    index, cache = CompanyIndex.for_home(home), board_cache_for_home(home)
    before = index.read("greenhouse", "acme")
    seen: list[dict[str, str]] = []

    def board(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), **{k.lower(): v for k, v in request.headers.items() if k.lower().startswith("if-")}})
        if request.headers.get("if-none-match") == "etag-acme":
            return httpx.Response(304, headers={"ETag": "etag-acme"})
        return httpx.Response(200, headers={"ETag": "etag-acme"}, content=json.dumps({"jobs": []}).encode())

    config = FindJobsConfig(
        roles=("software engineer",), merged_queries=("software engineer",), location="Denver, CO", remote=True,
        published_after=None, sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
    )
    with httpx.Client(transport=httpx.MockTransport(board)) as client:
        fetched = ATSBoardClients().fetch_board(client, "greenhouse", "acme", config, cache=cache)

    assert len(seen) == 1 and seen[0]["if-none-match"] == "etag-acme"
    assert seen[0]["if-modified-since"] == "Mon, 01 Sep 2026 00:00:00 GMT"
    assert fetched.stats.cache == "hit" and not list((home / "cache" / "scout" / "ats-boards").rglob("*.body.gz"))
    change = refresh_company(index, cache, ats="greenhouse", slug="acme", observed_at=LATER)
    assert change.status == STATUS_UNTOUCHED
    after = index.read("greenhouse", "acme")
    assert after.postings == before.postings and after.checked_at == LATER


# --- the trigger -------------------------------------------------------------------


def test_the_trigger_imports_into_an_empty_index_and_leaves_a_fresher_one_alone(builder: Path, day1: _Release, tmp_path: Path) -> None:
    home = tmp_path / "home"
    with day1.client() as client:
        first = maybe_import_snapshot(home, NOW, client=client, environ={MANIFEST_URL_ENV: URL})
        assert (first.status, first.kind) == ("imported", "full")

        # checked a short while ago: no request at all
        day1.requests.clear()
        again = maybe_import_snapshot(home, NOW + timedelta(hours=1), client=client, environ={MANIFEST_URL_ENV: URL})
        assert (again.status, again.reason, day1.requests) == ("skipped", "checked_recently", [])

    # the machine ran Update sources after day 2's snapshot was built: one manifest request, nothing imported
    day2 = _day2(builder, day1, tmp_path)
    index = CompanyIndex.for_home(home)
    index.write_update_summary({"status": "succeeded", "started_at": LATER, "finished_at": LATER})
    before = _local_data(home)
    with day2.client() as client:
        fresher = maybe_import_snapshot(home, NOW + timedelta(days=4), client=client, environ={MANIFEST_URL_ENV: URL})
        assert (fresher.status, fresher.reason, day2.requests) == ("skipped", "local_fresher", ["manifest.json"])
        assert _local_data(home) == before

        # the last update ended BEFORE the snapshot was built (the machine was off): it is imported
        index.write_update_summary({"status": "succeeded", "started_at": T1, "finished_at": T1})
        older = maybe_import_snapshot(home, NOW + timedelta(days=5), client=client, environ={MANIFEST_URL_ENV: URL})
        assert (older.status, older.kind) == ("imported", "delta")


def test_the_trigger_never_raises(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.write_text("not a directory")  # the home cannot hold a cache at all
    release = _Release(tmp_path / "none")
    with release.client() as client:
        result = maybe_import_snapshot(home, NOW, client=client, environ={MANIFEST_URL_ENV: URL})
    assert (result.status, result.reason) == ("failed", "apply_failed")


# --- CLI ---------------------------------------------------------------------------


def test_cli_imports_a_snapshot_directory_and_reports_the_status(day1: _Release, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SNAPSHOT_ENV, raising=False)
    monkeypatch.delenv(MANIFEST_URL_ENV, raising=False)
    home = tmp_path / "home"
    runner = CliRunner()

    never = runner.invoke(scout_group, ["snapshot", "status", "--home", str(home)])
    assert never.exit_code == 0 and "No snapshot has been imported." in never.output and "Snapshot download: on (default)." in never.output
    assert not home.exists()  # a status read creates nothing

    done = runner.invoke(scout_group, ["snapshot", "import", "--from", str(day1.directory), "--home", str(home)])
    assert done.exit_code == 0, done.output
    assert "Imported the snapshot as of 2026-10-01T06:00:00Z." in done.output and "5 postings on 3 boards and 5 title tags" in done.output
    assert _live(home, "greenhouse", "acme") == {"1", "2"}

    status = json.loads(runner.invoke(scout_group, ["snapshot", "status", "--home", str(home), "--json"]).output)
    assert (status["as_of"], status["last_result"], status["kind"]) == ("2026-10-01T06:00:00Z", "imported", "full")

    again = runner.invoke(scout_group, ["snapshot", "import", "--from", str(day1.directory / "manifest.json"), "--home", str(home), "--json"])
    assert again.exit_code == 0 and json.loads(again.output)["status"] == "up_to_date"

    # a snapshot that fails its checks exits 1; one that is simply not there exits 0
    victim = day1.directory / file_name("boards", "2026-10-01")
    victim.write_bytes(victim.read_bytes()[:-3])
    other = tmp_path / "other"
    refused = runner.invoke(scout_group, ["snapshot", "import", "--from", str(day1.directory), "--home", str(other), "--json"])
    assert refused.exit_code == 1 and json.loads(refused.output)["reason"] == "size_mismatch"
    missing = runner.invoke(scout_group, ["snapshot", "import", "--from", str(tmp_path / "no-such-dir"), "--home", str(other)])
    assert missing.exit_code == 0 and "no snapshot manifest" in missing.output.lower()
    off = runner.invoke(scout_group, ["snapshot", "import", "--from", str(day1.directory), "--home", str(other)], env={SNAPSHOT_ENV: "0"})
    assert off.exit_code == 0 and "turned off" in off.output
