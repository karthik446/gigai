"""uat-batch1 (operator UAT 2026-09-27): the UI's words and gating rules, run under node.

``ui/src/jobModel.js``, ``answersModel.js``, ``runText.js`` and ``theme.js``
are pure JavaScript (no React), so this test runs them under the system
``node`` the way ``test_ui_job_model_sort.py`` does (no JS test runner) and
asserts on the JSON the script prints. LOUD skip when ``node`` is not on
PATH. The layout rules that live in CSS/JSX are checked statically, by
reading the source.

What is pinned, by UAT item:

* O1  (SCOPE-ADD-3 D) a rank's reasons and blockers read as words: the
  model's own prose as it is, and a category id an older run stored
  (the retired hosted ranker's reason / mismatch-flag ids, inlined below as
  ``OLDER_RUN_REASON_IDS`` / ``OLDER_RUN_FLAG_IDS`` since that client is
  deleted) humanized; no card line ever carries a raw ``snake_case`` id.
* N5  each open question is placed in the requirement row it settles; one
  Re-assess is N ``POST /api/answers`` bodies with ``reassess`` on the LAST
  one only.
* N7  Re-assess is off until a box is filled; Tailor is on when the verdict
  is matched or every open question has an answer; an action that is off
  always has a reason.
* N4  the job description is a short excerpt.
* N8  ``Must-have: Met`` / ``Can ask: Unclear`` / ``Bonus: Met``.
* N9  ``Sponsorship not stated · N H-1B approvals (FY2026)``, plain
  ``Sponsorship not stated`` without approvals, positive tone only with them.
* N12 the rotation line never says ``?`` or ``unknown``; the run-confirm
  dialog names its sources in words (its company-board line is pinned in
  ``test_ui_uat_batch2_model.py`` since uat-batch2 changed what it counts).
* N2  the theme choice survives a storage that throws; both token sets exist.
* uat-bug-012  a run page names where a run failed; a response for a run the
  view no longer shows is dropped; a run page lists that run's postings only.
* N1/N10 no phone breakpoint, no collapsing menu, a 1400px page, and the
  modal above the sticky top bar.
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
JOB_MODEL_JS = UI_SRC / "jobModel.js"
ANSWERS_MODEL_JS = UI_SRC / "answersModel.js"
RUN_TEXT_JS = UI_SRC / "runText.js"
RANK_MODEL_JS = UI_SRC / "rankModel.js"
THEME_JS = UI_SRC / "theme.js"
STYLES_CSS = UI_SRC / "styles.css"

RAW_ID = re.compile(r"[a-z]+_[a-z_]+")

# The category ids an older run's stored RankScore may carry (reasons /
# mismatch_flags of the retired hosted ranker, deleted in SCOPE-ADD-3 C2),
# inlined here: a run sealed before 0.1.9 still reads.
OLDER_RUN_REASON_IDS = ("title_match", "stack_match", "seniority_match", "domain_mismatch", "seniority_mismatch", "location_mismatch")
OLDER_RUN_FLAG_IDS = ("domain", "seniority", "stack", "location", "sponsorship")

NODE_SCRIPT = """
import * as jobModel from {job_model_url};
import * as answers from {answers_url};
import * as runText from {run_text_url};
import * as theme from {theme_url};
import * as rankModel from {rank_model_url};
const input = JSON.parse(process.argv[1]);

const priorMap = (rows) => new Map((rows || []).map((row) => [row.question_id, row]));
const gating = input.gating.map((item) => {
  const states = answers.answerStates(item.questions, item.drafts, priorMap(item.prior));
  return {
    name: item.name,
    states,
    reassess: answers.reassessGate({ assessed: item.assessed, states }),
    tailor: answers.tailorGate({ assessed: item.assessed, verdict: item.verdict, states, hasUrl: item.hasUrl, hasProfile: item.hasProfile }),
    requests: answers.answerRequests(states, item.jobIdentity),
    unsaved: answers.unsavedAnswerRequests(states),
  };
});
const placed = answers.placeQuestions(input.placement.matrix, input.placement.questions);

const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
const memory = (() => { const data = {}; return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } }; })();
const attrs = {};
const root = { setAttribute: (k, v) => { attrs[k] = v; }, removeAttribute: (k) => { delete attrs[k]; } };
theme.applyTheme(root, "dark");
const applied = { ...attrs };
theme.applyTheme(root, null);

process.stdout.write(JSON.stringify({
  reasons: Object.fromEntries(input.reasonIds.map((id) => [id, rankModel.rankEntry({ score: 50, reasons: [id] }).reasons[0]])),
  flags: Object.fromEntries(input.flagIds.map((id) => [id, rankModel.rankEntry({ score: 50, mismatch_flags: [id] }).blockers[0]])),
  lines: input.ranks.map((rank) => {
    const entry = rankModel.rankEntry(rank);
    return [rankModel.rankReasonsLine(entry), rankModel.rankBlockersLine(entry)].filter(Boolean).join(" | ");
  }),
  gating,
  placed: { rows: Object.fromEntries([...placed.rows].map(([k, v]) => [k, v.map((q) => q.question_id)])), unplaced: placed.unplaced.map((q) => q.question_id) },
  excerpts: input.texts.map((text) => jobModel.jdExcerpt(text)),
  statusLabels: input.rows.map((row) => jobModel.requirementStatusLabel(row)),
  chips: input.chips.map(([sponsorship, h1b]) => jobModel.sponsorshipChip(sponsorship, h1b)),
  rotation: input.rotations.map(([rotation, boards]) => runText.rotationLine(rotation, boards)),
  sources: input.sourceNames.map((name) => runText.sourceLabel(name)),
  failures: input.failures.map(([status, postings]) => runText.runFailure(status, postings)),
  theme: {
    throwingRead: theme.readStoredTheme(throwing),
    throwingWrite: theme.storeTheme(throwing, "dark"),
    nullStorage: [theme.readStoredTheme(null), theme.storeTheme(null, "dark")],
    stored: (theme.storeTheme(memory, "dark"), theme.readStoredTheme(memory)),
    junk: (memory.setItem(theme.THEME_KEY, "purple"), theme.readStoredTheme(memory)),
    effective: [theme.effectiveTheme(null, true), theme.effectiveTheme(null, false), theme.effectiveTheme("light", true), theme.effectiveTheme("dark", false)],
    next: [theme.nextTheme("dark"), theme.nextTheme("light")],
    labels: [theme.toggleLabel("dark"), theme.toggleLabel("light")],
    applied,
    cleared: { ...attrs },
  },
}));
"""

Q_GCP = {"question_id": "cloud:gcp", "question": "Have you run services on GCP?", "requirement": "GCP"}
Q_LEAD = {"question_id": "team:lead", "question": "Have you led a team?", "requirement": "Team leadership"}
Q_LOOSE = {"question_id": "clearance:us", "question": "Do you hold a US clearance?", "requirement": None}
JOB = "https://boards.greenhouse.io/acme/jobs/101"

LONG_PARAGRAPH = " ".join(f"Sentence number {index} describes the role in some detail." for index in range(1, 40))


def _gating(name: str, **fields: object) -> dict:
    base: dict = {
        "name": name,
        "assessed": True,
        "verdict": "pending_user_answers",
        "questions": [Q_GCP, Q_LEAD],
        "drafts": {},
        "prior": [],
        "hasUrl": True,
        "hasProfile": True,
        "jobIdentity": JOB,
    }
    base.update(fields)
    return base


def _payload() -> dict:
    return {
        "reasonIds": [*OLDER_RUN_REASON_IDS, "domain_match", "brand_new_reason"],
        "flagIds": [*OLDER_RUN_FLAG_IDS, "brand_new_flag"],
        "ranks": [
            {"fit": "strong", "score": 89, "reasons": ["title_match"], "mismatch_flags": ["stack"]},
            {"fit": "maybe", "score": 50, "reasons": [], "mismatch_flags": list(OLDER_RUN_FLAG_IDS)},
            {"fit": "no", "score": 10, "reasons": ["brand_new_reason"], "mismatch_flags": ["brand_new_flag"]},
            {"fit": None, "score": None, "reasons": ["title_match"], "mismatch_flags": []},
            None,
            # the model ranker's own line: prose reasons and blockers as they are
            {"score": 82, "reasons": ["Python services match the resume", "Senior level fits"], "blockers": [], "demoted": False},
            {"score": 91, "reasons": ["Strong stack overlap"], "blockers": ["Requires an active TS/SCI clearance"], "demoted": True},
        ],
        "gating": [
            _gating("nothing typed"),
            _gating("one of two typed", drafts={"cloud:gcp": "  Yes, two years.  "}),
            _gating("both typed", drafts={"cloud:gcp": "Yes", "team:lead": "A team of four"}),
            _gating("whitespace only", drafts={"cloud:gcp": "   "}),
            _gating("recorded answer fills an untouched box", questions=[Q_GCP], prior=[{"question_id": "cloud:gcp", "answer": "Yes, on record"}]),
            _gating("recorded answer cleared by the operator", questions=[Q_GCP], drafts={"cloud:gcp": ""}, prior=[{"question_id": "cloud:gcp", "answer": "Yes, on record"}]),
            _gating("matched, no questions", verdict="matched_above_threshold", questions=[]),
            _gating("matched, a question left open", verdict="matched_above_threshold", questions=[Q_GCP]),
            _gating("not a match, no questions", verdict="not_a_match", questions=[]),
            _gating("not assessed", assessed=False, verdict="not_assessed", questions=[]),
            _gating("no url", verdict="matched_above_threshold", questions=[], hasUrl=False),
            _gating("no profile", verdict="matched_above_threshold", questions=[], hasProfile=False),
        ],
        "placement": {
            "matrix": [
                {"requirement": "Python", "class": "hard", "status": "met"},
                {"requirement": "GCP", "class": "askable", "status": "unclear"},
                {"requirement": "Team leadership ", "class": "askable", "status": "unclear"},
            ],
            "questions": [Q_GCP, {**Q_LEAD, "requirement": "team leadership"}, Q_LOOSE, {"question_id": "other:row", "question": "Anything else?", "requirement": "No such row"}],
        },
        "texts": [
            "Short first paragraph.\n\nSecond paragraph, still short.\n\n" + LONG_PARAGRAPH,
            LONG_PARAGRAPH,
            "One short posting.",
            "",
            None,
        ],
        "rows": [
            {"class": "hard", "status": "met"},
            {"class": "askable", "status": "unclear"},
            {"class": "nice_to_have", "status": "met"},
            {"class": "hard", "status": "unmet"},
            {"status": "met"},
            {"class": "some_new_class", "status": "partial"},
        ],
        "chips": [
            ["unknown", {"approvals": 32, "fiscal_years": [2026]}],
            ["unknown", {"approvals": 32, "fiscal_years": [2026], "denials": 2}],
            ["unknown", None],
            ["unknown", {"approvals": 0, "fiscal_years": [2026]}],
            [None, {"approvals": 1, "fiscal_years": [2025, 2026]}],
            ["offered", {"approvals": 32, "fiscal_years": [2026]}],
            ["not_offered", {"approvals": 32, "fiscal_years": [2026]}],
        ],
        "rotations": [
            # a first run ever, the pass running: nothing measured yet
            [{"total": 10370, "first": 1, "last": None, "page_size": None, "runs_per_rotation": None, "estimated": True}, {"status": "running", "done": 212, "total": 10370, "budget_seconds": 1200.0}],
            # a first run, no budget, nothing done yet
            [{"total": 10370, "first": 1, "last": None, "page_size": None, "runs_per_rotation": None, "estimated": True}, {"status": "running", "total": 10370, "budget_seconds": None}],
            # a later run, the pass running: the previous run's page is the estimate
            [{"total": 10370, "first": 2801, "last": None, "page_size": 2800, "runs_per_rotation": 4, "estimated": True}, {"status": "running", "done": 40, "total": 10370, "budget_seconds": 1200.0}],
            # the previous run's page (1 board) is no estimate for a watchlist that grew to 10,371
            [{"total": 10371, "first": 2, "last": None, "page_size": 1, "runs_per_rotation": 4622, "estimated": True}, {"status": "running", "done": 2960, "total": 10371, "budget_seconds": 1200.0}],
            # the previous run covered every board
            [{"total": 10371, "first": 1, "last": None, "page_size": 10371, "runs_per_rotation": 1, "estimated": True}, {"status": "running", "done": 150, "total": 10371, "budget_seconds": 1200.0}],
            # the pass ended: measured
            [{"total": 10370, "first": 1, "last": 2800, "page_size": 2800, "runs_per_rotation": 4, "estimated": False}, {"status": "done", "fetched": 2700, "cached": 90, "failed": 10}],
            [{"total": 12, "first": 1, "last": 12, "page_size": 12, "runs_per_rotation": 1, "estimated": False}, {"status": "done"}],
            # the pass ended and covered nothing new this cycle
            [{"total": 10370, "first": 2801, "last": None, "page_size": 0, "runs_per_rotation": None, "estimated": False}, {"status": "done", "fetched": 0, "cached": 40, "failed": 2}],
            # the rotation block alone (no boards summary)
            [{"total": 10370, "first": 1, "last": None, "page_size": None, "runs_per_rotation": None}, None],
            [None, None],
            [{"first": 1}, {"status": "running"}],
        ],
        "sourceNames": ["exa", "ats", "hiringcafe", "something_else"],
        "failures": [
            [{"status": "failed", "node_receipts": [{"node_slug": "acquire", "status": "failed", "failure": {"code": "acquire_failed", "message": "every source failed"}}]}, 0],
            [
                {
                    "status": "failed",
                    "node_receipts": [
                        {"node_slug": "acquire", "status": "complete", "failure": None},
                        {"node_slug": "assess", "status": "failed", "failure": {"code": "model_unavailable", "message": "no configured model target"}},
                    ],
                },
                3,
            ],
            [{"status": "failed", "node_receipts": []}, None],
            [{"status": "interrupted", "node_receipts": []}, 1],
            [{"status": "succeeded", "node_receipts": []}, 5],
            [{"status": "running", "node_receipts": []}, 0],
            [None, 0],
        ],
    }


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; uat-batch1 UI model checks not run")
    script = NODE_SCRIPT
    for name, path in (
        ("{job_model_url}", JOB_MODEL_JS),
        ("{answers_url}", ANSWERS_MODEL_JS),
        ("{run_text_url}", RUN_TEXT_JS),
        ("{theme_url}", THEME_JS),
        ("{rank_model_url}", RANK_MODEL_JS),
    ):
        script = script.replace(name, json.dumps(path.resolve().as_uri()))
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, "--", json.dumps(_payload())],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - a broken script, not a model bug
        raise AssertionError(f"node printed no JSON: {completed.stdout!r}\n{completed.stderr}") from exc


def _gate(out: dict, name: str) -> dict:
    return next(item for item in out["gating"] if item["name"] == name)


# --- O1 ------------------------------------------------------------------------


def test_every_id_an_older_run_stored_reads_as_words(out: dict) -> None:
    assert set(OLDER_RUN_REASON_IDS) <= set(out["reasons"])
    assert set(OLDER_RUN_FLAG_IDS) <= set(out["flags"])
    for table in (out["reasons"], out["flags"]):
        for raw, words in table.items():
            assert words and words != raw, (raw, words)
            assert not RAW_ID.search(words), f"{raw!r} still reads as an id: {words!r}"
            assert words[0].isupper(), words
    assert out["reasons"]["title_match"] == "Title match"
    assert out["flags"]["stack"] == "Stack"
    # An id no table has ever seen is humanized, never shown raw.
    assert out["reasons"]["brand_new_reason"] == "Brand new reason"
    assert out["flags"]["brand_new_flag"] == "Brand new flag"


def test_the_rank_line_is_words_only(out: dict) -> None:
    assert out["lines"][0] == "Title match | Blocker: Stack"
    assert out["lines"][1] == "Blocker: Domain · Seniority · Stack · Location · Sponsorship"
    assert out["lines"][2] == "Brand new reason | Blocker: Brand new flag"
    assert out["lines"][3] == "Title match"  # no score: the reasons stay, no blocker claimed
    assert out["lines"][4] == ""
    assert out["lines"][5] == "Python services match the resume · Senior level fits"
    assert out["lines"][6] == "Strong stack overlap | Blocker: Requires an active TS/SCI clearance"
    for line in out["lines"]:
        assert "flag:" not in line and not RAW_ID.search(line), line


# --- N5 ------------------------------------------------------------------------


def test_each_question_sits_in_the_row_it_settles(out: dict) -> None:
    placed = out["placed"]
    # The key is the ROW's own spelling; the match ignores case and padding.
    assert placed["rows"] == {"GCP": ["cloud:gcp"], "Team leadership ": ["team:lead"]}
    # No requirement, or one no row carries: still shown, in a row of its own.
    assert placed["unplaced"] == ["clearance:us", "other:row"]


def test_one_reassess_is_one_reassessment(out: dict) -> None:
    both = _gate(out, "both typed")["requests"]
    assert both == [
        {"question_id": "cloud:gcp", "answer": "Yes", "reassess": None},
        {"question_id": "team:lead", "answer": "A team of four", "reassess": {"job_identity": JOB}},
    ]
    one = _gate(out, "one of two typed")["requests"]
    assert one == [{"question_id": "cloud:gcp", "answer": "Yes, two years.", "reassess": {"job_identity": JOB}}]
    assert _gate(out, "nothing typed")["requests"] == []
    assert _gate(out, "whitespace only")["requests"] == []
    for item in out["gating"]:
        assert sum(1 for body in item["requests"] if body["reassess"]) <= 1, item["name"]
        assert all(body["reassess"] is None for body in item["requests"][:-1]), item["name"]


# --- N7 ------------------------------------------------------------------------


def test_reassess_is_off_until_an_answer_is_typed(out: dict) -> None:
    assert _gate(out, "nothing typed")["reassess"]["enabled"] is False
    assert _gate(out, "whitespace only")["reassess"]["enabled"] is False
    assert _gate(out, "one of two typed")["reassess"]["enabled"] is True
    assert _gate(out, "both typed")["reassess"]["enabled"] is True
    assert _gate(out, "matched, no questions")["reassess"]["enabled"] is False
    assert _gate(out, "not assessed")["reassess"]["enabled"] is False
    # An answer already on record for an open question counts without retyping.
    recorded = _gate(out, "recorded answer fills an untouched box")
    assert recorded["states"] == [{"question_id": "cloud:gcp", "value": "Yes, on record", "recorded": "Yes, on record", "filled": True, "isNew": False}]
    assert recorded["reassess"]["enabled"] is True
    assert recorded["unsaved"] == []
    cleared = _gate(out, "recorded answer cleared by the operator")
    assert cleared["reassess"]["enabled"] is False and cleared["tailor"]["enabled"] is False


def test_tailor_needs_a_match_or_every_question_answered(out: dict) -> None:
    assert _gate(out, "matched, no questions")["tailor"]["enabled"] is True
    assert _gate(out, "matched, a question left open")["tailor"]["enabled"] is True
    assert _gate(out, "nothing typed")["tailor"]["enabled"] is False
    one = _gate(out, "one of two typed")["tailor"]
    assert one["enabled"] is False and "1 left" in one["reason"]
    both = _gate(out, "both typed")
    assert both["tailor"]["enabled"] is True
    # Typed answers are saved before tailoring, never re-assessing.
    assert both["unsaved"] == [
        {"question_id": "cloud:gcp", "answer": "Yes", "reassess": None},
        {"question_id": "team:lead", "answer": "A team of four", "reassess": None},
    ]
    assert _gate(out, "not a match, no questions")["tailor"]["enabled"] is True
    assert _gate(out, "not assessed")["tailor"]["enabled"] is False
    assert _gate(out, "no url")["tailor"]["enabled"] is False
    assert _gate(out, "no profile")["tailor"]["enabled"] is False


def test_an_action_that_is_off_says_why(out: dict) -> None:
    for item in out["gating"]:
        for action in ("reassess", "tailor"):
            gate = item[action]
            assert isinstance(gate["reason"], str) and len(gate["reason"]) > 10, (item["name"], action, gate)


# --- N4 / N8 / N9 ----------------------------------------------------------------


def test_the_job_description_is_a_short_excerpt(out: dict) -> None:
    several, one_long, short, empty, missing = out["excerpts"]
    assert several["text"].startswith("Short first paragraph.\n\nSecond paragraph, still short.")
    assert several["truncated"] is True and len(several["text"]) <= 602
    assert one_long["truncated"] is True and len(one_long["text"]) <= 602
    assert one_long["text"].endswith(". …")  # cut at a sentence end
    assert short == {"text": "One short posting.", "truncated": False}
    assert empty is None and missing is None


def test_status_reads_class_then_status(out: dict) -> None:
    assert out["statusLabels"] == ["Must-have: Met", "Can ask: Unclear", "Bonus: Met", "Must-have: Not met", "Met", "Some new class: Partial"]


def test_the_status_rendering_is_option_a_only() -> None:
    """N8, operator decision: one chip, "Must-have: Met". The two other
    renderings and the ``?status=`` switch that showed them are deleted."""

    model = (UI_SRC / "jobModel.js").read_text(encoding="utf-8")
    for name in ("statusStyleFrom", "STATUS_STYLES", "DEFAULT_STATUS_STYLE", "CLASS_LABELS_SHORT"):
        assert name not in model, f"jobModel.js still has {name}"
    body = (UI_SRC / "components" / "AssessmentBody.jsx").read_text(encoding="utf-8")
    assert "statusStyle" not in body and "data-status-style" not in body
    assert '"columns"' not in body and '"badge"' not in body
    assert "<span className={`status-badge ${row.status}`}>{requirementStatusLabel(row)}</span>" in body
    assert "<th>Status</th>" in body and "<th>Type</th>" not in body
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert "statusStyle" not in page and "window.location.search" not in page


def test_the_sponsorship_chip(out: dict) -> None:
    with_record, with_denials, no_record, zero, null_status, offered, not_offered = out["chips"]
    assert with_record["label"] == "Sponsorship not stated · 32 H-1B approvals (FY2026)"
    assert with_record["positive"] is True and with_record["status"] == "unknown"
    assert "2 denials" in with_denials["title"] and "denial" not in with_denials["label"]
    assert no_record["label"] == "Sponsorship not stated" and no_record["positive"] is False
    assert zero["label"] == "Sponsorship not stated" and zero["positive"] is False
    assert null_status["label"] == "Sponsorship not stated · 1 H-1B approval (FY2025–2026)" and null_status["positive"] is True
    # A posting that states its position never shows the count.
    assert offered["label"] == "Sponsors visas" and offered["positive"] is False
    assert not_offered["label"] == "No sponsorship" and not_offered["positive"] is False
    for chip in out["chips"]:
        assert "Unknown" not in chip["label"]


# --- N12 -------------------------------------------------------------------------


def test_the_rotation_line_never_says_unknown(out: dict) -> None:
    first_run, first_run_bare, estimated, stale_estimate, all_boards, measured, every_run, nothing_new, no_boards, no_rotation, no_total = out["rotation"]
    assert all_boards == "Checking all 10,371 company boards this run. 150 done so far. Every board is checked every run."
    assert stale_estimate == "Checking up to 10,371 company boards this run (as many as fit in 20 min). 2,960 done so far. Boards it does not reach go first next run."
    assert first_run == "Checking up to 10,370 company boards this run (as many as fit in 20 min). 212 done so far. Boards it does not reach go first next run."
    assert first_run_bare == "Checking up to 10,370 company boards this run. Boards it does not reach go first next run."
    assert estimated == "Checking about 2,800 of 10,370 company boards this run, starting at board 2,801. 40 done so far. A full pass over every board takes about 4 runs."
    assert measured == "Checked boards 1–2,800 of 10,370 this run. A full pass over every board takes about 4 runs."
    assert every_run == "Checked boards 1–12 of 12 this run. Every board is checked every run."
    assert nothing_new == "Checked 42 of 10,370 company boards this run."
    assert no_boards == "10,370 company boards on your watchlist."
    assert no_rotation is None and no_total is None
    for line in out["rotation"]:
        if line is not None:
            assert "?" not in line and "unknown" not in line.lower() and "null" not in line and "undefined" not in line, line


def test_the_run_dialog_names_its_sources_in_words(out: dict) -> None:
    assert out["sources"] == ["Exa web search", "Company job boards (Greenhouse, Lever, Ashby)", "hiring.cafe", "something_else"]


# --- uat-bug-012 -------------------------------------------------------------------


def test_a_failed_run_says_where_it_failed(out: dict) -> None:
    acquire, assess, bare, interrupted, succeeded, running, missing = out["failures"]
    assert acquire == {"node": "acquire", "line": "This run failed during acquire; no postings.", "message": "every source failed"}
    assert assess == {"node": "assess", "line": "This run failed during assess; the 3 postings it had acquired are below.", "message": "no configured model target"}
    assert bare == {"node": None, "line": "This run failed.", "message": None}
    assert interrupted["line"] == "This run was interrupted; the 1 posting it had acquired is below."
    assert succeeded is None and running is None and missing is None


def test_a_run_page_shows_only_its_own_run() -> None:
    view = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    # Every response handler checks the run the view shows NOW.
    # run-reads-fast: the results reader takes an onPage callback; every page
    # it hands over is checked the same way (N33: createResultsPager).
    for call in ("createResultsPager(id, {", "getRunProgress(id)", "getRunStatus(id)", "getRunStatus(pastRunId)"):
        start = view.index(call)
        assert "shownRunId.current" in view[start : start + 260], f"{call}: its response is not checked against the shown run"
    # SCOPE-ADD-3 D: a re-rank pass's answers are checked the same way
    # (rankModel.createRankPass).
    start = view.index("createRankPass({")
    assert "isCurrent: () => shownRunId.current === id," in view[start : start + 260]
    assert "setRunId(" not in view.replace("setRunId(id);", "", 1).replace("[runId, setRunId]", ""), "set the shown run through showRun() only"
    run_page = view[view.index('if (route.view === "run") {') :]
    # 0.1.10.7 M4b: the run page is the view's last branch (the run-centric landing after it is gone).
    assert "JobsSummaryStrip" not in view
    assert "jobs={runJobs}" in run_page and "jobs={jobs}" not in run_page and "{grid}" not in run_page
    assert 'data-role="run-failure"' in run_page and "Open the last successful run" in run_page


# --- N2 --------------------------------------------------------------------------


def test_the_theme_choice_survives_a_storage_that_throws(out: dict) -> None:
    theme = out["theme"]
    assert theme["throwingRead"] is None and theme["throwingWrite"] is False
    assert theme["nullStorage"] == [None, False]
    assert theme["stored"] == "dark"
    assert theme["junk"] is None  # only "light" / "dark" are ever read back
    assert theme["effective"] == ["dark", "light", "light", "dark"]
    assert theme["next"] == ["light", "dark"]
    assert theme["labels"] == ["Light mode", "Dark mode"]  # the toggle names where a click goes
    assert theme["applied"] == {"data-theme": "dark"} and theme["cleared"] == {}


# --- static: CSS / JSX -------------------------------------------------------------


def _css() -> str:
    return STYLES_CSS.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{(?P<body>[^}}]*)\}}", css)
    assert match is not None, f"styles.css has no `{selector}` rule"
    return match.group("body")


def _tokens(body: str) -> set[str]:
    return set(re.findall(r"(--[a-z-]+)\s*:", body))


def test_both_token_sets_exist_and_match() -> None:
    css = _css()
    light = _tokens(_rule(css, ":root"))
    explicit_dark = _tokens(_rule(css, ':root[data-theme="dark"]'))
    system = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(?P<body>[^}]*)\}', css)
    assert system is not None, "styles.css must apply the dark tokens under prefers-color-scheme unless data-theme is light"
    system_dark = _tokens(system.group("body"))
    assert explicit_dark == system_dark, "the two dark rules must define the same tokens"
    colour_tokens = light - {"--gap"}
    assert colour_tokens <= explicit_dark, f"tokens with no dark value: {sorted(colour_tokens - explicit_dark)}"
    assert {"--bg", "--panel", "--text", "--accent", "--chip-bg"} <= explicit_dark
    # Past the token blocks no rule names a colour: it would not follow the theme.
    after_tokens = css[css.index(':root[data-theme="dark"]') :]
    after_tokens = after_tokens[after_tokens.index("}") :]
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", after_tokens), re.findall(r"#[0-9a-fA-F]{3,8}\b", after_tokens)


def test_desktop_only_layout() -> None:
    css = _css()
    assert "max-width: 1400px" in _rule(css, "#root")
    assert "@media (max-width" not in css, "uat-batch1 N1: no phone breakpoints"
    assert ".menu-" not in css
    top_bar = (UI_SRC / "components" / "TopBar.jsx").read_text(encoding="utf-8")
    assert "menu-toggle" not in top_bar and "menuOpen" not in top_bar
    assert "<ThemeToggle" in top_bar
    grid = _rule(css, ".card-grid")
    assert "grid-auto-rows: 1fr" in grid, "O2: every card the same height"
    minimum = re.search(r"minmax\((\d+)px, 1fr\)", grid)
    assert minimum is not None
    # 4-5 columns: a 1280px laptop (1232px of page) fits 4, the 1400px page fits 5.
    columns = lambda page: (page + 12) // (int(minimum.group(1)) + 12)  # noqa: E731
    assert columns(1280 - 48) == 4 and columns(1400 - 48) == 5


def test_the_modal_sits_above_the_top_bar() -> None:
    css = _css()
    z = lambda selector: int(re.search(r"z-index:\s*(\d+)", _rule(css, selector)).group(1))  # noqa: E731
    assert z(".modal-backdrop") > z(".top-bar")
    assert "position: fixed" in _rule(css, ".modal-backdrop")


def test_the_job_page_is_one_column() -> None:
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    code = "\n".join(line for line in page.splitlines() if not line.lstrip().startswith("//"))
    assert "← Jobs" in code and "Breadcrumb" not in code  # N3
    assert "Show full description" not in code and "jdExcerpt(" in code  # N4
    assert "two-col" not in code and "VerdictHistory" not in code  # N6
    assert "tailorGate(" in code and "useAnswerDrafts(" in code  # N5 / N7
    body = (UI_SRC / "components" / "AssessmentBody.jsx").read_text(encoding="utf-8")
    assert "Save and re-assess" not in body and "<RequirementActions" in body
    actions = (UI_SRC / "components" / "RequirementActions.jsx").read_text(encoding="utf-8")
    assert "title={action.reason}" in actions and "action-help" in actions  # a tooltip AND visible text
