"""0.1.10.7 M4b: the job page's step timeline and Settings' Background pipeline, their model run under node.

``ui/src/pipelineModel.js`` is plain JavaScript, so this runs it under the
system ``node`` over what the SERVER makes on a synthetic home
(``pipeline.overview.job_detail`` and ``overview``, the settings response)
and asserts on the JSON the script prints. LOUD skip without ``node``. What
lives in JSX is pinned by reading the source.

0.1.11 N6 (SPEC 4.2, section 6): the timeline is FOUR rows, Assessed -> Resume
picked -> Scout ATS -> Scout label, always all four; the "N -> M after
tailoring" line is gone. The server of this tree still runs the 0.1.10
pipeline (tailor -> reassess + ats -> label; packet N4 replaces it), so what
it serves is also the LEGACY case the page must read: its ``tailor`` step is
shown in the "Resume picked" row, ``reassess`` has no row, the label says
"made on 0.1.10's tailored resume" and the button "Check again". The rows a
0.1.11 server serves are pinned on hand-made details beside it.

Also pinned: each step's state and the model, tokens and time of its last
attempt; the Scout ATS chip's breakdown and the Scout label chip, each with
the wording the SERVER sends (the UI writes none of its own); "process now";
the job page reads the stored job resume again when a read of the timeline
says the pick step finished since the read before it, never for the first
read, and the timeline is not read again for that resume (0.1.10.9); the
daily caps with the rank counter's warning state past 60 and its stop at
100; the lanes; the approvals with the Approve / Deny body; the settings
form's patch (ONE model step, ``assess``); the last errors as codes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import ats_score
from gigai.scout.find_jobs import background_settings
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResumeInput
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.pipeline import steps, triggers
from gigai.scout.pipeline.overview import job_detail, overview
from gigai.scout.pipeline.runner import PipelineRunner
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.quick_assess import run_quick_assessment
from gigai.scout.scout_cli import scout_group

from tests.support.answers_stories_fixtures import config, pending
from tests.support.pipeline_fixtures import PipelineFixture, assessment, build_pipeline_fixture, resolved_job

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"

_TERRAFORM = ("tooling:terraform", "Have you used Terraform in production?")

SCRIPT = """
import * as m from MODEL_URL;
import { newerStored } from TAILORED_URL;

const data = DATA;
const out = {};
const flat = (stages) => stages.map((steps) => steps.map((step) => [step.name, step.state]));

// The job page's timeline.
out.done = { stages: m.stepTimeline(data.done), legacy: m.isLegacyPipeline(data.done), variantLine: typeof m.variantLine, ats: m.atsChip(data.done), label: m.labelChip(data.done), live: m.pipelineLive(data.done) };
out.waiting = { stages: flat(m.stepTimeline(data.waiting)), ats: m.atsChip(data.waiting), label: m.labelChip(data.waiting) };
out.never = { stages: m.stepTimeline(data.never), flat: flat(m.stepTimeline(null)), live: m.pipelineLive(data.never) };
// A 0.1.11 server's rows (hand-made: this tree's server has no `assess` / `pick` step yet).
const run = { outcome: "error", model: null, input_tokens: null, output_tokens: null, cached_tokens: null, seconds: 120, started_at: "2026-10-03T09:10:00Z" };
out.failed = m.stepTimeline({ steps: [
  { name: "assess", state: "failed", model_target: "codex_cli", attempts: 3, error_code: "assess_timeout", waiting: null, last_run: run },
  { name: "pick", state: "blocked", model_target: null, attempts: 0, error_code: null, waiting: null, last_run: null },
] });
out.capped = m.stepTimeline({ steps: [{ name: "assess", state: "ready", model_target: "claude_cli", attempts: 0, error_code: "daily_cap_reached", waiting: "daily_cap_reached", last_run: null }] });
const now = ["assess", "pick", "ats", "label"].map((name) => ({ name, state: "done", model_target: null, attempts: 1, error_code: null, waiting: null, updated_at: "2026-10-05T10:00:00Z", last_run: null }));
out.fresh = { stages: flat(m.stepTimeline({ steps: now })), legacy: m.isLegacyPipeline({ steps: now, label: { label: "recommended", reasons: [] } }), titles: m.stepTimeline({ steps: now }).map((stage) => stage[0].title) };
// A job the upgrade left: its step rows are gone, its score and label records stay.
const left = { state: null, enabled: true, steps: [], ats: { score: 80 }, label: { name: "Scout label", label: "recommended", reasons: [], wording: "L" } };
out.left = { stages: flat(m.stepTimeline(left)), legacy: m.isLegacyPipeline(left), label: m.labelChip(left, { assessedAt: "2030-01-01T00:00:00Z" }) };
out.ruled = [m.isLegacyPipeline({ steps: [], label: { label: "recommended", rule_version: "scout-label:2" } }), m.isLegacyPipeline({ steps: now, label: { label: "recommended", rule_version: "scout-label:1" } }), m.isLegacyPipeline(null)];
out.atsFull = m.atsChip({ ats: { score: 84, line: "Scout ATS 84: parses cleanly", parts: { fidelity: 38.5, coverage: 31, format: 20 }, key_skills: "9/11", missing: ["Terraform", "SOC 2"], failed_rules: ["single column"], wording: "W" } });
out.labels = [
  m.labelChip({ steps: now, label: { name: "Scout label", label: "needs_attention", reasons: ["open_questions", "ats_below_minimum"], min_ats: 70, wording: "L" } }),
  m.labelChip({ steps: now, label: { label: "ready_to_apply", reasons: [] } }),
];
const ids = { jobIdentity: data.done.job_identity, profileId: data.done.profile_id };
out.actions = {
  done: m.processAction(data.done, { assessed: true, ...ids }),
  fresh: m.processAction({ state: "done", enabled: true, steps: now }, { assessed: true, ...ids }),
  left: m.processAction(left, { assessed: true, ...ids }),
  never: m.processAction(data.never, { assessed: true, ...ids }),
  notAssessed: m.processAction(data.never, { assessed: false, ...ids }),
  running: m.processAction({ state: "running", enabled: true }, { assessed: true, ...ids }),
  off: m.processAction({ state: null, enabled: false }, { assessed: true, ...ids }),
  noProfile: m.processAction(null, { assessed: true, jobIdentity: ids.jobIdentity, profileId: null }),
};
out.results = [m.processResultLine({ result: "enqueued", runner: true }), m.processResultLine({ result: "noop_unchanged", runner: false }), m.processResultLine(null)];
out.liveStates = ["running", "waiting", "done", "failed", "awaiting_approval", null].map((state) => m.pipelineLive({ state }));

// The job page follows the pick step (a 0.1.10 server's tailor step): its stamp, a read against the read before it, and the resume it keeps.
const doneStamp = m.pickDoneStamp(data.done);
out.pick = {
  stamps: [doneStamp, m.pickDoneStamp(data.waiting), m.pickDoneStamp(data.never), m.pickDoneStamp(null), m.pickDoneStamp({ steps: [{ name: "pick", state: "done" }] }), m.pickDoneStamp({ steps: now })],
  finished: {
    first: m.pickFinished(undefined, doneStamp),
    polled: m.pickFinished("", doneStamp),
    same: m.pickFinished(doneStamp, doneStamp),
    again: m.pickFinished(doneStamp, "2030-01-01T00:00:00+00:00"),
    running: m.pickFinished(doneStamp, ""),
    never: m.pickFinished("", ""),
  },
};
const older = { stored_path: "older", updated_at: "2026-10-04T10:00:00+00:00" };
const newer = { stored_path: "newer", updated_at: "2026-10-04T10:05:00+00:00" };
const path = (item) => (item ? item.stored_path : null);
out.pick.kept = [
  path(newerStored(null, older)), path(newerStored(older, null)), path(newerStored(null, null)),
  path(newerStored(older, newer)), path(newerStored(newer, older)), newerStored(older, { ...older }) === older,
];

// Settings: caps, lanes, approvals, errors.
out.caps = Object.fromEntries(Object.entries(data.caps).map(([name, caps]) => [name, m.capRows({ caps })]));
out.capsNone = [m.capRows(null), m.capRows({ caps: null })];
out.lanes = m.laneRows(data.overview);
out.lanesBackoff = m.laneRows({ lanes: [{ lane: "codex_cli", running: 0, cap: 2, error_code: "model_rate_limited", retry_at: "2026-10-03T12:00:00+00:00" }] });
out.status = [
  m.statusLine(data.overview),
  m.statusLine({ setting: { enabled: false, source: "settings_unreadable" }, readable: false, runner: { active: true }, yielding_to: "assess_batch", counts: { jobs: {} } }),
  m.statusLine({ setting: { enabled: true, source: "environment" }, readable: true, runner: { active: false }, yielding_to: null, counts: { jobs: {} } }),
  m.statusLine(null),
];
out.approvals = m.approvalRows(data.overview);
out.approvalTokens = m.approvalRows({ approvals: { items: [{ id: "apv_1", jobs: 3, waiting_jobs: 1, est_calls: 2, est_tokens: 61000, trigger: "profile_changed", created_at: "t" }] } });
out.decisions = [m.decisionBody(true), m.decisionBody(false)];
out.errors = m.errorRows({ errors: [{ profile_id: "p1", job_identity: "https://x.test/1", step: "assess", error_code: "assess_timeout", attempt: 2, at: "2026-10-03T09:12:00Z" }, { step: "ats", error_code: null, attempt: 1, at: "t" }] });
out.noErrors = m.errorRows(data.overview);

// Settings: the form.
const draft = m.pipelineDraft(data.settings);
out.draft = draft;
out.shown = [m.hasPipelineSettings(data.settings), m.hasPipelineSettings({ settings: { sources: {} } }), m.hasPipelineSettings(null)];
out.patches = {
  none: m.pipelinePatch(draft, data.settings),
  off: m.pipelinePatch({ ...draft, enabled: false }, data.settings),
  caps: m.pipelinePatch({ ...draft, jobsPerTrigger: "5", callsPerDay: "20", labelMinAts: "70" }, data.settings),
  rank: m.pipelinePatch({ ...draft, rankCallsPerDay: "80", rankWarnAt: "40" }, data.settings),
  models: m.pipelinePatch({ ...draft, assessModel: "claude_cli" }, data.settings),
  modelBack: m.pipelinePatch({ ...m.pipelineDraft(data.settingsWithAssess), assessModel: "" }, data.settingsWithAssess),
  retired: [m.pipelineDraft(data.settingsWithModel), m.pipelinePatch(m.pipelineDraft(data.settingsWithModel), data.settingsWithModel)],
};
out.errorsForm = [
  m.pipelineFormError(draft),
  m.pipelineFormError({ ...draft, callsPerDay: "1001" }),
  m.pipelineFormError({ ...draft, labelMinAts: "101" }),
  m.pipelineFormError({ ...draft, jobsPerTrigger: "-1" }),
  m.pipelineFormError({ ...draft, rankWarnAt: "" }),
  m.pipelineFormError({ ...draft, rankCallsPerDay: "50", rankWarnAt: "60" }),
];
out.modelOptions = m.modelOptions().map((option) => option.value);
console.log(JSON.stringify(out));
"""


def _job(n: int) -> str:
    return f"https://jobs.example.test/acme/role-{n:02d}"


def _ask(fx: PipelineFixture, url: str) -> None:
    """A stored (base) assessment of ``url`` that leaves one question open."""

    fx.model.assessed = pending(*_TERRAFORM)
    run_quick_assessment(
        AssessRequest(job=AssessJobInput(job_url=url), resume=AssessResumeInput(profile_id=fx.profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(url),
    )
    fx.model.assessed = assessment(met=2)
    fx.model.assess_prompts.clear()


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the pipeline model was NOT run")
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    fx = build_pipeline_fixture(tmp_path, monkeypatch, base=False)
    # One job of a trigger runs, the rest wait for an approval.
    path = settings_path(fx.home_root, fx.target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "pipeline": {"auto_jobs_per_trigger": 1, "label_min_ats": 10}}), encoding="utf-8")
    for n in range(3):
        _ask(fx, _job(n))
    saved = CliRunner().invoke(
        scout_group,
        ["answers", "save", _TERRAFORM[0], "--answer-text", "Yes, three years of Terraform modules for our clusters.", "--question", _TERRAFORM[1],
         "--home", str(fx.home_root), "--target", str(fx.target), "--json"],
    )
    assert saved.exit_code == 0, saved.output
    fired = json.loads(saved.output)["pipeline"]
    (ran,) = [item["job_identity"] for item in fired["enqueued"]]
    held = sorted(item["job_identity"] for item in fired["awaiting_approval"])
    assert len(held) == 2
    drained = PipelineRunner(home_root=fx.home_root, target=fx.target, config=config(fx.home_root), busy=lambda: None).drain()
    assert drained.model_calls == 2

    caps = {}
    for name, spend in (("fresh", 0), ("at60", 60), ("at61", 1), ("at100", 39)):
        if spend:
            assert triggers.spend_rank_calls(fx.home_root, fx.target, spend)["allowed"] is True
        caps[name] = triggers.caps(fx.home_root, fx.target)
    assert triggers.spend_rank_calls(fx.home_root, fx.target, 1)["allowed"] is False  # the 101st is refused

    data = {
        "done": job_detail(fx.home_root, fx.target, fx.profile_id, ran),
        "waiting": job_detail(fx.home_root, fx.target, fx.profile_id, held[0]),
        "never": job_detail(fx.home_root, fx.target, fx.profile_id, "https://jobs.example.test/acme/never"),
        "overview": overview(fx.home_root, fx.target),
        "caps": caps,
        "settings": background_settings.background_settings(fx.home_root, fx.target),
    }
    path.write_text(json.dumps({"schema_version": "scout-settings:1", "pipeline": {"models": {"tailor": "codex_cli"}}}), encoding="utf-8")
    data["settingsWithModel"] = background_settings.background_settings(fx.home_root, fx.target)
    # What a 0.1.11 server answers for a file that names the one model step (hand-made: this tree's settings know no `assess`).
    data["settingsWithAssess"] = {"settings": {"pipeline": {**data["settings"]["settings"]["pipeline"], "models": {"assess": "codex_cli"}}, "rank": data["settings"]["settings"]["rank"]}}
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "pipelineModel.js").resolve().as_uri())).replace("DATA", json.dumps(data))
    script = script.replace("TAILORED_URL", json.dumps((UI_SRC / "tailoredResumeModel.js").resolve().as_uri()))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    result = json.loads(completed.stdout)
    result["data"] = data
    result["held"] = held
    return result


def test_the_timeline_is_four_rows_assessed_picked_ats_label(out: dict) -> None:
    # A 0.1.11 server's four steps, one row each, in order.
    assert out["fresh"] == {"stages": [[["assess", "done"]], [["pick", "done"]], [["ats", "done"]], [["label", "done"]]], "legacy": False, "titles": ["Assessed", "Resume picked", "Scout ATS", "Scout label"]}
    # What THIS tree's server serves (the 0.1.10 pipeline): still four rows; its tailor step, the one that stores the
    # job's resume, is in the "Resume picked" row, and its re-assessment of the tailored resume has no row.
    done = out["done"]
    served = {step["name"]: step for step in out["data"]["done"]["steps"]}
    assert set(served) == {"tailor", "reassess", "ats", "label"}, "this tree's pipeline: update this test with packet N4"
    assert [[step["name"] for step in stage] for stage in done["stages"]] == [["assess"], ["pick"], ["ats"], ["label"]]
    steps_by_name = {step["name"]: step for stage in done["stages"] for step in stage}
    assert [steps_by_name[name]["title"] for name in ("assess", "pick", "ats", "label")] == ["Assessed", "Resume picked", "Scout ATS", "Scout label"]
    assert [steps_by_name[name]["state"] for name in ("assess", "pick", "ats", "label")] == ["not_started", "done", "done", "done"]
    assert done["legacy"] is True and done["live"] is False
    # The step in the "Resume picked" row carries the numbers of ITS last attempt (the metrics); the local steps no model.
    assert steps_by_name["pick"]["model"] and steps_by_name["pick"]["seconds"] is not None
    assert steps_by_name["pick"]["model"].startswith(served["tailor"]["model_target"].split("_")[0]), "the model target's short name, then the model id"
    assert steps_by_name["ats"]["model"] is None and steps_by_name["label"]["model"] is None
    # A job waiting for an approval: its step says so, the rest wait behind it; nothing is made up.
    assert out["waiting"]["stages"] == [[["assess", "not_started"]], [["pick", "awaiting_approval"]], [["ats", "blocked"]], [["label", "blocked"]]]
    assert (out["waiting"]["ats"], out["waiting"]["label"]) == (None, None)
    # A job that never entered the pipeline still shows the four rows, not started.
    assert out["never"]["flat"] == [[["assess", "not_started"]], [["pick", "not_started"]], [["ats", "not_started"]], [["label", "not_started"]]]
    assert {step["stateLabel"] for stage in out["never"]["stages"] for step in stage} == {"Not started"} and out["never"]["live"] is False
    assess, pick = out["failed"][0][0], out["failed"][1][0]
    assert (assess["state"], assess["tone"], assess["error"], assess["attempts"], assess["seconds"], assess["tokens"]) == ("failed", "danger", "assess_timeout", 3, "2 min", None)
    assert (pick["state"], pick["stateLabel"]) == ("blocked", "Waiting for the step before") and out["failed"][2][0]["state"] == "not_started"
    capped = out["capped"][0][0]
    assert (capped["stateLabel"], capped["waiting"], capped["error"]) == ("Queued", "the daily cap of model calls is reached", None)
    assert out["liveStates"] == [True, True, False, False, False, False]


def test_the_before_and_after_tailoring_line_is_gone(out: dict) -> None:
    # 0.1.11 (SPEC 4.2): one assessment and no tailored variant: "73 -> 91 after tailoring" has nothing to print.
    assert out["done"]["variantLine"] == "undefined"
    assert out["data"]["done"]["requirements_met"]["tailored"] is not None, "this tree's server still serves the pair; the page shows no line for it"
    timeline = (UI_SRC / "components" / "PipelineTimeline.jsx").read_text(encoding="utf-8")
    assert "tailored-variant" not in timeline and "variantLine" not in timeline and "after tailoring" not in timeline.split("export default function")[1]


def test_a_job_the_0_1_10_pipeline_processed_keeps_its_label_and_says_so(out: dict) -> None:
    # After the upgrade a finished job has no step rows; its score and label records stay and keep showing.
    left = out["left"]
    assert left["legacy"] is True and left["stages"] == [[["assess", "not_started"]], [["pick", "not_started"]], [["ats", "not_started"]], [["label", "not_started"]]]
    label = left["label"]
    assert label["label"] == "Scout label: recommended (made on 0.1.10's tailored resume)" and label["legacy"] is True and label["tone"] == "plain"
    assert label["older"] is False, "it says the stronger thing once: made by 0.1.10"
    assert label["note"].startswith("This label was made by GigAI 0.1.10") and "Check again" in label["note"]
    ids = {"job_identity": out["data"]["done"]["job_identity"], "profile_id": out["data"]["done"]["profile_id"]}
    assert out["actions"]["left"] == {"enabled": True, "label": "Check again", "reason": "", "body": ids}
    # A label's own rule version says it when the server sends one; nothing held is not legacy.
    assert out["ruled"] == [False, True, False]


def test_the_ats_chip_and_the_label_chip_carry_only_the_servers_wording(out: dict) -> None:
    served = out["data"]["done"]
    ats, label = out["done"]["ats"], out["done"]["label"]
    assert ats["label"] == f"Scout ATS {served['ats']['score']}" and ats["line"] == served["ats"]["line"]
    assert ats["wording"] == ats_score.ATS_WORDING and label["wording"] == steps.LABEL_WORDING
    # This tree's server ran the 0.1.10 pipeline for this job: its label says so (test above).
    assert label["label"] == f"{steps.LABEL_NAME}: {served['label']['label'].replace('_', ' ')} (made on 0.1.10's tailored resume)" and label["minAts"] == 10
    assert out["atsFull"] == {
        "label": "Scout ATS 84", "score": 84, "line": "Scout ATS 84: parses cleanly",
        "rows": [
            "Parse fidelity: 38.5 of 40", "Keyword coverage: 31 of 40", "Format rules: 20 of 20", "Key skills found: 9/11",
            "Missing: Terraform, SOC 2", "Format rules not met: single column",
        ],
        "wording": "W",
    }
    attention, unknown = out["labels"]
    assert attention == {
        "label": "Scout label: needs attention", "code": "needs_attention", "tone": "warn",
        "reasons": ["open questions", "ats below minimum"], "minAts": 70, "wording": "L",
        # 0110-10-12: not older than the assessment shown (none was named): no suffix, no note.
        "older": False, "legacy": False, "note": None, "at": None,
    }
    assert unknown is None, "a label code the backend does not have is never shown"
    # The UI's own files hold neither sentence: both arrive in the response.
    for path in sorted(UI_SRC.rglob("*.js*")):
        text = path.read_text(encoding="utf-8")
        assert "Not any real ATS" not in text and "Not a prediction of what an employer" not in text, path.name
    assert set(steps.LABELS) == {"recommended", "needs_attention"}  # the two codes the model has words for


def test_process_now(out: dict) -> None:
    actions = out["actions"]
    ids = {"job_identity": out["data"]["done"]["job_identity"], "profile_id": out["data"]["done"]["profile_id"]}
    assert actions["never"] == {"enabled": True, "label": "Process now", "reason": "", "body": ids}
    assert actions["fresh"] == {"enabled": True, "label": "Process again", "reason": "", "body": {**ids, "force": True}}
    assert actions["done"] == {"enabled": True, "label": "Check again", "reason": "", "body": {**ids, "force": True}}, "this tree's finished job is a 0.1.10 one"
    assert (actions["notAssessed"]["enabled"], actions["notAssessed"]["reason"]) == (False, "Assess this posting first.")
    assert (actions["running"]["enabled"], actions["running"]["label"]) == (False, "Processing…")
    assert actions["off"]["enabled"] is False and "off" in actions["off"]["reason"]
    assert actions["noProfile"]["enabled"] is False
    assert out["results"] == ["enqueued", "noop unchanged; this server runs no pipeline (run: gigai scout pipeline run --once)", None]


def test_the_job_page_reads_the_stored_resume_again_when_the_pick_step_finishes(out: dict) -> None:
    """0.1.10.9 (found by the U3 browser flow): the panel and the job's state needed a reload."""

    served = {step["name"]: step for step in out["data"]["done"]["steps"]}
    pick = out["pick"]
    # The stamp is the `updated_at` of the step that stores the resume once it is done (`pick`; this tree's `tailor`);
    # a step that waits, or never started, has none.
    assert served["tailor"]["state"] == "done" and isinstance(served["tailor"]["updated_at"], str) and served["tailor"]["updated_at"]
    assert pick["stamps"] == [served["tailor"]["updated_at"], "", "", "", "done", "2026-10-05T10:00:00Z"]
    # The first read of a job tells nothing new (the page read the stored resume at the same moment); a read that
    # finds the step done after one that did not, or done again later, does; a step that runs again does not, yet.
    assert pick["finished"] == {"first": False, "polled": True, "same": False, "again": True, "running": False, "never": False}
    # The quiet re-read never puts an older resume, or nothing, over what the page holds; the same one changes nothing.
    assert pick["kept"] == ["older", "older", None, "newer", "newer", True]
    # The wiring: the timeline reports it, the page reads again, and its timeline is not read again for that resume.
    timeline = (UI_SRC / "components" / "PipelineTimeline.jsx").read_text(encoding="utf-8")
    assert "pickFinished(before, stamp)" in timeline and "pickDone.current();" in timeline
    hook = (UI_SRC / "components" / "JobResumePanel.jsx").read_text(encoding="utf-8")
    assert "setStored((held) => newerStored(held, latestStored(response.items)))" in hook
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert "onPickDone={resume.reload}" in page
    assert 'refreshKey={`${assessment ? assessment.verdict || "assessed" : "none"}:${resume.changes}`}' in page
    assert "resume.stored.updated_at" not in page, "the timeline's refreshKey follows the stored resume again: it resets itself"


def test_the_caps_warn_past_60_rank_calls_and_stop_at_100(out: dict) -> None:
    caps = out["caps"]
    fresh_pipeline, fresh_rank = caps["fresh"]
    assert (fresh_pipeline["id"], fresh_pipeline["used"], fresh_pipeline["limit"], fresh_pipeline["state"]) == ("pipeline", 2, 40, "ok")
    assert fresh_pipeline["text"] == "2 of 40 today" and fresh_pipeline["warnAt"] is None and fresh_pipeline["note"] == ""
    assert (fresh_rank["id"], fresh_rank["used"], fresh_rank["limit"], fresh_rank["warnAt"], fresh_rank["state"]) == ("rank", 0, 100, 60, "ok")
    assert fresh_rank["note"] == "Warning at 60."
    assert (caps["at60"][1]["used"], caps["at60"][1]["state"]) == (60, "ok"), "the 60th call is not flagged (the server's flag)"
    assert (caps["at61"][1]["used"], caps["at61"][1]["state"], caps["at61"][1]["note"]) == (61, "warning", "Past the warning level of 60.")
    assert (caps["at100"][1]["used"], caps["at100"][1]["state"]) == (100, "reached")
    assert caps["at100"][1]["note"] == "The cap is reached: it starts again tomorrow." and caps["at100"][1]["text"] == "100 of 100 today"
    assert out["data"]["caps"]["at61"]["rank_calls"]["warning"] is True and out["data"]["caps"]["at60"]["rank_calls"]["warning"] is False
    assert out["capsNone"] == [[], []]


def test_the_lanes_the_status_line_the_approvals_and_the_errors(out: dict) -> None:
    overview_ = out["data"]["overview"]
    assert [lane["lane"] for lane in out["lanes"]] == [lane["lane"] for lane in overview_["lanes"]] and out["lanes"]
    assert all(lane["state"] == "idle" and lane["text"].endswith("running") and lane["note"] == "" for lane in out["lanes"])
    assert out["lanesBackoff"] == [
        {"lane": "codex_cli", "text": "0 of 2 running", "state": "backoff", "note": "model_rate_limited, again at 2026-10-03T12:00:00+00:00"},
    ]
    assert out["status"][0] == "On · set by: setting · no runner in this server · jobs: 2 awaiting approval, 1 done".replace("set by: setting · ", "")
    assert out["status"][1] == "Off · the settings file cannot be read · waiting for: assess batch"
    assert out["status"][2] == "On · set by: environment · runner stopped" and out["status"][3] == ""
    (approval,) = out["approvals"]
    (served,) = overview_["approvals"]["items"]
    assert (approval["id"], approval["jobs"], approval["calls"], approval["tokens"]) == (served["id"], 2, served["est_calls"], None)
    assert approval["text"] == f"2 jobs · ~{served['est_calls']} model calls" and approval["trigger"] == "answer saved"
    assert sorted(item["job_identity"] for item in served["waiting"]) == out["held"]
    assert out["approvalTokens"][0]["text"] == "1 job · ~2 model calls · ~61k tokens"
    assert out["decisions"] == [{"approve": True, "actor": "operator"}, {"approve": False, "actor": "operator"}]
    first, second = out["errors"]
    assert (first["step"], first["code"], first["attempt"], first["job"], first["profileId"]) == ("Assessed", "assess_timeout", 2, "https://x.test/1", "p1")
    assert (second["step"], second["code"]) == ("Scout ATS", "unknown") and out["noErrors"] == []


def test_the_settings_form_sends_only_what_changed(out: dict) -> None:
    assert out["draft"] == {
        "enabled": True, "jobsPerTrigger": "1", "callsPerDay": "40", "labelMinAts": "10", "rankCallsPerDay": "100", "rankWarnAt": "60",
        "assessModel": "",
    }
    assert out["shown"] == [True, False, False]
    assert out["patches"] == {
        "none": None,
        "off": {"pipeline": {"enabled": False}},
        "caps": {"pipeline": {"auto_jobs_per_trigger": 5, "max_model_calls_per_day": 20, "label_min_ats": 70}},
        "rank": {"rank": {"max_calls_per_day": 80, "warn_calls_per_day": 40}},
        # 0.1.11: ONE model step. The two settings of 0.1.10 are neither shown nor sent: what the file holds stays.
        "models": {"pipeline": {"models": {"assess": "claude_cli"}}},
        "modelBack": {"pipeline": {"models": {"assess": None}}},
        "retired": out["patches"]["retired"],
    }
    retired_draft, retired_patch = out["patches"]["retired"]
    assert retired_draft["assessModel"] == "" and "tailorModel" not in retired_draft and retired_patch is None, "a 0.1.10 file's tailor model is neither shown nor sent"
    ok, too_many, over_100, negative, empty, warn_over = out["errorsForm"]
    assert ok == "" and "0 to 1000" in too_many and "0 to 100." in over_100 and negative and empty
    assert warn_over == "Rank warning level: not above the rank calls a day."
    assert out["modelOptions"] == ["", "ollama_local", "codex_cli", "claude_cli", "openrouter_api"]


def test_the_server_takes_the_patches_the_form_makes(out: dict) -> None:
    """Every patch the model builds is one the settings route accepts (the server's own validation)."""

    for name in ("off", "caps", "rank"):
        checked = background_settings.validate_patch(out["patches"][name])
        assert set(checked) == set(out["patches"][name]), name
    # `pipeline.models.assess` is 0.1.11's one model step (SPEC 4.2). The server knows it from packet N4; until
    # then this tree's server refuses the name, and says so (nothing is written).
    from gigai.scout.pipeline import settings as pipeline_settings

    for name in ("models", "modelBack"):
        if "assess" in pipeline_settings.MODEL_STEPS:
            assert set(background_settings.validate_patch(out["patches"][name])) == set(out["patches"][name]), name
        else:
            with pytest.raises(background_settings.SettingsError):
                background_settings.validate_patch(out["patches"][name])
    with pytest.raises(background_settings.SettingsError):  # and what the form refuses, the server refuses too
        background_settings.validate_patch({"rank": {"max_calls_per_day": 50, "warn_calls_per_day": 60}})


def test_the_job_page_and_settings_wiring_and_test_ids() -> None:
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert 'request("GET", "/api/pipeline")' in api and 'request("GET", `/api/pipeline/job?${query}`)' in api
    assert 'request("POST", `/api/pipeline/approvals/${encodeURIComponent(approvalId)}`, body)' in api
    assert 'request("POST", "/api/pipeline/process", body)' in api
    timeline = (UI_SRC / "components" / "PipelineTimeline.jsx").read_text(encoding="utf-8")
    assert 'data-testid="step-timeline"' in timeline and 'testId="ats-chip"' in timeline and 'testId="scout-label-chip"' in timeline
    assert "postPipelineProcess(action.body)" in timeline and 'data-action="process-now"' in timeline
    assert "{ats.wording && <p className=\"muted\">{ats.wording}</p>}" in timeline and "{label.wording && <p className=\"muted\">{label.wording}</p>}" in timeline
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert "<PipelineTimeline" in page and "assessed={Boolean(assessment)}" in page
    panel = (UI_SRC / "components" / "PipelinePanel.jsx").read_text(encoding="utf-8")
    assert 'data-testid="background-panel"' in panel and 'data-testid="approvals-list"' in panel
    assert "postPipelineApproval(id, decisionBody(approve))" in panel and 'data-action="approve"' in panel and 'data-action="deny"' in panel
    assert "putBackgroundSettings(patch)" in panel and "<code>{item.code}</code>" in panel
    settings = (UI_SRC / "views" / "SettingsView.jsx").read_text(encoding="utf-8")
    assert settings.index("<BackgroundUpdatesPanel />") < settings.index("<PipelinePanel />") < settings.index("<ModelMetricsPanel />")


def test_the_built_bundle_has_the_timeline_and_the_panel() -> None:
    bundle = "".join(path.read_text(encoding="utf-8") for path in (UI / "dist" / "assets").glob("index-*.js"))
    for needle in ("/api/pipeline/job?", "/api/pipeline/process", "/api/pipeline/approvals/", "step-timeline", "ats-chip", "scout-label-chip", "background-panel", "approvals-list", "Resume picked", "Check again"):
        assert needle in bundle, needle
    assert "after tailoring" not in bundle and "Tailor again" not in bundle, "0.1.11: the bundle offers no tailoring"
