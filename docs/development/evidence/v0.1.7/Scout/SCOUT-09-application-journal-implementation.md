# SCOUT-09 application journal implementation

Implemented the direct `gigai application record --gig GIG --input PATH --confirm`
vertical slice with reusable validation/publication service in
`src/gigai/application_events.py`. Application event JSON and its operation
receipt are committed together beneath the existing private-Git journal writer
lock; retries are operation-key idempotent and changed payloads conflict.

`application history` and `application status` read committed event artifacts
only and fold non-superseded events by occurred-at followed by journal order.
Corrections require an earlier same-opportunity event and cannot form a cycle.
No provider, network, Run, database authority, or outbound application effect
is invoked. Agent-message `selected_user_request` evidence and generic
`record-application` Run integration remain deferred, as do SQLite projection,
UI/reporting, and live provider execution.

Verification (2026-09-10): `.venv/bin/pytest -q
tests/test_scout09_application_events.py --disable-warnings --maxfail=1` passes
4 tests, and `ruff check src/gigai/application_events.py
src/gigai/application_cli.py tests/test_scout09_application_events.py` passes.
The real disposable initialized-workpad tests are
`test_confirmed_cli_publishes_and_replays_from_disposable_journal` and
`test_no_confirm_writes_nothing_and_statuses_are_per_opportunity`; they prove
CLI publication/replay/status and no-confirm refusal. They do not establish
that the regex-shaped opportunity ID is a redeemed committed opportunity
record, because no contracted durable opportunity authority currently exists;
that acceptance gate remains OPEN. Read-side event/receipt scope and hashes,
UTC instant ordering, and journal-sequence tie ordering are implemented;
agent-message evidence and generic Run integration remain deferred.

## Bounded correction pass (2026-09-10)

`_redeem_documents` now redeems the exact `snapshot_ref.path` through
`read_committed_artifact` pinned to the writer snapshot HEAD, validating safe
publication, committed bytes digest, size, revision scope, and requested
digest. Later edits to a working reference source therefore do not alter a
healthy historical document; missing, malformed, redirected, or mismatched
committed targets refuse atomically.

Direct evidence validation now binds the direct-command kind, command, operator
actor, scope digest, event actor, and command timestamp; replay additionally
requires receipt identity and nested event bytes to match the separately
committed event artifact. Correction validation rejects duplicate/self event
identity and accepts legitimate branches targeting an earlier same-opportunity
event; status still folds non-superseded events by UTC instant then journal
sequence.

Focused verification: `.venv/bin/pytest -q
tests/test_scout09_application_events.py --disable-warnings --maxfail=1`
passes **8 tests** (8 passed in 13.66s; exact wall time varies by disposable
fixture). Added real disposable coverage for pinned historical documents,
missing committed snapshot bytes with unchanged journal, correction branches
and identity/foreign-target refusals, and journaled evidence/receipt tamper
refusals. Ruff was run on the owned Python files and passed. Opportunity
authority redemption, `selected_user_request`, generic Run integration,
SQLite/UI/reporting, and provider execution remain deferred and unclaimed.
