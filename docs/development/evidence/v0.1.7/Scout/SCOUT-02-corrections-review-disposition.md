# SCOUT-02 — Correction re-review disposition

**Date:** 2026-09-08  
**Status:** Accepted; [final verification and acceptance](SCOUT-02-completion.md) recorded.

The coordinator read Claude's [completed re-review](SCOUT-02-corrections-rereview.md).
Its verdict accepts A02/the five corrections, not Scout or the release. The
worker was released with transcript preserved and its completion acknowledged.
Luna's independent full-suite and wheel verification subsequently passed. This
document records the remaining review notes
without modifying the independently authored reports or approved G44 inputs.

## Remaining notes and owners

| Review finding | Disposition |
| --- | --- |
| 1: Earlier implementation record under-describes v2 readers | Added a dated link to the correction handoff in its Deliberate limits section; original evidence retained. |
| 2: Invocation/handoff content binding | Non-blocking in Claude's A02 verdict; track for G44 agent-selection producer work. Before claiming producer-side exact provenance, bind the authenticating handoff to the particular invocation, not only matching agent-id metadata in a common commit. Use the existing validated journal boundary; no redesign or new schema is authorized by this note. |
| 3: v2 Plan state narrowing undocumented | v2 excludes the v1 draft/pre-seal states and requires non-null sealed fields. It is not the v1 draft-state machine. The v1 schema and historical Plan bytes remain unchanged. This clarification closes the documentation note, not a runtime defect. |
| 4: Selection actor session is optional | Track with G44 producer work: define and validate full invocation/selection actor-session binding before claiming session-level provenance. Any strict-schema change needs its normal compatibility treatment. |

Items 2 and 4 are explicit residuals, not proof that those tighter properties
already hold. Selection evidence never replaces direct Run consent. Neither
this disposition nor the review changes the unchanged G44 approved baseline.

## Evidence limits still to resolve

Final update: the coordinator added and passed independent negative-path and
both-graph execution checks after Luna's handoff. See the completion record
for the exact cases and limits. The G44 producer residuals above are retained,
not claimed fixed by these tests. The paragraph below records the review's
original evidence limitation.

The reviewer exercised existing tests, but did not author the additional
forged-journal/foreign-Graph-Set/altered-member-digest adversarial fixtures
mentioned in its limitations. The coordinator must account for the required
A02 negative-path coverage before claiming overall completion; the review
verdict alone is not that evidence. Do not describe aggregate test counts as
unique tests when regression slices overlap.
