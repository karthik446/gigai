"""Pure bridge tests; these do not prove journal or installed-CLI integration."""

from copy import deepcopy
from importlib import import_module

import pytest

from gigai.scout_discovery import ScoutDiscoveryError, validate_discovery_domain
from tests.test_scout07_discovery_packet import _build, _fixture, _optional_inputs


def _case():
    ids, selected, preferences, discovery, supporting = _fixture()
    selected.extend(_optional_inputs())
    renderer = import_module(
        "gigai.data.scout.tools.cap_00000000-0000-4000-8000-000000000074.discovery"
    )
    packet = _build(renderer, selected, preferences, discovery, supporting)
    return {
        "value": packet.sidecar, "markdown": packet.markdown,
        "supporting": supporting, "preference_bytes": preferences,
        "run_id": ids["run"], "project_id": ids["project"], "gig_id": ids["gig"],
        "gig_version": 1, "graph_id": ids["graph"], "graph_selector": "find-jobs",
        "graph_version": 1, "selected_inputs": selected,
    }


def test_bridge_accepts_exact_profile_optional_inputs_and_evidence():
    validate_discovery_domain(**_case())


@pytest.mark.parametrize("field", [
    "project_id", "gig_id", "run_id", "graph_id", "graph_selector",
    "gig_version", "graph_version",
])
def test_bridge_refuses_foreign_origin(field):
    case = _case()
    case[field] = 2 if field.endswith("version") else "other"
    with pytest.raises(ScoutDiscoveryError) as error:
        validate_discovery_domain(**case)
    assert error.value.code == "discovery_domain_origin_mismatch"


def test_bridge_refuses_changed_optional_input_even_without_reading_its_bytes():
    case = _case()
    case["selected_inputs"] = deepcopy(case["selected_inputs"])
    case["selected_inputs"][1]["snapshot_ref"]["content_sha256"] = "sha256:" + "1" * 64
    with pytest.raises(ScoutDiscoveryError) as error:
        validate_discovery_domain(**case)
    assert error.value.code == "discovery_domain_input_mismatch"


@pytest.mark.parametrize("mutation", [
    "missing_profile", "changed_profile", "extra_profile", "missing_capture",
    "changed_capture", "extra_capture", "markdown", "nested_type", "extra_field",
])
def test_bridge_refuses_corrupt_or_incomplete_evidence_with_typed_errors(mutation):
    case = _case()
    if mutation == "missing_profile":
        case["preference_bytes"] = {}
    elif mutation == "changed_profile":
        case["preference_bytes"] = {name: b"changed" for name in case["preference_bytes"]}
    elif mutation == "extra_profile":
        case["preference_bytes"]["records/unused.json"] = b"unused"
    elif mutation == "missing_capture":
        case["supporting"].pop("job_capture")
    elif mutation == "changed_capture":
        case["supporting"]["job_capture"] = b"changed"
    elif mutation == "extra_capture":
        case["supporting"]["unused"] = b"unused"
    elif mutation == "markdown":
        case["markdown"] += b"changed"
    elif mutation == "nested_type":
        case["value"]["discovery"]["postings"][0]["facts"]["geography"]["source_ids"] = [{}]
    else:
        case["value"]["unknown"] = "untrusted"
    with pytest.raises(ScoutDiscoveryError) as error:
        validate_discovery_domain(**case)
    assert error.value.code == "discovery_domain_invalid"
    assert "untrusted" not in str(error.value)


def test_bridge_has_no_editable_module_callback():
    case = _case()
    with pytest.raises(TypeError):
        validate_discovery_domain(**case, validator_source="user_gig.evil")
