"""Focused fixed-bridge checks for the unshipped Scout research domain."""

from __future__ import annotations

from copy import deepcopy
from importlib import import_module

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.scout.research import (
    DOMAIN_SCHEMA_ID,
    FIXED_DOMAIN_RESOURCES,
    ScoutResearchError,
    VALIDATOR_ID,
    validate_research_domain,
)


_PROJECT = "project_00000000-0000-4000-8000-000000000060"
_GIG = "gig_00000000-0000-4000-8000-000000000060"
_GRAPH = "graph_00000000-0000-4000-8000-000000000061"
_RUN = "run_00000000-0000-4000-8000-000000000062"
_CAPTURE = b"Synthetic FDE role responsibilities and delivery patterns."
_REVIEW = b"Independent reviewer compared the supplied role capture."


def _ref(name: str, value: bytes) -> dict[str, object]:
    return {
        "artifact_id": name,
        "content_sha256": digest_imported_bytes(value),
        "size_bytes": len(value),
    }


def _research() -> dict[str, object]:
    return {
        "role_title": "Forward Deployed Engineer",
        "role_summary": "Customer-facing engineering connects deployment work and product feedback.",
        "responsibilities": [{"responsibility_id": "responsibility_delivery", "description": "Deliver technical work with customer teams.", "claim_ids": ["claim_delivery"]}],
        "variations": [{"variation_id": "variation_product", "description": "Some employers emphasize product feedback.", "claim_ids": ["claim_delivery"]}],
        "reusable_sections": [{"section_id": "section_delivery", "heading": "Delivery context", "content": "Reuse this distinction later.", "claim_ids": ["claim_delivery"]}],
        "compensation": {"status": "unknown", "geography": None, "currency": None, "as_of_date": None, "pay_period": None, "base_range": None, "total_range": None, "source_limitations": ["No salary evidence was supplied."], "claim_ids": []},
        "sources": [{"source_id": "source_role", "locator": "https://example.test/role", "title": "Synthetic role profile", "publisher": "Example Labs", "kind": "employer", "published_date": None, "retrieved_date": "2026-09-09", "status": "independently_verified", "claim_ids": ["claim_delivery"], "capture_ref": _ref("role_capture", _CAPTURE), "verification": {"method": "independent_review", "evidence_ref": _ref("role_review", _REVIEW), "actor": {"kind": "reviewer", "id": "synthetic-reviewer"}}}],
        "claims": [{"claim_id": "claim_delivery", "statement": "The supplied role profile describes customer-team delivery.", "source_ids": ["source_role"], "status": "independently_verified"}],
        "uncertainties": [{"uncertainty_id": "uncertainty_scope", "topic": "Employer specificity", "detail": "Scope varies by employer.", "claim_ids": []}],
        "questions": [{"question_id": "question_priority", "prompt": "Which delivery emphasis matters most?", "reason": "It narrows later tailoring.", "claim_ids": []}],
        "checks": [{"check_id": "check_source", "kind": "source_integrity", "result": "pass", "detail": "Supplied bytes match declared refs.", "claim_ids": ["claim_delivery"]}],
        "output_roles": ["tailoring_context", "interview_context"],
    }


def _inputs(*, context: str | None = "B2B implementation work") -> list[dict[str, object]]:
    return [{"family": "role_request", "role_title": "Forward Deployed Engineer", "role_context": context}]


def _packet(*, inputs: list[dict[str, object]] | None = None):
    renderer = import_module(
        "gigai.scout.data.tools.cap_00000000-0000-4000-8000-000000000071.research"
    )
    actual_inputs = _inputs() if inputs is None else inputs
    supporting = {"role_capture": _CAPTURE, "role_review": _REVIEW}
    packet = renderer.build_research_packet(
        project_id=_PROJECT,
        gig_id=_GIG,
        gig_version=1,
        graph_id=_GRAPH,
        graph_version=1,
        run_id=_RUN,
        selected_inputs=actual_inputs,
        research=_research(),
        artifact_bytes=supporting,
    )
    return packet, actual_inputs, supporting


def _validate(packet, inputs, supporting, **overrides: object) -> None:
    validate_research_domain(
        value=packet.sidecar,
        markdown=packet.markdown,
        supporting=supporting,
        run_id=overrides.get("run_id", _RUN),
        project_id=overrides.get("project_id", _PROJECT),
        gig_id=overrides.get("gig_id", _GIG),
        gig_version=overrides.get("gig_version", 1),
        graph_id=overrides.get("graph_id", _GRAPH),
        graph_selector=overrides.get("graph_selector", "research-role"),
        graph_version=overrides.get("graph_version", 1),
        selected_inputs=overrides.get("selected_inputs", inputs),
    )


def test_fixed_bridge_accepts_exact_role_only_packet_and_exposes_fixed_resources() -> None:
    packet, inputs, supporting = _packet()
    _validate(packet, inputs, supporting)
    assert packet.sidecar["schema_version"] == "scout-research-sidecar:2"
    assert packet.sidecar["origin"]["project_id"] == _PROJECT
    assert packet.sidecar["selected_inputs"] == inputs
    assert FIXED_DOMAIN_RESOURCES == {
        "schema_id": DOMAIN_SCHEMA_ID,
        "schema_resource": "scout/data/tools/cap_00000000-0000-4000-8000-000000000071/research.schema.json",
        "validator_id": VALIDATOR_ID,
        "validator_source": "gigai.scout.research:validate_research_domain",
    }


@pytest.mark.parametrize("mutation", ["context", "role_title", "origin", "unsupported"])
def test_fixed_bridge_refuses_mismatched_sealed_input_or_origin(mutation: str) -> None:
    packet, inputs, supporting = _packet()
    if mutation == "context":
        trusted = _inputs(context="Changed context")
        kwargs = {"selected_inputs": trusted}
    elif mutation == "role_title":
        packet = deepcopy(packet)
        packet.sidecar["research"]["role_title"] = "Substituted role"
        kwargs = {}
    elif mutation == "origin":
        kwargs = {"gig_version": 2}
    else:
        packet = deepcopy(packet)
        packet.sidecar["selected_inputs"].append({"family": "unknown"})
        kwargs = {}
    with pytest.raises(ScoutResearchError) as refused:
        _validate(packet, inputs, supporting, **kwargs)
    assert refused.value.code in {
        "research_domain_invalid",
        "research_domain_input_mismatch",
        "research_domain_origin_mismatch",
    }


def test_fixed_bridge_requires_exact_supporting_coverage() -> None:
    packet, inputs, supporting = _packet()
    with pytest.raises(ScoutResearchError) as missing:
        _validate(packet, inputs, {"role_capture": _CAPTURE})
    assert missing.value.code == "research_domain_supporting_mismatch"
    with pytest.raises(ScoutResearchError) as extra:
        _validate(packet, inputs, {**supporting, "unused": b"not referenced"})
    assert extra.value.code == "research_domain_supporting_mismatch"


def test_fixed_bridge_preserves_strict_optional_g45_envelope() -> None:
    record_ref = {
        "path": "run-inputs/input.json",
        "content_sha256": "sha256:" + "1" * 64,
        "media_type": "application/json",
        "size_bytes": 12,
    }
    inputs = [
        *_inputs(),
        {
            "family": "g45_run_input",
            "run_input_id": "input_00000000-0000-4000-8000-000000000063",
            "record_ref": record_ref,
            "snapshot_ref": record_ref,
        },
    ]
    packet, inputs, supporting = _packet(inputs=inputs)
    _validate(packet, inputs, supporting)
    changed = deepcopy(inputs)
    changed[1]["snapshot_ref"]["content_sha256"] = "sha256:" + "2" * 64
    with pytest.raises(ScoutResearchError) as refused:
        _validate(packet, changed, supporting)
    assert refused.value.code == "research_domain_input_mismatch"


def test_fixed_bridge_preserves_strict_optional_native_envelope() -> None:
    blob_ref = {
        "path": "records/record_00000000-0000-4000-8000-000000000063/blobs/revision_00000000-0000-4000-8000-000000000064.json",
        "content_sha256": "sha256:" + "3" * 64,
        "media_type": "application/json",
        "size_bytes": 24,
    }
    inputs = [
        *_inputs(),
        {
            "family": "scout_record",
            "record_id": "record_00000000-0000-4000-8000-000000000063",
            "revision_id": "revision_00000000-0000-4000-8000-000000000064",
            "native_kind": "profile_preferences",
            "scope": {"mode": "saved_default", "task_context_id": None, "base": None},
            "content": {
                "family": "jsl_blob",
                "blob_ref": blob_ref,
                "content_sha256": "sha256:" + "3" * 64,
            },
        },
    ]
    packet, inputs, supporting = _packet(inputs=inputs)
    _validate(packet, inputs, supporting)
    assert packet.sidecar["selected_inputs"][1] == inputs[1]
