"""ui-pass (uat-bug-021): what the pages do and say about Jev, run under node.

``ui/src/jevModel.js`` is pure JavaScript (no React), so this test runs it
under the system ``node`` the way ``test_ui_rank_status_model.py`` does (no
JS test runner) and asserts on the JSON the script prints. LOUD skip when
``node`` is not on PATH. What lives in JSX is checked statically, by
reading the source.

What is pinned:

* a page never asks Jev on its own: ``FindJobsView`` calls ``POST /rank``
  only through ``jevModel.createRankPass``, whose ``start()`` (the "Score
  with Jev" click) is the one call that sends ``{"start": true}``; its
  reads send ``{}``; it stops reading when the pass ends, when the page
  stops it, or when another run is shown (fail-before: the view POSTed
  ``/rank`` after every run's results loaded);
* the button is offered only with a key, ranking on, room in today's
  budget, no pass running and something unscored;
* the cards' "– Jev" tooltip says why, per reason, and never guesses;
* "Your resume is sent to Jev to rank postings." only with a key and
  ranking on (fail-before: it was shown with no key, wizard finding 2);
* the disclosure's numbers are the ones the Python enforces;
* the run page and the latest-run panel pass the run's ``rank_status`` to
  ``NodeStatusList``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs import jev_budget, jev_rank
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.jev_client import JevClient
from gigai.scout.find_jobs.jev_rank import RankStatus

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
JEV_MODEL_JS = UI_SRC / "jevModel.js"
RUN_TEXT_JS = UI_SRC / "runText.js"

NODE_SCRIPT = """
import * as jev from {jev_model_url};
import * as runText from {run_text_url};
const input = JSON.parse(process.argv[process.argv.length - 1]);

// A "Score with Jev" pass against a scripted POST /rank.
async function simulate(answers, {{ stopAfter = null, current = true }} = {{}}) {{
  const calls = [];
  const seen = [];
  const timers = [];
  let cancelled = 0;
  const pending = [];
  const pass = jev.createRankPass({{
    runId: "run_1",
    postRank: (runId, fields) => {{
      calls.push({{ runId, fields }});
      const answer = answers[Math.min(calls.length - 1, answers.length - 1)];
      return new Promise((resolve) => pending.push(() => resolve(answer)));
    }},
    onResponse: (response) => seen.push(response.rank_status.status),
    isCurrent: () => current,
    schedule: (callback, ms) => {{ timers.push({{ callback, ms }}); return timers.length; }},
    cancel: () => {{ cancelled += 1; }},
    intervalMs: 2000,
  }});
  let step = pass.start();
  for (let turn = 0; turn < 10; turn += 1) {{
    if (stopAfter !== null && calls.length > stopAfter) {{
      pass.stop();
    }}
    const release = pending.shift();
    if (!release) {{
      break;
    }}
    release();
    await step;
    const timer = timers[turn];
    if (!timer) {{
      break;
    }}
    step = timer.callback();
  }}
  return {{ calls: calls.map((call) => call.fields), seen, timers: timers.map((timer) => timer.ms), cancelled }};
}}

const out = {{
  notice: input.settings.map((settings) => jev.jevNoticeText(settings)),
  runConsent: input.settings.map((settings) => jev.jevRunConsentLine(settings)),
  canScore: input.canScore.map((args) => jev.canScoreWithJev(args)),
  cardWords: input.statuses.map((status) => jev.jevCardSkipWords(status, null, null)),
  cardWordsRun: input.statuses.map((status) => jev.jevCardSkipWords(null, status, null)),
  cardWordsBoth: jev.jevCardSkipWords(input.statuses[0], input.statuses[1], null),
  cardWordsSettings: input.settings.map((settings) => jev.jevCardSkipWords(null, null, settings)),
  cardWordsNone: jev.jevCardSkipWords(null, null, null),
  lines: input.statuses.map((status) => runText.rankStatusLine(status)),
  merged: jev.mergeRankScores(input.stored, input.fresh),
  disclosureDefault: jev.jevDisclosureLines("0.50"),
  disclosureChanged: jev.jevDisclosureLines("0.10"),
  disclosureUnknown: jev.jevDisclosureLines(null),
  constants: {{
    cap: jev.JEV_RUN_COST_CAP_USD, budget: jev.JEV_DEFAULT_DAILY_BUDGET_USD,
    chars: jev.JEV_RESUME_CHARS, file: jev.JEV_SETTINGS_FILE,
  }},
  pass: await simulate(input.passAnswers),
  passStopped: await simulate(input.passAnswers, {{ stopAfter: 1 }}),
  passNotCurrent: await simulate(input.passAnswers, {{ current: false }}),
}};
process.stdout.write(JSON.stringify(out));
"""

REASONS = [
    "no_key", "no_resume", "no_profile", "no_run_input", "no_run_output", "no_candidates", "not_requested",
    "disabled", "cost_cap_reached", "daily_budget_reached", "jev_error:jev_http_502", "error:WorkpadConflictError",
]
STATUSES = [RankStatus.skipped(reason, total=10, cost_cap_usd=0.25) for reason in REASONS]
SCORED_ALL = RankStatus("scored", 10, 10, None, 0.25, 0.005, None, 0.005, 0.5)

USAGE_ROOM = {"spent_today_usd": "0.100000", "daily_budget_usd": "0.50", "budget_reached": False}
USAGE_SPENT = {"spent_today_usd": "0.500000", "daily_budget_usd": "0.50", "budget_reached": True}
SETTINGS = [
    {"has_key": True, "jev_rank_enabled": True, "jev_daily_budget_usd": 0.5, "usage": USAGE_ROOM},
    {"has_key": False, "jev_rank_enabled": True, "jev_daily_budget_usd": 0.5, "usage": USAGE_ROOM},
    {"has_key": True, "jev_rank_enabled": False, "jev_daily_budget_usd": 0.5, "usage": USAGE_ROOM},
    {"has_key": False, "jev_rank_enabled": False, "jev_daily_budget_usd": 0.5, "usage": USAGE_ROOM},
    None,
]
ON = SETTINGS[0]
RUNNING = {"status": "running", "scored": 3, "total": 10}
CAN_SCORE = [
    {"settings": ON, "usage": None, "rankStatus": None, "unscored": 5},  # yes
    {"settings": SETTINGS[1], "usage": None, "rankStatus": None, "unscored": 5},  # no key
    {"settings": SETTINGS[2], "usage": None, "rankStatus": None, "unscored": 5},  # ranking off
    {"settings": None, "usage": None, "rankStatus": None, "unscored": 5},  # not read yet
    {"settings": {**ON, "usage": USAGE_SPENT}, "usage": None, "rankStatus": None, "unscored": 5},  # budget spent (settings)
    {"settings": ON, "usage": USAGE_SPENT, "rankStatus": None, "unscored": 5},  # budget spent (a newer /rank answer)
    {"settings": {**ON, "usage": USAGE_SPENT}, "usage": USAGE_ROOM, "rankStatus": None, "unscored": 5},  # the newer answer wins
    {"settings": ON, "usage": None, "rankStatus": RUNNING, "unscored": 5},  # a pass is running
    {"settings": ON, "usage": None, "rankStatus": None, "unscored": 0},  # nothing to score
    {"settings": ON, "usage": None, "rankStatus": SCORED_ALL.to_json(), "unscored": 2},  # a pass ended, some left
]


def _score(url: str, score: int | None) -> dict:
    fit = None if score is None else "strong" if score >= 70 else "maybe"
    return {"normalized_url": url, "content_sha256": None, "fit": fit, "score": score, "reasons": [], "mismatch_flags": [],
            "hidden_by_default": False, "cost_usd": "0", "cached": True}


STORED = [_score("a", 80), _score("b", 55)]
FRESH = [_score("a", None), _score("b", 60), _score("c", 90), _score("d", None)]
PASS_ANSWERS = [
    {"rank_status": {"status": "running", "scored": 0, "total": 4}, "scores": []},
    {"rank_status": {"status": "running", "scored": 2, "total": 4}, "scores": []},
    {"rank_status": {"status": "scored", "scored": 4, "total": 4}, "scores": []},
]


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the Jev page model was not run")
    script = NODE_SCRIPT.format(jev_model_url=json.dumps(JEV_MODEL_JS.as_uri()), run_text_url=json.dumps(RUN_TEXT_JS.as_uri()))
    fixture = {
        "settings": SETTINGS,
        "canScore": CAN_SCORE,
        "statuses": [status.to_json() for status in STATUSES],
        "stored": STORED,
        "fresh": FRESH,
        "passAnswers": PASS_ANSWERS,
    }
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, "--", json.dumps(fixture)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def _source(relative: str) -> str:
    return (UI_SRC / relative).read_text(encoding="utf-8")


# --- a page never asks Jev on its own ------------------------------------------------------


def test_opening_a_run_posts_no_rank_request() -> None:
    view = _source("views/FindJobsView.jsx")
    # Fail-before: `postRank(id, {})` ran after every run's results loaded.
    assert "postRank(" not in view, "FindJobsView calls POST /rank itself"
    assert "start: true" not in view and "start:true" not in view
    # The one reference hands the function to the click's pass.
    assert view.count("postRank") == 2 and "createRankPass({" in view and "      postRank,\n" in view
    # The cards' scores are the stored ones, read with the results pages.
    assert "setRankScores(storedRankScores(response));" in view
    assert "onClick={scoreWithJev}" in view
    model = _source("jevModel.js")
    assert model.count("{ start: true }") == 1 and "return postRank(runId, { start: true })" in model


def test_the_click_starts_one_pass_and_the_page_reads_until_it_ends(out: dict) -> None:
    assert out["pass"]["calls"] == [{"start": True}, {}, {}]
    assert out["pass"]["seen"] == ["running", "running", "scored"]
    assert out["pass"]["timers"] == [2000, 2000], "a read is scheduled only while the pass runs"


def test_a_stopped_pass_or_another_run_shown_drops_the_answers_and_stops_reading(out: dict) -> None:
    assert out["passStopped"]["calls"] == [{"start": True}, {}]
    assert out["passStopped"]["seen"] == ["running"]
    assert out["passNotCurrent"]["calls"] == [{"start": True}]
    assert out["passNotCurrent"]["seen"] == [] and out["passNotCurrent"]["timers"] == []


def test_score_with_jev_is_offered_only_when_a_click_can_score(out: dict) -> None:
    assert out["canScore"] == [True, False, False, False, False, False, True, False, False, True]


def test_scores_fill_in_without_losing_a_stored_one(out: dict) -> None:
    merged = {item["normalized_url"]: item["score"] for item in out["merged"]}
    assert merged == {"a": 80, "b": 60, "c": 90, "d": None}


# --- the cards say why ------------------------------------------------------------------------


def test_the_cards_tooltip_says_why_per_reason(out: dict) -> None:
    expected = [
        "no Jev key", "no resume", "no profile selected", "the run's input could not be read",
        "the run's postings could not be read", "no new postings to score", "not asked yet",
        "Rank with Jev is off", "cost cap $0.25 reached before any score", "daily budget reached",
        "Jev error: jev_http_502", "error: WorkpadConflictError",
    ]
    assert out["cardWords"] == expected
    assert out["cardWordsRun"] == expected
    # The page's own pass is newer than the run's.
    assert out["cardWordsBoth"] == "no Jev key"
    # With nothing recorded, the settings say why: on (no guess), no key, off, off, unknown.
    assert out["cardWordsSettings"] == ["", "no Jev key", "Rank with Jev is off", "Rank with Jev is off", ""]
    assert out["cardWordsNone"] == ""


def test_the_badge_uses_the_run_words_and_never_guesses() -> None:
    badge = _source("components/JevBadge.jsx")
    assert "const why = jevSkipText(skipReason) || runSkipWords;" in badge
    assert '"Not scored by Jev"' in badge and "past the cost cap or no key" not in badge
    assert "runSkipWords={jevSkipWords}" in _source("components/JobCard.jsx")
    assert "jevSkipWords={jevSkipWords}" in _source("components/JobsGrid.jsx")
    view = _source("views/FindJobsView.jsx")
    assert "jevCardSkipWords(rankStatus, progress?.rank_status, jevSettings)" in view
    assert view.count("jevSkipWords={jevSkipWords}") == 2


@pytest.mark.xfail(
    "disabled" not in getattr(jev_rank, "_SKIP_WORDS", {}),
    strict=True,
    reason="needs jev_rank._SKIP_WORDS disabled entry from jev-cache-prefs",
)
def test_ranking_off_reads_the_same_on_the_page_and_in_the_server_log(out: dict) -> None:
    assert out["lines"] == [status.line for status in STATUSES]
    assert out["lines"][REASONS.index("disabled")] == "Jev: skipped (Rank with Jev is off)"


# --- what the pages disclose --------------------------------------------------------------------


def test_the_jev_notice_shows_only_with_a_key_and_ranking_on(out: dict) -> None:
    assert out["notice"] == ["Your resume is sent to Jev to rank postings.", None, None, None, None]
    view = _source("views/FindJobsView.jsx")
    # Fail-before: the sentence was a literal, shown whatever the key.
    assert "Your resume is sent to Jev to rank postings." not in view
    assert "{jevNotice && (" in view and 'data-role="jev-notice"' in view


def test_the_run_dialog_names_jev_only_when_the_run_will_use_it(out: dict) -> None:
    assert out["runConsent"][1:] == [None, None, None, None]
    assert out["runConsent"][0] == (
        "Rank with Jev is on: the first 2,000 characters of your resume go to Jev to score the postings, "
        "up to $0.25 for this run (Settings → Jev ranking)."
    )
    assert "jevLine={jevRunConsentLine(jevSettings)}" in _source("views/FindJobsView.jsx")
    assert "{jevLine && <div data-role=\"jev-run-consent\">{jevLine}</div>}" in _source("components/RunConfirmDialog.jsx")


def test_the_disclosure_numbers_are_the_ones_the_code_enforces(out: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.behaviors.scout_find_jobs.test_rank_status import _Jev, _posting

    monkeypatch.delenv(jev_budget.DAILY_BUDGET_ENV, raising=False)
    if hasattr(jev_rank, "project_id"):  # the per-project cache fallback: no project here
        monkeypatch.setattr(jev_rank, "project_id", lambda home_root, target: "proj-test")
    # What Jev is sent: ask it once with a long resume and read the request.
    fake = _Jev()
    home = tmp_path / "home"
    home.mkdir()
    jev_rank.rank_postings_report(
        (_posting(1),), client=JevClient("k", fake.client()), resume_text="x" * 5000,
        prefs=jev_rank.RankPreferences(target_titles=("software engineer",)),
        profile_id="p1", resume_revision_id="r1", home_root=home, target=tmp_path, cost_cap_usd=1.0,
    )
    sent = json.loads(fake.bodies[0])["state"]
    assert len(sent["resume"]) == out["constants"]["chars"] == 2000
    assert set(sent["posting"]) == {"company", "title", "location"}
    assert set(sent["preferences"]) == {"target_titles", "countries", "visa_sponsorship_required"}

    assert out["constants"]["cap"] == jev_budget.format_usd(jev_rank.DEFAULT_COST_CAP_USD) == "0.25"
    assert out["constants"]["budget"] == jev_budget.format_usd(jev_budget.DEFAULT_DAILY_BUDGET_USD) == "0.50"
    relative = jev_budget.settings_path(Path("/H")).relative_to(Path("/H"))
    assert out["constants"]["file"] == f"<home>/{relative.as_posix()}"

    text = " ".join(out["disclosureDefault"])
    assert "up to $0.25 a run" in text and "$0.50 a day by default" in text
    assert "the first 2,000 characters of the selected profile's resume" in text
    assert "A posting Jev already scored is cached and costs nothing again." in text
    assert "A pasted resume is never sent to Jev." in text
    assert "<home>/local/scout/jev-settings.json" in text
    assert "$0.10 a day." in " ".join(out["disclosureChanged"]) and "by default" not in out["disclosureChanged"][1]
    assert out["disclosureUnknown"] == out["disclosureDefault"]

    readme = (Path(static_module.__file__).resolve().parents[5] / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Privacy and security", 1)[1].split("\n## ", 1)[0]
    for fact in ("**$0.25 per run**", "**daily budget of $0.50**", "**2,000 characters of the selected\nprofile's resume**",
                 "`<home>/local/scout/jev-settings.json`", "`GIGAI_JEV_DAILY_BUDGET_USD`", "cached and costs nothing"):
        assert fact in section, fact


def test_settings_shows_the_toggle_the_budget_and_the_disclosure() -> None:
    assert "<JevSettingsPanel />" in _source("views/SettingsView.jsx")
    panel = _source("components/JevSettingsPanel.jsx")
    assert "jevDisclosureLines(inForce)" in panel
    # Each control saves its own setting only; the other keeps its stored value.
    assert "save({ jev_rank_enabled: event.target.checked }" in panel
    assert "save({ jev_daily_budget_usd: budgetValue }" in panel
    assert 'type="number"' in panel and 'min="0"' in panel
    assert "rankUsageLine(settings.usage)" in panel and 'data-role="jev-usage"' in panel
    api = _source("api.js")
    assert 'request("GET", "/api/jev/settings")' in api and 'request("PUT", "/api/jev/settings", fields)' in api


def test_the_run_page_and_the_latest_run_panel_show_the_rank_line() -> None:
    view = _source("views/FindJobsView.jsx")
    assert view.count("<NodeStatusList") == 2
    assert view.count("rankStatus={progress?.rank_status}") == 2
