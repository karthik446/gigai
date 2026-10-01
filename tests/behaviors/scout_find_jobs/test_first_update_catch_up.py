"""0110-028: the first update over an index older than the tag store and the text index must not stall.

The operator's first background check after the upgrade sat at "Checked 0 of
3,982" for over a minute: the update rules-tagged every stored title before
its first request, and the first board that settled built the whole text
index before it was counted. These tests pin the order of the work in steps
(company files read, store calls), never in wall seconds.

No network: every board request goes to ``test_sources_update._Boards``.
The index is synthetic (``_stored_companies``): many company files that no
update of this test wrote, as an index built by an earlier version is.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from gigai.scout.find_jobs import posting_tags, sources_update, text_index
from gigai.scout.find_jobs.company_index import COMPANY_INDEX_SCHEMA, CompanyIndex
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.sources_update import (
    STATUS_PARTIAL,
    STATUS_SUCCEEDED,
    board_cache_for_home,
    update_sources,
)

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _limits

from .test_sources_update import _Boards

#: Read with a default so this file still collects (and fails on the symptom) against a build before the fix.
CATCH_UP_COMPANIES_PER_BOARD = getattr(sources_update, "CATCH_UP_COMPANIES_PER_BOARD", 10)
STORED_COMPANIES = 1200
POSTINGS_EACH = 12
STAMP = "2026-09-30T00:00:00.000Z"


def _stored_companies(home: Path, count: int = STORED_COMPANIES) -> set[str]:
    """``count`` company files with ``POSTINGS_EACH`` postings each; returns the distinct normalized titles."""

    index = CompanyIndex.for_home(home)
    index.root.mkdir(parents=True, exist_ok=True)
    titles: set[str] = set()
    for number in range(count):
        slug = f"stored{number:05d}"
        postings = {}
        for item in range(POSTINGS_EACH):
            # Two of three titles carry a word the rules place; the rest wait for a model.
            title = f"Senior Software Engineer Team {number}-{item}" if item % 3 else f"Director, Coordinator Group {number}-{item}"
            titles.add(posting_tags.normalize_title(title))
            postings[f"p{item}"] = {
                "title": title, "location": "Remote", "url": f"https://example.test/{slug}/{item}", "updated_at": STAMP,
                "content_sha256": None, "first_seen": STAMP, "last_seen": STAMP,
            }
        payload = {
            "schema_version": COMPANY_INDEX_SCHEMA, "company": slug, "ats": "lever", "slug": slug, "checked_at": STAMP,
            "changed_at": None, "etag": None, "last_modified": None, "body_sha256": None, "postings": postings,
        }
        index.path("lever", slug).write_text(json.dumps(payload), encoding="utf-8")
    return titles


def _watchlist() -> list:
    return [_board(ATSProvider.GREENHOUSE, "acme"), _board(ATSProvider.GREENHOUSE, "globex"), _board(ATSProvider.LEVER, "initech")]


class _Steps:
    """Counts the one-time store work: stored company files read, rules passes, full text builds."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.stored_reads = 0
        self.rules_passes = 0
        self.text_builds = 0
        read, tag, build = CompanyIndex.read, posting_tags.tag_new_titles, text_index.rebuild_from_cache

        def counting_read(index: CompanyIndex, ats: str, slug: str):
            if slug.startswith("stored"):
                self.stored_reads += 1
            return read(index, ats, slug)

        def counting_tag(store, titles):
            self.rules_passes += 1
            return tag(store, titles)

        def counting_build(home_root):
            self.text_builds += 1
            return build(home_root)

        monkeypatch.setattr(CompanyIndex, "read", counting_read)
        monkeypatch.setattr(posting_tags, "tag_new_titles", counting_tag)
        monkeypatch.setattr(text_index, "rebuild_from_cache", counting_build)

    def now(self) -> tuple[int, int, int]:
        return self.stored_reads, self.rules_passes, self.text_builds


def _update(home: Path, boards: _Boards, watchlist: list, **kwargs):
    with boards.client() as client:
        return update_sources(
            watchlist, cache=board_cache_for_home(home), index=CompanyIndex.for_home(home), client=client,
            limits=_limits(concurrency=1), home_root=home, **kwargs,
        )


def test_the_first_board_is_asked_and_counted_before_any_one_time_store_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    stored_titles = _stored_companies(home)
    steps = _Steps(monkeypatch)
    monkeypatch.setattr(sources_update, "SNAPSHOT_INTERVAL_SECONDS", 0.0)  # every counted board is published
    boards = _Boards()
    at_first_request: list[tuple[int, int, int]] = []
    at_each_counted: dict[int, tuple[int, int, int]] = {}
    handler = boards.handler

    def recording(request):
        at_first_request.append(steps.now())
        return handler(request)

    boards.handler = recording  # type: ignore[method-assign]

    def on_progress(snapshot: dict[str, object]) -> None:
        if snapshot["status"] == "running":
            at_each_counted.setdefault(snapshot["boards"]["checked"], steps.now())  # type: ignore[index]

    result = _update(home, boards, _watchlist(), on_progress=on_progress)

    assert result.status == STATUS_SUCCEEDED
    # The symptom: nothing of the stored index is read, tagged or text-indexed before the first board is ASKED ...
    assert at_first_request[0] == (0, 0, 0)
    # ... nor before it is COUNTED in the snapshot (the full text build used to sit inside that first board).
    assert at_each_counted[1][0] == 0 and at_each_counted[1][2] == 0
    # Between two boards the catch-up reads a bounded number of company files, and no text build runs.
    assert at_each_counted[2][0] <= CATCH_UP_COMPANIES_PER_BOARD and at_each_counted[2][2] == 0
    assert at_each_counted[3][0] <= 2 * CATCH_UP_COMPANIES_PER_BOARD and at_each_counted[3][2] == 0

    # The work is all done by the end of the update: every stored title has a rules tag ...
    final = result.to_json()
    store = posting_tags.default_store(home)
    try:
        assert store.rules_catch_up_done()
        assert set(store.get_many(stored_titles)) == stored_titles
        assert final["stores"]["tags"]["titles_backfilled"] == len(stored_titles)
        assert final["stores"]["tags"]["titles_tagged"] + len(stored_titles) == store.count()
    finally:
        store.close()
    # ... each stored company file was read once, and the text index was built once, after the boards.
    assert steps.stored_reads >= STORED_COMPANIES and steps.text_builds == 1
    assert final["catch_up"] == {"tags": {"companies_done": STORED_COMPANIES, "companies_total": STORED_COMPANIES, "pending": False}, "text_index": None}
    stats = text_index.stats(home)
    text_index.close(home)
    assert stats.available and stats.postings >= STORED_COMPANIES * POSTINGS_EACH

    # The next update has no catch-up left: no stored company file is read for it.
    steps.stored_reads = steps.rules_passes = steps.text_builds = 0
    again = _update(home, boards, _watchlist(), full_refresh=True)
    assert again.to_json()["catch_up"] is None and again.to_json()["stores"]["tags"]["titles_backfilled"] == 0
    assert steps.text_builds == 0


def test_the_snapshot_keeps_being_rewritten_while_the_catch_up_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The heartbeat thread outlives the boards: a long catch-up (or text build) never reads as a dead update."""

    home = tmp_path / "home"
    _stored_companies(home, count=30)
    # Only the heartbeat rewrites the snapshot here: the ordinary progress writes are a day apart.
    monkeypatch.setattr(sources_update, "SNAPSHOT_INTERVAL_SECONDS", 86400.0)
    monkeypatch.setattr(sources_update, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    in_catch_up = threading.Event()
    beats = threading.Event()
    seen: list[dict[str, object]] = []
    tag = posting_tags.tag_new_titles

    def slow_tag(store, titles):
        in_catch_up.set()
        assert beats.wait(timeout=30), "the snapshot was not rewritten while the catch-up ran"
        return tag(store, titles)

    def on_progress(snapshot: dict[str, object]) -> None:
        if in_catch_up.is_set() and snapshot["status"] == "running":
            seen.append(snapshot)
            if len(seen) >= 3:
                beats.set()

    monkeypatch.setattr(posting_tags, "tag_new_titles", slow_tag)

    result = _update(home, _Boards(), [], on_progress=on_progress)  # no board is due: the whole catch-up runs after them

    assert result.status == STATUS_SUCCEEDED
    assert len(seen) >= 3 and len({snapshot["updated_at"] for snapshot in seen[:3]}) >= 2
    assert all(snapshot["catch_up"]["tags"]["pending"] for snapshot in seen[:3])  # type: ignore[index]
    assert result.to_json()["catch_up"]["tags"] == {"companies_done": 30, "companies_total": 30, "pending": False}


def test_a_stopped_update_leaves_the_catch_up_to_the_next_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    stored_titles = _stored_companies(home, count=200)
    steps = _Steps(monkeypatch)
    stop = threading.Event()
    stop.set()  # a shutdown, or a Full refresh taking over: the update ends between boards

    stopped = _update(home, _Boards(), _watchlist(), stop=stop)

    assert stopped.status == STATUS_PARTIAL and stopped.to_json()["cancelled"] is True
    assert steps.now() == (0, 0, 0)  # nothing waited for the one-time work
    assert stopped.to_json()["catch_up"] == {"tags": {"companies_done": 0, "companies_total": 200, "pending": True}, "text_index": "deferred"}
    store = posting_tags.default_store(home)
    try:
        assert not store.rules_catch_up_done()
    finally:
        store.close()

    finished = _update(home, _Boards(), _watchlist())

    assert finished.status == STATUS_SUCCEEDED
    assert finished.to_json()["stores"]["tags"]["titles_backfilled"] == len(stored_titles)
    assert finished.to_json()["catch_up"]["tags"]["pending"] is False and steps.text_builds == 1
