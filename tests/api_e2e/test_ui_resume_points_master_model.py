"""0.1.11.5 (b++) and the "Add a point" finding: the points list's model, on invented lines.

``ui/src/resumePointsModel.js`` is pure JavaScript, run under the system ``node`` (LOUD skip when it is not on PATH).

- SAVE THIS WORDING TO MY MASTER: only an EDITED point that stands for a line of the master, and only while its
  words are not what the master says now, carries the action (``masterWording``: the master line's id, what the
  master says, what it will say). A point as the master has it, a point of no master line, a Skills line and a line
  the master no longer holds carry none. What the page says after the write names the next pick of the other jobs;
  a refusal is said in the page's words, with no id.
- ADD A POINT leaves out the master's own one-line list of earlier roles (an Other line that starts "Earlier
  experience"): the resume prints its earlier roles as a block of its layout, so that line is not a point to add.
  Every other Other line is still offered.

What lives in JSX is checked statically, by reading the source: the master is written only from the confirm's own
button, with the revision the confirm showed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

MASTER_SAYS = "Cut the nightly reconcile of **Larkspur** ledgers from 6 hours to 40 minutes."
MY_WORDS = "Cut the nightly ledger reconcile from 6 hours to 40 minutes for 300 clinics."
EARLIER = "Earlier experience: Senior scheduling engineer (Thistledown Market, 2011 - 2014); Analyst (Brindlecombe, 2008 - 2011)"

MASTER = {
    "revision": 7,
    "entries": [{"id": "r-lark", "section": "experience", "heading": "Larkspur Table Systems"}],
    "items": [
        {"id": "b-aaaaaa", "section": "experience", "kind": "bullet", "entry_id": "r-lark", "text": MASTER_SAYS},
        {"id": "b-bbbbbb", "section": "experience", "kind": "bullet", "entry_id": "r-lark", "text": "Ran the on-call rota for 14 engineers."},
        {"id": "b-cccccc", "section": "experience", "kind": "bullet", "entry_id": "r-lark", "text": "Moved 40 services to one deploy pipeline."},
        {"id": "sk-dddddd", "section": "skills", "kind": "skills", "entry_id": None, "text": "Languages: Python, Go"},
        {"id": "o-eeeeee", "section": "other", "kind": "other", "entry_id": None, "text": EARLIER},
        {"id": "o-ffffff", "section": "other", "kind": "other", "entry_id": None, "text": "Speaker, Harbour Systems Meetup 2023: scheduling under load."},
        {"id": "o-gggggg", "section": "other", "kind": "other", "entry_id": None, "text": "**Earlier experience**"},
    ],
}


def _copy(line_id: str, item_id: str, text: str) -> dict:
    return {"id": line_id, "kind": "copy", "text": f"- {text}", "refs": [{"kind": "resume", "item_id": item_id, "text": text}]}


def _custom(line_id: str, item_id: str | None, text: str, was: str) -> dict:
    return {"id": line_id, "kind": "custom", "text": text, "refs": [], "edited_from": _copy(line_id, item_id, was) if item_id else None}


STORED = {
    "selection": {
        "picked_by": "model",
        "picked": [{"id": "b-aaaaaa", "code": "evidence", "reason": ""}, {"id": "b-bbbbbb", "code": "evidence", "reason": ""}],
        "left_out": [{"id": item, "code": "lower_value", "reason": ""} for item in ("b-cccccc", "sk-dddddd", "o-eeeeee", "o-ffffff", "o-gggggg")],
    },
    "result": {
        "sections": [
            {
                "heading": "experience",
                "entries": [
                    {
                        "heading": [{"id": "L1", "kind": "copy", "text": "### Larkspur Table Systems", "refs": [{"kind": "resume", "item_id": "r-lark", "text": "Larkspur Table Systems"}]}],
                        "bullets": [
                            _custom("L2", "b-aaaaaa", MY_WORDS, MASTER_SAYS),
                            _copy("L3", "b-bbbbbb", "Ran the on-call rota for 14 engineers."),
                            _custom("L4", None, "A line typed from nothing.", ""),
                            _custom("L5", "b-zzzzzz", "A line whose master line is gone.", "Gone."),
                        ],
                    }
                ],
            }
        ]
    },
}

NODE_SCRIPT = """
import { readFileSync } from "node:fs";
const points = await import(process.argv[1]);
const input = JSON.parse(readFileSync(0, "utf8"));
const lines = points.pointGroups(input.stored).flatMap((group) => group.lines);
const wording = (line, master = input.master) => points.masterWording(line, master);
const choices = points.leftOutChoices(input.stored, input.master);
const err = (code) => points.masterSaveErrorText({ code, detail: "master line 'b-aaaaaa' at revision 7" });
console.log(JSON.stringify({
  wordings: lines.map((line) => wording(line)),
  noMaster: wording(lines[0], null),
  sameAsMaster: wording({ ...lines[0], text: input.masterSays }),
  sameWithoutMarkers: wording({ ...lines[0], text: "Cut the nightly reconcile of Larkspur ledgers from 6 hours to 40 minutes." }),
  sameSpaced: wording({ ...lines[0], text: "  Cut the nightly reconcile of **Larkspur**   ledgers from 6 hours to 40 minutes. " }),
  skills: wording({ id: "L9", itemId: "sk-dddddd", text: "Languages: Python", edited: true }),
  needs: [points.needsMaster(input.stored), points.needsMaster({ ...input.stored, selection: null }), points.needsMaster({ selection: {}, result: { sections: [] } })],
  saved: [points.masterSavedText({ status: "revised", master: { revision: 8 } }), points.masterSavedText({ status: "unchanged", master: { revision: 7 } })],
  errors: ["revision_conflict", "master_item_not_found", "personal_info_refused", "master_line_exists", "master_not_found", "something_else"].map(err),
  label: points.SAVE_TO_MASTER_LABEL,
  choices: { total: choices.total, lines: choices.groups.flatMap((group) => group.lines.map((line) => [group.label, line.id])) },
  earlier: input.master.items.map((item) => [item.id, points.isEarlierRolesLine(item)]),
}));
"""
_ID = re.compile(r"\bb-[0-9a-f]{4,}\b|\bL\d+\b|revision_conflict|master_item|master_line|personal_info")


@pytest.fixture(scope="module")
def out() -> dict:
    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the points list's master model was NOT checked")
    done = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, (UI_SRC / "resumePointsModel.js").as_uri()],
        input=json.dumps({"stored": STORED, "master": MASTER, "masterSays": MASTER_SAYS}), capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_only_an_edited_point_whose_words_differ_from_the_master_carries_the_action(out: dict) -> None:
    edited, copied, typed, gone = out["wordings"]
    assert edited == {"id": "b-aaaaaa", "from": MASTER_SAYS, "to": MY_WORDS}, "the edited point does not say which master line it would change, from what, to what"
    assert copied is None, "a point as the master has it offers to save its wording"
    assert typed is None and gone is None, "a point with no line in the master offers to save its wording"
    assert out["noMaster"] is None, "the action is offered before the master is read: it could not say what it changes"
    assert out["sameAsMaster"] is None and out["sameWithoutMarkers"] is None and out["sameSpaced"] is None, "a point whose wording equals the master's shows the action"
    assert out["skills"] is None, "a Skills line is not a point"
    assert out["label"] == "Save this wording to my master"
    # The master is read for a resume with an edited point of a master line (to compare the words), not otherwise.
    assert out["needs"] == [True, False, False]


def test_what_the_page_says_after_the_write_and_when_it_is_refused(out: dict) -> None:
    assert out["saved"] == [
        "Saved to your master (revision 8): that one line. This job's resume already has these words; other jobs take the new wording on their next pick.",
        "Your master already says this. Nothing was changed.",
    ]
    conflict, missing, personal, exists, no_master, other = out["errors"]
    assert conflict == "Not saved: your master changed after this page read it. It is shown as it is now; look at the change again before you save it."
    assert missing == "Not saved: that line is no longer in your master. Nothing was changed."
    assert personal == "Not saved: a line of your master cannot hold a name or contact details. Nothing was changed."
    assert exists == "Not saved: your master already has a line with exactly these words. Nothing was changed."
    assert no_master == "Not saved: there is no master resume to save it to."
    assert other == "Not saved: your master could not be changed. Nothing was changed; try again."
    assert not any(_ID.search(text) for text in out["errors"]), "a refusal shows an id or a code"


def test_add_a_point_leaves_out_the_masters_one_line_list_of_earlier_roles(out: dict) -> None:
    """The operator's finding: the list started with an "Other" entry "Earlier experience: Senior ... (...); ..."."""

    offered = out["choices"]["lines"]
    assert ["Other", "o-eeeeee"] not in offered and ["Other", "o-gggggg"] not in offered, "the earlier-roles line is offered as a point to add"
    assert offered == [["Other", "o-ffffff"], ["Larkspur Table Systems", "b-cccccc"]], offered
    assert out["choices"]["total"] == 2, "the count (and the search box) still counts the line that is left out"
    assert dict(out["earlier"]) == {"b-aaaaaa": False, "b-bbbbbb": False, "b-cccccc": False, "sk-dddddd": False, "o-eeeeee": True, "o-ffffff": False, "o-gggggg": True}


def test_the_master_is_written_only_from_the_confirm_with_the_revision_it_showed() -> None:
    source = (UI_SRC / "components" / "ResumePoints.jsx").read_text(encoding="utf-8")
    assert source.count("putMasterLine(") == 1, "the master has more than one writer in the points list (or none)"
    assert "putMasterLine({ revision: ask.revision, id: ask.id, use: \"edit\", text: ask.to })" in source, "the write is not the one the confirm showed"
    assert source.count('data-action="confirm-master"') == 1 and source.count('data-action="save-to-master"') == 1 and source.count('data-action="cancel-master"') == 1
    assert "postMasterLine" not in source and "putMasterEntry" not in source, "the points list adds to the master or edits a role"
    assert "localStorage" not in source and "sessionStorage" not in source
