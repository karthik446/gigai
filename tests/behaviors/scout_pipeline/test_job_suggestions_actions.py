"""0.1.11 N5 (SPEC 2.1, 2.4, 5.3; 8.1 "suggestion add / resolve / dismiss and what a new assessment keeps").

On the END outcome, through the real CLI and the real record store (``scout.suggestions``), on the synthetic gig of
``tests/support/pipeline_fixtures`` with a small invented master. The job's record is seeded as its assessment
writes it (``suggestions.merged``: the fixture's model answers in the shape of before 0.1.11, which writes none).

- ``gigai scout suggestions add | resolve | dismiss | list``: who wrote is recorded, a closed suggestion stays with
  how it was closed, an id is never used twice, and a refused change (a line the master does not have, a
  requirement the job does not have, a contact detail, an unknown id) writes nothing.
- A NEW ASSESSMENT replaces only its own suggestions that are still open: every done or dismissed one, and every
  one an agent or the user added, is still listed after it.
- ``gigai scout resume store --resolves``: the named suggestions are done (``job_resume_edit``) once the hand-back
  is stored, a name the job does not have stores nothing, and the evidence check is made again on what the resume
  now prints: a hand-back that drops the only line behind a must-have is stored and reads "needs attention", and a
  reworded line counts for the master line it cites.
- ``gigai scout resume pick --use-proposed | --dismiss-proposed``: the one step that replaces a resume the user
  edited, and the one that drops the proposal and leaves the resume alone.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.scout import suggestions as sg
from gigai.scout.find_jobs.assess_contracts import AssessmentSuggestion
from gigai.scout.master_store import import_master
from gigai.scout.pipeline.settings import PIPELINE_ENV
from gigai.scout.scout_cli import scout_group
from gigai.scout.tailor_master import stored_master
from gigai.scout.tailored_resume import list_tailored_resumes

from tests.support.pipeline_fixtures import JOB, PipelineFixture, build_pipeline_fixture

RESUME = """## Experience

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.
- Operated Kubernetes clusters backed by PostgreSQL.

## Skills

- Python, Kubernetes, PostgreSQL
"""
MASTER = """## Summary

- Engineer with nine years on Python inference services.

## Experience

### Northwind Labs
Staff Engineer | 2023 - Present

- Own the Python inference services behind 40 product teams.
- Wrote the Terraform modules every Kubernetes cluster is built from.

### Acme Corp
Senior Engineer | 2019 - 2023

- Built Python services for six years; cut p99 latency by 40%.
- Operated Kubernetes clusters backed by PostgreSQL.

## Skills

- Platform: Python, Kubernetes, PostgreSQL, Terraform
"""
OWN = "Own the Python inference services behind 40 product teams."
TERRAFORM = "Wrote the Terraform modules every Kubernetes cluster is built from."
REWORDED = "Own the Python inference services that 40 product teams depend on."
REQ_PYTHON, REQ_TERRAFORM, REQ_GCP = "req-0000a1", "req-0000a2", "req-0000a3"
NOW, LATER = "2026-10-05T10:00:00Z", "2026-10-06T09:00:00Z"
SELECTION = {
    "picked_by": "model", "fallback": None, "draft": False, "pick_rules_version": "pick-rules:1", "selector_version": "sel-4", "made_at": NOW,
    "made_from": {"result_digest": None, "master_revision_id": None}, "model_pick": None, "problems": [], "added_by_code": [], "line_marks": [],
    "pages": 1, "max_pages": 2, "conflicts": [], "resume": None,
}


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipelineFixture:
    monkeypatch.setenv(PIPELINE_ENV, "on")
    fx = build_pipeline_fixture(tmp_path, monkeypatch, resume=RESUME)
    source = tmp_path / "master.md"
    source.write_text(MASTER, encoding="utf-8")
    assert import_master(home_root=fx.home_root, target=fx.target, source=source, gig_id=fx.gig.resolved.gig_id).status == "created"
    return fx


def _ids(fx: PipelineFixture) -> dict[str, str]:
    return {item.text: item.id for item in stored_master(fx.home_root, fx.target).master.items.values()}


def _path(fx: PipelineFixture) -> Path:
    return sg.suggestions_path(fx.home_root, fx.target, fx.profile_id, JOB)


def _assessed(fx: PipelineFixture, suggested: list[AssessmentSuggestion], *, now: str = NOW, **more: object) -> sg.SuggestionRecord:
    """The record as an assessment of the job writes it: what it replaces and what it keeps is ``suggestions.merged``'s."""

    ids = _ids(fx)
    path = _path(fx)
    rows = (
        sg.CoverageRow(REQ_PYTHON, "hard", "met", (ids[OWN],)),
        sg.CoverageRow(REQ_TERRAFORM, "askable", "met", (ids[TERRAFORM],)),
        sg.CoverageRow(REQ_GCP, "nice_to_have", "met", ("A cloud:gcp",)),
    )
    with sg.record_write_lock(path):
        record = sg.merged(
            sg.read_record(path), profile_id=fx.profile_id, job_identity=JOB, stored_path=str(path), now=now, basis={},
            gate={"decision": "suggest", "ready": False, "reasons": []}, requirements=rows, suggested=suggested, **{"selection": dict(SELECTION), **more},  # type: ignore[arg-type]
        )
        sg.save_record(record)
    return record


def _seed(fx: PipelineFixture) -> None:
    ids = _ids(fx)
    _assessed(fx, [
        AssessmentSuggestion(kind="reword", why="The line says who it serves and not what it runs on.", line=ids[OWN], requirement=REQ_PYTHON, posting_phrase="own Python inference services"),
        AssessmentSuggestion(kind="keyword", why="The posting says Terraform modules; the line does too, keep it.", line=ids[TERRAFORM], requirement=REQ_TERRAFORM),
    ])


def _invoke(fx: PipelineFixture, *args: str):
    return CliRunner().invoke(scout_group, [*args, "--home", str(fx.home_root), "--target", str(fx.target), "--json"])


def _ok(fx: PipelineFixture, *args: str) -> dict:
    result = _invoke(fx, *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def _refused(fx: PipelineFixture, *args: str) -> dict[str, object]:
    result = _invoke(fx, *args)
    assert result.exit_code == 1, result.output
    return json.loads(result.output.strip().splitlines()[-1])["error"]


def _list(fx: PipelineFixture, *more: str) -> dict:
    return _ok(fx, "suggestions", "list", "--job-url", JOB, *more)


def _by_id(body: dict) -> dict[str, dict]:
    return {item["id"]: item for item in body["suggestions"]}


def _file(tmp_path: Path, markdown: str) -> str:
    path = tmp_path / "handed-back.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path)


def _stored(fx: PipelineFixture):
    return list_tailored_resumes(fx.home_root, fx.target, profile_id=fx.profile_id, job_identity=JOB)


# --- add, resolve, dismiss, list ----------------------------------------------------------------------------------


def test_a_job_assessed_before_0_1_11_has_no_record_and_says_so(fx: PipelineFixture) -> None:
    error = _refused(fx, "suggestions", "list", "--job-url", JOB)
    assert error["code"] == "suggestions_not_found" and "gigai scout jobs assess URL --again" in str(error["message"])
    assert _refused(fx, "suggestions", "dismiss", "sg-1", "--job-url", JOB)["code"] == "suggestions_not_found"
    assert _refused(fx, "suggestions", "list", "--job-url", "https://jobs.example.test/acme/never-assessed")["code"] == "assessment_missing"
    assert not _path(fx).exists()


def test_add_resolve_dismiss_and_list_record_who_and_how_and_a_refused_change_writes_nothing(fx: PipelineFixture) -> None:
    _seed(fx)
    ids = _ids(fx)
    first = _list(fx)
    assert first["schema_version"] == "scout-job-suggestions-response:1" and first["profile_id"] == fx.profile_id and first["job_identity"] == JOB
    assert first["counts"] == {"open": 2, "done": 0, "dismissed": 0}
    assert [(item["id"], item["source"], item["status"]) for item in first["suggestions"]] == [("sg-1", "assessment", "open"), ("sg-2", "assessment", "open")]

    added = _ok(fx, "suggestions", "add", "--job-url", JOB, "--kind", "order", "--line", ids[OWN], "--why", "Lead with the inference line.", "--as", "agent")
    assert added["added"] == "sg-3"
    assert _by_id(added)["sg-3"] == {
        "id": "sg-3", "kind": "order", "line": ids[OWN], "requirement": None, "posting_phrase": None, "why": "Lead with the inference line.",
        "source": "agent", "created_at": _by_id(added)["sg-3"]["created_at"], "status": "open", "resolved": None,
    }
    mine = _ok(fx, "suggestions", "add", "--job-url", JOB, "--kind", "gap", "--requirement", REQ_GCP, "--why", "Say which GCP services.", "--posting-phrase", "GCP experience is a plus")
    assert mine["added"] == "sg-4" and _by_id(mine)["sg-4"]["source"] == "operator" and _by_id(mine)["sg-4"]["posting_phrase"] == "GCP experience is a plus"

    # A refused change writes nothing.
    kept = _path(fx).read_bytes()
    add = ("suggestions", "add", "--job-url", JOB, "--kind", "reword")
    assert _refused(fx, *add, "--line", "b-nosuch", "--why", "Say it plainly.")["code"] == "unknown_line"
    assert _refused(fx, *add, "--requirement", "req-00ffff", "--why", "Say it plainly.")["code"] == "unknown_requirement"
    assert _refused(fx, *add, "--line", ids[OWN], "--why", "Ask zora.quillfeather@zq.example.invalid about it.")["code"] == "personal_info_refused"
    assert _refused(fx, *add, "--why", "It names nothing.")["code"] == "invalid_value"
    assert _refused(fx, *add, "--line", ids[OWN], "--why", "x" * 301)["code"] == "invalid_value"
    assert _refused(fx, "suggestions", "resolve", "sg-9", "--job-url", JOB, "--how", "answer", "--ref", "cloud:gcp")["code"] == "suggestion_not_found"
    assert _refused(fx, "suggestions", "dismiss", "the-first", "--job-url", JOB)["code"] == "invalid_value"
    assert _path(fx).read_bytes() == kept

    resolved = _ok(fx, "suggestions", "resolve", "sg-3", "--job-url", JOB, "--how", "master_line", "--ref", ids[OWN], "--as", "agent")
    assert resolved["resolved"] == "sg-3"
    done = _by_id(resolved)["sg-3"]
    assert done["status"] == "done" and done["resolved"] == {"by": "agent", "at": done["resolved"]["at"], "how": "master_line", "ref": ids[OWN]}
    answered = _ok(fx, "suggestions", "resolve", "sg-2", "--job-url", JOB, "--how", "answer", "--ref", "cloud:gcp")
    assert _by_id(answered)["sg-2"]["resolved"]["how"] == "answer" and _by_id(answered)["sg-2"]["resolved"]["by"] == "operator"
    dismissed = _ok(fx, "suggestions", "dismiss", "sg-4", "--job-url", JOB, "--as", "agent")
    assert dismissed["dismissed"] == "sg-4"
    gone = _by_id(dismissed)["sg-4"]
    assert gone["status"] == "dismissed" and gone["resolved"]["how"] == "dismissed" and gone["resolved"]["by"] == "agent" and gone["resolved"]["ref"] is None

    # Resolving is recorded, never a delete; the list filters by state; an id is never used twice.
    everything = _list(fx)
    assert everything["counts"] == {"open": 1, "done": 2, "dismissed": 1} and list(_by_id(everything)) == ["sg-1", "sg-2", "sg-3", "sg-4"]
    assert list(_by_id(_list(fx, "--status", "open"))) == ["sg-1"] and list(_by_id(_list(fx, "--status", "dismissed"))) == ["sg-4"]
    assert _ok(fx, "suggestions", "add", "--job-url", JOB, "--kind", "keyword", "--line", ids[TERRAFORM], "--why", "Keep the word modules.")["added"] == "sg-5"
    plain = CliRunner().invoke(scout_group, ["suggestions", "list", "--job-url", JOB, "--home", str(fx.home_root), "--target", str(fx.target)])
    assert plain.exit_code == 0 and "Suggestions: 2 open, 2 done, 1 dismissed." in plain.output
    assert f"sg-3  order  line {ids[OWN]}  done (agent)  master_line by agent -> {ids[OWN]}" in plain.output
    assert fx.model.calls == 0


def test_a_new_assessment_replaces_only_its_own_open_suggestions(fx: PipelineFixture) -> None:
    _seed(fx)
    ids = _ids(fx)
    _ok(fx, "suggestions", "resolve", "sg-2", "--job-url", JOB, "--how", "job_resume_edit", "--as", "agent")  # the assessment's, done
    _ok(fx, "suggestions", "add", "--job-url", JOB, "--kind", "order", "--line", ids[OWN], "--why", "Lead with the inference line.", "--as", "agent")  # sg-3, open
    _ok(fx, "suggestions", "add", "--job-url", JOB, "--kind", "gap", "--requirement", REQ_GCP, "--why", "Say which GCP services.")  # sg-4
    _ok(fx, "suggestions", "dismiss", "sg-4", "--job-url", JOB)

    # The job is assessed again: the new assessment brings one suggestion of its own.
    _assessed(fx, [AssessmentSuggestion(kind="master_line", why="An answer states GCP and no line of the master does.", requirement=REQ_GCP)], now=LATER)

    after = _list(fx)
    listed = [(item["id"], item["source"], item["status"]) for item in after["suggestions"]]
    assert listed == [("sg-2", "assessment", "done"), ("sg-3", "agent", "open"), ("sg-4", "operator", "dismissed"), ("sg-5", "assessment", "open")]
    assert "sg-1" not in _by_id(after), "the assessment's own open suggestion is replaced by the new assessment's"
    assert _by_id(after)["sg-2"]["resolved"]["how"] == "job_resume_edit" and _by_id(after)["sg-3"]["why"] == "Lead with the inference line."
    assert _by_id(after)["sg-5"]["kind"] == "master_line" and after["updated_at"] == LATER
    # A suggestion id is never used twice: the next one is sg-6, not the freed sg-1.
    assert _ok(fx, "suggestions", "add", "--job-url", JOB, "--kind", "order", "--line", ids[OWN], "--why", "Then the Terraform line.")["added"] == "sg-6"


# --- the hand-back names the suggestions it settles ---------------------------------------------------------------


def test_store_resolves_the_named_suggestions_and_checks_the_evidence_again(fx: PipelineFixture, tmp_path: Path) -> None:
    _seed(fx)
    ids = _ids(fx)
    store = ("resume", "store", "--job-url", JOB, "--as", "agent")

    # A name the job does not have stores nothing.
    unknown = _refused(fx, *store, "--in", _file(tmp_path, MASTER), "--resolves", "sg-1,sg-9")
    assert unknown["code"] == "suggestion_not_found" and "sg-9" in str(unknown["message"])
    assert _stored(fx) == () and _list(fx)["counts"] == {"open": 2, "done": 0, "dismissed": 0}

    stored = _ok(fx, *store, "--in", _file(tmp_path, MASTER), "--resolves", "sg-1")
    assert stored["changed"] is True and stored["suggestions_error"] is None
    assert stored["suggestions"]["resolved"] == ["sg-1"] and stored["suggestions"]["counts"] == {"open": 1, "done": 1, "dismissed": 0}
    assert stored["suggestions"]["gate"] == {"decision": "suggest", "ready": True, "reasons": []}  # every must-have keeps a line
    done = _by_id(_list(fx))["sg-1"]
    assert done["status"] == "done" and done["resolved"]["how"] == "job_resume_edit" and done["resolved"]["by"] == "agent" and done["resolved"]["ref"] is None

    # The hand-back drops the only line behind a must-have: it is stored (the resume is the user's) and reads "needs attention".
    without = MASTER.replace(f"- {OWN}\n", "")
    dropped = _ok(fx, *store, "--in", _file(tmp_path, without))
    assert dropped["changed"] is True and OWN not in _stored(fx)[0].markdown
    assert dropped["suggestions"]["gate"] == {"decision": "suggest", "ready": False, "reasons": [{"code": "lost_mandatory_evidence", "requirement": REQ_PYTHON}]}
    record = sg.read_record(_path(fx))
    assert {row.id: row.coverage for row in record.requirements} == {REQ_PYTHON: "lost", REQ_TERRAFORM: "kept", REQ_GCP: "answer_only"}
    view = _ok(fx, "resume", "pick", "--job-url", JOB)
    assert view["gate"]["ready"] is False and view["resume"]["replaceable"] is False and view["picked"]["picked_by"] == "model"

    # A reworded line counts for the master line it cites: the evidence is shown again.
    reworded = MASTER.replace(f"- {OWN}", f"- {REWORDED} <!-- src: {ids[OWN]} -->")
    back = _ok(fx, *store, "--in", _file(tmp_path, reworded))
    assert back["suggestions"]["gate"] == {"decision": "suggest", "ready": True, "reasons": []}
    assert {row.id: row.in_resume for row in sg.read_record(_path(fx)).requirements}[REQ_PYTHON] == (ids[OWN],)
    # The brief of the job says the same, by id.
    brief = _ok(fx, "resume", "brief", "--job-url", JOB)
    assert brief["state"]["gate"]["ready"] is True and brief["state"]["picked"] == {"picked_by": "model", "fallback": None, "draft": False}
    assert {"id": REQ_PYTHON, "class": "hard", "status": "met", "sources": [ids[OWN]], "in_resume": [ids[OWN]], "coverage": "kept", "question_id": None} in brief["requirements"]
    by_id = {item["id"]: item for item in brief["suggestions"]}
    assert by_id["sg-1"]["why"] is None and by_id["sg-1"]["status"] == "done" and by_id["sg-2"]["status"] == "open"
    posting = _ok(fx, "resume", "brief", "--job-url", JOB, "--posting")
    assert {"id": "sg-1", "posting_phrase": "own Python inference services", "why": "The line says who it serves and not what it runs on."} in posting["suggestions"]


def _conflicted(fx: PipelineFixture) -> None:
    """A record whose selection recorded that the page limit cut the only line behind a must-have (SPEC 3.3)."""

    ids = _ids(fx)
    conflict = {"code": "mandatory_evidence_does_not_fit", "requirement": REQ_PYTHON, "lines": [ids[OWN]], "cut": True}
    _assessed(fx, [], selection={**SELECTION, "conflicts": [conflict]})


def test_the_check_is_recomputed_from_the_stored_resume_a_fixed_hand_back_reads_ready_again(fx: PipelineFixture, tmp_path: Path) -> None:
    _conflicted(fx)
    ids = _ids(fx)
    store = ("resume", "store", "--job-url", JOB, "--as", "agent")
    lost = _ok(fx, *store, "--in", _file(tmp_path, MASTER.replace(f"- {OWN}\n", "")))
    reasons = lost["suggestions"]["gate"]["reasons"]
    assert lost["suggestions"]["gate"]["ready"] is False and {item["code"] for item in reasons} == {"lost_mandatory_evidence", "selection_conflict"}
    assert len(_ok(fx, "resume", "pick", "--job-url", JOB)["conflicts"]) == 1

    # The hand-back brings the line back (reworded, citing it): the conflict is gone, the gate reads ready.
    back = _ok(fx, *store, "--in", _file(tmp_path, MASTER.replace(f"- {OWN}", f"- {REWORDED} <!-- src: {ids[OWN]} -->")))
    assert back["suggestions"]["gate"] == {"decision": "suggest", "ready": True, "reasons": []}
    view = _ok(fx, "resume", "pick", "--job-url", JOB)
    assert view["gate"]["ready"] is True and view["conflicts"] == []
    assert sg.read_record(_path(fx)).selection["conflicts"] == []


def test_use_proposed_recomputes_the_check_from_the_stored_resume_too(fx: PipelineFixture, tmp_path: Path) -> None:
    _seed(fx)
    _ok(fx, "resume", "store", "--job-url", JOB, "--as", "agent", "--in", _file(tmp_path, MASTER))
    (stored,) = _stored(fx)
    ids = _ids(fx)
    # The waiting selection recorded a conflict about a line the stored resume prints: taking it leaves no conflict.
    sibling = sg.proposed_resume_path(_path(fx))
    conflict = {"code": "mandatory_evidence_does_not_fit", "requirement": REQ_PYTHON, "lines": [ids[OWN]], "cut": True}
    proposed = {
        **SELECTION, "conflicts": [conflict], "resume": {"stored_path": str(sibling), "markdown_sha256": "sha256:" + "0" * 64, "origin": "pick"},
        "against": sg.revision_of(stored),  # made beside the resume that is stored (0.1.11.4 E1)
    }
    _assessed(fx, [], now=LATER, proposed=proposed)
    sibling.write_text(json.dumps(stored.to_json(), indent=2, sort_keys=True), encoding="utf-8")
    taken = _ok(fx, "resume", "pick", "--job-url", JOB, "--use-proposed")
    assert taken["conflicts"] == [] and taken["gate"]["ready"] is True


# --- a proposal beside a resume the user edited ----------------------------------------------------------------------


def _propose(fx: PipelineFixture, **more: object) -> Path:
    """A new selection waiting beside the stored resume, as a re-assessment leaves it for a resume that is the user's."""

    (stored,) = _stored(fx)
    sibling = sg.proposed_resume_path(_path(fx))
    record = _assessed(fx, [], now=LATER, proposed={**SELECTION, "picked_by": "code", "fallback": "no_pick", "made_at": LATER, "resume": {"stored_path": str(sibling), "markdown_sha256": "sha256:" + "0" * 64, "origin": "pick"}, "against": sg.revision_of(stored)}, **more)
    sibling.write_text(json.dumps(stored.to_json(), indent=2, sort_keys=True), encoding="utf-8")
    assert record.proposed is not None
    return sibling


def test_pick_drops_or_takes_the_proposed_resume_and_nothing_else_replaces_an_edited_one(fx: PipelineFixture, tmp_path: Path) -> None:
    _seed(fx)
    _ok(fx, "resume", "store", "--job-url", JOB, "--as", "agent", "--in", _file(tmp_path, MASTER))
    (edited,) = _stored(fx)
    calls = fx.model.calls  # the pipeline's own re-check of the edited resume; the pick steps below add none
    assert _refused(fx, "resume", "pick", "--job-url", JOB, "--use-proposed")["code"] == "no_proposed_resume"
    assert _refused(fx, "resume", "pick", "--job-url", JOB, "--dismiss-proposed")["code"] == "no_proposed_resume"

    sibling = _propose(fx)
    waiting = _ok(fx, "resume", "pick", "--job-url", JOB)
    assert waiting["proposed"]["picked_by"] == "code" and waiting["proposed"]["made_at"] == LATER and waiting["resume"]["replaceable"] is False
    assert _ok(fx, "resume", "brief", "--job-url", JOB)["state"]["proposed"] is True
    assert _stored(fx) == (edited,), "a proposal waits: the edited resume stays as it is"

    dropped = _ok(fx, "resume", "pick", "--job-url", JOB, "--dismiss-proposed")
    assert dropped["action"] == "dismiss_proposed" and dropped["proposed"] is None and not sibling.exists()
    assert _stored(fx) == (edited,) and sg.read_record(_path(fx)).proposed is None

    sibling = _propose(fx)
    taken = _ok(fx, "resume", "pick", "--job-url", JOB, "--use-proposed")
    assert taken["action"] == "use_proposed" and taken["proposed"] is None and not sibling.exists()
    record = sg.read_record(_path(fx))
    assert record.proposed is None and record.selection["picked_by"] == "code" and record.selection["resume"]["origin"] == "pick"
    assert taken["picked"]["picked_by"] == "code" and taken["picked"]["fallback"] == "no_pick"
    (now_stored,) = _stored(fx)
    assert now_stored.updated_at != edited.updated_at and now_stored.markdown == edited.markdown
    assert fx.model.calls == calls, "no pick step calls a model"


# --- the OPEN read: what the page reads, from the stored records, writing nothing ---------------------------------------


def _store_snapshot(fx: PipelineFixture) -> dict[str, bytes]:
    return {str(path.relative_to(fx.home_root)): path.read_bytes() for path in sorted(fx.home_root.rglob("*")) if path.is_file() and ".git" not in path.parts and "cache" not in path.parts}


def test_opening_a_job_serves_stale_pages_conflicts_proposed_and_rows_and_writes_nothing(fx: PipelineFixture, tmp_path: Path) -> None:
    _conflicted(fx)
    ids = _ids(fx)
    _ok(fx, "resume", "store", "--job-url", JOB, "--as", "agent", "--in", _file(tmp_path, MASTER.replace(f"- {OWN}\n", "")))
    conflict = {"code": "mandatory_evidence_does_not_fit", "requirement": REQ_PYTHON, "lines": [ids[OWN]], "cut": True}
    sibling = _propose(fx, selection={**SELECTION, "conflicts": [conflict]})
    _list(fx)  # one read first: a scratch cache may be written by the first read of a home
    before, calls = _store_snapshot(fx), fx.model.calls

    body = _list(fx)
    pick = _ok(fx, "resume", "pick", "--job-url", JOB)
    assert _store_snapshot(fx) == before and fx.model.calls == calls, "opening writes nothing and calls no model"

    for view in (body, pick):
        assert view["picked"]["pages"] == 1 and view["picked"]["max_pages"] == 2 and view["picked"]["picked_by"] == "model"
        assert view["conflicts"][0]["code"] == "mandatory_evidence_does_not_fit" and view["conflicts"][0]["lines"] == [ids[OWN]]
        assert isinstance(view["stale"], list)
        assert view["proposed"]["picked_by"] == "code" and view["proposed"]["pages"] == 1 and view["proposed"]["lines"] == []
        assert isinstance(view["selected_lines"], list)
        rows = {row["id"]: row for row in view["requirements"]}
        assert set(rows) == {REQ_PYTHON, REQ_TERRAFORM, REQ_GCP}
        assert set(rows[REQ_PYTHON]) == {"id", "class", "status", "sources", "in_resume", "coverage", "question_id"} and rows[REQ_PYTHON]["class"] == "hard"
    assert sibling.exists()
