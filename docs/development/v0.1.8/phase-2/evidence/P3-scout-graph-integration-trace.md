# P3 Scout graph integration trace

**Recorded:** 2026-09-22  
**Scope:** read-only integration trace for P2-FREEZE-04 Amendment 01  
**Status:** trace complete; implementation, execution, live/provider/model calls,
tests, and release acceptance remain separately unauthorized.

## Jira summary

| Field | Summary |
| --- | --- |
| Ticket | P3-FREEZE-04-A01: make the Phase 3 proof a real linear Scout graph |
| Decision traced | The traversal is `acquire ──COMPLETE──▶ assess ──COMPLETE──▶ present`; assessment labels stay in the assessment artifact, not in graph routing. The amendment explicitly does not authorize implementation, live acquisition, model/provider execution, or a release claim (`P2-FREEZE-04-amendment-01-scout-graph-proof.md:13-16,40-55`). |
| Current verdict | The repository has durable supplied-row acquisition, local proposal assessment, and derived report readers, but they are separate paths. The compiler emits one zero-edge graph, the generic goal executor writes placeholder evidence, and no current callable binds all three operations into one traversal (`src/gigai/scout_materialization.py:226-282`; `src/gigai/run.py:3248-3616`; `src/gigai/scout_acquisition_records.py:1-7`). |
| Bounded source recommendation | For the first no-provider graph proof, use one operator-supplied public-row JSON batch through `acquisition.import_supplied_rows`; this is the existing durable bounded acquisition callable and needs no live authorization. If `acquire` must fetch rather than ingest supplied rows, make Greenhouse's public job-board feed the single later S09 source candidate, subject to an explicit live-source authorization; this trace did not call it or authorize it. |
| Smallest coherent shape | Integration owns the explicit three-node graph, exact node-callable binding, node receipts, scheduler/effect/consent seam, and continuation policy. Packets A/B/C keep acquire/assess/present behavior and expose replaceable callables, as the amendment assigns (`P2-FREEZE-04-amendment-01-scout-graph-proof.md:109-123`). |
| Blockers | A source/authorization decision, a same-run versus new-run continuation decision, a versioned per-node receipt contract, the proposal answer-association choice, and a public assessment-state mapping remain open. The existing report status rule is incorrect for a multi-node run. |
| Next gate | Terra reviews this trace only after it is complete; root retains the final gate. The pre-amendment freeze closure `ctx_faaa104e3ea4` / `msg_a70bbd115366` explicitly does not cover Amendment 01 (`P2-FREEZE-04-interface-freeze.md:160-175`; `P2-FREEZE-04-amendment-01-scout-graph-proof.md:137-146`). |

## Evidence boundary and notation

This is a static source trace. I read the amendment, applicable repository
instructions, the frozen interface evidence, S09, S12 evidence, current callers,
producers, readers, schemas, and runner paths. No test, suite, model/provider,
network, live source, installed invocation, commit, or memory write was performed.

`[E]` means an existing, source-cited mechanism; `[P]` means a proposed seam or
implementation packet; `[O]` means an open decision or evidence boundary. Absence
claims below are limited to the inspected callers and sources; where a source
explicitly documents a boundary, that statement is cited instead of treating a
grep result as proof.

The freeze authority remains: committed records and journal entries own bytes;
public visibility precedes private assessment; provenance is pinned; views are
derived; and there is no implicit authorization (`P2-FREEZE-04-interface-freeze.md:32-40`).
The amendment's five acceptance conditions are therefore traced as receipt
requirements, not treated as already satisfied (`P2-FREEZE-04-amendment-01-scout-graph-proof.md:57-79`).

## Integration map

### End-to-end edge verdict

| Edge / node | Existing caller | Existing producer and persisted handoff | Existing reader | Trace result |
| --- | --- | --- | --- | --- |
| `acquire` | `gigai scout-acquisition import`, `resume`, and `status`; the direct CLI reads caller-supplied rows and calls the record service (`src/gigai/scout_acquisition_cli.py:20-101`). The copied wrapper reaches the same import/resume/status functions (`src/gigai/data/scout/gig.py:135-371`). | `import_public_rows` writes immutable `records/scout-acquisition/{batch}/input.json` plus chained progress revisions containing input digest/ref, processed prefix, outcomes, and journal ancestry (`src/gigai/scout_acquisition_records.py:273-355,467-533`). | `read_public_acquisition_status` authenticates the input and progress chain (`src/gigai/scout_acquisition_records.py:378-464`). The existing report opportunity reader does not read these records; it reads succeeded external discovery receipts and v2 discovery sidecars (`src/gigai/scout_report_readers.py:63-147`). | `[E]` durable bounded import/status/resume and within-batch dedup exist. `[P]` a graph node must call this operation and a presentation reader must expose its progress/failures. It is not a fetcher or scheduler. |
| `acquire ──COMPLETE──▶ assess` | No current multi-node caller. `launch_run` has a specialized `proposal_execution` branch, not a supplied-row-to-assessment traversal (`src/gigai/run.py:363-431`). | Acquisition rows are persisted before any assessment call. The existing import classifies each row as considered, duplicate, failure, or exclusion (`src/gigai/scout_acquisition_records.py:309-327,467-533`). | Status CLI can show the acquisition batch, but the report/projection path has no independent acquisition-progress reader (`src/gigai/scout_report_readers.py:83-147,415-444`). | `[P]` add one automatic dependency edge whose source outcome is runner `COMPLETE`. The acquired posting snapshot and its digest/ref must be an explicit assess input; acquisition failure must remain visible and must not become an absent row. |
| `assess` | Direct `gigai proposal` requires confirmation and calls `launch_run(..., proposal_execution=...)`; the command is assessment-only and does not Tailor/apply/submit/export (`src/gigai/cli.py:3048-3107`). The wrapper calls the same specialized proposal route (`src/gigai/data/scout/gig.py:415-438`). | `execute_local_proposal` resolves one authenticated posting plus selected private sources, invokes the configured local Ollama target with `offline=True`, persists invocation artifacts, validates the result, and publishes a result artifact (`src/gigai/scout_proposal_execution.py:91-173,206-270,285-365,1129-1176`). A complete result can then become an immutable proposal revision (`src/gigai/scout_proposal_records.py:356-470`). | Proposal revisions are read separately from discovery opportunity rows (`src/gigai/scout_report_readers.py:150-190`). The local result is also authenticated by its Goal evidence in the evidence reader (`src/gigai/scout_report_readers.py:346-411`). | `[E]` local assessment and its source lineage exist. `[E]` no hosted fallback is allowed by the target/`offline=True` checks. `[P]` an assess-node adapter must consume the acquisition handoff and selected private evidence under the same traversal. The current proposal path is not itself a three-node graph callable. |
| `assess ──COMPLETE──▶ present` | There is no current caller that feeds a proposal result and acquisition batch into one presentation node. `scout_report` rebuilds a projection from a journal snapshot and publishes a derived report (`src/gigai/scout_projection.py:273-337`; `src/gigai/scout_report.py:249-340`). | Proposal revisions and external discovery outputs are separate committed artifacts. The assessment result can carry questions and requested actions, but its write path does not create tracking/application state (`src/gigai/scout_proposal_execution.py:1-8`; `src/gigai/scout_proposal_records.py:146-188`). | `_proposal_rows` exposes assessment content and questions through proposal rows; `_opportunity_rows` exposes only completed external discovery postings as opportunities; acquisition progress is not joined (`src/gigai/scout_report_readers.py:83-190`). | `[P]` present must read explicit acquisition rows plus an assessment artifact/revision, then rebuild/publish the derived UI. The edge is runner `COMPLETE`; `matches/partial/no_match` remain domain fields read by present, not routing outcomes. |
| One traversal | `launch_run` prepares a Run and can execute one generic graph, but proposal execution is a specialized branch and external recording is a separate Run family (`src/gigai/run.py:142-207,363-431,657-721`; `src/gigai/run.py:738-765`). | Generic run details journal goal transitions; proposal execution publishes domain result and invocation artifacts; external recording has its own plan/run/checkpoint/receipt artifacts. | Run details, proposal readers, external-run readers, and evidence readers are not one node-aware acquisition/assessment/presentation view (`src/gigai/scout_report_readers.py:296-444`). | `[P]` one sealed graph/run must own the three callables and make every handoff explicit. Existing artifacts can be reused, but their separate authorities must not be silently merged. |

### Acquire: actual source, callable, persistence, and visibility

**Existing callable and source.** `scout_acquisition_records` explicitly accepts
rows that have already been acquired and states that it does not fetch, crawl,
schedule, assess, or create a discovery packet (`src/gigai/scout_acquisition_records.py:1-7`).
The transient discovery import has the same boundary: it classifies caller rows
until a deadline and performs no fetching, crawling, journal allocation, or private
matching (`src/gigai/scout_discovery_job.py:52-107`). Therefore the existing
`import_public_rows` callable is a bounded *ingestion* source, not evidence that a
connector exists.

The direct acquisition CLI and copied wrapper are the real entry points. The CLI
does not select a provider or schedule a fetch; it reads the caller's row file and
invokes import/status/resume (`src/gigai/scout_acquisition_cli.py:20-101`). A future
graph node should bind to the same service, not to the transient classifier, and
pass a sealed batch ID, canonical input bytes/digest, deadline, and resolved
workpad scope.

**Existing producer and dedup.** The import writes the input snapshot and a
progress revision with `input_sha256`, `input_ref`, parent revision/journal head,
prefix counters, and considered/duplicate/failure/exclusion arrays
(`src/gigai/scout_acquisition_records.py:330-355,467-533`). It authenticates one
root, one tip, a parent chain, and an exact processed prefix before returning
status (`src/gigai/scout_acquisition_records.py:378-456`). Within a batch, duplicate
identity is `(opportunity_id, snapshot_id)` and complete replay returns the
existing status when the input digest matches; a different input under the same
batch ID is refused (`src/gigai/scout_acquisition_records.py:481-505`).

That is strong evidence for same-batch replay and partial resume. It is not proof
of cross-batch/provider deduplication: a different batch ID can represent the same
posting identity, and the current importer has no provider-side fetch identity.
The `duplicate_posting_snapshot` case therefore needs an explicit cross-run key in
the future packet rather than an assumption from the existing within-batch set.

**Source decision.** The smallest first proof should use one operator-supplied
public-row JSON batch as its bounded source. It exercises real journal persistence,
progress/failure, and dedup without introducing a live authorization or pretending
that a connector is installed. This means the first proof demonstrates graph
integration over a bounded supplied source; it does not establish web freshness.

If the word `acquire` is required to mean network fetching, the single bounded S09
candidate for a later authorized packet should be a small user-curated Greenhouse
board watchlist. S09 recommends ATS-feed polling as the strongest documented
discovery/fetch path and describes Greenhouse's public keyless JSON feed, full
content option, and `updated_at` signal (`S09-local-search-retrieval-capability-sourcing-research.md:11-15,29-37,62-71`).
That is a proposed source choice only: S09 did not perform ATS polling here and
did not establish full ToS clearance, a publish-to-feed latency, or a live
authorization (`S09-local-search-retrieval-capability-sourcing-research.md:3-4,13,69`).
No Greenhouse call belongs in this trace or in the first no-provider proof.

**Acquisition visibility when assessment fails.** The committed acquisition
batch survives an assessment failure by construction if the assess node only
reads it; the public import's failure/duplicate/progress entries remain durable.
However, the current tracking/report reader does not expose those acquisition
records. It materializes opportunities only from succeeded external discovery
receipts with a v2 discovery sidecar (`src/gigai/scout_report_readers.py:83-147`).
The smallest present-side addition is an acquisition reader/DTO keyed by batch,
opportunity, and snapshot that exposes `new/pending/failed/saved` plus input and
progress refs, then optionally joins an assessment row. A missing assessment must
mean “not assessed” rather than “posting absent”; an assessment failure must be a
second state, not a public acquisition failure. This is consistent with the
frozen public/assessment state separation (`P2-FREEZE-04-interface-freeze.md:48-54,83-92,123-131`).

### Assess: real path, node adapter, and failure boundary

**Existing proposal contract.** `ScoutProposalRequest` accepts exactly one public
posting source and bounded private sources with explicit purposes and unique
handles (`src/gigai/scout_proposals.py:239-283`). Its prompt is local assessment
text only: no resume, Tailor, application, verification, external action, tools,
URLs, browsing, files, network, or external lookup (`src/gigai/scout_proposals.py:286-309`).
The output validator requires a closed v1 `scout-private-proposal` object with
`status=complete`, evidence handles, fit/blockers/unknowns, focus/questions,
ranking, and requested actions (`src/gigai/scout_proposals.py:366-470`).

The host verifies a committed active Run/Goal and exact `write_workpad` effect,
requires an installed local Ollama target, and calls model transport with
`offline=True` and `commit_goal_transition=False` before domain validation
(`src/gigai/scout_proposal_execution.py:133-173,206-246,629-695`). It records the
invocation attempt before publishing a terminal domain result, and identifies the
producer as `scout-proposal-execution` in that transition
(`src/gigai/scout_proposal_execution.py:285-350,803-905`). A stopped or invalid
model result remains a failed result rather than becoming a successful proposal
revision (`src/gigai/scout_proposal_execution.py:1129-1176`; `src/gigai/run.py:401-430`).

**Why an adapter is required.** `launch_run` currently enters this path only
when a direct caller supplies `proposal_execution`; it starts one proposal Goal,
calls `execute_local_proposal`, and records a proposal revision on success
(`src/gigai/run.py:363-431`). `_terminalize_goal_details` deliberately terminalizes
only that Goal and leaves other sealed Goals pending, which is correct for the
specialized caller but not sufficient as the assess step of a three-node traversal
(`src/gigai/scout_proposal_execution.py:736-801`).

The assess node therefore needs a narrow adapter, not a second proposal protocol:

1. Resolve the acquired posting selector to the exact saved snapshot and digest.
2. Resolve explicitly selected local profile/experience/preferences (and the
   chosen answer association shape) under the same journal snapshot.
3. Invoke the existing local proposal host with the graph Goal's Run/Goal
   authority and node operation key.
4. Return/persist a node output reference to the validated assessment result and,
   where selected by the packet, the immutable proposal revision.
5. Record model target, invocation reference, usage, evidence, and failure state
   in the node receipt; do not write application/tracking state.

The adapter must preserve the no-hosted-fallback rule. It should refuse a missing
local target or a `local_allowed=false` request, not route to a hosted provider.
The model-stopped-after-acquisition case must leave acquisition visible and the
assessment node failed/unfinished with explicit error and receipt refs.

### Present: readers, derived UI, and status gap

`projection_from_snapshot` builds a disposable view from journal-backed records
and readers; `rebuild_projection` caches a projection but does not make SQLite
authority (`src/gigai/scout_projection.py:1-8,273-337,366-394`). `publish_report`
renders that view and pins it to a journal head; a stale head is detected on read
(`src/gigai/scout_report.py:249-340`). These are appropriate present-side seams.

The current readers have three relevant, separate streams:

- `_opportunity_rows` reads only succeeded external discovery receipt outputs of
  kind `discovery`, validates their domain sidecars, and marks their acquisition
  state `considered` (`src/gigai/scout_report_readers.py:83-147`). It does not
  enumerate `records/scout-acquisition/.../progress`.
- `_proposal_rows` reads committed proposal revisions and exposes assessment,
  input revisions, answer associations, invocation, method, and source refs
  (`src/gigai/scout_report_readers.py:150-190`).
- `_run_rows` reads external runs and local `run-details`, while `_evidence_rows`
  authenticates result artifacts against a Goal's evidence (`src/gigai/scout_report_readers.py:296-411`).

The present callable should rebuild/publish from those authorities plus the new
acquisition DTO. It should display results and questions from the assessment
artifact, acquisition rows even when assessment failed, and per-node status,
evidence, producer, and usage refs. It must not infer an application/tracking
event from assessment output; the freeze keeps proposal, report, and application
operations separate (`P2-FREEZE-04-interface-freeze.md:34-40,101-110`).

### Explicit graph and `COMPLETE` edges

**Existing authoring.** `_compiled_snapshot` currently loops over selectors and
creates one goal per selector, with `outcomes: ["COMPLETE"]`, `edges: []`, one
entry/terminal goal, deterministic/offline executor metadata, and
`effects: ["write_workpad"]` (`src/gigai/scout_materialization.py:226-282`).
The `find-jobs` output contract is a v2 discovery packet, not an acquire/assess/
present graph contract (`src/gigai/scout_materialization.py:307-323`). S12's
mapping records that the real find-jobs result enters through external recording,
which does not consult graph edges; the amendment calls this out directly
(`P2-FREEZE-04-amendment-01-scout-graph-proof.md:20-32`).

**Proposed bounded graph.** Integration should author one explicit, versioned
three-goal graph for the proof, rather than changing the compiler into a generic
builder:

| Goal | Sealed input contract | Sealed output contract | Producer/evidence |
| --- | --- | --- | --- |
| `acquire` | One approved supplied-row batch (or, only after source authorization, one Greenhouse watchlist request); source scope, batch/input digest, deadline | Acquisition input/progress refs, row snapshot refs, dedup/failure summary | `acquisition` callable, source receipt, progress/failure evidence |
| `assess` | Exact acquired posting snapshot refs plus explicitly selected local evidence and answer-association version | Validated local assessment result and optional proposal revision refs | `assessment` callable, invocation/result/proposal evidence, local model usage |
| `present` | Acquisition refs plus assess result/revision refs and node status refs | Derived projection/report refs containing results, questions, public rows, and per-node status | `view` callable, report/projection evidence; no tracking mutation |

The graph has only two automatic dependency edges: `acquire → assess` with
`on_outcomes=["COMPLETE"]`, and `assess → present` with the same condition. The
runner's existing readiness code already requires every incoming dependency to
have a complete source and an allowed outcome (`src/gigai/run.py:3463-3488`).
Assessment domain labels `matches`, `partial`, and `no_match` remain in its
output artifact; they do not become graph outcomes, branches, or gates, exactly
as the amendment requires (`P2-FREEZE-04-amendment-01-scout-graph-proof.md:46-55`).

### Real callable binding

The current goal graph carries executor/capability metadata, but the generic
executor has no domain dispatch: `_execute_goal` writes only
`gigai-offline-ok:<goal_id>` evidence (`src/gigai/run.py:3546-3561`). The existing
proposal callable is reached only by a specialized branch, and external recording
uses a fixed domain-validator table for output validation rather than a graph-node
execution callback (`src/gigai/external_recording.py:139-194`). These are distinct
mechanisms, not a current end-to-end binding.

The smallest binding is an explicit integration-owned dispatch table for this
single approved graph/version and its three goal slugs. Before scheduling, the
runner resolves `(approved graph identity, graph version, goal slug)` to exactly
one packet callable and refuses an unknown or mismatched binding. Each callable
receives a sealed node context containing Run/Goal IDs, graph/manifest digests,
declared input refs, target observation, journal writer scope, and operation key;
it returns output refs, evidence refs, producer identity, usage, and a terminal
outcome. This is a concrete three-entry seam, not a catalog, plugin system,
generic graph builder, or orchestration framework.

The graph contract must bind the callable identity/version as an approved byte (or
as an integration-owned fixed mapping whose key is sealed by the graph). A Goal
with the right slug but an unapproved callable, input contract, or effect set must
fail closed before model/provider or writer effects. The packet should reuse the
existing proposal host and report/acquisition services behind these callables,
not duplicate their authority logic.

### Sealed graph, executor/effect policy, and consent

**Existing authority and sealing.** A Run resolves the active approved version or
immutable tag, revalidates the proposal/Graph Set, and reads descriptor references
from the approval commit. It checks exact digest/size for graph and attached
contracts, validates semantic graph shape, and enforces descriptor ceilings for
budget, effects, and executor capabilities (`src/gigai/run.py:1669-1896`). The
Run also seals the graph and manifest digest into `run_started` before execution
(`src/gigai/run.py:303-351`; `src/gigai/run.py:3248-3279`). These are reusable
sealing mechanisms.

**Existing scheduler policy.** `_validate_scheduler_policy` allows one automatic
goal at a time, `fail_gig`, automatic dependency edges only, executor capability
`gigai.offline` or `gigai.deterministic`, and exactly `effects=["write_workpad"]`
(`src/gigai/run.py:3438-3460`). The compiled Scout descriptors likewise declare
only deterministic provider eligibility, `gigai.offline`, and the workpad effect
(`src/gigai/scout_materialization.py:354-395`). This policy currently cannot
express a live network fetch, and the generic executor has no local-model/domain
callable despite proposal execution separately using a local offline invocation.

**Consent.** Direct Run consent must be a `direct_cli_confirm` envelope from the
local operator and is redeemed before allocation; its scope includes project,
Gig/version, target observation, and any sealed plan (`src/gigai/run.py:247-302`).
The CLI's `run` and `proposal` commands require direct confirmation
(`src/gigai/cli.py:2980-3033,3048-3107`). This is not provider authorization.
For the supplied-row first proof, no new network effect is needed. If a future
Greenhouse source is authorized, the source declaration, capability/effect ceiling,
consent scope, secret/privacy boundary, and failure receipt must be amended and
reviewed before a live call; do not widen the existing offline policy implicitly.

**Proposed policy seam.** Keep the scheduler fail-closed and linear. Add only the
three graph's declared callable/capability/effect combinations, with the first
proof retaining `write_workpad` and no provider effect. A live source, if later
approved, must have an explicit source capability/effect and operator consent
scope; it must not be smuggled through `gigai.offline` or the deterministic
placeholder executor. The no-hosted-fallback rule remains unchanged.

### Persisted per-node inputs, outputs, evidence, producer, and usage

**What exists.** `run-details` Goal entries currently persist Goal ID/version,
executor, status/outcome/errors, evidence, usage, and timestamps; they do not
persist node input refs, output refs, or producer identity in the Goal detail
schema (`src/gigai/run.py:4021-4099`; `src/gigai/schemas/run-details.schema.json:118-153`).
Generic Goal transitions include evidence and a zeroed usage object, but the actor
is hard-coded to `gigai/deterministic` (`src/gigai/run.py:3564-3586`). The generic
output is only placeholder evidence (`src/gigai/run.py:3546-3561`).

Proposal execution has richer but non-uniform evidence: model invocation records
contain request/response artifacts and usage; the host result includes request
digest, input lineage, sealed journal head, response artifact, and status
(`src/gigai/scout_proposal_execution.py:206-270,285-365,1129-1176`). Its terminal
journal actor is `scout-proposal-execution` (`src/gigai/scout_proposal_execution.py:875-888`).
External recording similarly persists sealed inputs, output/check contracts,
artifacts, checkpoints, and receipts, but uses a separate external-run family
and `external-recording` producer (`src/gigai/external_recording.py:781-1112,1167-1292,724-778`).
None of these facts proves that one graph traversal currently carries a uniform
per-node receipt.

**Proposed additive node receipt.** Integration should add one versioned,
journal-authenticated node receipt (or an explicit additive run-details extension)
with at least:

- `run_id`, `goal_id`, `goal_version`, graph/manifest digests, and node operation
  key;
- declared input refs and content digests, including source batch/posting and
  selected private revisions;
- output refs and content digests, including assessment/projection/report outputs;
- evidence refs and case/failure identifiers;
- producer identity: packet callable ID/version, actor, and model/provider target
  when applicable;
- usage/cost with an explicit availability/measured flag so generic zero usage is
  not mistaken for measured zero cost;
- started/finished timestamps and terminal outcome (`COMPLETE` or explicit
  failure/interruption).

The receipt must be written as part of the node transition, preserve immutable
artifact bytes, and be readable from the journal snapshot. It must not promote a
projection or model draft to authority. The per-node `required_evidence` already
exists in the graph contract and can remain the completion gate; the new receipt
adds the missing identity and handoff fields without introducing custom graph
outcomes (`src/gigai/scout_materialization.py:275-281`; `P2-FREEZE-04-amendment-01-scout-graph-proof.md:81-107`).

### Existing proposal assessment versus discovery-packet contracts

These contracts must be adapted, not conflated:

| Dimension | Discovery packet (`find-jobs`) | Proposal assessment | Integration consequence |
| --- | --- | --- | --- |
| Authority/input | The packaged renderer is pure and has no workpad/provider/journal authority; a later fixed bridge must authenticate selected input and persist bytes (`src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.py:1-5`). It binds a selected profile and supporting public evidence (`:417-432`). | Host joins one authenticated public posting with bounded private sources for purposes such as preferences/experience/answer; source lineage is host-owned (`src/gigai/scout_proposals.py:239-283`; `src/gigai/scout_proposal_execution.py:908-940`). | `acquire` supplies the saved public posting; `assess` must explicitly select local evidence. Do not let model output invent lineage or use private fields in a provider query. |
| Cardinality | Multiple posting snapshots, deduped by snapshot and opportunity identity; output includes postings and source evidence (`discovery.py:433-449`). | Exactly one posting per proposal request plus selected private sources (`scout_proposals.py:239-264`). | The assess adapter must iterate or schedule one bounded assessment per acquired snapshot without pretending a single proposal is the whole discovery packet. |
| Domain outcome | Lower-case `matches`, `partial`, or `no_match` are required inside the discovery object, with shortlist/exclusions/questions and consistency rules (`discovery.py:433-480`). | Output is closed `scout-private-proposal`, `status=complete`, with fit/blockers/unknowns/focus/questions/ranking/actions (`scout_proposals.py:400-470`). | Keep domain labels inside assessment output. Graph edges use only runner `COMPLETE`; present interprets the artifact. |
| Provenance | Packet origin carries `find-jobs`, graph/run identity, selected inputs, profile, and discovery; sidecar binds exact rendered bytes (`discovery.py:491-530`). | Host result carries request digest, input lineage, invocation digest, response artifact, sealed journal head, and model invocation refs (`scout_proposal_execution.py:236-270,1129-1176`). | Present must retain both source types and expose refs/digests; it must not recast a proposal result as a discovery packet. |
| Consumer | External recording validates a fixed discovery domain and report opportunity rows consume succeeded discovery receipts (`src/gigai/external_recording.py:139-194`; `src/gigai/scout_report_readers.py:83-147`). | Proposal rows consume immutable proposal revisions and show assessment/input/invocation data (`src/gigai/scout_report_readers.py:150-190`). | Add an explicit join keyed by acquired posting snapshot and assessment receipt; do not reuse discovery opportunity status as assessment state. |

The answer-association conflict remains an implementation blocker: proposal
revisions embed one association shape while the standalone association schema
requires additional identity fields. Preserve the existing revision and input
digests and choose a versioned adapter before Packet B closes
(`P2-FREEZE-04-interface-freeze.md:52-54,85-97,123-131`).

### Run status reporting

The generic runner has useful per-goal state machinery: it records ready/running/
complete/failed/blocked sets, critical path, evidence, and terminal status
(`src/gigai/run.py:3283-3319,3589-3616`). The terminal reducer marks a generic run
failed if any Goal fails, blocked if any Goal is blocked, otherwise succeeded
(`src/gigai/run.py:3491-3496`), and dependent Goals are blocked when a predecessor
does not complete with an allowed outcome (`src/gigai/run.py:3524-3543`).

The presentation reader is not equivalent: for local `run-details`, it sets the
run `succeeded` if *any* Goal is complete, then only checks failed and running
states (`src/gigai/scout_report_readers.py:315-342`). A three-node run with
`acquire=complete`, `assess=failed`, or with `present` still pending can therefore
be misreported as succeeded. This is a direct source finding, not a grep-derived
absence.

**Proposed status mapping.** Keep the existing per-node statuses and show them
unchanged in the UI. Add a node-aware aggregate that is succeeded only when all
required nodes are terminal-complete; otherwise surface interruption/failure/
blocked/pending/running explicitly. The exact precedence between interrupted,
failed, blocked, cancelled, and active is an open contract decision, but it must
never be “any complete implies succeeded.” The present node should expose
`goal_sets`, `critical_path`, node evidence, producer, and usage refs rather than
flattening them into one report status.

### Interrupted run, new-run continuation, and acquisition dedup

**Existing behavior.** A generic worker exit causes `_mark_interrupted`; the Run
is marked `interrupted`, all Goals are marked failed with `worker_interrupted`,
existing artifacts are preserved, and the journal says no automatic retry was
performed (`src/gigai/run.py:657-721,3883-3945`). `read_run_details` can also
mark a preparing/running Run interrupted when the target observation changes
(`src/gigai/run.py:738-801`). There is no current generic run-level resume entry
point in this path. Proposal execution refuses a non-running or already
terminalized Goal (`src/gigai/scout_proposal_execution.py:629-695`), so it cannot
be called as an implicit retry.

Acquisition is stronger: a committed batch can be read/resumed; same input bytes
under the same batch ID replay the complete result or continue from the exact
processed prefix; identities already in the batch are classified as duplicates
(`src/gigai/scout_acquisition_records.py:467-557`). External recording separately
has operation-key replay, deterministic Run IDs, and idempotent plan/start
artifacts (`src/gigai/external_recording.py:781-849,1167-1285,1387-1432`). Those
external semantics do not automatically apply to the local graph runner.

**Smallest recommendation: new-run continuation first.** Keep an interrupted
Run immutable and create a new traversal that references the same acquisition
batch/input digest. The new `acquire` callable invokes the existing import with
the same batch ID and bytes, returning the already committed status or continuing
the committed prefix without duplicate records. The new assess/present callables
use deterministic operation keys over the new Run plus the sealed input refs and
replay or refuse byte-identical existing node artifacts; they do not overwrite the
old Run. This is smaller and more auditable than teaching the current interrupted
Run to mutate failed Goal details, but it is still proposed and requires an
explicit operation-key/receipt contract.

Same-Run resume remains an open alternative. If selected, the runner needs a
committed checkpoint per node, a rule for re-entering only incomplete nodes, and
proof that no prior terminal handoff can be replaced. The choice must cover the
`duplicate_posting_snapshot` case, partial acquisition, assessment failure after
successful acquisition, and interruption between each pair of nodes. No current
Run behavior should be described as satisfying Amendment 01's resume criterion.

## Open decisions

1. **Source mode and authorization.** Confirm supplied public-row JSON as the
   first proof source. If network fetching is required, approve the bounded
   Greenhouse watchlist source and its source/privacy/terms/effect boundary; do
   not infer authorization from S09 research (`S09-local-search-retrieval-capability-sourcing-research.md:15,62-71,177-190`).
2. **Continuation model.** Choose the recommended new Run with immutable prior
   Run versus same-Run resume with committed node checkpoints. Record the choice
   before implementation.
3. **Node receipt version.** Choose an additive run-details extension or a
   separate versioned journal receipt carrying inputs, outputs, evidence,
   producer, usage availability, operation key, and failure case.
4. **Graph binding.** Approve exact graph ID/version, goal slugs, callable
   identity/version, input/output contracts, and two automatic `COMPLETE` edges.
   No custom outcomes, branches, OR joins, operator gates, catalog, or generic
   builder are in scope.
5. **Executor/effect ceiling.** For the no-provider proof, retain local/offline
   execution and `write_workpad`. If a live source is later selected, define its
   capability/effect and direct operator consent explicitly; do not weaken the
   existing scheduler checks.
6. **Assessment input adapter.** Specify how an acquired posting selector maps to
   one proposal posting source and which local profile/experience/preferences are
   selected. Resolve embedded versus standalone answer association before the
   assessment packet is accepted.
7. **Assessment state mapping.** Freeze a versioned mapping between assessment
   `new/pending/running/succeeded/failed/skipped` and existing proposal/Run
   records. Keep public acquisition state independent.
8. **Aggregate status.** Decide precedence for interrupted, failed, blocked,
   cancelled, running, pending, and succeeded; require all required Goals to be
   complete before succeeded.
9. **Usage semantics.** Decide how unavailable usage is represented so a generic
   zero object cannot be mistaken for measured zero cost. Keep model/provider
   identity in the node producer receipt.

## Smallest coherent implementation packets

These packets are ordered only by real data dependencies. They do not expand the
proof into branching, catalog, reusable framework, or autonomous improvement.

| Packet / owner | Smallest deliverable | Dependencies and handoff |
| --- | --- | --- |
| **Integration: graph/runner seam** | Author one explicit three-node `find-jobs` proof graph; bind its three goal slugs to fixed packet callables; carry sealed node input/output contracts and additive node receipts; enforce the existing authority, digest, budget, executor, effect, and direct-consent checks; implement the selected continuation/idempotency policy. Replace placeholder execution only at these three bindings. | Requires source mode, graph/contract IDs, receipt shape, continuation choice, and A/B/C callable signatures. Owns shared `run.py`, materialization, and schema/journal seam; does not absorb packet behavior. Terra reviews after the trace and implementation packet. |
| **Packet A: acquire / acquisition owner** | Expose `import_public_rows`, `status`, and `resume` as the `acquire` callable for one supplied-row batch; return authenticated input/progress/failure/dedup refs; add the smallest independent acquisition reader DTO needed by present. No connector, scheduler, provider, or private matching. | Depends on the supplied-row source decision and Integration's node context/receipt. Produces the exact posting snapshot refs and progress/failure states consumed by B and C. |
| **Packet B: assess / assessment owner** | Adapt the existing local proposal host to one graph Goal; consume exact saved posting snapshot(s) plus explicitly selected local evidence; preserve local Ollama/offline/no-hosted-fallback checks; persist invocation/result/proposal refs, failure case, producer, and usage; implement the versioned answer-association choice. | Depends on A's input/output refs and Integration's callable/receipt/continuation contract. Must not own acquisition, provider authorization, tracking, or UI projection. |
| **Packet C: present / UI-tracking owner** | Extend the derived reader/projection/report to show acquisition rows and failures independently, assessment result/questions, per-node status/evidence/producer/usage, and corrected aggregate Run status. Keep journal/committed bytes authoritative and do not write application events. | Depends on A and B receipts plus Integration's status contract. It is the only packet that shapes the tracking UI view; it does not silently relabel assessment as application state. |

## Estimate (after tracing)

This estimate follows the source trace; it is not an implementation commitment:

| Area | Relative size | Reason |
| --- | --- | --- |
| Integration seam | **L** | Cross-cutting graph authoring, callable dispatch, receipt/schema extension, policy checks, and restart/idempotency touch the shared Run authority. |
| Packet A | **S/M** | Existing journal-authoritative import/status/resume are reusable; the independent acquisition reader and callable handoff are the remaining work for supplied rows. A live source would be a separately authorized expansion. |
| Packet B | **M** | Existing proposal host is substantial and reusable, but mapping saved postings, local evidence, answer association, invocation/revision refs, and node failure/replay requires an adapter. |
| Packet C | **M** | Existing projection/report/readers are reusable, but acquisition visibility, result/questions join, per-node receipt display, and status correction cross the current reader boundaries. |
| Coordinated proof | **M/L** | Four packets with Integration as the dependency hub; the proof is not a one-line graph declaration because each node needs a callable, persisted handoff, and restart semantics. |

No estimate above includes a provider connector, scheduler/watchlist, branching,
catalog, framework rebuild, human-gold model-quality study, or release work.

## Future acceptance: behavior, evaluation, and installed lanes

### Future behavior receipt

A future authorized proof should leave one sealed Run and receipts that show all
of the following:

1. The bounded source commits a posting/input and progress receipt before
   assessment. A source failure, deadline, or duplicate is visible independently.
2. `assess` consumes the exact saved posting snapshot/digest plus selected local
   evidence; it cannot read an unpinned latest posting or send private evidence to
   a provider.
3. The linear graph advances only on node `COMPLETE`; assessment domain labels
   remain in the artifact and do not select edges.
4. `present` displays the public row even when assessment fails, and displays
   assessment results/questions when available without creating an application
   event.
5. Per-node receipts expose status, explicit input/output/evidence refs,
   producer identity, and usage/cost availability. The aggregate Run is
   succeeded only when all required nodes complete.
6. Replaying the same acquisition batch/input bytes is idempotent; an interrupted
   continuation does not append duplicate `(opportunity_id, snapshot_id)` rows.
   The chosen same-run/new-run policy is visible in receipts.

### Future evaluation lane

The minimum deterministic contract/evaluation matrix should cover: normal
completion; acquisition failure; assessment failure after acquisition; model
stopped after acquisition; duplicate posting snapshot; interruption after each
node; same input replay; partial acquisition resume; and report rebuild from a
pinned journal snapshot. Each case should check bytes, digests, operation keys,
node status, producer, usage availability, and UI visibility.

S08/EVAL-03 remains preparation-complete and execution-not-authorized; it may
provide a later bounded pack/rubric but does not establish model quality or human
gold (`P2-FREEZE-04-interface-freeze.md:38-40,95-99`; `P2-FREEZE-04-interface-freeze.md:114-121`).
Any local-model quality comparison and human-gold receipt is a separate authorized
lane, not an implicit consequence of graph completion. S11 full `make test`
acceptance remains pending; no competing execution was run for this trace.

### Future installed and live lanes

Installed acceptance should use a clean installed artifact and exercise the
bounded CLI/read paths (`scout-acquisition import/status/resume`, graph `run`,
`run-details`, and report status/publish) against a disposable workpad. It must
verify the pinned graph/contract bytes, no hosted fallback, durable receipts,
correct failure/restart states, and report rebuild from committed records. An
installed invocation is not live/provider acceptance.

Live/provider acceptance, if later authorized, must separately identify the
provider/source, source authorization, request privacy boundary, observed source
response, storage terms, and failure/degradation behavior. S09's documented
capability and pricing research is not a live freshness, storage-rights, or
provider-execution receipt (`P2-FREEZE-04-interface-freeze.md:60-64,112-121`; `S09-local-search-retrieval-capability-sourcing-research.md:3-4,13,62-71`).
Release remains blocked by the existing exact-tag/publication/UAT/installed/live
gates; this trace makes no shipment claim.

## Non-claims and review handoff

- This file is an integration trace and proposal boundary, not an implementation
  receipt or Terra approval.
- No current path proves a real `acquire → assess → present` traversal. Existing
  import, proposal, external-recording, projection, and report paths are reused
  only where their authorities and contract differences are named.
- The current generic executor's placeholder evidence, current local report's
  “any complete means succeeded” rule, and the lack of a uniform node receipt are
  explicit blockers, not inferred absence claims.
- The pre-amendment Terra closure is not Amendment 01 approval. Terra's review is
  the next review gate after this trace; root owns the final decision.
- S11 full `make test`, installed, live, model/provider, human-gold, publication,
  and release evidence remain pending or separately authorized.
