"""0110-022: each profile but the default one has its own search settings.

Record level (``profile_records``) and the overlay (``effective_config``):
the default profile and every record written before 0110-022 carry no
``search_settings`` key, so their bytes, ``content_digest`` and effective
config are what they were; another profile's own location, work mode,
countries and posted window replace the shared ones in ITS effective config
only. Synthetic fixtures only.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.scout.find_jobs.contracts import FindJobsConfig, PinnedResume, WorkModePreference
from gigai.scout.find_jobs.effective_config import default_search_settings, overlay_selected_profile
from gigai.scout.profile_records import (
    ProfileRecord,
    ProfileRecordError,
    ProfileSearchSettings,
    create_profile,
    default_profile,
    list_profiles,
    retrieve_profile_revision,
    selected_profile,
    write_profile,
)
from gigai.validators import validate_serialized_contract

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume, default_find_jobs_config

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. (fixture only.)\n"
_HOUSTON = ProfileSearchSettings(location="Houston, TX", work_mode="hybrid", countries=("US",), max_age_days=14)


def _old_digest(record: ProfileRecord) -> str:
    """``content_digest`` exactly as it was computed before 0110-022 (four fields)."""

    payload = {
        "titles": list(record.titles),
        "titles_to_avoid": list(record.titles_to_avoid),
        "queries": list(record.queries),
        "resume_ref": record.resume_ref.to_json(),
    }
    return digest_imported_bytes(canonical_json_bytes(payload))


def _gig(tmp_path: Path) -> tuple[ProfileFixtureGig, ProfileRecord, PinnedResume]:
    fx = build_gig_with_resume(tmp_path, resume_text=_RESUME)
    default = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert default is not None and default.origin == "migrated_default"
    return fx, default, default.resume_ref


def _second(fx: ProfileFixtureGig, resume_ref: PinnedResume, settings: ProfileSearchSettings | None, label: str = "Director") -> ProfileRecord:
    return create_profile(
        fx.resolved,
        label=label,
        titles=("director of ai",),
        titles_to_avoid=(),
        queries=("director of ai",),
        resume_ref=resume_ref,
        search_settings=settings,
    )


def _written(fx: ProfileFixtureGig, record: ProfileRecord) -> dict[str, object]:
    path = fx.resolved.path / "records" / "scout-profiles" / record.profile_id / "writes" / f"{record.seq:012d}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --- records without the new field are what they were ------------------------


def test_default_profile_has_no_settings_and_its_file_and_digest_are_unchanged(tmp_path: Path) -> None:
    fx, default, _ref = _gig(tmp_path)
    assert default.search_settings is None
    assert "search_settings" not in _written(fx, default)
    assert "search_settings" not in default.to_json()
    assert default.content_digest == _old_digest(default)


def test_a_record_written_before_the_field_reads_as_same_as_default() -> None:
    old = {
        "schema_version": "scout-profile:1",
        "profile_id": "profile_00000000-0000-4000-8000-000000000001",
        "seq": 1,
        "revision": 1,
        "label": "old",
        "state": "active",
        "origin": "operator_created",
        "resume_ref": {
            "record_id": "record_00000000-0000-4000-8000-000000000002",
            "revision_id": "revision_00000000-0000-4000-8000-000000000003",
            "content_sha256": "sha256:" + "0" * 64,
        },
        "titles": ["staff engineer"],
        "titles_to_avoid": [],
        "queries": ["staff engineer"],
        "content_digest": "sha256:" + "1" * 64,
        "created_at": "2026-09-25T00:00:00Z",
        "updated_at": "2026-09-25T00:00:00Z",
        "parent_seq": None,
    }
    raw = canonical_json_bytes(old)
    assert validate_serialized_contract("scout-profile.schema.json", raw).valid
    record = ProfileRecord.from_json(old)
    assert record.search_settings is None
    assert canonical_json_bytes(record.to_json()) == raw  # written back byte for byte

    shared = replace(default_find_jobs_config(), location="Denver, CO", max_age_days=21)
    effective = overlay_selected_profile(shared, record)
    assert effective == replace(shared, roles=("staff engineer",), merged_queries=("staff engineer",))


def test_the_first_profile_of_a_gig_stores_no_settings(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path, resume_text=_RESUME, write_config=False)
    ref = PinnedResume(fx.resume_record_id, fx.resume_revision_id, digest_imported_bytes(_RESUME))
    first = _second(fx, ref, _HOUSTON, label="first")
    assert first.search_settings is None  # it is the default profile
    assert first.content_digest == _old_digest(first)
    assert default_profile(list_profiles(fx.resolved)).profile_id == first.profile_id  # type: ignore[union-attr]


# --- another profile's own settings -----------------------------------------


def test_a_second_profile_stores_its_own_settings_in_file_schema_and_digest(tmp_path: Path) -> None:
    fx, default, ref = _gig(tmp_path)
    second = _second(fx, ref, _HOUSTON)

    assert second.search_settings == _HOUSTON
    written = _written(fx, second)
    assert written["search_settings"] == {"location": "Houston, TX", "work_mode": "hybrid", "countries": ["US"], "max_age_days": 14}
    assert validate_serialized_contract("scout-profile.schema.json", canonical_json_bytes(written)).valid
    assert second.content_digest != _old_digest(second)  # the settings are sealed content
    assert default_profile(list_profiles(fx.resolved)).profile_id == default.profile_id  # type: ignore[union-attr]


def test_changing_one_profiles_settings_changes_no_other_profile(tmp_path: Path) -> None:
    fx, default, ref = _gig(tmp_path)
    second = _second(fx, ref, _HOUSTON)
    third = _second(fx, ref, replace(_HOUSTON, location="Austin, TX"), label="third")

    changed = write_profile(fx.resolved, profile_id=second.profile_id, search_settings=replace(_HOUSTON, location="Seattle, WA"))

    assert changed.search_settings.location == "Seattle, WA"  # type: ignore[union-attr]
    assert changed.revision == second.revision + 1 and changed.content_digest != second.content_digest
    current = {item.profile_id: item for item in list_profiles(fx.resolved)}
    assert current[default.profile_id] == default  # same seq, revision, digest: not rewritten
    assert current[third.profile_id] == third
    # the run-sealed identity of the older revision still reproduces its settings
    before = retrieve_profile_revision(
        fx.resolved, profile_id=second.profile_id, revision=second.revision, content_digest=second.content_digest
    )
    assert before.search_settings == _HOUSTON


def test_same_settings_again_is_no_revision_bump_and_clearing_goes_back_to_default(tmp_path: Path) -> None:
    fx, _default, ref = _gig(tmp_path)
    second = _second(fx, ref, _HOUSTON)

    same = write_profile(fx.resolved, profile_id=second.profile_id, search_settings=_HOUSTON)
    assert same.revision == second.revision and same.content_digest == second.content_digest

    kept = write_profile(fx.resolved, profile_id=second.profile_id, label="Director (renamed)")
    assert kept.search_settings == _HOUSTON  # an edit of something else keeps them

    cleared = write_profile(fx.resolved, profile_id=second.profile_id, clear_search_settings=True)
    assert cleared.search_settings is None and cleared.revision == second.revision + 1
    assert cleared.content_digest == _old_digest(cleared)
    assert "search_settings" not in _written(fx, cleared)


def test_the_default_profile_refuses_settings_of_its_own(tmp_path: Path) -> None:
    fx, default, ref = _gig(tmp_path)
    _second(fx, ref, _HOUSTON)
    with pytest.raises(ProfileRecordError) as excinfo:
        write_profile(fx.resolved, profile_id=default.profile_id, search_settings=_HOUSTON)
    assert excinfo.value.code == "scout_profile_default_search_settings"
    assert list_profiles(fx.resolved)[0] == default


@pytest.mark.parametrize(
    "bad",
    [
        {"location": "", "work_mode": "any", "countries": [], "max_age_days": None},
        {"location": "REPLACE_WITH_YOUR_LOCATION", "work_mode": "any", "countries": [], "max_age_days": None},
        {"location": None, "work_mode": "sometimes", "countries": [], "max_age_days": None},
        {"location": None, "work_mode": "any", "countries": ["usa"], "max_age_days": None},
        {"location": None, "work_mode": "any", "countries": [], "max_age_days": 0},
        {"location": None, "work_mode": "any", "countries": [], "max_age_days": 366},
        {"location": None, "work_mode": "any", "countries": [], "max_age_days": True},
        {"location": None, "work_mode": "any", "countries": []},
        {"location": None, "work_mode": "any", "countries": [], "max_age_days": None, "bogus": 1},
    ],
)
def test_malformed_settings_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ProfileRecordError) as excinfo:
        ProfileSearchSettings.from_json(bad)
    assert excinfo.value.code == "scout_profile_invalid"


# --- the effective config ---------------------------------------------------


def test_overlay_gives_each_profile_its_own_settings_and_the_default_the_shared_ones(tmp_path: Path) -> None:
    fx, default, ref = _gig(tmp_path)
    second = _second(fx, ref, _HOUSTON)
    shared = replace(default_find_jobs_config(), location="Denver, CO", work_mode=WorkModePreference.REMOTE, max_age_days=45)

    for_default = overlay_selected_profile(shared, default)
    assert for_default == replace(shared, roles=default.titles, merged_queries=default.queries)

    for_second = overlay_selected_profile(shared, second)
    assert for_second.location == "Houston, TX"
    assert for_second.work_mode is WorkModePreference.HYBRID and for_second.remote is False
    assert for_second.countries == ("US",) and for_second.max_age_days == 14
    # everything else stays the shared file's
    assert for_second.sources == shared.sources and for_second.default_assess_cap == shared.default_assess_cap
    assert for_second.visa_sponsorship_required == shared.visa_sponsorship_required
    assert for_second.digest() != for_default.digest()
    assert FindJobsConfig.from_json(for_second.to_json()) == for_second


def test_overlay_window_a_profiles_days_clear_the_fixed_date_and_none_keeps_the_shared_window() -> None:
    profile = ProfileRecord(
        schema_version="scout-profile:1",
        profile_id="prof_1",
        seq=1,
        revision=1,
        label="own",
        state="active",
        origin="test",
        resume_ref=PinnedResume(record_id="rec_1", revision_id="rev_1", content_sha256="sha256:" + "0" * 64),
        titles=("staff engineer",),
        titles_to_avoid=(),
        queries=("staff engineer",),
        content_digest="sha256:" + "1" * 64,
        created_at="2026-09-25T00:00:00Z",
        updated_at="2026-09-25T00:00:00Z",
        parent_seq=None,
        search_settings=_HOUSTON,
    )
    fixed = replace(default_find_jobs_config(), published_after="2026-09-10", max_age_days=None)

    own_window = overlay_selected_profile(fixed, profile)
    assert own_window.max_age_days == 14 and own_window.published_after is None

    inherit = replace(profile, search_settings=replace(_HOUSTON, max_age_days=None))
    kept = overlay_selected_profile(fixed, inherit)
    assert kept.published_after == "2026-09-10" and kept.max_age_days is None
    assert kept.location == "Houston, TX"


def test_default_search_settings_reads_the_shared_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    home.mkdir()
    target.mkdir()
    assert default_search_settings(home_root=home, target=target) is None  # no config yet
    config = replace(default_find_jobs_config(), location="Denver, CO", work_mode=WorkModePreference.ONSITE, max_age_days=30)
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(config.to_json()))
    assert default_search_settings(home_root=home, target=target) == ProfileSearchSettings(
        location="Denver, CO", work_mode="onsite", countries=("US",), max_age_days=30
    )
