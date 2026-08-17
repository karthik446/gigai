# GigAI Internal Changelog

- Status: Historical backfill in progress; G21 closeout recorded
- Snapshot date: 2026-08-11
- Authority: goal contracts, accepted amendments, completion audits, terminal
  handoffs, and committed evidence remain authoritative
- Purpose: explain what happened across the project without requiring a reader
  to walk every Goal directory

## Reading and authority rules

This document is synthesis, not a replacement for Goal contracts or evidence.
It links to those records and records cross-goal context, review corrections,
deferred scope, and learning candidates.

Goal identifiers are not chronological identifiers. The current history is
non-contiguous and crosses lanes:

- Phase 1: G00–G11
- Release lane: G12
- Phase 3: G13–G18
- Phase 2: G22
- Phase 4: G19
- Phase 5: G20, G21, and G23 complete; G24 UAT proposed; G25 alpha readiness
  remains planning-only
- Research/contract records: S16-EVAL, S18-01 through S18-05, and S22-01

The implementation chronology and the dependency graph are both valid but are
not the same ordering. G22 was implemented before G19 even though its number
is higher; G12 is a release goal rather than a Phase goal.

## Current repository snapshot

**As of 2026-08-11.** Refresh this section whenever a Goal, contract,
schema-resource count, release, or next-goal status changes. A stale snapshot
must be marked with its last-known date; it must never silently be treated as
current.

- Roadmap gate: G00–G23 are represented as complete; G24 is the proposed
  local UAT/dogfooding gate and G25 remains the later alpha-readiness
  candidate.
- Packaged schema resources: 27.
- Accepted research/contract prerequisites: S16-EVAL, S18-01 through S18-05,
  and S22-01.
- G20: complete; its learning records, improvement manifest, two gates, recovery,
  improve approval path, mutation report, and installed replay are accepted.
  G21: complete; additive amendment, runtime implementation, mutation evidence,
  and installed replay accepted.
  G23: additive amendment accepted; runtime implementation and post-closeout
  publication/lineage repair are complete.
  G23 is independent of G21 and does not declare an alpha or public
  release.
- Current closeout: G21 and G23 are complete after their corrective review
  passes; G24 is the proposed local UAT gate and G25 is the later
  alpha-readiness lane.
- Known reconciliation work: some older README, Phase 3 status, and evidence
  status lines still reflect earlier snapshots and must be corrected explicitly.

## Goal timeline and evidence index

Each entry will use this shape:

```text
### GNN — Name

- Lane/phase:
- Contract intent:
- What changed:
- Evidence and closeout:
- Review corrections:
- Deferred or rejected scope:
- Learning candidates: L-NNNN
```

The headers are intentionally explicit rather than relying on numeric ranges.

### G00 — Standalone contract baseline

Backfill from the [G00 audit](evidence/phase-1/G00/completion-audit.md)
and [handoff](evidence/phase-1/G00/terminal-handoff.md).

### G01 — Canonical serialization

Backfill from the [G01 audit](evidence/phase-1/G01/completion-audit.md)
and [handoff](evidence/phase-1/G01/terminal-handoff.md).

### G02 — Minimal CLI and scenario harness

Backfill from the [G02 audit](evidence/phase-1/G02/completion-audit.md)
and [handoff](evidence/phase-1/G02/terminal-handoff.md).

### G03 — Setup, configuration, and diagnostics

Backfill from the [G03 audit](evidence/phase-1/G03/completion-audit.md)
and [handoff](evidence/phase-1/G03/terminal-handoff.md).

### G04 — Target binding

Backfill from the [G04 audit](evidence/phase-1/G04/completion-audit.md)
and [handoff](evidence/phase-1/G04/terminal-handoff.md).

### G05 — Workpad and private Git

Backfill from the [G05 audit](evidence/phase-1/G05/completion-audit.md)
and [handoff](evidence/phase-1/G05/terminal-handoff.md).

### G06 — Journal locking and recovery

Backfill from the [G06 audit](evidence/phase-1/G06/completion-audit.md)
and [handoff](evidence/phase-1/G06/terminal-handoff.md).

### G07 — Contract validators

Backfill from the [G07 audit](evidence/phase-1/G07/completion-audit.md)
and [handoff](evidence/phase-1/G07/terminal-handoff.md).

### G08 — Offline create lifecycle

Backfill from the [G08 audit](evidence/phase-1/G08/completion-audit.md)
and [handoff](evidence/phase-1/G08/terminal-handoff.md).

### G09 — Rebuildable index and read commands

Backfill from the [G09 audit](evidence/phase-1/G09/completion-audit.md)
and [handoff](evidence/phase-1/G09/terminal-handoff.md).

### G10 — Phase 1 completion audit

Backfill from the [G10 audit](evidence/phase-1/G10/completion-audit.md)
and [handoff](evidence/phase-1/G10/terminal-handoff.md).

### G11 — Model invocation foundation

Backfill from the [G11 audit](evidence/phase-1/G11/completion-audit.md)
and [handoff](evidence/phase-1/G11/terminal-handoff.md).

### G12 — Versioned PyPI distribution

This is a release-lane Goal, not a Phase 3 product Goal. Backfill from the
[G12 release goal](goals/release/G12-versioned-pypi-distribution.md),
[release runbook](evidence/release/G12/release-runbook.md), and
publication evidence.

### G13 — Sealed deterministic Run launch

Backfill from the [G13 audit](evidence/phase-3/G13/completion-audit.md)
and [handoff](evidence/phase-3/G13/terminal-handoff.md).

### G14 — Sequential Goal Graph scheduler

G14 has its own header because it is a distinct implementation milestone, not
an incidental part of G13 or G15. Backfill from the [G14 audit](evidence/phase-3/G14/completion-audit.md)
and [handoff](evidence/phase-3/G14/terminal-handoff.md), while
recording any branch/hosted-evidence placement discrepancy explicitly.

### G15 — Reference bundles and evaluator substrate

Backfill from the [G15 audit](evidence/phase-3/G15/completion-audit.md)
and [handoff](evidence/phase-3/G15/terminal-handoff.md).

### G16 — First Review Loop Gig

Backfill from the [G16 audit](evidence/phase-3/G16/completion-audit.md)
and [handoff](evidence/phase-3/G16/terminal-handoff.md).

### G17 — Proposal capability inspection

Backfill from the [G17 audit](evidence/phase-3/G17/completion-audit.md)
and [handoff](evidence/phase-3/G17/terminal-handoff.md). Reconcile
the older “pending hosted confirmation” wording against the current roadmap
and merged implementation record instead of silently rewriting history.

### S16-EVAL — Review Loop evaluation methodology

Research/contract record, not an implementation Goal. Backfill from its
[completion audit](evidence/phase-3/S16-EVAL/completion-audit.md),
corpus, calibration, ground-truth, and mutation evidence.

### S18/S22 — Provider and proposal prerequisite tranche

Research/contract records, not provider-support claims. Backfill from the
[terminal handoff](evidence/phase-3/S18-S22/terminal-handoff.md),
contract-impact review, and individual spike records.

### G18 — Provider comparison and model handoff

Backfill from the [G18 audit](evidence/phase-3/G18/completion-audit.md)
and [handoff](evidence/phase-3/G18/terminal-handoff.md), including
the supported/deferred adapter distinction and the eval-plumbing limitation.

### G22 — Deliberative create and proposal interview

G22 is Phase 2 and is intentionally listed by implementation chronology here,
not numeric order. Backfill from the [G22 audit](evidence/phase-2/G22/completion-audit.md),
[acceptance ledger](evidence/phase-2/G22/acceptance-ledger.md),
and [handoff](evidence/phase-2/G22/terminal-handoff.md).

### G19 — Approved target mutation

G19 is Phase 4 and follows G22 in implementation chronology. Backfill from its
[completion audit](evidence/phase-4/G19/completion-audit.md), [handoff](evidence/phase-4/G19/terminal-handoff.md),
accepted amendment, mutation report, and installed replay.

### G20 — Local `improve` and evaluator learning

Contract, additive amendment, runtime implementation, mutation evidence, and
installed replay complete. See the [G20 completion audit](evidence/phase-5/G20/completion-audit.md)
and [terminal handoff](evidence/phase-5/G20/terminal-handoff.md).
See the [G20 goal contract](goals/phase-5/G20-local-improve-and-evaluator-learning.md)
and [learning-contract amendment](evidence/phase-5/G20/learning-contract-amendment.md).

### G21 — Recurring and comparative Gigs

G21 is complete. It adds manual daily, weekly, and monthly occurrence
declarations, exact Review Bundle snapshot binding, one-Run linkage,
interruption-safe terminal outcomes, and sealed-output comparisons that never
select a winner. The implementation is deliberately not a scheduler, provider
runner, retry system, or target-effect authority. See the [completion audit](evidence/phase-5/G21/completion-audit.md),
[terminal handoff](evidence/phase-5/G21/terminal-handoff.md), [mutation report](evidence/phase-5/G21/mutation-report.md),
and [installed replay](evidence/phase-5/G21/installed-replay.md). The later
corrective review added schema-enforced refusal reason/outcome/actor fields,
prepared-Run reconciliation protection, and explicit Gig-version comparison
refusal, and mandatory caller-supplied actor attribution; the final suite
reports 505 passing tests.

### G23 — Gig self-containment and portability

Goal contract and additive amendment accepted; runtime implementation and
post-closeout repair are complete. See the [G23 goal contract](goals/phase-5/G23-gig-self-containment-and-portability.md)
and [accepted amendment](evidence/phase-5/G23/gig-self-containment-and-portability-contract-amendment.md).
The contract adds a `capability_manifest` reference to
`active-gig-version.json` and a read-only proposal-lineage resolver so an
approved Gig version can name its declared capability manifest and its full
proposal chain without following the Review Bundle's `tool_requirements`.
The capability-manifest binding is authoritative through the same
approval-time lifecycle write that already sets `goal_graph`; it is not
derived through `gig-proposal.creation_manifest`, which remains a
single-purpose slot already used by `create` and G20 `improve` proposals.
Implementation evidence is recorded under the G23 evidence directory. G23 is
independent of G21 and does not
declare an alpha or public release.

### G24 — Human UAT and dogfooding

G24 is the proposed human-executed UAT gate before alpha planning. The operator
runs real workflows on the real machine; the review partner inspects each
checkpoint across CLI behavior, SQLite, workpad artifacts, Git handoffs, active
version state, and rebuildable projections. UAT records remain local and
sanitized. An early checkpoint distinguishes `registry.sqlite` from workpad
`state.sqlite` and tests whether projection rebuild preserves G22's
`interview_events` rows. G24 does not add runtime behavior or declare alpha; it
supplies the operator evidence G25 must use.

### G26 — Model-facilitated Gig builder

G26 is implemented on the `0.1.4` UAT release path. It adds bounded model
builder calls, adaptive-question plumbing, proposal research/review, recovery,
and explicit approval. Its repository evidence is complete; real operator
acceptance remains part of G24.

### G27 — Adaptive Gig discovery and pre-proposal research

G27 is the proposed follow-on that makes the HTMX session the actual Gig-
definition canvas. It will let the configured model explain available
capabilities, propose bounded research, and select up to five high-value
direction questions. It will reuse G20's evidence gates for future improve
flows and does not itself declare an alpha release.

### S27 spikes and G28 — v0.1.5 readiness foundation

S27-EVAL, S27-ROLE, and S27-CREATE are accepted prerequisite spikes for a
unified behavioral-eval framework, central namespaced roles, and a
browser-first setup/create path. G28 is active against their accepted
decisions and owns the v0.1.5 candidate. G24 human UAT and G27 runtime work
wait for that candidate; G25 remains the later alpha-readiness and
release-decision lane.

### v0.1.5 readiness gate

The next meaningful UAT candidate must close three technical-debt items before
G24 begins: a unified evaluation taxonomy and behavioral-eval framework, a
central namespaced role registry, and a browser-first setup/model-selection
flow where `gigai create <gig-name>` opens the HTMX discovery experience after
normal project setup. The current `0.1.4` release proves implementation
plumbing but is not the final human-UAT candidate for these product behaviors.

## Cross-goal change ledger

Backfill synthesis entries under these themes:

- authority and permission boundaries;
- additive schema/resource evolution;
- canonical bytes, provenance, and replay;
- lifecycle state machines and recovery;
- evaluation quality and ground truth;
- provider, credential, network, and effect boundaries;
- package/release verification; and
- goal activation, acceptance commits, and terminal handoffs.

## Review-correction ledger

This is the highest-value synthesis section. Record corrections that changed
the implementation or prevented an overstated claim.

| ID | Goal | Finding | Resolution/evidence | Candidate lesson |
|---|---|---|---|---|
| RC-0001 | S16-EVAL | Calibration bar and stop boundary contradicted each other | Define the bar before G18; G18 only runs a candidate judge | L-0001 |
| RC-0002 | S16-EVAL | Harness assertions did not measure review quality | Add expected findings, precision, severity, abstention, and citation support | L-0002 |
| RC-0003 | G19 | Amendment evidence links pointed at wrong phase/shape | Corrected links before acceptance | L-0003 |
| RC-0004 | G19 | Sandbox loopback failures looked like product failures | Re-ran with localhost permission; 471 tests passed | L-0004 |
| RC-0005 | G19 | Atomic-exposure mutant was paired with a non-mutation-sensitive static test | Explicit mutated-source import and specific AST guard; 7/7 killed | L-0005 |
| RC-0006 | G18 | Eval plumbing copied ground truth and top-line PASS could overstate judge quality | Audit labels it methodology plumbing, not candidate-judge scoring | L-0002 |
| RC-0007 | G22 | S22-01 protocol was not enough; durable interview state needed an amendment | Accepted proposal-interview resource before runtime | L-0006 |

## Deferred and rejected scope

Record capabilities that were researched, discussed, or implemented as
fixtures but deliberately not shipped. This section is where provider
feasibility must remain distinct from adapter support, and where G19's
one-file effect must remain distinct from arbitrary mutation.

## Documentation drift and reconciliation

Track stale snapshots without silently rewriting them:

- root README capability/schema inventory;
- `docs/README.md` phase descriptions;
- Phase 3 status table and older evidence status lines;
- release-version capability claims; and
- roadmap versus Goal README status.

Each item needs: source, stale claim, current authority, correction commit,
and date resolved.

## G20 starting state

G20 begins from the [G19 terminal handoff](evidence/phase-4/G19/terminal-handoff.md)
and must preserve explicit mutation authority, provenance, evaluator
distinctions, and the rule that learning proposes changes rather than editing
approved history automatically.

## Backfill protocol

Backfill one Goal or research tranche at a time. For each entry, read the Goal
contract first, then its completion audit, terminal handoff, amendment records,
and acceptance/review corrections. Link; do not paste evidence. Commit each
backfill cluster independently so the historical record remains reviewable.

Do not create `docs/development/learnings.md` during this pass. Promote a
learning candidate only after it has evidence from multiple independent Goals
and a later review confirms that it is a reusable principle rather than a
one-off implementation detail.
