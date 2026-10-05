"""0.1.10.11: an Ashby posting whose board sends no ``descriptionPlain`` has its text from ``descriptionHtml``. Synthetic only.

Such a posting had no text anywhere it was read: ``scout new`` asked the board for it once more (the 0110-8-02 fetch), got the same
row, and the assessment failed ``job_text_unavailable`` (``no_text``), every time. The board body is put straight into the board cache
under the fixture home and indexed from there (``tests/support/posting_fixtures.py``); the fixture transport stands in for the public
board API and counts every request. The end outcome is the assessment RECORD and the prompt the model was given.

What an assessment reads is parsed from the stored board list each time, so the fix alone makes a posting already in the index
assessable. What the index itself keeps per posting (its content digest, "changed", the keyword index) is written by a sources
update, and an update skips a company whose list is byte-identical: ``company_index._TEXT_REVISION`` (``ashby-text-2``) makes the
next update read each Ashby company once more, as ``lever-text-2`` did for Lever (uat-bug-046).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import httpx
import pytest

from gigai.scout import scout_new
from gigai.scout.find_jobs import job_input, text_index
from gigai.scout.find_jobs.ats_board_clients import posting_content_digest
from gigai.scout.find_jobs.company_index import STATUS_UNTOUCHED, CompanyIndex, board_list_url, company_key, index_stamp, refresh_company
from gigai.scout.find_jobs.contracts import ATSProvider, SourceKind, WatchlistEntry, WatchlistFirstSeen, content_hash, normalize_url
from gigai.scout.find_jobs.market_acquisition import AcquireLimits
from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources
from gigai.scout.find_jobs.watchlist import add_company_from_url
from gigai.scout.quick_assess import read_quick_assessment

from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago

SLUG = "orbit"


def _html(n: int) -> str:
    """``posting_fixtures.posting_text`` as the markup a board sends: the requirements are list items."""

    return (
        f"<p>Posting {n}: own the Python inference services.</p><h3>Requirements</h3>"
        "<ul><li>5+ years of Python in production</li><li>Kubernetes</li><li>Terraform</li><li>GCP experience is a plus</li></ul>"
        "<p>Remote within the United States.</p>"
    )


def _url(n: int) -> str:
    return f"https://jobs.ashbyhq.com/{SLUG}/{SLUG}-{n:05d}"


def _ashby_job(n: int, **description: object) -> dict[str, object]:
    """One Ashby posting with no description field at all unless ``description`` gives one."""

    return {
        "id": f"{SLUG}-{n:05d}", "title": TITLE_BOTH, "jobUrl": _url(n), "location": "Remote - United States",
        "address": {"postalAddress": {"addressCountry": "United States"}}, "workplaceType": "Remote", "isRemote": True,
        "publishedAt": NOW.isoformat(), **description,
    }


def _seed(fx: PostingsFixture, jobs: list[dict[str, object]], *, seen_at: datetime) -> None:
    add_company_from_url(f"https://jobs.ashbyhq.com/{SLUG}", fx.home_root, fx.target)
    body = json.dumps({"jobs": jobs}).encode("utf-8")
    fx.cache.store("ashby", board_list_url("ashby", SLUG), body=body, etag=None, last_modified=None, marker=None)
    refresh_company(fx.index, fx.cache, ats="ashby", slug=SLUG, observed_at=index_stamp(seen_at))


def _board(monkeypatch: pytest.MonkeyPatch, jobs: list[dict[str, object]]) -> list[str]:
    """The public Ashby board endpoint answering ``jobs``; every request is in the returned list."""

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(f"{request.url.host}{request.url.path}")
        return httpx.Response(200, json={"jobs": jobs})

    monkeypatch.setattr(job_input, "job_fetch_client", lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))
    return requests


def test_an_ashby_posting_with_only_description_html_is_assessed_from_its_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    jobs = [_ashby_job(1, descriptionHtml=_html(1)), _ashby_job(2, descriptionPlain="", descriptionHtml=_html(2))]
    _seed(fx, jobs, seen_at=days_ago(1))
    requests = _board(monkeypatch, jobs)  # the board says what the cache holds: asking it again brings nothing more

    done = scout_new.scout_new(fx.home_root, fx.target, now=NOW, assess=True)

    assessed = done["assessed"]
    assert assessed["failed"] == []  # type: ignore[index]
    assert assessed["requested"] == 2 and assessed["assessed"] == 2 and assessed["fetched_on_demand"] == 0  # type: ignore[index]
    assert requests == []  # the text is the indexed board's own: nothing is asked again
    for n in (1, 2):
        assert read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, normalize_url(_url(n))) is not None  # the record
    # What the model was given is the posting's words, never its markup.
    prompts = sorted(fx.base.model.assess_prompts)
    assert len(prompts) == 2 and "<li>" not in "".join(prompts)
    assert "Posting 1: own the Python inference services." in prompts[0] and "5+ years of Python in production" in prompts[0]
    # The index's digest of the posting is of that text (a change of it is what the next update sees).
    entry = fx.index.read("ashby", SLUG)
    assert entry is not None
    text = "Posting 1: own the Python inference services.\nRequirements\n5+ years of Python in production\nKubernetes\nTerraform\nGCP experience is a plus\nRemote within the United States."
    assert entry.postings[f"{SLUG}-00001"].content_sha256 == content_hash((TITLE_BOTH + "\n" + text).encode("utf-8"))


def test_an_ashby_posting_with_no_description_at_all_still_names_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    jobs = [_ashby_job(3)]
    _seed(fx, jobs, seen_at=days_ago(1))
    requests = _board(monkeypatch, jobs)

    done = scout_new.scout_new(fx.home_root, fx.target, now=NOW, assess=True)

    assert requests == [f"api.ashbyhq.com/posting-api/job-board/{SLUG}"]  # the one request for it, as before
    assert done["assessed"]["failed"] == [  # type: ignore[index]
        {"job_identity": normalize_url(_url(3)), "profile_id": fx.default_profile_id, "error_code": "job_text_unavailable", "reason": "no_text"}
    ]


def test_the_on_demand_fetch_reads_description_html_too() -> None:
    """A posting indexed before its board had any description: the one request for it finds the HTML one."""

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"jobs": [_ashby_job(4, descriptionHtml=_html(4))]})))
    with client:
        found = job_input.fetch_missing_description(client, provider="ashby", token=SLUG, posting_id=f"{SLUG}-00004", url=_url(4))
    assert found.fetch_kind == "ats_board" and found.title == TITLE_BOTH
    assert "5+ years of Python in production" in found.text and "<" not in found.text


def test_a_stored_ashby_posting_without_text_gets_its_text_on_the_next_sources_update(tmp_path: Path) -> None:
    """The board's list did not change by a byte; the index an earlier release wrote for it is brought up to the text once."""

    home = tmp_path / "home"
    index, cache = CompanyIndex.for_home(home), board_cache_for_home(home)
    html_only, plain = f"{SLUG}-00005", f"{SLUG}-00006"
    jobs = [_ashby_job(5, descriptionHtml=_html(5)), _ashby_job(6, descriptionPlain="Posting 6: keep the build green.")]
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={"jobs": jobs})

    board = WatchlistEntry(
        watchlist_id=f"scout_watchlist:ashby:{SLUG}", provider=ATSProvider.ASHBY, board_token=SLUG, company=SLUG, state="active",
        first_seen=WatchlistFirstSeen(SourceKind.ATS, f"https://example.test/{SLUG}", "staff ai engineer", "batch-1", "2026-09-22T00:00:00Z"),
    )
    limits = AcquireLimits(concurrency_per_provider=1, min_request_interval_seconds=0.0, time_budget_seconds=None)

    def update() -> dict[str, object]:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return update_sources([board], cache=cache, index=index, client=client, limits=limits, full_refresh=True, home_root=home).to_json()

    key = company_key("ashby", SLUG)
    try:
        update()
        entry = index.read("ashby", SLUG)
        assert entry is not None
        raw_digest = cache.lookup("ashby", board_list_url("ashby", SLUG)).sha256  # type: ignore[union-attr]

        # What the earlier release stored for this very body: the untagged digest, the title-only posting digest, no words to search.
        no_text = replace(entry.postings[html_only], content_sha256=posting_content_digest(TITLE_BOTH, None))
        index.write(replace(entry, body_sha256=raw_digest, postings={**entry.postings, html_only: no_text}))
        kept_text = text_index.TextPosting(plain, TITLE_BOTH, "Posting 6: keep the build green.")
        assert text_index.upsert_company(home, key, [text_index.TextPosting(html_only, TITLE_BOTH, None), kept_text])
        assert text_index.search(home, "Kubernetes", company_keys=[key]).hits == ()

        done = update()  # the next sources update: one request, the same bytes

        assert requests == [f"/posting-api/job-board/{SLUG}"] * 2
        assert [hit.posting_id for hit in text_index.search(home, "Kubernetes", company_keys=[key]).hits] == [html_only]  # found by its words
        after = index.read("ashby", SLUG)
        assert after is not None
        text = "Posting 5: own the Python inference services.\nRequirements\n5+ years of Python in production\nKubernetes\nTerraform\nGCP experience is a plus\nRemote within the United States."
        assert after.postings[html_only].content_sha256 == posting_content_digest(TITLE_BOTH, text)
        assert after.postings[html_only].changed_at == after.checked_at  # it shows as changed, once
        assert after.postings[plain] == replace(entry.postings[plain], last_seen=after.checked_at)  # a posting with plain text: as it was
        assert done["stores"]["text"] == {"companies_written": 1, "companies_removed": 0, "failures": 0}  # type: ignore[index]
        assert after.body_sha256 != raw_digest  # tagged with the text revision

        # And never again for the same body.
        update()
        again = index.read("ashby", SLUG)
        assert again is not None and again.postings[html_only].changed_at == after.postings[html_only].changed_at
        assert refresh_company(index, cache, ats="ashby", slug=SLUG).status == STATUS_UNTOUCHED
    finally:
        text_index.close(home)
