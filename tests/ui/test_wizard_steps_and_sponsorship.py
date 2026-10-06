"""0.1.11.2 UAT-007 and UAT-008: the setup wizard (opened by Settings -> Edit preferences).

Pinned: four steps (Resume, Resume display, Target, Review), no Companies step; the Target step's sponsorship choice
reads "Yes" / "No" with the help line that sponsorship only labels (no "hard filter" anywhere on the page); the
company lists and add-by-URL are on Settings, outside onboarding, and a save there sends the saved preferences
back with the two lists replaced.
"""

from __future__ import annotations

import pytest

from tests.ui.evidence import shot

pytestmark = pytest.mark.ui
STEPS = '.wz-steps .wz-step-label'


def next_step(ui) -> None:
    ui.page.get_by_role("button", name="Next").click()


def test_the_wizard_has_four_steps_and_the_sponsorship_choice_says_it_only_labels(ui) -> None:
    ui.goto("/#/settings")
    ui.page.get_by_role("button", name="Edit preferences").click()
    ui.page.locator(".wz-steps").wait_for()
    ui.settle()
    assert ui.page.locator(STEPS).all_text_contents() == ["Resume", "Resume display", "Target", "Review"]
    next_step(ui)  # 2
    next_step(ui)  # 3: Target
    help_line = ui.page.locator('[data-role="sponsorship-help"]')
    help_line.wait_for()
    toggles = ui.page.locator('[aria-label="Visa sponsorship"] button').all_text_contents()
    assert [text.strip() for text in toggles] == ["Yes", "No"]
    assert (help_line.text_content() or "").strip() == "Postings are labelled when they say they do not sponsor; nothing is filtered out."
    assert "hard filter" not in (ui.page.locator("body").text_content() or "").lower()
    shot(ui, "wizard-target-sponsorship")
    next_step(ui)  # 4: Review, the last step
    ui.page.get_by_role("button", name="Finish").wait_for()
    assert ui.page.locator(".wz-step.active .wz-step-label").text_content() == "Review"
    body = (ui.page.locator("body").text_content() or "").lower()
    assert "hard filter" not in body and "exclude companies" not in body
    shot(ui, "wizard-review-four-steps")
    ui.page.get_by_role("button", name="Back", exact=True).click()
    ui.page.get_by_role("button", name="Back", exact=True).click()
    ui.page.get_by_role("button", name="Back", exact=True).click()
    ui.page.get_by_role("button", name="Cancel").click()


def test_the_company_lists_and_add_by_url_stay_on_settings(ui) -> None:
    ui.goto("/#/settings")
    panel = ui.page.locator('[data-role="company-lists"]')
    panel.wait_for()
    ui.settle()
    assert ui.page.locator("#settings-add-company").count() == 1
    box = panel.locator("#settings-exclude")
    box.fill("Acme Synthetic Corp")
    box.press("Enter")
    with ui.page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/api/setup")) as saved:
        panel.locator('[data-action="save-company-lists"]').click()
    assert saved.value.status == 200, saved.value.text()
    assert saved.value.request.post_data_json["exclude_companies"] == ["Acme Synthetic Corp"]
    panel.locator('[data-role="company-lists-saved"]').wait_for()
    shot(ui, "settings-companies")
    assert ui.server_json("/api/setup")["prefs"]["exclude_companies"] == ["Acme Synthetic Corp"]
