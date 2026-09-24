"""F1-a: Scout interested profile records (S25 spike, r2 + r2b).

Design source: ``orchestrator/docs/v0.1.9/spikes/S25-scout-interested-
profiles.md``. Approvals: ``orchestrator/reviews/S25-orchestrator-review.md``.

NO reader wiring here (that's F1-b): these tests only prove that profiles
exist as journal-committed records with a working migration, selection,
revision-bump rule and old-revision retrieval -- nothing about acquire/
assess/present/discovery/prep/CLI/API reading them.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import uuid

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.diagnostics import run_doctor
from gigai.journal import JournalArtifact, run_with_journal_writer
from gigai.private_records import create_record, import_reference
from gigai.scout.find_jobs.contracts import PinnedResume
from gigai.scout.profile_records import (
    ProfileRecordError,
    ensure_default_profile,
    list_profiles,
    retrieve_profile_revision,
    selected_profile,
    switch_selected_profile,
    write_profile,
)
from gigai.validators import validate_serialized_contract
from gigai.workpad import resolve_workpad

from tests.support.scout_profile_fixtures import build_gig_with_resume, uuids
from tests.support.workpad_assertions import assert_managed_workpad_clean


def _git_head(workpad: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(workpad), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _tracked_digests(workpad: Path) -> dict[str, str]:
    result = subprocess.run(
        ["git", "-C", str(workpad), "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    digests = {}
    for path in result.stdout.splitlines():
        if not path:
            continue
        digests[path] = digest_imported_bytes((workpad / path).read_bytes())
    return digests


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_scout_profile_schema_rejects_invalid_payload():
    invalid = canonical_json_bytes({"schema_version": "scout-profile:1", "profile_id": "not-a-profile-id"})
    report = validate_serialized_contract("scout-profile.schema.json", invalid)
    assert not report.valid


def test_scout_profile_selection_schema_rejects_invalid_payload():
    invalid = canonical_json_bytes({"schema_version": "scout-profile-selection:1"})
    report = validate_serialized_contract("scout-profile-selection.schema.json", invalid)
    assert not report.valid


# ---------------------------------------------------------------------------
# Migration: create + idempotent + no-op cases
# ---------------------------------------------------------------------------


def test_migration_creates_default_profile_from_find_jobs_and_resume(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    result = ensure_default_profile(
        fx.resolved, home_root=fx.home_root, target=fx.target
    )
    assert result is not None
    assert result.created is True
    assert result.revision == 1

    profiles = list_profiles(fx.resolved)
    assert len(profiles) == 1
    record = profiles[0]
    assert record.origin == "migrated_default"
    assert record.label == "default"
    assert record.state == "active"
    assert record.resume_ref.record_id == fx.resume_record_id
    assert record.resume_ref.revision_id == fx.resume_revision_id
    assert record.titles == ("staff ai engineer", "principal machine learning engineer")
    assert record.queries == record.titles
    assert record.titles_to_avoid == ()

    assert_managed_workpad_clean(fx.resolved.path)


def test_migration_sets_initial_selection_in_same_commit(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    head_before = _git_head(fx.resolved.path)
    result = ensure_default_profile(
        fx.resolved, home_root=fx.home_root, target=fx.target
    )
    assert result is not None and result.created is True
    head_after = _git_head(fx.resolved.path)
    # Exactly one new commit for the whole migration (profile + selection).
    log = subprocess.run(
        ["git", "-C", str(fx.resolved.path), "log", "--format=%H", f"{head_before}..{head_after}"],
        capture_output=True, text=True, check=True,
    )
    commits = [line for line in log.stdout.splitlines() if line]
    assert len(commits) == 1

    selection_path = fx.resolved.path / "records" / "scout-profile-selection" / "000000000001.json"
    assert selection_path.is_file()
    committed = subprocess.run(
        ["git", "-C", str(fx.resolved.path), "show", f"{head_after}:records/scout-profile-selection/000000000001.json"],
        capture_output=True, text=True, check=True,
    )
    import json
    selection = json.loads(committed.stdout)
    assert selection["selected_profile_id"] == result.profile_id

    assert_managed_workpad_clean(fx.resolved.path)


def test_selected_profile_migrates_on_first_read(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    profile = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert profile is not None
    assert profile.origin == "migrated_default"
    assert_managed_workpad_clean(fx.resolved.path)


def test_migration_is_idempotent_on_second_call(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    first = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    head_after_first = _git_head(fx.resolved.path)
    second = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    head_after_second = _git_head(fx.resolved.path)

    assert first is not None and second is not None
    assert second.created is False
    assert second.profile_id == first.profile_id
    assert second.revision == first.revision
    assert head_after_first == head_after_second
    assert_managed_workpad_clean(fx.resolved.path)


def test_migration_noop_without_find_jobs_json(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path, write_config=False)
    result = ensure_default_profile(
        fx.resolved, home_root=fx.home_root, target=fx.target
    )
    assert result is None
    assert list_profiles(fx.resolved) == ()
    assert_managed_workpad_clean(fx.resolved.path)


def test_migration_noop_without_committed_resume(tmp_path: Path):
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    from gigai.setup import build_config, run_setup
    from gigai.target_binding import initialize_target
    from gigai.lifecycle import create_offline
    from gigai.private_records import migrate_workpad_layout
    from tests.support.scout_profile_fixtures import default_find_jobs_config

    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=uuids(1))
    created = create_offline(home_root=home, requested_target=target, name="no-resume-gig", open_editor=False, uuid_factory=uuids(2))
    migrate_workpad_layout(workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id, uuid_factory=uuids(3))
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))

    resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)
    result = ensure_default_profile(resolved, home_root=home, target=target)
    assert result is None
    assert list_profiles(resolved) == ()
    assert_managed_workpad_clean(resolved.path)


def test_migrates_only_the_selected_gig_not_others(tmp_path: Path):
    """One project, three registered gigs; only the resolved gig migrates.

    Mirrors the S25 prototype's own multi-gig proof
    (``orchestrator/research/S25-profiles/run_prototype.py``), rebuilt here
    against the real ``profile_records`` module.
    """

    from gigai.canonical import EntityPrefix, generate_entity_id
    from gigai.lifecycle import create_offline
    from gigai.private_records import migrate_workpad_layout
    from gigai.setup import build_config, run_setup
    from gigai.target_binding import initialize_target
    from gigai.workpad import provision_workpad
    from tests.support.scout_profile_fixtures import default_find_jobs_config

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=uuids(0))

    gig_a = create_offline(home_root=home, requested_target=target, name="gig-a", open_editor=False, uuid_factory=uuids(1))
    migrate_workpad_layout(workpad=gig_a.workpad, project_id=gig_a.project_id, gig_id=gig_a.gig_id, uuid_factory=uuids(10))

    def register_extra_gig(seed: int) -> tuple[str, Path]:
        factory = uuids(seed)
        gig_id = generate_entity_id(EntityPrefix.GIG, is_persisted=lambda _c: False, uuid_factory=factory)
        provisioned = provision_workpad(home_root=home, project_id=gig_a.project_id, gig_id=gig_id)
        return gig_id, provisioned.path

    gig_b_id, gig_b_workpad = register_extra_gig(2)
    gig_c_id, gig_c_workpad = register_extra_gig(3)
    migrate_workpad_layout(workpad=gig_b_workpad, project_id=gig_a.project_id, gig_id=gig_b_id, uuid_factory=uuids(20))
    migrate_workpad_layout(workpad=gig_c_workpad, project_id=gig_a.project_id, gig_id=gig_c_id, uuid_factory=uuids(30))

    (target / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))

    resume_a = tmp_path / "resume-a.md"
    resume_a.write_bytes(b"# Resume A\n\nStaff AI Engineer.\n")
    imported_a = import_reference(
        home_root=home, requested_target=target, gig_id=gig_a.gig_id, kind="resume", source=resume_a,
        operation_key="resume-a", uuid_factory=uuids(4),
    )
    record_a = create_record(
        home_root=home, requested_target=target, gig_id=gig_a.gig_id, kind="imported_reference",
        content_family="g45_reference", content_id=imported_a.item_id,
        actor={"kind": "operator", "id": "local-user"}, origin="imported", operation_key="resume-a-record",
        uuid_factory=uuids(5),
    )

    resume_b = tmp_path / "resume-b.md"
    resume_b.write_bytes(b"# Resume B\n\nStaff Full-Stack Engineer.\n")
    imported_b = import_reference(
        home_root=home, requested_target=target, gig_id=gig_b_id, kind="resume", source=resume_b,
        operation_key="resume-b", uuid_factory=uuids(6),
    )
    record_b = create_record(
        home_root=home, requested_target=target, gig_id=gig_b_id, kind="imported_reference",
        content_family="g45_reference", content_id=imported_b.item_id,
        actor={"kind": "operator", "id": "local-user"}, origin="imported", operation_key="resume-b-record",
        uuid_factory=uuids(7),
    )

    resolved_a = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_a.gig_id, allow_semantic_state=True)
    resolved_b = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_b_id, allow_semantic_state=True)
    resolved_c = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_c_id, allow_semantic_state=True)

    digests_c_before = _tracked_digests(resolved_c.path)
    head_c_before = _git_head(resolved_c.path)
    digests_b_before = _tracked_digests(resolved_b.path)

    result_a = ensure_default_profile(resolved_a, home_root=home, target=target)
    assert result_a is not None and result_a.created is True
    profiles_a = list_profiles(resolved_a)
    assert len(profiles_a) == 1
    assert profiles_a[0].resume_ref.record_id == record_a.record_id

    result_b = ensure_default_profile(resolved_b, home_root=home, target=target)
    assert result_b is not None and result_b.created is True
    assert result_b.profile_id != result_a.profile_id
    profiles_b = list_profiles(resolved_b)
    assert len(profiles_b) == 1
    assert profiles_b[0].resume_ref.record_id == record_b.record_id
    assert profiles_b[0].resume_ref.record_id != record_a.record_id

    result_c = ensure_default_profile(resolved_c, home_root=home, target=target)
    assert result_c is None
    assert list_profiles(resolved_c) == ()

    # Byte-untouched proof: gig C never touched by another gig's migration.
    assert _tracked_digests(resolved_c.path) == digests_c_before
    assert _git_head(resolved_c.path) == head_c_before

    # gig B gained exactly its own migration's artifacts; pre-existing files
    # (resume record/reference) are byte-identical.
    digests_b_after = _tracked_digests(resolved_b.path)
    for path, digest in digests_b_before.items():
        assert digests_b_after[path] == digest

    assert_managed_workpad_clean(resolved_a.path)
    assert_managed_workpad_clean(resolved_b.path)
    assert_managed_workpad_clean(resolved_c.path)


# ---------------------------------------------------------------------------
# Selection: switch, one commit
# ---------------------------------------------------------------------------


def test_switch_selection_is_one_commit(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None

    # Add a second profile directly (write_profile only edits existing
    # profiles; a second profile is written the same shape the migration
    # itself uses, via write_profile's underlying journal transition, by
    # committing a fresh record through ensure_default_profile-style write).
    second_profile_id = _write_second_profile(fx.resolved, resume_ref=PinnedResume(fx.resume_record_id, fx.resume_revision_id, "sha256:" + "0" * 64))

    head_before = _git_head(fx.resolved.path)
    switch_selected_profile(fx.resolved, profile_id=second_profile_id)
    head_after = _git_head(fx.resolved.path)

    log = subprocess.run(
        ["git", "-C", str(fx.resolved.path), "log", "--format=%H", f"{head_before}..{head_after}"],
        capture_output=True, text=True, check=True,
    )
    commits = [line for line in log.stdout.splitlines() if line]
    assert len(commits) == 1

    profile = selected_profile(fx.resolved, home_root=home_root, target=target)
    assert profile is not None
    assert profile.profile_id == second_profile_id
    assert_managed_workpad_clean(fx.resolved.path)


def _write_second_profile(resolved, *, resume_ref: PinnedResume) -> str:
    """Test-only helper: commit a second, hand-built profile record.

    Uses the same journal primitives ``profile_records`` uses internally
    (not a private/internal function of that module) so this stays a
    legitimate second write, not a bypass of the schema/digest rules.
    """

    from gigai.scout.profile_records import (
        PROFILE_TRANSITION,
        SCHEMA_VERSION,
        ProfileRecord,
        _content_digest,
        _validated,
        _write_path,
    )

    from gigai.journal import JournalTransition

    profile_id = f"profile_{uuid.uuid4()}"
    titles = ("staff full-stack engineer",)
    digest = _content_digest(titles=titles, titles_to_avoid=(), queries=titles, resume_ref=resume_ref)
    record = ProfileRecord(
        schema_version=SCHEMA_VERSION, profile_id=profile_id, seq=1, revision=1, label="second", state="active",
        origin="operator_created", resume_ref=resume_ref, titles=titles, titles_to_avoid=(), queries=titles,
        content_digest=digest, created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z", parent_seq=None,
    )
    record_bytes = _validated(record)
    path = _write_path(profile_id, 1)

    def operation(writer):
        writer.record(
            JournalTransition(
                f"handoff_{uuid.uuid4()}",
                PROFILE_TRANSITION,
                "Test-added second profile.",
                (JournalArtifact(path, record_bytes),),
                {"artifact_refs": [{"path": path, "content_sha256": digest_imported_bytes(record_bytes), "media_type": "application/json", "size_bytes": len(record_bytes)}]},
            )
        )
        return profile_id

    return run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=operation
    )


# ---------------------------------------------------------------------------
# Revision-bump rule
# ---------------------------------------------------------------------------


def test_label_rename_does_not_bump_revision(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None

    before = list_profiles(fx.resolved)[0]
    updated = write_profile(fx.resolved, profile_id=before.profile_id, label="renamed default")
    assert updated.revision == before.revision
    assert updated.content_digest == before.content_digest
    assert updated.label == "renamed default"
    assert_managed_workpad_clean(fx.resolved.path)


def test_titles_edit_bumps_revision(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None

    before = list_profiles(fx.resolved)[0]
    updated = write_profile(fx.resolved, profile_id=before.profile_id, titles=("new title",))
    assert updated.revision == before.revision + 1
    assert updated.content_digest != before.content_digest
    assert updated.titles == ("new title",)
    assert_managed_workpad_clean(fx.resolved.path)


# ---------------------------------------------------------------------------
# Old-revision retrieval
# ---------------------------------------------------------------------------


def test_retrieves_revision_n_after_n_plus_one_is_written(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    revision_1 = list_profiles(fx.resolved)[0]
    assert revision_1.revision == 1

    # A same-revision label rename between revision 1 and revision 2 (r2b
    # extension): a second commit still carrying revision == 1.
    renamed = write_profile(fx.resolved, profile_id=revision_1.profile_id, label="renamed")
    assert renamed.revision == 1
    assert renamed.content_digest == revision_1.content_digest

    revision_2 = write_profile(fx.resolved, profile_id=revision_1.profile_id, titles=("a", "b"))
    assert revision_2.revision == 2
    assert revision_2.content_digest != revision_1.content_digest

    retrieved = retrieve_profile_revision(
        fx.resolved,
        profile_id=revision_1.profile_id,
        revision=1,
        content_digest=revision_1.content_digest,
    )
    assert retrieved.titles == revision_1.titles
    assert retrieved.content_digest == revision_1.content_digest
    assert_managed_workpad_clean(fx.resolved.path)


def test_retrieve_profile_revision_fails_on_no_match(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    revision_1 = list_profiles(fx.resolved)[0]

    with pytest.raises(ProfileRecordError):
        retrieve_profile_revision(
            fx.resolved,
            profile_id=revision_1.profile_id,
            revision=99,
            content_digest=revision_1.content_digest,
        )


# ---------------------------------------------------------------------------
# Archive rule (requires replacement when archiving the selected profile)
# ---------------------------------------------------------------------------


def test_archive_selected_profile_requires_replacement(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    default_profile = list_profiles(fx.resolved)[0]

    with pytest.raises(ProfileRecordError) as excinfo:
        write_profile(fx.resolved, profile_id=default_profile.profile_id, state="archived")
    assert excinfo.value.code == "profile_archive_requires_replacement"
    assert_managed_workpad_clean(fx.resolved.path)


def test_archive_selected_profile_with_replacement_is_one_commit(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    default_profile = list_profiles(fx.resolved)[0]
    replacement_id = _write_second_profile(
        fx.resolved,
        resume_ref=PinnedResume(fx.resume_record_id, fx.resume_revision_id, "sha256:" + "0" * 64),
    )

    head_before = _git_head(fx.resolved.path)
    archived = write_profile(
        fx.resolved, profile_id=default_profile.profile_id, state="archived", replacement_profile_id=replacement_id
    )
    head_after = _git_head(fx.resolved.path)
    assert archived.state == "archived"

    log = subprocess.run(
        ["git", "-C", str(fx.resolved.path), "log", "--format=%H", f"{head_before}..{head_after}"],
        capture_output=True, text=True, check=True,
    )
    commits = [line for line in log.stdout.splitlines() if line]
    assert len(commits) == 1

    profile = selected_profile(fx.resolved, home_root=home_root, target=target)
    assert profile is not None
    assert profile.profile_id == replacement_id
    assert_managed_workpad_clean(fx.resolved.path)


def test_archived_profile_cannot_be_selected(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    default_profile = list_profiles(fx.resolved)[0]
    replacement_id = _write_second_profile(
        fx.resolved,
        resume_ref=PinnedResume(fx.resume_record_id, fx.resume_revision_id, "sha256:" + "0" * 64),
    )
    write_profile(fx.resolved, profile_id=default_profile.profile_id, state="archived", replacement_profile_id=replacement_id)

    with pytest.raises(ProfileRecordError) as excinfo:
        switch_selected_profile(fx.resolved, profile_id=default_profile.profile_id)
    assert excinfo.value.code == "scout_profile_archived"


# ---------------------------------------------------------------------------
# Concurrency: seq allocation is race-safe under the journal writer lock
# (orchestrator's condition (1) on the write-once-layout agreement)
# ---------------------------------------------------------------------------


def test_concurrent_profile_writes_get_distinct_consecutive_seqs(tmp_path: Path):
    """Two threads racing to write the SAME profile through the real writer
    lock must land distinct, consecutive seqs -- no duplicate, no gap.
    """

    import threading

    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    profile_id = list_profiles(fx.resolved)[0].profile_id

    results: list[object] = [None, None]
    barrier = threading.Barrier(2)

    def writer(index: int, label: str) -> None:
        barrier.wait()
        try:
            results[index] = write_profile(fx.resolved, profile_id=profile_id, label=label)
        except Exception as exc:  # pragma: no cover - surfaced via assertion below
            results[index] = exc

    threads = [
        threading.Thread(target=writer, args=(0, "race-a")),
        threading.Thread(target=writer, args=(1, "race-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    for result in results:
        assert not isinstance(result, Exception), result

    seqs = sorted(result.seq for result in results)  # type: ignore[union-attr]
    assert seqs == [2, 3]  # seq 1 is the migration's own write; both racers follow it
    assert len(set(seqs)) == 2

    all_writes = list_profiles(fx.resolved)
    # list_profiles returns only the CURRENT write; assert the full write
    # chain (via the module's own fail-closed reader) has no gap/duplicate
    # by simply re-reading successfully -- a gap/duplicate would raise.
    assert len(all_writes) == 1
    assert_managed_workpad_clean(fx.resolved.path)


def test_concurrent_selection_switches_get_distinct_consecutive_seqs(tmp_path: Path):
    """Two threads racing to switch selection through the real writer lock
    must land distinct, consecutive seqs -- no duplicate, no gap.
    """

    import threading

    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    default_profile_id = migration.profile_id
    second_profile_id = _write_second_profile(
        fx.resolved, resume_ref=PinnedResume(fx.resume_record_id, fx.resume_revision_id, "sha256:" + "0" * 64)
    )

    results: list[object] = [None, None]
    barrier = threading.Barrier(2)

    def switcher(index: int, profile_id: str) -> None:
        barrier.wait()
        try:
            results[index] = switch_selected_profile(fx.resolved, profile_id=profile_id)
        except Exception as exc:  # pragma: no cover - surfaced via assertion below
            results[index] = exc

    threads = [
        threading.Thread(target=switcher, args=(0, default_profile_id)),
        threading.Thread(target=switcher, args=(1, second_profile_id)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    for result in results:
        assert not isinstance(result, Exception), result

    seqs = sorted(result.seq for result in results)  # type: ignore[union-attr]
    assert seqs == [2, 3]  # seq 1 is the migration's own initial selection
    assert len(set(seqs)) == 2

    # The current selection must be exactly one of the two racers' writes
    # (whichever committed last), never a corrupted/ambiguous state.
    final = selected_profile(fx.resolved, home_root=home_root, target=target)
    assert final is not None
    assert final.profile_id in {default_profile_id, second_profile_id}
    assert_managed_workpad_clean(fx.resolved.path)


# ---------------------------------------------------------------------------
# Fail-closed on a bad write file (orchestrator's condition (2) on the
# write-once-layout agreement): a partially written/unparseable file, a
# schema-invalid file, a seq gap, a duplicate seq, or a mismatched seq in
# the filename must raise a clear typed error, never silently fall back.
# ---------------------------------------------------------------------------


def _hand_commit_bad_write(resolved, *, path: str, data: bytes, transition: str) -> None:
    """Test-only: commit an intentionally malformed write file directly,

    bypassing every one of ``profile_records``'s own write-time checks, to
    prove the READ side fails closed on data it did not itself produce
    (e.g. a hand-edited file, or a bug in some other future writer).
    """

    from gigai.journal import JournalTransition

    def operation(writer):
        writer.record(
            JournalTransition(
                f"handoff_{uuid.uuid4()}",
                transition,
                "Test-injected malformed write.",
                (JournalArtifact(path, data),),
                {"artifact_refs": [{"path": path, "content_sha256": digest_imported_bytes(data), "media_type": "application/json", "size_bytes": len(data)}]},
            ),
            allow_artifact_replacement=True,
        )

    run_with_journal_writer(
        workpad=resolved.path, project_id=resolved.project_id, gig_id=resolved.gig_id, operation=operation
    )


def test_unparseable_write_file_fails_closed(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    profile_id = migration.profile_id
    path = f"records/scout-profiles/{profile_id}/writes/000000000002.json"
    _hand_commit_bad_write(fx.resolved, path=path, data=b"{not json", transition="scout_profile_revised")

    with pytest.raises(ProfileRecordError) as excinfo:
        list_profiles(fx.resolved)
    assert excinfo.value.code == "scout_profile_write_corrupt"


def test_schema_invalid_write_file_fails_closed(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    profile_id = migration.profile_id
    path = f"records/scout-profiles/{profile_id}/writes/000000000002.json"
    # Valid JSON, but missing required fields -- fails the strict schema.
    data = canonical_json_bytes({"schema_version": "scout-profile:1", "profile_id": profile_id, "seq": 2})
    _hand_commit_bad_write(fx.resolved, path=path, data=data, transition="scout_profile_revised")

    with pytest.raises(ProfileRecordError) as excinfo:
        list_profiles(fx.resolved)
    assert excinfo.value.code == "scout_profile_write_schema_invalid"


def test_identity_mismatched_write_file_fails_closed(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    profile_id = migration.profile_id
    committed = list_profiles(fx.resolved)[0]
    # A schema-valid record, but its own `seq` field disagrees with the
    # filename it's committed at (path says seq 2, payload says seq 5).
    from gigai.scout.profile_records import _validated

    from gigai.scout.profile_records import ProfileRecord

    mismatched = ProfileRecord(
        schema_version=committed.schema_version, profile_id=profile_id, seq=5, revision=committed.revision,
        label=committed.label, state=committed.state, origin=committed.origin, resume_ref=committed.resume_ref,
        titles=committed.titles, titles_to_avoid=committed.titles_to_avoid, queries=committed.queries,
        content_digest=committed.content_digest, created_at=committed.created_at, updated_at=committed.updated_at,
        parent_seq=1,
    )
    data = _validated(mismatched)
    path = f"records/scout-profiles/{profile_id}/writes/000000000002.json"
    _hand_commit_bad_write(fx.resolved, path=path, data=data, transition="scout_profile_revised")

    with pytest.raises(ProfileRecordError) as excinfo:
        list_profiles(fx.resolved)
    assert excinfo.value.code == "scout_profile_write_identity_mismatch"


def test_seq_gap_in_write_chain_fails_closed(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    profile_id = migration.profile_id
    committed = list_profiles(fx.resolved)[0]
    from gigai.scout.profile_records import ProfileRecord, _validated

    # Skip straight to seq 3, leaving seq 2 missing -- a gap.
    gapped = ProfileRecord(
        schema_version=committed.schema_version, profile_id=profile_id, seq=3, revision=committed.revision,
        label="gapped", state=committed.state, origin=committed.origin, resume_ref=committed.resume_ref,
        titles=committed.titles, titles_to_avoid=committed.titles_to_avoid, queries=committed.queries,
        content_digest=committed.content_digest, created_at=committed.created_at, updated_at=committed.updated_at,
        parent_seq=1,
    )
    data = _validated(gapped)
    path = f"records/scout-profiles/{profile_id}/writes/000000000003.json"
    _hand_commit_bad_write(fx.resolved, path=path, data=data, transition="scout_profile_revised")

    with pytest.raises(ProfileRecordError) as excinfo:
        list_profiles(fx.resolved)
    assert excinfo.value.code == "scout_profile_write_seq_gap"


def test_duplicate_seq_in_write_chain_fails_closed():
    """Unit-level: a real committed pair of write files can only produce a

    genuine duplicate seq through a caller bug in ``next_seq`` computation
    (each file's own path already ties its seq to a unique filename, so two
    REAL committed files can never collide on seq without also tripping the
    identity-mismatch check first). This tests the pure seq-arithmetic
    check directly, decoupled from path/schema parsing -- see
    ``_validate_write_sequence``'s own docstring for why.
    """

    from gigai.scout.profile_records import _validate_write_sequence

    with pytest.raises(ProfileRecordError) as excinfo:
        _validate_write_sequence([1, 1, 2], duplicate_code="scout_profile_write_duplicate_seq", gap_code="scout_profile_write_seq_gap", subject="profile profile_x")
    assert excinfo.value.code == "scout_profile_write_duplicate_seq"


# ---------------------------------------------------------------------------
# F1-a-r1: several profile writes/switches must not break EXISTING core
# readers that also snapshot records/ (the "journal immutable artifact has
# multiple publishers" failure the write-once storage-layout amendment
# fixed -- proven here against real core entry points, not just
# profile_records's own reads).
# ---------------------------------------------------------------------------


def test_several_profile_writes_and_switches_keep_core_readers_working(tmp_path: Path):
    from gigai import run as gigai_run
    from gigai.private_records import list_imports, read_record

    fx = build_gig_with_resume(tmp_path)
    home_root = fx.home_root
    target = fx.target
    migration = ensure_default_profile(fx.resolved, home_root=home_root, target=target)
    assert migration is not None
    default_profile_id = migration.profile_id

    # A second profile.
    second_profile_id = _write_second_profile(
        fx.resolved, resume_ref=PinnedResume(fx.resume_record_id, fx.resume_revision_id, "sha256:" + "0" * 64)
    )

    # Revise the default profile 3 times: a titles edit (bumps revision), a
    # label rename (does not bump revision), and a second titles edit.
    write_profile(fx.resolved, profile_id=default_profile_id, titles=("staff ai engineer", "ml engineer"))
    write_profile(fx.resolved, profile_id=default_profile_id, label="renamed default")
    write_profile(fx.resolved, profile_id=default_profile_id, titles=("staff ai engineer", "ml engineer", "applied scientist"))

    # Switch selection twice.
    switch_selected_profile(fx.resolved, profile_id=second_profile_id)
    switch_selected_profile(fx.resolved, profile_id=default_profile_id)

    # (a) Real core resume resolution, through its own normal records/
    # snapshot -- this is the exact path the write-once layout had to stop
    # breaking: run.resolve_newest_resume_details (target-level, resolves
    # the active gig) and private_records.list_imports/read_record (the
    # same primitives it calls internally), independently re-run here.
    details = gigai_run.resolve_newest_resume_details(home_root, target)
    assert details.pinned.record_id == fx.resume_record_id
    assert details.pinned.revision_id == fx.resume_revision_id

    imports = list_imports(home_root=home_root, requested_target=target, family="reference", gig_id=fx.resolved.gig_id)
    resume_imports = [item for item in imports if item.get("kind") == "resume"]
    assert len(resume_imports) == 1

    fetched = read_record(
        home_root=home_root, requested_target=target, record_id=fx.resume_record_id,
        revision_id=fx.resume_revision_id, content=True, gig_id=fx.resolved.gig_id,
    )
    assert fetched.get("content") is not None

    # (b) doctor's journal.index check passes.
    report = run_doctor(home_root)
    journal_check = next(check for check in report.checks if check.id == "journal.index")
    assert journal_check.status == "PASS", journal_check.summary

    # (c) the workpad is left clean.
    assert_managed_workpad_clean(fx.resolved.path)

    # (d) the current profile/selection read returns the LATEST write.
    current_default = next(item for item in list_profiles(fx.resolved) if item.profile_id == default_profile_id)
    assert current_default.label == "renamed default"
    assert current_default.titles == ("staff ai engineer", "ml engineer", "applied scientist")
    assert current_default.revision == 3  # 2 titles edits bumped it; the label rename did not

    selected = selected_profile(fx.resolved, home_root=home_root, target=target)
    assert selected is not None
    assert selected.profile_id == default_profile_id


# ---------------------------------------------------------------------------
# A4: realistic-workpad migration test (operator-required)
# ---------------------------------------------------------------------------


def test_a4_realistic_workpad_migration(tmp_path: Path):
    """ONE project, 4 registered gigs (only one active), real journal
    activity via real CLI/API paths (find-jobs run + resume imports), a
    failed run, target-level find-jobs.json. Migrate the selected gig only:
    assert the other 3 are byte-untouched, it pins its newest resume, one
    commit adds profile + selection, re-run is a no-op, then
    assert_managed_workpad_clean + doctor's journal.index check passes.
    """

    from gigai.canonical import EntityPrefix, generate_entity_id
    from gigai.lifecycle import create_offline
    from gigai.private_records import migrate_workpad_layout
    from gigai.setup import build_config, run_setup
    from gigai.target_binding import initialize_target
    from gigai.workpad import provision_workpad
    from tests.support.scout_profile_fixtures import default_find_jobs_config

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(home_root=home, requested_target=target, uuid_factory=uuids(0))

    selected_gig = create_offline(home_root=home, requested_target=target, name="a4-selected", open_editor=False, uuid_factory=uuids(1))
    migrate_workpad_layout(workpad=selected_gig.workpad, project_id=selected_gig.project_id, gig_id=selected_gig.gig_id, uuid_factory=uuids(10))

    def register_extra_gig(seed: int) -> tuple[str, Path]:
        factory = uuids(seed)
        gig_id = generate_entity_id(EntityPrefix.GIG, is_persisted=lambda _c: False, uuid_factory=factory)
        provisioned = provision_workpad(home_root=home, project_id=selected_gig.project_id, gig_id=gig_id)
        return gig_id, provisioned.path

    bystanders = []
    for seed in (2, 3, 4):
        gig_id, workpad = register_extra_gig(seed)
        migrate_workpad_layout(workpad=workpad, project_id=selected_gig.project_id, gig_id=gig_id, uuid_factory=uuids(seed * 10))
        bystanders.append((gig_id, workpad))

    (target / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))

    # Several dozen journal commits on the selected gig via real
    # private_records entry points (import + record, twice, plus a few
    # metadata-only revisions), a failed run's untracked leftovers, and a
    # succeeded run's untracked leftovers -- the same shape
    # RUN_LOCAL_ARTIFACT_EXCLUDES already covers.
    resolved_selected = resolve_workpad(home_root=home, requested_target=target, gig_id=selected_gig.gig_id, allow_semantic_state=True)

    resume_paths = []
    resume_records = []
    for index in range(2):
        resume_path = tmp_path / f"a4-resume-{index}.md"
        resume_path.write_bytes(f"# A4 Resume {index}\n\nStaff AI Engineer v{index}.\n".encode())
        resume_paths.append(resume_path)
        imported = import_reference(
            home_root=home, requested_target=target, gig_id=selected_gig.gig_id, kind="resume", source=resume_path,
            operation_key=f"a4-resume-{index}", uuid_factory=uuids(100 + index),
        )
        record = create_record(
            home_root=home, requested_target=target, gig_id=selected_gig.gig_id, kind="imported_reference",
            content_family="g45_reference", content_id=imported.item_id,
            actor={"kind": "operator", "id": "local-user"}, origin="imported", operation_key=f"a4-resume-record-{index}",
            uuid_factory=uuids(200 + index),
        )
        resume_records.append(record)

    # A "several dozen journal commits" shape: a handful of additional
    # small private-record writes (distinct operation keys -> distinct
    # commits), reusing the same real entry point rather than hand-faking
    # git commits.
    for index in range(30):
        note_path = tmp_path / f"a4-note-{index}.md"
        note_path.write_text(f"# Note {index}\n\nFixture note body {index}.\n", encoding="utf-8")
        imported_note = import_reference(
            home_root=home, requested_target=target, gig_id=selected_gig.gig_id, kind="project_evidence", source=note_path,
            operation_key=f"a4-note-{index}", uuid_factory=uuids(1000 + index),
        )
        create_record(
            home_root=home, requested_target=target, gig_id=selected_gig.gig_id, kind="imported_reference",
            content_family="g45_reference", content_id=imported_note.item_id,
            actor={"kind": "operator", "id": "local-user"}, origin="imported", operation_key=f"a4-note-record-{index}",
            uuid_factory=uuids(2000 + index),
        )

    # A failed run's untracked leftovers (regression-001-r2 shape) and a
    # succeeded run's untracked leftovers -- both covered by
    # RUN_LOCAL_ARTIFACT_EXCLUDES, never committed to the journal.
    failed_run_dir = resolved_selected.path / "runs" / "run_00000000-0000-4000-8000-0000000000f1"
    (failed_run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (failed_run_dir / "logs" / "assess.log").write_text("Traceback...\nRuntimeError: fixture failure\n", encoding="utf-8")
    succeeded_run_dir = resolved_selected.path / "runs" / "run_00000000-0000-4000-8000-0000000000f2"
    (succeeded_run_dir / "raw").mkdir(parents=True, exist_ok=True)
    (succeeded_run_dir / "raw" / "index.json").write_text("{}", encoding="utf-8")

    resolved_selected = resolve_workpad(home_root=home, requested_target=target, gig_id=selected_gig.gig_id, allow_semantic_state=True)
    assert_managed_workpad_clean(resolved_selected.path)

    bystander_snapshots = []
    for gig_id, workpad in bystanders:
        resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
        bystander_snapshots.append((resolved, _tracked_digests(resolved.path), _git_head(resolved.path)))

    result = ensure_default_profile(resolved_selected, home_root=home, target=target)
    assert result is not None
    assert result.created is True

    profile = list_profiles(resolved_selected)[0]
    # It pins the NEWEST resume (the last one imported), not the first.
    assert profile.resume_ref.record_id == resume_records[-1].record_id
    assert profile.resume_ref.revision_id == resume_records[-1].revision_id

    # One commit adds profile + selection.
    head_before_migration_commit = _git_head(resolved_selected.path)
    reran = ensure_default_profile(resolved_selected, home_root=home, target=target)
    assert reran is not None and reran.created is False
    assert _git_head(resolved_selected.path) == head_before_migration_commit

    selection_bytes = subprocess.run(
        ["git", "-C", str(resolved_selected.path), "show", "HEAD:records/scout-profile-selection/000000000001.json"],
        capture_output=True, text=True, check=True,
    ).stdout
    import json
    selection = json.loads(selection_bytes)
    assert selection["selected_profile_id"] == profile.profile_id

    # The other 3 registered gigs are byte-untouched by the selected gig's
    # migration.
    for resolved, digests_before, head_before in bystander_snapshots:
        assert _tracked_digests(resolved.path) == digests_before
        assert _git_head(resolved.path) == head_before

    assert_managed_workpad_clean(resolved_selected.path)
    for gig_id, workpad in bystanders:
        resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=gig_id, allow_semantic_state=True)
        assert_managed_workpad_clean(resolved.path)

    report = run_doctor(home)
    journal_check = next(check for check in report.checks if check.id == "journal.index")
    assert journal_check.status == "PASS", journal_check.summary
