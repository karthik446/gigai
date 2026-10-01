"""0110-022: the profile form's search settings, run under node.

``ui/src/profileSettingsModel.js`` is pure JavaScript, so it runs under the
system ``node`` (LOUD skip when absent); what lives in JSX is read statically.

Pinned: the default profile shows the setup settings and is not edited here;
a profile with its own settings shows and prefills those, one without shows
and prefills the default's; the saved body carries all four keys (an empty
window is ``null``: the default profile's); "use the default profile's" sends
``search_settings: null``; the view no longer says location and work mode are
shared, and the location field says it is not printed on the resume.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
MODEL_JS = UI_SRC / "profileSettingsModel.js"

NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const m = await import(input.url);
const [dflt, own, plain] = input.profiles;
const d = input.defaults;
const out = {
  own: [m.hasOwnSettings(dflt), m.hasOwnSettings(own), m.hasOwnSettings(plain)],
  summary: [m.settingsSummary(dflt, d), m.settingsSummary(own, d), m.settingsSummary(plain, d), m.settingsSummary(plain, null)],
  source: [m.settingsSource(dflt), m.settingsSource(own), m.settingsSource(plain)],
  formOwn: m.initialSettingsForm(own, d),
  formPlain: m.initialSettingsForm(plain, d),
  body: m.settingsBody({ location: "  Austin, TX ", workMode: "onsite", countries: ["US"], maxAgeDays: "30" }),
  bodyEmpty: m.settingsBody({ location: "  ", workMode: "any", countries: [], maxAgeDays: "" }),
  clear: m.clearSettingsBody(),
  errors: ["", "30", "0", "366", "1.5", "abc"].map((value) => m.settingsFormError({ maxAgeDays: value })),
};
console.log(JSON.stringify(out));
"""

_DEFAULTS = {"location": "Denver, CO", "work_mode": "remote", "countries": ["US"], "max_age_days": 60}
_OWN = {"location": "Houston, TX", "work_mode": "hybrid", "countries": ["US", "CA"], "max_age_days": 14}


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the profile search settings model was not run")
    payload = {
        "url": MODEL_JS.as_uri(),
        "defaults": _DEFAULTS,
        "profiles": [
            {"profile_id": "p1", "is_default": True, "search_settings": None},
            {"profile_id": "p2", "is_default": False, "search_settings": _OWN},
            {"profile_id": "p3", "is_default": False, "search_settings": None},
        ],
    }
    completed = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT, "--", json.dumps(payload)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_only_a_non_default_profile_with_settings_has_its_own(out: dict) -> None:
    assert out["own"] == [False, True, False]
    assert out["source"] == [
        "Default profile: uses the settings from Preferences.",
        "This profile's own settings.",
        "Same as the default profile.",
    ]


def test_each_profile_shows_what_it_searches_with(out: dict) -> None:
    assert out["summary"][0] == "Denver, CO · Remote-only · US"
    assert out["summary"][1] == "Houston, TX · Hybrid · US, CA · last 14 days"
    assert out["summary"][2] == "Denver, CO · Remote-only · US"  # same as the default
    assert out["summary"][3] == "no area · Any · any country"  # nothing loaded yet


def test_the_form_is_prefilled_with_the_profiles_own_or_the_defaults(out: dict) -> None:
    assert out["formOwn"] == {"location": "Houston, TX", "workMode": "hybrid", "countries": ["US", "CA"], "maxAgeDays": "14"}
    # no own settings: the default's values; the window is left empty (= the default profile's)
    assert out["formPlain"] == {"location": "Denver, CO", "workMode": "remote", "countries": ["US"], "maxAgeDays": ""}


def test_the_saved_body_carries_all_four_keys(out: dict) -> None:
    assert out["body"] == {"search_settings": {"location": "Austin, TX", "work_mode": "onsite", "countries": ["US"], "max_age_days": 30}}
    assert out["bodyEmpty"] == {"search_settings": {"location": None, "work_mode": "any", "countries": [], "max_age_days": None}}
    assert out["clear"] == {"search_settings": None}


def test_the_window_is_empty_or_one_to_365_days(out: dict) -> None:
    assert [error is None for error in out["errors"]] == [True, True, False, False, False, False]


def test_the_view_shows_the_settings_per_profile_and_says_what_the_location_is() -> None:
    view = (UI_SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    panel = (UI_SRC / "components" / "ProfileSearchSettings.jsx").read_text(encoding="utf-8")
    hooks = (UI_SRC / "hooks.js").read_text(encoding="utf-8")
    assert "<ProfileSearchSettings" in view and 'data-role="profile-search-summary"' in view
    assert "Everything else is shared" not in view and "Countries, work mode, visa" not in view
    assert "config.location" not in view and "config.remote" not in view  # no longer listed as shared
    assert "It is not printed on the resume." in panel
    assert "!profile.is_default" in panel  # the default profile is edited in Preferences
    assert "updateProfile(profile.profile_id, body)" in panel
    assert "response.default_search_settings" in hooks
