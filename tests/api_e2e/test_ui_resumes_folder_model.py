"""0110-10-05: the resumes folder and the "edited" mark in the UI (model + panel), under node.

What is pinned:

* the folder reads as the user types it, and says when it is the default;
* the job page's line is the folder plus that job's markdown file, and nothing when the job has no
  file there;
* Save is offered only for a typed path that is not the shown one, and an empty field asks for the
  default folder;
* a tailored resume the user or their agent attached says who edited it (and the writer's source);
  one a model tailored says nothing;
* the panel renders that mark, with the source as text (never as markup).

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
MODEL_JS = UI / "src" / "resumesFolderModel.js"
PANEL_JSX = UI / "src" / "components" / "TailoredResumePanel.jsx"

DEFAULT = {"path": "/home/you/Documents/GigAI/resumes", "shown": "~/Documents/GigAI/resumes", "source": "default", "default": "~/Documents/GigAI/resumes", "exists": True}
CHOSEN = {**DEFAULT, "path": "/home/you/Resumes", "shown": "~/Resumes/", "source": "setting"}
FILE = "acme-staff-engineer-2026-10-04.md"
EVIL = "<script>alert(1)</script> notes"


def _response(edited: dict | None) -> dict:
    line = {"kind": "copy", "text": "- Ran the release calendar for 4 teams.", "refs": [{"kind": "resume", "line": 2, "text": "- Ran the release calendar for 4 teams."}], "id": "L1"}
    body = {
        "result": {"schema_version": "scout-tailored-resume:1", "header": [], "sections": [{"heading": "summary", "lines": [line]}]},
        "resume": {"profile_id": "p1"}, "updated_at": "2026-10-04T10:00:00+00:00", "stored_path": "/x.json",
    }
    return body if edited is None else {**body, "edited": edited}


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; resumes folder model check not run")
    return node


def _run(script: str, payload: dict) -> dict:
    completed = subprocess.run([_node(), "--input-type=module", "-e", script, "--", json.dumps(payload)], capture_output=True, text=True, timeout=60, check=False, cwd=UI)
    assert completed.returncode == 0, f"node failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


MODEL_SCRIPT = f"""
import {{ folderLine, folderFilePath, folderRequest, folderChanged, editedLine }} from {json.dumps(MODEL_JS.resolve().as_uri())};
const input = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify({{
  lines: input.folders.map((folder) => folderLine(folder)),
  files: input.files.map((folder) => folderFilePath(folder)),
  requests: input.typed.map((value) => folderRequest(value)),
  changed: input.typed.map((value) => folderChanged(input.folders[0], value)),
  edited: input.responses.map((response) => editedLine(response)),
}}));
"""


def test_the_folder_and_the_edited_mark_read_as_the_user_expects() -> None:
    out = _run(
        MODEL_SCRIPT,
        {
            "folders": [DEFAULT, CHOSEN, None],
            "files": [{**DEFAULT, "files": {"markdown": FILE, "pdf": None}}, {**CHOSEN, "files": {"markdown": FILE, "pdf": None}}, {**DEFAULT, "files": {"markdown": None, "pdf": None}}, DEFAULT, None],
            "typed": ["  ~/Resumes ", "", "~/Documents/GigAI/resumes", "/home/you/Documents/GigAI/resumes", None],
            "responses": [
                _response(None), _response({"written_by": "agent", "edited_at": "2026-10-04T10:00:00Z", "source": None}),
                _response({"written_by": "operator", "edited_at": "2026-10-04T10:00:00Z", "source": "trimmed to two pages"}), None,
            ],
        },
    )
    assert out["lines"] == ["~/Documents/GigAI/resumes (the default)", "~/Resumes/", ""]
    assert out["files"] == [f"~/Documents/GigAI/resumes/{FILE}", f"~/Resumes/{FILE}", "", "", ""]
    assert out["requests"] == [{"path": "~/Resumes"}, {"path": ""}, {"path": "~/Documents/GigAI/resumes"}, {"path": "/home/you/Documents/GigAI/resumes"}, {"path": ""}]
    assert out["changed"] == [True, False, False, False, False], "Save is for a typed path that is not the shown folder"
    assert out["edited"] == ["", "Edited by your agent", "Edited by you · source: trimmed to two pages", ""]


SSR_SCRIPT = f"""
import {{ build }} from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import fs from "node:fs";
const input = JSON.parse(process.argv[1]);
const out = fs.mkdtempSync(path.join(process.cwd(), "node_modules", ".resumes-folder-ssr-"));
await build({{ root: process.cwd(), logLevel: "silent", plugins: [react()], build: {{ ssr: {json.dumps(PANEL_JSX.resolve().as_posix())}, outDir: out, emptyOutDir: true, rollupOptions: {{ output: {{ format: "esm", entryFileNames: "panel.mjs" }} }} }} }});
const {{ Preview }} = await import(path.join(out, "panel.mjs"));
const React = (await import("react")).default;
const {{ renderToStaticMarkup }} = await import("react-dom/server");
const render = (response) => renderToStaticMarkup(React.createElement(Preview, {{ response, profileLabel: "p1", rendered: (text) => text }}));
process.stdout.write(JSON.stringify({{ edited: render(input.edited), tailored: render(input.tailored) }}));
fs.rmSync(out, {{ recursive: true, force: true }});
"""


def test_the_panel_says_who_edited_the_resume() -> None:
    if not (UI / "node_modules" / "vite").is_dir():
        pytest.skip("ui/node_modules missing; panel render check not run")
    out = _run(SSR_SCRIPT, {"edited": _response({"written_by": "agent", "edited_at": "2026-10-04T10:00:00Z", "source": EVIL}), "tailored": _response(None)})
    assert 'data-role="edited-by"' in out["edited"] and "Edited by your agent · source: " in out["edited"]
    assert "<script>" not in out["edited"] and "&lt;script&gt;alert(1)&lt;/script&gt; notes" in out["edited"]
    assert 'data-role="edited-by"' not in out["tailored"] and "Edited by" not in out["tailored"]
