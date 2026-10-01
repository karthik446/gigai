"""0110-020: the sources UI never starts an update on its own; Full refresh is explicit.

The UI lane here is static: the JSX is read (no browser), the pure model
runs under the system ``node`` (LOUD skip without it).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"


def _source(*parts: str) -> str:
    return (UI_SRC.joinpath(*parts)).read_text(encoding="utf-8")


def test_only_button_handlers_start_an_update_never_a_profile_change() -> None:
    callers = {
        path.relative_to(UI_SRC).as_posix()
        for path in UI_SRC.rglob("*.js*")
        if "startSourcesUpdate(" in path.read_text(encoding="utf-8") and path.name != "api.js"
    }
    # The two button components are the only callers: the views, the app
    # shell and the profile switch never call it.
    assert callers == {"components/SourcesStrip.jsx", "components/SourcesUpdatePanel.jsx"}
    for name in ("components/SourcesStrip.jsx", "components/SourcesUpdatePanel.jsx"):
        text = _source(*name.split("/"))
        for match in re.finditer(r"startSourcesUpdate\(", text):
            window = text[max(0, match.start() - 400) : match.start()]
            assert "const start = useCallback(" in window, f"{name}: startSourcesUpdate must sit inside the click handler"
    strip = _source("components", "SourcesStrip.jsx")
    assert "useEffect" not in strip, "the strip never starts anything from an effect"
    app = _source("App.jsx")
    switch = app[app.index("function handleSelectProfile") :][:600]
    assert "startSourcesUpdate" not in switch and "sources/update" not in switch


def test_full_refresh_is_an_explicit_control_that_sends_the_flag() -> None:
    strip = _source("components", "SourcesStrip.jsx")
    assert 'data-action="full-refresh-sources-strip"' in strip and "onClick={() => start(true)}" in strip
    assert "onClick={() => start(false)}" in strip
    assert "full_refresh = true" in _source("api.js").replace("body.full_refresh = true", "full_refresh = true")
    assert 'data-action="full-refresh-sources"' in _source("components", "SourcesUpdatePanel.jsx")


SCRIPT = """
import * as m from URL;
const now = new Date("2026-09-29T12:00:00Z").getTime();
const status = { running: false, update: { status: "succeeded", boards: { total: 0, checked: 0, up_to_date: 10370 }, summary: "0 companies with new postings: 0 new, 0 changed, 0 removed" },
  index: { status: "ready", companies_indexed: 10360, last_checked_at: "2026-09-29T11:48:00Z" } };
console.log(JSON.stringify({ strip: m.sourcesStrip(status, { now }), other: m.sourcesStrip(status, { now, hasRun: false }) }));
"""


def test_the_strip_says_updated_n_minutes_ago_up_to_date() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the sources strip model was NOT run")
    script = SCRIPT.replace("URL", json.dumps((UI_SRC / "sourcesStripModel.js").resolve().as_uri()))
    done = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["strip"]["line"] == "Company postings: 10,360 companies stored · updated 12 minutes ago · up to date"
    assert out["strip"]["running"] is False and out["strip"]["amber"] is False
