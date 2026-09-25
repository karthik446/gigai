"""Behavior tests for ``run_discovery``/``latest_discovery`` orchestration (S2-A).

Covers the operator rule (2026-09-24): the H-1B source only runs when
``prefs.visa_sponsorship_required`` is true; when false, OpenAI runs alone
and H-1B is recorded as skipped with no I/O at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout.find_jobs.discovery import (
    DiscoveryBudgetExceeded,
    latest_discovery,
    run_discovery,
)
from gigai.scout.find_jobs.discovery import h1b_source, openai_source
from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs
from gigai.scout.find_jobs.discovery.types import SourceRunOutcome
from gigai.workpad import select_active_workpad
from tests.behaviors.scout_research.test_scout06_research_inputs import _fixture


@pytest.fixture
def bound_project(tmp_path: Path) -> tuple[Path, Path]:
    home, target, gig_id = _fixture(tmp_path)
    # run_discovery's frozen interface takes no gig_id (S2-A/S2-B contract);
    # it relies on the target's own "active Gig" binding, the same as any
    # other Scout CLI command run after `gigai gig use <gig_id>`.
    select_active_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    return home, target


def _prefs(**overrides: object) -> DiscoveryPrefs:
    values: dict[str, object] = {
        "roles": ("staff backend",),
        "countries": ("US",),
        "budget_usd_per_session": 5.0,  # generous; not testing the budget guard here
    }
    values.update(overrides)
    return DiscoveryPrefs(**values)


def _empty_outcome(name: str, **overrides: object) -> SourceRunOutcome:
    values: dict[str, object] = {"name": name, "runs": 0, "cost_usd": 0.0, "candidates": ()}
    values.update(overrides)
    return SourceRunOutcome(**values)


def test_h1b_skipped_and_no_io_when_sponsorship_not_required(
    bound_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = bound_project
    h1b_called = {"n": 0}

    def fake_h1b_run(**kwargs: object) -> SourceRunOutcome:
        h1b_called["n"] += 1
        return _empty_outcome("h1b")

    def fake_openai_run(**kwargs: object) -> SourceRunOutcome:
        return _empty_outcome("openai_web_search", runs=1)

    monkeypatch.setattr(h1b_source, "run", fake_h1b_run)
    monkeypatch.setattr(openai_source, "run", fake_openai_run)

    result = run_discovery(
        home_root=home, target=target, prefs=_prefs(visa_sponsorship_required=False), runs=1
    )

    assert h1b_called["n"] == 0
    source_by_name = {s.name: s for s in result.sources}
    assert source_by_name["h1b"].skip_reason == "sponsorship_not_required"
    assert source_by_name["h1b"].runs == 0
    assert source_by_name["openai_web_search"].runs == 1


def test_h1b_runs_when_sponsorship_required(
    bound_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = bound_project
    h1b_called = {"n": 0}

    def fake_h1b_run(**kwargs: object) -> SourceRunOutcome:
        h1b_called["n"] += 1
        return _empty_outcome("h1b", runs=1)

    def fake_openai_run(**kwargs: object) -> SourceRunOutcome:
        return _empty_outcome("openai_web_search", runs=1)

    monkeypatch.setattr(h1b_source, "run", fake_h1b_run)
    monkeypatch.setattr(openai_source, "run", fake_openai_run)

    result = run_discovery(
        home_root=home, target=target, prefs=_prefs(visa_sponsorship_required=True), runs=1
    )

    assert h1b_called["n"] == 1
    source_by_name = {s.name: s for s in result.sources}
    assert source_by_name["h1b"].skip_reason is None
    assert source_by_name["h1b"].runs == 1


def test_budget_guard_refuses_before_any_spend(
    bound_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = bound_project
    called = {"n": 0}

    def fail_if_called(**kwargs: object) -> SourceRunOutcome:
        called["n"] += 1
        raise AssertionError("no source should run when the budget guard refuses")

    monkeypatch.setattr(h1b_source, "run", fail_if_called)
    monkeypatch.setattr(openai_source, "run", fail_if_called)

    with pytest.raises(DiscoveryBudgetExceeded):
        run_discovery(
            home_root=home,
            target=target,
            prefs=_prefs(budget_usd_per_session=0.0001),
            runs=3,
        )

    assert called["n"] == 0


def test_latest_discovery_round_trips_after_a_run(
    bound_project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = bound_project

    def fake_h1b_run(**kwargs: object) -> SourceRunOutcome:
        return _empty_outcome("h1b")

    def fake_openai_run(**kwargs: object) -> SourceRunOutcome:
        return _empty_outcome("openai_web_search", runs=1)

    monkeypatch.setattr(h1b_source, "run", fake_h1b_run)
    monkeypatch.setattr(openai_source, "run", fake_openai_run)

    result = run_discovery(
        home_root=home, target=target, prefs=_prefs(visa_sponsorship_required=False), runs=1
    )

    reloaded = latest_discovery(home_root=home, target=target)
    assert reloaded is not None
    assert reloaded.discovery_id == result.discovery_id
    assert reloaded.status == result.status
    assert reloaded.cost_usd == result.cost_usd


def test_latest_discovery_returns_none_when_nothing_has_run(bound_project: tuple[Path, Path]) -> None:
    home, target = bound_project
    assert latest_discovery(home_root=home, target=target) is None
