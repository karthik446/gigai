# P2-FREEZE-04 — Dispatch matrix

Recorded 2026-09-22. This matrix turns the freeze ledger into three non-overlapping, documentation-derived implementation handoffs. It is not a staffing plan, provider authorization, release approval, or source/schema change.

## Jira decision

**Dispatch-ready for bounded implementation packet preparation after Terra reviews this matrix and the interface freeze.** Root coordinates and retains the final gate; Luna records implementation; Terra independently verifies completed outputs. Release remains blocked by the exact-tag G03 failure and the absent publication/UAT/installed/live/human-gold receipts documented in P2-AUD-01 and P2-EVAL-03.

## Packet boundaries

| Packet | Owns | Does not own | Required completion evidence |
| --- | --- | --- | --- |
| **A — Acquisition / scheduling** | Supplied-row input/progress/status/resume; source connector boundary; ATS/search watchlist and scheduling decisions; dedup/restart/failure; public records; source/freshness/privacy/storage capability receipts; acquisition CLI and focused behavior receipts. | Assessment/private input selection, model interpretation, proposal answers, Tailor, application/tracking mutations, UI truth, or claims that a connector exists because a description mentions it. | Source bytes/record revisions/digests, explicit unavailable/refused cases, public new/pending/failed visibility before assessment, restart/idempotency receipt, and authorized source/privacy decision. |
| **B — Assessment / evaluation** | Requirements, posting/proposal invocation, local/private input selection, assessment result/revision/answer association, resume/Tailor document selection, S08 pack/cases/grader, R6-compatible typed envelope adapter, route/refusal/failure receipts. | Acquisition connectors/watchlists/scheduler, public pending source authority, application event/tracking writer, projection/report/UI navigation, provider/model authorization, release approval. | Pinned input/result revisions, explicit local/refusal/failure/cancelled states, one answer-association choice, R6 adapter receipt without silent coercion, pack/self-check digests, and later authorized execution/gold receipts if requested. |
| **C — UI / tracking / navigation** | Rebuildable projection/report/table/detail/status readers; public saved/pending/failed display; explicit application event/correction/status history; CLI/UI navigation; reader receipts and no-private-mixing checks. | Acquisition/source authority, assessment/model interpretation, proposal/resume/Tailor semantics, a second state store/journal truth, storage migration, silent vocabulary coercion, or provider/model calls. | Projection rebuild/stale-cursor receipt, event append/correction/idempotency receipt, explicit tracking state map, public visibility independent of assessment, and wrapper/UI acceptance receipt. |

## Shared integration role (exclusive seam)

The integration role owns only coordination and seam work for shared:

| Shared path/area | Integration responsibility | Explicit exclusion |
| --- | --- | --- |
| `src/gigai/run.py` | Reconcile operation entrypoints and receipt handoff between packets. | No packet behavior absorbed; no provider/model authorization. |
| `src/gigai/journal.py` | Reconcile committed journal/event identity, cursor, revision, and recovery receipts. | No second authority or unreviewed schema mutation. |
| Schema registry and existing schemas | Register an approved versioned amendment only after packet proposal and Terra review. | Do not merge answer/tracking/EVAL shapes silently. |
| `src/gigai/cli.py` | Wire reviewed command entrypoints and explicit ownership handoff. | Do not infer support from parser registration; no live call. |
| Copied wrappers (`src/gigai/data/scout/gig.py`) | Coordinate inventory/coverage and record missing branches as gaps. | Wrapper output is not runtime or application authority. |
| Inventory and phase/daily receipts | Reconcile paths, hashes, and review receipts. | Inventory is not a runtime selector or release receipt. |

Any packet that needs a shared path proposes a handoff containing exact file, operation, schema/version, authority, acceptance receipt, and rollback/no-change disposition. Integration records that handoff; it does not make the packet’s behavior or approve release alone.

## Dependency and handoff order

```text
P2-FREEZE-04 interface/ledger (this packet)
        |
        +--> A Acquisition/scheduling ----+
        |                                  |
        +--> B Assessment/evaluation ------+--> integration seam reconciliation --> Terra verification --> root final gate
        |                                  |
        +--> C UI/tracking/navigation ----+
```

The three packets can prepare in parallel from the frozen source map. A packet cannot claim acceptance until it consumes the cited authority/revision contract and records its own receipt. UI consumes acquisition/assessment outputs but does not become a prerequisite for source records; assessment consumes resolved source inputs but does not become a prerequisite for public pending visibility. Integration reconciliation follows packet completion and is not permission to run providers, models, search, daemons, installation, publication, or UAT.

## Cross-packet interface contracts

| Edge | Producer authority | Consumer obligation | Failure boundary |
| --- | --- | --- | --- |
| A → B resolved posting | Acquisition source row/snapshot with revision/digest | Assessment pins the exact snapshot and records missing/changed bytes | `missing_posting_bytes`, `proposal_input_revision_changed`; no latest fallback. |
| A → C public status | Acquisition progress/failure journal | UI exposes new/pending/failed/saved even when assessment is absent | `model_stopped_after_acquisition`; preserve row and show unresolved assessment separately. |
| B → C assessment view | Local/private proposal/result/revision | UI displays result provenance and state mapping without mutating tracking | Refusal/failure/cancelled remains explicit; no model-to-application inference. |
| B → B Tailor | Proposal/answer revision and selected source digest | Document selection pins revision/digest and produces a separate document record | `selected_document_digest_mismatch`; no application side effect. |
| C → C tracking history | Explicit application event/correction journal | Projection/report rebuilds and exposes committed status history | `application_retry`, `application_correction`, `stale_journal_head`; append explicit event. |
| B → EVAL/R6 | Pack/case/setup/attempt receipts | Adapter preserves R6 and typed envelope fields and refusal outcomes | Unknown/malformed fields refuse; no silent coercion/fallback. |
| All → integration | Exact source/path/schema/revision/receipt references | Shared seams carry identifiers without changing authority | Missing edge is unresolved, not assumed implemented. |

## File ownership allowlist

The following are role-level handoff areas, not permission to edit them during this documentation task. A later implementation dispatch must narrow the list to exact files before editing.

| Role | Candidate implementation areas from current evidence | Forbidden overlap |
| --- | --- | --- |
| Acquisition/scheduling | `src/gigai/scout_acquisition_records.py`, `src/gigai/scout_discovery_job.py`, `src/gigai/scout_posting_inputs.py`, source/progress schemas only after separate approval, acquisition-focused CLI/behavior receipts | No `scout_proposals*.py`, proposal answer schema, `application_events.py`, report writer, model/provider route. |
| Assessment/evaluation | `src/gigai/scout_proposals.py`, `src/gigai/scout_proposal_execution.py`, `src/gigai/scout_proposal_records.py`, `src/gigai/scout_tailor_selection.py`, `src/gigai/scout_document_records.py`, EVAL documentation/grader/adapter receipts | No acquisition connector/scheduler, public source-progress writer, `application_events.py`, projection/report/UI authority, hosted/live route. |
| UI/tracking/navigation | `src/gigai/scout_projection.py`, `src/gigai/scout_report_readers.py`, `src/gigai/scout_report.py`, `src/gigai/scout_report_cli.py`, `src/gigai/application_events.py`, `src/gigai/application_cli.py`, navigation/UI behavior receipts | No proposal/model interpretation, acquisition source writer, second journal/store, silent schema enum change. |
| Integration | `src/gigai/run.py`, `src/gigai/journal.py`, schema registry, `src/gigai/cli.py`, `src/gigai/data/scout/gig.py`, inventory/receipt coordination | No packet implementation, release approval, provider/model execution, or broad cleanup. |

The copied wrapper’s absence of an application parser/branch is retained as an explicit UI/integration gap; parser presence alone cannot establish runtime support. Any test or schema changes remain outside this freeze and require their own owned implementation dispatch.

## State vocabulary handoff

| Vocabulary | Authority | Packet | Required rule |
| --- | --- | --- | --- |
| Public acquisition `new/pending/failed/saved` | Committed acquisition row/progress | A produces, C reads | Must exist before assessment and survive model stop. |
| Assessment `new/pending/running/succeeded/failed/skipped` | Proposal/Run/result record and explicit view map | B produces, C reads | Keep private/local; do not rename source values silently. |
| Tracking `shortlisted/applied/interviewing/rejected/archived/untracked` | Explicit application event journal | C produces, C reads | Resolve current event/schema mismatch by versioned map/amendment, not inference. |
| EVAL `typed_decision/abstain/uncertain/refused_or_failed` plus status/sufficiency | EVAL pack/attempt receipt; R6 compatibility | B produces | Wrapper is proposed; R6 `r7-output-contract:1` remains current anchor. |

## Evidence lane and gate matrix

| Lane | Accepted evidence in this freeze | Cannot be promoted to |
| --- | --- | --- |
| Source/static | AUD-01/AUD-02 paths, claims, ledgers; current callers/schemas | Installed/live behavior or publication. |
| Offline/synthetic | EVAL-03 pack/manifest/self-check hashes; 20 cases and rubric; no execution | Human gold, model quality, release score. |
| Focused groundwork | S11 13/13 checkout-local lifecycle receipt | Full suite, installed, live, release, or UAT. |
| Exact-tag CI | Run `35660274375`, failed at `tools/verify_installed_g03.py` | Shipment; correction/rerun still required. |
| Review | Terra/Luna/Terra supplied context receipts; corrected sole EVAL finding | Terra review of this freeze or root final approval. |
| Publication/UAT/installed/live | No positive receipt in AUD-01; states unproven/absent | Nothing; each needs its own authorized receipt. |

Human adjudication and explicit execution authorization are downstream evaluation/release gates, not prerequisites to producing this documentation freeze. The EVAL manifest’s held-out judgment cases remain pending/not-ready and no provider/model route is implied by naming Qwen, Luna, or future setup rows.

## Completion and exit

Proposed exit is **dispatch-ready** on 2026-09-22 for bounded packet preparation, with these concrete unresolved actions:

1. Acquisition records the independent public pending/failed reader and obtains any source/privacy/freshness/storage decision before live work.
2. Assessment resolves embedded versus standalone answer association, documents the assessment state map, and records an explicit R6/EVAL adapter without runtime schema mutation.
3. UI/tracking resolves the event-to-roadmap state vocabulary and records projection/event correction/idempotency receipts without a second truth.
4. Integration reconciles only the shared seams and receipts; Terra reviews all completed packets; root makes the final gate decision.
5. Release owner separately corrects and reruns exact-tag G03, then supplies build/attestation/publication/UAT/installed/live receipts; no shipment claim is made here.
6. S12 remains exploratory and does not block Scout or trigger a framework/composition redesign. **Amended 2026-09-22 for the Phase 3 proof:** see [Amendment 01](P2-FREEZE-04-amendment-01-scout-graph-proof.md). The proof runs as a linear `acquire → assess → present` graph. Integration gains the graph-runner seam (node binding, executor/effect policy, restart, and the explicit graph declaration). Packets A, B and C each expose their node's operation as a callable. Luna traces the integration before the proof is sized or dispatched. There is still no catalog, generic builder or orchestration rebuild.

