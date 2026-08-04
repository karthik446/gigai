from __future__ import annotations

import ast
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import provision_workpad


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True,
        text=True,
        check=check,
        shell=False,
    )


def _workpad(tmp_path: Path) -> Path:
    home, root, target = tmp_path / "home", tmp_path / "workpads", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=root, editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
    return provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID).path


def _handoff(index: int) -> str:
    return f"handoff_{index:08x}-1234-4abc-8def-123456789abc"


def _write(workpad: Path, index: int, **kwargs: object):
    from gigai.journal import record_transition

    return record_transition(
        workpad=workpad,
        project_id=PROJECT_ID,
        gig_id=GIG_ID,
        handoff_id=_handoff(index),
        transition="creation_started",
        body=f"Transition {index}",
        **kwargs,
    )


def test_first_transition_commits_g05_infrastructure_and_canonical_handoff(tmp_path: Path) -> None:
    from gigai.workpad import resolve_workpad

    workpad = _workpad(tmp_path)
    result = _write(workpad, 1)
    assert result.sequence == 1
    assert result.path.name == "000000000001-creation-started.txt"
    assert _git(workpad, "rev-parse", "--verify", "HEAD").returncode == 0
    assert f"GigAI-Handoff-Sequence: 000000000001\nGigAI-Handoff: {_handoff(1)}" in _git(workpad, "show", "--format=%B", "--no-patch", "HEAD").stdout
    tracked = _git(workpad, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    assert tracked == [".gitignore", "handoffs/000000000001-creation-started.txt"]
    document = result.path.read_bytes()
    assert document.startswith(b"---gigai-json\n")
    assert b"Transition 1\n" in document
    # G05 resolution remains valid after the journal creates the first commit.
    home = tmp_path / "home"
    target = tmp_path / "target"
    assert resolve_workpad(home_root=home, requested_target=target, gig_id=GIG_ID).path == workpad


def test_normal_sequence_reads_head_and_rejects_an_uncommitted_next_handoff(tmp_path: Path) -> None:
    from gigai.journal import JournalReconciliationRequired, record_transition

    workpad = _workpad(tmp_path)
    _write(workpad, 1)
    _write(workpad, 2)
    assert _git(workpad, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    with pytest.raises(RuntimeError, match="after_replace"):
        _write(workpad, 3, observer=lambda step: (_ for _ in ()).throw(RuntimeError(step)) if step == "after_replace" else None)
    # The injected observer leaves a complete, uncommitted handoff at sequence 3.
    assert (workpad / "handoffs" / "000000000003-creation-started.txt").is_file()
    with pytest.raises(JournalReconciliationRequired, match="uncommitted"):
        record_transition(workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID, handoff_id=_handoff(4), transition="creation_started", body="next")


@pytest.mark.parametrize("failpoint", ("before_replace", "after_replace", "before_commit", "after_commit"))
def test_explicit_recovery_converges_crash_boundaries(tmp_path: Path, failpoint: str) -> None:
    from gigai.journal import JournalReconciliationRequired, reconcile_journal

    workpad = _workpad(tmp_path)
    def crash(step: str) -> None:
        if step == failpoint:
            raise RuntimeError(step)
    with pytest.raises(RuntimeError, match=failpoint):
        _write(workpad, 1, observer=crash)
    if failpoint == "before_replace":
        result = reconcile_journal(workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID)
        assert result.reconciled is False
        assert _git(workpad, "rev-parse", "--verify", "HEAD", check=False).returncode != 0
        _write(workpad, 1)
    else:
        result = reconcile_journal(workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID)
        assert result.reconciled is (failpoint != "after_commit")
        assert _git(workpad, "rev-list", "--count", "HEAD").stdout.strip() == "1"
        _write(workpad, 2)


@pytest.mark.parametrize("failpoint", ("before_replace", "after_replace", "before_commit", "after_commit"))
def test_later_transition_crash_recovery_never_duplicates_sequence(tmp_path: Path, failpoint: str) -> None:
    from gigai.journal import reconcile_journal

    workpad = _workpad(tmp_path)
    _write(workpad, 1)
    def crash(step: str) -> None:
        if step == failpoint:
            raise RuntimeError(step)
    with pytest.raises(RuntimeError, match=failpoint):
        _write(workpad, 2, observer=crash)
    result = reconcile_journal(workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID)
    assert result.reconciled is (failpoint != "before_replace" and failpoint != "after_commit")
    entry = _write(workpad, 2 if failpoint == "before_replace" else 3)
    assert entry.sequence == (2 if failpoint == "before_replace" else 3)


def _worker(workpad: str, index: int) -> None:
    _write(Path(workpad), index)


def test_eight_process_race_allocates_strict_committed_order(tmp_path: Path) -> None:
    workpad = _workpad(tmp_path)
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_worker, args=(os.fspath(workpad), index)) for index in range(1, 9)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    names = sorted(path.name for path in (workpad / "handoffs").iterdir())
    assert [name[:12] for name in names] == [f"{index:012d}" for index in range(1, 9)]
    assert _git(workpad, "rev-list", "--count", "HEAD").stdout.strip() == "8"


def test_timeout_remote_and_identity_ownership_fail_closed(tmp_path: Path) -> None:
    from gigai.journal import InterprocessLockUnavailable, JournalConflictError

    workpad = _workpad(tmp_path)
    lock = workpad / ".git" / "gigai-writer.lock"
    import fcntl
    with lock.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(b"pid=4242\n")
        stream.flush()
        with pytest.raises(InterprocessLockUnavailable, match="timeout owner=pid="):
            _write(workpad, 1, lock_timeout_seconds=0.01)
    _git(workpad, "remote", "add", "forbidden", "https://example.invalid/journal.git")
    with pytest.raises(JournalConflictError, match="remote"):
        _write(workpad, 1)


def test_writer_lock_retries_with_a_nonzero_backoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai import journal
    from gigai.journal import InterprocessLockUnavailable

    workpad = _workpad(tmp_path)
    lock = workpad / ".git" / "gigai-writer.lock"
    import fcntl

    with lock.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        monotonic_values = iter((0.0, 0.0, 0.00099, 0.001))
        sleeps: list[float] = []
        monkeypatch.setattr(journal.time, "monotonic", lambda: next(monotonic_values))
        monkeypatch.setattr(journal.time, "sleep", sleeps.append)
        with pytest.raises(InterprocessLockUnavailable, match="writer lock timeout"):
            with journal._writer_lock(lock, 0.001):
                pass
    assert sleeps == [0.001]


def test_journal_trailers_use_only_the_final_paragraph_and_reject_duplicates() -> None:
    from gigai.journal import JournalReconciliationRequired, _trailers

    message = (
        "journal: normal transition\n\n"
        "A body line that happens to say GigAI-Handoff-Sequence: 999999999999.\n\n"
        f"GigAI-Handoff-Sequence: 000000000001\nGigAI-Handoff: {_handoff(1)}\n"
    )
    assert _trailers(message) == {
        "GigAI-Handoff-Sequence": "000000000001",
        "GigAI-Handoff": _handoff(1),
    }
    duplicate = message + f"GigAI-Handoff: {_handoff(2)}\n"
    with pytest.raises(JournalReconciliationRequired, match="duplicate GigAI-Handoff"):
        _trailers(duplicate)


def test_failed_mount_probe_blocks_before_handoff_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.diagnostics import DiagnosticCheck
    from gigai.journal import InterprocessLockUnavailable

    workpad = _workpad(tmp_path)
    monkeypatch.setattr(
        "gigai.journal.run_mount_probes",
        lambda _root: (DiagnosticCheck("mount.atomic_replace", "mount", "FAIL", "injected", (), None, 0),),
    )
    with pytest.raises(InterprocessLockUnavailable, match="mount.atomic_replace"):
        _write(workpad, 1)
    assert not (workpad / "handoffs").exists()


def test_mount_change_and_divergent_head_require_operator_recovery(tmp_path: Path) -> None:
    from gigai.journal import JournalConflictError, JournalReconciliationRequired

    workpad = _workpad(tmp_path)
    original = tmp_path / "original-workpad"
    replacement = tmp_path / "replacement-workpad"
    def repoint(step: str) -> None:
        if step == "after_replace":
            workpad.rename(original)
            replacement.mkdir()
            workpad.symlink_to(replacement, target_is_directory=True)
    with pytest.raises(JournalConflictError, match="mount changed"):
        _write(workpad, 1, observer=repoint)

    workpad.unlink()
    original.rename(workpad)
    from gigai.journal import reconcile_journal
    assert reconcile_journal(workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID).reconciled is True
    (workpad / "unexpected.txt").write_text("divergence\n")
    _git(workpad, "add", "unexpected.txt")
    _git(workpad, "commit", "--quiet", "-m", "manual divergence")
    with pytest.raises(JournalReconciliationRequired, match="trailers"):
        _write(workpad, 2)


def test_journal_module_never_allocates_project_or_gig_or_provisions_workpad() -> None:
    path = Path(__file__).parents[1] / "src" / "gigai" / "journal.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prohibited = {"uuid4", "generate_entity_id", "provision_workpad", "initialize_target"}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not prohibited & names
    record = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "record_transition")
    methods = {node.attr for node in ast.walk(record) if isinstance(node, ast.Attribute)}
    assert not {"glob", "iterdir", "rglob"} & methods
