"""uat-bug-021: a run that gets no Jev scores says why.

Operator evidence (UAT 2026-09-28, build a0c5b75): a search over 10,336
indexed companies matched 1,458 postings, imported 500, and every card
showed "– Jev"; ``outputs/acquire.json`` had no ``rank_scores`` and nothing
anywhere said why.

Root cause (EXECUTED, see ``test_a_catalog_size_search_with_a_key_and_a_resume_is_scored``,
which fails on a0c5b75): ``market_acquisition._read_resume_text_for_rank``
imported ``gigai.scout.private_records``, a module that does not exist, so
it raised ``ModuleNotFoundError`` on every call; it also named the workpad
as the target of the lookup, and so did the Jev cache lookup. Each was
swallowed by a bare ``except``.

What these tests pin, all with ``acquire_node`` called directly on a real
journaled workpad that holds a real resume record (``_assess_fixture``), a
sealed run input on disk, and a fake Jev transport that charges what Jev
charges (about $0.00048 a call, P6's live measurement):

* each reason a pass can be skipped for is recorded on the run
  (``progress/rank.json``, read back by ``read_progress``), logged once at
  WARNING, and is the run's progress line;
* a pass that scores says ``scored N of M`` and what it cost at INFO, and
  the scores are on the sealed output;
* the operator's run at its own size is scored up to the cost cap;
* which candidates are asked, and in which order;
* the day's budget holds across runs, and the ledger names no posting;
* ``jev_rank.rank_postings_report``: the early stop, the retry and the
  slowdown after a busy answer, the calls made side by side, one cache for
  every project keyed by the resume's text, the fit label from the score.

A run asks Jev 8 postings at a time (``jev_rank.RUN_CONCURRENCY``), so the
cap and the budget are checked every 8 calls.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import threading
import time

import httpx
import pytest

from gigai.scout.find_jobs import jev_rank, market_acquisition
from gigai.scout.find_jobs.api import rank as rank_api
from gigai.scout.find_jobs.ats_board_clients import ATSBoardClients
from gigai.scout.find_jobs.company_index import CompanyIndex
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
    WatchlistEntry,
    WatchlistFirstSeen,
)
from gigai.scout.find_jobs.jev_client import JEV_API_KEY_ENV_VAR, JevClient
from gigai.scout.find_jobs.market_acquisition import (
    BOARDS_FROM_INDEX,
    IMPORT_ROW_CAP,
    AcquireLimits,
    acquire_node,
)
from gigai.scout.find_jobs.progress import read_progress
from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources
from tests.behaviors.scout_find_jobs.test_assess_model_policy import _assess_fixture

FIXTURES = Path(__file__).parent / "fixtures"
LOGGER = "gigai.scout.server"
# What one Jev call costs (P6 live acceptance: 24 calls, $0.011518).
JEV_CALL_USD = 0.00048
RUN_ID = "run_00000000-0000-4000-8000-000000000021"
RESUME_TEXT = "Built and operated Python backend services for 6 years."


# --- the substrate ------------------------------------------------------------------


def _config(*, ats: bool = False) -> FindJobsConfig:
    config = FindJobsConfig.from_json(json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text()))
    return replace(config, sources=SourceToggles(exa=False, ats=ats, hiringcafe=False), published_after=None, max_age_days=60)


def _posting(index: int, *, title: str = "Software Engineer", company: str | None = None, age_minutes: int | None = None) -> PostingRow:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    published = (now - timedelta(days=1, minutes=index if age_minutes is None else age_minutes)).isoformat().replace("+00:00", "Z")
    url = f"https://jobs.lever.co/co{index}/1"
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.LEVER, board_token=f"co{index}",
        company=company or f"Company {index}", title=title, location="Denver, CO",
        published_at=published, content_sha256="sha256:" + f"{index:064x}",
        source_kind=SourceKind.ATS, query_key="software engineer", text=f"Work at company {index}.",
    )


class _Jev:
    """A fake Jev: it answers every posting, charges per call, and keeps what it was asked."""

    def __init__(self, *, status: int = 200, cost_usd: float = JEV_CALL_USD, score_for=None) -> None:
        self.status = status
        self.cost_usd = cost_usd
        self.score_for = score_for
        self.asked: list[dict[str, object]] = []
        self.bodies: list[str] = []
        self.authorization: list[str] = []
        self._lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        posting = json.loads(body)["state"]["posting"]
        with self._lock:
            self.asked.append(posting)
            self.bodies.append(body)
            self.authorization.append(request.headers.get("authorization", ""))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "refused"}, request=request)
        level = self.score_for(posting) if self.score_for is not None else 8
        return httpx.Response(
            200,
            json={
                "model": "jev-test",
                "answers": {
                    "fit": {"choice": "strong" if level >= 6 else "no"},
                    "score": {"score": level},
                    "top_reason": {"choice": "stack_match"},
                    "flag_domain": {"noul": 0.0},
                    "flag_seniority": {"noul": 0.0},
                    "flag_stack": {"noul": 0.0},
                    "flag_location": {"noul": 0.0},
                    "flag_sponsorship": {"noul": 0.0},
                },
                "usage": {"cost_usd": self.cost_usd},
            },
            request=request,
        )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class _Exa:
    def search(self, client, config, *, home_root=None):
        return ()


class _Watchlist:
    def __init__(self, boards=()) -> None:
        self.boards = list(boards)

    def add_to_watchlist(self, entry):
        return entry

    def active_entries(self):
        return tuple(self.boards)


@pytest.fixture
def substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A journaled workpad with a stored resume, a Jev key, and no test seam."""

    fixture, target = _assess_fixture(tmp_path)
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "jv_test_key_never_logged")
    monkeypatch.delenv("GIGAI_JEV_COST_CAP_USD", raising=False)
    monkeypatch.delenv("GIGAI_JEV_DAILY_BUDGET_USD", raising=False)
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_JEV", raising=False)
    monkeypatch.setattr(jev_rank, "_RETRY_PAUSE_SECONDS", (0.0, 0.0), raising=False)
    return {**fixture, "target": target}


def _expected(status: str, scored: int, total: int, *, line: str, reason: str | None = None, cost: float = 0.0,
              cap: str | None = "0.25", spent: float | None = None, budget: str = "0.50", throttled: str | None = None) -> dict:
    """``RankStatus.to_json()`` as a run records it; ``spent`` defaults to what the pass cost."""

    spent = cost if spent is None else spent
    text = f"scored {scored:,} of {total:,}" if status == "scored" else f"skipped: {reason}"
    shown = "0.00" if spent <= 0 else f"{spent:.2f}" if spent >= 0.01 else f"{spent:.4f}".rstrip("0")
    return {
        "status": status, "scored": scored, "total": total, "reason": reason,
        "cost_cap_usd": cap, "cost_usd": f"{cost:.6f}", "throttled": throttled,
        "spent_today_usd": f"{spent:.6f}", "daily_budget_usd": budget,
        "text": text, "line": line, "usage_line": f"Jev: ${shown} of ${budget} today",
    }


@pytest.fixture
def log(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> pytest.LogCaptureFixture:
    # A server started earlier in this process turns propagation off.
    monkeypatch.setattr(logging.getLogger(LOGGER), "propagate", True)
    caplog.set_level(logging.INFO, logger=LOGGER)
    return caplog


def _seal_run_input(substrate: dict, config: FindJobsConfig, *, pinned: dict | None = None, run_id: str = RUN_ID) -> None:
    sealed_dir = substrate["resolved"].path / "runs" / run_id / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    (sealed_dir / "find-jobs-run-input.json").write_text(
        json.dumps({
            "schema_version": "scout-find-jobs-run-input:1",
            "config": config.to_json(),
            "config_digest": config.digest(),
            "selection_cap": 10,
            "selection_rule": "new_or_edited_role_match",
            "model_target": "ollama_local",
            "pinned_resume": pinned if pinned is not None else substrate["pinned"].to_json(),
        })
    )


def _context(substrate: dict, run_id: str = RUN_ID) -> NodeContext:
    resolved = substrate["resolved"]
    return NodeContext(
        run_id=run_id, project_id=resolved.project_id, gig_id=substrate["gig_id"],
        graph_id="find-jobs:functional", graph_version=1, goal_slug="acquire",
        manifest_digest="sha256:" + "a" * 64, operation_key=f"acquire-{run_id}",
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(resolved.path), redeemed_consent_ref="consent",
        model_target=ModelTarget.OLLAMA_LOCAL,
    )


def _acquire(
    substrate: dict,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[PostingRow],
    *,
    jev: _Jev | None = None,
    sealed: bool = True,
    pinned: dict | None = None,
    cap: int = 10,
    run_id: str = RUN_ID,
) -> tuple[AcquireOutput, dict | None]:
    """One acquire pass over ``rows``; returns the output and the recorded rank_status."""

    config = _config()
    if sealed:
        _seal_run_input(substrate, config, pinned=pinned, run_id=run_id)
    if jev is not None:
        monkeypatch.setattr(market_acquisition, "_jev_http_client", jev.client)
    output = acquire_node(
        _context(substrate, run_id),
        AcquireInput(config, "sha256:" + "c" * 64, None, tuple(rows), cap, SelectionRule.NEW_OR_EDITED_ROLE_MATCH),
        http_client=None, exa=_Exa(), ats=ATSBoardClients(), watchlist=_Watchlist(),
        home_root=substrate["home"], target=substrate["target"],
    )
    return output, read_progress(substrate["resolved"].path / "runs" / run_id).rank_status


def _status_lines(log: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in log.records if record.name == LOGGER and record.getMessage().startswith("scout acquire: Jev: ")]


def _assert_skipped(
    output: AcquireOutput, status: dict | None, log: pytest.LogCaptureFixture, *, reason: str, line: str, total: int
) -> None:
    assert output.rank_scores == ()
    assert "rank_scores" not in output.to_json()
    assert status is not None, "nothing was recorded on the run"
    assert status["status"] == "skipped" and status["reason"] == reason
    assert status["text"] == f"skipped: {reason}"
    assert status["line"] == line
    assert status["scored"] == 0 and status["total"] == total
    records = _status_lines(log)
    assert len(records) == 1, [record.getMessage() for record in log.records]
    assert records[0].levelno == logging.WARNING
    assert line in records[0].getMessage() and f"reason={reason}" in records[0].getMessage()
    assert f"run_id={RUN_ID}" in records[0].getMessage()


def _assert_nothing_private_was_logged(log: pytest.LogCaptureFixture) -> None:
    text = "\n".join(record.getMessage() for record in log.records)
    assert "jv_test_key_never_logged" not in text
    assert RESUME_TEXT not in text and "Python backend services" not in text


# --- one test per reason a pass is skipped for -------------------------------------------


def test_no_key_is_recorded_logged_and_shown(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    monkeypatch.delenv(JEV_API_KEY_ENV_VAR, raising=False)
    jev = _Jev()

    output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(3)], jev=jev)

    assert jev.asked == []
    _assert_skipped(output, status, log, reason="no_key", line="Jev: skipped (no Jev key)", total=3)
    # The run itself is untouched: every posting is imported and selected by date.
    assert len(output.rows) == 3 and len(output.selected_postings) == 3


def test_no_sealed_run_input_is_recorded_logged_and_shown(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    jev = _Jev()

    output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(3)], jev=jev, sealed=False)

    assert jev.asked == []
    _assert_skipped(
        output, status, log, reason="no_run_input", line="Jev: skipped (the run's input could not be read)", total=3
    )


def test_a_resume_that_cannot_be_read_is_recorded_logged_and_shown(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    jev = _Jev()
    gone = {**substrate["pinned"].to_json(), "record_id": "record_00000000-0000-4000-8000-00000000dead"}

    output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(3)], jev=jev, pinned=gone)

    assert jev.asked == []
    _assert_skipped(output, status, log, reason="no_resume", line="Jev: skipped (no resume)", total=3)
    # Why the resume could not be read is in the log, as the exception's type.
    why = [record for record in log.records if "the run's resume could not be read: " in record.getMessage()]
    assert len(why) == 1 and why[0].levelno == logging.WARNING
    assert why[0].getMessage().rsplit(": ", 1)[1].endswith("Error")
    _assert_nothing_private_was_logged(log)


def test_a_jev_error_is_recorded_with_its_code_and_stops_the_pass(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    jev = _Jev(status=402)  # insufficient credits: every later call gets the same answer

    output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(40)], jev=jev)

    assert len(jev.asked) == 8, "a refused key or account was asked again after the first 8 calls"
    assert status is not None and status["status"] == "skipped"
    assert status["reason"] == "jev_error:jev_http_402" and status["text"] == "skipped: jev_error:jev_http_402"
    assert status["line"] == "Jev: skipped (Jev error: jev_http_402)"
    assert status["scored"] == 0 and status["total"] == 40
    # One entry per candidate, none scored: the run goes on in date order.
    assert len(output.rank_scores) == 40 and all(item.score is None for item in output.rank_scores)
    assert len(output.rows) == 40 and len(output.selected_postings) == 10
    records = _status_lines(log)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "Jev: skipped (Jev error: jev_http_402)" in records[0].getMessage()
    _assert_nothing_private_was_logged(log)


def test_a_jev_that_keeps_failing_is_not_asked_about_every_posting(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    jev = _Jev(status=502)

    _output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(40)], jev=jev)

    # The first 8 postings, each asked three times (two retries); then no more.
    assert len(jev.asked) == 24 and len({posting["company"] for posting in jev.asked}) == 8
    assert status is not None and status["reason"] == "jev_error:jev_http_502" and status["status"] == "skipped"
    assert status["throttled"] == "8 -> 4 after 502"
    assert status["line"] == "Jev: skipped (Jev error: jev_http_502, throttled: 8 -> 4 after 502)"


def test_any_other_exception_is_recorded_with_its_type(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    def broken() -> httpx.Client:
        raise RuntimeError("the transport could not be built: jv_test_key_never_logged")

    config = _config()
    _seal_run_input(substrate, config)
    monkeypatch.setattr(market_acquisition, "_jev_http_client", broken)
    output = acquire_node(
        _context(substrate),
        AcquireInput(config, "sha256:" + "c" * 64, None, tuple(_posting(n) for n in range(3)), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH),
        http_client=None, exa=_Exa(), ats=ATSBoardClients(), watchlist=_Watchlist(),
        home_root=substrate["home"], target=substrate["target"],
    )
    status = read_progress(substrate["resolved"].path / "runs" / RUN_ID).rank_status

    _assert_skipped(output, status, log, reason="error:RuntimeError", line="Jev: skipped (error: RuntimeError)", total=3)
    _assert_nothing_private_was_logged(log)


def test_the_cost_cap_is_recorded_with_how_many_were_scored_before_it(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    monkeypatch.setenv("GIGAI_JEV_COST_CAP_USD", "0.005")
    jev = _Jev()

    output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(20)], jev=jev)

    # 0.00048 a call, 8 at a time: 8 calls cost $0.0038, the next 8 pass $0.005.
    assert len(jev.asked) == 16
    line = "Jev: scored 16 of 20 (cost $0.0077, cost cap $0.005)"
    assert status == _expected("scored", 16, 20, reason="cost_cap_reached", cost=16 * JEV_CALL_USD, cap="0.005", line=line)
    assert [item.score is not None for item in output.rank_scores] == [True] * 16 + [False] * 4
    records = _status_lines(log)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert line in records[0].getMessage() and "reason=cost_cap_reached" in records[0].getMessage()


def test_a_cap_that_allows_no_call_is_a_skip(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    monkeypatch.setenv("GIGAI_JEV_COST_CAP_USD", "0")
    jev = _Jev()

    _output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(5)], jev=jev)

    assert jev.asked == []
    assert status is not None and status["status"] == "skipped" and status["reason"] == "cost_cap_reached"
    assert status["line"] == "Jev: skipped (cost cap $0.00 reached before any score)"


def test_a_run_with_nothing_new_to_score_says_so(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    jev = _Jev()

    output, status = _acquire(substrate, monkeypatch, [], jev=jev)

    assert jev.asked == [] and output.rows == ()
    assert status is not None and status["reason"] == "no_candidates"
    assert status["line"] == "Jev: skipped (no new postings to score)"


# --- the pass that scores ----------------------------------------------------------------


def test_scores_are_on_the_sealed_output_and_the_status_says_scored_n_of_m(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    # The three OLDEST postings are the best fits.
    best = {"Company 5", "Company 6", "Company 7"}
    jev = _Jev(score_for=lambda posting: 9 if posting["company"] in best else 2)

    output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(8)], jev=jev, cap=3)

    assert len(jev.asked) == 8
    assert status == _expected("scored", 8, 8, cost=8 * JEV_CALL_USD, line="Jev: scored 8 of 8 (cost $0.0038)")
    assert status["usage_line"] == "Jev: $0.0038 of $0.50 today"
    sealed = output.to_json()
    assert [item["normalized_url"] for item in sealed["rank_scores"]] == [row.posting.normalized_url for row in output.rows]
    assert all(isinstance(item["score"], int) and item["fit"] in {"strong", "no"} for item in sealed["rank_scores"])
    # The symptom of uat-bug-010 this bug hid: the assess cap goes to the best fits.
    selected = {posting.normalized_url for posting in output.selected_postings}
    assert selected == {f"https://jobs.lever.co/co{n}/1" for n in (5, 6, 7)}
    records = _status_lines(log)
    assert len(records) == 1 and records[0].levelno == logging.INFO
    assert "Jev: scored 8 of 8 (cost $0.0038) [" in records[0].getMessage() and "reason=-" in records[0].getMessage()
    # Jev is sent the resume's text and the key; the log has neither.
    assert all(RESUME_TEXT in body for body in jev.bodies)
    assert set(jev.authorization) == {"Bearer jv_test_key_never_logged"}
    _assert_nothing_private_was_logged(log)


def test_a_second_run_reads_the_scores_from_the_cache(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    jev = _Jev()
    rows = [_posting(n) for n in range(4)]

    _acquire(substrate, monkeypatch, rows, jev=jev)
    second_id = "run_00000000-0000-4000-8000-000000000022"
    output, status = _acquire(substrate, monkeypatch, rows, jev=jev, run_id=second_id)

    assert len(jev.asked) == 4, "a scored posting was asked about again"
    assert status is not None and status["text"] == "scored 4 of 4" and status["cost_usd"] == "0.000000"
    assert status["line"] == "Jev: scored 4 of 4 (cost $0.00)"
    # What the first run paid is still the day's spend.
    assert status["usage_line"] == "Jev: $0.0019 of $0.50 today"
    assert all(item.cached for item in output.rank_scores)
    # One cache under the home, for every project.
    assert len(list((substrate["home"] / "cache" / "scout" / "jev" / "scores").glob("*.json"))) == 4
    assert not (substrate["home"] / "scout" / substrate["resolved"].project_id / "jev_cache").exists()


def test_duplicates_are_not_asked_about_and_the_best_titles_are_asked_first(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    rows = [
        _posting(1, title="Senior Software Engineer, Payments", age_minutes=10),
        _posting(2, title="Software Engineer", age_minutes=500),
        _posting(3, title="Platform Lead", company="Software Engineer Works", age_minutes=1),
        _posting(4, title="Software Engineer", age_minutes=20),
        _posting(5, title="software engineer!", company="Company 4", age_minutes=900),  # duplicate of 4, older
        _posting(6, title="Staff Software Engineer", age_minutes=5),
    ]
    jev = _Jev()
    monkeypatch.setattr(jev_rank, "RUN_CONCURRENCY", 1)  # one at a time: the order asked is the order ranked

    output, status = _acquire(substrate, monkeypatch, rows, jev=jev)

    # The title IS a target role (newest first), then contains one (newest
    # first), then the posting whose company matched the role.
    assert [(posting["company"], posting["title"]) for posting in jev.asked] == [
        ("Company 4", "Software Engineer"),
        ("Company 2", "Software Engineer"),
        ("Company 6", "Staff Software Engineer"),
        ("Company 1", "Senior Software Engineer, Payments"),
        ("Software Engineer Works", "Platform Lead"),
    ]
    assert status is not None and status["text"] == "scored 5 of 5"
    # One entry per candidate, in the rows' order; the duplicate is unscored.
    assert [item.normalized_url for item in output.rank_scores] == [row.normalized_url for row in rows]
    assert [item.score is None for item in output.rank_scores] == [False, False, False, False, True, False]


# --- the day's budget --------------------------------------------------------------------


def test_the_daily_budget_stops_a_run_and_the_next_run_of_the_day(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    monkeypatch.setenv("GIGAI_JEV_DAILY_BUDGET_USD", "0.004")
    jev = _Jev()

    output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(30)], jev=jev)

    # 8 calls are $0.0038, under the budget; the next 8 pass it.
    assert len(jev.asked) == 16
    line = "Jev: scored 16 of 30 (cost $0.0077, daily budget $0.004 reached)"
    assert status == _expected(
        "scored", 16, 30, reason="daily_budget_reached", cost=16 * JEV_CALL_USD, budget="0.004", line=line
    )
    assert status["usage_line"] == "Jev: $0.0077 of $0.004 today"
    assert sum(1 for item in output.rank_scores if item.score is not None) == 16
    records = _status_lines(log)
    assert len(records) == 1 and records[0].levelno == logging.WARNING and line in records[0].getMessage()

    # Another run, other postings, the same day: Jev is not asked at all.
    log.clear()
    second_id = "run_00000000-0000-4000-8000-000000000022"
    _second, again = _acquire(substrate, monkeypatch, [_posting(n) for n in range(100, 110)], jev=jev, run_id=second_id)

    assert len(jev.asked) == 16
    assert again == _expected(
        "skipped", 0, 10, reason="daily_budget_reached", spent=16 * JEV_CALL_USD, budget="0.004",
        line="Jev: skipped (daily budget $0.004 reached)",
    )
    records = _status_lines(log)
    assert len(records) == 1 and records[0].levelno == logging.WARNING


def test_the_ledger_is_the_homes_and_names_no_posting_resume_or_key(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    from gigai.scout.find_jobs import jev_budget

    jev = _Jev()
    _acquire(substrate, monkeypatch, [_posting(n) for n in range(3)], jev=jev)

    home = substrate["home"]
    ledgers = list((home / "cache" / "scout" / "jev" / "spend").glob("*.jsonl"))
    assert len(ledgers) == 1
    lines = [json.loads(line) for line in ledgers[0].read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    assert all(set(line) == {"at", "cost_usd", "where", "run_id"} for line in lines)
    assert {(line["cost_usd"], line["where"], line["run_id"]) for line in lines} == {("0.000480", "acquire", RUN_ID)}
    text = ledgers[0].read_text(encoding="utf-8")
    assert "jv_test_key_never_logged" not in text and "Python" not in text and "lever.co" not in text
    assert jev_budget.spent_today_usd(home) == pytest.approx(3 * JEV_CALL_USD)
    assert jev_budget.usage(home) == {
        "day": ledgers[0].stem, "spent_today_usd": "0.001440", "daily_budget_usd": "0.50",
        "remaining_usd": "0.498560", "budget_reached": False, "line": "Jev: $0.0014 of $0.50 today",
    }


def test_the_budget_is_read_in_one_place(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from gigai.scout.find_jobs import jev_budget

    monkeypatch.delenv("GIGAI_JEV_DAILY_BUDGET_USD", raising=False)
    assert jev_budget.daily_budget_usd(tmp_path) == jev_budget.DEFAULT_DAILY_BUDGET_USD == 0.50
    monkeypatch.setenv("GIGAI_JEV_DAILY_BUDGET_USD", "1.25")
    assert jev_budget.daily_budget_usd(tmp_path) == 1.25
    for unusable in ("", "a lot", "-1"):
        monkeypatch.setenv("GIGAI_JEV_DAILY_BUDGET_USD", unusable)
        assert jev_budget.daily_budget_usd(tmp_path) == 0.50
    # A day with no ledger has cost nothing; a line that cannot be read is skipped.
    assert jev_budget.spent_today_usd(tmp_path) == 0.0
    jev_budget.record_spend(tmp_path, 0.002, where="rank")
    jev_budget.record_spend(tmp_path, 0.0, where="rank")
    ledger = next(jev_budget.spend_dir(tmp_path).glob("*.jsonl"))
    ledger.write_text(ledger.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    assert jev_budget.spent_today_usd(tmp_path) == pytest.approx(0.002)


# --- the operator's run, at its own size -------------------------------------------------

COMPANIES = 10_336
MATCHED = 1_458


def _board(token: str) -> WatchlistEntry:
    return WatchlistEntry(
        watchlist_id=f"scout_watchlist:lever:{token}", provider=ATSProvider.LEVER, board_token=token,
        company=token, state="active",
        first_seen=WatchlistFirstSeen(SourceKind.ATS, f"https://example.test/{token}", "catalog:test-rev", "batch-1", "2026-09-22T00:00:00Z"),
    )


def _lever_board(request: httpx.Request) -> httpx.Response:
    token = request.url.path.rsplit("/", 1)[-1]
    index = int(token.removeprefix("co"))
    created = int((time.time() - 86400 - index * 60) * 1000)
    return httpx.Response(
        200,
        json=[{
            "id": "job-1", "text": "Software Engineer" if index < MATCHED else "Recruiter",
            "hostedUrl": f"https://jobs.lever.co/{token}/job-1", "categories": {"location": "Denver, CO"},
            "country": "US", "createdAt": created, "descriptionPlain": f"Work at {token}.",
        }],
    )


def test_a_catalog_size_search_with_a_key_and_a_resume_is_scored(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    """The operator's run: 10,336 indexed companies, 1,458 matches, 500 imported.

    Fails on a0c5b75 at the first assertion about the scores: the sealed
    output had none.
    """

    home, resolved = substrate["home"], substrate["resolved"]
    boards = [_board(f"co{n}") for n in range(COMPANIES)]
    config = _config(ats=True)
    limits = AcquireLimits(concurrency_per_provider=8, min_request_interval_seconds=0.0, time_budget_seconds=None)
    with httpx.Client(transport=httpx.MockTransport(_lever_board)) as client:
        update = update_sources(
            boards, cache=board_cache_for_home(home), index=CompanyIndex.for_home(home), client=client, config=config, limits=limits,
        )
    assert update.status == "succeeded"
    _seal_run_input(substrate, config)
    jev = _Jev()
    monkeypatch.setattr(market_acquisition, "_jev_http_client", jev.client)
    asked_a_board: list[str] = []

    def no_board(request: httpx.Request) -> httpx.Response:
        asked_a_board.append(str(request.url))
        return httpx.Response(500, json={"error": "a search must not fetch"})

    with httpx.Client(transport=httpx.MockTransport(no_board)) as client:
        output = acquire_node(
            _context(substrate),
            AcquireInput(config, "sha256:" + "c" * 64, None, (), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH),
            http_client=client, exa=_Exa(), ats=ATSBoardClients(), watchlist=_Watchlist(boards),
            home_root=home, target=substrate["target"], limits=limits, boards_from=BOARDS_FROM_INDEX,
        )

    # The operator's numbers.
    snapshot = read_progress(resolved.path / "runs" / RUN_ID)
    assert asked_a_board == []
    assert snapshot.boards["source"] == "index" and snapshot.boards["cached"] == COMPANIES
    assert snapshot.boards["matched"] == MATCHED
    assert len(output.rows) == IMPORT_ROW_CAP == 500 and output.not_imported_count == MATCHED - 500 == 958

    # The symptom: the sealed output has scores, one for every imported posting.
    sealed = output.to_json()
    assert "rank_scores" in sealed, "outputs/acquire.json has no rank_scores"
    scored = {item["normalized_url"] for item in sealed["rank_scores"] if item["score"] is not None}
    assert scored == {row.posting.normalized_url for row in output.rows}

    # Jev was asked until the cap, 8 postings at a time: 520 calls at
    # $0.00048 are $0.2496, so one more group of 8 is asked.
    assert len(jev.asked) == 528
    line = "Jev: scored 528 of 1,458 (cost $0.25, cost cap $0.25)"
    assert snapshot.rank_status == _expected("scored", 528, MATCHED, reason="cost_cap_reached", cost=528 * JEV_CALL_USD, line=line)
    assert snapshot.rank_status["usage_line"] == "Jev: $0.25 of $0.50 today"
    records = _status_lines(log)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert line in records[0].getMessage()
    # The newest matches were asked first (every title is the target role).
    assert {posting["company"] for posting in jev.asked[:8]} == {f"co{n}" for n in range(8)}
    assert {posting["company"] for posting in jev.asked} == {f"co{n}" for n in range(528)}


# --- jev_rank: the pass itself -----------------------------------------------------------


@pytest.fixture
def cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setattr(jev_rank, "project_id", lambda home_root, target: "proj-test")
    monkeypatch.delenv("GIGAI_JEV_DAILY_BUDGET_USD", raising=False)
    home, target = tmp_path / "cache-home", tmp_path / "cache-target"
    home.mkdir()
    target.mkdir()
    return home, target


def _report(rows, jev_client: JevClient, cache_home: tuple[Path, Path], *, resume_text: str = "resume", profile_id: str = "p1", **kwargs):
    home, target = cache_home
    return jev_rank.rank_postings_report(
        tuple(rows), client=jev_client, resume_text=resume_text,
        prefs=jev_rank.RankPreferences(target_titles=("software engineer",)),
        profile_id=profile_id, resume_revision_id="r1", home_root=home, target=target, **kwargs,
    )


def test_one_failed_call_between_good_ones_does_not_stop_the_pass(cache_home) -> None:
    good = _Jev()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            return httpx.Response(502, json={}, request=request)
        return good.handler(request)

    report = _report([_posting(n) for n in range(4)], JevClient("k", httpx.Client(transport=httpx.MockTransport(handler))), cache_home)

    assert calls["n"] == 4 and report.calls == 4
    assert [item.score is not None for item in report.scores] == [True, False, True, True]
    assert report.errors == (("jev_http_502", 1),) and report.stopped_on is None and report.throttled is None
    status = jev_rank.RankStatus.from_report(report, cost_cap_usd=0.25)
    assert status.text == "scored 3 of 4" and status.reason == "jev_error:jev_http_502"
    assert status.line == "Jev: scored 3 of 4 (cost $0.0014, Jev error: jev_http_502)"


def test_a_busy_answer_is_asked_again_after_a_pause_when_retries_are_on(cache_home) -> None:
    good = _Jev()
    calls = {"n": 0}
    pauses: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, json={}, request=request)
        return good.handler(request)

    client = JevClient("k", httpx.Client(transport=httpx.MockTransport(handler)))
    report = _report([_posting(1)], client, cache_home, retries=2, sleep=pauses.append)

    assert calls["n"] == 3 and pauses == [0.5, 1.5]
    assert report.scored == 1 and report.errors == () and report.calls == 1
    # One call at a time cannot slow down further.
    assert report.throttled is None


def test_a_busy_answer_halves_the_calls_made_side_by_side_for_the_rest_of_the_pass(cache_home) -> None:
    good = _Jev()
    lock = threading.Lock()
    state = {"busy_left": 1, "now": 0, "most_after": 0, "answered": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            busy = state["busy_left"] > 0
            state["busy_left"] -= 1 if busy else 0
            state["now"] += 1
            if state["answered"] >= 8:
                state["most_after"] = max(state["most_after"], state["now"])
        try:
            if busy:
                return httpx.Response(429, json={}, request=request)
            time.sleep(0.01)
            return good.handler(request)
        finally:
            with lock:
                state["now"] -= 1
                state["answered"] += 0 if busy else 1

    client = JevClient("k", httpx.Client(transport=httpx.MockTransport(handler)))
    report = _report([_posting(n) for n in range(24)], client, cache_home, concurrency=8, retries=2, sleep=lambda _seconds: None)

    assert report.scored == 24 and report.errors == ()
    assert report.throttled == "8 -> 4 after 429"
    assert 1 <= state["most_after"] <= 4, "more than 4 calls were made side by side after the busy answer"
    status = jev_rank.RankStatus.from_report(report, cost_cap_usd=0.25)
    assert status.status == "scored" and status.reason is None
    assert status.line == "Jev: scored 24 of 24 (cost $0.01, throttled: 8 -> 4 after 429)"
    assert status.to_json()["throttled"] == "8 -> 4 after 429"


def test_a_throttled_pass_is_a_warning_in_the_log(log) -> None:
    jev_rank.log_rank_status(
        jev_rank.RankStatus("scored", 24, 24, None, 0.25, 0.01152, "8 -> 4 after 429", 0.01152, 0.5), run_id="run_1", where="acquire"
    )

    assert [record.levelno for record in log.records] == [logging.WARNING]
    assert "throttled: 8 -> 4 after 429" in log.records[0].getMessage()


def test_a_refused_key_is_never_asked_again(cache_home) -> None:
    jev = _Jev(status=401)
    pauses: list[float] = []

    report = _report([_posting(n) for n in range(6)], JevClient("k", jev.client()), cache_home, retries=2, sleep=pauses.append)

    assert len(jev.asked) == 1 and pauses == []
    assert report.stopped_on == "jev_http_401" and report.scored == 0 and len(report.scores) == 6


def test_calls_made_side_by_side_give_the_same_scores_and_stop_at_the_cap(cache_home) -> None:
    rows = [_posting(n) for n in range(30)]
    active = {"now": 0, "most": 0}
    lock = threading.Lock()
    jev = _Jev(score_for=lambda posting: int(str(posting["company"]).split()[-1]) % 10)

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            active["now"] += 1
            active["most"] = max(active["most"], active["now"])
        time.sleep(0.01)
        try:
            return jev.handler(request)
        finally:
            with lock:
                active["now"] -= 1

    client = JevClient("k", httpx.Client(transport=httpx.MockTransport(handler)))
    report = _report(rows, client, cache_home, concurrency=8, cost_cap_usd=20 * JEV_CALL_USD)

    # The cap is checked before each group of 8: 8, 16, 24 calls, then it holds.
    assert report.calls == 24 and report.unscored_by_cap == 6 and report.capped is True
    assert 1 < active["most"] <= 8
    assert [item.normalized_url for item in report.scores] == [row.normalized_url for row in rows]
    assert [item.score for item in report.scores[:24]] == [round((n % 10) * 100 / 9) for n in range(24)]
    assert all(item.score is None for item in report.scores[24:])


def test_rows_with_no_content_do_not_share_one_cached_score(cache_home) -> None:
    jev = _Jev(score_for=lambda posting: 9 if posting["company"] == "Company 1" else 1)
    bare = [replace(_posting(n), content_sha256=None, text=None) for n in (1, 2)]

    first = _report(bare, JevClient("k", jev.client()), cache_home)
    again = _report(bare, JevClient("k", jev.client()), cache_home)

    assert len(jev.asked) == 2
    assert [item.score for item in first.scores] == [100, 11]
    assert [(item.normalized_url, item.score, item.cached) for item in again.scores] == [
        (bare[0].normalized_url, 100, True),
        (bare[1].normalized_url, 11, True),
    ]


def test_a_cached_score_carries_the_url_of_the_row_it_is_returned_for(cache_home) -> None:
    jev = _Jev()
    row = _posting(1)
    moved = replace(row, url="https://jobs.lever.co/co1/moved", normalized_url="https://jobs.lever.co/co1/moved")

    _report([row], JevClient("k", jev.client()), cache_home)
    report = _report([moved], JevClient("k", jev.client()), cache_home)

    assert len(jev.asked) == 1
    assert report.scores[0].cached is True and report.scores[0].normalized_url == moved.normalized_url


def test_one_cache_for_every_project_and_profile_keyed_by_the_resumes_text(cache_home, monkeypatch: pytest.MonkeyPatch) -> None:
    home, _target = cache_home
    jev = _Jev()
    rows = [_posting(1), _posting(2)]

    _report(rows, JevClient("k", jev.client()), cache_home, profile_id="p1")
    # Another project, another profile, the same resume: nothing is paid for again.
    monkeypatch.setattr(jev_rank, "project_id", lambda home_root, target: "another-project")
    same = _report(rows, JevClient("k", jev.client()), cache_home, profile_id="p2")
    # An unbound target has no earlier cache to look in; the shared one still answers.
    def unbound(home_root, target):
        raise RuntimeError("target is not bound to a GigAI project")

    monkeypatch.setattr(jev_rank, "project_id", unbound)
    unbound_report = _report(rows, JevClient("k", jev.client()), cache_home, profile_id="p3")

    assert len(jev.asked) == 2
    assert same.cache_hits == 2 and same.calls == 0 and same.total_cost_usd == 0.0
    assert unbound_report.cache_hits == 2 and unbound_report.calls == 0
    assert sorted(path.parent for path in jev_rank.cache_dir(home).glob("*.json")) == [home / "cache" / "scout" / "jev" / "scores"] * 2

    # Another resume is another score.
    other = _report(rows, JevClient("k", jev.client()), cache_home, resume_text="another resume")
    assert len(jev.asked) == 4 and other.cache_hits == 0


def test_an_earlier_per_project_cache_entry_is_no_longer_read(cache_home) -> None:
    """uat-bug-021 decision d: the earlier per-project cache carries no
    preferences digest, so it cannot be trusted to answer for a fresh key
    that names one -- it is left on disk, untouched, and simply never a
    hit any more. A row with such an entry costs one rescore."""

    home, _target = cache_home
    jev = _Jev()
    row = _posting(1)
    legacy_key = jev_rank._legacy_cache_key(
        content_sha256=row.content_sha256, profile_id="p1", resume_revision_id="r1", model="jev-latest"
    )
    legacy = home / "scout" / "proj-test" / "jev_cache" / f"{legacy_key}.json"
    legacy.parent.mkdir(parents=True)
    # Written before the label came from the score: Jev said "no" at 86.
    legacy.write_text(json.dumps({
        "normalized_url": row.normalized_url, "content_sha256": row.content_sha256, "fit": "no", "score": 86,
        "reasons": ["stack_match"], "mismatch_flags": [], "hidden_by_default": False, "cost_usd": "0.000480", "cached": True,
    }))

    report = _report([row], JevClient("k", jev.client()), cache_home)

    assert len(jev.asked) == 1 and report.cache_hits == 0  # not read: Jev was asked, once
    assert report.scores[0].score == 89  # _Jev's own fresh answer (level 8 of 9), not the legacy 86
    # The legacy file is untouched -- no migration, no deletion.
    assert legacy.is_file()
    assert json.loads(legacy.read_text())["score"] == 86
    # The fresh score is now in the shared cache.
    assert len(list(jev_rank.cache_dir(home).glob("*.json"))) == 1


def test_the_fit_label_is_the_scores(cache_home) -> None:
    assert jev_rank.FIT_THRESHOLDS == (("strong", 70), ("maybe", 40), ("no", 0))
    assert [jev_rank.fit_for_score(score) for score in (100, 70, 69, 40, 39, 0)] == ["strong", "strong", "maybe", "maybe", "no", "no"]

    # Jev's own label says strong for every posting; the scores say otherwise.
    def handler(request: httpx.Request) -> httpx.Response:
        level = {"Company 1": 9, "Company 2": 5, "Company 3": 2}[json.loads(request.content)["state"]["posting"]["company"]]
        return httpx.Response(200, request=request, json={
            "answers": {"fit": {"choice": "strong"}, "score": {"score": level}, "top_reason": {"choice": "stack_match"}},
            "usage": {"cost_usd": JEV_CALL_USD},
        })

    client = JevClient("k", httpx.Client(transport=httpx.MockTransport(handler)))
    fresh = _report([_posting(n) for n in (1, 2, 3)], client, cache_home)
    cached = _report([_posting(n) for n in (1, 2, 3)], client, cache_home)

    for report in (fresh, cached):
        assert [(item.score, item.fit, item.hidden_by_default) for item in report.scores] == [
            (100, "strong", False), (56, "maybe", False), (22, "no", True),
        ]


def test_reading_the_cache_never_asks_jev(cache_home) -> None:
    home, target = cache_home
    jev = _Jev()
    rows = [_posting(n) for n in range(3)]
    _report(rows[:2], JevClient("k", jev.client()), cache_home)

    scores = jev_rank.read_cached_scores(
        rows, resume_text="resume", prefs=jev_rank.RankPreferences(target_titles=("software engineer",)),
        profile_id="p1", resume_revision_id="r1", home_root=home, target=target,
    )

    assert len(jev.asked) == 2
    assert [(item.normalized_url, item.score is not None, item.cached) for item in scores] == [
        (rows[0].normalized_url, True, True), (rows[1].normalized_url, True, True), (rows[2].normalized_url, False, False),
    ]


def test_rank_postings_still_returns_its_three_values(cache_home) -> None:
    home, target = cache_home
    jev = _Jev()

    scores, total_cost, capped = jev_rank.rank_postings(
        (_posting(1), _posting(2)), client=JevClient("k", jev.client()), resume_text="resume",
        prefs=jev_rank.RankPreferences(), profile_id="p1", resume_revision_id="r1", home_root=home, target=target,
    )

    assert len(scores) == 2 and total_cost == pytest.approx(2 * JEV_CALL_USD) and capped is False


def test_a_pass_the_budget_stopped_reports_capped_to_its_earlier_callers(cache_home, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = cache_home
    monkeypatch.setenv("GIGAI_JEV_DAILY_BUDGET_USD", "0")
    jev = _Jev()

    scores, total_cost, capped = jev_rank.rank_postings(
        (_posting(1),), client=JevClient("k", jev.client()), resume_text="resume",
        prefs=jev_rank.RankPreferences(), profile_id="p1", resume_revision_id="r1", home_root=home, target=target,
    )

    assert jev.asked == [] and scores[0].score is None and total_cost == 0.0 and capped is True


@pytest.mark.parametrize(
    ("status", "text", "line"),
    [
        (("scored", 412, 1458, "cost_cap_reached", 0.25, 0.23), "scored 412 of 1,458", "Jev: scored 412 of 1,458 (cost $0.23, cost cap $0.25)"),
        (("scored", 1458, 1458, None, 0.25, 0.7), "scored 1,458 of 1,458", "Jev: scored 1,458 of 1,458 (cost $0.70)"),
        (("scored", 500, 500, None, 0.25, 0.0), "scored 500 of 500", "Jev: scored 500 of 500 (cost $0.00)"),
        (("scored", 412, 1458, "daily_budget_reached", 0.25, 0.23, None, 0.5, 0.5), "scored 412 of 1,458", "Jev: scored 412 of 1,458 (cost $0.23, daily budget $0.50 reached)"),
        (("skipped", 0, 1458, "daily_budget_reached", 0.25, 0.0, None, 0.5, 0.5), "skipped: daily_budget_reached", "Jev: skipped (daily budget $0.50 reached)"),
        (("running", 40, 500), "scoring: 40 of 500", "Jev: scoring, 40 of 500 so far"),
        (("skipped", 0, 1458, "no_resume"), "skipped: no_resume", "Jev: skipped (no resume)"),
        (("skipped", 0, 0, "no_key"), "skipped: no_key", "Jev: skipped (no Jev key)"),
        (("skipped", 0, 0, "no_run_input"), "skipped: no_run_input", "Jev: skipped (the run's input could not be read)"),
        (("skipped", 0, 0, "jev_error:jev_transport"), "skipped: jev_error:jev_transport", "Jev: skipped (Jev error: jev_transport)"),
        (("skipped", 0, 0, "error:OSError"), "skipped: error:OSError", "Jev: skipped (error: OSError)"),
    ],
)
def test_the_status_in_words(status: tuple, text: str, line: str) -> None:
    rank_status = jev_rank.RankStatus(*status)

    assert rank_status.text == text and rank_status.line == line
    assert rank_status.to_json()["text"] == text and rank_status.to_json()["line"] == line


def test_the_days_usage_in_words() -> None:
    status = jev_rank.RankStatus("scored", 412, 1458, "cost_cap_reached", 0.25, 0.23, None, 0.31, 0.5)

    assert status.usage_line == "Jev: $0.31 of $0.50 today"
    assert status.to_json()["spent_today_usd"] == "0.310000" and status.to_json()["daily_budget_usd"] == "0.50"
    assert jev_rank.RankStatus.skipped("no_key").usage_line is None


# --- a page that reads a run never spends ------------------------------------------------


def test_a_direct_acquire_call_has_no_committed_output_and_the_route_says_so(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    jev = _Jev()
    _acquire(substrate, monkeypatch, [_posting(n) for n in range(3)], jev=jev)
    log.clear()

    # The run's output is not committed to the journal by a direct acquire call.
    response, status = rank_api.rank_run(
        home_root=substrate["home"], target=substrate["target"], run_id=RUN_ID, profile_id=None,
    )

    assert response.scores == () and response.to_json()["scores"] == []
    assert status.text == "skipped: no_run_output"
    records = [record for record in log.records if record.getMessage().startswith("scout rank: Jev: ")]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "Jev: skipped (the run's postings could not be read)" in records[0].getMessage()
