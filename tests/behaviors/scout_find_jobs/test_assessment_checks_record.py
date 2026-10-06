"""0.1.11.4 OBS: a stored v9 assessment keeps COUNTS of what the code checks settled or dropped (``checks``).

Offline, synthetic. The unit half reads the counts off a scripted answer (A2 settled rows, A1 dropped suggestions and
cleaned citations); the flow half stores a v9 assessment end to end and reads it back. Counts and kinds only: no
requirement, master or resume text and no row id; an assessment stored before the field has no ``checks`` key at all.
"""

from __future__ import annotations

import json

from gigai.canonical import parse_json_bytes
from gigai.scout.find_jobs.assess_contracts import AssessChecks, AssessResponse
from gigai.scout.quick_assess import _checks_of

from tests.behaviors.scout_find_jobs import test_assessment_v9_flow as flow
from tests.behaviors.scout_find_jobs import test_evidence_suggestion_location_checks as evidence
from tests.behaviors.scout_find_jobs import test_stated_questions_end_outcome as stated

fx = flow.fx  # the stored-master postings fixture


def test_settled_rows_and_dropped_suggestions_are_counted_exactly() -> None:
    attempt = stated._assess([stated.GO_ROW, stated._row("Experience with React"), stated._row("Strong Kubernetes experience")], [stated._question("Experience with React", "react")])
    checks = _checks_of(attempt.extras)
    assert checks is not None
    assert (checks.settled_rows, checks.settled_by_rule, checks.questions_dropped) == (2, (("master_line", 1), ("skills_line", 1)), 1)

    answer = evidence._answer(
        [evidence._row("Operate services", "met", ["b-000001"], ["x"], "hard"), evidence._row("Billing APIs", "met", ["b-000004"], ["y"])],
        [
            {"kind": "reword", "why": "Lead with it.", "line": "b-000001", "requirement": "Operate services", "posting_phrase": "on-call"},
            {"kind": "reword", "why": "Lead with it.", "line": "b-000004", "requirement": "Operate services", "posting_phrase": "billing"},
            {"kind": "keyword", "why": "Say it.", "line": "b-000001", "requirement": "Operate services", "posting_phrase": "incident command"},
        ],
    )
    dropped = _checks_of(evidence._assess(answer).extras)
    assert dropped is not None and dropped.suggestions_dropped == (("line_not_a_source_of_row", 1), ("phrase_not_in_line", 1))
    assert dropped.settled_rows == 0 and dropped.questions_dropped == 0


def test_no_extras_means_no_record() -> None:
    assert _checks_of(None) is None


def test_the_record_round_trips_and_an_old_assessment_has_no_key() -> None:
    checks = AssessChecks(2, (("master_line", 2),), 1, 3, (("phrase_not_in_line", 1),), (("trimmed_to_line", 4),))
    assert AssessChecks.from_json(checks.to_json()) == checks
    assert AssessChecks.from_json(json.loads(json.dumps(checks.to_json()))).to_json() == checks.to_json()


def test_a_stored_v9_assessment_carries_the_counts_and_no_text(fx) -> None:
    stored, _prompt = flow._assess(fx, flow._v9_answer(fx))
    assert stored.checks is not None and stored.checks.settled_rows == 0 and stored.checks.suggestions_dropped == ()
    written = flow._assessment_path(fx).read_bytes()
    value = parse_json_bytes(written)
    assert value["checks"] == stored.checks.to_json()
    assert AssessResponse.from_json(value).checks == stored.checks
    assert json.dumps(AssessResponse.from_json(value).to_json(), indent=2, sort_keys=True).encode("utf-8") == written
    # No text: only the fixed key names, integers, and rule/reason/action names.
    texts = [*flow.REQUIREMENTS, flow.PYTHON_LINE, flow.KUBERNETES_LINE, flow.TERRAFORM_LINE, flow.OWN_LINE]
    assert all(text not in json.dumps(value["checks"]) for text in texts)
    stripped = {key: item for key, item in value.items() if key != "checks"}
    assert "checks" not in AssessResponse.from_json(stripped).to_json()  # an assessment from before the field


def test_a_v8_answer_stores_no_checks(fx) -> None:
    stored, _prompt = flow._assess(fx, flow._v8_answer())
    assert stored.checks is None and "checks" not in parse_json_bytes(flow._assessment_path(fx).read_bytes())
