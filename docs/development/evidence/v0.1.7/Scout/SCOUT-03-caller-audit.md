# SCOUT-03 — Private records and resumable context caller audit

**Date:** 2026-09-08  
**Worker:** Luna  
**Status:** Read-only pre-implementation audit; no source or private `.gigai`
state was changed.  
**Inspected baseline:** current worktree at `fda4857`, including the accepted
SCOUT-02 additions and existing unrelated dirty work.

## Audit boundary and verdict

This audit traces the current callers named by the accepted SCOUT-00
contract, its B2 G45 bridge, A03, the user-owned-Gig amendment, G45, the
SCOUT roadmap, and the accepted SCOUT-02 completion record. It does not
implement SCOUT-03, add schemas, run tests, invoke providers, inspect private
`.gigai` data, alter an approved baseline, or migrate an existing workpad.

**Verdict: SCOUT-03 is not implementable by adding a records module alone.**
The current system has a journal-authoritative v1 foundation and a simple
rebuildable `state.sqlite` projection, but the required callers still assume
the old top-level layout, v1 invocation/Plan families, path-based review
inputs, v2 registry, and package-only portability. The implementation wave
must first establish additive strict schema families and the v2 layout/registry
boundary, then route all private-record mutations through one journal-backed
operation service and one locked SQLite projection service.

Two current behaviors are immediate authority/data-loss blockers:

1. `index._write_projection` creates a replacement SQLite file containing only
   `projection` and, when recognized, `interview_events`. Unknown tables are
   dropped and malformed trace databases can be treated as absent. This
   conflicts with the amendment's requirement to fail visibly rather than
   discard unknown/malformed trace data.
2. `proposal_interview.persist_trace` commits directly to `state.sqlite`, and
   `lifecycle._persist_interview_trace` opens an independent connection. Neither
   participates in the private-Git writer lock or an index/trace coordination
   lock. Rebuilding with `os.replace` can race with a G22 trace write and lose
   the trace even when the trace table itself is valid.

The following ordering is required for a safe implementation: schema and
identity dispatch; common writer/database locking; workpad layout and registry
migrations; G45 immutable import families; revision wrappers and journal CRUD;
projection/context readers; selected-input Plan/Run ingress; then package,
privacy, export, and acceptance evidence.

## Contract obligations carried into the audit

- The private Git journal and typed revisions are authority. `state.sqlite`,
  `indexes/context.json`, HTML, and other views are rebuildable projections.
- v1 records/readers/Plans/Run evidence remain byte-compatible. Changed strict
  contracts get distinct schema files/URNs and explicit reader dispatch;
  unknown schema versions return `unsupported_schema_version`.
- G45 remains the sole imported-content authority for its immutable
  `private-reference:1` and `run-input-record:1` families at
  `references/ref_<uuid>/source.txt` and
  `run-inputs/input_<uuid>/source.txt`. Scout wrappers reference those bytes;
  they do not duplicate or relabel them.
- Scout's `private-record-revision:1` is a private-sensitive, immutable
  revision wrapper with project/Gig scope, parent revision, actor, origin,
  kind, relationships, and a discriminated exact content reference
  (`g45_reference`, `g45_run_input`, or native `jsl_blob`). Updates use
  compare-and-swap and operation-key idempotency.
- A Run resolves and pins exact record/revision/blob refs. A task-only
  override is not a saved default update. A successor Run is explicit when a
  required answer changes sealed inputs.
- Metadata/context inspection is scoped and redacted by default. Exact private
  content requires an explicitly named content-read operation. No provider
  disclosure, all-record read, ambient conversation harvesting, or hidden
  reasoning capture is permitted.
- The user-owned layout is v2 under the registry-resolved Gig root. `records/`,
  `references/`, `run-inputs/`, `docs/`, `runs/`, `manifests/`, `handoffs/`,
  `indexes/`, `reports/scout/`, `state.sqlite`, and the v2 software/tool
  inventory must be validated as a single ownership boundary. Existing v1
  workpads remain unchanged until an explicit migration is selected.

## Caller inventory and required changes

### 1. SQLite projection and G22 trace preservation

| Current seam | Current behavior | Required SCOUT-03 change / acceptance |
|---|---|---|
| `src/gigai/index.py:49-57` `rebuild_index` | Replays only committed journal handoffs, then replaces `state.sqlite`. | Extend the projection contract to a closed, versioned Scout table set and cursor (`journal HEAD`, projection schema). Rebuild only Scout-managed tables while preserving the recognized G22 trace and refusing unknown/malformed tables. Add legacy-first and Scout-first rebuild tests. |
| `src/gigai/index.py:60-126` `_authoritative_projection` | Projection authority is limited to handoff entries, `gig-proposal.json`, and `active-gig-version.json`; it has no record/revision/context replay. | Replay validated committed record revisions, tombstone/archive events, operation receipts, and layout/version metadata into deterministic Scout rows. Never read uncommitted files or dashboard prose. Keep graph/Gig identity distinct from semantic selectors. |
| `src/gigai/index.py:129-145` `read_index` | Recomputes authority and repairs a divergent SQLite file automatically. | Read the cursor and compare it to authoritative HEAD. Repair only through the coordinated projection service; return a typed stale/pending result where repair cannot complete. Do not silently use stale application state for a new Plan. |
| `src/gigai/index.py:163-202` `_write_projection` | Writes a temporary database and `os.replace`s it. It creates only `projection` and optional `interview_events`; any other existing table is absent from the replacement. | Replace this with a common locked database publication path. Preserve all recognized legacy trace rows, refuse unknown tables and incomplete trace recovery, and atomically advance the Scout cursor only after all Scout tables are rebuilt. Test concurrent trace activity and publication failure. |
| `src/gigai/index.py:205-243` `_read_interview_events` | A missing table is `None`; SQLite/open/schema errors often return `None` too (`217-228`), which lets replacement discard data. It validates only the six-column G22 table. | Differentiate absent, valid, malformed, and unknown-table states. Malformed/unknown/incomplete trace state must fail closed with a recovery diagnostic, never become “no trace.” Preserve exact bytes/rows on transfer. |
| `src/gigai/index.py:245-271` `_read_projection` | Reads one opaque `projection.payload`; it does not enforce a projection schema/version, table inventory, cursor, or project/Gig types beyond later callers. | Add strict projection metadata and version dispatch. An unsupported projection version must refuse, not execute caller-supplied migration SQL. |
| `src/gigai/index.py:297-306` `_root`/`_require_clean_authority` | Requires a clean Git workpad for normal authority replay. | Preserve this boundary for authority reads, but add v2 layout marker validation and an explicit migration mode; do not make dirty user-owned working copies disappear or silently relocate. |

**G22 direct writers:** `src/gigai/proposal_interview.py:556-606`
(`persist_trace`/`load_trace`) create/query/insert/commit the trace table on an
arbitrary supplied connection. The HTTP handler calls it at
`proposal_interview.py:754-758`; lifecycle opens that connection directly at
`src/gigai/lifecycle.py:2345-2350`. This is the complete production SQLite
write path besides registry writes found in this audit. The new service must
make G22 trace writes and Scout projection rebuilds share the same lock/order,
while preserving `interview_events` as a legacy projection/evidence table and
not treating it as Scout record authority. The handler's in-process lock is
not an interprocess database lock.

### 2. Journal, workpad validation, and v2 layout

| Current seam | Current behavior | Required SCOUT-03 change / acceptance |
|---|---|---|
| `src/gigai/workpad.py:35` `WORKPAD_GITIGNORE` | Exact v1 policy only ignores `/objects/`, `/scratch/`, and `/state.sqlite`. | Add a strict v2 policy including the declared root working-copy paths, `/indexes/`, `/reports/scout/`, and bounded SQLite transient siblings, while retaining v1 behavior. A policy change is journaled and migration-specific; never treat ignore rules as trust. |
| `src/gigai/workpad.py:436-515` `_validate_workpad_repository` | Allowed semantic roots are a hard-coded pre-v2 set and there is no layout marker. `.gitignore` must equal the v1 constant exactly. `records`, `references`, `run-inputs`, `docs`, `indexes`, and user-owned software roots are refused as unexpected. | Add strict `workpad-layout:2` validation under `manifests/`, root/manifest safety checks without following links, exact journal binding, and conditional v1/v2 allowed roots. Validate every component and inventoried source; reject forged markers, unsafe roots, redirects, unknown ignore changes, and collisions. |
| `src/gigai/workpad.py:98-160`, `163-174`, `227-270`, `609-651` | Provisioning and resolution are registry/Gig-ID based and assume one existing semantic workpad. No layout migration, owner, template-instance, or record scope is resolved. | Keep the registry-resolved physical root; add explicit v1-to-v2 migration with identity-preserving collision refusal and dirty-file preservation. Resolved operations must carry project/Gig/version scope into every record read/write. |
| `src/gigai/journal.py:156-182`, `215-261` | Journal transitions validate IDs/artifact paths and serialize under `.git/gigai-writer.lock`. | Reuse this lock and publication/recovery protocol for record revisions, tombstones, operation receipts, layout migration, and context-index cursor events. Add transition families and typed front matter without weakening old handoff schemas. |
| `src/gigai/journal.py:462-480` `_validate_workpad` | Checks identity markers, exact v1 ignore bytes, no remote, and Git ownership, but not layout marker/root inventory. | Make v2 journal writes require the authenticated layout marker and permitted root map. A v1 journal must not accidentally publish v2 roots without migration. |
| `src/gigai/journal.py:604-627`, `749-800` artifact validation/publication | Generic relative paths and bytes are accepted; safe path components and immutable collision checks exist, but no kind-specific record schema or private-content family rule exists. | Add schema/sidecar validation before artifact publication; enforce canonical G45 paths, immutable snapshots, wrapper-to-source digest equality, bounded native blobs, and no duplicate content authority. Keep CAS parent and operation-key checks inside the writer lock. |
| `src/gigai/journal.py:324-384` recovery | Explicit recovery handles journal transaction manifests and next handoffs. | Extend recovery to record operation receipts, projection-pending state, and G45 import publication. A post-journal projection failure returns `committed` + `projection_pending`; it must not duplicate the event or roll back authority. |

### 3. Registry and initialization callers

| Current seam | Current behavior | Required SCOUT-03 change / acceptance |
|---|---|---|
| `src/gigai/registry.py:18-58` | Registry is schema v2 with only `projects`, `workpads`, and `active_workpads`; expected tables are exact. | Add the separately reviewed v3 migration containing `workspace_owners` and `template_instances`, with strict identity/owner/template/package fields, uniqueness/FK constraints, and no user-name-as-Gig-ID behavior. |
| `src/gigai/registry.py:340-367`, `410-585` | `open_project_registry` migrates only v1 to v2; any other version or table set is refused. | Add v2-to-v3 migration with the same lock/backup/recovery discipline, exact preflight, no active-Gig replacement, and explicit `workspace_owner_conflict`/unsupported-version diagnostics. Preserve v1 readers and old registry bytes until migration. |
| `src/gigai/registry.py:644-683` migration | Migration creates only the v2 tables and sets `user_version=2`. | Extend migration inventory and rollback/recovery for owner row and template-instance batch state; do not infer authority from cache rows. |
| `src/gigai/workpad.py:518-537` `_register_record` | Registers a `(project_id, gig_id, locator)` row only; no owner/template-instance binding. | Instance creation must journal a typed binding and verify owner/inventory/Gig identities before registry cache publication. |
| `src/gigai/package.py:253-367` `initialize_project_package`/`_initialize_and_prepare` | `gigai init` initializes one project package/binding and either adopts one package or creates an empty package. It does not accept a username or create default private Gig instances. | SCOUT-03 must expose the record/layout prerequisites to SCOUT-05's general `gigai init` batch. Do not implement a per-Gig clone shortcut here. Init must never execute copied tools, import private evidence, grant approval, or replace existing instances. |
| `src/gigai/cli.py:1601-1671` `init` | CLI has target/home/json/adopt/confirm only; no `--username`, owner conflict, batch inventory, or partial-batch next action. | Keep this as a downstream SCOUT-05 caller, but SCOUT-03 must define the project/Gig scope and layout APIs it consumes. Acceptance must include fresh-session owner metadata recovery and no arbitrary active-Gig replacement. |

### 4. G45 import, revision wrappers, and context readers

No production implementation currently provides `reference add/list/show`,
`run-input add/show`, private-reference schemas, or a Scout context index. The
G45 contract is explicit at
`G45-private-references-and-pasted-run-inputs.md:93-144` and `146-188`, but
the source inventory has no `reference*.schema.json`,
`run-input-record.schema.json`, or `private-record-revision.schema.json`.

Required caller chain:

1. A bounded importer must validate exact UTF-8 text/Markdown, source kind,
   size (1 MiB reference / 256 KiB Run input), label, path components,
   symlinks, and project scope before writing canonical G45 files. It must
   preserve exact bytes and return an idempotent receipt keyed by
   `(project, kind, privacy, media, digest)`.
2. A Scout record operation must create a typed revision wrapper under
   `records/<record-id>/revisions/<revision-id>.json`, validate the parent
   revision and operation key under the journal lock, and point to the exact
   G45 record/snapshot or bounded native blob. It must not copy G45 source
   bytes into a second mutable store.
3. Reads/list/status/context must default to safe metadata. The explicitly
   named `record read --id ID --revision REV --content` path is the only
   content-returning inspection operation. It must revalidate project/Gig,
   record/revision/digest, and exact path; it is not provider consent.
4. `indexes/context.json` is a rebuildable metadata projection with journal
   HEAD/cursor, record IDs/kinds/revisions, safe summaries, locations,
   outstanding questions, and current Run metadata. It must not contain raw
   private source text or broaden a provider input set.

The current nearest caller, `src/gigai/proposal_interview.py:430-480`, emits a
G22 snapshot that includes questions/answers/events and
`src/gigai/proposal_interview.py:483-553` rehydrates it from a workpad record.
That is useful precedent for typed rehydration but is not a substitute for
SCOUT private revisions: its records are proposal-interview-shaped, have no
Scout kind/privacy/content discriminator, and do not implement saved-default
versus run-override or stale-parent CAS. Selected conversation capture must be
a separate bounded, explicitly supplied operation with exact excerpt-vs-summary
and declared source labels.

### 5. Invocation, Plan, Run, and selected private inputs

| Current seam | Current behavior | Required SCOUT-03 change / acceptance |
|---|---|---|
| `src/gigai/invocation.py:13-18`, `97-140`, `143-152` | One manual v1 envelope; commands are `setup/models/doctor/create/run`; actor is always a known `agent` with session; forbidden-key recursion and 256 KiB envelope limit apply. | Preserve v1 parsing unchanged. Add an additive, strict external-recording invocation family with required `origin` discriminator (`agent_invocation` or `direct_cli`), matching actor union, operation key, normalized payload digest, explicit scope, and no provider/capability/consent fields. Direct CLI envelopes must be constructed only by the direct path. |
| `src/gigai/cli.py:408-435` `invoke` | Validates and echoes an agent envelope but creates no authority and has no record/context/external operation namespace. | Add explicit record/context/external commands through the shared validated service. A successful parse alone must not imply mutation, provider disclosure, approval, or Run consent. |
| `src/gigai/cli.py:2399-2430`, `src/gigai/run_plan.py:872-915` | `run-plan create` accepts arbitrary explicit file paths via repeated `--input`; it reads/copies those bytes into the Plan and requires no G45 IDs. | Add a selected-input operation that accepts exact G45 IDs or Scout record/revision IDs. Normalize one deduplicated resolved ref list before sealing; revalidate project/Gig, schema, revision chain, path, size/media/digest, and preserve the old v1/v2 input path behavior. Do not inspect directories for “likely” résumé/job material. |
| `src/gigai/run_plan.py:839-853`, `1049-1148` | Plan reader dispatches only `run-plan.schema.json` vs `run-plan-v2.schema.json`; plan identity is G43 review-oriented and includes generic artifact refs, target/provider participants, and `sealed_sources`. | Add a distinct external-recording Plan family/mode. Do not fabricate an external Plan by inserting G45 refs into the G43 review Plan. External Plan identity must include ordered selected record/revision/blob refs and exact normalized invocation payload, while excluding timestamps/session-local IDs. |
| `src/gigai/run.py:84-220`, `593-651` | Run ingress resolves active authority through `read_index`, validates approved v1/v2 authority, then allocates a Run only after direct consent. | Keep authority-before-allocation and direct consent. Add external `plan/start/checkpoint/submit/cancel/inspect` ingress that revalidates every sealed source under the journal lock, records statuses (`active`, `waiting_input`, `succeeded`, `cancelled`, `interrupted`), and never treats external process exit as success. |
| `src/gigai/run.py:758-900` `_validate_plan_handoff` | Validates Plan bytes, generic sealed source paths/digests, graph selection, target configuration and provider readiness; it has no G45 family discriminator or revision-chain validation. | Split managed G43 and external recording validation. External validation must refuse foreign/changed/missing G45 snapshots, duplicate wrappers, altered old bytes, unpinned records, unsafe paths, and provider fields. A required-input change returns `external_successor_required` with selected metadata only. |
| `src/gigai/run.py:933-1117` `_prepare_records` | Run brief/manifest hard-code operator identity and store `invocation_argv`; manifest's sealed sources contain graph/capability/Plan/consent refs, not private record semantics. | Keep old Run manifest bytes/readers. Add a separate external Run manifest/records family with exact invocation, mode, Plan, selected inputs, checkpoints, and disclosure policy. Do not let a direct CLI `--confirm` claim come from an agent envelope or imported activity. |
| `src/gigai/cli.py:2500-2614` `run` | Agent invocation is accepted only when it describes `command=run`; CLI separately requires `--confirm`, verifies home/project/Gig/version/wait equality, then constructs operator consent. | Preserve this direct-consent bridge for managed Runs. Add external operation commands whose start still requires explicit direct consent where the contract says so; selected private inputs are data, not provider grants. The external path must not emit or accept managed provider target configuration. |
| `src/gigai/run.py:127`, `src/gigai/run_plan.py:875`, `1115` | Plan/Run callers use `read_index` for active authority and can trigger a projection repair during ingress. | Make the context/Scout projection cursor check explicit and distinguish stale/pending application projections from immutable approved Graph authority. A projection repair must not alter graph/version approval or allocate a Run from stale private state. |

### 6. Package, privacy, portability, and export

| Current seam | Current behavior | Required SCOUT-03 change / acceptance |
|---|---|---|
| `src/gigai/package.py:138-212` `inspect_package` | Recursively inventories all files under a package, rejects symlinks/hooks/executables/oversized files, and validates `gig-package.schema.json`; it has no Scout private-record classification. | Keep portable packages authority-free, but make inventory explicitly exclude/reject `records`, `references`, `run-inputs`, `runs`, `docs` with private state, context indexes, reports, credentials, and conversation material if they are ever presented as package material. Add source/tool/UI inventory rules separately from private state. |
| `src/gigai/package.py:215-249` `export_package` | Exports only a validated package directory and reports `authority_imported: false`; it has no private-transfer mode. | Preserve clean package export semantics. Add a separately scoped private transfer/backup contract later in SCOUT-11; do not make package export carry private G45/Scout records or silently copy authority. |
| `src/gigai/package.py:253-367` `initialize_project_package`/`_initialize_and_prepare` | `gigai init` initializes one project package/binding and either adopts one package or creates an empty package. It does not accept a username or create default private Gig instances. | SCOUT-03 must expose the record/layout prerequisites to SCOUT-05's general `gigai init` batch. Do not implement a per-Gig clone shortcut here. Init must never execute copied tools, import private evidence, grant approval, or replace existing instances. |
| `src/gigai/cli.py:1601-1671` `init` | CLI has target/home/json/adopt/confirm only; no `--username`, owner conflict, batch inventory, or partial-batch next action. | Keep this as a downstream SCOUT-05 caller, but SCOUT-03 must define the project/Gig scope and layout APIs it consumes. Acceptance must include fresh-session owner metadata recovery and no arbitrary active-Gig replacement. |
| `src/gigai/package.py:370-427` install | Installs package bytes into `.gigai/packages/` and writes a local installation record; no private data is imported. | Keep this no-authority-import boundary. Add explicit checks that package install cannot create private records, owner bindings, active versions, or Run consent. |
| `src/gigai/portability.py:51-104` | v2 active-version portability is explicitly inspection-only (`unsupported_schema_version`); v1 authority/capability lineage is the supported path. | Do not expand this into private-record export. Add metadata-only package/context checks that refuse unsupported private/layout versions and never turn a projection into portable authority. |
| `src/gigai/package.py:739-773` private-home fingerprint | Migration preservation hashes private-home files but excludes only config/registry/backup/migrations; it has no workpad layout or private-transfer policy. | Include explicit v2 migration/transfer inventory boundaries and omit credentials, locks, transient SQLite files, and reports unless a reviewed private transfer selects them. Preserve exact record/Run history and no authority escalation. |

## Strict schema, version, and inventory gaps

1. `src/gigai/validators.py:28-73` has no `private-reference`,
   `run-input-record`, `private-record-revision`, `workpad-layout`, Scout
   operation, or external-recording Plan/Run/checkpoint/receipt schemas.
   `validate_serialized_contract` at `132-153` can validate only the packaged
   names and does not dispatch a new family by URN/mode.
2. `src/gigai/schemas/SHA256SUMS:1-44`,
   `tools/verify_installed_schemas.py:10-55`, and the schema README's
   inventory (`README.md:38-54`, `144-148`) have no SCOUT-03/G45 resources.
   Adding a file without updating all three is an installed-wheel/inventory
   failure. Existing v1 hashes must remain unchanged.
3. `src/gigai/run_plan.py:808-824` dispatches by the integer `plan_version`
   only. It cannot distinguish a future external-recording Plan from a managed
   G43 v2 Plan. The external family needs a distinct schema identity/mode and
   explicit reader/writer refusal of the other family.
4. `src/gigai/run_plan.py:124-154` and `758-822` treat an artifact ref as a
   path/digest/size and do not validate G45 record IDs, `record_ref` versus
   `snapshot_ref` semantics, wrapper revision chains, privacy class, or source
   family. Path validation is necessary but insufficient.
5. `src/gigai/invocation.py:19-47` has no `origin`/actor union or operation-key
   fields. Its v1 recursive forbidden-key behavior must remain unchanged while
   the new external boundary is additive; do not widen v1 schemas in place.
6. `src/gigai/workpad.py:35`, `436-515`, and `src/gigai/journal.py:462-480`
   encode exact v1 layout/ignore assumptions. A v2 marker and migration family
   must be distinct and journal-authenticated; accepting an unknown marker as
   a v1 workpad would bypass the new root boundary.
7. Current G45 prose requires strict schemas at
   `docs/development/v0.1.7/goals/G45-private-references-and-pasted-run-inputs.md:93-144`,
   but no corresponding runtime/CLI/schema callers exist in the inspected
   source tree. This is an implementation gap, not completion evidence.

## Acceptance gaps against A03 and the roadmap

The accepted A03 matrix requires “saved vs task-only preference, stale-parent
conflict, prior inputs unchanged, fresh-session metadata recovery, no
cross-Gig/private leak” (`SCOUT-00-contract-amendments.md:307-317`). Current
callers do not provide any complete A03 path:

- **Saved vs task-only:** no Scout record operation or operation payload
  distinguishes `saved_default` from `run_override`; current G43 Plan input is
  generic `--input` file copying (`run_plan.py:914-939`).
- **Stale parent:** journal artifact collision protection exists, but there is
  no record revision parent/CAS check or typed `stale_parent` diagnostic.
- **Prior inputs unchanged:** G43 seals copied artifact refs, but no G45
  immutable record IDs/snapshot refs are selected or revalidated by a family;
  a newer wrapper revision cannot currently be distinguished from the old
  sealed input.
- **Fresh session:** no `indexes/context.json`, context cursor, scoped metadata
  reader, or current-Run/checkpoint summary exists. G22 snapshot recovery is a
  separate proposal-interview path.
- **No leak:** package, public diagnostics, Plan/Run readers, and external
  recording have no common private-content redaction/source-family policy.

SCOUT-03's roadmap completion condition also requires the correct work to be
recoverable in a fresh session, task-only overrides to differ from updates,
exact historical Run inputs to survive updates, and private/unselected records
to stay out of package/public paths (`v0.1.7-scout-roadmap.md:261-273`). The
accepted workspace amendment adds required scenarios for CRUD/import/recovery,
G22 trace survival, forged layout refusal, changed tool/schema refusal, report
privacy, and clean/private transfer
(`SCOUT-00-user-owned-gig-amendment.md:369-420`). None can be claimed from the
accepted SCOUT-02 evidence: SCOUT-02 explicitly limits itself to graph/version
foundation and says private operator-Gig execution and user-owned portability
remain downstream (`SCOUT-02-completion.md:50-68`).

Required adversarial evidence before SCOUT-03 acceptance:

- equivalent G45 import is idempotent; changed exact bytes create a new record;
  missing/altered/foreign/symlink/traversal/binary/invalid-UTF-8/oversized
  inputs fail closed without source-text disclosure;
- stale-parent and changed-operation-key conflicts are serialized under the
  journal lock; interrupted journal publication and projection failure recover
  without duplicate records or invented completion;
- index rebuilds preserve G22 trace rows and refuse malformed/unknown trace
  tables, under both Scout-first and trace-first ordering and concurrent access;
- fresh-session context recovers metadata only, while explicit selected content
  reads return only the named private bytes and never provider disclosure;
- an older sealed Plan remains bound to the old exact G45 snapshot when a newer
  wrapper/source exists, and altered old bytes refuse before Run allocation;
- two Gigs cannot select one another's records or wrappers; package inspection,
  clean export, public diagnostics, and report output contain no private bytes,
  absolute personal paths, credentials, or ambient conversation;
- v1 readers and schema hashes remain unchanged; unknown v2/projection/layout
  versions return typed unsupported diagnostics rather than executing migration
  SQL or silently rewriting bytes.

## Implementation handoff and ordering

1. Freeze the schema/URN inventory, exact G45 bridge fields, operation-key
   identity projection, layout marker, registry-v3 rows, and public diagnostic
   codes. Keep old v1 schemas/readers and hashes untouched.
2. Build one private operation service around the existing journal writer lock,
   with record/revision CAS, immutable G45 import publication, typed receipts,
   and explicit recovery. Do not let Gig-owned `gig.py` tools write SQL,
   journal files, receipts, manifests, or Run evidence directly.
3. Build one coordinated SQLite service. Define the closed Scout table set and
   cursor, preserve/validate G22 `interview_events`, fail closed on unknown
   tables, and make trace writes and rebuilds use the same lock/order.
4. Add v2 workpad validation/migration and registry-v3 owner/instance caches;
   validate layout and source inventory before admitting records or tools.
5. Add scoped record/context readers and exact-content read. Add selected-input
   normalization, then the distinct external recording Plan/Run ingress with
   direct-consent and no-provider-disclosure checks.
6. Update package/init/export/privacy callers and installed schema inventory;
   finish with A03/G45 adversarial fixtures, fresh-session replay, concurrent
   G22/index tests, and sanitized private UAT. Report fixture/offline evidence
   separately from live agent/provider evidence.

## Evidence limits

This report is source/document evidence only. No current runtime, installed
wheel, test suite, provider/agent call, private operator workspace, migration,
commit, release publication, or approval was performed by this worker.
