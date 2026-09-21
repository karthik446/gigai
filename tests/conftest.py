"""Conservative, source-based timing lanes for the offline test inventory.

The hook only adds markers.  It never changes fixtures, test order, selection,
assertions, or runtime behavior.  A test is fast-unit eligible only when its
function and statically reachable local helpers do not use filesystem,
persistence, process, network, CLI, concurrency, or mutable-workpad seams.
Unrecognized shapes fall into the integration lane rather than being promoted
by speed or filename.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

_INTEGRATION_TOKENS = frozenset(
    {
        "tmp_path", "tmpdir", "monkeypatch", "capsys", "capfd", "caplog",
        "subprocess", "multiprocessing", "sqlite", "sqlite3", "socket",
        "http", "httpx", "requests", "urllib", "CliRunner", "Process",
        "HTTPServer", "SetupHTTPServer",
        "Thread", "run_setup", "build_config", "initialize_target",
        "launch_run", "start_run", "create_offline", "approve_offline",
        "propose_graph_set_offline", "migrate_workpad_layout", "state.sqlite",
        "write_text", "write_bytes", "mkdir", "mkdtemp", "git",
    }
)
_RELEASE_TOKENS = frozenset(
    {
        "importlib.metadata", "importlib.resources", "setuptools", "wheel",
        "build_wheel", "build_sdist", "release_check",
    }
)


def _function_nodes(tree: ast.AST) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _names(node: ast.AST) -> set[str]:
    result: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            result.add(child.id)
        elif isinstance(child, ast.Attribute):
            result.add(child.attr)
    return result


def _isolation_literals(node: ast.AST) -> set[str]:
    """Return only path literals whose value changes the isolation boundary."""

    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant)
        and child.value == "state.sqlite"
    }


def _arguments(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    return {arg.arg for arg in args} | ({node.args.vararg.arg} if node.args.vararg else set()) | ({node.args.kwarg.arg} if node.args.kwarg else set())


def _import_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                modules.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(
                f"{node.module}.{alias.name}" for alias in node.names
            )
            modules.update(alias.asname or alias.name for alias in node.names)
    return modules


def _has_live_marker(tree: ast.AST) -> bool:
    """Keep explicitly opt-in provider tests out of the offline unit lane."""

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "g30_live":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "g30_live":
            return True
    return False


@lru_cache(maxsize=256)
def classify_source(path_string: str, function_name: str) -> str:
    """Classify a test from isolation requirements, conservatively."""

    path = Path(path_string)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path_string)
    except (OSError, UnicodeError, SyntaxError):
        return "integration"
    functions = _function_nodes(tree)
    root = functions.get(function_name)
    if root is None:
        return "integration"
    if _has_live_marker(tree):
        return "integration"
    imported = _import_modules(tree)
    if imported & {"subprocess", "multiprocessing", "sqlite3", "socket", "http", "httpx", "requests", "urllib"}:
        return "integration"
    if any(module.startswith(("importlib.resources", "importlib.metadata", "setuptools", "wheel")) for module in imported):
        return "release"

    reachable: list[ast.AST] = [root]
    seen: set[str] = {function_name}
    for current in reachable:
        called = _names(current)
        for name, helper in functions.items():
            if name in called and name not in seen:
                seen.add(name)
                reachable.append(helper)

    observed = set().union(
        *(_names(node) | _arguments(node) | _isolation_literals(node) for node in reachable)
    )
    if observed & _RELEASE_TOKENS:
        return "release"
    if observed & _INTEGRATION_TOKENS:
        return "integration"
    return "fast_unit"


def pytest_collection_modifyitems(config, items) -> None:
    del config
    for item in items:
        function_name = item.name.split("[", 1)[0]
        lane = classify_source(str(item.path), function_name)
        item.add_marker(lane)


__all__ = ["classify_source", "pytest_collection_modifyitems"]
