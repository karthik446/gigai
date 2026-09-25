"""test-gap-001's approved new seam: ``run_supervisor.start(allow_test_seams=...)``.

The coordinator approved adding a keyword-only ``allow_test_seams: bool =
False`` parameter to ``run_supervisor.start()`` (``src/gigai/scout/
run_supervisor.py``) so this suite can drive a real ``gigai scout run
--no-browser``-equivalent server with the existing, already-proven-inert
``GIGAI_SCOUT_FIND_JOBS_TEST_HTTP``/``_TEST_MODEL`` env seams active in the
spawned child -- ``present_api.py``'s own ``main()`` otherwise refuses to
start (``SystemExit(2)``) when either is set, unless ``--allow-test-seams``
is also on argv (present_api.py's own safety gate against a real server
accidentally starting with test seams active).

Conditions from the approval: no CLI flag or env var may reach this
parameter (``scout_cli.py``'s ``run`` command never passes it, so `gigai
scout run` can never set it), and a test must assert the default argv omits
the flag while an opted-in call includes it. This file is that test --
inspecting the built ``subprocess.Popen`` argv directly (monkeypatched) so
it never depends on a live child actually starting.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gigai.scout import run_supervisor
from tests.api_e2e.harness import free_port

_REAL_POPEN = subprocess.Popen


class _ArgvCapturingPopen:
    """Delegates to the real ``subprocess.Popen`` for every call except the
    ``present_api`` server launch, which it records instead of starting --
    ``run_supervisor.start()`` is the only caller this test drives, but
    ``gigai init``'s own setup path shells out to ``git`` through the same
    ``subprocess`` module-level import, so a blanket fake would also
    swallow those calls."""

    captured_argv: list[str] | None = None

    def __new__(cls, argv: list[str], **kwargs: object):
        if "gigai.scout.find_jobs.present_api" in argv:
            cls.captured_argv = argv
            return _FakeServerProcess()
        return _REAL_POPEN(argv, **kwargs)  # type: ignore[arg-type]


class _FakeServerProcess:
    """Stands in for the real server child; never actually starts one."""

    def __init__(self) -> None:
        self.pid = 999_999  # never alive; the health-check loop is bypassed below

    def poll(self) -> int | None:
        return None


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    from click.testing import CliRunner

    from gigai.cli import cli

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    runner = CliRunner()
    setup_result = runner.invoke(
        cli,
        [
            "setup", "--non-interactive", "--home", str(home),
            "--workpad-root", str(tmp_path / "workpads"),
            "--editor", "/usr/bin/true",
            "--credential-ref", "provider=environment:GIGAI_PROVIDER_TOKEN",
            "--endpoint", "remote=openai_api:provider:https://api.example.test",
            "--model-target", "remote=remote:smoke-test",
            "--create-model-target", "remote", "--json",
        ],
    )
    assert setup_result.exit_code == 0, setup_result.output
    init_result = runner.invoke(
        cli, ["init", "--home", str(home), "--target", str(target), "--username", "seam-test", "--json"],
    )
    assert init_result.exit_code == 0, init_result.output

    _ArgvCapturingPopen.captured_argv = None
    monkeypatch.setattr(run_supervisor.subprocess, "Popen", _ArgvCapturingPopen)
    # The health check would otherwise block/timeout against a process that
    # never really starts; report unhealthy immediately so start() raises
    # its own ScoutRunError fast instead of waiting out HEALTH_TIMEOUT_SECONDS
    # -- this test only cares about the argv start() built, not a live server.
    monkeypatch.setattr(run_supervisor, "_health_ok", lambda *_a, **_k: False)
    monkeypatch.setattr(run_supervisor, "_process_is_alive", lambda *_a, **_k: False)
    return home, target


def test_default_start_argv_omits_allow_test_seams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _prepare(tmp_path, monkeypatch)

    with pytest.raises(run_supervisor.ScoutRunError):
        run_supervisor.start(
            home_root=home, requested_target=target, port=free_port(), foreground=False, open_browser=False,
        )

    argv = _ArgvCapturingPopen.captured_argv
    assert argv is not None, "start() never reached subprocess.Popen"
    assert "--allow-test-seams" not in argv, argv


def test_opted_in_start_argv_includes_allow_test_seams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = _prepare(tmp_path, monkeypatch)

    with pytest.raises(run_supervisor.ScoutRunError):
        run_supervisor.start(
            home_root=home, requested_target=target, port=free_port(), foreground=False, open_browser=False,
            allow_test_seams=True,
        )

    argv = _ArgvCapturingPopen.captured_argv
    assert argv is not None, "start() never reached subprocess.Popen"
    assert "--allow-test-seams" in argv, argv
