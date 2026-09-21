# SCOUT proposal terminal consistency (bounded correction)

Status: implemented and focused-tested for the local proposal caller. This is
not a claim that the first-class proposal scheduler, UI, replay service, or
whole-Run lifecycle is complete.

## Correctness boundary

`execute_local_proposal` now authenticates the committed Goal transition history
as well as `run-details.json`. A committed `goal_completed`, `goal_failed`, or
`goal_blocked` for the requested Goal is terminal authority even if a stale
materialized details file still says `running`. A later committed `goal_started`
establishes a new eligible scheduler handoff; this is why the focused fixture
can use a genuine allocated Run while explicitly labelling its test-only
reactivation. Production callers must obtain a real running proposal Goal from
the scheduler and must not reopen a terminal Goal themselves.

Publication is one writer transition containing the immutable model invocation
artifacts, private proposal result, and the replacement `run-details.json`.
The selected Goal is materialized as `complete/COMPLETE` or `failed/FAILED`,
with evidence references and the corresponding `goal_completed` or
`goal_failed` handoff; the enclosing Run remains `running` because this caller
does not own completion of other sealed Goals. Existing committed bytes are
never replaced except the deliberately materialized run-details path, and new
invocation/result artifacts are checked for collisions before the transition.

## Focused evidence

The two new repeat tests execute a real synthetic discovery/private-input
fixture through the local transport, then invoke the same Run/Goal again. For
both a valid result and an invalid/spoofed result, the second call is refused
with `execution_authority_refused`, performs no transport call, creates no
journal commit/artifact, and the normal Run reader reports the Goal as
`complete/COMPLETE` or `failed/FAILED`. Existing authority, source-purpose,
source-tamper, and valid/invalid-result tests remain in the same focused file.

The fixture allocates its Run and Goal via the genuine Run lifecycle, then
publishes a test-only `goal_started` state after the deterministic fixture has
finished; it is not scheduler integration and does not represent a production
allocation API. No private user records, provider calls, model server, or
activation were used.

Verification commands (11 September 2026, synthetic/offline transport only):

* `ruff check src/gigai/scout_proposal_execution.py tests/test_scout_proposal_execution.py` — passed (final check, 0.2 s).
* `./.venv/bin/pytest -q tests/test_scout_proposal_execution.py -k 'completed_goal_rejects or failed_goal_rejects'` — 2 passed, 6 deselected (33.17 s including the preceding Ruff check).
* `./.venv/bin/pytest -q tests/test_scout_proposal_execution.py` — 8 passed (125.83 s).

The focused lane did not run provider/model/network, G18, the full suite, or
activation tests; those remain outside this correction.

## Concurrency and residual gate

All terminal publication checks run under `run_with_journal_writer`, so two
writers cannot publish duplicate terminal Goal transitions: the first commit
updates details and the second sees the committed terminal history. Invocation
transport currently occurs before this final publication lock is acquired by
the existing caller shape. If a caller cancellation or external terminalizer
wins after transport but before proposal publication, this module refuses the
stale publication and does not fabricate a second Goal event; there is no
accepted nonterminal invocation-evidence transition in the current runtime
contract to durably attach that already-performed invocation without broadening
the protocol. A future scheduler-owned reservation/evidence path must close
that race before claiming concurrent invocation evidence is fully durable.

This correction therefore proves terminal consistency and duplicate-publication
prevention, while leaving first-class running proposal Goal allocation and the
transport/publication race as explicit integration gates.
