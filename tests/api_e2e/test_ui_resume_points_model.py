"""0.1.11.5 (b): the points of a job's resume beside its preview, UI side.

``ui/src/resumePointsModel.js`` and ``masterModel.pickedLeftOut`` are pure JavaScript, run under the system ``node``
(LOUD skip when it is not on PATH), on the stored resume of a REAL pick (the pick fixture of
``test_pick_header_room.py``, read through the real routes):

- the list is the stored resume's Summary and each role's bullets, under their role, in the order they print;
- "Add a point" offers the master's lines the resume leaves out, by role in the master's order (newest first), a line
  the person removed first in its role as "Put back", never a Skills line; the search keeps the lines with every word;
- Picked / Left out FOLLOW THE STORED RESUME: after Restore (``PUT /api/tailored-resumes/length``), which puts the
  lines cut for length back without writing the pick's record, the two counts are what the resume prints (the
  orchestrator's re-check: the buttons stayed at the pick's numbers);
- an edit sends nothing when the box holds what the point says; a refusal is said in the page's words, with no id.

What lives in JSX is checked statically, by reading the source: the preview is asked for again when the stored
resume's text changes, from the ONE effect that asks for it; an Add never asks the server to cut for room; nothing is
kept in the browser's storage.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

from tests.behaviors.scout_find_jobs.test_pick_header_room import _JOB, _Server, _assess, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.support.old_pick_fixture import stored_as_before_0_1_11_5
from tests.support.posting_fixtures import PostingsFixture

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
import { readFileSync } from "node:fs";
const points = await import(process.argv[1]);
const master = await import(process.argv[2]);
const input = JSON.parse(readFileSync(0, "utf8"));
const counts = (stored) => master.pickedLeftOut(stored, input.master).counts;
const choices = (stored, query) => points.leftOutChoices(stored, input.master, query);
const flat = (view) => view.groups.flatMap((group) => group.lines.map((line) => ({ ...line, label: group.label })));
const err = (code, detail) => points.pointErrorText({ code, detail });
console.log(JSON.stringify({
  groups: points.pointGroups(input.picked),
  count: points.pointCount(points.pointGroups(input.picked)),
  label: points.pointLabel({ label: "Thistledown Market" }, 1),
  movable: [points.canMovePoints(input.picked), points.canMovePoints({ result: input.picked.result }), points.canMovePoints(null)],
  none: points.pointGroups(null),
  picked: counts(input.picked),
  restored: counts(input.restored),
  restoredCodes: [...new Set(master.pickedLeftOut(input.restored, input.master).picked.flatMap((group) => group.lines.map((line) => line.code)))].sort(),
  removed: counts(input.removed),
  choices: { total: choices(input.picked).total, lines: flat(choices(input.picked)), labels: choices(input.picked).groups.map((group) => group.label) },
  afterRemove: flat(choices(input.removed)).slice(0, 2),
  afterRemoveLabels: choices(input.removed).groups.map((group) => group.label),
  search: flat(choices(input.picked, "role 2 LINE 5:")).map((line) => line.text),
  searchRole: choices(input.picked, "lanternfish").groups.map((group) => group.label),
  searchNone: choices(input.picked, "zebra"),
  restoredChoices: choices(input.restored).total,
  edits: [points.editOf({ text: "As it is." }, "As it is."), points.editOf({ text: "As it is." }, "  As it   is. "), points.editOf({ text: "As it is." }, "   "), points.editOf({ text: "As it is." }, " New\\n words. ")],
  errors: ["master_line_not_found", "selection_line_not_shown", "tailored_resume_changed", "personal_info_refused", "selection_line_unsupported", "something_else"].map((code) => err(code, "master line 'b-12ab34'")),
  tooLong: err("invalid_value", "text is longer than 400 characters"),
  noLine: err("invalid_value", "no line 'L9' in this tailored resume"),
  texts: [points.SAVING_TEXT, points.SAVED_TEXT, points.EMPTY_TEXT, points.NO_SELECTION_TEXT],
  limit: points.MAX_POINT_CHARS,
}));
"""
_ID = re.compile(r"\bb-[0-9a-f]{4,}\b|\bL\d+\b|req-[0-9a-f]+|tailored_resume|item_id|selection_")


def _run(payload: dict) -> dict:
    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the resume points model was NOT checked")
    done = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, (UI_SRC / "resumePointsModel.js").as_uri(), (UI_SRC / "masterModel.js").as_uri()],
        input=json.dumps(payload), capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _held(srv: _Server) -> dict:
    return srv.client.get("/api/tailored-resumes", params=srv.key).json()["items"][0]


def _bullets(held: dict) -> list[dict]:
    return [line for section in held["result"]["sections"] for entry in section.get("entries", ()) for line in entry["bullets"]]


@pytest.fixture
def out(fx: PostingsFixture, server: _Server) -> dict:  # noqa: F811
    """The model's answers on a real pick: as picked, after its first bullet is removed, and after Restore.

    0.1.11.5 FX: the pick is stored the way a pick BEFORE 0.1.11.5 left it (lines cut for length, on its length
    record). A pick of 0.1.11.5 counts no page and cuts nothing, so Restore had no line to put back and the proof
    that the counts FOLLOW THE STORED RESUME after it could not move (``20 > 20``)."""

    _assess(fx)
    stored_as_before_0_1_11_5(fx, _JOB)
    picked = _held(server)
    master = server.client.get("/api/master").json()["master"]
    key = {**server.key, "updated_at": picked["updated_at"]}
    victim = _bullets(picked)[0]["refs"][0]["item_id"]
    removed = server.client.put("/api/tailored-resumes/selection", json={**key, "use": "remove", "item_id": victim})
    assert removed.status_code == 200, removed.text
    back = server.client.put("/api/tailored-resumes/selection", json={**key, "use": "add", "item_id": victim, "fit": "keep"})
    assert back.status_code == 200, back.text
    restored = server.client.put("/api/tailored-resumes/length", json={**key, "use": "restore"})
    assert restored.status_code == 200, restored.text
    answers = _run({"picked": picked, "removed": {name: value for name, value in removed.json().items() if name != "selection_change"}, "restored": restored.json(), "master": master})
    return {**answers, "_picked": picked, "_before_restore": back.json(), "_restored": restored.json(), "_master": master, "_victim": victim}


def test_the_list_is_the_stored_resumes_points_under_their_roles(out: dict) -> None:
    picked = out["_picked"]
    sections = {section["heading"]: section for section in picked["result"]["sections"]}
    roles = [entry for entry in sections["experience"]["entries"] if entry["bullets"]]
    groups = out["groups"]
    assert [group["label"] for group in groups] == ["Summary", *(entry["heading"][0]["text"].lstrip("# ") for entry in roles)], "the points are not in the order the resume prints them"
    for group, entry in zip(groups[1:], roles):
        assert [(line["id"], line["text"], line["edited"]) for line in group["lines"]] == [(line["id"], line["text"].lstrip("- "), False) for line in entry["bullets"]]
        assert all(line["itemId"] == source["refs"][0]["item_id"] for line, source in zip(group["lines"], entry["bullets"]))
    assert out["count"] == 1 + len(_bullets(picked)) and "Python, Go" not in json.dumps(groups), "the Skills line is not a point"
    assert out["label"] == "Point 2 of Thistledown Market" and out["movable"] == [True, False, False] and out["none"] == []


def test_add_a_point_offers_the_left_out_lines_by_role_newest_first(out: dict) -> None:
    master, picked = out["_master"], out["_picked"]
    items = {item["id"]: item for item in master["items"]}
    left = [line["id"] for line in picked["selection"]["left_out"] if items[line["id"]]["kind"] == "bullet"]
    choices = out["choices"]
    assert choices["total"] == len(left) and sorted(line["id"] for line in choices["lines"]) == sorted(left)
    assert all(line["text"] == items[line["id"]]["text"] and line["removed"] is False for line in choices["lines"])
    order = [entry["heading"] for entry in master["entries"]]
    assert choices["labels"] == [heading for heading in order if heading in choices["labels"]] and len(choices["labels"]) >= 2, "the roles are not in the master's order"
    # A line the person removed is offered first in its role, to put back.
    first = out["afterRemove"][0]
    assert (first["id"], first["removed"], first["text"]) == (out["_victim"], True, items[out["_victim"]]["text"]) and out["afterRemove"][1]["removed"] is False
    assert out["afterRemoveLabels"][0] == order[0]
    # The search keeps the lines with every word (any case), in the line or in its role's name.
    assert out["search"] and all(text.startswith("Role 2 line 5:") for text in out["search"])
    assert out["searchRole"] == [heading for heading in order if "Lanternfish" in heading] and out["searchNone"] == {"total": len(left), "groups": []}


def test_picked_and_left_out_follow_the_stored_resume_after_restore(out: dict) -> None:
    picked, restored = out["_picked"], out["_restored"]
    selection = picked["selection"]
    assert out["picked"] == {"picked": len(selection["picked"]), "leftOut": len(selection["left_out"])}, "a pick's own counts moved"
    assert out["removed"] == {"picked": len(selection["picked"]) - 1, "leftOut": len(selection["left_out"]) + 1}
    # Restore put the lines cut for length back; the pick's record is as it was, the resume prints them.
    assert restored["selection"] == out["_before_restore"]["selection"] and len(_bullets(restored)) > len(_bullets(picked))
    printed = 1 + len(_bullets(restored))
    assert out["restored"] == {"picked": printed, "leftOut": len(selection["picked"]) + len(selection["left_out"]) - printed}, (
        f"after Restore the buttons say {out['restored']}, the resume prints {printed} of the master's lines"
    )
    assert "shown_again" in out["restoredCodes"] and out["restoredChoices"] == out["restored"]["leftOut"]


def test_an_edit_sends_only_new_words_and_a_refusal_is_said_plainly() -> None:
    answers = _run({"picked": {"result": {"sections": []}, "selection": {"picked": [], "left_out": []}}, "removed": None, "restored": None, "master": {"items": [], "entries": []}})
    assert answers["edits"] == [None, None, None, "New words."]
    assert answers["errors"][0] == "Not added: that line is no longer in your master. Nothing was changed."
    assert answers["errors"][1] == "Not removed: that point is no longer on this resume. Nothing was changed."
    assert answers["errors"][5] == "Not saved: the change could not be stored. Nothing was changed; try again."
    assert answers["tooLong"] == "Not saved: a point is at most 400 characters." and answers["limit"] == 400
    assert answers["noLine"] == "Not saved: that point is no longer on this resume. Nothing was changed."
    for sentence in (*answers["errors"], answers["tooLong"], answers["noLine"], *answers["texts"]):
        assert sentence and not _ID.search(sentence), sentence
    assert answers["texts"][1] == "Saved for this job. Your master is unchanged."


def test_the_preview_follows_the_stored_resume_from_one_place_and_the_list_keeps_nothing() -> None:
    code = lambda path: "\n".join(line for line in (UI_SRC / path).read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//"))  # noqa: E731
    preview, panel, points, model = (code(name) for name in ("components/ResumePreview.jsx", "components/JobResumePanel.jsx", "components/ResumePoints.jsx", "resumePointsModel.js"))
    # ONE effect asks for the preview, and the stored resume's text is one of the things it follows: every change of
    # the lines (a point, Restore, a line choice) reaches it through the panel's stored resume, no button asks.
    assert preview.count("postResumePreview(") == 1 and preview.count("useEffect(") == 1
    assert "}, [profileId, jobIdentity, wanted, slider, content]);" in preview
    assert "const wait = !asked.current || rewritten ? 0 :" in preview, "a change of the lines is rendered at once"
    assert 'content={stored.markdown || ""}' in panel and "postResumePreview" not in panel and "postResumePreview" not in points
    # The list writes through the two routes that were there, and an Add never asks for a cut.
    assert points.count("putTailoredResumeLine(") == 1 and points.count("putTailoredResumeSelection(") == 1 and 'fit: use === "add" ? "keep" : undefined' in points
    # 0.1.11.5 (b++): the list writes the master in ONE place, the confirm of "Save this wording to my master"
    # (test_ui_resume_points_master_model.py pins that write); an edit, a Remove and an Add never do.
    assert points.count("putMasterLine(") == 1 and "const saveToMaster = useCallback((ask) =>" in points and "/api/master/" not in points, "the list writes the master outside the confirm"
    assert "putMasterLine" not in points.split("const saveToMaster = useCallback((ask) =>")[0].split("export default function ResumePoints")[1], "an edit, a Remove or an Add writes the master"
    for source in (points, model):
        for kept in ("localStorage", "sessionStorage", "document.cookie", "indexedDB", "location.hash", "history.", "console.", "dangerouslySetInnerHTML"):
            assert kept not in source, kept
