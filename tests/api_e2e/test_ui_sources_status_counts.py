"""0110-028 / 0110-029: the sources status lines say what the API counts, in honest words.

One tag store fixture, read by the API's ``tags`` block
(``sources_status.tags_block``) and rendered by the UI model
(``ui/src/sourcesStatusModel.js`` under the system ``node``; LOUD skip
without it). Pinned:

* the numbers in the UI line are the API's, and the function split adds up
  to the stored titles (the operator saw "116,524 by rules" beside a status
  that said 166,907: every stored title has a rules LEVEL, fewer a FUNCTION);
* "waiting" counts only the titles a model WILL tag under the settings in
  effect; the rest are "not tagged by a model", with the reason;
* a background check and a manual update are labelled for what they are.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gigai.scout.find_jobs import posting_tags, sources_status
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.model_tag import Demand, TaggingSetting, TagQueue
from gigai.scout.find_jobs.refresh_plan import CheckSchedule
from gigai.scout.find_jobs.refresh_tick import RefreshTicker, schedule_status
from gigai.scout.find_jobs.sources_status import refresh_block, tags_block

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

BY_RULES = ["Senior Software Engineer", "Staff Data Analyst", "Director of Engineering"]  # the rules find a function
BY_MODEL = "Director of Potions"
MODEL_OTHER = "Senior Tinkerer"
DEMAND_WAITING = ["Director of Wizardry 1", "Director of Wizardry 2"]  # director level: the active profile asks for it
REST_WAITING = ["Senior Alchemist 1", "Senior Alchemist 2", "Senior Alchemist 3"]

SCRIPT = """
const input = JSON.parse(process.argv[1]);
const lines = await import(input.status);
const out = {};
for (const [name, status] of Object.entries(input.statuses)) {
  out[name] = Object.fromEntries(lines.statusLines(status).map((item) => [item.role, item.text]));
}
console.log(JSON.stringify(out));
"""


def _render(statuses: dict[str, dict]) -> dict[str, dict[str, str]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the sources status lines were NOT run")
    payload = {"status": (UI_SRC / "sourcesStatusModel.js").as_uri(), "statuses": statuses}
    done = subprocess.run(
        [node, "--input-type=module", "-e", SCRIPT, json.dumps(payload)],
        capture_output=True, text=True, timeout=60, check=False, env={**os.environ, "TZ": "America/Denver"},
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    store = posting_tags.default_store(home)
    try:
        posting_tags.tag_new_titles(store, [*BY_RULES, BY_MODEL, MODEL_OTHER, *DEMAND_WAITING, *REST_WAITING])
        assert store.set_model_function(posting_tags.normalize_title(BY_MODEL), "operations", model="codex_cli:default", prompt_version="tag-v1")
        assert store.set_model_function(posting_tags.normalize_title(MODEL_OTHER), None, model="codex_cli:default", prompt_version="tag-v1")
    finally:
        store.close()
    return home


def _block(home: Path, monkeypatch: pytest.MonkeyPatch, *, model: bool = True, backfill: bool = False, refresh: bool = True, thread: bool = True, manual_live: bool = False) -> dict:
    setting = TaggingSetting(model, backfill, "configured", "setting")
    monkeypatch.setattr(sources_status, "tagging_setting", lambda *_args, **_kwargs: setting)
    target = home.parent / "project"
    ticker = None
    if thread:
        queue = TagQueue(home_root=home, target=target, demand_loader=lambda _home, _target: Demand(("Director of Engineering",), "codex_cli"), setting=lambda: setting)
        ticker = RefreshTicker(home_root=home, target=target, tag_queue=queue)
    background = {"auto_refresh": {"enabled": refresh}, "in_progress": manual_live, "trigger": "manual" if manual_live else None}
    return tags_block(home, target, ticker=ticker, environ={}, background=background)


def _numbers(text: str) -> list[int]:
    return [int(found.replace(",", "")) for found in re.findall(r"\d[\d,]*", text)]


def test_the_ui_line_carries_the_api_counts_and_the_function_split_adds_up(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = _block(home, monkeypatch)

    # What each number counts (the API's own words, sources_status.py).
    assert api["titles"] == 10  # every stored title; each has a rules LEVEL
    assert (api["tagged_by_rules"], api["tagged_by_model"], api["model_other"], api["awaiting_model"]) == (3, 1, 1, 5)
    assert api["tagged_by_rules"] + api["tagged_by_model"] + api["model_other"] + api["awaiting_model"] == api["titles"]
    assert (api["awaiting_model_queued"], api["awaiting_model_not_queued"], api["not_queued_reason"]) == (2, 3, "backfill_off")
    assert api["awaiting_model_queued"] + api["awaiting_model_not_queued"] == api["awaiting_model"]

    ui = _render({"default": {"tags": api}})["default"]

    assert ui["tags"] == (
        "Titles: 10 stored, each with a level from the rules. Function: 3 by rules, 1 by model, 1 a model could not place, "
        "2 waiting for the model, 3 not tagged by a model (background tagging of the rest is off)"
    )
    shown = _numbers(ui["tags"])
    assert shown == [api["titles"], api["tagged_by_rules"], api["tagged_by_model"], api["model_other"], api["awaiting_model_queued"], api["awaiting_model_not_queued"]]
    assert sum(shown[1:]) == shown[0]
    assert ui["tagging"] == "Tagging: running, 2 titles to go (it also runs while a background check does)"


def test_waiting_counts_only_what_a_model_will_tag_under_the_settings(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocks = {
        "backfill_off": _block(home, monkeypatch),
        "backfill_on": _block(home, monkeypatch, backfill=True),
        "model_off": _block(home, monkeypatch, model=False),
        "refresh_off": _block(home, monkeypatch, refresh=False),
        "no_thread": _block(home, monkeypatch, thread=False),
        "manual_update": _block(home, monkeypatch, manual_live=True),
    }
    queued = {name: (block["awaiting_model_queued"], block["awaiting_model_not_queued"], block["not_queued_reason"], block["tagging"]["state"]) for name, block in blocks.items()}
    assert queued == {
        "backfill_off": (2, 3, "backfill_off", "running"),
        "backfill_on": (5, 0, None, "running"),
        "model_off": (0, 5, "model_off", "off"),
        "refresh_off": (0, 5, "refresh_off", "paused"),
        "no_thread": (0, 5, "no_refresh_thread", "inactive"),
        "manual_update": (2, 3, "backfill_off", "waiting_for_update"),
    }

    ui = _render({name: {"tags": block} for name, block in blocks.items()})

    assert ui["backfill_off"]["tags"].endswith("2 waiting for the model, 3 not tagged by a model (background tagging of the rest is off)")
    assert ui["backfill_on"]["tags"].endswith("1 a model could not place, 5 waiting for the model")
    assert ui["model_off"]["tags"].endswith("1 a model could not place, 5 not tagged by a model (tagging with the model is off)")
    assert ui["refresh_off"]["tags"].endswith("5 not tagged by a model (automatic updates are off)")
    assert ui["no_thread"]["tags"].endswith("5 not tagged by a model (this server runs no background tagging)")
    # Nothing says "waiting" for titles that cannot be tagged under the settings in effect.
    for name in ("model_off", "refresh_off", "no_thread"):
        assert "waiting" not in ui[name]["tags"] and "waiting" not in ui[name].get("tagging", "")
    assert ui["model_off"]["tagging"] == "Tagging: off (tagging with the model is off)"
    assert ui["refresh_off"]["tagging"] == "Tagging: paused (automatic updates are off)"
    assert "tagging" not in ui["no_thread"]
    assert ui["manual_update"]["tagging"] == "Tagging: 2 titles wait for this update to finish, then the model starts"


def test_a_failing_lane_is_the_tagging_state_with_its_reason(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setting = TaggingSetting(True, False, "configured", "setting")
    monkeypatch.setattr(sources_status, "tagging_setting", lambda *_args, **_kwargs: setting)
    target = home.parent / "project"
    queue = TagQueue(home_root=home, target=target, demand_loader=lambda _home, _target: Demand(("Director of Engineering",), None), setting=lambda: setting, live_update=lambda: False)
    ticker = RefreshTicker(home_root=home, target=target, tag_queue=queue)
    assert queue.drain().state == "backoff"  # no model target is configured for this project

    api = tags_block(home, target, ticker=ticker, environ={}, background={"auto_refresh": {"enabled": True}, "in_progress": False, "trigger": None})

    assert api["tagging"]["state"] == "failing" and api["tagging"]["detail"] == "no model target is configured for this project"
    assert api["tagging"]["retry_after"] is not None
    ui = _render({"failing": {"tags": api}})["failing"]
    assert ui["tags-error"] == "The model could not tag titles: no model target is configured for this project. It is tried again by itself."
    assert "tagging" not in ui  # the error line says it


def test_a_background_check_and_a_manual_update_are_labelled_for_what_they_are(tmp_path: Path) -> None:
    now = datetime(2026, 10, 1, 18, 10, tzinfo=timezone.utc)  # 12:10 in Denver
    schedule = {**CheckSchedule().to_json(), "source": "default", "checks_today": 8}
    background = {
        "auto_refresh": {"enabled": True}, "state": "waiting", "in_progress": False, "trigger": "auto",
        "last_update": {"finished_at": "2026-10-01T17:58:00.000Z", "started_at": "2026-10-01T17:46:00.000Z"},
        "next_tick_at": "2026-10-01T19:00:00.000Z",
    }
    idle = refresh_block(background, now=now, schedule=schedule)
    assert idle["schedule"] == schedule and idle["next_tick_in_minutes"] == 50 and idle["last_updated_minutes_ago"] == 12
    running = {**idle, "state": "running", "in_progress": True, "last_updated_at": None, "last_updated_minutes_ago": None, "next_tick_at": None, "next_tick_in_minutes": None}
    check = {"status": "running", "trigger": "auto", "boards": {"checked": 1204, "total": 3982}, "catch_up": {"tags": {"companies_done": 3840, "companies_total": 10370, "pending": True}, "text_index": "deferred"}}
    manual = {"status": "running", "trigger": "manual", "boards": {"checked": 1204, "total": 10360}, "catch_up": {"tags": {"companies_done": 10370, "companies_total": 10370, "pending": False}, "text_index": "building"}}

    ui = _render({
        "idle": {"refresh": idle, "update": {"status": "succeeded", "trigger": "auto"}},
        "check": {"refresh": {**running, "trigger": "auto"}, "update": check},
        "manual": {"refresh": {**running, "trigger": "manual"}, "update": manual},
        "off": {"refresh": {**idle, "enabled": False, "state": "disabled", "next_tick_at": None, "next_tick_in_minutes": None}},
        "older_server": {"refresh": {key: value for key, value in idle.items() if key != "schedule"}},
    })

    assert ui["idle"] == {"check": "Background check: 8 a day on weekdays, 2 on weekend days · next at 13:00 · last 11:58 (12 min ago)"}
    assert ui["check"]["check"] == (
        "Background check running: 1,204 of 3,982 boards checked (busy boards every check, quiet ones about twice a day), at a moderate pace on purpose"
    )
    assert ui["check"]["catch-up"] == "Tagging stored titles by the rules (once): 3,840 of 10,370 companies"
    assert ui["manual"]["check"] == "Update sources running at full speed: 1,204 of 10,360 boards checked"
    assert ui["manual"]["catch-up"] == "Building the keyword index from the stored descriptions (once; the boards are done)"
    assert ui["off"] == {"check": "Background check: off"}
    assert ui["older_server"] == {}

    # The schedule block is the server's: the default times, from a home with no settings file.
    assert schedule_status(tmp_path / "home", None, now=now)["weekdays"] == schedule["weekdays"]
