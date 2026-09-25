"""Behavior tests for P6's Jev ranking orchestration: cache keying, cost cap,
and ordering stability (``jev_rank.py``).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.contracts import ATSProvider, PostingRow, SourceKind
from gigai.scout.find_jobs.jev_client import JevClient
from gigai.scout.find_jobs import jev_rank
from gigai.scout.find_jobs.jev_rank import (
    DEFAULT_COST_CAP_USD,
    RankPreferences,
    order_by_rank,
    rank_postings,
)


def _row(n: int, *, digest_suffix: str | None = None, text: str = "desc") -> PostingRow:
    suffix = digest_suffix or str(n)
    return PostingRow(
        url=f"https://boards.greenhouse.io/acme/jobs/{n}",
        normalized_url=f"https://boards.greenhouse.io/acme/jobs/{n}",
        provider=ATSProvider.GREENHOUSE,
        board_token="acme",
        company="Acme",
        title=f"SWE {n}",
        location="Remote",
        published_at=None,
        content_sha256="sha256:" + (suffix * 64)[:64],
        source_kind=SourceKind.ATS,
        query_key="q",
        text=text,
    )


@pytest.fixture(autouse=True)
def _isolate_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test uses a fake, stable project id so the cache dir under
    tmp_path is deterministic without needing a real journaled workpad."""

    monkeypatch.setattr(jev_rank, "project_id", lambda home_root, target: "proj-test")


def _client(handler) -> JevClient:
    return JevClient("fake-key", httpx.Client(transport=httpx.MockTransport(handler)))


def _handler_factory(calls: list[str], *, fit: str = "strong", score: float = 8, cost_usd: float = 0.0005):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "fit": {"choice": fit},
                    "score": {"score": score},
                    "top_reason": {"choice": "stack_match"},
                    "flag_domain": {"noul": 0.0},
                    "flag_seniority": {"noul": 0.0},
                    "flag_stack": {"noul": 0.0},
                    "flag_location": {"noul": 0.0},
                    "flag_sponsorship": {"noul": 0.0},
                },
                "usage": {"cost_usd": cost_usd},
            },
            request=request,
        )

    return handler


def _prefs() -> RankPreferences:
    return RankPreferences(target_titles=("SWE",), countries=("US",), visa_sponsorship_required=False)


def test_cache_miss_then_hit_makes_no_second_call(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    row = _row(1)
    calls: list[str] = []
    client = _client(_handler_factory(calls))

    scores1, cost1, capped1 = rank_postings(
        (row,), client=client, resume_text="resume", prefs=_prefs(),
        profile_id="p1", resume_revision_id="r1", home_root=home, target=target,
    )
    assert len(calls) == 1
    assert capped1 is False
    assert scores1[0].cached is False
    assert scores1[0].fit == "strong"

    scores2, cost2, capped2 = rank_postings(
        (row,), client=client, resume_text="resume", prefs=_prefs(),
        profile_id="p1", resume_revision_id="r1", home_root=home, target=target,
    )
    assert len(calls) == 1  # no new call
    assert cost2 == 0.0
    assert scores2[0].cached is True
    assert scores2[0].fit == "strong"


def test_different_profile_id_is_a_cache_miss(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    row = _row(1)
    calls: list[str] = []
    client = _client(_handler_factory(calls))

    rank_postings((row,), client=client, resume_text="resume", prefs=_prefs(), profile_id="p1", resume_revision_id="r1", home_root=home, target=target)
    rank_postings((row,), client=client, resume_text="resume", prefs=_prefs(), profile_id="p2", resume_revision_id="r1", home_root=home, target=target)

    assert len(calls) == 2  # distinct profile_id -> distinct cache key


def test_different_resume_revision_is_a_cache_miss(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    row = _row(1)
    calls: list[str] = []
    client = _client(_handler_factory(calls))

    rank_postings((row,), client=client, resume_text="resume", prefs=_prefs(), profile_id="p1", resume_revision_id="r1", home_root=home, target=target)
    rank_postings((row,), client=client, resume_text="resume", prefs=_prefs(), profile_id="p1", resume_revision_id="r2", home_root=home, target=target)

    assert len(calls) == 2


def test_different_content_sha_is_a_cache_miss(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    row_a = _row(1, digest_suffix="a")
    row_b = _row(1, digest_suffix="b")
    calls: list[str] = []
    client = _client(_handler_factory(calls))

    rank_postings((row_a,), client=client, resume_text="resume", prefs=_prefs(), profile_id="p1", resume_revision_id="r1", home_root=home, target=target)
    rank_postings((row_b,), client=client, resume_text="resume", prefs=_prefs(), profile_id="p1", resume_revision_id="r1", home_root=home, target=target)

    assert len(calls) == 2


def test_only_successful_scores_are_cached(tmp_path: Path) -> None:
    """A failed Jev call must never be memoized -- a transient outage should
    not permanently blank a posting's score for this resume."""

    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    row = _row(1)
    attempt = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempt["n"] += 1
        if attempt["n"] == 1:
            return httpx.Response(502, json={"error": "upstream"}, request=request)
        return _handler_factory([])(request)

    client = _client(handler)

    scores1, _cost1, _capped1 = rank_postings((row,), client=client, resume_text="resume", prefs=_prefs(), profile_id="p1", resume_revision_id="r1", home_root=home, target=target)
    assert scores1[0].fit is None  # unscored: the call failed, never cached

    scores2, _cost2, _capped2 = rank_postings((row,), client=client, resume_text="resume", prefs=_prefs(), profile_id="p1", resume_revision_id="r1", home_root=home, target=target)
    assert attempt["n"] == 2  # retried -- the failed attempt was never cached
    assert scores2[0].fit == "strong"


def test_cost_cap_stops_calling_and_marks_rest_unscored(tmp_path: Path) -> None:
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    rows = tuple(_row(n) for n in range(1, 4))
    calls: list[str] = []
    client = _client(_handler_factory(calls, cost_usd=0.10))

    scores, total_cost, capped = rank_postings(
        rows, client=client, resume_text="resume", prefs=_prefs(),
        profile_id="p1", resume_revision_id="r1", home_root=home, target=target,
        cost_cap_usd=0.15,
    )

    assert len(scores) == 3  # one entry per input row, always
    assert len(calls) == 2  # cap reached after the 2nd call (0.20 >= 0.15)
    assert capped is True
    assert scores[0].fit == "strong"
    assert scores[1].fit == "strong"
    assert scores[2].fit is None  # unscored, past the cap
    assert scores[2].score is None
    assert scores[2].cost_usd == "0"
    assert total_cost == pytest.approx(0.20)


def test_default_cost_cap_is_a_quarter_dollar() -> None:
    assert DEFAULT_COST_CAP_USD == 0.25


def test_order_by_rank_sorts_descending_by_score() -> None:
    row_low = _row(1)
    row_high = _row(2)
    row_mid = _row(3)
    from gigai.scout.find_jobs.jev_contracts import RankScore

    scores = (
        RankScore(row_low.normalized_url, row_low.content_sha256, "maybe", 40, (), (), False, "0.0005", False),
        RankScore(row_high.normalized_url, row_high.content_sha256, "strong", 90, (), (), False, "0.0005", False),
        RankScore(row_mid.normalized_url, row_mid.content_sha256, "maybe", 60, (), (), False, "0.0005", False),
    )
    ordered = order_by_rank((row_low, row_high, row_mid), scores)
    assert [row.normalized_url for row in ordered] == [row_high.normalized_url, row_mid.normalized_url, row_low.normalized_url]


def test_order_by_rank_puts_unscored_last_and_is_stable() -> None:
    from gigai.scout.find_jobs.jev_contracts import RankScore

    row_a = _row(1)
    row_b = _row(2)
    row_c = _row(3)  # no matching score at all
    scores = (
        RankScore(row_a.normalized_url, row_a.content_sha256, None, None, (), (), False, "0", False),  # unscored (past cap)
        RankScore(row_b.normalized_url, row_b.content_sha256, "strong", 90, (), (), False, "0.0005", False),
    )
    ordered = order_by_rank((row_a, row_b, row_c), scores)
    assert ordered[0].normalized_url == row_b.normalized_url  # only scored row first
    # row_a (explicitly unscored) and row_c (no score entry at all) both
    # sort last, in their original relative order (stability).
    assert [row.normalized_url for row in ordered[1:]] == [row_a.normalized_url, row_c.normalized_url]


def test_order_by_rank_is_noop_on_empty_scores() -> None:
    rows = tuple(_row(n) for n in range(1, 4))
    assert order_by_rank(rows, ()) == rows
