"""Conservative, source-based timing lanes for the offline test inventory.

The hook only adds markers.  It never changes fixtures, test order, selection,
assertions, or runtime behavior.  A test is fast-unit eligible only when its
function and statically reachable local helpers do not use filesystem,
persistence, process, network, CLI, concurrency, or mutable-workpad seams.
Unrecognized shapes fall into the integration lane rather than being promoted
by speed or filename.

ci-shard-suite: the one exception to "never changes selection" is opt-in.
When GIGAI_TEST_SHARDS and GIGAI_TEST_SHARD are both set (CI's source-tests
jobs), only the test files of that shard stay selected and the rest are
reported as deselected; tests/support/sharding.py owns the rule.  With either
unset, selection is untouched.
"""

from __future__ import annotations

import ast
import os
from functools import lru_cache
from pathlib import Path

import pytest

from tests.support.scout_servers import orphaned_test_servers, scout_test_servers, stop_test_servers
from tests.support.sharding import ShardUsageError, requested_shard, split_items

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


def _requested_shard(environ) -> tuple[int, int] | None:
    try:
        return requested_shard(os.environ if environ is None else environ)
    except ShardUsageError as exc:
        raise pytest.UsageError(str(exc)) from exc


def _apply_shard(config, items, environ=None) -> None:
    """Keep only this shard's test files; see tests/support/sharding.py."""

    requested = _requested_shard(environ)
    if requested is None:
        return
    shard, shards = requested
    try:
        kept, deselected = split_items(
            items, shard=shard, shards=shards, root=config.rootpath
        )
    except ShardUsageError as exc:
        raise pytest.UsageError(str(exc)) from exc
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


def pytest_configure(config) -> None:
    # Bad shard values stop the run here, before collection and before xdist
    # starts a worker, as one usage error rather than one per worker.
    del config
    _requested_shard(None)


def pytest_report_header(config) -> list[str] | None:
    del config
    lines: list[str] = []
    requested = _requested_shard(None)
    if requested is not None:
        shard, shards = requested
        lines.append(f"gigai shard: {shard}/{shards} (test files by sha1 of repo-relative path)")
    # 0110-028: a pytest process that was killed cannot stop the Scout servers
    # its tests started. Say so at the next start; nothing is signalled here
    # (they belong to another session, which may be a concurrent one's parent).
    orphans = orphaned_test_servers()
    if orphans:
        lines.append(f"gigai: {len(orphans)} Scout test server(s) left running by an earlier pytest session: pids {[server.pid for server in orphans]}")
    return lines or None


def _session_basetemp(config) -> Path | None:
    """This session's base temp directory, when a test asked for one (never created here)."""

    factory = getattr(config, "_tmp_path_factory", None)
    base = getattr(factory, "_basetemp", None)
    return Path(base) if base is not None else None


def pytest_sessionfinish(session, exitstatus) -> None:
    """0110-028: no test leaves a Scout server running. A leftover fails the session, and is stopped.

    A leftover is a live process whose command line names the server module
    (``present_api``) and a path under THIS session's pytest temp directory;
    nothing else is looked at. The controller checks for its workers (their
    temp directories are inside its own).
    """

    del exitstatus
    if hasattr(session.config, "workerinput"):
        return
    base = _session_basetemp(session.config)
    if base is None:
        return
    left = scout_test_servers(under=base)
    if not left:
        return
    stop_test_servers(left)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    message = [f"LEAKED SCOUT SERVER: {len(left)} server(s) started by this test session were still running at its end (stopped now):"]
    message.extend(f"  {server.line()}" for server in left)
    message.append("  The directory after pytest-N/ names the test. Stop the server in a finally or a fixture finalizer.")
    if reporter is not None:
        reporter.write_sep("=", "leaked Scout servers", red=True, bold=True)
        for line in message:
            reporter.write_line(line, red=True)
    else:
        print("\n".join(message))
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_collection_modifyitems(config, items) -> None:
    _apply_shard(config, items)
    for item in items:
        function_name = item.name.split("[", 1)[0]
        lane = classify_source(str(item.path), function_name)
        item.add_marker(lane)


__all__ = [
    "classify_source",
    "pytest_collection_modifyitems",
    "pytest_configure",
    "pytest_report_header",
    "pytest_sessionfinish",
]


@pytest.fixture(autouse=True)
def _canned_cli_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI capability probe (codex features list / mcp list / debug models, claude --help) is answered locally: fake CLIs in tests only know the call under test. test_cli_adapter_hardening overrides this fixture to exercise the real probe."""

    from gigai.adapters import cli_probe

    cli_probe.reset_probe_cache()
    monkeypatch.setattr(
        cli_probe,
        "_run_probe",
        lambda argv: (
            "shell_tool stable true\nmemories stable false\n"
            if argv[1:] == ("features", "list")
            else "[]"  # 0110-8-07: `codex mcp list --json`, no MCP server configured
            if argv[1:3] == ("mcp", "list")
            else '{"models": []}'  # 0110-8-07: `codex debug models`
            if argv[1:3] == ("debug", "models")
            else "--setting-sources\n--strict-mcp-config\n--tools\n"
        ),
    )


@pytest.fixture(autouse=True)
def _no_background_model_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test server may spend a real model call on background tagging; tests of the tag queue pass the setting explicitly."""

    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")


@pytest.fixture(autouse=True)
def _no_snapshot_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Update sources tries the shipped snapshot first: no test may reach the real release URL. Snapshot tests pass their own environ/client."""

    monkeypatch.setenv("GIGAI_SCOUT_SNAPSHOT", "0")


@pytest.fixture(autouse=True)
def _no_provider_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.1.11.8: a provider's own floor (Workable: 2 s between requests) is for the real host. No fake board is paced by it; the pacing tests set the switch themselves."""

    monkeypatch.setenv("GIGAI_SCOUT_ATS_PROVIDER_FLOORS", "0")


@pytest.fixture(autouse=True)
def _no_robots_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.1.11.8: the sources update asks each board host for its robots.txt once a day: no fake board models that file, so no test counts its request. The guard's own tests build a guard explicitly."""

    monkeypatch.setenv("GIGAI_SCOUT_ROBOTS", "0")


@pytest.fixture(autouse=True)
def _no_posting_liveness_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.1.11.4 R1: opening or assessing a job asks its board whether the posting is still open: no test may reach a real board. The liveness tests switch it on with their own fake transport."""

    monkeypatch.setenv("GIGAI_SCOUT_POSTING_LIVENESS", "0")
