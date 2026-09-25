"""Q1 (v0.1.9, SCOPE-ADD-2): the rolling publication window.

UAT (edec732): a live Kong posting from 2026-08-11 never appeared because
``published_after`` was a fixed ``2026-09-10`` -- and, the coordinator's
finding, only ``exa_client.py`` ever read that field: the ATS path applied
NO date filter at all. Now ``FindJobsConfig.max_age_days`` (default 60) is
turned into one cutoff by ``filters.published_cutoff`` and applied to every
source's ``PostingRow.published_at`` in ``filters.exclusion_reason`` (the
rule acquire's drop loop and assess's not-assessed labeling both call), and
to Exa's ``startPublishedDate``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from gigai.scout.find_jobs.contracts import (
    DEFAULT_MAX_AGE_DAYS,
    ATSProvider,
    FindJobsConfig,
    FindJobsContractError,
    NotAssessedReason,
    PinnedResume,
    PostingRow,
    SourceKind,
    SourceToggles,
)
from gigai.scout.find_jobs.effective_config import overlay_selected_profile
from gigai.scout.find_jobs.exa_client import EXA_API_KEY_ENV_VAR, ExaSearchClient
from gigai.scout.find_jobs.filters import exclusion_reason, published_cutoff, published_too_old
from gigai.scout.profile_records import ProfileRecord

from .conftest import load_fixture

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _config(**overrides: object) -> FindJobsConfig:
    values: dict[str, object] = {
        "roles": ("software engineer",),
        "merged_queries": ("software engineer",),
        "location": "Denver, CO",
        "remote": True,
        "published_after": None,
        "sources": SourceToggles(exa=True, ats=True, hiringcafe=False),
    }
    values.update(overrides)
    return FindJobsConfig(**values)


def _row(**overrides: object) -> PostingRow:
    values: dict[str, object] = {
        "url": "https://jobs.ashbyhq.com/kong/ea7b507b-0000",
        "normalized_url": "https://jobs.ashbyhq.com/kong/ea7b507b-0000",
        "provider": ATSProvider.ASHBY,
        "board_token": "kong",
        "company": "kong",
        "title": "Software Engineer",
        "location": "Denver, CO",
        "published_at": None,
        "content_sha256": None,
        "source_kind": SourceKind.ATS,
        "query_key": "ats",
    }
    values.update(overrides)
    return PostingRow(**values)


def _days_ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


# --- the config field ------------------------------------------------------


def test_config_without_max_age_days_parses_to_none_and_digests_as_before() -> None:
    raw = load_fixture("fixture-find-jobs-config-v1.json")
    config = FindJobsConfig.from_json(raw)
    assert config.max_age_days is None
    assert "max_age_days" not in config.to_json()
    # The same pinned digest test_contracts.py's C0 test guards: an
    # operator file that never set the key must digest exactly as before Q1.
    assert config.to_json() == raw
    assert config.digest() == "sha256:e14f80205b4dda9cdcd2f594c2eb341c224c4ee2a10d5765e0848df1db6e4dee"


def test_config_max_age_days_round_trips_and_changes_the_digest() -> None:
    raw = load_fixture("fixture-find-jobs-config-v1.json")
    raw = {**raw, "max_age_days": 30}
    config = FindJobsConfig.from_json(raw)
    assert config.max_age_days == 30
    assert config.to_json()["max_age_days"] == 30
    assert FindJobsConfig.from_json(config.to_json()) == config
    assert config.digest() != FindJobsConfig.from_json(load_fixture("fixture-find-jobs-config-v1.json")).digest()


@pytest.mark.parametrize("bad", [0, 366, -1, "30", True, 1.5])
def test_config_max_age_days_rejects_out_of_range_or_wrong_type(bad: object) -> None:
    raw = {**load_fixture("fixture-find-jobs-config-v1.json"), "max_age_days": bad}
    with pytest.raises(FindJobsContractError):
        FindJobsConfig.from_json(raw)


def test_config_max_age_days_null_is_the_same_as_absent() -> None:
    raw = {**load_fixture("fixture-find-jobs-config-v1.json"), "max_age_days": None}
    config = FindJobsConfig.from_json(raw)
    assert config.max_age_days is None
    assert "max_age_days" not in config.to_json()


# --- the one cutoff helper --------------------------------------------------


def test_cutoff_defaults_to_sixty_days_when_neither_is_set() -> None:
    assert DEFAULT_MAX_AGE_DAYS == 60
    assert published_cutoff(_config(), now=NOW) == NOW - timedelta(days=60)


def test_cutoff_uses_max_age_days_when_set() -> None:
    assert published_cutoff(_config(max_age_days=14), now=NOW) == NOW - timedelta(days=14)


def test_cutoff_fixed_date_wins_when_both_are_set() -> None:
    config = _config(published_after="2026-09-10", max_age_days=14)
    assert published_cutoff(config, now=NOW) == datetime(2026, 9, 10, tzinfo=timezone.utc)


def test_cutoff_fixed_date_time_with_zulu_suffix_parses() -> None:
    config = _config(published_after="2026-09-15T00:00:00Z")
    assert published_cutoff(config, now=NOW) == datetime(2026, 9, 15, tzinfo=timezone.utc)


def test_cutoff_unparsable_fixed_date_is_a_loud_config_error() -> None:
    with pytest.raises(FindJobsContractError) as excinfo:
        published_cutoff(_config(published_after="last tuesday"), now=NOW)
    assert excinfo.value.code == "invalid_value"


def test_cutoff_uses_real_now_when_not_injected() -> None:
    before = datetime.now(timezone.utc)
    cutoff = published_cutoff(_config(max_age_days=7))
    after = datetime.now(timezone.utc)
    assert before - timedelta(days=7) <= cutoff <= after - timedelta(days=7)


# --- applied to every source's rows ----------------------------------------


@pytest.mark.parametrize("source_kind", [SourceKind.ATS, SourceKind.EXA])
def test_row_older_than_the_default_window_is_dropped_from_any_source(source_kind: SourceKind) -> None:
    row = _row(published_at=_days_ago(61), source_kind=source_kind)
    assert published_too_old(row, _config(), now=NOW) is True
    assert exclusion_reason(row, _config(), now=NOW) is NotAssessedReason.PUBLISHED_TOO_OLD


@pytest.mark.parametrize("source_kind", [SourceKind.ATS, SourceKind.EXA])
def test_row_inside_the_default_window_is_kept_from_any_source(source_kind: SourceKind) -> None:
    row = _row(published_at=_days_ago(59), source_kind=source_kind)
    assert published_too_old(row, _config(), now=NOW) is False
    assert exclusion_reason(row, _config(), now=NOW) is None


def test_kong_case_forty_five_days_old_kept_by_rolling_window_dropped_by_old_fixed_date() -> None:
    """The UAT posting: published 2026-08-11, run on 2026-09-25 (45 days).
    The fixed ``2026-09-10`` dropped it; the default 60-day window keeps it."""

    kong = _row(published_at="2026-08-11T00:00:00Z")
    assert exclusion_reason(kong, _config(), now=NOW) is None
    assert exclusion_reason(kong, _config(published_after="2026-09-10"), now=NOW) is NotAssessedReason.PUBLISHED_TOO_OLD


def test_row_with_no_published_at_is_kept() -> None:
    row = _row(published_at=None)
    assert published_too_old(row, _config(max_age_days=1), now=NOW) is False
    assert exclusion_reason(row, _config(max_age_days=1), now=NOW) is None


def test_row_with_unparsable_published_at_is_kept() -> None:
    row = _row(published_at="yesterday-ish")
    assert published_too_old(row, _config(max_age_days=1), now=NOW) is False
    assert exclusion_reason(row, _config(max_age_days=1), now=NOW) is None


def test_lever_epoch_rendered_and_greenhouse_offset_dates_both_apply() -> None:
    # ats_board_clients renders Lever's epoch-ms as a `Z` date-time and
    # passes Greenhouse's `+00:00` ISO string through verbatim.
    assert exclusion_reason(_row(published_at="2026-07-01T00:00:00Z"), _config(), now=NOW) is NotAssessedReason.PUBLISHED_TOO_OLD
    assert exclusion_reason(_row(published_at="2026-07-01T00:00:00+00:00"), _config(), now=NOW) is NotAssessedReason.PUBLISHED_TOO_OLD
    assert exclusion_reason(_row(published_at="2026-09-01T00:00:00-07:00"), _config(), now=NOW) is None


def test_window_is_checked_before_the_location_and_visa_rules() -> None:
    row = _row(published_at=_days_ago(90), location="Bengaluru")
    assert exclusion_reason(row, _config(countries=("US",)), now=NOW) is NotAssessedReason.PUBLISHED_TOO_OLD
    fresh = _row(published_at=_days_ago(1), location="Bengaluru")
    assert exclusion_reason(fresh, _config(countries=("US",)), now=NOW) is NotAssessedReason.LOCATION_MISMATCH


def test_both_set_fixed_date_decides_the_drop() -> None:
    config = _config(published_after="2026-09-10", max_age_days=365)
    assert exclusion_reason(_row(published_at="2026-09-09T00:00:00Z"), config, now=NOW) is NotAssessedReason.PUBLISHED_TOO_OLD
    assert exclusion_reason(_row(published_at="2026-09-11T00:00:00Z"), config, now=NOW) is None


# --- shared field: the profile overlay passes it through --------------------


def test_effective_config_overlay_keeps_the_window_fields() -> None:
    config = _config(max_age_days=21, published_after=None)
    profile = ProfileRecord(
        schema_version="scout-profile:1",
        profile_id="prof_1",
        seq=1,
        revision=1,
        label="staff",
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
    )
    effective = overlay_selected_profile(config, profile)
    assert effective.roles == ("staff engineer",)
    assert effective.max_age_days == 21
    assert effective.published_after is None
    fixed = overlay_selected_profile(_config(published_after="2026-09-10"), profile)
    assert fixed.published_after == "2026-09-10"


# --- Exa asks for the same cutoff ------------------------------------------


def _exa_body(monkeypatch: pytest.MonkeyPatch, config: FindJobsConfig) -> dict[str, object]:
    monkeypatch.setenv(EXA_API_KEY_ENV_VAR, "secret-exa-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    ExaSearchClient().search(httpx.Client(transport=httpx.MockTransport(handler)), config)
    return json.loads(captured[0].content)


def test_exa_sends_the_fixed_date_verbatim_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _exa_body(monkeypatch, _config(published_after="2026-09-15T00:00:00Z", max_age_days=7))
    assert body["startPublishedDate"] == "2026-09-15T00:00:00Z"


def test_exa_sends_the_rolling_cutoff_when_only_max_age_days_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    before = datetime.now(timezone.utc)
    body = _exa_body(monkeypatch, _config(max_age_days=30))
    after = datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(body["startPublishedDate"].replace("Z", "+00:00"))
    assert before - timedelta(days=30) <= parsed <= after - timedelta(days=30)


def test_exa_bare_fixed_date_is_sent_as_a_full_date_time(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _exa_body(monkeypatch, _config(published_after="2026-09-10"))
    assert body["startPublishedDate"] == "2026-09-10T00:00:00Z"


# --- the wizard's save: PUT /api/setup carries max_age_days ----------------
#
# Coordinator (Q1 dispatch, ownership answer): a Preferences save WITHOUT
# max_age_days must keep the existing value (the silent-drop case --
# `_update_find_jobs_config` rebuilds FindJobsConfig field by field), and a
# save WITH it must clear `published_after` so the rolling form wins.


def _setup_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "roles": ("staff backend",),
        "titles_to_avoid": (),
        "countries": ("US",),
        "work_mode": "remote",
        "city": None,
        "visa_sponsorship_required": False,
        "exclude_companies": (),
        "watch_companies": (),
        "company_stage_size": None,
        "industries_include": (),
        "industries_exclude": (),
        "must_have_stack": (),
        "dealbreaker_stack": (),
        "cadence_days": 7,
        "budget_usd_per_session": 0.50,
    }
    fields.update(overrides)
    return fields


def _backend_with_config(tmp_path, monkeypatch: pytest.MonkeyPatch, config: FindJobsConfig):
    from gigai.canonical import canonical_json_bytes
    from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend

    from .test_present_setup_discover import _FakeDiscoveryPrefs, _install_fake_discovery_module

    home = tmp_path / "home"
    target = tmp_path / "target"
    home.mkdir()
    target.mkdir()
    (target / "find-jobs.json").write_bytes(canonical_json_bytes(config.to_json()))
    prefs_store: dict[str, _FakeDiscoveryPrefs] = {}
    _install_fake_discovery_module(monkeypatch, prefs_store=prefs_store, results_store={})
    return ScoutFindJobsBackend(home_root=home, target=target), target / "find-jobs.json", prefs_store


def test_setup_save_without_max_age_days_keeps_the_existing_window(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, path, prefs_store = _backend_with_config(tmp_path, monkeypatch, _config(max_age_days=21))

    backend.write_setup(_setup_fields())

    saved = FindJobsConfig.from_json(json.loads(path.read_text(encoding="utf-8")))
    assert saved.max_age_days == 21
    assert saved.published_after is None
    assert not hasattr(prefs_store[str(backend.target)], "max_age_days")


def test_setup_save_with_max_age_days_writes_it_and_clears_the_fixed_date(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, path, prefs_store = _backend_with_config(tmp_path, monkeypatch, _config(published_after="2026-09-10", max_age_days=None))

    backend.write_setup(_setup_fields(max_age_days=45))

    saved = FindJobsConfig.from_json(json.loads(path.read_text(encoding="utf-8")))
    assert saved.max_age_days == 45
    assert saved.published_after is None
    # never a DiscoveryPrefs field (the fake dataclass would have refused the kwarg)
    assert "max_age_days" not in prefs_store[str(backend.target)].to_json()


def test_setup_save_without_max_age_days_keeps_an_existing_fixed_date(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, path, _prefs_store = _backend_with_config(tmp_path, monkeypatch, _config(published_after="2026-09-10"))

    backend.write_setup(_setup_fields())

    saved = FindJobsConfig.from_json(json.loads(path.read_text(encoding="utf-8")))
    assert saved.published_after == "2026-09-10"
    assert saved.max_age_days is None


def test_setup_save_on_a_missing_config_writes_the_window(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend, path, _prefs_store = _backend_with_config(tmp_path, monkeypatch, _config())
    path.unlink()

    backend.write_setup(_setup_fields(max_age_days=30))

    saved = FindJobsConfig.from_json(json.loads(path.read_text(encoding="utf-8")))
    assert saved.max_age_days == 30
    assert saved.published_after is None


def test_validate_setup_body_accepts_and_rejects_max_age_days() -> None:
    from gigai.scout.find_jobs.api.setup import _validate_setup_body
    from gigai.scout.find_jobs.present_api import SetupValidationError

    body = {**{key: (list(value) if isinstance(value, tuple) else value) for key, value in _setup_fields().items()}}
    assert "max_age_days" not in _validate_setup_body(body)
    assert _validate_setup_body({**body, "max_age_days": None}).get("max_age_days") is None
    assert _validate_setup_body({**body, "max_age_days": 45})["max_age_days"] == 45
    for bad in (0, 366, "45", True, 4.5):
        with pytest.raises(SetupValidationError) as excinfo:
            _validate_setup_body({**body, "max_age_days": bad})
        assert "max_age_days" in excinfo.value.field_errors
