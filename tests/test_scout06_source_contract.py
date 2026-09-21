"""Inert compiler proof; no default promotion, approval, or execution."""

import json
from copy import deepcopy
from pathlib import PurePosixPath
import uuid

import pytest

from gigai.canonical import canonical_json_bytes, digest_imported_bytes
from gigai.graph_set import _attached_contract_valid
from gigai.scout_bundled_tools import (
    SCOUT_CRUD_ENTRY_PATH,
    SCOUT_CRUD_SCHEMA_PATH,
    prepared_scout_crud_manifest,
)
from gigai.scout_materialization import _compiled_snapshot, _source_digest
from gigai.scout_template import (
    SCOUT_DEFINITION_VERSION,
    SCOUT_RESEARCH_SCHEMA_PATH,
    SCOUT_RESEARCH_SOURCE_PATH,
    scout_graph_source,
    scout_source_files,
)


GIG_ID = "gig_00000000-0000-4000-8000-000000000001"


# This is the accepted Scout authoring map. Keep it literal so a new source
# member cannot make this test pass by changing the count or enumerating the
# implementation's own output. The proposal-assessment graph is intentionally
# included as a candidate authoring member, not as CRUD capability authority.
EXPECTED_SOURCE_PATHS = frozenset({
    "README.md",
    "CHANGELOG.md",
    "gig.py",
    "goalgraphs/README.md",
    "goalgraphs/research-role.md",
    "goalgraphs/find-jobs.md",
    "goalgraphs/tailor-application.md",
    "goalgraphs/record-application.md",
    "goalgraphs/prepare-interview.md",
    "goalgraphs/proposal-assessment.md",
    "ui/template.html",
    "ui/style.css",
    "tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py",
    "tools/cap_00000000-0000-4000-8000-000000000071/operation.schema.json",
    "tools/cap_00000000-0000-4000-8000-000000000074/discovery.py",
    "tools/cap_00000000-0000-4000-8000-000000000074/discovery.schema.json",
    "tools/cap_00000000-0000-4000-8000-000000000075/tailoring.py",
    "tools/cap_00000000-0000-4000-8000-000000000075/tailoring.schema.json",
    "tools/cap_00000000-0000-4000-8000-000000000076/research.py",
    "tools/cap_00000000-0000-4000-8000-000000000076/research.schema.json",
    "definition/scout-source.json",
})


def test_research_source_is_outside_closed_crud_inventory():
    source = scout_source_files()
    assert set(source) == EXPECTED_SOURCE_PATHS
    assert len(source) == 21
    assert PurePosixPath(SCOUT_RESEARCH_SOURCE_PATH).parent != PurePosixPath(SCOUT_CRUD_ENTRY_PATH).parent
    assert SCOUT_RESEARCH_SOURCE_PATH in source
    assert SCOUT_RESEARCH_SCHEMA_PATH in source
    assert "tools/cap_00000000-0000-4000-8000-000000000074/discovery.py" in source
    assert "tools/cap_00000000-0000-4000-8000-000000000074/discovery.schema.json" in source
    proposal = scout_graph_source("proposal-assessment")
    assert proposal.instructions_path == "goalgraphs/proposal-assessment.md"
    assert proposal.required_inputs == ("posting", "candidate_evidence")
    assert proposal.optional_inputs == ("preferences", "experience_answers", "research_revisions")
    assert proposal.outputs == ("proposal_assessment",)
    definition = json.loads(source["definition/scout-source.json"])
    proposal_definition = next(
        item for item in definition["graphs"] if item["selector"] == "proposal-assessment"
    )
    assert proposal_definition == {
        "selector": "proposal-assessment",
        "title": "Assess a saved opportunity",
        "purpose": "Produce one private, evidence-backed fit assessment from explicitly selected inputs.",
        "instructions": "goalgraphs/proposal-assessment.md",
        "required_inputs": ["posting", "candidate_evidence"],
        "optional_inputs": ["preferences", "experience_answers", "research_revisions"],
        "outputs": ["proposal_assessment"],
    }
    manifest = prepared_scout_crud_manifest(
        gig_id=GIG_ID,
        goal_ids=["goal_00000000-0000-4000-8000-000000000002"],
        source=source,
    )
    # Research cannot silently expand the previously closed CRUD authority.
    inventory_paths = {
        item["path"] for item in manifest["capabilities"][0]["tool_binding"]["inventory"]
    }
    assert inventory_paths == {
        "gig.py",
        SCOUT_CRUD_ENTRY_PATH,
        SCOUT_CRUD_SCHEMA_PATH,
    }
    serialized = json.dumps(manifest)
    assert SCOUT_RESEARCH_SOURCE_PATH not in serialized
    assert SCOUT_RESEARCH_SCHEMA_PATH not in serialized
    assert "goalgraphs/proposal-assessment.md" not in serialized


def test_composite_output_contract_pins_exact_inventoried_source_and_schema():
    source = scout_source_files()
    compiled, _goals = _compiled_snapshot(gig_id=GIG_ID, source=source, uuid_factory=uuid.uuid4)
    contract = json.loads(compiled["compiled/research-role/output_contract.json"])
    assert scout_graph_source("research-role").outputs == ("research",)
    assert contract["schema_version"] == "2.0"
    assert contract["fields"] == ["research"]
    assert set(contract["domains"]) == {"research"}
    binding = contract["domains"]["research"]
    assert set(binding) == {"schema_id", "schema_ref", "validator_id", "validator_source_ref"}
    assert binding["schema_id"] == "urn:gigai:scout:research-packet:3"
    assert binding["validator_id"] == "scout-role-research:3"
    prefix = f"manifests/software/scout-{SCOUT_DEFINITION_VERSION}-{_source_digest(source).removeprefix('sha256:')[:16]}"
    for field, path in (
        ("schema_ref", SCOUT_RESEARCH_SCHEMA_PATH),
        ("validator_source_ref", SCOUT_RESEARCH_SOURCE_PATH),
    ):
        assert binding[field]["path"] == f"{prefix}/{path}"
        assert binding[field]["content_sha256"] == digest_imported_bytes(source[path])
        assert binding[field]["size_bytes"] == len(source[path])
    find_jobs = json.loads(compiled["compiled/find-jobs/output_contract.json"])
    assert find_jobs["schema_version"] == "2.0"
    assert find_jobs["fields"] == ["discovery"]
    assert find_jobs["domains"]["discovery"]["schema_id"] == "urn:gigai:scout:discovery-packet:2"
    assert find_jobs["domains"]["discovery"]["validator_id"] == "scout-job-discovery:2"
    tailoring = json.loads(compiled["compiled/tailor-application/output_contract.json"])
    assert tailoring["schema_version"] == "2.0"
    assert tailoring["fields"] == ["tailoring"]
    assert tailoring["domains"]["tailoring"]["schema_id"] == "urn:gigai:scout:tailoring-packet:1"
    assert tailoring["domains"]["tailoring"]["validator_id"] == "scout-application-tailoring:1"
    for selector in ("record-application", "prepare-interview"):
        other = json.loads(compiled[f"compiled/{selector}/output_contract.json"])
        assert other["schema_version"] == "1.0"
        assert "domains" not in other


def _contract():
    compiled, _ = _compiled_snapshot(
        gig_id=GIG_ID, source=scout_source_files(), uuid_factory=uuid.uuid4
    )
    return json.loads(compiled["compiled/research-role/output_contract.json"])


def test_graph_set_admits_only_the_declared_v2_output_attachment():
    contract = _contract()
    assert _attached_contract_valid("output_contract", canonical_json_bytes(contract), gig_id=GIG_ID)
    assert not _attached_contract_valid("input_contract", canonical_json_bytes(contract), gig_id=GIG_ID)
    assert not _attached_contract_valid("output_contract", canonical_json_bytes(contract), gig_id="gig_foreign")
    legacy = {"schema_version": "1.0", "kind": "run_output_contract", "gig_id": GIG_ID, "fields": ["research"]}
    assert _attached_contract_valid("output_contract", canonical_json_bytes(legacy), gig_id=GIG_ID)


@pytest.mark.parametrize("mutation", [
    "unknown_root", "unknown_domain", "unknown_ref", "missing_source",
    "unknown_schema", "unknown_validator", "empty_fields", "duplicate_fields",
    "malformed_fields", "bad_path", "bad_digest", "bool_size", "wrong_media",
])
def test_graph_set_v2_domain_contract_rejects_malformed_nested_authority(mutation):
    contract = deepcopy(_contract())
    domain = contract["domains"]["research"]
    if mutation == "unknown_root":
        contract["unexpected"] = True
    elif mutation == "unknown_domain":
        domain["unexpected"] = True
    elif mutation == "unknown_ref":
        domain["schema_ref"]["unexpected"] = True
    elif mutation == "missing_source":
        del domain["validator_source_ref"]
    elif mutation == "unknown_schema":
        domain["schema_id"] = "urn:unknown"
    elif mutation == "unknown_validator":
        domain["validator_id"] = "caller.callback"
    elif mutation == "empty_fields":
        contract["fields"] = []
    elif mutation == "duplicate_fields":
        contract["fields"] *= 2
    elif mutation == "malformed_fields":
        contract["fields"] = [[]]
    elif mutation == "bad_path":
        domain["validator_source_ref"]["path"] = "../research.py"
    elif mutation == "bad_digest":
        domain["schema_ref"]["content_sha256"] = "not-a-digest"
    elif mutation == "bool_size":
        domain["schema_ref"]["size_bytes"] = True
    else:
        domain["schema_ref"]["media_type"] = "text/html"
    assert not _attached_contract_valid("output_contract", canonical_json_bytes(contract), gig_id=GIG_ID)
