"""S25 F1-b2: ``gigai scout resume add --profile`` behavior.

Design source: ``orchestrator/docs/v0.1.9/spikes/S25-scout-interested-
profiles.md`` (open question 5, DECIDED 2026-09-24, r2b): an omitted
``--profile`` attaches the just-imported resume to the SELECTED profile and
the command MUST print which profile it attached to (label + profile_id;
``profile_id`` alone in ``--json`` output).

Fixture: the real installed CLI surface (``gigai setup``/``gigai init``/
``gigai scout install``/``gigai scout resume add``), the same pattern
``test_scout_setup_cli.py`` uses -- never a hand-faked git tree.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout.find_jobs.contracts import FindJobsConfig, PinnedResume, SourceToggles
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend
from gigai.scout.profile_records import create_profile, list_profiles, selected_profile
from gigai.workpad import resolve_workpad


def _write_real_find_jobs_config(target: Path) -> None:
    """Overwrite the STARTER placeholder with real roles.

    ``ensure_default_profile`` deliberately refuses to migrate while
    ``find-jobs.json`` still carries ``scout install``'s starter placeholder
    roles verbatim (see ``profile_records.py``'s own guard) -- a real
    setup-interview save (or, here, a direct write standing in for one) is
    what a migration needs to have something real to migrate.
    """

    config = FindJobsConfig(
        roles=("staff backend engineer",),
        merged_queries=("staff backend engineer",),
        location="Remote",
        remote=True,
        published_after=None,
        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
        countries=("US",),
        visa_sponsorship_required=False,
    )
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(config.to_json()))


def _setup_and_init(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir(parents=True)

    runner = CliRunner()
    setup_result = runner.invoke(
        cli,
        [
            "setup",
            "--non-interactive",
            "--home",
            str(home),
            "--workpad-root",
            str(tmp_path / "workpads"),
            "--editor",
            "/usr/bin/true",
            "--credential-ref",
            "provider=environment:GIGAI_PROVIDER_TOKEN",
            "--endpoint",
            "remote=openai_api:provider:https://api.example.test",
            "--model-target",
            "remote=remote:smoke-test",
            "--create-model-target",
            "remote",
            "--json",
        ],
    )
    assert setup_result.exit_code == 0, setup_result.output

    init_result = runner.invoke(
        cli,
        ["init", "--home", str(home), "--target", str(target), "--username", "s25-cli-test", "--json"],
    )
    assert init_result.exit_code == 0, init_result.output
    return home, target


def _resolved(home: Path, target: Path):
    return resolve_workpad(home_root=home, requested_target=target, gig_id=None, allow_semantic_state=True)


def test_resume_add_without_profile_attaches_to_selected_and_prints_it(tmp_path: Path) -> None:
    """The exact acceptance test the S25 spike names (F1-b's packet-plan row)."""

    home, target = _setup_and_init(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]).exit_code == 0
    _write_real_find_jobs_config(target)

    first_resume = tmp_path / "first-resume.md"
    first_resume.write_text("First resume version: staff backend engineer.\n", encoding="utf-8")
    first = runner.invoke(
        cli,
        ["scout", "resume", "add", str(first_resume), "--home", str(home), "--target", str(target), "--json"],
    )
    assert first.exit_code == 0, first.output

    # The first `resume add` alone migrates a default profile into
    # existence (via `present_api`/`profile_records`'s own migrate-on-
    # first-read, triggered by this same command's F1-b2 attach step).
    resolved = _resolved(home, target)
    selected_before = selected_profile(resolved, home_root=home, target=target)
    assert selected_before is not None

    second_resume = tmp_path / "second-resume.md"
    second_resume.write_text("Second resume version: staff platform engineer.\n", encoding="utf-8")

    result = runner.invoke(
        cli,
        ["scout", "resume", "add", str(second_resume), "--home", str(home), "--target", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert f"Attached to profile {selected_before.label} ({selected_before.profile_id})" in result.output

    json_result = runner.invoke(
        cli,
        ["scout", "resume", "add", str(second_resume), "--home", str(home), "--target", str(target), "--json"],
    )
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["profile_id"] == selected_before.profile_id

    # The END outcome: the profile's OWN resume_ref moved to the new resume.
    after = selected_profile(_resolved(home, target), home_root=home, target=target)
    assert after is not None
    assert after.resume_ref.record_id != selected_before.resume_ref.record_id
    assert after.revision > selected_before.revision


def test_resume_add_via_cli_is_immediately_visible_through_resume_details(tmp_path: Path) -> None:
    """F1-b2-r1 (missing invalidation proof): the REAL user flow.

    ``gigai scout resume add <file>`` (the CLI, which attaches the new
    resume to the SELECTED profile per the operator's decision) must be
    immediately visible through ``ScoutFindJobsBackend.resume_details()``
    (the same resolution ``GET /api/config`` uses) -- never a stale
    ``server.py`` per-workpad-HEAD cache entry (``_selected_profile_cache``/
    ``_profile_resume_details_cache``) serving the OLD resume, since the
    CLI's own journal commit (attaching the resume) changes the workpad's
    git HEAD and must miss both caches on the very next read.
    """

    home, target = _setup_and_init(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]).exit_code == 0
    _write_real_find_jobs_config(target)

    first_resume = tmp_path / "first-resume.md"
    first_resume.write_text("First resume version: staff backend engineer.\n", encoding="utf-8")
    assert runner.invoke(
        cli,
        ["scout", "resume", "add", str(first_resume), "--home", str(home), "--target", str(target), "--json"],
    ).exit_code == 0

    backend = ScoutFindJobsBackend(home_root=home, target=target)
    before = backend.resume_details()
    assert before is not None
    before_config, _ = backend.read_config()

    # Warm both server.py caches (the selected-profile cache and the
    # profile-resume-details cache) BEFORE the CLI's own commit below, so a
    # stale cache entry surviving the commit would actually be caught here.
    assert backend.resume_details() is not None
    assert backend.resume_details().pinned.record_id == before.pinned.record_id

    second_resume = tmp_path / "second-resume.md"
    second_resume.write_text("Second resume version: staff platform engineer.\n", encoding="utf-8")
    result = runner.invoke(
        cli,
        ["scout", "resume", "add", str(second_resume), "--home", str(home), "--target", str(target), "--json"],
    )
    assert result.exit_code == 0, result.output

    after = backend.resume_details()
    assert after is not None
    assert after.pinned.record_id != before.pinned.record_id, (
        "resume_details() served a stale (cached) resume after `gigai scout resume add`"
    )
    after_config, _ = backend.read_config()
    assert after_config.roles == before_config.roles  # unaffected; sanity check same profile/gig


def test_resume_add_with_explicit_profile_attaches_to_that_profile_not_selected(tmp_path: Path) -> None:
    """``--profile`` overrides "the selected profile" -- it attaches to the NAMED one."""

    home, target = _setup_and_init(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["scout", "install", "--home", str(home), "--target", str(target), "--json"]).exit_code == 0
    _write_real_find_jobs_config(target)

    resume_source = tmp_path / "first-resume.md"
    resume_source.write_text("First resume version.\n", encoding="utf-8")
    assert runner.invoke(
        cli,
        ["scout", "resume", "add", str(resume_source), "--home", str(home), "--target", str(target), "--json"],
    ).exit_code == 0

    resolved = _resolved(home, target)
    default_profile = selected_profile(resolved, home_root=home, target=target)
    assert default_profile is not None

    other_ref = PinnedResume(
        record_id=default_profile.resume_ref.record_id,
        revision_id=default_profile.resume_ref.revision_id,
        content_sha256=default_profile.resume_ref.content_sha256,
    )
    other_profile = create_profile(
        resolved,
        label="second profile",
        titles=("staff platform engineer",),
        titles_to_avoid=(),
        queries=("staff platform engineer",),
        resume_ref=other_ref,
    )
    # Selected profile stays the default one -- creating doesn't select.
    assert selected_profile(_resolved(home, target), home_root=home, target=target).profile_id == default_profile.profile_id

    new_resume = tmp_path / "attach-to-other.md"
    new_resume.write_text("A resume meant for the SECOND profile.\n", encoding="utf-8")

    result = runner.invoke(
        cli,
        [
            "scout", "resume", "add", str(new_resume),
            "--home", str(home), "--target", str(target),
            "--profile", other_profile.profile_id, "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile_id"] == other_profile.profile_id

    # The SELECTED (default) profile's own resume_ref is untouched.
    default_after = selected_profile(_resolved(home, target), home_root=home, target=target)
    assert default_after.resume_ref.record_id == default_profile.resume_ref.record_id
    assert default_after.revision == default_profile.revision

    # The named profile's resume_ref moved.
    updated_other = next(
        item for item in list_profiles(_resolved(home, target)) if item.profile_id == other_profile.profile_id
    )
    assert updated_other.resume_ref.record_id != other_profile.resume_ref.record_id
    assert updated_other.revision == other_profile.revision + 1
