"""0.1.10.9 master P5: the UI's master resume rules (``ui/src/masterModel.js``), under node.

The Master page, the profiles' selection status and Picked / Left out on the
job page are thin React over these pure functions.  What is pinned:

* the page lists the master BY ROLE, with the strength mark the server derived
  and the revision it read; a write that crosses the agent's (409) says so;
* a profile's status line and the ONE button it may offer: nothing for a
  current selection, Refresh for "3 new master lines: refresh?", "Print its
  resume again" for a stale view, a way back for a detached one;
* the migration: blocked / nothing to do / questions (all must be answered) / ready;
* Picked / Left out: grouped by role, a picked line's text from the resume, a
  left-out one's from the master, the reason from the stored selection; an Add
  that needs room is a question naming what would be cut;
* "save this wording to your master" is offered only for an edited line that
  replaced a master line.

The master is the product's own JSON (the spike's synthetic master through
``parse_master``), not hand-written.  LOUD skip when ``node`` is missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.master_resume import parse_master

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
SRC = UI / "src"
MODEL_JS = SRC / "masterModel.js"
MASTER = parse_master((Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "master" / "master.md").read_text(encoding="utf-8"))


def _master_json(revision: int = 3, written_by: str = "agent") -> dict:
    return {**MASTER.to_json(), "revision": revision, "written_by": written_by, "updated_at": "2026-10-04T10:05:00.000000Z"}


def _run(body: str, payload: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; master model check not run")
    script = f"import * as model from {json.dumps(MODEL_JS.resolve().as_uri())};\nconst input = JSON.parse(process.argv[1]);\nconst out = {{}};\n{body}\nprocess.stdout.write(JSON.stringify(out));\n"
    completed = subprocess.run([node, "--input-type=module", "-e", script, "--", json.dumps(payload)], capture_output=True, text=True, timeout=120, check=False, cwd=UI)
    assert completed.returncode == 0, f"node failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_page_lists_the_master_by_role_with_strength_marks_and_its_revision() -> None:
    master = _master_json()
    first_role = MASTER.entries_in("experience")[0]
    quantified = next(item for item in MASTER.items.values() if item.strength == "quantified")
    stated = next(item for item in MASTER.items.values() if item.strength == "stated")
    out = _run(
        """
        const sections = model.masterSections(input.master);
        out.order = sections.map((group) => group.section);
        out.roles = sections[0].entries.map((group) => [group.entry.id, group.lines.map((line) => line.id)]);
        out.plain = Object.fromEntries(sections.filter((group) => group.entries.length === 0).map((group) => [group.section, group.lines.map((line) => line.id)]));
        out.marks = input.items.map((item) => model.strengthMark(item));
        out.backed = model.strengthMark({ strength: "backed" });
        out.revision = model.revisionLine(input.master);
        out.when = model.entryWhen(input.master.entries[0]);
        out.shown = [model.shownByLabel("a", input.shownBy, input.profiles), model.shownByLabel("b", input.shownBy, input.profiles), model.shownByLabel("c", input.shownBy, input.profiles)];
        out.none = model.masterSections(null);
        """,
        {
            "master": master, "items": [quantified.to_json(), stated.to_json()],
            "shownBy": {"a": ["p1", "p2"], "b": ["p9"]}, "profiles": [{"profile_id": "p1", "label": "Staff AI Engineer"}, {"profile_id": "p2", "label": "Staff Software Engineer"}],
        },
    )
    # Roles first (the page is "by role"), then projects, then the plain sections; every section is listed, even an empty one.
    assert out["order"] == ["experience", "projects", "summary", "skills", "education", "other"] and out["none"] == []
    assert out["roles"] == [[entry.id, list(entry.bullets)] for entry in MASTER.entries_in("experience")]
    assert out["plain"]["summary"] == [item.id for item in MASTER.in_section("summary")] and out["plain"]["skills"] == [item.id for item in MASTER.in_section("skills")]
    assert [mark["mark"] for mark in out["marks"]] == ["#", "·"] and out["backed"]["mark"] == "B"
    assert "states a number" in out["marks"][0]["title"] and "story or an answer" in out["backed"]["title"]
    counts = MASTER.counts()
    assert out["revision"] == f"Revision 3 · written by your agent · 2026-10-04 · {counts['items']} lines, {counts['entries']} entries, {counts['skills']} skills"
    assert out["when"] == " · ".join(first_role.sublines)
    assert out["shown"] == ["Shown by Staff AI Engineer, Staff Software Engineer", "Shown by p9", ""]


def test_a_write_that_crosses_the_agents_says_so_and_a_saved_one_says_what_it_did_to_the_profiles() -> None:
    out = _run(
        """
        out.conflict = model.conflictOf(input.conflict);
        out.other = model.conflictOf(input.other);
        out.after = model.afterWriteLine(input.profiles);
        out.nothing = model.afterWriteLine({ synced: [], offers: [] });
        out.near = model.nearDuplicateLine({ status: "near_duplicate", written: false, near_duplicates: [input.near, { id: "b-other", text: "Another.", similarity: 0.63 }] });
        out.otherNumbers = model.nearDuplicateLine({ status: "near_duplicate", near_duplicates: [{ ...input.near, same_numbers: false }] });
        out.notNear = [model.nearDuplicateLine(null), model.nearDuplicateLine({ status: "revised", near_duplicates: [] })];
        out.history = model.historyRows(input.history);
        out.retired = model.retiredRows(input.history);
        """,
        {
            "master": _master_json(),
            "near": {"id": "b-1", "text": "Cut deploy time from 40 minutes to 6.", "entry_id": "r-1", "similarity": 0.71, "same_numbers": True},
            "conflict": {"code": "revision_conflict", "current": {"revision": 5, "written_by": "agent"}},
            "other": {"code": "personal_info_refused", "message": "no"},
            "profiles": {
                "synced": [{"label": "Staff AI Engineer", "changed": ["b-1"], "retired": []}],
                "offers": [{"label": "Staff Software Engineer", "offer": "1 new master line: refresh?"}],
            },
            "history": {
                "revisions": [{"revision": 2, "revision_id": "revision_b", "written_by": "agent", "updated_at": "2026-10-04T10:00:00Z", "items": 72, "added": 1, "removed": 0, "changed": 0}],
                "retired": [
                    {"id": "b-9", "what": "line", "text": "An old line.", "section": "experience", "entry_heading": "Hexa Cloud", "retired_in": 4},
                    {"id": "r-9", "what": "entry", "kind": "entry", "text": "Casterly", "sublines": ["Engineer | 2012 - 2015"], "section": "experience", "retired_in": 5},
                ],
            },
        },
    )
    assert out["conflict"] == {"revision": 5, "message": "Not saved: your agent changed the master after this page read it (it is at revision 5 now). It is shown as it is now; make the change again."}
    assert out["other"] is None
    assert out["after"] == "Staff AI Engineer: the resume now shows this change. Staff Software Engineer: 1 new master line: refresh?" and out["nothing"] == ""
    # A line the master already has in other words is asked about, not added: the closest line is named, and "Add it anyway".
    assert out["near"] == (
        'Not added: the master already has a line that says nearly this (71% alike): "Cut deploy time from 40 minutes to 6.". '
        "If it is the same fact, edit that line instead; to keep both, choose Add it anyway."
    )
    assert "(71% alike, with different numbers)" in out["otherNumbers"] and out["notNear"] == ["", ""]
    assert out["history"] == [{"key": "revision_b", "revision": 2, "text": "Revision 2 · 2026-10-04 · your agent · 72 lines · 1 added, 0 retired, 0 changed"}]
    assert out["retired"] == [
        {"id": "b-9", "what": "line", "text": "An old line.", "where": "Hexa Cloud", "note": "retired in revision 4"},
        {"id": "r-9", "what": "entry", "text": "Casterly · Engineer | 2012 - 2015", "where": "Experience", "note": "retired in revision 5"},
    ]


def test_a_profiles_status_line_and_the_one_button_it_may_offer() -> None:
    base = {"profile_id": "p1", "label": "Staff Engineer", "has_selection": True, "attached": True, "shown": 36, "skills": 20, "made_from_revision": 1, "changed": [], "retired": [], "skills_retired": [], "stale": False, "offer": None, "pending": False}
    out = _run(
        "for (const [name, status] of Object.entries(input.statuses)) out[name] = model.selectionRow(status);\nout.refresh = input.changes.map((change) => model.refreshLine(change));",
        {
            "statuses": {
                "current": base,
                "offer": {**base, "offer": "3 new master lines: refresh?"},
                "stale": {**base, "stale": True, "changed": ["b-1"], "retired": ["b-2"], "skills_retired": ["Go"]},
                "detached": {**base, "attached": False, "offer": None},
                "none": {**base, "has_selection": False, "attached": None},
                "pending": {**base, "has_selection": False, "pending": True},
            },
            "changes": [
                None,
                {"action": "refreshed", "shown": 38, "skills": 22, "pages": 2, "fits": True, "added": ["a", "b"], "removed": ["c"]},
                {"action": "first", "shown": 30, "skills": 18, "pages": 3, "fits": False, "added": [], "removed": []},
                {"action": "synced", "changed": ["a"], "retired": ["b", "c"]},
            ],
        },
    )
    size = "36 entries and lines, 20 skills, made from revision 1"
    assert out["current"] == {"line": f"{size}.", "offer": "", "action": None, "state": "current"}
    assert out["offer"] == {"line": f"{size}.", "offer": "3 new master lines: refresh?", "action": {"use": "refresh", "label": "Refresh"}, "state": "offer"}
    assert out["stale"]["action"] == {"use": "sync", "label": "Print its resume again"} and "(1 shown line edited, 2 retired)" in out["stale"]["line"]
    assert out["detached"]["action"] == {"use": "refresh", "label": "Select from the master again"} and "no longer shows this selection" in out["detached"]["line"]
    assert out["none"] == {"line": "Shows its own resume, not a selection of the master.", "offer": "", "action": {"use": "refresh", "label": "Select from the master"}, "state": "none"}
    assert out["pending"] == {"line": "Making its first selection from your master…", "offer": "", "action": None, "state": "pending"}
    assert out["refresh"] == [
        "Nothing to do: the resume already says what the master says.",
        "Refreshed: 38 entries and lines and 22 skills on 2 pages; 2 came in, 1 went. The profile's resume is now this selection.",
        "First selection: 30 entries and lines and 18 skills on 3 pages. The profile's resume is now this selection. It does not fit 2 pages after every cut the rules allow.",
        "Printed again from the master: 1 line edited, 2 retired.",
    ]


def test_the_migration_asks_every_question_before_it_can_be_made() -> None:
    question = {"question_id": "mq-1", "section": "experience", "entry": "Lumenfold", "choices": ["a", "b", "both"], "options": []}
    plan = {"resumes": 2, "lines_in": 71, "lines_out": 65, "entries": 13, "exact_duplicates": 4, "near_duplicates": [{}, {}], "questions": [question]}
    out = _run(
        """
        out.states = input.payloads.map((payload) => model.migrationState(payload));
        out.summary = model.migrationSummary(input.payloads[2]);
        out.complete = input.answers.map((answers) => model.answersComplete([input.question], answers));
        out.where = model.questionWhere(input.question);
        out.noQuestions = model.answersComplete([], {});
        """,
        {
            "payloads": [None, {"status": "blocked", "blocked": {"code": "migration_no_profiles"}}, {"status": "needs_answers", "migration": plan, "questions": [question]}, {"status": "ready", "migration": {**plan, "questions": []}, "questions": []}, {"status": "unchanged", "migration": None, "questions": []}],
            "question": question, "answers": [{}, {"mq-1": "c"}, {"mq-1": "both"}, {"other": "a"}],
        },
    )
    assert out["states"] == ["loading", "blocked", "questions", "ready", "nothing"]
    assert out["summary"] == "2 resumes, 71 lines in: 4 exact duplicates and 2 near-duplicates folded, 1 conflict. The master would hold 65 lines and 13 entries."
    assert out["complete"] == [False, False, True, False] and out["noQuestions"] is True and out["where"] == "Experience / Lumenfold"


def _line(number: int, text: str, item_id: str | None, **more: object) -> dict:
    ref = {"kind": "resume", "line": number, "text": text, **({"item_id": item_id} if item_id else {})}
    return {"kind": "copy", "text": text, "refs": [ref], "id": f"L{number}", "origin": "model", **more}


def test_picked_and_left_out_are_grouped_by_role_with_their_reasons_and_an_add_that_needs_room_is_asked() -> None:
    roles = MASTER.entries_in("experience")
    first, second = roles[0], roles[1]
    summary = MASTER.in_section("summary")[0]
    picked_ids = [summary.id, first.bullets[0], first.bullets[1], second.bullets[0]]
    left_ids = [first.bullets[2], second.bullets[1], "b-gone-from-master"]
    edited_from = _line(12, f"- {MASTER.items[first.bullets[1]].text}", first.bullets[1])
    response = {
        "selection": {
            "picked_by": "model",
            "picked": [{"id": item_id, "code": "offered", "reason": "names Python"} for item_id in picked_ids],
            "left_out": [{"id": item_id, "code": "role_limit", "reason": "its role already shows its best lines"} for item_id in left_ids],
        },
        "result": {"sections": [
            {"heading": "summary", "lines": [_line(2, f"- {summary.text}", summary.id)]},
            {"heading": "experience", "entries": [
                {"heading": [_line(10, f"### {first.heading}", first.id)], "bullets": [
                    _line(11, f"- {MASTER.items[first.bullets[0]].text}", first.bullets[0]),
                    {"kind": "custom", "text": "My own wording of it.", "refs": [], "id": "L12", "origin": "user", "edited_from": edited_from},
                ]},
                {"heading": [_line(20, f"### {second.heading}", second.id)], "bullets": [_line(21, f"- {MASTER.items[second.bullets[0]].text}", second.bullets[0])]},
            ]},
            {"heading": "skills", "lines": [_line(30, "- Languages: Python, Go", None)]},
        ]},
    }
    asked = {"use": "add", "applied": False, "changed": False, "pages": 3, "max_pages": 2, "would_cut": [{"id": "b-1", "kind": "bullet", "text": "Maintained the nightly jobs.", "role": "Fintra Labs"}], "cut": []}
    out = _run(
        """
        out.view = model.pickedLeftOut(input.response, input.master);
        out.noMaster = model.pickedLeftOut(input.response, null);
        out.unavailable = model.pickedLeftOut({ result: input.response.result }, input.master);
        out.by = [model.pickedByLine(out.view), model.pickedByLine({ available: true, pickedBy: "code" }), model.pickedByLine(out.unavailable)];
        out.room = model.roomQuestion(input.asked);
        out.noRoom = model.roomQuestion({ ...input.asked, would_cut: [] });
        out.applied = model.roomQuestion({ applied: true });
        out.lines = input.changes.map((change) => model.changeLine(change));
        out.ids = input.lines.map((line) => model.lineItemId(line));
        out.save = input.rows.map((row) => model.saveWordingTarget(row));
        """,
        {
            "response": response, "master": _master_json(), "asked": asked,
            "changes": [
                asked,
                {"use": "remove", "applied": True, "changed": True, "pages": 2, "max_pages": 2, "cut": []},
                {"use": "add", "applied": True, "changed": True, "pages": 2, "max_pages": 2, "cut": [{"id": "b-1"}]},
                {"use": "add", "applied": True, "changed": True, "pages": 3, "max_pages": 2, "cut": []},
                {"use": "add", "applied": True, "changed": False, "pages": 2, "max_pages": 2, "cut": []},
            ],
            "lines": [response["result"]["sections"][1]["entries"][0]["bullets"][1], response["result"]["sections"][2]["lines"][0], None],
            "rows": [
                {"edited": True, "text": " My own wording of it. ", "editedFrom": edited_from},
                {"edited": True, "text": "Typed from nothing.", "editedFrom": None},
                {"edited": True, "text": "An edit of the Skills line.", "editedFrom": _line(30, "- Languages: Python, Go", None)},
                {"edited": False, "text": "A copy.", "editedFrom": None},
            ],
        },
    )
    view = out["view"]
    assert (view["available"], view["pickedBy"], view["counts"]) == (True, "model", {"picked": 4, "leftOut": 3})
    assert [(group["label"], [line["id"] for line in group["lines"]]) for group in view["picked"]] == [
        ("Summary", [summary.id]), (first.heading, [first.bullets[0], first.bullets[1]]), (second.heading, [second.bullets[0]]),
    ]
    by_id = {line["id"]: line for group in view["picked"] for line in group["lines"]}
    assert by_id[first.bullets[0]] == {"id": first.bullets[0], "text": MASTER.items[first.bullets[0]].text, "known": True, "code": "offered", "reason": "names Python"}
    assert by_id[first.bullets[1]]["text"] == "My own wording of it."  # the resume's own text, an edit included
    left = {line["id"]: (group["label"], line) for group in view["leftOut"] for line in group["lines"]}
    assert left[first.bullets[2]] == (first.heading, {"id": first.bullets[2], "text": MASTER.items[first.bullets[2]].text, "known": True, "code": "role_limit", "reason": "its role already shows its best lines"})
    assert left["b-gone-from-master"][1]["known"] is False and left["b-gone-from-master"][1]["text"] is None  # a line the master no longer has: no Add
    # Before the master is read, a left-out line has no text yet (the picked ones are the resume's own).
    assert all(not line["known"] for group in out["noMaster"]["leftOut"] for line in group["lines"]) and out["noMaster"]["picked"][0]["lines"][0]["known"] is True
    assert out["unavailable"] == {"available": False, "pickedBy": None, "picked": [], "leftOut": [], "counts": {"picked": 0, "leftOut": 0}}
    assert out["by"][0].startswith("GigAI picked the candidate lines from your whole master") and "did not give a usable answer" in out["by"][1] and out["by"][2] == ""
    assert out["room"] == {
        "text": 'With this line the resume is 3 pages. To keep 2 pages, this line would be cut: "Maintained the nightly jobs." (Fintra Labs).',
        "canCut": True, "cutLabel": "Add it and cut that line", "keepLabel": "Keep both (3 pages)",
    }
    assert out["noRoom"]["canCut"] is False and "No line can go to make room." in out["noRoom"]["text"] and out["applied"] is None
    assert out["lines"] == [
        "",
        "Removed from this resume. It is under Left out; your master is unchanged.",
        'Added to this resume. To keep 2 pages, 1 line went: Restore under "Cut for length" puts it back.',
        "Added to this resume. The resume is now 3 pages.",
        "That line is already on the resume.",
    ]
    assert out["ids"] == [first.bullets[1], None, None]
    assert out["save"] == [{"id": first.bullets[1], "text": "My own wording of it."}, None, None, None]


def test_the_pages_are_wired_to_the_routes() -> None:
    api = (SRC / "api.js").read_text(encoding="utf-8")
    for call in (
        'request("GET", `/api/master${', 'request("GET", "/api/master/history")', 'request("POST", "/api/master/lines"', 'request("PUT", "/api/master/lines"',
        'request("POST", "/api/master/entries"', 'request("PUT", "/api/master/entries"', 'request("GET", "/api/master/migration")',
        'request("POST", "/api/master/migration"', 'request("GET", "/api/master/selection")', 'request("POST", "/api/master/selection"',
        'request("PUT", "/api/tailored-resumes/selection"',
    ):
        assert call in api, call
    routing = (SRC / "routing.js").read_text(encoding="utf-8")
    assert '{ view: "master", path: "#/master", label: "Master resume"' in routing and 'MASTER_HASH = "#/master"' in routing
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    assert 'route.view === "master" && <MasterView' in app
    settings = (SRC / "views" / "SettingsView.jsx").read_text(encoding="utf-8")
    assert "href={MASTER_HASH}" in settings and "<MasterSelections" in settings
    page = (SRC / "views" / "MasterView.jsx").read_text(encoding="utf-8")
    # Every write sends the revision the page read, and no text of the master is injected as markup.
    assert page.count("write((revision) =>") == 7 and "call(revision)" in page and "dangerouslySetInnerHTML" not in page
    panel = (SRC / "components" / "TailoredResumePanel.jsx").read_text(encoding="utf-8")
    assert "<PickedLeftOut stored={stored} state={state} />" in panel and 'data-action="save-wording"' in panel
    assert "putMasterLine({ revision: body.master.revision, id: wording.id, use: \"edit\", text: wording.text })" in panel
    picked = (SRC / "components" / "PickedLeftOut.jsx").read_text(encoding="utf-8")
    assert "putTailoredResumeSelection({ profileId: state.profileId, jobIdentity: state.jobIdentity, updatedAt: stored.updated_at, use, itemId, fit })" in picked
    assert "dangerouslySetInnerHTML" not in picked
