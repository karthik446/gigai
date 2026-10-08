"""FB1 (0.1.11.7): a job page opened BY ADDRESS resolves a posting only the company index holds (the APPLIED-01 class).

``GET /api/jobs?url=`` read the posting read model (profile-matched postings) and nothing else, so a posting in a board
file that no profile holds and nothing assessed answered 404 and the page said "We have no stored posting at this
address". Now the route falls back to the company index: the search index when it is current (a keyed narrowing by the
address, then the exact ``normalize_job_identity`` test), else a scan of the company files.

Pinned, on a synthetic home with the index built AND with none:

- an unheld, unassessed posting -> 200 with its company, title, location, address, dates and a null description;
- the same after the index file is deleted (the scan), and after the index goes stale -> the same body;
- an address in no board -> 404 as before, with the index and without it;
- a removed posting resolves and its liveness is ``closed``;
- a posting a profile holds -> the reply is what it was before (the fallback never runs; compared as JSON);
- an address with tracking parameters, a trailing slash or a host in capitals resolves to the same posting;
- the reads write nothing (the home is byte-identical; SQLite's ``-shm`` mtime is the one thing allowed to move).

No request leaves the process (the posting liveness check is off) and no model is called. Every name is made up.
"""

from __future__ import annotations

import hashlib
import json
import os
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote

import pytest

from gigai.scout.find_jobs import company_names, free_search, search_index
from gigai.scout.find_jobs.api.agent_routes import AgentRoutesMixin
from gigai.scout.find_jobs.company_index import CompanyIndex, CompanyIndexEntry, IndexedPosting
from gigai.scout.find_jobs.contracts import normalize_url
from gigai.scout.find_jobs.posting_live import LIVENESS_ENV

from tests.behaviors.scout_find_jobs.test_job_index_join import _URL as HELD_URL, _seed
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture

_STAMP = "2026-10-01T09:00:00Z"
_LIVE = "https://jobs.example.test/zeta/postings/501"
_LIVE_RAW = "https://Jobs.Example.test/zeta/postings/501/?utm_source=newsletter&gh_src=abc"
_GONE = "https://jobs.example.test/zeta/postings/502"
_ROOT = "https://zeta-example.test"
_COMPANY_SITE = "https://www.zeta-example.test/careers?gh_jid=503"
_UNKNOWN = "https://jobs.example.test/zeta/postings/999"


def _posting(posting_id: str, url: str, **more: object) -> IndexedPosting:
    return IndexedPosting(
        posting_id=posting_id, title=f"Platform Engineer {posting_id}", location="Remote, United States", url=url,
        updated_at=None, content_sha256=None, first_seen=_STAMP, last_seen=_STAMP, published_at="2026-09-30T00:00:00Z", published_kind="posted", **more,  # type: ignore[arg-type]
    )


def _write_zeta(home: Path) -> None:
    CompanyIndex.for_home(home).write(CompanyIndexEntry(
        company="Zeta Example", ats="greenhouse", slug="zeta", checked_at=_STAMP, etag=None, body_sha256=None,
        postings={
            "501": _posting("501", _LIVE_RAW),
            "502": _posting("502", _GONE, removed_at="2026-10-03T00:00:00Z"),
            "503": _posting("503", _COMPANY_SITE),
        },
    ))


class _Handler(AgentRoutesMixin):
    """``GET /api/jobs?url=`` as the server routes it, with no socket around it."""

    def __init__(self, fx: PostingsFixture, address: str) -> None:
        self.path = f"/api/jobs?url={quote(address, safe='')}"
        self._backend = type("_Backend", (), {"target": fx.target, "home_root": fx.home_root})()
        self.answer: tuple[int, dict] | None = None

    def _write_json(self, status, body) -> None:
        self.answer = (int(status), body)

    def _error(self, status, code, message) -> None:
        self.answer = (int(status), {"error": code})


def _get(fx: PostingsFixture, address: str) -> tuple[int, dict]:
    handler = _Handler(fx, address)
    handler._handle_get_job()
    assert handler.answer is not None
    status, body = handler.answer
    if status == HTTPStatus.OK:
        body = company_names.with_company_names(body, fx.home_root)  # type: ignore[assignment]
    return status, body


def _snapshot(home: Path) -> dict[str, tuple[int, str]]:
    found: dict[str, tuple[int, str]] = {}
    for base, _dirs, names in os.walk(home):
        for name in names:
            path = Path(base) / name
            found[str(path.relative_to(home))] = (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
    return found


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(LIVENESS_ENV, "off")
    fixture = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _write_zeta(fixture.home_root)
    built = search_index.rebuild_from_index(fixture.home_root)
    assert built.available, built
    yield fixture
    search_index.close(fixture.home_root)


def _delete_index(home: Path) -> None:
    search_index.close(home)
    for path in search_index.search_index_path(home).parent.glob("search.sqlite*"):
        path.unlink()


def test_an_unheld_unassessed_posting_resolves_from_the_index_and_from_a_scan(fx: PostingsFixture) -> None:
    identity = normalize_url(_LIVE)
    status, indexed = _get(fx, _LIVE)
    assert status == 200
    posting = indexed["posting"]
    assert (posting["title"], posting["company"], posting["location"]) == ("Platform Engineer 501", "Zeta Example", "Remote, United States")
    assert (posting["job_identity"], posting["normalized_url"], posting["source_url"]) == (identity, identity, _LIVE_RAW)
    assert (posting["provider"], posting["board_token"], posting["fetch_kind"]) == ("greenhouse", "zeta", "company_index")
    assert (posting["first_seen"], posting["published_at"], posting["removed_at"]) == (_STAMP, "2026-09-30T00:00:00Z", None)
    assert posting["url"] == _LIVE_RAW
    assert posting["text"] is None  # the index keeps no description: said, not invented
    assert indexed["job_identity"] == identity and indexed["liveness"]["state"] != "closed"
    # Everything that needs an assessment or a profile is absent, as for an unassessed job.
    assert indexed["assessments"] == [] and indexed["runs"] == [] and indexed["tailored_resumes"] == []
    assert (indexed["rank"], indexed["h1b"], indexed["index_posting"]) == (None, None, None)

    _delete_index(fx.home_root)
    assert _get(fx, _LIVE) == (200, indexed)  # the scan: the same body

    search_index.rebuild_from_index(fx.home_root)
    _write_zeta(fx.home_root)  # rewritten after the build: the stamp is stale, the index cannot answer
    CompanyIndex.for_home(fx.home_root).path("greenhouse", "zeta").touch()
    assert search_index.rows_with_url_part(fx.home_root, "/zeta/").available is False
    assert _get(fx, _LIVE) == (200, indexed)


@pytest.mark.parametrize("with_index", [True, False])
def test_an_address_in_no_board_is_still_not_found(fx: PostingsFixture, with_index: bool) -> None:
    if not with_index:
        _delete_index(fx.home_root)
    status, body = _get(fx, _UNKNOWN)
    assert (status, body) == (404, {"error": "not_found"})


@pytest.mark.parametrize("with_index", [True, False])
def test_a_removed_posting_resolves_and_is_closed(fx: PostingsFixture, with_index: bool) -> None:
    if not with_index:
        _delete_index(fx.home_root)
    status, body = _get(fx, _GONE)
    assert status == 200
    assert body["posting"]["removed_at"] == "2026-10-03T00:00:00Z"
    assert (body["liveness"]["state"], body["liveness"]["closed_at"]) == ("closed", "2026-10-03T00:00:00Z")


@pytest.mark.parametrize("with_index", [True, False])
@pytest.mark.parametrize(
    "spelling",
    [
        "https://jobs.example.test/zeta/postings/501/",
        "https://JOBS.example.test/zeta/postings/501?utm_medium=x&utm_source=y",
        _LIVE_RAW,
        "https://jobs.example.test:443/zeta/postings/501",
    ],
)
def test_an_address_the_identity_folds_resolves_to_the_same_posting(fx: PostingsFixture, with_index: bool, spelling: str) -> None:
    if not with_index:
        _delete_index(fx.home_root)
    status, body = _get(fx, spelling)
    assert status == 200 and body["job_identity"] == normalize_url(_LIVE) and body["posting"]["title"] == "Platform Engineer 501"


@pytest.mark.parametrize("with_index", [True, False])
def test_a_company_site_address_with_a_query_resolves(fx: PostingsFixture, with_index: bool) -> None:
    if not with_index:
        _delete_index(fx.home_root)
    status, body = _get(fx, _COMPANY_SITE + "&utm_campaign=z")
    assert status == 200 and body["posting"]["title"] == "Platform Engineer 503"
    # Another job on the same path is not it.
    assert _get(fx, "https://www.zeta-example.test/careers?gh_jid=504")[0] == 404
    assert _get(fx, _ROOT)[0] == 404  # a bare host in no board


def test_a_posting_a_profile_holds_reads_as_it_did_before(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import postings

    _seed(fx)
    postings.refresh(fx.home_root, fx.target, now=NOW)
    status, now = _get(fx, HELD_URL)
    assert status == 200 and now["index_posting"] is not None

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the company index is not asked for a posting the read model holds")

    monkeypatch.setattr(free_search, "find_posting", forbidden)
    before = _get(fx, HELD_URL)
    assert json.dumps(before, sort_keys=True) == json.dumps((status, now), sort_keys=True)


@pytest.mark.parametrize("with_index", [True, False])
def test_the_reads_write_nothing(fx: PostingsFixture, with_index: bool) -> None:
    if not with_index:
        _delete_index(fx.home_root)
    for address in (_LIVE, _GONE, _UNKNOWN):  # one read first: scratch caches and SQLite's sidecars settle
        _get(fx, address)
    held = _snapshot(fx.home_root)
    for address in (_LIVE, _GONE, _UNKNOWN, _LIVE_RAW, _COMPANY_SITE):
        _get(fx, address)
    # SQLite's ``-shm`` may move its mtime, never its name or (as a mapped index) its meaning; a size/hash change is a write.
    assert {name: value for name, value in _snapshot(fx.home_root).items() if not name.endswith("-shm")} == {
        name: value for name, value in held.items() if not name.endswith("-shm")
    }
    assert set(_snapshot(fx.home_root)) == set(held)
