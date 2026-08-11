# G21 Occurrence Lifecycle Record

- Status: Accepted evidence
- Implementation commits: `d010881`, `942b4f0`, `45838fc`, `42f4343`
- Corrective focused result: `22 passed`

The accepted lifecycle is:

```text
declared -> triggered -> snapshot_verified -> run_prepared
         -> run_terminal -> compared -> closed
```

`blocked`, `skipped`, `cancelled`, `unavailable`, `failed`, and `missed` are
closed outcomes. A terminal occurrence cannot be relaunched, and reconciliation
only observes an already-prepared Run; it never creates a second Run.

Refusal records retain the immutable declaration-time `trigger_actor` and add
an explicit `outcome_actor`, bounded reason, and populated outcome. A prepared
occurrence cannot be marked cancelled/failed/etc. while its linked Run is in
flight or complete but unreconciled; the caller must reconcile the Run first.
The Python API and CLI require the outcome actor and reject omission rather than
falling back to `operator/local-user`.

The trigger boundary revalidates the complete declared snapshot, including
Bundle identity, version, artifact digest, and reference-set digest, before Run
creation. The occurrence stores the resulting Run identity and does not advance
the active Gig version or create target effects.
