"""uat-bug-021 / P6: the Jev ranker module (``jev_rank``) on its own.

SCOPE-ADD-3 C1 moved ranking out of Jev: a run's ranking step is the
operator's own model (``model_rank``), and its run-path tests live in
``test_run_rank_step.py`` (skip reasons, fail open, sealed output, streamed
batches, selection and import cap by rank). The run-path tests that were
here pinned the Jev pre-rank inside ``acquire_node`` and were removed with
it. What is left pins ``jev_rank`` itself -- ``rank_postings_report``'s
early stop, retry and slowdown after a busy answer, the calls made side by
side, one cache for every project keyed by the resume's text, the fit label
from the score, ``RankStatus`` in words -- until C2 deletes the module.
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

from gigai.scout.find_jobs import jev_rank
from gigai.scout.find_jobs.contracts import (
    ATSProvider,
    PostingRow,
    SourceKind,
)
from gigai.scout.find_jobs.jev_client import JevClient

FIXTURES = Path(__file__).parent / "fixtures"
LOGGER = "gigai.scout.server"
# What one Jev call costs (P6 live acceptance: 24 calls, $0.011518).
JEV_CALL_USD = 0.00048
RUN_ID = "run_00000000-0000-4000-8000-000000000021"
RESUME_TEXT = "Built and operated Python backend services for 6 years."


# --- the substrate ------------------------------------------------------------------


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


@pytest.fixture
def log(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> pytest.LogCaptureFixture:
    # A server started earlier in this process turns propagation off.
    monkeypatch.setattr(logging.getLogger(LOGGER), "propagate", True)
    caplog.set_level(logging.INFO, logger=LOGGER)
    return caplog


# --- one test per reason a pass is skipped for -------------------------------------------


# --- the pass that scores ----------------------------------------------------------------


# --- the day's budget --------------------------------------------------------------------


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

