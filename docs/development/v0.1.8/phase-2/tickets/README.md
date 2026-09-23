# v0.1.8 Phase 2 — audit and interface-freeze tickets

**Status:** The AUD-01, AUD-02 and EVAL-03 outputs are complete and
Terra-reviewed. The FREEZE-04 packet is complete, amended by
[Amendment 01](../evidence/P2-FREEZE-04-amendment-01-scout-graph-proof.md)
on 2026-09-22. Terra closed the original freeze (`ctx_faaa104e3ea4` /
`msg_a70bbd115366`: "Phase 2 documentation complete, subject to root
integration"). That review predates Amendment 01, which needs its own
review. These are
documentation and contract-preparation stories only. They do not authorize feature implementation, schema/storage
migration, daemon/model/provider execution, live search, release publication,
or a new frontend.

The bounded [S11 Phase 1 receipt](../../evidence/S11-groundwork.md) and its
independent Terra verification (`task_6795b28e61ad` /
`ctx_b6f405f97b53`) now unblock this documentation packet. Terra's follow-up
confirmed the focused project-local lifecycle result (13/13, exit 0, no
skips/errors, exact node identities); that is still not broad, installed,
live/provider, or release acceptance. The stories below turn the
roadmap's audit/interface-freeze phase into a reviewable packet before the
three later implementation packets; every story remains planned/not started.

## Ticket order and dependencies

| Order | Ticket | Status | Dependency shape | Output owner role |
| --- | --- | --- | --- | --- |
| 1a | [P2-AUD-01 — v0.1.7 baseline and S10 local invocation audit](P2-AUD-01-v017-baseline-and-s10-invocation-audit.md) | Complete; Terra-reviewed | Independent; keep the v0.1.7 release prerequisite explicit | Luna/max audit writer; root reconciles release evidence |
| 1b | [P2-AUD-02 — Scout operation, record, and caller map](P2-AUD-02-scout-operation-record-caller-map.md) | Complete; Terra-reviewed | Independent of 1a for initial inventory; consumes current source and R0 sheet | Luna/max audit writer; root owns cross-packet reconciliation |
| 1c | [P2-EVAL-03 — synthetic evaluation pack and rubric preparation](P2-EVAL-03-synthetic-evaluation-pack-and-rubric.md) | Preparation complete; execution not authorized; Terra-reviewed | Can begin independently; uses 1b's operation vocabulary before finalizing cases | Luna/max preparation owner; Terra reviews the completed pack |
| 2 | [P2-FREEZE-04 — shared interface and ownership freeze](P2-FREEZE-04-shared-interface-and-ownership-freeze.md) | Complete; Terra closure `ctx_faaa104e3ea4` / `msg_a70bbd115366` (predates Amendment 01); Amendment 01 review pending | Requires 1a, 1b, and 1c reviewable outputs | Root coordinates; Luna/max records amendments; Terra verifies afterward |

The three `1*` audits can proceed in parallel. P2-EVAL-03 may draft its
synthetic cases before P2-AUD-02 is complete, but it must reconcile case
vocabulary and source boundaries with that map before the freeze. P2-FREEZE-04
is the only convergence story: it consumes the three reports and is the gate
for dispatching substantial acquisition/scheduling, assessment/evaluation, and
UI/tracking implementation packets.

```text
P2-AUD-01 ─┐
P2-AUD-02 ─┼─> P2-FREEZE-04 ─> later implementation packets + integration proof
P2-EVAL-03 ┘
```

## Shared Phase 2 rules

- Verify the actual v0.1.7 source/tag/artifact/publication state. A commit title,
  local tag, package version, candidate wheel, or prior report alone does not
  prove a shipped release. If evidence conflicts, retain both receipts, state
  which observation is current, and leave the release prerequisite unresolved
  until the exact artifact and publication evidence agree.
- Treat the journal and authenticated committed artifacts as authority; treat
  `state.sqlite` and HTML/report output as rebuildable views. A selector,
  model response, report row, or source category cannot create authority.
- Keep assessment state separate from explicit user tracking state. Selecting or
  finalizing a resume, generating a proposal, or tailoring a document never
  implies an application event.
- Every ledger row must name producer, consumer, record/input/output shape,
  revision/provenance, failure/recovery behavior, exact file/schema owner,
  public CLI/Run/UI entry points, and acceptance evidence. Missing behavior is
  a bounded proposed amendment with disposition, not an invented current API.
- Offline/source/injected proof, normally installed proof, and live/provider
  proof are separate claims. No story authorizes a daemon, model download,
  provider call, paid comparison, live search, personal workpad, or private data.
- S12 is exploratory graph framing only. It is not an architecture decision,
  prerequisite, orchestration rebuild, schema migration, or platform program.

## Roadmap and evidence anchors

- [v0.1.8 roadmap](../../roadmaps/v0.1.8-scout-search-first-roadmap.md),
  especially its audit/interface-freeze phase and three later packets.
- [S11 Phase 1 receipt](../../evidence/S11-groundwork.md),
  [S11 mapping](../../evidence/S11-acquisition-mapping.json), and focused
  [13-case receipt](../../evidence/S11-lifecycle-uv.json).
- [S08 methodology](../../spikes/S08-cross-model-decision-evaluation-methodology.md)
  and [S08 research evidence](../../spikes/evidence/S08-cross-model-eval-methodology-research.md).
- [S09 sourcing brief](../../spikes/S09-local-search-retrieval-capability-sourcing.md)
  and [S09 research evidence](../../spikes/evidence/S09-local-search-retrieval-capability-sourcing-research.md).
- [S10 invocation brief](../../spikes/S10-ollama-invocation-and-harness-onboarding.md)
  and [S10 research evidence](../../spikes/evidence/S10-ollama-invocation-and-harness-onboarding-research.md).
- [S12 exploratory ticket](../../spikes/S12-gig-graph-traversal-and-auditable-execution.md).
- Prior interface and release evidence that these stories must re-check:
  [SCOUT R0 shared interfaces](../../../evidence/v0.1.7/Scout/SCOUT-R0-shared-interfaces.md),
  [R7 final candidate verification](../../../evidence/v0.1.7/Scout/SCOUT-R7-final-candidate-verification-20260921.md),
  [R7 publication preparation](../../../evidence/v0.1.7/Scout/SCOUT-R7-publication-preparation-20260921.md),
  and [SCOUT-12 UAT checklist](../../../evidence/v0.1.7/Scout/SCOUT-12-user-uat-checklist.md).

## Phase 2 exit

Phase 2 is complete only when Terra has independently reviewed the producer
outputs and the freeze ledger is sufficient to dispatch the three substantial
parallel implementation packets with exact file/schema ownership, dependency
edges, integration responsibility, acceptance receipts, and unresolved
blockers. “Research recorded,” “candidate passed offline checks,” “local model
adapter works,” or “13/13 S11 cases pass” cannot substitute for that agreement.
