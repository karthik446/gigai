"""0.1.11.2 RANKVIS, THE CAUSE: a tab left open over an upgrade says "Scout was updated" and offers Reload. A REAL server.

Why the local release check saw no rank row: its browser tab still ran the PREVIOUS build's bundle against the new
server (the old "N weak fits, ranked low: show" line it read exists in that bundle only). A tab that stays open, or is
sent to `#/jobs` (a navigation inside the same document), never reads `index.html` again.

Here the real server (`serve()`, in this process) serves a COPY of the built `ui/dist`; the page is opened; then the
copy is "upgraded" as a release does it (the same bundle under a new hashed name, `index.html` naming it), with the
tab left open. Nothing is routed or stubbed.

Pinned:
- a fresh page shows no banner;
- after the upgrade, the next page change (`hashchange`) shows "Scout was updated…" with "Reload" (the page does not
  reload by itself: it still runs the old bundle);
- Reload loads the new bundle; no banner then, also after another page change (never a false alarm).
"""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from tests.ui.support import tid
from tests.ui.test_jobs_rank_row import RankServer, _seed, rank_home, rank_ui  # noqa: F401  (the fixtures: a real server on a synthetic home)

pytestmark = pytest.mark.ui

BANNER = tid("ui-updated-banner")
RUNNING_JS = "() => new URL(document.querySelector('script[type=\"module\"][src]').src).pathname"


def _served_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The server serves a copy of the built UI (so the test can upgrade it); the bundle's path in it."""

    from gigai.scout.find_jobs import present_api
    from gigai.scout.find_jobs.api import static

    built = static._ui_dist_root()
    assert built is not None, "the UI is not built (src/gigai/scout/ui/dist)"
    copy = tmp_path / "served-dist"
    shutil.copytree(str(built), copy)
    # An absolute part replaces the package's own folder (`Path.joinpath`): the route reads this at every request.
    monkeypatch.setattr(present_api, "_UI_DIST_RELATIVE_PARTS", (str(copy),))
    assert Path(str(static._ui_dist_root())) == copy
    (bundle,) = (copy / "assets").glob("index-*.js")
    return bundle


def test_a_tab_left_open_over_an_upgrade_says_scout_was_updated_and_reload_runs_the_new_ui(
    rank_ui, rank_home: RankServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    ui = rank_ui
    old = _served_copy(tmp_path, monkeypatch)
    _seed(rank_home, 2)

    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()
    assert ui.page.evaluate(RUNNING_JS) == f"/assets/{old.name}"
    assert ui.page.locator(BANNER).count() == 0  # a fresh page: nothing to say

    # THE UPGRADE, with the tab left open: a new hashed bundle, and index.html names it.
    new = old.with_name("index-NEWBUILD1.js")
    old.rename(new)
    index = old.parent.parent / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace(old.name, new.name), encoding="utf-8")

    ui.page.evaluate("() => { window.location.hash = '#/assessments'; }")  # the same document: no reload
    ui.page.locator(BANNER).wait_for()
    assert "Scout was updated. This page still runs the old version" in " ".join((ui.page.locator(BANNER).text_content() or "").split())
    assert ui.page.evaluate(RUNNING_JS) == f"/assets/{old.name}"  # the page did not reload by itself

    ui.network.drop_open("dropped by the reload")
    with ui.page.expect_navigation():
        ui.page.locator(f"{BANNER} [data-action='reload-ui']").click()
    ui.page.wait_for_function("() => !!document.querySelector('.app, .wizard, main')")
    ui.settle()
    # THE OUTCOME: the tab runs the new bundle, and says nothing more, also after another page change.
    assert ui.page.evaluate(RUNNING_JS) == f"/assets/{new.name}"
    assert ui.page.locator(BANNER).count() == 0
    ui.page.evaluate("() => { window.location.hash = '#/jobs'; }")
    ui.wait_for_jobs_list()
    ui.settle()
    assert ui.page.locator(BANNER).count() == 0
    ui.assert_clean()
