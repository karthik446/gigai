"""Free search labels (0.1.11.7 FS-perf): the label read is batched for the page and the count reads no label.

The cost of a label read is its spawns and file opens, so the tests count calls, not seconds: the read model once for
the page, the stored assessments with ONE project lookup for all the jobs, the profile list read on a thread that
starts before the index is read, and no label store opened at all by ``count``. The labels themselves are pinned
by ``test_free_search_labels_cli.py`` (which runs unmodified).
"""

from __future__ import annotations

import sqlite3

from gigai.scout import posting_search, quick_assess
from gigai.scout.find_jobs import free_search
from gigai.scout.find_jobs.free_search import SearchRequest
from tests.behaviors.scout_find_jobs.test_free_search_labels_cli import MOMENT, WATCHED, _by_job, _search, fx  # noqa: F401 - the fixture
from tests.support.posting_fixtures import PostingsFixture, job_url


def _spy(monkeypatch, owner, name):
    calls: list[tuple] = []
    original = getattr(owner, name)

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, spy)
    return calls


def test_one_page_reads_each_label_store_once_for_all_its_rows(fx: PostingsFixture, monkeypatch) -> None:  # noqa: F811
    """Several jobs assessed only by address: one read-model query, one stored-assessment pass, one project lookup."""

    from gigai.scout.pipeline.store import pipeline_path

    jobs = [job_url(WATCHED, n) for n in (1, 2, 3)]
    posting_search.assess_these(fx.home_root, fx.target, jobs=jobs, approve=True, now=MOMENT)
    conn = sqlite3.connect(pipeline_path(fx.home_root, fx.target))
    conn.execute("DELETE FROM posting WHERE job IN (?, ?, ?)", jobs)
    conn.commit()
    conn.close()
    model = _spy(monkeypatch, free_search, "_read_model_rows")
    stored = _spy(monkeypatch, free_search, "_stored_assessments")
    lookups = _spy(monkeypatch, quick_assess, "quick_assess_path")

    rows = _by_job(_search(fx, "staff ai engineer"))

    assert len(model) == 1 and len(stored) == 1 and len(lookups) == 1
    assert set(stored[0][2]) <= set(rows)  # only the page's jobs
    for job in jobs:
        assert rows[job]["assessment"] is not None and rows[job]["assessment"]["state"] != "not_assessed"


def test_a_page_with_a_profile_row_reads_the_list_on_a_thread_and_waits_for_it(fx: PostingsFixture, monkeypatch) -> None:  # noqa: F811
    started = _spy(monkeypatch, free_search._ProfilesAhead, "__init__")
    waited = _spy(monkeypatch, free_search._ProfilesAhead, "wait")

    response = _search(fx, "staff ai engineer")

    assert len(started) == 1 and waited  # always waited for: a thread left at exit would leave a probe file in the workpad
    assert response["profiles"] and any(row["profiles"] for row in response["postings"]["rows"])


def test_the_count_alone_reads_no_label(fx: PostingsFixture, monkeypatch) -> None:  # noqa: F811
    labels = _spy(monkeypatch, free_search, "_labels")
    ahead = _spy(monkeypatch, free_search._ProfilesAhead, "__init__")

    total, total_all = free_search.count(fx.home_root, SearchRequest.typed("staff ai engineer"), target=fx.target, now=MOMENT)

    assert 0 < total <= total_all
    assert labels == [] and ahead == []


def test_a_page_with_no_profile_row_asks_no_profile_list(fx: PostingsFixture, monkeypatch) -> None:  # noqa: F811
    """The list is read on a thread only once a row of the page is in a profile's list; none is: no thread, no list."""

    started = _spy(monkeypatch, free_search._ProfilesAhead, "__init__")

    unwatched = _search(fx, "staff ai engineer", company="quiet harbor")

    assert unwatched["postings"]["rows"] and all(row["profiles"] == [] for row in unwatched["postings"]["rows"])
    assert not unwatched["profiles"] and started == []
