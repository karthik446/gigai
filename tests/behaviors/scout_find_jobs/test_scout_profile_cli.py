"""0110-022: ``gigai scout profile list|update`` (a profile's own search settings).

Synthetic fixtures only. The default profile uses the setup settings and is
refused by ``update``; another profile's options replace only what was
given and change no other profile; ``--same-as-default`` drops them.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.profile_records import create_profile, list_profiles, selected_profile

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_SHARED = {"location": "Remote", "work_mode": "any", "countries": ["US"], "max_age_days": None}


def _gig(tmp_path: Path) -> tuple[ProfileFixtureGig, str, str]:
    fx = build_gig_with_resume(tmp_path)
    default = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert default is not None
    second = create_profile(
        fx.resolved, label="Director", titles=("director of ai",), titles_to_avoid=(),
        queries=("director of ai",), resume_ref=default.resume_ref,
    )
    return fx, default.profile_id, second.profile_id


def _run(fx: ProfileFixtureGig, *args: str, as_json: bool = True):
    base = ["scout", "profile", *args, "--home", str(fx.home_root), "--target", str(fx.target)]
    return CliRunner().invoke(cli, base + (["--json"] if as_json else []))


def test_list_shows_the_default_and_each_profiles_settings(tmp_path: Path) -> None:
    fx, default_id, second_id = _gig(tmp_path)
    result = _run(fx, "list")
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["default_profile_id"] == default_id and body["default_search_settings"] == _SHARED
    by_id = {item["profile_id"]: item for item in body["profiles"]}
    assert by_id[default_id]["is_default"] is True and by_id[default_id]["selected"] is True
    assert by_id[default_id]["search_settings"] is None
    assert by_id[second_id]["is_default"] is False and by_id[second_id]["search_settings"] is None

    plain = _run(fx, "list", as_json=False)
    assert plain.exit_code == 0, plain.output
    assert "[default, selected]" in plain.output and "setup settings: Remote" in plain.output
    assert "same as default: Remote" in plain.output


def test_update_sets_only_what_was_given_on_that_profile_only(tmp_path: Path) -> None:
    fx, default_id, second_id = _gig(tmp_path)
    before = {item.profile_id: item for item in list_profiles(fx.resolved)}

    result = _run(fx, "update", second_id, "--location", "Houston, TX", "--work-mode", "hybrid")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["profile"]["search_settings"] == {
        "location": "Houston, TX", "work_mode": "hybrid", "countries": ["US"], "max_age_days": None,
    }  # countries and window: the default's, which it had until now

    again = _run(fx, "update", second_id, "--country", "us", "--country", "CA", "--max-age-days", "30")
    assert again.exit_code == 0, again.output
    assert json.loads(again.output)["profile"]["search_settings"] == {
        "location": "Houston, TX", "work_mode": "hybrid", "countries": ["US", "CA"], "max_age_days": 30,
    }
    after = {item.profile_id: item for item in list_profiles(fx.resolved)}
    assert after[default_id] == before[default_id]  # not rewritten

    plain = _run(fx, "list", as_json=False)
    assert "own settings: Houston, TX · hybrid · US, CA · last 30 days" in plain.output

    cleared = _run(fx, "update", second_id, "--same-as-default")
    assert cleared.exit_code == 0, cleared.output
    assert json.loads(cleared.output)["profile"]["search_settings"] is None


def test_update_refuses_the_default_profile_bad_values_and_unknown_profiles(tmp_path: Path) -> None:
    fx, default_id, second_id = _gig(tmp_path)

    on_default = _run(fx, "update", default_id, "--location", "Houston, TX")
    assert on_default.exit_code == 1
    assert json.loads(on_default.output)["error"]["code"] == "scout_profile_default_search_settings"

    missing = _run(fx, "update", "profile_00000000-0000-4000-8000-00000000ffff", "--work-mode", "remote")
    assert missing.exit_code == 1 and json.loads(missing.output)["error"]["code"] == "scout_profile_unavailable"

    bad_country = _run(fx, "update", second_id, "--country", "usa")
    assert bad_country.exit_code == 1 and json.loads(bad_country.output)["error"]["code"] == "scout_profile_invalid"

    nothing = _run(fx, "update", second_id)
    assert nothing.exit_code == 1 and "nothing to change" in json.loads(nothing.output)["error"]["message"]

    both = _run(fx, "update", second_id, "--same-as-default", "--work-mode", "remote")
    assert both.exit_code == 1 and "alone" in json.loads(both.output)["error"]["message"]

    assert all(item.search_settings is None for item in list_profiles(fx.resolved))  # nothing was written
