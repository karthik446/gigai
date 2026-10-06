"""0.1.11.3 P10: ``GET /api/postings/ranking``, the light read the Jobs page polls while a rank runs. The REAL Scout server.

Pinned: it answers the same ``ranking`` block as ``GET /api/postings`` (the counts move as postings are ranked); it
refreshes nothing and reads no posting row (``postings.refresh`` and ``PipelineStore.postings`` are never called, so the
cost does not grow with the size of the home); it writes nothing (the project's pipeline file is byte-identical across
reads); it takes no query keys. (That the count moves as a rank runs is pinned in the browser: tests/ui/test_jobs_rank_row.py.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import threading
import urllib.error
import urllib.request

import pytest

from tests.support.posting_fixtures import TITLE_SECOND_ONLY, build_postings_fixture, lever_job


@pytest.fixture
def served(tmp_path, monkeypatch):
    from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve

    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    now = datetime.now(UTC)
    fx.seed(
        "rr", [lever_job("rr", n, title=TITLE_SECOND_ONLY, text=f"Posting {n}: Python services, variant {n}.", created=now - timedelta(hours=n)) for n in (1, 2, 3)],
        seen_at=now - timedelta(minutes=30),
    )
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fx, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_ranking_read_is_the_listed_block_and_reads_no_row_and_writes_nothing(served, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout import postings
    from gigai.scout.pipeline.store import PipelineStore

    fx, url = served
    status, listed = _get(url + "/api/postings?limit=5")  # builds the read model once, as the page's first read does
    assert status == 200 and listed["ranking"]["in_progress"] is True

    refreshes, row_reads = [], []
    real_refresh, real_postings = postings.refresh, PipelineStore.postings
    monkeypatch.setattr(postings, "refresh", lambda *a, **k: refreshes.append(1) or real_refresh(*a, **k))
    monkeypatch.setattr(PipelineStore, "postings", lambda self, *a, **k: row_reads.append(1) or real_postings(self, *a, **k))
    databases = sorted(path for path in fx.home_root.rglob("*") if path.is_file() and path.suffix in {".db", ".sqlite", ".sqlite3"})
    before = {path: path.read_bytes() for path in databases}

    status, read = _get(url + "/api/postings/ranking")
    assert status == 200 and read["schema_version"] == "scout-postings-ranking:1"
    assert read["ranking"] == listed["ranking"] and read["job"] is None
    assert refreshes == [] and row_reads == [], "the light read refreshed the read model or read posting rows"
    assert before and {path: path.read_bytes() for path in databases} == before, "the light read wrote to the pipeline file"


def test_a_query_key_is_refused(served) -> None:
    _fx, url = served
    status, error = _get(url + "/api/postings/ranking?limit=1")
    assert status == 422 and error["error"]["code"] == "unknown_key", error
