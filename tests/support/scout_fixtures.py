"""Scout's suite-wide test guards, kept out of the root conftest.

``tests/conftest.py`` loads this module with ``pytest_plugins``, so every
autouse fixture here still applies to every test under ``tests/`` (Scout
code is reached from many directories, through imports and CLI children,
not only from the ``scout_*`` ones), and the session hook still runs once in
the controller. The core CLI probe stub (``_canned_cli_probe``) stays in the
root conftest: it stands in for ``gigai.adapters``, not for Scout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.scout_servers import scout_test_servers, stop_test_servers


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


__all__ = [
    "pytest_sessionfinish",
]


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
