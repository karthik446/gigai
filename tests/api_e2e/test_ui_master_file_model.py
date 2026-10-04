"""0.1.10.9 M8: the UI's rules for the master's file and for the migration's line count (``ui/src/masterModel.js``), under node.

What is pinned:

* the Master page's line about ``master.md`` in the resumes folder, and the ONE button it offers: "Import the file"
  for a file that is there (it says "changes not imported yet" when the user changed it, and that the master moved on
  when it did), "Write the file" for one that is missing or behind, nothing when there is no master and no file;
* what an import did, in one line (imported with its counts and what it did to the profiles, nothing to import, written);
* an import the server refused because the master moved on (409) or GigAI never wrote the file (422) is ASKED about:
  "Import it anyway" sends the current revision; any other error is not;
* a write made on the page says so when ``master.md`` was left alone and the revision went beside it;
* the migration preview says what became of every line of the resumes and lists the left-out lines by resume, line
  number and reason, with the server's own sentence, never a line's text.

The payloads are the product's own: ``master_file.file_status`` on a real folder, the route's OpenAPI example (which
``test_master_file_journey.py`` checks against the real reply), ``plan_migration`` on a fixture resume. LOUD skip
when ``node`` is missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout import master_file, resumes_folder
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.api.openapi import _ROUTE_ENTRIES
from gigai.scout.master_migration import SourceResume, plan_migration
from gigai.scout.master_resume import parse_master
from gigai.scout.master_store import MasterRevision, StoredMaster, strip_contact, write_file

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
SRC = UI / "src"
MODEL_JS = SRC / "masterModel.js"
FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "master"
MASTER = parse_master((FIXTURES / "master.md").read_text(encoding="utf-8"))


def _run(body: str, payload: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; master file model check not run")
    script = f"import * as model from {json.dumps(MODEL_JS.resolve().as_uri())};\nconst input = JSON.parse(process.argv[1]);\nconst out = {{}};\n{body}\nprocess.stdout.write(JSON.stringify(out));\n"
    completed = subprocess.run([node, "--input-type=module", "-e", script, "--", json.dumps(payload)], capture_output=True, text=True, timeout=120, check=False, cwd=UI)
    assert completed.returncode == 0, f"node failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


def _stored(number: int) -> StoredMaster:
    revision = MasterRevision(number, f"revision_{number:08d}-0000-4000-8000-000000000000", None, "operator", "2026-10-04T10:05:00.000000Z", "sha256:x")
    return StoredMaster("record_00000000-0000-4000-8000-000000000001", revision, number, MASTER)


def test_the_file_line_says_how_master_md_stands_and_offers_the_one_button(tmp_path: Path) -> None:
    home = tmp_path / "home"
    file = home / "resumes" / "master.md"
    statuses: dict[str, dict] = {"none": master_file.file_status(home, None), "missing": master_file.file_status(home, _stored(3))}
    written = write_file(home, _stored(3))
    statuses["current"] = master_file.file_status(home, _stored(3))
    statuses["stale"] = master_file.file_status(home, _stored(5))  # GigAI's own file, untouched, from an earlier revision
    file.write_text(file.read_text(encoding="utf-8") + "\n- A line typed by hand.\n", encoding="utf-8")
    statuses["changed"] = master_file.file_status(home, _stored(3))
    beside = write_file(home, _stored(5))  # the master moved on: the revision goes beside the user's file
    statuses["behind"] = master_file.file_status(home, _stored(5))
    assert [statuses[key]["state"] for key in ("none", "missing", "current", "stale", "changed", "behind")] == ["missing", "missing", "current", "current", "changed", "changed"]
    assert (beside["wrote"], beside["not_imported"], written["wrote"]) == ("master-2.md", True, "master.md")

    out = _run(
        """
        out.lines = Object.fromEntries(Object.entries(input.statuses).map(([key, status]) => [key, model.fileLine(status)]));
        out.nothing = [model.fileLine(null), model.fileLine(undefined), model.fileLine("x")];
        out.wrote = [model.fileWriteLine(input.written), model.fileWriteLine(input.beside), model.fileWriteLine(null), model.fileWriteLine({error: "folder_unwritable"})];
        """,
        {"statuses": statuses, "written": written, "beside": beside},
    )
    lines = out["lines"]
    shown = resumes_folder.master_file(home).shown
    assert lines["none"] is None and out["nothing"] == [None, None, None]
    assert lines["current"] == {"state": "current", "text": f"Also a file you can edit: {shown}. Change it in your own editor, then import it here.", "button": "Import the file"}
    assert lines["changed"] == {"state": "changed", "text": f"master.md has changes not imported yet ({shown}).", "button": "Import the file"}
    assert lines["behind"] == {
        "state": "changed", "button": "Import the file",
        "text": f"master.md has changes not imported yet ({shown}). The master changed here since that file was written (revision 3, now 5; the master as it is now is in master-2.md).",
    }
    assert lines["missing"]["button"] == lines["stale"]["button"] == "Write the file"
    assert lines["missing"]["state"] == "missing" and lines["missing"]["text"].startswith("master.md is not in your resumes folder (")
    assert lines["stale"] == {"state": "behind", "text": "master.md in your resumes folder holds revision 3, not 5.", "button": "Write the file"}
    assert out["wrote"] == [
        "", "master.md has changes not imported yet, so it was left as it is: this revision is in master-2.md beside it.", "",
        "The master could not be written into your resumes folder; it is stored.",
    ]
    # No line of the master is in what the page is told about the file.
    assert not any(item.text in json.dumps(statuses) for item in MASTER.items.values())


def test_an_import_says_what_it_did_and_a_master_that_moved_on_is_asked_about() -> None:
    example = next(entry.example for entry in _ROUTE_ENTRIES if (entry.method, entry.path) == ("POST", "/api/master/sync"))
    offers = {"synced": [{"label": "Staff Engineer"}], "offers": [{"label": "Staff AI Engineer", "offer": "1 new master line: refresh?"}]}
    current = {"revision": 5, "written_by": "agent"}
    out = _run(
        """
        out.imported = model.syncLine(input.example);
        out.profiles = model.syncLine({...input.example, changes: {added: 1, removed: 0, changed: 0}, ids: {kept: 7, assigned: 1, restored: 1}, profiles: input.offers});
        out.unchanged = model.syncLine({...input.example, status: "unchanged", written: false});
        out.written = model.syncLine({...input.example, status: "written", written: false});
        out.none = model.syncLine(null);
        out.conflict = model.importAnywayOf({code: "revision_conflict", current: input.current, message: "x"});
        out.required = model.importAnywayOf({code: "revision_required", current: input.current, message: "x"});
        out.others = [model.importAnywayOf({code: "personal_info_refused", message: "x"}), model.importAnywayOf({code: "revision_conflict"}), model.importAnywayOf(null)];
        """,
        {"example": example, "offers": offers, "current": current},
    )
    assert out["imported"] == "Imported master.md as revision 4: 1 added, 1 changed, 1 retired; 3 ids kept, 1 new. History can put a retired line back."
    assert out["profiles"] == (
        "Imported master.md as revision 4: 1 added, 0 changed, 0 retired; 7 ids kept, 1 new, 1 restored. "
        "Staff Engineer: the resume now shows this change. Staff AI Engineer: 1 new master line: refresh?"
    )
    assert out["unchanged"] == "Nothing to import: master.md says what the master holds."
    assert out["written"] == "Wrote master.md to your resumes folder. Nothing was imported." and out["none"] == ""
    assert out["conflict"]["revision"] == out["required"]["revision"] == 5 and out["conflict"]["button"] == out["required"]["button"] == "Import it anyway"
    assert out["conflict"]["message"].startswith("Not imported: the master changed here or through your agent after master.md was written (it is at revision 5 now).")
    assert "retires what was added since" in out["conflict"]["message"] and "--revision" not in out["conflict"]["message"]
    assert out["required"]["message"].startswith("Not imported: GigAI did not write this master.md")
    assert out["others"] == [None, None, None]


def _plan(name: str) -> dict:
    """The migration's own JSON for one fixture resume, through the privacy strip, as ``master_profiles.migrate`` does it."""

    clean, gone = strip_contact((FIXTURES / "shapes" / name).read_text(encoding="utf-8"))
    source = SourceResume("k", clean, ("default",), tuple(sorted({line for _kind, line in gone.lines})))
    return plan_migration([source], base=None, answers={}).to_json()


def test_the_migration_preview_counts_every_line_and_lists_the_left_out_ones_with_why() -> None:
    plan = _plan("bold-title.md")
    lines = plan["source_lines"]
    assert lines["left_out"] > 0 and lines["in"] == lines["kept"] + lines["folded"] + lines["left_out"]
    whole = _plan("gigai-format.md")
    out = _run(
        """
        out.line = model.sourceLinesLine({migration: input.plan});
        out.rows = model.leftOutRows({migration: input.plan});
        out.whole = [model.sourceLinesLine({migration: input.whole}), model.leftOutRows({migration: input.whole})];
        out.two = model.leftOutRows({migration: {source_lines: {resumes: [
          {profiles: ["A", "B"], left_out: [{reason: "unknown_section", why: "under a heading GigAI does not know", lines: [12]}]},
          {profiles: ["C"], left_out: []},
        ]}}});
        out.absent = [model.sourceLinesLine(null), model.sourceLinesLine({migration: null}), model.sourceLinesLine({migration: {}}), model.leftOutRows(null), model.leftOutRows({migration: {}})];
        out.summary = model.migrationSummary({migration: input.plan});
        """,
        {"plan": plan, "whole": whole},
    )
    assert out["line"] == (
        f"Of {lines['in']} lines of resume text (headings, role lines and wrapped lines counted): {lines['kept']} kept, "
        f"{lines['folded']} folded into a line the master already holds, {lines['left_out']} left out."
    )
    # One row per resume and reason, with the server's own sentence; the numbers are the stored resume's line numbers.
    served = [(row["reason"], row["lines"], row["why"]) for resume in lines["resumes"] for row in resume["left_out"]]
    assert [(row["reason"], row["why"], row["resume"], row["count"]) for row in out["rows"]] == [(reason, why, "default", len(numbers)) for reason, numbers, why in served]
    assert [row["lines"] for row in out["rows"]] == [("line " if len(numbers) == 1 else "lines ") + ", ".join(str(number) for number in numbers) for _reason, numbers, _why in served]
    assert sum(row["count"] for row in out["rows"]) == lines["left_out"] and len({row["key"] for row in out["rows"]}) == len(out["rows"])
    # A resume the reader keeps whole: the sentence says so, and there is no list.
    assert out["whole"][0].endswith("0 left out.") and out["whole"][1] == []
    assert out["two"] == [{"key": "0:unknown_section", "resume": "A, B", "lines": "line 12", "count": 1, "reason": "unknown_section", "why": "under a heading GigAI does not know"}]
    assert out["absent"] == ["", "", "", [], []]
    assert out["summary"].startswith("1 resume, ")  # the P5 sentence is as it was
    # Never a line of the resume: only numbers, the profile's label and the reason.
    resume_lines = [line.strip("-*# ").strip() for line in (FIXTURES / "shapes" / "bold-title.md").read_text(encoding="utf-8").splitlines() if len(line.strip()) > 12]
    assert not any(line in json.dumps(out["rows"]) + out["line"] for line in resume_lines)


def test_the_master_page_is_wired_to_the_file_and_to_the_left_out_lines() -> None:
    api = (SRC / "api.js").read_text(encoding="utf-8")
    assert 'request("POST", "/api/master/sync", revision ? { revision } : {})' in api
    page = (SRC / "views" / "MasterView.jsx").read_text(encoding="utf-8")
    assert "fileLine(body ? body.file : null)" in page and 'data-role="master-file"' in page and 'data-action="import-file"' in page
    assert "postMasterSync(anywayRevision ? { revision: anywayRevision } : {})" in page and 'data-action="import-anyway"' in page
    assert "importAnywayOf(err)" in page and "syncLine(response)" in page and "fileWriteLine(response.file)" in page
    assert 'data-role="migration-source-lines"' in page and 'data-role="migration-left-out"' in page and "leftOutRows(plan)" in page
    # The page has ONE way to read the file into the master: the button. No effect, no timer, no load path calls the import.
    assert page.count("postMasterSync(") == 1 and page.count("importFile(") == 2
    assert "useEffect(() => {\n    importFile" not in page and "setInterval" not in page
