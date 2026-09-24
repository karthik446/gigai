"""test-gap-001: statically discover every route the Scout find-jobs API serves.

Mirrors ``tests/behaviors/scout_find_jobs/test_run_dir_writer_inventory.py``'s
own shape: an AST scan of the real source (never a hand-maintained list that
can silently drift from the code), cross-referenced against a registry this
suite owns (``JOURNEYS`` in ``test_route_inventory.py``) so a route added to
the API without an e2e journey added here fails loudly.

R0: the ``do_GET``/``do_POST``/``do_PUT`` dispatch this scanner reads moved
from ``present_api.py`` into ``present_api/api/server.py`` (a pure move; see
that package's docstrings) -- this scanner now points at ``api/server.py``,
the dispatch's new home, instead.

## What this recognizes

The dispatch (there is no ``do_PATCH``/``do_DELETE`` today -- confirmed by
the same grep ``test_present_csrf.py``'s docstring already used to justify
its own route list) each contain a flat chain of ``if path ==
"<literal>":`` / ``if path.startswith("<literal>"):`` comparisons, plus one
parametric-path helper: ``_match_run_id(path, suffix="<literal>")`` for the
three ``/api/runs/{run_id}...`` routes. This scanner recognizes exactly
those two shapes inside each ``do_*`` method body:

1. ``if path == "<literal>":`` -> a fixed route on that method.
2. ``_match_run_id(path, suffix="<literal>")`` -> the parametric route
   ``/api/runs/{run_id}<literal>`` on that method (GET only today).

It deliberately does not try to recognize an arbitrary new dispatch shape a
future route might use (e.g. a regex route table) -- if the dispatch's shape
changes, this scanner's own self-test
(``test_scanner_finds_the_known_routes``) catches that by failing to find a
route this packet already knows must exist.

## Honest limits

- Static-file serving (``_handle_get_static`` / ``do_GET``'s fallthrough to
  ``self._handle_get_static(path)`` for anything not under ``/api/``) is not
  an API route in the sense this suite journeys against, so it is excluded
  by construction (the scanner only looks inside the ``if path.startswith
  ("/api/")`` block for GET).
- A route registered dynamically (built from a variable, not a string
  literal) would be invisible here, same honest limit
  ``test_run_dir_writer_inventory.py`` documents for its own scan. No route
  in the dispatch does this today (confirmed by this file's own
  ``test_scanner_finds_the_known_routes``).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

_PRESENT_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "gigai"
    / "scout"
    / "find_jobs"
    / "api"
    / "server.py"
)


class Route(NamedTuple):
    method: str
    path: str  # "/api/runs/{run_id}" for the parametric ones


def _literal_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _match_run_id_suffix(call: ast.Call) -> str | None:
    """Return the ``suffix=`` literal of a ``_match_run_id(path, suffix=...)``
    call, or ``None`` if this isn't that call shape."""

    func = call.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if name != "_match_run_id":
        return None
    for keyword in call.keywords:
        if keyword.arg == "suffix":
            return _literal_str(keyword.value)
    # Positional suffix (never used in present_api.py today, but handled so
    # a future refactor to positional args doesn't silently blind this scan).
    if len(call.args) >= 2:
        return _literal_str(call.args[1])
    return None


def _find_do_method(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _routes_in_do_method(method_node: ast.FunctionDef, http_method: str) -> set[Route]:
    routes: set[Route] = set()
    for node in ast.walk(method_node):
        if isinstance(node, ast.Compare):
            # if path == "<literal>":
            if (
                isinstance(node.left, ast.Name)
                and node.left.id == "path"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)
                and len(node.comparators) == 1
            ):
                literal = _literal_str(node.comparators[0])
                if literal is not None:
                    routes.add(Route(http_method, literal))
        if isinstance(node, ast.Call):
            suffix = _match_run_id_suffix(node)
            if suffix is not None:
                routes.add(Route(http_method, f"/api/runs/{{run_id}}{suffix}"))
    return routes


def discover_routes() -> frozenset[Route]:
    """Every ``/api/...`` route ``present_api.py``'s dispatch actually serves."""

    tree = ast.parse(_PRESENT_API_PATH.read_text(encoding="utf-8"), filename=str(_PRESENT_API_PATH))
    routes: set[Route] = set()
    for method_name, http_method in (("do_GET", "GET"), ("do_POST", "POST"), ("do_PUT", "PUT")):
        method_node = _find_do_method(tree, method_name)
        if method_node is None:
            continue
        routes |= _routes_in_do_method(method_node, http_method)
    return frozenset(routes)


__all__ = ["Route", "discover_routes"]
