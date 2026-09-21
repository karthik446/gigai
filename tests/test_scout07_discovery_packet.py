from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from gigai.canonical import canonical_json_bytes, digest_imported_bytes


_ROOT = Path(__file__).parents[1]
_TOOL = _ROOT / "src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.py"
_SCHEMA = _TOOL.with_name("discovery.schema.json")


def _module():
    spec = importlib.util.spec_from_file_location("candidate_scout_discovery", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ids() -> dict[str, str]:
    return {
        "project": "project_00000000-0000-4000-8000-000000000074",
        "gig": "gig_00000000-0000-4000-8000-000000000074",
        "graph": "graph_00000000-0000-4000-8000-000000000074",
        "run": "run_00000000-0000-4000-8000-000000000074",
        "record": "record_00000000-0000-4000-8000-000000000074",
        "revision": "revision_00000000-0000-4000-8000-000000000074",
    }


def _fact(state: str = "known", value: str | None = "required") -> dict[str, object]:
    return {
        "state": state,
        "value": value if state == "known" else None,
        "context": None,
        "provenance": {"kind": "user_reported", "source_refs": []},
        "conflict_refs": [],
    }


def _selected_profile(*, sponsorship_state: str = "known") -> tuple[dict[str, object], dict[str, bytes]]:
    ids = _ids()
    payload = {
        "hard_constraints": {
            "geography": _fact(value="Denver, CO"),
            "work_mode": _fact(value="hybrid"),
            "seniority": _fact(value="senior"),
            "employment_type": _fact(value="full-time"),
            "compensation": _fact(value="150000 USD annual"),
        },
        "soft_priorities": {},
        "sponsorship_need": _fact(state=sponsorship_state, value="needs_sponsorship"),
        "employer_sponsorship": _fact(value="offers_sponsorship"),
        "eligibility": _fact(value="eligible"),
    }
    native = {"schema_version": "1.0", "kind": "profile_preferences", "scope": {"mode": "saved_default", "task_context_id": None, "base": None}, "payload": payload}
    blob = canonical_json_bytes(native)
    path = f"records/{ids['record']}/blobs/{ids['revision']}.json"
    ref = {"path": path, "content_sha256": digest_imported_bytes(blob), "media_type": "application/json", "size_bytes": len(blob)}
    selected = {"family": "scout_record", "record_id": ids["record"], "revision_id": ids["revision"], "native_kind": "profile_preferences", "scope": native["scope"], "content": {"family": "jsl_blob", "blob_ref": ref, "content_sha256": ref["content_sha256"]}}
    return selected, {path: blob}


def _artifact(name: str, value: bytes) -> dict[str, object]:
    return {"artifact_id": name, "content_sha256": digest_imported_bytes(value), "size_bytes": len(value)}


def _sealed_ref(path: str) -> dict[str, object]:
    return {"path": path, "content_sha256": "sha256:" + "0" * 64, "media_type": "application/json", "size_bytes": 0}


def _optional_inputs() -> list[dict[str, object]]:
    return [
        {"family": "g45_reference", "reference_id": "ref_00000000-0000-4000-8000-000000000074", "record_ref": _sealed_ref("references/ref-074.json"), "snapshot_ref": _sealed_ref("references/ref-074.snapshot.json")},
        {"family": "scout_record", "record_id": "record_00000000-0000-4000-8000-000000000076", "revision_id": "revision_00000000-0000-4000-8000-000000000076", "native_kind": "experience_qa", "scope": {"mode": "saved_default", "task_context_id": None, "base": None}, "content": {"family": "jsl_blob", "blob_ref": _sealed_ref("records/record_00000000-0000-4000-8000-000000000076/blobs/revision_00000000-0000-4000-8000-000000000076.json"), "content_sha256": "sha256:" + "0" * 64}},
        {"family": "scout_record", "record_id": "record_00000000-0000-4000-8000-000000000077", "revision_id": "revision_00000000-0000-4000-8000-000000000077", "content": {"family": "g45_run_input", "run_input_id": "input_00000000-0000-4000-8000-000000000074", "record_ref": _sealed_ref("run-inputs/input-074.json"), "snapshot_ref": _sealed_ref("run-inputs/input-074.snapshot.json")}},
    ]


def _posting_facts(*, sponsorship: str = "known") -> dict[str, object]:
    values = {
        "geography": "Denver, CO",
        "work_mode": "hybrid",
        "seniority": "senior",
        "employment_type": "full-time",
        "compensation": "150000 USD annual",
        "employer_sponsorship": "offers_sponsorship",
        "eligibility": "eligible",
    }
    return {name: {"state": sponsorship if name == "employer_sponsorship" else "known", "value": value if name != "employer_sponsorship" or sponsorship == "known" else None, "source_ids": ["source_job"]} for name, value in values.items()}


def _fixture(*, sponsorship_state: str = "known", discovery_outcome: str = "no_match") -> tuple[dict[str, str], list[dict[str, object]], dict[str, bytes], dict[str, object], dict[str, bytes]]:
    ids = _ids()
    selected, preference_bytes = _selected_profile(sponsorship_state=sponsorship_state)
    supporting = {"job_capture": b"Synthetic dated job snapshot.", "job_review": b"Synthetic independent comparison."}
    posting = {
        "employer": "Example Labs",
        "title": "Forward Deployed Engineer",
        "source_posting_id": "fde-074",
        "observed_date": "2026-09-10",
        "availability": "reported_open",
        "source": {"source_id": "source_job", "locator": "https://jobs.example.test/openings/fde-074", "title": "Synthetic job posting", "publisher": "Example Labs", "published_date": "2026-09-09", "retrieved_date": "2026-09-10", "status": "independently_verified", "capture_ref": _artifact("job_capture", supporting["job_capture"]), "verification": {"method": "independent_review", "evidence_ref": _artifact("job_review", supporting["job_review"]), "actor": {"kind": "reviewer", "id": "synthetic-reviewer"}}},
        "facts": _posting_facts(sponsorship="unknown" if sponsorship_state != "known" else "known"),
    }
    discovery = {"outcome": discovery_outcome, "postings": [posting], "shortlist": [], "exclusions": [], "questions": []}
    return ids, [selected], preference_bytes, discovery, supporting


def _build(module, selected: list[dict[str, object]], preferences: dict[str, bytes], discovery: dict[str, object], supporting: dict[str, bytes]):
    ids = _ids()
    return module.build_discovery_packet(project_id=ids["project"], gig_id=ids["gig"], gig_version=1, graph_id=ids["graph"], graph_version=1, run_id=ids["run"], selected_inputs=selected, preference_bytes=preferences, discovery=discovery, supporting=supporting)


def _match_shortlist(packet) -> dict[str, object]:
    posting = packet.sidecar["discovery"]["postings"][0]
    return {"opportunity_id": posting["opportunity_id"], "snapshot_id": posting["snapshot_id"], "assessment": "agent_reported_match", "reasons": [{"preference_class": "hard", "preference_field": field, "snapshot_id": posting["snapshot_id"], "source_id": "source_job", "detail": f"Synthetic {field} evidence."} for field in ("geography", "work_mode", "seniority", "employment_type", "compensation")] + [{"preference_class": "sponsorship", "preference_field": field, "snapshot_id": posting["snapshot_id"], "source_id": "source_job", "detail": f"Synthetic {field} evidence."} for field in ("sponsorship_need", "employer_sponsorship", "eligibility")], "unresolved_fields": []}


def test_deterministic_match_binds_exact_profile_bytes_and_schema() -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture()
    provisional = _build(module, selected, preferences, discovery, supporting)
    discovery["outcome"] = "matches"
    discovery["shortlist"] = [_match_shortlist(provisional)]
    packet = _build(module, selected, preferences, discovery, supporting)
    assert packet.markdown.startswith(b"# Job discovery: matches\n")
    assert packet.sidecar["selected_inputs"] == selected
    assert Draft202012Validator(json.loads(_SCHEMA.read_text()), format_checker=FormatChecker()).is_valid(packet.sidecar)
    module.validate_rendered_packet(markdown=packet.markdown, sidecar=packet.sidecar, preference_bytes=preferences, supporting=supporting)
    assert packet == _build(module, selected, preferences, discovery, supporting)


@pytest.mark.parametrize("identities", [
    (("a\nb", "c", "https://example.test/job"), ("a", "b\nc", "https://example.test/job")),
    (("a", "https://example.test/job", "https://example.test/other"), ("a", None, "https://example.test/job")),
])
def test_opportunity_identity_preserves_field_and_namespace_boundaries(identities) -> None:
    module = _module()
    assert module._opportunity_id(*identities[0]) != module._opportunity_id(*identities[1])


def test_no_match_unknown_sponsorship_and_stale_duplicate_remain_honest() -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(sponsorship_state="unknown", discovery_outcome="partial")
    first = _build(module, selected, preferences, discovery, supporting)
    snapshot = first.sidecar["discovery"]["postings"][0]
    discovery["shortlist"] = [{"opportunity_id": snapshot["opportunity_id"], "snapshot_id": snapshot["snapshot_id"], "assessment": "agent_reported_partial", "reasons": [{"preference_class": "hard", "preference_field": "geography", "snapshot_id": snapshot["snapshot_id"], "source_id": "source_job", "detail": "Location is supplied evidence."}], "unresolved_fields": ["sponsorship_need"]}]
    packet = _build(module, selected, preferences, discovery, supporting)
    assert packet.sidecar["discovery"]["shortlist"][0]["assessment"] == "agent_reported_partial"
    with pytest.raises(module.DiscoveryPacketError, match="hard or sponsorship"):
        discovery["shortlist"][0]["assessment"] = "agent_reported_match"
        _build(module, selected, preferences, discovery, supporting)
    discovery["shortlist"] = []
    discovery["outcome"] = "no_match"
    discovery["exclusions"] = [{"snapshot_id": snapshot["snapshot_id"], "opportunity_id": snapshot["opportunity_id"], "preference_field": "sponsorship_need", "source_id": "source_job", "reason": "Employer sponsorship is unknown."}]
    no_match = _build(module, selected, preferences, discovery, supporting)
    assert no_match.sidecar["discovery"]["outcome"] == "no_match"


def test_duplicate_snapshot_retains_both_and_normalizes_identity() -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(discovery_outcome="no_match")
    duplicate = deepcopy(discovery["postings"][0])
    duplicate["observed_date"] = "2026-09-11"
    duplicate["availability"] = "stale"
    discovery["postings"].append(duplicate)
    packet = _build(module, selected, preferences, discovery, supporting)
    postings = packet.sidecar["discovery"]["postings"]
    assert len(postings) == 2
    assert postings[0]["opportunity_id"] == postings[1]["opportunity_id"]
    assert {posting["duplicate_of"] for posting in postings} >= {None}
    assert {posting["availability"] for posting in postings} == {"reported_open", "stale"}


@pytest.mark.parametrize("mutator", [
    lambda selected, preferences, discovery, supporting: selected.append(deepcopy(selected[0])),
    lambda selected, preferences, discovery, supporting: preferences.__setitem__("unused", b"bytes"),
    lambda selected, preferences, discovery, supporting: discovery["postings"][0]["source"].__setitem__("locator", "https://[broken"),
    lambda selected, preferences, discovery, supporting: discovery["postings"][0]["facts"]["geography"].__setitem__("source_ids", ["foreign_source"]),
    lambda selected, preferences, discovery, supporting: discovery["postings"][0]["facts"].__setitem__("unknown", {"state": "known", "value": "x", "source_ids": ["source_job"]}),
])
def test_foreign_or_malformed_input_is_refused(mutator) -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(discovery_outcome="no_match")
    mutator(selected, preferences, discovery, supporting)
    with pytest.raises(module.DiscoveryPacketError):
        _build(module, selected, preferences, discovery, supporting)


def test_captured_bytes_and_markdown_text_are_bound_and_inert() -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(discovery_outcome="no_match")
    forged = "Useful text\n## forged heading\n- forged list\n```not code```"
    discovery["postings"][0]["title"] = forged
    packet = _build(module, selected, preferences, discovery, supporting)
    assert packet.markdown.count(b"## Posting snapshots") == 1
    assert b"\\#\\# forged heading" in packet.markdown
    assert b"\\- forged list" in packet.markdown
    tampered = deepcopy(discovery)
    tampered["postings"][0]["source"]["capture_ref"]["content_sha256"] = digest_imported_bytes(b"other")
    with pytest.raises(module.DiscoveryPacketError) as refused:
        _build(module, selected, preferences, tampered, supporting)
    assert refused.value.code == "discovery_packet_artifact_mismatch"


def test_preference_revision_change_creates_new_packet_without_mutating_old_bytes() -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(discovery_outcome="no_match")
    old = _build(module, selected, preferences, discovery, supporting)
    changed_selected, changed_preferences = _selected_profile()
    changed_selected["revision_id"] = "revision_00000000-0000-4000-8000-000000000075"
    old_path = changed_selected["content"]["blob_ref"]["path"]
    new_path = old_path.replace("000000000074", "000000000075")
    content = changed_preferences.pop(old_path)
    changed_selected["content"]["blob_ref"]["path"] = new_path
    changed_preferences[new_path] = content
    newer = _build(module, [changed_selected], changed_preferences, discovery, supporting)
    assert old.markdown == _build(module, selected, preferences, discovery, supporting).markdown
    assert old.sidecar["selected_inputs"][0]["revision_id"] != newer.sidecar["selected_inputs"][0]["revision_id"]


def test_optional_g45_and_experience_envelopes_are_retained_in_exact_order() -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(discovery_outcome="no_match")
    optional = _optional_inputs()
    selected = [optional[0], selected[0], optional[1], optional[2]]
    packet = _build(module, selected, preferences, discovery, supporting)
    assert packet.sidecar["selected_inputs"] == selected
    assert Draft202012Validator(json.loads(_SCHEMA.read_text()), format_checker=FormatChecker()).is_valid(packet.sidecar)
    module.validate_rendered_packet(markdown=packet.markdown, sidecar=packet.sidecar, preference_bytes=preferences, supporting=supporting)


def test_optional_inputs_cannot_supply_or_override_profile_constraints() -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(sponsorship_state="unknown", discovery_outcome="partial")
    optional = _optional_inputs()
    selected = [selected[0], optional[1]]
    provisional = _build(module, selected, preferences, discovery, supporting)
    discovery["shortlist"] = [_match_shortlist(provisional)]
    discovery["outcome"] = "matches"
    with pytest.raises(module.DiscoveryPacketError):
        _build(module, selected, preferences, discovery, supporting)


@pytest.mark.parametrize(("needs_optional", "mutator"), [
    (False, lambda selected: selected.append(deepcopy(selected[0]))),
    (False, lambda selected: selected.append({"family": "unknown"})),
    (True, lambda selected: selected.__setitem__(0, {**selected[0], "record_ref": {**selected[0]["record_ref"], "path": "references/../escape.json"}})),
])
def test_duplicate_profile_and_malformed_optional_inputs_are_refused(needs_optional: bool, mutator) -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(discovery_outcome="no_match")
    optional = _optional_inputs()
    selected = [optional[0], selected[0]] if needs_optional else [selected[0]]
    mutator(selected)
    with pytest.raises(module.DiscoveryPacketError):
        _build(module, selected, preferences, discovery, supporting)


def test_title_only_never_establishes_duplicate_identity() -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(discovery_outcome="no_match")
    first = discovery["postings"][0]
    first["source_posting_id"] = None
    second = deepcopy(first)
    second["source"]["locator"] = "https://jobs.example.test/openings/different-source-id"
    discovery["postings"].append(second)
    packet = _build(module, selected, preferences, discovery, supporting)
    postings = packet.sidecar["discovery"]["postings"]
    assert len({posting["opportunity_id"] for posting in postings}) == 2
    assert all(posting["duplicate_of"] is None for posting in postings)


def test_rendered_packet_rejects_tampered_data_and_digests() -> None:
    module = _module()
    _ids_value, selected, preferences, discovery, supporting = _fixture(discovery_outcome="no_match")
    packet = _build(module, selected, preferences, discovery, supporting)
    tampered = deepcopy(packet.sidecar)
    tampered["discovery"]["postings"][0]["title"] = "Changed without markdown"
    with pytest.raises(module.DiscoveryPacketError):
        module.validate_rendered_packet(markdown=packet.markdown, sidecar=tampered, preference_bytes=preferences, supporting=supporting)
    optional = _optional_inputs()
    selected = [optional[0], selected[0]]
    packet = _build(module, selected, preferences, discovery, supporting)
    tampered = deepcopy(packet.sidecar)
    tampered["selected_inputs"][0]["reference_id"] = "ref_00000000-0000-4000-8000-000000000075"
    with pytest.raises(module.DiscoveryPacketError):
        module.validate_rendered_packet(markdown=packet.markdown, sidecar=tampered, preference_bytes=preferences, supporting=supporting)
    tampered = deepcopy(packet.sidecar)
    tampered["discovery"]["postings"][0]["facts"]["compensation"]["value"] = "999999 USD annual"
    with pytest.raises(module.DiscoveryPacketError):
        module.validate_rendered_packet(markdown=packet.markdown, sidecar=tampered, preference_bytes=preferences, supporting=supporting)
    with pytest.raises(module.DiscoveryPacketError):
        module.validate_rendered_packet(markdown=packet.markdown + b"x", sidecar=packet.sidecar, preference_bytes=preferences, supporting=supporting)
