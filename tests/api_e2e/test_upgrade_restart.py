"""test-gap-001: an upgrade restart (a state file with an old version).

uat-bug-006: a live server that is genuinely ours but whose state file
records an older (or missing) ``gigai_version``/``package_path`` than what's
installed now must be stopped and replaced, never silently reused -- so an
upgrade actually takes effect. ``test_scout_run_supervisor.py``'s own
``test_run_restarts_a_live_server_recorded_with_an_older_gigai_version``
already proves the supervisor-level contract (``reused`` is ``False``, a new
pid). This file adds the HTTP-level view test-gap-001 asks for: after the
restart, the NEW server actually answers requests -- ``/api/health`` and
``/api/config`` succeed against the replacement process, not just "some pid
exists".
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from gigai.scout import run_supervisor
from gigai.workpad import resolve_bound_project

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    free_port,
    resolve_workpad_path,
    setup_and_init,
    start_server,
)


def _process_is_alive(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until_gone(pid: int, *, timeout: float = 5.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_is_alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_a_state_file_with_an_old_version_is_stopped_and_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)

    first_server = start_server(home, target, monkeypatch=monkeypatch)
    old_pid = first_server.pid
    try:
        # Sanity: the first server actually answers before we simulate an
        # upgrade -- otherwise a later "the new one answers" pass would be
        # vacuous (nothing to contrast against).
        health = first_server.client.get("/api/health")
        assert health.status_code == 200, health.text
        first_server.client.close()

        # Rewrite the state file as if it were written by an older build:
        # no version/package_path fields at all (older builds never wrote
        # them) -- mirrors test_scout_run_supervisor.py's own
        # upgrade-restart test.
        bound = resolve_bound_project(home_root=home, requested_target=target)
        state_path = run_supervisor._state_path(home, bound.project_id)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        raw.pop("gigai_version", None)
        raw.pop("package_path", None)
        state_path.write_text(json.dumps(raw), encoding="utf-8")

        # A second `gigai scout run --no-browser`-equivalent call must
        # detect the version mismatch, stop the old (genuinely-ours,
        # identity-checked) process, and start a fresh one -- on a
        # different port, since the OS can hold the old port in TIME_WAIT
        # right after SIGTERM.
        second_port = free_port()
        result = run_supervisor.start(
            home_root=home,
            requested_target=target,
            port=second_port,
            foreground=False,
            open_browser=False,
            allow_test_seams=True,
        )
        assert result.reused is False, (
            "a server recorded with no/older gigai version must be restarted, not reused"
        )
        assert result.restarted_from_version is not None
        new_pid = result.state.pid
        assert new_pid != old_pid
        assert _wait_until_gone(old_pid), "the old-version server must have been stopped"
        assert _process_is_alive(new_pid)

        # The HTTP-level proof test-gap-001 actually asks for: the NEW
        # server answers real requests, not just "a pid exists".
        new_client = httpx.Client(base_url=result.state.url, timeout=20.0)
        try:
            health_response = new_client.get("/api/health")
            assert health_response.status_code == 200, health_response.text

            config_response = new_client.get("/api/config")
            assert config_response.status_code == 200, config_response.text
            assert config_response.json()["resume_preview"] is not None

            workpad = resolve_workpad_path(home, target)
        finally:
            new_client.close()
    finally:
        run_supervisor.stop(home_root=home, requested_target=target)

    assert not _process_is_alive(old_pid)
    assert_clean_and_healthy(workpad, home)


def test_a_state_file_with_the_same_version_but_a_different_build_is_stopped_and_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uat-bug-006-r2: same ``gigai_version``/``package_path`` (a same-version
    reinstall -- every dev branch build lands on ``0.1.9.dev0`` at the same uv
    tool path) but a different recorded ``build_id`` must be stopped and
    replaced too, at the HTTP level, not just the supervisor-level contract
    ``test_scout_run_supervisor.py`` already proves. This is the operator's
    actual r2 symptom: a reinstall from the same branch left the old server
    answering, so the new UI talked to old routes.
    """

    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)

    first_server = start_server(home, target, monkeypatch=monkeypatch)
    old_pid = first_server.pid
    try:
        health = first_server.client.get("/api/health")
        assert health.status_code == 200, health.text
        first_server.client.close()

        # Rewrite the state file as if a rebuild had happened in place: same
        # version, same package path, but a different build identity (a
        # fresh `uv tool install` from a new commit on the same branch).
        bound = resolve_bound_project(home_root=home, requested_target=target)
        state_path = run_supervisor._state_path(home, bound.project_id)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        assert raw["gigai_version"] == run_supervisor._installed_gigai_version()
        assert raw["package_path"] == run_supervisor._installed_package_path()
        raw["build_id"] = "a-different-build-than-what-is-installed-now"
        state_path.write_text(json.dumps(raw), encoding="utf-8")

        second_port = free_port()
        result = run_supervisor.start(
            home_root=home,
            requested_target=target,
            port=second_port,
            foreground=False,
            open_browser=False,
            allow_test_seams=True,
        )
        assert result.reused is False, (
            "same version + same package path but a different build identity "
            "must be restarted, not reused -- uat-bug-006-r2's exact repro"
        )
        assert result.restarted_from_version is not None
        new_pid = result.state.pid
        assert new_pid != old_pid
        assert _wait_until_gone(old_pid), "the stale-build server must have been stopped"
        assert _process_is_alive(new_pid)

        new_client = httpx.Client(base_url=result.state.url, timeout=20.0)
        try:
            health_response = new_client.get("/api/health")
            assert health_response.status_code == 200, health_response.text

            config_response = new_client.get("/api/config")
            assert config_response.status_code == 200, config_response.text
            assert config_response.json()["resume_preview"] is not None

            workpad = resolve_workpad_path(home, target)
        finally:
            new_client.close()
    finally:
        run_supervisor.stop(home_root=home, requested_target=target)

    assert not _process_is_alive(old_pid)
    assert_clean_and_healthy(workpad, home)
