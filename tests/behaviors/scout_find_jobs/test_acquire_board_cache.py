"""A run's own board fetch must never be answered with a bodiless 304 from a snapshot's validators."""

from __future__ import annotations

from pathlib import Path

from gigai.scout.find_jobs import market_acquisition
from gigai.scout.find_jobs.company_index import CompanyIndex
from gigai.scout.find_jobs.sources_update import board_cache_for_home

URL = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"


def test_the_acquire_cache_ignores_index_validators_but_sources_update_keeps_them(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(CompanyIndex, "validators_for_url", lambda self, provider, url: ("etag-from-snapshot", None))
    (tmp_path / "cache" / "scout" / "companies").mkdir(parents=True)

    update_cache = board_cache_for_home(tmp_path)
    assert update_cache.indexed_validators("greenhouse", URL) == ("etag-from-snapshot", None)

    acquire_cache = market_acquisition._board_cache(tmp_path)
    assert acquire_cache is not None
    assert acquire_cache.indexed_validators("greenhouse", URL) is None
