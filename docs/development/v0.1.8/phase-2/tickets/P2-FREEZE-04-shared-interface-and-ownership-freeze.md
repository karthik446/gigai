# P2-FREEZE-04 — Freeze shared interfaces, ownership, and dispatch boundaries

**Status:** Documentation packet complete. Amended 2026-09-22 by [Amendment 01](../evidence/P2-FREEZE-04-amendment-01-scout-graph-proof.md), which makes the Phase 3 proof a real linear Scout graph. Terra's final freeze closure: dispatch `ctx_faaa104e3ea4`, receipt `msg_a70bbd115366`, verdict "Phase 2 documentation complete, subject to root integration". That review **predates Amendment 01**. The amendment and Luna's integration trace (in progress, documentation only) need their own Terra review. **Owner role:** Root coordinator owns the
decision gate and integration ledger; Luna/max records the reconciled contract
packet; Terra/medium independently verifies after producer completion.

## Jira-style ticket

**User problem:** The roadmap intends to dispatch three substantial packets,
but acquisition/scheduling, assessment/evaluation, and UI/tracking all touch
the same journal, records, Run receipts, CLI and projection surfaces. Without a
reviewed ledger, workers may create duplicate authorities, blur assessment and
tracking, or claim a local/model/installed result proves the release.

**Intended behavior/outcome:** Consume the three Phase 2 audit outputs and
publish one reviewable shared-interface and ownership freeze. The freeze names
existing contracts that may be consumed as-is, bounded proposed amendments
where a gap is real, exact source/schema ownership, caller and reader edges,
integration responsibility, acceptance receipts, dependencies, and unresolved
blockers. It is sufficient to dispatch the later packets without silently
expanding scope.

**Scope:** Documentation-only reconciliation and contract decision record;
producer/consumer ledger; packet boundaries; integration and review gates;
explicit dispositions for release, S09, S10, S08, V018-01 and S12 interactions.

**Non-goals:** No production/test/config/schema edits, migrations, storage/UI
implementation, scheduler/daemon, model/provider/live calls, release
publication, orchestration-framework rebuild, or invented staffed assignment.

**Tasks:**

1. Reconcile P2-AUD-01's release/invocation matrix, P2-AUD-02's Scout
   operation/caller ledger, and P2-EVAL-03's pack/rubric disposition; retain
   exact source/evidence links and stale/conflicting observations.
2. Freeze the non-negotiable authority rules: journal/committed bytes over
   SQLite/cache/model output; closed selectors and digest redemption; explicit
   operator tracking; assessment independent of acquisition visibility; no
   hosted fallback for private assessment.
3. For each required cross-packet record or operation, identify producer,
   consumer, input/output, schema/version, revision/provenance, failure/recovery,
   owner path, public CLI/Run/UI entry points, and acceptance receipt. Mark
   existing, partial, missing, conflicting, or proposed.
4. Define the three later packet boundaries and exact file ownership:
   acquisition/scheduling, assessment/evaluation, and UI/tracking/navigation.
   Name a separate integration owner role for cross-packet receipts and
   installed proof; root coordinates, and Terra reviews after completion.
5. Turn missing capabilities into small proposed amendments with disposition
   (reuse, additive DTO, versioned schema, defer, or user decision). Do not
   hide a gap behind a blanket “no schema changes” rule or pretend a proposal
   is current support.
6. Record the Phase 2 exit decision: dispatch-ready, blocked by named issue,
   or requires a user scope/authorization decision. Keep v0.1.7 release proof,
   installed/live proof, S09 freshness/storage, S10 caller route, S08 human
   adjudication, and the thin vertical proof as distinct gates.

**Acceptance:**

- A Terra reviewer can trace every roadmap-required behavior to a current
  producer/consumer and exact ownership path, or see a named unresolved gap;
  no API or schema is asserted solely because a design document proposed it.
- The ledger explicitly separates acquisition/public records, assessment and
  proposal revisions, selected resume/document revisions, tracking/application
  events, Run/invocation evidence, and derived projection/report/UI rows.
- Later packet boundaries do not overlap on authoritative writers. Shared
  `run.py`, `journal.py`, schema registry, `cli.py`, inventory, and wrapper
  changes have an integration-owner handoff rather than concurrent ownership.
- Dispatch order, dependencies, integration receipts, independent Terra review,
  and unresolved blockers are written down. No later packet is implicitly
  authorized to call a provider/model or publish a release.
- The gate says whether the evidence is sufficient to dispatch implementation;
  it does not claim v0.1.7 shipment, installed/live acceptance, model quality,
  or S12 architecture adoption.

**Required evidence:** Final freeze decision at
`docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-interface-freeze.md`,
machine-readable ledger at
`docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-ledger.json`, and
packet/ownership matrix at
`docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-dispatch-matrix.md`.

**Dependencies:** Requires reviewable P2-AUD-01, P2-AUD-02, and P2-EVAL-03;
the current roadmap, S11, S08–S10 and S12 remain evidence/context links. No
runtime dependency is introduced.

## Granular freeze specification

### Ledger minimum

For every shared row, record:

```text
behavior_id, current_status, producer_role/path, consumer_role/path,
input_record_or_bytes, output_record_or_bytes, schema/version,
revision/provenance, authority_and_scope, failure/recovery/idempotency,
public_cli_entry, Run entry, UI/report reader, acceptance_receipt,
implementation_owner_role, integration_owner_role, review_gate,
dependency_edges, proposed_amendment, disposition, unresolved_blocker
```

At minimum, cover:

- public acquisition input/progress and discovery Run posting authority;
- immediate new/pending/failed visibility before assessment;
- proposal invocation/result and immutable assessment revision/answer
  association;
- explicit selected resume and document revision/final selection;
- tracking/application event, correction/supersession and status history;
- Run/goal/invocation evidence and local-model route policy;
- journal snapshot/readers, SQLite projection cursor, HTML/report output and
  public CLI/Run/UI callers;
- S08 pack/case/gold/grader receipt and the later Qwen-vs-Luna boundary;
- S09 source capability/privacy/storage/freshness decisions; and
- v0.1.7 artifact/release/publication disposition from P2-AUD-01.

### Packet ownership and integration

Use roles, not invented person assignments:

| Later packet | Primary ownership | Must not own | Integration handoff |
| --- | --- | --- | --- |
| Acquisition and scheduling | Source connectors/watchlists, bounded scheduling, dedup/restart/failure visibility, public acquisition records and behavior/CLI tests | Assessment internals, private preferences, proposal meaning, UI writer | Cross-packet receipt proves saved/new-pending visibility before assessment and S09 privacy/source caveats |
| Assessment and evaluation | Requirements/proposal/resume selection route, local invocation caller, S08 pack/grader and corresponding tests/receipts | Acquisition source authority, explicit tracking state, UI truth | Receipt proves selected inputs, local/no-fallback policy, rubric disposition and human-gold status |
| UI, tracking and navigation | Journal-derived projection/report/readable table/detail and explicit application/tracking mutations | Second source of truth, model interpretation, assessment semantics, storage migration | Receipt proves inference-off browse/status update and links to exact committed records |
| Integration role | Shared `run.py`, `journal.py`, schema registry, `cli.py`, wrapper/inventory coordination, daily/phase receipts, installed evidence | Absorbing packet implementation or approving a release alone | Reconciles packet outputs and raises unresolved issues to root/user |

Any shared-file touch is a planned integration handoff, not permission for
parallel workers to edit it casually. The matrix should identify exact file
sets after the audit, including whether an additive adapter/DTO or schema
version is genuinely required.

## Completion evidence (2026-09-22)

The documentation-only reconciliation is recorded in the three owned evidence artifacts:

- [`P2-FREEZE-04-interface-freeze.md`](../evidence/P2-FREEZE-04-interface-freeze.md) — Jira-style decision, authority/state vocabulary, roadmap reconciliation, R6/EVAL compatibility, proposed amendments, receipts, and dated exit proposal.
- [`P2-FREEZE-04-ledger.json`](../evidence/P2-FREEZE-04-ledger.json) — required behavior rows with producer/consumer paths, record/schema/provenance, recovery/idempotency, entrypoints/readers, receipts, ownership, dependencies, dispositions, and blockers.
- [`P2-FREEZE-04-dispatch-matrix.md`](../evidence/P2-FREEZE-04-dispatch-matrix.md) — non-overlapping acquisition, assessment/evaluation, and UI/tracking packets plus integration-only shared seam.

Proposed exit is `dispatch-ready` for bounded implementation dispatch after Terra reviews this freeze and root retains the final gate. Release remains blocked by the exact-tag G03 failure and absent publication/UAT/installed/live/human-gold receipts; no product, schema, provider, runtime, or release change was made.

### Decision and edge-case rules

Freeze these distinctions explicitly:

- A candidate artifact can pass offline checks while publication, exact-tag CI,
  Debian, remote tag, package-index install, and human UAT remain open.
- A public posting may be saved and visible as new/pending while assessment is
  slow, stopped, failed, or absent; it must not become hidden or acquire a
  private assessment field through the public record path.
- A proposal, resume selection, tailored document, or report row cannot create
  an application event. Tracking changes require the validated explicit event
  command and immutable receipt.
- A local adapter's loopback/identity checks do not prove caller policy,
  installed invocation, no-hosted-fallback end to end, or model quality.
- A draft label, model judge, synthetic injected transport, or S11 deterministic
  receipt cannot become human gold or release proof.
- S12's graph traversal and worker-ack ideas remain exploratory context; no
  orchestration rewrite or product architecture decision enters this freeze.

End with a dated decision: `dispatch-ready`, `blocked`, or `user-decision-needed`,
with each blocker tied to a source/evidence path and a next authorized action.
