"""0.1.11.9 IDX1: a job the user ASSESSED that no role's list holds is in the Jobs list, once, with its state.

`gigai scout jobs assess <URL>` (and the Search tab) assess a posting the company index holds and no role found.
The assessment was stored as any job's, and then the job was in no list: `gigai scout jobs list` and the Jobs
page showed only what a role tags, so the user could find the job again only by searching for the same title.

- the assessed job is one row of the list (the command, its ``--json`` and ``GET /api/postings`` agree), with its
  state, no role tag (``tags: []``) and the words "in no role: assessed from search" where the tags go;
- the counts count it once; a state filter and the words filter judge it as any row; a role filter (a filter on
  the tags) lists none of it;
- a posting in no role's list that was NOT assessed is not listed;
- a job posted once per country is ONE row with its copies;
- nothing is stored for it in the read model: the rows are derived per read.

Every posting and company is made up. No network, no real home; the model is the fixture's scripted one.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading

from click.testing import CliRunner
import httpx
import pytest

from gigai.cli import cli
from gigai.scout import posting_search, postings
from gigai.scout.find_jobs.api.server import serve
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend

from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job, posting_text

#: A title in no role's list.
TITLE = "Office Coordinator"
BOARD = "quiet-harbor"
ASSESSED, NOT_ASSESSED, COPY_ABROAD, COPY_US = 7, 8, 9, 10
MARKER = "[in no role: assessed from search]"


def _job(n: int, *, text: str | None = None, location: str = "Remote - United States", country: str = "US") -> dict[str, object]:
    job = lever_job(BOARD, n, title=TITLE, text=text, created=days_ago(2))
    job["categories"] = {"location": location}
    job["country"] = country
    return job


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(1))  # one job both roles' lists hold
    same = posting_text(9)
    fixture.seed(
        BOARD,
        [_job(ASSESSED), _job(NOT_ASSESSED), _job(COPY_ABROAD, text=same, location="London, United Kingdom", country="GB"), _job(COPY_US, text=same)],
        seen_at=days_ago(1), watch=False,
    )
    postings.refresh(fixture.home_root, fixture.target, now=NOW)
    return fixture


def _cli(fx: PostingsFixture, *args: str) -> str:
    result = CliRunner().invoke(cli, ["scout", "jobs", *args, "--home", str(fx.home_root), "--target", str(fx.target)])
    assert result.exit_code == 0, result.output
    return result.output


def _list(fx: PostingsFixture, *args: str) -> dict:
    return json.loads(_cli(fx, "list", *args, "--json").strip().splitlines()[-1])


def _jobs(response: dict) -> list[str]:
    return [row["job_identity"] for row in response["postings"]["rows"]]


def _api(fx: PostingsFixture, path: str) -> dict:
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://{server.server_address[0]}:{server.server_address[1]}", timeout=60.0) as client:
            answer = client.get(path)
            assert answer.status_code == 200, answer.text
            return answer.json()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def test_an_assessed_job_no_role_holds_is_one_row_of_the_list_with_its_state(fx: PostingsFixture) -> None:
    url = job_url(BOARD, ASSESSED)
    before = _list(fx)
    assert _jobs(before) == [job_url("acme", 1)] and before["counts"]["matched"] == 1, "only what a role tags, before"

    done = json.loads(_cli(fx, "assess", url, "--yes", "--json").strip().splitlines()[-1])
    assert done["status"] == "assessed" and done["assessed"]["assessed"] == 1, done
    state = done["postings"]["rows"][0]["state"]
    assert state != "not_assessed"

    listed = _list(fx)
    assert sorted(_jobs(listed)) == sorted([job_url("acme", 1), url]), "the assessed job is listed, once; the one not assessed is not"
    row = next(item for item in listed["postings"]["rows"] if item["job_identity"] == url)
    assert row["state"] == state and row["tags"] == [] and row["profiles"] == [] and row["title"] == TITLE
    assert row["assessment"] is not None and row["copies"] == 1
    # The counts count it once, with its state.
    counts = listed["counts"]
    assert counts["matched"] == counts["shown"] == counts["postings"] == 2
    assert counts["by_state"] == {"not_assessed": 1, state: 1}
    assert [item["matched"] for item in listed["profiles"]] == [1, 1], "no role's number counts it: no role tags it"

    # The command says where the tags go that no role found it, and why it is there.
    plain = _cli(fx, "list")
    assert plain.count(url) == 1 and f"{TITLE} {MARKER} " in plain and "2 posting(s) match" in plain

    # The API's list is the same rows and the same counts.
    served = _api(fx, "/api/postings?limit=50")
    assert _jobs(served) == _jobs(listed) and served["counts"] == counts
    assert next(item for item in served["postings"]["rows"] if item["job_identity"] == url)["tags"] == []

    # The filters judge it as any row: its state, the words. A role filter is a filter on the tags.
    assert _jobs(_list(fx, "--state", state)) == [url]
    assert _jobs(_list(fx, "--state", "not_assessed")) == [job_url("acme", 1)]
    assert _jobs(_list(fx, "--query", "coordinator")) == [url]
    for role in (fx.default_profile_id, fx.second_profile_id):
        assert _jobs(_list(fx, "--profile", role)) == [job_url("acme", 1)], role
    assert _jobs(_list(fx, "--removed")) == []

    # Derived per read: the read model stores no row for it.
    store = postings.open_store(fx.home_root, fx.target)
    try:
        assert {item.job for item in store.postings(live=False)} == {job_url("acme", 1)}
    finally:
        store.close()


def test_a_posting_in_no_role_that_was_not_assessed_is_not_listed(fx: PostingsFixture) -> None:
    listed = posting_search.search_postings(fx.home_root, fx.target, now=NOW, us_only=False, collapse=False)

    assert _jobs(listed) == [job_url("acme", 1)] and listed["counts"]["matched"] == listed["counts"]["postings"] == 1


def test_an_assessed_job_posted_once_per_country_is_one_row_with_its_copies(fx: PostingsFixture) -> None:
    abroad, us = job_url(BOARD, COPY_ABROAD), job_url(BOARD, COPY_US)

    done = json.loads(_cli(fx, "assess", us, "--yes", "--json").strip().splitlines()[-1])
    assert done["status"] == "assessed" and done["assessed"]["assessed"] == 1, done

    # One row: the job's US posting, with both copies. US only is off so the copy abroad is one of them.
    listed = _list(fx, "--no-us-only")
    rows = [item for item in listed["postings"]["rows"] if item["tags"] == []]
    assert [item["job_identity"] for item in rows] == [us] and rows[0]["copies"] == 2 and rows[0]["state"] != "not_assessed"
    assert sorted(member["job_identity"] for member in rows[0]["members"]) == sorted([abroad, us])
    assert listed["counts"]["matched"] == 2 and listed["counts"]["postings"] == 3
    # US only (the default): the copy abroad is left out as any posting abroad is; the job is still one row.
    default = _list(fx)
    assert [item["job_identity"] for item in default["postings"]["rows"] if item["tags"] == []] == [us]
    assert default["counts"]["matched"] == 2 and default["counts"]["us_only_left_out"] == 1
    # Each posting on its own line: both copies, each with the job's one state.
    each = _list(fx, "--no-us-only", "--no-collapse")
    assert sorted(item["job_identity"] for item in each["postings"]["rows"] if item["tags"] == []) == sorted([abroad, us])
    assert len({item["state"] for item in each["postings"]["rows"] if item["tags"] == []}) == 1


def test_the_jobs_page_bundle_carries_the_marker_of_a_job_no_role_found() -> None:
    """The Jobs row shows "assessed from search" where the role chips would be (postingsModel.noRoleMarker)."""

    ui = Path(posting_search.__file__).parent / "ui"
    assert 'NO_ROLE_LABEL = "assessed from search"' in (ui / "src" / "postingsModel.js").read_text(encoding="utf-8")
    assert 'data-testid="no-role-marker"' in (ui / "src" / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    bundle = "".join(path.read_text(encoding="utf-8") for path in (ui / "dist" / "assets").glob("*.js"))
    assert "assessed from search" in bundle and "no-role-marker" in bundle
