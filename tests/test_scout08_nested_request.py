"""SCOUT-08 request nested-shape validation before Plan publication."""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai import external_recording
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.private_records import import_run_input
from gigai.scout_tailoring import ScoutTailoringError, validate_tailoring_request
from tests.test_scout07_discovery_run_flow import _envelope, _fixture
from tests.test_scout08_run_integration import _journal_state, _request


POSTING = b"Python required in Denver\n"
CANDIDATE = b"Built reliable Python tools\n"


def _valid_request() -> dict[str, object]:
    return _request(outputs=["resume"], posting=POSTING, candidate=CANDIDATE)


def _mutated_request(kind: str) -> dict[str, object]:
    request = _valid_request()
    if kind == "posting":
        request["requirements"][0]["posting_ref"] = {"unexpected_nested_key": "accepted"}
    elif kind == "candidate":
        ref = dict(request["requirements"][0]["candidate_evidence_refs"][0])
        ref["unexpected_nested_key"] = "accepted"
        request["requirements"][0]["candidate_evidence_refs"] = [ref]
    elif kind == "claim":
        ref = dict(request["claim_evidence"][0]["evidence_refs"][0])
        ref["quote_sha256"] = "sha256:bad"
        request["claim_evidence"][0]["evidence_refs"] = [ref]
    elif kind == "gap":
        request["gaps"] = [{
            "item_id": "gap-source",
            "topic": "candidate evidence",
            "detail": "A bounded gap",
            "source_refs": [{"source_id": "candidate", "start_byte": 0, "end_byte": 6, "quote_sha256": digest_imported_bytes(CANDIDATE[:6]), "unexpected_nested_key": "accepted"}],
        }]
    elif kind == "question":
        request["questions"] = [{
            "item_id": "question-source",
            "prompt": "Can you provide evidence?",
            "reason": "The supplied claim is incomplete.",
            "source_refs": [{"source_id": "candidate", "start_byte": False, "end_byte": 6, "quote_sha256": digest_imported_bytes(CANDIDATE[:6])}],
        }]
    elif kind == "assessment":
        request["requirements"][0]["assessment"] = []
    else:  # pragma: no cover - parameter table is closed
        raise AssertionError(kind)
    return request


@pytest.mark.parametrize("kind", ["posting", "candidate", "claim", "gap", "question", "assessment"])
def test_nested_request_shapes_refuse_before_renderer(kind: str) -> None:
    with pytest.raises(ScoutTailoringError) as refused:
        validate_tailoring_request(canonical_json_bytes(_mutated_request(kind)))
    assert refused.value.code == "tailoring_input_invalid"


@pytest.mark.parametrize("kind", ["posting", "candidate", "claim", "gap", "question", "assessment"])
def test_nested_request_shapes_refuse_public_plan_without_publication(tmp_path: Path, kind: str) -> None:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    posting = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=POSTING, label="posting")
    candidate = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=CANDIDATE, label="resume")
    request = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(_mutated_request(kind)), label=f"tailoring-request-{kind}")
    refs = [{"family": "g45_run_input", "id": item.item_id} for item in (posting, candidate, request)]
    before = _journal_state(home, target, gig_id)
    with pytest.raises(external_recording.ExternalRecordingError) as refused:
        external_recording.plan_v2(
            home_root=home,
            requested_target=target,
            gig_id=gig_id,
            envelope=_envelope(
                f"nested-request-{kind}",
                {
                    "graph_selector": "tailor-application",
                    "gig_version": None,
                    "selection_record": None,
                    "input_refs": refs,
                    "output_kinds": ["tailoring"],
                    "predecessor": None,
                },
            ),
        )
    after = _journal_state(home, target, gig_id)
    assert refused.value.code == "tailoring_input_invalid"
    assert after == before


@pytest.mark.parametrize("field", ["assessment", "status", "document_kind"])
@pytest.mark.parametrize("replacement", [None, False, 1, {}, [], "not-an-enum"], ids=["null", "bool", "number", "object", "array", "string"])
def test_nested_enum_json_types_refuse_without_type_error(field: str, replacement: object) -> None:
    request = _valid_request()
    if field == "assessment":
        request["requirements"][0]["assessment"] = replacement
    elif field == "status":
        request["claim_evidence"][0]["status"] = replacement
    else:
        request["requirements"][0]["draft_evidence_refs"][0]["document_kind"] = replacement
    with pytest.raises(ScoutTailoringError) as refused:
        validate_tailoring_request(canonical_json_bytes(request))
    assert refused.value.code == "tailoring_input_invalid"


@pytest.mark.parametrize("kind", ["offset", "digest", "limit", "duplicate"])
def test_nested_reference_bounds_and_identity_guards(kind: str) -> None:
    request = _valid_request()
    if kind == "offset":
        request["requirements"][0]["posting_ref"]["end_byte"] = 1_048_577
    elif kind == "digest":
        request["requirements"][0]["posting_ref"]["quote_sha256"] = "sha256:bad"
    elif kind == "limit":
        ref = request["requirements"][0]["candidate_evidence_refs"][0]
        request["requirements"][0]["candidate_evidence_refs"] = [dict(ref) for _ in range(129)]
    else:
        ref = request["requirements"][0]["candidate_evidence_refs"][0]
        request["requirements"][0]["candidate_evidence_refs"] = [dict(ref), dict(ref)]
    with pytest.raises(ScoutTailoringError) as refused:
        validate_tailoring_request(canonical_json_bytes(request))
    assert refused.value.code == "tailoring_input_invalid"


def test_valid_nested_request_seals_plan_and_starts_run(tmp_path: Path) -> None:
    home, target, gig_id, _workpad = _fixture(tmp_path)
    posting = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=POSTING, label="posting")
    candidate = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=CANDIDATE, label="resume")
    request = import_run_input(home_root=home, requested_target=target, gig_id=gig_id, data=canonical_json_bytes(_valid_request()), label="tailoring-request")
    plan = external_recording.plan_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope(
            "nested-request-valid",
            {
                "graph_selector": "tailor-application",
                "gig_version": None,
                "selection_record": None,
                "input_refs": [{"family": "g45_run_input", "id": item.item_id} for item in (posting, candidate, request)],
                "output_kinds": ["tailoring"],
                "predecessor": None,
            },
        ),
    )
    started = external_recording.start_v2(
        home_root=home,
        requested_target=target,
        gig_id=gig_id,
        envelope=_envelope("nested-request-valid-start", {"run_plan_id": plan.payload["run_plan_id"]}),
    )
    assert plan.created and started.created
    assert plan.payload["tailoring_request"]["input_index"] == 2
