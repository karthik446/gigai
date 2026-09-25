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

F1-b1-r1 (coordinator's lane, a regression this packet introduced and then
split honestly rather than silently widening the original bound):
``ScoutFindJobsBackend.read_config()`` now also resolves the gig's SELECTED
profile (S25 F1-b's own overlay), which is a SECOND
``run_with_journal_writer`` acquisition per cold call -- and every such
acquisition used to pay a fixed, structural core cost this packet does
not own or touch: ``journal._require_mount_probes`` -> ``diagnostics.
_interprocess_lock_check`` spawning a whole new Python subprocess
(``sys.executable -m gigai.diagnostics --contend-lock``) to prove the
workpad's exclusion lock actually excludes, regardless of what git work
followed (profiled: ~0.35s on this fixture, on top of the pre-existing
~0.97s single resume resolution -- cProfile evidence in the worker
report). Two ADDITIONAL fixes landed alongside the bound split below (see
``server.py``'s ``_selected_profile``/``profile_records.
ensure_default_profile`` for the full writeup):
  - a real deadlock: the migration's own resume lookup
    (``_resolve_newest_resume_for_gig`` -> ``private_records.list_imports``)
    opens its OWN ``run_with_journal_writer`` -- nesting that inside an
    already-held writer lock hangs forever (the per-workpad lock is not
    reentrant). Fixed by checking for an existing default profile in its
    own, separate writer acquisition FIRST, and only resolving+opening a
    second writer for the resume + create when one is truly needed.
  - a cache-key timing bug: caching the selected profile under the
    PRE-call git HEAD (mirroring ``run.py``'s pure-read resume cache
    naively) guarantees a miss on every call after the first, because
    THIS call can itself commit (the first-ever migration) -- the cache
    key must be the POST-call HEAD.

F1-b1-r2 (2026-09-25): the proposed core packet landed
(``86e8b69``, "cache a passing workpad mount probe per process") --
``journal.py`` now caches a PASSING mount probe per process (keyed by the
workpad root plus its ``st_dev``/``st_ino``), so only the FIRST
``run_with_journal_writer`` acquisition in this test process ever pays the
subprocess spawn; every acquisition after that (including the SECOND one
this packet's own profile resolution added) is a cache hit. The
already-migrated/cold-cache bound below is tightened back to its original
1.0s.
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

# uat-bug-008's original bound, for an ALREADY-migrated gig with a cold
# (fresh-process) cache -- the normal path an operator hits on every Scout
# server start. F1-b1-r1 widened this to 1.5s because /api/config now also
# resolves the selected profile (one MORE run_with_journal_writer
# acquisition than pre-F1-b), and every such acquisition paid the core
# mount-probe subprocess spawn documented in this module's own docstring
# (~0.35s). F1-b1-r2: tightened back to the original 1.0s now that the
# proposed core probe-cache packet (``86e8b69``) landed -- the extra
# journal writer acquisition this packet adds no longer pays the probe
# subprocess after the first one per process, so there is no longer a
# structural reason for this bound to be wider than uat-bug-008's own.
_COLD_TIMING_BOUND_SECONDS = 1.0

# F1-b1-r1: a SEPARATE, wider bound for the RARE case this bound above does
# not cover -- the very first /api/config call on a gig that has never
# migrated a default profile yet. That call pays for TWO resume
# resolutions (the migration's own, to build the new profile's
# ``resume_ref``, plus ``resume_details()``'s) rather than one, since the
# migration's commit invalidates any cache entry the first resolution
# would have warmed. Coordinator decision: accept this as a legitimate,
# one-time-per-gig cost rather than engineer around it.
_FIRST_MIGRATION_TIMING_BOUND_SECONDS = 2.0

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


def _clear_process_caches() -> None:
    """Simulate a fresh server process's cold in-memory caches.

    F1-b1-r1: both ``run.py``'s per-journal-head resume cache and
    ``server.py``'s per-journal-head selected-profile cache are plain
    module-level dicts -- cleared directly here (never through a public
    API, since neither module exposes one; this is the honest way to
    reproduce "a fresh server process's first request" without actually
    spawning a second process).
    """

    from gigai import run as run_module
    from gigai.scout.find_jobs.api import server as server_module

    run_module._resume_details_cache.clear()
    server_module._selected_profile_cache.clear()
    server_module._profile_resume_details_cache.clear()


def test_get_config_resolves_the_resume_in_well_under_a_second(
    operator_shaped_workpad: tuple[Path, Path, str],
) -> None:
    """The exact calls ``_handle_get_config`` makes must be fast even cold,

    on an ALREADY-migrated gig (a default profile already exists from an
    earlier call -- the normal path an operator hits on every Scout server
    start, since the gig migrated once, long ago). Only the in-memory
    caches are cold here, never the on-disk migration state.

    Fails on the pre-fix code (``git show HEAD:src/gigai/run.py`` /
    ``git show HEAD:src/gigai/journal.py`` swapped in): that version takes
    low double-digit seconds on this fixture (see the worker report's
    baseline profile), far past this bound.
    """

    home, target, _gig_id = operator_shaped_workpad
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    # Migrate once, unmeasured -- this call's own cost belongs to
    # ``test_first_ever_config_call_on_an_unmigrated_gig`` below, not here.
    backend.read_config()
    backend.resume_details()
    _clear_process_caches()

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


def test_first_ever_config_call_on_an_unmigrated_gig(
    operator_shaped_workpad: tuple[Path, Path, str],
) -> None:
    """F1-b1-r1: the VERY FIRST ``/api/config`` call on a gig that has never

    migrated a default profile yet pays for the one-time migration too --
    a second resume resolution (the migration's own, to build the new
    profile's ``resume_ref``) on top of ``resume_details()``'s, since the
    migration's own commit invalidates any cache entry the first
    resolution would otherwise have warmed. This is a legitimate,
    one-time-per-gig cost (coordinator decision, 2026-09-25), bounded
    separately and more generously than the already-migrated case above.
    """

    home, target, _gig_id = operator_shaped_workpad
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    started = time.monotonic()
    config, _config_bytes = backend.read_config()
    resume_result = backend.resume_details()
    elapsed = time.monotonic() - started

    assert elapsed < _FIRST_MIGRATION_TIMING_BOUND_SECONDS, (
        f"the first-ever /api/config call (including migration) took "
        f"{elapsed:.3f}s, expected < {_FIRST_MIGRATION_TIMING_BOUND_SECONDS}s"
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


def test_resume_add_alone_does_not_change_the_selected_profiles_shown_resume(
    operator_shaped_workpad: tuple[Path, Path, str],
) -> None:
    """S25 F1-b2: ``resume_details()`` shows the SELECTED PROFILE's pinned resume,

    never workpad-wide "newest" -- so importing a new resume WITHOUT
    attaching it to the selected profile (``gigai scout resume add`` has no
    ``--profile`` in this test; F1-b2's own CLI change is asserted
    separately in ``test_scout_cli.py``) must NOT change what
    ``resume_details()`` shows: the selected profile's own ``resume_ref``
    still points at the old resume. This replaces this file's pre-F1-b2
    version of this test, which asserted the opposite ("newest always
    wins") -- that was exactly the behaviour F1-b2 intentionally retires
    once a selected-profile authority exists (see the S25 spike's
    Q2/ReaderChangeList row for ``resume_preview``/``resume_metadata``).
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
    assert after.pinned.record_id == before.pinned.record_id, (
        "resume_details() changed the shown resume after an UNATTACHED "
        "`gigai scout resume add` -- it must keep showing the selected "
        "profile's own pinned resume_ref, never workpad-wide newest"
    )


def test_resume_details_shows_the_selected_profiles_resume_after_a_switch(
    operator_shaped_workpad: tuple[Path, Path, str],
) -> None:
    """S25 F1-b2 acceptance: switching the selected profile changes what
    ``resume_details()`` shows to that profile's own pinned resume.
    """

    from gigai.private_records import create_record as _create_record
    from gigai.private_records import import_reference as _import_reference
    from gigai.scout.profile_records import create_profile, switch_selected_profile
    from gigai.scout.find_jobs.contracts import PinnedResume

    home, target, gig_id = operator_shaped_workpad
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    before = backend.resume_details()
    assert before is not None

    resolved = resolve_workpad(
        home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True
    )
    resume_source = target.parent / "resume_second_profile.md"
    resume_source.write_text("Resume for the SECOND profile.\n", encoding="utf-8")
    imported = _import_reference(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        kind="resume",
        source=resume_source,
        operation_key="resume-import-second-profile",
    )
    record = _create_record(
        home_root=home,
        requested_target=target,
        gig_id=resolved.gig_id,
        kind="imported_reference",
        content_family="g45_reference",
        content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"},
        origin="imported",
        operation_key="resume-record-second-profile",
    )
    second_resume_ref = PinnedResume(
        record_id=record.record_id,
        revision_id=record.revision_id,
        content_sha256=str(imported.record["content_sha256"]),
    )
    second_profile = create_profile(
        resolved,
        label="second",
        titles=("staff backend engineer",),
        titles_to_avoid=(),
        queries=("staff backend engineer",),
        resume_ref=second_resume_ref,
    )
    switch_selected_profile(resolved, profile_id=second_profile.profile_id)

    after = backend.resume_details()
    assert after is not None
    assert after.pinned.record_id == record.record_id, (
        "resume_details() did not follow the switched selected profile's own resume_ref"
    )
    assert after.pinned.record_id != before.pinned.record_id


def test_profile_edit_invalidates_the_selected_profile_cache(
    operator_shaped_workpad: tuple[Path, Path, str],
) -> None:
    """F1-b1-r1's own required test: the selected-profile cache

    (``server.py``'s ``_selected_profile_cache``) must never serve a stale
    effective config after the selected profile's content changes -- a
    real journal commit (``write_profile``), the same shape a setup-
    interview save or a profile switch produces. The very next
    ``read_config()`` call must see the NEW titles, never the cached ones.
    """

    from gigai.scout.profile_records import selected_profile, write_profile
    from gigai.workpad import resolve_workpad as _resolve_workpad

    home, target, _gig_id = operator_shaped_workpad
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    before, _ = backend.read_config()
    assert before.roles == ("software engineer",)  # warms the cache

    resolved = _resolve_workpad(
        home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True
    )
    profile = selected_profile(resolved, home_root=home, target=target)
    assert profile is not None
    write_profile(resolved, profile_id=profile.profile_id, titles=("staff backend engineer",), queries=("staff backend engineer",))

    after, _ = backend.read_config()
    assert after.roles == ("staff backend engineer",), (
        "read_config() served a stale effective config after a profile edit"
    )
