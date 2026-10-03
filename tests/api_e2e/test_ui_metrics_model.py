"""0.1.10.7 E: the words the UI makes from ``GET /api/metrics``, run under node.

``ui/src/metricsModel.js`` is plain JavaScript, so this runs it under the
system ``node`` and asserts on the JSON the script prints. LOUD skip when
``node`` is not on PATH. What lives in JSX is pinned by reading the source.

Pinned: the average label beside Re-assess / Assess all ("avg 19.5k tokens,
11 s per job on codex") for the model target in use, and nothing at all with
no history for it; the Settings table's rows; the report the model reads is
the one the server makes (``call_metrics.metrics_report``); the label sits
beside both actions and the table in Settings, and the built bundle has them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import call_metrics
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.pipeline.store import PipelineStore, StepMetrics, pipeline_path

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
UI_SRC = UI / "src"

SCRIPT = """
import * as m from MODEL_URL;

const report = REPORT;
const out = {};
out.labels = {
  codex: m.averageLabel(report, "assess", "codex_cli"),
  claude: m.averageLabel(report, "assess", "claude_cli"),
  tailor: m.averageLabel(report, "tailor", "codex_cli"),
  rank: m.averageLabel(report, "rank", "codex_cli"),
  // no history: another target, another kind, only failed calls, no report at all
  ollama: m.averageLabel(report, "assess", "ollama_local"),
  tag: m.averageLabel(report, "tag", "codex_cli"),
  onlyFailures: m.averageLabel(report, "extract", "codex_cli"),
  noTarget: m.averageLabel(report, "assess", undefined),
  empty: m.averageLabel({ comparison: [] }, "assess", "codex_cli"),
  missing: [m.averageLabel(null, "assess", "codex_cli"), m.averageLabel({}, "assess", "codex_cli")],
  // a call that reported no tokens still has its time
  timeOnly: m.averageLabel({ comparison: [{ kind: "assess", model_target: "codex_cli", avg_tokens: null, avg_seconds: 42.4 }] }, "assess", "codex_cli"),
};
out.rows = m.comparisonRows(report);
out.noRows = [m.comparisonRows(null), m.comparisonRows({ comparison: [] })];
out.tokens = [19500, 20000, 850, 1049, 123456, null, undefined, NaN].map(m.tokensText);
out.seconds = [11.2, 2.44, 3, 0.04, 119.6, 150, 3600, null].map(m.secondsText);
out.cost = [0.15, 0.0421, 0.002, 0, null].map(m.costText);
out.names = ["codex_cli", "claude_cli", "ollama_local", "openrouter_api", "other_api", null].map(m.targetName);
console.log(JSON.stringify(out));
"""


def _report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The server's own report over a synthetic history (numbers only)."""

    monkeypatch.setattr("gigai.scout.find_jobs.discovery.storage.project_id", lambda home, target: "proj_ui")
    home, target = tmp_path / "home", tmp_path / "target"
    store = PipelineStore(pipeline_path(home, target))
    codex = StepMetrics(adapter="codex_cli", model="gpt-5.1-codex", input_tokens=18000, output_tokens=1500, cached_tokens=9000, cost_status="unavailable")
    for seconds in (10.0, 12.0, 11.0, 11.0):
        store.record_call(kind="assess", lane="codex_cli", seconds=seconds, metrics=codex)
    store.record_call(kind="assess", lane="codex_cli", seconds=120.0, outcome="error", error_code="model_timeout", metrics=StepMetrics(adapter="codex_cli"))
    for model, tokens, seconds, cost in (("claude-sonnet-5-5", 20000, 20.0, 0.05), ("claude-opus-5-5", 40000, 30.0, 0.25)):
        store.record_call(kind="assess", lane="claude_cli", seconds=seconds, metrics=StepMetrics(
            adapter="claude_cli", model=model, input_tokens=tokens, output_tokens=tokens // 20, cost_usd=cost, cost_status="provider_reported"))
    store.record_call(kind="tailor", lane="codex_cli", seconds=151.0, metrics=StepMetrics(adapter="codex_cli", model="gpt-5.1-codex", input_tokens=30000, output_tokens=4000))
    store.record_call(kind="rank", lane="codex_cli", seconds=8.04, items=50, metrics=StepMetrics(adapter="codex_cli", model="gpt-5.1-codex", input_tokens=600, output_tokens=250))
    store.record_call(kind="extract", lane="codex_cli", seconds=3.0, outcome="error", error_code="model_call_failed", metrics=StepMetrics(adapter="codex_cli"))
    store.close()
    return call_metrics.metrics_report(home, target)


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the metrics model was not run")
    script = SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "metricsModel.js").resolve().as_uri()))
    script = script.replace("REPORT", json.dumps(_report(tmp_path, monkeypatch)))
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_average_label_names_tokens_time_and_the_model_in_use(out: dict) -> None:
    labels = out["labels"]
    assert labels["codex"] == "avg 19.5k tokens, 11 s per job on codex"
    assert labels["claude"] == "avg 31.5k tokens, 25 s per job on claude"  # both Claude model ids together
    assert labels["tailor"] == "avg 34k tokens, 2.5 min per resume on codex"
    assert labels["rank"] == "avg 850 tokens, 8 s per call on codex"
    assert labels["timeOnly"] == "avg 42 s per job on codex"


def test_no_history_shows_nothing(out: dict) -> None:
    labels = out["labels"]
    assert [labels[name] for name in ("ollama", "tag", "onlyFailures", "noTarget", "empty")] == [None] * 5
    assert labels["missing"] == [None, None] and out["noRows"] == [[], []]


def test_the_settings_table_has_one_row_per_kind_and_model(out: dict) -> None:
    assert out["rows"] == [
        {"key": "assess:claude_cli", "kind": "Assess", "model": "claude", "models": "claude-opus-5-5, claude-sonnet-5-5", "calls": 2, "tokens": "31.5k", "seconds": "25 s", "cost": "$0.15", "failed": "0%"},
        {"key": "assess:codex_cli", "kind": "Assess", "model": "codex", "models": "gpt-5.1-codex", "calls": 5, "tokens": "19.5k", "seconds": "11 s", "cost": "-", "failed": "20%"},
        {"key": "extract:codex_cli", "kind": "Read resume", "model": "codex", "models": "", "calls": 1, "tokens": "-", "seconds": "-", "cost": "-", "failed": "100%"},
        {"key": "rank:codex_cli", "kind": "Rank", "model": "codex", "models": "gpt-5.1-codex", "calls": 1, "tokens": "850", "seconds": "8 s", "cost": "-", "failed": "0%"},
        {"key": "tailor:codex_cli", "kind": "Tailor resume", "model": "codex", "models": "gpt-5.1-codex", "calls": 1, "tokens": "34k", "seconds": "2.5 min", "cost": "-", "failed": "0%"},
    ]


def test_the_number_phrases(out: dict) -> None:
    assert out["tokens"] == ["19.5k", "20k", "850", "1k", "123.5k", None, None, None]
    assert out["seconds"] == ["11 s", "2.4 s", "3 s", "0 s", "120 s", "2.5 min", "60 min", None]
    assert out["cost"] == ["$0.15", "$0.04", "$0.0020", "$0.0000", None]
    assert out["names"] == ["codex", "claude", "ollama", "openrouter", "other_api", "unknown"]


def test_the_label_sits_beside_both_actions_and_the_table_in_settings() -> None:
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert 'request("GET", text ? `/api/metrics?${text}` : "/api/metrics")' in api
    average = (UI_SRC / "components" / "ModelAverage.jsx").read_text(encoding="utf-8")
    assert "averageLabel(report, kind, used)" in average and "return label ? (" in average and ") : null;" in average
    # Re-assess: the job page's action row, only where the button re-assesses (not "Save answers").
    actions = (UI_SRC / "components" / "RequirementActions.jsx").read_text(encoding="utf-8")
    reassess = actions.index('<Action action={reassess} name="reassess" primary busy={busy} />')
    assert actions.index('{reassess.average && <ModelAverage kind="assess" />}') > reassess
    assert "average: Boolean(jobIdentity)," in (UI_SRC / "components" / "AssessmentBody.jsx").read_text(encoding="utf-8")
    # Assess all: beside the button, for the model target the plan names.
    jobs = (UI_SRC / "views" / "FindJobsView.jsx").read_text(encoding="utf-8")
    button = jobs.index("{assessAllButtonLabel(assessAllPlan)}")
    label = jobs.index('<ModelAverage kind="assess" target={assessAllPlan.model_target} />')
    assert 0 < label - button < 400
    # Settings: the comparison table, after Background updates; nothing until a call is recorded.
    settings = (UI_SRC / "views" / "SettingsView.jsx").read_text(encoding="utf-8")
    assert settings.index("<BackgroundUpdatesPanel />") < settings.index("<ModelMetricsPanel />") < settings.index('id="settings-preferences"')
    panel = (UI_SRC / "components" / "ModelMetricsPanel.jsx").read_text(encoding="utf-8")
    assert "comparisonRows(report)" in panel and "if (rows.length === 0) {\n    return null;" in panel


def test_the_built_bundle_has_the_label_and_the_table() -> None:
    bundle = "".join(path.read_text(encoding="utf-8") for path in (UI / "dist" / "assets").glob("index-*.js"))
    assert "/api/metrics" in bundle and "model-average" in bundle and "settings-model-metrics" in bundle
    assert " tokens" in bundle and "Avg tokens" in bundle
