"""0.1.11.7 FS1: ``GET /api/search``, the free search over every stored posting. The REAL Scout server.

Pinned: it takes no profile; it answers the object ``gigai scout jobs search --json`` prints; the page comes without a
count and ``count=1`` adds it; a stored posting no profile's list holds is found; the index and the scan answer the
same rows; an unknown key, a bad flag and a request for nothing are refused; and it WRITES NOTHING (every file of the
home is byte-identical across reads, and it refreshes no read model).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import threading
import urllib.error
import urllib.parse
import urllib.request

from click.testing import CliRunner
import pytest

from tests.support.posting_fixtures import TITLE_BOTH, TITLE_SECOND_ONLY, build_postings_fixture, job_url, lever_job

WATCHED, UNWATCHED = "acme-health", "quiet-harbor"


@pytest.fixture
def served(tmp_path, monkeypatch):
    from gigai.scout.find_jobs import search_index
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    now = datetime.now(UTC)
    fx.seed(
        WATCHED, [lever_job(WATCHED, n, title=TITLE_BOTH if n <= 3 else TITLE_SECOND_ONLY, created=now - timedelta(hours=n)) for n in range(1, 6)],
        seen_at=now - timedelta(minutes=30),
    )
    fx.seed(UNWATCHED, [lever_job(UNWATCHED, n, title=TITLE_BOTH, created=now - timedelta(hours=10 + n)) for n in (1, 2)],
            seen_at=now - timedelta(minutes=30), watch=False)
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fx, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
        search_index.close(fx.home_root)


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _search(url: str, **query: object) -> tuple[int, dict]:
    return _get(f"{url}/api/search?{urllib.parse.urlencode(query, doseq=True)}")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_the_search_takes_no_profile_and_pages_first_then_counts(served) -> None:
    from gigai.scout.find_jobs import search_index

    fx, url = served
    status, listed = _get(url + "/api/postings?limit=50")  # the profiles' lists, as the Jobs page builds them
    assert status == 200 and len(listed["postings"]["rows"]) == 5

    # No index yet on this home: the scan answers (and knows the total already).
    status, by_scan = _search(url, title="staff ai engineer")
    assert status == 200 and by_scan["schema_version"] == "scout-free-search:1"
    assert by_scan["source"] == "scan" and by_scan["index"] == {"used": False, "reason": "missing"}
    rows = {row["job_identity"]: row for row in by_scan["postings"]["rows"]}
    assert sorted(rows) == sorted([job_url(WATCHED, n) for n in (1, 2, 3)] + [job_url(UNWATCHED, n) for n in (1, 2)])
    both = {fx.default_profile_id, fx.second_profile_id}
    assert {item["profile_id"] for item in rows[job_url(WATCHED, 1)]["profiles"]} == both
    assert rows[job_url(UNWATCHED, 1)]["profiles"] == [], "a stored posting no profile holds is found, with no label"
    assert [item["profile_id"] for item in by_scan["profiles"]] == [fx.default_profile_id, fx.second_profile_id]
    posted = [row["posted"] for row in by_scan["postings"]["rows"]]
    assert posted == sorted(posted, reverse=True) and by_scan["ranked"] is False

    # With the index: the page has no count; count=1 adds it; the rows are the scan's.
    assert search_index.rebuild_from_index(fx.home_root).available
    status, page = _search(url, title="staff ai engineer")
    assert status == 200 and page["source"] == "index" and page["postings"] == by_scan["postings"]
    assert page["counts"] == {"shown": 5, "more": False, "total": None, "total_all": None, "hidden": None}
    status, counted = _search(url, title="staff ai engineer", count=1)
    assert status == 200 and counted["counts"] == {"shown": 5, "more": False, "total": 5, "total_all": 5, "hidden": 0}
    assert counted["postings"] == page["postings"]

    # Several titles, a page of two, show all, a company word (a whole word).
    status, paged = _search(url, title="Staff AI Engineer, Staff Engineer", limit=2, offset=2, all=1, count=1)
    assert status == 200 and paged["counts"]["shown"] == 2 and paged["counts"]["more"] is True and paged["counts"]["total"] == 7
    assert paged["filters"] is None and paged["query"]["titles"] == ["Staff AI Engineer", "Staff Engineer"]
    status, words = _search(url, title="staff engineer", company="quiet", location="remote", count=1)
    assert status == 200 and {row["company_key"] for row in words["postings"]["rows"]} == {f"lever:{UNWATCHED}"} and words["counts"]["total"] == 2
    status, part = _search(url, title="staff engineer", company="qui")
    assert status == 200 and part["postings"]["rows"] == []

    # The command prints the same object.
    from gigai.cli import cli

    printed = json.loads(CliRunner().invoke(
        cli, ["scout", "jobs", "search", "staff ai engineer", "--json", "--home", str(fx.home_root), "--target", str(fx.target)]
    ).output)
    for key in ("postings", "counts", "profiles", "source", "query", "footer", "ranked", "order", "labels_read"):
        assert printed[key] == counted[key], key


def test_the_search_writes_nothing_and_refreshes_nothing(served, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import postings
    from gigai.scout.find_jobs import search_index

    fx, url = served
    assert _get(url + "/api/postings?limit=5")[0] == 200
    assert search_index.rebuild_from_index(fx.home_root).available
    search_index.close(fx.home_root)
    refreshes = []
    real = postings.refresh
    monkeypatch.setattr(postings, "refresh", lambda *a, **k: refreshes.append(1) or real(*a, **k))

    def reads() -> None:
        for query in ({"title": "staff ai engineer"}, {"title": "staff engineer", "count": 1}, {"title": "staff engineer", "all": 1, "removed": 1, "count": 1},
                      {"company": "acme"}, {"title": "no such title", "count": 1}):
            status, _body = _search(url, **query)
            assert status == 200, query

    reads()  # one read first: the server's connections are open (SQLite's -shm beside a WAL file exists from here on)
    before = _snapshot(fx.home_root)
    reads()
    after = _snapshot(fx.home_root)
    assert sorted(after) == sorted(before) and [name for name in before if after[name] != before[name]] == []
    assert refreshes == [], "the free search refreshed the posting read model"
    # Without the index (the scan runs): the same, and no index is built.
    search_index.close(fx.home_root)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{search_index.search_index_path(fx.home_root)}{suffix}").unlink(missing_ok=True)
    reads()
    before = _snapshot(fx.home_root)
    reads()
    assert _snapshot(fx.home_root) == before and not search_index.search_index_path(fx.home_root).exists()


def test_what_is_refused(served) -> None:
    _fx, url = served
    for query, code in (
        ({"title": "staff engineer", "profile_id": "prof_1"}, "unknown_key"),  # it takes no profile
        ({"title": "staff engineer", "q": "x"}, "unknown_key"),
        ({}, "invalid_value"),
        ({"title": "of the"}, "invalid_value"),
        ({"title": "staff engineer", "all": "yes"}, "invalid_value"),
        ({"title": "staff engineer", "count": "2"}, "invalid_value"),
        ({"title": "staff engineer", "limit": "many"}, "invalid_value"),
        ({"title": "staff engineer", "limit": 201}, "invalid_value"),
        ({"title": "staff engineer", "offset": -1}, "invalid_value"),
    ):
        status, error = _search(url, **query)
        assert status == 422 and error["error"]["code"] == code, (query, error)


def test_the_two_switches_of_0_1_11_8_on_both_routes(served) -> None:
    """``us_only`` and ``collapse`` on GET /api/search and GET /api/postings: what they take, what they answer, what is refused."""

    from gigai.scout.find_jobs import job_copies

    fx, url = served
    # The SAME description for every posting of a title: three Staff AI Engineer and two Staff Engineer at acme-health
    # are two jobs, two Staff AI Engineer at quiet-harbor one. (The fixture's own postings each have their own text.)
    now = datetime.now(UTC)
    same = "Own the Python inference services. Requirements: Python in production; Kubernetes."
    fx.seed(
        WATCHED, [lever_job(WATCHED, n, title=TITLE_BOTH if n <= 3 else TITLE_SECOND_ONLY, text=same, created=now - timedelta(hours=n)) for n in range(1, 6)],
        seen_at=now - timedelta(minutes=20),
    )
    fx.seed(UNWATCHED, [lever_job(UNWATCHED, n, title=TITLE_BOTH, text=same, created=now - timedelta(hours=10 + n)) for n in (1, 2)],
            seen_at=now - timedelta(minutes=20), watch=False)
    assert _get(url + "/api/postings?limit=5")[0] == 200

    # The search: three copies at acme-health and two at quiet-harbor are two rows; collapse=0 lists the five.
    status, found = _search(url, title="staff ai engineer", count=1)
    assert status == 200 and found["counts"]["total"] == 2 and found["query"] == {**found["query"], "us_only": True, "collapse": True}
    assert sorted(row["copies"] for row in found["postings"]["rows"]) == [2, 3]
    assert {row["job_identity"] for row in found["postings"]["rows"]} == {job_url(WATCHED, 3), job_url(UNWATCHED, 2)}, "each row is its earliest posting"
    assert found["us_only"] == {"on": True, "default": True, "rule": job_copies.US_ONLY_RULE}
    assert all(row["location_unclear"] is False for row in found["postings"]["rows"])
    status, each = _search(url, title="staff ai engineer", count=1, collapse=0)
    assert status == 200 and each["counts"]["total"] == 5 and each["query"]["collapse"] is False
    # US only is its own switch: with all=1 it is still on until us_only=0.
    status, with_all = _search(url, title="staff ai engineer", all=1)
    assert status == 200 and with_all["query"]["us_only"] is True and with_all["filters"] is None and with_all["scope_text"] == "US only, any date"
    status, without = _search(url, title="staff ai engineer", all=1, us_only=0)
    assert status == 200 and without["query"]["us_only"] is False and without["us_only"]["on"] is False and without["scope_text"] == "any place, any date"

    # The profiles' list: the same two switches, the same answer shape.
    status, listed = _get(url + "/api/postings?limit=50")
    assert status == 200 and listed["filters"]["us_only"] is True and listed["filters"]["collapse"] is True
    assert listed["us_only"] == found["us_only"] and listed["counts"]["us_only_left_out"] == 0
    assert listed["counts"]["matched"] == 2 and listed["counts"]["postings"] == 5, "three Staff AI Engineer and two Staff Engineer: two rows"
    assert sorted(row["copies"] for row in listed["postings"]["rows"]) == [2, 3]
    assert {row["job_identity"] for row in listed["postings"]["rows"]} == {job_url(WATCHED, 3), job_url(WATCHED, 5)}
    status, every = _get(url + "/api/postings?limit=50&collapse=0&us_only=0")
    assert status == 200 and every["counts"]["matched"] == 5 and every["filters"]["us_only"] is False and every["filters"]["collapse"] is False

    for path in ("/api/search?title=staff&us_only=yes", "/api/search?title=staff&collapse=2", "/api/postings?us_only=maybe", "/api/postings?collapse=x"):
        status, refused = _get(url + path)
        assert status == 422 and refused["error"]["code"] == "invalid_value", path
