"""0.1.11.6 (APPLIED-01): ONE posting read by its address is found whatever hides it from the Jobs list. Synthetic only.

A job page opened by its address used to search the Jobs list for its posting (the first 200 rows, then the first 200
removed ones). Whatever that list leaves out or does not reach had no page: a posting with an application (left out
since 0.1.11.5), a weak fit, a posting past row 200. ``search_postings(jobs=[address])``
(``GET /api/postings?job=<address>``) is the exact read the page uses now:

- a posting with an application (applied, rejected) is a row, with its ``application``; the list still leaves it out;
- a weak fit is a row; the list still leaves it out;
- a removed posting with an application is a row (``removed_at`` set), with no ``removed`` flag asked;
- a posting at row 250 of the list is a row;
- a posting only the profile that is NOT selected matches is a row (the read is of every active profile);
- an address the store does not hold is no row, never an error; a value that is no address is ``invalid_value``;
- the route takes ``job``, raw or normalized, and answers the same row.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen
import uuid

import pytest

from gigai.application_events import record_application
from gigai.scout import posting_search
from gigai.workpad import committed_read_cache

from tests.support.fit_fixtures import weak_fixture
from tests.support.posting_fixtures import NOW, TITLE_SECOND_ONLY, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

SLUG = "acme-health"


def _record(fx: PostingsFixture, url: str, kind: str, occurred_at: str) -> None:
    with committed_read_cache():
        result = record_application(
            resolved=fx.base.gig.resolved,
            data={
                "external_ref": url, "event_kind": kind, "occurred_at": occurred_at, "timezone": "UTC",
                "operation_key": f"posting-by-address-{uuid.uuid4()}",
            },
            confirm=True,
        )
    assert result["status"] == "recorded"


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.seed(SLUG, [lever_job(SLUG, n) for n in range(1, 4)], seen_at=days_ago(1))
    return fixture


def _search(fx: PostingsFixture, **more: object) -> dict:
    return posting_search.search_postings(fx.home_root, fx.target, **{"now": NOW, **more})  # type: ignore[arg-type]


def _jobs(response: dict) -> list[str]:
    return sorted(row["job_identity"] for row in response["postings"]["rows"])


def _one(fx: PostingsFixture, url: str, **more: object) -> dict:
    """The one row of the read by address."""

    found = _search(fx, jobs=[url], **more)
    assert _jobs(found) == [url], f"the read by address did not find {url}: {found['counts']}"
    assert found["filters"]["jobs"] == [url] and found["counts"]["matched"] == 1
    return found["postings"]["rows"][0]


def test_a_posting_with_an_application_is_found_by_its_address_and_the_list_still_leaves_it_out(fx: PostingsFixture) -> None:
    applied, rejected, plain = (job_url(SLUG, n) for n in (1, 2, 3))
    _record(fx, applied, "applied", "2026-10-01T10:00:00Z")
    _record(fx, rejected, "applied", "2026-10-01T11:00:00Z")
    _record(fx, rejected, "rejected", "2026-10-02T11:00:00Z")
    assert _jobs(_search(fx, limit=200)) == [plain], "0.1.11.5: the list leaves the applied ones out"
    assert _one(fx, applied)["application"]["status"] == "applied"
    assert _one(fx, rejected)["application"]["status"] == "rejected"
    assert _one(fx, plain)["application"] is None
    # Named with the profile that owns neither the application nor an assessment: still found (both profiles match it).
    assert set(item["profile_id"] for item in _one(fx, applied)["profiles"]) == {fx.default_profile_id, fx.second_profile_id}
    assert _one(fx, applied, profile_ids=[fx.second_profile_id])["profile_id"] == fx.second_profile_id
    assert _jobs(_search(fx, jobs=[applied, rejected, plain])) == sorted([applied, rejected, plain]), "several addresses, one read"
    # The list is untouched by the new read.
    assert _jobs(_search(fx, limit=200)) == [plain] and _search(fx)["counts"]["applied"] == 2 and "jobs" not in _search(fx)["filters"]


def test_a_weak_fit_is_found_by_its_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    weak = weak_fixture(fixture, monkeypatch)["weak"]
    assert weak not in _jobs(_search(fixture, limit=200)), "the list leaves a weak fit out"
    assert _one(fixture, weak)["state"] == "weak_fit"
    _record(fixture, weak, "applied", "2026-10-01T10:00:00Z")
    row = _one(fixture, weak)
    assert (row["state"], row["application"]["status"]) == ("weak_fit", "applied"), "an applied weak fit too"


def test_a_removed_posting_with_an_application_is_found_by_its_address(fx: PostingsFixture) -> None:
    gone = job_url(SLUG, 1)
    _record(fx, gone, "applied", "2026-10-01T10:00:00Z")
    assert _one(fx, gone)["removed_at"] is None
    later = NOW + timedelta(hours=3)
    fx.seed(SLUG, [lever_job(SLUG, n) for n in (2, 3)], seen_at=later)  # the next sources update: the board no longer lists posting 1
    assert gone not in _jobs(_search(fx, limit=200, now=later)) and gone not in _jobs(_search(fx, limit=200, removed=True, now=later)), "in neither list"
    assert _jobs(_search(fx, limit=200, removed=True, states=["applied"], now=later)) == [gone], "only Applied + Removed lists it"
    row = _one(fx, gone, now=later)
    assert row["removed_at"] is not None and row["application"]["status"] == "applied"
    assert _one(fx, job_url(SLUG, 2), now=later)["removed_at"] is None, "and a live one, with the same read"


def test_a_posting_past_the_first_200_rows_is_found_by_its_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fixture.seed("bulk", [lever_job("bulk", n) for n in range(1, 261)], seen_at=days_ago(1))
    listed = _search(fixture, limit=200)
    assert (listed["counts"]["matched"], listed["counts"]["shown"]) == (260, 200)
    first = set(_jobs(listed))
    beyond = _search(fixture, limit=200, offset=249)["postings"]["rows"][0]["job_identity"]  # row 250
    assert beyond not in first, "the first 200 rows (all the page used to search) do not hold it"
    assert _one(fixture, beyond)["job_identity"] == beyond


def test_a_posting_only_the_other_profile_matches_is_found_by_its_address(fx: PostingsFixture) -> None:
    fx.seed("second-only", [lever_job("second-only", 1, title=TITLE_SECOND_ONLY)], seen_at=days_ago(1))
    job = job_url("second-only", 1)
    _record(fx, job, "applied", "2026-10-01T10:00:00Z")
    row = _one(fx, job)  # no profile is asked for: the read is of every active profile, whichever one the page has selected
    assert row["profile_id"] == fx.second_profile_id and [item["profile_id"] for item in row["profiles"]] == [fx.second_profile_id]
    assert row["application"]["status"] == "applied"
    # Asking for the profile that does not match it is the one way to not find it.
    assert _jobs(_search(fx, jobs=[job], profile_ids=[fx.default_profile_id])) == []


def test_an_address_the_store_does_not_hold_is_no_row_and_a_value_that_is_no_address_is_refused(fx: PostingsFixture) -> None:
    unknown = _search(fx, jobs=[job_url("nobody", 9)])
    assert unknown["postings"]["rows"] == [] and unknown["counts"]["matched"] == 0
    for bad in (["not an address"], []):
        with pytest.raises(posting_search.PostingSearchError) as refused:
            _search(fx, jobs=bad)
        assert refused.value.code == "invalid_value"


def test_the_route_reads_one_posting_by_its_address(fx: PostingsFixture) -> None:
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    applied = job_url(SLUG, 1)
    _record(fx, applied, "applied", "2026-10-01T10:00:00Z")
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api/postings"

    def get(query: str) -> dict:
        with urlopen(f"{base}?{query}", timeout=30) as response:  # noqa: S310 - the test's own server
            return json.loads(response.read())

    try:
        assert applied not in _jobs(get("limit=200"))
        for address in (applied, applied + "/?utm_source=mail"):  # raw: read the way the store keeps it
            found = get(f"job={quote(address, safe='')}&limit=1")
            assert _jobs(found) == [applied] and found["postings"]["rows"][0]["application"]["status"] == "applied"
            assert found["filters"]["jobs"] == [applied]
        with pytest.raises(HTTPError) as refused:
            get("job=nonsense")
        assert refused.value.code == 422 and json.loads(refused.value.read())["error"]["code"] == "invalid_value"
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
