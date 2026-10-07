"""0.1.11.5 (a): the job page shows the resume AS IT WILL PRINT, with one spacing slider and a live page count.

Real Chromium against the real server, nothing stubbed, on the small home's tailored job. For this test the job's
stored resume is a synthetic 20-bullet one (`tests/support/resume_spacing_fixture.py`, stored through the real CLI on
a throwaway gig and put in the job's own file; the file is put back afterwards). That resume is 3 pages at spacing
1.2 and reaches 2 pages at a lower spacing (0.1.11.5 (d) set four of its blocks in fewer lines: it fits 2 pages
up to 1.05, where the preview opens; before that it opened at 0.85 and was 3 pages at 1.0).

Pinned, on what the page shows and on the PDF the browser downloads:

- the preview is asked for ONCE when the job's resume panel opens, and shows the pages as pictures (one a page), the
  slider at the fitted spacing with its number, and "2 pages"; no further request follows by itself;
- a slider move re-renders: "3 pages" in plain words at 1.20x (with the way back), the pages on screen are never
  gone while the next ones are made, and only the answer to the LAST move is shown;
- the move is SAVED for this job with no Save button: after a reload the slider is where it was left;
- Generate PDF downloads the same render: its request carries the slider's spacing, and the PDF has as many pages
  as the preview shows, with the header typed into the form (which the preview shows too once it is typed);
- 0.1.11.5 PH: the preview always shows a header and one line says whose: "Placeholder header: add yours in Generate
  PDF" with no header file, nothing while the form's own values are shown, and "Showing your header from header.json"
  once the person's file is there (the page itself holds no value of it: they are in the pictures only).
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

import pytest
from click.testing import CliRunner

from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import TailorResponse, list_tailored_resumes

from tests.behaviors.scout_find_jobs.test_pdf_header_file import FILE, MARKERS
from tests.support.pipeline_fixtures import JOB, build_pipeline_fixture
from tests.support.resume_spacing_fixture import resume
from tests.ui import support

pytestmark = pytest.mark.ui
UI_ORDER = 60  # the job's resume file is swapped for the test and put back: nothing is left for the other flows

PANEL = ".job-page #job-resume"
PREVIEW = f'{PANEL} [data-testid="resume-preview"]'
SLIDER = f'{PREVIEW} [data-role="preview-spacing"]'
PAGES = f'{PREVIEW} [data-role="preview-page"]'
COUNT = f'{PREVIEW} [data-role="preview-pages"]'
FORM = f'{PANEL} [data-role="generate-pdf-form"]'
HEADER_NOTE = f'{PREVIEW} [data-role="preview-header-note"]'
ROUTE = "/api/tailored-resumes/preview"
NAME = "Zephyrine Quillfeather"
#: Set GIGAI_UI_PREVIEW_SHOT to a folder to keep pictures of the panel (hand check only).
SHOT = os.environ.get("GIGAI_UI_PREVIEW_SHOT")
#: The fewest page pictures on screen, sampled every frame while the test moves the slider.
WATCH_JS = """() => {
  window.__fewest = Infinity;
  const look = () => {
    const pages = document.querySelectorAll('[data-testid="resume-preview"] [data-role="preview-page"]').length;
    window.__fewest = Math.min(window.__fewest, pages);
    window.__watching = requestAnimationFrame(look);
  };
  look();
}"""


def _donor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """The 20-bullet resume as the store holds a job's resume: stored through the real CLI on a throwaway gig of its own."""

    own = support.refuse_real_home(tmp_path / "home")
    own.mkdir()
    monkeypatch.setenv("HOME", str(own))
    monkeypatch.setenv(PIPELINE_ENV, "off")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=resume(20), pipeline=False)
    master = tmp_path / "master.md"
    master.write_text(resume(30, master=True), encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=master, gig_id=fx.gig.resolved.gig_id).status == "created"
    picked = tmp_path / "picked.md"
    picked.write_text(resume(20), encoding="utf-8")
    stored = CliRunner().invoke(scout_group, ["resume", "store", "--in", str(picked), "--job-url", JOB, "--as", "agent", "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert stored.exit_code == 0, stored.output
    return list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)[0].to_json()


@pytest.fixture
def job(ui, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A tailored job of the shared home whose stored resume is, for this test, the 20-bullet one (an agent's edited
    resume); the job's own resume files are put back afterwards, byte for byte."""

    rows = ui.server_json("/api/postings?limit=50")["postings"]["rows"]
    row = next(row for row in rows if row["tailored"] and row["state"] == "matched" and not row["open_questions"])
    profile_id = ui.server_json("/api/profiles")["selected_profile_id"]
    held = ui.server_json(f"/api/tailored-resumes?profile_id={quote(profile_id, safe='')}&job_identity={quote(row['job_identity'], safe='')}")["items"][0]
    files = {Path(held["stored_path"]): None, Path(held["markdown_path"]): None}
    for path in files:
        files[path] = path.read_bytes()
    donor = _donor(tmp_path, monkeypatch)
    on_file = json.loads(Path(held["stored_path"]).read_text(encoding="utf-8"))
    mine = {key: value for key, value in on_file.items() if key != "selection"}
    mine.update(result=donor["result"], markdown=donor["markdown"], edited=donor["edited"])
    TailorResponse.from_json(mine)  # the store reads it (a file it cannot read is "no resume")
    Path(held["stored_path"]).write_text(json.dumps(mine, indent=2, sort_keys=True), encoding="utf-8")
    Path(held["markdown_path"]).write_text(donor["markdown"], encoding="utf-8")
    try:
        yield SimpleNamespace(identity=row["job_identity"], profile_id=profile_id, stored_path=Path(held["stored_path"]))
    finally:
        for path, was in files.items():
            path.write_bytes(was)
        Path(held["stored_path"]).with_suffix(".layout").unlink(missing_ok=True)


def _ready(ui, *, pages: int | None = None, spacing: str | None = None) -> None:
    wanted = f'{PREVIEW}[data-state="ready"]' + (f'[data-pages="{pages}"]' if pages is not None else "") + (f'[data-spacing="{spacing}"]' if spacing is not None else "")
    ui.page.locator(wanted).wait_for()
    ui.settle()


def _shown(ui) -> dict:
    preview = ui.page.locator(PREVIEW)
    sizes = ui.page.locator(PAGES).evaluate_all("(images) => images.map((image) => [image.complete, image.naturalWidth, image.naturalHeight])")
    assert sizes and all(done and width > 1000 and height > width for done, width, height in sizes), f"the pages are not drawn: {sizes}"
    return {
        "pages": int(preview.get_attribute("data-pages")),
        "pictures": len(sizes),
        "spacing": preview.get_attribute("data-spacing"),
        "slider": ui.page.locator(SLIDER).input_value(),
        "label": (ui.page.locator(f'{PREVIEW} [data-role="preview-spacing-value"]').text_content() or "").strip(),
        "count": (ui.page.locator(f"{COUNT} strong").text_content() or "").strip(),
        "over": ui.page.locator(COUNT).get_attribute("data-over"),
    }


def _shot(ui, name: str) -> None:
    if SHOT:
        Path(SHOT).mkdir(parents=True, exist_ok=True)
        ui.page.locator(PANEL).screenshot(path=str(Path(SHOT) / f"{name}.png"))


def _pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [page.extract_text() for page in PdfReader(io.BytesIO(path.read_bytes())).pages]


def _header_note(ui) -> tuple[str | None, str | None]:
    """(whose header the preview shows, the line under it or None)."""
    note = ui.page.locator(HEADER_NOTE)
    return ui.page.locator(PREVIEW).get_attribute("data-header-shown"), (note.text_content() or "").strip() if note.count() else None


def test_the_job_page_previews_the_resume_and_the_slider_sets_and_saves_its_spacing(ui, job, scout_server) -> None:
    stored = lambda: json.loads(job.stored_path.read_text(encoding="utf-8"))  # noqa: E731
    layout = job.stored_path.with_suffix(".layout")
    saved = lambda: json.loads(layout.read_text(encoding="utf-8"))["spacing_percent"] if layout.exists() else None  # noqa: E731
    first = stored()
    assert saved() is None

    # --- the panel opens: ONE preview request, the pages as pictures, the fitted spacing, "2 pages" ---
    ui.step("open")
    ui.goto("/#/jobs/" + quote(job.identity, safe=""))
    ui.wait_for_job_page()
    ui.page.locator(PANEL).wait_for()
    _ready(ui)
    opened = _shown(ui)
    assert (opened["pages"], opened["pictures"], opened["count"], opened["over"]) == (2, 2, "2 pages", "false"), opened
    assert opened["spacing"] == "1.05" and float(opened["slider"]) == 1.05 and opened["label"] == "1.05x", opened
    assert ui.page.locator(f"{PANEL} .md-preview").count() == 0, "the markdown is shown in place of the rendered pages"
    assert "Led the redesign of scheduling service 0" in (ui.page.locator(PREVIEW).text_content() or ""), "the pictures have no text behind them"
    assert ui.requests_after("open", ROUTE) == 1, "the preview is asked for once when the panel opens"
    header_file = scout_server.home / "Documents" / "GigAI" / "header.json"
    assert not header_file.exists(), "the synthetic home starts without a header file"
    assert _header_note(ui) == ("placeholder", "Placeholder header: add yours in Generate PDF")
    assert saved() is None, "opening the page saved a spacing"
    _shot(ui, "1-opened")
    # Nothing more by itself: the page's own polls do not ask for the preview again.
    ui.step("idle")
    ui.page.wait_for_timeout(2500)
    assert ui.requests_after("idle", ROUTE) == 0 and ui.writes_after("idle") == []

    # --- a slider move re-renders: "3 pages" plainly, never a blank, only the last move's answer ---
    ui.page.evaluate(WATCH_JS)
    ui.step("moved")
    slider = ui.page.locator(SLIDER)
    slider.focus()
    for _ in range(3):  # 1.05 -> 1.20, a step a key press: one request once the slider rests
        ui.page.keyboard.press("ArrowRight")
    assert (ui.page.locator(f'{PREVIEW} [data-role="preview-spacing-value"]').text_content() or "").strip() == "1.20x", "the number follows the slider at once"
    ui.page.locator(f'{PREVIEW}[data-state="updating"]').wait_for()
    assert (ui.page.locator(f'{PREVIEW} [data-role="preview-status"]').text_content() or "").strip() == "Updating the preview"
    _ready(ui, pages=3, spacing="1.20")
    loose = _shown(ui)
    assert (loose["pages"], loose["pictures"], loose["count"], loose["over"]) == (3, 3, "3 pages", "true"), loose
    assert "Move the slider left to reach 2 pages." in (ui.page.locator(COUNT).text_content() or "")
    assert ui.page.evaluate("() => window.__fewest") >= 2, "the pages went blank while the preview was updated"
    assert ui.requests_after("moved", ROUTE) == 1 and ui.writes_after("moved") == [f"POST {ROUTE}"], "three key presses, one request"
    assert saved() == 120, "the slider's spacing was not saved for the job"
    _shot(ui, "2-three-pages")

    # --- a reload opens where the slider was left ---
    ui.step("reloaded")
    ui.reload()
    ui.wait_for_job_page()
    _ready(ui, pages=3, spacing="1.20")
    again = _shown(ui)
    assert (float(again["slider"]), again["label"], again["count"]) == (1.2, "1.20x", "3 pages"), again
    assert ui.page.locator(f'{PREVIEW} [data-role="preview-saved"]').get_attribute("data-saved") == "true"

    # --- tighter: 2 pages again, and Generate PDF downloads that render ---
    ui.step("tightened")
    ui.page.locator(SLIDER).focus()
    for _ in range(8):
        ui.page.keyboard.press("ArrowLeft")
    _ready(ui, pages=2, spacing="0.80")
    assert _shown(ui)["count"] == "2 pages" and saved() == 80
    ui.page.locator(f'{PANEL} [data-action="apply"]').click()
    form = ui.page.locator(FORM)
    as_is = ui.page.locator(f'{PANEL} [data-action="apply-as-is"]')
    form.or_(as_is).first.wait_for()
    if as_is.count():
        as_is.click()
    form.wait_for()
    assert ui.page.locator(PREVIEW).get_attribute("data-header") == "false"
    ui.page.fill("#generate-pdf-name", NAME)
    ui.page.locator(f'{PREVIEW}[data-header="true"][data-state="ready"]').wait_for()
    ui.settle()
    with_header = _shown(ui)
    assert with_header["pages"] == 2 and with_header["spacing"] == "0.80", with_header
    assert _header_note(ui) == ("form", None), "the typed header is on screen: no line about a placeholder"
    _shot(ui, "3-with-header")
    ui.step("generate")
    with ui.page.expect_download() as waiting:
        with ui.page.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == "/api/tailored-resumes/pdf") as sent:
            form.locator('[data-role="generate-pdf"]').click()
    downloaded = Path(waiting.value.path())
    ui.page.locator(f'{PANEL} [data-role="pdf-saved"]').wait_for()
    ui.settle()
    assert ui.writes_after("generate") == ["POST /api/tailored-resumes/pdf"], "Generate is the PDF and nothing after it"
    body = sent.value.post_data_json
    assert body["spacing_scale"] == 0.8 and body["header"]["name"] == NAME
    pages = _pdf_pages(downloaded)
    assert len(pages) == with_header["pages"] == 2, "the downloaded PDF is not the pages the preview shows"
    assert NAME.upper() in pages[0] and "scheduling service 19," in pages[1]
    # The job's stored resume is what it was (still the agent's edited resume); its spacing is in the small file beside it.
    kept = stored()
    assert kept == first and kept["edited"]["written_by"] == "agent" and saved() == 80, "the job's stored resume was written"
    assert NAME not in job.stored_path.read_text(encoding="utf-8")

    # --- 0.1.11.5 PH: with the person's header file the preview shows THEIR header, and says so without a value ---
    header_file.parent.mkdir(parents=True, exist_ok=True)
    header_file.write_text(json.dumps(FILE), encoding="utf-8")
    os.chmod(header_file, 0o600)
    try:
        ui.step("with-file")
        ui.reload()
        ui.wait_for_job_page()
        ui.page.locator(f'{PREVIEW}[data-state="ready"][data-header-shown="file"]').wait_for()
        ui.settle()
        assert _header_note(ui) == ("file", "Showing your header from header.json")
        mine = _shown(ui)
        assert (mine["pages"], mine["spacing"]) == (2, "0.80"), mine
        picture = ui.page.locator(PAGES).first.get_attribute("src")
        _shot(ui, "4-header-file")
        # The page holds the header as a picture only: no value in its text, its markup, its address or its storage.
        held = ui.page.evaluate(
            "() => [document.documentElement.outerHTML.replace(/src=\"data:image[^\"]*\"/g, ''), location.href, JSON.stringify(localStorage), JSON.stringify(sessionStorage), document.cookie].join('\\n')"
        )
        assert "Showing your header from header.json" in held
        for marker in MARKERS:
            assert marker not in held, f"the page holds {marker!r} outside the preview's pictures"
        assert ui.requests_after("with-file", "/api/pdf-header") == 0, "the page read the header file's values itself"
        # Without the file it is the placeholder again, another picture of the same pages.
        header_file.unlink()
        ui.reload()
        ui.wait_for_job_page()
        ui.page.locator(f'{PREVIEW}[data-state="ready"][data-header-shown="placeholder"]').wait_for()
        assert _header_note(ui) == ("placeholder", "Placeholder header: add yours in Generate PDF")
        assert _shown(ui)["pages"] == mine["pages"] and ui.page.locator(PAGES).first.get_attribute("src") != picture
    finally:
        header_file.unlink(missing_ok=True)
    leaked = sorted(
        f"{path.relative_to(scout_server.home)}: {marker}"
        for path in scout_server.home.rglob("*")
        if path.is_file() and not path.is_symlink()
        for marker in MARKERS
        if marker.encode() in path.read_bytes()
    )
    assert leaked == [], f"the server's home holds a value of the header file: {leaked}"
