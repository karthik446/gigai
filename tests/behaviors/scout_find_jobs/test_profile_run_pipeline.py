"""F1-b1: runs use the selected profile; reuse key gets profile scoping.

Design source: ``orchestrator/docs/v0.1.9/spikes/S25-scout-interested-
profiles.md`` (Q2/Q3, "Complete reader disposition", A2, "Legacy-run
policy", Amendment A1, and the Packet plan's F1-b row); the coordinator's
F1-b1 dispatch resolution for the digest-guard/effective-config seam.

Scope: a find-jobs run uses the SELECTED profile's titles/queries/resume,
and seals which profile it used (``FindJobsRunInput.profile_ref``); the
assess carry-forward predicate only reuses an assessment from the same
profile AND the same resume revision. Each test asserts the END outcome
(what gets sealed, assessed, or skipped) -- never an intermediate.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import uuid

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.lifecycle import create_offline
from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    AcquireOutput,
    AssessmentResult,
    AssessOutput,
    FindJobsConfig,
    MatrixStatus,
    ModelTarget,
    NodeContext,
    PinnedResume,
    PostingRow,
    PostingRowResult,
    Producer,
    ProfileRef,
    RequirementMatrixRow,
    RowOutcome,
    SelectedPosting,
    SelectionRule,
    SourceKind,
    SourceToggles,
    ATSProvider,
)
from gigai.scout.find_jobs.effective_config import overlay_selected_profile
from gigai.scout.find_jobs.market_acquisition import acquire_node
from gigai.scout.profile_records import ensure_default_profile, selected_profile, write_profile
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai import run as run_module

from tests.support.scout_profile_fixtures import build_gig_with_resume, default_find_jobs_config, uuids

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# ProfileRef contract round-trip (additive field)
# ---------------------------------------------------------------------------


def _run_input_payload(*, profile_ref: dict | None) -> dict:
    payload = {
        "schema_version": "scout-find-jobs-run-input:1",
        "config": FindJobsConfig.from_json(
            json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text())
        ).to_json(),
        "config_digest": FindJobsConfig.from_json(
            json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text())
        ).digest(),
        "selection_cap": 10,
        "selection_rule": "new_or_edited_role_match",
        "model_target": "ollama_local",
        "pinned_resume": {
            "record_id": "resume-record-001",
            "revision_id": "resume-revision-007",
            "content_sha256": "sha256:" + "9" * 64,
        },
    }
    if profile_ref is not None:
        payload["profile_ref"] = profile_ref
    return payload


def test_profile_ref_round_trips_through_find_jobs_run_input():
    from gigai.scout.find_jobs.contracts import FindJobsRunInput

    profile_ref = {"profile_id": "profile_" + "0" * 8 + "-0000-4000-8000-000000000001", "revision": 3, "content_digest": "sha256:" + "a" * 64}
    parsed = FindJobsRunInput.from_json(_run_input_payload(profile_ref=profile_ref))
    assert parsed.profile_ref == ProfileRef(profile_ref["profile_id"], 3, profile_ref["content_digest"])
    # Byte round-trip: to_json() -> from_json() reproduces the identical ref.
    again = FindJobsRunInput.from_json(parsed.to_json())
    assert again.profile_ref == parsed.profile_ref


def test_old_sealed_input_without_profile_ref_still_parses_with_the_same_digest():
    """An old sealed run's exact bytes (no ``profile_ref`` key at all) --

    the real fixture ``fixture-run-input-v1.json`` -- must still parse, with
    ``profile_ref is None``, and its digest is computed the same way either
    way (additive field, omitted at its None default -- never a schema
    bump)."""
    from gigai.scout.find_jobs.contracts import FindJobsRunInput

    raw = (FIXTURES / "fixture-run-input-v1.json").read_bytes()
    payload = json.loads(raw)
    assert "profile_ref" not in payload
    parsed = FindJobsRunInput.from_json(payload)
    assert parsed.profile_ref is None
    # to_json() must omit the key too, so the digest is unaffected.
    assert "profile_ref" not in parsed.to_json()
    assert canonical_json_bytes(parsed.to_json()) == canonical_json_bytes(payload)


# ---------------------------------------------------------------------------
# effective_config: profile titles win over the shared find-jobs.json
# ---------------------------------------------------------------------------


def test_post_migration_profile_titles_win_over_target_level_find_jobs_roles(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path)
    profile = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert profile is not None

    # Change the profile's titles -- the shared find-jobs.json's roles are
    # left exactly as the fixture wrote them (untouched, still physically
    # present with the OLD values).
    updated = write_profile(
        fx.resolved, profile_id=profile.profile_id, titles=("staff backend engineer",), queries=("staff backend engineer",)
    )
    shared_config = FindJobsConfig.from_json(
        json.loads((fx.target / "find-jobs.json").read_bytes())
    )
    assert shared_config.roles == ("staff ai engineer", "principal machine learning engineer")

    effective = overlay_selected_profile(shared_config, updated)
    assert effective.roles == ("staff backend engineer",)
    assert effective.merged_queries == ("staff backend engineer",)
    # Every other field stays the shared file's own value.
    assert effective.location == shared_config.location
    assert effective.countries == shared_config.countries


def test_overlay_with_no_profile_falls_back_to_shared_config_unchanged():
    config = default_find_jobs_config()
    assert overlay_selected_profile(config, None) is config


# ---------------------------------------------------------------------------
# api-e2e finding (coordinator, 2026-09-25): migration must not fire while
# find-jobs.json is still the STARTER placeholder -- a real roles write
# moments later must not be silently shadowed by a placeholder-titled
# default profile that already won.
# ---------------------------------------------------------------------------


def test_migration_no_ops_while_find_jobs_json_is_still_the_starter_placeholder(tmp_path: Path):
    from gigai.scout.scout_cli import STARTER_FIND_JOBS_CONFIG

    fx = build_gig_with_resume(
        tmp_path, write_config=False,
    )
    (fx.target / "find-jobs.json").write_bytes(canonical_json_bytes(STARTER_FIND_JOBS_CONFIG.to_json()))

    result = ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert result is None

    profile = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert profile is None

    # The effective config falls back to the shared (starter) file exactly
    # as pre-F1-b -- nothing breaks while there is truly no profile yet.
    effective = overlay_selected_profile(STARTER_FIND_JOBS_CONFIG, profile)
    assert effective.roles == STARTER_FIND_JOBS_CONFIG.roles


def test_migration_fires_once_real_roles_replace_the_starter_placeholder(tmp_path: Path):
    from gigai.scout.scout_cli import STARTER_FIND_JOBS_CONFIG

    fx = build_gig_with_resume(tmp_path, write_config=False)
    (fx.target / "find-jobs.json").write_bytes(canonical_json_bytes(STARTER_FIND_JOBS_CONFIG.to_json()))
    assert ensure_default_profile(fx.resolved, home_root=fx.home_root, target=fx.target) is None

    real_config = default_find_jobs_config()
    (fx.target / "find-jobs.json").write_bytes(canonical_json_bytes(real_config.to_json()))

    profile = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert profile is not None
    assert profile.titles == real_config.roles
    assert profile.queries == real_config.merged_queries


# ---------------------------------------------------------------------------
# run.resolve_profile_resume: pins the GIVEN gig, never the active one
# ---------------------------------------------------------------------------


def test_resolve_profile_resume_uses_the_given_gig_not_the_active_one(tmp_path: Path):
    """Two registered gigs under one target/project; gig A stays active.

    Resolving gig B's own profile resume (an explicitly resolved, inactive
    gig) via ``run.resolve_profile_resume`` must return gig B's resume, not
    gig A's -- the exact cross-gig-distinct-resume proof the S25 spike's
    migration prototype already established for the migration path, now
    proven for the F1-b runtime reader too (r2 item 2 / Q2's reader-
    disposition table entry for ``resolve_newest_resume_details``'s
    ``gig_id=None`` hardcode).
    """
    from gigai.canonical import EntityPrefix, generate_entity_id
    from gigai.workpad import provision_workpad, resolve_workpad
    from gigai.private_records import migrate_workpad_layout

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)
    run_setup(
        build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False)
    )
    initialize_target(home_root=home, requested_target=target, uuid_factory=uuids(1))

    gig_a = create_offline(home_root=home, requested_target=target, name="gig-a", open_editor=False, uuid_factory=uuids(2))
    migrate_workpad_layout(workpad=gig_a.workpad, project_id=gig_a.project_id, gig_id=gig_a.gig_id, uuid_factory=uuids(20))

    # gig B: registered under the SAME project/target, never made active
    # (gig A stays the project's active gig throughout) -- provision_workpad
    # is the same lower-level primitive create_offline itself calls, but it
    # needs an explicit gig_id allocated first (it never derives one).
    gig_b_id = generate_entity_id(EntityPrefix.GIG, is_persisted=lambda _candidate: False, uuid_factory=uuids(3))
    provisioned_b = provision_workpad(home_root=home, project_id=gig_a.project_id, gig_id=gig_b_id)
    migrate_workpad_layout(workpad=provisioned_b.path, project_id=provisioned_b.project_id, gig_id=provisioned_b.gig_id, uuid_factory=uuids(30))

    from gigai.private_records import create_record, import_reference

    def _add_resume(gig_id: str, seed: int, text: bytes) -> tuple[str, str]:
        resume_path = tmp_path / f"resume-{seed}.md"
        resume_path.write_bytes(text)
        digest = digest_imported_bytes(text)
        imported = import_reference(
            home_root=home, requested_target=target, gig_id=gig_id, kind="resume", source=resume_path,
            operation_key=f"scout-resume-add:resume-{seed}.md:{digest}", uuid_factory=uuids(seed),
        )
        record = create_record(
            home_root=home, requested_target=target, gig_id=gig_id, kind="imported_reference",
            content_family="g45_reference", content_id=imported.item_id,
            actor={"kind": "operator", "id": "local-user"}, origin="imported",
            operation_key=f"scout-resume-record:{imported.item_id}", uuid_factory=uuids(seed + 1),
        )
        return record.record_id, record.revision_id

    record_a, _revision_a = _add_resume(gig_a.gig_id, 400, b"# Resume A\nStaff AI engineer.\n")
    record_b, revision_b = _add_resume(provisioned_b.gig_id, 500, b"# Resume B\nStaff backend engineer.\n")
    assert record_a != record_b

    resolved_b = resolve_workpad(home_root=home, requested_target=target, gig_id=provisioned_b.gig_id, allow_semantic_state=True)

    pinned = run_module.resolve_profile_resume(resolved_b, record_b, revision_b, home_root=home, target=target)
    assert pinned.record_id == record_b
    assert pinned.revision_id == revision_b
    assert pinned.record_id != record_a


# ---------------------------------------------------------------------------
# Acquire/assess carry-forward: profile identity is part of the cache key
# ---------------------------------------------------------------------------


def _managed_workpad(tmp_path: Path, name: str = "profile-pipeline-proof") -> tuple[Path, str, str]:
    home, target = tmp_path / "home", tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False)
    )
    initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
    values = iter(uuid.UUID(f"00000000-0000-4000-8000-{value:012x}") for value in range(1, 32))
    created = create_offline(home_root=home, requested_target=target, name=name, open_editor=False, uuid_factory=lambda: next(values))
    return created.workpad, created.project_id, created.gig_id


def _context(workpad: Path, project_id: str, gig_id: str, run_id: str, *, operation_key: str) -> NodeContext:
    return NodeContext(
        run_id=run_id, project_id=project_id, gig_id=gig_id,
        graph_id="find-jobs:functional", graph_version=1, goal_slug="acquire",
        manifest_digest="sha256:" + "a" * 64, operation_key=operation_key,
        target_observation_digest="sha256:" + "b" * 64,
        workpad_path=str(workpad), redeemed_consent_ref="consent",
        model_target="ollama_local",
    )


def _config() -> FindJobsConfig:
    payload = json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text())
    config = FindJobsConfig.from_json(payload)
    return replace(config, sources=SourceToggles(exa=False, ats=False, hiringcafe=False))


def _row(url: str, *, company: str = "Acme", title: str = "Software Engineer", content_sha256: str = "sha256:" + "c" * 64) -> PostingRow:
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token="acme",
        company=company, title=title, location="Denver, CO",
        published_at="2026-09-20T00:00:00Z", content_sha256=content_sha256,
        source_kind=SourceKind.EXA, query_key="software-engineer",
    )


class _Exa:
    def search(self, client, config, *, home_root=None):
        return ()


class _ATS:
    def list_board(self, client, provider, board_token, config):
        return ()


class _Watchlist:
    def add_to_watchlist(self, entry):
        return entry

    def active_entries(self):
        return ()


def _seal_run_input(workpad: Path, run_id: str, *, resume_revision_id: str, profile_ref: dict | None) -> None:
    config = _config()
    run_dir = workpad / "runs" / run_id
    (run_dir / "sealed").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "scout-find-jobs-run-input:1",
        "config": config.to_json(),
        "config_digest": config.digest(),
        "selection_cap": 10,
        "selection_rule": "new_or_edited_role_match",
        "model_target": "ollama_local",
        "pinned_resume": {
            "record_id": "record_00000000-0000-4000-8000-000000000001",
            "revision_id": resume_revision_id,
            "content_sha256": "sha256:" + "d" * 64,
        },
    }
    if profile_ref is not None:
        payload["profile_ref"] = profile_ref
    (run_dir / "sealed" / "find-jobs-run-input.json").write_text(json.dumps(payload))


def _write_successful_assess_output(workpad: Path, run_id: str, *, posting: PostingRow, resume_revision_id: str) -> None:
    assert posting.content_sha256 is not None
    selected = SelectedPosting(posting.normalized_url, posting.url, posting.content_sha256, True)
    pinned = PinnedResume("record_00000000-0000-4000-8000-000000000001", resume_revision_id, "sha256:" + "d" * 64)
    assessment = AssessmentResult(
        posting=selected,
        matrix=(RequirementMatrixRow("Python", ("Built APIs",), MatrixStatus.MET),),
        suggestions=(), questions=(), proposal_revision_ref=None,
    )
    output = AssessOutput(
        selected_postings=(selected,), pinned_resume=pinned, target=str(workpad),
        selection_cap=10, selection_rule=SelectionRule.NEW_OR_EDITED_ROLE_MATCH,
        candidate_rows=(PostingRowResult(posting, RowOutcome.NEW),),
        assessments=(assessment,), not_assessed=(), proposal_revision_refs=(),
        model_target=ModelTarget.OLLAMA_LOCAL,
        producer=Producer("scout.find_jobs.assess", "1", "scout-assess", ModelTarget.OLLAMA_LOCAL, "fixture"),
        usage=None, failures=(),
    )
    outputs_dir = workpad / "runs" / run_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "assess.json").write_bytes(canonical_json_bytes(output.to_json()))


def _acquire(workpad: Path, project_id: str, gig_id: str, run_id: str, rows: list[PostingRow]) -> AcquireOutput:
    input = AcquireInput(_config(), "sha256:" + "e" * 64, None, tuple(rows), 10, SelectionRule.NEW_OR_EDITED_ROLE_MATCH)
    return acquire_node(
        _context(workpad, project_id, gig_id, run_id, operation_key=f"acquire-{run_id}"), input,
        http_client=None, exa=_Exa(), ats=_ATS(), watchlist=_Watchlist(),
    )


def _profile_ref(profile_id_suffix: str, *, revision: int = 1) -> dict:
    return {
        "profile_id": "profile_00000000-0000-4000-8000-" + profile_id_suffix,
        "revision": revision,
        "content_digest": "sha256:" + "f" * 64,
    }


def test_legacy_run_without_profile_ref_attributes_to_default_profile(tmp_path: Path):
    """No profile has ever been migrated for this fake/non-journaled workpad

    (``_default_profile_id`` degrades to ``None`` for it, per its own
    docstring), so BOTH the legacy run and the new run attribute to
    ``profile_id=None`` -- they still match each other on that dimension,
    proving legacy attribution is consistent rather than "excluded" or
    "visible to all profiles."
    """
    workpad, project_id, gig_id = _managed_workpad(tmp_path)
    resume_revision_id = "revision_00000000-0000-4000-8000-000000000001"
    posting = _row("https://boards.greenhouse.io/acme/jobs/601")

    run1_id = "run_00000000-0000-4000-8000-000000000061"
    _seal_run_input(workpad, run1_id, resume_revision_id=resume_revision_id, profile_ref=None)
    _acquire(workpad, project_id, gig_id, run1_id, [posting])
    _write_successful_assess_output(workpad, run1_id, posting=posting, resume_revision_id=resume_revision_id)

    run2_id = "run_00000000-0000-4000-8000-000000000062"
    _seal_run_input(workpad, run2_id, resume_revision_id=resume_revision_id, profile_ref=None)
    out2 = _acquire(workpad, project_id, gig_id, run2_id, [posting])

    assert {row.outcome for row in out2.rows} == {RowOutcome.UNCHANGED}
    assert out2.selected_postings == ()
    assert len(out2.carried_forward_assessments) == 1


def test_legacy_resume_revision_x_assessment_does_not_carry_into_default_profile_run_pinned_to_y(tmp_path: Path):
    """A legacy run's successful assessment (resume revision X) must NOT

    carry forward into a new run pinned to a DIFFERENT resume revision Y,
    even though both attribute to the same (default) profile identity --
    the corrected A2 cache key keeps the resume-revision dimension
    independent of the profile dimension (r2's named regression test)."""
    workpad, project_id, gig_id = _managed_workpad(tmp_path)
    posting = _row("https://boards.greenhouse.io/acme/jobs/701")

    run1_id = "run_00000000-0000-4000-8000-000000000071"
    old_revision = "revision_00000000-0000-4000-8000-000000000001"
    _seal_run_input(workpad, run1_id, resume_revision_id=old_revision, profile_ref=None)
    _acquire(workpad, project_id, gig_id, run1_id, [posting])
    _write_successful_assess_output(workpad, run1_id, posting=posting, resume_revision_id=old_revision)

    run2_id = "run_00000000-0000-4000-8000-000000000072"
    new_revision = "revision_00000000-0000-4000-8000-000000000099"
    _seal_run_input(workpad, run2_id, resume_revision_id=new_revision, profile_ref=None)
    out2 = _acquire(workpad, project_id, gig_id, run2_id, [posting])

    assert {row.outcome for row in out2.rows} == {RowOutcome.UNCHANGED}
    assert len(out2.selected_postings) == 1
    assert out2.selected_postings[0].normalized_url == posting.normalized_url
    assert out2.carried_forward_assessments == ()


def test_profile_b_run_never_carries_forward_profile_a_assessment_of_same_posting(tmp_path: Path):
    """Two DIFFERENT profiles' runs, same resume revision, same posting --

    profile A's successful assessment must never be carried forward into
    profile B's run of the identical posting content (A2's cross-profile
    leak the corrected cache key closes)."""
    workpad, project_id, gig_id = _managed_workpad(tmp_path)
    resume_revision_id = "revision_00000000-0000-4000-8000-000000000001"
    posting = _row("https://boards.greenhouse.io/acme/jobs/801")

    profile_a = _profile_ref("00000000000a")
    profile_b = _profile_ref("00000000000b")

    run1_id = "run_00000000-0000-4000-8000-000000000081"
    _seal_run_input(workpad, run1_id, resume_revision_id=resume_revision_id, profile_ref=profile_a)
    _acquire(workpad, project_id, gig_id, run1_id, [posting])
    _write_successful_assess_output(workpad, run1_id, posting=posting, resume_revision_id=resume_revision_id)

    run2_id = "run_00000000-0000-4000-8000-000000000082"
    _seal_run_input(workpad, run2_id, resume_revision_id=resume_revision_id, profile_ref=profile_b)
    out2 = _acquire(workpad, project_id, gig_id, run2_id, [posting])

    assert {row.outcome for row in out2.rows} == {RowOutcome.UNCHANGED}
    # profile B never assessed this posting before -- it must be re-eligible,
    # never silently carried forward from profile A's result.
    assert len(out2.selected_postings) == 1
    assert out2.selected_postings[0].normalized_url == posting.normalized_url
    assert out2.carried_forward_assessments == ()


def test_same_profile_same_resume_revision_still_carries_forward(tmp_path: Path):
    """Sanity check: the SAME profile, same resume revision, same posting --

    carry-forward still works exactly as uat-bug-009 fixed it; the new
    profile_id dimension must not break the existing, correct case."""
    workpad, project_id, gig_id = _managed_workpad(tmp_path)
    resume_revision_id = "revision_00000000-0000-4000-8000-000000000001"
    posting = _row("https://boards.greenhouse.io/acme/jobs/901")
    profile_a = _profile_ref("00000000000a")

    run1_id = "run_00000000-0000-4000-8000-000000000091"
    _seal_run_input(workpad, run1_id, resume_revision_id=resume_revision_id, profile_ref=profile_a)
    _acquire(workpad, project_id, gig_id, run1_id, [posting])
    _write_successful_assess_output(workpad, run1_id, posting=posting, resume_revision_id=resume_revision_id)

    run2_id = "run_00000000-0000-4000-8000-000000000092"
    _seal_run_input(workpad, run2_id, resume_revision_id=resume_revision_id, profile_ref=profile_a)
    out2 = _acquire(workpad, project_id, gig_id, run2_id, [posting])

    assert {row.outcome for row in out2.rows} == {RowOutcome.UNCHANGED}
    assert out2.selected_postings == ()
    assert len(out2.carried_forward_assessments) == 1
    assert out2.carried_forward_assessments[0].normalized_url == posting.normalized_url


# ---------------------------------------------------------------------------
# Legacy-run policy: a run after switching profiles seals the new profile
# ---------------------------------------------------------------------------


def test_run_after_switching_profiles_seals_the_new_profile_ref_and_uses_its_resume(tmp_path: Path):
    """After the selected profile's resume pin changes (a resume-add-shaped

    edit, per the bump rule -- ``resume_ref`` is one of the four bumping
    fields), the NEXT run resolves and seals THAT updated pin, not the
    original fixture resume -- proven directly against ``profile_records``/
    ``run.resolve_profile_resume`` (the same seam ``server.py``'s
    ``start_run`` calls: resolve the selected profile, then pin its own
    resume_ref), without needing the HTTP layer. F1-b1 owns no profile
    CREATE surface (that is F1-c's ``create_profile()``), so this proves
    the switch via a revision of the one profile F1-a's migration
    produces, which is exactly what a real "add a new resume to the
    selected profile" edit does to the SAME profile_id.
    """
    fx = build_gig_with_resume(tmp_path)
    default_profile = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert default_profile is not None
    original_revision = default_profile.revision

    second_resume_path = tmp_path / "resume-second.md"
    second_resume_path.write_bytes(b"# Second Resume\nStaff backend engineer.\n")
    from gigai.private_records import create_record, import_reference

    digest = digest_imported_bytes(second_resume_path.read_bytes())
    imported = import_reference(
        home_root=fx.home_root, requested_target=fx.target, gig_id=fx.created.gig_id, kind="resume",
        source=second_resume_path, operation_key=f"scout-resume-add:resume-second.md:{digest}",
        uuid_factory=uuids(90),
    )
    record = create_record(
        home_root=fx.home_root, requested_target=fx.target, gig_id=fx.created.gig_id, kind="imported_reference",
        content_family="g45_reference", content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"}, origin="imported",
        operation_key=f"scout-resume-record:{imported.item_id}", uuid_factory=uuids(91),
    )
    second_pinned = PinnedResume(record.record_id, record.revision_id, digest)
    revised = write_profile(fx.resolved, profile_id=default_profile.profile_id, resume_ref=second_pinned)
    assert revised.revision == original_revision + 1  # resume_ref bumps revision (Q1's rule)

    current = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert current is not None
    assert current.profile_id == default_profile.profile_id  # same profile, new content
    assert current.resume_ref.record_id == record.record_id
    assert current.resume_ref.record_id != fx.resume_record_id

    pinned = run_module.resolve_profile_resume(
        fx.resolved, current.resume_ref.record_id, current.resume_ref.revision_id,
        home_root=fx.home_root, target=fx.target,
    )
    assert pinned.record_id == record.record_id
    assert pinned.revision_id == record.revision_id
