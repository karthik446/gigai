"""0.1.10.8 U2 (0110-8-02): a posting with no stored description is fetched on demand, once, before it is assessed. Synthetic only.

The fixture transport (``httpx.MockTransport``) stands in for the public board API; it counts every request. The end outcome is the
assessment RECORD (and the count the response reports), never an intermediate.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gigai.scout import scout_new
from gigai.scout.find_jobs import job_input
from gigai.scout.posting_search import assess_these
from gigai.scout.quick_assess import read_quick_assessment

from tests.support.greenhouse_fixtures import gh_detail, gh_job, gh_url, posting_text, seed_greenhouse
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job


class _Boards:
    """The public Greenhouse single-job and Lever list endpoints, counted. ``removed`` ids answer 404; ``refused`` ids answer 403."""

    def __init__(self, *, removed: tuple[int, ...] = (), refused: tuple[int, ...] = (), lever: list[dict[str, object]] | None = None) -> None:
        self.requests: list[str] = []
        self.removed, self.refused, self.lever = removed, refused, lever or []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.url.host}{request.url.path}")
        if request.url.host == "api.lever.co":
            return httpx.Response(200, json=self.lever)
        job_id = int(request.url.path.rstrip("/").split("/")[-1])  # /v1/boards/acme/jobs/<id>
        if job_id in self.removed:
            return httpx.Response(404, json={"error": "Job not found"})
        if job_id in self.refused:
            return httpx.Response(403, json={})
        return httpx.Response(200, json=gh_detail(gh_job("acme", job_id, TITLE_BOTH), posting_text(job_id)))

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(job_input, "job_fetch_client", lambda: httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=True))


def _new_yes(fx: PostingsFixture) -> dict[str, object]:
    return scout_new.scout_new(fx.home_root, fx.target, now=NOW, assess=True)  # type: ignore[arg-type]


def _record(fx: PostingsFixture, url: str):
    return read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, url)


def test_a_posting_with_no_text_is_fetched_once_and_assessed_and_a_removed_one_names_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    # Two new Greenhouse postings, no description cached for either (a title that passed no rule when the board was last updated).
    seed_greenhouse(fx, "acme", [gh_job("acme", 301, TITLE_BOTH), gh_job("acme", 302, TITLE_BOTH)], seen_at=days_ago(1))
    boards = _Boards(removed=(302,))
    boards.install(monkeypatch)

    done = _new_yes(fx)

    assert boards.requests == ["boards-api.greenhouse.io/v1/boards/acme/jobs/301", "boards-api.greenhouse.io/v1/boards/acme/jobs/302"]  # one each
    assessed = done["assessed"]
    assert assessed["requested"] == 2 and assessed["assessed"] == 1 and assessed["fetched_on_demand"] == 1  # type: ignore[index]
    assert assessed["failed"] == [  # type: ignore[index]
        {"job_identity": gh_url("acme", 302), "profile_id": fx.default_profile_id, "error_code": "job_text_unavailable", "reason": "posting_removed"}
    ]
    assert _record(fx, gh_url("acme", 301)) is not None  # the record
    assert _record(fx, gh_url("acme", 302)) is None
    assert len(fx.base.model.assess_prompts) == 1 and posting_text(301) in fx.base.model.assess_prompts[0]

    # No loop: the next yes asks for the one unassessed posting again, once, and says why again.
    boards.requests.clear()
    again = scout_new.scout_new(fx.home_root, fx.target, now=NOW, assess=True, since=done["since"])  # type: ignore[arg-type]
    assert boards.requests == ["boards-api.greenhouse.io/v1/boards/acme/jobs/302"]
    assert again["assessed"]["failed"][0]["reason"] == "posting_removed"  # type: ignore[index]


def test_a_board_that_refuses_is_a_named_reason_and_a_posting_with_text_is_never_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    seed_greenhouse(fx, "acme", [gh_job("acme", 311, TITLE_BOTH), gh_job("acme", 312, TITLE_BOTH)], seen_at=days_ago(1), details={312: (posting_text(312), "2026-10-02T09:00:00Z")})
    boards = _Boards(refused=(311,))
    boards.install(monkeypatch)

    done = _new_yes(fx)

    assert boards.requests == ["boards-api.greenhouse.io/v1/boards/acme/jobs/311"]  # 312 has its stored text: no request for it
    assessed = done["assessed"]
    assert assessed["assessed"] == 1 and assessed["fetched_on_demand"] == 0  # type: ignore[index]
    assert [(item["error_code"], item["reason"]) for item in assessed["failed"]] == [("job_text_unavailable", "board_refused")]  # type: ignore[index]


def test_assess_these_fetches_a_missing_description_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    seed_greenhouse(fx, "acme", [gh_job("acme", 321, TITLE_BOTH)], seen_at=days_ago(1))
    boards = _Boards()
    boards.install(monkeypatch)

    done = assess_these(fx.home_root, fx.target, jobs=[gh_url("acme", 321)], approve=True, now=NOW)

    assert boards.requests == ["boards-api.greenhouse.io/v1/boards/acme/jobs/321"]
    assert done["assessed"]["assessed"] == 1 and done["assessed"]["fetched_on_demand"] == 1  # type: ignore[index]
    assert _record(fx, gh_url("acme", 321)) is not None


def test_a_lever_posting_with_no_text_is_fetched_through_its_board_list_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    fx.seed("acme", [lever_job("acme", 1, text="")], seen_at=days_ago(1))  # indexed with no description
    boards = _Boards(lever=[lever_job("acme", 1)])
    boards.install(monkeypatch)

    done = _new_yes(fx)

    assert boards.requests == ["api.lever.co/v0/postings/acme"]
    assert done["assessed"]["assessed"] == 1 and done["assessed"]["fetched_on_demand"] == 1  # type: ignore[index]
    assert _record(fx, job_url("acme", 1)) is not None
