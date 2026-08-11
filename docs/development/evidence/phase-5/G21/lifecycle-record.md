# G21 Occurrence Lifecycle Record

- Status: Accepted evidence
- Implementation commits: `d010881`, `942b4f0`, `45838fc`, `42f4343`
- Focused result: `16 passed`

The accepted lifecycle is:

```text
declared -> triggered -> snapshot_verified -> run_prepared
         -> run_terminal -> compared -> closed
```

`blocked`, `skipped`, `cancelled`, `unavailable`, `failed`, and `missed` are
closed outcomes. A terminal occurrence cannot be relaunched, and reconciliation
only observes an already-prepared Run; it never creates a second Run.

The trigger boundary revalidates the complete declared snapshot, including
Bundle identity, version, artifact digest, and reference-set digest, before Run
creation. The occurrence stores the resulting Run identity and does not advance
the active Gig version or create target effects.
