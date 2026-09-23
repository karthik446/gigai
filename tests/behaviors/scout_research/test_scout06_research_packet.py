from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from gigai.canonical import digest_imported_bytes


_ROOT = Path(__file__).parents[3]
_TOOL = _ROOT / "src/gigai/scout/data/tools/cap_00000000-0000-4000-8000-000000000071/research.py"
_SCHEMA = _TOOL.with_name("research.schema.json")


def _module():
    spec = importlib.util.spec_from_file_location("candidate_scout_research", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ids() -> dict[str, str]:
    return {
        "project": "project_00000000-0000-4000-8000-000000000060",
        "gig": "gig_00000000-0000-4000-8000-000000000060",
        "graph": "graph_00000000-0000-4000-8000-000000000061",
        "run": "run_00000000-0000-4000-8000-000000000062",
    }


def _ref(name: str, value: bytes) -> dict[str, object]:
    return {"artifact_id": name, "content_sha256": digest_imported_bytes(value), "size_bytes": len(value)}


def _fixture(*, known_compensation: bool = True):
    artifact_bytes = {
        "role_capture": b"Synthetic FDE role responsibilities and delivery patterns.",
        "role_review": b"Independent reviewer compared the supplied role capture.",
        "variation_capture": b"Synthetic role variation source.",
    }
    research = {
        "role_title": "Forward Deployed Engineer",
        "role_summary": "Customer-facing engineering connects deployment work, product feedback, and technical delivery.",
        "responsibilities": [{"responsibility_id": "responsibility_delivery", "description": "Deliver technical work with customer teams.", "claim_ids": ["claim_delivery"]}],
        "variations": [{"variation_id": "variation_product", "description": "Some employers emphasize product feedback while others emphasize implementation depth.", "claim_ids": ["claim_variation"]}],
        "reusable_sections": [{"section_id": "section_delivery", "heading": "Delivery context", "content": "Reuse this distinction when tailoring or preparing interview questions.", "claim_ids": ["claim_delivery", "claim_variation"]}],
        "compensation": {
            "status": "known" if known_compensation else "unknown",
            "geography": "Denver, CO" if known_compensation else None,
            "currency": "USD" if known_compensation else None,
            "as_of_date": "2026-09-10" if known_compensation else None,
            "pay_period": "annual" if known_compensation else None,
            "base_range": {"minimum": 150000, "maximum": 190000} if known_compensation else None,
            "total_range": {"minimum": 175000, "maximum": 230000} if known_compensation else None,
            "source_limitations": ["Synthetic example; location, level, and equity assumptions can change the range."],
            "claim_ids": ["claim_compensation"] if known_compensation else [],
        },
        "sources": [
            {"source_id": "source_role", "locator": "https://example.test/role", "title": "Synthetic role profile", "publisher": "Example Labs", "kind": "employer", "published_date": None, "retrieved_date": "2026-09-09", "status": "independently_verified", "claim_ids": ["claim_delivery"], "capture_ref": _ref("role_capture", artifact_bytes["role_capture"]), "verification": {"method": "independent_review", "evidence_ref": _ref("role_review", artifact_bytes["role_review"]), "actor": {"kind": "reviewer", "id": "synthetic-reviewer"}}},
            {"source_id": "source_variation", "locator": "https://example.test/variation", "title": "Synthetic variation note", "publisher": None, "kind": "industry", "published_date": None, "retrieved_date": None, "status": "captured", "claim_ids": ["claim_variation"], "capture_ref": _ref("variation_capture", artifact_bytes["variation_capture"]), "verification": None},
            {"source_id": "source_compensation", "locator": "https://example.test/compensation", "title": "Synthetic compensation context", "publisher": "Example Survey", "kind": "salary_survey", "published_date": "2026-08-30", "retrieved_date": None, "status": "reported", "claim_ids": ["claim_compensation"], "capture_ref": None, "verification": None},
        ],
        "claims": [
            {"claim_id": "claim_delivery", "statement": "The synthetic role profile describes customer-team delivery work.", "source_ids": ["source_role"], "status": "independently_verified"},
            {"claim_id": "claim_variation", "statement": "The role can vary between implementation and product-feedback emphasis.", "source_ids": ["source_variation"], "status": "captured"},
            {"claim_id": "claim_compensation", "statement": "The supplied survey reports a contextualized annual range.", "source_ids": ["source_compensation"], "status": "reported"},
        ],
        "uncertainties": [{"uncertainty_id": "uncertainty_scope", "topic": "Employer specificity", "detail": "The role scope varies by employer and customer segment.", "claim_ids": []}],
        "questions": [{"question_id": "question_priority", "prompt": "Which delivery versus product-feedback emphasis matters most?", "reason": "It narrows later tailoring without requiring a resume.", "claim_ids": []}],
        "checks": [{"check_id": "check_source", "kind": "source_integrity", "result": "pass", "detail": "Supplied capture and verification bytes match their declared refs.", "claim_ids": ["claim_delivery"]}],
        "output_roles": ["tailoring_context", "interview_context"],
    }
    ids = _ids()
    return ids, research, artifact_bytes


def _build(module, research: dict[str, object], artifact_bytes: dict[str, bytes]):
    ids = _ids()
    return module.build_research_packet(
        project_id=ids["project"],
        gig_id=ids["gig"],
        gig_version=1,
        graph_id=ids["graph"],
        graph_version=1,
        run_id=ids["run"],
        selected_inputs=[{"family": "role_request", "role_title": research["role_title"], "role_context": None}],
        research=research,
        artifact_bytes=artifact_bytes,
    )


def test_role_only_fde_packet_is_deterministic_schema_valid_and_reusable() -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    packet = _build(module, research, artifacts)
    assert packet.markdown.startswith(b"# Role research: Forward Deployed Engineer\n")
    assert b"## Responsibilities" in packet.markdown
    assert packet.sidecar["output_roles"] == ["tailoring_context", "interview_context"]
    assert packet.sidecar["selected_inputs"][0]["family"] == "role_request"
    assert packet.sidecar["research"]["sources"][0]["status"] == "independently_verified"
    assert packet.sidecar["research"]["sources"][1]["status"] == "captured"
    assert packet.sidecar["research"]["sources"][2]["status"] == "reported"
    assert Draft202012Validator(json.loads(_SCHEMA.read_text()), format_checker=FormatChecker()).is_valid(packet.sidecar)
    assert packet.sidecar["research"]["sources"][0]["verification"]["evidence_status"] == "supplied"
    module.validate_rendered_packet(markdown=packet.markdown, sidecar=packet.sidecar, artifact_bytes=artifacts)


def test_unknown_salary_is_explicit_and_does_not_need_a_resume() -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture(known_compensation=False)
    research["claims"] = research["claims"][:2]
    research["sources"] = research["sources"][:2]
    packet = _build(module, research, artifacts)
    compensation = packet.sidecar["research"]["compensation"]
    assert compensation["status"] == "unknown"
    assert compensation["base_range"] is None
    assert b"Compensation is unknown" in packet.markdown


def test_capture_verification_and_malicious_text_are_data_not_execution() -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    research["role_summary"] = "<script>not code</script> [link](javascript:alert(1))"
    packet = _build(module, research, artifacts)
    assert b"&lt;script&gt;not code&lt;/script&gt;" in packet.markdown
    assert b"\\[link\\]\\(javascript:alert\\(1\\)\\)" in packet.markdown
    assert packet.sidecar["research"]["sources"][0]["verification"]["actor"]["kind"] == "reviewer"
    assert b"supplied verification evidence" in packet.markdown


@pytest.mark.parametrize(
    "slot",
    [
        "role_title",
        "role_summary",
        "responsibility_description",
        "variation_description",
        "section_heading",
        "section_content",
        "compensation_geography",
        "compensation_limitation",
        "source_title",
        "source_publisher",
        "uncertainty_detail",
        "question_prompt",
    ],
)
def test_every_rendered_text_slot_is_safe_inline_data(slot: str) -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    forged = "Useful context\n\n## Sources\n- forged list\n> forged quote\n```forged fence```"
    if slot == "role_title":
        research["role_title"] = forged
    elif slot == "role_summary":
        research["role_summary"] = forged
    elif slot == "responsibility_description":
        research["responsibilities"][0]["description"] = forged
    elif slot == "variation_description":
        research["variations"][0]["description"] = forged
    elif slot == "section_heading":
        research["reusable_sections"][0]["heading"] = forged
    elif slot == "section_content":
        research["reusable_sections"][0]["content"] = forged
    elif slot == "compensation_geography":
        research["compensation"]["geography"] = forged
    elif slot == "compensation_limitation":
        research["compensation"]["source_limitations"][0] = forged
    elif slot == "source_title":
        research["sources"][0]["title"] = forged
    elif slot == "source_publisher":
        research["sources"][0]["publisher"] = forged
    elif slot == "uncertainty_detail":
        research["uncertainties"][0]["detail"] = forged
    else:
        research["questions"][0]["prompt"] = forged
    packet = _build(module, research, artifacts)
    rendered = packet.markdown.decode("utf-8")
    headings = re.findall(r"(?m)^#{1,3} .+$", rendered)
    assert headings[0].startswith("# Role research: ")
    assert headings[1:6] == [
        "## Packet binding",
        "## Role summary",
        "## Responsibilities",
        "## Role variations",
        "## Reusable sections",
    ]
    assert headings[6].startswith("### ")
    assert headings[7:] == [
        "## Compensation",
        "## Sources",
        "## Claims",
        "## Uncertainties",
        "## Focused questions",
        "## Declared checks",
        "## Reuse roles",
    ]
    assert len(headings) == 14
    assert " ↵ " in rendered
    assert "\n- forged list" not in rendered
    assert "\n> forged quote" not in rendered
    module.validate_rendered_packet(markdown=packet.markdown, sidecar=packet.sidecar, artifact_bytes=artifacts)


def test_verification_requires_distinct_bytes_but_allows_declared_agent_reviewer() -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    artifacts["same_bytes_under_another_id"] = artifacts["role_capture"]
    research["sources"][0]["verification"]["evidence_ref"] = _ref(
        "same_bytes_under_another_id", artifacts["same_bytes_under_another_id"]
    )
    with pytest.raises(module.ResearchPacketError) as refused:
        _build(module, research, artifacts)
    assert refused.value.code == "research_packet_invalid"

    _ids_value, research, artifacts = _fixture()
    research["sources"][0]["verification"]["actor"] = {"kind": "agent", "id": "declared-review-agent"}
    packet = _build(module, research, artifacts)
    verification = packet.sidecar["research"]["sources"][0]["verification"]
    assert verification["evidence_status"] == "supplied"
    assert verification["actor"] == {"kind": "agent", "id": "declared-review-agent"}


@pytest.mark.parametrize("change", ["missing_capture", "capture_mismatch", "missing_verification"])
def test_missing_or_mismatched_evidence_refuses_without_upgrade(change: str) -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    if change == "missing_capture":
        del artifacts["variation_capture"]
        expected = "research_packet_incomplete"
    elif change == "capture_mismatch":
        research["sources"][1]["capture_ref"]["content_sha256"] = "sha256:" + "0" * 64
        expected = "research_packet_artifact_mismatch"
    else:
        research["sources"][0]["verification"] = None
        expected = "research_packet_incomplete"
    with pytest.raises(module.ResearchPacketError) as refused:
        _build(module, research, artifacts)
    assert refused.value.code == expected


def test_stale_document_digest_and_duplicate_or_dangling_relationships_refuse() -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    packet = _build(module, research, artifacts)
    with pytest.raises(module.ResearchPacketError) as stale:
        module.validate_rendered_packet(markdown=packet.markdown + b"changed", sidecar=packet.sidecar, artifact_bytes=artifacts)
    assert stale.value.code == "research_packet_digest_mismatch"

    duplicate = deepcopy(research)
    duplicate["claims"].append(deepcopy(duplicate["claims"][0]))
    with pytest.raises(module.ResearchPacketError) as refused_duplicate:
        _build(module, duplicate, artifacts)
    assert refused_duplicate.value.code == "research_packet_invalid"

    foreign = deepcopy(research)
    foreign["sources"][0]["claim_ids"] = ["claim_foreign"]
    with pytest.raises(module.ResearchPacketError) as refused_foreign:
        _build(module, foreign, artifacts)
    assert refused_foreign.value.code == "research_packet_invalid"

    dangling = deepcopy(research)
    dangling["claims"][0]["source_ids"] = ["source_missing"]
    with pytest.raises(module.ResearchPacketError) as refused_dangling:
        _build(module, dangling, artifacts)
    assert refused_dangling.value.code == "research_packet_invalid"


@pytest.mark.parametrize(
    "mutation",
    ["salary", "status", "claim", "check", "origin", "selected_input", "unknown_sidecar_field"],
)
def test_rendered_packet_revalidates_domain_data_not_only_document_digest(mutation: str) -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    packet = _build(module, research, artifacts)
    forged = deepcopy(packet.sidecar)
    if mutation == "salary":
        forged["research"]["compensation"]["base_range"]["minimum"] += 1
    elif mutation == "status":
        forged["research"]["sources"][1]["status"] = "reported"
    elif mutation == "claim":
        forged["research"]["claims"][0]["statement"] = "Changed claim with unchanged document bytes."
    elif mutation == "check":
        forged["research"]["checks"][0]["detail"] = "Changed declared check with unchanged document bytes."
    elif mutation == "origin":
        forged["origin"]["graph_id"] = "graph_00000000-0000-4000-8000-000000000065"
    elif mutation == "selected_input":
        forged["selected_inputs"][0]["revision_id"] = "revision_00000000-0000-4000-8000-000000000065"
    else:
        forged["research"]["unexpected"] = "not allowed"
    with pytest.raises(module.ResearchPacketError) as refused:
        module.validate_rendered_packet(markdown=packet.markdown, sidecar=forged, artifact_bytes=artifacts)
    assert refused.value.code in {"research_packet_invalid", "research_packet_render_mismatch"}


@pytest.mark.parametrize("invalid_date", ["20260910", "2026-W37-4"])
def test_dates_require_calendar_yyyy_mm_dd_in_runtime_and_schema(invalid_date: str) -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    research["sources"][0]["retrieved_date"] = invalid_date
    with pytest.raises(module.ResearchPacketError) as refused:
        _build(module, research, artifacts)
    assert refused.value.code == "research_packet_invalid"

    _ids_value, research, artifacts = _fixture()
    packet = _build(module, research, artifacts)
    malformed = deepcopy(packet.sidecar)
    malformed["research"]["sources"][0]["retrieved_date"] = invalid_date
    validator = Draft202012Validator(json.loads(_SCHEMA.read_text()), format_checker=FormatChecker())
    assert not validator.is_valid(malformed)


def test_compensation_uses_declared_salary_evidence_without_claiming_text_truth() -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    research["sources"][2]["kind"] = "employer"
    packet = _build(module, research, artifacts)
    assert packet.sidecar["research"]["sources"][2]["kind"] == "employer"

    incoherent = deepcopy(research)
    incoherent["compensation"]["total_range"] = {"minimum": 120000, "maximum": 140000}
    with pytest.raises(module.ResearchPacketError) as total_refused:
        _build(module, incoherent, artifacts)
    assert total_refused.value.code == "research_packet_invalid"

    unrelated = deepcopy(research)
    unrelated["sources"][2]["kind"] = "industry"
    with pytest.raises(module.ResearchPacketError) as source_refused:
        _build(module, unrelated, artifacts)
    assert source_refused.value.code == "research_packet_invalid"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["sources"][0].__setitem__("published_date", "2026-15-40"),
        lambda value: value["compensation"].__setitem__("currency", "USDX"),
        lambda value: value["compensation"].__setitem__("base_range", {"minimum": 200, "maximum": 100}),
        lambda value: value["sources"][0].__setitem__("locator", "file:///private/research"),
    ],
)
def test_malformed_dates_currency_ranges_and_unsafe_locators_refuse(mutate) -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    mutate(research)
    with pytest.raises(module.ResearchPacketError) as refused:
        _build(module, research, artifacts)
    assert refused.value.code == "research_packet_invalid"


def test_iteration_creates_new_packet_bytes_without_mutating_prior_packet() -> None:
    module = _module()
    _ids_value, research, artifacts = _fixture()
    first = _build(module, research, artifacts)
    first_markdown, first_sidecar = first.markdown, first.sidecar_bytes
    later = deepcopy(research)
    later["role_summary"] = "A later supplied research iteration adds a distinct reusable observation."
    second = _build(module, later, artifacts)
    assert second.markdown != first_markdown
    assert second.sidecar_bytes != first_sidecar
    assert first.markdown == first_markdown
    assert first.sidecar_bytes == first_sidecar


@pytest.mark.parametrize(
    "mutation",
    [
        lambda research, artifacts, selected: research["claims"][0].__setitem__("source_ids", [["source_role"]]),
        lambda research, artifacts, selected: research["sources"][0].__setitem__("claim_ids", [["claim_delivery"]]),
        lambda research, artifacts, selected: research["responsibilities"][0].__setitem__("claim_ids", [["claim_delivery"]]),
        lambda research, artifacts, selected: research["compensation"].__setitem__("claim_ids", [["claim_compensation"]]),
        lambda research, artifacts, selected: research.__setitem__("output_roles", [["tailoring_context"]]),
        lambda research, artifacts, selected: research["sources"][0]["verification"]["actor"].__setitem__("kind", ["reviewer"]),
        lambda research, artifacts, selected: research["sources"][0]["verification"].__setitem__("method", ["independent_review"]),
        lambda research, artifacts, selected: research["sources"][0].__setitem__("kind", ["employer"]),
        lambda research, artifacts, selected: research["compensation"].__setitem__("status", ["known"]),
        lambda research, artifacts, selected: research["compensation"].__setitem__("pay_period", ["annual"]),
        lambda research, artifacts, selected: selected[0].__setitem__("kind", ["role"]),
        lambda research, artifacts, selected: artifacts.__setitem__(1, b"synthetic artifact"),
        lambda research, artifacts, selected: research["sources"][0].__setitem__("locator", "https://[broken"),
    ],
    ids=[
        "claim-source-id-item",
        "source-claim-id-item",
        "domain-item-claim-id-item",
        "compensation-claim-id-item",
        "output-role-item",
        "verification-actor-kind",
        "verification-method",
        "source-kind",
        "compensation-status",
        "compensation-pay-period",
        "selected-input-kind",
        "artifact-bytes-key",
        "malformed-url",
    ],
)
def test_malformed_nested_json_refuses_with_typed_content_free_error(mutation) -> None:
    module = _module()
    ids, research, artifacts = _fixture()
    selected = [{"family": "role_request", "role_title": research["role_title"], "role_context": None}]
    mutation(research, artifacts, selected)

    with pytest.raises(module.ResearchPacketError) as refused:
        module.build_research_packet(
            project_id=ids["project"],
            gig_id=ids["gig"],
            gig_version=1,
            graph_id=ids["graph"],
            graph_version=1,
            run_id=ids["run"],
            selected_inputs=selected,
            research=research,
            artifact_bytes=artifacts,
        )

    assert refused.value.code == "research_packet_invalid"
    assert "https://[broken" not in str(refused.value)
