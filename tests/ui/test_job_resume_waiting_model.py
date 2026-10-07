"""0.1.11.5 SP: the texts of the waiting-resume callout and of the named stale line, without a browser.

`ui/src/jobResumeModel.js` under node, on hand-made payloads in the shapes the server answers (`GET
/api/jobs/suggestions` with the job's stored view, `job_actions.OPEN_KEYS`; a stored job resume).

- THE WAITING RESUME IS READ FROM THE OPEN READ: `proposed` of `GET /api/jobs/suggestions` reaches the page's record with
  no pick step, and Compare lists its lines from the ids the server names (`lines`);
- a pick step's answer no longer hides a LATER read: the page drops it when a read serves the view (`servesView`);
- THE CALLOUT SAYS WHAT "USE IT" COSTS: it replaces the resume the user edited, and lists the edited points by their
  words, short; a resume with no edited point says nothing extra;
- THE STALE NOTICE NAMES THE LINE (its words on this resume, short, never an id) with the fix that needs no model:
  retired -> "Remove it from this resume", reworded -> "Use the new wording"; a line the resume does not show is not listed.

Not marked `ui`: no browser. LOUD skip without `node`.
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
LONG = (
    "Rebuilt the night-shift roster engine so a schedule change reaches every clinic in under a minute, then ran the migration of every tenant onto it "
    "over nine months with the old and the new system reconciled row by row each night."
)
EDITED = "Kept the payroll export under nine minutes for two years."


def _ref(item: str, text: str) -> dict:
    return {"kind": "resume", "item_id": item, "line": 1, "text": text}


def _copy(line: str, item: str, text: str) -> dict:
    return {"id": line, "kind": "copy", "origin": "pick", "text": f"- {text}", "refs": [_ref(item, text)]}


STORED = {
    "updated_at": "2026-10-07T10:00:00Z", "producer": {"callable": "scout.pick"},
    "result": {"sections": [{"heading": "Experience", "lines": [], "entries": [{"heading": [{"text": "### Thistledown"}], "bullets": [
        _copy("L1", "b-000001", LONG),
        {"id": "L2", "kind": "custom", "origin": "user", "text": f"- {EDITED}", "refs": [], "edited_from": _copy("L2", "b-000002", "Cut the weekly payroll export to nine minutes.")},
        _copy("L3", "b-000003", "Operated Kubernetes clusters backed by PostgreSQL."),
    ]}]}]},
}
PLAIN = {**STORED, "result": {"sections": [{"heading": "Experience", "lines": [], "entries": [{"heading": [{"text": "### Thistledown"}], "bullets": [_copy("L1", "b-000001", LONG)]}]}]}}
#: `GET /api/jobs/suggestions` as the server answers it for a job with a resume waiting (`job_actions.list_suggestions`, with_view).
OPENED = {
    "schema_version": "scout-job-suggestions-response:1", "job_identity": "https://jobs.example.test/a", "profile_id": "profile_1", "updated_at": "t",
    "gate": {"decision": "suggest", "ready": True, "reasons": []}, "counts": {"open": 0, "done": 0, "dismissed": 0}, "suggestions": [],
    "verdict": "matched_above_threshold", "basis": "master", "master_stored": True, "master_education": True,
    "stale": ["picked_line_changed", "assessment_newer"],
    "stale_lines": [{"id": "b-000003", "change": "retired"}, {"id": "b-000001", "change": "reworded"}, {"id": "b-000009", "change": "retired"}],
    "picked": {"picked_by": "model", "fallback": None, "draft": False, "made_at": "t", "pages": None, "max_pages": 2, "max_bullets": 20},
    "problems": [], "added_by_code": [], "conflicts": [], "selection_error": None,
    "proposed": {"picked_by": "model", "fallback": None, "draft": False, "made_at": "t", "pages": None, "max_pages": 2, "max_bullets": 20, "conflicts": [], "lines": ["b-000001", "b-000002", "b-000004"]},
    "selected_lines": ["b-000001", "b-000002", "b-000003"], "requirements": [],
}
#: The answer of an earlier pick step on the same page visit: nothing waiting then.
STEP = {**{key: OPENED[key] for key in ("job_identity", "profile_id", "gate", "picked", "problems", "added_by_code", "conflicts", "selection_error")}, "stale": ["picked_line_changed"], "stale_lines": [], "proposed": None, "resume": None, "action": "dismiss_proposed"}

SCRIPT = """
const m = await import(__URL__);
const input = JSON.parse(process.argv[1]);
const out = {};
const onOpen = m.suggestionsAnswer(input.opened, null);
out.onOpen = { stale: onOpen.stale, proposed: onOpen.record.proposed, staleLines: onOpen.record.stale_lines };
out.change = m.proposedChange(onOpen.record, input.stored);
out.servesView = [m.servesView(input.opened), m.servesView({ gate: {}, counts: {}, suggestions: [] }), m.servesView(null)];
out.stepHides = m.suggestionsAnswer(input.opened, input.step).record.proposed;
out.replaces = [m.proposedReplaces(input.stored), m.proposedReplaces(input.plain), m.proposedReplaces(null)];
out.editedPoints = m.editedPoints(input.stored);
out.fixes = m.staleLineFixes(onOpen.record, input.stored);
out.noFixes = [m.staleLineFixes(m.suggestionsAnswer({ ...input.opened, stale_lines: undefined }, null).record, input.stored), m.staleLineFixes(onOpen.record, null)];
out.short = [m.shortWords("- Short line."), m.shortWords(input.long), m.shortWords(input.long, 30), m.shortWords("x".repeat(200), 20)];
out.labels = [m.REMOVE_STALE_LABEL, m.REWORD_STALE_LABEL];
out.printed = m.printedWords(input.stored);
out.summary = [m.proposedSummary(out.change), m.proposedSummary({ adds: ["a", "b"], drops: [] }), m.proposedSummary({ adds: [], drops: [] }), m.proposedSummary({ adds: null, drops: null }), m.proposedSummary(null)];
out.ready = [m.PROPOSED_READY_TEXT, m.PROPOSED_KEEPS_TEXT];
out.generic = m.staleItems(["picked_line_changed"])[0].label;
console.log(JSON.stringify(out));
"""

_ID = re.compile(r"\bb-[0-9a-f]{6}\b|\bL\d+\b|picked_line_changed|stale_lines")


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the job page's model was NOT run")
    payload = json.dumps({"opened": OPENED, "step": STEP, "stored": STORED, "plain": PLAIN, "long": LONG})
    script = SCRIPT.replace("__URL__", json.dumps((UI_SRC / "jobResumeModel.js").as_uri()))
    done = subprocess.run([node, "--input-type=module", "-e", script, payload], capture_output=True, text=True, check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_waiting_resume_is_read_from_the_read_that_opens_the_job(out: dict) -> None:
    assert out["onOpen"]["stale"] == ["picked_line_changed", "assessment_newer"]
    assert out["onOpen"]["proposed"] == OPENED["proposed"], "no pick step was needed: the open read holds the waiting resume"
    assert out["onOpen"]["staleLines"] == OPENED["stale_lines"]
    # Compare, from the ids the server names: what the new one would add, and what it would leave out.
    assert out["change"]["adds"] == ["b-000004"] and out["change"]["drops"] == ["b-000003"]
    assert out["change"]["pickedBy"] == "model" and out["change"]["draft"] is False


def test_a_later_read_is_not_hidden_by_an_earlier_step(out: dict) -> None:
    assert out["servesView"] == [True, False, False]
    # While a step's answer is held it wins (it is the newest); the panel drops it when a read serves the view.
    assert out["stepHides"] is None


def test_the_callout_says_use_it_replaces_the_edited_resume_and_lists_the_edited_points(out: dict) -> None:
    replaces, plain, none = out["replaces"]
    assert replaces == {"text": "Using it replaces the resume you edited. It would drop your edited point:", "points": [EDITED]}
    assert out["editedPoints"] == [EDITED]
    assert plain is None and none is None, "a resume with no edited point says nothing extra"
    # What it changes, in one sentence at the top of the card; nothing when the server names no line.
    assert out["summary"] == [
        "It adds 1 line and leaves out 1 line this resume prints.", "It adds 2 lines.", "It prints the same lines as this resume, as your master words them now.", "", "",
    ]
    assert out["ready"] == ["A new resume is ready for this job.", "Yours stays as it is until you take the new one."]
    # Compare names a line by the words this resume prints for it (a retired line is no longer in the master).
    assert out["printed"] == {"b-000001": LONG, "b-000002": EDITED, "b-000003": "Operated Kubernetes clusters backed by PostgreSQL."}


def test_the_stale_notice_names_the_line_and_offers_the_fix_that_needs_no_model(out: dict) -> None:
    retired, reworded = out["fixes"]
    assert len(out["fixes"]) == 2, "a line the resume does not show is not listed"
    assert (retired["id"], retired["lineId"], retired["change"]) == ("b-000003", "L3", "retired")
    assert retired["label"] == "a line this resume prints was retired from your master: “Operated Kubernetes clusters backed by PostgreSQL.”"
    assert retired["action"] == {"use": "remove", "label": "Remove it from this resume"}
    assert (reworded["id"], reworded["lineId"], reworded["change"]) == ("b-000001", "L1", "reworded")
    assert reworded["label"].startswith("a line this resume prints was reworded in your master: “Rebuilt the night-shift roster engine") and reworded["label"].endswith("…”")
    assert reworded["action"] == {"use": "reword", "label": "Use the new wording"}
    assert out["labels"] == ["Remove it from this resume", "Use the new wording"]
    for fix in out["fixes"]:
        assert not _ID.search(fix["label"]) and not _ID.search(fix["action"]["label"]), fix
        assert len(fix["words"]) <= 91
    # Nothing served, or no stored resume: the general sentence stands.
    assert out["noFixes"] == [[], []]
    assert out["generic"] == "a line this resume prints was changed or retired in your master"


def test_a_lines_words_are_cut_short_at_a_word(out: dict) -> None:
    whole, cut, shorter, unbroken = out["short"]
    assert whole == "Short line."
    assert cut.endswith("…") and len(cut) <= 91 and LONG.startswith(cut[:-1]) and LONG[len(cut) - 1] == " "
    assert shorter == "Rebuilt the night-shift roster…"
    assert unbroken == "x" * 20 + "…"
