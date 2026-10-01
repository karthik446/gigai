"""0110-026d (F3): the one-time Greenhouse ``?content=true`` fill, then plain lists + details for NEW postings only.

Fake transport only (``httpx.MockTransport``), synthetic boards, no network. Updates run through ``update_sources``
with ``home_root`` so the text index follows the company files.
"""

from __future__ import annotations

import threading
from pathlib import Path

import httpx

from gigai.scout.find_jobs import text_index
from gigai.scout.find_jobs.company_index import CompanyIndex
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _config, _limits


class _Gh:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.lock = threading.Lock()
        self.jobs = [
            {"id": 11, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-20T00:00:00Z"},
            {"id": 12, "title": "Marketing Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "location": {"name": "Remote"}, "updated_at": "2026-09-20T00:00:00Z"},
        ]
        self.text = {11: "Tune quasarflux pipelines.", 12: "Plan zebrafish campaigns."}
        self.lever_requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request.url.path + ("?" + request.url.query.decode() if request.url.query else ""))
        parts = request.url.path.strip("/").split("/")
        if request.url.host == "api.lever.co":
            return httpx.Response(200, json=[{"id": "l1", "text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/initech/l1", "categories": {"location": "Austin, TX"}, "country": "US", "descriptionPlain": "Lever only text."}])
        if len(parts) == 4:  # /v1/boards/acme/jobs
            if request.url.params.get("content") == "true":
                return httpx.Response(200, json={"jobs": [{**j, "content": f"&lt;p&gt;{self.text[j['id']]}&lt;/p&gt;"} for j in self.jobs]})
            return httpx.Response(200, json={"jobs": self.jobs})
        for job in self.jobs:
            if parts[4] == str(job["id"]):
                return httpx.Response(200, json={**job, "content": f"&lt;p&gt;{self.text[job['id']]}&lt;/p&gt;"})
        return httpx.Response(404, json={})

    def asked(self) -> list[str]:
        out, self.requests = self.requests, []
        return out


def _run(home: Path, gh: _Gh, boards=None, **kwargs):
    boards = boards if boards is not None else [_board(ATSProvider.GREENHOUSE, "acme")]
    with httpx.Client(transport=httpx.MockTransport(gh.handler)) as client:
        return update_sources(
            boards,
            cache=board_cache_for_home(home),
            index=CompanyIndex.for_home(home),
            client=client,
            config=_config(),
            limits=_limits(concurrency=1),
            stale_after_hours=0,
            home_root=home,
            **kwargs,
        )


def _hits(home: Path, query: str) -> list[str]:
    result = text_index.search(home, query)
    try:
        return [hit.posting_id for hit in result.hits]
    finally:
        text_index.close(home)


def test_the_fill_stores_descriptions_marks_the_board_and_feeds_the_text_index(tmp_path: Path) -> None:
    gh = _Gh()
    _run(tmp_path, gh)
    assert gh.asked() == ["/v1/boards/acme/jobs?content=true"]  # one request, no detail
    cache = board_cache_for_home(tmp_path)
    assert cache.content_filled("greenhouse", "acme")
    # Descriptions live in the cache body, including the title that does not match the roles.
    assert set(cache.filled_jobs("acme")) == {"11", "12"}
    assert _hits(tmp_path, "quasarflux") == ["11"]
    assert _hits(tmp_path, "zebrafish") == ["12"]


def test_the_second_update_asks_for_no_fill_and_no_detail_for_unchanged_postings(tmp_path: Path) -> None:
    gh = _Gh()
    _run(tmp_path, gh)
    gh.asked()
    _run(tmp_path, gh)
    assert gh.asked() == ["/v1/boards/acme/jobs"]  # a plain list only
    _run(tmp_path, gh)
    assert gh.asked() == ["/v1/boards/acme/jobs"]
    assert _hits(tmp_path, "quasarflux") == ["11"] and _hits(tmp_path, "zebrafish") == ["12"]


def test_a_new_posting_gets_exactly_one_detail_request_and_a_non_matching_one_none(tmp_path: Path) -> None:
    gh = _Gh()
    _run(tmp_path, gh)
    gh.asked()
    gh.jobs += [
        {"id": 13, "title": "Staff Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "Denver, CO"}, "updated_at": "2026-09-22T00:00:00Z"},
        {"id": 14, "title": "Office Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/14", "location": {"name": "Remote"}, "updated_at": "2026-09-22T00:00:00Z"},
    ]
    gh.text.update({13: "Operate nebulacore services.", 14: "Order staplers."})
    _run(tmp_path, gh)
    assert gh.asked() == ["/v1/boards/acme/jobs", "/v1/boards/acme/jobs/13"]
    assert _hits(tmp_path, "nebulacore") == ["13"]
    _run(tmp_path, gh)
    assert gh.asked() == ["/v1/boards/acme/jobs"]  # 13 is now known: no second detail


def test_an_updated_at_only_change_makes_no_request_and_keeps_the_text(tmp_path: Path) -> None:
    gh = _Gh()
    _run(tmp_path, gh)
    gh.asked()
    gh.jobs[0] = {**gh.jobs[0], "updated_at": "2026-09-25T00:00:00Z"}
    _run(tmp_path, gh)
    assert gh.asked() == ["/v1/boards/acme/jobs"]  # decision: no detail request for updated_at alone
    assert _hits(tmp_path, "quasarflux") == ["11"]  # the older description is kept (stale beats none)


def test_a_full_refresh_redoes_the_fill(tmp_path: Path) -> None:
    gh = _Gh()
    _run(tmp_path, gh)
    gh.asked()
    _run(tmp_path, gh, full_refresh=True)
    assert gh.asked() == ["/v1/boards/acme/jobs?content=true"]


def test_a_failed_fill_falls_back_to_the_plain_path_and_is_not_marked(tmp_path: Path) -> None:
    gh = _Gh()
    base = gh.handler

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("content") == "true":
            return httpx.Response(500, json={})
        return base(request)

    gh.handler = flaky  # type: ignore[method-assign]
    _run(tmp_path, gh)
    assert not board_cache_for_home(tmp_path).content_filled("greenhouse", "acme")
    assert _hits(tmp_path, "quasarflux") == ["11"]  # the plain two-phase path still fetched 11's detail


def test_lever_is_unaffected(tmp_path: Path) -> None:
    gh = _Gh()
    boards = [_board(ATSProvider.LEVER, "initech")]
    _run(tmp_path, gh, boards)
    asked = gh.asked()
    assert len(asked) == 1 and "content=true" not in asked[0]
    assert not board_cache_for_home(tmp_path).content_filled("lever", "initech")
    assert _hits(tmp_path, "Lever") == ["l1"]
