# SCOUT-00 — Lifecycle contract amendments

> Documentation rename only: `SCOUT-*` refers to the same historical `JSL-*`
> delivery work. Findings, verdicts, test commands and measured results below
> retain their original scope; they do not review or accept the new
> [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md).

**Date:** 2026-09-07  
**Status:** Original lifecycle scope accepted after independent correction
review on 2026-09-07. The additional user-owned Gig workspace/tooling/SQLite/UI
scope was separately accepted in the operator-supplied independent re-review
on 2026-09-08; implementation still follows accepted dependencies.
**Source baseline:** `fda4857`; Wave A closeout changes are separately scoped.  
**Roadmap:** [Scout delivery goals](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md)

## 1. Authority and compatibility

Sections 1–9 below preserve the previously reviewed lifecycle design under
the Scout documentation name. The separate
[user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md) identifies
changes to initialization/naming, workspace organization, storage ownership,
local supporting tools, HTML reporting, and explicit handoff acceptance.
Where these differ, the separately accepted replacement contract governs;
do not implement a guessed combination. In particular, the old `jsl`
examples below are historical proposed interfaces, not a second current
product name or shipped command-compatibility promise.

**2026-09-08 replacement decisions, separately accepted:** the updated amendment
supersedes workspace-only bare init and named-template initialization with
username-aware `gigai init` for all bundled defaults. It freezes journal-backed
CRUD with Scout projections in existing `state.sqlite`, a v2 workspace path
map, inventoried Gig-owned tools using validated local persistence, and separate
UI source/generated output. The B3 single-instance recovery primitive below
is retained inside the new pinned default-inventory batch; its command and
owner assumptions are replaced. Their acceptance comes from the separate
workspace re-review, not the historical review of this file. See the
[finding response and verdict](SCOUT-00-workspace-review-response.md).

Retain `Gig version -> immutable Graph Set -> selected graph -> sealed Run
Plan -> Run -> journaled evidence`. A graph is a reusable contract, not a
hard-coded job-search state machine. One Run selects one graph. A suggested
next graph never starts a Run. User records may change between Runs; approved
graph definitions and historical inputs may not.

G43.2's Graph Descriptor `graph_id` is a semantic selector such as
`research-role`. It is NOT the UUID-valued `graph_id` in the existing Goal
Graph schema. Preserve both namespaces: the descriptor references the complete
Goal Graph artifact; selection pins descriptor selector, Goal Graph UUID,
Graph Set digest, and approved Gig version. Do not rename existing UUIDs or
silently reinterpret old fields. Alias normalization occurs before sealing.

The five canonical selectors are `research-role`, `find-jobs`,
`tailor-application`, `record-application`, and `prepare-interview`.
Missing selection in a multi-graph Gig returns `graph_selection_required`
with safe available descriptors. No default based on ambient conversation.
G43.2's `operator_explicit` must not be emitted for an agent invocation:
add `agent_explicit` with an agent actor and exact invocation reference; it
records an explicit selector, not a claim of operator consent. Only-member
selection and later reviewed G44 routing retain their distinct provenance.

Use additive schema versions with distinct URNs/files for changed strict
contracts. Old serializers/readers, approval tags, journal commits, Plan
identity projections, and Run evidence remain valid unchanged. A virtual
single-member view of v1 authority is inspection-only. New writers must not
write a fabricated v2 Graph Set into an old approval or rewrite v1 bytes.
Extend central deterministic identity derivation, not per-feature UUID code.

Reject mismatched graph/Gig/Plan identity and altered, missing, foreign,
symlinked or traversal-bearing references BEFORE allocating a Run ID. Every
new record has a bounded strict schema and semantic validation. A shaped file
alone is not journal authority. Rebuild projections from validated transitions;
do not use a mutable index as proof that a Run/approval happened.

## 2. Named initialization and package ownership

`gigai init [template]` accepts `job-search-lifecycle` and alias `jsl`.
Bare init keeps its workspace initialization behavior. Named init is offline,
idempotent setup/instance creation; it neither researches nor invokes a model.
If runtime configuration is absent, return actionable setup requirements
before partial instance creation. Do not probe provider availability merely
to store an external-agent template.

Amend G41/G42's one-package-per-project restriction to a set of independently
validated packages keyed by canonical package ID. Keep `.gigai/packages/`
portable and the project binding unchanged. Bare init with existing packages
does not select an arbitrary first package: report the set; operations needing
one package require an explicit selector when ambiguous. Legacy one-package
responses remain supported for that case. Existing empty packages are kept.
Installation and adoption validate the selected package, not a global count
of exactly one. Validation must still reject unsafe or malformed sibling paths
instead of treating multiplicity as a bypass.

Named init resolves canonical catalog ID/version/digest and materializes an
immutable portable source package. A private instance binding maps
`(project_id, canonical_template_id, instance_name=default)` to one private
`gig_id`, original package digest, current approved Gig version (if any), and
customization lineage. This binding belongs in the private registry/workpad,
not in exported catalog content. Repeating either alias returns the same Gig
and current state without resetting records, creating another proposal, or
changing the selected active Gig as an undocumented side effect.

First named init prepares the instantiated proposal with locally allocated
Gig/Goal Graph identities and explicit effects. Existing approval remains a
separate direct operator action. Return `approval_required`, the proposal
reference, and exact next command. Reopen returns pending/approved state.
Template provenance is not user approval. Multiple default-instance creation
attempts serialize under the existing target-init/registry locking pattern;
interruption recovery must reconcile package, workpad, and binding without
allocating duplicates. Additional named instances are not required for 0.1.7.

Do not map Scout catalog authoring JSON straight into runtime Goal Graph schema:
the existing catalog's `definition/goal-graph.json` is authoring material, not
an approved executable graph. Instantiation must validate the produced Graph
Set, all members, and referenced contracts before proposal publication.

## 3. Private data and progressive preferences

Use each registered private Gig workpad as the authority root. Portable
packages contain no resume, user preferences, application history, Runs, or
conversation records. Logical private layout, resolved only through the
existing registry, is:

```text
records/<record-id>/revisions/<revision-id>.json
records/<record-id>/blobs/<content-digest>.<declared-extension>
runs/<run-id>/...                     exact Run snapshots and submissions
indexes/context.json                  rebuildable metadata projection
```

Filenames derive from validated IDs/digests, never user titles or URLs.
Blob extensions/media types are allowlisted. Reuse workpad journal atomic
publication and project/Gig scoping, not a parallel unjournaled record store.
Private records carry kind, stable ID, revision ID, parent revision, content
digest, origin, timestamps, actor, and explicit relationships. Reject a stale
parent update (compare-and-swap); do not silently overwrite a concurrent edit.
Repeating an operation ID with identical payload is idempotent; different
payload with the same ID is a conflict. Original imported bytes remain intact.

Kinds cover profile/preference, experience answer, reference, source, role
research, job snapshot, shortlist, application document, check result,
application event, interview preparation, and selected conversation material.
Domain output contracts refine these kinds; avoid one unconstrained JSON bag.

Question state distinguishes `missing`, `answered`, `declined`, and
`not_applicable`; whether an input is optional comes from its contract, not
from a fabricated answer. Preferences distinguish hard constraints and soft
priorities. Answers record provenance as user-reported, imported, or inferred;
inference is not promoted to user-confirmed fact. Sponsorship need, employer
sponsorship evidence, and eligibility are separate facts; unknown stays unknown.

Updates explicitly choose `saved_default` or `run_override`. A Run resolves
selected defaults to exact revisions, copies/seals needed content, and retains
that selection even if the user later edits the default. A new answer during
an in-progress Run is a checkpoint dependency, never a mutation to sealed
starting inputs: completing with changed required inputs requires a successor
Plan/Run linked to the prior checkpoint. Resume the unchanged Plan when only
partial output changed. Superseded work remains inspectable, not retroactively
successful. This preserves dynamic iteration without weakening sealing.

Context inspection defaults to scoped metadata: IDs, kind, version, safe
summary, location, outstanding questions, and current Runs. Exact selected
record reads are explicit operations. No automatic all-record payload, cross-
Gig search, provider disclosure, or ambient agent-session harvesting.

Conversation import is a separate bounded operation for explicitly supplied
UTF-8 messages, Q&A, summary, or session reference. It labels exact excerpt vs
summary and declared source/selection. A claimed session reference is not proof
of complete provenance. No hidden reasoning or unrelated session collection.
Keep v1 invocation's recursive forbidden-key behavior unchanged; introduce a
versioned selected-record input boundary, not a global removal of protections.

## 4. External-agent execution versus managed execution

Add an explicit `external_agent_recording` execution mode with a dedicated
sealed Plan contract. Existing G43 provider-review Plans remain managed Plans,
including their configured targets, consent, eligibility, phases, budgets,
cost accounting, and launch checks. Do not shoehorn external work into a fake
provider invocation with zero usage or into a local-capability executor that
claims to have performed the semantic work.

The external Plan seals the approved graph, selected inputs, required outputs,
checks, actor/invocation, and effects limited to private-workpad recording.
It grants no network, provider, local arbitrary-code, target-repository, or
application-submission authority. An explicit agent invocation may request
these local recording operations under an approved Gig; it records actor kind
`agent`, never `direct_cli_confirm`. This is an explicit amendment to G43.2's
universal direct-confirmed-Run wording for RECORDING ONLY. Direct confirmation
remains required for Gig approval and managed provider execution. Agent tools
remain governed by their own user's permissions, outside GigAI's observation.

Expose the `gigai external` CLI/JSON family specified in section 9 with discovery,
inspect-required-inputs, plan, start, checkpoint, submit, and inspect operations.
All return typed next actions and stable IDs. `start` validates a sealed Plan
and allocates a Run; `checkpoint` records bounded partial outputs/questions;
`submit` validates exact outputs and checks, then journals completion or a
blocked/invalid result. Required missing outputs cannot succeed. Terminal
submission is immutable. Retry with the same key/digest returns the same
receipt; changed payload conflicts. Cancellation/interruption preserves work.

Submission records what the external agent declares separately from what
GigAI validates: supplying actor/model names is self-report, not attestation.
External tool calls/cost are `unobserved` unless separately reported and then
still `reported`, never fully accounted. Supplying sources does not mean
GigAI fetched, verified, or independently corroborated them. Never import an
external response as a successful managed model invocation/verification.

The CLI must not resolve a URL, open a session locator, or execute supplied
source code on inspect/import/submit. Bounded UTF-8 Markdown/text and JSON
inputs suffice for 0.1.7. Unsupported binary resume formats return actionable
conversion guidance; no implicit renderer/network/parser dependency.

## 5. Domain outputs, questions, and checks

Each output has Markdown for user reading and a strict JSON sidecar for
sources, relationships, selected input revisions, checks, and unresolved
questions. JSON must reference the exact document bytes. Free prose is data,
not executable instruction. Formatting checks do not establish truth.

| Graph | Required minimum output | Questions / evidence constraints |
|---|---|---|
| research-role | Role summary, responsibilities, variations, compensation section, source list, uncertainties | Role required; compensation context includes geography, currency, date, base/total/range/period and source limitations; missing salary evidence is explicit, never guessed |
| find-jobs | Dated job snapshots and shortlist with fit reasons and gaps | Saved preferences selected explicitly; material missing constraints are asked; no claim of completeness or active vacancy without dated evidence |
| tailor-application | Exactly requested resume and/or cover letter, requirement-to-evidence matrix, gaps, check report | Posting and candidate evidence required; ask about unsupported experience; no fabricated employment, skills, metrics, sponsorship, or harness-engineering claims |
| record-application | Append-only selected-opportunity event with explicit user request evidence | Finalized document is not applied; action/date/document revisions recorded separately; no external application request |
| prepare-interview | Stage-specific prep, relevant source/experience links, practice questions and unresolved gaps | Role-only prep allowed; job-specific prep selects posting; feedback is not evidence of employment experience |

Source records minimally contain supplied URL/locator, title, publisher if
known, observed/retrieved date if known, published date if known, source kind,
claim references, and status `reported`, `captured`, or `independently_verified`.
`captured` requires exact supplied content digest. Independent verification
requires a separate verification record with method/evidence/actor, not the
author's assertion or existence of a URL. Unknown timestamps remain null;
local import time is not represented as retrieval time. Salary and sponsorship
claims point to exact source records and indicate uncertainty/conflicts.

ATS-oriented checks are advisory, inspectable rules: readable plain-text
structure and section headings; required contact-field presence without
echoing values; declared output-length bounds; posting requirement coverage
against explicitly supplied candidate evidence; unsupported claim flags;
obvious placeholder text; and missing job/company specificity in cover letters.
Use rule/version, inputs/digests, findings, severity, and evidence references.
Lexical matches are `term_present`, not proof of competence. Semantic checks
are agent-reported unless independently reviewed. No numeric employer ATS
score, pass guarantee, or opaque keyword-stuffing recommendation. A newer draft
gets a newer check report; an old report cannot certify changed document bytes.

## 6. Application events

Use an append-only event journal, not a mandatory ordered funnel. Supported
facts are `saved`, `applied`, `interview_scheduled`, `offer_received`,
`rejected`, and `withdrawn`, plus a correction that explicitly supersedes an
event. A late-arriving event may predate another; retain occurred-at and
recorded-at separately. Rebuild current status deterministically using
non-superseded event ordering and show history, not just a mutable status field.
Define tie-breaking by journal sequence; do not guess dates from relative text
without saving the resolved timezone/date and user context.

An agent may record the user's explicit instruction such as 'mark it applied'
with an explicitly selected conversation/request reference labeled
`agent_reported_user_request`. It is not cryptographic proof or direct CLI
consent. If no request evidence exists, return a question and leave status
unchanged. A suggestion, resume completion, check pass, or generated cover
letter is never request evidence. Idempotence prevents duplicate applied events.

## 7. Supporting code, customization, and portability

Amend G41's blanket executable-content exclusion narrowly: versioned packages
may inventory supporting source as inert, non-executable files with safe paths,
media type, digest, declared purpose, input/output contract and effects.
Package inspection, init, export, install, and restore never run it. No hooks,
arbitrary shell commands, embedded credentials, or auto dependency installs.
Invocation requires an explicitly registered local capability and a separately
authorized effects path; external agents may edit source but GigAI does not
certify or automatically execute the edits. Shipped deterministic check logic
should use existing local-capability integration where appropriate.

Customization creates a new proposal/Gig version with parent/template lineage;
it never edits an approved snapshot. Template updates produce a digest-based
comparison and explicit adopt/defer choice. Divergent local modifications
block automatic replacement. Defer changes nothing. Adoption creates a new
proposal preserving private records, selected historical inputs, and prior
versions. No in-place merge guess or rollback by overwriting history.

Template export is inventory-only and rejects unlisted/private files. Explicit
private backup is a DIFFERENT command/format with a manifest, checksums,
selected Gig scope, disclosure notice, and no credentials or global provider
configuration. Restore verifies the complete archive before publication,
rejects traversal/absolute paths/symlinks/hard links, refuses nonempty or nested
unsafe destinations, and reconciles project/Gig registry identity explicitly.
Imported historical authority stays historical: no imported active pointer,
operator receipt, or provider target becomes fresh execution authorization.
Cross-machine new execution requires local binding/configuration and approval;
history remains readable with original identities and detached provenance.

## 8. Caller ownership and acceptance matrix

Current seams verified locally: `package.initialize_project_package` /
`_initialize_and_prepare`, `catalog.materialize_catalog_package`,
`lifecycle.approve_offline`, `run._resolve_authority` /
`_validate_plan_handoff`, `run_plan._identity_projection`, strict
`active-gig-version.schema.json`, `validators.validate_goal_graph`,
`invocation.parse_invocation`, and `workpad.resolve_workpad`. The independent
caller audit adds exact downstream projection/reporting/portability callers.
Workers must update all affected schema dispatch and packaged inventory checks,
not merely the CLI happy path. Shared schemas/CLI have one writer per wave.

| Acceptance ID | Goal(s) | Required evidence |
|---|---|---|
| A01 | 01 | Existing public clean review or correction/re-review, separate supported no-fix receipt; fake tests are not live evidence |
| A02 | 02 | Two selectable graphs, aliases, invalid/missing selection, changed version, unchanged v1 readers, tamper rejection before Run allocation |
| A03 | 03 | Saved vs task-only preference, stale-parent conflict, prior inputs unchanged, fresh-session metadata recovery, no cross-Gig/private leak |
| A04 | 04 | Offline adapter fixtures plus installed Codex and Claude external recording; interrupted checkpoint, replay conflict, missing output refusal, honest provenance |
| A05 | 05 | Installed wheel fresh init, bare-init-then-jsl, unrelated-package preservation, repeat alias, pending approval, recovery after partial failure |
| A06 | 06 | Synthetic FDE research without resume; source gaps and salary uncertainty; user-authorized agent research with saved evidence separately labeled |
| A07 | 07 | Search preference changes, unknown sponsorship, stale/duplicate jobs, shortage honestly reported; no invented live jobs |
| A08 | 08 | Resume-only, cover-letter-only, both; unsupported harness-engineering claim triggers question; revised draft invalidates old checks |
| A09 | 09 | No auto-applied; explicit request event; replay/correction/out-of-order histories; no outbound application effects |
| A10 | 10 | Role-only and opportunity-specific prep; selected earlier research reused, no unrelated records disclosed |
| A11 | 11 | Template privacy sentinel, explicit private round trip, malicious archive rejection, customization adopt/defer, no imported consent escalation |
| A12 | 12 | Full suite, schema inventory, clean-room wheel/CLI, independent review, agent continuity and human UAT; publish remains separately authorized |

Each evidence report states exact command, input class (fixture/replay/live),
result, source revision/diff, artifact references, and what was not tested.
No pass based solely on worker completion. Runtime goal activation waits for
its accepted dependencies; changing that order requires a recorded decision.

## 9. Frozen implementation supplements (independent review B1–B4, M1)

These requirements resolve the first independent review's gaps. They are
normative refinements of sections 2–6, not an alternative architecture. The
[caller audit](SCOUT-00-caller-audit.md) is source evidence and advisory options;
where it recommends retaining the one-package restriction or universal direct
confirmation for external recording, the explicitly amended decisions above
take precedence after review acceptance.

### B1: External protocol, identities, and state

Implement these strict schema resources with schema version `1.0` and URNs
`urn:gigai:schema:<name>:1`. Every object rejects unknown fields; all artifact
references use the common digest/size/media/path shape. Invocation `origin`
is a required discriminator: `agent_invocation` uses a strict agent actor
(`kind=agent`, bounded known agent ID and opaque session ID); `direct_cli`
uses a strict actor (`kind=operator`, `id=local-user`, no session/model fields).
The CLI adapter constructs the latter only from its direct command path, not
from an agent-supplied envelope. Origin is provenance, not proof of identity,
target configuration, or managed-provider consent. It does not waive the
separate `--confirm` for approval, provider Runs, or direct application events.

| Schema name | Required fields beyond schema version |
|---|---|
| external-recording-invocation | invocation_id, operation, project_id, gig_id, origin, actor, input, operation_key, payload_sha256, created_at |
| external-recording-plan | run_plan_id, mode, project_id, gig_id, gig_version, journal_commit, graph_set, selected_graph_id, goal_graph_id, selected_graph, selection_record, invocation, inputs, output_contract, check_contract, effects, limits, predecessor, state, created_at, sealed_at |
| external-recording-run | run_id, run_plan, invocation, mode, project_id, gig_id, gig_version, status, started_at |
| external-recording-checkpoint | checkpoint_id, run_id, run_plan, invocation, sequence, parent_checkpoint, questions, artifacts, reason, created_at |
| external-recording-receipt | receipt_id, run_id, run_plan, invocation, operation_key, payload_sha256, outcome, outputs, checks, disclosure, created_at |

`mode` is exactly `external_agent_recording`; `effects` is exactly
`["write_workpad"]`; Plan state is exactly `sealed`. IDs reuse canonical
`run_plan_<uuidv4>` and `run_<uuidv4>` namespaces. New checkpoint/receipt/
operation IDs use centrally registered canonical UUID prefixes; no feature-
local derivation. The selected graph reference points to the Goal Graph;
the selection record pins the complete Descriptor by Graph Set digest and
semantic selector. `goal_graph_id` must equal that Goal Graph's UUID.

Plan identity derives centrally from schema/mode, project/Gig/version/approval
commit, Graph Set/Goal Graph/selection digests and selector, ordered selected
input record/revision/blob references and digests, output/check contract
digests, effects, limits, predecessor reference, and normalized invocation
payload digest. Timestamps and session-local invocation allocation IDs are
excluded so equivalent plan requests resolve the same Plan. Store the exact
invocation as sealed evidence; replay returns the original Plan and its
original invocation reference rather than overwriting it with a later one.
The same operation key and normalized payload reuse the original selection
and invocation. With a different operation key, the caller must explicitly
reuse that sealed selection or obtain a new selection and Plan; a newly
allocated session-specific selection must not claim an older Plan identity.
`payload_sha256` hashes operation + project/Gig scope + origin + normalized
actor identity (`{kind,id}`, session excluded) +
strict typed inputs, excluding timestamps, session ID, operation key and
invocation ID. Raw caller bytes never become an identity without validation.

`inputs` contains exact revision and content references, with a source-family
discriminator from B2. `predecessor` is null or the exact same-Gig prior
Run/Plan/checkpoint reference. A successor is explicit, not an implicit clone
of every artifact. `limits` fixes max envelope bytes 262144, max artifact
bytes 1048576, max artifacts per operation 32, max total bytes per operation
4194304, max checkpoint questions 32, and max checkpoints per Run 256.
These are recording bounds, not claims about external execution budgets.
Job-description imports retain the stricter 262144-byte G45 limit. There is
no hidden unlimited attachment, unbounded recursive JSON, or URL dereference.

The `external` namespace has `graphs`, `requirements`, `plan`, `start`,
`checkpoint`, `submit`, `cancel`, and `inspect`. Read-only operations take
`--gig`, explicit `--graph` or `--run` as applicable, optional `--target` /
`--home`, and `--json`. Writers require `--invocation PATH` (or explicit stdin)
containing the corresponding strict operation input. The entry adapter may
construct an envelope from a direct user's explicitly supplied CLI input;
it must label that origin and never infer an agent is the user. Reuse a shared
validated operation service; no weaker direct CLI bypass around JSON validation.

| Operation | Strict input fields | State / result |
|---|---|---|
| plan | graph_selector, gig_version (nullable), selection_record (nullable), input_refs, output_kinds, predecessor (nullable) | Validates approved authority and required inputs; explicitly reuses the supplied valid sealed selection or creates a new one; seals Plan or returns typed missing questions; no Run allocation |
| start | run_plan_id | Revalidates every sealed source; records one active Run; no provider call |
| checkpoint | run_id, parent_checkpoint (nullable), questions, artifact_refs, reason | Appends next sequence under lock; required question sets `waiting_input`; otherwise preserves `active` |
| submit | run_id, parent_checkpoint (nullable), output_refs, check_refs, disclosure | Validates pinned contracts and all current content; journals terminal `succeeded` only when requirements pass |
| cancel | run_id, reason | Journals terminal `cancelled`; retains all prior evidence |

Run projection statuses are `active`, `waiting_input`, `succeeded`,
`cancelled`, `interrupted`; there is no semantic `succeeded` merely because
an agent process exited. Checkpoint states live in journal events, not by
rewriting the initial Run manifest. Invalid/incomplete submit returns
`external_output_invalid` / `external_output_missing`, records no successful
receipt, and leaves progress available for correction. A required-input
checkpoint can resume the same Run only if sealed inputs are unchanged;
otherwise return `external_successor_required` plus pinned predecessor and
missing-input metadata. Explicitly cancel/supersede the old active work when
the user chooses; do not silently terminalize it upon successor creation.

Transitions are `external_recording_plan_sealed`,
`external_recording_started`, `external_recording_checkpointed`,
`external_recording_waiting_input`, `external_recording_succeeded`,
`external_recording_cancelled`, and `external_recording_interrupted`.
All use the common workpad writer lock and journal continuity validation.
Operation uniqueness is `(project_id, gig_id, operation, operation_key)`.
Check receipt existence and payload equality INSIDE the publication lock;
identical retry returns the receipt/Run, conflicting retry returns
`external_operation_conflict`. Terminal Runs refuse new checkpoints or changed
submissions with `external_run_terminal`. An interruption between artifact
publication and journal commit requires the existing explicit journal recovery
path before further writes; it is not an invitation to overwrite evidence.

Additional stable codes: `external_invocation_invalid`,
`external_authority_mismatch`, `external_input_mismatch`,
`external_record_not_found`, `external_limit_exceeded`,
`external_scope_refused`, `external_checkpoint_conflict`,
`external_reconciliation_required`. Error JSON contains code, safe message,
and typed next action, never raw supplied content or absolute source paths.
Both managed and external CLI readers discriminate by schema/mode; each writer
refuses the other's Plan/receipt family. Provider targets, managed invocation
IDs, direct_cli_confirm claims, network/capability grants, and arbitrary code
execution fields are invalid in this protocol. Selected source metadata is
data and cannot create a grant.

### B2: G45 bridge and single content authority

Keep G45's `private-reference:1` / `run-input-record:1` immutable family and
exact `references/ref_.../source.txt` / `run-inputs/input_.../source.txt` paths
as the canonical imported resume/evidence/posting content store. G45 is still
a proposed contract at the inspected baseline; this compatibility bridge is
required for its implementation and fixtures, not a claim that it already ran.
The generic Scout revision record links those immutable records; it does not
duplicate their blob or assign a second mutable content truth.

Scout owns strict `private-record-revision:1` with fields `record_id`,
`revision_id`, `parent_revision`, `project_id`, `gig_id`, `kind`,
`privacy_class`, `origin`, `actor`, `content`, `relationships`, `created_at`,
and schema version. `privacy_class` is `private_sensitive`. `content` is a
discriminated exact reference: `g45_reference` + reference ID/record ref /
snapshot ref; `g45_run_input` + input ID/record ref/snapshot ref; or `jsl_blob`
+ exact blob ref for native records only. Kind-specific structured sidecars
remain required. G45 records are imported unchanged, not relabelled as mutable
Scout blobs; wrappers are lineage/context metadata with immutable content refs.

New G45 imports preserve G45's `(project, kind, privacy, media, digest)`
idempotence. The Scout record wrapper binds a registered Gig, and cross-Gig
selection is refused unless a future explicit sharing operation is approved.
Plan input discriminator accepts exact G45 IDs with record/snapshot digests or
exact Scout record/revision IDs whose chain resolves to canonical content.
Normalize to ONE resolved input ref list before sealing; duplicate wrappers
must not multiply the same content or override an explicit revision.
Historical sealed G45 inputs remain readable and byte-identical. Test an
older Plan against a newer wrapper revision and altered source bytes: only
the exact old sealed snapshot remains eligible, otherwise refuse.

Explicit `record read --id ... --revision ... --content` may return the selected
private bytes to the invoking agent as requested. This amends G45's metadata-
only JSON inspection restriction ONLY for an explicitly named content-read
operation in the private context. List/status/public diagnostics/export remain
metadata-only/redacted. The recorded read is not permission for GigAI to send
the material to a provider, and no command reads all records by default.

### B3: Registry v3 and recoverable instance creation

Advance registry v2 to v3 using the existing application ID, schema validation,
exclusive migration lock, backup-before-write, and transaction/rollback
patterns. Preserve existing projects/workpads/active_workpads rows exactly.
The backup is `registry.sqlite.v2.bak`; do not overwrite an existing backup
with different bytes. Unknown versions or unexpected tables refuse. Read-only
commands do not migrate; named init exposes/executes the local migration as an
explicit setup step in its result. Old binaries may refuse v3; do not claim
backward writer compatibility. Old data/authority compatibility is mandatory.

Add `template_instances` with primary key
`(project_id, template_id, instance_name)`, `instance_name` constrained to
`default`, unique `gig_id`, and foreign key `(project_id,gig_id)` to workpads.
Columns also include package_id, original_package_digest, binding_artifact_ref,
binding_sha256, and journal_commit. Authority is the strict workpad
`template-instance-binding:1` artifact, journaled by `template_instance_bound`,
with those scope/provenance fields, proposal reference, nullable approved-version
reference, and customization parent reference. Current approval/customization
is projected from later journal events, never trusted from stale registry data.
The registry row is a validated locator/cache of the journaled binding, not
standalone approval. Rebuild/refuse divergence explicitly.

Before mutation, validate configuration, project binding, complete sibling
package inventories, built-in template and intended destination. Acquire the
target-init lock (add an equivalent private lock for non-Git targets) before
reserving the unique instance key; use one documented lock order:
target-init -> registry transaction -> workpad journal. Do not hold registry
locks while waiting for a provider or user input. Named init never changes an
existing active Gig. With no active Gig, return explicit Scout selection in the
result without manufacturing an active pointer; callers use returned --gig.

Persist a bounded private initialization intent under `.gigai/local/` before
cross-store publication. It pins transaction ID, project/template/default key,
package digest, reserved Gig ID, validated relative destinations, and stage.
Stages: `prepared`, `package_published`, `workpad_published`, `bound`.
Package publication is atomic from a validated sibling staging directory;
matching existing bytes are reused, differing bytes refuse. Provision the
reserved workpad/proposal and journal its binding next; commit the registry
mapping last. Mark intent bound only after all digests/identities reconcile.

On repeated named init, the matching intent resumes these exact identities;
it must not allocate a new Gig or discard a published workpad. Uncommitted
workpad journal state requires explicit reconciliation first. A different
intent/digest at the same key returns `template_instance_conflict`. Missing
intent with a matching valid journaled binding may reconstruct only that
locator after full validation; ambiguous candidates refuse. Do not delete an
unrelated package, backup, workpad, or active selection during recovery.
Inject failure at every publication stage and prove idempotent recovery and
unchanged unrelated bytes. Provide typed `template_reconciliation_required`
when recovery cannot prove ownership. Existing G41 adoption consent still
applies when adopting tracked user-supplied packages; a shipped builtin source
is not an excuse to adopt unrelated tracked material.

### B4: Application event schema and evidence binding

`application-event:1` is strict and append-only. Required fields: event_id,
project_id, gig_id, opportunity_ref, event_kind, occurred_at, timezone,
recorded_at, document_refs, notes (bounded), supersedes (nullable),
request_evidence, requested_event_sha256, operation_key, payload_sha256, actor,
and schema version.
Refs pin record/revision/digest; all must belong to the same Gig/opportunity.
`supersedes` can name only an earlier same-opportunity event and cannot form a
cycle. The corrected event contains the replacement fact; status folds
non-superseded events by occurred_at then journal sequence. Reject ambiguous
or missing required date resolution before publication, leaving history intact.

`request_evidence` is a strict union:

- `selected_user_request`: exact private conversation/request record and
  revision/content digest, selection actor, source label
  `agent_reported_user_request`, selected message index, and a normalized
  requested-event digest. The selected record must contain a
  user-attributed message, the normalized target opportunity/event/date/
  documents being requested, and a digest over those fields equal to the event's
  `requested_event_sha256`. For a correction, the requested normalization also
  includes the exact superseded event; notes supplied as part of the request
  are included. Normalization excludes generated IDs and recording time.
- `direct_event_command`: journaled direct event command receipt containing
  the exact same scope and requested-event digest, actor `operator`, and timestamp.
  This may be written only by `gigai application record --gig ... --input PATH
  --confirm`, never consumed from the agent envelope as an operator identity.

`requested_event_sha256` hashes canonical JSON of project/Gig/opportunity,
event kind, occurred-at/timezone, ordered document refs, requested notes (null
when absent), and supersedes (null when absent). `payload_sha256` is a DIFFERENT
operation hash: canonical JSON of every semantic event field including all
notes, supersedes, actor, request_evidence and requested_event_sha256; exclude
only event_id, operation_key, payload_sha256 itself, and recorded_at. Thus a
changed note, correction target or evidence ref conflicts under the same
operation key even when the narrow user-request content is unchanged.
Publication recomputes both hashes; neither is trusted merely because supplied.

Both forms require exact-byte validation and committed provenance. Bare agent
assertion, a generated draft, check result, URL, source record, or unselected
message is rejected with `application_request_evidence_required` or
`application_request_mismatch`. The normalized message interpretation remains
agent-reported, not cryptographically authenticated user intent. Display it as
such; do not claim parsing guarantees the semantic truth of an external chat.

`record-application` submit atomically publishes its receipt and validated event
under the journal lock. Other graph submissions cannot publish application
events. `application record` uses the same validator/publication service.
The event itself cannot authorize an external job application, provider call,
approval, or a second Run. Add fixtures for missing/foreign/tampered/mismatched
request references, duplicate retries, correction chains, and ordinary resume
completion: every rejection leaves current status and event history unchanged.

### M1: Question-driven successor acceptance

A03/A04/A08 additionally require: start tailoring from exact candidate/posting
revisions; checkpoint an unsupported harness-engineering question; save a
user-reported answer; inspect the old Run as `waiting_input`; return
`external_successor_required`; explicitly seal/start a successor using the new
answer and selected old checkpoint artifacts; retain old input bytes and
check results unchanged. The typed next action exposes only selected input
metadata. A fixture tests this chain; fresh-session Codex/Claude UAT must
exercise it without depending on the previous agent's ambient memory.
