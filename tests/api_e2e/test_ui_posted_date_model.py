"""0110-10-14 (the correction): the page's rules for the posted date, the board's last change and the order.

Run under node against the UI's own modules (no browser):

- ``postedLine``: "posted N days ago" only for a posting day (``published_kind: posted``; a Greenhouse row is one now).
  The board's last change (``updated_at``) is said BESIDE it when it is a later day (``updated``), never in its
  place; for a posting with no posting day it is said beside "first seen".
- The order: "Newest posted" is one chip, off by default. On, the list is asked with ``sort=newest_posted`` and the
  address carries it; it is not a filter (nothing is selected by it, "Clear filters" is not offered for it).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import posting_search
from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const m = await import(__URL__);
const input = JSON.parse(process.argv[1]);
const now = Date.parse(input.now);
const call = (rule, ...args) => (typeof rule === "function" ? rule(...args) : null);
const on = { ...m.EMPTY_FILTER, sort: call(m.toggleSort, m.EMPTY_FILTER.sort) };
process.stdout.write(JSON.stringify({
  posted: input.dates.map((row) => m.postedLine(row, now)),
  chip: m.ORDER_CHIP || null,
  sortOn: on.sort,
  sortOff: call(m.toggleSort, on.sort),
  query: [m.postingsQuery(m.EMPTY_FILTER), m.postingsQuery(on), m.postingsQuery({ ...on, window: "7d" }, { offset: 50 })],
  hash: [m.jobsHash(m.EMPTY_FILTER), m.jobsHash(on), m.jobsHash({ ...on, states: ["assessed"] }, 2)],
  parsed: ["#/jobs", "#/jobs?sort=newest_posted", "#/jobs?page=2&state=assessed&sort=newest_posted", "#/jobs?sort=oldest"].map((hash) => m.parseJobsHash(hash)),
  hasFilter: [m.hasFilter(m.EMPTY_FILTER), m.hasFilter(on), m.hasFilter({ ...on, window: "7d" })],
}));
"""

NOW = "2026-10-04T18:00:00Z"
_SEEN = "2026-10-01T12:00:00.000000Z"
DATES = [
    # 0 the operator's posting: up since Aug 2, the board changed it Oct 1.
    {"published_at": "2026-08-02T13:30:00.000000Z", "published_kind": "posted", "updated_at": "2026-10-01T15:15:00.000000Z", "first_seen_at": _SEEN},
    # 1 never changed since: the last change is the posting day itself (a few minutes later).
    {"published_at": "2026-09-24T12:00:00.000000Z", "published_kind": "posted", "updated_at": "2026-09-24T12:05:00.000000Z", "first_seen_at": _SEEN},
    # 2 a board that gives no last change (Lever).
    {"published_at": "2026-09-24T12:00:00.000000Z", "published_kind": "posted", "updated_at": None, "first_seen_at": _SEEN},
    # 3 no posting day, a last change: "first seen", and the change beside it.
    {"published_at": None, "published_kind": None, "updated_at": "2026-09-30T12:00:00.000000Z", "first_seen_at": _SEEN},
    # 4 a board kind that gives only its last change: that IS its one date, said once.
    {"published_at": "2026-10-01T12:00:00.000000Z", "published_kind": "updated", "updated_at": "2026-10-01T12:00:00.000000Z", "first_seen_at": _SEEN},
    # 5 an older server's row: no `updated_at` at all.
    {"published_at": "2026-09-24T12:00:00.000000Z", "published_kind": "posted", "first_seen_at": _SEEN},
]


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the UI model cannot run")
    payload = json.dumps({"dates": DATES, "now": NOW})
    proc = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT.replace("__URL__", json.dumps((UI_SRC / "postingsModel.js").as_uri())), payload],
        capture_output=True, text=True, check=False, env={**os.environ, "TZ": "UTC"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_last_change_is_said_beside_the_posting_day_never_in_its_place(out: dict) -> None:
    edited, same_day, lever, undated, update_only, old_server = out["posted"]

    # Two months old, edited three days ago: "posted 2 months ago", and the change beside it.
    assert (edited["kind"], edited["text"], edited["at"]) == ("posted", "posted 2 months ago", DATES[0]["published_at"])
    assert (edited["updated"]["text"], edited["updated"]["at"]) == ("updated 3 days ago", DATES[0]["updated_at"])
    assert edited["title"].startswith("Posted Aug 2, 2026 (the board's date). The board last changed it Oct 1, 2026.")
    # A last change on the posting day itself, or none: nothing beside the line.
    for line in (same_day, lever, old_server):
        assert (line["kind"], line["text"], line["updated"]) == ("posted", "posted 10 days ago", None)
    # No posting day: the first sighting, with the board's last change beside it. Never "posted".
    assert (undated["kind"], undated["text"], undated["updated"]["text"]) == ("first_seen", "first seen 3 days ago", "updated 4 days ago")
    assert "posted" not in undated["text"] and "Posted" not in undated["title"] and "gives no posting day" in undated["title"]
    # A board kind that gives only its last change says it once.
    assert (update_only["kind"], update_only["text"], update_only["updated"]) == ("updated", "updated 3 days ago", None)

    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert 'data-role="posting-updated"' in page and "posted.updated.text" in page
    assert "posted.updated" not in view  # the Jobs row keeps its one date


def test_newest_posted_is_an_order_the_server_makes_and_the_address_keeps(out: dict) -> None:
    assert out["chip"]["value"] == out["sortOn"] == posting_search.SORT_NEWEST_POSTED and out["sortOff"] is None
    assert out["chip"]["label"] == "Newest posted"
    # Off: the request and the address are what they were. On: the server is asked for the order.
    assert out["query"] == ["limit=50", "sort=newest_posted&limit=50", "window=7d&sort=newest_posted&limit=50&offset=50"]
    assert out["hash"] == ["#/jobs", "#/jobs?sort=newest_posted", "#/jobs?page=2&state=assessed&sort=newest_posted"]
    plain, ordered, paged, unknown = out["parsed"]
    assert (plain["filter"]["sort"], ordered["filter"]["sort"], unknown["filter"]["sort"]) == (None, "newest_posted", None)
    assert (paged["page"], paged["filter"]["states"], paged["filter"]["sort"]) == (2, ["assessed"], "newest_posted")
    # An order selects nothing: it is not a filter.
    assert out["hasFilter"] == [False, False, True]

    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert 'data-testid="order-chip-newest-posted"' in view and "toggleSort(current.sort)" in view
    assert ".sort(" not in view  # the page never sorts the rows itself
