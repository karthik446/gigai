# G09 Terminal Handoff

- Goal: [G09 — Rebuildable Index and Read Commands](../../../goals/phase-1/G09-index-and-read-commands.md)
- Date: 2026-08-05
- Outcome: Correction complete locally pending hosted confirmation

## Delivered surface

- Disposable authoritative-journal projection in ignored `state.sqlite`.
- Offline `gigs`, `proposals`, `status`, `show`, `history`, and `plan` commands with stable JSON forms.
- Journal/index health in offline doctor.
- Semantic index-tamper reconciliation: the committed journal is replayed before
  reads or doctor results are returned, and divergent `state.sqlite` content is
  replaced from that authority.
- Fresh-wheel verifier and CI coverage for the installed read surface.

## Next transition

G10 may audit Phase 1 only after the G09 goal commit passes hosted CI. It must treat committed workpad journals and explicit active-version pointers as authority, never `state.sqlite` or a lexical latest value.
