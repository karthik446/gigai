"""0.1.11.2 THIN: the page's words for a thin posting, the models run under node over what the SERVER makes.

``ui/src/postingsModel.js``, ``display.js``, ``jobResumeModel.js`` and
``jobStateModel.js`` are plain JavaScript: this runs them under the system
``node`` over ``search_postings`` on the synthetic scene of
``test_thin_posting`` (a 2-of-2, two 4-of-4, a lone "No stated requirements"
row, a stored match with an empty matrix). LOUD skip without ``node``. What
lives in JSX is pinned by reading the source.

Pinned: a row whose ``thin_posting`` is true has a "Thin posting" chip (warn,
never the green "Matched") and its score column is the server's thin label;
the verdict label, the job page's header chip and its ONE requirements line
say thin for a match of fewer than 4 matrix rows and "Matched" for 4.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import posting_search
from gigai.scout.find_jobs.api import static as static_module

from tests.behaviors.scout_pipeline.test_thin_posting import THIN, _scene
from tests.support.posting_fixtures import NOW, build_postings_fixture

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
LABEL = "Thin posting: too few requirements to judge"

SCRIPT = """
import * as m from POSTINGS_URL;
import * as d from DISPLAY_URL;
import * as r from RESUME_URL;
import * as s from STATE_URL;

const data = DATA;
const out = { rows: {} };
for (const [name, row] of Object.entries(data.rows)) {
  out.rows[name] = { chips: m.rowChips(row), score: m.scoreText(row) };
}
// A thin match with a minor gap never says "1 minor gap" as a match does.
out.gap = [
  m.scoreText({ state: "matched", thin_posting: true, score_text: "thin · 2 of 3 requirements", minor_gap_text: "1 minor gap: Helm" }),
  m.scoreText({ state: "matched", thin_posting: false, score_text: "Matched · 4 of 5 requirements", minor_gap_text: "1 minor gap: Helm" }),
];
const matrix = (rows) => ({ matrix: Array.from({ length: rows }, (_, n) => ({ requirement: `Topic ${n}`, status: "met" })) });
const matched = "matched_above_threshold";
out.thin = [0, 1, 3, 4, 9].map((rows) => d.isThinMatch(matched, matrix(rows)));
out.otherVerdicts = ["pending_user_answers", "not_a_match", null].map((verdict) => d.isThinMatch(verdict, matrix(2)));
out.noAssessment = [d.isThinMatch(matched, null), d.verdictLabel(matched), d.verdictLabel(matched, null)];
out.labels = [d.verdictLabel(matched, matrix(2)), d.verdictLabel(matched, matrix(4)), d.verdictLabel("not_a_match", matrix(2))];
out.lines = [d.thinPostingLine(matched, matrix(2)), d.thinPostingLine(matched, matrix(0)), d.thinPostingLine(matched, matrix(4))];
const only = "Only 2 requirements were read from this posting. Open the posting to check.";
const capped = "Only the first 12,000 characters of this posting were assessed.";
out.stored = [
  d.thinPostingLine(matched, matrix(2), only), d.thinPostingLine(matched, matrix(2), `${only} ${capped}`),
  d.thinPostingLine(matched, matrix(1), "Only 1 requirement was read from this posting. Open the posting to check."),
  d.thinPostingLine(matched, matrix(4), only),
];
// The server never says thin for a posting nothing assessed; the chip needs a match.
out.notAssessed = m.rowChips({ state: "not_assessed", thin_posting: true }).map((chip) => chip.label);
out.header = [
  r.headerChip("matched", { assessment: matrix(2) }),
  r.headerChip("matched", { assessment: matrix(4) }),
  r.headerChip("thin_posting", { assessment: matrix(1) }),
  r.headerChip("needs_answers", { assessment: matrix(2) }),
];
out.stateLabel = s.stateLabel("thin_posting");
out.stateOrder = s.STATE_ORDER.includes("thin_posting");
console.log(JSON.stringify(out));
"""


@pytest.fixture
def out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the thin-posting model was NOT run")
    assert node is not None
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = _scene(fx, monkeypatch)
    names = {url: name for name, url in jobs.items()}
    calls = fx.base.model.calls
    found = posting_search.search_postings(fx.home_root, fx.target, now=NOW)
    assert fx.base.model.calls == calls, "reading called a model"
    data = {"rows": {names[str(row["job_identity"])]: row for row in found["postings"]["rows"]}}  # type: ignore[index]
    script = SCRIPT.replace("DATA", json.dumps(data))
    for name, module in (("POSTINGS_URL", "postingsModel.js"), ("DISPLAY_URL", "display.js"), ("RESUME_URL", "jobResumeModel.js"), ("STATE_URL", "jobStateModel.js")):
        script = script.replace(name, json.dumps((UI_SRC / module).resolve().as_uri()))
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def _state_chip(row: dict) -> dict:
    return next(chip for chip in row["chips"] if chip["kind"] == "state")


def test_a_thin_row_has_its_own_chip_and_the_servers_words_never_a_green_matched(out: dict) -> None:
    rows = out["rows"]
    for name in ("two", "lone", "empty"):
        chip = _state_chip(rows[name])
        assert (chip["label"], chip["tone"], chip["testId"]) == ("Thin posting", "warn", "thin-posting-chip"), (name, chip)
        assert all(chip["label"] != "Matched" for chip in rows[name]["chips"])
        assert rows[name]["score"].startswith(THIN) and "Matched" not in rows[name]["score"] and "fit 100%" not in rows[name]["score"]
    assert rows["two"]["score"] == f"{THIN} · 2 of 2 requirements · rank 90"
    # A 4-row match is unchanged, and so is a posting that waits on answers.
    for name in ("four", "unranked_four"):
        chip = _state_chip(rows[name])
        assert (chip["label"], chip["tone"]) == ("Matched", "ok") and "testId" not in chip
        assert rows[name]["score"].startswith("Matched · fit 100% · 4 of 4 requirements")
    assert _state_chip(rows["needs"])["tone"] == "warn" and _state_chip(rows["needs"])["label"].startswith("Needs your answers")
    assert out["gap"] == ["thin · 2 of 3 requirements", "Matched · 4 of 5 requirements · 1 minor gap: Helm"]


def test_the_verdict_label_the_header_chip_and_the_one_requirements_line_say_thin_below_four_rows(out: dict) -> None:
    assert out["thin"] == [True, True, True, False, False]
    assert out["otherVerdicts"] == [False, False, False], "only a match is relabelled"
    assert out["noAssessment"] == [False, "Matched", "Matched"], "a verdict with no assessment in hand keeps its word"
    assert out["labels"] == [LABEL, "Matched", "Not a match"]
    assert out["lines"] == [f"{LABEL} (2 read). Open the posting to check.", f"{LABEL} (0 read). Open the posting to check.", None]
    # The stored "Only N requirements were read" sentence is replaced, never repeated; another note follows on the line.
    assert out["stored"] == [
        f"{LABEL} (2 read). Open the posting to check.",
        f"{LABEL} (2 read). Open the posting to check. Only the first 12,000 characters of this posting were assessed.",
        f"{LABEL} (1 read). Open the posting to check.", None,
    ]
    assert out["notAssessed"] == ["Not assessed"]
    thin, matched, state, needs = out["header"]
    assert (thin["state"], thin["label"], thin["tone"]) == ("thin_posting", LABEL, "warn")
    assert (matched["state"], matched["label"], matched["tone"]) == ("matched", "Matched", "ok")
    assert (state["state"], state["label"]) == ("thin_posting", LABEL)
    assert needs["state"] == "needs_answers"
    assert out["stateLabel"] == "Thin posting" and out["stateOrder"] is True


def test_the_job_page_and_the_chips_use_the_one_rule() -> None:
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    # ONE line: the thin line replaces the stored "Only 2 requirements were read" note, in the same element.
    assert "const requirementsNote = thinLine || storedNote;" in page and "thinPostingLine(job.verdict, assessment, storedNote)" in page
    assert page.count('data-role="requirements-note"') == 1
    chip = (UI_SRC / "components" / "VerdictChip.jsx").read_text(encoding="utf-8")
    assert "isThinMatch(verdict, assessment)" in chip and "THIN_LABEL" in chip
    badge = (UI_SRC / "components" / "MatrixBadge.jsx").read_text(encoding="utf-8")
    assert "verdictLabel(status, assessment)" in badge
    assert ".verdict-chip.fit-thin_posting" in (UI_SRC / "styles.css").read_text(encoding="utf-8")
