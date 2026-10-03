"""0.1.10.8 docs item 6: the one-time network notice before the very first Update sources.

``ui/src/networkNoticeModel.js`` is pure JavaScript, run under the system ``node`` (LOUD skip when it is not on
PATH). "First" is the server's fact (the store holds no company yet); "seen" is remembered per browser, with every
storage call wrapped. What lives in JSX is pinned by reading the source.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import wording
from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
const m = await import(process.argv[1]);
const w = await import(process.argv[2]);
const memory = () => { const data = {}; return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } }; };
const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
const empty = { index: { status: "empty", companies_indexed: 0, last_checked_at: null }, update: null };
const emptyRunning = { index: { status: "empty", companies_indexed: 0 }, update: { status: "running", boards: { total: 0 } } };
const ready = { index: { status: "ready", companies_indexed: 10360, last_checked_at: "2026-09-29T09:00:00Z" }, update: null };
const stale = { index: { status: "stale", companies_indexed: 12 }, update: null };
const fresh = memory();
const before = m.needsNetworkNotice(empty, fresh);
const marked = m.markNoticeSeen(fresh);
console.log(JSON.stringify({
  key: m.NETWORK_NOTICE_KEY,
  settingsLine: m.NETWORK_NOTICE_SETTINGS_LINE,
  lead: w.NETWORK_NOTICE_LEAD,
  body: w.NETWORK_NOTICE_BODY,
  first: { empty: m.isFirstUpdate(empty), emptyRunning: m.isFirstUpdate(emptyRunning), ready: m.isFirstUpdate(ready), stale: m.isFirstUpdate(stale), unread: m.isFirstUpdate(null), noIndex: m.isFirstUpdate({}) },
  before, marked, stored: fresh.data, after: m.needsNetworkNotice(empty, fresh),
  readyUnseen: m.needsNetworkNotice(ready, memory()),
  unread: m.needsNetworkNotice(null, memory()),
  noStorage: { needs: m.needsNetworkNotice(empty, null), marked: m.markNoticeSeen(null), seen: m.noticeSeen(null) },
  throwing: { needs: m.needsNetworkNotice(empty, throwing), marked: m.markNoticeSeen(throwing), seen: m.noticeSeen(throwing), ready: m.needsNetworkNotice(ready, throwing) },
  otherValue: m.noticeSeen({ getItem: () => "yes" }),
}));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the network notice model was NOT run")
    done = subprocess.run(
        [node, "--input-type=module", "-e", SCRIPT, (UI_SRC / "networkNoticeModel.js").as_uri(), (UI_SRC / "wording.js").as_uri()],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_notice_is_the_one_wording_constant(out: dict) -> None:
    assert out["lead"] == wording.NETWORK_NOTICE_LEAD and out["body"] == wording.NETWORK_NOTICE_BODY
    assert out["settingsLine"] == "Background checks can be turned off in Settings > Background updates."


def test_it_is_asked_only_before_the_very_first_update(out: dict) -> None:
    # "First" = the store holds no company yet, running or not; a store that was ever filled is not first.
    assert out["first"] == {"empty": True, "emptyRunning": True, "ready": False, "stale": False, "unread": False, "noIndex": False}
    assert out["before"] is True
    assert out["readyUnseen"] is False, "a store with postings never asks, whatever the browser remembers"
    assert out["unread"] is False, "nothing is asked before the status was read (the buttons are off then)"


def test_it_is_shown_once_per_browser(out: dict) -> None:
    assert out["marked"] is True and out["stored"] == {out["key"]: "1"} and out["after"] is False
    assert out["key"] == "scout.networkNotice.seen"
    assert out["otherValue"] is False


def test_storage_that_is_missing_or_throws_never_breaks_the_update(out: dict) -> None:
    # No usable storage: the notice shows (it cannot be remembered) and nothing throws; once the store holds
    # postings the server's own fact stops it.
    assert out["noStorage"] == {"needs": True, "marked": False, "seen": False}
    assert out["throwing"] == {"needs": True, "marked": False, "seen": False, "ready": False}


def test_both_update_sources_buttons_go_through_the_notice() -> None:
    dialog = (UI_SRC / "components" / "NetworkNotice.jsx").read_text(encoding="utf-8")
    assert 'data-testid="network-notice"' in dialog
    assert "<strong>{NETWORK_NOTICE_LEAD}</strong> {NETWORK_NOTICE_BODY}" in dialog
    assert "{NETWORK_NOTICE_SETTINGS_LINE}" in dialog and "SETTINGS_HASH" in dialog
    # Continue remembers, then starts; Cancel starts nothing.
    assert "markNoticeSeen(browserStorage())" in dialog and "onCancel={() => setPending(null)}" in dialog
    strip = (UI_SRC / "components" / "SourcesStrip.jsx").read_text(encoding="utf-8")
    assert strip.count("notice.guard(() => start(") == 2 and "onClick={() => start(" not in strip and "{notice.dialog}" in strip
    assert "<SourcesStrip strip={strip} read={sources.read} status={sources.status} />" in (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    panel = (UI_SRC / "components" / "SourcesUpdatePanel.jsx").read_text(encoding="utf-8")
    assert panel.count("onClick={() => guardedStart(") == 3 and "onClick={() => start(" not in panel and "{notice.dialog}" in panel
    # The sentences live in wording.js only; the model and the dialog import them.
    for path in UI_SRC.rglob("*.js*"):
        if path.name != "wording.js":
            assert wording.NETWORK_NOTICE_LEAD not in path.read_text(encoding="utf-8"), path.name
    # localStorage is touched only through the wrapped helpers.
    assert "localStorage" not in dialog and "window.localStorage" not in (UI_SRC / "networkNoticeModel.js").read_text(encoding="utf-8").split("import")[1]
