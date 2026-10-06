"""0.1.11 N6 (SPEC section 6): the job page's rules without a browser. `ui/src/jobResumeModel.js` (and the `has_gap` state of `jobStateModel.js`) run under node.

The browser flows (`test_job_page_matched.py`, `test_job_page_resume.py`) show the page; this pins the rules behind
it on hand-made payloads in the STORED shapes (SPEC 1.3, 2.1; the suggestion record as `suggestions.SuggestionRecord
.to_json` writes it), the branches a small home cannot reach included: every gate decision and its one sentence, the
header's chip, the stale list with the refresh each code allows, a row's coverage with and without a record, who made
the stored resume, the conflicts of 3.3, a picked line's reason, the suggestion list, the two brief commands and what
Apply says for a stale resume.

Not marked `ui`: no browser. LOUD skip without `node`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
const [m, state] = await Promise.all(__URLS__.map((url) => import(url)));
const input = JSON.parse(process.argv[1]);
const out = {};
const A = input.assessment;

// The gate: the record's, else the assessment's, else the verdict's.
out.gates = {
  record: m.gateOf({ record: input.record, quick: { resume_gate: { decision: "hold_unmet", reasons: [] } }, assessment: A }),
  assessment: m.gateOf({ quick: { resume_gate: input.gapGate }, assessment: A }),
  verdicts: ["matched_above_threshold", "pending_user_answers", "not_a_match"].map((verdict) => m.gateOf({ assessment: { verdict, matrix: [] } }).decision),
  none: m.gateOf({ assessment: null }),
};
out.sentences = {
  suggest: m.holdSentence({ decision: "suggest", reasons: [] }, A),
  gap: m.holdSentence(input.gapGate, A),
  questions: m.holdSentence({ decision: "hold_question", reasons: [{ code: "question_open", requirement: "req-000002" }, { code: "question_open", requirement: "req-000003" }] }, A),
  oneQuestion: m.holdSentence({ decision: "hold_question", reasons: [] }, input.pending),
  notAMatch: m.holdSentence({ decision: "not_a_match", reasons: [] }, { not_a_match_reason: "The role is on site in Lisbon.", matrix: [] }),
};

// The header's chip, and the state a held match reads.
const job = (verdict, quick, row) => ({ id: "j", verdict, quick, row: row || null, assessmentSource: "quick" });
out.fit = [
  state.fitStateFor(job("matched_above_threshold", { resume_gate: input.gapGate })),
  state.fitStateFor(job("matched_above_threshold", { resume_gate: { decision: "suggest", reasons: [] } })),
  state.fitStateFor(job("matched_above_threshold", { job_state: { state: "has_gap", next_events: ["applied"] } })),
  state.fitStateFor(job("pending_user_answers", { job_state: { state: "weak_fit", next_events: [] } })),
  state.fitStateFor(job("not_a_match", {})),
  state.fitStateFor(null),
];
out.jobState = [
  state.jobStateFor(job("matched_above_threshold", { resume_gate: input.gapGate }), null, null).state,
  state.jobStateFor(job("matched_above_threshold", { resume_gate: input.gapGate, job_state: { state: "tailored", next_events: ["applied"] } }), null, null).state,
  state.stateLabel("has_gap"), state.stateLabel("tailored"), state.STATE_ORDER.indexOf("has_gap") - state.STATE_ORDER.indexOf("matched"),
];
out.chips = [
  m.headerChip("matched", { assessment: A }),
  m.headerChip("matched", { assessment: { verdict: "matched_above_threshold", matrix: [{ requirement: "Python", class: "hard", status: "met" }, { requirement: "Kubernetes", class: "hard", status: "met" }, { requirement: "Helm", class: "list_item", status: "unmet" }, { requirement: "Istio", class: "nice_to_have", status: "unclear" }] } }),  // 0.1.11.2: 4 rows, or the chip reads "thin posting"
  m.headerChip("needs_answers", { assessment: input.pending }),
  m.headerChip("has_gap", { assessment: A, gate: input.gapGate }),
  m.headerChip("not_a_match", {}), m.headerChip("weak_fit", {}), m.headerChip("not_assessed", {}),
].map((chip) => [chip.state, chip.label, chip.tone, chip.title]);

// The stale list (SPEC 2.4): each code's words, whether it is a note, and the refresh it allows.
const items = (codes) => m.staleItems(codes).map((item) => [item.code, item.note, item.action]);
out.stale = {
  each: items(["picked_line_changed", "master_newer", "selection_rules_changed", "assessment_newer"]),
  withOldAssessment: items(["assessment_stale:older_prompt", "picked_line_changed"]),
  labels: m.staleItems(["assessment_stale:older_prompt", "assessment_stale:settings_changed", "master_newer"]).map((item) => item.label),
  actions: [["picked_line_changed"], ["assessment_stale:older_prompt", "picked_line_changed"], ["assessment_newer"], ["master_newer"], []].map((codes) => m.staleActions(m.staleItems(codes)).map((action) => [action.use, action.label])),
  isStale: [["master_newer"], ["master_newer", "picked_line_changed"], []].map((codes) => m.isStale(m.staleItems(codes))),
  served: m.staleCodes({ served: ["master_newer"], job: job("matched_above_threshold", { basis_stale: true, basis_stale_reason: "older_prompt" }), stored: { updated_at: "2026-10-01T00:00:00Z" }, assessedAt: "2026-10-05T00:00:00Z" }),
  derived: m.staleCodes({ job: job("matched_above_threshold", { basis_stale: true, basis_stale_reason: "older_prompt" }), stored: { updated_at: "2026-10-01T00:00:00Z" }, assessedAt: "2026-10-05T00:00:00Z" }),
  current: m.staleCodes({ job: job("matched_above_threshold", { basis_stale: false, prompt_version: "v9" }), stored: { updated_at: "2026-10-06T00:00:00Z" }, assessedAt: "2026-10-05T00:00:00Z" }),
};

// Coverage (SPEC 2.3): the record's rows; without a record, the same rule from the rows' sources and what is printed.
const cover = (map) => [...map.entries()].map(([id, row]) => [id, row.coverage, row.inResume, row.putBack]);
out.coverage = {
  record: cover(m.coverageRows({ assessment: A, record: input.record, stored: input.stored })),
  derived: cover(m.coverageRows({ assessment: A, stored: input.stored })),
  noResume: cover(m.coverageRows({ assessment: A })),
  notes: A.matrix.map((row) => m.coverageNote(row, m.coverageRows({ assessment: A, stored: input.stored }))),
  alternatives: A.matrix.map((row) => m.alternativesLine(row)),
  printed: m.printedIds(input.stored),
  lines: m.printedLineCount(input.stored),
};

// Who made the stored resume, and the one provenance line.
const pick = { ...input.stored, producer: { callable: "scout.pick" } };
const line = (stored, record) => { const origin = m.resumeOrigin(stored, { record }); return [origin, m.provenanceLine({ stored, record, origin }).text]; };
const selection = (over) => ({ ...input.record, selection: { ...input.record.selection, ...over } });
out.provenance = {
  model: line(pick, input.record),
  code: ["no_pick", "pick_too_small", "master_revision_unreadable", "pick_failed"].map((fallback) => line(pick, selection({ picked_by: "code", fallback }))[1]),
  draft: line(pick, selection({ picked_by: "code", fallback: "draft_requested", draft: true })),
  edited: line({ ...pick, edited: { written_by: "agent", edited_at: "2026-10-04T09:30:00Z", source: null } }, input.record),
  editedByYou: line({ ...pick, edited: { written_by: "operator", edited_at: "2026-10-04T09:30:00Z" } }, input.record)[1],
  added: line({ ...pick, selection: { picked: [{ id: "b-000001", code: "added_by_you", reason: "you added it to this resume" }], left_out: [] } }, input.record)[0],
  oldTailor: line(input.stored, null),
  oldTailorYours: line({ ...input.stored, result: input.changed }, null),
  served: m.resumeOrigin(pick, { served: "old_tailor" }),
  none: [m.resumeOrigin(null), m.provenanceLine({ stored: null })],
  users: ["pick", "edited", "old_tailor", "old_tailor_yours", null].map((origin) => m.isUsersResume(origin)),
  day: [m.shortDay("2026-10-04T09:30:00Z"), m.shortDay("2026-01-31T00:00:00Z"), m.shortDay("")],
};

// Needs attention: the conflicts first (SPEC 3.3), then the evidence the resume no longer shows (2.3).
out.attention = {
  lost: m.attentionItems({ record: input.record, assessment: A }),
  conflicts: m.attentionItems({ record: selection({ conflicts: input.conflicts }), assessment: A }).map((item) => [item.code, item.requirement, item.text, item.lines]),
  ready: m.attentionItems({ record: { ...input.record, gate: { decision: "suggest", ready: true, reasons: [] } }, assessment: A }),
  none: m.attentionItems({}),
};

// Picked: the requirements a line supports and what Scout added. Changed, Skills, proposed.
const notes = m.pickedNotes(selection({ added_by_code: input.added }));
const rows = m.rowsById(A);
out.picked = ["b-000001", "b-000002", "b-000003", "b-000004", "b-000009"].map((id) => m.pickedReason(id, notes, rows, "the stored sentence"));
out.pickedKept = m.pickedReason("b-000001", notes, rows, "you added it to this resume", { keep: true });
out.changed = m.changedLines({ result: input.changed, edited: { written_by: "agent", edited_at: "2026-10-04T09:30:00Z" } });
out.skills = m.skillsView({ selection: { skills: input.skills } });
out.proposed = [m.proposedChange({ proposed: input.proposed }, input.stored), m.proposedChange({ proposed: null }, input.stored), m.proposedChange({ proposed: { line_marks: { "b-000002": "x" } } }, input.stored).adds];

// Suggestions, the two brief commands, Apply, no master.
out.suggestions = m.suggestionRows({ record: input.record, assessment: A, lineText: (id) => (id === "b-000001" ? "Ran the Postgres fleet." : "") });
out.unstored = m.suggestionRows({ assessment: { ...A, structured_suggestions: [{ kind: "gap", requirement: "req-000003", why: "Only an answer can close this." }] } });
out.brief = [m.briefCommands("https://jobs.example.test/a?b=1&c='x'", "profile_1"), m.briefCommands("https://jobs.example.test/a", null).map((item) => item.command), m.briefCommands(null, "p")];
const apply = (codes, stored = input.stored) => m.applyState({ stored, items: m.staleItems(codes) });
out.apply = [apply([]), apply(["master_newer"]), apply(["picked_line_changed"]), apply(["assessment_stale:older_prompt", "picked_line_changed"]), apply(["assessment_newer"]), apply([], null)];
out.noMaster = [m.hasNoMaster({ basis: { master: null }, selection: null }), m.hasNoMaster(input.record), m.hasNoMaster(null), m.NO_MASTER_TEXT];
// 0.1.11.3: why nothing can be picked (what the server says the resume is made from now), and a refused pick in the page's own words.
const servedBasis = (basis, master_stored, selection = null) => m.suggestionsAnswer(input.served, { ...input.pickView, picked: selection, basis, master_stored }).record;
out.cannotPick = [
  m.unpickable(servedBasis("profile_resume", false)), m.unpickable(servedBasis("profile_resume", true)), m.unpickable(servedBasis("master", true)),
  m.unpickable(servedBasis("profile_resume", true, input.pickView.picked)), m.unpickable(servedBasis(null, false)), m.unpickable(null),
  m.hasNoMaster(servedBasis("profile_resume", false)), m.hasNoMaster(servedBasis("profile_resume", true)), m.OWN_RESUME_TEXT,
];
out.pickErrors = input.pickCodes.map((code) => m.pickErrorText({ code, status: 409, message: "scout.pick.settle_stored is not part of it", detail: "pick_not_available" }));
out.oldAssessment = [
  m.assessmentIsOld(m.staleItems(["assessment_stale:resume_changed"])), m.assessmentIsOld(m.staleItems(["assessment_stale"])),
  m.assessmentIsOld(m.staleItems(["picked_line_changed", "master_newer"])), m.assessmentIsOld([]), m.assessmentIsOld(null), m.NO_RESUME_OLD_ASSESSMENT_TEXT,
];
out.pickErrorOther = [
  m.pickErrorText({ status: 0, message: "Could not reach the local API." }), m.pickErrorText(null), m.pickErrorText(new Error("TypeError: x is undefined")),
  m.selectionErrorText("pages_unmeasured"), m.selectionErrorText("pick_failed"), m.selectionErrorText(null),
];
out.answer = [
  m.suggestionsAnswer(input.served, input.pickView), m.suggestionsAnswer(input.served, null), m.suggestionsAnswer(null, null),
  m.suggestionsAnswer(null, { ...input.pickView, resume: { made_by: "scout.tailor", edited: { written_by: "agent" } } }).origin,
];
out.pickedFromAssessment = [...m.pickedNotes(m.suggestionsAnswer(input.served, null).record, A).entries()];
out.labels = [m.REPICK_LABEL, m.REASSESS_LABEL, m.DRAFT_LABEL, m.APPLY_LABEL];
process.stdout.write(JSON.stringify(out));
"""

ROW = {"class_basis": "What we are looking for"}
ASSESSMENT = {
    "verdict": "matched_above_threshold",
    "matrix": [
        {**ROW, "id": "req-000001", "requirement": "Postgres in production", "class": "hard", "status": "met", "resume_evidence": ["Ran the fleet"], "sources": ["b-000001", "b-000002"]},
        {**ROW, "id": "req-000002", "requirement": "Kubernetes in production", "class": "askable", "status": "met", "resume_evidence": ["GKE"], "sources": ["b-000003"]},
        {**ROW, "id": "req-000003", "requirement": "A document store", "class": "askable", "status": "met", "alternatives": ["Cassandra", "MongoDB"], "resume_evidence": ["MongoDB"], "sources": ["A tooling:mongodb"]},
        {**ROW, "id": "req-000004", "requirement": "Terraform", "class": "askable", "status": "unmet", "resume_evidence": []},
        {**ROW, "id": "req-000005", "requirement": "Go", "class": "askable", "status": "met", "resume_evidence": ["Go services"], "sources": []},
    ],
}
PENDING = {
    "verdict": "pending_user_answers",
    "matrix": [{"requirement": "GCP", "class": "askable", "status": "unclear", "resume_evidence": []}, {"requirement": "Python", "class": "hard", "status": "met", "resume_evidence": ["x"]}],
    "structured_questions": [{"question_id": "cloud:gcp", "question": "Which cloud?", "requirement": "GCP"}],
}
GAP_GATE = {"decision": "hold_unmet", "reasons": [{"code": "askable_unmet", "requirement": "req-000004"}]}


def _copy(item: str, text: str, line: int) -> dict:
    return {"kind": "copy", "text": f"- {text}", "id": f"L{line}", "origin": "model", "refs": [{"kind": "resume", "line": line, "text": f"- {text}", "item_id": item}]}


STORED = {
    "updated_at": "2026-10-05T10:00:00+00:00",
    "producer": {"callable": "scout.tailor"},
    "sources": {"master": {"revision": 5}},
    "result": {"sections": [
        {"heading": "summary", "lines": [_copy("sum-000001", "Staff engineer.", 1)]},
        {"heading": "experience", "entries": [{"heading": [{"kind": "copy", "text": "### Mossbank", "refs": []}], "bullets": [_copy("b-000001", "Ran the Postgres fleet.", 4), _copy("b-000004", "Cut deploys to 12 minutes.", 5)]}]},
    ]},
}
CHANGED = {"sections": [{"heading": "experience", "entries": [{"heading": [{"kind": "copy", "text": "### Mossbank", "refs": []}], "bullets": [
    {"kind": "rewritten", "text": "Ran the Postgres fleet for the scheduling services.", "id": "L4", "origin": "user",
     "refs": [{"kind": "resume", "line": 4, "text": "- Ran the Postgres fleet.", "item_id": "b-000001"}, {"kind": "answer", "question_id": "team:size", "text": "Six."}],
     "alternative": {"kind": "copy", "text": "- Ran the Postgres fleet.", "refs": []}},
    {"kind": "rewritten", "text": "Cut deploys.", "id": "L5", "origin": "model", "refs": [{"kind": "resume", "line": 5, "text": "- Cut deploys to 12 minutes.", "item_id": "b-000004"}]},
    {"kind": "custom", "text": "My own words.", "id": "L6", "refs": [], "edited_from": _copy("b-000002", "Moved the primary.", 6)},
]}]}]}
RECORD = {
    "schema_version": "scout-job-suggestions:1", "profile_id": "profile_1", "job_identity": "https://jobs.example.test/a", "created_at": "t", "updated_at": "t", "stored_path": "s",
    "basis": {"master": {"revision_id": "rev", "revision": 5, "content_sha256": "sha256:0"}},
    "gate": {"decision": "suggest", "ready": False, "reasons": [{"code": "lost_mandatory_evidence", "requirement": "req-000002"}]},
    "requirements": [
        {"id": "req-000001", "class": "hard", "status": "met", "sources": ["b-000001", "b-000002"], "in_resume": ["b-000001"], "coverage": "kept"},
        {"id": "req-000002", "class": "askable", "status": "met", "sources": ["b-000003"], "in_resume": [], "coverage": "lost"},
        {"id": "req-000003", "class": "askable", "status": "met", "sources": ["A tooling:mongodb"], "in_resume": [], "coverage": "answer_only"},
        {"id": "req-000004", "class": "askable", "status": "unmet", "sources": [], "in_resume": [], "coverage": None},
    ],
    "selection": {
        "picked_by": "model", "fallback": None, "draft": False, "pick_rules_version": "pick-rules:1", "selector_version": "sel-4", "made_at": "t",
        "made_from": {"result_digest": "sha256:1", "master_revision_id": "rev"}, "model_pick": None, "problems": [], "added_by_code": [],
        "line_marks": [{"id": "sum-000001", "mark": "m"}, {"id": "b-000001", "mark": "m"}, {"id": "b-000004", "mark": "m"}], "pages": 2, "max_pages": 2, "conflicts": [],
        "resume": {"stored_path": "r", "markdown_sha256": "sha256:2", "origin": "pick"},
    },
    "proposed": None,
    "suggestions": [
        {"id": "sg-1", "kind": "reword", "line": "b-000001", "requirement": "req-000001", "posting_phrase": "run Postgres at scale", "why": "Lead with the fleet.", "source": "assessment", "created_at": "t", "status": "open", "resolved": None},
        {"id": "sg-2", "kind": "master_line", "line": None, "requirement": "req-000003", "posting_phrase": None, "why": "Add a line.", "source": "agent", "created_at": "t", "status": "done",
         "resolved": {"by": "operator", "at": "2026-10-05T12:00:00Z", "how": "master_line", "ref": "b-000010"}},
        {"id": "sg-3", "kind": "order", "line": "b-000009", "requirement": None, "posting_phrase": None, "why": "Move it up.", "source": "operator", "created_at": "t", "status": "dismissed",
         "resolved": {"by": "agent", "at": "2026-10-05T12:00:00Z", "how": "dismissed", "ref": None}},
    ],
}
CONFLICTS = [
    {"code": "mandatory_evidence_does_not_fit", "requirement": "req-000001", "lines": ["b-000002"], "cut": True},
    {"code": "pinned_line_does_not_fit", "requirement": None, "lines": ["b-000007", "b-000008"], "cut": True},
    {"code": "skills_do_not_fit", "requirement": None, "lines": [], "cut": True},
    {"code": "over_page_limit", "requirement": None, "lines": [], "cut": False},
]
ADDED = [
    {"id": "b-000002", "code": "added_for_coverage", "requirement": "req-000001"}, {"id": "b-000003", "code": "recent_role_present", "requirement": None},
    {"id": "b-000004", "code": "room_left", "requirement": None},
]
SKILLS = {
    "picked": [{"name": "Python", "code": "posting_must", "reason": "the posting asks for it"}, {"name": "Postgres", "code": "listed", "reason": "kept"}],
    "left_out": [{"name": "Rust", "code": "skills_cut_for_length", "reason": "cut to keep 2 pages"}, {"name": "Perl", "code": "not_asked", "reason": "the posting does not ask for it"}],
}
PROPOSED = {"picked_by": "model", "pages": 2, "line_marks": [{"id": "sum-000001", "mark": "m"}, {"id": "b-000001", "mark": "m"}, {"id": "b-000003", "mark": "m"}]}


# What the two routes answer (N5, `job_actions`): the suggestions of a job, and its stored view after a pick step.
SERVED = {
    "schema_version": "scout-job-suggestions-view:1", "job_identity": "https://jobs.example.test/a", "profile_id": "profile_1", "updated_at": "t",
    "gate": {"decision": "suggest", "ready": False, "reasons": [{"code": "lost_mandatory_evidence", "requirement": "req-000002"}]},
    "counts": {"open": 1, "done": 1, "dismissed": 1}, "suggestions": RECORD["suggestions"],
}
PICK_VIEW = {
    "schema_version": "scout-job-pick:1", "job_identity": "https://jobs.example.test/a", "profile_id": "profile_1", "verdict": "matched_above_threshold",
    "gate": {"decision": "suggest", "ready": True, "reasons": []}, "stale": ["master_newer", "picked_line_changed"],
    "resume": {"updated_at": "t", "made_by": "scout.pick", "edited": None, "replaceable": True, "lines": 3, "counts": None, "folder_path": None, "markdown": ""},
    "picked": {"picked_by": "code", "fallback": "no_pick", "draft": False, "made_at": "t", "pages": 2, "max_pages": 2, "pick_rules_version": "pick-rules:1", "selector_version": "sel-4"},
    "problems": [], "added_by_code": [{"id": "b-000002", "code": "added_for_coverage", "requirement": "req-000001"}],
    "conflicts": [{"code": "skills_do_not_fit", "requirement": None, "lines": [], "cut": True}], "selection_error": None,
    "proposed": {"picked_by": "model", "fallback": None, "draft": False, "made_at": "t", "pages": 3, "conflicts": [{"code": "over_page_limit"}]},
}


#: Every code `POST /api/job-resumes/pick` refuses with (`job_actions.pick_action`, `pick.settle_stored`), and one the page does not know.
PICK_CODES = [
    "pick_not_available", "pick_failed", "pages_unmeasured", "assessment_stale", "assessment_missing", "no_master", "profile_resume_in_use",
    "profile_not_found", "resume_held", "draft_not_needed", "no_proposed_resume", "some_new_code",
]


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the job page's model was NOT run")
    urls = [(UI_SRC / name).as_uri() for name in ("jobResumeModel.js", "jobStateModel.js")]
    payload = json.dumps({
        "assessment": ASSESSMENT, "pending": PENDING, "gapGate": GAP_GATE, "stored": STORED, "changed": CHANGED, "record": RECORD, "conflicts": CONFLICTS,
        "added": ADDED, "skills": SKILLS, "proposed": PROPOSED, "served": SERVED, "pickView": PICK_VIEW, "pickCodes": PICK_CODES,
    })
    done = subprocess.run([node, "--input-type=module", "-e", SCRIPT.replace("__URLS__", json.dumps(urls)), payload], capture_output=True, text=True, check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_gate_is_the_records_then_the_assessments_then_the_verdicts(out: dict) -> None:
    gates = out["gates"]
    assert (gates["record"]["decision"], gates["record"]["ready"], gates["record"]["from"]) == ("suggest", False, "record")
    assert (gates["assessment"]["decision"], gates["assessment"]["ready"], gates["assessment"]["from"]) == ("hold_unmet", None, "assessment")
    assert gates["verdicts"] == ["suggest", "hold_question", "not_a_match"] and gates["none"] is None
    assert out["sentences"] == {
        "suggest": None,
        "gap": "Has a gap: Terraform",
        "questions": "2 must-have requirements wait for your answer",
        "oneQuestion": "1 must-have requirement waits for your answer",
        "notAMatch": "Not a match: The role is on site in Lisbon.",
    }


def test_a_match_the_gate_holds_reads_has_a_gap_and_the_header_has_one_chip(out: dict) -> None:
    assert out["fit"] == ["has_gap", "matched", "has_gap", "weak_fit", "not_a_match", "not_assessed"]
    # A stored resume still wins the State line (the server's precedence); the fit chip is the gate's.
    assert out["jobState"] == ["has_gap", "tailored", "Has a gap", "Resume ready", 1]
    assert out["chips"] == [
        ["matched", "Matched", "ok", None],
        ["matched", "Matched · 2 minor gaps", "ok", None],
        ["needs_answers", "Needs your answers (1)", "warn", None],
        ["has_gap", "Has a gap", "warn", "Has a gap: Terraform"],
        ["not_a_match", "Not a match", "danger", None],
        ["weak_fit", "Weak fit", "plain", None],
        ["not_assessed", "Not assessed", "plain", None],
    ]


def test_each_stale_code_says_what_it_is_and_which_refresh_it_allows(out: dict) -> None:
    stale = out["stale"]
    assert stale["each"] == [["picked_line_changed", False, "repick"], ["master_newer", True, "repick"], ["selection_rules_changed", False, "repick"], ["assessment_newer", False, "compare"]]
    # An old assessment: never a re-pick (a new selection never sits beside scores made on other evidence).
    assert stale["withOldAssessment"] == [["assessment_stale:older_prompt", False, "reassess"], ["picked_line_changed", False, "reassess"]]
    assert stale["labels"][:2] == ["old assessment: older prompt", "old assessment: settings changed"] and "still exactly what was checked" in stale["labels"][2]
    assert stale["actions"] == [[["repick", "Re-pick · no model call"]], [["reassess", "Re-assess · 1 model call"]], [], [["repick", "Re-pick · no model call"]], []]
    assert stale["isStale"] == [False, True, False], "`master_newer` is a note, not a warning"
    # The server's list is used as it is; without one the page says only what it reads itself.
    assert stale["served"] == ["master_newer"]
    assert stale["derived"] == ["assessment_stale:older_prompt", "assessment_newer"] and stale["current"] == []


def test_a_met_row_says_where_its_evidence_is(out: dict) -> None:
    coverage = out["coverage"]
    assert coverage["printed"] == ["sum-000001", "b-000001", "b-000004"] and coverage["lines"] == 3
    assert coverage["record"] == [
        ["req-000001", "kept", ["b-000001"], ["b-000001", "b-000002"]], ["req-000002", "lost", [], ["b-000003"]],
        ["req-000003", "answer_only", [], []], ["req-000004", None, [], []],
    ]
    # Without a record: SPEC 2.3 from the rows' sources and the printed ids (an unmet row has no coverage; a met row with no source reads `none`).
    assert coverage["derived"] == [
        ["req-000001", "kept", ["b-000001"], ["b-000001", "b-000002"]], ["req-000002", "lost", [], ["b-000003"]],
        ["req-000003", "answer_only", [], []], ["req-000005", "none", [], []],
    ]
    assert coverage["noResume"] == [], "nothing is 'not in the resume' of a job that has none"
    assert coverage["notes"] == [
        {"coverage": "kept", "label": "in the resume", "putBack": []},
        {"coverage": "lost", "label": "not in the resume", "putBack": ["b-000003"]},
        {"coverage": "answer_only", "label": "from your answer only", "putBack": []},
        None, None,
    ]
    assert coverage["alternatives"] == ["", "", "any one of: Cassandra, MongoDB", "", ""]


def test_one_provenance_line_says_who_made_the_resume(out: dict) -> None:
    said = out["provenance"]
    assert said["model"] == ["pick", "Picked by the assessment from your master (revision 5) · 3 lines · 2 pages"]
    assert said["code"] == [
        "Picked by Scout's own rules: the assessment's pick could not be used (the assessment gave no pick) · 3 lines · 2 pages",
        "Picked by Scout's own rules: the assessment's pick could not be used (too few lines) · 3 lines · 2 pages",
        "Picked by Scout's own rules: the assessment's pick could not be used (the master revision it read could not be loaded) · 3 lines · 2 pages",
        "Picked by Scout's own rules: the assessment's pick could not be used (it could not be fitted) · 3 lines · 2 pages",
    ]
    assert said["draft"] == ["pick", "A draft, picked by Scout's own rules because you asked for one · 3 lines · 2 pages"]
    assert said["edited"] == ["edited", "Your edited resume (agent, 4 Oct)"] and said["editedByYou"] == "Your edited resume (you, 4 Oct)"
    assert said["added"] == "edited", "an Add makes the resume the user's (SPEC 2.4)"
    assert said["oldTailor"] == ["old_tailor", "Made by the tailoring of 0.1.10"]
    assert said["oldTailorYours"] == ["old_tailor_yours", "Made by the tailoring of 0.1.10, with your own changes"]
    assert said["served"] == "old_tailor" and said["none"] == [None, None]
    assert said["users"] == [False, True, True, True, False] and said["day"] == ["4 Oct", "31 Jan", ""]


def test_needs_attention_names_each_requirement_and_line(out: dict) -> None:
    attention = out["attention"]
    assert attention["lost"] == [{"code": "lost_mandatory_evidence", "requirement": "req-000002", "text": "the resume no longer shows the evidence for Kubernetes in production", "lines": ["b-000003"]}]
    assert attention["conflicts"] == [
        ["mandatory_evidence_does_not_fit", "req-000001", "the evidence for Postgres in production does not fit 2 pages: 1 line was cut", ["b-000002"]],
        ["pinned_line_does_not_fit", None, "2 pinned lines do not fit 2 pages", ["b-000007", "b-000008"]],
        ["skills_do_not_fit", None, "your Skills section does not fit 2 pages whole: some groups were cut", []],
        ["over_page_limit", None, "the resume is over 2 pages and nothing more can be cut", []],
        ["lost_mandatory_evidence", "req-000002", "the resume no longer shows the evidence for Kubernetes in production", ["b-000003"]],
    ], "the conflicts come first"
    assert attention["ready"] == [] and attention["none"] == []


def test_picked_changed_skills_and_proposed(out: dict) -> None:
    assert out["picked"] == [
        "supports req-000001",
        "added by Scout: the only evidence for Postgres in production · supports req-000001",
        "added by Scout: a recent role always shows at least one line · supports req-000002",
        "room left on the page",
        "the stored sentence",
    ]
    assert out["pickedKept"] == "you added it to this resume · supports req-000001"
    chat, old, typed = out["changed"]
    assert (chat["by"], chat["mine"], chat["restore"], chat["before"], chat["entry"]) == ("your agent", True, "original", "Ran the Postgres fleet.", "Mossbank")
    assert [source["label"] for source in chat["sources"]] == ["master line b-000001", "your answer team:size"]
    assert (old["by"], old["mine"], old["restore"], old["before"]) == ("the old tailor", False, None, "Cut deploys to 12 minutes.")
    assert (typed["mine"], typed["restore"], typed["before"], typed["sources"]) == (True, "original", "Moved the primary.", [])
    assert out["skills"] == {"shown": ["Python", "Postgres"], "cut": [{"name": "Rust", "reason": "cut to keep 2 pages"}], "other": [{"name": "Perl", "reason": "the posting does not ask for it"}]}
    assert out["proposed"] == [{"adds": ["b-000003"], "drops": ["b-000004"], "pages": 2, "pickedBy": "model", "draft": False, "conflicts": 0}, None, ["b-000002"]]


def test_suggestions_the_brief_commands_apply_and_no_master(out: dict) -> None:
    first, second, third = out["suggestions"]
    assert (first["kindLabel"], first["about"], first["phrase"], first["who"], first["statusLine"], first["open"], first["stored"]) == (
        "Reword a line", "Ran the Postgres fleet. · Postgres in production", "run Postgres at scale", "the assessment", "Open", True, True,
    )
    assert (second["kindLabel"], second["about"], second["who"], second["open"]) == ("Add a line to your master", "A document store", "your agent", False)
    assert second["statusLine"].startswith("Done · a line of your master was added or changed · by you · ")
    assert (third["about"], third["who"]) == ("line b-000009", "you") and third["statusLine"].startswith("Dismissed · by your agent · ")
    (unstored,) = out["unstored"]
    assert (unstored["id"], unstored["stored"], unstored["open"], unstored["about"]) == ("new-1", False, True, "A document store")
    quoted, plain, none = out["brief"]
    base = "gigai scout resume brief --job-url 'https://jobs.example.test/a?b=1&c='\\''x'\\''' --profile 'profile_1'"
    assert [item["command"] for item in quoted] == [base, f"{base} --posting"] and [item["part"] for item in quoted] == ["yours", "posting"]
    assert plain == ["gigai scout resume brief --job-url 'https://jobs.example.test/a'", "gigai scout resume brief --job-url 'https://jobs.example.test/a' --posting"] and none == []
    fresh, note, changed, old, newer, missing = out["apply"]
    assert fresh == {"available": True, "stale": False, "ask": None, "offers": []} and note == fresh, "a note never stops Apply"
    assert changed["offers"] == [{"use": "repick", "label": "Re-pick first · no model call"}, {"use": "as_is", "label": "Use it as it is"}] and changed["ask"].startswith("This resume is stale: ")
    assert old["offers"] == [{"use": "reassess", "label": "Re-assess first · 1 model call"}, {"use": "as_is", "label": "Use it as it is"}]
    assert newer["offers"] == [{"use": "as_is", "label": "Use it as it is"}] and missing["available"] is False
    assert out["noMaster"][:3] == [True, False, False]
    assert out["noMaster"][3] == "This profile's own resume is used as it is. Build your master resume to get a resume picked for each job"
    both, only_suggestions, nothing, origin = out["answer"]
    # The two routes' answers, put in the shape of the stored record the rules read.
    assert both["stale"] == ["master_newer", "picked_line_changed"] and both["origin"] == "pick" and origin == "edited"
    record = both["record"]
    assert record["gate"]["ready"] is True, "the pick view's gate (after the step) wins over the list's"
    assert record["selection"]["fallback"] == "no_pick" and record["selection"]["conflicts"] == PICK_VIEW["conflicts"]
    assert record["proposed"]["pages"] == 3 and len(record["suggestions"]) == 3 and record["requirements"] == []
    assert only_suggestions["stale"] is None and only_suggestions["record"]["selection"] is None and only_suggestions["record"]["gate"]["ready"] is False
    assert nothing == {"record": None, "stale": None, "origin": None}
    # No requirement rows are served: which requirements a line supports comes from the assessment's own rows.
    assert dict(out["pickedFromAssessment"]) == {
        "b-000001": {"supports": ["req-000001"], "added": None}, "b-000002": {"supports": ["req-000001"], "added": None},
        "b-000003": {"supports": ["req-000002"], "added": None},
    }
    assert out["labels"] == ["Re-pick · no model call", "Re-assess · 1 model call", "Make a draft anyway", "Generate PDF"]


def test_a_refused_pick_and_a_profile_nothing_is_picked_for_are_said_in_plain_words(out: dict) -> None:
    """0.1.11.3: the operator read "scout.pick.settle_stored is not part of it" on a job page. Never again: one sentence by code."""

    import re

    from gigai.scout import pick

    no_master, own_resume, on_master, picked, unknown, nothing, has_none, has_own, own_text = out["cannotPick"]
    assert (no_master, own_resume, on_master, picked, unknown, nothing) == ("no_master", "own_resume", None, None, None, None)
    assert (has_none, has_own) == (True, False) and own_text.startswith("This profile uses the resume you put in by hand")
    internal = re.compile(r"scout\.|settle_stored|pick_not_available|gigai |`|\b[a-z]+(?:_[a-z]+)+\b")
    sentences = dict(zip(PICK_CODES, out["pickErrors"]))
    for code, sentence in sentences.items():
        assert sentence and sentence[0].isupper() and sentence.endswith(".") and not internal.search(sentence), (code, sentence)
    general = sentences["pick_failed"]
    assert general == "The resume could not be picked for this job. Try again, or re-assess the job to get a new pick."
    assert sentences["pick_not_available"] == sentences["some_new_code"] == general  # a code the page does not know reads as the general one
    assert len(set(sentences.values())) == len(PICK_CODES) - 2
    assert "Master page" in sentences["no_master"] and "by hand" in sentences["profile_resume_in_use"] and "draft" in sentences["resume_held"]
    assert "Re-assess the job first" in sentences["assessment_stale"]
    # Every code the pick step itself refuses with has its sentence on the page.
    assert set(pick.MESSAGES) <= set(PICK_CODES)
    offline, none, thrown, unmeasured, failed, silent = out["pickErrorOther"]
    assert offline == "Could not reach the local API." and none == thrown == general
    assert "pages could not be measured" in unmeasured and failed == "It could not be picked when the job was assessed." and silent == ""
    panel = (UI_SRC / "components" / "JobResumePanel.jsx").read_text(encoding="utf-8")
    assert "setError(pickErrorText(err))" in panel and "selection_error).replace" not in panel
    # An old assessment: the page offers Re-assess, never a pick that would be refused.
    assert out["oldAssessment"][:5] == [True, True, False, False, False] and "re-assess the job to get one" in out["oldAssessment"][5]
    assert "!cannotPick && oldAssessment && (" in panel and "!cannotPick && !oldAssessment && (" in panel


def test_the_one_switch_and_the_three_routes() -> None:
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    # The real routes (N5): the pick always names an action; a legacy job (no `resume_gate`) asks nothing.
    assert "JOB_RESUME_ROUTES" not in api and "jobResumeRoutesLive" not in api
    assert 'request("GET", `/api/jobs/suggestions?${query}`)' in api and 'request("POST", "/api/jobs/suggestions"' in api
    assert 'request("POST", "/api/job-resumes/pick", { job_url: jobUrl, profile_id: profileId, action })' in api
    assert 'request("GET", `/api/jobs/brief?${query}`)' in api and "if (!expect) {" in api
    # No model writes a resume from the UI: the tailor call is gone.
    assert 'request("POST", "/api/tailored-resumes", ' not in api and "postTailoredResume(" not in api
    for path in sorted(UI_SRC.rglob("*.js*")):
        assert "postTailoredResume(" not in path.read_text(encoding="utf-8"), path.name
