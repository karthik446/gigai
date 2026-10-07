"""0.1.11.5 (b): the job page lists the POINTS of the job's resume beside its preview: change one's words, take one
off, add one. Each change is saved at once and the preview follows.

Real Chromium against a REAL server of its own (the product's own handler, served from this process on a free
port), nothing stubbed, on a synthetic home: the pick fixture of
`tests/behaviors/scout_find_jobs/test_pick_header_room.py` (a job assessed against an invented 56-line master; its
resume picked by the assessment). SINCE 0.1.11.5 (item 1c) A PICK COUNTS NO PAGE AND IS NEVER CUT FOR LENGTH, so the
stored resume is put in the shape a pick BEFORE 0.1.11.5 stored (`tests/support/old_pick_fixture.py`: twelve of its bullets
shown, the others cut for length on its length record, with Restore): that is the only resume Restore still shows
for, and the re-check below is about it. No page count is written into this test: the BASELINE is what the preview
says at the spacing the person left (read again after an edit, whose shorter words can end the resume a page
earlier), and every count is that baseline, or more pages after Restore and after enough Adds. The shared small home
is not used: its resume is half a page and has nothing cut for length, so neither the page count nor Restore can move.
ONE answer is the test's: `GET /api/setup`. The fixture home was never through the setup interview, so the app would
open the interview in place of the job page; the route's own pre-fill is answered as the saved preferences. Every
route this flow is about (the stored resume, its lines, its selection, its length, the preview, the PDF, the master)
is the real server's.

Pinned, on what the page shows WITHOUT A RELOAD and on the PDF the browser downloads:

- ANY CHANGE OF THE LINES RE-RENDERS THE PREVIEW AT ONCE (the orchestrator's re-check of part (a): after Restore the
  preview kept the old page count): Restore asks for the preview once, the pages on screen are new pictures and the
  count says what the PDF will have; "Cut for length again" brings it back;
- PICKED / LEFT OUT FOLLOW THE STORED RESUME (the same re-check: the buttons stayed at the pick's numbers): after
  Restore, after a Remove and after an Add the two counts are what the resume prints;
- EDIT, by keyboard: type in a point's box, Enter saves it ("Saved for this job. Your master is unchanged."); Tab
  goes to the edited point's "Save this wording to my master" (0.1.11.5 (b++)), then to its Remove, then to the next
  point's box, and leaving a box saves it too; each is ONE request and the preview shows the new words;
- REMOVE takes the point off (the list, the preview and the counts follow); under "Add a point" it is first in its
  role, as "Put back";
- ADD: the lines left out, by role, with a search; one click puts a line on, under its role; enough of them and the
  preview says one page more in plain words, with the slider where the person left it;
- a reload shows the saved state, and the downloaded PDF is the preview: the edited words, the added lines, not the
  removed one, as many pages;
- the master's files are byte for byte what they were; the job's saved spacing is the slider's; no id is shown.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

import pytest

from gigai.scout.resume_pdf import job_layout_path
from gigai.scout.tailored_resume import tailored_resume_path

from tests.behaviors.scout_find_jobs.test_pick_header_room import _JOB, _Server, _assess, _master, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.support.old_pick_fixture import stored_as_before_0_1_11_5
from tests.ui import support
from tests.ui.conftest import _ui_session

pytestmark = pytest.mark.ui
UI_ORDER = 61  # a server and a home of its own: nothing of the shared home is read or written

PANEL = "#job-resume"
PREVIEW = f'{PANEL} [data-testid="resume-preview"]'
POINTS = f'{PANEL} [data-testid="resume-points"]'
POINT = f'{POINTS} [data-role="point"]'
CHOICE = f'{POINTS} [data-role="choice"]'
STATUS = f'{POINTS} [data-role="points-status"]'
COUNT = f'{PREVIEW} [data-role="preview-pages"]'
SLIDER = f'{PREVIEW} [data-role="preview-spacing"]'
VIEW = f'{PANEL} [data-testid="picked-left-out"]'
FORM = f'{PANEL} [data-role="generate-pdf-form"]'
PREVIEW_ROUTE = "/api/tailored-resumes/preview"
LINES = "PUT /api/tailored-resumes/lines"
SELECTION = "PUT /api/tailored-resumes/selection"
SAVED = "Saved for this job. Your master is unchanged."
FIRST = "Rebuilt the night-shift roster engine so a schedule change reaches every clinic in under a minute."
SECOND = "Cut the weekly payroll export from four hours to nine minutes."
NAME = "Zephyrine Quillfeather"
_ID = re.compile(r"\bb-[0-9a-f]{6}\b|\bsum-[0-9a-f]{6}\b|\bL\d+\b|req-[0-9a-f]+")
#: Set GIGAI_UI_POINTS_SHOT to a folder to keep pictures of the panel (hand check only).
SHOT = os.environ.get("GIGAI_UI_POINTS_SHOT")


@pytest.fixture(autouse=True)
def _scratch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME is a folder of this test: nothing of the person's is read or written."""

    home = support.refuse_real_home(tmp_path / "home")
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def page(request: pytest.FixtureRequest, ui_browser, ui_artifacts: Path, fx, server: _Server, tmp_path: Path):  # noqa: ANN001, F811
    """`ui` on this test's own server: the pick fixture's job, assessed, its resume picked from the master."""

    _assess(fx)
    stored_as_before_0_1_11_5(fx, _JOB)
    log = tmp_path / "server.log"
    log.write_text("", encoding="utf-8")
    own = SimpleNamespace(url=f"http://127.0.0.1:{server.server.server_address[1]}", pid=os.getpid(), log_path=str(log))
    prefill = server.client.get("/api/setup").json()["error"].get("prefill") or {}
    with _ui_session(request, ui_browser, own, ui_artifacts) as session:
        session.fx, session.api = fx, server
        session.page.route("**/api/setup", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"prefs": prefill})))
        yield session


def _count(pages: int) -> str:
    return f"{pages} page{'' if pages == 1 else 's'}"


def _ready(ui, *, pages: int | None = None) -> None:
    ui.page.locator(f'{PREVIEW}[data-state="ready"]' + (f'[data-pages="{pages}"]' if pages is not None else "")).wait_for()
    ui.settle()


def _open(ui) -> None:
    ui.goto("/#/jobs/" + quote(_JOB, safe=""))
    ui.page.locator(f'{PANEL}[data-state="stored"]').wait_for()
    assert ui.page.locator(f'{PANEL} [data-action="view-clean"]').get_attribute("aria-pressed") == "true", "the job page did not open on Preview"
    _ready(ui)


def _shown(ui) -> dict:
    preview = ui.page.locator(PREVIEW)
    return {
        "pages": int(preview.get_attribute("data-pages")),
        "count": (ui.page.locator(f"{COUNT} strong").text_content() or "").strip(),
        "over": ui.page.locator(COUNT).get_attribute("data-over"),
        "spacing": preview.get_attribute("data-spacing"),
        "pictures": ui.page.locator(f'{PREVIEW} [data-role="preview-page"]').evaluate_all("(images) => images.map((image) => image.src.length + ':' + image.src.slice(-64))"),
        "text": " ".join((preview.locator(".visually-hidden").text_content() or "").split()),
        "points": int(ui.page.locator(POINTS).get_attribute("data-points")),
        "tabs": [(ui.page.locator(f'{VIEW} [data-action="show-{name}"]').text_content() or "").strip() for name in ("picked", "left-out")],
    }


def _held(ui) -> dict:
    return ui.api.client.get("/api/tailored-resumes", params=ui.api.key).json()["items"][0]


def _printed(held: dict) -> int:
    """The master lines a stored resume prints: its Summary and its bullets."""

    return sum(len(section.get("lines", ())) for section in held["result"]["sections"] if section["heading"] == "summary") + sum(
        len(entry["bullets"]) for section in held["result"]["sections"] for entry in section.get("entries", ())
    )


def _tabs(held: dict) -> list[str]:
    total = len(held["selection"]["picked"]) + len(held["selection"]["left_out"])
    return [f"Picked ({_printed(held)})", f"Left out ({total - _printed(held)})"]


def _saved(ui) -> None:
    ui.page.locator(f'{POINTS}[data-state="saved"]').wait_for()
    assert (ui.page.locator(STATUS).text_content() or "").strip() == SAVED


def _files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file() and "master" in str(path.relative_to(root))}


def _shot(ui, name: str) -> None:
    if SHOT:
        Path(SHOT).mkdir(parents=True, exist_ok=True)
        ui.page.locator(PANEL).screenshot(path=str(Path(SHOT) / f"{name}.png"))


def _pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [" ".join(page.extract_text().split()) for page in PdfReader(io.BytesIO(path.read_bytes())).pages]


def test_a_point_is_edited_removed_and_added_beside_the_preview_and_the_preview_follows_every_change(page) -> None:  # noqa: ANN001
    ui = page
    fixture = ui.fx
    stored_file = tailored_resume_path(fixture.home_root, fixture.target, fixture.default_profile_id, _JOB)
    layout = job_layout_path(stored_file)
    masters = _files(fixture.home_root)
    assert masters, "the home holds a master"
    master = {item.id: item for item in _master(fixture).items.values()}
    first = _held(ui)
    assert first["selection"] and first["result"]["length"]["status"] == "cut"

    # --- the panel opens on the preview, with the list of points beside it ---
    ui.step("open")
    _open(ui)
    opened = _shown(ui)
    assert opened["count"] == _count(opened["pages"]), opened
    assert opened["points"] == _printed(first) == ui.page.locator(POINT).count() and opened["tabs"] == _tabs(first), opened
    assert ui.requests_after("open", PREVIEW_ROUTE) == 1 and ui.writes_after("open") == [f"POST {PREVIEW_ROUTE}"], "opening the page wrote something"
    assert ui.requests_after("open", "/api/master") == 0, "the master is read when 'Add a point' is opened or a point is edited, not before"
    _shot(ui, "1-opened")

    # --- the slider is left at 0.85: saved for the job, and the count below is at that spacing ---
    # (0.90 until 0.1.11.5 (d): the Skills as plain lines and a degree on one line then put the restored resume on
    # the cut one's pages there, and Restore must move the count.)
    ui.page.locator(SLIDER).focus()
    ui.page.keyboard.press("Home")
    for _ in range(3):  # 0.70 -> 0.85
        ui.page.keyboard.press("ArrowRight")
    ui.page.locator(f'{PREVIEW}[data-state="ready"][data-spacing="0.85"]').wait_for()
    ui.settle()
    assert json.loads(layout.read_text(encoding="utf-8")) == {"spacing_percent": 85}
    spacing = layout.read_bytes()
    # THE BASELINE: the pages of the resume as it is stored, at the spacing the person left. The pick counted none.
    base = _shown(ui)["pages"]
    assert base >= 2 and _shown(ui)["count"] == _count(base), "the fixture's resume must fill pages, or no count below proves anything"

    # --- RESTORE (not a button of the list): the preview is made again at once, and the counts follow the resume ---
    before = _shown(ui)
    ui.step("restore")
    ui.page.locator(f'{PANEL} [data-action="length-restore"]').click()
    ui.page.locator(f'{PREVIEW}[data-state="ready"]:not([data-pages="{base}"])').wait_for()
    ui.settle()
    restored, held = _shown(ui), _held(ui)
    assert held["result"]["length"]["status"] == "restored" and _printed(held) > _printed(first)
    assert restored["pages"] > base and restored["count"] == _count(restored["pages"]) and restored["over"] == "true", f"after Restore the preview still says {restored['count']}"
    assert restored["pictures"] != before["pictures"] and len(restored["pictures"]) == restored["pages"], "the pages on screen are the old render"
    assert restored["tabs"] == _tabs(held) != before["tabs"], f"after Restore the buttons say {restored['tabs']}, the resume prints {_printed(held)} lines"
    assert restored["points"] == _printed(held)
    assert ui.requests_after("restore", PREVIEW_ROUTE) == 1 and ui.writes_after("restore") == ["PUT /api/tailored-resumes/length", f"POST {PREVIEW_ROUTE}"]
    ui.step("cut-again")
    ui.page.locator(f'{PANEL} [data-action="length-cut"]').click()
    _ready(ui, pages=base)
    again = _shown(ui)
    assert (again["tabs"], again["points"], again["count"]) == (opened["tabs"], opened["points"], _count(base)) and ui.requests_after("cut-again", PREVIEW_ROUTE) == 1

    # --- EDIT by keyboard: Enter saves; Tab goes to Remove, then to the next point; leaving a box saves it too ---
    boxes = ui.page.locator(f'{POINT} [data-role="point-text"]')
    role_points = ui.page.locator(f'{POINTS} [data-role="point-group"]').nth(1).locator('[data-role="point"]')
    one, two = role_points.nth(0), role_points.nth(1)
    was_one, was_two = one.locator("textarea").input_value(), two.locator("textarea").input_value()
    assert was_one in again["text"] and (one.locator("textarea").get_attribute("aria-label") or "").startswith("Point 1 of ")
    ui.step("edit")
    one.locator("textarea").focus()
    ui.page.keyboard.press("ControlOrMeta+A")
    ui.page.keyboard.type(FIRST)
    ui.page.keyboard.press("Enter")
    _saved(ui)
    ui.page.wait_for_function("([selector, words]) => (document.querySelector(selector)?.textContent || '').includes(words)", arg=[f"{PREVIEW} .visually-hidden", FIRST])
    _ready(ui)
    edited = _shown(ui)
    # (The new words are far shorter than the line they replace: the resume may end a page earlier, never later.)
    assert edited["pages"] in (base, base - 1) and edited["count"] == _count(edited["pages"])
    assert FIRST in edited["text"] and was_one not in edited["text"] and edited["pictures"] != again["pictures"], "the preview does not show the edit"
    assert one.get_attribute("data-edited") == "true" and one.locator("textarea").input_value() == FIRST
    assert ui.writes_after("edit") == [LINES, f"POST {PREVIEW_ROUTE}"], "Enter then nothing else: one write, one preview"
    # 0.1.11.5 (b++): with an edited point the master is read, once (does the point's wording differ from the master's?).
    assert ui.requests_after("edit", "/api/master") == 1
    # 0.1.11.5 (b++): an edited point's actions, in reading order: "Save this wording to my master", then Remove.
    one.locator('[data-action="save-to-master"]').wait_for()
    ui.page.keyboard.press("Tab")
    assert ui.page.evaluate("() => document.activeElement.dataset.action") == "save-to-master", "Tab from an edited point's box does not reach its master action"
    ui.page.keyboard.press("Tab")
    assert ui.page.evaluate("() => document.activeElement.dataset.action") == "remove-point", "Tab does not reach the point's Remove"
    ui.page.keyboard.press("Tab")
    assert ui.page.evaluate("() => document.activeElement === document.querySelectorAll('#job-resume [data-role=\"point-group\"]')[1].querySelectorAll('textarea')[1]"), "Tab does not reach the next point"
    ui.step("edit-two")
    ui.page.keyboard.press("ControlOrMeta+A")
    ui.page.keyboard.type(SECOND)
    ui.page.keyboard.press("Tab")  # leaving the box saves it
    _saved(ui)
    ui.page.wait_for_function("([selector, words]) => (document.querySelector(selector)?.textContent || '').includes(words)", arg=[f"{PREVIEW} .visually-hidden", SECOND])
    _ready(ui)
    assert _shown(ui)["pages"] <= edited["pages"] and _shown(ui)["count"] == _count(_shown(ui)["pages"])
    assert SECOND in _shown(ui)["text"] and was_two not in _shown(ui)["text"] and ui.writes_after("edit-two") == [LINES, f"POST {PREVIEW_ROUTE}"]
    assert boxes.count() == again["points"], "an edit changed how many points there are"
    _shot(ui, "2-edited")

    # --- REMOVE: the point is off the list, the preview and the counts ---
    victim = role_points.nth(2)
    victim_id, victim_text = victim.get_attribute("data-item-id"), victim.locator("textarea").input_value()
    before = _shown(ui)
    ui.step("remove")
    victim.locator('[data-action="remove-point"]').click()
    ui.page.locator(f'{POINT}[data-item-id="{victim_id}"]').wait_for(state="detached")
    _saved(ui)
    ui.page.wait_for_function("([selector, words]) => !(document.querySelector(selector)?.textContent || '').includes(words)", arg=[f"{PREVIEW} .visually-hidden", victim_text])
    _ready(ui)
    removed, held = _shown(ui), _held(ui)
    assert victim_text not in removed["text"] and removed["points"] == before["points"] - 1 == _printed(held) and removed["pictures"] != before["pictures"]
    assert removed["tabs"] == _tabs(held) == [f"Picked ({before['points'] - 1})", f"Left out ({len(first['selection']['left_out']) + 1})"], removed["tabs"]
    assert ui.writes_after("remove") == [SELECTION, f"POST {PREVIEW_ROUTE}"]

    # --- ADD: the lines left out, by role; the removed one first in its role, to put back; a search; one click adds ---
    ui.step("choices")
    ui.page.locator(f'{POINTS} [data-action="add-point"]').click()
    ui.page.locator(CHOICE).first.wait_for()
    ui.settle()
    assert ui.requests_after("choices", "/api/master") == 0 and ui.writes_after("choices") == [], "the master was read at the first edit: 'Add a point' reads it no second time"
    offered = ui.page.locator(CHOICE).evaluate_all("(items) => items.map((item) => [item.dataset.itemId, item.dataset.removed, item.querySelector('button').textContent, item.querySelector('[data-role=\"choice-text\"]').textContent])")
    assert len(offered) == len(first["selection"]["left_out"]) + 1 and all(text == master[item_id].text for item_id, _removed, _label, text in offered)
    assert offered[0] == [victim_id, "true", "Put back", master[victim_id].text] and all(row[1:3] == ["false", "Add"] for row in offered[1:])
    labels = ui.page.locator(f'{POINTS} [data-role="choice-group"] .resume-points-role').all_text_contents()
    order = [entry.heading for entry in _master(fixture).entries.values()]
    assert labels == [heading for heading in order if heading in labels] and len(labels) >= 2, "the roles are not listed newest first"
    ui.page.locator(f'{POINTS} [data-role="point-search"]').fill("role 1 line 9:")
    found = ui.page.locator(f'{CHOICE} [data-role="choice-text"]').all_text_contents()
    assert 1 <= len(found) < len(offered) and all(all(word in text.lower() for word in ("role", "1", "line", "9:")) for text in found), found
    ui.page.locator(f'{POINTS} [data-role="point-search"]').fill("")
    before = _shown(ui)
    base = before["pages"]  # the baseline of the Adds: the resume as it is now (two edits and a Remove later)
    added: list[str] = []
    ui.step("add")
    for _ in range(20):
        choice = ui.page.locator(CHOICE).nth(1)  # not the removed one: that stays off
        item_id = choice.get_attribute("data-item-id")
        choice.locator('[data-action="add-choice"]').click()
        ui.page.locator(f'{CHOICE}[data-item-id="{item_id}"]').wait_for(state="detached")
        ui.page.locator(f'{POINT}[data-item-id="{item_id}"]').wait_for()
        _saved(ui)
        ui.page.wait_for_function("([selector, words]) => (document.querySelector(selector)?.textContent || '').includes(words)", arg=[f"{PREVIEW} .visually-hidden", master[item_id].text])
        _ready(ui)
        added.append(item_id)
        assert _shown(ui)["pages"] in (base, base + 1), "one added point moved the count by more than a page"
        if _shown(ui)["pages"] == base + 1:
            break
    grown, held = _shown(ui), _held(ui)
    assert base + 1 > 2, "the grown resume must be over its 2 pages, or the 'over' sentence below proves nothing"
    assert (grown["pages"], grown["count"], grown["over"], grown["spacing"]) == (base + 1, _count(base + 1), "true", "0.85"), f"{len(added)} added points did not move the page count: {grown['count']}"
    assert "Move the slider left to reach 2 pages." in (ui.page.locator(COUNT).text_content() or "")
    assert all(master[item_id].text in grown["text"] for item_id in added) and victim_text not in grown["text"]
    assert grown["points"] == before["points"] + len(added) == _printed(held) and grown["tabs"] == _tabs(held)
    assert ui.writes_after("add").count(SELECTION) == len(added) and ui.requests_after("add", PREVIEW_ROUTE) == len(added), "an Add is one write and one preview"
    last = ui.page.locator(f'{POINT}[data-item-id="{added[-1]}"]')
    assert last.evaluate("(node) => node === node.parentElement.lastElementChild"), "an added point is not the last of its role"
    assert last.evaluate("(node) => node.closest('[data-role=\"point-group\"]').querySelector('.resume-points-role').textContent") == _master(fixture).entries[master[added[-1]].entry_id].heading
    assert not _ID.search(ui.page.locator(POINTS).inner_text()), "the list shows an id"
    _shot(ui, "3-added")

    # --- a reload shows the saved state ---
    ui.step("reloaded")
    ui.reload()
    ui.page.locator(f'{PANEL}[data-state="stored"]').wait_for()
    # 0.1.11.5: the page opens on Preview ALWAYS, also for a resume with changed points (it reopened on "Show changes").
    assert ui.page.locator(f'{PANEL} [data-action="view-clean"]').get_attribute("aria-pressed") == "true", "a resume with a changed point did not reopen on Preview"
    _ready(ui, pages=base + 1)
    reloaded = _shown(ui)
    assert (reloaded["count"], reloaded["spacing"], reloaded["points"], reloaded["tabs"]) == (_count(base + 1), "0.85", grown["points"], grown["tabs"]), reloaded
    texts = ui.page.locator(f'{POINT} [data-role="point-text"]').evaluate_all("(boxes) => boxes.map((box) => box.value)")
    assert FIRST in texts and SECOND in texts and victim_text not in texts and all(master[item_id].text in texts for item_id in added)
    assert ui.writes_after("reloaded") == [f"POST {PREVIEW_ROUTE}"], "a reload wrote something"

    # --- the downloaded PDF is the preview ---
    ui.page.locator(f'{PANEL} [data-action="apply"]').click()
    form = ui.page.locator(FORM)
    as_is = ui.page.locator(f'{PANEL} [data-action="apply-as-is"]')
    form.or_(as_is).first.wait_for()
    if as_is.count():
        as_is.click()
    form.wait_for()
    ui.page.fill("#generate-pdf-name", NAME)
    ui.page.locator(f'{PREVIEW}[data-header="true"][data-state="ready"]').wait_for()
    ui.settle()
    with_header = _shown(ui)
    with ui.page.expect_download() as waiting:
        with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/tailored-resumes/pdf"):
            form.locator('[data-role="generate-pdf"]').click()
    ui.page.locator(f'{PANEL} [data-role="pdf-saved"]').wait_for()
    pages = _pdf_pages(Path(waiting.value.path()))
    printed = " ".join(pages)
    assert len(pages) == with_header["pages"], f"the PDF has {len(pages)} pages, the preview shows {with_header['pages']}"
    assert NAME.upper() in pages[0] and FIRST in printed and SECOND in printed and was_one not in printed and victim_text not in printed
    assert all(master[item_id].text in printed for item_id in added)

    # --- only this job's resume was written: the master is what it was, and the job's spacing is the slider's ---
    assert _files(fixture.home_root) == masters, "a change of a job's points wrote the master"
    assert layout.read_bytes() == spacing, "a change of a job's points wrote the job's saved spacing"
    on_disk = json.loads(stored_file.read_text(encoding="utf-8"))
    assert FIRST in on_disk["markdown"] and on_disk["updated_at"] == first["updated_at"] and NAME not in stored_file.read_text(encoding="utf-8")
    ui.assert_clean()
