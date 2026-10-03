"""0.1.10.7 M4b: the job page's step timeline and Settings' Background pipeline, their model run under node.

``ui/src/pipelineModel.js`` is plain JavaScript, so this runs it under the
system ``node`` over what the SERVER makes on a synthetic home
(``pipeline.overview.job_detail`` and ``overview``, the settings response)
and asserts on the JSON the script prints. LOUD skip without ``node``. What
lives in JSX is pinned by reading the source.

Pinned: the timeline is always tailor -> reassess + Scout ATS -> Scout label,
with each step's state and the model, tokens and time of its last attempt;
"N -> M after tailoring" is the backend's requirements_met numbers; the Scout
ATS chip's breakdown and the Scout label chip, each with the wording the
SERVER sends (the UI writes none of its own); "process now"; the daily caps
with the rank counter's warning state past 60 and its stop at 100; the lanes;
the approvals with the Approve / Deny body; the settings form's patch; the
last errors as codes.
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

const data = DATA;
const out = {};
const flat = (stages) => stages.map((steps) => steps.map((step) => [step.name, step.state]));

// The job page's timeline.
out.done = { stages: m.stepTimeline(data.done), variant: m.variantLine(data.done), ats: m.atsChip(data.done), label: m.labelChip(data.done), live: m.pipelineLive(data.done) };
out.waiting = { stages: flat(m.stepTimeline(data.waiting)), variant: m.variantLine(data.waiting), ats: m.atsChip(data.waiting), label: m.labelChip(data.waiting) };
out.never = { stages: m.stepTimeline(data.never), flat: flat(m.stepTimeline(null)), live: m.pipelineLive(data.never) };
out.failed = m.stepTimeline({ steps: [
  { name: "tailor", state: "failed", model_target: "codex_cli", attempts: 3, error_code: "assess_timeout", waiting: null, last_run: { outcome: "error", model: null, input_tokens: null, output_tokens: null, seconds: 120.4 } },
  { name: "reassess", state: "ready", model_target: "claude_cli", attempts: 0, error_code: "daily_cap_reached", waiting: "daily_cap_reached", last_run: null },
] });
out.variants = [
  m.variantLine({ requirements_met: { base: { met: 8, total: 11, percent: 73 }, tailored: { met: 10, total: 11, percent: 91 } } }),
  m.variantLine({ requirements_met: { base: { met: 8, total: 11, percent: 73 }, tailored: null } }),
  m.variantLine({ requirements_met: { base: { met: 9, total: 11, percent: 82 }, tailored: { met: 9, total: 11, percent: 82 } } }),
];
out.atsFull = m.atsChip({ ats: { score: 84, line: "Scout ATS 84: parses cleanly", parts: { fidelity: 38.5, coverage: 31, format: 20 }, key_skills: "9/11", missing: ["Terraform", "SOC 2"], failed_rules: ["single column"], wording: "W" } });
out.labels = [
  m.labelChip({ label: { name: "Scout label", label: "needs_attention", reasons: ["open_questions", "ats_below_minimum"], min_ats: 70, wording: "L" } }),
  m.labelChip({ label: { label: "ready_to_apply", reasons: [] } }),
];
const ids = { jobIdentity: data.done.job_identity, profileId: data.done.profile_id };
out.actions = {
  done: m.processAction(data.done, { assessed: true, ...ids }),
  never: m.processAction(data.never, { assessed: true, ...ids }),
  notAssessed: m.processAction(data.never, { assessed: false, ...ids }),
  running: m.processAction({ state: "running", enabled: true }, { assessed: true, ...ids }),
  off: m.processAction({ state: null, enabled: false }, { assessed: true, ...ids }),
  noProfile: m.processAction(null, { assessed: true, jobIdentity: ids.jobIdentity, profileId: null }),
};
out.results = [m.processResultLine({ result: "enqueued", runner: true }), m.processResultLine({ result: "noop_unchanged", runner: false }), m.processResultLine(null)];
out.liveStates = ["running", "waiting", "done", "failed", "awaiting_approval", null].map((state) => m.pipelineLive({ state }));

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
out.errors = m.errorRows({ errors: [{ profile_id: "p1", job_identity: "https://x.test/1", step: "tailor", error_code: "assess_timeout", attempt: 2, at: "2026-10-03T09:12:00Z" }, { step: "ats", error_code: null, attempt: 1, at: "t" }] });
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
  models: m.pipelinePatch({ ...draft, tailorModel: "claude_cli", reassessModel: "codex_cli" }, data.settings),
  modelBack: m.pipelinePatch({ ...m.pipelineDraft(data.settingsWithModel), tailorModel: "" }, data.settingsWithModel),
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
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "pipelineModel.js").resolve().as_uri())).replace("DATA", json.dumps(data))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    result = json.loads(completed.stdout)
    result["data"] = data
    result["held"] = held
    return result


def test_the_timeline_is_tailor_then_reassess_and_ats_then_the_label(out: dict) -> None:
    done = out["done"]
    assert [[step["name"] for step in stage] for stage in done["stages"]] == [["tailor"], ["reassess", "ats"], ["label"]]
    steps_by_name = {step["name"]: step for stage in done["stages"] for step in stage}
    assert {step["state"] for step in steps_by_name.values()} == {"done"} and {step["tone"] for step in steps_by_name.values()} == {"ok"}
    assert [steps_by_name[name]["title"] for name in ("tailor", "reassess", "ats", "label")] == [
        "Tailor resume", "Assess the tailored resume", "Scout ATS", "Scout label",
    ]
    # The model steps name their model target and the time of their last attempt (the metrics); the local steps no model.
    served = {step["name"]: step for step in out["data"]["done"]["steps"]}
    for name in ("tailor", "reassess"):
        assert steps_by_name[name]["model"] and steps_by_name[name]["seconds"] is not None, name
        assert served[name]["last_run"]["outcome"] == "ok"
        assert steps_by_name[name]["model"].startswith(served[name]["model_target"].split("_")[0]), "the model target's short name, then the model id"
    assert steps_by_name["ats"]["model"] is None and steps_by_name["label"]["model"] is None
    assert done["live"] is False
    # A job waiting for an approval: its first step says so, the rest wait behind it; nothing is made up.
    assert out["waiting"]["stages"] == [[["tailor", "awaiting_approval"]], [["reassess", "blocked"], ["ats", "blocked"]], [["label", "blocked"]]]
    assert (out["waiting"]["variant"], out["waiting"]["ats"], out["waiting"]["label"]) == (None, None, None)
    # A job that never entered the pipeline still shows the four steps, not started.
    assert out["never"]["flat"] == [[["tailor", "not_started"]], [["reassess", "not_started"], ["ats", "not_started"]], [["label", "not_started"]]]
    assert {step["stateLabel"] for stage in out["never"]["stages"] for step in stage} == {"Not started"} and out["never"]["live"] is False
    tailor, reassess = out["failed"][0][0], out["failed"][1][0]
    assert (tailor["state"], tailor["tone"], tailor["error"], tailor["attempts"], tailor["seconds"], tailor["tokens"]) == ("failed", "danger", "assess_timeout", 3, "2 min", None)
    assert (reassess["stateLabel"], reassess["waiting"], reassess["error"]) == ("Queued", "the daily cap of model calls is reached", None)
    assert out["failed"][1][1]["state"] == "not_started"
    assert out["liveStates"] == [True, True, False, False, False, False]


def test_the_variant_reads_n_to_m_after_tailoring_from_the_backends_numbers(out: dict) -> None:
    served = out["data"]["done"]["requirements_met"]
    base, tailored = served["base"], served["tailored"]
    assert out["done"]["variant"] == {
        "text": f"{base['percent']} → {tailored['percent']} after tailoring",
        "detail": f"Requirements met: {base['met']} of {base['total']} → {tailored['met']} of {tailored['total']}",
        "improved": tailored["percent"] > base["percent"],
    }
    assert out["variants"] == [
        {"text": "73 → 91 after tailoring", "detail": "Requirements met: 8 of 11 → 10 of 11", "improved": True},
        None,
        {"text": "82 · no change after tailoring", "detail": "Requirements met: 9 of 11 → 9 of 11", "improved": False},
    ]


def test_the_ats_chip_and_the_label_chip_carry_only_the_servers_wording(out: dict) -> None:
    served = out["data"]["done"]
    ats, label = out["done"]["ats"], out["done"]["label"]
    assert ats["label"] == f"Scout ATS {served['ats']['score']}" and ats["line"] == served["ats"]["line"]
    assert ats["wording"] == ats_score.ATS_WORDING and label["wording"] == steps.LABEL_WORDING
    assert label["label"] == f"{steps.LABEL_NAME}: {served['label']['label'].replace('_', ' ')}" and label["minAts"] == 10
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
    assert actions["done"] == {"enabled": True, "label": "Process again", "reason": "", "body": {**ids, "force": True}}
    assert (actions["notAssessed"]["enabled"], actions["notAssessed"]["reason"]) == (False, "Assess this posting first.")
    assert (actions["running"]["enabled"], actions["running"]["label"]) == (False, "Processing…")
    assert actions["off"]["enabled"] is False and "off" in actions["off"]["reason"]
    assert actions["noProfile"]["enabled"] is False
    assert out["results"] == ["enqueued", "noop unchanged; this server runs no pipeline (run: gigai scout pipeline run --once)", None]


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
    assert (first["step"], first["code"], first["attempt"], first["job"], first["profileId"]) == ("Tailor resume", "assess_timeout", 2, "https://x.test/1", "p1")
    assert (second["step"], second["code"]) == ("Scout ATS", "unknown") and out["noErrors"] == []


def test_the_settings_form_sends_only_what_changed(out: dict) -> None:
    assert out["draft"] == {
        "enabled": True, "jobsPerTrigger": "1", "callsPerDay": "40", "labelMinAts": "10", "rankCallsPerDay": "100", "rankWarnAt": "60",
        "tailorModel": "", "reassessModel": "",
    }
    assert out["shown"] == [True, False, False]
    assert out["patches"] == {
        "none": None,
        "off": {"pipeline": {"enabled": False}},
        "caps": {"pipeline": {"auto_jobs_per_trigger": 5, "max_model_calls_per_day": 20, "label_min_ats": 70}},
        "rank": {"rank": {"max_calls_per_day": 80, "warn_calls_per_day": 40}},
        "models": {"pipeline": {"models": {"tailor": "claude_cli", "reassess": "codex_cli"}}},
        "modelBack": {"pipeline": {"models": {"tailor": None}}},
    }
    ok, too_many, over_100, negative, empty, warn_over = out["errorsForm"]
    assert ok == "" and "0 to 1000" in too_many and "0 to 100." in over_100 and negative and empty
    assert warn_over == "Rank warning level: not above the rank calls a day."
    assert out["modelOptions"] == ["", "ollama_local", "codex_cli", "claude_cli", "openrouter_api"]


def test_the_server_takes_the_patches_the_form_makes(out: dict) -> None:
    """Every patch the model builds is one the settings route accepts (the server's own validation)."""

    for name in ("off", "caps", "rank", "models", "modelBack"):
        checked = background_settings.validate_patch(out["patches"][name])
        assert set(checked) == set(out["patches"][name]), name
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
    for needle in ("/api/pipeline/job?", "/api/pipeline/process", "/api/pipeline/approvals/", "step-timeline", "ats-chip", "scout-label-chip", "background-panel", "approvals-list", "after tailoring"):
        assert needle in bundle, needle
