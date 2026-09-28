"""uat-bug-011 (UAT N14, P0): a full-catalog run must never fail on the import bound.

Evidence (run_3f4b11e0, 2026-09-27): 10,359 boards fetched in 1,158 s,
7,426 postings matched the title prefilter, and the run then died in
``acquisition_records._validate_rows`` with ``ScoutAcquisitionError: public
import batch exceeds its bound`` (``_MAX_ROWS = 512``): 19 minutes of
fetching, 0 postings.

What these tests pin, all with ``acquire_node`` called directly (the same
construction ``test_acquire_scale.py`` uses), real ``ATSBoardClients`` over
a fake transport where the fetch matters, and the REAL journal import (no
``import_public_rows`` patch) on a real journaled workpad:

* more than 512 rows left after every filter: the run succeeds, imports at
  most ``IMPORT_ROW_CAP`` (500) rows and records the remainder as a count;
* the imported rows are the best ranked: Jev score when scores exist, else
  newest; a row left out is not recorded as observed, so it is NEW again
  the next time it is seen;
* the window, location and role filters run before the import (a lock:
  true before this packet too, see the ticket's corrected cause);
* what can be refused without the rows (the batch identity) is refused
  before any board is fetched; one row the journal would refuse costs that
  row, not the run;
* the new ``not_imported_count`` is additive: an acquire output sealed
  before it existed parses and re-serializes byte-identically.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.scout.acquisition_records import ScoutAcquisitionError
from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients, BoardFetchResult, BoardFetchStats
from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    AcquireOutput,
    ATSProvider,
    FindJobsConfig,
    NotAssessedReason,
    PostingRow,
    ProgressStatus,
    RowOutcome,
    SelectionRule,
    SourceKind,
    SourceToggles,
    WatchlistEntry,
    WatchlistFirstSeen,
)
from gigai.scout.find_jobs.jev_contracts import RankScore
from gigai.scout.find_jobs.market_acquisition import BOARDS_FROM_FETCH, AcquireLimits, acquire_node
from gigai.scout.find_jobs.progress import ProgressWriter, read_progress
from tests.behaviors.scout_find_jobs.test_unchanged_reassess import _context, _managed_workpad

FIXTURES = Path(__file__).parent / "fixtures"

BOARDS = 6
US_PER_BOARD = 100
ABROAD_PER_BOARD = 5
STALE_PER_BOARD = 5
OFF_ROLE_PER_BOARD = 3
MATCHED = BOARDS * US_PER_BOARD  # 600: what is left after every filter
CAP = 500


def _config(*, ats: bool = True) -> FindJobsConfig:
    config = FindJobsConfig.from_json(json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text()))
    return replace(
        config,
        sources=SourceToggles(exa=False, ats=ats, hiringcafe=False),
        published_after=None,
        max_age_days=60,
        countries=("US",),
    )


def _input(config: FindJobsConfig, rows: tuple[PostingRow, ...] = ()) -> AcquireInput:
    return AcquireInput(config, "sha256:" + "c" * 64, None, rows, 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH)


def _limits() -> AcquireLimits:
    return AcquireLimits(concurrency_per_provider=4, min_request_interval_seconds=0.0, time_budget_seconds=None)


def _board(token: str) -> WatchlistEntry:
    return WatchlistEntry(
        watchlist_id=f"scout_watchlist:lever:{token}", provider=ATSProvider.LEVER, board_token=token,
        company=token, state="active",
        first_seen=WatchlistFirstSeen(SourceKind.ATS, f"https://example.test/{token}", "software engineer", "batch-1", "2026-09-22T00:00:00Z"),
    )


class _Watchlist:
    def __init__(self, boards):
        self.boards = tuple(boards)

    def add_to_watchlist(self, entry):
        return entry

    def active_entries(self):
        return self.boards


class _Exa:
    def search(self, client, config, *, home_root=None):
        return ()


class _LeverCatalog:
    """Six Lever boards: 100 in-window US postings each, plus rows every filter must drop.

    ``age(board, job)`` numbers the 600 kept postings 0 (newest) to 599
    (oldest), one minute apart on whole seconds, so "the newest 500" is
    exactly the postings whose age is below 500.
    """

    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
        self.requests: list[str] = []

    @staticmethod
    def age(board: int, job: int) -> int:
        return job * BOARDS + board

    @staticmethod
    def url(board: int, job: int | str) -> str:
        return f"https://jobs.lever.co/co{board}/{job}"

    def _created(self, age_minutes: int) -> int:
        return int((self.now - timedelta(minutes=age_minutes)).timestamp() * 1000)

    def _job(self, board: int, job: int | str, title: str, *, created: int, country: str, location: str) -> dict[str, object]:
        return {
            "id": f"co{board}-{job}", "text": title, "hostedUrl": self.url(board, job), "country": country,
            "categories": {"location": location}, "createdAt": created,
            "descriptionPlain": f"Build systems at co{board}, posting {job}.",
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        token = request.url.path.rsplit("/", 1)[-1]
        board = int(token.removeprefix("co"))
        jobs = [
            self._job(board, job, f"Software Engineer {job}", created=self._created(self.age(board, job)), country="US", location="Denver, CO")
            for job in range(US_PER_BOARD)
        ]
        jobs += [
            self._job(board, f"abroad{job}", f"Software Engineer Abroad {job}", created=self._created(job), country="IN", location="Bengaluru, India")
            for job in range(ABROAD_PER_BOARD)
        ]
        stale = int((self.now - timedelta(days=200)).timestamp() * 1000)
        jobs += [
            self._job(board, f"stale{job}", f"Software Engineer Stale {job}", created=stale, country="US", location="Denver, CO")
            for job in range(STALE_PER_BOARD)
        ]
        jobs += [
            self._job(board, f"recruiter{job}", f"Recruiter {job}", created=self._created(job), country="US", location="Denver, CO")
            for job in range(OFF_ROLE_PER_BOARD)
        ]
        return httpx.Response(200, json=jobs)

    def kept_urls(self, *, ages: range) -> set[str]:
        return {
            self.url(board, job)
            for board in range(BOARDS)
            for job in range(US_PER_BOARD)
            if self.age(board, job) in ages
        }


def _run(workpad: Path, project_id: str, gig_id: str, catalog: _LeverCatalog, *, run_id: str) -> AcquireOutput:
    boards = [_board(f"co{index}") for index in range(BOARDS)]
    with httpx.Client(transport=httpx.MockTransport(catalog.handler)) as client:
        return acquire_node(
            _context(workpad, project_id, gig_id, run_id, operation_key=f"acquire-{run_id}"), _input(_config()),
            http_client=client, exa=_Exa(), ats=ATSBoardClients(), watchlist=_Watchlist(boards), limits=_limits(),
            boards_from=BOARDS_FROM_FETCH,
        )


def _journal_rows(workpad: Path, run_id: str) -> list[dict[str, object]]:
    path = workpad / "records" / "scout-acquisition" / f"acquire-{run_id}" / "input.json"
    return json.loads(path.read_text())["rows"]


# --- the P0: more than 512 matched rows ---------------------------------------


def test_a_run_with_more_matched_rows_than_the_journal_bound_succeeds(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path, "uat-bug-011")
    catalog = _LeverCatalog()

    out = _run(workpad, project_id, gig_id, catalog, run_id="run_01")

    # The symptom: the run completes and its postings are there.
    assert out.progress_status is ProgressStatus.COMPLETE
    assert len(catalog.requests) == BOARDS
    assert len(out.rows) == CAP
    assert {row.outcome for row in out.rows} == {RowOutcome.NEW}
    assert out.selected_postings, "nothing was selected for assessment"

    # The journal, the sealed rows and the live progress all hold the same rows.
    journal = _journal_rows(workpad, "run_01")
    sealed_urls = {row.posting.normalized_url for row in out.rows}
    assert len(journal) == CAP
    assert {row["source_snapshot"]["locator"] for row in journal} == sealed_urls
    snapshot = read_progress(workpad / "runs" / "run_01")
    assert {posting["normalized_url"] for posting in snapshot.postings} == sealed_urls
    assert snapshot.steps["acquire"]["status"] == "done"

    # No Jev scores in this run: the newest 500 are imported.
    assert sealed_urls == catalog.kept_urls(ages=range(CAP))
    assert {item.normalized_url for item in out.selected_postings} <= sealed_urls

    # The remainder is a count, never a failure.
    assert out.not_imported_count == MATCHED - CAP
    assert snapshot.not_imported_count == MATCHED - CAP
    assert snapshot.candidate_count == CAP
    cap_file = json.loads((workpad / "runs" / "run_01" / "progress" / "cap.json").read_text())
    assert cap_file["not_imported_count"] == MATCHED - CAP
    assert out.failures == ()
    assert AcquireOutput.from_json(out.to_json()) == out
    assert out.to_json()["not_imported_count"] == MATCHED - CAP

    # Filters ran first: what they dropped never competed for the 500.
    dropped = {item.reason: item.count for item in out.dropped_counts}
    assert dropped[NotAssessedReason.LOCATION_MISMATCH] == BOARDS * ABROAD_PER_BOARD
    assert dropped[NotAssessedReason.PUBLISHED_TOO_OLD] == BOARDS * STALE_PER_BOARD


def test_the_import_is_ranked_by_jev_score_and_a_row_left_out_is_new_next_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path, "uat-bug-011-ranked")
    catalog = _LeverCatalog()
    first = _run(workpad, project_id, gig_id, catalog, run_id="run_01")
    oldest = catalog.kept_urls(ages=range(CAP, MATCHED))
    assert not oldest & {row.posting.normalized_url for row in first.rows}

    # Run 2 has Jev scores: the 100 oldest postings (left out of run 1) are
    # the best fits, 50 of the newest are scored as poor fits.
    newest = catalog.kept_urls(ages=range(50))

    def fake_rank(candidates, **_kwargs):
        scores = []
        for row in candidates:
            score = 90 if row.normalized_url in oldest else 5 if row.normalized_url in newest else None
            scores.append(RankScore(
                normalized_url=row.normalized_url, content_sha256=row.content_sha256 or "sha256:" + "0" * 64,
                fit=None if score is None else "strong" if score >= 70 else "no", score=score, reasons=(),
                mismatch_flags=(), hidden_by_default=False, cost_usd="0", cached=True,
            ))
        return tuple(scores)

    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition._rank_candidates", fake_rank)
    second = _run(workpad, project_id, gig_id, catalog, run_id="run_02")

    assert second.progress_status is ProgressStatus.COMPLETE
    imported = {row.posting.normalized_url: row.outcome for row in second.rows}
    assert len(imported) == CAP and second.not_imported_count == MATCHED - CAP
    # Scored rows lead (90s, then the 5s), the newest unscored rows fill the rest.
    assert set(imported) == oldest | newest | catalog.kept_urls(ages=range(50, 400))
    # Never imported before, so never recorded as observed: NEW, not unchanged.
    assert {imported[url] for url in oldest} == {RowOutcome.NEW}
    assert {imported[url] for url in newest} == {RowOutcome.UNCHANGED}
    # The sealed scores cover the imported rows only.
    assert {item.normalized_url for item in second.rank_scores} == set(imported)
    # Selection picks from the imported rows, best Jev fits first.
    assert {item.normalized_url for item in second.selected_postings} <= oldest
    assert len(_journal_rows(workpad, "run_02")) == CAP


def test_under_the_cap_nothing_is_left_out(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path, "uat-bug-011-small")
    rows = tuple(_row(index) for index in range(7))

    out = acquire_node(
        _context(workpad, project_id, gig_id, "run_01", operation_key="acquire-run_01"), _input(_config(ats=False), rows),
        http_client=None, exa=_Exa(), ats=ATSBoardClients(), watchlist=_Watchlist(()),
    )

    assert [row.posting.normalized_url for row in out.rows] == [row.normalized_url for row in rows]
    assert out.not_imported_count == 0
    assert "not_imported_count" not in out.to_json()
    assert read_progress(workpad / "runs" / "run_01").not_imported_count == 0


# --- filters before the import (a lock) ----------------------------------------


def _row(index: int, *, title: str | None = None, location: str = "Denver, CO", age_days: int = 1, company: str | None = None) -> PostingRow:
    url = f"https://boards.example/co{index}/1"
    published = (datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=age_days, minutes=index)).isoformat().replace("+00:00", "Z")
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.LEVER, board_token=f"co{index}",
        company=company or f"Company {index}", title=title or f"Software Engineer {index}", location=location,
        published_at=published, content_sha256="sha256:" + f"{index:064x}",
        source_kind=SourceKind.ATS, query_key="software engineer",
    )


def test_window_location_and_role_filters_run_before_the_import(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path, "uat-bug-011-filters")
    kept = [_row(0), _row(1)]
    rows = (
        *kept,
        _row(2, location="Bengaluru, India"),
        _row(3, age_days=200),
        _row(4, title="Recruiter"),
        _row(5, location="EMEA"),
    )

    out = acquire_node(
        _context(workpad, project_id, gig_id, "run_01", operation_key="acquire-run_01"), _input(_config(ats=False), rows),
        http_client=None, exa=_Exa(), ats=ATSBoardClients(), watchlist=_Watchlist(()),
    )

    kept_urls = {row.normalized_url for row in kept}
    assert {row["source_snapshot"]["locator"] for row in _journal_rows(workpad, "run_01")} == kept_urls
    assert {row.posting.normalized_url for row in out.rows} == kept_urls
    assert {item.reason: item.count for item in out.dropped_counts} == {
        NotAssessedReason.LOCATION_MISMATCH: 1,
        NotAssessedReason.PUBLISHED_TOO_OLD: 1,
        NotAssessedReason.ROLE_MISMATCH: 1,
        NotAssessedReason.REGION_ONLY: 1,
    }


# --- fail fast, and one bad row is not the run ---------------------------------


class _CountingATS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_board(self, client, provider, board_token, config, *, cache=None):
        self.calls.append(board_token)
        row = replace(_row(len(self.calls)), board_token=board_token)
        return BoardFetchResult((row,), BoardFetchStats(requests=1, cache="miss", listed=1))


def test_a_batch_identity_the_journal_refuses_fails_before_any_board_is_fetched(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path, "uat-bug-011-preflight")
    ats = _CountingATS()

    with pytest.raises(ScoutAcquisitionError) as excinfo:
        acquire_node(
            # "_" is kept by the batch-id sanitizer but the journal refuses
            # an identity that does not start with a letter or digit.
            _context(workpad, project_id, gig_id, "run_01", operation_key="_acquire-run_01"), _input(_config()),
            http_client=None, exa=_Exa(), ats=ats, watchlist=_Watchlist([_board("co0"), _board("co1")]), limits=_limits(),
            boards_from=BOARDS_FROM_FETCH,
        )

    assert excinfo.value.code == "acquisition_batch_invalid"
    assert ats.calls == [], "boards were fetched before the batch identity was checked"
    assert read_progress(workpad / "runs" / "run_01").steps["acquire"]["status"] == "failed"


def test_a_batch_already_bound_to_other_rows_fails_before_any_board_is_fetched(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path, "uat-bug-011-rebound")
    context = _context(workpad, project_id, gig_id, "run_01", operation_key="acquire-run_01")
    first = _CountingATS()
    acquire_node(
        context, _input(_config()), http_client=None, exa=_Exa(), ats=first,
        watchlist=_Watchlist([_board("co0")]), limits=_limits(),
        boards_from=BOARDS_FROM_FETCH,
    )
    assert first.calls == ["co0"]

    again = _CountingATS()
    with pytest.raises(ScoutAcquisitionError) as excinfo:
        acquire_node(
            context, _input(_config()), http_client=None, exa=_Exa(), ats=again,
            watchlist=_Watchlist([_board("co0")]), limits=_limits(),
            boards_from=BOARDS_FROM_FETCH,
        )

    assert excinfo.value.code == "acquisition_input_conflict"
    assert again.calls == [], "boards were fetched for a batch the journal had already bound"


def test_one_row_the_journal_would_refuse_costs_that_row_not_the_run(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path, "uat-bug-011-shape")
    good = [_row(0), _row(1), _row(2)]
    oversized = _row(3, title="Software Engineer " + "x" * 5000)

    out = acquire_node(
        _context(workpad, project_id, gig_id, "run_01", operation_key="acquire-run_01"),
        _input(_config(ats=False), (good[0], oversized, *good[1:])),
        http_client=None, exa=_Exa(), ats=ATSBoardClients(), watchlist=_Watchlist(()),
    )

    assert out.progress_status is ProgressStatus.COMPLETE
    assert [row.posting.normalized_url for row in out.rows] == [row.normalized_url for row in good]
    assert len(_journal_rows(workpad, "run_01")) == 3
    assert [(failure.code, failure.url) for failure in out.failures] == [("public_row_invalid", oversized.url)]
    assert out.not_imported_count == 0


# --- the count is additive ------------------------------------------------------


def test_an_acquire_output_sealed_before_the_count_round_trips_byte_identically() -> None:
    sealed_before = json.loads((FIXTURES / "fixture-acquire-batch-v1.json").read_text())
    assert "not_imported_count" not in sealed_before

    parsed = AcquireOutput.from_json(sealed_before)

    assert parsed.not_imported_count == 0
    assert canonical_json_bytes(parsed.to_json()) == canonical_json_bytes(sealed_before)

    counted = replace(parsed, not_imported_count=6926)
    assert counted.to_json() == {**parsed.to_json(), "not_imported_count": 6926}
    assert AcquireOutput.from_json(counted.to_json()) == counted
    for bad in (-1, True, "7", 1.5):
        with pytest.raises(Exception) as excinfo:
            AcquireOutput.from_json({**parsed.to_json(), "not_imported_count": bad})
        assert getattr(excinfo.value, "code", None) in {"wrong_type", "invalid_value"}


def test_progress_carries_the_count_and_older_progress_reads_as_zero(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run_01"
    assert read_progress(run_root).not_imported_count == 0

    writer = ProgressWriter(run_root)
    writer.cap_known(cap=10, candidate_count=500, not_imported_count=6926)
    snapshot = read_progress(run_root)
    assert (snapshot.cap, snapshot.candidate_count, snapshot.not_imported_count) == (10, 500, 6926)
    assert snapshot.to_json()["not_imported_count"] == 6926

    # A cap.json written before the key existed (an older run directory).
    (run_root / "progress" / "cap.json").write_text(json.dumps({"cap": 10, "candidate_count": 42}))
    older = read_progress(run_root)
    assert (older.cap, older.candidate_count, older.not_imported_count) == (10, 42, 0)
    # The keyword is optional: an existing caller writes a zero.
    writer.cap_known(cap=5, candidate_count=3)
    assert read_progress(run_root).not_imported_count == 0


def test_the_progress_route_payload_carries_the_count(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend

    class _Backend(ScoutFindJobsBackend):
        def _require_run(self, run_id: str):
            return SimpleNamespace(path=tmp_path)

        def _payload(self, run_id: str):
            return SimpleNamespace(rows=(), assessments=(), not_assessed=())

        def _sealed_selection_cap(self, run_id: str) -> int | None:
            return None

    backend = _Backend(home_root=tmp_path / "home", target=tmp_path)
    assert backend.run_progress("run_01")["not_imported_count"] == 0

    ProgressWriter(tmp_path / "runs" / "run_01").cap_known(cap=10, candidate_count=500, not_imported_count=6926)
    body = backend.run_progress("run_01")
    assert body["not_imported_count"] == 6926
    assert (body["cap"], body["candidate_count"]) == (10, 500)
