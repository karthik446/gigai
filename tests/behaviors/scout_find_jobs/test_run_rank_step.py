"""SCOPE-ADD-3 C1: the model ranking pass is a step of the run, before selection.

``acquire_node`` is called directly on a real journaled workpad that holds a
real resume record (``_assess_fixture``) and a sealed run input on disk; the
model is a scripted port behind the adapter seam every model caller shares
(``proposal_execution.resolve_model_adapter``). No live model, no Jev: a Jev
client that fails if it is ever built is installed for every test here.

Pinned:

* every posting that passed the filters is ranked (not only the new ones),
  with the run's own model target, before the import cap and the selection;
* the selection and the 500-row import cap take rows BY RANK: scored
  unblocked rows by score, then unscored, then blocked rows (demoted, never
  dropped); not by date;
* fail open: an unavailable model target or a pass cut short by its call
  cap leaves today's order and never blocks assess (the selection is made);
* ``runs/<run_id>/outputs/rank.json`` is committed to the journal in the
  ``RankResult.to_json()`` shape; ``progress/rank.jsonl`` has one line per
  landed batch, and ``read_progress`` shows "Ranked N of M" and the scores
  of the rows ranked so far WHILE the pass runs, ordered by rank;
* "Assessing X of Y" counts come from the selection and assess's lines.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading

import pytest

from gigai.journal import read_committed_artifact
from gigai.scout.find_jobs import jev_client, jev_rank, market_acquisition, rank_run
from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    AcquireOutput,
    ATSProvider,
    FindJobsConfig,
    ModelTarget,
    NodeContext,
    PostingRow,
    SelectionRule,
    SourceKind,
    SourceToggles,
)
from gigai.scout.find_jobs.market_acquisition import IMPORT_ROW_CAP, acquire_node
from gigai.scout.find_jobs.progress import ProgressWriter, read_progress
from tests.behaviors.scout_find_jobs.test_assess_model_policy import _assess_fixture
from tests.support.rank_fakes import RankPort, install_rank_port

FIXTURES = Path(__file__).parent / "fixtures"
RUN_ID = "run_00000000-0000-4000-8000-0000000000c1"


def _config() -> FindJobsConfig:
    config = FindJobsConfig.from_json(json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text()))
    return replace(config, sources=SourceToggles(exa=False, ats=False, hiringcafe=False), published_after=None, max_age_days=60)


def _posting(index: int, *, title: str = "Software Engineer", company: str | None = None) -> PostingRow:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    # A higher index is OLDER: date order is p0 first.
    published = (now - timedelta(days=1, minutes=index)).isoformat().replace("+00:00", "Z")
    url = f"https://jobs.lever.co/co{index}/1"
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.LEVER, board_token=f"co{index}",
        company=company or f"Company {index}", title=title, location="Denver, CO",
        published_at=published, content_sha256="sha256:" + f"{index:064x}",
        source_kind=SourceKind.ATS, query_key="software engineer", text=f"Work at company {index}.",
    )


def _company_number(line: str) -> int:
    return int(line.split(" @ Company ", 1)[1].split(" |", 1)[0])


class _Exa:
    def search(self, client, config, *, home_root=None):
        return ()


class _Watchlist:
    def add_to_watchlist(self, entry):
        return entry

    def active_entries(self):
        return ()


@pytest.fixture
def substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    fixture, target = _assess_fixture(tmp_path)
    monkeypatch.setenv(jev_client.JEV_API_KEY_ENV_VAR, "jv_test_key_never_used")

    def refuse(*_args, **_kwargs):
        raise AssertionError("the run path asked Jev")

    monkeypatch.setattr(jev_client.JevClient, "__init__", refuse)
    monkeypatch.setattr(jev_rank, "rank_postings_report", refuse)
    monkeypatch.setattr(market_acquisition, "_jev_http_client", refuse)
    return {**fixture, "target": target}


def _seal_run_input(substrate: dict, config: FindJobsConfig, run_id: str = RUN_ID) -> None:
    sealed_dir = substrate["resolved"].path / "runs" / run_id / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    (sealed_dir / "find-jobs-run-input.json").write_text(json.dumps({
        "schema_version": "scout-find-jobs-run-input:1",
        "config": config.to_json(),
        "config_digest": config.digest(),
        "selection_cap": 10,
        "selection_rule": "new_or_edited_role_match",
        "model_target": "ollama_local",
        "pinned_resume": substrate["pinned"].to_json(),
    }))


def _context(substrate: dict, *, model_target: ModelTarget = ModelTarget.OLLAMA_LOCAL, run_id: str = RUN_ID) -> NodeContext:
    resolved = substrate["resolved"]
    return NodeContext(
        run_id=run_id, project_id=resolved.project_id, gig_id=substrate["gig_id"],
        graph_id="find-jobs:functional", graph_version=1, goal_slug="acquire",
        manifest_digest="sha256:" + "a" * 64, operation_key=f"acquire-{run_id}",
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(resolved.path), redeemed_consent_ref="consent",
        model_target=model_target,
    )


def _acquire(substrate: dict, rows: list[PostingRow], *, cap: int = 10, run_id: str = RUN_ID,
             model_target: ModelTarget = ModelTarget.OLLAMA_LOCAL) -> AcquireOutput:
    config = _config()
    _seal_run_input(substrate, config, run_id)
    return acquire_node(
        _context(substrate, model_target=model_target, run_id=run_id),
        AcquireInput(config, "sha256:" + "c" * 64, None, tuple(rows), cap, SelectionRule.NEW_OR_EDITED_ROLE_MATCH),
        http_client=None, exa=_Exa(), ats=ATSBoardClients(), watchlist=_Watchlist(),
        home_root=substrate["home"], target=substrate["target"],
    )


def _run_dir(substrate: dict, run_id: str = RUN_ID) -> Path:
    return substrate["resolved"].path / "runs" / run_id


def _urls(output: AcquireOutput) -> list[str]:
    return [item.normalized_url for item in output.selected_postings]


# --- the step, before selection ------------------------------------------------------------


def test_every_filtered_posting_is_ranked_and_the_selection_takes_them_by_rank(substrate: dict, monkeypatch) -> None:
    # Company 5 is the best fit, then 4 ... then 0 (the newest is the worst).
    port = RankPort(lambda line: (50 + 10 * _company_number(line), []))
    asked = install_rank_port(monkeypatch, port)
    rows = [_posting(n) for n in range(6)]

    output = _acquire(substrate, rows, cap=3)

    assert asked == ["ollama-default"]  # the run's own model target, the assess seam
    assert len(port.asked) == 6  # every row that passed the filters, one call
    assert {item.normalized_url: item.score for item in output.rank_scores} == {
        row.normalized_url: 50 + 10 * n for n, row in enumerate(rows)
    }
    # By rank, not by date: date order would have picked companies 0, 1, 2.
    assert set(_urls(output)) == {rows[5].normalized_url, rows[4].normalized_url, rows[3].normalized_url}


def test_a_blocked_posting_is_demoted_below_unscored_ones_never_dropped(substrate: dict, monkeypatch) -> None:
    # Company 0 scores highest but names a blocker; companies 1..3 do not.
    def score_for(line: str) -> tuple[int, list[str]]:
        number = _company_number(line)
        return (99, ["no_sponsor"]) if number == 0 else (40 + number, [])

    install_rank_port(monkeypatch, RankPort(score_for))
    rows = [_posting(n) for n in range(4)]

    capped = _acquire(substrate, rows, cap=3)
    everything = _acquire(substrate, rows, cap=10, run_id="run_00000000-0000-4000-8000-0000000000c2")

    blocked = rows[0].normalized_url
    assert blocked not in _urls(capped)  # demoted below every unblocked row ...
    assert blocked in _urls(everything)  # ... never dropped
    [sealed] = [item for item in capped.rank_scores if item.normalized_url == blocked]
    assert sealed.score == 99 and sealed.mismatch_flags == ("no_sponsor",) and sealed.hidden_by_default is False


def test_the_import_cap_takes_the_best_ranked_rows_not_the_newest(substrate: dict, monkeypatch) -> None:
    total = IMPORT_ROW_CAP + 12
    # The 12 OLDEST postings are the best fits; the 12 newest the worst.
    port = RankPort(lambda line: (95 if _company_number(line) >= IMPORT_ROW_CAP else 30 if _company_number(line) >= 12 else 5, []))
    install_rank_port(monkeypatch, port)
    rows = [_posting(n) for n in range(total)]

    output = _acquire(substrate, rows, cap=5)

    imported = {row.posting.normalized_url for row in output.rows}
    assert len(imported) == IMPORT_ROW_CAP and output.not_imported_count == 12
    assert {row.normalized_url for row in rows[IMPORT_ROW_CAP:]} <= imported  # the oldest, best ranked
    assert not {row.normalized_url for row in rows[:12]} & imported  # the newest, worst ranked
    assert set(_urls(output)) <= {row.normalized_url for row in rows[IMPORT_ROW_CAP:]}
    assert len(port.asked) == total  # ranked BEFORE the cap: every row


# --- fail open -----------------------------------------------------------------------------


def test_an_unavailable_model_target_fails_open_to_date_order_and_assess_still_runs(substrate: dict, monkeypatch) -> None:
    port = RankPort(lambda _line: (90, []))
    install_rank_port(monkeypatch, port)
    rows = [_posting(n) for n in range(4)]

    # No codex_cli target is configured in this home.
    output = _acquire(substrate, rows, cap=2, model_target=ModelTarget.CODEX_CLI)

    assert port.prompts == []
    assert output.rank_scores == ()
    assert _urls(output) == [rows[0].normalized_url, rows[1].normalized_url]  # newest first, as before
    status = read_progress(_run_dir(substrate)).rank_status
    assert status is not None and status["status"] == "skipped"
    assert status["reason"].startswith("model_target_unavailable")


def test_a_pass_cut_short_by_its_call_cap_keeps_the_scored_rows_first(substrate: dict, monkeypatch) -> None:
    port = RankPort(lambda line: (10 + _company_number(line) % 50, []))
    install_rank_port(monkeypatch, port)
    monkeypatch.setattr(rank_run, "run_call_cap", lambda total, **_kwargs: 1)
    monkeypatch.setattr(rank_run, "run_concurrency", lambda cpus=None: 1)
    rows = [_posting(n) for n in range(120)]

    output = _acquire(substrate, rows, cap=5)

    assert len(port.prompts) == 1
    scored = [item for item in output.rank_scores if item.score is not None]
    assert len(scored) == 50 and len(output.rank_scores) == 120
    status = read_progress(_run_dir(substrate)).rank_status
    assert status["status"] == "scored" and status["reason"].startswith("call_budget: 70 of 120 postings unscored")
    assert set(_urls(output)) <= {item.normalized_url for item in scored}
    assert len(_urls(output)) == 5  # assess still gets its selection


def test_a_model_that_never_answers_validly_fails_open_and_assess_still_runs(substrate: dict, monkeypatch) -> None:
    install_rank_port(monkeypatch, RankPort(lambda _line: (90, []), fail=True))
    rows = [_posting(n) for n in range(3)]

    output = _acquire(substrate, rows, cap=2)

    assert all(item.score is None for item in output.rank_scores)
    assert _urls(output) == [rows[0].normalized_url, rows[1].normalized_url]


# --- the sealed rank.json and the streamed batches -----------------------------------------


def test_rank_json_is_committed_in_the_rank_result_shape(substrate: dict, monkeypatch) -> None:
    install_rank_port(monkeypatch, RankPort(lambda line: (70, ["onsite"] if _company_number(line) == 1 else [])))
    rows = [_posting(n) for n in range(3)]

    _acquire(substrate, rows)

    resolved = substrate["resolved"]
    raw, _commit = read_committed_artifact(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        path=f"runs/{RUN_ID}/outputs/rank.json",
    )
    sealed = json.loads(raw)
    assert sealed["schema_version"] == "scout-rank:1" and sealed["run_id"] == RUN_ID and sealed["kind"] == "run"
    assert (sealed["prompt_version"], sealed["digest_version"]) == ("rank-v1", "digest-v2")
    assert (sealed["model_target"], sealed["configured_target"], sealed["resolved_model"]) == (
        "ollama_local", "ollama-default", "fake-rank-model")
    assert {"effort", "effort_applied", "batch_size", "concurrency", "max_calls", "status", "fail_open_reason"} <= set(sealed)
    assert sealed["status"] == "complete" and sealed["fail_open_reason"] is None and sealed["batch_size"] == 50
    assert sealed["totals"]["postings"] == 3 and sealed["totals"]["calls"] == 1 and sealed["totals"]["demoted"] == 1
    [batch] = sealed["batches"]
    assert batch["batch_id"] == "b000" and batch["valid"] is True and batch["attempts"] == 1
    by_url = {item["normalized_url"]: item for item in sealed["postings"]}
    demoted = by_url[rows[1].normalized_url]
    assert demoted["score"] == 70 and demoted["blockers"] == ["onsite"] and demoted["demoted"] is True
    assert demoted["reasons"] == ["fits the stack"] and demoted["batch_id"] == "b000"


def test_batches_stream_to_rank_jsonl_and_progress_reads_them_mid_pass(substrate: dict, monkeypatch) -> None:
    port = RankPort(lambda line: (100 - _company_number(line) % 100, []), hold_after_first=True)
    install_rank_port(monkeypatch, port)
    rows = [_posting(n) for n in range(120)]
    run_dir = _run_dir(substrate)
    outputs: list[AcquireOutput] = []
    worker = threading.Thread(target=lambda: outputs.append(_acquire(substrate, rows, cap=3)))
    worker.start()
    try:
        assert port.first_answered.wait(30)
        # The first batch lands on the caller's thread right after its answer.
        for _ in range(200):
            snapshot = read_progress(run_dir)
            if snapshot.rank is not None and snapshot.rank["ranked"] == 50:
                break
            threading.Event().wait(0.05)
        assert snapshot.rank is not None
        assert snapshot.rank["status"] == "running"
        assert (snapshot.rank["ranked"], snapshot.rank["total"]) == (50, 120)
        assert snapshot.rank["text"] == "Ranked 50 of 120"
        # GET /progress passes rank_status through: the live counts are on it.
        assert snapshot.rank_status is not None and snapshot.rank_status["status"] == "running"
        assert (snapshot.rank_status["ranked"], snapshot.rank_status["total"]) == (50, 120)
        assert snapshot.rank_status["text"] == "Ranked 50 of 120"
        ranked = [item for item in snapshot.postings if item.get("rank") is not None]
        assert len(ranked) == 50 and all(isinstance(item["rank"]["score"], int) for item in ranked)
        # Ranked rows first, by score; the rows not ranked yet after them.
        assert snapshot.postings[:50] == sorted(ranked, key=lambda item: -item["rank"]["score"])
        assert all(item.get("rank") is None for item in snapshot.postings[50:])
        lines = [json.loads(line) for line in (run_dir / "progress" / "rank.jsonl").read_text().splitlines()]
        assert [line["event"] for line in lines] == ["started", "batch"]
        assert lines[1]["ranked"] == 50 and len(lines[1]["postings"]) == 50
    finally:
        port.gate.set()
        worker.join(60)
    snapshot = read_progress(run_dir)
    assert snapshot.rank["status"] == "complete" and snapshot.rank["ranked"] == 120
    assert snapshot.rank_status["text"] == "scored 120 of 120"
    assert outputs and len(outputs[0].selected_postings) == 3


def test_assessing_x_of_y_comes_from_the_selection_and_assess_lines(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / RUN_ID
    progress = ProgressWriter(run_root)
    progress.cap_known(cap=10, candidate_count=40, selected_count=10)
    assert read_progress(run_root).assess_counts["text"] == "10 to assess"

    progress.start_step("assess")
    for index in range(3):
        progress.assessment_started(f"u{index}")
    progress.assessment_finished("u0", ok=True)
    progress.assessment_finished("u1", ok=False, reason="model_output_invalid")
    counts = read_progress(run_root).assess_counts
    assert counts["text"] == "Assessing 3 of 10"
    assert (counts["selected"], counts["started"], counts["finished"], counts["failed"], counts["in_flight"]) == (10, 3, 2, 1, 1)

    progress.finish_step("assess", ok=True)
    assert read_progress(run_root).assess_counts["text"] == "Assessed 2 of 10"
