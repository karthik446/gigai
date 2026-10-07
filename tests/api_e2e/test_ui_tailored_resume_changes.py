"""uat-bug-044: the tailored resume shows what changed (model + panel), under node.

* ``previewLines`` never doubles a heading marker (a copied ``### Role`` line
  shows ``### Role``) and gives every rewritten line the resume lines it cites
  (``original``) plus a word diff;
* ``changeSummary`` counts come from the same rewritable lines (N + kept + copied == M; headings excluded);
* the panel (server-rendered through vite's SSR build, real React) is ONE view
  (0.1.11.5: no "Show changes" / "Clean copy" toggle): the change summary, then
  the page's rendered preview given the clean copy, which renders formatted text
  and escapes model text (``<script>`` reaches the page as ``&lt;script&gt;``);
  the panel source has no innerHTML.

LOUD skip when ``node`` or the ui ``node_modules`` is missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
TAILORED_JS = UI / "src" / "tailoredResumeModel.js"
PANEL_JSX = UI / "src" / "components" / "TailoredResumePanel.jsx"

R1 = "# Riley Example"
R4 = "### EXAMPLE CORP"
R5 = "- Built Python services for the tutoring platform."
R6 = "Led a team of five engineers."
EVIL = "<script>alert(1)</script> & **bold** claim"


def _ref(line: int, text: str) -> dict:
    return {"kind": "resume", "line": line, "text": text}


def _copy(line: int, text: str) -> dict:
    return {"kind": "copy", "text": text, "refs": [_ref(line, text)]}


def _rewritten(text: str, refs: list[dict]) -> dict:
    return {"kind": "rewritten", "text": text, "refs": refs}


NEW_BULLET = "Built Python services for the Kubernetes tutoring platform."
RESPONSE = {
    "job": {"title": "Engineer", "company": "Acme"},
    "resume": {"profile_id": "p1"},
    "result": {
        "header": [_copy(1, R1)],
        "sections": [
            {"heading": "summary", "lines": [_rewritten(EVIL, [_ref(5, R5), _ref(6, R6)])]},
            {"heading": "education", "entries": [{"heading": [_copy(4, R4)], "bullets": [_rewritten(NEW_BULLET, [_ref(5, R5)])]}]},
            {"heading": "skills", "lines": [_copy(6, R6)]},
        ],
    },
    "markdown": "# raw\n",
    "updated_at": "2026-09-29T10:00:00+00:00",
    "stored_path": "/x.json",
}


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; tailored-resume changes check not run")
    return node


def _run(script: str, payload: dict) -> dict:
    completed = subprocess.run([_node(), "--input-type=module", "-e", script, "--", json.dumps(payload)], capture_output=True, text=True, timeout=60, check=False, cwd=UI)
    assert completed.returncode == 0, f"node failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


MODEL_SCRIPT = f"""
import {{ previewLines, previewStats, changeSummary, inlineSegments }} from {json.dumps(TAILORED_JS.resolve().as_uri())};
const input = JSON.parse(process.argv[1]);
const lines = previewLines(input.result);
const stats = previewStats(lines);
process.stdout.write(JSON.stringify({{
  lines: lines.filter((l) => l.kind === "copy" || l.kind === "rewritten").map((l) => ({{ kind: l.kind, display: l.display, original: l.original || null, diff: l.diff || null }})),
  stats, summary: changeSummary(stats), inline: inlineSegments(input.inline),
}}));
"""


def test_heading_marker_is_never_doubled_and_rewritten_lines_carry_their_originals() -> None:
    out = _run(MODEL_SCRIPT, {"result": RESPONSE["result"], "inline": EVIL})
    displays = [line["display"] for line in out["lines"]]
    assert "### EXAMPLE CORP" in displays  # fail-before: "### ### EXAMPLE CORP"
    assert not any("### ###" in shown or "# #" in shown for shown in displays)
    assert displays[0] == "# Riley Example"
    summary_line, bullet_line = out["lines"][1], out["lines"][3]
    assert [item["text"] for item in summary_line["original"]] == ["Built Python services for the tutoring platform.", R6]
    assert [item["label"] for item in summary_line["original"]] == ["Resume line 5", "Resume line 6"]
    # The word diff marks only the words the rewrite added.
    assert [s["text"] for s in bullet_line["diff"] if s["added"]] == ["Kubernetes"]
    assert " ".join(s["text"] for s in bullet_line["diff"]) == NEW_BULLET
    # A copied line has no original block.
    assert out["lines"][0]["original"] is None


def test_summary_counts_match_the_line_provenance() -> None:
    out = _run(MODEL_SCRIPT, {"result": RESPONSE["result"], "inline": ""})
    kinds = [line["kind"] for line in out["lines"]]
    assert len(kinds) == 5  # the header and the entry heading are content lines too ...
    assert out["stats"]["total"] == 3  # ... but not rewritable lines: they stay out of the counts
    assert out["stats"]["rewritten"] == 2
    assert out["stats"]["copied"] == 1
    assert out["summary"] == "2 of 3 lines rewritten · 1 copied; New words (not in the cited lines): Kubernetes"


def test_inline_segments_never_turn_text_into_markup() -> None:
    out = _run(MODEL_SCRIPT, {"result": RESPONSE["result"], "inline": EVIL})
    assert out["inline"] == [{"text": "<script>alert(1)</script> & ", "bold": False}, {"text": "bold", "bold": True}, {"text": " claim", "bold": False}]


SSR_SCRIPT = f"""
import {{ build }} from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import fs from "node:fs";
const input = JSON.parse(process.argv[1]);
const out = fs.mkdtempSync(path.join(process.cwd(), "node_modules", ".tailor-ssr-"));
await build({{ root: process.cwd(), logLevel: "silent", plugins: [react()], build: {{ ssr: {json.dumps(PANEL_JSX.resolve().as_posix())}, outDir: out, emptyOutDir: true, rollupOptions: {{ output: {{ format: "esm", entryFileNames: "panel.mjs" }} }} }} }});
const {{ Preview }} = await import(path.join(out, "panel.mjs"));
const React = (await import("react")).default;
const {{ renderToStaticMarkup }} = await import("react-dom/server");
const render = (props) => renderToStaticMarkup(React.createElement(Preview, {{ response: input.response, profileLabel: "p1", rendered: (text) => React.createElement("div", {{ "data-role": "rendered" }}, text), ...props }}));
process.stdout.write(JSON.stringify({{ one: render({{}}), withHandler: render({{ onLength: () => {{}}, choiceBusy: false }}) }}));
fs.rmSync(out, {{ recursive: true, force: true }});
"""


def test_the_panel_is_one_view_with_a_clean_escaped_copy() -> None:
    if not (UI / "node_modules" / "vite").is_dir():
        pytest.skip("ui/node_modules missing; panel render check not run")
    out = _run(SSR_SCRIPT, {"response": RESPONSE})["one"]
    assert "2 of 3 lines rewritten · 1 copied" in out
    assert "Show changes" not in out and "Clean copy" not in out and "view-toggle" not in out and 'aria-label="Resume view"' not in out
    assert 'class="md-line' not in out and 'data-view="changes"' not in out and "diff-added" not in out
    assert 'data-role="rendered"' in out and 'data-view="clean"' in out
    assert "<h5" in out and "EXAMPLE CORP" in out and "###" not in out and "# Riley" not in out
    assert "<strong>bold</strong>" in out
    assert "<script>" not in out and "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_the_panel_never_sets_inner_html() -> None:
    assert "dangerouslySetInnerHTML" not in PANEL_JSX.read_text(encoding="utf-8")
    assert "innerHTML" not in PANEL_JSX.read_text(encoding="utf-8")


# --- 0110-006 packet C: the per-line "keep original" controls --------------------

STAFF = "- Led a team of 7 engineers delivering platform end to end, owning technical direction."
WEAKER = "Led 7 engineers delivering platform, with technical direction."
DSAR = "Built the MRX workflow for OH discharge lists."


def _alternative(kind: str, text: str, line: int, **extra: object) -> dict:
    return {"kind": kind, "text": text, "refs": [_ref(line, text)], **extra}


CHOICES = {
    "header": [_copy(1, R1)],
    "sections": [
        {
            "heading": "experience",
            "entries": [
                {
                    "heading": [_copy(2, "**Staff Engineer — Example Corp**")],
                    "bullets": [
                        # a shown rewrite: Keep original
                        {**_rewritten(NEW_BULLET, [_ref(5, R5)]), "id": "L1", "origin": "model", "reason": {"kind": "surface", "requirement": "M3", "posting_phrase": "event-driven"}, "alternative": _alternative("copy", R5, 5)},
                        # a fallback: the no-loss check kept the original
                        {**_copy(3, STAFF), "id": "L2", "origin": "fallback", "alternative": _alternative("rewritten", WEAKER, 3, lost={"ownership": ["own"], "scope": ["end to end"]})},
                        # the operator's choice: Undo
                        {**_copy(4, DSAR), "id": "L3", "origin": "user", "alternative": _alternative("rewritten", "Built an MRX workflow.", 4, lost={"entities": ["oh"]})},
                        # an older line: no id, no alternative, no controls
                        _copy(6, "- Plain bullet from an older result."),
                    ],
                }
            ],
        }
    ],
}
CHOICES_RESPONSE = {**RESPONSE, "result": CHOICES}

CHOICES_MODEL_SCRIPT = f"""
import {{ previewLines, previewStats, changeSummary, lineAction, reasonLabel, lostLabels }} from {json.dumps(TAILORED_JS.resolve().as_uri())};
const input = JSON.parse(process.argv[1]);
const lines = previewLines(input.result);
const content = lines.filter((l) => l.kind === "copy" || l.kind === "rewritten");
const stats = previewStats(lines);
process.stdout.write(JSON.stringify({{
  rows: content.map((l) => ({{ display: l.display, heading: l.heading, id: l.id, origin: l.origin, action: lineAction(l), reason: reasonLabel(l.reason), lost: lostLabels(l.alternative) }})),
  stats, summary: changeSummary(stats),
  reasons: [reasonLabel({{ kind: "summary" }}), reasonLabel({{ kind: "answer" }}), reasonLabel({{ kind: "surface", requirement: "M3" }}), reasonLabel(null)],
  phrases: ["A working practice of testing", "AWS experience", "SQL tuning", "I led the team", "already lower"].map((p) => reasonLabel({{ kind: "surface", posting_phrase: p }})).concat([reasonLabel({{ kind: "surface", requirement: "M3", posting_phrase: "Event-driven design" }})]),
}}));
"""


def test_each_line_offers_the_one_button_its_origin_allows_and_headings_are_not_counted() -> None:
    out = _run(CHOICES_MODEL_SCRIPT, {"result": CHOICES})
    rows = {row["id"] or row["display"]: row for row in out["rows"]}
    assert rows["L1"]["action"] == {"use": "original", "label": "Keep original"}
    assert rows["L1"]["reason"] == "for M3: event-driven"
    assert rows["L2"]["action"] == {"use": "rewritten", "label": "Use rewrite anyway"}
    assert rows["L2"]["lost"] == ["ownership: own", "scope: end to end"]
    assert rows["L3"]["action"] == {"use": "rewritten", "label": "Undo"}
    older = next(row for row in out["rows"] if row["id"] is None and not row["heading"])
    assert older["action"] is None and older["origin"] == "model"
    assert all(row["action"] is None for row in out["rows"] if row["heading"])
    assert out["reasons"] == ["for the summary", "from your answer", "for M3", ""]
    assert out["phrases"] == ["for a working practice of testing", "for AWS experience", "for SQL tuning", "for I led the team", "for already lower", "for M3: event-driven design"]
    # 4 bullets are the rewritable lines; the header and the entry heading are not.
    assert out["stats"]["total"] == 4 and out["stats"]["keptOriginal"] == 1 and out["stats"]["rewritten"] == 1
    assert out["summary"].startswith("1 of 4 lines rewritten · 1 kept as your original (the rewrite dropped facts) · 2 copied")


def test_a_copied_bullet_never_shows_a_doubled_marker() -> None:
    out = _run(CHOICES_MODEL_SCRIPT, {"result": CHOICES})
    displays = [row["display"] for row in out["rows"]]
    assert f"- {STAFF[2:]}" in displays  # fail-before: "- - Led a team ..."
    assert not any("- - " in shown for shown in displays)


def test_the_panel_draws_no_per_line_controls_any_more() -> None:
    """The per-line controls (Keep original / Use rewrite anyway / Undo) lived in the removed "Show changes" view; Restore
    is the Changed tab's (PickedLeftOut), "Save this wording to my master" the points list's (ResumePoints)."""
    if not (UI / "node_modules" / "vite").is_dir():
        pytest.skip("ui/node_modules missing; panel render check not run")
    out = _run(SSR_SCRIPT, {"response": CHOICES_RESPONSE})["withHandler"]
    for gone in ("Keep original", "Use rewrite anyway", ">Undo<", "Kept your line", "line-controls", "save-wording"):
        assert gone not in out
    assert "1 of 4 lines rewritten · 1 kept as your original (the rewrite dropped facts) · 2 copied" in out


def test_the_panel_puts_the_choice_and_reloads_on_a_409() -> None:
    api = (UI / "src" / "api.js").read_text(encoding="utf-8")
    assert 'request("PUT", "/api/tailored-resumes/lines"' in api
    for key in ("profile_id: profileId", "job_identity: jobIdentity", "updated_at: updatedAt", "line_id: lineId", "use,"):
        assert key in api
    panel = (PANEL_JSX.parent / "JobResumePanel.jsx").read_text(encoding="utf-8")  # 0.1.11: the panel around the preview
    assert "putTailoredResumeLine" in panel and 'err.code === "tailored_resume_changed"' in panel
