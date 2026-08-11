# G20 Authoring Recovery Evidence

- Status: Accepted implementation evidence
- Recorded: 2026-08-10

G20's recovery boundary is authoring-time only. It does not roll back an
accepted Gig version and does not add a second version ledger.

## Learning-record publication

`publish_learning_record` performs schema validation, exact source and active
pointer verification, temporary-file write with flush/fsync, atomic rename, and
journal publication under the derived `home_root / "learning"` root. The
following interruption points are represented by executable failpoints:

- after temporary record write;
- after atomic record rename; and
- before journal publication.

`reconcile_learning_root` removes temporary files, malformed records, orphaned
records, duplicate observations, and journal entries whose record digest no
longer matches. It retains only records with a valid journal entry and repairs
the journal atomically. No orphan is silently completed.

The runtime tests cover atomic publication and duplicate refusal, an orphan
left after rename interruption, and a symlinked learning-root refusal. The
source and active-pointer checks also fail closed on missing, redirected, or
changed bytes.

## Improvement approval recovery

The improve interview reuses G22's short-lived loopback approval surface. The
approved improve request is converted into an ordinary `gig-proposal` with
`kind: "improve"`; the existing lifecycle is the only authority that advances
the active version. Replaying the approval returns the existing proposal and
does not advance the pointer again.

