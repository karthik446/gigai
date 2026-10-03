"""0.1.10.8 U2 (0110-8-03): the sources update fetches a Greenhouse description for a title ANY active profile matches. Synthetic only.

Before: the gate was the SELECTED profile's whole-word rule alone, so a new posting that only the second profile matched, or that
matched only by function tag, never got a description. The end outcome is the description on disk (and the request count the update
reports), through ``load_effective_config`` -> ``update_sources`` exactly as ``gigai scout sources update`` calls them.
"""

from __future__ import annotations

import threading
from pathlib import Path

import httpx
import pytest

from gigai.scout import profile_records
from gigai.scout.find_jobs.company_index import CompanyIndex, cached_posting_rows
from gigai.scout.find_jobs.contracts import ATSProvider
from gigai.scout.find_jobs.sources_update import board_cache_for_home, load_effective_config, update_sources

from tests.behaviors.scout_find_jobs.test_acquire_scale import _board, _limits
from tests.support.greenhouse_fixtures import gh_content, gh_job
from tests.support.posting_fixtures import PostingsFixture, build_postings_fixture
from tests.support.scout_profile_fixtures import uuids

TITLE_DEFAULT = "Staff AI Engineer"  # the default profile's rule
TITLE_SECOND_ONLY = "Staff Engineer"  # the second profile's rule only
TITLE_TAG_ONLY = "Staff Machine Learning Scientist"  # no profile's rule; the default's function tag (staff, ai_ml)
TITLE_ARCHIVED_ONLY = "Marketing Manager"  # only the archived profile's rule
TITLE_NONE = "Account Executive"


class _Board:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[str] = []
        self.jobs = [gh_job("acme", 201, TITLE_DEFAULT)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request.url.path + ("?" + request.url.query.decode() if request.url.query else ""))
        parts = request.url.path.strip("/").split("/")
        if len(parts) == 4:  # /v1/boards/acme/jobs
            if request.url.params.get("content") == "true":
                return httpx.Response(200, json={"jobs": [{**job, "content": gh_content(f"Description {job['id']}.")} for job in self.jobs]})
            return httpx.Response(200, json={"jobs": self.jobs})
        for job in self.jobs:
            if parts[4] == str(job["id"]):
                return httpx.Response(200, json={**job, "content": gh_content(f"Description {job['id']}.")})
        return httpx.Response(404, json={})

    def asked(self) -> list[str]:
        out, self.requests = self.requests, []
        return out


def _update(fx: PostingsFixture, board: _Board) -> dict[str, object]:
    with httpx.Client(transport=httpx.MockTransport(board.handler)) as client:
        result = update_sources(
            [_board(ATSProvider.GREENHOUSE, "acme")], cache=board_cache_for_home(fx.home_root), index=CompanyIndex.for_home(fx.home_root),
            client=client, config=load_effective_config(fx.home_root, fx.target), limits=_limits(concurrency=1), stale_after_hours=0,
            home_root=fx.home_root,
        )
    return result.to_json()


def _has_description(fx: PostingsFixture, job_id: int) -> bool:
    found = cached_posting_rows(board_cache_for_home(fx.home_root), "greenhouse", "acme", [str(job_id)], allow_stale=True)
    return str(job_id) in found.rows and str(job_id) not in found.without_text


def test_a_new_posting_gets_its_description_when_any_active_profile_matches_it_by_rule_or_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)  # default: staff ai engineer; second: staff engineer; a deleted profile
    archived = profile_records.create_profile(
        fx.base.gig.resolved, label="Marketing", titles=("marketing manager",), titles_to_avoid=(), queries=("marketing manager",),
        resume_ref=next(p for p in profile_records.list_profiles(fx.base.gig.resolved) if p.profile_id == fx.default_profile_id).resume_ref,
        uuid_factory=uuids(60),
    )
    profile_records.write_profile(fx.base.gig.resolved, profile_id=archived.profile_id, state="archived", uuid_factory=uuids(61))
    board = _Board()
    _update(fx, board)  # the one-time fill: every posting of the board, with its description
    assert board.asked() == ["/v1/boards/acme/jobs?content=true"]

    board.jobs += [
        gh_job("acme", 202, TITLE_SECOND_ONLY), gh_job("acme", 203, TITLE_ARCHIVED_ONLY),
        gh_job("acme", 204, TITLE_TAG_ONLY), gh_job("acme", 205, TITLE_NONE),
    ]
    snapshot = _update(fx, board)

    asked = board.asked()
    assert sorted(item for item in asked if "/jobs/" in item) == ["/v1/boards/acme/jobs/202", "/v1/boards/acme/jobs/204"]  # one detail each
    assert [_has_description(fx, n) for n in (202, 204)] == [True, True]  # the second profile's, and the tag-only one
    assert [_has_description(fx, n) for n in (203, 205)] == [False, False]  # the archived profile's and nobody's: no request
    # The report names the requests: the list plus the two details, for this board.
    assert snapshot["requests"] == 3 and snapshot["requests_by_board"] == {"greenhouse:acme": 3}
