"""0.1.11 N3 (SPEC section 2): the suggestion record, the final-selection check and the stale list. Pure parts.

No model, no home and no layout here: the record, the check and the stale list are functions over ids.  The store
(the job resume and the record written together, ``proposed``, opening a job writes nothing) is exercised end to end
in ``test_assessment_v9_flow.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gigai.canonical import parse_json_bytes
from gigai.scout import suggestions as sg
from gigai.scout.find_jobs.assess_contracts import AssessmentSuggestion, GateReason, GateRecord
from gigai.scout.find_jobs.contracts import MatrixStatus, RequirementClass, RequirementMatrixRow

DIGEST = "sha256:" + "a" * 64
NOW, LATER = "2026-10-05T10:00:00Z", "2026-10-06T09:00:00Z"
GO, KAFKA, HELM, CLOUD, MONGO, OPEN = (f"req-00000{number}" for number in range(1, 7))


def _row(row_id: str, klass: str, status: str, *sources: str) -> sg.RequirementRow:
    return sg.RequirementRow(row_id, klass, status, tuple(sources))


ROWS = (
    _row(GO, "hard", "met", "b-go1", "b-go2", "A tooling:go"),
    _row(KAFKA, "askable", "met", "b-kafka"),
    _row(HELM, "list_item", "met", "b-helm"),
    _row(CLOUD, "askable", "met", "A cloud:gcp", "A story:gcp-move"),
    _row(MONGO, "askable", "met"),
    _row(OPEN, "askable", "unclear", "b-open"),
)


# --- the requirement rows, by id ---------------------------------------------------------------------------------


def test_the_matrix_is_reduced_to_ids_and_an_old_row_gets_its_place() -> None:
    matrix = [
        RequirementMatrixRow("Go in production", ("Built Go services",), MatrixStatus.MET, RequirementClass.HARD, id=GO, sources=("b-go1", "A tooling:go")),
        RequirementMatrixRow("Helm", (), MatrixStatus.UNCLEAR, RequirementClass.LIST_ITEM),
        {"requirement": "An old shape", "status": "unmet", "resume_evidence": []},
    ]
    rows = sg.requirement_rows(matrix)
    assert rows == (sg.RequirementRow(GO, "hard", "met", ("b-go1", "A tooling:go")), sg.RequirementRow("r2", "list_item", "unclear"), sg.RequirementRow("r3", None, "unmet"))
    assert [row.mandatory for row in rows] == [True, False, True]  # a row with no class is an old one and reads as a must-have
    assert rows[0].master_sources() == ("b-go1",) and sg.is_answer_source("A story:gcp-move") and not sg.is_answer_source("b-go1")


# --- the final-selection check (2.3) --------------------------------------------------------------------------------


def test_each_met_row_is_kept_lost_answer_only_or_without_a_source() -> None:
    check = sg.check_selection(ROWS, ["b-go2", "b-other", "b-open"])
    by_id = {row.id: row for row in check.rows}
    assert (by_id[GO].coverage, by_id[GO].in_resume) == ("kept", ("b-go2",))
    assert (by_id[KAFKA].coverage, by_id[KAFKA].in_resume) == ("lost", ())
    assert by_id[HELM].coverage == "lost" and by_id[CLOUD].coverage == "answer_only" and by_id[MONGO].coverage == "none"
    assert by_id[OPEN].coverage is None and by_id[OPEN].in_resume == ()  # a row that is not met has no coverage to lose
    # Only a MANDATORY row that is lost makes the resume not ready; the optional one is listed and holds nothing.
    assert not check.ready and [reason.to_json() for reason in check.reasons] == [{"code": "lost_mandatory_evidence", "requirement": KAFKA}]
    assert check.lost() == (KAFKA,) and check.answer_only() == (CLOUD,)
    assert by_id[GO].to_json() == {"id": GO, "class": "hard", "status": "met", "sources": ["b-go1", "b-go2", "A tooling:go"], "in_resume": ["b-go2"], "coverage": "kept"}
    assert sg.CoverageRow.from_json(by_id[CLOUD].to_json()) == by_id[CLOUD]


def test_the_resume_is_ready_when_every_mandatory_row_keeps_a_line_and_nothing_conflicts() -> None:
    check = sg.check_selection(ROWS, ["b-go1", "b-kafka"])
    assert check.ready and check.reasons == () and check.lost() == ()
    assert {row.id: row.coverage for row in check.rows}[HELM] == "lost"
    conflict = SimpleNamespace(code="mandatory_evidence_does_not_fit", requirement=GO)
    pinned = SimpleNamespace(code="pinned_line_does_not_fit", requirement=None)
    held = sg.check_selection(ROWS, ["b-go1", "b-kafka"], conflicts=[conflict, pinned])
    assert not held.ready and [reason.to_json() for reason in held.reasons] == [
        {"code": "selection_conflict", "requirement": GO}, {"code": "selection_conflict", "requirement": None},
    ]
    assert sg.check_selection((), ()).ready  # nothing to lose


def test_the_page_count_is_never_a_reason_a_stored_page_driven_conflict_leaves_the_resume_ready() -> None:
    """0.1.11.5 item 1c: the pick knows no page limit. A selection stored before it may hold a conflict the page made
    (the Skills cut, a heading line cut, still over the limit): it stays in the record and is no reason."""

    assert sg.PAGE_CONFLICTS == {"skills_do_not_fit", "earlier_roles_do_not_fit", "over_page_limit"}
    stored = [SimpleNamespace(code=code, requirement=None) for code in sorted(sg.PAGE_CONFLICTS)]
    check = sg.check_selection(ROWS, ["b-go1", "b-kafka"], conflicts=stored)
    assert check.ready and check.reasons == ()
    # ... and it never hides a real one beside it.
    mixed = sg.check_selection(ROWS, ["b-go1", "b-kafka"], conflicts=[*stored, SimpleNamespace(code="mandatory_evidence_does_not_fit", requirement=GO)])
    assert not mixed.ready and [reason.to_json() for reason in mixed.reasons] == [{"code": "selection_conflict", "requirement": GO}]
    # Read back from a stored record (``live_selection``): the conflicts are kept as stored, and only the real one is a reason.
    selection = {"conflicts": [
        {"code": "skills_do_not_fit", "requirement": None, "lines": [], "cut": True},
        {"code": "earlier_roles_do_not_fit", "requirement": None, "lines": [], "cut": True, "message": "2 older roles are not listed on this resume"},
        {"code": "over_page_limit", "requirement": None, "lines": [], "cut": False},
        {"code": "mandatory_evidence_does_not_fit", "requirement": GO, "lines": ["b-go2"], "cut": True},
    ]}
    kept, reasons = sg.live_selection(selection, ["b-go1", "b-kafka"])
    assert kept["conflicts"] == selection["conflicts"] and [reason.to_json() for reason in reasons] == [{"code": "selection_conflict", "requirement": GO}]
    kept, reasons = sg.live_selection({"conflicts": selection["conflicts"][:3]}, ["b-go1"])
    assert len(kept["conflicts"]) == 3 and reasons == ()


# --- the record ---------------------------------------------------------------------------------------------------------

SELECTION = {
    "picked_by": "model", "fallback": None, "draft": False, "pick_rules_version": "pick-rules:1", "selector_version": "sel-4",
    "made_at": NOW, "made_from": {"result_digest": DIGEST, "master_revision_id": "revision_5"},
    "model_pick": {"summary": "sum-4c1d2e", "section_order": ["projects", "experience"], "lines": ["b-go1", "b-kafka", "b-zzzzzz"]},
    "problems": [{"code": "unknown_id", "id": "b-zzzzzz"}], "added_by_code": [{"id": "b-go2", "code": "added_for_coverage", "requirement": GO}],
    "line_marks": sg.marks_json({"b-go1": "9f0c2a41d7e3b6a8", "b-kafka": "1111222233334444"}), "pages": 2, "max_pages": 2, "conflicts": [],
    "resume": {"stored_path": "/home/scout/project/resumes/profile_1/abc.json", "markdown_sha256": DIGEST, "origin": "pick"},
}
BASIS = {
    "assessment": {"stored_path": "/a.json", "assessed_at": NOW, "result_digest": DIGEST, "prompt_version": "assess-prompt-v9", "instructions_digest": DIGEST,
                   "model": "fixture-model", "constraints_digest": DIGEST, "story_bank_digest": None},
    "posting_sha256": DIGEST, "requirements": {"rules_version": "req-rules:1", "digest": DIGEST, "list": "stored"},
    "master": {"revision_id": "revision_5", "revision": 5, "content_sha256": DIGEST},
}
SUGGESTED = (
    AssessmentSuggestion("reword", "Lead with the cost result.", line="b-go1", requirement=GO, posting_phrase="control cost with prompt caching"),
    AssessmentSuggestion("gap", "Only an answer can close this.", requirement=OPEN),
)


def _record(previous: sg.SuggestionRecord | None = None, *, now: str = NOW, suggested=SUGGESTED, printed=("b-go1", "b-kafka"), **more: object) -> sg.SuggestionRecord:
    check = sg.check_selection(ROWS, printed)
    values = {
        "profile_id": "profile_1", "job_identity": "https://jobs.example.test/1", "stored_path": "/home/scout/project/suggestions/profile_1/abc.json",
        "basis": BASIS, "gate": sg.gate_json(GateRecord("suggest"), check), "requirements": check.rows, "suggested": suggested, "selection": SELECTION,
        **more,
    }
    return sg.merged(previous, now=now, **values)  # type: ignore[arg-type]


def test_the_record_round_trips_through_the_stored_file_reader_and_holds_ids_only() -> None:
    record = _record()
    stored = json.dumps(record.to_json(), indent=2, sort_keys=True).encode("utf-8")
    again = sg.SuggestionRecord.from_json(parse_json_bytes(stored))  # the reader refuses a member name like "b-go1": line_marks is a list
    assert again == record and json.dumps(again.to_json(), indent=2, sort_keys=True).encode("utf-8") == stored
    value = record.to_json()
    assert value["schema_version"] == "scout-job-suggestions:1" and list(value) == [
        "schema_version", "profile_id", "job_identity", "created_at", "updated_at", "stored_path", "basis", "gate", "requirements", "selection",
        "proposed", "suggestions",
    ]
    assert value["gate"] == {"decision": "suggest", "ready": True, "reasons": []} and value["proposed"] is None
    assert record.printed() == ("b-go1", "b-kafka") and sg.recorded_marks(record.selection)["b-go1"] == "9f0c2a41d7e3b6a8"
    # The assessment's two suggestions, then one for the met row only an answer supports (no line of the master states it).
    assert [(item.id, item.kind, item.requirement, item.source, item.status) for item in record.suggestions] == [
        ("sg-1", "reword", GO, "assessment", "open"), ("sg-2", "gap", OPEN, "assessment", "open"), ("sg-3", "master_line", CLOUD, "assessment", "open"),
    ]
    assert record.suggestions[2].why == sg.ANSWER_ONLY_WHY and record.suggestions[0].to_json() == {
        "id": "sg-1", "kind": "reword", "line": "b-go1", "requirement": GO, "posting_phrase": "control cost with prompt caching",
        "why": "Lead with the cost result.", "source": "assessment", "created_at": NOW, "status": "open", "resolved": None,
    }
    with pytest.raises(sg.SuggestionError):
        sg.SuggestionRecord.from_json({**value, "schema_version": "scout-job-suggestions:2"})
    with pytest.raises(sg.SuggestionError):
        sg.SuggestionRecord.from_json({**value, "suggestions": [value["suggestions"][0], value["suggestions"][0]]})


def test_a_new_assessment_replaces_its_own_open_suggestions_and_keeps_every_other_one() -> None:
    record = _record()
    record = sg.add_suggestion(record, kind="keyword", why="The posting says Kubernetes; b-go2 supports it.", source="agent", now=NOW, line="b-go2")
    record = sg.add_suggestion(record, kind="order", why="Move the Kafka line up.", source="operator", now=NOW, line="b-kafka", requirement=KAFKA)
    record = sg.resolve_suggestion(record, "sg-1", by="agent", how="job_resume_edit", now=NOW)
    record = sg.resolve_suggestion(record, "sg-3", by="operator", how="dismissed", now=NOW)
    assert [(item.id, item.status) for item in record.suggestions] == [("sg-1", "done"), ("sg-2", "open"), ("sg-3", "dismissed"), ("sg-4", "open"), ("sg-5", "open")]
    assert record.suggestions[0].resolved == sg.Resolved("agent", NOW, "job_resume_edit")
    new = (AssessmentSuggestion("gap", "Still open.", requirement=OPEN), AssessmentSuggestion("master_line", "An answer states GCP.", requirement=CLOUD))
    after = _record(record, now=LATER, suggested=new, printed=("b-go1",))
    # Kept: what is done or dismissed (whoever wrote it) and what an agent or the operator added. Replaced: sg-2, the assessment's open one.
    assert [(item.id, item.source, item.status, item.kind) for item in after.suggestions] == [
        ("sg-1", "assessment", "done", "reword"), ("sg-3", "assessment", "dismissed", "master_line"), ("sg-4", "agent", "open", "keyword"),
        ("sg-5", "operator", "open", "order"), ("sg-6", "assessment", "open", "gap"), ("sg-7", "assessment", "open", "master_line"),
    ]
    # An id is never used twice in a record; the row an answer supports got no second suggestion (the assessment wrote one).
    assert len({item.id for item in after.suggestions}) == 6 and after.created_at == NOW and after.updated_at == LATER
    # Basis, gate and requirements are the new assessment's: Kafka's line is no longer printed.
    assert after.gate == {"decision": "suggest", "ready": False, "reasons": [{"code": "lost_mandatory_evidence", "requirement": KAFKA}]}
    assert {row.id: row.coverage for row in after.requirements}[KAFKA] == "lost"
    sg.SuggestionRecord.from_json(parse_json_bytes(json.dumps(after.to_json()).encode("utf-8")))


def test_a_suggestion_is_added_by_an_agent_or_the_operator_never_with_a_contact_detail_and_closing_is_recorded() -> None:
    record = _record(suggested=())
    for bad in (
        {"kind": "keyword", "why": "Write to jane.roe@example.com about this line.", "source": "agent", "line": "b-go1"},
        {"kind": "keyword", "why": "Fine words.", "source": "assessment", "line": "b-go1"},
        {"kind": "rewrite", "why": "Fine words.", "source": "agent", "line": "b-go1"},
        {"kind": "keyword", "why": "Fine words.", "source": "agent"},
        {"kind": "keyword", "why": "Fine words.", "source": "agent", "requirement": "the third row"},
    ):
        with pytest.raises(sg.SuggestionError):
            sg.add_suggestion(record, now=NOW, **bad)  # type: ignore[arg-type]
    with pytest.raises(sg.SuggestionError) as missing:
        sg.resolve_suggestion(record, "sg-9", by="agent", how="dismissed", now=NOW)
    assert missing.value.code == "suggestion_not_found"
    with pytest.raises(sg.SuggestionError):
        sg.resolve_suggestion(record, "sg-1", by="agent", how="deleted", now=NOW)
    closed = sg.resolve_suggestion(record, "sg-1", by="operator", how="master_line", now=LATER, ref="b-new001")
    assert closed.suggestions[0].status == "done" and closed.suggestions[0].resolved.to_json() == {"by": "operator", "at": LATER, "how": "master_line", "ref": "b-new001"}
    assert len(closed.suggestions) == len(record.suggestions)  # recorded, never deleted


def test_the_gate_json_is_ready_only_for_a_suggested_resume_that_passed_the_check() -> None:
    passed, failed = sg.check_selection(ROWS, ["b-go1", "b-kafka"]), sg.check_selection(ROWS, ["b-go1"])
    assert sg.gate_json(GateRecord("suggest"), passed) == {"decision": "suggest", "ready": True, "reasons": []}
    assert sg.gate_json(GateRecord("suggest"), failed)["ready"] is False
    assert sg.gate_json(GateRecord("suggest"), None) == {"decision": "suggest", "ready": False, "reasons": []}  # no selection: nothing is ready
    held = sg.gate_json(GateRecord("hold_unmet", (GateReason("askable_unmet", KAFKA),)), passed)
    assert held == {"decision": "hold_unmet", "ready": False, "reasons": [{"code": "askable_unmet", "requirement": KAFKA}]}
    # A change of what the resume prints with no new assessment: the decision stays, ready and the rows are the new check's.
    record = _record()
    changed = sg.with_selection(record, now=LATER, check=failed, selection=SELECTION)
    assert changed.gate == {"decision": "suggest", "ready": False, "reasons": [{"code": "lost_mandatory_evidence", "requirement": KAFKA}]}
    assert sg.with_selection(changed, now=LATER, check=passed, selection=SELECTION).gate == record.gate


# --- the stale list (2.4): derived from what is stored, nothing recomputed ------------------------------------------------

MARKS = {"b-go1": "9f0c2a41d7e3b6a8", "b-kafka": "1111222233334444", "b-unprinted": "aaaa"}
CURRENT = {"result_digest_now": DIGEST, "master_revision_id": "revision_5", "marks_now": MARKS, "pick_rules_version": "pick-rules:1", "selector_version": "sel-4"}


def test_a_selection_made_from_what_is_stored_now_is_not_stale() -> None:
    assert sg.stale(_record(), **CURRENT) == ()
    assert sg.stale(None, **CURRENT) == () and sg.stale(_record(selection=None), **CURRENT) == ()


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"assessment_stale": "older_prompt"}, ("assessment_stale:older_prompt",)),
        ({"assessment_stale": "resume_changed"}, ("assessment_stale:resume_changed",)),
        # A printed line's text changed (another mark), or the line is retired.
        ({"marks_now": {**MARKS, "b-kafka": "ffff000011112222"}, "master_revision_id": "revision_6"}, ("picked_line_changed",)),
        ({"marks_now": {"b-go1": MARKS["b-go1"]}, "master_revision_id": "revision_6"}, ("picked_line_changed",)),
        # The master moved on and no printed line changed: a note, not a warning.
        ({"marks_now": {**MARKS, "b-unprinted": "bbbb", "b-new": "cccc"}, "master_revision_id": "revision_6"}, ("master_newer",)),
        ({"pick_rules_version": "pick-rules:2"}, ("selection_rules_changed",)),
        ({"selector_version": "sel-5"}, ("selection_rules_changed",)),
        ({"result_digest_now": "sha256:" + "b" * 64}, ("assessment_newer",)),
        (
            {"assessment_stale": "story_bank_changed", "marks_now": {}, "selector_version": "sel-5", "result_digest_now": "sha256:" + "b" * 64},
            ("assessment_stale:story_bank_changed", "picked_line_changed", "selection_rules_changed", "assessment_newer"),
        ),
    ],
)
def test_each_stale_code_has_its_one_cause(change: dict[str, object], expected: tuple[str, ...]) -> None:
    assert sg.stale(_record(), **{**CURRENT, **change}) == expected  # type: ignore[arg-type]


def test_a_stale_assessment_is_named_even_without_a_selection() -> None:
    assert sg.stale(_record(selection=None), assessment_stale="settings_changed") == ("assessment_stale:settings_changed",)
    assert sg.stale(None, assessment_stale="older_prompt") == ("assessment_stale:older_prompt",)
