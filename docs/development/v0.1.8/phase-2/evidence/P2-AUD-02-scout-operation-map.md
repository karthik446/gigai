# P2-AUD-02 - Scout operation, record, and caller map

Recorded: 2026-09-22  
Owner role: Luna/max audit writer; Terra/medium review; root owns convergence  
Status: completed as a read-only source/schema/evidence audit. This is an offline
map for interface freeze; it is not implementation, release, installed-package,
provider, scheduler, model-quality, UAT, or publication readiness.

## Scope and evidence discipline

This map fulfills the P2-AUD-02 ticket: supplied-row acquisition and immediate
visibility, completed-discovery posting authority, local assessment/proposal and
answer association, selected resume/Tailor/document paths, explicit application
events, journal readers/projections/reports, and public CLI/Run/UI callers. The
ticket's required fields and edge cases are recorded in the machine-readable
ledger at `P2-AUD-02-interface-ledger.json`; every ledger row has a status from
the ticket's closed vocabulary (`existing`, `partial`, `missing`, `conflicting`,
or `proposed`). The ticket itself defines this boundary at
`docs/development/v0.1.8/phase-2/tickets/P2-AUD-02-scout-operation-record-caller-map.md:15-79,86-171`.

No product source, test, config, schema, or shared roadmap/index was changed.
No suite, model, provider, search, scheduler, daemon, private workpad, package
installation, activation, commit, push, or publication was performed. Existing
dirty work was preserved; the initial checkout status had unrelated modified,
deleted, and untracked paths, and the audited checkout remained at
`b01675d0b7ef39b49853a26df61aadeea2064a6a` on
`karthik446/gigai-v0.1.8`. Existing focused receipts below are cited as prior
evidence with their stated limits, not rerun evidence.

The authority rule is unchanged from R0: the journal and authenticated committed
artifact bytes are authority; SQLite and HTML are rebuildable views; selectors
must identify exact committed bytes under a pinned snapshot. R0 states this at
`docs/development/evidence/v0.1.7/Scout/SCOUT-R0-shared-interfaces.md:8-17`.
The current source now supplies several R1/R2/R3 readers that R0 described as
proposals, so the current source and schemas below supersede the historical sheet
where they differ. That does not turn source-only or focused evidence into
installed/live acceptance.

## Executive disposition

1. **Supplied public-row persistence exists, but acquisition does not.**
   `scout_acquisition_records` accepts an already acquired bounded JSON row list,
   commits immutable input/progress artifacts, and reconstructs status in a fresh
   process. Its module explicitly does not fetch, crawl, schedule, assess, or
   create a discovery packet (`src/gigai/scout_acquisition_records.py:1-7`). The
   standalone discovery classifier has the same boundary
   (`src/gigai/scout_discovery_job.py:52-107`). ATS watchlists, general search,
   scheduler/daemon ownership, and provider error degradation therefore remain
   missing/proposed capabilities, not inferred from an import row.

2. **Completed discovery has an authenticated posting path, but only for a
   completed Run.** The closed selector, succeeded unique terminal receipt,
   same-scope Plan/Run/checkpoint chain, passing checks, domain validation, and
   exact capture bytes are enforced by
   `src/gigai/scout_posting_inputs.py:341-489`. A discovery sidecar or report row
   is not proof that a provider was polled.

3. **Local assessment has a real host path, but the roadmap lifecycle vocabulary
   is not normalized.** Proposal execution resolves one authenticated posting and
   selected private revisions under a pinned writer, requires a configured local
   Ollama target, records invocation evidence, and writes a complete/failed
   result (`src/gigai/scout_proposal_execution.py:91-173,1129-1176`). The host
   result uses `complete` or `failed`; terminal Goal states also include
   `blocked` and `cancelled` (`:66-72`). The roadmap's `new`, `pending`,
   `running`, `succeeded`, `failed`, and `skipped` assessment states are not a
   single current schema/reader vocabulary.

4. **Proposal revisions and Tailor documents are journal-backed, explicit, and
   separate from tracking.** Proposal revision records bind opportunity
   references, exact selected private revision digests, answer associations,
   invocation identity, local method identity, and a sealed journal head
   (`src/gigai/scout_proposal_records.py:146-188`). Tailor selection and document
   revision paths require explicit source roles, exact bytes/digests, and
   host-authenticated Run provenance (`src/gigai/scout_tailor_selection.py:197-236`,
   `src/gigai/scout_document_records.py:138-249`). None creates an application
   event.

5. **Answer association has a shape conflict to freeze.** The current proposal
   record embeds `record_id`, `revision_id`, `question_ids`, `purpose`, and
   `content_sha256` (`src/gigai/scout_proposal_records.py:163-174`), matching the
   embedded `answer_associations` shape in
   `src/gigai/schemas/scout-proposal-revision.schema.json:16-18`. The standalone
   `scout-answer-association:1` schema additionally requires `schema_version` and
   `association_id` (`src/gigai/schemas/scout-answer-association.schema.json:3-12`),
   but the host helper emits no such standalone object. This is a freeze
   decision, not permission to edit either schema in this audit.

6. **Explicit tracking exists as journal events but does not match the roadmap
   status vocabulary.** The current event/schema enum is `saved`, `applied`,
   `interview_scheduled`, `offer_received`, `rejected`, and `withdrawn`
   (`src/gigai/application_events.py:27-34`,
   `src/gigai/schemas/application-event.schema.json:11-19`). There is no direct
   `shortlisted`, `interviewing`, or `archived` event kind, and `untracked` is a
   view default rather than a committed event. Therefore do not map `saved` to
   `shortlisted`, or `interview_scheduled` to `interviewing`, without a freeze
   decision.

7. **Projection/report readers are derived views and are deliberately read-only.**
   Projection reads one pinned snapshot, verifies application/proposal
   opportunity links, and carries a journal-head cursor
   (`src/gigai/scout_projection.py:273-306`). SQLite is disposable
   (`:309-363`), and rebuild uses production readers then caches only a payload
   (`:366-394`). Report rows require authenticated discovery/proposal/document
   refs; opportunity rows skip v1 output lacking a domain sidecar and only read
   succeeded discovery receipts (`src/gigai/scout_report_readers.py:83-147`). The
   generated HTML has no action forms; it renders jobs/history/proposals/questions
   and marks unresolved application links (`src/gigai/scout_report.py:114-197`).
   This is not the roadmap's immediate new/pending/failed tracking UI.

8. **The direct CLI is broader than the copied Scout Gig wrapper.** The installed
   CLI registers `application`, `report`, `document`, `answer`, and acquisition
   groups (`src/gigai/cli.py:47-52,4041-4048`). It exposes proposal/tailor/run
   details (`src/gigai/cli.py:3048-3207`) and application record/history/status
   (`src/gigai/application_cli.py:15-99`). The copied
   `src/gigai/data/scout/gig.py:70-147,337-372,415-554` wraps report, acquisition,
   proposal, Tailor, answers, and final document selection, but has no
   `application` parser or dispatch branch. There is no audited Run caller that
   records application events; the direct application command is the public
   writer.

## Canonical operation vocabulary

These names are stable reconciliation vocabulary for P2-EVAL-03 and the later
freeze. They describe operation boundaries, not new APIs.

| Operation | Current authoritative producer | Current consumer/view | Status |
| --- | --- | --- | --- |
| `acquisition.import_supplied_rows` | `import_public_rows` writes `records/scout-acquisition/{batch}/input.json` and a progress revision | acquisition status/resume; no discovery provider | existing, bounded to supplied rows |
| `acquisition.status` | `_load_status` authenticates input and progress chain | `scout-acquisition status`; status DTO | existing |
| `acquisition.resume` | `resume_public_acquisition` reuses exact committed input | `scout-acquisition resume` | existing |
| `discovery.resolve_completed_posting` | completed Run receipt/checkpoint/domain/capture tuple | proposal/Tailor/report readers | existing, completed-Run only |
| `assessment.select_private_inputs` | `scout_inputs` and native/private record resolvers | proposal/Tailor host callers | existing, exact revision/digest |
| `assessment.invoke_local` | proposal Run + local execution host | proposal result and Run details | existing, local route only |
| `assessment.publish_result` | host result + Goal terminal transition | proposal/report reader | existing/partial lifecycle normalization |
| `assessment.record_revision` | `record_proposal_revision` | proposal rows and Tailor source | existing, complete results only |
| `assessment.associate_answers` | embedded proposal association helper | proposal revision / Tailor selection | conflicting schema vocabulary |
| `tailor.select_sources` | `TailorSelection` and sealed Tailor Run | local Tailor invocation | existing |
| `tailor.publish_document_revision` | `record_document_revision` | document/report/application redemption | existing |
| `tailor.final_select_documents` | explicit operator final-select | selection reader/report/application document refs | existing, v1/v2 compatibility |
| `tracking.record_event` | `record_application` + operation receipt | journal event/history/projection/report | existing, enum narrower than roadmap |
| `tracking.read_history` | `read_application` from event snapshot | `application history/status` | existing |
| `view.rebuild_projection` | `rebuild_projection` from pinned journal snapshot | disposable SQLite cache | existing/derived |
| `view.publish_report` | `publish_report` from projection | generated local HTML and selector | existing/derived, read-only |
| `view.read_report_status` | `read_current_report` selector + current HEAD comparison | `report status` | existing, stale flag only |

## Canonical edge-case vocabulary

The following cases must be reconciled by later evaluation/freeze work. The map
does not execute them.

| Case | Current source behavior | Disposition |
| --- | --- | --- |
| `duplicate_posting_snapshot` | supplied-row import classifies repeated `(opportunity_id, snapshot_id)` or `duplicate_of` as duplicate; completed discovery resolver requires exactly one matching posting | existing for bounded import; provider dedup not implemented |
| `missing_posting_bytes` | resolver requires the captured `capture_ref`, exact committed bytes, digest/size, and rejects absent/incomplete capture | existing refusal |
| `stale_journal_head` | report selector compares recorded journal head to current HEAD; projection cursor records its pinned head; no automatic latest substitution | existing stale detection; rebuild/read policy remains a freeze input |
| `model_stopped_after_acquisition` | acquisition status is independent and durable, but report opportunity rows currently come from succeeded discovery Run outputs, not supplied-import progress | partial; immediate pending/failed dashboard behavior is not shown by current path |
| `proposal_input_revision_changed` | proposal/Tailor selectors redeem exact record/revision/digest under a pinned snapshot; changed/missing refs refuse | existing refusal |
| `unanswered_question` | native `experience_qa` states are `missing`, `answered`, `declined`, `not_applicable`; proposal prompt preserves unknown/notprovided/declined | existing native state; assessment lifecycle mapping remains partial |
| `selected_document_digest_mismatch` | document reader recomputes content digest/size and final selection requires a matching committed revision; application Scout document refs redeem exact Tailor result and digest | existing refusal |
| `application_retry` | operation receipt replay returns `already_recorded` only for identical request/event bytes; changed payload returns conflict | existing |
| `application_correction` | correction must target an earlier same-opportunity event; event IDs prevent identity collision; corrected events are supersession-aware in history/projection | existing, branch semantics should remain explicit in freeze |
| `unresolved_opportunity` | application event can be read and shown as unresolved when no opportunity reader matches; strict projection can refuse with `require_opportunity_links` | existing explicit unresolved view / optional strict refusal |
| `report_rebuild` | rebuild uses journal snapshot and production readers; SQLite cache is rewritten, not authority | existing derived rebuild |
| `tracking_without_assessment` | application record does not require a proposal revision; opportunity/document linkage is validated independently when readers are available | existing separation, but copied wrapper/UI caller is missing |

## Acquisition, save, pending, and discovery map

### Supplied-row persistence

The input schema is `scout-public-import-input:1` and accepts a bounded array of
public row objects; allowed fields and row states are enforced by
`src/gigai/scout_acquisition_records.py:33-45,246-270` and
`src/gigai/schemas/scout-public-import-input.schema.json:1-30`. Private fields are
rejected. The input is committed at
`records/scout-acquisition/{batch_id}/input.json`; progress revisions are
`records/scout-acquisition/{batch_id}/progress/{revision_id}.json`
(`src/gigai/scout_acquisition_records.py:273-292`).

Classification is deterministic: repeated identity/`duplicate_of` becomes
`duplicate`; row error/`failed`/`error` becomes failure; excluded rows become
exclusion; everything else is considered
(`src/gigai/scout_acquisition_records.py:309-327`). Progress carries the input
digest/ref, revision and parent revision, parent journal head, exact prefix
counters, deadline, stop reason, and outcome arrays
(`src/gigai/scout_acquisition_records.py:330-355`). The progress schema only
allows `completed` or `deadline` stop reasons and outcome records
(`src/gigai/schemas/scout-public-import-progress.schema.json:1-52`).

Fresh status authenticates working bytes against committed bytes, validates the
schema and scope, requires one root/one tip in the progress CAS chain, checks
parent revision/head links, and requires an exact processed-index prefix
(`src/gigai/scout_acquisition_records.py:378-456`). Import refuses a changed
input for an existing batch, replays a complete batch, or appends the next
progress revision through one journal transition
(`src/gigai/scout_acquisition_records.py:467-540`). Resume reuses the exact
committed input when no rows are supplied (`:543-557`).

The public CLI has `import`, `resume`, and `status` only
(`src/gigai/scout_acquisition_cli.py:14-101`); the copied Gig wrapper delegates
the same operations (`src/gigai/data/scout/gig.py:135-147,355-371`). Neither
path discovers or fetches a posting. The input file is a caller-supplied local
regular JSON file guarded against symlink/traversal paths
(`src/gigai/scout_acquisition_records.py:161-209`).

### Discovery Run authority and report rows

`run_bounded_public_import` is a transient classifier of caller-supplied rows;
its docstring explicitly excludes fetching, crawling, journal allocation, and
private matching (`src/gigai/scout_discovery_job.py:52-107`).
`resolve_selected_discovery_job` is instead a reader over the completed posting
resolver and derives `considered`/`uncertain` from source status while carrying
Run/receipt/checkpoint/posting provenance (`:155-185`).

The completed posting resolver has the authoritative chain: closed selector;
exact succeeded receipt and one terminal receipt; same-scope Run/Plan and
version/provenance checks; one exact checkpoint output; passing committed checks;
fixed domain validator; and exact posting capture bytes
(`src/gigai/scout_posting_inputs.py:341-489`). It never follows “latest” or
fabricates an opportunity. The historical SCOUT-07 review accepted this as a
bounded resolver but explicitly did not accept CLI/UI/Tailor/application/provider
execution (`docs/development/evidence/v0.1.7/Scout/SCOUT-07-posting-input-resolution-review.md:9-24,84-122`).

Report opportunity rows scan only succeeded discovery receipts, require an
authenticated domain sidecar, then resolve each posting against the same
snapshot; v1 records without a domain sidecar are skipped
(`src/gigai/scout_report_readers.py:63-147`). This is authoritative view
construction for completed discovery, not evidence of an external poll. The
current row path does not consume `records/scout-acquisition/{batch_id}/progress/*.json` as a
job row, so roadmap-required “new/pending/failed before assessment” visibility
is `partial` in the ledger.

### S09 design inputs only

S09 recommends public-only query construction and explicitly keeps private
preferences out of provider-bound search requests; it also notes provider
logging/forwarding and ATS differences
(`docs/development/v0.1.8/spikes/evidence/S09-local-search-retrieval-capability-sourcing-research.md:177-190`).
The roadmap records ATS watchlists and one general-search backstop as design
inputs while stating that freshness/indexing latency and durable storage rights
are not established (`docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md:34-37,63-103`).
S09's open questions retain freshness, caching/storage, provider-selection, and
ToS uncertainty (`docs/development/v0.1.8/spikes/evidence/S09-local-search-retrieval-capability-sourcing-research.md:340-359`),
and its measured addendum is only a small live reachability/result-shape trial,
not a freshness or sustained-coverage acceptance proof
(`docs/development/v0.1.8/spikes/evidence/S09-local-search-retrieval-capability-sourcing-research.md:361-388`). No legal
conclusion or live route is made here. The ledger therefore marks ATS polling,
general search, scheduler ownership, freshness, durable storage, and privacy
acceptance as proposed/open rather than existing.

## Assessment, proposal, answer, resume, and Tailor map

### Private input and assessment authority

`ScoutProposalRequest` accepts exactly one public posting source and bounded
private sources with explicit purposes; its lineage digest is host-built
(`src/gigai/scout_proposals.py:239-283`). The prompt exposes source handles only,
prohibits tools/URLs/browsing/network/external lookup, and says the result is
assessment text only, not a resume, Tailor action, application, verification, or
external action (`:286-309`). Output validation requires a complete structured
assessment (`:366-430`, especially `:400-402`).

The host caller resolves exact posting/private selectors under one writer, checks
the active Run/Goal and local Ollama target, and refuses a non-local target or
missing local permission (`src/gigai/scout_proposal_execution.py:91-173`). It
records invocation attempt/evidence, creates
`runs/{run_id}/scout-proposals/{invocation_id}/result.json`, and terminalizes the
Goal as completed or failed in one host transition
(`src/gigai/scout_proposal_execution.py:803-905,1129-1176`). A stopped/failed
model preserves failed invocation/result evidence; it does not silently become a
complete proposal. `src/gigai/cli.py:3048-3107` exposes the direct proposal Run;
the copied wrapper adds its own direct-confirm proposal caller at
`src/gigai/data/scout/gig.py:415-438`.

### Durable proposal revision and answer association

The model result is not itself a durable proposal revision. The host record is
`scout-proposal-revision:1`, state `active`, with an opportunity tuple, complete
assessment digest, input revision identities/digests/purposes, embedded answer
associations, invocation identity, local method/configured model digest, and a
sealed journal head (`src/gigai/scout_proposal_records.py:1-8,146-188`; schema
`src/gigai/schemas/scout-proposal-revision.schema.json:3-23`). Redemption
revalidates posting and private bytes and checks result/invocation identity under
the pinned snapshot (`src/gigai/scout_proposal_records.py:195-310`). Recording
is journal-backed and operation-key replay is byte-identity based
(`:314-470`). There is no `parent_revision` field in this strict schema; a new
proposal revision uses a new record/revision identity, so the freeze should state
whether “revision” means immutable sibling record or an explicit ancestry chain.

Native `experience_qa` answers are immutable revisions. Current states are
`missing`, `answered`, `declined`, and `not_applicable`; answered requires answer
text plus provenance, while unanswered states cannot contain answer text
(`src/gigai/schemas/native-record-content.schema.json:29-32`,
`src/gigai/native_records.py:115-145`). The answer CLI edits one selected native
revision only after explicit confirmation and operation key
(`src/gigai/scout_answer_cli.py:1-85`). The embedded proposal association
checks question IDs and answer digest, but the standalone association schema has
extra identity fields. This is the smallest known interface conflict.

### Tailor and final document selection

`TailorSelection` requires exact opportunity/snapshot identity, requested outputs,
one posting source, one candidate-evidence source, optional explicit answers and
proposal, and operator identity; its selector is
`scout-tailor-selection:2` (`src/gigai/scout_tailor_selection.py:187-236`). The
Tailor request is `scout-tailoring-request:1` with source roles and bounded
requirements/claims/gaps/questions (`:246-292`). The direct Tailor CLI requires
explicit private selectors, optional answer/proposal selectors, and confirmation
(`src/gigai/cli.py:3110-3174`); the Run writes immutable document revisions and a
`scout-tailor-run-result:1` result, while failures preserve invocation evidence
(`src/gigai/run.py:1231-1364`).

Document revisions carry exact content digest, source lineage, checks,
opportunity/snapshot, record/revision, and parent revision metadata; the journal
writer refuses identity-to-different-bytes conflicts
(`src/gigai/scout_document_records.py:138-197`). Final selection is an explicit
operator operation. It authenticates each revision and, for v2, Tailor Run
provenance; identical selection bytes replay, conflicting bytes refuse
(`:217-269`). The document CLI is `scout-documents final-select` and
`read-selection` (`src/gigai/scout_documents_cli.py:27-122`). Selection or
Tailor never implies application; the direct application path must still receive
its own confirmation and event input.

## Tracking/application, journal, projection, report, and UI map

### Event writer and reader

`record_application` requires `--confirm`, a validated opportunity identity,
timezone-bearing occurrence, operation key, and no caller-supplied request
evidence; the command constructs direct operator evidence and requested/payload
digests (`src/gigai/application_events.py:470-591`). The application event schema
is strict and closed (`src/gigai/schemas/application-event.schema.json:1-20`).
The writer validates Scout document refs and Tailor result provenance when those
refs are used (`src/gigai/application_events.py:199-383`), redeems legacy private
document refs, and publishes event plus operation receipt in one journal
transition (`:592-730`). Exact same-operation replay returns `already_recorded`;
different requested/event bytes conflict. Corrections require an earlier
same-opportunity event and cannot self-cycle (`:668-687`).

The current reader scans committed event artifacts in one snapshot, validates
each event, orders by UTC occurrence and journal sequence, groups by opportunity,
and removes superseded events from `current_status`/`statuses`
(`src/gigai/application_events.py:745-801`). Baseline SCOUT-09 recorded earlier
document-byte/evidence-binding findings and then recorded their correction
disposition; its final clarification is historical evidence only
(`docs/development/evidence/v0.1.7/Scout/SCOUT-09-application-service-review.md:14-38,40-57`).
The current source's `_validate_event` now binds direct evidence semantically
(`src/gigai/application_events.py:129-180`).

### Projection and report authority

Projection uses committed private revisions and event artifacts rather than
SQLite, annotates unresolved application/proposal opportunity links, and can be
strict or permissive (`src/gigai/scout_projection.py:110-306`). The in-memory
SQLite query view and `state.sqlite` cache are explicitly disposable
(`:309-363`); rebuild caches the snapshot-derived payload only (`:366-394`).
The production default readers scan completed Run/discovery, proposal, document,
evidence, and local Run artifacts (`src/gigai/scout_report_readers.py:296-455`).

HTML rendering is escaped, local/relative-link constrained, and has sections for
jobs, application history, proposals, questions, documents, evidence, and Runs
(`src/gigai/scout_report.py:114-231`). Publication records projection cursor and
journal head in generated metadata and atomically replaces a selector
(`:249-317`). Status validates the selector/path and marks it stale when its
recorded head differs from current `HEAD` (`:320-340`). The template is static;
there are no application update forms or tracking mutation controls in the
audited UI path. A later V018-01 UI can call the validated event operation, but
must not make HTML/SQLite a second authority.

### Public caller matrix

| Public caller | Source path | Reads/writes | Evidence boundary / gap |
| --- | --- | --- | --- |
| `gigai scout-acquisition import/resume/status` | `src/gigai/scout_acquisition_cli.py:14-101`; `src/gigai/cli.py:47-52,4041-4048` | journal writes for import/resume; reads status | supplied rows only; no fetch/schedule |
| copied Scout `gig.py acquisition ...` | `src/gigai/data/scout/gig.py:135-147,355-371` | same acquisition service | wrapper does not add a provider |
| `gigai proposal` | `src/gigai/cli.py:3048-3107` | Run/invocation/result writes | explicit local confirmation and selectors; no application |
| copied Scout `gig.py proposal` | `src/gigai/data/scout/gig.py:415-438` | delegates `launch_run` | source caller exists; no tracking |
| `gigai tailor` | `src/gigai/cli.py:3110-3174` | Tailor Run and document writes | explicit sources/outputs/confirmation |
| copied Scout `gig.py tailor` | `src/gigai/data/scout/gig.py:439-472` | delegates Tailor Run | no application event |
| `gigai scout-answer save` | `src/gigai/scout_answer_cli.py:22-85`; wrapper `:473-521` | native answer revision write | selected revision/question only |
| `gigai scout-documents final-select/read-selection` | `src/gigai/scout_documents_cli.py:27-122`; wrapper `src/gigai/data/scout/gig.py:522-554` | final-selection write/read | v2 provenance; explicit operator action |
| `gigai application record/history/status` | `src/gigai/application_cli.py:15-99` | event/receipt write; event reads | direct CLI only in audited wrapper set; no proposal prerequisite |
| report generate/status | `src/gigai/scout_report_cli.py:14-60`; wrapper `src/gigai/data/scout/gig.py:346-354` | derived report generation/status | read-only UI; stale cursor flag |
| local Run details | `src/gigai/cli.py:3177-3207`; report readers `src/gigai/scout_report_readers.py:296-343` | reads Run/result evidence | does not expose imported-row pending state |
| copied Scout `gig.py application` | audited parser/dispatch `src/gigai/data/scout/gig.py:70-147,337-554` | absent | smallest caller gap; do not infer an API |

## Assessment and tracking state vocabulary

The freeze must keep these namespaces distinct:

| Namespace | Actual current values | What it means | Roadmap/freeze issue |
| --- | --- | --- | --- |
| supplied acquisition row | `considered`, `uncertain`, `excluded`, `failed`, `error`; progress `completed`/`deadline` | caller-supplied public import outcome | not a provider/search lifecycle |
| completed discovery job | `considered` or `uncertain` derived from source status | authenticated posting view | no imported-row pending view |
| proposal result | `complete` or `failed`; error may be `invocation_not_succeeded`/`proposal_output_invalid` | model assessment execution/result | no normalized new/pending/running/succeeded/skipped enum |
| Run/Goal | active/receipt outcome; terminal Goal `complete`, `failed`, `blocked`, `cancelled`; report may expose `waiting_input` | execution lifecycle | must not be silently renamed to assessment status |
| proposal revision | `state: active`; assessment `status: complete` | durable immutable proposal record | not Run progress |
| native experience question | `missing`, `answered`, `declined`, `not_applicable` | user evidence availability | unanswered question is not failed assessment |
| Tailor/document | Tailor result `complete`/`failed`; document revision immutable; final selection v1/v2 explicit | generated/private artifact lifecycle and operator choice | never application state |
| application event | `saved`, `applied`, `interview_scheduled`, `offer_received`, `rejected`, `withdrawn`; view fallback `not recorded` | explicit operator tracking event | does not equal roadmap shortlist/interviewing/archived/untracked exactly |

The roadmap's acceptance sentence requires assessment states
`new/pending/running/succeeded/failed/skipped` and tracking states
`shortlisted/applied/interviewing/rejected/archived/untracked`
(`docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md:47-61`).
The current system has enough separate records to preserve the distinction, but
not enough shared vocabulary to claim the end-to-end promise.

## Smallest amendments and ownership handoffs

These are freeze questions and ownership handoffs, not implementation requests in
this packet.

1. **Acquisition owner:** decide whether the existing journal-backed supplied-row
   records are the durable handoff consumed by a future connector/scheduler, or
   whether a separate source snapshot/Run record is required. Preserve the
   existing input/progress schemas; do not make them fetchers. Define a pending
   projection row that can be shown before assessment and has a visible failure
   state, without adding private assessment fields to public rows.
2. **Acquisition/integration owner:** assign future ATS watchlist and general
   search connector/scheduler ownership. The smallest contract is a producer that
   emits the existing closed opportunity/posting tuple or an explicitly versioned
   new source record; it must carry source status, capture bytes/digests, and
   failure/retry identity. No source description is current connector evidence.
3. **Assessment owner:** choose and document a normalized view-only mapping from
   Run/Goal/result states to roadmap assessment states, or explicitly retain
   separate namespaces and teach evaluators the mapping. Do not alter the strict
   proposal revision schema merely to rename `complete`.
4. **R1/assessment owner:** resolve embedded-vs-standalone answer association.
   Smallest option A is to declare the embedded proposal shape authoritative and
   mark standalone `scout-answer-association:1` unused; option B is to make the
   embedded shape include the standalone identity/version. Either choice needs a
   freeze disposition and later focused validation; this audit changes neither.
5. **R1/R2:** document whether proposal “revision” is an immutable sibling
   record/revision identity (current behavior) or needs a parent/ancestry link.
   Current strict schema has no parent field; future ancestry must be additive and
   versioned rather than inferred from timestamps.
6. **Tracking/UI owner:** choose whether roadmap statuses are represented by a
   new versioned event enum or by a view mapping over existing events. Preserve
   direct confirmation, operation-key replay, supersession, UTC ordering, and
   exact opportunity/document redemption. `saved` and `interview_scheduled`
   should not be silently relabeled.
7. **Integration/UI owner:** add a caller edge for application record/history/status
   to the copied Scout Gig or explicitly document direct CLI as the only public
   caller. The later UI should call the same validated event writer and show
   unresolved opportunities without inventing links.
8. **Projection/report owner:** consume acquisition progress and Run failures in
   one pinned snapshot if “new/pending/failed before assessment” is release
   required. Existing `_opportunity_rows` only consumes authenticated succeeded
   discovery outputs; do not make SQLite authoritative to bridge the gap.
9. **S09/release owner:** before provider implementation, disposition public-query
   allowlists, freshness measurement, caching/storage rights, provider fallback,
   and ToS/legal review. S09 research is a design input and its small measured
   trial is not a connector acceptance receipt.

## Existing evidence and command receipts

The following are source/evidence receipts used for this map. They are
read-only/source inspection receipts and do not claim runtime execution here.

| Receipt | Result and limit |
| --- | --- |
| `rtk git status --short --branch` and `rtk git rev-parse HEAD` | exit 0; dirty paths preserved; HEAD `b01675d0b7ef39b49853a26df61aadeea2064a6a` |
| `rtk proxy nl -ba docs/development/evidence/v0.1.7/Scout/SCOUT-R0-shared-interfaces.md | sed -n '1,45p;132,181p'` | exit 0; historical authority/ownership sheet read, not treated as current proof |
| `rtk proxy nl -ba src/gigai/scout_acquisition_records.py | sed -n '1,210p;246,557p'` | exit 0; supplied-row input/progress/status/resume source audit |
| `rtk proxy nl -ba src/gigai/scout_discovery_job.py | sed -n '1,205p'` and `rtk proxy nl -ba src/gigai/scout_posting_inputs.py | sed -n '341,500p'` | exit 0; transient import boundary and completed-Run resolver audit |
| `rtk proxy nl -ba src/gigai/scout_proposal_execution.py | sed -n '1,180p;803,905p;1129,1180p'` | exit 0; local assessment host/result/failure audit |
| `rtk proxy nl -ba src/gigai/scout_proposal_records.py | sed -n '146,470p'` and schema reads | exit 0; proposal revision and association shape audit |
| `rtk proxy nl -ba src/gigai/scout_tailor_selection.py | sed -n '187,347p'` and `rtk proxy nl -ba src/gigai/scout_document_records.py | sed -n '138,269p'` | exit 0; Tailor/document selection and digest/provenance audit |
| `rtk proxy nl -ba src/gigai/application_events.py | sed -n '27,245p;470,801p'` and `application_cli.py` | exit 0; event writer/reader/CLI audit |
| `rtk proxy nl -ba src/gigai/scout_projection.py | sed -n '1,115p;248,410p'`, report-reader, and report-source reads | exit 0; authority/view/stale/report/UI audit |
| `rtk proxy nl -ba docs/development/evidence/v0.1.7/Scout/SCOUT-09-application-service-review.md | sed -n '1,57p'` and SCOUT-07/08 baseline reads | exit 0; prior focused evidence and explicit limits retained; not rerun |
| `rtk proxy nl -ba docs/development/v0.1.8/spikes/evidence/S09-local-search-retrieval-capability-sourcing-research.md | sed -n '177,245p;340,390p'` | exit 0; S09 privacy/storage/freshness design inputs only |

No broad test command appears in these receipts because the ticket prohibits
running suites. The existing S11 receipt remains bounded focused checkout-local
evidence, not release/installed/live acceptance; the phase rules state this at
`docs/development/v0.1.8/phase-2/tickets/README.md:39-60,82-89`.

## Final disposition

P2-AUD-02 is complete as a self-contained source-verified map and machine
ledger. The later freeze must reconcile the supplied-row/import versus connector
boundary, pending visibility before assessment, assessment lifecycle vocabulary,
embedded answer association shape, roadmap tracking enum, copied-wrapper caller
gap, and S09 provider/privacy/storage caveats. Nothing in this packet changes
schemas, adds callers, activates providers/models/daemons, or claims release
readiness.
