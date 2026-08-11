# G21 Occurrence Corpus

- Status: Accepted evidence
- Commit: `d010881`, `942b4f0`, `45838fc`, `42f4343`

The manual occurrence corpus covers the three contract-required cadence shapes:

| Fixture | Cadence | Evidence |
|---|---|---|
| Market-state snapshot | daily | Two distinct occurrences create distinct Runs and preserve the declared Review Bundle snapshot. |
| Screening snapshot | weekly | An absent external trigger is represented as the explicit terminal `missed` state. |
| Spreadsheet snapshot | monthly | A separate occurrence creates a separate Run without changing the active Gig pointer. |

The lifecycle tests also cover duplicate slot refusal, declaration-time snapshot
identity, valid replacement at the same path, interruption before and after Run
preparation, reconciliation without relaunch, and idempotent terminal replay.

The implementation is manual-trigger only. No scheduler, daemon, provider,
network, credential, or target-effect path is part of this corpus.
