"""0110-10-14 (the correction): a snapshot and the day a Greenhouse posting went up.

A Greenhouse posting's ``published_at`` in the company index is its posting day only when the posting carries
``published_kind: posted`` (``company_index``): until 0.1.10.10 that field held the list's ``updated_at``. A snapshot
row carries the mark (one more listed field; ``format_version`` is unchanged), so:

- a snapshot built by this version keeps the posting days, and the board's validators stay usable (a fresh install's
  first update asks ``If-None-Match`` and gets a ``304``);
- a snapshot built BEFORE (no mark on any row) imports as it always did, and its Greenhouse dates are not read as
  posting days: those postings are undated until the machine's own update has read the board, which that update does
  with its one list request, made unconditional (a ``200`` with the body where a ``304`` would bring none). Lever and
  Ashby rows are what they were;
- a reader that does not know the mark (an older GigAI: it copies only the fields it lists) imports a snapshot that
  has it exactly as it imports the same snapshot without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout.find_jobs import snapshot
from gigai.scout.find_jobs.company_index import CompanyIndex, CompanyIndexEntry, IndexedPosting, board_list_url
from gigai.scout.find_jobs.snapshot import export_snapshot, read_lines

from .test_snapshot_import import DAY1, T1, T2, _BOARD_ROW, _INDEX_ROW, _Release, _craft, _index_files

_POSTED = "2026-08-02T09:30:00-04:00"
_UPDATED = "2026-10-01T11:15:00-04:00"
_GREENHOUSE_URL = board_list_url("greenhouse", "acme")
_LEVER_URL = board_list_url("lever", "globex")
#: The rows a 0.1.10.10 exporter wrote: a Greenhouse posting's ``published_at`` is its ``updated_at``, and no mark.
_OLD_GREENHOUSE = {**_INDEX_ROW, "updated_at": _UPDATED, "published_at": _UPDATED}
_LEVER_BOARD = {**_BOARD_ROW, "ats": "lever", "slug": "globex", "company": "Globex"}
_LEVER_ROW = {**_INDEX_ROW, "ats": "lever", "slug": "globex", "company": "Globex", "id": "a", "url": "https://jobs.lever.co/globex/a", "published_at": "2026-09-19T00:00:00Z"}


def test_a_snapshot_built_by_this_version_keeps_the_day_a_greenhouse_posting_went_up(tmp_path: Path) -> None:
    builder = tmp_path / "builder"
    posting = IndexedPosting(
        posting_id="1", title="Engineer", location="Remote", url="https://boards.example/jobs/1", updated_at=_UPDATED,
        content_sha256="ab" * 32, first_seen=T1, last_seen=T1, published_at=_POSTED, published_kind="posted",
    )
    CompanyIndex.for_home(builder).write(CompanyIndexEntry(
        company="Acme", ats="greenhouse", slug="acme", checked_at=T1, etag="etag-acme", body_sha256="cd" * 32, postings={"1": posting},
    ))
    out = tmp_path / "release"
    export_snapshot(builder, out, as_of=DAY1)
    (row,) = [line for name in sorted(path.name for path in out.iterdir()) if name.startswith("index-") for line in read_lines(out / name)]
    assert (row["published_at"], row["published_kind"], row["updated_at"]) == (_POSTED, "posted", _UPDATED)

    home = tmp_path / "home"
    assert _Release(out).run(home).status == "imported"

    index = CompanyIndex.for_home(home)
    entry = index.read("greenhouse", "acme")
    assert entry is not None and not entry.dates_pending
    assert (entry.postings["1"].published_at, entry.postings["1"].updated_at) == (_POSTED, _UPDATED)
    # The board's validators are offered: the first update's list request is conditional.
    assert index.validators_for_url("greenhouse", _GREENHOUSE_URL) == ("etag-acme", None)


def test_a_snapshot_from_before_never_gives_a_greenhouse_posting_its_last_change_as_its_posting_day(tmp_path: Path) -> None:
    home = tmp_path / "home"
    release = _craft(tmp_path / "old", index_rows=[_OLD_GREENHOUSE, _LEVER_ROW], board_rows=[_BOARD_ROW, _LEVER_BOARD])

    assert release.run(home).status == "imported"

    index = CompanyIndex.for_home(home)
    greenhouse, lever = index.read("greenhouse", "acme"), index.read("lever", "globex")
    assert greenhouse is not None and lever is not None
    # Greenhouse: no posting day yet (its last change is kept as what it is), and the company waits for the update.
    assert (greenhouse.postings["1"].published_at, greenhouse.postings["1"].updated_at) == (None, _UPDATED)
    assert greenhouse.dates_pending
    # So its validators are withheld: the update's list request brings the body (a 200), which has the posting day.
    assert index.validators_for_url("greenhouse", _GREENHOUSE_URL) is None
    # Lever's date always was the posting day: read as before, and its request stays conditional.
    assert lever.postings["a"].published_at == "2026-09-19T00:00:00Z" and not lever.dates_pending
    assert index.validators_for_url("lever", _LEVER_URL) == ("e", None)


def test_a_reader_that_does_not_know_the_mark_imports_the_snapshot_as_it_did(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 0.1.10.10 reader's rule, in place of this version's: only the fields IT lists are copied."""

    def older_reader(row, last_seen):
        url = snapshot._text(row, "url", required=True)
        countries = row.get("countries")
        return IndexedPosting(
            posting_id=snapshot._text(row, "id", required=True), title=snapshot._text(row, "title", required=True), location=snapshot._text(row, "location") or "",
            url=url, updated_at=snapshot._text(row, "updated_at"), content_sha256=snapshot._text(row, "content_sha256"),
            first_seen=snapshot._text(row, "first_seen") or last_seen, last_seen=last_seen, changed_at=snapshot._text(row, "changed_at"),
            published_at=snapshot._text(row, "published_at"), countries=tuple(item for item in countries if type(item) is str) if isinstance(countries, list) else None,
        )

    monkeypatch.setattr(snapshot, "_posting_from_row", older_reader)
    marked = {**_INDEX_ROW, "updated_at": _UPDATED, "published_at": _POSTED, "published_kind": "posted"}
    unmarked = {key: value for key, value in marked.items() if key != "published_kind"}
    with_mark, without_mark = tmp_path / "home-a", tmp_path / "home-b"

    first = _craft(tmp_path / "marked", index_rows=[marked, _LEVER_ROW], board_rows=[{**_BOARD_ROW, "checked_at": T2}, _LEVER_BOARD]).run(with_mark)
    second = _craft(tmp_path / "unmarked", index_rows=[unmarked, _LEVER_ROW], board_rows=[{**_BOARD_ROW, "checked_at": T2}, _LEVER_BOARD]).run(without_mark)

    assert (first.status, first.counts) == (second.status, second.counts) == ("imported", first.counts)
    # Byte for byte the same company files: the key it does not list changes nothing it stores.
    assert _index_files(with_mark) == _index_files(without_mark) and len(_index_files(with_mark)) >= 2
    assert all(b"published_kind" not in data for data in _index_files(with_mark).values())
