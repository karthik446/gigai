"""uat-bug-033: Exa is an optional extra, off for a new setup.

Rule for existing configs: ``sources.exa`` is a REQUIRED key of the config
contract (``SourceToggles.from_json``), so it is never defaulted on read and
an existing ``find-jobs.json`` keeps exactly what it saved. Only a NEWLY
written config (the starter file, a first ``PUT /api/setup``) writes
``exa: false``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout.find_jobs.contracts import FindJobsConfig, FindJobsContractError
from gigai.scout.find_jobs.market_acquisition import acquire_node
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend
from gigai.scout.scout_cli import STARTER_FIND_JOBS_CONFIG, write_starter_find_jobs_config
from tests.behaviors.scout_find_jobs.test_acquire_scale import (
    _Watchlist,
    _config,
    _context,
    _input,
    _limits,
    _patch_import,
)


class _CountingExa:
    """The fake Exa seam: counts every ``search`` call."""

    def __init__(self) -> None:
        self.calls = 0

    def search(self, client, config, *, home_root=None):
        self.calls += 1
        return ()


class _NoATS:
    def fetch_board(self, *args, **kwargs):  # pragma: no cover - ats is off in these runs
        raise AssertionError("ats is off")


def _acquire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, exa: bool, exa_client: _CountingExa) -> None:
    _patch_import(monkeypatch)
    acquire_node(
        _context(tmp_path),
        _input(_config(ats=False, exa=exa)),
        http_client=None,
        exa=exa_client,
        ats=_NoATS(),
        watchlist=_Watchlist([]),
        limits=_limits(concurrency=1),
    )


def test_a_new_starter_config_has_exa_off(tmp_path: Path) -> None:
    assert STARTER_FIND_JOBS_CONFIG.sources.exa is False
    assert STARTER_FIND_JOBS_CONFIG.sources.ats is True
    assert write_starter_find_jobs_config(tmp_path) is True
    written = json.loads((tmp_path / "find-jobs.json").read_text())
    assert written["sources"] == {"ats": True, "exa": False, "hiringcafe": False}


def test_a_search_with_exa_off_makes_zero_exa_requests(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exa = _CountingExa()
    _acquire(monkeypatch, tmp_path, exa=False, exa_client=exa)
    assert exa.calls == 0


def test_the_same_search_with_exa_on_does_ask_exa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Control: the counter really sees a call when Exa is on."""

    exa = _CountingExa()
    _acquire(monkeypatch, tmp_path, exa=True, exa_client=exa)
    assert exa.calls == 1


def _old_config(exa: bool) -> dict:
    return {**STARTER_FIND_JOBS_CONFIG.to_json(), "sources": {"exa": exa, "ats": True, "hiringcafe": False}}


def test_an_old_config_with_exa_true_keeps_it() -> None:
    assert FindJobsConfig.from_json(_old_config(True)).sources.exa is True
    assert FindJobsConfig.from_json(_old_config(False)).sources.exa is False


def test_a_config_without_the_exa_key_is_refused_never_defaulted() -> None:
    old = _old_config(True)
    old["sources"] = {"ats": True, "hiringcafe": False}
    with pytest.raises(FindJobsContractError):
        FindJobsConfig.from_json(old)


def test_the_setup_save_keeps_an_existing_files_exa_value(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    path = target / "find-jobs.json"
    path.write_text(json.dumps(_old_config(True)))
    backend = ScoutFindJobsBackend(home_root=tmp_path / "home", target=target)
    backend._update_find_jobs_config(
        {
            "roles": ["Staff Engineer"],
            "work_mode": "remote",
            "city": None,
            "countries": ["US"],
            "visa_sponsorship_required": False,
        }
    )
    assert json.loads(path.read_text())["sources"]["exa"] is True


def test_the_setup_save_of_a_new_config_writes_exa_off(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    backend = ScoutFindJobsBackend(home_root=tmp_path / "home", target=target)
    backend._update_find_jobs_config(
        {
            "roles": ["Staff Engineer"],
            "work_mode": "remote",
            "city": None,
            "countries": ["US"],
            "visa_sponsorship_required": False,
        }
    )
    assert json.loads((target / "find-jobs.json").read_text())["sources"]["exa"] is False


def test_set_exa_source_changes_only_that_toggle(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    path = target / "find-jobs.json"
    path.write_text(json.dumps(_old_config(False)))
    backend = ScoutFindJobsBackend(home_root=tmp_path / "home", target=target)
    backend.set_exa_source(True)
    after = json.loads(path.read_text())
    assert after["sources"] == {"exa": True, "ats": True, "hiringcafe": False}
    assert {k: v for k, v in after.items() if k != "sources"} == {k: v for k, v in _old_config(False).items() if k != "sources"}
    backend.set_exa_source(False)
    assert json.loads(path.read_text())["sources"]["exa"] is False
