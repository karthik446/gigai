"""uat-bug-024: ``PUT /api/setup`` writes the titles to the profile it is told to.

The wizard's "Create a new profile" saved the new profile, then sent its
titles as ``roles`` on ``PUT /api/setup``, which wrote them to the SELECTED
profile: the selected profile's titles were overwritten by the new one's.

``PUT /api/setup`` now takes an optional ``profile_id``. Against a real gig
(``build_gig_with_resume``), the real ``ScoutFindJobsBackend`` and
``serve()`` (in-process server thread):

* with ``profile_id`` the titles go to that profile and the selected one is
  left as it is (titles, queries, revision, digest, ``updated_at``);
* without it (or with ``null``) they go to the selected profile, as before;
* a ``profile_id`` that is not a profile of this gig, or an archived one, is
  refused before anything is saved.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import httpx
import pytest

from gigai.scout.find_jobs.api.server import SetupValidationError
from gigai.scout.find_jobs.api.setup import _validate_setup_body
from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.profile_records import (
    ProfileRecord,
    ProfileRecordError,
    create_profile,
    list_profiles,
    selected_profile,
    write_profile,
)

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

TITLES_B = ["Engineering Manager", "Director of Engineering"]
AVOID_B = ["Intern"]
UNKNOWN_PROFILE = "profile_00000000-0000-4000-8000-000000000000"


def _body(roles: list[str], **extra: object) -> dict[str, object]:
    """``wizardState.setupBody`` with the wizard's defaults."""

    return {
        "roles": roles,
        "titles_to_avoid": [],
        "countries": ["US"],
        "work_mode": "remote",
        "city": None,
        "visa_sponsorship_required": False,
        "exclude_companies": [],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        "budget_usd_per_session": 0.5,
        "max_age_days": 60,
        **extra,
    }


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path)


@pytest.fixture
def client(fx: ProfileFixtureGig):
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        with httpx.Client(base_url=f"http://{host}:{port}", timeout=20.0) as http:
            yield http
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _profile(fx: ProfileFixtureGig, profile_id: str) -> ProfileRecord:
    return next(item for item in list_profiles(fx.resolved) if item.profile_id == profile_id)


def _selected_and_new(fx: ProfileFixtureGig) -> tuple[ProfileRecord, ProfileRecord]:
    """A, the selected profile, and B, a new one the wizard just created
    (``POST /api/profiles``: its own titles, not selected)."""

    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None and selected.titles
    created = create_profile(
        fx.resolved,
        label="Management",
        titles=tuple(TITLES_B),
        titles_to_avoid=tuple(AVOID_B),
        queries=tuple(TITLES_B),
        resume_ref=selected.resume_ref,
    )
    assert set(created.titles).isdisjoint(selected.titles)
    return selected, created


# --- the body ------------------------------------------------------------------------------


def test_profile_id_is_an_optional_field_of_the_body() -> None:
    assert "profile_id" not in _validate_setup_body(_body(["Staff Engineer"]))
    assert "profile_id" not in _validate_setup_body(_body(["Staff Engineer"], profile_id=None))
    assert _validate_setup_body(_body(["Staff Engineer"], profile_id=UNKNOWN_PROFILE))["profile_id"] == UNKNOWN_PROFILE


@pytest.mark.parametrize("bad", ["", 7, True, ["profile_x"], {"id": "profile_x"}])
def test_a_profile_id_that_is_not_a_string_is_a_field_error(bad: object) -> None:
    with pytest.raises(SetupValidationError) as refused:
        _validate_setup_body(_body(["Staff Engineer"], profile_id=bad))
    assert set(refused.value.field_errors) == {"profile_id"}


# --- the route -------------------------------------------------------------------------------


def test_a_new_profiles_titles_do_not_reach_the_selected_profile(
    fx: ProfileFixtureGig, client: httpx.Client, caplog: pytest.LogCaptureFixture
) -> None:
    selected, created = _selected_and_new(fx)

    # The wizard's last request for "Create a new profile": the new
    # profile's titles, and the new profile's id.
    with caplog.at_level(logging.INFO, logger="gigai.scout.server"):
        response = client.put(
            "/api/setup", json=_body(TITLES_B, titles_to_avoid=AVOID_B, profile_id=created.profile_id)
        )
    assert response.status_code == 200, response.text

    # The symptom: the selected profile is as it was.
    after = _profile(fx, selected.profile_id)
    assert after.titles == selected.titles
    assert after.titles_to_avoid == selected.titles_to_avoid
    assert after.queries == selected.queries
    assert after.revision == selected.revision
    assert after.content_digest == selected.content_digest
    assert after.seq == selected.seq and after.updated_at == selected.updated_at
    still_selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert still_selected is not None and still_selected.profile_id == selected.profile_id

    # The new profile has the new titles (the values POST /api/profiles
    # gave it, so this save changed nothing in them).
    new = _profile(fx, created.profile_id)
    assert list(new.titles) == TITLES_B and list(new.titles_to_avoid) == AVOID_B and list(new.queries) == TITLES_B
    # 0110-022: this used to assert "no new revision". The save's city /
    # work mode / countries / window now go to the NEW profile as its own
    # search settings (one revision), not into the shared find-jobs.json.
    assert new.revision == created.revision + 1
    assert new.search_settings is not None
    assert new.search_settings.to_json() == {"location": None, "work_mode": "remote", "countries": ["US"], "max_age_days": 60}

    # The shared preferences: every active profile's titles.
    prefs = response.json()["prefs"]
    assert prefs["roles"] == [*selected.titles, *TITLES_B]
    assert prefs["titles_to_avoid"] == [*selected.titles_to_avoid, *AVOID_B]
    # Field names only in the log, and which profile by kind, never by id.
    assert "setup saved: fields=" in caplog.text and "profile=named" in caplog.text
    assert created.profile_id not in caplog.text and "Engineering Manager" not in caplog.text


def test_named_titles_are_written_to_the_named_profile(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    selected, created = _selected_and_new(fx)
    edited = ["VP Engineering"]

    response = client.put("/api/setup", json=_body(edited, profile_id=created.profile_id))
    assert response.status_code == 200, response.text

    new = _profile(fx, created.profile_id)
    assert list(new.titles) == edited and list(new.queries) == edited and new.titles_to_avoid == ()
    assert new.revision == created.revision + 1
    after = _profile(fx, selected.profile_id)
    assert (after.titles, after.revision, after.seq) == (selected.titles, selected.revision, selected.seq)


@pytest.mark.parametrize("extra", [{}, {"profile_id": None}])
def test_without_a_profile_id_the_titles_go_to_the_selected_profile(
    fx: ProfileFixtureGig, client: httpx.Client, extra: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    """What every caller that sends no ``profile_id`` gets: today's behaviour."""

    selected, created = _selected_and_new(fx)
    edited = ["Principal Engineer"]

    with caplog.at_level(logging.INFO, logger="gigai.scout.server"):
        response = client.put("/api/setup", json=_body(edited, **extra))
    assert response.status_code == 200, response.text

    after = _profile(fx, selected.profile_id)
    assert list(after.titles) == edited and list(after.queries) == edited
    assert after.revision == selected.revision + 1
    new = _profile(fx, created.profile_id)
    assert (new.titles, new.revision, new.seq) == (created.titles, created.revision, created.seq)
    assert "profile=selected" in caplog.text


def test_naming_the_selected_profile_is_the_same_as_naming_none(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    selected, created = _selected_and_new(fx)

    response = client.put("/api/setup", json=_body(["Principal Engineer"], profile_id=selected.profile_id))
    assert response.status_code == 200, response.text

    after = _profile(fx, selected.profile_id)
    assert list(after.titles) == ["Principal Engineer"] and after.revision == selected.revision + 1
    assert _profile(fx, created.profile_id).seq == created.seq


def test_a_refused_profile_id_saves_nothing(fx: ProfileFixtureGig, client: httpx.Client) -> None:
    selected, created = _selected_and_new(fx)
    saved = client.put("/api/setup", json=_body(list(selected.titles)))
    assert saved.status_code == 200, saved.text
    selected = _profile(fx, selected.profile_id)
    prefs_before = client.get("/api/setup").json()
    config_before = (fx.target / "find-jobs.json").read_bytes()

    unknown = client.put("/api/setup", json=_body(TITLES_B, city="Denver, CO", work_mode="hybrid", profile_id=UNKNOWN_PROFILE))
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "profile_not_found"

    write_profile(fx.resolved, profile_id=created.profile_id, state="archived")
    archived = client.put(
        "/api/setup", json=_body(["VP Engineering"], city="Denver, CO", work_mode="hybrid", profile_id=created.profile_id)
    )
    assert archived.status_code == 409, archived.text
    assert archived.json()["error"]["code"] == "scout_profile_archived"

    for bad in ("", 7):
        invalid = client.put("/api/setup", json=_body(TITLES_B, profile_id=bad))
        assert invalid.status_code == 400, invalid.text
        assert set(invalid.json()["error"]["field_errors"]) == {"profile_id"}

    assert client.get("/api/setup").json() == prefs_before
    assert (fx.target / "find-jobs.json").read_bytes() == config_before
    after = _profile(fx, selected.profile_id)
    assert (after.titles, after.revision, after.seq) == (selected.titles, selected.revision, selected.seq)
    assert list(_profile(fx, created.profile_id).titles) == TITLES_B


# --- the backend -----------------------------------------------------------------------------


def test_the_backend_refuses_a_profile_that_is_not_committed(fx: ProfileFixtureGig) -> None:
    """The route answers first; this is what stops a caller that skips it."""

    selected, _created = _selected_and_new(fx)
    backend = ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target)
    fields = _validate_setup_body(_body(TITLES_B))

    with pytest.raises(ProfileRecordError) as refused:
        backend.write_setup(fields, profile_id=UNKNOWN_PROFILE)
    assert refused.value.code == "scout_profile_unavailable"
    after = _profile(fx, selected.profile_id)
    assert (after.titles, after.revision, after.seq) == (selected.titles, selected.revision, selected.seq)
