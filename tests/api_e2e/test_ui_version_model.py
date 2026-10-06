"""0.1.11.2 RANKVIS: "is this tab running the UI the server serves now?", the model run under node.

``ui/src/uiVersionModel.js`` is plain JavaScript: this runs it under the system ``node`` over the REAL built
``index.html`` (``ui/dist``). LOUD skip without ``node``. What lives in JSX is pinned by reading the source.

Pinned: the bundle the built index.html names; the same bundle is never stale (a URL with an origin or a query is the
same bundle); another hashed name is stale; a page that cannot be read, or names no bundle, is never a false alarm.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI = Path(static_module.__file__).resolve().parents[2] / "ui"

SCRIPT = """
import * as m from MODEL_URL;

const html = DATA;
const out = {};
out.served = m.bundleScripts(html);
const mine = out.served[0];
out.same = [m.isStaleUi(html, mine), m.isStaleUi(html, "http://127.0.0.1:8765" + mine), m.isStaleUi(html, mine + "?v=1")];
out.other = m.isStaleUi(html, "/assets/index-OLDBUILD.js");
out.quiet = [m.isStaleUi(null, mine), m.isStaleUi("", mine), m.isStaleUi("<html><body>502</body></html>", mine), m.isStaleUi(html, null), m.isStaleUi(html, "")];
out.classic = m.bundleScripts('<script src="/legacy.js"></script><script type="module" crossorigin src="/assets/a.js"></script>');
out.line = m.UI_UPDATED_LINE;
console.log(JSON.stringify(out));
"""


def test_only_another_bundle_than_the_served_one_is_a_stale_ui() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the UI version model was NOT run")
    html = (UI / "dist" / "index.html").read_text(encoding="utf-8")
    (bundle,) = (UI / "dist" / "assets").glob("index-*.js")
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI / "src" / "uiVersionModel.js").resolve().as_uri())).replace("DATA", json.dumps(html))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    out = json.loads(completed.stdout)
    assert out["served"] == [f"/assets/{bundle.name}"]  # what the built index.html names is the bundle that is shipped
    assert out["same"] == [False, False, False]
    assert out["other"] is True
    assert out["quiet"] == [False, False, False, False, False]  # never a false alarm
    assert out["classic"] == ["/assets/a.js"]
    assert out["line"].startswith("Scout was updated.")


def test_the_banner_is_mounted_beside_the_app_and_never_reloads_by_itself() -> None:
    main = (UI / "src" / "main.jsx").read_text(encoding="utf-8")
    assert "<UpdatedBanner />\n    <App />" in main  # outside the app: on every page, the wizard too
    banner = (UI / "src" / "components" / "UpdatedBanner.jsx").read_text(encoding="utf-8")
    for needle in ('data-testid="ui-updated-banner"', 'data-action="reload-ui"', 'fetch("/", { cache: "no-store"', '"hashchange"', '"visibilitychange"'):
        assert needle in banner, needle
    assert banner.count("location.reload()") == 1 and "onClick={() => window.location.reload()}" in banner  # only on the click
