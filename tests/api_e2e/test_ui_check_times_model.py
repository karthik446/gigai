"""0110-033: Settings > Background updates, the times of day the boards are checked at.

``sources.check_times`` was file-only. The form now edits it: one line of
times for weekdays, one for weekend days, a reset to the default times, and
what is in effect now. ``ui/src/backgroundSettingsModel.js`` is pure
JavaScript, so its rules run under the system ``node`` (LOUD skip without
it) against the body the server's own ``background_settings`` builds; every
``PUT`` body the model makes is then given to the server's validator and
writer, and the body read back is given to the model again. What lives in
JSX is pinned by reading the source.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.background_settings import (
    MAX_CHECK_TIMES_PER_DAY,
    SettingsError,
    background_settings,
    validate_patch,
    write_background_settings,
)
from gigai.scout.find_jobs.refresh_plan import DEFAULT_WEEKDAY_TIMES, DEFAULT_WEEKEND_TIMES
from gigai.scout.find_jobs.refresh_tick import check_schedule_setting, settings_path

from tests.behaviors.scout_find_jobs.test_sources_update import _installed

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
MODEL = UI_SRC / "backgroundSettingsModel.js"

SCRIPT = """
const input = JSON.parse(process.argv[1]);
const m = await import(input.model);
const out = {};
for (const [name, call] of Object.entries(input.calls)) {
  const [fn, ...args] = call;
  out[name] = m[fn](...args);
}
out.constants = { max: m.MAX_CHECK_TIMES_PER_DAY, days: m.CHECK_TIME_DAYS, help: m.CHECK_TIMES_HELP, autoHelp: m.AUTO_REFRESH_HELP };
console.log(JSON.stringify(out));
"""


def _node(calls: dict[str, list[object]]) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the check-times model was NOT run")
    payload = {"model": MODEL.as_uri(), "calls": calls}
    done = subprocess.run([node, "--input-type=module", "-e", SCRIPT, json.dumps(payload)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    for name in ("GIGAI_SCOUT_AUTO_REFRESH", "GIGAI_SCOUT_MODEL_TAGS", "GIGAI_SCOUT_SNAPSHOT", "GIGAI_SCOUT_SNAPSHOT_MANIFEST_URL"):
        monkeypatch.delenv(name, raising=False)
    return _installed(tmp_path)


def _body(project: tuple[Path, Path]) -> dict[str, Any]:
    return json.loads(json.dumps(background_settings(*project)))  # as the route answers it


WEEKDAYS = ", ".join(DEFAULT_WEEKDAY_TIMES)
WEEKENDS = ", ".join(DEFAULT_WEEKEND_TIMES)


def test_the_form_shows_the_stored_times_and_what_is_in_effect(project: tuple[Path, Path]) -> None:
    fresh = _body(project)
    out = _node({
        "draft": ["draftFromResponse", fresh],
        "shown": ["hasCheckTimes", fresh],
        "summary": ["scheduleSummary", fresh],
        "defaults": ["defaultCheckTimes", fresh],
    })
    assert (out["draft"]["weekdayTimes"], out["draft"]["weekendTimes"]) == (WEEKDAYS, WEEKENDS)
    assert out["shown"] is True
    assert out["defaults"] == {"weekdays": list(DEFAULT_WEEKDAY_TIMES), "weekends": list(DEFAULT_WEEKEND_TIMES)}
    assert out["summary"] == (
        f"In effect now: weekdays at {WEEKDAYS} (8 a day); weekend days at {WEEKENDS} (2 a day). Local time. These are the default times."
    )
    assert out["constants"]["max"] == MAX_CHECK_TIMES_PER_DAY and out["constants"]["days"] == ["weekdays", "weekends"]
    assert "24-hour times" in out["constants"]["help"] and f"1 to {MAX_CHECK_TIMES_PER_DAY} a day" in out["constants"]["help"]
    assert "about once an hour" not in out["constants"]["autoHelp"] and "at the times below" in out["constants"]["autoHelp"]

    write_background_settings(*project, validate_patch({"sources": {"check_times": {"weekdays": ["12:00"], "weekends": ["10:00"]}}}))
    saved = _body(project)
    out = _node({"draft": ["draftFromResponse", saved], "summary": ["scheduleSummary", saved]})
    assert (out["draft"]["weekdayTimes"], out["draft"]["weekendTimes"]) == ("12:00", "10:00")
    assert out["summary"] == "In effect now: weekdays at 12:00 (once a day); weekend days at 10:00 (once a day). Local time."

    # Stored times that cannot be used: the defaults run, and the form says so.
    path = settings_path(*project)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "sources": {"check_times": {"weekdays": ["noon"]}}}), encoding="utf-8")
    out = _node({"summary": ["scheduleSummary", _body(project)]})
    assert out["summary"].endswith("The saved times cannot be used, so these are the default times.")


def test_a_line_of_times_is_read_sorted_and_once_and_refused_like_the_server_refuses(project: tuple[Path, Path]) -> None:
    lines = {
        "tidy": "07:00, 13:30",
        "loose": " 13:30 ,7:00  07:00,",
        "spaces": "09:00 18:00",
        "empty": "",
        "blank": " , ",
        "word": "07:00, noon",
        "hour": "25:00",
        "minute": "07:60",
        "short": "07:0",
        "thirteen": ", ".join(f"{hour:02d}:15" for hour in range(13)),
        "twelve": ", ".join(f"{hour:02d}:15" for hour in range(12)),
    }
    out = _node({name: ["parseCheckTimes", text, "Weekdays"] for name, text in lines.items()})
    assert out["tidy"] == {"times": ["07:00", "13:30"], "error": ""}
    assert out["loose"] == out["tidy"] and out["spaces"] == {"times": ["09:00", "18:00"], "error": ""}
    assert len(out["twelve"]["times"]) == 12 and out["twelve"]["error"] == ""
    assert out["empty"]["error"] == out["blank"]["error"] == "Weekdays: enter at least one time."
    assert out["thirteen"]["error"] == "Weekdays: at most 12 times a day."
    for name, bad in (("word", "noon"), ("hour", "25:00"), ("minute", "07:60"), ("short", "07:0")):
        assert out[name] == {"times": [], "error": f"Weekdays: “{bad}” is not a 24-hour time like 07:00 or 13:30."}

    # The same verdict as the server's, line by line: what the form accepts the PUT accepts, and the other way round.
    for name, parsed in out.items():
        if name == "constants":
            continue
        if parsed["error"]:
            # What the form refuses it never sends; sent anyway, the server refuses it too.
            raw = [part for part in lines[name].replace(",", " ").split() if part] if name != "thirteen" else lines[name].split(", ")
            with pytest.raises(SettingsError):
                validate_patch({"sources": {"check_times": {"weekdays": raw}}})
        else:
            assert validate_patch({"sources": {"check_times": {"weekdays": parsed["times"]}}})["sources"]["check_times"] == {"weekdays": parsed["times"]}


def test_the_put_body_names_only_the_day_that_changed_and_round_trips_through_the_server(project: tuple[Path, Path]) -> None:
    fresh = _body(project)
    draft = _node({"draft": ["draftFromResponse", fresh]})["draft"]
    out = _node({
        "unchanged": ["buildPatch", draft, fresh],
        "reordered": ["buildPatch", {**draft, "weekdayTimes": " ".join(reversed(DEFAULT_WEEKDAY_TIMES))}, fresh],
        "weekday": ["buildPatch", {**draft, "weekdayTimes": "16:30, 8:00, 12:00, 08:00"}, fresh],
        "both": ["buildPatch", {**draft, "weekdayTimes": "08:00", "weekendTimes": "10:00, 20:00", "autoRefresh": False}, fresh],
        "invalid": ["buildPatch", {**draft, "weekdayTimes": "25:00", "autoRefresh": False}, fresh],
        "invalidError": ["formError", {**draft, "weekdayTimes": "25:00"}, fresh],
        "emptyError": ["formError", {**draft, "weekendTimes": ""}, fresh],
        "fine": ["formError", {**draft, "weekdayTimes": "08:00"}, fresh],
    })
    assert out["unchanged"] is None and out["reordered"] is None, "the same times in another order are not a change"
    assert out["weekday"] == {"sources": {"check_times": {"weekdays": ["08:00", "12:00", "16:30"]}}}
    assert out["both"] == {"sources": {"auto_refresh": False, "check_times": {"weekdays": ["08:00"], "weekends": ["10:00", "20:00"]}}}
    assert out["invalid"] == {"sources": {"auto_refresh": False}}, "a line that cannot be saved is never sent"
    assert out["invalidError"].startswith("Weekdays: “25:00”") and out["emptyError"] == "Weekend days: enter at least one time."
    assert out["fine"] == ""

    # Server side: the body is accepted, written, and read back as the draft the form then shows.
    write_background_settings(*project, validate_patch(out["both"]))
    saved = _body(project)
    assert saved["settings"]["sources"] == {"auto_refresh": False, "check_times": {"weekdays": ["08:00"], "weekends": ["10:00", "20:00"]}}
    schedule = check_schedule_setting(*project)
    assert (schedule.schedule.weekdays, schedule.schedule.weekends, schedule.source) == (("08:00",), ("10:00", "20:00"), "setting")
    again = _node({"draft": ["draftFromResponse", saved]})["draft"]
    assert (again["weekdayTimes"], again["weekendTimes"], again["autoRefresh"]) == ("08:00", "10:00, 20:00", False)
    assert _node({"unchanged": ["buildPatch", again, saved]})["unchanged"] is None

    # "Use the default times": both lines back to the defaults; saved as null, so the file holds no list.
    reset = _node({"draft": ["withDefaultCheckTimes", again, saved]})["draft"]
    assert (reset["weekdayTimes"], reset["weekendTimes"], reset["autoRefresh"]) == (WEEKDAYS, WEEKENDS, False)
    patch = _node({"patch": ["buildPatch", reset, saved]})["patch"]
    assert patch == {"sources": {"check_times": {"weekdays": None, "weekends": None}}}
    write_background_settings(*project, validate_patch(patch))
    assert json.loads(settings_path(*project).read_text(encoding="utf-8"))["sources"] == {"auto_refresh": False}
    assert _body(project)["effective"]["sources"]["check_times"]["source"] == "default"

    # Typing a default list by hand is the same reset for that day only.
    write_background_settings(*project, validate_patch({"sources": {"check_times": {"weekdays": ["08:00"], "weekends": ["10:00"]}}}))
    stored = _body(project)
    typed = _node({"patch": ["buildPatch", {**_node({"d": ["draftFromResponse", stored]})["d"], "weekendTimes": WEEKENDS}, stored]})["patch"]
    assert typed == {"sources": {"check_times": {"weekends": None}}}
    write_background_settings(*project, validate_patch(typed))
    assert _body(project)["settings"]["sources"]["check_times"] == {"weekdays": ["08:00"], "weekends": list(DEFAULT_WEEKEND_TIMES)}


def test_a_server_that_sends_no_check_times_shows_no_fields_and_is_sent_none(project: tuple[Path, Path]) -> None:
    """An older server: the form must not send a key it would refuse as unknown."""

    older = _body(project)
    del older["settings"]["sources"]["check_times"]  # type: ignore[index]
    del older["effective"]["sources"]["check_times"]  # type: ignore[index]
    draft = _node({"draft": ["draftFromResponse", older]})["draft"]
    out = _node({
        "shown": ["hasCheckTimes", older],
        "summary": ["scheduleSummary", older],
        "patch": ["buildPatch", {**draft, "weekdayTimes": "08:00", "autoRefresh": False}, older],
        "error": ["formError", {**draft, "weekdayTimes": ""}, older],
    })
    assert (draft["weekdayTimes"], draft["weekendTimes"]) == ("", "")
    assert out["shown"] is False and out["summary"] == "" and out["error"] == ""
    assert out["patch"] == {"sources": {"auto_refresh": False}}


def test_the_panel_has_the_two_lines_the_reset_and_the_effective_schedule() -> None:
    panel = (UI_SRC / "components" / "BackgroundUpdatesPanel.jsx").read_text(encoding="utf-8")
    for needle in (
        'id="background-check-times-weekdays"',
        'id="background-check-times-weekends"',
        'data-action="check-times-default"',
        'data-role="check-times-effective"',
        'data-role="check-times-error"',
        "onClick={() => change(withDefaultCheckTimes(draft, response))}",
        "{timesShown && (",
        "const invalid = draft ? formError(draft, response) : \"\";",
    ):
        assert needle in panel, needle
    # Still saved by the one button, with the other settings; a line that cannot be saved keeps Save off.
    assert panel.count("putBackgroundSettings(") == 1 and "disabled={off || !patch || Boolean(invalid)}" in panel
    assert panel.count("useEffect(") == 1, "the one effect is the first read; no autosave"
