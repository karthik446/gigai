# SCOUT R0 implementation evidence

Status: bounded R0 implementation, 2026-09-11.  This lane repaired proposal
invocation receipt authority and froze the shared interfaces needed by R1--R3;
it does not implement those lanes or claim release, live-model, scheduler,
Tailor, UI, application, or user-Gig acceptance.

## Delivered

`read_proposal_invocation_attempts` now captures one committed `HEAD` and
reads all matching receipt and artifact values against that same snapshot.  A
receipt must identify `proposal_invocation_recorded`, the exact Run/Goal/
invocation, source `scout-proposal-execution`, and the proposal host actor and
model target.  Its closed artifact references must contain exactly request and
record plus an optional response, with safe scoped paths, JSON media type,
digest and size; each is checked through `read_committed_artifact` and must be
published by the receipt's one journal commit.  The v1/v2 record's owner IDs,
configured target, strict model-invocation schema, request artifact reference,
and optional response reference must agree with those receipt artifacts.
Duplicate invocation identities and mismatched request/response references are
typed refusal paths; no historical v1 record is rewritten.

New receipt publications now carry the explicit source and actor metadata used
by that reader.  This remains a non-terminal invocation evidence transition,
so malformed proposal output and a competing cancellation retain request,
response, record, and usage evidence without creating a second Goal terminal
event.  Existing selector/Goal-shape and terminal recovery corrections remain
in force.

The shared interface sheet is [SCOUT-R0-shared-interfaces.md](SCOUT-R0-shared-interfaces.md).
It maps actual discovery opportunity fields and resolver, private revision and
answer shapes/readers, pure and durable proposal identity, Tailor request
selectors, document final selection, application event fields, and the
journal-authoritative/SQLite-rebuildable report DTO boundary.  Proposed fields
are labeled proposed; they do not fabricate G45 references or silently turn
SQLite into authority.

## Focused verification

Final focused command after all source/test changes:

```text
time .venv/bin/pytest -q tests/test_scout_proposal_run.py
10 passed in 152.25s (shell 2:32.37)

time ruff check src/gigai/scout_proposal_execution.py tests/test_scout_proposal_run.py
All checks passed! (shell 0.068s)
```

The ten Run tests include the supported successful path and exact repeat
refusal, invalid-domain-result evidence retention, sealed request/config
mutation, target-mutation recovery, injected final journal conflict recovery,
unavailable-selector refusal, real approved Tailor descriptor alias refusal,
extra Goal refusal, committed receipt target tamper refusal, and competing
cancellation evidence retention.  A focused standalone malformed-output
execution test also passed in 29.69s before the final file run; the final Run
file exercises the same receipt reader through the public lifecycle.

No schema JSON, schema hash inventory, provider/model/network, private data,
activation, or broad suite was used.  The additive `proposal_invocation_recorded`
transition is deliberately a closed journal transition; historical schema
bytes and old readers remain unchanged.

After the final reader exception-boundary tightening, the committed tamper
regression remained green and lint remained clean:

```text
time .venv/bin/pytest -q tests/test_scout_proposal_run.py::test_committed_receipt_target_tamper_is_refused_by_pinned_reader
1 passed in 17.18s (shell 17.308s)

time ruff check src/gigai/scout_proposal_execution.py tests/test_scout_proposal_run.py
All checks passed! (shell 0.065s)
```

## Remaining decisions and gates

The strict receipt reader authenticates receipts written by this proposal host;
it does not retroactively bless older records that lack the new source/actor
metadata.  A real concurrent writer race and scheduler-owned proposal Run
allocation remain R4/integration work even though the injected final-conflict
and competing-cancellation regressions cover the bounded recovery behavior.
R1 still needs an explicit durable proposal-revision/answer association,
R2 a reviewed versioned Tailor/document-selection contract, and R3 the
journal-to-SQLite opportunity/report views; the interface sheet records those
minimum fields and ownership without implementing them here.
