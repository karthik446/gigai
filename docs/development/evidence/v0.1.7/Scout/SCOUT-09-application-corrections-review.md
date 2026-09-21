# SCOUT-09 application corrections review (independent)

Date: 2026-09-10. Read-only independent acceptance review of the bounded
corrections in `application_events.py` against frozen B4 and
`SCOUT-09-application-service-review.md`. No source or test files were changed;
this review owns only this document.

## Verdict

**Bounded correction accepted; no reproducible blocker found.**

The correction genuinely redeems document bytes from committed journal
authority, binds direct evidence and replay receipt identity, and permits
legitimate correction branches while refusing self, duplicate, and foreign
targets. This is not whole SCOUT-09 acceptance: opportunity authority,
`selected_user_request`, generic Run integration, projection/UI, activation, and
provider execution remain explicitly deferred.

## Evidence reviewed

The implementation addendum reports the focused command
`.venv/bin/pytest -q tests/test_scout09_application_events.py
--disable-warnings --maxfail=1` as **8 passed in 13.66s**, plus Ruff passing on
the owned Python files. Per dispatch, the focused suite was not rerun.

The actual tests include disposable initialized-workpad publication and replay,
missing committed target refusal, correction branch/identity/foreign-target
cases, and event/receipt tamper cases. They exercise the service's real writer
and journal rather than replacing validator acceptance with mocks.

## F1 — committed document redemption: verified

`application_events.py:189-265` loads the selected revision from the locked
`records/` snapshot, validates project/Gig identity and the revision schema,
requires a typed `snapshot_ref`, then calls `read_committed_artifact` for the
exact referenced path at `snapshot.head`. It checks committed byte digest and
size against both the revision reference and requested document digest before
publication. The reader enforces safe paths, one authenticated publisher, exact
handoff references, and pinned Git bytes.

`test_historical_document_bytes_are_pinned_after_working_copy_edit` edits the
working source after import but still records successfully, correctly proving
that healthy historical committed bytes—not current working bytes—are redeemed.
`test_missing_committed_document_bytes_refuse_without_publication` constructs a
real committed malformed revision pointing to absent bytes and confirms typed
refusal with unchanged journal revision count. No claim is made that working
tree mutation is committed-byte corruption; it is specifically a proof of
historical pinning.

## F3 — direct evidence and replay identity: verified

`application_events.py:119-170` validates the event's requested and payload
digests, then semantically binds evidence kind (`direct_event_command`), exact
command (`gigai application record`), operator actor, scope digest, and
recorded-at timestamp to the event. The direct constructor at `330-376` emits
those same values.

Replay at `386-434` validates the stored event, requires its separately
committed event artifact, validates that artifact again, and checks receipt
filename/operation key, receipt operation/request/payload digests, and exact
canonical event bytes. `test_direct_evidence_and_receipt_identity_are_validated`
mutates committed event evidence and receipt operation identity and observes
typed refusal; this is a real journal tamper path, not an unrelated schema-only
failure.

## F2 — correction branches and ordering: verified

`application_events.py:436-456` rejects duplicate event identity and self-target
correction, while requiring a superseded event with the same opportunity. It
does not reject a legitimate second child targeting the same earlier event;
the focused branch test records both `applied` and `rejected` children and
rejects a foreign-opportunity target. `read_application` folds active events by
UTC-normalized `occurred_at`, then journal sequence (`514-560`), preserving
deterministic tie ordering and supersession semantics.

## Scope and remaining gates

Accepted here: the direct confirmed application-event journal slice, committed
document byte redemption, direct evidence/receipt identity, branch guards, and
UTC/journal ordering. Not accepted or claimed: regex-only opportunity IDs as
durable opportunity authority, selected-user-request evidence, generic
`record-application` Run integration, SQLite projection/UI/reporting, outbound
provider operation, activation, installation, or release acceptance.

No new correction scope was opened for those acknowledged gaps, and no full
suite, wheel, network/provider, private-user-state, or commit operation was
performed.

## Correction to F3 evidence claim (2026-09-10)

The earlier F3 paragraph was misleading: `test_direct_evidence_and_receipt_identity_are_validated`
edits event/receipt working-copy bytes after publication and observes
`application_journal_conflict`. Its real boundary is committed mirror-drift
refusal, not semantic validation of a legitimately singly journaled
inconsistent artifact.

The corrected committed-artifact proof is recorded in
`SCOUT-09-committed-evidence-verification.md`. Its new tests construct one
event-plus-receipt publication through the real disposable journal fixture,
recompute requested/payload hashes, and then exercise wrong direct-evidence
scope and actor consistency. History refuses with `application_request_mismatch`;
new publication refuses through the wrapped event-invalid boundary with the
same semantic cause. A second real-journal test commits self-consistent
receipt variants and confirms same-key retry refusal for operation identity
conflict and missing separately committed event (`application_receipt_invalid`).
Each asserts unchanged HEAD and no new artifacts; no multipublisher or schema
failure is involved.

Correction verification ran only the four new parametrized cases and owned
Ruff; the earlier 8 tests were not rerun. Source inspection found no blocker
and no production files were changed. Opportunity authority and the other
previously deferred gates remain open.
