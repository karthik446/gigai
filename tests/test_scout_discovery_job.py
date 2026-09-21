from __future__ import annotations

import pytest

from gigai.scout_discovery_job import DiscoveryJob, run_bounded_public_import


def test_discovery_job_exposes_public_acquisition_fields_only() -> None:
    value = DiscoveryJob(
        opportunity_id="opportunity_" + "a" * 32,
        snapshot_id="snapshot_" + "b" * 32,
        acquisition_state="considered",
        source_kind="agent_discovered",
        title="Synthetic role",
        employer="Synthetic employer",
        public_source={"locator": "https://jobs.example.invalid/role", "status": "captured"},
        provenance={"run_ref": {"path": "runs/example"}},
    )
    assert value.acquisition_state == "considered"
    assert value.source_kind == "agent_discovered"
    with pytest.raises(TypeError):
        value.public_source["x"] = "y"  # type: ignore[index]


def test_discovery_job_rejects_private_assessment_state() -> None:
    with pytest.raises(ValueError, match="acquisition state"):
        DiscoveryJob(
            opportunity_id="opportunity_" + "a" * 32,
            snapshot_id="snapshot_" + "b" * 32,
            acquisition_state="fit_rejected",
            source_kind="agent_discovered",
            title=None,
            employer=None,
            public_source={},
            provenance={},
        )


def test_bounded_public_import_preserves_considered_duplicate_failure_and_exclusion() -> None:
    rows = [
        {"opportunity_id": "opportunity_a", "snapshot_id": "snapshot_1", "title": "A"},
        {"opportunity_id": "opportunity_a", "snapshot_id": "snapshot_1", "duplicate_of": "opportunity_a"},
        {"opportunity_id": "opportunity_b", "snapshot_id": "snapshot_2", "error": "timeout"},
        {"opportunity_id": "opportunity_c", "snapshot_id": "snapshot_3", "acquisition_state": "excluded", "excluded_reason": "robots"},
    ]
    result = run_bounded_public_import(rows, deadline_seconds=10, clock=lambda: 0)
    assert result.stopped_reason == "completed"
    assert [item["opportunity_id"] for item in result.considered] == ["opportunity_a"]
    assert [item["reason"] for item in result.duplicates] == ["duplicate"]
    assert [item["reason"] for item in result.failures] == ["acquisition_failed"]
    assert [item["reason"] for item in result.exclusions] == ["acquisition_excluded"]
    assert result.to_json()["schema_version"] == "scout-public-import-progress:1"


def test_bounded_public_import_deadline_reports_next_index_without_private_fields() -> None:
    ticks = iter([0.0, 0.0, 2.0])
    rows = [
        {"opportunity_id": "opportunity_a", "snapshot_id": "snapshot_1"},
        {"opportunity_id": "opportunity_b", "snapshot_id": "snapshot_2"},
    ]
    result = run_bounded_public_import(rows, deadline_seconds=1, clock=lambda: next(ticks))
    assert result.stopped_reason == "deadline"
    assert result.processed == 1
    assert result.next_index == 1
    assert "salary" not in result.to_json()
