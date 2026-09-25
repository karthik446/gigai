"""probe-cache: journal writer acquisition must not re-spawn the mount probe.

Every ``run_with_journal_writer``/``record_transition``/``reconcile_journal``
acquisition calls ``journal._require_mount_probes``, which used to call
``diagnostics.run_mount_probes`` -> ``_interprocess_lock_check`` on every
single call -- spawning ``python -m gigai.diagnostics --contend-lock`` (a
whole subprocess, ~0.35s) to prove the workpad's interprocess advisory lock
excludes, even though that fact does not change while the mount stays the
same. This caches only a PASS, per process, keyed by the resolved workpad
root plus its ``(st_dev, st_ino)`` -- a remount or a replaced directory
changes one of those and re-probes. A FAIL is never cached: a failing probe
must keep raising on every acquisition. ``gigai doctor`` always bypasses the
cache so an operator's diagnostic reflects the mount right now.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
import uuid

import pytest

from gigai.diagnostics import DiagnosticCheck
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import provision_workpad


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

_PASS = (
    DiagnosticCheck(
        "mount.atomic_replace", "mount", "PASS", "ok", (), None, 0
    ),
    DiagnosticCheck(
        "mount.interprocess_lock", "mount", "PASS", "ok", (), None, 0
    ),
)
_FAIL = (
    DiagnosticCheck(
        "mount.atomic_replace", "mount", "PASS", "ok", (), None, 0
    ),
    DiagnosticCheck(
        "mount.interprocess_lock",
        "mount",
        "FAIL",
        "injected failure",
        (),
        None,
        0,
    ),
)


@pytest.fixture(autouse=True)
def _clear_mount_probe_cache():
    """Each test starts with a cold process-level cache."""

    import gigai.journal as journal_module

    journal_module._mount_probe_cache.clear()
    yield
    journal_module._mount_probe_cache.clear()


def _workpad(tmp_path: Path) -> Path:
    home, root, target = tmp_path / "home", tmp_path / "workpads", tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=root,
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    return provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID).path


def _handoff(index: int) -> str:
    return f"handoff_{index:08x}-1234-4abc-8def-123456789abc"


def _write(workpad: Path, index: int):
    from gigai.journal import record_transition

    return record_transition(
        workpad=workpad,
        project_id=PROJECT_ID,
        gig_id=GIG_ID,
        handoff_id=_handoff(index),
        transition="creation_started",
        body=f"Transition {index}",
    )


def test_second_acquisition_in_one_process_spawns_no_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workpad = _workpad(tmp_path)
    calls = 0

    def _fake_probes(_root: Path):
        nonlocal calls
        calls += 1
        return _PASS

    monkeypatch.setattr("gigai.journal.run_mount_probes", _fake_probes)

    _write(workpad, 1)
    assert calls == 1

    _write(workpad, 2)
    assert calls == 1, "second acquisition on the same mount must hit the cache"


def test_changed_st_dev_reprobes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gigai.journal as journal_module

    workpad = _workpad(tmp_path)
    calls = 0

    def _fake_probes(_root: Path):
        nonlocal calls
        calls += 1
        return _PASS

    monkeypatch.setattr("gigai.journal.run_mount_probes", _fake_probes)

    _write(workpad, 1)
    assert calls == 1

    real_stat = Path.stat

    def _fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == workpad.resolve():
            return os.stat_result(
                (
                    result.st_mode,
                    result.st_ino,
                    result.st_dev + 1,  # simulate a remount: different device
                    result.st_nlink,
                    result.st_uid,
                    result.st_gid,
                    result.st_size,
                    result.st_atime_ns,
                    result.st_mtime_ns,
                    result.st_ctime_ns,
                )
            )
        return result

    monkeypatch.setattr(journal_module.Path, "stat", _fake_stat)
    _write(workpad, 2)
    assert calls == 2, "a changed st_dev must re-probe"


def test_changed_st_ino_reprobes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gigai.journal as journal_module

    workpad = _workpad(tmp_path)
    calls = 0

    def _fake_probes(_root: Path):
        nonlocal calls
        calls += 1
        return _PASS

    monkeypatch.setattr("gigai.journal.run_mount_probes", _fake_probes)

    _write(workpad, 1)
    assert calls == 1

    real_stat = Path.stat

    def _fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == workpad.resolve():
            return os.stat_result(
                (
                    result.st_mode,
                    result.st_ino + 1,  # simulate a replaced directory
                    result.st_dev,
                    result.st_nlink,
                    result.st_uid,
                    result.st_gid,
                    result.st_size,
                    result.st_atime_ns,
                    result.st_mtime_ns,
                    result.st_ctime_ns,
                )
            )
        return result

    monkeypatch.setattr(journal_module.Path, "stat", _fake_stat)
    _write(workpad, 2)
    assert calls == 2, "a changed st_ino must re-probe"


def test_failing_probe_raises_on_every_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gigai.journal import InterprocessLockUnavailable

    workpad = _workpad(tmp_path)
    calls = 0

    def _fake_probes(_root: Path):
        nonlocal calls
        calls += 1
        return _FAIL

    monkeypatch.setattr("gigai.journal.run_mount_probes", _fake_probes)

    with pytest.raises(InterprocessLockUnavailable, match="mount.interprocess_lock"):
        _write(workpad, 1)
    assert calls == 1

    with pytest.raises(InterprocessLockUnavailable, match="mount.interprocess_lock"):
        _write(workpad, 2)
    assert calls == 2, "a FAIL must never be cached; every acquisition re-probes"


def test_doctor_always_probes_bypassing_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gigai doctor`'s mount.interprocess_lock check always runs the real

    probe, even when the journal cache already holds a warm PASS for this
    exact mount.
    """

    from gigai.diagnostics import run_doctor
    from gigai.workpad import resolve_workpad

    workpad = _workpad(tmp_path)
    calls = 0

    def _fake_probes(_root: Path):
        nonlocal calls
        calls += 1
        return _PASS

    monkeypatch.setattr("gigai.journal.run_mount_probes", _fake_probes)
    monkeypatch.setattr("gigai.diagnostics.run_mount_probes", _fake_probes)

    _write(workpad, 1)  # warms the journal's cache
    assert calls == 1

    home = tmp_path / "home"
    resolved = resolve_workpad(
        home_root=home, requested_target=tmp_path / "target", gig_id=GIG_ID
    )
    assert resolved.path == workpad

    run_doctor(home)
    assert calls == 2, "doctor must bypass the journal's mount-probe cache"


def test_concurrent_acquisitions_probe_at_most_once_for_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Several threads race to acquire the writer lock on the same, still-cold

    mount at once. The cache lock must serialize the probe itself (rather
    than let two threads both observe a cache miss and both probe), so at
    most one call to ``run_mount_probes`` happens for the eventual PASS --
    proven as a call-count assertion; the other threads simply wait for the
    lock and then hit the now-warm cache.
    """

    workpad = _workpad(tmp_path)
    calls = 0
    calls_lock = threading.Lock()
    ready = threading.Barrier(8, timeout=5)

    def _fake_probes(_root: Path):
        nonlocal calls
        with calls_lock:
            calls += 1
        return _PASS

    monkeypatch.setattr("gigai.journal.run_mount_probes", _fake_probes)

    errors: list[BaseException] = []

    def _acquire(index: int) -> None:
        try:
            ready.wait()  # start all threads at once, racing on the cold cache
            _write(workpad, index)
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=_acquire, args=(i,)) for i in range(1, 9)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"concurrent acquisitions raised: {errors}"
    assert calls == 1, "concurrent threads must probe at most once for a PASS"
