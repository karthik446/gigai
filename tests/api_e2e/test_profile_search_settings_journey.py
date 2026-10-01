"""0110-022: each profile but the default one runs with its own search settings.

Over HTTP against the real supervised server, synthetic fixtures, every
source off (acquire is an empty pass; what is checked is what each run
SEALS). The default profile keeps the setup settings: its effective config,
its digest and the config a run for it seals are what they were before a
second profile existed. A second profile is prefilled with the default's
settings, then given its own location / work mode / countries / posted
window: a run for it seals those, and changing them changes no other
profile. A profile whose record has no ``search_settings`` reads the
default's.

The wizard's save (``PUT /api/setup`` with ``profile_id``) for a profile that
is not the default one puts the city, work mode, countries and window on
that profile; the shared ``find-jobs.json`` and the saved preferences keep
the default profile's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.api_e2e.after_journey import assert_clean_and_healthy
from tests.api_e2e.harness import (
    add_resume,
    poll_until_terminal,
    resolve_workpad_path,
    run_request_body,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

_SETTINGS_KEYS = ["countries", "location", "max_age_days", "work_mode"]


def _config(client) -> dict:
    response = client.get("/api/config")
    assert response.status_code == 200, response.text
    return response.json()


def _run_and_read_sealed(client, workpad: Path) -> tuple[dict, dict]:
    """Run for the selected profile; its sealed find-jobs-config and run input."""

    config = _config(client)
    started = client.post("/api/run", json=run_request_body(config["config_digest"]))
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    status = poll_until_terminal(client, run_id)
    assert status["status"] == "succeeded", status
    sealed = workpad / "runs" / run_id / "sealed"
    sealed_config = json.loads((sealed / "find-jobs-config.json").read_text(encoding="utf-8"))
    run_input = json.loads((sealed / "find-jobs-run-input.json").read_text(encoding="utf-8"))
    assert sealed_config == config["config"]  # the run sealed what the form showed
    return sealed_config, run_input


def _select(client, profile_id: str) -> None:
    response = client.post("/api/profiles/selection", json={"profile_id": profile_id})
    assert response.status_code == 200, response.text


def _profile_file(workpad: Path, profile: dict, seq: int) -> dict:
    path = workpad / "records" / "scout-profiles" / str(profile["profile_id"]) / "writes" / f"{seq:012d}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_each_profile_runs_with_its_own_search_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)  # location "Denver, CO", every source off
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)

        # -- the default profile: the setup settings, as before -----------------
        listed = client.get("/api/profiles").json()
        (default,) = listed["profiles"]
        assert default["is_default"] is True and default["search_settings"] is None
        assert listed["default_profile_id"] == default["profile_id"]
        shared = listed["default_search_settings"]
        assert sorted(shared) == _SETTINGS_KEYS and shared["location"] == "Denver, CO"
        assert "search_settings" not in _profile_file(workpad, default, 1)

        default_before = _config(client)
        assert default_before["config"]["location"] == "Denver, CO"

        # -- a second profile: prefilled with the default's, then its own -------
        created = client.post("/api/profiles", json={"label": "Director AI", "titles": ["director of ai"]})
        assert created.status_code == 201, created.text
        second = created.json()["profile"]
        assert second["is_default"] is False
        assert second["search_settings"] == shared  # a copy of the default's
        assert _profile_file(workpad, second, 1)["search_settings"] == shared

        edited = client.put(
            f"/api/profiles/{second['profile_id']}",
            json={"search_settings": {"location": "Houston, TX", "work_mode": "hybrid", "countries": ["US", "CA"], "max_age_days": 14}},
        )
        assert edited.status_code == 200, edited.text
        second = edited.json()["profile"]
        assert second["search_settings"] == {"location": "Houston, TX", "work_mode": "hybrid", "countries": ["US", "CA"], "max_age_days": 14}
        assert second["revision"] == 2

        # the default profile is still selected: nothing of it moved
        assert _config(client) == default_before
        assert client.get("/api/profiles").json()["profiles"][0] == default

        # -- a run for each seals its own -----------------------------------------
        sealed_default, input_default = _run_and_read_sealed(client, workpad)
        assert sealed_default == default_before["config"]
        assert input_default["config_digest"] == default_before["config_digest"]
        assert sealed_default["location"] == "Denver, CO" and "max_age_days" not in sealed_default

        _select(client, second["profile_id"])
        sealed_second, input_second = _run_and_read_sealed(client, workpad)
        assert sealed_second["location"] == "Houston, TX"
        assert sealed_second["work_mode"] == "hybrid" and sealed_second["remote"] is False
        assert sealed_second["countries"] == ["US", "CA"] and sealed_second["max_age_days"] == 14
        assert sealed_second["roles"] == ["director of ai"]
        assert input_second["config_digest"] != input_default["config_digest"]
        assert input_second["profile_ref"]["profile_id"] == second["profile_id"]
        assert input_second["profile_ref"]["content_digest"] == second["content_digest"]
        # shared fields stay the shared file's
        for key in ("sources", "default_assess_cap", "default_model_target", "published_after"):
            assert sealed_second[key] == sealed_default[key], key

        # -- changing one does not change the other -------------------------------
        moved = client.put(f"/api/profiles/{second['profile_id']}", json={"search_settings": {"location": "Seattle, WA"}})
        assert moved.status_code == 200, moved.text
        assert moved.json()["profile"]["search_settings"] == {
            "location": "Seattle, WA", "work_mode": "hybrid", "countries": ["US", "CA"], "max_age_days": 14,
        }  # the keys left out keep their value
        assert _config(client)["config"]["location"] == "Seattle, WA"
        _select(client, default["profile_id"])
        assert _config(client) == default_before
        assert (target / "find-jobs.json").read_text(encoding="utf-8").count("Denver, CO") == 1  # the shared file was not written

        # the default's setting changes (the shared file): the second keeps its own
        shared_file = json.loads((target / "find-jobs.json").read_text(encoding="utf-8"))
        shared_file["location"] = "Austin, TX"
        (target / "find-jobs.json").write_text(json.dumps(shared_file), encoding="utf-8")
        assert _config(client)["config"]["location"] == "Austin, TX"
        _select(client, second["profile_id"])
        assert _config(client)["config"]["location"] == "Seattle, WA"

        # -- a profile without the fields reads the default's ----------------------
        plain = client.post("/api/profiles", json={"label": "Plain", "titles": ["data engineer"], "search_settings": None})
        assert plain.status_code == 201, plain.text
        third = plain.json()["profile"]
        assert third["search_settings"] is None and third["is_default"] is False
        assert "search_settings" not in _profile_file(workpad, third, 1)  # the record a pre-0110-022 profile has
        _select(client, third["profile_id"])
        sealed_third, _input_third = _run_and_read_sealed(client, workpad)
        assert sealed_third["location"] == "Austin, TX" and sealed_third["roles"] == ["data engineer"]
        assert {key: sealed_third.get(key) for key in ("remote", "work_mode", "countries", "max_age_days", "published_after")} == {
            key: sealed_default.get(key) for key in ("remote", "work_mode", "countries", "max_age_days", "published_after")
        }

        # back to "same as default" for the second profile too
        cleared = client.put(f"/api/profiles/{second['profile_id']}", json={"search_settings": None})
        assert cleared.status_code == 200 and cleared.json()["profile"]["search_settings"] is None
        _select(client, second["profile_id"])
        assert _config(client)["config"]["location"] == "Austin, TX"

        # -- refusals ---------------------------------------------------------------
        on_default = client.put(f"/api/profiles/{default['profile_id']}", json={"search_settings": {"location": "Houston, TX"}})
        assert on_default.status_code == 409, on_default.text
        assert on_default.json()["error"]["code"] == "scout_profile_default_search_settings"

        nested = client.put(f"/api/profiles/{second['profile_id']}", json={"search_settings": {"city": "Houston"}})
        assert nested.status_code == 400, nested.text
        error = nested.json()["error"]
        assert error["allowed_keys"] == _SETTINGS_KEYS
        assert error["field_errors"]["search_settings"] == (
            "search_settings contains unknown key(s): ['city'] (allowed: countries, location, max_age_days, work_mode)"
        )

        top = client.put(f"/api/profiles/{second['profile_id']}", json={"location": "Houston, TX"})
        assert top.status_code == 400, top.text
        assert "search_settings" in top.json()["error"]["allowed_keys"]  # the route's own keys name the nested object

        for bad in ({"work_mode": "sometimes"}, {"countries": ["usa"]}, {"max_age_days": 0}, {"location": ""}):
            refused = client.put(f"/api/profiles/{second['profile_id']}", json={"search_settings": bad})
            assert refused.status_code == 400, (bad, refused.text)
            assert "search_settings" in refused.json()["error"]["field_errors"], bad
        assert client.get("/api/profiles").json()["profiles"][0] == default  # the default record: never rewritten
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)


def _setup_body(roles: list[str], **extra: object) -> dict:
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


def test_the_wizard_save_for_another_profile_never_changes_the_defaults_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        (default,) = client.get("/api/profiles").json()["profiles"]

        # the default profile's own setup save: the shared file, as always
        first = client.put("/api/setup", json=_setup_body(["software engineer"], work_mode="onsite", city="Denver, CO", max_age_days=45))
        assert first.status_code == 200, first.text
        default_config = _config(client)
        assert default_config["config"]["location"] == "Denver, CO" and default_config["config"]["work_mode"] == "onsite"
        assert default_config["config"]["max_age_days"] == 45
        shared_bytes = (target / "find-jobs.json").read_bytes()
        prefs_before = client.get("/api/setup").json()["prefs"]
        (default,) = client.get("/api/profiles").json()["profiles"]
        assert default["search_settings"] is None

        # the wizard's "Create a new profile": POST, then PUT /api/setup with its id
        created = client.post("/api/profiles", json={"label": "Director AI", "titles": ["director of ai"]})
        assert created.status_code == 201, created.text
        second = created.json()["profile"]
        saved = client.put(
            "/api/setup",
            json={
                **_setup_body(["director of ai"], work_mode="hybrid", city="Houston, TX", countries=["US", "CA"], max_age_days=14),
                "profile_id": second["profile_id"],
            },
        )
        assert saved.status_code == 200, saved.text

        listed = {item["profile_id"]: item for item in client.get("/api/profiles").json()["profiles"]}
        assert listed[second["profile_id"]]["search_settings"] == {
            "location": "Houston, TX", "work_mode": "hybrid", "countries": ["US", "CA"], "max_age_days": 14,
        }
        # the symptom: the default profile's location became the new profile's
        assert listed[default["profile_id"]] == default
        assert (target / "find-jobs.json").read_bytes() == shared_bytes
        assert _config(client) == default_config  # the default is still selected
        prefs_after = client.get("/api/setup").json()["prefs"]
        assert {key: prefs_after[key] for key in ("city", "work_mode", "countries")} == {
            key: prefs_before[key] for key in ("city", "work_mode", "countries")
        }
        assert prefs_after["roles"] == ["software engineer", "director of ai"]  # the titles union, as before

        # a run for each seals its own
        sealed_default, _ = _run_and_read_sealed(client, workpad)
        assert sealed_default["location"] == "Denver, CO" and sealed_default["max_age_days"] == 45
        _select(client, second["profile_id"])
        sealed_second, _ = _run_and_read_sealed(client, workpad)
        assert sealed_second["location"] == "Houston, TX" and sealed_second["work_mode"] == "hybrid"
        assert sealed_second["countries"] == ["US", "CA"] and sealed_second["max_age_days"] == 14

        # Edit preferences with the second profile selected: the form opens with ITS values ...
        shown = client.get("/api/setup").json()["prefs"]
        assert (shown["city"], shown["work_mode"], shown["countries"]) == ("Houston, TX", "hybrid", ["US", "CA"])
        # ... and a save without profile_id (the selected one) stays on it
        again = client.put("/api/setup", json=_setup_body(["director of ai"], work_mode="onsite", city="Austin, TX", max_age_days=21))
        assert again.status_code == 200, again.text
        assert _config(client)["config"]["location"] == "Austin, TX"
        assert (target / "find-jobs.json").read_bytes() == shared_bytes
        _select(client, default["profile_id"])
        assert _config(client) == default_config
        assert client.get("/api/setup").json()["prefs"]["city"] == "Denver, CO"
    finally:
        stop_server(server)

    assert_clean_and_healthy(workpad, home)
