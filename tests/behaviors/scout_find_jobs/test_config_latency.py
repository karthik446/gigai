"""uat-bug-008: GET /api/config must be fast on an operator-shaped workpad.

Root cause (see the worker report for full profiling evidence): resolving
the newest resume (``run.resolve_newest_resume_details``) replayed the
*entire* committed journal on every call -- ``journal._capture_committed_snapshot``
spawned several ``git`` subprocesses per committed artifact under
``records/``/``references/``/``run-inputs/`` -- and ``/api/config`` called
that resolver twice per request (once via ``resume_preview()``, once via
``resume_metadata()``). On a real fixture with dozens of journal commits
this took low double-digit seconds *per call*, and the UI's two back-to-back
loads of ``/api/config`` doubled it.

The fix has two parts:
  1. ``run.resolve_newest_resume_details`` now caches its result per
     workpad, keyed by the workpad's exact git HEAD -- a new commit (a
     resume add, a run, anything) always misses the cache, so it can never
     serve a resume that predates the newest commit.
  2. ``journal._capture_committed_snapshot`` batches its git plumbing
     (one ``git log`` walk + one ``git show --name-only`` + one
     ``git cat-file --batch`` instead of ~4 subprocess spawns per artifact)
     so even a cold cache resolves in well under a second on a realistic
     fixture.

This file builds a real managed workpad in a temp home through the real
CLI/API paths (no network, no mocked git) shaped like the operator's:
several resume imports plus a few dozen other journal commits.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from gigai.canonical import canonical_json_bytes
from gigai.config import Endpoint, ModelTarget as ConfigModelTarget, Profile
from gigai.default_init import initialize_defaults
from gigai.lifecycle import approve_offline
from gigai.private_records import create_record, import_reference, import_run_input
from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend
from gigai.scout.template import scout_candidate_inventory
from gigai.setup import build_config, run_setup
from gigai.workpad import resolve_workpad, select_active_workpad

# Generous bound (ticket's own suggestion): real, non-instrumented timings on
# this exact fixture land around 0.1-1.0s cold and ~0.08s warm; 1s leaves
# ample headroom against CI/parallel-test contention while still catching
# a regression back to the old per-artifact-subprocess behavior (which took
# low double-digit seconds on a fixture this size).
_COLD_TIMING_BOUND_SECONDS = 1.0

_ENDPOINTS = (Endpoint("local-test", "ollama_local", base_url="http://127.0.0.1:11434"),)
_MODEL_TARGETS = (
    ConfigModelTarget(
        name="ollama_local",
        endpoint="local-test",
        model="test-model",
        capabilities=("text",),
        max_output_tokens=512,
        reasoning_effort=None,
        model_digest="sha256:" + "0" * 64,
        context_tokens=2048,
        max_response_bytes=65536,
    ),
)
_PROFILES = (Profile("default", "ollama_local", "ollama_local", "ollama_local"),)


def _operator_shaped_workpad(
    tmp_path: Path, *, resume_imports: int = 2, extra_journal_commits: int = 25
) -> tuple[Path, Path, str]:
    """A real managed workpad shaped like the operator's: several resume

    imports (newest wins) plus a few dozen other journal commits (run-input
    imports -- cheap, one journal commit each, same records/references/
    run-inputs prefixes ``resolve_newest_resume_details`` walks). Built
    through the real CLI/API paths (``import_reference``, ``create_record``,
    ``initialize_defaults``, ``approve_offline``), no network.

    Returns ``(home, target, gig_id)``.
    """

    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "Latency Test"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "config", "user.email", "latency-test@gigai.invalid"], check=True
    )
    (target / "README.md").write_text("uat-bug-008 latency fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "--quiet", "-m", "init"], check=True)

    config = build_config(
        home_root=home,
        workpad_root=tmp_path / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=_ENDPOINTS,
        model_targets=_MODEL_TARGETS,
        profiles=_PROFILES,
    )
    run_setup(config)
    initialized = initialize_defaults(
        home_root=home,
        requested_target=target,
        username="owner",
        inventory=scout_candidate_inventory(),
    )
    instance = initialized.instances[0]
    assert instance.proposal_id is not None
    approve_offline(
        home_root=home,
        requested_target=target,
        gig_id=instance.gig_id,
        proposal_id=str(instance.proposal_id),
    )
    select_active_workpad(
        home_root=home, requested_target=target, gig_id=instance.gig_id, allow_semantic_state=True
    )

    for i in range(resume_imports):
        resume_source = tmp_path / f"resume_{i}.md"
        resume_source.write_text(f"Resume version {i}: Python engineer.\n", encoding="utf-8")
        imported = import_reference(
            home_root=home,
            requested_target=target,
            gig_id=instance.gig_id,
            kind="resume",
            source=resume_source,
            operation_key=f"resume-import-{i}",
        )
        create_record(
            home_root=home,
            requested_target=target,
            gig_id=instance.gig_id,
            kind="imported_reference",
            content_family="g45_reference",
            content_id=imported.item_id,
            actor={"kind": "operator", "id": "local-user"},
            origin="imported",
            operation_key=f"resume-record-{i}",
        )

    for i in range(extra_journal_commits):
        import_run_input(
            home_root=home,
            requested_target=target,
            gig_id=instance.gig_id,
            data=f"pasted job description body {i}".encode(),
            label=f"posting-{i}",
            operation_key=f"run-input-{i}",
        )

    find_jobs_config = FindJobsConfig(
        roles=("software engineer",),
        merged_queries=("software engineer",),
        location="Denver, CO",
        remote=True,
        published_after="2026-09-15T00:00:00Z",
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
    )
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(find_jobs_config.to_json()))
    return home, target, instance.gig_id


@pytest.fixture
def operator_shaped_workpad(tmp_path: Path) -> tuple[Path, Path, str]:
    return _operator_shaped_workpad(tmp_path)


def test_get_config_resolves_the_resume_in_well_under_a_second(
    operator_shaped_workpad: tuple[Path, Path, str],
) -> None:
    """The exact calls ``_handle_get_config`` makes must be fast even cold.

    Fails on the pre-fix code (``git show HEAD:src/gigai/run.py`` /
    ``git show HEAD:src/gigai/journal.py`` swapped in): that version takes
    low double-digit seconds on this fixture (see the worker report's
    baseline profile), far past this bound.
    """

    home, target, _gig_id = operator_shaped_workpad
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    started = time.monotonic()
    config, _config_bytes = backend.read_config()
    resume_result = backend.resume_details()
    elapsed = time.monotonic() - started

    assert elapsed < _COLD_TIMING_BOUND_SECONDS, (
        f"GET /api/config's resume resolution took {elapsed:.3f}s, "
        f"expected < {_COLD_TIMING_BOUND_SECONDS}s"
    )
    assert config is not None
    assert resume_result is not None
    assert resume_result.pinned is not None


def test_second_config_call_on_the_same_head_does_not_replay_the_journal(
    operator_shaped_workpad: tuple[Path, Path, str],
) -> None:
    """uat-bug-008's other half: the UI calls ``/api/config`` twice on load

    (and ``_handle_get_config`` itself used to resolve the resume twice per
    request, via ``resume_preview()`` + ``resume_metadata()``). A repeat
    call against an unchanged workpad must hit ``run.py``'s per-journal-head
    cache, not repeat the expensive resolution -- proven here as a
    call-count-shaped assertion (a tight timing bound on the *second* call)
    rather than only the generous end-to-end bound above.
    """

    home, target, _gig_id = operator_shaped_workpad
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    backend.read_config()
    backend.resume_details()  # warms the per-journal-head cache

    started = time.monotonic()
    backend.read_config()
    backend.resume_details()
    elapsed = time.monotonic() - started

    # A cache hit is two orders of magnitude faster than a cold resolution
    # on this fixture (~0.08s vs ~0.9s measured without instrumentation);
    # 0.3s leaves headroom for CI contention while still catching a
    # regression back to "resolves on every call."
    assert elapsed < 0.3, (
        f"a repeat /api/config call took {elapsed:.3f}s; expected a cache "
        "hit (the journal did not change) well under the cold-call bound"
    )


def test_resume_add_invalidates_the_cache_never_serves_a_stale_resume(
    operator_shaped_workpad: tuple[Path, Path, str],
) -> None:
    """No cache that can serve a stale resume after ``gigai scout resume add``.

    Warms the per-journal-head cache with the fixture's existing resume,
    then commits a new resume the same way ``gigai scout resume add``
    would (``import_reference`` + ``create_record``, a real journal commit)
    and asserts the very next ``/api/config``-equivalent call sees the new
    resume, not the cached one.
    """

    home, target, gig_id = operator_shaped_workpad
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    backend.read_config()
    before = backend.resume_details()
    assert before is not None

    resume_source = target.parent / "resume_new.md"
    resume_source.write_text("Resume version NEW: Python engineer v2.\n", encoding="utf-8")
    resolved = resolve_workpad(
        home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True
    )
    imported = import_reference(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        kind="resume",
        source=resume_source,
        operation_key="resume-import-new",
    )
    create_record(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        kind="imported_reference",
        content_family="g45_reference",
        content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"},
        origin="imported",
        operation_key="resume-record-new",
    )

    after = backend.resume_details()
    assert after is not None
    assert after.pinned.record_id != before.pinned.record_id, (
        "cache served a stale resume after `gigai scout resume add`"
    )
