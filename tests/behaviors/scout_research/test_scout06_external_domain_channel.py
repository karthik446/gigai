"""Focused transport and dispatch regressions for external recording v2.

The positive research bridge remains root-owned and is intentionally not
claimed here.  These tests exercise the strict generic channel and its
fail-closed bridge boundary without executing source or using a provider.
"""

from __future__ import annotations

import base64
from copy import deepcopy

import pytest

from gigai import external_recording
from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.validators import validate_serialized_contract


_PROJECT = "project_00000000-0000-4000-8000-000000000001"
_GIG = "gig_00000000-0000-4000-8000-000000000001"
_RUN = "run_00000000-0000-4000-8000-000000000001"
_CHECKPOINT = "checkpoint_00000000-0000-4000-8000-000000000001"
_REF_DATA = b"{}"
_REF = {
    "path": "runs/example.json",
    "content_sha256": digest_imported_bytes(_REF_DATA),
    "media_type": "application/json",
    "size_bytes": len(_REF_DATA),
}


def _selected_input() -> dict[str, object]:
    return {
        "family": "g45_run_input",
        "run_input_id": "input_00000000-0000-4000-8000-000000000001",
        "record_ref": _REF,
        "snapshot_ref": _REF,
    }


def _supporting() -> dict[str, object]:
    content = b"captured source bytes"
    return {
        "artifact_id": "source_capture",
        "media_type": "text/plain",
        "content_base64": base64.b64encode(content).decode("ascii"),
        "content_sha256": digest_imported_bytes(content),
        "size_bytes": len(content),
    }


def _domain_input() -> dict[str, object]:
    return {
        "schema_id": external_recording.RESEARCH_DOMAIN_SCHEMA_ID,
        "value": {"schema_version": "1.0", "selected_inputs": []},
    }


def _v2_invocation() -> dict[str, object]:
    artifact = {
        "kind": "research",
        "markdown": "# Research\n",
        "sidecar": {
            "document_sha256": digest_imported_bytes(b"# Research\n"),
            "output_kind": "research",
            "run_id": _RUN,
            "selected_inputs": [_selected_input()],
        },
        "domain_sidecar": _domain_input(),
        "supporting_artifacts": [_supporting()],
    }
    return {
        "schema_version": "2.0",
        "invocation_id": "inv_00000000-0000-4000-8000-000000000001",
        "operation": "checkpoint",
        "project_id": _PROJECT,
        "gig_id": _GIG,
        "origin": "direct_cli",
        "actor": {"kind": "operator", "id": "local-user"},
        "input": {
            "run_id": _RUN,
            "parent_checkpoint": None,
            "questions": [],
            "artifact_refs": [artifact],
            "reason": "fixture",
        },
        "operation_key": "fixture-v2",
        "payload_sha256": "sha256:" + "1" * 64,
        "created_at": "2026-09-10T00:00:00Z",
    }


def _artifact_refs() -> dict[str, object]:
    markdown = b"# Research\n"
    domain = canonical_json_bytes(_domain_input())
    content = b"captured source bytes"
    return {
        "kind": "research",
        "markdown": external_recording._ref(
            f"runs/{_RUN}/artifacts/{_CHECKPOINT}/01.md", markdown, "text/markdown"
        ),
        "sidecar": external_recording._ref(
            f"runs/{_RUN}/artifacts/{_CHECKPOINT}/01.json",
            canonical_json_bytes(_v2_invocation()["input"]["artifact_refs"][0]["sidecar"]),
        ),
        "domain_sidecar": external_recording._ref(
            f"runs/{_RUN}/artifacts/{_CHECKPOINT}/01.domain.json", domain
        ),
        "supporting_artifacts": [
            {
                "artifact_id": "source_capture",
                "ref": external_recording._ref(
                    f"runs/{_RUN}/artifacts/{_CHECKPOINT}/01.supporting/source_capture.bin",
                    content,
                    "application/octet-stream",
                ),
            }
        ],
    }
def test_v2_invocation_schema_accepts_complete_typed_artifact() -> None:
    payload = _v2_invocation()
    report = validate_serialized_contract(
        "external-recording-invocation-v2.schema.json", canonical_json_bytes(payload)
    )
    assert report.valid, report.as_dict()


def test_v2_invocation_schema_refuses_domain_downgrade_or_unknown_fields() -> None:
    payload = _v2_invocation()
    del payload["input"]["artifact_refs"][0]["domain_sidecar"]
    assert not validate_serialized_contract(
        "external-recording-invocation-v2.schema.json", canonical_json_bytes(payload)
    ).valid


def test_v2_checkpoint_and_receipt_schemas_accept_exact_domain_refs() -> None:
    invocation = _v2_invocation()
    output = _artifact_refs()
    check_markdown = b"# Completion\n"
    check = {
        "kind": "research-check",
        "markdown": external_recording._ref(
            f"runs/{_RUN}/artifacts/{_CHECKPOINT}/02.md",
            check_markdown,
            "text/markdown",
        ),
        "sidecar": external_recording._ref(
            f"runs/{_RUN}/artifacts/{_CHECKPOINT}/02.json",
            canonical_json_bytes(
                {
                    "evidence_kind": "research-check",
                    "run_id": _RUN,
                    "output_sha256": output["markdown"]["content_sha256"],
                    "result": "pass",
                }
            ),
        ),
    }
    checkpoint = {
        "schema_version": "2.0",
        "checkpoint_id": _CHECKPOINT,
        "run_id": _RUN,
        "run_plan": _REF,
        "invocation": invocation,
        "sequence": 1,
        "parent_checkpoint": None,
        "questions": [],
        "artifacts": [output, check],
        "reason": "fixture",
        "created_at": "2026-09-10T00:00:00Z",
    }
    report = validate_serialized_contract(
        "external-recording-checkpoint-v2.schema.json", canonical_json_bytes(checkpoint)
    )
    assert report.valid, report.as_dict()
    submit_invocation = deepcopy(invocation)
    submit_invocation.update(
        operation="submit",
        invocation_id="inv_00000000-0000-4000-8000-000000000002",
        input={
            "run_id": _RUN,
            "parent_checkpoint": _CHECKPOINT,
            "output_refs": [output],
            "check_refs": [_REF],
            "disclosure": {"execution": "unobserved", "actor_report": "declared"},
        },
    )
    receipt = {
        "schema_version": "2.0",
        "receipt_id": "receipt_00000000-0000-4000-8000-000000000001",
        "run_id": _RUN,
        "run_plan": _REF,
        "invocation": submit_invocation,
        "operation_key": "fixture-v2-submit",
        "payload_sha256": "sha256:" + "2" * 64,
        "outcome": "succeeded",
        "outputs": [output],
        "checks": [_REF],
        "disclosure": {"execution": "unobserved", "actor_report": "declared"},
        "created_at": "2026-09-10T00:00:00Z",
    }
    report = validate_serialized_contract(
        "external-recording-receipt-v2.schema.json", canonical_json_bytes(receipt)
    )
    assert report.valid, report.as_dict()

    payload = _v2_invocation()
    payload["input"]["artifact_refs"][0]["domain_sidecar"]["value"]["extra"] = 1
    # The generic envelope preserves the complete closed domain value; the
    # fixed domain schema, rather than this transport schema, owns its fields.
    assert validate_serialized_contract(
        "external-recording-invocation-v2.schema.json", canonical_json_bytes(payload)
    ).valid


def test_supporting_bytes_require_canonical_base64_and_exact_digest() -> None:
    artifact = _v2_invocation()["input"]["artifact_refs"][0]
    with pytest.raises(external_recording.ExternalRecordingError) as caught:
        external_recording._validate_domain_input(
            {**artifact, "domain_sidecar": _domain_input(), "supporting_artifacts": [{**_supporting(), "content_base64": "YQ==\n"}]},
            markdown=b"# Research\n",
        )
    assert caught.value.code == "external_domain_invalid"

    with pytest.raises(external_recording.ExternalRecordingError) as caught:
        external_recording._validate_domain_input(
            {**artifact, "domain_sidecar": _domain_input(), "supporting_artifacts": [{**_supporting(), "content_sha256": "sha256:" + "0" * 64}]},
            markdown=b"# Research\n",
        )
    assert caught.value.code == "external_domain_invalid"


def test_unknown_domain_id_refuses_before_any_fixed_dispatch() -> None:
    artifact = _v2_invocation()["input"]["artifact_refs"][0]
    domain = {"schema_id": "urn:gigai:unknown:1", "value": {}}
    with pytest.raises(external_recording.ExternalRecordingError) as caught:
        external_recording._validate_domain_input(
            {**artifact, "domain_sidecar": domain, "supporting_artifacts": []},
            markdown=b"# Research\n",
        )
    assert caught.value.code == "external_domain_unsupported"


def test_known_research_domain_uses_fixed_bridge_and_refuses_malformed_value() -> None:
    artifact = _v2_invocation()["input"]["artifact_refs"][0]
    domain, supporting = external_recording._validate_domain_input(
        artifact, markdown=b"# Research\n"
    )
    assert set(domain) == {"schema_id", "value"}
    with pytest.raises(external_recording.ExternalRecordingError) as caught:
        external_recording._validate_fixed_domain(
            domain, markdown=b"# Research\n", supporting=supporting
        )
    assert caught.value.code == "research_domain_invalid"


def test_derived_domain_paths_are_same_run_and_refs_are_exact() -> None:
    domain = _domain_input()
    content = {"source_capture": b"captured source bytes"}
    artifacts, domain_ref, supporting_refs = external_recording._domain_refs(
        root=f"runs/{_RUN}/artifacts/{_CHECKPOINT}/01",
        domain=domain,
        supporting=content,
    )
    assert [item.path for item in artifacts] == [
        f"runs/{_RUN}/artifacts/{_CHECKPOINT}/01.domain.json",
        f"runs/{_RUN}/artifacts/{_CHECKPOINT}/01.supporting/source_capture.bin",
    ]
    assert domain_ref["path"].endswith(".domain.json")
    assert supporting_refs[0]["artifact_id"] == "source_capture"
    assert supporting_refs[0]["ref"]["content_sha256"] == digest_imported_bytes(content["source_capture"])


def test_version_dispatch_refuses_unknown_version_without_downgrade() -> None:
    payload = _v2_invocation()
    payload["schema_version"] = "9.0"
    with pytest.raises(external_recording.ExternalRecordingError) as caught:
        external_recording._recorded_dispatch(
            canonical_json_bytes(payload),
            v1_schema="external-recording-invocation.schema.json",
            v2_schema="external-recording-invocation-v2.schema.json",
            code="external_authority_mismatch",
        )
    assert caught.value.code == "external_protocol_unsupported"


def test_v1_output_sidecar_remains_exact_four_field_contract() -> None:
    markdown = b"# Legacy\n"
    sidecar = {
        "document_sha256": digest_imported_bytes(markdown),
        "output_kind": "report",
        "run_id": _RUN,
        "selected_inputs": [_selected_input()],
    }
    assert external_recording._output_sidecar_is_valid(
        sidecar,
        run_id=_RUN,
        inputs=[_selected_input()],
        kind="report",
        markdown=markdown,
    )
    assert not external_recording._output_sidecar_is_valid(
        {**sidecar, "domain_sidecar": {}},
        run_id=_RUN,
        inputs=[_selected_input()],
        kind="report",
        markdown=markdown,
    )
