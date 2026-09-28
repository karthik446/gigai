"""ui-pass (uat-bug-021 decision a): the home's Jev settings, and every
place that may ask Jev obeying them.

``jev_budget`` reads both settings, in one place each:

* ``daily_budget_usd``: ``GIGAI_JEV_DAILY_BUDGET_USD`` (the environment
  wins, so an eval runs under its own cap), else the stored setting, else
  $0.50;
* ``rank_enabled``: the stored setting, else on.

They are stored in ``<home>/local/scout/jev-settings.json``: the home's (the
spend ledger is the home's), never under ``cache/``. A file that cannot be
read turns ranking OFF: a spend switch fails closed.

With ranking off a run's rank pass records ``skipped: disabled`` and asks
Jev nothing, and a quick assessment asks Jev nothing and stores no reason
(``RANK_SKIP_REASONS`` is a stored contract's enum). A budget stored as 0
stops a run before its first call; the environment still wins over it.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil

import pytest

from gigai.scout.find_jobs import jev_budget
from tests.behaviors.scout_find_jobs.test_quick_assess_jev import (  # noqa: F401 - fixtures
    _FakeJev,
    _add_key,
    _hermetic,
    _run,
    _stored,
    _url_job,
    fx,
    jev,
)
from tests.behaviors.scout_find_jobs.test_rank_status import (  # noqa: F401 - fixtures
    RUN_ID,
    _acquire,
    _Jev,
    _posting,
    _status_lines,
    log,
    substrate,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(jev_budget.DAILY_BUDGET_ENV, raising=False)
    return tmp_path / "home"


# --- the settings ----------------------------------------------------------------------------


def test_the_defaults_are_on_and_fifty_cents_and_a_read_writes_nothing(home: Path) -> None:
    assert jev_budget.read_settings(home) == {"jev_daily_budget_usd": 0.50, "jev_rank_enabled": True}
    assert jev_budget.daily_budget_usd(home) == jev_budget.DEFAULT_DAILY_BUDGET_USD == 0.50
    assert jev_budget.rank_enabled(home) is True
    assert jev_budget.read_settings(None) == {"jev_daily_budget_usd": 0.50, "jev_rank_enabled": True}
    assert not jev_budget.settings_path(home).exists()


def test_the_budget_is_the_environment_then_the_setting_then_the_default(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jev_budget.write_settings(home, daily_budget_usd=0.2)
    assert jev_budget.daily_budget_usd(home) == 0.2
    monkeypatch.setenv(jev_budget.DAILY_BUDGET_ENV, "1.25")
    assert jev_budget.daily_budget_usd(home) == 1.25
    assert jev_budget.budget_env_override() == 1.25
    # An environment value that is not a usable amount is not an override.
    for unusable in ("", "a lot", "-1"):
        monkeypatch.setenv(jev_budget.DAILY_BUDGET_ENV, unusable)
        assert jev_budget.budget_env_override() is None
        assert jev_budget.daily_budget_usd(home) == 0.2
    monkeypatch.delenv(jev_budget.DAILY_BUDGET_ENV)
    jev_budget.settings_path(home).unlink()
    assert jev_budget.daily_budget_usd(home) == 0.50
    # The usage block reports the budget in force.
    monkeypatch.setenv(jev_budget.DAILY_BUDGET_ENV, "0.7")
    assert jev_budget.usage(home)["daily_budget_usd"] == "0.70"


def test_each_save_keeps_the_other_setting(home: Path) -> None:
    assert jev_budget.write_settings(home, rank_enabled=False) == {"jev_daily_budget_usd": 0.50, "jev_rank_enabled": False}
    assert jev_budget.write_settings(home, daily_budget_usd=0) == {"jev_daily_budget_usd": 0.0, "jev_rank_enabled": False}
    assert jev_budget.write_settings(home, rank_enabled=True) == {"jev_daily_budget_usd": 0.0, "jev_rank_enabled": True}
    stored = json.loads(jev_budget.settings_path(home).read_text(encoding="utf-8"))
    assert stored == {"schema_version": "scout-jev-settings:1", "jev_daily_budget_usd": 0.0, "jev_rank_enabled": True}
    assert jev_budget.daily_budget_usd(home) == 0.0 and jev_budget.rank_enabled(home) is True


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"daily_budget_usd": -0.01}, "invalid_value"),
        ({"daily_budget_usd": float("nan")}, "invalid_value"),
        ({"daily_budget_usd": float("inf")}, "invalid_value"),
        ({"daily_budget_usd": "0.10"}, "invalid_value"),
        ({"daily_budget_usd": True}, "invalid_value"),
        ({"rank_enabled": "off"}, "wrong_type"),
        ({"rank_enabled": 0}, "wrong_type"),
    ],
)
def test_a_bad_value_is_refused_and_nothing_is_written(home: Path, fields: dict, code: str) -> None:
    jev_budget.write_settings(home, daily_budget_usd=0.3)
    before = jev_budget.settings_path(home).read_bytes()
    with pytest.raises(jev_budget.JevSettingsError) as refused:
        jev_budget.write_settings(home, **fields)
    assert refused.value.code == code
    assert jev_budget.settings_path(home).read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"schema_version": "scout-jev-settings:0", "jev_rank_enabled": True}),
        json.dumps({"schema_version": "scout-jev-settings:1", "jev_rank_enabled": "yes"}),
        json.dumps({"schema_version": "scout-jev-settings:1", "jev_daily_budget_usd": -1}),
        json.dumps(["scout-jev-settings:1"]),
    ],
)
def test_a_settings_file_that_cannot_be_read_turns_ranking_off(
    home: Path, content: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging.getLogger("gigai.scout.server"), "propagate", True)
    caplog.set_level(logging.WARNING, logger="gigai.scout.server")
    path = jev_budget.settings_path(home)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    assert jev_budget.rank_enabled(home) is False
    assert jev_budget.daily_budget_usd(home) == 0.50
    messages = [record.getMessage() for record in caplog.records]
    assert any("jev-settings.json could not be read" in message and "Rank with Jev is off" in message for message in messages)
    assert not any(content in message for message in messages if len(content) > 8)


def test_a_symlinked_settings_file_is_not_read_or_written(home: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"schema_version": "scout-jev-settings:1", "jev_rank_enabled": True}), encoding="utf-8")
    path = jev_budget.settings_path(home)
    path.parent.mkdir(parents=True)
    os.symlink(elsewhere, path)

    assert jev_budget.rank_enabled(home) is False
    with pytest.raises(jev_budget.JevSettingsError):
        jev_budget.write_settings(home, rank_enabled=True)
    assert json.loads(elsewhere.read_text(encoding="utf-8"))["jev_rank_enabled"] is True


def test_the_settings_are_the_homes_and_clearing_the_cache_keeps_them(home: Path) -> None:
    jev_budget.write_settings(home, daily_budget_usd=0.1, rank_enabled=False)
    jev_budget.record_spend(home, 0.002, where="rank")
    path = jev_budget.settings_path(home)
    assert path == home / "local" / "scout" / "jev-settings.json"
    assert (home / "cache") not in path.parents

    shutil.rmtree(home / "cache")

    assert jev_budget.read_settings(home) == {"jev_daily_budget_usd": 0.1, "jev_rank_enabled": False}


# --- a run obeys them ------------------------------------------------------------------------


def test_a_run_with_ranking_off_records_disabled_and_asks_jev_nothing(substrate: dict, monkeypatch: pytest.MonkeyPatch, log) -> None:
    jev_budget.write_settings(substrate["home"], rank_enabled=False)
    jev = _Jev()

    output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(3)], jev=jev)

    assert jev.asked == []
    assert output.rank_scores == () and "rank_scores" not in output.to_json()
    assert status is not None and status["status"] == "skipped" and status["reason"] == "disabled"
    assert status["text"] == "skipped: disabled" and status["total"] == 3
    records = _status_lines(log)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "reason=disabled" in records[0].getMessage() and f"run_id={RUN_ID}" in records[0].getMessage()
    # The run itself is untouched: every posting is imported and selected by date.
    assert len(output.rows) == 3 and len(output.selected_postings) == 3
    # Nothing was paid for.
    assert list((substrate["home"] / "cache" / "scout" / "jev").glob("spend/*.jsonl")) == []


def test_a_stored_budget_of_zero_stops_a_run_before_its_first_call_and_the_environment_wins(
    substrate: dict, monkeypatch: pytest.MonkeyPatch, log
) -> None:
    jev_budget.write_settings(substrate["home"], daily_budget_usd=0)
    jev = _Jev()

    _output, status = _acquire(substrate, monkeypatch, [_posting(n) for n in range(3)], jev=jev)

    assert jev.asked == []
    assert status is not None and status["reason"] == "daily_budget_reached" and status["daily_budget_usd"] == "0.00"

    monkeypatch.setenv(jev_budget.DAILY_BUDGET_ENV, "1.0")
    second = "run_00000000-0000-4000-8000-000000000023"
    _again, scored = _acquire(substrate, monkeypatch, [_posting(n) for n in range(10, 13)], jev=jev, run_id=second)

    assert len(jev.asked) == 3
    assert scored is not None and scored["text"] == "scored 3 of 3" and scored["daily_budget_usd"] == "1.00"


# --- quick assess obeys them -----------------------------------------------------------------


def test_quick_assess_with_ranking_off_asks_jev_nothing_and_stores_no_reason(fx, jev: _FakeJev) -> None:
    _add_key(fx)
    jev_budget.write_settings(fx.home_root, rank_enabled=False)

    response = _run(fx, _url_job())

    assert jev.requests == []
    assert response.rank_score is None and response.rank_skip_reason is None
    payload = _stored(response)
    assert "rank_score" not in payload and "rank_skip_reason" not in payload

    # On again: the same assessment is scored.
    jev_budget.write_settings(fx.home_root, rank_enabled=True)
    again = _run(fx, _url_job())
    assert len(jev.requests) == 1 and again.rank_score is not None
