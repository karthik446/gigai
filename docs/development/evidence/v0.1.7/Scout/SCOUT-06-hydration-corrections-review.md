# SCOUT-06 hydration correction closeout review

Date: 2026-09-10. This is an independent, bounded closeout of the hydration
correction only; it does not review or claim ownership of tailoring,
application recording, caller integration, Plan/Run integration, or the whole
SCOUT feature.

## Verdict

**Bounded correction accepted.** The corrected hydration path now makes one
writer-pinned snapshot, retains every committed checkpoint and receipt under
the selected Run, authenticates each retained artifact, and supports a
caller-held writer without nested lock acquisition. The verdict is limited to
the helper and its negative fixtures; no caller or publication acceptance is
inferred.

## Evidence reviewed

- Frozen SCOUT-00 rules: `SCOUT-00-contract-amendments.md`, especially the
  immutable Run/checkpoint/receipt identity and writer-lock requirements
  (sections around lines 354-424).
- Prior finding and correction: `SCOUT-06-hydration-review.md` and
  `SCOUT-06-hydration-corrections.md`.
- Source: `src/gigai/scout_research_inputs.py:600-725`.
- Existing focused tests: all 13 tests in
  `tests/test_scout06_research_inputs.py`, including the real journal fixtures
  for an unreferenced forked checkpoint, an extra competing terminal receipt,
  and held-writer hydration (`:118-173`).

## Findings and disposition

The former incomplete-history defect is corrected. At the bootstrap HEAD,
hydration adds every `runs/<run>/checkpoints/*.json` and
`runs/<run>/receipts/*.json` path (`scout_research_inputs.py:630-640`), then
reads each exact path through `read_committed_artifact(..., head=bootstrap.head)`
(`:704-711`). `_checkpoint_history` subsequently validates every retained
checkpoint's path identity, Run/Plan identity, invocation scope, contiguous
sequence, and parent chain (`:358-391`). The unreferenced-fork test publishes
an actual journal artifact and proves the fork is visible and refused; the
competing-terminal test likewise publishes a second receipt and proves unique
terminal validation sees it. These are authority-history fixtures, not merely
in-memory helper dictionaries.

The former nested-lock defect is corrected. The standalone function acquires
one writer via `run_with_journal_writer`, while the supplied-writer path checks
root/project/Gig scope and calls the operation directly (`:713-720`). The
held-writer test invokes `resolve_research_input_from_journal` from inside a
real writer operation and succeeds, demonstrating no second lock acquisition.

The path normalizer also handles an already complete checkpoint filename
without manufacturing `.json.json` (`SCOUT-06-hydration-corrections.md:11-12`),
and all discovered artifacts remain pinned to the bootstrap HEAD. No defect was
found in this bounded correction review.

## Verification boundary

The prior recorded focused lane is accepted as provenance but was not rerun:
`timeout 300 .venv/bin/pytest -q tests/test_scout06_research_inputs.py -p
no:randomly` — **13 passed in 113.31s (0:01:53), exit 0**; Ruff was recorded
passing on the source and test file. Source and test inspection independently
confirmed the claims above. No new scratch probe was needed because the three
meaningful corrected cases are real journal tests, not helper-only fixtures.

Outstanding gates remain caller revalidation/publication integration, Plan/Run
product acceptance, full-suite/package proof, activation, provider execution,
and any broader feature claim.
