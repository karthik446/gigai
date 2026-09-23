from __future__ import annotations

from pathlib import Path

from gigai.scout.find_jobs.contracts import WatchlistEntry
from gigai.scout.find_jobs.watchlist import JournalWatchlistClient, list_active
from gigai.workpad import resolve_workpad
from tests.behaviors.scout_research.test_scout06_research_inputs import _fixture


def _entry() -> WatchlistEntry:
    from json import loads
    from pathlib import Path

    raw = loads((Path(__file__).parent / "fixtures/fixture-watchlist-v1.json").read_text())
    return WatchlistEntry.from_json(raw["entries"][0])


def test_add_list_and_idempotent_readd_round_trip(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    client = JournalWatchlistClient(home, target, gig_id)
    entry = _entry()

    assert client.add_to_watchlist(entry) == entry
    assert client.add_to_watchlist(entry) == entry
    assert list_active(home, target, gig_id) == (entry,)


def test_list_active_reads_after_fresh_process_style_reload(tmp_path: Path) -> None:
    home, target, gig_id = _fixture(tmp_path)
    entry = _entry()
    JournalWatchlistClient(home, target, gig_id).add_to_watchlist(entry)

    reloaded = list_active(home, target, gig_id)
    assert len(reloaded) == 1
    assert WatchlistEntry.from_json(reloaded[0].to_json()) == entry
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
    assert (resolved.path / "records/scout-watchlist/scout_watchlist:greenhouse:acme.json").is_file()
