"""0110-024b (P2): the tag query in search, ONE matcher for both call sites.

``title_query.TitleMatcher`` is what ``index_search.read_indexed_boards`` and
``market_acquisition._role_match`` both call. A posting whose title passes the
committed whole-word rule always matches; the profile's roles, tagged by the
same tagger, add the postings whose stored tag has the same level and function.
A posting with no function tag is judged by the rule alone, and with no tag
store nothing changes. Synthetic titles only.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs import index_search, market_acquisition, title_query
from gigai.scout.find_jobs.ats_board_clients import matches_roles
from gigai.scout.find_jobs.company_index import CompanyIndex
from gigai.scout.find_jobs.contracts import ATSProvider, PostingRow, SourceKind
from gigai.scout.find_jobs.index_search import read_indexed_boards
from gigai.scout.find_jobs.market_acquisition import BOARDS_FROM_INDEX, _role_match, acquire_node
from gigai.scout.find_jobs.posting_tags import default_store, normalize_title, tag_new_titles
from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources
from gigai.scout.find_jobs.tag_store import TagStore, TitleTag
from gigai.scout.find_jobs.title_query import TitleMatcher, open_tag_store, tag_query_for_roles, title_matches

from tests.behaviors.scout_find_jobs.test_acquire_reads_index import _NoBoardClient, _SearchClient
from tests.behaviors.scout_find_jobs.test_acquire_scale import _Exa, _Watchlist, _board, _config, _input, _limits, _managed, _real_context

ROLES = ("director of engineering",)

# (title, matched by the 021 rule, matched by the tag query only)
TITLES = [
    ("Director of Engineering", True),
    ("Engineering Director", True),
    ("Director, Engineering", True),
    ("Dir. of Engineering", False),            # abbreviation: tag only (director + software)
    ("Director, Software Development", False),  # same level + function: tag only
    ("VP Engineering", False),                  # other level: stays out
    ("Head of Engineering", False),             # other level: stays out
    ("Director of Marketing", False),           # other function: stays out
    ("Senior Software Engineer", False),        # other level: stays out
    ("Director, Eng", False),                   # the rules find no function for "eng": untagged, rule alone
]
TAG_ONLY = {"Dir. of Engineering", "Director, Software Development"}


def _store(home: Path, titles) -> TagStore:
    store = default_store(home)
    tag_new_titles(store, titles)
    return store


def test_the_tag_query_for_a_role_is_its_level_and_function() -> None:
    assert tag_query_for_roles(("Director of Engineering", "Director of AI")).pairs == {("director", "software"), ("director", "ai_ml")}
    # No function found in the role: no tag query, the plain rule only.
    assert not tag_query_for_roles(("zzz",))
    assert not tag_query_for_roles(("",))


def test_tags_add_matches_and_the_rule_still_always_matches(tmp_path: Path) -> None:
    store = _store(tmp_path, [t for t, _ in TITLES])
    matcher = TitleMatcher(ROLES, store)
    got = {title: matcher.matches(title) for title, _ in TITLES}
    for title, by_rule in TITLES:
        assert matches_roles(title, ROLES) is by_rule
        assert got[title] is (by_rule or title in TAG_ONLY), title
    assert matcher.counts.matched_by_rule == 3 and matcher.counts.matched_by_tag == 2
    # "Director, Eng" has a store row with no function: rule alone.
    assert matcher.counts.untagged_fallback == 1


def test_a_title_missing_from_the_store_is_judged_by_the_rule_alone(tmp_path: Path) -> None:
    store = _store(tmp_path, ["Something Else Entirely"])
    matcher = TitleMatcher(ROLES, store)
    assert matcher.matches("Dir. of Engineering") is False  # not tagged yet: 021 rule says no
    assert matcher.matches("Engineering Director") is True
    assert matcher.counts.untagged_fallback == 1 and matcher.counts.matched_by_tag == 0


def test_a_model_or_rules_row_without_function_falls_back_to_the_rule(tmp_path: Path) -> None:
    store = default_store(tmp_path)
    store.write_rules([TitleTag(normalize_title("Dir. of Engineering"), "director", "rules", None, None, store.tagger_version)])
    assert TitleMatcher(ROLES, store).matches("Dir. of Engineering") is False
    store.set_model_function(normalize_title("Dir. of Engineering"), "software", model="m", prompt_version="tag-v1")
    assert TitleMatcher(ROLES, store).matches("Dir. of Engineering") is True


@pytest.mark.parametrize("roles", [ROLES, ("software engineer", "data engineer"), ("product manager",), ("zzz",)])
def test_no_tag_store_is_exactly_the_old_rule(tmp_path: Path, roles) -> None:
    assert open_tag_store(tmp_path) is None  # nothing created, nothing raised
    assert not (tmp_path / "cache").exists()
    for title, _ in TITLES + [("Staff Software Engineer", False), ("Data Engineer II", False)]:
        assert title_matches(title, roles, None) is matches_roles(title, roles)
        assert TitleMatcher(roles, None).matches(title) is matches_roles(title, roles)


def test_an_unreadable_store_never_raises_and_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "scout" / "tags.sqlite"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a sqlite file" * 100)
    store = open_tag_store(tmp_path)
    assert store is not None
    assert TitleMatcher(ROLES, store).matches("Engineering Director") is True
    assert TitleMatcher(ROLES, store).matches("Dir. of Engineering") is False

    class Broken:
        def get(self, key):
            import sqlite3

            raise sqlite3.OperationalError("disk I/O error")

    matcher = TitleMatcher(ROLES, Broken())  # type: ignore[arg-type]
    assert matcher.matches("Dir. of Engineering") is False
    assert matcher.matches("Engineering Director") is True


def test_both_call_sites_use_the_same_matcher_and_agree(tmp_path: Path, monkeypatch) -> None:
    # The SAME function: _role_match and index search both resolve to title_query.
    assert index_search.TitleMatcher is title_query.TitleMatcher
    assert market_acquisition.title_matches is title_query.title_matches
    assert not hasattr(index_search, "matches_roles") and not hasattr(market_acquisition, "matches_roles")

    store = _store(tmp_path, [t for t, _ in TITLES])
    table = [(roles, title) for roles in (ROLES, ("software engineer",), ("director of ai",)) for title, _ in TITLES]
    seen = {"matcher": 0, "title": 0}
    real_matcher_matches = TitleMatcher.matches
    real_title_matches = title_query.title_matches

    def spy(self, title):
        seen["matcher"] += 1
        return real_matcher_matches(self, title)

    monkeypatch.setattr(TitleMatcher, "matches", spy)
    for roles, title in table:
        row = PostingRow(
            url="https://x.test/1", normalized_url="https://x.test/1", provider=ATSProvider.LEVER, board_token="t", company="t",
            title=title, location="Denver, CO", published_at="2026-09-20T00:00:00Z", content_sha256="sha256:" + "a" * 64,
            source_kind=SourceKind.ATS, query_key="q",
        )
        assert _role_match(row, roles, store) is TitleMatcher(roles, store).matches(title) is real_title_matches(title, roles, store)
    # _role_match went through TitleMatcher.matches once per pair (via title_matches).
    assert seen["matcher"] >= 2 * len(table)


# --- the end outcome: a candidate only the tag query finds reaches assess ------------

JOBS = [
    {"id": 21, "title": "Director of Engineering", "absolute_url": "https://boards.greenhouse.io/acme/jobs/21", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
    {"id": 22, "title": "Dir. of Engineering", "absolute_url": "https://boards.greenhouse.io/acme/jobs/22", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
    {"id": 23, "title": "VP Engineering", "absolute_url": "https://boards.greenhouse.io/acme/jobs/23", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
    {"id": 24, "title": "Director of Marketing", "absolute_url": "https://boards.greenhouse.io/acme/jobs/24", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
]


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/boards/acme/jobs":
        return httpx.Response(200, json={"jobs": JOBS}, headers={"etag": 'W/"acme"'})
    for job in JOBS:
        if path == f"/v1/boards/acme/jobs/{job['id']}":
            return httpx.Response(200, json={**job, "content": f"&lt;p&gt;Lead {job['id']}.&lt;/p&gt;"})
    return httpx.Response(404, json={})


def _update(home: Path, config) -> None:
    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        result = update_sources(
            [_board(ATSProvider.GREENHOUSE, "acme")], cache=board_cache_for_home(home), index=CompanyIndex.for_home(home),
            client=client, config=config, limits=_limits(concurrency=1),
        )
    assert result.status == "succeeded", result.to_json()


def _run(substrate, config, n: int):
    home, target, workpad, gig_id = substrate
    search = _SearchClient()
    with search.client() as client:
        out = acquire_node(
            _real_context(home, target, workpad, gig_id, key=f"tag-query-{n}", run_id=f"run_{n:02d}"),
            _input(config),
            http_client=client, exa=_Exa(), ats=_NoBoardClient(), watchlist=_Watchlist([_board(ATSProvider.GREENHOUSE, "acme")]),
            home_root=home, target=target, limits=_limits(concurrency=1), boards_from=BOARDS_FROM_INDEX,
        )
    assert search.requests == []
    return out


def _selected_titles(out) -> list[str]:
    by_url = {job["absolute_url"]: job["title"] for job in JOBS}
    return sorted(by_url[item.url] for item in out.selected_postings)


def _config_for_roles():
    return replace(_config(), roles=ROLES, merged_queries=("director of engineering",))


def test_a_candidate_only_the_tag_query_finds_reaches_assess(tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    home = substrate[0]
    config = _config_for_roles()
    _update(home, config)
    tag_new_titles(default_store(home), [job["title"] for job in JOBS])

    out = _run(substrate, config, 1)

    selected = _selected_titles(out)
    # On HEAD (b60d504, whole-word rule only) this list is ["Director of Engineering"]:
    # "Dir. of Engineering" fails the rule at index search and again at _role_match.
    assert selected == ["Dir. of Engineering", "Director of Engineering"]
    assert "VP Engineering" not in selected and "Director of Marketing" not in selected


def test_untagged_postings_behave_as_the_021_rule(tmp_path: Path) -> None:
    # Same index, tag store present but holding none of these titles.
    substrate = _managed(tmp_path)
    home = substrate[0]
    config = _config_for_roles()
    _update(home, config)
    tag_new_titles(default_store(home), ["Unrelated Title"])

    out = _run(substrate, config, 1)

    assert _selected_titles(out) == ["Director of Engineering"]


def test_no_tag_store_is_the_old_run(tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    home = substrate[0]
    config = _config_for_roles()
    _update(home, config)
    assert open_tag_store(home) is None

    out = _run(substrate, config, 1)

    assert _selected_titles(out) == ["Director of Engineering"]
    assert not (home / "cache" / "scout" / "tags.sqlite").exists()


def test_index_search_reports_the_title_match_counts(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = _config_for_roles()
    _update(home, config)
    store = _store(home, [job["title"] for job in JOBS])
    boards = [_board(ATSProvider.GREENHOUSE, "acme")]

    rows, _failures, summary = read_indexed_boards(
        boards, index=CompanyIndex.for_home(home), cache=board_cache_for_home(home), config=config, tags=store,
    )
    assert summary["title_match"] == {"matched_by_rule": 1, "matched_by_tag": 1, "untagged_fallback": 0, "vetoed_by_tag": 0, "tag_pending": 0}
    assert summary["prefiltered_out"] == 2

    _rows, _failures, plain = read_indexed_boards(
        boards, index=CompanyIndex.for_home(home), cache=board_cache_for_home(home), config=config,
    )
    assert plain["title_match"] == {"matched_by_rule": 1, "matched_by_tag": 0, "untagged_fallback": 0, "vetoed_by_tag": 0, "tag_pending": 0}
    assert plain["prefiltered_out"] == 3
