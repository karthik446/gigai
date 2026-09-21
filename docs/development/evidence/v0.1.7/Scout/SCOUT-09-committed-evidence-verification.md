# SCOUT-09 committed evidence verification

Date: 2026-09-10. This bounded correction verifies semantic refusal of
malformed committed application evidence and receipt identity using real
disposable initialized workpads. It does not change `application_events.py`.

## Corrected claim

The earlier corrections review overstated F3 by treating
`test_direct_evidence_and_receipt_identity_are_validated` as proof of semantic
validation for a legitimately journaled but inconsistent event. That test
edits event or receipt bytes after publication and receives
`application_journal_conflict`; its real boundary is committed mirror-drift
refusal, not semantic validation of a singly published artifact.

## Committed-artifact regression provenance

`test_committed_hash_valid_event_with_inconsistent_direct_evidence_refuses_semantically`
uses `_publish_application_artifacts` to construct and publish one event plus
its receipt in one real journal transition. The event's requested and payload
hashes are recomputed after construction, and the artifact passes the strict
JSON schema. Two small variants then make only direct evidence semantically
inconsistent: a wrong scope digest or an evidence actor that differs from the
event actor. `read_application` raises `application_request_mismatch`; a new
application attempt refuses through the `_events` wrapper as
`application_event_invalid` with an `application_request_mismatch` cause. This
establishes the application semantic boundary rather than schema, hash,
mirror, or multiple-publisher refusal.

`test_same_key_retry_refuses_inconsistent_committed_receipt_semantically`
also publishes through one real journal transition. Its operation-identity
variant commits a self-consistent receipt/event pair under the retry's receipt
path but with a different operation identity, and refuses with
`application_operation_conflict`. Its missing-event variant commits a
self-consistent receipt whose separately committed event is absent, and
refuses with `application_receipt_invalid`. Both variants assert unchanged
HEAD and no new event or receipt publication.

The existing `test_direct_evidence_and_receipt_identity_are_validated` remains
in place and is explicitly retained as the working-copy tamper / committed
mirror-drift boundary. It is not used as evidence of semantic validation.

## Verification status and scope

Source inspection of `application_events.py` confirms the semantic guards and
the wrapped cause described above; no production blocker was found. The new
tests were run with:

    .venv/bin/pytest -q tests/test_scout09_application_events.py -k
    'committed_hash_valid_event or same_key_retry'

Result: **4 passed**. Owned Ruff was run on
`tests/test_scout09_application_events.py` and passed. The earlier 8-test
application-events result was not rerun in this correction pass. Opportunity
authority, selected-user-request binding, generic Run integration,
projection/UI/reporting, activation, and provider execution remain open and
are outside this correction.
