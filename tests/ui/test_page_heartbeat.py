"""The page's heartbeat (`support.Heartbeat`), on a real page: what a timeout's report reads the page's state from.

U3 saw one browser stall in 20 runs with every core busy: the server idle, the page silent for 57 s,
the next click timing out after 20 s. A retry is forbidden (tests/ui/README.md), so a timeout has to
explain itself, and the page cannot be asked: with tracing on, a call into a page whose script is
stuck waits for the page instead of timing out. So every page tells the harness twice a second that
its script runs and its frames are drawn, and `timeout.txt` reads that record.

The record only matters on a failure, so this plants the stall: the page's script is kept busy for
4 s. The heartbeat must show the silence, and must show the page alive before and after it. The
report's wording is pinned without a browser in `test_harness_support.py`.
"""

from __future__ import annotations

import time

import pytest

from tests.ui import support

pytestmark = pytest.mark.ui

#: How long the planted script holds the page: longer than `support.STALE_SECONDS` plus one beat, so the page is
#: silent by the report's own rule whenever the last beat before it was sent. No beat can be sent during it.
BUSY_MS = 4000
BUSY_JS = "(ms) => { const end = Date.now() + ms; while (Date.now() < end) {} return Date.now(); }"
#: The page has sent this many beats, and has drawn a frame since the given count.
BEATS_JS = "([beats, frames]) => window.__gigaiHeartbeat.count >= beats && window.__gigaiHeartbeat.frames > frames"


def test_the_heartbeat_shows_a_stalled_page_and_a_live_one(ui) -> None:
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.page.wait_for_function(BEATS_JS, arg=[3, 0])  # a live page beats and draws
    live = ui.heartbeat.pulse(time.time())
    assert live.beats >= 2 and live.frames_silent is not None, live
    assert not live.script_stalled and not live.drawing_stalled, live

    before = ui.page.evaluate("() => ({...window.__gigaiHeartbeat})")
    ended_ms = ui.page.evaluate(BUSY_JS, BUSY_MS)  # the page's script, and with it its timers and frames, stops for 4 s
    # As of the last moment of the busy loop (what a timeout in it would have read): no beat since before it began.
    stalled = ui.heartbeat.pulse((ended_ms - 1) / 1000)
    assert stalled.script_silent is not None and stalled.script_silent >= (BUSY_MS / 1000) - support.HEARTBEAT_SECONDS, stalled
    assert stalled.script_stalled, stalled
    reading = support.timeout_reading([], [], stalled)
    assert reading.endswith("the browser stalled, not the server."), reading

    ui.page.wait_for_function(BEATS_JS, arg=[before["count"] + 3, before["frames"]])  # and it comes back
    back = ui.heartbeat.pulse(time.time())
    assert not back.script_stalled and not back.drawing_stalled, back
    assert back.longest_gap >= (BUSY_MS / 1000) - support.HEARTBEAT_SECONDS, back  # the silence stays on the record
