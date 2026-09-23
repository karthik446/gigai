from __future__ import annotations

import uuid

import pytest

from gigai.adapters.factory import resolve_model_adapter
from gigai.scout.proposal_execution import execute_local_proposal
from gigai.scout.proposal_records import (
    ScoutProposalRecordError,
    answer_association_for_source,
    read_proposal_revision,
    record_proposal_revision,
    validate_proposal_revision,
)

from tests.behaviors.scout_proposals_tools.test_scout_proposal_execution import _Transport, _config, _setup_inputs


def test_complete_assessment_is_an_immutable_revision_with_idempotent_replay(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved, posting, selectors, run_id, goal_id = _setup_inputs(tmp_path)
    transport = _Transport()
    monkeypatch.setattr(
        "gigai.model_execution.resolve_model_adapter",
        lambda active, target: resolve_model_adapter(
            active, target, transport_overrides={"local-loopback": transport}
        ),
    )
    execution = execute_local_proposal(
        resolved=resolved,
        config=_config(tmp_path),
        run_id=run_id,
        goal_id=goal_id,
        model_target="local-proposal",
        posting_selector=posting,
        private_selectors=selectors,
        local_allowed=True,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000941"),
    )
    first = record_proposal_revision(
        resolved=resolved,
        execution=execution,
        posting_selector=posting,
        private_selectors=selectors,
        model_target="local-proposal",
        configured_digest="sha256:" + "a" * 64,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000942"),
    )
    assert first.created
    record = first.record
    assert record["schema_version"] == "scout-proposal-revision:1"
    assert record["opportunity"]["opportunity_id"] == posting["opportunity_id"]
    loaded = read_proposal_revision(
        resolved=resolved,
        record_id=record["record_id"],
        revision_id=record["revision_id"],
    )
    assert loaded == record
    before = list(transport.calls)
    replay = record_proposal_revision(
        resolved=resolved,
        execution=execution,
        posting_selector=posting,
        private_selectors=selectors,
        model_target="local-proposal",
        configured_digest="sha256:" + "a" * 64,
        uuid_factory=lambda: uuid.UUID("00000000-0000-4000-8000-000000000943"),
    )
    assert not replay.created
    assert replay.record == record
    assert transport.calls == before


def test_revision_shape_is_closed_and_rejects_missing_assessment() -> None:
    with pytest.raises(ScoutProposalRecordError) as error:
        validate_proposal_revision({"schema_version": "scout-proposal-revision:1"})
    assert error.value.code == "proposal_record_invalid"


def test_answer_association_requires_explicit_native_question_selection() -> None:
    source = {"family": "scout_record", "native_kind": "experience_qa", "record_id": "record_00000000-0000-4000-8000-000000000941", "revision_id": "revision_00000000-0000-4000-8000-000000000942", "content_sha256": "sha256:" + "a" * 64}
    association = answer_association_for_source(source=source, question_ids=["question_example"])
    assert association["purpose"] == "answer"
    with pytest.raises(ScoutProposalRecordError) as error:
        answer_association_for_source(source=source, question_ids=["question_example", "question_example"])
    assert error.value.code == "answer_association_invalid"
