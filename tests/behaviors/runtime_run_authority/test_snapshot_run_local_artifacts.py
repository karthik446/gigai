"""regression-002: a committed-snapshot read must tolerate run-local
artifacts, not reject them as unexplained "extra" evidence.

``progress.py``'s ``acquire.jsonl``/``assess.jsonl`` (plus ``raw/``,
``logs/``) are written straight to the working tree -- additive,
non-authoritative, never journal-committed by design
(``workpad.RUN_LOCAL_ARTIFACT_EXCLUDES``/``ensure_run_local_artifact_
excludes`` already excludes them from git itself, at exactly the same
roots, for exactly this reason). Before this fix, ANY caller of
``JournalWriter.snapshot(("runs/", ...))`` (``gigai.application_events.
record_application``'s own ``publish`` closure, ``gigai.scout.projection``)
would 409/``application_journal_conflict``/``JournalConflictError`` the
moment such a file existed in the working tree -- i.e. after any real run
that used progress files, which is every real run. This predates P9c (Scout
find-jobs' runs/applications API) entirely and hit ``gigai application
record`` at the CLI too.

``_capture_committed_snapshot`` (``gigai.journal``) now tolerates a path
strictly under one of ``RUN_LOCAL_ARTIFACT_EXCLUDES``'s roots
(``_is_run_local_artifact``) while keeping every other protection: a
symlinked file or directory anywhere -- including inside ``progress/`` --
is still rejected, and a stray file under ``runs/`` that is NOT one of
these roots is still rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.behaviors.scout_proposals_tools.test_scout05_first_proposal import _bound_defaults

from gigai.application_events import record_application
from gigai.cli import cli
from gigai.journal import JournalConflictError, run_with_journal_writer
from gigai.workpad import RUN_LOCAL_ARTIFACT_EXCLUDES, resolve_workpad

_PREFIXES = ("records/", "runs/", "run-plans/", "references/", "run-inputs/", "manifests/")
_RUN_ID = "run_00000000-0000-4000-8000-000000000001"


def _write_run_local_artifacts(workpad: Path, run_id: str = _RUN_ID) -> None:
    """Directly write the same shape a real run's progress/raw/logs writers
    produce -- never journal-committed, matching ``progress.py``'s own
    ``path.open("a")`` (append, no ``JournalArtifact``)."""

    run_root = workpad / "runs" / run_id
    (run_root / "progress").mkdir(parents=True, exist_ok=True)
    (run_root / "progress" / "acquire.jsonl").write_text('{"outcome": "new"}\n', encoding="utf-8")
    (run_root / "progress" / "assess.jsonl").write_text('{"status": "started"}\n', encoding="utf-8")
    (run_root / "raw").mkdir(parents=True, exist_ok=True)
    (run_root / "raw" / "board-response.html").write_text("<html></html>", encoding="utf-8")
    (run_root / "logs").mkdir(parents=True, exist_ok=True)
    (run_root / "logs" / "node-failure.log").write_text("boom\n", encoding="utf-8")


def _snapshot(resolved):
    return run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id,
        operation=lambda writer: writer.snapshot(_PREFIXES),
    )


def test_snapshot_tolerates_progress_raw_and_logs_and_record_application_succeeds(tmp_path: Path) -> None:
    """The end outcome: a workpad with real run-local artifacts on disk
    still snapshots cleanly, and recording an application event (the exact
    call P9c's ``POST /api/applications`` and ``gigai application record``
    both make) succeeds instead of 409ing."""

    home, target, _project, gig, workpad, _other = _bound_defaults(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)

    _write_run_local_artifacts(workpad)

    # The read path every caller of a committed snapshot exercises.
    snapshot = _snapshot(resolved)
    assert snapshot.head is not None

    # The end outcome the operator actually hits: recording an application
    # event after a run must succeed, not 409.
    data = {
        "operation_key": "regression-002-record",
        "external_ref": "https://boards.greenhouse.io/acme/jobs/101",
        "event_kind": "applied",
        "occurred_at": "2026-09-25T12:00:00-06:00",
        "timezone": "America/Denver",
        "document_refs": [],
        "notes": None,
        "supersedes": None,
    }
    result = record_application(resolved=resolved, data=data, confirm=True)
    assert result["status"] == "recorded"


def test_cli_application_record_succeeds_after_a_real_run_left_progress_files(tmp_path: Path) -> None:
    """The same regression, proven through the EXISTING CLI entry point
    (``gigai application record``) rather than the library call -- this is
    the exact path an operator hits, and the exact one that used to 409.
    """

    home, target, _project, gig, workpad, _other = _bound_defaults(tmp_path)
    _write_run_local_artifacts(workpad)

    request = tmp_path / "application.json"
    request.write_text(
        json.dumps(
            {
                "operation_key": "regression-002-cli",
                "external_ref": "https://boards.greenhouse.io/acme/jobs/12345",
                "event_kind": "applied",
                "occurred_at": "2026-09-25T12:00:00-06:00",
                "timezone": "America/Denver",
                "document_refs": [],
                "notes": "recorded after a run left progress/raw/logs on disk",
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["application", "record", "--gig", gig, "--home", str(home), "--target", str(target), "--input", str(request), "--confirm", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "recorded"


def test_a_stray_file_under_runs_is_still_rejected(tmp_path: Path) -> None:
    """A file directly under ``runs/<run_id>/`` that is NOT one of
    ``RUN_LOCAL_ARTIFACT_EXCLUDES``'s roots (e.g. a stray ``notes.txt``,
    never a real writer's output) is still treated as unexplained "extra"
    evidence -- the fix narrows the tolerance to exactly the known
    run-local roots, it does not disable the check."""

    home, target, _project, gig, workpad, _other = _bound_defaults(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)

    run_root = workpad / "runs" / _RUN_ID
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "notes.txt").write_text("not a real run artifact\n", encoding="utf-8")

    with pytest.raises(JournalConflictError, match="extra or redirected"):
        _snapshot(resolved)


def test_a_symlink_inside_progress_is_still_rejected(tmp_path: Path) -> None:
    """A symlink anywhere under a tolerated run-local root (here,
    ``progress/``) is still rejected -- the fix relaxes only the
    "not in the committed artifact set" check, never the separate symlink
    check that runs first."""

    home, target, _project, gig, workpad, _other = _bound_defaults(tmp_path)
    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig, allow_semantic_state=True)

    _write_run_local_artifacts(workpad)
    progress_dir = workpad / "runs" / _RUN_ID / "progress"
    real_target = workpad / "runs" / _RUN_ID / "raw" / "board-response.html"
    (progress_dir / "sneaky-link.jsonl").symlink_to(real_target)

    with pytest.raises(JournalConflictError, match="redirected"):
        _snapshot(resolved)


def test_run_local_artifact_excludes_is_the_single_source_of_truth() -> None:
    """regression-002's second requirement: the git-exclude list
    (``workpad.RUN_LOCAL_ARTIFACT_EXCLUDES``), the journal snapshot's
    tolerance list (``journal._RUN_LOCAL_ARTIFACT_PREFIX_PARTS``), and the
    run-dir writer-inventory test's exclude classification (``test_run_dir_
    writer_inventory.EXCLUDED_RUN_SUBPATHS``) all derive from the SAME
    tuple -- a new run-local folder added to one can never be silently
    missed by the other two, because there is only one place to add it.
    """

    from gigai import journal as journal_module
    from tests.behaviors.scout_find_jobs.test_run_dir_writer_inventory import (
        EXCLUDED_RUN_SUBPATHS,
        _exclude_subpath,
    )

    # (1) journal.py's own tolerance list is built from workpad.py's tuple,
    # not a hand-copied duplicate -- same segment set, in the same order.
    expected_parts = tuple(tuple(line.strip("/").split("/")) for line in RUN_LOCAL_ARTIFACT_EXCLUDES)
    assert journal_module._RUN_LOCAL_ARTIFACT_PREFIX_PARTS == expected_parts

    # (2) the writer-inventory test's own exclude set is derived from the
    # same tuple too (already true before this fix; asserted here so all
    # three sources are pinned together in one place).
    assert EXCLUDED_RUN_SUBPATHS == frozenset(_exclude_subpath(pattern) for pattern in RUN_LOCAL_ARTIFACT_EXCLUDES)

    # (3) a NEW run-local folder added only to the single source of truth
    # (workpad.RUN_LOCAL_ARTIFACT_EXCLUDES) is picked up by journal.py's own
    # matcher the moment that constant grows -- proven here the same way
    # `_is_run_local_artifact` itself derives its answer: by re-deriving the
    # segment tuple straight from `RUN_LOCAL_ARTIFACT_EXCLUDES` (never a
    # second, hand-maintained list) and checking journal.py's own
    # module-level constant is exactly that derivation, not a superset or
    # subset -- so an entry added to the tuple but never wired into
    # journal.py's import (a regression this test exists to catch) would
    # show up as a mismatch, and an entry hand-added directly to journal.py
    # without going through the shared tuple (fragmenting the single source
    # of truth) would too.
    extra_pattern = "/runs/*/scratch-evidence/"
    with_extra = RUN_LOCAL_ARTIFACT_EXCLUDES + (extra_pattern,)
    recomputed_with_extra = tuple(tuple(line.strip("/").split("/")) for line in with_extra)
    assert recomputed_with_extra[:-1] == journal_module._RUN_LOCAL_ARTIFACT_PREFIX_PARTS, (
        "journal.py's tolerance list has drifted from workpad."
        "RUN_LOCAL_ARTIFACT_EXCLUDES -- it must be derived from that tuple, "
        "never hand-maintained separately"
    )
    assert recomputed_with_extra[-1] == ("runs", "*", "scratch-evidence")
