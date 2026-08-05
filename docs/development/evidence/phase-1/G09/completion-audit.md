# G09 Completion Audit

- Goal: [G09 — Rebuildable Index and Read Commands](../../../goals/phase-1/G09-index-and-read-commands.md)
- Date: 2026-08-05
- Local result: Correction pass pending hosted confirmation

## Acceptance reconciliation

1. **Disposable deterministic projection — Pass.** `state.sqlite` is rebuilt only from committed journal handoffs; deletion and corrupt bytes reconstruct the same structured projection.
2. **Idempotency — Pass.** A matching index is reused unchanged; rebuilt structured output is equivalent.
3. **Explicit scope — Pass.** Read commands use G05's target and optional canonical Gig resolution.
4. **Authority-labelled plan — Pass.** `plan` reports `proposed` or `approved` explicitly and starts no work.
5. **Named validation — Pass.** Existing `check` continues to call G07 validation without target mutation.
6. **Structured open — Pass.** Existing G05 `open` behavior is retained; semantic resolution now explicitly permits the declared ignored index.
7. **Offline doctor — Pass.** `journal.index` replays each managed private journal, reconciles malformed, stale, or semantically divergent disposable index state, and does not invoke the live path or credentials.
8. **Read-only target surface — Pass.** Command-level tests run every new read command against a real G08 workpad and prove the bound target tree is unchanged.
9. **Corruption boundary — Pass.** Missing, malformed, stale, and semantically divergent index content is reconciled from committed journal authority before reads or doctor results are returned; uncommitted authoritative journal divergence raises `JournalIndexError` rather than being concealed.

## Verification

- Complete split source matrix: 278 passed, 22 subtests passed.
- Installed G09 verifier: passed, exercising installed `gigs`, `proposals`, `status`, `show`, `history`, and `plan` against a real temporary G08 proposal.
- Ruff and `git diff --check`: passed.

## Completion decision

The G09 correction is locally complete. Hosted CI on the correction commit remains
the publication gate for G10.
