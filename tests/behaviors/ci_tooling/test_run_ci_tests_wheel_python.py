"""Pure unit tests for the wheel-lane venv safety fix in tools/run_ci_tests.py.

These tests never invoke ``uv`` and never create a real venv; they only
exercise the pure path-derivation and guard functions against temp
directories and symlinks, proving the fix for the destructive bug where
``.wheel-venv/bin/python`` (a symlink to a real interpreter) was resolved
through to that interpreter, and the derived "venv" directory was then
handed to ``uv venv --allow-existing``, overwriting the real interpreter's
install tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import run_ci_tests


def test_resolve_wheel_python_does_not_follow_symlink_outside_repo(tmp_path: Path) -> None:
    """A symlinked .wheel-venv/bin/python must derive <repo>/.wheel-venv, never the symlink target."""

    fake_repo = tmp_path / "repo"
    wheel_venv_bin = fake_repo / ".wheel-venv" / "bin"
    wheel_venv_bin.mkdir(parents=True)
    symlink_path = wheel_venv_bin / "python"

    # The symlink target looks exactly like a uv-managed CPython install,
    # entirely outside the repo -- the real shape of the destructive bug.
    managed_interpreter_dir = tmp_path / "managed-uv-pythons" / "cpython-3.11.14-macos-aarch64-none"
    managed_interpreter_bin = managed_interpreter_dir / "bin"
    managed_interpreter_bin.mkdir(parents=True)
    real_interpreter = managed_interpreter_bin / "python3.11"
    real_interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    symlink_path.symlink_to(real_interpreter)

    original_root = run_ci_tests.ROOT
    try:
        run_ci_tests.ROOT = fake_repo
        resolved = run_ci_tests._resolve_wheel_python(symlink_path)
    finally:
        run_ci_tests.ROOT = original_root

    # Unresolved: still the symlink's own path, not the real interpreter.
    assert resolved == symlink_path
    assert resolved != real_interpreter
    assert managed_interpreter_dir not in resolved.parents

    # What _run_wheel derives from this must stay inside the repo.
    derived_virtualenv = resolved.parent.parent
    assert derived_virtualenv == fake_repo / ".wheel-venv"
    assert derived_virtualenv != managed_interpreter_dir


def test_resolve_wheel_python_normalizes_relative_and_dotted_paths(tmp_path: Path) -> None:
    """Relative/`..`-bearing input is normalized lexically, never touching the filesystem."""

    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    original_root = run_ci_tests.ROOT
    try:
        run_ci_tests.ROOT = fake_repo
        resolved = run_ci_tests._resolve_wheel_python(Path(".wheel-venv") / "bin" / "python")
        also_resolved = run_ci_tests._resolve_wheel_python(
            fake_repo / "sub" / ".." / ".wheel-venv" / "bin" / "python"
        )
    finally:
        run_ci_tests.ROOT = original_root

    assert resolved == fake_repo / ".wheel-venv" / "bin" / "python"
    assert also_resolved == fake_repo / ".wheel-venv" / "bin" / "python"


def test_guard_refuses_a_managed_interpreter_looking_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dir shaped like .../uv/python/cpython-x/bin/python3.11's venv, with no pyvenv.cfg, must be refused."""

    fake_home = tmp_path / "home"
    fake_uv_python_dir = fake_home / ".local" / "share" / "uv" / "python" / "cpython-3.11.14-macos-aarch64-none"
    (fake_uv_python_dir / "bin").mkdir(parents=True)
    # No pyvenv.cfg: this is a real interpreter install directory, not a venv.

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    with pytest.raises(SystemExit):
        run_ci_tests._ensure_safe_virtualenv_dir(fake_uv_python_dir)


def test_guard_refuses_a_pyenv_root_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    fake_pyenv_dir = fake_home / ".pyenv" / "versions" / "3.11.14"
    fake_pyenv_dir.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    with pytest.raises(SystemExit):
        run_ci_tests._ensure_safe_virtualenv_dir(fake_pyenv_dir)


def test_guard_refuses_a_directory_outside_the_repo_entirely(tmp_path: Path) -> None:
    outside = tmp_path / "somewhere-else" / ".wheel-venv"
    outside.mkdir(parents=True)

    original_root = run_ci_tests.ROOT
    try:
        run_ci_tests.ROOT = tmp_path / "repo"
        with pytest.raises(SystemExit):
            run_ci_tests._ensure_safe_virtualenv_dir(outside)
    finally:
        run_ci_tests.ROOT = original_root


def test_guard_refuses_an_existing_non_venv_directory_inside_the_repo(tmp_path: Path) -> None:
    """Even inside the repo, an existing dir with no pyvenv.cfg must not be reused as a venv."""

    fake_repo = tmp_path / "repo"
    existing_non_venv = fake_repo / ".wheel-venv"
    existing_non_venv.mkdir(parents=True)
    (existing_non_venv / "some-other-file.txt").write_text("not a venv\n", encoding="utf-8")

    original_root = run_ci_tests.ROOT
    try:
        run_ci_tests.ROOT = fake_repo
        with pytest.raises(SystemExit):
            run_ci_tests._ensure_safe_virtualenv_dir(existing_non_venv)
    finally:
        run_ci_tests.ROOT = original_root


def test_guard_allows_a_normal_repo_local_wheel_venv_that_does_not_exist_yet(tmp_path: Path) -> None:
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    not_yet_created = fake_repo / ".wheel-venv"

    original_root = run_ci_tests.ROOT
    try:
        run_ci_tests.ROOT = fake_repo
        run_ci_tests._ensure_safe_virtualenv_dir(not_yet_created)
    finally:
        run_ci_tests.ROOT = original_root
    # No exception: a not-yet-created repo-local venv path is safe to hand to `uv venv`.


def test_guard_allows_a_normal_repo_local_wheel_venv_that_already_looks_like_a_venv(tmp_path: Path) -> None:
    fake_repo = tmp_path / "repo"
    existing_venv = fake_repo / ".wheel-venv"
    (existing_venv / "bin").mkdir(parents=True)
    (existing_venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")

    original_root = run_ci_tests.ROOT
    try:
        run_ci_tests.ROOT = fake_repo
        run_ci_tests._ensure_safe_virtualenv_dir(existing_venv)
    finally:
        run_ci_tests.ROOT = original_root
    # No exception: an existing repo-local dir with pyvenv.cfg is a real venv.


def test_run_wheel_invokes_the_guard_before_any_uv_venv_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_wheel must call the guard, and must not shell out to uv at all when it fails."""

    fake_repo = tmp_path / "repo"
    fake_home = tmp_path / "home"
    fake_uv_python_dir = fake_home / ".local" / "share" / "uv" / "python" / "cpython-3.11.14-macos-aarch64-none"
    fake_uv_python_bin = fake_uv_python_dir / "bin"
    fake_uv_python_bin.mkdir(parents=True)
    dangerous_symlink_target = fake_uv_python_bin / "python3.11"
    dangerous_symlink_target.write_text("#!/bin/sh\n", encoding="utf-8")

    wheel_venv_bin = fake_repo / ".wheel-venv" / "bin"
    wheel_venv_bin.mkdir(parents=True)
    symlinked_wheel_python = wheel_venv_bin / "python"
    symlinked_wheel_python.symlink_to(dangerous_symlink_target)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    calls: list[list[str]] = []

    def _fail_if_uv_runs(command, **kwargs):  # noqa: ANN001, ANN003 -- test double
        calls.append([str(item) for item in command])
        raise AssertionError("uv must never be invoked once the safety guard refuses the target")

    monkeypatch.setattr(run_ci_tests, "_run", _fail_if_uv_runs)

    original_root = run_ci_tests.ROOT
    try:
        run_ci_tests.ROOT = fake_repo
        with pytest.raises(SystemExit):
            run_ci_tests._run_wheel(symlinked_wheel_python)
    finally:
        run_ci_tests.ROOT = original_root

    assert calls == []


def test_run_wheel_proceeds_past_the_guard_for_a_safe_repo_local_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal repo-local .wheel-venv must pass the guard and reach the first real _run call."""

    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    wheel_python = fake_repo / ".wheel-venv" / "bin" / "python"

    calls: list[list[str]] = []

    def _record_and_stop(command, **kwargs):  # noqa: ANN001, ANN003 -- test double
        calls.append([str(item) for item in command])
        # Stop the real _run_wheel body here (before it shells out to `uv
        # build`) by returning a non-zero code, which _run_wheel treats as an
        # early return; this test only proves the guard did not raise.
        return 1

    monkeypatch.setattr(run_ci_tests, "_run", _record_and_stop)

    original_root = run_ci_tests.ROOT
    try:
        run_ci_tests.ROOT = fake_repo
        returncode = run_ci_tests._run_wheel(wheel_python)
    finally:
        run_ci_tests.ROOT = original_root

    assert returncode == 1
    assert calls, "expected _run to be reached at least once (uv build)"
    assert calls[0][:2] == ["uv", "build"]


def test_unresolved_absolute_never_touches_the_filesystem(tmp_path: Path) -> None:
    """A path that does not exist at all must still resolve lexically without error."""

    fake_repo = tmp_path / "repo"
    original_root = run_ci_tests.ROOT
    try:
        run_ci_tests.ROOT = fake_repo
        resolved = run_ci_tests._unresolved_absolute("nonexistent/nested/python")
    finally:
        run_ci_tests.ROOT = original_root

    assert resolved == fake_repo / "nonexistent" / "nested" / "python"
    assert not resolved.exists()
