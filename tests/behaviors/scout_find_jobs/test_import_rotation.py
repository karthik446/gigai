"""The import cap rotates (orchestrator decision 2026-09-27, on top of uat-bug-011).

A run over more matching postings than ``IMPORT_ROW_CAP`` imports the best
500 and counts the rest. Without Jev scores "best" was "newest", so every
run imported the same 500 and the rest never got a turn. The rule now:
where the score does not separate two rows (the same score, or no scores at
all), the row no earlier run imported goes first, then the newest.

"Imported before" is what acquire already knows: the rows of this target's
earlier sealed batches (``_prior_observations``). No new record kind.

Two groups: ``rank_rows`` alone (pure), and three real runs through
``acquire_node`` with the REAL journal import over 1,200 rows.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    ATSProvider,
    FindJobsConfig,
    PostingRow,
    ProgressStatus,
    RowOutcome,
    SelectionRule,
    SourceKind,
    SourceToggles,
)
from gigai.scout.find_jobs.rank_contracts import RankScore
from gigai.scout.find_jobs.market_acquisition import IMPORT_ROW_CAP, acquire_node
from gigai.scout.find_jobs.selection import rank_rows, select_for_assessment
from tests.behaviors.scout_find_jobs.test_unchanged_reassess import _context, _managed_workpad

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class _Row:
    normalized_url: str
    published_at: str | None
    company: str = "Acme"
    title: str = "Software Engineer"
    location: str = "Remote, United States"


@dataclass(frozen=True)
class _Score:
    normalized_url: str
    score: int | None


def _rows(count: int) -> list[_Row]:
    # r0 is the newest, r<count-1> the oldest.
    return [_Row(f"https://jobs.example/r{n}", f"2026-09-{27 - n:02d}T00:00:00Z") for n in range(count)]


def _names(rows) -> list[str]:
    return [row.normalized_url.rsplit("/", 1)[-1] for row in rows]


# --- rank_rows (pure) --------------------------------------------------------


def test_without_scores_rows_never_imported_go_first_then_newest() -> None:
    rows = _rows(6)
    imported_before = {rows[0].normalized_url, rows[1].normalized_url, rows[4].normalized_url}

    ranked = rank_rows(rows, imported_before=imported_before)

    assert _names(ranked) == ["r2", "r3", "r5", "r0", "r1", "r4"]


def test_without_scores_and_nothing_imported_the_order_is_newest_first_as_before() -> None:
    rows = list(reversed(_rows(5)))

    assert _names(rank_rows(rows)) == ["r0", "r1", "r2", "r3", "r4"]
    assert _names(rank_rows(rows, imported_before=())) == ["r0", "r1", "r2", "r3", "r4"]
    # Every row imported before: no row has a turn to claim, newest first.
    assert _names(rank_rows(rows, imported_before={row.normalized_url for row in rows})) == ["r0", "r1", "r2", "r3", "r4"]


def test_with_scores_the_jev_order_stands_and_only_ties_rotate() -> None:
    rows = _rows(7)
    scores = [
        _Score(rows[0].normalized_url, 40),
        _Score(rows[1].normalized_url, 90),
        _Score(rows[2].normalized_url, 90),
        _Score(rows[3].normalized_url, 90),
        _Score(rows[4].normalized_url, 70),
        _Score(rows[5].normalized_url, None),  # never scored (past the cost cap)
    ]
    imported_before = {rows[1].normalized_url, rows[4].normalized_url, rows[5].normalized_url}

    ranked = rank_rows(rows, scores, imported_before=imported_before)

    # 90s first: r2 and r3 (never imported, newest first) ahead of r1
    # (imported before); then 70, then 40; the unscored rows last, the one
    # never imported (r6) ahead of the one that was (r5).
    assert _names(ranked) == ["r2", "r3", "r1", "r4", "r0", "r6", "r5"]
    # An imported row with the better score still beats a fresh one with a worse score.
    assert _names(ranked).index("r1") < _names(ranked).index("r0")
    # Without the rotation input the same scores rank exactly as before it existed.
    assert _names(rank_rows(rows, scores)) == ["r1", "r2", "r3", "r4", "r0", "r5", "r6"]


def test_the_assess_selection_does_not_rotate() -> None:
    # `select_for_assessment` takes no `imported_before`: acquire's selection
    # and assess's recompute must rank alike, from the sealed rows alone.
    rows = [_Row(f"https://jobs.example/c{n}", f"2026-09-{27 - n:02d}T00:00:00Z", company=f"Company {n}") for n in range(4)]

    selection = select_for_assessment(rows, cap=2)

    assert _names(selection.selected) == ["c0", "c1"]
    with pytest.raises(TypeError):
        select_for_assessment(rows, cap=2, imported_before=set())  # type: ignore[call-arg]


# --- three real runs over more rows than the cap --------------------------------

TOTAL = 1200


def _config() -> FindJobsConfig:
    config = FindJobsConfig.from_json(json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text()))
    return replace(config, sources=SourceToggles(exa=False, ats=False, hiringcafe=False), published_after=None, max_age_days=60)


def _posting(index: int, now: datetime) -> PostingRow:
    # index 0 is the newest posting, one minute apart.
    url = f"https://jobs.lever.co/co{index}/1"
    published = (now - timedelta(days=1, minutes=index)).isoformat().replace("+00:00", "Z")
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.LEVER, board_token=f"co{index}",
        company=f"Company {index}", title=f"Software Engineer {index}", location="Denver, CO",
        published_at=published, content_sha256="sha256:" + f"{index:064x}",
        source_kind=SourceKind.ATS, query_key="software engineer",
    )


class _Unused:
    def search(self, client, config, *, home_root=None):
        raise AssertionError("the rows are given; no source is asked")

    def add_to_watchlist(self, entry):
        return entry

    def active_entries(self):
        return ()


def _run(workpad: Path, project_id: str, gig_id: str, rows: tuple[PostingRow, ...], *, run_id: str):
    return acquire_node(
        _context(workpad, project_id, gig_id, run_id, operation_key=f"acquire-{run_id}"),
        AcquireInput(_config(), "sha256:" + "c" * 64, None, rows, 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH),
        http_client=None, exa=_Unused(), ats=_Unused(), watchlist=_Unused(),
    )


def _indexes(output) -> set[int]:
    return {int(row.posting.board_token.removeprefix("co")) for row in output.rows}


def test_three_runs_without_jev_import_disjoint_slices_first_then_the_newest(tmp_path: Path) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path, "import-rotation")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = tuple(_posting(index, now) for index in range(TOTAL))
    assert IMPORT_ROW_CAP == 500

    first = _run(workpad, project_id, gig_id, rows, run_id="run_01")
    second = _run(workpad, project_id, gig_id, rows, run_id="run_02")
    third = _run(workpad, project_id, gig_id, rows, run_id="run_03")

    for output in (first, second, third):
        assert output.progress_status is ProgressStatus.COMPLETE
        assert len(output.rows) == IMPORT_ROW_CAP and output.not_imported_count == TOTAL - IMPORT_ROW_CAP
        assert output.failures == ()
    # Run 1: nothing was imported before, so the newest 500.
    assert _indexes(first) == set(range(0, 500))
    # Run 2: the 700 never imported go first, newest of them first.
    assert _indexes(second) == set(range(500, 1000))
    assert not _indexes(first) & _indexes(second)
    # Run 3: the last 200 never imported, then the newest of the rest.
    assert _indexes(third) == set(range(1000, 1200)) | set(range(0, 300))
    # Every posting has been imported by now.
    assert _indexes(first) | _indexes(second) | _indexes(third) == set(range(TOTAL))

    # A row is NEW the first time it is imported, UNCHANGED when it comes round again.
    assert {row.outcome for row in first.rows} == {RowOutcome.NEW}
    assert {row.outcome for row in second.rows} == {RowOutcome.NEW}
    outcomes = {int(row.posting.board_token.removeprefix("co")): row.outcome for row in third.rows}
    assert {outcomes[index] for index in range(1000, 1200)} == {RowOutcome.NEW}
    assert {outcomes[index] for index in range(0, 300)} == {RowOutcome.UNCHANGED}

    # A fourth run: everything was imported before, so newest first again.
    fourth = _run(workpad, project_id, gig_id, rows, run_id="run_04")
    assert _indexes(fourth) == set(range(0, 500))


def test_with_jev_scores_the_best_fits_are_imported_and_ties_rotate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workpad, project_id, gig_id = _managed_workpad(tmp_path, "import-rotation-jev")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = tuple(_posting(index, now) for index in range(TOTAL))
    best = set(range(1100, 1200))  # the 100 oldest postings are the best fits
    tied = set(range(0, 1000))  # 1,000 postings share one middling score

    def fake_rank(candidates, **_kwargs):
        scores = []
        for row in candidates:
            index = int(row.board_token.removeprefix("co"))
            score = 95 if index in best else 50 if index in tied else 10
            scores.append(RankScore(
                normalized_url=row.normalized_url, content_sha256=row.content_sha256 or "sha256:" + "0" * 64,
                fit="strong" if score >= 70 else "no", score=score, reasons=(),
                mismatch_flags=(), hidden_by_default=False, cost_usd="0", cached=True,
            ))
        return tuple(scores)

    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition._rank_candidates", fake_rank)

    first = _run(workpad, project_id, gig_id, rows, run_id="run_01")
    second = _run(workpad, project_id, gig_id, rows, run_id="run_02")

    # Jev order stands in both runs: the 100 best fits are always imported,
    # the score-10 rows (1000..1099) never while better rows fill the cap.
    assert best <= _indexes(first) and best <= _indexes(second)
    assert not set(range(1000, 1100)) & (_indexes(first) | _indexes(second))
    # The 400 places left go to the tied rows: newest first in run 1, and in
    # run 2 the tied rows run 1 did not import, newest first.
    assert _indexes(first) - best == set(range(0, 400))
    assert _indexes(second) - best == set(range(400, 800))
