"""0110-032: an edited tailored-resume line in the UI (model + panel), under node.

``PUT /api/tailored-resumes/lines`` with ``use: "custom"`` makes a line ``kind: custom`` with no
refs and ``edited_from`` (the line it replaced).  What is pinned:

* the model shows the edit's own text, diffed against the line it replaced, counts it as EDITED
  (not rewritten, not copied, not "unsourced") and says so in the summary and the stats line;
* the ways back are the versions the replaced line has: ``Use original`` and, when it kept a
  rewrite, ``Use rewrite`` -- the same PUT the other buttons send;
* a resume with no edited line counts and reads exactly as before (no ``edited`` key);
* the panel (0.1.11.5: ONE view) draws no per-line note, mark or button; the points list and the Changed tab own them.

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

HEADING = "### EXAMPLE CORP"
ORIGINAL = "- Rebuilt the scheduling service on Python and Postgres, cutting p95 latency by 40%."
REWRITE = "Rebuilt the scheduling service on Python and Postgres."
PLAIN = "- Ran the release calendar for 4 teams."
EDIT_ONE = "Rebuilt the scheduling service on Python and Postgres for 4 hospital teams, cutting p95 latency by 40%."
EDIT_TWO = "Ran the release calendar and on-call rota for 4 teams."
EVIL = "<script>alert(1)</script> edit"


def _ref(line: int, text: str) -> dict:
    return {"kind": "resume", "line": line, "text": text}


def _copy(line: int, text: str, line_id: str, **extra: object) -> dict:
    return {"kind": "copy", "text": text, "refs": [_ref(line, text)], "id": line_id, "origin": "model", **extra}


FALLBACK = _copy(2, ORIGINAL, "L2", origin="fallback", alternative={"kind": "rewritten", "text": REWRITE, "refs": [_ref(2, ORIGINAL)], "lost": {"numbers": ["40%"]}})
PLAIN_COPY = _copy(3, PLAIN, "L3")


def _custom(text: str, replaced: dict) -> dict:
    return {"kind": "custom", "text": text, "refs": [], "id": replaced["id"], "origin": "user", "edited_from": replaced}


def _response(bullets: list[dict]) -> dict:
    return {
        "result": {
            "schema_version": "scout-tailored-resume:1",
            "header": [],
            "sections": [{"heading": "experience", "entries": [{"heading": [_copy(1, HEADING, "L1")], "bullets": bullets}]}],
        },
        "resume": {"profile_id": "p1"},
        "updated_at": "2026-09-29T10:00:00+00:00",
        "stored_path": "/x.json",
    }


EDITED = _response([_custom(EDIT_ONE, FALLBACK), _custom(EDIT_TWO, PLAIN_COPY)])
UNEDITED = _response([FALLBACK, PLAIN_COPY])


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; edited tailored-resume line check not run")
    return node


def _run(script: str, payload: dict) -> dict:
    completed = subprocess.run([_node(), "--input-type=module", "-e", script, "--", json.dumps(payload)], capture_output=True, text=True, timeout=60, check=False, cwd=UI)
    assert completed.returncode == 0, f"node failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


MODEL_SCRIPT = f"""
import {{ previewLines, previewStats, changeSummary, statsLine, lineAction, lineActions, editedActions, sourcesHover }} from {json.dumps(TAILORED_JS.resolve().as_uri())};
const input = JSON.parse(process.argv[1]);
const lines = previewLines(input.result);
const stats = previewStats(lines);
process.stdout.write(JSON.stringify({{
  rows: lines.filter((l) => l.id).map((l) => ({{
    id: l.id, kind: l.kind, edited: l.edited, display: l.display, original: l.original || null,
    added: (l.diff || []).filter((s) => s.added).map((s) => s.text), actions: lineActions(l), single: lineAction(l), hover: sourcesHover(l),
  }})),
  stats, summary: changeSummary(stats), statsLine: statsLine(stats),
}}));
"""


def test_an_edited_line_shows_its_text_its_change_and_the_ways_back() -> None:
    out = _run(MODEL_SCRIPT, EDITED)
    rows = {row["id"]: row for row in out["rows"]}
    one, two = rows["L2"], rows["L3"]
    assert (one["kind"], one["edited"], one["display"]) == ("custom", True, f"- {EDIT_ONE}")
    assert one["original"] == [{"label": "Before the edit", "text": ORIGINAL[2:]}], "diffed against the line it replaced, shown without its marker"
    assert one["added"] == ["Postgres", "for", "4", "hospital", "teams,"]  # word-level: "Postgres," became "Postgres"
    assert one["actions"] == [{"use": "original", "label": "Use original"}, {"use": "rewritten", "label": "Use rewrite"}]
    assert two["actions"] == [{"use": "original", "label": "Use original"}], "a plain copy has no rewrite to go back to"
    assert one["single"] is None and one["hover"] == "Edited: your own text, no source cited."
    assert rows["L1"]["actions"] == [] and rows["L1"]["edited"] is False  # the entry heading is never edited

    assert out["stats"] == {"total": 2, "copied": 0, "keptOriginal": 0, "rewritten": 0, "citingAnswers": 0, "unsourced": 0, "keywords": [], "edited": 2}
    assert out["summary"] == "0 of 2 lines rewritten · 0 copied · 2 edited"
    assert out["statsLine"] == "2 lines · 0 copied verbatim · 0 rewritten · 2 edited"


def test_a_resume_without_an_edit_counts_and_reads_as_before() -> None:
    out = _run(MODEL_SCRIPT, UNEDITED)
    assert "edited" not in out["stats"] and out["stats"]["total"] == 2 and out["stats"]["copied"] == 2
    assert out["summary"] == "0 of 2 lines rewritten · 1 kept as your original (the rewrite dropped facts) · 1 copied"
    assert "edited" not in out["statsLine"]
    rows = {row["id"]: row for row in out["rows"]}
    assert rows["L2"]["actions"] == [{"use": "rewritten", "label": "Use rewrite anyway"}] == [rows["L2"]["single"]]
    assert rows["L3"]["actions"] == [] and all(row["edited"] is False for row in out["rows"])


SSR_SCRIPT = f"""
import {{ build }} from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import fs from "node:fs";
const input = JSON.parse(process.argv[1]);
const out = fs.mkdtempSync(path.join(process.cwd(), "node_modules", ".tailor-edit-ssr-"));
await build({{ root: process.cwd(), logLevel: "silent", plugins: [react()], build: {{ ssr: {json.dumps(PANEL_JSX.resolve().as_posix())}, outDir: out, emptyOutDir: true, rollupOptions: {{ output: {{ format: "esm", entryFileNames: "panel.mjs" }} }} }} }});
const {{ Preview }} = await import(path.join(out, "panel.mjs"));
const React = (await import("react")).default;
const {{ renderToStaticMarkup }} = await import("react-dom/server");
const render = (response) => renderToStaticMarkup(React.createElement(Preview, {{ response, profileLabel: "p1", onLength: () => {{}}, rendered: (text) => text }}));
process.stdout.write(JSON.stringify({{ edited: render(input.edited), unedited: render(input.unedited) }}));
fs.rmSync(out, {{ recursive: true, force: true }});
"""


def test_the_panel_shows_the_edited_text_in_the_one_view() -> None:
    """0.1.11.5: the preview is ONE view; an edited point is marked "Your words" in the points list (ResumePoints) and the
    ways back are the Changed tab's Restore: the preview draws no per-line markers or buttons."""
    if not (UI / "node_modules" / "vite").is_dir():
        pytest.skip("ui/node_modules missing; panel render check not run")
    evil = _response([_custom(EVIL, FALLBACK), _custom(EDIT_TWO, PLAIN_COPY)])
    out = _run(SSR_SCRIPT, {"edited": evil, "unedited": UNEDITED})
    edited = out["edited"]
    assert "0 of 2 lines rewritten · 0 copied · 2 edited" in edited
    assert EDIT_TWO in edited
    assert "<script>" not in edited and "&lt;script&gt;" in edited
    for gone in ('data-role="edited"', 'data-role="edited-mark"', 'data-action="original"', "Use original", 'class="md-line', "resume-legend"):
        assert gone not in edited
    unedited = out["unedited"]
    assert "Use original" not in unedited and "Use rewrite anyway" not in unedited and 'data-role="edited' not in unedited
