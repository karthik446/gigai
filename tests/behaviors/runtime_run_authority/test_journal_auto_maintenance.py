"""Journal commits must not hand the workpad's ``.git`` to git's automatic
maintenance.

``git commit`` forks ``git maintenance run --auto --detach`` unless
``maintenance.auto`` is off.  With git's default strategy that detached
process repacks loose objects (``repack -d`` prunes them and removes the
now-empty ``objects/xx`` fan-out directories) and writes a commit-graph --
all after the commit has returned.  A journal ``git add`` racing that prune
fails with ``unable to create temporary file``, and a workpad being removed
by ``TemporaryDirectory`` races the repack re-creating ``objects/pack``
(``.git`` "Directory not empty").  Both were observed in CI (g16-flake).

The test forces every default auto-maintenance task to fire on each commit
and pins it to the foreground, so a journal that still triggers maintenance
leaves packs and a commit-graph behind deterministically.
"""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import uuid

from gigai.journal import record_transition
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import provision_workpad


PROJECT_ID = "project_12345678-1234-4234-9234-123456789abc"
GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

# Fire git's auto-maintenance tasks unconditionally (a negative ``.auto``
# forces a task) and keep the run in the foreground so its effects are
# visible as soon as ``git commit`` returns.  ``gc.auto=1`` covers the
# pre-geometric ``gc`` strategy of older gits the same way.
_FORCE_AUTO_MAINTENANCE = {
    "maintenance.geometric-repack.auto": "-1",
    "maintenance.commit-graph.auto": "-1",
    "maintenance.loose-objects.auto": "-1",
    "maintenance.incremental-repack.auto": "-1",
    "maintenance.autoDetach": "false",
    "gc.auto": "1",
    "gc.autoDetach": "false",
}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )


def _workpad(tmp_path: Path) -> Path:
    home, root, target = tmp_path / "home", tmp_path / "workpads", tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=root, editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
    return provision_workpad(home_root=home, project_id=PROJECT_ID, gig_id=GIG_ID).path


def _maintenance_artifacts(workpad: Path) -> list[str]:
    objects = workpad / ".git" / "objects"
    found = [os.fspath(path.relative_to(workpad)) for path in (objects / "pack").glob("*.pack")]
    found += [os.fspath(path.relative_to(workpad)) for path in (objects / "info").glob("commit-graph*")]
    return sorted(found)


def test_journal_commits_never_trigger_git_auto_maintenance(tmp_path: Path) -> None:
    workpad = _workpad(tmp_path)
    for key, value in _FORCE_AUTO_MAINTENANCE.items():
        _git(workpad, "config", "--local", key, value)

    for index in range(1, 4):
        record_transition(
            workpad=workpad,
            project_id=PROJECT_ID,
            gig_id=GIG_ID,
            handoff_id=f"handoff_{index:08x}-1234-4abc-8def-123456789abc",
            transition="creation_started",
            body=f"Transition {index}",
        )

    # The symptom: no repack, no prune, no commit-graph -- nothing but the
    # journal itself ever rewrote `.git/objects`, and every object it wrote
    # is still loose.
    assert _maintenance_artifacts(workpad) == []
    counts = dict(line.split(": ", 1) for line in _git(workpad, "count-objects", "-v").stdout.splitlines())
    assert int(counts["count"]) > 0 and counts["in-pack"] == "0" and counts["packs"] == "0"
    assert _git(workpad, "rev-list", "--count", "HEAD").stdout.strip() == "3"
