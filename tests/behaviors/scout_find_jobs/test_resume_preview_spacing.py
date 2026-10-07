"""0.1.11.5 (a): the stronger spacing scale, the job page's preview and the job's own saved spacing, on the END outcome.

The operator's case, on synthetic data (``tests/support/resume_spacing_fixture.py``: a 20-bullet job resume in the
shape of a real pick): at spacing 1.0 it is 3 pages, and the old scale (the gaps alone) could not bring it to 2 at
any spacing. Pinned here, on the PDF that comes back (pypdf reads it) and on Typst's own layout:

- AT 1.0 AND ABOVE NOTHING MOVES: where the resume ends is what the template before this change measured, to the
  last digit, and a body line is 14.3pt from the next;
- BELOW 1.0 the body's lines tighten too: 11.5pt at 0.7 (the 9.5pt type's own single-spaced line: the readability
  floor), the type size never changes, and the slider's range moves the resume by MANY bullets (the table in the
  test); the 20-bullet resume reaches 2 pages inside the range;
- THE PREVIEW AND THE PDF ARE ONE RENDER: ``POST /api/tailored-resumes/preview`` answers as many page pictures as the
  PDF of the same header and spacing has pages (the route, and the CLI with the person's header file);
- it OPENS at the fitted spacing (2 pages, nothing saved) and a spacing where 2 is not reached says "3 pages" plainly;
- A SLIDER MOVE IS SAVED FOR THIS JOB: one small file beside the job's stored resume and nothing else (the stored
  resume itself is byte for byte what it was: its lines, markdown, ``edited`` and ``updated_at``; no other file of
  the home, so never the master or the saved layout); a later read shows it, the PDF uses it, storing the job's
  resume again keeps it, and a file that cannot be read is ignored (the preview opens fitted).

Synthetic only: an invented person on reserved domains.
"""

from __future__ import annotations

import base64
import io
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from pypdf import PdfReader

from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.resume_display import SPACING_MAX, SPACING_MIN, display_path
from gigai.scout.resume_pdf import job_layout_path, job_spacing, measure_markdown, render_markdown_pdf, save_job_spacing
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailored_resume import list_tailored_resumes, tailored_resume_path

from tests.support.pipeline_fixtures import JOB, PipelineFixture, build_pipeline_fixture
from tests.support.resume_spacing_fixture import resume

STAMP = datetime(2026, 10, 6, tzinfo=timezone.utc)
PICKED = resume(20)
FORM = {
    "name": "Zora Quillfeather", "email": "zora.quillfeather@example.invalid", "phone": "+1 (555) 010-0142", "location": "Nowhere Springs, Colorado",
    "github": "zora-quillfeather", "linkedin": "zora-quillfeather", "work_authorization": "H-1B, requires sponsorship",
}
#: Where the 20-bullet resume ends (page, fill of that page) as the template BEFORE 0.1.11.5 measured it.
BEFORE = {1.0: (3, 0.36040462427745673), 1.2: (3, 0.3851156069364162), 1.4: (3, 0.40982658959537577)}
#: Role bullets that fit on 2 pages, with the header's room kept (measured 2026-10-07; before: 19 at 0.7, 14 at 1.0).
ROOM = {0.7: 28, 0.85: 20, 1.0: 14, 1.4: 11}


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _body_lines(spacing: float) -> tuple[float, set[float]]:
    """(the distance between the two printed lines of the first bullet, every type size of a bullet's text), in points."""

    pdf = render_markdown_pdf(PICKED, None, timestamp=STAMP, spacing_scale=spacing, auto_fit=False).pdf
    rows: list[tuple[float, float, str]] = []

    def seen(text: str, cm: list[float], _tm: list[float], _font: object, size: float) -> None:
        if text.strip():
            rows.append((cm[5], size, text.strip()))

    PdfReader(io.BytesIO(pdf)).pages[0].extract_text(visitor_text=seen)
    first = next(index for index, row in enumerate(rows) if row[2].startswith("Led the redesign"))
    assert rows[first + 1][2].startswith("pipeline and cutting"), rows[first : first + 2]
    return round(rows[first][0] - rows[first + 1][0], 2), {size for _y, size, text in rows if text.startswith(("Led the redesign", "pipeline and cutting"))}


def _fits(spacing: float) -> int:
    count = 0
    while measure_markdown(resume(count + 1), spacing_scale=spacing, printed=True)[0] <= 2:
        count += 1
    return count


def test_at_one_and_above_the_layout_is_what_it_was() -> None:
    for spacing, (page, fill) in BEFORE.items():
        measured = measure_markdown(PICKED, spacing_scale=spacing, printed=True)
        assert measured[0] == page and measured[1] == pytest.approx(fill, abs=1e-12), f"the layout at {spacing} moved: {measured}"
        assert _body_lines(spacing) == (14.3, {9.5})


def test_below_one_the_lines_tighten_down_to_the_readable_floor_and_the_type_never_shrinks() -> None:
    pitch, sizes = _body_lines(SPACING_MIN)
    # The floor: the 9.5pt type's own single-spaced line (Inter: ascender + descender = 1.21 x the size), so a line's
    # descenders never reach into the next line's ascenders.
    assert pitch == 11.5 and pitch >= (0.96875 + 0.2412109375) * 9.5 and sizes == {9.5}
    assert _body_lines(0.85) == (12.9, {9.5})


def test_the_slider_range_moves_a_twenty_bullet_resume_by_many_bullets() -> None:
    assert {spacing: _fits(spacing) for spacing in ROOM} == ROOM
    assert ROOM[SPACING_MIN] - ROOM[1.0] >= 5 and ROOM[SPACING_MIN] - ROOM[SPACING_MAX] >= 5
    # The 20-bullet resume: 3 pages at 1.0, 2 pages inside the range.
    assert measure_markdown(PICKED, spacing_scale=1.0, printed=True)[0] == 3 and measure_markdown(PICKED, spacing_scale=0.85, printed=True)[0] == 2
    # A page ESTIMATE (what a pick and the length rule budget with) keeps the full line height: it counts as it did.
    assert {spacing: measure_markdown(PICKED, spacing_scale=spacing) for spacing in (0.7, 0.85)} == {0.7: (3, pytest.approx(0.05865751445086707)), 0.85: (3, pytest.approx(0.09280491329479769))}


def _invoke(fx: PipelineFixture, *args: str) -> dict:
    result = CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    """The synthetic gig with the 20-bullet resume stored for ``JOB`` by the user's agent (an EDITED resume), and a master."""

    monkeypatch.setenv(PIPELINE_ENV, "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=PICKED)
    master = tmp_path / "master.md"
    master.write_text(resume(30, master=True), encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=master, gig_id=fx.gig.resolved.gig_id).status == "created"
    picked = tmp_path / "picked.md"
    picked.write_text(PICKED, encoding="utf-8")
    _invoke(fx, "resume", "store", "--in", str(picked), "--job-url", JOB, "--as", "agent")
    return fx


class _Server:
    def __init__(self, fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve

        monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")
        monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
        self.server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.server.server_address[1]}", timeout=120)
        self.key = {"profile_id": fx.profile_id, "job_identity": JOB}

    def preview(self, **body: object) -> dict:
        response = self.client.post("/api/tailored-resumes/preview", json={**self.key, **body})
        assert response.status_code == 200, response.text
        payload = response.json()
        pictures = [base64.b64decode(image) for image in payload["images"]]
        assert payload["image_type"] == "image/png" and all(picture.startswith(b"\x89PNG") for picture in pictures)
        assert len(pictures) == payload["pages"], "the preview's pictures are its pages"
        return payload

    def pdf(self, **body: object) -> httpx.Response:
        response = self.client.post("/api/tailored-resumes/pdf", json={**self.key, **body})
        assert response.status_code == 200, response.text
        return response

    def close(self) -> None:
        self.client.close()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch):
    running = _Server(fx, monkeypatch)
    yield running
    running.close()


def _stored(fx: PipelineFixture):
    return list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)[0]


def _home_files(fx: PipelineFixture) -> dict[str, bytes]:
    return {str(path.relative_to(fx.home_root)): path.read_bytes() for path in sorted(fx.home_root.rglob("*")) if path.is_file()}


def test_the_preview_opens_fitted_on_two_pages_and_saves_nothing(fx: PipelineFixture, server: _Server) -> None:
    before = _home_files(fx)
    opened = server.preview(header=FORM)
    assert (opened["pages"], opened["max_pages"], opened["saved"], opened["note"]) == (2, 2, False, None)
    assert SPACING_MIN <= opened["spacing_scale"] < 1.0 and opened["spacing"] == {"min": SPACING_MIN, "max": SPACING_MAX, "step": 0.05}
    # Asked again it is the same spacing, and the PDF that names no spacing is rendered at it: the same 2 pages.
    assert server.preview(header=FORM)["spacing_scale"] == opened["spacing_scale"]
    fitted = server.pdf(header=FORM)
    assert float(fitted.headers["x-gigai-spacing-scale"]) == opened["spacing_scale"] and _pages(fitted.content) == 2
    assert fitted.content == server.pdf(header=FORM, spacing_scale=opened["spacing_scale"]).content, "the fitted PDF is not the PDF at the spacing the preview shows"
    assert _home_files(fx) == before, "reading the preview wrote to the home"


def test_the_preview_and_the_pdf_are_the_same_pages_at_every_spacing(fx: PipelineFixture, server: _Server, tmp_path: Path) -> None:
    header_file = tmp_path / "header.json"
    header_file.write_text(json.dumps(FORM), encoding="utf-8")
    header_file.chmod(0o600)
    counts: dict[float, int] = {}
    for spacing in (0.7, 0.8, 0.85, 0.9, 1.0, 1.2, 1.4):
        shown = server.preview(header=FORM, spacing_scale=spacing)
        assert shown["spacing_scale"] == spacing and shown["saved"] is True
        named = server.pdf(header=FORM, spacing_scale=spacing)
        saved = server.pdf(header=FORM)  # no spacing named: the job's saved one
        assert named.content == saved.content, f"the PDF at the saved spacing {spacing} is not the PDF at that spacing named"
        assert _pages(named.content) == int(named.headers["x-gigai-pages"]) == shown["pages"], f"preview and PDF differ at {spacing}"
        assert float(named.headers["x-gigai-spacing-scale"]) == spacing
        # The CLI with the person's header file, at the job's saved spacing: the same pages.
        out = tmp_path / "out" / f"cli-{spacing}.pdf"
        payload = _invoke(fx, "resume", "pdf", "--job-url", JOB, "--header", str(header_file), "--out", str(out))
        assert (payload["spacing_scale"], payload["pages"], _pages(out.read_bytes())) == (spacing, shown["pages"], shown["pages"])
        # Without a header the count is the headerless PDF's (a blank block of the header's height is kept).
        assert server.preview()["pages"] == _pages(server.pdf().content)
        counts[spacing] = shown["pages"]
    # 0.9 is 2 pages with the Skills laid out compactly (the render does that at the spacing given before it says "3 pages").
    assert counts == {0.7: 2, 0.8: 2, 0.85: 2, 0.9: 2, 1.0: 3, 1.2: 3, 1.4: 3}


def test_a_spacing_that_does_not_reach_two_pages_says_three_pages_plainly(fx: PipelineFixture, server: _Server) -> None:
    loose = server.preview(header=FORM, spacing_scale=1.0)
    assert (loose["pages"], loose["max_pages"], loose["spacing_scale"]) == (3, 2, 1.0), "the slider's spacing was not used as given"
    assert loose["note"].startswith("This resume takes 3 pages at spacing 1.00: its limit is 2 pages. Move the spacing slider")
    pdf = server.pdf(header=FORM, spacing_scale=1.0)
    assert _pages(pdf.content) == 3 and pdf.headers["x-gigai-fit-note"] == loose["note"]
    for bad in (0.5, 1.6, "1.0", True):
        refused = server.client.post("/api/tailored-resumes/preview", json={**server.key, "spacing_scale": bad})
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_value"
    assert job_spacing(tailored_resume_path(fx.home_root, fx.target, fx.profile_id, JOB)) == 1.0, "a refused spacing was saved"


def test_a_slider_move_is_saved_for_this_job_and_nothing_else_is_written(fx: PipelineFixture, server: _Server, tmp_path: Path) -> None:
    stored_file = tailored_resume_path(fx.home_root, fx.target, fx.profile_id, JOB)
    layout = job_layout_path(stored_file)
    key = str(layout.relative_to(fx.home_root))
    before = _home_files(fx)
    was = _stored(fx)
    assert was.edited is not None and was.edited.written_by == "agent" and not layout.exists() and not display_path(fx.home_root).exists()

    moved = server.preview(header=FORM, spacing_scale=0.8)
    assert (moved["spacing_scale"], moved["saved"], moved["pages"]) == (0.8, True, 2)

    # ONE new small file beside the job's stored resume. Every other file of the home is byte for byte what it was: the
    # job's resume (its lines, markdown, edited mark, updated_at), the master, the display settings (there are none).
    after = _home_files(fx)
    assert {name for name in set(before) | set(after) if before.get(name) != after.get(name)} == {key}, "more than the job's spacing file was written"
    assert layout.parent == stored_file.parent and json.loads(after[key]) == {"spacing_percent": 80}, "nothing of the header, or of the resume, is in it"
    assert _stored(fx) == was and job_spacing(stored_file) == 0.8
    # The stored resume as the list route gives it is what it was: an older GigAI on this home reads it as before.
    listed = server.client.get("/api/tailored-resumes", params=server.key).json()["items"][0]
    assert "spacing_percent" not in listed and "spacing_scale" not in listed and listed["updated_at"] == was.updated_at and listed["markdown"] == was.markdown
    # A reload reads it: a preview that names no spacing. The downloaded PDF uses it.
    reopened = server.preview(header=FORM)
    assert (reopened["spacing_scale"], reopened["saved"], reopened["pages"]) == (0.8, True, 2)
    assert float(server.pdf(header=FORM).headers["x-gigai-spacing-scale"]) == 0.8

    # The job's resume is stored again (the agent hands back one bullet fewer): the job keeps its spacing.
    again = tmp_path / "again.md"
    again.write_text(resume(19), encoding="utf-8")
    _invoke(fx, "resume", "store", "--in", str(again), "--job-url", JOB, "--as", "agent")
    kept = _stored(fx)
    assert kept.markdown != was.markdown and "scheduling service 19," not in kept.markdown and kept.edited is not None
    assert job_spacing(stored_file) == 0.8 and server.preview(header=FORM)["spacing_scale"] == 0.8

    # A spacing file that cannot be read is ignored: the preview opens fitted again, and says nothing is saved.
    for unreadable in ("not json", '{"spacing_percent": 0.8}', '{"spacing_percent": 500}', "[80]"):
        layout.write_text(unreadable, encoding="utf-8")
        opened = server.preview(header=FORM)
        assert job_spacing(stored_file) is None and opened["saved"] is False and opened["pages"] == 2 and opened["note"] is None, unreadable


def test_a_copied_home_reopens_at_its_own_saved_spacing(fx: PipelineFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The orchestrator's spot check, on a copy of a home: the slider was saved (``.layout`` said 80) and the page
    reopened at the fitted 0.85, "Fitted automatically". A stored resume records the path it was first written at
    (``stored_path``), which in a copied or moved home names the ORIGINAL home; the saved spacing was looked for
    beside that path, and written beside the copy's. It is read where it is written: this home's store."""

    stored_file = tailored_resume_path(fx.home_root, fx.target, fx.profile_id, JOB)
    record = json.loads(stored_file.read_text(encoding="utf-8"))
    elsewhere = tmp_path / "the-original-home" / "scout" / "resumes" / stored_file.name
    for name in ("stored_path", "markdown_path"):
        record[name] = str(elsewhere.with_suffix(Path(record[name]).suffix))
    stored_file.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    # The store reads the resume as being where it is, not where it says it was first written.
    assert _stored(fx).stored_path == str(stored_file) and _stored(fx).markdown_path == str(stored_file.with_suffix(".md")) and not elsewhere.parent.exists()

    first = _Server(fx, monkeypatch)
    try:
        assert first.preview(header=FORM)["saved"] is False
        moved = first.preview(header=FORM, spacing_scale=0.8)
        assert (moved["spacing_scale"], moved["saved"]) == (0.8, True)
    finally:
        first.close()
    assert json.loads(job_layout_path(stored_file).read_text(encoding="utf-8")) == {"spacing_percent": 80} and not elsewhere.parent.exists()

    # A fresh server and a fresh client (the page opened again): the saved spacing, and it says so.
    again = _Server(fx, monkeypatch)
    try:
        reopened = again.preview()
        assert (reopened["spacing_scale"], reopened["saved"], reopened["pages"]) == (0.8, True, 2), "the page reopened without the job's saved spacing"
        assert float(again.pdf(header=FORM).headers["x-gigai-spacing-scale"]) == 0.8
    finally:
        again.close()
    out = tmp_path / "out" / "copied.pdf"
    assert _invoke(fx, "resume", "pdf", "--job-url", JOB, "--out", str(out))["spacing_scale"] == 0.8

    # A change of the resume itself (here: one line's wording, the same write path as Restore of what was cut for
    # length) is written to THIS home's file and is there on the next read; nothing is written at the recorded path.
    third = _Server(fx, monkeypatch)
    try:
        held = third.client.get("/api/tailored-resumes", params=third.key).json()["items"][0]
        line = next(line for section in held["result"]["sections"] for entry in section.get("entries", ()) for line in entry["bullets"])
        wording = "Led the redesign of the scheduling service."
        changed = third.client.put("/api/tailored-resumes/lines", json={**third.key, "updated_at": held["updated_at"], "line_id": line["id"], "use": "custom", "text": wording})
        assert changed.status_code == 200, changed.text
        assert wording in third.client.get("/api/tailored-resumes", params=third.key).json()["items"][0]["markdown"], "the change did not persist"
    finally:
        third.close()
    assert wording in stored_file.read_text(encoding="utf-8") and wording in stored_file.with_suffix(".md").read_text(encoding="utf-8")
    assert not elsewhere.parent.exists(), "a change of a copied home's resume was written at the path the resume recorded (the original home)"


def test_the_spacing_of_a_job_with_no_stored_resume_is_not_saved(fx: PipelineFixture, server: _Server) -> None:
    assert save_job_spacing(fx.home_root, fx.target, fx.profile_id, "https://jobs.example.test/none", 0.8) is None
    missing = server.client.post("/api/tailored-resumes/preview", json={**server.key, "job_identity": "https://jobs.example.test/none", "spacing_scale": 0.8})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "tailored_resume_not_found"
    with pytest.raises(ValueError):
        save_job_spacing(fx.home_root, fx.target, fx.profile_id, JOB, 2.0)
