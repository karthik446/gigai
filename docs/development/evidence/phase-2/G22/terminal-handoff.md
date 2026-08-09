# G22 terminal handoff

- Status: Terminal; G22 complete
- Next authorized consumer: G19 approved target mutation
- Handoff date: 2026-08-09

## What G22 hands off

G22 hands off an operator-approved, schema-validated Gig proposal sealed by
the existing workpad/journal lifecycle. The handoff includes the proposal,
goal graph, creation manifest, exact selected-reference bundle, approved
interview snapshot, and `active-gig-version.json`. The interview is terminal
at approval and repeat approval is idempotent.

## G19 entry conditions

G19 may begin only after reading this handoff and the completion audit. It may
consume the active proposal and its selected-reference/effect decisions, then
apply only the separately authorized target effect defined by G19. G19 must
revalidate the active proposal and current target/workpad state at action time;
it must not infer authority from a browser page, SQLite row, model response,
or abandoned draft.

## Explicitly not handed off

G22 grants no target mutation, patch application, target commit, capability
installation or execution, credential acquisition, provider fallback, public
hosting, background worker, or Run authority. A non-deterministic provider
questioner is not shipped as a supported G22 path; it remains a future
boundary decision for an explicitly authorized caller.

The G22 implementation and evidence are committed. No further G22 runtime
work is authorized unless a new contract or goal explicitly reopens it.
