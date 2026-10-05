"""0110-10-12 and 0110-10-14 (UI rules): an old assessment can be renewed from its job page, and a posting says its date.

Pure JavaScript under the system ``node`` (a LOUD skip without it); synthetic fixtures only. The browser flows are
``tests/ui/test_reassess_stale.py`` and ``tests/ui/test_posting_date.py``.

0110-10-12, the operator's job: "Matched (old assessment: older prompt)", state "Resume tailored", no open question,
and Re-assess off ("There are no open questions, so there is nothing new to re-assess with."). Two causes:

- the page learned that an assessment is old from ``job_state.assessment_stale`` alone, which the server sends only
  while the state is the verdict's. The stored item says it itself (``basis_stale``, ``basis_stale_reason``), and a
  tailored job's state carries no marker: the page showed the old assessment as current;
- the Re-assess gate looked at the answer boxes only.

Pinned here: the item's own ``basis_stale`` marks the job, whatever its state; the gate is ON for an old assessment
with nothing typed, and says the reason and the cost; the reason is said once, in the Jobs row's words (the row said
"old assessment: older prompt" and "Stale: older settings"); the date of an assessment is when it was MADE
(``updated_at``), not ``created_at``; a Scout label and a tailored resume made before the assessment shown say so;
"minor gaps" is said beside a match only.

0110-10-14: ``postedLine``. "posted" for a board's posting day, "updated" for a board that gives only its last change,
"first seen" when the board gives no date: never one as the other.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import scout_new
from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const [state, answers, postings, jobs, pipeline] = await Promise.all(__URLS__.map((url) => import(url)));
const input = JSON.parse(process.argv[1]);
const now = Date.parse(input.now);
// A rule this fix added answers null on a tree from before it, so the run says what the page did then, not "no such function".
const call = (rule, ...args) => (typeof rule === "function" ? rule(...args) : null);
const gate = (job, states, canAssess = true) => answers.reassessGate({ assessed: true, states, stale: call(state.staleReasonWords, job), canAssess });
const day = (row) => {
  const found = call(postings.postingDate, row);
  return found ? new Date(found.at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : null;
};
process.stdout.write(JSON.stringify({
  stale: input.jobs.map((job) => state.assessmentStaleFor(job)),
  notes: input.jobs.map((job) => state.staleAssessmentNote(job)),
  words: input.jobs.map((job) => call(state.staleReasonWords, job)),
  gates: input.jobs.map((job) => gate(job, [])),
  gateOpenQuestion: gate(input.jobs[0], [{ question_id: "q", filled: false }]),
  gateTyped: gate(input.jobs[0], [{ question_id: "q", filled: true }]),
  gateNoLink: gate(input.jobs[0], [], false),
  gateCurrent: [answers.reassessGate({ assessed: true, states: [] }), answers.reassessGate({ assessed: true, states: [{ question_id: "q", filled: false }] })],
  gateNotAssessed: answers.reassessGate({ assessed: false, states: [], stale: "older prompt" }),
  errors: [{ code: "posting_requirements_unreadable", message: "x" }, { status: 504, message: "x" }, { message: "the server said no" }].map((err) => call(answers.reassessErrorText, err)),
  rowChips: input.rows.map((row) => postings.rowChips(row).filter((chip) => chip.kind === "stale").map((chip) => [chip.label, chip.testId || null])),
  rowScores: input.rows.map((row) => postings.scoreText(row)),
  staleWords: Object.fromEntries(Object.keys(input.serverWords).map((code) => [code, call(postings.staleWords, code)])),
  assessedAt: input.dated.map((job) => jobs.assessedAt(job)),
  tailoredBefore: input.tailored.map(([stored, at]) => call(jobs.tailoredBeforeAssessment, stored, at)),
  gapLines: input.assessments.map((assessment) => call(jobs.minorGapLine, assessment)),
  labels: input.labels.map(([detail, assessedAt]) => pipeline.labelChip(detail, { assessedAt })),
  labelPlain: pipeline.labelChip(input.labels[0][0]),
  posted: input.dates.map((row) => call(postings.postedLine, row, now)),
  ago: input.ago.map((iso) => call(postings.agoText, iso, now)),
  dayOf: input.dates.map(day),
}));
"""

TAILORED = {"state": "tailored", "since": "2026-10-03T10:00:00.000000Z", "next_events": ["applied"]}
MATCHED = {"state": "matched", "since": None, "next_events": ["applied"]}


def _item(reason: str | None, job_state: dict, **more: object) -> dict:
    stale = {"basis_stale": False} if reason is None else {"basis_stale": True, "basis_stale_reason": reason}
    return {"quick": {"job_state": job_state, **stale, **more}, "assessmentSource": "quick"}


JOBS = [
    # 0: the operator's job. Old (older prompt), state "Resume tailored": the state carries NO marker.
    _item("older_prompt", TAILORED),
    _item("settings_changed", {"state": "applied", "since": None, "next_events": []}),  # 1 applied: no marker either
    _item("story_bank_changed", TAILORED),  # 2
    _item("resume_changed", TAILORED),  # 3
    _item("older_prompt", {**MATCHED, "assessment_stale": {"reason": "older_prompt"}}),  # 4 the state says it too: one reason
    _item(None, TAILORED),  # 5 current
    # 6: re-assessed on this page: the response replaced job.quick (no job_state, basis_stale false).
    {"quick": {"basis_stale": False, "prompt_version": "assess-prompt-v8"}, "assessmentSource": "quick"},
    # 7: the run's own assessment is the one shown (newer than the stored item): the item's flag is not about it.
    {"quick": {"job_state": TAILORED, "basis_stale": True, "basis_stale_reason": "older_prompt"}, "assessmentSource": "run", "row": {"jobState": MATCHED}},
]
ROWS = [
    # The server's row: the score column says the assessment is old.
    {"state": "matched", "stale_reason": "older_prompt", "stale_label": "old assessment: older prompt", "score_text": "Matched (old assessment: older prompt) · fit 100% · 12 of 12 requirements · rank 88"},
    {"state": "needs_answers", "stale_reason": "settings_changed", "stale_label": "old assessment: settings changed", "score_text": "Needs your answers (old assessment: settings changed) · fit 50% · 1 of 2 requirements · rank 60"},
    # An older server's row (no score text): the chip says it, in the same words.
    {"state": "matched", "stale_reason": "older_prompt", "score": 100, "score_kind": "assessment"},
    {"state": "matched", "stale_reason": None, "stale_label": None, "score_text": "Matched · fit 100% · 2 of 2 requirements · rank 60"},
]
DATED = [
    {"quick": {"created_at": "2026-10-03T01:15:14.564060Z", "updated_at": "2026-10-05T03:36:00.000000Z"}},  # re-assessed two days later
    {"quick": {"created_at": "2026-10-03T01:15:14.564060Z", "updated_at": None}},  # assessed once
    {"quick": None},
]
STORED = {"updated_at": "2026-10-03T12:00:00.000000Z"}
TAILORED_CASES = [
    [STORED, "2026-10-05T03:36:00.000000Z"],  # tailored, then re-assessed: older
    [STORED, "2026-10-03T11:00:00.000000Z"],  # assessed, then tailored: not older
    [STORED, ""],
    [None, "2026-10-05T03:36:00.000000Z"],
    [STORED, "2026-10-03T12:00:30.000000Z"],  # re-assessed the same day: the line names no day twice
]
_GAPS = [{"requirement": "Cassandra", "class": "list_item", "status": "unclear"}, {"requirement": "ClickHouse", "class": "list_item", "status": "unmet"}]
ASSESSMENTS = [
    {"verdict": "matched_above_threshold", "matrix": _GAPS},
    {"verdict": "pending_user_answers", "matrix": _GAPS, "structured_questions": [{"question_id": "q", "question": "?"}]},
    {"verdict": "not_a_match", "matrix": _GAPS},
]
_LABEL = {"label": {"name": "Scout label", "label": "recommended", "reasons": [], "min_ats": 0, "wording": "L", "updated_at": "2026-10-03T12:00:10.000000Z"}}
LABELS = [
    [_LABEL, "2026-10-05T03:36:00.000000Z"],  # made before the assessment shown
    [_LABEL, "2026-10-03T11:00:00.000000Z"],  # made after it
    [_LABEL, None],
]
NOW = "2026-10-04T18:00:00Z"
DATES = [
    {"published_at": "2026-09-24T12:00:00.000000Z", "published_kind": "posted", "first_seen_at": "2026-10-01T12:00:00.000000Z", "first_seen": "2026-10-01T12:00:00.000000Z"},  # 0 Lever
    {"published_at": "2026-10-01T12:00:00.000000Z", "published_kind": "updated", "first_seen_at": "2026-09-20T12:00:00.000000Z"},  # 1 Greenhouse
    {"published_at": None, "published_kind": None, "first_seen_at": "2026-10-01T12:00:00.000000Z"},  # 2 no board date
    {"first_seen": "2026-10-03T12:00:00.000000Z"},  # 3 an older server's row: first seen only
    {"published_at": "not a date", "first_seen_at": None},  # 4 nothing usable
    {},  # 5
]
AGO = ["2026-10-04T12:00:00Z", "2026-10-03T12:00:00Z", "2026-09-24T12:00:00Z", "2026-07-04T12:00:00Z", "2024-10-04T12:00:00Z", "2026-10-09T12:00:00Z"]


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the UI model cannot run")
    urls = [(UI_SRC / name).as_uri() for name in ("jobStateModel.js", "answersModel.js", "postingsModel.js", "jobModel.js", "pipelineModel.js")]
    payload = json.dumps({
        "jobs": JOBS, "rows": ROWS, "dated": DATED, "tailored": TAILORED_CASES, "assessments": ASSESSMENTS, "labels": LABELS,
        "dates": DATES, "ago": AGO, "now": NOW, "serverWords": scout_new._STALE_WORDS,
    })
    # Days are counted in the reader's time zone: a fixed one here, so "10 days ago" does not depend on the machine.
    proc = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT.replace("__URLS__", json.dumps(urls)), payload],
        capture_output=True, text=True, check=False, env={**os.environ, "TZ": "UTC"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_an_old_assessment_is_marked_whatever_the_jobs_state(out: dict) -> None:
    reasons = [found and found["reason"] for found in out["stale"]]
    assert reasons == ["older_prompt", "settings_changed", "story_bank_changed", "resume_changed", "older_prompt", None, None, None]
    assert out["notes"][:2] == ["Old assessment (older prompt): re-assess", "Old assessment (settings changed): re-assess"]
    assert out["notes"][5:] == [None, None, None]


def test_re_assess_is_on_for_an_old_assessment_with_no_open_question(out: dict) -> None:
    gates = out["gates"]
    # The operator's job: tailored, older prompt, no question open, nothing typed.
    assert gates[0] == {"enabled": True, "stale": True, "reason": "This assessment is old (older prompt). Re-assessing it is one model call."}
    assert [gate["enabled"] for gate in gates] == [True, True, True, True, True, False, False, False]
    assert [gate["reason"].split("(")[1].split(")")[0] for gate in gates[:5]] == ["older prompt", "settings changed", "answers changed", "resume changed", "older prompt"]
    # A current assessment: as it always was.
    assert gates[5] == {"enabled": False, "reason": "There are no open questions, so there is nothing new to re-assess with."}
    assert out["gateCurrent"][0] == gates[5] and out["gateCurrent"][1]["enabled"] is False
    # Old, with a question open and nothing typed: on too (it was "Type an answer ... first").
    assert out["gateOpenQuestion"]["enabled"] is True and out["gateOpenQuestion"]["stale"] is True
    # With an answer typed it is the Re-assess it always was: the answer is saved and the posting re-assessed once.
    assert out["gateTyped"] == {"enabled": True, "reason": "Saves your answer and re-assesses this posting once."}
    # A pasted posting has no link to assess again from: off, and it says why.
    assert out["gateNoLink"]["enabled"] is False and "no stored link" in out["gateNoLink"]["reason"] and "older prompt" in out["gateNoLink"]["reason"]
    assert out["gateNotAssessed"]["enabled"] is False
    assert out["errors"] == [
        "The posting's requirements could not be read, so it was not assessed again. The assessment shown stays.",
        "The model timed out assessing this posting. Try again, or a faster model target.",
        "the server said no",
    ]


def test_the_row_and_the_job_page_say_one_reason_in_the_servers_words(out: dict) -> None:
    # The UI's words for a reason are the server's (scout_new._STALE_WORDS): "older prompt" was "older settings" on the chip.
    assert out["staleWords"] == scout_new._STALE_WORDS
    assert out["words"][:5] == ["older prompt", "settings changed", "answers changed", "resume changed", "older prompt"]
    # ONE label on a row: the score column says it, so no chip beside it says it again in other words.
    assert out["rowChips"] == [[], [], [["Old assessment: older prompt", "stale-chip"]], []]
    assert out["rowScores"][0].count("old assessment") == 1 and "Stale" not in json.dumps(out["rowChips"])
    wiring = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert wiring.count('data-role="assessment-stale"') == 1 and "<AssessNow" in wiring and 'label="Re-assess"' not in wiring


def test_an_assessments_date_is_when_it_was_made(out: dict) -> None:
    assert out["assessedAt"] == ["2026-10-05T03:36:00.000000Z", "2026-10-03T01:15:14.564060Z", ""]
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert "dateLabel(assessedAt(job))" in page and "job.quick.created_at" not in page


def test_what_was_made_before_the_assessment_shown_says_so(out: dict) -> None:
    older, newer, unknown = out["labels"]
    assert older["older"] is True and older["label"] == "Scout label: recommended (from before the latest assessment)" and older["tone"] == "plain"
    assert older["note"].startswith("This label was made before the latest assessment")
    for label in (newer, unknown, out["labelPlain"]):
        assert (label["older"], label["label"], label["tone"], label["note"]) == (False, "Scout label: recommended", "ok", None)
    before, *others = out["tailoredBefore"]
    assert before.startswith("The resume was tailored ") and "before the latest assessment" in before and before.endswith("Tailor again to make it from the assessment shown.")
    assert others == [None, None, None, "The resume was tailored before the latest assessment. Tailor again to make it from the assessment shown."]
    # "minor gaps" is a match's wording: under "Needs your answers" the same rows are what the job waits on.
    assert out["gapLines"] == ["2 minor gaps: Cassandra, ClickHouse", None, None]


def test_a_posting_says_its_date_with_the_word_for_what_the_date_is(out: dict) -> None:
    posted, updated, seen, old_server, bad, empty = out["posted"]
    days = out["dayOf"]
    assert (posted["kind"], posted["text"], posted["at"], posted["date"]) == ("posted", "posted 10 days ago", DATES[0]["published_at"], days[0])
    assert (updated["kind"], updated["text"], updated["at"]) == ("updated", "updated 3 days ago", DATES[1]["published_at"])
    assert (seen["kind"], seen["text"], seen["at"]) == ("first_seen", "first seen 3 days ago", DATES[2]["first_seen_at"])
    assert (old_server["kind"], old_server["text"]) == ("first_seen", "first seen yesterday")
    assert bad is None and empty is None
    # The hover says what the date is; a first sighting or a last change is never called the posting day.
    assert posted["title"].startswith(f"Posted {days[0]} (the board's date).") and "First seen by Scout" in posted["title"]
    assert updated["title"].startswith("The board last changed this posting") and "gives no posting day" in updated["title"]
    assert seen["title"].startswith("First seen by Scout") and "gives no date" in seen["title"]
    for line in (updated, seen, old_server):
        assert "posted" not in line["text"] and "Posted" not in line["title"]
    assert out["ago"] == ["today", "yesterday", "10 days ago", "3 months ago", "2 years ago", "today"]
    view = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert 'data-role="posted"' in view and "postedLine(row)" in view
    assert 'data-role="posted"' in page and "postedLine(rowDated ? listedRow : servedDates)" in page
