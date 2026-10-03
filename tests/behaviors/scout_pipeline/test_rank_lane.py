"""0.1.10.7 M4a: the background rank lane (``scout/pipeline/rank_lane.py``). Synthetic only, the fixture model.

(b) The lane ranks each ACTIVE profile's demand set (a deleted profile is
    never ranked), records every call in the metrics, and the score cache
    keeps an unchanged posting from being ranked again: a second pass makes 0
    calls. The daily counters are shared by the profiles: the 61st call of
    the day is flagged, the 101st is not made and the lane waits for the next
    day. The lane yields to live work, is off with the pipeline, and has one
    holder at a time.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.cli import cli
from gigai.scout.pipeline import rank_lane
from gigai.scout.pipeline.settings import PIPELINE_ENV, PipelineSetting
from gigai.scout.pipeline.store import CAP_RANK_CALLS, LEASE_RANK, PipelineStore, pipeline_path

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import MARKERS, PipelineModel, install_model
from tests.support.posting_fixtures import (
    NOW,
    TITLE_SECOND_ONLY,
    PostingsFixture,
    build_postings_fixture,
    days_ago,
    lever_job,
)

_LINE = re.compile(r"^(p\d+) \| ", re.MULTILINE)
_DAY = NOW.date().isoformat()


class RankModel(PipelineModel):
    """The scripted model, answering a rank prompt with one score per posting line. Every rank prompt is kept."""

    def __init__(self) -> None:
        super().__init__()
        self.current = SimpleNamespace(target=SimpleNamespace(model="fixture-model"))
        self.rank_prompts: list[str] = []

    def answer(self, prompt: str) -> InvocationResult:
        if "\nPOSTINGS (" not in prompt:
            return super().answer(prompt)
        self.rank_prompts.append(prompt)
        block = prompt.split("\nPOSTINGS (", 1)[1].split("\n\nAnswer with ONLY", 1)[0]
        items = [{"posting_id": pid, "score": 80 - int(pid[1:]) % 40, "reasons": ["fits"], "blockers": []} for pid in _LINE.findall(block)]
        return InvocationResult(
            status="success", output_text=json.dumps(items), resolved_model="fixture-model", raw_usage={},
            normalized_usage=NormalizedUsage(100, 10, 110), cost_status="unavailable",
        )


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[PostingsFixture, RankModel]:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    model = RankModel()
    install_model(monkeypatch, model)
    # The rank cache is keyed by the configured model: the fixture's model targets, as the CLI would load them.
    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    return fx, model


def _tick(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", lambda: NOW)
    kwargs.setdefault("busy", lambda: None)
    kwargs.setdefault("config", fixture_config(fx.home_root))
    return rank_lane.rank_tick(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _store(fx: PostingsFixture) -> PipelineStore:
    return PipelineStore(pipeline_path(fx.home_root, fx.target))


def _rank_calls(fx: PostingsFixture) -> list[tuple[str | None, int, str]]:
    connection = sqlite3.connect(pipeline_path(fx.home_root, fx.target))
    try:
        return connection.execute("SELECT profile_id, items, outcome FROM model_call WHERE kind='rank' ORDER BY id").fetchall()
    finally:
        connection.close()


def test_the_lane_ranks_each_active_profiles_demand_set_once_and_never_a_deleted_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx, model = _fixture(tmp_path, monkeypatch)
    fx.seed("acme", [lever_job("acme", 1), lever_job("acme", 2, title=TITLE_SECOND_ONLY)], seen_at=days_ago(1))
    fx.seed("old", [lever_job("old", 1)], seen_at=days_ago(20))

    first = _tick(fx)

    assert (first["schema_version"], first["state"], first["reason"]) == ("scout-rank-lane:1", "ran", None)
    assert (first["calls"], first["ranked"], first["warning"]) == (2, 5, False)  # one call per profile: 2 + 3 postings
    assert first["profiles"] == [
        {"profile_id": fx.default_profile_id, "unranked": 2, "ranked": 2, "calls": 1},
        {"profile_id": fx.second_profile_id, "unranked": 3, "ranked": 3, "calls": 1},
    ]
    assert first["calls_today"] == {"day": _DAY, "used": 2, "limit": 100, "warn_at": 60, "warning": False, "reached": False}
    assert len(model.rank_prompts) == 2 and model.assess_prompts == []
    # The rows have their scores (the read model reads them from the score cache), per profile.
    store = _store(fx)
    try:
        rows = store.postings()
        assert len(rows) == 5 and all(type(row.rank_score) is int for row in rows)
        assert {row.profile_id for row in rows} == {fx.default_profile_id, fx.second_profile_id}
        assert not store.lease_held(LEASE_RANK)  # released when the turn ended
    finally:
        store.close()
    # Metrics: one recorded call per model call, with its profile and how many postings it ranked.
    assert _rank_calls(fx) == [(fx.default_profile_id, 2, "ok"), (fx.second_profile_id, 3, "ok")]
    # The deleted profile (its titles match everything) was never ranked, and nothing private is in the result.
    dumped = json.dumps(first)
    assert fx.deleted_profile_id is not None and fx.deleted_profile_id not in dumped
    assert fx.deleted_profile_id not in {profile for profile, _items, _outcome in _rank_calls(fx)}
    assert all(marker not in dumped for marker in MARKERS)

    # A second pass over unchanged postings: the cache answers, 0 calls, nothing counted.
    second = _tick(fx)
    assert (second["state"], second["calls"], second["ranked"]) == ("idle", 0, 0)
    assert len(model.rank_prompts) == 2 and second["calls_today"]["used"] == 2  # type: ignore[index]
    assert [item["unranked"] for item in second["profiles"]] == [0, 0]  # type: ignore[union-attr]

    # A new posting: only it is ranked (the others are not sent again).
    fx.seed("late", [lever_job("late", 1, title=TITLE_SECOND_ONLY)], seen_at=NOW)
    third = _tick(fx)
    assert (third["state"], third["calls"], third["ranked"]) == ("ran", 1, 1)
    assert model.rank_prompts[-1].count("\np0 | ") == 1 and "\np1 | " not in model.rank_prompts[-1]
    assert _rank_calls(fx)[-1] == (fx.second_profile_id, 1, "ok")

    # The CLI runs one turn on the calling thread: nothing is left to rank.
    printed = CliRunner().invoke(cli, ["scout", "pipeline", "rank", "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert printed.exit_code == 0, printed.output
    assert json.loads(printed.output)["state"] == "idle" and len(model.rank_prompts) == 3


def test_the_day_counter_warns_past_60_stops_at_100_and_is_shared_by_two_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, model = _fixture(tmp_path, monkeypatch)
    # The counter itself: 101 calls in one day, taken for no profile in particular.
    scratch = PipelineStore(tmp_path / "counter.sqlite")
    taken = [rank_lane.take_rank_call(scratch, PipelineSetting(), _DAY) for _ in range(101)]
    scratch.close()
    assert [call.allowed for call in taken] == [True] * 100 + [False]
    assert [call.warning for call in taken[:60]] == [False] * 60 and taken[59].used == 60
    assert taken[60].to_json() == {"allowed": True, "used": 61, "limit": 100, "warn_at": 60, "warning": True}  # the 61st: made, flagged
    assert taken[100].to_json() == {"allowed": False, "used": 100, "limit": 100, "warn_at": 60, "warning": True}  # the 101st: not made

    # The lane: 51 postings both profiles match, one posting per call, so 102 calls are wanted in one day.
    fx.seed("acme", [lever_job("acme", n) for n in range(51)], seen_at=days_ago(1))

    sixty = _tick(fx, batch_size=1, max_calls=60)
    assert (sixty["state"], sixty["calls"], sixty["warning"]) == ("ran", 60, False)
    assert sixty["calls_today"]["used"] == 60 and sixty["calls_today"]["warning"] is False  # type: ignore[index]
    # The first profile used 51 of the day's calls, the second the next 9: one counter for both.
    assert [(item["profile_id"], item["calls"]) for item in sixty["profiles"]] == [(fx.default_profile_id, 51), (fx.second_profile_id, 9)]  # type: ignore[union-attr]

    sixty_first = _tick(fx, batch_size=1, max_calls=1)
    assert (sixty_first["state"], sixty_first["calls"], sixty_first["warning"]) == ("ran", 1, True)  # the 61st call returns the warning
    assert sixty_first["calls_today"] == {"day": _DAY, "used": 61, "limit": 100, "warn_at": 60, "warning": True, "reached": False}
    assert sixty_first["profiles"][-1] == {"profile_id": fx.second_profile_id, "unranked": 42, "ranked": 1, "calls": 1}  # type: ignore[index]

    rest = _tick(fx, batch_size=1)
    # 39 more calls reach 100; the 101st is not made: the lane waits for tomorrow.
    assert (rest["state"], rest["reason"], rest["calls"], rest["warning"]) == ("waiting", "daily_cap_reached", 39, True)
    assert rest["retry_at"] is not None and str(rest["retry_at"]).startswith((NOW + timedelta(days=1)).date().isoformat())
    assert rest["calls_today"] == {"day": _DAY, "used": 100, "limit": 100, "warn_at": 60, "warning": True, "reached": True}
    assert len(model.rank_prompts) == 100
    waited = _tick(fx, batch_size=1)
    assert (waited["state"], waited["calls"]) == ("waiting", 0) and len(model.rank_prompts) == 100
    store = _store(fx)
    try:
        assert store.used(CAP_RANK_CALLS, _DAY) == 100
        assert sum(1 for row in store.postings(profile_id=fx.second_profile_id) if row.rank_score is None) == 2  # the two that wait
    finally:
        store.close()
    by_profile = {profile: 0 for profile in (fx.default_profile_id, fx.second_profile_id)}
    for profile, _items, _outcome in _rank_calls(fx):
        by_profile[str(profile)] += 1
    assert by_profile == {fx.default_profile_id: 51, fx.second_profile_id: 49}

    # The next local day has its own count: the two that waited are ranked.
    tomorrow = _tick(fx, batch_size=1, now=lambda: NOW + timedelta(days=1))
    assert (tomorrow["state"], tomorrow["calls"], tomorrow["ranked"], tomorrow["warning"]) == ("ran", 2, 2, False)
    assert tomorrow["calls_today"]["used"] == 2  # type: ignore[index]


def test_the_lane_yields_is_off_with_the_pipeline_and_has_one_holder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, model = _fixture(tmp_path, monkeypatch)
    # Nothing stored to rank and no pipeline file yet: a look does not create it.
    assert not pipeline_path(fx.home_root, fx.target).exists()
    assert _tick(fx)["state"] == "idle" and not pipeline_path(fx.home_root, fx.target).exists()
    fx.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(1))

    yielded = _tick(fx, busy=lambda: "sources_update")
    assert (yielded["state"], yielded["reason"], yielded["calls"]) == ("yielded", "sources_update", 0)
    off = _tick(fx, environ={PIPELINE_ENV: "0"})
    assert (off["state"], off["reason"]) == ("disabled", "environment")
    other = _store(fx)
    try:
        assert other.take_lease(LEASE_RANK, worker="other")
        elsewhere = _tick(fx)
        assert (elsewhere["state"], elsewhere["calls"]) == ("busy_elsewhere", 0)
        other.release_lease(LEASE_RANK, worker="other")
    finally:
        other.close()
    assert model.rank_prompts == []  # none of the three made a call
    status = rank_lane.rank_status(fx.home_root, fx.target, now=lambda: NOW)
    assert status == {"enabled": True, "calls_today": {"day": _DAY, "used": 0, "limit": 100, "warn_at": 60, "warning": False, "reached": False}}

    # A model answer that cannot be read: the call is counted, nothing is scored, and the lane says so (the caller backs off).
    model.answer = lambda prompt: InvocationResult(  # type: ignore[method-assign]
        status="success", output_text="not json", resolved_model="fixture-model", raw_usage={},
        normalized_usage=NormalizedUsage(1, 1, 2), cost_status="unavailable",
    )
    unreadable = _tick(fx)
    assert (unreadable["state"], unreadable["reason"], unreadable["calls"], unreadable["ranked"]) == ("unavailable", "model_output_invalid", 1, 0)
    assert unreadable["calls_today"]["used"] == 1  # type: ignore[index]
