"""uat-bug-031: a run over the 500 import cap fills the grid while its ranking pass runs.

The operator's real run matched 1,458 postings. Before this fix, acquire
wrote those runs' ``/progress`` posting lines only after the ranking pass
and the import cap, so the grid stayed empty and only "Ranked N of 1,458"
moved. Now each posting's line is appended once, when its rank batch lands:
``read_progress`` serves the ranked rows by score while the pass runs, and
once the cap has picked the run's rows (``progress/imported.json``) it serves
exactly the top 500 by rank, in the same order as before.

``acquire_node`` runs on a real journaled workpad with a scripted rank port
(the shared adapter seam, no live model), as in ``test_run_rank_step``.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading

from gigai.adapters.port import InvocationRequest, InvocationResult, NormalizedUsage
from gigai.scout.find_jobs.contracts import AcquireOutput, ModelTarget
from gigai.scout.find_jobs.market_acquisition import IMPORT_ROW_CAP
from gigai.scout.find_jobs.progress import ProgressWriter, rank_tier, read_progress
from tests.behaviors.scout_find_jobs.test_run_rank_step import (  # noqa: F401 - `substrate` is a fixture
    _acquire,
    _company_number,
    _posting,
    _run_dir,
    substrate,
)
from tests.support.rank_fakes import batch_lines, install_rank_port

MATCHED = 1_458  # the operator's real run


def _score(line: str) -> int:
    # Spread over 0-99 so each landed batch interleaves with the ones before it.
    return (_company_number(line) * 37) % 100


class _StagedPort:
    """Answers the first call at once; every later call waits for a token the test releases."""

    def __init__(self) -> None:
        self.calls = 0
        self.tokens = threading.Semaphore(0)
        self._lock = threading.Lock()

    def invoke(self, request: InvocationRequest) -> InvocationResult:
        with self._lock:
            self.calls += 1
            first = self.calls == 1
        if not first:
            assert self.tokens.acquire(timeout=60), "the test never released this batch"
        items = [
            {"posting_id": pid, "score": _score(rest), "reasons": ["fits the stack"], "blockers": []}
            for pid, rest in batch_lines(request.prompt)
        ]
        return InvocationResult("success", json.dumps(items), "fake-rank-model", {}, NormalizedUsage(100, 10, 110), "unavailable")


def _wait_for_ranked(run_dir: Path, ranked: int):
    snapshot = read_progress(run_dir)
    for _ in range(400):
        if snapshot.rank is not None and snapshot.rank["ranked"] >= ranked:
            return snapshot
        threading.Event().wait(0.05)
        snapshot = read_progress(run_dir)
    raise AssertionError(f"never reached {ranked} ranked: {snapshot.rank}")


def _urls(postings: list[dict]) -> list[str]:
    return [item["normalized_url"] for item in postings]


def _acquire_lines(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "progress" / "acquire.jsonl").read_text().splitlines()]


def test_an_over_cap_run_fills_the_grid_by_rank_while_it_ranks_then_keeps_the_top_500(substrate: dict, monkeypatch) -> None:
    port = _StagedPort()
    install_rank_port(monkeypatch, port)
    rows = [_posting(n) for n in range(MATCHED)]
    run_dir = _run_dir(substrate)
    outputs: list[AcquireOutput] = []
    worker = threading.Thread(target=lambda: outputs.append(_acquire(substrate, rows, cap=5)))
    worker.start()
    try:
        # The first batch lands: the grid shows its 50 postings, ranked, best first.
        first = _wait_for_ranked(run_dir, 50)
        assert (first.rank["ranked"], first.rank["total"]) == (50, MATCHED)
        assert len(first.postings) == 50, "the grid is empty while the pass runs"
        assert all(isinstance(item["rank"]["score"], int) for item in first.postings)
        scores = [item["rank"]["score"] for item in first.postings]
        assert scores == sorted(scores, reverse=True)
        assert not (run_dir / "progress" / "imported.json").exists()

        # One more batch: the list grows and re-orders (new rows join above old ones).
        port.tokens.release()
        second = _wait_for_ranked(run_dir, 100)
        assert len(second.postings) == 100
        scores = [item["rank"]["score"] for item in second.postings]
        assert scores == sorted(scores, reverse=True)
        old = set(_urls(first.postings))
        assert [url for url in _urls(second.postings) if url in old] == _urls(first.postings)
        assert _urls(second.postings) != _urls(first.postings) + [u for u in _urls(second.postings) if u not in old]
    finally:
        for _ in range(MATCHED):
            port.tokens.release()
        worker.join(180)
    assert outputs, "acquire did not finish"
    output = outputs[0]

    final = read_progress(run_dir)
    sealed = [row.posting.normalized_url for row in output.rows]
    # The final set is the imported top 500 BY RANK, as before this fix.
    assert len(sealed) == IMPORT_ROW_CAP and output.not_imported_count == MATCHED - IMPORT_ROW_CAP
    assert set(_urls(final.postings)) == set(sealed)
    score_by_url = {row.normalized_url: (index * 37) % 100 for index, row in enumerate(rows)}  # `_score` of Company <index>
    left_out = set(score_by_url) - set(sealed)
    assert min(score_by_url[url] for url in sealed) >= max(score_by_url[url] for url in left_out)
    # ... in the order today's final read gives: rank tier, then the sealed rows' order.
    by_url = {item["normalized_url"]: item for item in final.postings}
    expected = sorted(range(len(sealed)), key=lambda index: (rank_tier(by_url[sealed[index]]["rank"]), index))
    assert _urls(final.postings) == [sealed[index] for index in expected]
    # The counts match.
    assert (final.rank["ranked"], final.rank["total"], final.rank["status"]) == (MATCHED, MATCHED, "complete")
    assert final.not_imported_count == MATCHED - IMPORT_ROW_CAP
    assert final.candidate_count == IMPORT_ROW_CAP
    # Bounded: each matched posting's line is written at most once.
    lines = _acquire_lines(run_dir)
    assert len(lines) <= MATCHED and len({line["normalized_url"] for line in lines}) == len(lines)
    imported = json.loads((run_dir / "progress" / "imported.json").read_text())
    assert imported == {"cap": IMPORT_ROW_CAP, "normalized_urls": sealed}


def test_an_over_cap_run_whose_pass_fails_open_still_shows_its_imported_rows(substrate: dict) -> None:
    rows = [_posting(n) for n in range(IMPORT_ROW_CAP + 12)]
    run_dir = _run_dir(substrate)

    # No codex_cli target is configured in this home: the pass is skipped, no batch lands.
    output = _acquire(substrate, rows, cap=2, model_target=ModelTarget.CODEX_CLI)

    snapshot = read_progress(run_dir)
    assert snapshot.rank_status is not None and snapshot.rank_status["status"] == "skipped"
    sealed = [row.posting.normalized_url for row in output.rows]
    assert _urls(snapshot.postings) == sealed == [row.normalized_url for row in rows[:IMPORT_ROW_CAP]]
    assert len(_acquire_lines(run_dir)) == IMPORT_ROW_CAP  # only the imported rows' lines


def test_read_progress_serves_only_the_imported_rows_once_the_cap_has_picked_them(tmp_path: Path) -> None:
    writer = ProgressWriter(tmp_path)
    writer.postings_acquired([({"normalized_url": f"u{n}", "title": f"t{n}"}, "new") for n in range(4)])
    assert _urls(read_progress(tmp_path).postings) == ["u0", "u1", "u2", "u3"]

    writer.import_capped(cap=2, normalized_urls=["u3", "u1", "u9"])  # u9: no line, skipped
    snapshot = read_progress(tmp_path)
    assert _urls(snapshot.postings) == ["u3", "u1"]
    assert snapshot.postings[0] == {"normalized_url": "u3", "title": "t3", "outcome": "new"}


def test_an_unreadable_imported_file_leaves_the_lines_as_they_are(tmp_path: Path) -> None:
    writer = ProgressWriter(tmp_path)
    writer.postings_acquired([({"normalized_url": "u0"}, "new"), ({"normalized_url": "u1"}, "edited")])
    (tmp_path / "progress" / "imported.json").write_text(json.dumps({"normalized_urls": "not a list"}))
    assert _urls(read_progress(tmp_path).postings) == ["u0", "u1"]
    (tmp_path / "progress" / "imported.json").write_text(json.dumps({"normalized_urls": [["u0"], "u1"]}))
    assert _urls(read_progress(tmp_path).postings) == ["u1"]
