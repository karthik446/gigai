"""Flow 10 (REPORT.md 5.3): delete a profile. The LAST flow on the small home: it changes it for good.

Real server, nothing stubbed. Settings > Profiles shows the selected profile's detail with Delete under it; the
default profile cannot be deleted, and any other is deleted only after a confirmation that says what happens.

Pinned: with the default profile selected, Delete says why not and offers no confirmation, and nothing is sent; the
other profile is selected in the top bar (one request); its Delete shows the confirmation, which names the profile
and the one Scout switches to, and Cancel sends nothing; "Delete profile" is ONE `DELETE /api/profiles/<id>`; then
the profile is gone from the switcher and the list, the default one is selected (here and on the server), and the
Jobs page no longer offers it as a filter or tags a row with it. Zero console errors.

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): the delete 0.45 to 0.46 s wall until
the switcher is back without the profile (the delete, then the profiles read again).
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from tests.ui.support import INTERACTIVE_WALL_SECONDS, tid

pytestmark = pytest.mark.ui
UI_ORDER = 90  # removes a profile from the shared home: after every other flow

DELETE_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
SWITCHER = 'select[aria-label="Profile"]'
PROFILES = "#settings-profiles"
DELETE = f'{PROFILES} [data-action="delete-profile"]'
CONFIRM = f'{PROFILES} [data-action="confirm-delete-profile"]'
CANCEL = f'{PROFILES} [data-action="cancel-delete-profile"]'


def active(ui) -> tuple[list[dict], str]:
    body = ui.server_json("/api/profiles")
    return [item for item in body["profiles"] if item.get("state") not in ("archived", "deleted")], body["selected_profile_id"]


def switcher_profiles(ui) -> list[str]:
    return [value for value in ui.page.locator(f"{SWITCHER} option").evaluate_all("(options) => options.map((option) => option.value)") if value.startswith("profile_")]


def test_delete_a_profile(ui) -> None:
    profiles, selected = active(ui)
    default = next(item for item in profiles if item["is_default"])
    other = next(item for item in profiles if not item["is_default"])
    assert len(profiles) == 2 and selected == default["profile_id"], "the small home has the default profile selected and one more"

    ui.goto("/#/settings")
    ui.page.locator(DELETE).wait_for()
    assert sorted(switcher_profiles(ui)) == sorted(item["profile_id"] for item in profiles)

    # The default profile cannot be deleted: the page says why, asks nothing and sends nothing.
    ui.page.click(DELETE)
    assert "The default profile uses the setup settings and cannot be deleted." in (ui.page.locator(PROFILES).text_content() or "")
    assert ui.page.locator(CONFIRM).count() == 0
    assert ui.writes_after("start") == []

    # Select the other profile in the top bar.
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path.startswith("/api/profiles")):
        ui.page.select_option(SWITCHER, other["profile_id"])
    ui.page.wait_for_function("([selector, id]) => (document.querySelector(selector) || {}).value === id", arg=[SWITCHER, other["profile_id"]])
    ui.settle()
    assert active(ui)[1] == other["profile_id"]
    ui.step("selected")

    # Delete asks first, and says what happens; Cancel sends nothing.
    ui.page.click(DELETE)
    ui.page.locator(CONFIRM).wait_for()
    said = ui.page.locator(f"{PROFILES} .callout.danger").first.text_content() or ""
    assert f'Delete "{other["label"]}"?' in said and "Your story bank and answers are not touched." in said
    assert f'Scout switches to "{default["label"]}".' in said
    ui.page.click(CANCEL)
    ui.page.locator(CONFIRM).wait_for(state="detached")
    assert ui.writes_after("selected") == [] and len(active(ui)[0]) == 2

    # Delete profile: one request, and the profile is gone everywhere.
    ui.page.click(DELETE)
    ui.step("before-delete")
    with ui.page.expect_response(lambda response: response.request.method == "DELETE") as deleted:
        ui.page.click(CONFIRM)
    # (the switcher leaves the page while the profiles are read again: wait for it to be back, on the default profile)
    ui.page.wait_for_function(
        """([selector, gone, selected]) => {
          const switcher = document.querySelector(selector);
          return !!switcher && switcher.value === selected && !Array.from(switcher.options).some((option) => option.value === gone);
        }""",
        arg=[SWITCHER, other["profile_id"], default["profile_id"]],
    )
    ui.step("deleted")
    assert urlsplit(deleted.value.url).path == f"/api/profiles/{other['profile_id']}" and deleted.value.status == 200
    assert ui.writes_after("before-delete") == [f"DELETE /api/profiles/{other['profile_id']}"]
    assert switcher_profiles(ui) == [default["profile_id"]]
    assert ui.page.locator(f"{PROFILES} .profile-list .action-item").count() == 1
    left, now_selected = active(ui)
    assert [item["profile_id"] for item in left] == [default["profile_id"]] and now_selected == default["profile_id"]
    ui.wall_budget("delete a profile", DELETE_WALL_SECONDS, "before-delete", "deleted")

    # Jobs: the deleted profile is no filter and no tag any more.
    ui.settle()
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    chips = ui.page.locator('[data-role="profile-filter-chip"]').all_text_contents()
    assert len(chips) == 1 and chips[0].startswith(default["label"])
    tags = set(ui.page.locator(f"{tid('job-row')} {tid('profile-chip')}").all_text_contents())
    assert tags == {default["label"]}, f"a row is still tagged with the deleted profile: {tags}"
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests
