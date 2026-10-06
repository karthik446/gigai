"""0.1.10.7 M4a: the background rank lane (``scout/pipeline/rank_lane.py``). Synthetic only, the fixture model.

(b) The lane ranks each ACTIVE profile's demand set (a deleted profile is
    never ranked), records every call in the metrics, and the score cache
    keeps an unchanged posting from being ranked again: a second pass makes 0
    calls. The daily counters are shared by the profiles: the 61st call of
    the day is flagged, the 101st is not made and the lane waits for the next
    day. The lane yields to live work and has one holder at a time.

0.1.11.2 RANK-A: the lane has its OWN switch (``rank.enabled``, on by
default): it ranks with the pipeline off while the drain (the tailoring)
stays disabled; unreadable settings turn it off. It ranks only postings that
went up in the last 7 days (first seen in them, for a posting with no date).
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
from gigai.scout.pipeline import rank_lane, triggers
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.pipeline.runner import DRAIN_DISABLED, PipelineRunner
from gigai.scout.pipeline.settings import PIPELINE_ENV, PipelineSetting, pipeline_setting
from gigai.scout.pipeline.store import CAP_RANK_CALLS, LEASE_RANK, PipelineStore, pipeline_path

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import MARKERS, PipelineModel, install_model, set_pipeline_enabled
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
    taken = [
        triggers.spend_rank_calls(fx.home_root, fx.target, 1, setting=PipelineSetting(), store=scratch, now=NOW) for _ in range(101)
    ]
    scratch.close()
    assert [call["allowed"] for call in taken] == [True] * 100 + [False]
    assert [call["warning"] for call in taken[:60]] == [False] * 60 and taken[59]["used"] == 60
    # The 61st: made, flagged. The 101st: not made.
    assert taken[60] == {"allowed": True, "used": 61, "limit": 100, "warn_at": 60, "warning": True, "day": _DAY}
    assert taken[100] == {"allowed": False, "used": 100, "limit": 100, "warn_at": 60, "warning": True, "day": _DAY}

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


def test_the_rank_lane_and_a_second_caller_take_from_one_counter_across_two_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.1.10.7 int2: ONE rank counter (``triggers.spend_rank_calls``). The lane has no counter of its own."""

    assert not hasattr(rank_lane, "take_rank_call") and not hasattr(rank_lane, "RankCall")
    fx, model = _fixture(tmp_path, monkeypatch)
    # 4 postings both profiles match, one posting per call: the lane wants 8 calls.
    fx.seed("acme", [lever_job("acme", n) for n in range(4)], seen_at=days_ago(1))

    def second_caller(calls: int) -> dict[str, object]:
        return triggers.spend_rank_calls(fx.home_root, fx.target, calls, now=NOW)

    def per_profile(result: dict[str, object]) -> dict[str, int]:
        return {str(item["profile_id"]): int(item["calls"]) for item in result["profiles"]}  # type: ignore[union-attr,index,call-overload]

    assert second_caller(57) == {"allowed": True, "used": 57, "limit": 100, "warn_at": 60, "warning": False, "day": _DAY}
    # The lane's first three calls are the day's 58th to 60th: the first profile's, not flagged.
    three = _tick(fx, batch_size=1, max_calls=3)
    assert (three["state"], three["calls"], three["warning"]) == ("ran", 3, False)
    assert per_profile(three) == {fx.default_profile_id: 3}
    assert three["calls_today"] == {"day": _DAY, "used": 60, "limit": 100, "warn_at": 60, "warning": False, "reached": False}
    # The 61st call of the day is the lane's fourth: made and flagged, wherever the first 57 came from.
    sixty_first = _tick(fx, batch_size=1, max_calls=1)
    assert (sixty_first["state"], sixty_first["calls"], sixty_first["warning"]) == ("ran", 1, True)
    assert sixty_first["calls_today"]["used"] == 61  # type: ignore[index]
    assert triggers.caps(fx.home_root, fx.target, now=NOW)["rank_calls"] == {"used": 61, "limit": 100, "warn_at": 60, "warning": True}

    assert second_caller(37)["used"] == 98
    # The second profile wants 4 calls; two are left in the day. The 101st is not made: the lane waits.
    rest = _tick(fx, batch_size=1)
    assert (rest["state"], rest["reason"], rest["calls"], rest["warning"]) == ("waiting", "daily_cap_reached", 2, True)
    assert per_profile(rest) == {fx.default_profile_id: 0, fx.second_profile_id: 2}
    assert rest["calls_today"] == {"day": _DAY, "used": 100, "limit": 100, "warn_at": 60, "warning": True, "reached": True}
    # ... and so does the second caller, in the same place.
    refused = second_caller(1)
    assert (refused["allowed"], refused["used"], refused["warning"]) == (False, 100, True)
    assert len(model.rank_prompts) == 6
    store = _store(fx)
    try:
        assert store.used(CAP_RANK_CALLS, _DAY) == 100  # 94 the second caller's, 6 the lane's
    finally:
        store.close()

    # A call the second caller gives back is one the lane can make.
    triggers.refund_rank_calls(fx.home_root, fx.target, 1, now=NOW)
    one_more = _tick(fx, batch_size=1)
    assert (one_more["state"], one_more["calls"]) == ("waiting", 1) and len(model.rank_prompts) == 7
    assert per_profile(one_more)[fx.second_profile_id] == 1


def test_the_lane_yields_is_off_with_the_environment_and_has_one_holder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, model = _fixture(tmp_path, monkeypatch)
    # Nothing stored to rank and no pipeline file yet: a look does not create it.
    assert not pipeline_path(fx.home_root, fx.target).exists()
    assert _tick(fx)["state"] == "idle" and not pipeline_path(fx.home_root, fx.target).exists()
    fx.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(1))

    yielded = _tick(fx, busy=lambda: "sources_update")
    assert (yielded["state"], yielded["reason"], yielded["calls"]) == ("yielded", "sources_update", 0)
    # The variable that turns the pipeline off turns ranking off too, while the file does not name ``rank.enabled``.
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


# --- 0.1.11.2 RANK-A (1a): ranking on its own switch -----------------------------------------------------


def _write_settings(fx: PostingsFixture, **blocks: object) -> None:
    path = settings_path(fx.home_root, fx.target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", **blocks}), encoding="utf-8")


def _scores(fx: PostingsFixture) -> dict[str, int | None]:
    """``job -> rank_score`` of every stored row (a job two profiles match must agree on having one or not)."""

    store = _store(fx)
    try:
        found: dict[str, set[bool]] = {}
        for row in store.postings():
            found.setdefault(row.job, set()).add(row.rank_score is not None)
        assert all(len(kinds) == 1 for kinds in found.values()), found
        return {row.job: row.rank_score for row in store.postings()}
    finally:
        store.close()


@pytest.mark.parametrize("pipeline_enabled", [False, None], ids=["pipeline_off_in_settings", "pipeline_off_by_default"])
def test_ranking_runs_with_the_pipeline_off_and_the_drain_stays_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pipeline_enabled: bool | None
) -> None:
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    fx, model = _fixture(tmp_path, monkeypatch)
    set_pipeline_enabled(fx.home_root, fx.target, pipeline_enabled)
    assert pipeline_setting(fx.home_root, fx.target, environ={}).enabled is False
    fx.seed("acme", [lever_job("acme", 1), lever_job("acme", 2)], seen_at=days_ago(1))

    ticked = _tick(fx, environ={})

    # THE OUTCOME: the rows carry a rank score, with the pipeline off.
    assert (ticked["state"], ticked["reason"], ticked["calls"], ticked["ranked"]) == ("ran", None, 2, 4)
    scores = _scores(fx)
    assert len(scores) == 2 and all(type(score) is int for score in scores.values())
    assert rank_lane.rank_status(fx.home_root, fx.target, environ={}, now=lambda: NOW)["enabled"] is True
    setting = pipeline_setting(fx.home_root, fx.target, environ={})
    assert (setting.rank_enabled, setting.rank_source) == (True, "default")
    # ... and the pipeline did not come back with it: the drain answers disabled, no step exists, nothing was tailored.
    runner = PipelineRunner(home_root=fx.home_root, target=fx.target, config=fixture_config(fx.home_root), busy=lambda: None, environ={})
    drained = runner.drain()
    assert (drained.state, drained.steps) == (DRAIN_DISABLED, [])
    assert drained.reason == ("setting" if pipeline_enabled is False else "default")
    store = _store(fx)
    try:
        assert store.steps() == ()
    finally:
        store.close()
    assert model.tailor_prompts == [] and model.assess_prompts == [] and len(model.rank_prompts) == 2
    # `gigai scout pipeline status` says the two apart.
    printed = CliRunner().invoke(cli, ["scout", "pipeline", "status", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert printed.exit_code == 0, printed.output
    assert "Pipeline: off" in printed.output
    assert "Ranking: on (default); switched separately from the pipeline." in printed.output


def test_rank_enabled_is_its_own_setting_and_unreadable_settings_turn_ranking_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    fx, model = _fixture(tmp_path, monkeypatch)
    fx.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(1))

    def rank_block(environ: dict[str, str]) -> dict[str, object]:
        return pipeline_setting(fx.home_root, fx.target, environ=environ).to_json()["rank"]  # type: ignore[return-value]

    # rank.enabled false: off, whatever the pipeline's switch says.
    _write_settings(fx, pipeline={"enabled": True}, rank={"enabled": False})
    assert rank_block({}) == {"enabled": False, "source": "setting", "max_calls_per_day": 100, "warn_calls_per_day": 60}
    off = _tick(fx, environ={})
    assert (off["state"], off["reason"], off["calls"]) == ("disabled", "setting", 0)
    assert rank_lane.rank_status(fx.home_root, fx.target, environ={}, now=lambda: NOW)["enabled"] is False
    assert pipeline_setting(fx.home_root, fx.target, environ={}).enabled is True  # the pipeline's switch is not ranking's
    # The variable set to off: ranking is off with the pipeline when the file does not name rank.enabled ...
    _write_settings(fx, rank={"max_calls_per_day": 50})
    assert rank_block({PIPELINE_ENV: "off"}) == {"enabled": False, "source": "environment", "max_calls_per_day": 50, "warn_calls_per_day": 60}
    assert _tick(fx, environ={PIPELINE_ENV: "off"})["reason"] == "environment"
    # ... set to on it changes nothing for ranking, and a file that says rank.enabled false stays off.
    assert rank_block({PIPELINE_ENV: "on"})["enabled"] is True
    _write_settings(fx, rank={"enabled": False})
    assert rank_block({PIPELINE_ENV: "on"}) == {"enabled": False, "source": "setting", "max_calls_per_day": 100, "warn_calls_per_day": 60}

    # The settings API writes the same key (PUT /api/settings/background): false, true, and null takes it out of the file.
    from gigai.scout.find_jobs import background_settings

    def put(body: dict[str, object]) -> dict[str, object]:
        background_settings.write_background_settings(fx.home_root, fx.target, background_settings.validate_patch(body))
        return background_settings.background_settings(fx.home_root, fx.target, environ={})

    _write_settings(fx)
    assert put({"rank": {"enabled": False}})["settings"]["rank"] == {"enabled": False, "max_calls_per_day": 100, "warn_calls_per_day": 60}  # type: ignore[index]
    assert rank_block({})["source"] == "setting" and _tick(fx, environ={})["state"] == "disabled"
    back = put({"rank": {"enabled": None, "max_calls_per_day": 70}})
    assert back["settings"]["rank"]["enabled"] is True and back["effective"]["pipeline"]["rank"]["source"] == "default"  # type: ignore[index]
    assert json.loads(settings_path(fx.home_root, fx.target).read_text(encoding="utf-8"))["rank"] == {"max_calls_per_day": 70}
    with pytest.raises(background_settings.SettingsError) as refused:
        background_settings.validate_patch({"rank": {"enabled": "yes"}})
    assert refused.value.code == "wrong_type"

    # Settings that cannot be read: ranking is OFF, also for the CLI's forced turn and with the variable set to on.
    for text in ("{not json", json.dumps({"schema_version": "scout-settings:1", "rank": {"enabled": "yes"}}),
                 json.dumps({"schema_version": "scout-settings:1", "rank": ["enabled"]})):
        settings_path(fx.home_root, fx.target).write_text(text, encoding="utf-8")
        for environ in ({}, {PIPELINE_ENV: "on"}):
            assert rank_block(environ)["enabled"] is False and rank_block(environ)["source"] == "settings_unreadable", text
            for forced in (False, True):
                unreadable = _tick(fx, environ=environ, force_enabled=forced)
                assert (unreadable["state"], unreadable["reason"], unreadable["calls"]) == ("disabled", "settings_unreadable", 0), text
            assert rank_lane.rank_status(fx.home_root, fx.target, environ=environ, now=lambda: NOW)["enabled"] is False
    assert model.rank_prompts == [] and all(score is None for score in _scores_or_none(fx).values())

    # The file says rank.enabled true: the variable set to off does not switch ranking off (the file is explicit).
    _write_settings(fx, rank={"enabled": True})
    assert rank_block({PIPELINE_ENV: "off"})["source"] == "setting"
    assert pipeline_setting(fx.home_root, fx.target, environ={PIPELINE_ENV: "off"}).enabled is False
    on = _tick(fx, environ={PIPELINE_ENV: "off"})
    assert (on["state"], on["calls"]) == ("ran", 2) and all(type(score) is int for score in _scores(fx).values())


def _scores_or_none(fx: PostingsFixture) -> dict[str, int | None]:
    """:func:`_scores`, or nothing when no turn has made the pipeline file yet."""

    return _scores(fx) if pipeline_path(fx.home_root, fx.target).is_file() else {}


# --- 0.1.11.2 RANK-A (1b, operator decision D2): only postings posted in the last 7 days ------------------


def _undated(slug: str, n: int) -> dict[str, object]:
    job = lever_job(slug, n)
    del job["createdAt"]  # a board that gives no date: the window judges when Scout first saw it
    return job


def test_only_postings_posted_in_the_last_7_days_are_ranked_and_a_ranked_one_never_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    fx, model = _fixture(tmp_path, monkeypatch)
    set_pipeline_enabled(fx.home_root, fx.target, False)
    # One number per posting: the score cache is keyed by posting content, so two postings with one text share a score.
    numbers = {"recent": 1, "stale": 2, "lateseen": 3, "earlyseen": 4, "nodatenew": 5, "nodateold": 6, "fresh": 7}

    def url(slug: str) -> str:
        return f"https://jobs.lever.co/{slug}/{slug}-{numbers[slug]:05d}"

    fx.seed("recent", [lever_job("recent", 1, created=days_ago(3))], seen_at=days_ago(3))
    fx.seed("stale", [lever_job("stale", 2, created=days_ago(10))], seen_at=days_ago(10))
    fx.seed("lateseen", [lever_job("lateseen", 3, created=days_ago(10))], seen_at=NOW)  # posted 10 days ago, first seen today
    fx.seed("earlyseen", [lever_job("earlyseen", 4, created=days_ago(3))], seen_at=days_ago(10))  # posted 3 days ago, seen 10 ago
    fx.seed("nodatenew", [_undated("nodatenew", 5)], seen_at=days_ago(3))
    fx.seed("nodateold", [_undated("nodateold", 6)], seen_at=days_ago(10))
    inside = {url("recent"), url("earlyseen"), url("nodatenew")}
    outside = {url("stale"), url("lateseen"), url("nodateold")}

    first = _tick(fx, environ={})

    assert (first["state"], first["calls"], first["ranked"]) == ("ran", 2, 6)  # 3 postings, two profiles, one call each
    assert [item["unranked"] for item in first["profiles"]] == [3, 3]  # type: ignore[union-attr]  # counted inside the window
    scores = _scores(fx)
    assert set(scores) == inside | outside  # all six are stored rows: the window is the lane's, not the read model's
    assert {job for job, score in scores.items() if score is not None} == inside
    assert all(scores[job] is None for job in outside)
    sent = "\n".join(model.rank_prompts)
    # What the model was sent: the three inside the window, never one outside it.
    assert all(f"@ {slug} |" in sent for slug in ("recent", "earlyseen", "nodatenew"))
    assert all(slug not in sent for slug in ("stale", "lateseen", "nodateold"))

    # A second turn: 0 calls. The unranked old postings are not "left over" work, and no cache mismatch is reported.
    second = _tick(fx, environ={})
    assert (second["state"], second["reason"], second["calls"], second["ranked"]) == ("idle", None, 0, 0)
    assert [item["unranked"] for item in second["profiles"]] == [0, 0]  # type: ignore[union-attr]
    assert len(model.rank_prompts) == 2 and second["calls_today"]["used"] == 2  # type: ignore[index]

    # A posting that goes up later is the only one ranked: the ranked ones are not sent again, the old ones still never.
    fx.seed("fresh", [lever_job("fresh", 7)], seen_at=NOW)
    third = _tick(fx, environ={})
    assert (third["state"], third["reason"], third["calls"], third["ranked"]) == ("ran", None, 2, 2)
    assert all(prompt.count("\np0 | ") == 1 and "\np1 | " not in prompt for prompt in model.rank_prompts[2:])
    after = _scores(fx)
    assert {job for job, score in after.items() if score is not None} == inside | {url("fresh")}
    assert {job: after[job] for job in inside} == {job: scores[job] for job in inside}
    assert _tick(fx, environ={})["calls"] == 0 and len(model.rank_prompts) == 4
