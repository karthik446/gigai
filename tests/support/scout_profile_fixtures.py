"""Shared fixture helpers for F1-a scout profile tests.

Builds a real, journal-authoritative workpad (via ``run_setup``/
``initialize_target``/``create_offline``, the same provisioning path
``gigai create`` uses) with a committed resume and a target-level
``find-jobs.json`` -- never a hand-faked git tree. Mirrors the S25 spike's
own prototype fixture (``orchestrator/research/S25-profiles/fixture.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import uuid

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.lifecycle import CreateResult, create_offline
from gigai.private_records import create_record, import_reference, migrate_workpad_layout
from gigai.scout.find_jobs.contracts import FindJobsConfig, SourceToggles
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import ResolvedWorkpad, resolve_workpad


def uuids(seed: int):
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{(seed * 1000 + value):012x}") for value in range(1, 256)
    )
    return lambda: next(values)


@dataclass(frozen=True)
class ProfileFixtureGig:
    created: CreateResult
    resolved: ResolvedWorkpad
    resume_record_id: str
    resume_revision_id: str
    home_root: Path
    target: Path


def default_find_jobs_config(
    *, roles: tuple[str, ...] = ("staff ai engineer", "principal machine learning engineer")
) -> FindJobsConfig:
    return FindJobsConfig(
        roles=roles,
        merged_queries=roles,
        location="Remote",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
        countries=("US",),
        visa_sponsorship_required=False,
    )


def build_gig_with_resume(
    tmp_path: Path,
    *,
    name: str = "profile-fixture-gig",
    seed: int = 1,
    resume_text: bytes = b"# Fixture Resume\n\nStaff AI Engineer. (fixture only.)\n",
    write_config: bool = True,
) -> ProfileFixtureGig:
    """One project/target, ONE gig, a committed resume, target-level find-jobs.json."""

    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True, exist_ok=True)

    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(home_root=home, requested_target=target, uuid_factory=uuids(seed))

    created = create_offline(
        home_root=home,
        requested_target=target,
        name=name,
        open_editor=False,
        uuid_factory=uuids(seed + 1),
    )

    migrate_workpad_layout(
        workpad=created.workpad, project_id=created.project_id, gig_id=created.gig_id, uuid_factory=uuids(seed + 10)
    )

    if write_config:
        config = default_find_jobs_config()
        (target / "find-jobs.json").write_bytes(canonical_json_bytes(config.to_json()))

    resume_path = tmp_path / f"resume-{seed}.md"
    resume_path.write_bytes(resume_text)
    digest = digest_imported_bytes(resume_text)
    imported = import_reference(
        home_root=home,
        requested_target=target,
        gig_id=created.gig_id,
        kind="resume",
        source=resume_path,
        operation_key=f"scout-resume-add:resume-{seed}.md:{digest}",
        uuid_factory=uuids(seed + 2),
    )
    record = create_record(
        home_root=home,
        requested_target=target,
        gig_id=created.gig_id,
        kind="imported_reference",
        content_family="g45_reference",
        content_id=imported.item_id,
        actor={"kind": "operator", "id": "local-user"},
        origin="imported",
        operation_key=f"scout-resume-record:{imported.item_id}",
        uuid_factory=uuids(seed + 3),
    )

    resolved = resolve_workpad(
        home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True
    )
    return ProfileFixtureGig(
        created=created,
        resolved=resolved,
        resume_record_id=record.record_id,
        resume_revision_id=record.revision_id,
        home_root=home,
        target=target,
    )


__all__ = ["ProfileFixtureGig", "build_gig_with_resume", "default_find_jobs_config", "uuids"]
