"""Flow 8 (REPORT.md 5.3): Settings > Background updates, and the Background pipeline panel beside it.

Real server, nothing stubbed. The server of the small home runs with the three background jobs switched off by its
environment (`GIGAI_SCOUT_AUTO_REFRESH=0`, `GIGAI_SCOUT_SNAPSHOT=0`, `GIGAI_SCOUT_MODEL_TAGS=0`), so the panel must
name each override beside the switch it decides, and saving a switch here changes the settings file and starts
nothing. The test puts the one switch it saves back, and ends by comparing the settings with what it found.

Pinned: the panel shows the saved settings and Save is off until something changed; a bad time of day is refused in
the page (an error line, Save off, nothing sent) and "Use the default times" clears it; Save is ONE request that
carries only what changed, the page says "Saved.", and a reload shows the saved value; the Background pipeline
panel shows the server's status line and exactly the approvals that wait, and nothing is approved by looking.

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): Settings 0.6 to 1.1 s wall to both
panels from a new page, 0.52 to 0.59 server CPU seconds, 17 requests; a save 0.23 s.
"""

from __future__ import annotations

import pytest

from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui

SETTINGS = "/api/settings/background"
PANEL = '[data-role="background-settings"]'
SAVE = '[data-action="save-background-settings"]'
SNAPSHOT = "#background-snapshot"
WEEKDAYS = "#background-check-times-weekdays"
SETTINGS_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
SETTINGS_CPU_SECONDS = 4.0  # 0.52 to 0.59 measured idle, up to 0.90 with every core busy
SAVE_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
SETTINGS_REQUESTS = 36  # 17 measured for the whole page (the app's own 7 and the panels' 10)


def open_settings(ui) -> None:
    ui.goto("/#/settings")
    ui.page.locator(f"{PANEL} {SNAPSHOT}").wait_for()
    ui.page.locator(f"{tid('background-panel')} [data-role='pipeline-status']").wait_for()


def save(ui, before: str, after: str) -> dict:
    ui.step(before)
    with ui.page.expect_request(lambda request: request.method == "PUT" and request.url.endswith(SETTINGS)) as sent:
        ui.page.click(f"{PANEL} {SAVE}")
    ui.page.locator(f'{PANEL} [data-role="background-saved"]').wait_for()
    ui.step(after)
    assert ui.writes_after(before) == [f"PUT {SETTINGS}"]
    ui.wall_budget(f"save background settings ({after})", SAVE_WALL_SECONDS, before, after)
    return sent.value.post_data_json


def test_background_updates_are_shown_saved_and_put_back(ui) -> None:
    found = ui.server_json(SETTINGS)
    pipeline = ui.server_json("/api/pipeline")

    open_settings(ui)
    ui.step("shown")
    panel = ui.page.locator(PANEL)
    snapshot = panel.locator(SNAPSHOT)
    was_on = snapshot.is_checked()

    # What the environment decides is said beside each switch; nothing changed yet, so Save is off.
    for role in ("override-sources", "override-tagging", "override-snapshot"):
        assert (panel.locator(f'[data-role="{role}"]').text_content() or "").strip(), f"no override note ({role}) although the server's environment decides it"
    assert panel.locator(SAVE).is_disabled()

    # A bad time of day is refused here, before anything is sent.
    weekdays = panel.locator(WEEKDAYS)
    good = weekdays.input_value()
    weekdays.fill("25:99")
    assert (panel.locator('[data-role="check-times-error"]').text_content() or "").strip()
    assert panel.locator(SAVE).is_disabled()
    panel.locator('[data-action="check-times-default"]').click()
    assert panel.locator('[data-role="check-times-error"]').count() == 0
    weekdays.fill(good)
    assert panel.locator(SAVE).is_disabled(), "the form is as it was found, so there is nothing to save"
    assert ui.writes_after("start") == []

    # One switch, saved: one request with only what changed, and a reload shows it.
    snapshot.set_checked(not was_on)
    sent = save(ui, "before-save", "saved")
    assert list(sent) == ["snapshot"] and list(sent["snapshot"].values()) == [not was_on], sent
    ui.reload()
    open_settings(ui)
    assert ui.page.locator(f"{PANEL} {SNAPSHOT}").is_checked() == (not was_on), "the saved switch did not survive a reload"
    assert ui.page.locator(f"{PANEL} {SAVE}").is_disabled()

    # Put back, the same way.
    ui.page.locator(f"{PANEL} {SNAPSHOT}").set_checked(was_on)
    save(ui, "before-restore", "restored")
    assert ui.server_json(SETTINGS) == found, "the settings are not what the test found"

    # The Background pipeline panel: the server's own state, and the approvals that wait (looked at, not decided).
    pipeline_panel = ui.page.locator(tid("background-panel"))
    assert (pipeline_panel.locator('[data-role="pipeline-status"]').text_content() or "").strip()
    assert pipeline_panel.locator('[data-role="pipeline-caps"] [data-cap]').count() >= 2
    pending = [item for item in pipeline["approvals"]["items"] if item["state"] == "pending"]
    assert pipeline_panel.locator(f"{tid('approvals-list')} [data-role='approval']").count() == len(pending)
    ui.settle()
    assert ui.requests_after("start", "/api/pipeline/approvals") == 0
    assert ui.server_json("/api/pipeline")["approvals"]["pending"] == pipeline["approvals"]["pending"]

    ui.cpu_budget("Settings, both background panels (small home)", SETTINGS_CPU_SECONDS, "start", "shown")
    ui.wall_budget("Settings, both background panels (small home)", SETTINGS_WALL_SECONDS, "start", "shown")
    assert ui.requests_between("start", "shown") <= SETTINGS_REQUESTS
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
