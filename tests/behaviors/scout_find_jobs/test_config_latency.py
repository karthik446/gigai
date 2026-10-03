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

0110-045: this file measured ``backend.read_config()`` +
``backend.resume_details()`` called bare, and those calls resolved and
checked the workpad again for each of their reads: 109 git launches cold
(208 for the first-ever call, 34 on a repeat), where the route itself,
inside ``workpad.committed_read_cache``, made 34 / 3. The backend's two
reads now open that cache themselves and read the head from ``.git``
files, so every caller pays the route's price: 30 cold, 48 first-ever, 0
on a repeat. The bounds below are those counts with a little room. The
time bound is this process's own CPU time (``time.process_time``): it does
not include the launched processes, a wait or another process's work, so a
loaded machine cannot flip it; the earlier bound took the wall clock and
subtracted an estimate of what launches cost, and failed under a 14-way
suite with no code change (2.824 s, 1.734 s of it launches).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import subprocess
import time
from pathlib import Path
from typing import Iterator

import pytest

from gigai import journal as journal_module
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
from tests.support.latency import latency_bound

# 0110-045: what a call may cost IN THIS PROCESS (``time.process_time``:
# the CPU this process itself used, never the launched git processes, a lock
# wait or what else the machine is doing), scaled by ``latency_bound()`` for
# CI. Measured on the reference Mac: 0.07 s already-migrated cold, 0.12 s
# first-ever, 0.01 s on a repeat. The bounds are several times that: they
# catch a slow in-process loop (the pre-uat-bug-008 per-artifact replay, a
# per-item schema validation), not a busy machine. How MANY processes a
# call launches is a property of the code, and the count bounds below are
# what pin it.
_COLD_CPU_BOUND_SECONDS = 1.0

# F1-b1-r1: the very first /api/config call on a gig that has never
# migrated a default profile also pays for the one-time migration (a second
# resume resolution and a journal commit). Coordinator decision: a
# legitimate, one-time-per-gig cost, bounded separately.
_FIRST_MIGRATION_CPU_BOUND_SECONDS = 2.0

# A repeat call against an unchanged workpad is a pure cache hit.
_WARM_CPU_BOUND_SECONDS = 0.3

# Git launches made while a journal snapshot is being captured
# (``journal._capture_committed_snapshot``, the one place both
# ``JournalWriter.snapshot`` and ``journal.read_committed_snapshot`` read
# committed history). Proves the batched read (uat-bug-008) is what runs:
# the per-artifact replay it replaced launched thousands on a comparable
# fixture. Measured on this fixture (2 resume imports + 25 run-input
# commits): 3 captures / 12 launches already-migrated, 5 captures / 17
# launches for the first-ever migrating call.
_SNAPSHOT_SUBPROCESS_BOUND_ALREADY_MIGRATED = 20
_SNAPSHOT_SUBPROCESS_BOUND_FIRST_MIGRATION = 30

# Every process the call launches, from any module (all git). Measured on
# this fixture, identical run to run and on Python 3.11 and 3.13: 30
# already-migrated cold, 48 first-ever, 0 on a warm repeat. Before 0110-045
# the same calls launched 109 / 208 / 34. The bounds leave room for one more
# read, not for another check of the whole workpad (13 launches).
_PROCESS_LAUNCH_BOUND_ALREADY_MIGRATED = 40
_PROCESS_LAUNCH_BOUND_FIRST_MIGRATION = 65
_PROCESS_LAUNCH_BOUND_WARM = 3

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
    # 0110-045: the backend's reads now keep their workpad checks and
    # committed journal reads (``workpad.committed_read_cache``); a fresh
    # process has none of those either.
    from gigai import workpad as workpad_module

    workpad_module._validated_repositories.clear()
    workpad_module._resolved_targets.clear()
    journal_module._validated_workpads.clear()
    journal_module._snapshot_cache.clear()
    journal_module._artifact_cache.clear()


@dataclass
class CallCost:
    """What one measured call cost, in counts and in seconds."""

    snapshot_captures: int = 0
    snapshot_process_launches: int = 0
    process_launches: int = 0
    cpu_seconds: float = 0.0


@contextmanager
def _measure_call() -> Iterator[CallCost]:
    """Count the journal snapshot captures and process launches in the body,

    and take the CPU time this process spent in it (never the wall clock:
    0110-045). ``subprocess.Popen.__init__`` is the one place every
    ``subprocess`` launch passes through, whichever module makes it;
    ``journal._capture_committed_snapshot`` is the one place committed
    history is read. Both are patched process-wide for the body only and
    restored in ``finally``; the measured calls are single-threaded.
    """

    cost = CallCost()
    original_snapshot = journal_module._capture_committed_snapshot
    original_popen_init = subprocess.Popen.__init__
    capturing = 0

    def counting_popen_init(self, *args, **kwargs):
        cost.process_launches += 1
        if capturing > 0:
            cost.snapshot_process_launches += 1
        return original_popen_init(self, *args, **kwargs)

    def counting_snapshot(*args, **kwargs):
        nonlocal capturing
        cost.snapshot_captures += 1
        capturing += 1
        try:
            return original_snapshot(*args, **kwargs)
        finally:
            capturing -= 1

    journal_module._capture_committed_snapshot = counting_snapshot
    subprocess.Popen.__init__ = counting_popen_init
    started = time.process_time()
    try:
        yield cost
    finally:
        cost.cpu_seconds = time.process_time() - started
        journal_module._capture_committed_snapshot = original_snapshot
        subprocess.Popen.__init__ = original_popen_init


def _assert_in_process_cpu(cost: CallCost, *, bound_seconds: float, what: str) -> None:
    """The CPU time this process spent in the call must be under

    ``bound_seconds`` (scaled for CI). Catches what a launch count cannot: a
    slow in-process loop. Launched processes and waiting are not in it, so
    what else the machine is doing cannot fail it.
    """

    bound = latency_bound(bound_seconds)
    assert cost.cpu_seconds < bound, (
        f"{what} used {cost.cpu_seconds:.3f}s of this process's CPU time "
        f"({cost.process_launches} process launches, not counted in it); expected < {bound}s"
    )


def test_get_config_resolves_the_resume_in_well_under_a_second(
    operator_shaped_workpad: tuple[Path, Path, str],
) -> None:
    """The exact calls ``_handle_get_config`` makes must be cheap even cold,

    on an ALREADY-migrated gig (a default profile already exists from an
    earlier call -- the normal path an operator hits on every Scout server
    start, since the gig migrated once, long ago). Only the in-memory
    caches are cold here, never the on-disk migration state.

    The pre-uat-bug-008 code replayed the entire committed journal per
    artifact, thousands of git launches on this fixture; both count bounds
    fail on it. 0110-045: before the backend's reads kept their workpad
    checks, this call launched 109 processes; the count bound fails on that.
    """

    home, target, _gig_id = operator_shaped_workpad
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    # Migrate once, unmeasured -- this call's own cost belongs to
    # ``test_first_ever_config_call_on_an_unmigrated_gig`` below, not here.
    backend.read_config()
    backend.resume_details()
    _clear_process_caches()

    with _measure_call() as cost:
        config, _config_bytes = backend.read_config()
        resume_result = backend.resume_details()

    assert cost.snapshot_process_launches < _SNAPSHOT_SUBPROCESS_BOUND_ALREADY_MIGRATED, (
        f"an already-migrated cold GET /api/config launched "
        f"{cost.snapshot_process_launches} git processes across "
        f"{cost.snapshot_captures} journal snapshot capture(s), expected < "
        f"{_SNAPSHOT_SUBPROCESS_BOUND_ALREADY_MIGRATED} -- looks like a "
        "regression back to the pre-uat-bug-008 per-artifact replay"
    )
    assert cost.process_launches < _PROCESS_LAUNCH_BOUND_ALREADY_MIGRATED, (
        f"an already-migrated cold GET /api/config launched {cost.process_launches} "
        f"processes, expected < {_PROCESS_LAUNCH_BOUND_ALREADY_MIGRATED}"
    )
    _assert_in_process_cpu(
        cost,
        bound_seconds=_COLD_CPU_BOUND_SECONDS,
        what="an already-migrated cold GET /api/config",
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

    with _measure_call() as cost:
        config, _config_bytes = backend.read_config()
        resume_result = backend.resume_details()

    assert cost.snapshot_process_launches < _SNAPSHOT_SUBPROCESS_BOUND_FIRST_MIGRATION, (
        f"the first-ever /api/config call launched "
        f"{cost.snapshot_process_launches} git processes across "
        f"{cost.snapshot_captures} journal snapshot capture(s), expected < "
        f"{_SNAPSHOT_SUBPROCESS_BOUND_FIRST_MIGRATION}"
    )
    assert cost.process_launches < _PROCESS_LAUNCH_BOUND_FIRST_MIGRATION, (
        f"the first-ever /api/config call launched {cost.process_launches} "
        f"processes, expected < {_PROCESS_LAUNCH_BOUND_FIRST_MIGRATION}"
    )
    _assert_in_process_cpu(
        cost,
        bound_seconds=_FIRST_MIGRATION_CPU_BOUND_SECONDS,
        what="the first-ever /api/config call (including migration)",
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
    cache, not repeat the expensive resolution: it reads no committed
    history at all, so it captures exactly zero journal snapshots.
    """

    home, target, _gig_id = operator_shaped_workpad
    backend = ScoutFindJobsBackend(home_root=home, target=target)

    backend.read_config()
    backend.resume_details()  # warms the per-journal-head cache

    with _measure_call() as cost:
        backend.read_config()
        backend.resume_details()

    assert cost.snapshot_captures == 0 and cost.snapshot_process_launches == 0, (
        f"a repeat /api/config call on an unchanged workpad captured "
        f"{cost.snapshot_captures} journal snapshot(s) "
        f"({cost.snapshot_process_launches} git processes); expected 0 -- the "
        "journal did not change, so this must be a pure cache hit"
    )
    assert cost.process_launches < _PROCESS_LAUNCH_BOUND_WARM, (
        f"a repeat /api/config call launched {cost.process_launches} "
        f"processes, expected < {_PROCESS_LAUNCH_BOUND_WARM}"
    )
    _assert_in_process_cpu(
        cost,
        bound_seconds=_WARM_CPU_BOUND_SECONDS,
        what="a repeat /api/config call",
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
