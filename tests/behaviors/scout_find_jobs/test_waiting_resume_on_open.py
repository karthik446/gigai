"""0.1.11.5 SP: opening a job serves the resume that WAITS beside an edited one, and names the line the master changed.

The operator's case, on synthetic data (the pick fixture of ``test_pick_header_room``: an invented 56-line master, one
posting, a scripted model, the pipeline off) through the REAL server: he edited a point of a job's resume, retired a
master line the resume prints, the page said "a line this resume prints was changed or retired" with only Re-assess,
he re-assessed, and the new pick waited beside his resume.

Pinned, on what ``GET /api/jobs/suggestions`` (the read the job page makes when it opens) answers:

- THE WAITING RESUME IS SERVED ON OPEN: ``proposed`` (with the line ids it prints), ``stale`` and ``conflicts``, after a
  re-assess of an edited resume. The read WRITES NOTHING (every file under the home, bytes and mtime) and costs the
  same reads with a resume waiting as without one: the stored resume once, and never the waiting resume's own file.
- ``stale_lines`` NAMES THE LINES behind ``picked_line_changed``: ``{id, change: retired | reworded}``, ids only.
- THE NO-MODEL FIX SETTLES IT: taking the retired line off the resume (``PUT /api/tailored-resumes/selection``), or
  putting the master's new words on the reworded point (``PUT /api/tailored-resumes/lines``), and the line is no longer
  stale: the rule is about what the stored resume STILL prints (before, only a new pick cleared it).
- the rule itself (``suggestions.changed_lines``), pure.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from gigai.scout import suggestions, tailored_resume

from tests.behaviors.scout_find_jobs.test_pick_header_room import (  # noqa: F401 - `fx`, `server` and `_pipeline_off` are the fixtures
    _JOB, _URL, KUBERNETES_LINE, PYTHON_LINE, REACT_LINE, TERRAFORM_LINE, _Server, _answer, _assess, _ids, _pipeline_off, fx, server,
)
from tests.support.posting_fixtures import PostingsFixture

EDITED = "Edited by hand: kept the night-shift roster running through three migrations."
REWORDED = "Reworded in the master: cut the weekly payroll export from four hours to nine minutes."


def suggestions_url(server: _Server) -> str:
    return f"/api/jobs/suggestions?url={quote(_JOB, safe='')}&profile_id={server.key['profile_id']}"


def opened(server: _Server) -> dict:
    """What the job page reads when it opens."""

    answer = server.client.get(suggestions_url(server))
    assert answer.status_code == 200, answer.text
    return answer.json()


def stored(server: _Server) -> dict:
    return server.client.get("/api/tailored-resumes", params=server.key).json()["items"][0]


def bullets(resume: dict) -> list[dict]:
    return [bullet for section in resume["result"]["sections"] for entry in section.get("entries", []) for bullet in entry["bullets"]]


def cited(line: dict) -> str | None:
    refs = [*line.get("refs", []), *((line.get("edited_from") or {}).get("refs", []))]
    return next((ref["item_id"] for ref in refs if ref.get("kind") == "resume" and ref.get("item_id")), None)


def plain_bullets(fx: PostingsFixture, server: _Server) -> list[dict]:
    """The stored resume's bullets that copy a master line no requirement rests on (retiring one leaves the assessment current)."""

    must = {_ids(fx)[line] for line in (PYTHON_LINE, TERRAFORM_LINE, REACT_LINE)} | {_ids(fx).get(KUBERNETES_LINE)}
    return [line for line in bullets(stored(server)) if line["kind"] == "copy" and cited(line) not in must]


def edit_point(server: _Server, line_id: str, text: str = EDITED) -> dict:
    answer = server.client.put("/api/tailored-resumes/lines", json={**server.key, "updated_at": stored(server)["updated_at"], "line_id": line_id, "use": "custom", "text": text})
    assert answer.status_code == 200, answer.text
    return answer.json()


def master_line(server: _Server, item_id: str, use: str, text: str | None = None) -> None:
    """One change of the master through the Master page's own route: ``retire``, or ``edit`` with the new words."""

    revision = server.client.get("/api/master").json()["master"]["revision"]
    answer = server.client.put("/api/master/lines", json={"revision": revision, "id": item_id, "use": use, **({"text": text} if text is not None else {})})
    assert answer.status_code == 200, answer.text


def remove_point(server: _Server, item_id: str) -> dict:
    answer = server.client.put("/api/tailored-resumes/selection", json={**server.key, "updated_at": stored(server)["updated_at"], "use": "remove", "item_id": item_id})
    assert answer.status_code == 200, answer.text
    return answer.json()


def script_reassessment(fx: PostingsFixture, server: _Server) -> None:
    """The scripted model's NEXT answer: Matched again, on the requirement rows the job already has, without the retired Kubernetes line."""

    answer = json.loads(_answer(fx, must=(PYTHON_LINE, TERRAFORM_LINE, TERRAFORM_LINE, REACT_LINE)))
    for row, known in zip(answer["matrix"], opened(server)["requirements"]):
        row["id"] = known["id"]
    fx.base.model.assessed = json.dumps(answer)


def edited_then_retired(fx: PostingsFixture, server: _Server) -> str:
    """The operator's state before his Re-assess: one edited point, and a master line the resume prints (a requirement's
    evidence) retired. Returns the retired line's id."""

    _assess(fx)
    edit_point(server, plain_bullets(fx, server)[0]["id"])
    retired = _ids(fx)[KUBERNETES_LINE]
    master_line(server, retired, "retire")
    return retired


def _files(root: Path) -> dict[str, tuple[bytes, int]]:
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns) for path in sorted(root.rglob("*")) if path.is_file()}


def test_opening_a_job_serves_the_resume_that_waits_and_writes_nothing(fx: PostingsFixture, server: _Server, monkeypatch: pytest.MonkeyPatch) -> None:
    retired = edited_then_retired(fx, server)
    before = opened(server)
    assert before["stale"] == ["assessment_stale:resume_changed", "picked_line_changed"] and before["proposed"] is None
    assert before["stale_lines"] == [{"id": retired, "change": "retired"}]

    reads: list[str] = []
    real_resume, real_record = tailored_resume.read_tailored_resume, suggestions.read_record
    monkeypatch.setattr(tailored_resume, "read_tailored_resume", lambda path, *a, **k: (reads.append(f"resume:{Path(path).name}"), real_resume(path, *a, **k))[1])
    monkeypatch.setattr(suggestions, "read_record", lambda path, *a, **k: (reads.append("record"), real_record(path, *a, **k))[1])
    opened(server)
    without = list(reads)

    script_reassessment(fx, server)
    assessed = server.client.post("/api/assess", json={"job": {"job_url": _URL}})  # what the page's Re-assess sends
    assert assessed.status_code == 200, assessed.text
    held = _files(fx.home_root)
    reads.clear()
    view = opened(server)
    # The new pick WAITS beside his resume, and the read that opens the job says so.
    assert view["stale"] == ["picked_line_changed", "assessment_newer"]
    assert view["gate"] == {"decision": "suggest", "ready": True, "reasons": []} and view["conflicts"] == []
    waiting = view["proposed"]
    assert waiting is not None and waiting["picked_by"] == "model" and retired not in waiting["lines"] and len(waiting["lines"]) > 10
    assert retired in view["selected_lines"] and view["stale_lines"] == [{"id": retired, "change": "retired"}]
    assert not any(isinstance(value, str) and KUBERNETES_LINE in value for value in (json.dumps(view["stale_lines"]), json.dumps(waiting)))
    # No write on open, and a flat cost: the same reads as with nothing waiting; the stored resume once, never the waiting one's file.
    assert _files(fx.home_root) == held
    assert reads == without, (reads, without)
    assert len([name for name in reads if name.startswith("resume:")]) == 1 and "proposed-resume" not in " ".join(reads), reads
    record = suggestions.read_suggestions(fx.home_root, fx.target, server.key["profile_id"], _JOB)
    assert record is not None and suggestions.proposed_resume_path(Path(record.stored_path)).is_file()
    # His edited resume is still the stored one.
    assert EDITED in stored(server)["markdown"]


def test_taking_the_retired_line_off_or_using_the_new_wording_settles_the_line_with_no_model(fx: PostingsFixture, server: _Server) -> None:
    _assess(fx)
    first, second = plain_bullets(fx, server)[:2]
    gone, changed = cited(first), cited(second)
    master_line(server, gone, "retire")
    master_line(server, changed, "edit", REWORDED)
    view = opened(server)
    assert view["stale"] == ["picked_line_changed"], "no requirement rests on either line: the assessment is current"
    assert sorted(view["stale_lines"], key=lambda item: item["change"]) == [{"id": gone, "change": "retired"}, {"id": changed, "change": "reworded"}]
    calls = len(fx.base.model.assess_prompts)

    after = remove_point(server, gone)
    assert first["text"].lstrip("- ") not in after["markdown"]
    view = opened(server)
    assert view["stale"] == ["picked_line_changed"] and view["stale_lines"] == [{"id": changed, "change": "reworded"}]

    line = next(item for item in bullets(stored(server)) if cited(item) == changed)
    after = edit_point(server, line["id"], REWORDED)
    assert REWORDED in after["markdown"]
    view = opened(server)
    # (the master moved since the pick: that stays a NOTE, with no line of the resume behind it)
    assert view["stale"] == ["master_newer"] and view["stale_lines"] == []
    assert len(fx.base.model.assess_prompts) == calls, "no model was called"


def test_the_rule_counts_only_what_the_resume_still_prints() -> None:
    selection = {"line_marks": [{"id": "b-000001", "mark": "a"}, {"id": "b-000002", "mark": "b"}, {"id": "b-000003", "mark": "c"}]}
    now = {"b-000001": "a", "b-000002": "B"}
    retired, reworded = {"id": "b-000003", "change": "retired"}, {"id": "b-000002", "change": "reworded"}
    # Nothing known about the resume (the rule as it was): every recorded line counts.
    assert suggestions.changed_lines(selection, now) == (reworded, retired)
    assert suggestions.changed_lines(selection, None) == () and suggestions.changed_lines(None, now) == ()
    # Printed as the master's copy: both count. Taken off the resume: neither does.
    assert suggestions.changed_lines(selection, now, {"b-000001": True, "b-000002": True, "b-000003": True}) == (reworded, retired)
    assert suggestions.changed_lines(selection, now, {"b-000001": True}) == ()
    # In the user's own words: a reworded master line is settled, a RETIRED one is still printed.
    assert suggestions.changed_lines(selection, now, {"b-000002": False, "b-000003": False}) == (retired,)
