# P2-FREEZE-04 — Shared interface and ownership freeze

Recorded 2026-09-22. Documentation-only reconciliation of the three Phase 2 producer packets; this file freezes the evidence map and proposed handoffs, not a product contract or implementation.

## Jira-style decision summary

| Field | Decision |
| --- | --- |
| Issue | P2-FREEZE-04: reconcile AUD-01 baseline/invocation, AUD-02 Scout operation map, and EVAL-03 evaluation pack into one dispatchable interface/ownership ledger. |
| Decision | **Dispatch-ready for three bounded implementation packets after Terra reviews this freeze; not release-ready.** Existing authority and callers remain authoritative; proposed amendments are explicitly non-implemented. |
| Frozen authority | Committed source/record bytes and journal/receipt rows are authoritative. SQLite, projections, reports, HTML, and UI are readers/views unless a cited operation explicitly commits a tracking event. Assessment is local/private and never supplies public saved/pending state. |
| Main gaps | Public pending/failed visibility is not a complete independent reader contract; answer association and tracking vocabularies conflict with roadmap language; connectors/scheduling/freshness/storage rights, installed/live S10 proof, EVAL execution/human gold, and release evidence remain open. |
| Ownership | Acquisition/scheduling owns source intake and restart; assessment/evaluation owns proposal/resume/local evaluation and EVAL receipts; UI/tracking owns projections/report/navigation and explicit application mutations; integration owns shared `run.py`, `journal.py`, schema registry, `cli.py`, wrappers, and inventory coordination only. Root keeps the final gate; Luna records implementation; Terra verifies completed packets. |
| Exit proposal | **dispatch-ready**, dated 2026-09-22, subject to the concrete blockers and Terra/root gates in [Exit decision](#exit-decision). Release remains blocked by the exact-tag G03 failure and absent publication/UAT/installed/live/human-gold receipts. |

### Inputs and review receipts

The reconciliation consumed these current checkout artifacts (the paths are inputs, not edits):

- [`P2-AUD-01-baseline-and-invocation.md`](P2-AUD-01-baseline-and-invocation.md) and [`P2-AUD-01-claims.json`](P2-AUD-01-claims.json), including exact-tag CI/publication/UAT/S10 boundaries.
- [`P2-AUD-02-scout-operation-map.md`](P2-AUD-02-scout-operation-map.md) and [`P2-AUD-02-interface-ledger.json`](P2-AUD-02-interface-ledger.json), including operation/case vocabulary and source caller map.
- [`P2-EVAL-03-pack-and-rubric.md`](P2-EVAL-03-pack-and-rubric.md), [`P2-EVAL-03-manifest.json`](P2-EVAL-03-manifest.json), and `synthetic-evaluation/` under this evidence directory, including pack and self-check digests.

The dispatch supplied these review receipts: Terra `ctx_7ff479b90fc9` passed AUD-01/AUD-02/EVAL integrity with one EVAL-manifest freeze-gate finding; Luna `ctx_461e730dc554` corrected that finding; Terra `ctx_57133c5bc2bb` passed the corrected packet and closed the sole finding. Those context receipts establish review provenance supplied to this task; they do not constitute installed, live, publication, UAT, provider, model, or human-gold evidence.

Roadmap and research context read for this reconciliation: `docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md`; `docs/development/v0.1.8/spikes/S08-cross-model-decision-evaluation-methodology.md`; `docs/development/v0.1.8/spikes/S09-local-search-retrieval-capability-sourcing.md`; `docs/development/v0.1.8/spikes/S10-ollama-invocation-and-harness-onboarding.md`; `docs/development/v0.1.8/spikes/S11-behavior-based-test-organization.md`; and `docs/development/v0.1.8/spikes/S12-gig-graph-traversal-and-auditable-execution.md`. The Phase 2 ticket rules and order are in `docs/development/v0.1.8/phase-2/tickets/README.md`.

### Scope boundary

This freeze records current paths, schemas, callers, authority, provenance, failure/recovery semantics, ownership, and acceptance receipts. It does not edit product source, schemas, tests, configuration, wrappers, provider adapters, or runtime behavior. It does not authorize a model/provider/search/daemon call, human adjudication, execution, publication, or a framework/Improve/S12 redesign.

## Frozen interface rules

1. **Record authority.** A committed record or journal event with its source bytes, revision/digest, and provenance is the write authority. `src/gigai/journal.py:237-250,393-405,461-516` captures pinned snapshots, commit identity, and explicit reconciliation. A reader may not promote a latest file, cache, HTML rendering, model draft, or inferred status to authority. Existing R0 guidance states the journal/committed bytes and pinned selectors are authoritative ([`SCOUT-R0-shared-interfaces.md`](../../../evidence/v0.1.7/Scout/SCOUT-R0-shared-interfaces.md), lines 8–17, 21–28).
2. **Public visibility precedes inference.** A supplied row can be new, pending, failed, or saved before any assessment. Assessment must select private/local inputs and may fail or be skipped without removing the public row. No public/private query separation or privacy claim is inferred from a connector description; S09 remains a design input with freshness, storage, and terms questions ([`S09-local-search-retrieval-capability-sourcing-research.md`](../../spikes/evidence/S09-local-search-retrieval-capability-sourcing-research.md), lines 177–190, 340–388).
3. **Proposal, resume, tailoring, and tracking are different operations.** A proposal answer is not an application event; a selected document is not an application; a report is not a state mutation. A tracking correction must append/commit an explicit event, then rebuild/read a projection. No reader may infer tracking from assessment or model output.
4. **Pinned provenance.** Every future packet must retain source/record revision, content digest, schema/version, operation/case identifier, and receipt references. “Current/latest” is not a selector. Missing bytes, digest mismatch, stale journal head, and changed input revision fail closed or produce an explicit unresolved outcome.
5. **Views are derived.** SQLite cursors, projections, HTML, report DTOs, and UI tables are consumer views. They may be rebuilt from committed records and must expose pending/failed/unknown rather than silently dropping rows. Their receipts cannot replace the source journal or committed bytes.
6. **Evaluation is bounded.** EVAL-03 is preparation-complete and execution-not-authorized. The proposed `gigai.typed_decision.v1` wrapper is documentation-only; R6’s existing `r7-output-contract:1` remains the compatibility anchor. No model draft is human gold, and no score is release acceptance until the downstream gates are authorized and receipted.
7. **No implicit authorization.** Source route, injected offline route, installed route, live local route, hosted route, publication, and UAT are separate evidence lanes. A source guard or synthetic pack does not authorize provider/model execution or fallback.

## Roadmap behavior reconciliation

Status vocabulary in this table is deliberately limited to `existing`, `partial`, `missing`, `conflicting`, and `proposed`; it describes evidence/contract state, not implementation priority.

| Roadmap behavior | Status | Current authoritative producer and consumer | Reconciliation / proposed disposition |
| --- | --- | --- | --- |
| Import supplied rows and preserve public source identity | existing | `src/gigai/scout_acquisition_records.py:246-292,309-355,378-557`; input/progress schemas `src/gigai/schemas/scout-public-import-input.schema.json` and `scout-public-import-progress.schema.json`; readers in `scout_report_readers.py:63-147` | Keep acquisition journal/row bytes authoritative; add no connector assumption. |
| ATS/search connectors, watchlists, schedule, dedup, restart | proposed | No current source path proves a web/ATS connector or scheduler; AUD-02 covers supplied rows and completed-posting resolution only | Acquisition packet may design/implement an authorized boundary later. Freshness/storage/terms remain S09 questions; no live calls in this freeze. |
| New/pending/failed visibility before assessment | partial | Acquisition progress is recorded by `scout_acquisition_records.py`; report/projection readers are `scout_projection.py:248-394` and `scout_report_readers.py:296-455`; no complete independent pending contract was found | Add a view/DTO mapping sourced only from acquisition records and explicit failures; preserve rows when assessment is absent. |
| Completed posting resolution | existing | `src/gigai/scout_posting_inputs.py:341-489`; accepted bounded review in `docs/development/evidence/v0.1.7/Scout/SCOUT-07-posting-input-resolution-review.md:84-122` | Keep deterministic snapshot/digest resolution; do not imply search/fetch support. |
| Proposal invocation and result | existing | Request/host `src/gigai/scout_proposals.py:239-309,366-430`; execution `src/gigai/scout_proposal_execution.py:91-173,803-905,1129-1176`; record `scout_proposal_records.py:146-188,195-310,314-470` | Assessment packet consumes this contract; source row and proposal revision stay separate. |
| Local assessment invocation/status | partial | Proposal/execution records and `src/gigai/run.py` integration seam exist; roadmap states are `new,pending,running,succeeded,failed,skipped` (`docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md:47-61`), but no single reconciled public state projection is frozen | Define an additive view/state mapping in the assessment packet; do not rename existing schema states silently. Local/private only. |
| Assessment revision and input association | conflicting | Proposal revision schema `src/gigai/schemas/scout-proposal-revision.schema.json`; embedded answers in `scout_proposal_records.py:163-174`; standalone `src/gigai/schemas/scout-answer-association.schema.json:3-12` | Preserve existing revision/digest; resolve embedded-versus-standalone answer association through an explicit adapter/version decision before implementation. |
| Resume/tailoring/document selection | existing | `src/gigai/scout_tailor_selection.py:187-292`; document records `src/gigai/scout_document_records.py:138-269`; CLI `src/gigai/scout_documents_cli.py:27-122` | Keep selected source/document digest and revision pinned. No application side effect. |
| Explicit application event/correction | existing | Writer/service `src/gigai/application_events.py:27-34,129-180,199-383,470-742,745-801`; schema `src/gigai/schemas/application-event.schema.json:1-20`; CLI `src/gigai/application_cli.py:15-99` | Tracking packet owns validated event append/correction; proposal/report/model never writes application state implicitly. |
| Roadmap tracking vocabulary (`shortlisted`, `applied`, `interviewing`, `rejected`, `archived`, `untracked`) | conflicting | Application event schema/service has its own event/state semantics; current records and roadmap labels are not one declared enum contract | Freeze event authority now; add a documented alias/version map or deliberate schema amendment later. No silent relabeling. |
| Tracking history/status projection | partial | Event journal is authoritative; projection/report readers `src/gigai/scout_projection.py:248-394` and `scout_report_readers.py:296-455`; `scout_report.py:114-340` renders report | Tracking packet defines rebuild, correction, stale-head, and idempotency receipt; projection remains a view. |
| Report/UI rows and detail | partial | Report `src/gigai/scout_report.py:114-340`, CLI `src/gigai/scout_report_cli.py:14-60`, direct registrations `src/gigai/cli.py:47-52,3048-3207,4041-4048`, copied wrapper `src/gigai/data/scout/gig.py:70-147,337-554` | UI packet owns readers/navigation and explicit actions; copied wrapper’s missing application branch remains a named gap, not implied support. |
| Run/goal/invocation route and no-hosted-fallback policy | partial | AUD-01 source route and cancellation semantics are source-proven; injected route is offline-only; `src/gigai/run.py` and CLI route are integration seams | Keep local-only/private policy and failure/refusal receipts. Installed/live proof and any provider authorization are downstream. |
| S08 pack/case/gold/grader and Qwen-vs-Luna comparison | partial | EVAL-03 pack/manifest and `synthetic-evaluation/`; `docs/development/v0.1.8/spikes/S08-cross-model-decision-evaluation-methodology.md:41-113`; R6 paths in `docs/development/evidence/v0.1.7/Scout/SCOUT-R6-contract.md`, `src/gigai/runtime_comparison.py`, `src/gigai/schemas/runtime-comparison-attempt.schema.json` | Reuse current pack/R6 envelope where compatible; wrapper/adapter is proposed documentation only. Human gold and execution remain downstream gates. |
| S09 capability/privacy/storage/freshness | proposed | S09 is research/design input; no measured connector, freshness, storage-rights, or public/private-query experiment is present | Acquisition packet must keep capability claims separate from measured evidence and obtain an authorized decision before any live/provider work. |
| S11 inventory/selector groundwork | partial | `docs/development/v0.1.8/evidence/S11-groundwork.md:34-69,104-156`; focused lifecycle receipt 13/13 | Treat inventory as planning aid; runtime selector authority and release proof remain direct node IDs/receipts. |
| v0.1.7 artifact/build/publication/UAT/installed/live evidence | missing | `docs/development/v0.1.8/phase-2/evidence/P2-AUD-01-baseline-and-invocation.md:17-46,134-180`; exact-tag CI run `35660274375` failed in `tools/verify_installed_g03.py`; publication/TestPyPI/PyPI/GitHub Release/provenance and human UAT unproven/absent | Preserve exact-tag failure and absent evidence as release blockers; no shipment claim. |
| S12 graph traversal/auditable execution (**superseded for the Phase 3 proof by [Amendment 01](P2-FREEZE-04-amendment-01-scout-graph-proof.md)**) | proposed | `docs/development/v0.1.8/spikes/S12-gig-graph-traversal-and-auditable-execution.md:1-39,85-97` | Keep exploratory; do not redesign framework, composition, or block the Scout packet. |

## Canonical operation and case vocabulary

The vocabulary below is the reconciliation surface for evaluation/freeze. It names current or proposed operations without claiming each is implemented.

### Operations

`acquisition.import_supplied_rows`, `acquisition.status`, `acquisition.resume`, `discovery.resolve_completed_posting`, `assessment.select_private_inputs`, `assessment.invoke_local`, `assessment.publish_result`, `assessment.record_revision`, `assessment.associate_answers`, `tailor.select_sources`, `tailor.publish_document_revision`, `tailor.final_select_documents`, `tracking.record_event`, `tracking.read_history`, `view.rebuild_projection`, `view.publish_report`, and `view.read_report_status`.

For every operation, the record/journal producer owns bytes and revision/digest; consumers must identify the source receipt. A missing or changed input is an explicit refusal/failure, not a silent retry. An idempotent replay is permitted only where the existing operation’s event/record identity makes it provable; otherwise the packet must define the key before implementation.

### Cases

`duplicate_posting_snapshot`, `missing_posting_bytes`, `stale_journal_head`, `model_stopped_after_acquisition`, `proposal_input_revision_changed`, `unanswered_question`, `selected_document_digest_mismatch`, `application_retry`, `application_correction`, `unresolved_opportunity`, `report_rebuild`, and `tracking_without_assessment`.

These are case identifiers, not claims that all cases have executed receipts. Each implementation packet must carry the relevant case, source digest/revision, refusal/failure state, recovery attempt, and resulting committed event or unresolved disposition.

## State and envelope vocabulary

### Public assessment state versus tracking state

| Domain | Current/frozen meaning | Roadmap vocabulary | Rule |
| --- | --- | --- | --- |
| Public acquisition | `new`, `pending`, `failed`, `saved` as source/progress/view dispositions | release promise says selected employers can save/show pending/failed independently | Must be visible without inference; no assessment is required to retain a supplied row. |
| Assessment | `new`, `pending`, `running`, `succeeded`, `failed`, `skipped` | same roadmap list | Treat as an assessment view/state mapping until one authoritative schema is selected; do not alias a failure to a public acquisition failure. |
| Tracking | committed application events plus current derived state | `shortlisted`, `applied`, `interviewing`, `rejected`, `archived`, `untracked` | Event/journal authority wins; vocabulary conflict requires explicit alias/version amendment. |
| Proposal answer | embedded answer association exists; standalone schema also exists | proposal/assessment input association | Preserve revision and input digest; no unqualified merge of the two shapes. |
| Evaluation outcome | `typed_decision`, `abstain`, `uncertain`, `refused_or_failed`; decision statuses `supported`, `unclear`, `unsupported`, `not_applicable`; sufficiency `sufficient`, `insufficient` | S08/R6 typed decision intent | EVAL-03’s `gigai.typed_decision.v1` is a documentation wrapper; no runtime schema change is frozen. |

### R6/EVAL compatibility

The existing R6 output contract remains `r7-output-contract:1`, with verdict/criteria/unsupported-claims fields as recorded by EVAL-03. EVAL-03’s proposed `gigai.typed_decision.v1` requires `schema_version`, `case_id`, `outcome`, `decisions`, `evidence_refs`, `abstention`, and `uncertainty`; it is currently a documentation-only wrapper with no installed schema or runtime adapter. The smallest safe amendment is an explicit, versioned adapter/receipt that maps the wrapper to R6 without changing R6 bytes, omitting fields, silently coercing outcomes, or treating an assessment draft as gold. The adapter belongs to the assessment/evaluation packet, while shared schema registry changes—if later authorized—belong to integration and require Terra review.

EVAL-03 records 20 synthetic cases (10 objective, 10 judgment), five held out, pack digest `fa2f486f74628be295ce585c3f30066cd8162507652e5ef69fa98032d08c1143`, and self-check digest `d9bc8517dec063a85456e28b9b5c916023f6b34210f22ebb72fd8babc95a6ed4`. Its state is preparation-complete/execution-not-authorized; no human gold, model/provider attempt, or release score is present. The three review receipts above close the supplied manifest finding but do not advance those downstream states.

## Ownership and integration handoff

The non-overlapping packet boundaries are detailed in [`P2-FREEZE-04-dispatch-matrix.md`](P2-FREEZE-04-dispatch-matrix.md). In summary:

- **Acquisition/scheduling packet:** source connector boundaries, supplied-row import, watchlist/schedule/dedup/restart/failure, public records and acquisition CLI/receipts. It must not own assessment interpretation, private model inputs, tracking mutations, or UI truth.
- **Assessment/evaluation packet:** requirements/proposal/resume/local invocation, private-input selection, answer/revision association, Tailor selection, S08 pack/grader/R6 adapter and evidence receipts. It must not own acquisition, tracking, UI projection, or provider authorization.
- **UI/tracking/navigation packet:** projection/report/table/detail/navigation and explicit application event/correction/status history, preserving journal authority and public visibility. It must not create a second truth, infer state from model output, or change storage/schema without an integration handoff.
- **Integration role:** shared `src/gigai/run.py`, `src/gigai/journal.py`, schema registry, `src/gigai/cli.py`, copied wrappers, inventory coordination, and phase/daily receipts only. Integration reconciles seams and does not absorb packet implementation or approve release alone.

Root coordinates and owns the final gate. Luna records implementation after dispatch. Terra independently verifies completed outputs. These are roles from the ticket, not claims of staffed implementation or authorization.

## Acceptance receipt boundaries

| Receipt lane | Current evidence | What it can establish | What remains open |
| --- | --- | --- | --- |
| Source/static | AUD-01/AUD-02 source paths and JSON ledgers | Current source shapes, callers, route guards, and bounded operation map | End-to-end/installed/runtime behavior. |
| Synthetic/offline EVAL | EVAL-03 manifest/pack/self-check hashes; no execution | Pack identity, case inventory, compatibility proposal, deterministic-first rubric | Authorized execution, human gold, model/provider identity, quality decision. |
| Focused groundwork | S11 `13/13` focused lifecycle receipt | Bounded checkout-local groundwork | Release/installed/live acceptance. |
| Exact-tag CI | AUD-01 exact-tag run `35660274375` failed at `tools/verify_installed_g03.py` | Exact failure is a release blocker | Corrected rerun and all later gates. |
| Publication/UAT/installed/live | AUD-01 records unproven/absent | Nothing positive | Authorized build/attestation, package publication, human UAT, installed invocation, and live local/provider proof. |
| Review | Terra/Luna/Terra context receipts supplied to this task | Producer packet integrity and closure of the one manifest finding | Terra review of this freeze and root’s final gate. |

## Proposed amendments and dispositions

1. **Independent pending view (smallest):** add a reader/DTO that consumes acquisition source/progress records and explicit failure rows, retaining `new/pending/failed/saved` before assessment. It must expose source digest/revision and distinguish no assessment from assessment failure. Acquisition owns the record; UI owns the view; integration owns only the shared seam. **Disposition:** proposed, not implemented.
2. **Assessment view mapping:** document a versioned mapping from existing proposal/Run records to roadmap assessment states. A later schema amendment is allowed only if current record states cannot represent it. **Disposition:** proposed, no silent rename.
3. **Answer association adapter:** resolve the embedded answer association versus standalone schema conflict with one explicitly versioned adapter or a deliberate schema choice, preserving question/input revision and answer provenance. **Disposition:** conflicting, assessment packet blocker before implementation closes.
4. **Tracking vocabulary map:** map roadmap labels to committed application event/state semantics or amend a versioned enum; never infer application status from proposal, report, or model output. **Disposition:** conflicting, tracking packet blocker before acceptance.
5. **R6/EVAL envelope adapter:** retain R6 `r7-output-contract:1`; document the typed wrapper and explicit mapping receipt with no runtime schema change in this phase. **Disposition:** proposed, assessment/evaluation packet follow-up.
6. **S09 acquisition claims:** separate capability descriptions from measured source/freshness/storage/privacy evidence; public/private query separation remains a design input. **Disposition:** proposed, acquisition packet/user authorization required for any live work.
7. **S10 and release proofs:** preserve source-proven/injected-offline-only/no-hosted-fallback claims, but require separately authorized installed/live/provider evidence. **Disposition:** blocked downstream gate, not a freeze prerequisite.

## Exit decision

| Issue | Dated disposition (2026-09-22) | Next action / owner | Gate |
| --- | --- | --- | --- |
| Three producer packets and corrected EVAL manifest | **Dispatch-ready** | Root dispatches the three bounded packets; Luna records; Terra verifies each completion. | Terra review of this freeze, then root final gate. |
| Pending/public visibility independent of assessment | **Dispatch-ready with proposed amendment** | Acquisition defines source/progress failure contract; UI defines derived reader; integration reviews seam. | Source/projection receipt and no-private-mixing check. |
| Answer association and tracking vocabulary conflicts | **Blocked for packet acceptance, not for dispatch** | Assessment and tracking owners choose explicit adapter/version map and record migration-free disposition. | Terra verifies no silent alias or second truth. |
| R6/EVAL compatibility and human gold | **Dispatch-ready for offline preparation; execution blocked** | Assessment owner documents adapter and later requests explicit execution/human adjudication authorization. | EVAL manifest/pack hash, gold identity/date, execution receipt. |
| S09 connectors/freshness/storage/privacy | **User-decision-needed before live acquisition** | Root obtains an authorized source/privacy/freshness decision; no connector inference from descriptions. | Acquisition/source receipt, not this freeze. |
| Exact-tag CI/publication/UAT/installed/live | **Blocked** | Owner corrects G03, then records exact-tag rerun, build/attestation, publication, UAT, installed, and live receipts. | AUD-01 release gate; no shipment claim now. |
| S12 graph/Improve/framework (**Phase 3 proof amended: see [Amendment 01](P2-FREEZE-04-amendment-01-scout-graph-proof.md)**) | **Dispatch-ready with no dependency** | Keep spike exploratory; do not redesign framework or block Scout. | Root excludes from packet acceptance. |

**Proposed exit:** `dispatch-ready` for documentation-derived implementation dispatch on 2026-09-22, with the explicit blockers above. This is not release readiness. Root retains the final decision after Terra reviews this freeze; absent that review, the freeze remains a proposal rather than an accepted gate.

## Bounded validation receipts

Validation was intentionally limited to JSON parsing, required-field/status checks, internal path checks, manifest digest checks, Markdown-link checks, and owned-file whitespace checks; no suite, runtime, provider, model, search, daemon, installation, or publication command was run.

- `rtk proxy python -m json.tool docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-ledger.json >/dev/null` — exit `0`.
- Bounded ledger completeness check — exit `0`: `rows=18 required_fields=21 missing=0 bad_status=0`.
- Bounded source/internal-path check — exit `0`: `references_checked=33 missing=0`.
- Read-only EVAL manifest digest check — exit `0`: `manifest_hash_checks=[('pack', True), ('self_checks', True)]`; pack `fa2f486f74628be295ce585c3f30066cd8162507652e5ef69fa98032d08c1143`, self-checks `d9bc8517dec063a85456e28b9b5c916023f6b34210f22ebb72fd8babc95a6ed4`.
- Markdown relative-link check — exit `0`: `markdown_relative_links=12 missing=0`.
- Owned Markdown trailing-whitespace check — exit `0`: `owned_markdown_files=3 trailing_whitespace=0`.

A whole-tree diff/whitespace check was not used as acceptance because it would include unrelated dirty work.

## Amendments

**Final Terra closure of this freeze (pre-amendment):** dispatch
`ctx_faaa104e3ea4`, receipt `msg_a70bbd115366`, verdict "Phase 2
documentation complete, subject to root integration". It covers the freeze
as recorded above. It does not cover Amendment 01.

- **2026-09-22, [Amendment 01: the Phase 3 proof runs as a real, small
  Scout graph](P2-FREEZE-04-amendment-01-scout-graph-proof.md).** The
  operator decided that the thin vertical proof runs as a linear
  `acquire → assess → present` graph with `COMPLETE` edges.
  - It supersedes the S12 row's "keep exploratory" disposition for that
    proof only.
  - It adds a Luna integration trace of real node execution as the first
    step.
  - Everything else in this freeze stands.
