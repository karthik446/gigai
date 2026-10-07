"""0.1.11.5 (b): update, delete or add a POINT of a job's resume, on the END outcome (the preview and the PDF that come back).

The job page lists the points of the job's resume beside its preview; each change is ONE request to a route that was
there already and is saved at once for THIS job's resume. On the pick fixture of ``test_pick_header_room.py`` (a job
assessed against an invented 56-line master; its resume picked by the assessment: its 20 best bullets, the rest under
Left out), through the real routes of a real server. THE PICK COUNTS NO PAGE (0.1.11.5 item 1c), so no page count is
written into this test: the BASELINE is what the preview says for the pick at the job's saved spacing (several pages
on this fixture's long lines; read again after the edit, whose shorter words can end the resume a page earlier), and
every count after it is that baseline or one page more:

- an EDIT (``PUT /api/tailored-resumes/lines`` ``use: custom``) prints the new words in the PDF and no longer the old;
- an ADD of a left-out line (``PUT /api/tailored-resumes/selection`` ``use: add``, ``fit: keep``: nothing is cut to
  make room, the person fits the page with the slider) prints the line under its own role, last in it; enough of
  them and the preview says one page more than the baseline, and the PDF has as many;
- a REMOVE takes the line off: the preview and the PDF are back on the baseline's pages and the line's words are gone;
- the preview and the PDF agree after every change, and a fresh read of the store (a reload) shows the saved state;
- NOTHING but this job's resume is written: the master's files and the job's saved spacing (its ``.layout`` file) are
  byte for byte what they were;
- a line that does not exist is refused in a typed answer and nothing is written (an Add of a line the master does
  not have, a Remove of a line the resume does not show, an edit of a line it does not have, empty words);
- THE EDITED FLAG (existing behaviour, kept): an edit, a Remove and an Add each make the resume the user's: a
  re-pick then stores nothing over it and waits beside it as the new suggested resume. ``edited`` (the mark of a
  resume handed back whole) is not set by any of the three;
- on a COPIED home (the stored resume records the paths of the home it was first written in) an Add and a Remove
  are written to THIS home's file, never at the recorded path.

Synthetic only: an invented person, employers and lines.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from gigai.scout.resume_pdf import job_layout_path
from gigai.scout.tailored_resume import list_tailored_resumes, tailored_resume_path

from tests.behaviors.scout_find_jobs.test_pick_header_room import _JOB, _URL, _Server, _assess, _master, _ok, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.support.posting_fixtures import PostingsFixture

EDITED = "Rebuilt the night-shift roster engine so a schedule change reaches every clinic in under a minute."
LINES = "/api/tailored-resumes/lines"
SELECTION = "/api/tailored-resumes/selection"


def _files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _masters(files: dict[str, bytes]) -> dict[str, bytes]:
    return {name: data for name, data in files.items() if "master" in name}


def _held(srv: _Server) -> dict:
    return srv.client.get("/api/tailored-resumes", params=srv.key).json()["items"][0]


def _bullets(held: dict) -> list[dict]:
    return [line for section in held["result"]["sections"] for entry in section.get("entries", ()) for line in entry["bullets"]]


def _item(line: dict) -> str:
    base = line.get("edited_from") or line
    return next(ref["item_id"] for ref in base["refs"] if ref.get("item_id"))


def _preview(srv: _Server, **body: object) -> dict:
    response = srv.client.post("/api/tailored-resumes/preview", json={**srv.key, **body})
    assert response.status_code == 200, response.text
    return response.json()


def _pdf(srv: _Server) -> list[str]:
    response = srv.client.post("/api/tailored-resumes/pdf", json=srv.key)
    assert response.status_code == 200, response.text
    return [" ".join(page.extract_text().split()) for page in PdfReader(io.BytesIO(response.content)).pages]


def _put(srv: _Server, route: str, held: dict, **body: object):
    return srv.client.put(route, json={**srv.key, "updated_at": held["updated_at"], **body})


def _same(srv: _Server, pages: int) -> list[str]:
    """The PDF's pages as text, once the preview is seen to have as many (the two are one render)."""

    preview, pdf = _preview(srv), _pdf(srv)
    assert preview["pages"] == len(preview["images"]) == len(pdf) == pages, f"the preview says {preview['pages']} pages, the PDF has {len(pdf)}, expected {pages}"
    return pdf


def test_an_edit_an_add_and_a_remove_each_show_in_the_preview_and_the_pdf_and_only_this_jobs_resume_is_written(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    _assess(fx)
    path = tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    # The person left the slider at 0.90 (saved for the job): the page count below is at THAT spacing, not a new fit.
    assert _preview(server, spacing_scale=0.9)["saved"] is True
    layout = job_layout_path(path).read_bytes()
    before = _files(fx.home_root)
    first = _held(server)
    master = {item.id: item for item in _master(fx).items.values()}
    assert first["selection"] and first.get("edited") is None
    # THE BASELINE: the pick's pages as the preview counts them at the saved spacing (the pick itself counts none).
    base = _preview(server)["pages"]
    assert base >= 1 and _preview(server)["spacing_scale"] == 0.9
    start = " ".join(_same(server, base))

    # --- EDIT: the new words print, the old ones do not ---
    line = _bullets(first)[0]
    old = line["text"].lstrip("- ")
    assert old in start and EDITED not in start
    answer = _put(server, LINES, first, line_id=line["id"], use="custom", text=EDITED)
    assert answer.status_code == 200, answer.text
    # The new words are far shorter than the line they replace, so the resume may now end a page earlier: the count is
    # read again (preview and PDF still agree on it), and it is the baseline of the Adds and Removes below.
    picked, base = base, _preview(server)["pages"]
    assert base in (picked, picked - 1), "an edit to shorter words made the resume longer, or shorter by more than a page"
    edited = " ".join(_same(server, base))
    assert EDITED in edited and old not in edited, "the edit is not what the PDF prints"

    # --- ADD: left-out lines go on under their own role, last in it; nothing is cut to make room; the count follows ---
    left = [entry["id"] for entry in first["selection"]["left_out"] if master[entry["id"]].kind == "bullet"]
    assert len(left) >= 12
    added: list[str] = []
    pages = base
    for item_id in left:
        answer = _put(server, SELECTION, _held(server), use="add", item_id=item_id, fit="keep")
        assert answer.status_code == 200, answer.text
        change = answer.json()["selection_change"]
        assert (change["applied"], change["changed"], change["cut"]) == (True, True, []), "an Add with fit keep cut a line to make room"
        added.append(item_id)
        pages = _preview(server)["pages"]
        assert pages in (base, base + 1), "one added point moved the count by more than a page"
        if pages == base + 1:
            break
    assert pages == base + 1, "adding points never moved the page count"
    grown = " ".join(_same(server, base + 1))
    assert all(master[item_id].text in grown for item_id in added) and EDITED in grown
    held = _held(server)
    last = added[-1]
    role = next(entry for section in held["result"]["sections"] for entry in section.get("entries", ()) if any(_item(line) == last for line in entry["bullets"]))
    assert _item(role["heading"][0]) == master[last].entry_id and _item(role["bullets"][-1]) == last, "an added line is not the last line of its own role"
    assert {entry["id"]: entry["code"] for entry in held["selection"]["picked"]}[last] == "added_by_you"

    # --- REMOVE: the lines are off again, and the count is back ---
    for item_id in added:
        answer = _put(server, SELECTION, _held(server), use="remove", item_id=item_id)
        assert answer.status_code == 200, answer.text
    shrunk = " ".join(_same(server, base))
    assert not any(master[item_id].text in shrunk for item_id in added) and EDITED in shrunk
    held = _held(server)
    assert {entry["id"]: entry["code"] for entry in held["selection"]["left_out"]}[last] == "removed_by_you"

    # --- a reload (a fresh read of the store) shows the saved state; `edited` and `updated_at` are the pick's ---
    on_disk = list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.default_profile_id, job_identity=_JOB)[0].to_json()
    assert all(on_disk[part] == held[part] for part in ("result", "markdown", "selection")) and EDITED in on_disk["markdown"] and (on_disk.get("edited"), on_disk["updated_at"]) == (None, first["updated_at"])
    custom = next(bullet for bullet in _bullets(on_disk) if bullet["id"] == line["id"])
    assert (custom["kind"], custom["text"], custom["origin"], custom["edited_from"]["text"]) == ("custom", EDITED, "user", line["text"])

    # --- nothing of the master, and not the job's saved spacing, was written ---
    after = _files(fx.home_root)
    assert _masters(after) == _masters(before) and _masters(before), "a change of a job's points wrote the master"
    assert job_layout_path(path).read_bytes() == layout, "a change of a job's points wrote the job's saved spacing"
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    # What was written: this job's stored resume (its two files) and its copy in the jobs folder (with that folder's index).
    own = {str(path.relative_to(fx.home_root)), str(path.with_suffix(".md").relative_to(fx.home_root))}
    assert own <= set(changed) and len(changed) == 4, f"more than this job's resume was written: {changed}"
    assert all(name.startswith("jobs/") and name.endswith("/resume.md") or name == "scout/jobs-folder-index.json" for name in set(changed) - own), changed


def test_a_line_that_does_not_exist_is_refused_and_nothing_is_written(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    _assess(fx)
    held = _held(server)
    before = _files(fx.home_root)
    refused = [
        (_put(server, SELECTION, held, use="add", item_id="b-000000", fit="keep"), 404, "master_line_not_found"),
        (_put(server, SELECTION, held, use="remove", item_id="b-000000"), None, "selection_line_not_shown"),
        (_put(server, LINES, held, line_id="L9999", use="custom", text=EDITED), 422, "invalid_value"),
        (_put(server, LINES, held, line_id=_bullets(held)[0]["id"], use="custom", text="   "), 422, "invalid_value"),
        (_put(server, LINES, held, line_id=_bullets(held)[0]["id"], use="custom", text="x" * 401), 422, "invalid_value"),
    ]
    for answer, status, code in refused:
        assert answer.status_code >= 400 and (status is None or answer.status_code == status), answer.text
        assert answer.json()["error"]["code"] == code, answer.text
    assert _files(fx.home_root) == before, "a refused change wrote something"
    assert _held(server) == held


@pytest.mark.parametrize("change", ["edit", "remove", "add"])
def test_a_changed_point_makes_the_resume_the_users_and_a_re_pick_waits_beside_it(fx: PostingsFixture, server: _Server, change: str) -> None:  # noqa: F811
    _assess(fx)
    path = tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    # Untouched, the pick is Scout's own: a re-pick may replace it.
    assert _ok(fx, "resume", "pick", "--job-url", _URL)["resume"]["replaceable"] is True
    held = _held(server)
    if change == "edit":
        answer = _put(server, LINES, held, line_id=_bullets(held)[0]["id"], use="custom", text=EDITED)
    elif change == "remove":
        answer = _put(server, SELECTION, held, use="remove", item_id=_item(_bullets(held)[0]))
    else:
        answer = _put(server, SELECTION, held, use="add", item_id=held["selection"]["left_out"][0]["id"], fit="keep")
    assert answer.status_code == 200, answer.text
    mine = path.read_bytes()
    assert json.loads(mine).get("edited") is None, "`edited` marks a resume handed back whole, not a changed point"

    again = _ok(fx, "resume", "pick", "--job-url", _URL, "--refresh")
    assert path.read_bytes() == mine, f"a re-pick replaced a resume whose point the user changed ({change})"
    assert again["resume"]["replaceable"] is False and again["proposed"] is not None, "the new pick does not wait beside the user's resume"


def test_on_a_copied_home_an_add_and_a_remove_are_written_to_this_homes_file(fx: PostingsFixture, server: _Server, tmp_path: Path) -> None:  # noqa: F811
    _assess(fx)
    stored_file = tailored_resume_path(fx.home_root, fx.target, fx.default_profile_id, _JOB)
    record = json.loads(stored_file.read_text(encoding="utf-8"))
    elsewhere = tmp_path / "the-original-home" / "scout" / "resumes" / stored_file.name
    for name in ("stored_path", "markdown_path"):
        record[name] = str(elsewhere.with_suffix(Path(record[name]).suffix))
    stored_file.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")

    held = _held(server)
    master = {item.id: item for item in _master(fx).items.values()}
    gone = _item(_bullets(held)[0])
    come = next(entry["id"] for entry in held["selection"]["left_out"] if master[entry["id"]].kind == "bullet")
    assert _put(server, SELECTION, held, use="remove", item_id=gone).status_code == 200
    assert _put(server, SELECTION, held, use="add", item_id=come, fit="keep").status_code == 200
    for file in (stored_file, stored_file.with_suffix(".md")):
        text = file.read_text(encoding="utf-8")
        assert master[come].text in text and master[gone].text not in (text if file.suffix == ".md" else json.loads(text)["markdown"])
    assert master[come].text in _held(server)["markdown"], "the change is not there on the next read"
    assert not elsewhere.parent.exists(), "a change of a copied home's resume was written at the path the resume recorded (the original home)"
