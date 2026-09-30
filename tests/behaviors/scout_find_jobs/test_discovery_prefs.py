"""Behavior tests for ``DiscoveryPrefs`` load/save (S2-A packet, contracts.py:1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs.discovery.prefs import DiscoveryPrefs, DiscoveryPrefsError, load_prefs, save_prefs


def _setup_and_init(tmp_path: Path) -> tuple[Path, Path]:
    """Minimal bound (non-Gig) project -- the same flow packet B / run_supervisor tests use."""

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)

    runner = CliRunner()
    setup_result = runner.invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(tmp_path / "workpads"),
            "--editor",
            "/usr/bin/true",
            "--credential-ref",
            "provider=environment:GIGAI_PROVIDER_TOKEN",
            "--endpoint",
            "remote=openai_api:provider:https://api.example.test",
            "--model-target",
            "remote=remote:smoke-test",
            "--create-model-target",
            "remote",
            "--json",
        ],
    )
    assert setup_result.exit_code == 0, setup_result.output

    init_result = runner.invoke(
        cli,
        ["init", "--home", str(home), "--target", str(target), "--username", "discovery-prefs-test", "--json"],
    )
    assert init_result.exit_code == 0, init_result.output
    return home, target


@pytest.fixture
def bound_project(tmp_path: Path) -> tuple[Path, Path]:
    return _setup_and_init(tmp_path)


def _prefs(**overrides: object) -> DiscoveryPrefs:
    values: dict[str, object] = {
        "roles": ("staff backend", "senior backend"),
        "countries": ("US",),
        "work_mode": "remote",
        "city": "Denver, CO",
        "visa_sponsorship_required": True,
        "exclude_companies": ("Coupang", "ClickHouse", "Gen Digital"),
    }
    values.update(overrides)
    return DiscoveryPrefs(**values)


def test_load_prefs_returns_none_when_never_saved(bound_project: tuple[Path, Path]) -> None:
    home, target = bound_project
    assert load_prefs(home_root=home, target=target) is None


def test_save_then_load_round_trips(bound_project: tuple[Path, Path]) -> None:
    home, target = bound_project
    prefs = _prefs()
    save_prefs(home_root=home, target=target, prefs=prefs)

    loaded = load_prefs(home_root=home, target=target)
    assert loaded == prefs


def test_prefs_file_lands_under_home_not_target(bound_project: tuple[Path, Path]) -> None:
    home, target = bound_project
    save_prefs(home_root=home, target=target, prefs=_prefs())

    matches = list(home.rglob("prefs.json"))
    assert len(matches) == 1
    assert "discovery" in matches[0].parts
    assert not any(p.name == "prefs.json" for p in target.rglob("prefs.json"))


def test_defaults_applied_when_optional_fields_omitted(bound_project: tuple[Path, Path]) -> None:
    home, target = bound_project
    minimal = DiscoveryPrefs(roles=("software engineer",))
    save_prefs(home_root=home, target=target, prefs=minimal)

    loaded = load_prefs(home_root=home, target=target)
    assert loaded is not None
    assert loaded.cadence_days == 7
    assert loaded.budget_usd_per_session == 0.50
    assert loaded.work_mode == "any"
    assert loaded.visa_sponsorship_required is False


def test_invalid_work_mode_rejected() -> None:
    with pytest.raises(DiscoveryPrefsError) as excinfo:
        DiscoveryPrefs(roles=("x",), work_mode="not-a-mode")
    assert excinfo.value.code == "invalid_value"


def test_invalid_country_code_rejected() -> None:
    with pytest.raises(DiscoveryPrefsError):
        DiscoveryPrefs(roles=("x",), countries=("USA",))


def test_non_positive_budget_rejected() -> None:
    with pytest.raises(DiscoveryPrefsError):
        DiscoveryPrefs(roles=("x",), budget_usd_per_session=0.0)


def test_non_positive_cadence_rejected() -> None:
    with pytest.raises(DiscoveryPrefsError):
        DiscoveryPrefs(roles=("x",), cadence_days=0)


def test_resaving_overwrites_previous_prefs(bound_project: tuple[Path, Path]) -> None:
    home, target = bound_project
    save_prefs(home_root=home, target=target, prefs=_prefs())
    updated = _prefs(roles=("principal backend",), budget_usd_per_session=1.0)
    save_prefs(home_root=home, target=target, prefs=updated)

    loaded = load_prefs(home_root=home, target=target)
    assert loaded == updated


def test_from_json_rejects_unsupported_schema_version() -> None:
    with pytest.raises(DiscoveryPrefsError) as excinfo:
        DiscoveryPrefs.from_json({"schema_version": "scout-discovery-prefs:99"})
    assert excinfo.value.code == "bad_enum"
