from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, is_dataclass
from typing import Any

import pytest

from gigai.canonical import canonical_json_bytes
from gigai.scout.find_jobs.contracts import (
    API_BIND,
    ROUTES,
    AcquireInput,
    AcquireOutput,
    AggregateStatus,
    ArtifactRef,
    AssessmentResult,
    AssessInput,
    AssessOutput,
    ConfigRequest,
    ConfigResponse,
    ConsentActor,
    EditedURL,
    FailureRow,
    FindJobsConfig,
    FindJobsContractError,
    FindJobsRunInput,
    GoalError,
    ModelTarget,
    NodeContext,
    NodeFailure,
    NodeReceipt,
    NodeReceiptFixture,
    NotAssessedReason,
    NotAssessedRow,
    PinnedResume,
    PostingRow,
    PostingRowResult,
    PresentInput,
    PresentOutput,
    PresentPayload,
    Producer,
    RequirementMatrixRow,
    RowOutcome,
    RouteSpec,
    RunLookupRequest,
    RunRequest,
    RunResponse,
    RunResultsResponse,
    RunStatusResponse,
    SelectedPosting,
    SelectionReason,
    SourceToggles,
    SponsorshipStatus,
    UIConsentEnvelope,
    URLObservation,
    URLSetDiff,
    UsageBlock,
    WatchlistEntry,
    WatchlistFirstSeen,
    WatchlistFixture,
    aggregate_status,
    diff_url_sets,
    normalize_url,
    parse_board_url,
)
from gigai.validators import validate_serialized_contract

from .conftest import load_fixture


FIXTURE_TYPES: dict[str, type[Any]] = {
    "fixture-find-jobs-config-v1.json": FindJobsConfig,
    "fixture-run-input-v1.json": FindJobsRunInput,
    "fixture-acquire-input-v1.json": AcquireInput,
    "fixture-watchlist-v1.json": WatchlistFixture,
    "fixture-acquire-batch-v1.json": AcquireOutput,
    "fixture-assessment-v1.json": AssessOutput,
    "fixture-assessment-model-unavailable-v1.json": AssessOutput,
    "fixture-assessment-model-denied-v1.json": AssessOutput,
    "fixture-ui-consent-v1.json": UIConsentEnvelope,
    "fixture-node-receipts-v1.json": NodeReceiptFixture,
    "fixture-present-payload-v1.json": PresentPayload,
    "fixture-api-config-request-v1.json": ConfigRequest,
    "fixture-api-config-response-v1.json": ConfigResponse,
    "fixture-api-run-request-v1.json": RunRequest,
    "fixture-api-run-response-v1.json": RunResponse,
    "fixture-api-run-status-response-v1.json": RunStatusResponse,
    "fixture-api-run-results-response-v1.json": RunResultsResponse,
}


def _node_payload() -> dict[str, Any]:
    return load_fixture("fixture-node-receipts-v1.json")["receipts"][0]  # type: ignore[index,return-value]


def _present_payload() -> dict[str, Any]:
    return load_fixture("fixture-present-payload-v1.json")


def _assess_input() -> dict[str, Any]:
    acquire = load_fixture("fixture-acquire-batch-v1.json")
    selected = acquire["selected_postings"]  # type: ignore[index]
    return {
        "schema_version": "scout-find-jobs-assess-input:1",
        "acquire_batch_ref": "records/scout-acquisition/batch-001/input.json",
        "acquire_output_digest": AcquireOutput.from_json(acquire).digest(),
        "selected_postings": selected,
        "selection_cap": 2,
        "selection_reasons": {
            selected[0]["normalized_url"]: "edited",  # type: ignore[index]
            selected[1]["normalized_url"]: "new",  # type: ignore[index]
        },
        "pinned_resume": {
            "record_id": "resume-record-001",
            "revision_id": "resume-revision-007",
            "content_sha256": "sha256:9999999999999999999999999999999999999999999999999999999999999999",
        },
        "target": "targets/acme",
        "model_target": "ollama_local",
        "answer_association_version": "scout-answer-association:1",
    }


@pytest.mark.parametrize("filename,contract_type", FIXTURE_TYPES.items())
def test_every_v1_fixture_round_trips(filename: str, contract_type: type[Any]) -> None:
    value = load_fixture(filename)
    contract = contract_type.from_json(value)
    assert contract.to_json() == value
    assert contract.digest().startswith("sha256:")
    assert is_dataclass(contract)


def test_digest_is_key_order_independent() -> None:
    value = load_fixture("fixture-find-jobs-config-v1.json")
    reversed_value = dict(reversed(list(value.items())))
    assert FindJobsConfig.from_json(value).digest() == FindJobsConfig.from_json(reversed_value).digest()


def test_run_input_rejects_config_digest_mismatch() -> None:
    value = load_fixture("fixture-run-input-v1.json")
    value["config_digest"] = "sha256:" + "0" * 64
    with pytest.raises(FindJobsContractError) as raised:
        FindJobsRunInput.from_json(value)
    assert raised.value.code == "invalid_value"


def test_contract_dataclasses_are_frozen() -> None:
    config = FindJobsConfig.from_json(load_fixture("fixture-find-jobs-config-v1.json"))
    with pytest.raises(FrozenInstanceError):
        config.roles = ("changed",)  # type: ignore[misc]


# --- C0 (v0.1.8.1): old-shape parsing and digest stability -----------------
#
# U12/U19/U20/U22/U25 add optional fields to frozen find-jobs contracts.
# Every existing serialized find-jobs-config:1 / acquire / assess payload
# (an operator's already-written find-jobs.json, or a prior run's on-disk
# JSON) must keep parsing with defaults, and an old config's digest must
# stay byte-for-byte the same so the graph doesn't think it changed.


def test_old_shape_config_without_new_keys_parses_with_defaults() -> None:
    old_value = load_fixture("fixture-find-jobs-config-v1.json")
    assert "countries" not in old_value and "visa_sponsorship_required" not in old_value
    config = FindJobsConfig.from_json(old_value)
    assert config.countries == ()
    assert config.visa_sponsorship_required is False
    assert config.to_json() == old_value


def test_old_shape_config_digest_is_unchanged_by_c0() -> None:
    old_value = load_fixture("fixture-find-jobs-config-v1.json")
    config = FindJobsConfig.from_json(old_value)
    # Frozen before C0 (v0.1.8.1); a regression here would mean an
    # unmodified operator find-jobs.json now digests differently.
    assert config.digest() == "sha256:e14f80205b4dda9cdcd2f594c2eb341c224c4ee2a10d5765e0848df1db6e4dee"


def test_config_countries_and_visa_sponsorship_round_trip() -> None:
    new_value = load_fixture("fixture-find-jobs-config-v1-sponsorship.json")
    config = FindJobsConfig.from_json(new_value)
    assert config.countries == ("US",)
    assert config.visa_sponsorship_required is True
    assert config.to_json() == new_value
    # A non-default value changes the digest relative to the old shape,
    # since the logical config actually differs.
    old_config = FindJobsConfig.from_json(load_fixture("fixture-find-jobs-config-v1.json"))
    assert config.digest() != old_config.digest()


def test_config_countries_rejects_non_iso_codes() -> None:
    value = deepcopy(load_fixture("fixture-find-jobs-config-v1.json"))
    value["countries"] = ["USA"]
    with pytest.raises(FindJobsContractError) as raised:
        FindJobsConfig.from_json(value)
    assert raised.value.code == "invalid_value"


def test_config_visa_sponsorship_required_rejects_wrong_type() -> None:
    value = deepcopy(load_fixture("fixture-find-jobs-config-v1.json"))
    value["visa_sponsorship_required"] = "yes"
    with pytest.raises(FindJobsContractError) as raised:
        FindJobsConfig.from_json(value)
    assert raised.value.code == "wrong_type"


def test_old_shape_posting_row_without_text_or_sponsorship_parses() -> None:
    acquire = load_fixture("fixture-acquire-batch-v1.json")
    old_posting = acquire["rows"][0]["posting"]  # type: ignore[index]
    assert "text" not in old_posting and "sponsorship" not in old_posting
    row = PostingRow.from_json(old_posting)
    assert row.text is None
    assert row.sponsorship is None
    assert row.to_json() == old_posting


def test_posting_row_text_and_sponsorship_round_trip() -> None:
    acquire = load_fixture("fixture-acquire-batch-v1.json")
    old_posting = deepcopy(acquire["rows"][0]["posting"])  # type: ignore[index]
    old_posting["text"] = "We are hiring a Software Engineer. No sponsorship available."
    old_posting["sponsorship"] = "not_offered"
    row = PostingRow.from_json(old_posting)
    assert row.text == "We are hiring a Software Engineer. No sponsorship available."
    assert row.sponsorship is SponsorshipStatus.NOT_OFFERED
    assert row.to_json() == old_posting


def test_posting_row_sponsorship_rejects_bad_enum() -> None:
    acquire = load_fixture("fixture-acquire-batch-v1.json")
    posting = deepcopy(acquire["rows"][0]["posting"])  # type: ignore[index]
    posting["sponsorship"] = "maybe"
    with pytest.raises(FindJobsContractError) as raised:
        PostingRow.from_json(posting)
    assert raised.value.code == "bad_enum"


def test_old_shape_assessment_result_without_sponsorship_parses() -> None:
    assessment = load_fixture("fixture-assessment-v1.json")
    old_result = assessment["assessments"][0]  # type: ignore[index]
    assert "sponsorship" not in old_result
    result = AssessmentResult.from_json(old_result)
    assert result.sponsorship is None
    assert result.to_json() == old_result


def test_assessment_result_sponsorship_round_trips() -> None:
    assessment = load_fixture("fixture-assessment-v1.json")
    value = deepcopy(assessment["assessments"][0])  # type: ignore[index]
    value["sponsorship"] = "offered"
    result = AssessmentResult.from_json(value)
    assert result.sponsorship is SponsorshipStatus.OFFERED
    assert result.to_json() == value


def test_not_assessed_reason_accepts_new_c0_values() -> None:
    assessment = load_fixture("fixture-assessment-v1.json")
    for reason in ("location_mismatch", "sponsorship_excluded", "model_output_invalid"):
        value = deepcopy(assessment["not_assessed"][0])  # type: ignore[index]
        value["reason"] = reason
        row = NotAssessedRow.from_json(value)
        assert row.reason is NotAssessedReason(reason)
        assert row.to_json() == value


def _public_dto_cases() -> list[tuple[str, type[Any], dict[str, Any]]]:
    config = load_fixture("fixture-find-jobs-config-v1.json")
    acquire_input = load_fixture("fixture-acquire-input-v1.json")
    run_input = load_fixture("fixture-run-input-v1.json")
    acquire_output = load_fixture("fixture-acquire-batch-v1.json")
    assessment = load_fixture("fixture-assessment-v1.json")
    present = _present_payload()
    node = _node_payload()
    consent = load_fixture("fixture-ui-consent-v1.json")
    api_config = load_fixture("fixture-api-config-response-v1.json")
    api_run = load_fixture("fixture-api-run-request-v1.json")
    api_run_response = load_fixture("fixture-api-run-response-v1.json")
    api_status = load_fixture("fixture-api-run-status-response-v1.json")
    api_results = load_fixture("fixture-api-run-results-response-v1.json")
    return [
        ("source_toggles", SourceToggles, config["sources"]),  # type: ignore[arg-type]
        ("find_jobs_config", FindJobsConfig, config),
        ("find_jobs_run_input", FindJobsRunInput, run_input),
        ("posting_row", PostingRow, acquire_output["rows"][0]["posting"]),  # type: ignore[index]
        ("posting_row_result", PostingRowResult, acquire_output["rows"][0]),  # type: ignore[index]
        ("failure_row", FailureRow, acquire_output["failures"][0]),  # type: ignore[index]
        ("watchlist_first_seen", WatchlistFirstSeen, load_fixture("fixture-watchlist-v1.json")["entries"][0]["first_seen"]),  # type: ignore[index]
        ("watchlist_entry", WatchlistEntry, load_fixture("fixture-watchlist-v1.json")["entries"][0]),  # type: ignore[index]
        ("watchlist_fixture", WatchlistFixture, load_fixture("fixture-watchlist-v1.json")),
        ("node_context", NodeContext, {
            "schema_version": "scout-node-context:1",
            "run_id": "run_123e4567-e89b-42d3-a456-426614174002",
            "project_id": "project_123e4567-e89b-42d3-a456-426614174000",
            "gig_id": "gig_123e4567-e89b-42d3-a456-426614174001",
            "graph_id": "find-jobs:functional:1",
            "graph_version": 1,
            "goal_slug": "acquire",
            "manifest_digest": "sha256:" + "1" * 64,
            "operation_key": "find-jobs:acquire:001",
            "target_observation_digest": "sha256:" + "2" * 64,
            "workpad_path": "workpads/scout",
            "redeemed_consent_ref": "records/consent/001.json",
            "model_target": "ollama_local",
        }),
        ("selected_posting", SelectedPosting, acquire_output["selected_postings"][0]),  # type: ignore[index]
        ("selection_reason", SelectionReason, {"normalized_url": "https://jobs.example.test/1", "reason": "new"}),
        ("pinned_resume", PinnedResume, _present_payload()["pinned_resume"]),  # type: ignore[arg-type]
        ("acquire_input", AcquireInput, acquire_input),
        ("url_observation", URLObservation, acquire_output["url_set_diff"]["added"][0]),  # type: ignore[index]
        ("edited_url", EditedURL, acquire_output["url_set_diff"]["edited"][0]),  # type: ignore[index]
        ("url_set_diff", URLSetDiff, acquire_output["url_set_diff"]),  # type: ignore[arg-type]
        ("acquire_output", AcquireOutput, acquire_output),
        ("requirement_matrix_row", RequirementMatrixRow, assessment["assessments"][0]["matrix"][0]),  # type: ignore[index]
        ("not_assessed_row", NotAssessedRow, assessment["not_assessed"][0]),  # type: ignore[index]
        ("assessment_result", AssessmentResult, assessment["assessments"][0]),  # type: ignore[index]
        ("usage_block", UsageBlock, node["usage"]),
        ("producer", Producer, node["producer"]),
        ("goal_error", GoalError, {
            "code": "model_unavailable",
            "message": "local model was unavailable",
            "retryable": True,
            "invocation_id": None,
        }),
        ("artifact_ref", ArtifactRef, node["evidence"][0]),  # type: ignore[index]
        ("node_failure", NodeFailure, {"code": "model_unavailable", "message": "local model was unavailable"}),
        ("node_receipt", NodeReceipt, node),
        ("node_receipt_fixture", NodeReceiptFixture, load_fixture("fixture-node-receipts-v1.json")),
        ("assess_input", AssessInput, _assess_input()),
        ("assess_output", AssessOutput, assessment),
        ("present_input", PresentInput, {
            "schema_version": "scout-find-jobs-present-input:1",
            "batch_ref": "records/scout-acquisition/batch-001/input.json",
            "assessment_ref": "records/scout-assessment/revision-001.json",
            "node_receipts": load_fixture("fixture-node-receipts-v1.json")["receipts"],
        }),
        ("present_payload", PresentPayload, present),
        ("present_output", PresentOutput, {
            "schema_version": "scout-find-jobs-present-output:1",
            "payload": present,
            "aggregate_status": "interrupted",
        }),
        ("consent_actor", ConsentActor, consent["actor"]),  # type: ignore[index]
        ("ui_consent", UIConsentEnvelope, consent),
        ("config_request", ConfigRequest, load_fixture("fixture-api-config-request-v1.json")),
        ("config_response", ConfigResponse, api_config),
        ("run_request", RunRequest, api_run),
        ("run_response", RunResponse, api_run_response),
        ("run_lookup_request", RunLookupRequest, {"run_id": api_run_response["run_id"]}),  # type: ignore[index]
        ("run_status_response", RunStatusResponse, api_status),
        ("run_results_response", RunResultsResponse, api_results),
    ]


def _wrong_type(value: object) -> object:
    if type(value) is bool:
        return "not-a-boolean"
    if type(value) is int:
        return "not-an-integer"
    if type(value) is str:
        return 42
    if type(value) is list:
        return {}
    if type(value) is dict:
        return []
    if value is None:
        return {}
    return []


@pytest.mark.parametrize("case_id,dto_type,payload", _public_dto_cases(), ids=lambda item: item if isinstance(item, str) else None)
@pytest.mark.parametrize("mutation", ("unknown_key", "missing_key", "wrong_type"))
def test_every_public_dto_rejects_closed_set_mutations(case_id: str, dto_type: type[Any], payload: dict[str, Any], mutation: str) -> None:
    value = deepcopy(payload)
    if mutation == "unknown_key":
        value["unexpected"] = True
    elif mutation == "missing_key":
        value.pop(next(iter(value)))
    else:
        key = next((item for item in value if item != "schema_version"), next(iter(value)))
        value[key] = _wrong_type(value[key])
    with pytest.raises(FindJobsContractError) as raised:
        dto_type.from_json(value)
    assert raised.value.code in {"unknown_key", "missing_key", "wrong_type", "bad_enum", "invalid_value"}, case_id


ENUM_MUTATIONS: tuple[tuple[str, type[Any], str, str], ...] = (
    ("find_jobs_config", FindJobsConfig, "default_model_target", "not-a-model"),
    ("find_jobs_run_input", FindJobsRunInput, "model_target", "not-a-model"),
    ("posting_row", PostingRow, "provider", "not-a-provider"),
    ("posting_row_result", PostingRowResult, "outcome", "not-an-outcome"),
    ("failure_row", FailureRow, "source_kind", "not-a-source"),
    ("watchlist_first_seen", WatchlistFirstSeen, "source_kind", "not-a-source"),
    ("watchlist_entry", WatchlistEntry, "provider", "not-a-provider"),
    ("node_context", NodeContext, "model_target", "not-a-model"),
    ("selection_reason", SelectionReason, "reason", "not-a-reason"),
    ("acquire_input", AcquireInput, "selection_rule", "not-a-rule"),
    ("acquire_output", AcquireOutput, "progress_status", "not-a-progress"),
    ("requirement_matrix_row", RequirementMatrixRow, "status", "not-a-matrix-status"),
    ("not_assessed_row", NotAssessedRow, "reason", "not-a-reason"),
    ("producer", Producer, "model_target", "not-a-model"),
    ("node_receipt", NodeReceipt, "status", "not-a-node-status"),
    ("assess_input", AssessInput, "model_target", "not-a-model"),
    ("assess_output", AssessOutput, "model_target", "not-a-model"),
    ("present_payload", PresentPayload, "status", "not-an-aggregate-status"),
    ("present_output", PresentOutput, "aggregate_status", "not-an-aggregate-status"),
    ("consent_actor", ConsentActor, "kind", "not-an-actor"),
    ("ui_consent", UIConsentEnvelope, "action", "not-an-action"),
    ("run_request", RunRequest, "selection_rule", "not-a-rule"),
    ("run_response", RunResponse, "status", "not-an-aggregate-status"),
    ("run_status_response", RunStatusResponse, "status", "not-an-aggregate-status"),
)


@pytest.mark.parametrize("case_id,dto_type,key,bad_value", ENUM_MUTATIONS, ids=lambda item: item if isinstance(item, str) else None)
def test_every_public_enum_rejects_bad_enum(case_id: str, dto_type: type[Any], key: str, bad_value: str) -> None:
    payload = next(payload for item_id, _, payload in _public_dto_cases() if item_id == case_id)
    payload = deepcopy(payload)
    payload[key] = bad_value
    with pytest.raises(FindJobsContractError) as raised:
        dto_type.from_json(payload)
    assert raised.value.code == "bad_enum", case_id


def test_aggregate_status_uses_all_precedence_levels() -> None:
    cases = (
        ([], "pending"),
        (["ready"], "pending"),
        (["complete"], "succeeded"),
        (["complete", "running"], "running"),
        (["complete", "waiting_for_gate"], "running"),
        (["complete", "verifying"], "running"),
        (["pending", "cancelled"], "cancelled"),
        (["blocked", "cancelled"], "blocked"),
        (["failed", "blocked"], "failed"),
        (["interrupted", "failed"], "interrupted"),
        (["cancelled", "pending"], "cancelled"),
        (["complete", "complete"], "succeeded"),
    )
    for statuses, expected in cases:
        assert aggregate_status(statuses) == expected
    assert set(AggregateStatus) == {
        AggregateStatus.PENDING,
        AggregateStatus.RUNNING,
        AggregateStatus.SUCCEEDED,
        AggregateStatus.FAILED,
        AggregateStatus.BLOCKED,
        AggregateStatus.CANCELLED,
        AggregateStatus.INTERRUPTED,
    }


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme")),
        ("https://job-boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme")),
        ("https://jobs.lever.co/acme/123", ("lever", "acme")),
        ("https://jobs.ashbyhq.com/acme/123", ("ashby", "acme")),
        ("https://boards.greenhouse.io/embed/job_board?for=acme", ("greenhouse", "acme")),
        ("https://job-boards.greenhouse.io/embed/job_board?for=acme", ("greenhouse", "acme")),
        ("https://boards.greenhouse.io/embed/not-a-board?for=acme", None),
        ("https://example.com/acme", None),
        ("https://boards.greenhouse.io@evil.test/acme", None),
    ],
)
def test_parse_board_url(url: str, expected: tuple[str, str] | None) -> None:
    assert parse_board_url(url) == expected


def test_url_normalization_covers_malformed_userinfo_ports_fragments_tracking_and_dedup() -> None:
    assert normalize_url("https://EXAMPLE.test:8443/jobs/123/?b=2&utm_source=x&a=1#details") == "https://example.test:8443/jobs/123?a=1&b=2"
    assert normalize_url("https://example.test/jobs/123/") == normalize_url("https://example.test/jobs/123")
    with pytest.raises(FindJobsContractError) as malformed:
        normalize_url("not-a-url")
    assert malformed.value.code == "invalid_value"
    with pytest.raises(FindJobsContractError) as userinfo:
        normalize_url("https://user:secret@example.test/jobs/123")
    assert userinfo.value.code == "invalid_value"
    with pytest.raises(FindJobsContractError) as bad_port:
        normalize_url("https://example.test:99999/jobs/123")
    assert bad_port.value.code == "invalid_value"
    diff = diff_url_sets(
        {
            "https://example.test/jobs/1/?utm_source=repeat": "sha256:" + "a" * 64,
            "https://example.test/jobs/1": "sha256:" + "a" * 64,
        },
        {"https://example.test/jobs/1#fragment": "sha256:" + "b" * 64},
    )
    assert len(diff.edited) == 1
    assert not diff.added and not diff.removed and not diff.unchanged


def test_assess_input_seals_acquire_selection_identity_and_cap() -> None:
    valid = _assess_input()
    assert AssessInput.from_json(valid).to_json() == valid
    missing_reason = deepcopy(valid)
    missing_reason["selection_reasons"].pop(next(iter(missing_reason["selection_reasons"])))
    duplicate = deepcopy(valid)
    duplicate["selected_postings"].append(deepcopy(duplicate["selected_postings"][0]))
    over_cap = deepcopy(valid)
    over_cap["selection_cap"] = 1
    role_mismatch = deepcopy(valid)
    role_mismatch["selected_postings"][0]["role_match"] = False
    for value in (missing_reason, duplicate, over_cap, role_mismatch):
        with pytest.raises(FindJobsContractError) as raised:
            AssessInput.from_json(value)
        assert raised.value.code == "invalid_value"


def test_assessment_accounting_rejects_overlap_and_missing_candidates() -> None:
    valid = load_fixture("fixture-assessment-v1.json")
    assert AssessOutput.from_json(valid)
    overlap = deepcopy(valid)
    overlap["not_assessed"][0]["posting"] = deepcopy(overlap["candidate_rows"][0]["posting"])
    with pytest.raises(FindJobsContractError) as raised:
        AssessOutput.from_json(overlap)
    assert raised.value.code == "invalid_value"
    missing = deepcopy(valid)
    missing["candidate_rows"] = []
    with pytest.raises(FindJobsContractError) as raised:
        AssessOutput.from_json(missing)
    assert raised.value.code == "invalid_value"


def test_present_payload_has_pinned_resume_and_output_status_agreement() -> None:
    payload = PresentPayload.from_json(_present_payload())
    assert payload.pinned_resume is not None
    assert payload.rows and payload.assessments and payload.not_assessed and len(payload.node_receipts) == 3
    output = PresentOutput.from_json({
        "schema_version": "scout-find-jobs-present-output:1",
        "payload": payload.to_json(),
        "aggregate_status": payload.status.value,
    })
    assert output.aggregate_status is payload.status
    mismatched = output.to_json()
    mismatched["aggregate_status"] = "pending"
    with pytest.raises(FindJobsContractError) as raised:
        PresentOutput.from_json(mismatched)
    assert raised.value.code == "invalid_value"


def test_run_results_payload_identity_is_sealed() -> None:
    value = load_fixture("fixture-api-run-results-response-v1.json")
    assert RunResultsResponse.from_json(value)
    mismatch = deepcopy(value)
    mismatch["payload"]["run_id"] = "run_123e4567-e89b-42d3-a456-426614174003"
    with pytest.raises(FindJobsContractError) as raised:
        RunResultsResponse.from_json(mismatch)
    assert raised.value.code == "invalid_value"


def test_node_receipt_to_goal_details_is_lossless_against_run_details_schema() -> None:
    receipt = NodeReceiptFixture.from_json(load_fixture("fixture-node-receipts-v1.json")).receipts[0]
    goal = receipt.to_goal_details()
    goal_id = goal["goal_id"]
    run = {
        "schema_version": "1.0",
        "run_id": "run_123e4567-e89b-42d3-a456-426614174002",
        "gig_id": "gig_123e4567-e89b-42d3-a456-426614174001",
        "gig_version": 1,
        "goal_graph_sha256": "sha256:" + "3" * 64,
        "status": "succeeded",
        "started_at": "2026-09-22T00:00:00Z",
        "finished_at": "2026-09-22T00:00:01Z",
        "goal_sets": {"pending": [], "ready": [], "active": [], "complete": [goal_id], "failed": [], "blocked": [], "gated": [], "cancelled": []},
        "goals": [goal],
        "critical_path": [goal_id],
        "realized_max_parallel_goals": 1,
        "execution_summary": "",
        "tool_errors": [],
        "model_errors": [],
        "aggregate_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": None, "currency": None, "cost_status": "not_applicable"},
        "remaining_budget": {"max_model_calls": 10, "max_tool_calls": 10, "max_tokens": 1000, "max_cost": None, "currency": None, "max_wall_time_ms": 1000, "max_parallel_goals": 1},
        "target_before": {"path": "targets/before.md", "content_sha256": "sha256:" + "4" * 64, "media_type": "text/markdown", "size_bytes": 1},
        "target_after": None,
        "completion_audit": {"status": "missing", "path": None},
        "terminal_handoff": None,
        "workpad_commit": None,
        "next_actions": [],
    }
    report = validate_serialized_contract("run-details.schema.json", canonical_json_bytes(run))
    assert report.valid, report.as_dict()
    interrupted = NodeReceipt.from_json(load_fixture("fixture-node-receipts-v1.json")["receipts"][2])  # type: ignore[index]
    with pytest.raises(FindJobsContractError) as raised:
        interrupted.to_goal_details()
    assert raised.value.code == "invalid_value"


def test_routes_bind_types_methods_paths_and_statuses_exactly() -> None:
    assert API_BIND == ("127.0.0.1", 8765)
    assert ROUTES == (
        RouteSpec("GET", "/api/config", ConfigRequest, ConfigResponse, (200, 404, 422)),
        RouteSpec("POST", "/api/run", RunRequest, RunResponse, (202, 400, 403, 409, 422, 504)),
        RouteSpec("GET", "/api/runs/{run_id}", RunLookupRequest, RunStatusResponse, (200, 404)),
        RouteSpec("GET", "/api/runs/{run_id}/results", RunLookupRequest, RunResultsResponse, (200, 404)),
    )


def test_effect_sets_and_model_target_are_frozen() -> None:
    from gigai.scout.find_jobs.contracts import (
        ACQUIRE_EFFECTS,
        ASSESS_EFFECTS,
        ASSESS_LOCAL_EFFECTS,
        PRESENT_EFFECTS,
    )

    assert ACQUIRE_EFFECTS == {"network_read", "credential_use", "write_workpad"}
    assert ASSESS_EFFECTS == ACQUIRE_EFFECTS
    assert ASSESS_LOCAL_EFFECTS == {"write_workpad"}
    assert PRESENT_EFFECTS == {"write_workpad"}
    assert set(ModelTarget) == {ModelTarget.OLLAMA_LOCAL, ModelTarget.CODEX_CLI, ModelTarget.OPENROUTER_API}
    assert not hasattr(RowOutcome, "PENDING")
