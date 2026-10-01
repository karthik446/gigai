"""0110-026f: the background settings journey (``GET``/``PUT /api/settings/background``).

Through the real supervised server, like every journey in this suite. The
settings are the project's ``<home>/scout/<project_id>/settings.json``: the
file three background jobs read (the hourly refresh, the model tag queue, the
snapshot download).

1. ``GET`` on a fresh project: the defaults, and no file is created.
2. ``PUT`` changes the named keys; ``GET`` reads them back; the file holds
   them; the sources status (``snapshot`` block) follows the file.
3. A ``PUT`` keeps every key it does not name, known or not.
4. ``manifest_url: null`` puts the default location back.
5. A wrong type, an unknown key, a bad model name or an empty body is 422 and
   changes nothing.
6. A stored file Scout cannot read is never overwritten: 409, bytes intact.
7. A foreign ``Origin`` is 403.
8. 0110-033: ``sources.check_times`` round trip (one day at a time, sorted,
   reset by ``null``), its invalid bodies, and the sources status showing the
   saved times as the schedule.

The harness forces ``GIGAI_SCOUT_AUTO_REFRESH`` for every journey server (so
no journey gets a tick it did not ask for), which is why ``effective`` says
``environment`` here while ``settings`` shows what the file holds. That the
running refresh thread honours a written ``sources.auto_refresh`` is proved
in-process, without the override, in
``tests/behaviors/scout_find_jobs/test_background_settings.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout.find_jobs.refresh_tick import settings_path

from tests.api_e2e.after_journey import assert_clean_and_healthy, timed_request
from tests.api_e2e.harness import (
    add_resume,
    resolve_workpad_path,
    setup_and_init,
    start_server,
    stop_server,
    write_offline_find_jobs_config,
)

DEFAULT_URL = "https://github.com/karthik446/gigai/releases/download/scout-snapshot/manifest.json"
DEFAULT_TIMES = {
    "weekdays": ["03:00", "07:00", "09:00", "11:00", "13:00", "15:00", "17:00", "19:00"],
    "weekends": ["09:00", "18:00"],
}
DEFAULTS = {
    "sources": {"auto_refresh": True, "check_times": DEFAULT_TIMES},
    "tagging": {"model_enabled": True, "backfill_enabled": False, "tag_backfill_model": "configured"},
    "snapshot": {"enabled": True, "manifest_url": DEFAULT_URL},
}


def test_background_settings_round_trip_validate_and_keep_what_they_do_not_know(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = setup_and_init(tmp_path)
    add_resume(home, target, tmp_path)
    write_offline_find_jobs_config(target)
    monkeypatch.delenv("GIGAI_SCOUT_SNAPSHOT", raising=False)  # the suite's default switches it off; this journey reads the real default (no update runs here)
    server = start_server(home, target, monkeypatch=monkeypatch)
    try:
        client = server.client
        workpad = resolve_workpad_path(home, target)
        path = settings_path(home, target)

        # -- 1. the defaults; reading creates nothing ---------------------------
        first, latency = timed_request("GET /api/settings/background", lambda: client.get("/api/settings/background"))
        assert first.status_code == 200, first.text
        latency.assert_within_budget()
        assert first.json() == {
            "schema_version": "scout-background-settings:1",
            "readable": True,
            "settings": DEFAULTS,
            "effective": {
                # The harness and the suite's conftest override these two for every journey server.
                "sources": {
                    "auto_refresh": False,
                    "source": "environment",
                    "check_times": {**DEFAULT_TIMES, "source": "default", "default": DEFAULT_TIMES},
                },
                "tagging": {"model_enabled": False, "backfill_enabled": False, "tag_backfill_model": "configured", "source": "environment"},
                "snapshot": {"enabled": True, "manifest_url": DEFAULT_URL, "source": "default"},
            },
        }
        assert not path.exists()

        # -- 2. a write is read back, lands in the file, and the status follows --
        change = {
            "sources": {"auto_refresh": False},
            "tagging": {"backfill_enabled": True, "tag_backfill_model": "haiku"},
            "snapshot": {"enabled": False, "manifest_url": "  https://example.test/scout/manifest.json "},
        }
        saved, put_latency = timed_request("PUT /api/settings/background", lambda: client.put("/api/settings/background", json=change))
        assert saved.status_code == 200, saved.text
        put_latency.assert_within_budget()
        expected = {
            "sources": {"auto_refresh": False, "check_times": DEFAULT_TIMES},
            "tagging": {"model_enabled": True, "backfill_enabled": True, "tag_backfill_model": "haiku"},
            "snapshot": {"enabled": False, "manifest_url": "https://example.test/scout/manifest.json"},
        }
        assert saved.json()["settings"] == expected
        read_back = client.get("/api/settings/background").json()
        assert read_back == saved.json() and read_back["readable"] is True
        assert read_back["effective"]["snapshot"] == {"enabled": False, "manifest_url": "https://example.test/scout/manifest.json", "source": "setting"}
        assert read_back["effective"]["tagging"]["backfill_enabled"] is True and read_back["effective"]["tagging"]["tag_backfill_model"] == "haiku"
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "schema_version": "scout-settings:1",
            "sources": {"auto_refresh": False},
            # model_enabled was not named, so it is not written: its default still applies.
            "tagging": {"backfill_enabled": True, "tag_backfill_model": "haiku"},
            "snapshot": {"enabled": False, "manifest_url": "https://example.test/scout/manifest.json"},
        }
        assert [item.name for item in path.parent.iterdir() if item.name.startswith("settings.json")] == ["settings.json"], "no temp file is left"
        status = client.get("/api/sources/update").json()
        assert (status["snapshot"]["enabled"], status["snapshot"]["setting_source"], status["snapshot"]["manifest_url"]) == (False, "setting", "https://example.test/scout/manifest.json")
        assert status["tags"]["setting"]["backfill_enabled"] is True and status["tags"]["models"] == {"demand": "ollama_local", "backfill": "claude_cli:haiku"}

        # -- 3. keys the route does not know survive a write ----------------------
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["a_later_version"] = {"keeps": ["this"]}
        stored["sources"]["some_other_key"] = 7
        path.write_text(json.dumps(stored), encoding="utf-8")
        again = client.put("/api/settings/background", json={"tagging": {"model_enabled": False}})
        assert again.status_code == 200, again.text
        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["a_later_version"] == {"keeps": ["this"]} and after["sources"] == {"auto_refresh": False, "some_other_key": 7}
        assert after["tagging"] == {"backfill_enabled": True, "tag_backfill_model": "haiku", "model_enabled": False}
        assert after["snapshot"] == stored["snapshot"]

        # -- 4. null puts the default location back -------------------------------
        reset = client.put("/api/settings/background", json={"snapshot": {"manifest_url": None, "enabled": True}})
        assert reset.status_code == 200, reset.text
        assert reset.json()["settings"]["snapshot"] == {"enabled": True, "manifest_url": DEFAULT_URL}
        assert json.loads(path.read_text(encoding="utf-8"))["snapshot"] == {"enabled": True}

        # -- 5. validation: 422, and the file is not touched -----------------------
        before_bytes = path.read_bytes()
        for body, code in (
            ({"sources": {"auto_refresh": "no"}}, "wrong_type"),
            ({"sources": {"auto_refresh": 0}}, "wrong_type"),
            ({"tagging": {"model_enabled": None}}, "wrong_type"),
            ({"tagging": {"backfill_enabled": "true"}}, "wrong_type"),
            ({"tagging": {"tag_backfill_model": 3}}, "wrong_type"),
            ({"tagging": {"tag_backfill_model": "gpt"}}, "bad_enum"),
            ({"snapshot": {"enabled": 1}}, "wrong_type"),
            ({"snapshot": {"manifest_url": 5}}, "wrong_type"),
            ({"snapshot": {"manifest_url": "  "}}, "invalid_value"),
            ({"snapshot": {"manifest_url": "ftp://example.test/manifest.json"}}, "invalid_value"),
            ({"snapshot": []}, "wrong_type"),
            ({"sources": {"auto_refresh": True, "interval": 5}}, "unknown_key"),
            ({"bogus": 1}, "unknown_key"),
            ({"schema_version": "scout-settings:1"}, "unknown_key"),
            ({}, "invalid_value"),
            ({"sources": {}}, "invalid_value"),
            ([], "wrong_type"),
            # 0110-033: the check times.
            ({"sources": {"check_times": "09:00"}}, "wrong_type"),
            ({"sources": {"check_times": {}}}, "invalid_value"),
            ({"sources": {"check_times": {"weekdays": "09:00"}}}, "wrong_type"),
            ({"sources": {"check_times": {"weekdays": [9]}}}, "wrong_type"),
            ({"sources": {"check_times": {"weekdays": []}}}, "invalid_value"),
            ({"sources": {"check_times": {"weekdays": [f"{hour:02d}:15" for hour in range(13)]}}}, "invalid_value"),
            ({"sources": {"check_times": {"weekdays": ["9:00"]}}}, "invalid_value"),
            ({"sources": {"check_times": {"weekdays": ["24:00"]}}}, "invalid_value"),
            ({"sources": {"check_times": {"weekends": ["12:60"]}}}, "invalid_value"),
            ({"sources": {"check_times": {"weekdays": ["noon"]}}}, "invalid_value"),
            ({"sources": {"check_times": {"mondays": ["09:00"]}}}, "unknown_key"),
            ({"sources": {"auto_refresh": True, "check_times": {"weekdays": ["09:00"], "weekends": ["9"]}}}, "invalid_value"),
            # One bad key refuses the whole body: the good one beside it is not written.
            ({"sources": {"auto_refresh": True}, "tagging": {"model_enabled": "yes"}}, "wrong_type"),
        ):
            refused = client.put("/api/settings/background", json=body)
            assert refused.status_code == 422, (body, refused.text)
            assert refused.json()["error"]["code"] == code, (body, refused.text)
        assert path.read_bytes() == before_bytes

        # -- 6. a file Scout cannot read is left alone ----------------------------
        path.write_text('{"schema_version": "scout-settings:1", "sources": ', encoding="utf-8")
        broken = path.read_bytes()
        conflict = client.put("/api/settings/background", json={"sources": {"auto_refresh": True}})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "settings_unreadable", conflict.text
        assert path.read_bytes() == broken
        unreadable = client.get("/api/settings/background").json()
        assert unreadable["readable"] is False
        assert unreadable["settings"]["sources"] == {"auto_refresh": False, "check_times": DEFAULT_TIMES}
        assert unreadable["effective"]["snapshot"]["source"] == "settings_unreadable"
        assert unreadable["effective"]["sources"]["check_times"]["source"] == "settings_unreadable"
        path.write_bytes(before_bytes)
        assert client.get("/api/settings/background").json()["readable"] is True

        # -- 7. the write is guarded like every other one --------------------------
        evil = client.put("/api/settings/background", json={"sources": {"auto_refresh": True}}, headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403, evil.text
        assert path.read_bytes() == before_bytes

        # -- 8. 0110-033: the check times ----------------------------------------
        # One day, unsorted with a repeat: stored sorted and once; the other day keeps its default and is not written.
        times = client.put("/api/settings/background", json={"sources": {"check_times": {"weekdays": ["16:30", "08:00", "12:00", "08:00"]}}})
        assert times.status_code == 200, times.text
        assert times.json()["settings"]["sources"]["check_times"] == {"weekdays": ["08:00", "12:00", "16:30"], "weekends": DEFAULT_TIMES["weekends"]}
        assert times.json()["effective"]["sources"]["check_times"] == {
            "weekdays": ["08:00", "12:00", "16:30"], "weekends": DEFAULT_TIMES["weekends"], "source": "setting", "default": DEFAULT_TIMES,
        }
        assert client.get("/api/settings/background").json() == times.json()
        stored_sources = json.loads(path.read_text(encoding="utf-8"))["sources"]
        assert stored_sources["check_times"] == {"weekdays": ["08:00", "12:00", "16:30"]}
        assert stored_sources["some_other_key"] == 7 and stored_sources["auto_refresh"] is False  # nothing else in the block moved
        # The schedule the background checks follow is the saved one.
        schedule = client.get("/api/sources/update").json()["refresh"]["schedule"]
        assert (schedule["kind"], schedule["weekdays"], schedule["weekends"], schedule["source"]) == ("times", ["08:00", "12:00", "16:30"], DEFAULT_TIMES["weekends"], "setting")
        # The other day: the first stays.
        weekend = client.put("/api/settings/background", json={"sources": {"check_times": {"weekends": ["10:00"]}}})
        assert weekend.status_code == 200, weekend.text
        assert weekend.json()["settings"]["sources"]["check_times"] == {"weekdays": ["08:00", "12:00", "16:30"], "weekends": ["10:00"]}
        # A refused body changes nothing.
        kept_bytes = path.read_bytes()
        refused_times = client.put("/api/settings/background", json={"sources": {"check_times": {"weekdays": ["8:00"]}}})
        assert refused_times.status_code == 422 and refused_times.json()["error"]["code"] == "invalid_value", refused_times.text
        nested_times = client.put("/api/settings/background", json={"sources": {"check_times": {"mondays": ["09:00"]}}})
        assert nested_times.status_code == 422 and nested_times.json()["error"]["allowed_keys"] == ["weekdays", "weekends"], nested_times.text
        assert path.read_bytes() == kept_bytes
        # null for one day: that day's default; null for the setting: both defaults, and the key leaves the file.
        one_default = client.put("/api/settings/background", json={"sources": {"check_times": {"weekdays": None}}})
        assert one_default.status_code == 200, one_default.text
        assert one_default.json()["settings"]["sources"]["check_times"] == {"weekdays": DEFAULT_TIMES["weekdays"], "weekends": ["10:00"]}
        both_default = client.put("/api/settings/background", json={"sources": {"check_times": None}})
        assert both_default.status_code == 200, both_default.text
        assert both_default.json()["settings"]["sources"]["check_times"] == DEFAULT_TIMES
        assert both_default.json()["effective"]["sources"]["check_times"]["source"] == "default"
        assert "check_times" not in json.loads(path.read_text(encoding="utf-8"))["sources"]
        assert client.get("/api/sources/update").json()["refresh"]["schedule"]["source"] == "default"

        assert_clean_and_healthy(workpad, home)
    finally:
        stop_server(server)
