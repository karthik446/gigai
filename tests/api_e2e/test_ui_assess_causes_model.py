"""0110-10-13 (UI rules): the job page says a typed cause's facts and next action, and what one assessment sends.

Pure JavaScript under the system ``node`` (a LOUD skip without it); synthetic fixtures only.

* ``assessCauseText``: for an error that carries a typed cause's fields (``assess_causes``, as the API sends them in
  the error body), the page says the server's own message, whether a model call started and may have used tokens,
  that no new assessment was stored, and the next action. Before, a 503 ``model_unavailable`` read "This feature is
  not available yet." and nothing said whether tokens were spent. An error with no cause fields is said as before.
* ``assessSendsLine``: the short line beside the Assess / Re-assess actions; a CLI target is "your own login".
* ``assessSummaryLines``: the no-call preview's ``model_input_summary`` as lines (never a line of the user's text:
  the summary holds none).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import assess_causes
from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const answers = await import(__URL__);
const input = JSON.parse(process.argv[1]);
// A rule this fix added answers null on a tree from before it, so the run says what the page did then.
const call = (rule, ...args) => (typeof rule === "function" ? rule(...args) : null);
process.stdout.write(JSON.stringify({
  causes: Object.fromEntries(input.errors.map((err) => [err.code, call(answers.assessCauseText, err)])),
  reassess: Object.fromEntries(input.errors.map((err) => [err.code, answers.reassessErrorText(err)])),
  plain: [call(answers.assessCauseText, { status: 404, code: "profile_not_found", message: "no such profile" }), call(answers.assessCauseText, null)],
  plainReassess: answers.reassessErrorText({ status: 404, code: "profile_not_found", message: "no such profile" }),
  sends: [["codex_cli", "Codex (codex CLI)"], ["ollama_local", "Ollama (local)"], [null, null]].map(([target, label]) => call(answers.assessSendsLine, target, label)),
  summary: [call(answers.assessSummaryLines, input.summary), call(answers.assessSummaryLines, input.fetched), call(answers.assessSummaryLines, null)],
}));
"""

#: The API's status and its generic message for it (``api.js`` ``STATUS_MESSAGES``), which the page showed before.
_STATUS = {
    "model_target_unavailable": (503, "This feature is not available yet."),
    "model_unavailable": (503, "This feature is not available yet."),
    "model_denied": (403, "This action was refused: consent was missing, stale, or the target isn't allowed from this UI."),
    "assess_timeout": (504, "The server timed out handling this request. It may still be running; try checking status again shortly."),
    "model_output_invalid": (502, "the model's answer was invalid after one retry"),
    "assessment_not_stored": (500, "the assessment could not be stored (OSError); nothing was saved"),
}
_DETAIL = {
    "model_target_unavailable": "codex executable is not available on PATH",
    "model_unavailable": "the configured model is unavailable right now",
    "model_denied": "the configured policy refused this model call",
    "assess_timeout": "the model call timed out; try again or pick a faster model target",
    "model_output_invalid": "the model's answer was invalid after one retry",
    "assessment_not_stored": "the assessment could not be stored (OSError); nothing was saved.",
}
SUMMARY = {
    "schema_version": "scout-assess-input:1", "postings": 1, "model_calls": 1, "model_target": "codex_cli", "model_target_runs": "own_login",
    "profiles": [{"profile_id": "profile_1", "label": "Staff Engineer", "postings": 1, "resume_source": "master_evidence"}],
    "answers_used": True, "answers_saved": 12, "stories_used": False, "stories_saved": 0,
    "public_fetch_needed": False, "public_fetch_postings": 0,
}


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD SKIP: node is not on PATH; the UI model cannot run")
    # Each error as the page's ApiError holds it: the status, the per-status message, the server's own (`detail`),
    # and the cause's fields as the API sends them (the product's own table, not a copy).
    errors = [
        {"status": _STATUS[code][0], "code": code, "message": _STATUS[code][1], "detail": _DETAIL[code], **assess_causes.cause_fields(code)}
        for code in sorted(assess_causes.CAUSES)
    ]
    fetched = {**SUMMARY, "profiles": [{**SUMMARY["profiles"][0], "resume_source": "profile_view"}], "answers_used": False, "stories_used": True, "stories_saved": 3, "public_fetch_needed": True}  # type: ignore[dict-item]
    proc = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT.replace("__URL__", json.dumps((UI_SRC / "answersModel.js").as_uri())),
         json.dumps({"errors": errors, "summary": SUMMARY, "fetched": fetched})],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


NO_CALL = "No model call was made and no tokens were used. No new assessment was stored."
CALLED = "A model call started and may have used tokens. No new assessment was stored."


def test_a_typed_cause_says_the_servers_message_the_facts_and_the_next_action(out: dict) -> None:
    def next_action(code: str) -> str:
        return str(assess_causes.CAUSES[code].next_action)

    assert out["causes"] == {
        "assess_timeout": f"The model call timed out; try again or pick a faster model target. {CALLED} Next: {next_action('assess_timeout')}",
        "assessment_not_stored": f"The assessment could not be stored (OSError); nothing was saved. {CALLED} Next: {next_action('assessment_not_stored')}",
        "model_denied": f"The configured policy refused this model call. {NO_CALL} Next: {next_action('model_denied')}",
        "model_output_invalid": f"The model's answer was invalid after one retry. {CALLED} Next: {next_action('model_output_invalid')}",
        "model_target_unavailable": f"Codex executable is not available on PATH. {NO_CALL} Next: {next_action('model_target_unavailable')}",
        "model_unavailable": f"The configured model is unavailable right now. {CALLED} Next: {next_action('model_unavailable')}",
    }
    # Re-assess (and the job page's Assess, which uses the same rule) says the same; never the per-status text.
    assert out["reassess"] == out["causes"]
    assert "This feature is not available yet." not in json.dumps(out["reassess"])


def test_an_error_with_no_typed_cause_is_said_as_before(out: dict) -> None:
    assert out["plain"] == [None, None]
    assert out["plainReassess"] == "no such profile"


def test_the_line_beside_assess_says_what_one_assessment_sends(out: dict) -> None:
    rest = (
        ": the stored posting, your resume with its contact lines removed by pattern (which can miss an unusual name or contact "
        "format), your search preferences, your saved answers and the stories that match."
    )
    assert out["sends"] == [
        "What one assessment sends to your model target (Codex (codex CLI), your own login)" + rest,
        "What one assessment sends to your model target (Ollama (local))" + rest,
        "What one assessment sends to your model target" + rest,
    ]


def test_the_previews_summary_as_lines(out: dict) -> None:
    assert out["summary"] == [
        [
            "Profile Staff Engineer: the lines of your master resume picked for this posting.",
            "Saved answers: 12. Saved stories that can match: none.",
            "The posting's text is stored: nothing is fetched.",
        ],
        [
            "Profile Staff Engineer: this profile's own resume.",
            "Saved answers: none. Saved stories that can match: 3.",
            "The posting's text is not stored: it is fetched from its public board first.",
        ],
        [],
    ]
