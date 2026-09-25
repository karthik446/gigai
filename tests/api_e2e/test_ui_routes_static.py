"""Q4a-nav: every hash route the Scout UI declares maps to a view, and the
served bundle carries every one of them.

A STATIC check, deliberately (no JS test runner, no browser): the UI's one
route table is ``ROUTES`` in ``ui/src/routing.js`` (the single source every
link, view switch and breadcrumb reads). This test parses that table out of
the source with a regex, then asserts

1. every route's ``view`` is rendered by ``ui/src/App.jsx`` -- either as a
   ``route.view === "<view>"`` branch or as one of the views
   ``views/FindJobsView.jsx`` owns (``jobs`` / ``job`` / ``run``, checked
   the same way there);
2. the top bar's link set (``NAV_VIEWS``) only names views the table has;
3. the packaged, served ``ui/dist`` bundle (``api/static.py``'s own
   ``_ui_dist_root``, i.e. what ``GET /`` and ``GET /assets/…`` hand to the
   browser) contains every route's ``path`` literal, so a stale dist that
   predates a route is a failing test, not a 404-looking blank page.

Hermetic: reads files under ``src/gigai/scout/ui`` only. The bundle check
is skipped (not passed) when ``ui/dist`` is absent, as it is on a source
checkout that never ran ``vite build``; it is never skipped when the dist
exists but is stale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
ROUTING_JS = UI_SRC / "routing.js"
APP_JSX = UI_SRC / "App.jsx"
FIND_JOBS_VIEW_JSX = UI_SRC / "views" / "FindJobsView.jsx"

# One `{ view: "jobs", path: "#/jobs", ... }` entry of the ROUTES array.
_ROUTE_ENTRY = re.compile(r'\{\s*view:\s*"(?P<view>[a-z]+)",\s*path:\s*"(?P<path>#/[^"]*)"')
_ROUTES_BLOCK = re.compile(r"export const ROUTES = \[(?P<body>.*?)\n\];", re.DOTALL)
_NAV_VIEWS = re.compile(r'export const NAV_VIEWS = \[(?P<body>[^\]]*)\]')

# The views the app renders in App.jsx / the ones FindJobsView owns.
_APP_VIEW_BRANCH = re.compile(r'route\.view === "(?P<view>[a-z]+)"')
_FIND_JOBS_OWNED = re.compile(r'const ownsRoute = (?P<expr>[^;]+);')

# Views that are static (top-bar) destinations must have a literal path; a
# parameterised route's path is its prefix (e.g. "#/jobs/").
EXPECTED_ROUTES = {
    "jobs": "#/jobs",
    "job": "#/jobs/",
    "questions": "#/questions",
    "applications": "#/applications",
    "runs": "#/runs",
    "run": "#/runs/",
    "settings": "#/settings",
    "assess": "#/assess",
}


def _route_table() -> dict[str, str]:
    source = ROUTING_JS.read_text(encoding="utf-8")
    block = _ROUTES_BLOCK.search(source)
    assert block is not None, "routing.js must export the ROUTES array (the one route table)"
    routes = {match.group("view"): match.group("path") for match in _ROUTE_ENTRY.finditer(block.group("body"))}
    assert routes, "ROUTES parsed to nothing; the entry shape changed (view/path first)"
    return routes


def _rendered_views() -> set[str]:
    app = APP_JSX.read_text(encoding="utf-8")
    rendered = set(_APP_VIEW_BRANCH.findall(app))
    find_jobs = FIND_JOBS_VIEW_JSX.read_text(encoding="utf-8")
    owned = _FIND_JOBS_OWNED.search(find_jobs)
    assert owned is not None, "FindJobsView.jsx must declare `const ownsRoute = …` naming the routes it renders"
    rendered.update(_APP_VIEW_BRANCH.findall(owned.group("expr")))
    assert "FindJobsView" in app, "App.jsx must mount FindJobsView (it owns the jobs/job/run routes)"
    return rendered


def test_route_table_is_the_expected_set() -> None:
    assert _route_table() == EXPECTED_ROUTES


def test_every_route_has_a_view() -> None:
    routes = _route_table()
    rendered = _rendered_views()
    missing = sorted(view for view in routes if view not in rendered)
    assert not missing, f"routes with no rendered view in App.jsx/FindJobsView.jsx: {missing}"


def test_nav_views_are_routes() -> None:
    routes = _route_table()
    source = ROUTING_JS.read_text(encoding="utf-8")
    nav = _NAV_VIEWS.search(source)
    assert nav is not None, "routing.js must export NAV_VIEWS (the top bar's link order)"
    nav_views = re.findall(r'"([a-z]+)"', nav.group("body"))
    assert nav_views == ["jobs", "questions", "applications", "runs"]
    assert all(view in routes for view in nav_views)


def _served_bundle_text() -> str:
    dist_root = static_module._ui_dist_root()
    if dist_root is None:
        pytest.skip("ui/dist is not built on this checkout (vite build never ran); nothing is served")
    scripts = [entry for entry in (dist_root / "assets").iterdir() if entry.name.endswith(".js")]
    assert scripts, "ui/dist/assets has no JS bundle"
    return "\n".join(entry.read_text(encoding="utf-8") for entry in scripts)


def test_served_bundle_contains_every_route() -> None:
    """The dist the server serves must be built from a source that has every
    route; a route added in ui/src without a rebuild fails here by name."""

    routes = _route_table()
    bundle = _served_bundle_text()

    def present(path: str) -> bool:
        # esbuild emits string literals with whichever quote is shortest,
        # backticks included (`path:`#/jobs``); accept all three.
        return any(f"{quote}{path}{quote}" in bundle for quote in ('"', "'", "`"))

    missing = sorted(f"{view} ({path})" for view, path in routes.items() if not present(path))
    assert not missing, f"served ui/dist bundle lacks these routes (rebuild ui/dist): {missing}"
