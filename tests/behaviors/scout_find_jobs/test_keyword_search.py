"""0110-026 F2: the keywords of a search filter the tag/rule candidates through the text index.

End outcome first: a posting only the keyword finds (in its description,
not its title) is SELECTED FOR ASSESS by a real acquire pass over the index,
a posting whose text was checked and does not match is not, and a posting
with no stored text is kept and counted. Keywords never add a posting the
profile's titles did not match. Without a text index, or on a SQLite with no
FTS5, the keywords are ignored with a reason and the search runs as before.

No network: the board is an ``httpx.MockTransport``; the text index is
filled directly (``text_index.upsert_company``), so the test says exactly
which posting has stored text and what it is.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs import text_index
from gigai.scout.find_jobs.company_index import CompanyIndex, company_key
from gigai.scout.find_jobs.contracts import ASSESS_ALL, ATSProvider, FindJobsConfig, FindJobsContractError, search_keywords
from gigai.scout.find_jobs.index_search import (
    KEYWORDS_NO_TEXT_INDEX,
    KEYWORDS_TEXT_INDEX_UNAVAILABLE,
    index_line,
    keyword_query,
    read_indexed_boards,
)
from gigai.scout.find_jobs.market_acquisition import BOARDS_FROM_INDEX, acquire_node
from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources
from gigai.scout.find_jobs.text_index import TextPosting

from tests.behaviors.scout_find_jobs.test_acquire_reads_index import _NoBoardClient, _SearchClient
from tests.behaviors.scout_find_jobs.test_acquire_scale import _Exa, _Watchlist, _board, _config, _input, _limits, _managed, _real_context

ROLES = ("software engineer",)
ACME = company_key("greenhouse", "acme")

MATCH = "Software Engineer"                 # description mentions Kubernetes; the title does not
NO_MATCH = "Senior Software Engineer"       # description checked, no keyword in it
NO_TEXT = "Software Engineer, Platform"     # no stored text: cannot be checked
OTHER_ROLE = "Kubernetes Administrator"     # the keyword is in the TITLE, but no profile title matches it

JOBS = [
    {"id": 31, "title": MATCH, "absolute_url": "https://boards.greenhouse.io/acme/jobs/31", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
    {"id": 32, "title": NO_MATCH, "absolute_url": "https://boards.greenhouse.io/acme/jobs/32", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
    {"id": 33, "title": NO_TEXT, "absolute_url": "https://boards.greenhouse.io/acme/jobs/33", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
    {"id": 34, "title": OTHER_ROLE, "absolute_url": "https://boards.greenhouse.io/acme/jobs/34", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
]
TEXT = {
    "31": "You will run our Kubernetes clusters and write Go services.",
    "32": "You will build Rails applications for our billing team.",
    "33": None,
    "34": "Administer Kubernetes for the platform group.",
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/boards/acme/jobs":
        return httpx.Response(200, json={"jobs": JOBS}, headers={"etag": 'W/"acme"'})
    for job in JOBS:
        if path == f"/v1/boards/acme/jobs/{job['id']}":
            return httpx.Response(200, json={**job, "content": f"&lt;p&gt;Posting {job['id']}.&lt;/p&gt;"})
    return httpx.Response(404, json={})


def _config_for(keywords: tuple[str, ...] = ()) -> FindJobsConfig:
    return replace(_config(), roles=ROLES, merged_queries=ROLES, keywords=keywords)


def _update(home: Path) -> None:
    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        result = update_sources(
            [_board(ATSProvider.GREENHOUSE, "acme")], cache=board_cache_for_home(home), index=CompanyIndex.for_home(home),
            client=client, config=_config_for(), limits=_limits(concurrency=1),
        )
    assert result.status == "succeeded", result.to_json()


def _index_text(home: Path) -> None:
    """The text index as an Update sources would leave it: three postings with text, one without."""

    by_id = {str(job["id"]): job["title"] for job in JOBS}
    assert text_index.upsert_company(home, ACME, [TextPosting(posting_id, by_id[posting_id], text) for posting_id, text in TEXT.items()])


def _read(home: Path, keywords: tuple[str, ...]):
    return read_indexed_boards(
        [_board(ATSProvider.GREENHOUSE, "acme")], index=CompanyIndex.for_home(home), cache=board_cache_for_home(home), config=_config_for(keywords),
    )


def _titles(rows) -> list[str]:
    return sorted(row.title for row in rows)


def _run(substrate, keywords: tuple[str, ...], n: int):
    home, target, workpad, gig_id = substrate
    search = _SearchClient()
    with search.client() as client:
        out = acquire_node(
            _real_context(home, target, workpad, gig_id, key=f"keywords-{n}", run_id=f"run_{n:02d}"),
            # "all": no cap and no two-per-company limit, so what is selected is decided by the filters alone.
            replace(_input(_config_for(keywords)), selection_cap=ASSESS_ALL),
            http_client=client, exa=_Exa(), ats=_NoBoardClient(), watchlist=_Watchlist([_board(ATSProvider.GREENHOUSE, "acme")]),
            home_root=home, target=target, limits=_limits(concurrency=1), boards_from=BOARDS_FROM_INDEX,
        )
    assert search.requests == []
    return out


def _selected_titles(out) -> list[str]:
    by_url = {job["absolute_url"]: job["title"] for job in JOBS}
    return sorted(by_url[item.url] for item in out.selected_postings)


# --- the end outcome: what reaches assess ------------------------------------------------


def test_a_keyword_only_match_reaches_assess_and_a_posting_without_text_is_kept(tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    home = substrate[0]
    _update(home)
    _index_text(home)

    out = _run(substrate, ("kubernetes",), 1)

    # The keyword is only in posting 31's description: it is selected for assess. Posting 33 has
    # no stored text, so it could not be checked and stays. Posting 32's text was checked and
    # does not match: dropped. Posting 34 has the keyword in its title but is not a role match:
    # keywords never widen the candidates.
    assert _selected_titles(out) == sorted([MATCH, NO_TEXT])


def test_without_keywords_the_same_search_selects_every_role_match(tmp_path: Path) -> None:
    """The control: the difference above is the keyword's doing, and no keyword means no text index read."""

    substrate = _managed(tmp_path)
    home = substrate[0]
    _update(home)

    out = _run(substrate, (), 1)

    assert _selected_titles(out) == sorted([MATCH, NO_MATCH, NO_TEXT])
    assert not text_index.text_index_path(home).exists()


# --- the index read: what is kept, dropped and counted --------------------------------


def test_the_summary_counts_matched_dropped_and_text_not_checked(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _update(home)
    _index_text(home)

    rows, failures, summary = _read(home, ("kubernetes",))

    assert _titles(rows) == sorted([MATCH, NO_TEXT]) and failures == []
    assert summary["keywords"] == {
        "terms": ["kubernetes"],
        "mode": "filter",
        "applied": True,
        "reason": None,
        "message": None,
        "matched": 1,
        "dropped": 1,
        "text_not_checked": 1,
    }
    assert summary["matched"] == 2 and summary["prefiltered_out"] == 1  # 34 fell to the title rule, before any keyword
    assert "keywords: 1 matched, 1 dropped, 1 kept with text not checked" in index_line(summary)

    _plain_rows, _failures, plain = _read(home, ())
    assert "keywords" not in plain and plain["matched"] == 3


def test_any_one_keyword_is_enough_and_each_is_a_phrase(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _update(home)
    _index_text(home)

    either, _f, summary = _read(home, ("kubernetes", "rails applications"))
    assert _titles(either) == sorted([MATCH, NO_MATCH, NO_TEXT])
    assert (summary["keywords"]["matched"], summary["keywords"]["dropped"]) == (2, 0)

    # "applications rails" is not a phrase of posting 32 ("Rails applications"): a keyword is not a bag of words.
    phrase, _f, summary = _read(home, ("applications rails",))
    assert _titles(phrase) == [NO_TEXT]
    assert (summary["keywords"]["matched"], summary["keywords"]["dropped"], summary["keywords"]["text_not_checked"]) == (0, 2, 1)

    # No stemming, and FTS5 operators typed by the user are plain words inside a phrase.
    stem, _f, summary = _read(home, ("cluster",))
    assert _titles(stem) == [NO_TEXT]
    assert keyword_query(("kubernetes", 'say "hi" OR x')) == '"kubernetes" OR "say ""hi"" OR x"'
    quoted, _f, summary = _read(home, ('kubernetes" OR "rails',))
    assert summary["keywords"]["applied"] is True and _titles(quoted) == [NO_TEXT]

    # A title hit counts too: the text index holds title and description.
    title, _f, _s = _read(home, ("senior",))
    assert _titles(title) == sorted([NO_MATCH, NO_TEXT])


def test_a_posting_the_text_index_has_never_seen_is_kept_and_counted(tmp_path: Path) -> None:
    """A company the text index does not hold (indexed after it was last fed): nothing of it can be checked, so nothing is dropped."""

    home = tmp_path / "home"
    _update(home)
    assert text_index.upsert_company(home, "greenhouse:another", [TextPosting("1", "Engineer", "kubernetes")])
    assert text_index.remove_company(home, ACME)  # the first write built the index from the cache, acme included

    rows, _failures, summary = _read(home, ("kubernetes",))

    assert _titles(rows) == sorted([MATCH, NO_MATCH, NO_TEXT])
    assert (summary["keywords"]["applied"], summary["keywords"]["matched"], summary["keywords"]["dropped"], summary["keywords"]["text_not_checked"]) == (True, 0, 0, 3)


# --- no text index, no FTS5: ignored with a reason, never an error ---------------------


def test_without_a_text_index_the_keywords_are_ignored_with_a_reason(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _update(home)
    assert not text_index.text_index_path(home).exists()

    rows, failures, summary = _read(home, ("kubernetes",))

    assert _titles(rows) == sorted([MATCH, NO_MATCH, NO_TEXT]) and failures == []
    block = summary["keywords"]
    assert (block["applied"], block["reason"]) == (False, KEYWORDS_NO_TEXT_INDEX)
    assert "Run Update sources" in block["message"]
    assert (block["matched"], block["dropped"], block["text_not_checked"]) == (0, 0, 0)
    assert f"keywords ignored ({KEYWORDS_NO_TEXT_INDEX})" in index_line(summary)
    # A search never builds the text index: that is Update sources' work.
    assert not text_index.text_index_path(home).exists()


def test_on_a_sqlite_without_fts5_the_keywords_are_ignored_with_a_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_index, "_FTS_DDL", "CREATE VIRTUAL TABLE text USING no_such_module(title)")
    monkeypatch.setattr(text_index, "_FTS_DDL_NO_DELETE", "CREATE VIRTUAL TABLE text USING no_such_module(title)")
    home = tmp_path / "home"
    _update(home)
    text_index.text_index_path(home).touch()  # a file is there, but this SQLite cannot make an FTS5 table in it

    rows, failures, summary = _read(home, ("kubernetes",))

    assert _titles(rows) == sorted([MATCH, NO_MATCH, NO_TEXT]) and failures == []
    block = summary["keywords"]
    assert (block["applied"], block["reason"]) == (False, KEYWORDS_TEXT_INDEX_UNAVAILABLE)
    assert "FTS5" in block["message"]


def test_the_whole_run_survives_keywords_with_no_text_index(tmp_path: Path) -> None:
    substrate = _managed(tmp_path)
    home = substrate[0]
    _update(home)

    out = _run(substrate, ("kubernetes",), 1)

    assert _selected_titles(out) == sorted([MATCH, NO_MATCH, NO_TEXT])


# --- the contract: an additive optional key ---------------------------------------------


def test_a_config_without_keywords_digests_as_before_and_one_with_them_says_so() -> None:
    plain = _config()
    assert "keywords" not in plain.to_json()
    # Byte-for-byte what a config parsed before this field existed serializes to.
    assert replace(plain, keywords=()).to_json() == plain.to_json() and replace(plain, keywords=()).digest() == plain.digest()
    assert FindJobsConfig.from_json(plain.to_json()).keywords == ()

    with_keywords = replace(plain, keywords=("kubernetes", "machine learning"))
    assert with_keywords.to_json()["keywords"] == ["kubernetes", "machine learning"]
    assert with_keywords.digest() != plain.digest()
    assert FindJobsConfig.from_json(json.loads(json.dumps(with_keywords.to_json()))) == with_keywords
    without_key = {key: value for key, value in with_keywords.to_json().items() if key != "keywords"}
    assert without_key == plain.to_json()


def test_keywords_are_trimmed_deduplicated_and_validated() -> None:
    assert search_keywords(["  machine   learning ", "Kubernetes", "kubernetes", "C++"]) == ("machine learning", "Kubernetes", "C++")
    assert search_keywords([]) == ()
    for bad, code in (
        ("kubernetes", "wrong_type"),
        ([1], "wrong_type"),
        ([None], "wrong_type"),
        (["   "], "invalid_value"),
        (["++"], "invalid_value"),
        (["x" * 101], "invalid_value"),
        ([f"k{n}" for n in range(21)], "invalid_value"),
    ):
        with pytest.raises(FindJobsContractError) as caught:
            search_keywords(bad)
        assert caught.value.code == code, bad
