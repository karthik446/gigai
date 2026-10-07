"""0.1.11.5 SP: the job page shows the resume that WAITS beside an edited one, and names the line the master changed.

THE BUG (the operator's home): he had edited a point of a job's resume, then retired a master line the resume prints.
The job page said "old assessment: resume changed" and "a line this resume prints was changed or retired in your
master", with ONLY "Re-assess · 1 model call". He re-assessed, and the old resume still showed with no way forward: the
new pick waited beside his edited resume.

Real Chromium against a REAL server of its own, nothing stubbed but `GET /api/setup` (the fixture home was never
through the setup interview), on the synthetic home of `tests/behaviors/scout_find_jobs/test_pick_header_room.py`
(an invented 56-line master, one posting, a scripted model, the pipeline off). The states are made through the routes
the pages use (`tests/behaviors/scout_find_jobs/test_waiting_resume_on_open.py` holds the steps).

Pinned, on what the page shows:

- THE NAMED LINE (`test_the_stale_notice_names_...`): the notice says WHICH line, by its words on this resume, short,
  never an id: a retired one with "Remove it from this resume", a reworded one with "Use the new wording"; the refresh
  button stays as the other choice. Each fix is ONE write and no model call; its row goes, the points list and the
  preview follow. The fixes work after a Re-pick on the same visit too (that step's answer no longer hides the
  stale list read after it).
- THE WAITING RESUME (`test_a_resume_that_waits_...`): the operator's steps. Before the re-assess the notice names
  the retired line and offers to remove it, beside Re-assess. After "Re-assess · 1 model call" the callout "A new
  resume is ready for this job" is the card's FIRST line (above Generate PDF and the stale list) WITHOUT A RELOAD, and
  on a fresh load, which writes nothing. It says what it changes (lines added, left out), that using it replaces the
  resume he edited, and lists the edited point it would drop. The re-assess NAMES THE PAGE'S PROFILE: another profile
  selected off the page meanwhile (the operator has two) does not get the assessment. The no-model fix of the
  retired line comes FIRST, before Re-assess. "Dismiss" leaves his resume; "Use it"
  (on a proposal that waits again) replaces it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

import pytest

from gigai.scout import job_actions

from tests.behaviors.scout_find_jobs.test_pick_header_room import _JOB, _URL, KUBERNETES_LINE, _Server, _assess, _ids, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.behaviors.scout_find_jobs.test_waiting_resume_on_open import (
    EDITED, REWORDED, bullets, cited, edit_point, edited_then_retired, master_line, opened, plain_bullets, script_reassessment, stored,
)
from tests.ui import support
from tests.ui.conftest import _ui_session

pytestmark = pytest.mark.ui
UI_ORDER = 62  # a server and a home of its own: nothing of the shared home is read or written

PANEL = "#job-resume"
PREVIEW = f'{PANEL} [data-testid="resume-preview"]'
POINTS = f'{PANEL} [data-testid="resume-points"]'
STALE = f'{PANEL} [data-role="resume-stale"]'
LINE_ROW = f'{STALE} [data-stale="picked_line_changed"]'
WAITING = f'{PANEL} [data-role="proposed"]'
REPLACES = f'{WAITING} [data-role="proposed-replaces"]'
PREVIEW_WRITE = "POST /api/tailored-resumes/preview"
PICK = "POST /api/job-resumes/pick"
READY = "A new resume is ready for this job. Yours stays as it is until you take the new one."
CHANGES = re.compile(r"^It (adds \d+ lines?)?( and )?(leaves out \d+ lines? this resume prints)?\.$")
REPLACES_TEXT = "Using it replaces the resume you edited. It would drop your edited point:"
_ID = re.compile(r"\bb-[0-9a-f]{6}\b|\bsum-[0-9a-f]{6}\b|\bL\d+\b|req-[0-9a-f]+|picked_line_changed")
#: Set GIGAI_UI_WAITING_SHOT to a folder to keep pictures of the panel (hand check only).
SHOT = os.environ.get("GIGAI_UI_WAITING_SHOT")


@pytest.fixture(autouse=True)
def _scratch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME is a folder of this test: nothing of the person's is read or written."""

    home = support.refuse_real_home(tmp_path / "person")
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def page(request: pytest.FixtureRequest, ui_browser, ui_artifacts: Path, fx, server: _Server, tmp_path: Path):  # noqa: ANN001, F811
    """`ui` on this test's own server; the test makes the job's state before it opens the page."""

    log = tmp_path / "server.log"
    log.write_text("", encoding="utf-8")
    own = SimpleNamespace(url=f"http://127.0.0.1:{server.server.server_address[1]}", pid=os.getpid(), log_path=str(log))
    prefill = server.client.get("/api/setup").json()["error"].get("prefill") or {}
    with _ui_session(request, ui_browser, own, ui_artifacts) as session:
        session.fx, session.api = fx, server
        session.page.route("**/api/setup", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"prefs": prefill})))
        yield session


def _open(ui) -> None:
    """A FRESH load of the job page (the whole app is loaded again), its preview rendered."""

    address = "/#/jobs/" + quote(_JOB, safe="")
    if ui.page.url.endswith(address):
        ui.settle()
        ui.reload()
    else:
        ui.goto(address)
    ui.page.locator(f'{PANEL}[data-state="stored"]').wait_for()
    _ready(ui)


def _ready(ui) -> None:
    ui.page.locator(f'{PREVIEW}[data-state="ready"]').wait_for()
    ui.settle()


def _preview(ui) -> str:
    return " ".join((ui.page.locator(f"{PREVIEW} .visually-hidden").text_content() or "").split())


def _points(ui) -> str:
    return " ".join(ui.page.locator(f'{POINTS} [data-role="point"] textarea, {POINTS} [data-role="point"] input').evaluate_all("(boxes) => boxes.map((box) => box.value)"))


def _words(ui, selector: str) -> str:
    return " ".join((ui.page.locator(selector).first.text_content() or "").split())


def _replaces(ui) -> tuple[str, list[str]]:
    """What the waiting callout says Use it would cost: its sentence, and the edited points it lists."""

    return (ui.page.locator(f"{REPLACES} p").text_content() or "").strip(), [(item.text_content() or "").strip() for item in ui.page.locator(f"{REPLACES} li").all()]


def _at_the_top(ui) -> None:
    """The waiting resume is the card's first line: above Generate PDF and above the stale list; it says what it changes."""

    order = ui.page.evaluate(
        """([waiting, stale, panel]) => {
          const box = document.querySelector(waiting);
          const after = (other) => Boolean(other) && Boolean(box.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING);
          return [after(document.querySelector(stale)), after(document.querySelector(panel + ' [data-action="apply"]')), document.querySelector(panel + ' .callout') === box];
        }""",
        [WAITING, STALE, PANEL],
    )
    assert order == [True, True, True], order
    changes = _words(ui, f'{WAITING} [data-role="proposed-changes"]')
    assert CHANGES.match(changes) and "leaves out" in changes, changes
    assert (ui.page.locator(f'{WAITING} [data-action="use-proposed"]').get_attribute("class") or "").split() == ["button", "small"], "Use it is the primary button"


def _other_profile_selected(api) -> str:
    """Another profile is made and SELECTED off the page (another tab, the CLI), as the operator's home has two. Returns its id."""

    origin = {"Origin": str(api.client.base_url).rstrip("/")}
    made = api.client.post("/api/profiles", json={"label": "Staff backend", "titles": ["staff backend engineer"]}, headers=origin)
    assert made.status_code == 201, made.text
    other = made.json()["profile"]["profile_id"]
    _select(api, other)
    return other


def _select(api, profile_id: str) -> None:
    switched = api.client.post("/api/profiles/selection", json={"profile_id": profile_id}, headers={"Origin": str(api.client.base_url).rstrip("/")})
    assert switched.status_code == 200 and switched.json()["selected_profile_id"] == profile_id, switched.text


def _shot(ui, name: str) -> None:
    if SHOT:
        Path(SHOT).mkdir(parents=True, exist_ok=True)
        ui.page.locator(PANEL).screenshot(path=str(Path(SHOT) / f"{name}.png"))


def _short(line: dict) -> str:
    """The first words of a stored line, as the notice quotes them."""

    return " ".join(line["text"].lstrip("- ").split())[:60]


def test_the_stale_notice_names_the_line_and_removing_it_or_using_the_new_wording_needs_no_model(page) -> None:  # noqa: ANN001
    ui = page
    fixture, api = ui.fx, ui.api
    _assess(fixture)
    mine, gone, changed = plain_bullets(fixture, api)[:3]
    edit_point(api, mine["id"])  # the resume is the user's from here on
    master_line(api, cited(gone), "retire")
    master_line(api, cited(changed), "edit", REWORDED)
    calls = len(fixture.base.model.assess_prompts)

    ui.step("open")
    _open(ui)
    rows = ui.page.locator(LINE_ROW)
    assert rows.count() == 2, _words(ui, STALE)
    retired, reworded = ui.page.locator(f'{LINE_ROW}[data-change="retired"]'), ui.page.locator(f'{LINE_ROW}[data-change="reworded"]')
    said = (" ".join((retired.text_content() or "").split()), " ".join((reworded.text_content() or "").split()))
    assert said[0].startswith(f"Stale: a line this resume prints was retired from your master: “{_short(gone)}") and said[0].endswith("Remove it from this resume"), said[0]
    assert said[1].startswith(f"Stale: a line this resume prints was reworded in your master: “{_short(changed)}") and said[1].endswith("Use the new wording"), said[1]
    assert not _ID.search(_words(ui, STALE)), _words(ui, STALE)
    # The refresh stays as the other choice (the assessment is current, so it is the re-pick), with its cost.
    assert (ui.page.locator(f'{STALE} [data-action="repick"]').text_content() or "").strip() == "Re-pick · no model call"
    assert _short(gone) in _preview(ui) and _short(gone) in _points(ui)
    assert ui.writes_after("open") == [PREVIEW_WRITE], "opening the job wrote something"
    _shot(ui, "1-named-lines")

    # --- a Re-pick on this visit: the new pick waits beside the edited resume, and says what Use it would cost ---
    ui.step("repick")
    ui.page.locator(f'{STALE} [data-action="repick"]').click()
    ui.page.locator(WAITING).wait_for()
    assert _replaces(ui) == (REPLACES_TEXT, [EDITED])
    assert rows.count() == 2, "the lines are still named"
    _ready(ui)

    # --- Remove it from this resume: ONE write, the row goes, the points and the preview follow ---
    ui.step("remove")
    with ui.page.expect_request(lambda request: request.method == "PUT" and urlsplit(request.url).path == "/api/tailored-resumes/selection") as sent:
        retired.locator('[data-action="remove-stale-line"]').click()
    retired.wait_for(state="detached")
    assert sent.value.post_data_json["use"] == "remove" and sent.value.post_data_json["item_id"] == cited(gone)
    _ready(ui)
    assert _short(gone) not in _preview(ui) and _short(gone) not in _points(ui)
    assert [write for write in ui.writes_after("remove") if write != PREVIEW_WRITE] == ["PUT /api/tailored-resumes/selection"]
    assert rows.count() == 1 and EDITED in _preview(ui), "the edited point is still his"
    # (an edit of the resume drops a proposal made before it: the page shows what the server holds)
    assert ui.page.locator(WAITING).count() == (0 if opened(api)["proposed"] is None else 1)
    _shot(ui, "2-removed")

    # --- Use the new wording: the master's words as they are now, on that point ---
    ui.step("reword")
    with ui.page.expect_request(lambda request: request.method == "PUT" and urlsplit(request.url).path == "/api/tailored-resumes/lines") as sent:
        reworded.locator('[data-action="reword-stale-line"]').click()
    ui.page.locator(LINE_ROW).first.wait_for(state="detached")
    assert sent.value.post_data_json["use"] == "custom" and sent.value.post_data_json["text"] == REWORDED
    _ready(ui)
    assert REWORDED in _preview(ui) and REWORDED in _points(ui) and _short(changed) not in _preview(ui)
    assert [write for write in ui.writes_after("reword") if write != PREVIEW_WRITE] == ["PUT /api/tailored-resumes/lines"]
    assert rows.count() == 0
    # The server agrees, and so does a fresh load; no model was called.
    view = opened(api)
    assert "picked_line_changed" not in view["stale"] and view["stale_lines"] == []
    _open(ui)
    assert ui.page.locator(LINE_ROW).count() == 0 and REWORDED in _preview(ui)
    assert len(fixture.base.model.assess_prompts) == calls
    _shot(ui, "3-reworded")
    ui.assert_clean()


def test_a_resume_that_waits_beside_an_edited_one_is_shown_on_a_fresh_load_and_after_a_reassess(page) -> None:  # noqa: ANN001
    ui = page
    fixture, api = ui.fx, ui.api
    edited_then_retired(fixture, api)

    # --- before: the old assessment, and the retired line NAMED with its own fix beside Re-assess ---
    ui.step("open")
    _open(ui)
    said = _words(ui, STALE)
    assert "Stale: old assessment: resume changed." in said and not _ID.search(said), said
    row = ui.page.locator(f'{LINE_ROW}[data-change="retired"]')
    assert f"was retired from your master: “{KUBERNETES_LINE}”" in " ".join((row.text_content() or "").split())
    assert (row.locator('[data-action="remove-stale-line"]').text_content() or "").strip() == "Remove it from this resume"
    assert (ui.page.locator(f'{STALE} [data-action="reassess-stale"]').text_content() or "").strip() == "Re-assess · 1 model call"
    assert ui.page.locator(WAITING).count() == 0
    # The fix that needs no model is offered FIRST: its row and button come before the other reason and Re-assess.
    assert ui.page.locator(f"{STALE} li").first.get_attribute("data-change") == "retired"
    assert [button.get_attribute("data-action") for button in ui.page.locator(f"{STALE} button").all()] == ["remove-stale-line", "reassess-stale"]
    assert ui.writes_after("open") == [PREVIEW_WRITE]
    _shot(ui, "4-before-the-reassess")

    # --- Re-assess (the scripted model): the new pick WAITS, and the page says so without a reload ---
    # Meanwhile ANOTHER profile is selected off the page: the re-assess still names the profile this page shows.
    script_reassessment(fixture, api)
    _other_profile_selected(api)
    ui.step("reassess")
    with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/assess") as asked:
        ui.page.locator(f'{STALE} [data-action="reassess-stale"]').click()
    ui.page.locator(WAITING).wait_for()
    sent_body = asked.value.post_data_json
    assert sent_body["job"] == {"job_url": _URL} and sent_body["resume"] == {"profile_id": api.key["profile_id"]}, sent_body
    # SAME VISIT, no reload: the ready resume is the card's first line, with what it changes and the edited point it would drop.
    assert _words(ui, WAITING).startswith(READY)
    _at_the_top(ui)
    assert _replaces(ui) == (REPLACES_TEXT, [EDITED])
    assert "old assessment" not in _words(ui, STALE) and "made before the latest assessment" in _words(ui, STALE)
    _select(api, api.key["profile_id"])
    _ready(ui)
    assert [write for write in ui.writes_after("reassess") if write != PREVIEW_WRITE] == ["POST /api/assess"]

    # --- A FRESH LOAD shows it: one read, no write, no click ---
    ui.step("fresh")
    _open(ui)
    waiting = ui.page.locator(WAITING)
    assert waiting.count() == 1 and _words(ui, WAITING).startswith(READY)
    _at_the_top(ui)
    assert _replaces(ui) == (REPLACES_TEXT, [EDITED])
    assert [(button.get_attribute("data-action"), (button.text_content() or "").strip()) for button in waiting.locator("button").all()] == [
        ("compare-proposed", "Compare"), ("use-proposed", "Use it"), ("dismiss-proposed", "Dismiss"),
    ]
    assert ui.writes_after("fresh") == [PREVIEW_WRITE], "a fresh load of the job wrote something"
    assert EDITED in _preview(ui) and ui.page.locator(PANEL).get_attribute("data-origin") == "edited"
    assert not _ID.search(_words(ui, WAITING)), _words(ui, WAITING)
    # Compare lists what the new one would add and leave out, by their words.
    waiting.locator('[data-action="compare-proposed"]').click()
    waiting.locator('[data-role="proposed-drops"]').wait_for()
    ui.page.wait_for_function("(sel) => !/\\bb-[0-9a-f]{6}\\b/.test(document.querySelector(sel).textContent)", arg=WAITING)  # the master's words are read once
    assert KUBERNETES_LINE in _words(ui, f'{WAITING} [data-role="proposed-drops"]'), "the retired line is named by its words on this resume, never its id"
    _shot(ui, "5-waiting-on-a-fresh-load")

    # --- Dismiss leaves his resume as it is ---
    ui.step("dismiss")
    with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/job-resumes/pick") as sent:
        waiting.locator('[data-action="dismiss-proposed"]').click()
    waiting.wait_for(state="detached")
    assert sent.value.post_data_json["action"] == "dismiss_proposed"
    _ready(ui)
    assert [write for write in ui.writes_after("dismiss") if write != PREVIEW_WRITE] == [PICK]
    assert opened(api)["proposed"] is None and EDITED in stored(api)["markdown"] and EDITED in _preview(ui)

    # --- a pick that waits again (made off the page): a fresh load shows it, and Use it REPLACES his resume ---
    again = job_actions.pick_action(fixture.home_root, fixture.target, _URL, "refresh", profile_id=api.key["profile_id"])
    assert again["proposed"] is not None and EDITED in stored(api)["markdown"]
    ui.step("again")
    _open(ui)
    waiting = ui.page.locator(WAITING)
    assert waiting.count() == 1 and _replaces(ui) == (REPLACES_TEXT, [EDITED])
    assert ui.writes_after("again") == [PREVIEW_WRITE]
    ui.step("use")
    with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/job-resumes/pick") as sent:
        waiting.locator('[data-action="use-proposed"]').click()
    waiting.wait_for(state="detached")
    assert sent.value.post_data_json["action"] == "use_proposed"
    ui.page.locator(f'{PANEL}[data-origin="pick"]').wait_for()
    _ready(ui)
    assert [write for write in ui.writes_after("use") if write != PREVIEW_WRITE] == [PICK]
    held = stored(api)["markdown"]
    assert EDITED not in held and KUBERNETES_LINE not in held and EDITED not in _preview(ui) and KUBERNETES_LINE not in _preview(ui)
    assert ui.page.locator(LINE_ROW).count() == 0 and opened(api)["proposed"] is None
    assert all(cited(line) != _ids(fixture).get(KUBERNETES_LINE, "gone") for line in bullets(stored(api)))
    _shot(ui, "6-used")
    ui.assert_clean()
