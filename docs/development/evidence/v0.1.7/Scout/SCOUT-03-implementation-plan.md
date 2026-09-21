# SCOUT-03 — Private records and resumable context

**Date:** 2026-09-08  
**Status:** Changes requested after independent review; verification finishing;
[bounded corrections](SCOUT-03-correction-plan.md) queued; not accepted.  
**Authorization:** Operator: “Go on..” after SCOUT-02 acceptance and the
SCOUT-03 next-goal explanation.  
**Owners:** Astra coordinates; Terra implements; Claude independently reviews;
Luna audits callers and independently verifies the finished implementation.

## Outcome and authority

An agent can explicitly import private source material, save or revise a
candidate's preferences and experience, recover useful context in a fresh
session, and select exact revisions for later work. CRUD appends journaled
records. SQLite and the context index are rebuildable views, not alternative
authority. Old Run inputs remain reproducible after updates or archival.

This implements the accepted [SCOUT-00 lifecycle contract](SCOUT-00-contract-amendments.md),
especially section 3, B2, A03 and the input-selection part of M1, plus the
[workspace amendment](SCOUT-00-user-owned-gig-amendment.md), especially sections
2, 4 and 7. [G45](../../../v0.1.7/goals/G45-private-references-and-pasted-run-inputs.md)
defines imported text limits, canonical paths and diagnostics; B2 expressly
amends its explicit selected-content read boundary. The workspace amendment
takes precedence for native document paths and the single database.
[SCOUT-02 completion](SCOUT-02-completion.md) is the accepted prerequisite.

Read these contracts completely before implementation. Read the new
[caller audit](SCOUT-03-caller-audit.md) once available; it supplements, not
replaces, the accepted contracts. Resolve contradictions with the coordinator
before weakening a requirement or silently adding another authority.

## Implementation order

### 1. Layout and database compatibility

- Add strict, journal-authenticated `workpad-layout:2` identity and exact v2
  ignore policy. Preserve unchanged v1 behavior when the marker is absent;
  reject forged/unknown markers before admitting additional roots.
- Support explicit, recoverable v1-to-v2 migration with identity, clean-authority
  and collision checks. Preserve tracked legacy paths and dirty ignored drafts.
  No automatic migration of existing user workpads. New-instance v2 support
  must be ready for SCOUT-05; do not implement its general-init/default batch.
- Honor the complete ownership map: `docs/` for native document bytes,
  canonical G45 paths for imports, ignored software authoring copies,
  immutable inventoried snapshots under `manifests/`, bounded report/index
  subtrees, and no second workpad tree or second database.
- Coordinate `index._write_projection`, `read_index`, all G22 trace writers
  and Scout projection writes through a common database lock with documented
  ordering relative to the journal writer lock. Preserve `interview_events`;
  refuse unsupported/malformed state instead of silently discarding it.

### 2. Immutable imports and typed revision storage

- Implement G45 reference and Run-input records at the specified
  `references/ref_.../` and `run-inputs/input_.../` paths. Explicit bounded
  local UTF-8 text/Markdown only; read limit + 1 bytes, preserve exact bytes,
  validate parent/file safety, and use owner-only private storage.
- Expose the documented `reference add/list/show` and `run-input add/show`
  commands, bound to the registered project and selected Gig. Import does
  not approve a Gig or Run, call providers, or fetch anything.
- Implement strict `private-record-revision:1` wrappers with project/Gig,
  record/revision/parent identity, kind, private-sensitive classification,
  origin, actor, exact content reference and typed relationships. B2's
  discriminated G45 reference/Run-input/native-content forms must preserve
  ONE canonical content authority; do not duplicate imported bytes.
- Provide closed kind-specific content/sidecar validation for the SCOUT-03
  record families: profile/preferences, experience Q&A, imported references,
  supplied sources/artifacts and selected conversation excerpts/summaries.
  Later domain outputs remain subject to their own consumer contracts, not
  an unconstrained JSON escape hatch.
- Preserve explicit missing/answered/declined/not-applicable question states,
  hard constraints versus soft priorities, and user-reported/imported/inferred
  provenance. Sponsorship need, employer evidence and eligibility are separate
  potentially unknown facts. Neither inferred facts nor absent answers become
  user-confirmed experience.

### 3. Validated local CRUD and rebuildable projections

- Provide a small in-process operation service and explicit agent-readable
  command boundary. Create appends; update requires the current parent;
  delete archives/tombstones without removing historical inputs.
- Under the same journal writer lock, check identity, contracts, parent,
  operation key, actor, approved tool/source inventory and effects. Publish
  immutable record/event and operation receipt together. Same key/payload
  returns the original receipt; changed payload conflicts, including after
  interrupted publication/recovery. Avoid an unlocked read-then-write race.
- Supported Gig-owned tool requests bind the actual approved Gig/version,
  entry and tool bytes, source inventory, operation/effects and agent origin;
  recheck before publication. Reject changed/extra/foreign/unapproved source
  and broadened effects. Do not accept a caller-supplied digest as proof.
  Keep direct built-in commands distinct from claims of approved Gig-tool
  execution. Neither path manufactures operator consent.
- Add a closed versioned Scout table set in `state.sqlite`. Project validated
  committed records only, with journal HEAD/schema cursor. Transactional
  rebuild changes only managed tables and preserves existing G22 evidence.
  Readers repair or report staleness; new input selection cannot silently use
  a stale projection. Unsupported declarative versions refuse; no supplied
  migration SQL is executed.
- A committed operation whose projection fails returns committed plus
  `projection_pending` and a rebuild action. Retry cannot duplicate authority.
  Direct SQL modification is repaired/refused, never promoted to authority.

### 4. Context recovery and exact input selection

- Distinguish saved defaults from task-only overrides explicitly. The latter
  must not change saved preferences or affect another task's selection.
- Build scoped metadata-only `indexes/context.json` from committed records
  and existing Run state: IDs, revisions, safe summaries, relative locations,
  relationships/backlinks, outstanding questions and current work. Do not
  embed raw private payloads, source paths, ambient chat or cross-Gig data.
- Explicit `record read --id ... --revision ... --content` returns only the
  named private bytes to the invoking agent. Metadata listing/export/error
  paths remain redacted. Selected conversation capture consumes only supplied
  bounded content, labels excerpt versus summary, and never follows a session
  locator or reads surrounding agent history.
- Resolve exact G45 IDs and exact Scout record/revision selections to one
  normalized input set. Pin record and content digests in Plan inputs and
  sealed sources without duplicate blobs. Authenticate the committed revision
  chain and scope, and revalidate before Run allocation. Deduplication must
  not erase an explicit revision choice.
- Preserve existing v1/v2 Plan schemas and identity guarantees. Prefer their
  existing typed artifact-reference seams where sufficient. If a distinct
  serialized boundary is necessary, add a strict versioned schema and update
  every affected reader deliberately; do not loosen old schemas or recursive
  v1 invocation forbidden-key checks.
- Import/selection/content read is NOT provider disclosure authority. Reject
  private selected inputs at unrestricted provider ingress before any call;
  do not relabel them public to reuse document review. External recording and
  successor-Run execution are SCOUT-04; supply and test the exact-input seams
  needed for its later question/checkpoint story without inventing a Run now.

## Ownership and exclusions

### Caller-audit disposition

Astra read the completed audit on 2026-09-08. Its database replacement and
uncoordinated trace-write findings are required implementation corrections,
not new product-approval gates. They are covered by stages 1 and 3 above.

Some audit recommendations span later goals: registry-v3 owner/default-instance
batch work belongs to SCOUT-05; external invocation/Plan/Run/checkpoint
execution belongs to SCOUT-04; private transfer belongs to SCOUT-11. Do not
pull those implementations into SCOUT-03 merely because the audit lists their
callers. Metadata index cursors remain disposable; they are not a new journal
authority or a reason to append an event for every context read. This plan
and the accepted goal dependencies govern the bounded implementation scope.

Existing managed Plans may pin explicitly selected private artifact identities
only with fail-closed provider ingress. That is storage/binding proof, not an
external-execution Plan or permission to send private content. SCOUT-04 will
implement its separately specified external family and consume the same exact
selection resolver; do not disguise that mode as a managed review Run.

Terra is the sole runtime/schema/test implementation writer. Expected seams:
`workpad.py`, `journal.py`, `index.py`, G22 trace persistence/callers,
`canonical.py`, `validators.py`, new private-record/storage modules, `cli.py`,
Plan/Run input and provider-ingress validation, invocation/capability dispatch,
and relevant package/export safeguards. Update all schema inventories, golden
fixtures and installed-resource checks for any added schemas. Keep strict
nested definitions and new code readable; the v0.1.8 redesign remains deferred.

Astra owns this plan, roadmap and execution ledger. Luna's initial audit owns
only its new report. No other source writer is active when Terra starts.
Preserve all existing dirty SCOUT-02/G43.1 work; do not alter the approved G44
requirements baseline, existing `.gigai/`, private user Gigs or installed user
configuration. Do not commit, merge, publish, call providers, fetch research,
install Dolt, create a daemon/HTTP CRUD API, or run other agents.

Per-Gig shipped scaffolds/default initialization are SCOUT-05. Actual research,
job matching, tailoring, application facts and HTML tracker vertical slices
remain SCOUT-06 onward. This goal supplies their persistence/tool foundation,
not a premature claim those workflows function. Private human UAT remains an
explicit later gate; fixture tests do not complete it.

## Acceptance and review gates

1. Sanitized fixture import preserves original bytes; equivalent imports are
   idempotent and changed content is a new immutable object. Reject malformed
   IDs, binary/invalid UTF-8/oversize content, unsafe parents and foreign scope.
2. Saved preference revision versus task-only override produces distinct
   intended behavior. Stale/concurrent parents and changed-payload operation
   retries fail without extra journal commits. Archive retains old selections.
3. Restart/rebuild recovers the same records, relationships, questions and
   exact historical inputs. Inject failures around journal publication,
   projection commit and recovery; prove no duplicate or invented success.
4. Legacy-first and Scout-first index access, rebuilds and concurrent G22
   trace writes preserve both data families. Unknown/malformed tables and
   missing trace evidence fail visibly. Cursor drift/direct SQL tampering do
   not affect authoritative selection.
5. Layout migration preserves v1 historical bytes and ignored drafts; forged
   markers, ignore changes, unknown roots, collisions and symlink escapes
   refuse. Approved-tool mutations prove inventory/effect/version checks.
6. A fresh agent session can list scoped metadata, explicitly read one chosen
   revision, and select it with a posting. A subsequent saved answer/update
   leaves the older sealed Plan unchanged; missing/changed/unjournaled/foreign
   refs refuse before Run allocation. No unselected/private provider leakage.
7. Source/schema/CLI and legacy G43/SCOUT-02 tests pass, schema inventories and
   installed resource verification pass, and an isolated wheel proves the new
   commands/resources outside the source checkout. Run the full offline suite
   and distinguish any environment-only failures and their bounded reruns.

Terra finishes implementation, tests and
`SCOUT-03-implementation.md` before sending `worker_done`. The coordinator
does not run competing tests or inspect in-flight code. Then Claude reviews
the frozen source and Luna independently verifies it. Findings return to a
bounded implementation task; no acceptance is inferred from a worker's claim.

## Dispatch provenance

- Initial read-only audit: `task_3048d75c30db`, dispatch
  `ctx_0c1c578c596a`, in the existing worktree. Custom launch:
  `codex --model gpt-5.6-luna --approve-for-me -c model_reasoning_effort="high"`.
  The reused-terminal dispatch receipt records `ready/input_accepted`; its null
  model fields are not model evidence, so the launch command is recorded here.
  The audit completed and its report was read. Release returned
  `retained/external_terminal`, no process action; no forced terminal close.
- Active implementation: `task_ec34c05cade5`, dispatch `ctx_8ab74b3542cd`,
  started after the completed audit (`ready/input_accepted`). Fresh
  terminal `term_2b1402d1-b293-45ba-8494-b809bea9d7fc` was launched with
  `codex --model gpt-5.6-terra --approve-for-me -c model_reasoning_effort="high"`;
  the task uses that exact terminal. The user-owned Terra terminal was
  not modified or restarted.
- Queued independent review: `task_27278b1bcc11` (Claude); independent
  verification: `task_2650ada48379` (Luna). Both depend on Terra's finished
  implementation/testing handoff; neither has started.
- Dispatch receipts will be recorded as tasks start. No provider dogfood or
  private-human acceptance is claimed.

### First handoff and diagnostic review

Terra settled the first attempt with outcome `failed` because complete-suite
evidence was unavailable in its report. Source inspection also identifies
missing CRUD/archive, native typed content, saved-default/override and Plan
selection integration; the report's broad delivered claims are not acceptance.
Release returned `retained/external_terminal` with no process action.

The original dependency-gated review/verification tasks are explicitly blocked
and superseded by diagnostic review `task_efeb4b00f438` (Claude,
`ctx_cd6503a3277b`) and verification `task_6980e886e443` (Luna,
`ctx_261d38fe25b7`). Both reached `ready/input_accepted`. Luna was launched
with `codex --model gpt-5.6-luna --approve-for-me -c model_reasoning_effort="high"`;
Claude uses configured defaults. Both may write only their evidence reports.

At 22:11 UTC, root found original full-suite PID 30461 still running, contrary
to the worker's cutoff interpretation. Root immediately interrupted only its
new duplicate PTY suite (session 96883), which has no final acceptance result.
A read-only recovery task (`task_f80a5320701f`) could not attach to the same
Terra terminal: attempt `ctx_788ec92713c2` failed at `agent_readiness` on a
Codex low-quota interactive reminder, before task injection. No model setting,
user terminal, or original test process was changed. Full-suite evidence must
be recovered or rerun after that exact original process settles.

The coordinator's later [full-suite run](SCOUT-03-full-suite-recovery.md)
finished with 769 passed, 104 subtests, one intentional skip, and exit code 0.
Claude's completed review still rejects the implementation for missing contract
behavior. Its terminal was released with transcript captured. Following the
operator's usage reset, Luna's same verification assignment was resumed to
finish its adversarial report; no source edits run underneath it.
