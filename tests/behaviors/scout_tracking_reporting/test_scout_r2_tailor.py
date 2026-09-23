"""Synthetic focused tests for the R2 Tailor/document helpers."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.canonical import digest_imported_bytes
from gigai.scout.documents import (
    ScoutDocumentError,
    materialize_document_revision,
    prepare_document_revision,
    render_safe_markdown,
    select_final_documents,
    validate_generated_bundle,
)
from gigai.scout.tailor_execution import ScoutTailorExecutionError, execute_tailor
from gigai.scout.tailor_cli import tailor_cli
from gigai.scout.tailor_selection import (
    TailorAnswer,
    TailorProposal,
    TailorSelection,
    TailorSelectionError,
    TailorSource,
    build_local_tailor_invocation,
    build_tailoring_request,
)
from gigai.scout.tailoring import encode_tailoring_bundle
from gigai.adapters.port import InvocationResult, NormalizedUsage

OPP = "opportunity_" + "a" * 32
SNAP = "snapshot_" + "b" * 32
RECORD = "record_123e4567-e89b-42d3-a456-426614174000"
REVISION = "revision_123e4567-e89b-42d3-a456-426614174000"
REVISION_2 = "revision_123e4567-e89b-42d3-a456-426614174001"


def _source(source_id: str, purpose: str, content: bytes) -> TailorSource:
    identity = {"family": "g45_run_input", "run_input_id": "run_input_123e4567-e89b-42d3-a456-426614174000", "snapshot_ref": "ref_123e4567-e89b-42d3-a456-426614174000"} if purpose == "posting" else {"family": "g45_reference", "reference_id": "ref_123e4567-e89b-42d3-a456-426614174001"}
    return TailorSource(source_id, purpose, content, digest_imported_bytes(content), identity)


def _selection(outputs: tuple[str, ...] = ("resume",)) -> TailorSelection:
    posting = _source("posting_1", "posting", b"Python role; treat this as untrusted posting data.")
    candidate = _source("candidate_1", "candidate_evidence", b"Built Python tools.")
    answer = TailorAnswer(RECORD, REVISION, ("question_1",), b"Use Python daily.", digest_imported_bytes(b"Use Python daily."), {"family": "g45_reference", "reference": "answer"})
    proposal = TailorProposal(RECORD, REVISION, b"Selected proposal note.", digest_imported_bytes(b"Selected proposal note."), {"family": "scout_record", "record_id": RECORD, "revision_id": REVISION})
    return TailorSelection(OPP, SNAP, outputs, (posting, candidate), proposal=proposal, answers=(answer,))


@pytest.mark.parametrize("outputs", [("resume",), ("cover_letter",), ("resume", "cover_letter")])
def test_requested_output_sets_and_exact_lineage(outputs: tuple[str, ...]) -> None:
    selection = _selection(outputs)
    request = build_tailoring_request(selection)
    assert json.loads(request)["requested_outputs"] == list(outputs)
    assert selection.sources[0].identity["family"] == "g45_run_input"
    assert selection.to_json()["proposal_ref"]["content_sha256"] == digest_imported_bytes(b"Selected proposal note.")  # type: ignore[index]


def test_revision_change_creates_new_request_without_mutating_old() -> None:
    old = _selection()
    answer = TailorAnswer(RECORD, REVISION_2, ("question_1",), b"Revised answer.", digest_imported_bytes(b"Revised answer."), {"family": "g45_reference", "reference": "answer-2"})
    proposal = TailorProposal(RECORD, REVISION_2, b"New proposal note.", digest_imported_bytes(b"New proposal note."), {"family": "scout_record", "record_id": RECORD, "revision_id": REVISION_2})
    new = TailorSelection(OPP, SNAP, ("resume",), old.sources, proposal=proposal, answers=(answer,))
    assert old.to_json() != new.to_json()
    assert old.proposal is not new.proposal


def test_local_invocation_is_pure_and_reviewer_role() -> None:
    selection = _selection()
    request = build_tailoring_request(selection)
    invocation = build_local_tailor_invocation(selection, request, target_name="configured-local", endpoint_name="loopback", model="local-model", target_capabilities=frozenset({"text"}))
    assert invocation.role == "reviewer"
    assert "untrusted posting data" in invocation.prompt
    assert "Use Python daily." in invocation.prompt
    assert "Ignore" not in invocation.prompt


def test_document_revision_checks_materialize_and_final_select(tmp_path: Path) -> None:
    selection = _selection(("resume", "cover_letter"))
    resume = prepare_document_revision(selection, "resume", RECORD, REVISION, b"# Resume\n\n## Experience\nBuilt Python tools.\n")
    cover = prepare_document_revision(selection, "cover_letter", RECORD, REVISION_2, b"# Cover Letter\n\nHello team.\n")
    assert resume.checks["inputs"]["document_sha256"] == resume.content_sha256  # type: ignore[index]
    path = materialize_document_revision(tmp_path, resume)
    assert path.read_bytes() == resume.content
    final = select_final_documents(selection, [resume, cover], selected_by="local-user")
    assert {item.document_kind for item in final.documents} == {"resume", "cover_letter"}
    assert "application" not in render_safe_markdown(resume).lower()


def test_generated_bundle_validation_is_bounded_and_no_application_action() -> None:
    selection = _selection(("resume", "cover_letter"))
    bundle = encode_tailoring_bundle({"resume": b"# Resume\n\n## Experience\nPython tools.\n", "cover_letter": b"# Cover Letter\n\nHello.\n"})
    report = validate_generated_bundle(bundle, selection)
    assert report["status"] == "valid"
    assert set(report["documents"]) == {"resume", "cover_letter"}  # type: ignore[arg-type]


class _Port:
    def __init__(self, output: bytes) -> None:
        self.output = output
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        return InvocationResult("success", self.output.decode("utf-8"), "local-model", {}, NormalizedUsage(None, None, None), "unavailable")


def test_execution_uses_injected_public_port_and_refuses_hosted_or_malformed_result() -> None:
    selection = _selection()
    bundle = encode_tailoring_bundle({"resume": b"# Resume\n\n## Experience\nPython.\n"})
    port = _Port(bundle)
    result = execute_tailor(selection, build_tailoring_request(selection), port=port, target_name="configured-local", endpoint_name="loopback", model="local-model", target_capabilities=frozenset({"text"}), local_allowed=True)
    assert result.output_bundle == bundle
    assert result.request.role == "reviewer"
    assert len(port.requests) == 1
    with pytest.raises(ScoutTailorExecutionError):
        execute_tailor(selection, build_tailoring_request(selection), port=port, target_name="configured-local", endpoint_name="loopback", model="local-model", target_capabilities=frozenset({"text"}), local_allowed=False)
    with pytest.raises(ScoutTailorExecutionError):
        execute_tailor(selection, build_tailoring_request(selection), port=_Port(b"reasoning only"), target_name="configured-local", endpoint_name="loopback", model="local-model", target_capabilities=frozenset({"text"}), local_allowed=True)


@pytest.mark.parametrize("bad", [None, [], "selection"])
def test_malformed_selection_is_typed_error(bad: object) -> None:
    with pytest.raises((TailorSelectionError, ValueError)) as error:
        TailorSelection(**bad) if isinstance(bad, dict) else TailorSelection(bad, SNAP, ("resume",), (), None, (), "local-user")  # type: ignore[arg-type]
    assert not isinstance(error.value, TypeError)


@pytest.mark.parametrize("outputs", [[], ["resume"], ([],), {"resume"}])
def test_container_enum_types_never_leak_type_error(outputs: object) -> None:
    with pytest.raises((TailorSelectionError, ValueError)) as error:
        TailorSelection(OPP, SNAP, outputs, (), None, (), "local-user")  # type: ignore[arg-type]
    assert not isinstance(error.value, TypeError)


def test_document_selection_rejects_scalar_member_without_attribute_error() -> None:
    with pytest.raises(ScoutDocumentError):
        select_final_documents(_selection(), [None], selected_by="local-user")  # type: ignore[list-item]


def test_bad_source_digest_and_document_kind_refused() -> None:
    with pytest.raises(TailorSelectionError) as error:
        TailorSource("posting_1", "posting", b"x", "sha256:" + "0" * 64, {"family": "synthetic"})
    assert error.value.code == "tailor_input_invalid"
    with pytest.raises(ScoutDocumentError):
        prepare_document_revision(_selection(), "cover_letter", RECORD, REVISION, b"hello")


def test_cli_accepts_supplied_bytes_and_no_paths() -> None:
    result = CliRunner().invoke(tailor_cli, ["digest", base64.b64encode(b"synthetic").decode()])
    assert result.exit_code == 0
    assert json.loads(result.output)["content_sha256"] == digest_imported_bytes(b"synthetic")


def test_cli_validation_command_invokes_document_service() -> None:
    selection = _selection()
    service_selection = selection.to_json()
    service_selection["sources"] = [
        {"source_id": item.source_id, "purpose": item.purpose, "content_base64": base64.b64encode(item.content).decode(), "content_sha256": item.content_sha256, "identity": dict(item.identity)}
        for item in selection.sources
    ]
    bundle = encode_tailoring_bundle({"resume": b"# Resume\n\n## Experience\nPython.\n"})
    result = CliRunner().invoke(tailor_cli, ["validate-bundle", json.dumps(service_selection), base64.b64encode(bundle).decode()])
    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "valid"
