"""0.1.11.4 items 9 and 9d (packet H2): "Your master has no education" on the Master page and on the job's resume card; the Earlier experience block.

Real server and page on the small home (no model call, no network). The small home's persona HAS a school, so its
master has an Education entry and a resume picked from it prints one: that is the state in which nothing is said.
The states the small home cannot reach are served the way the other job-page flows serve them (the real answers,
fetched by the route, with ONE thing laid over them):

- the Master page: `GET /api/master` without its Education entry. The page says "Your master has no education." with
  the "Add education" button; the click opens the Education section's own form (the one "Add a school" opens), scrolls
  to it and puts the cursor in its first field. Nothing is written. With the real master, nothing is said;
- the job's stored resume: the small home's fixture model writes a resume of a few lines with no Experience section,
  so the resume the job page shows here is the real stored one with its `result.sections` laid over: every entry and
  line of the home's REAL master (`GET /api/master`), each citing its master id, in the stored shape;
- the job page's resume card: the same one line with a link to the Master page when the server says the master holds
  no education (`master_education: false` in the answer the page already loads) AND the stored resume prints none;
  not when the master has it, and not when the resume prints it;
- the job page's resume: a role with no line shown is ONE line under "Earlier experience", after the roles that show
  lines (the line the markdown and the PDF print), not a bare heading in its place; and the pick's conflict "earlier
  roles do not fit" reads as the pick's own sentence under "Needs attention".

The rules behind the page are pinned without a browser in `test_education_earlier_roles_model.py`; what the server
says in tests/behaviors (`test_master_education_report.py`, `test_pick_view_master_education.py`).

This module CHANGES the shared home when no flow before it did (a master, the hero job's stored resume), so it runs
late (`UI_ORDER`). It writes nothing through the page.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from gigai.scout.tailored_resume import EARLIER_HEADING, heading_only_line

from tests.ui.evidence import shot
from tests.ui.job_resume_fixtures import JobResumeFixture, fresh_resume, stored_resume
from tests.ui.support import tid
from tests.ui.test_master_page import ensure_master

pytestmark = pytest.mark.ui
UI_ORDER = 47  # may store a master and the hero job's resume on the shared home: after the Master page and the job-page flows that make them

MASTER_PAGE = '[data-role="master-page"]'
PANEL = f".job-page {tid('job-resume')}"
NOTICE = '[data-role="no-education"]'
EDUCATION = f'{MASTER_PAGE} [data-master-section="education"]'
MESSAGE = (
    "1 older role is not listed on this resume, not even by a single heading line: there was no room left on 2 pages "
    "beside the lines your must-have requirements and pins rest on"
)


def _without_education(route) -> None:
    if route.request.method != "GET":
        route.continue_()
        return
    body = route.fetch().json()
    master = body["master"]
    master["entries"] = [entry for entry in master["entries"] if entry["section"] != "education"]
    master["items"] = [item for item in master["items"] if item["section"] != "education"]
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def test_the_master_page_says_no_education_and_add_education_opens_the_school_form(ui) -> None:
    served = ensure_master(ui)["master"]
    assert [entry for entry in served["entries"] if entry["section"] == "education"], "the small home's master has a school"

    # --- the master as it is: it has its education, nothing is said ---
    ui.goto("/#/master")
    ui.page.locator(f'{MASTER_PAGE}[data-master-revision="{served["revision"]}"]').wait_for()
    assert ui.page.locator(f"{EDUCATION} [data-entry-id]").count() >= 1
    assert ui.page.locator(f"{MASTER_PAGE} {NOTICE}").count() == 0, "a master with a school is never nagged"

    # --- a master with no Education entry ---
    ui.settle()
    ui.page.goto("about:blank")
    ui.page.route("**/api/master", _without_education)
    try:
        ui.goto("/#/master")
        notice = ui.page.locator(f"{MASTER_PAGE} {NOTICE}")
        notice.wait_for()
        ui.step("said")
        assert " ".join((notice.text_content() or "").split()) == "Your master has no education. A resume picked from it prints no degree. Add education"
        button = notice.locator('[data-action="add-education"]')
        assert (button.text_content() or "").strip() == "Add education"
        assert ui.page.locator(f"{EDUCATION} [data-entry-id]").count() == 0 and ui.page.locator(f"{EDUCATION} form").count() == 0
        shot(ui, "master-no-education")

        button.click()
        field = ui.page.locator(f'{EDUCATION} form input[aria-label="Employer, project or school"]')
        field.wait_for()
        # The cursor is in the school field of the Education section, and the field is on the screen.
        ui.page.wait_for_function("(selector) => document.activeElement === document.querySelector(selector)", arg=f'{EDUCATION} form input')
        assert field.get_attribute("placeholder") == "School"
        assert ui.page.locator(f"{EDUCATION} form textarea").get_attribute("placeholder") == "Degree | Year"
        box, height = field.bounding_box(), ui.page.evaluate("() => window.innerHeight")
        assert box is not None and 0 <= box["y"] and box["y"] + box["height"] <= height, (box, height)
        # What is typed goes into that field (the page did not move the cursor elsewhere).
        ui.page.keyboard.type("Lakeside University")
        assert field.input_value() == "Lakeside University"
        shot(ui, "master-add-education-form")
        ui.settle()
        assert ui.writes_after("start") == [], "saying it and opening the form writes nothing"
    finally:
        ui.page.unroute_all()
    ui.assert_clean()


def _resume(ui, demo) -> dict:
    """The hero job's stored resume, made from the master (made again when what is stored was not: a subset run)."""

    ensure_master(ui)
    stored = stored_resume(ui, demo.hero_profile_id, demo.hero_job)
    if stored is None or not (stored.get("sources") or {}).get("master"):
        stored = fresh_resume(ui, demo.hero_profile_id, demo.hero_job)
    return stored


def _copy(text: str, item_id: str, line: int) -> dict:
    return {"kind": "copy", "text": text, "refs": [{"kind": "resume", "line": line, "text": text, "item_id": item_id}]}


def _sections(master: dict, *, education: bool = True, heading_only: str | None = None) -> list[dict]:
    """The whole master as a stored resume's sections (every heading and line a copy that cites its master id).

    ``education=False`` leaves the Education section out; ``heading_only`` names a role that keeps its heading lines
    and no bullet: what the pick stores for a role none of whose lines is shown (0.1.11.4 item 9)."""

    items = {item["id"]: item for item in master["items"]}
    out, line = [], 0
    for name in ("summary", "experience", "skills", "education", "projects", "other"):
        if name == "education" and not education:
            continue
        section: dict = {"heading": name, "lines": [], "entries": []}
        for entry in (entry for entry in master["entries"] if entry["section"] == name):
            heading = []
            for text in (entry["heading"], *entry["sublines"]):
                line += 1
                heading.append(_copy(text, entry["id"], line))
            bullets = []
            for item_id in ([] if entry["id"] == heading_only else entry["bullets"]):
                line += 1
                bullets.append(_copy(items[item_id]["text"], item_id, line))
            section["entries"].append({"heading": heading, "bullets": bullets})
        for item in (item for item in master["items"] if item["section"] == name and item["entry_id"] is None):
            line += 1
            section["lines"].append(_copy(item["text"], item["id"], line))
        if section["lines"] or section["entries"]:
            out.append(section)
    return out


def _fixture(ui, demo) -> JobResumeFixture:
    stored = _resume(ui, demo)
    fixture = JobResumeFixture(ui, profile_id=demo.hero_profile_id, job_identity=demo.hero_job)
    fixture.master = {"revision_id": "rev-fixture", "revision": stored["sources"]["master"]["revision"], "content_sha256": "sha256:" + "0" * 64}
    return fixture


def _open(ui, demo) -> None:
    ui.goto("/#/jobs/" + quote(demo.hero_job, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(f'{PANEL}[data-state="stored"]').wait_for()
    ui.page.locator(f"{PANEL} .md-preview, {PANEL} .clean-wrap").first.wait_for()


@pytest.mark.parametrize(
    ("master_education", "resume_prints_it", "said"),
    [(False, False, True), (True, False, False), (False, True, False), (None, False, False)],
    ids=["neither has it", "the master has it", "the resume prints it", "the server does not say"],
)
def test_the_resume_card_says_no_education_only_when_neither_the_master_nor_the_resume_has_it(
    ui, scout_server, master_education: bool | None, resume_prints_it: bool, said: bool,
) -> None:
    demo = scout_server.demo
    fixture = _fixture(ui, demo)
    fixture.master_education = master_education
    master = ui.server_json("/api/master")["master"]
    assert [entry for entry in master["entries"] if entry["section"] == "education"], "the small home's master has a school"
    sections = _sections(master, education=resume_prints_it)
    fixture.lay_over = lambda item: item["result"].update({"sections": sections})
    fixture.install()
    try:
        _open(ui, demo)
        ui.settle()
        text = ui.page.locator(PANEL).inner_text()
        headings = [(heading or "").strip().lower() for heading in ui.page.locator(f"{PANEL} .clean-resume h4.clean-section").all_text_contents()]
        assert ("education" in headings) is resume_prints_it and "experience" in headings, headings
        notice = ui.page.locator(f"{PANEL} {NOTICE}")
        assert notice.count() == (1 if said else 0), text
        if said:
            assert " ".join((notice.text_content() or "").split()) == "Your master has no education. This resume prints none: add it on the Master page."
            link = notice.locator("a")
            assert (link.get_attribute("href"), (link.text_content() or "").strip()) == ("#/master", "add it on the Master page")
            shot(ui, "job-resume-no-education")
        assert ui.requests_after("start", "/api/master") == 0, "the notice needs no read of the master"
        assert ui.writes_after("start") == []
    finally:
        ui.page.unroute_all()
    ui.assert_clean()


def test_a_role_with_no_line_shown_is_listed_under_earlier_experience_and_the_conflict_is_a_sentence(ui, scout_server) -> None:
    from gigai.scout import pick

    demo = scout_server.demo
    fixture = _fixture(ui, demo)
    master = ui.server_json("/api/master")["master"]
    roles = [entry for entry in master["entries"] if entry["section"] == "experience"]
    assert len(roles) >= 2 and all(entry["bullets"] and entry["sublines"] for entry in roles), "the small home's master has two roles with lines and dates"
    old = roles[-1]
    employer = old["heading"]
    one_line = heading_only_line([old["heading"], *old["sublines"]])
    assert one_line and employer in one_line and one_line != employer
    sections = _sections(master, heading_only=old["id"])
    fixture.lay_over = lambda item: item["result"].update({"sections": sections})
    fixture.conflicts = [pick.PickConflict(pick.CONFLICT_HEADINGS, message=MESSAGE).to_json()]
    fixture.install()
    try:
        _open(ui, demo)
        panel = ui.page.locator(PANEL)
        # --- the conflict: its own plain sentence, never the code's words ---
        item = panel.locator('[data-role="needs-attention"] li[data-code="earlier_roles_do_not_fit"]')
        item.wait_for()
        assert " ".join((item.text_content() or "").split()) == MESSAGE
        assert "earlier roles do not fit" not in panel.inner_text()

        # --- the block, in both views of the preview: one heading, one line for the role, after the roles that show lines ---
        for view in ("clean", "changes"):
            ui.page.click(f'{PANEL} [data-action="view-{view}"]')
            heading = panel.locator('[data-role="earlier-heading"]')
            heading.wait_for()
            assert heading.count() == 1 and EARLIER_HEADING in (heading.text_content() or "")
            roles = panel.locator('[data-role="earlier-role"]')
            assert roles.count() == 1 and one_line in " ".join((roles.first.text_content() or "").split())
            lines = [" ".join(text.split()) for text in panel.locator(".clean-resume > *, .md-preview .md-line").all_text_contents()]
            lines = [text.lstrip("·R ").strip() for text in lines if text.strip(" · ")]
            at = next(place for place, text in enumerate(lines) if text.removeprefix("### ") == EARLIER_HEADING)
            assert lines[at + 1] == one_line, lines[at:at + 3]
            # The role is not ALSO printed as a bare heading in its place.
            assert not [text for text in lines if text.removeprefix("### ") == employer], lines
            # The block closes Experience: the next thing is the next section.
            first_role = next(place for place, text in enumerate(lines) if text.removeprefix("## ").lower() == "experience")
            assert first_role < at and lines[at + 2].removeprefix("## ").lower() in {"skills", "education", "projects", "other"}, lines[at:at + 4]
            shot(ui, f"job-resume-earlier-experience-{view}")
        ui.settle()
        assert ui.writes_after("start") == []
    finally:
        ui.page.unroute_all()
    ui.assert_clean()
