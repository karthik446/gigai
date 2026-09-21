# SCOUT-00 Caller and Compatibility Audit

> Documentation rename only: `SCOUT-*` refers to the same historical `JSL-*`
> delivery work. Findings, verdicts, test commands and measured results below
> retain their original scope; they do not review or accept the new
> [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md).

**Date:** 2026-09-07  
**Inspected baseline:** `fda48574f8642e66c0e7d53e7303ec04f04d7bb8`  
**Scope:** read-only source/schema/goal audit for the approved Job Search
Lifecycle roadmap. This report is the only file written by this audit. No
provider or model calls were made, no secrets were read, and no `.gigai` state,
migration, schema, or runtime code was changed as part of execution.

## Executive findings

The current implementation has a sound single-Gig/single-Goal-Graph authority
chain, but it has no shipped Scout template, private instance/record layer, or
external-agent work protocol. The minimum compatible implementation is
additive: keep the existing UUID `graph_id`, `gig_id`, proposal/approval,
journal, Run Plan v1, Run manifest v1, and active-workpad authorities intact;
add a versioned Graph Set and semantic selector layer, a private instance and
record store, and an external-work writer/reader that never calls a model.

The existing G42 one-package rule creates the largest initialization decision.
On a fresh unbound target, named Scout initialization can materialize the shipped
package and bind it. On an already initialized target with a different or
empty package, it must not replace or merge that package; it should retain the
existing package and bind a private Scout instance to the built-in template
provenance, unless a separately approved multi-package amendment changes this
rule.

## Evidence baseline and current callers

### CLI surface (`src/gigai/cli.py`)

| Concern | Current entry point and exact behavior | Scout implication |
|---|---|---|
| Workspace setup | `init_command` accepts only `--target`, `--home`, `--json`, `--adopt-package`, and `--confirm` (`cli.py:1600-1669`). | Add a positional optional template/alias (`jsl`, `job-search-lifecycle`) without changing bare `init` behavior. Keep `--adopt-package --confirm` semantics for the existing package boundary. |
| Package/catalog | Catalog commands call `catalog_entries`, `get_catalog_entry`, `validate_catalog_entry`, and `materialize_catalog_package` (`cli.py:1672-1770`). Package commands call `inspect_package`, `install_package`, and `export_package` (`cli.py:1773-1848`). | Add the Scout catalog entry/template descriptor and an instance-binding operation. Do not make catalog install approve a Gig, select a graph, or start work. |
| Custom authoring | `create_command` requires an explicit agent invocation and calls `create_offline` (`cli.py:1893-2005`). | Named Scout initialization must not route through `create_offline`; that path allocates a fresh Gig and can invoke a model when `model_output` is absent. `gigai create` remains custom authoring. |
| Agent invocation | `invoke_command` only validates/normalizes an envelope and creates no authority (`cli.py:407-435`). `run_command` requires direct `--confirm` and forwards to `launch_run` (`cli.py:2433-2559`). | Extend the interface additively for external start/checkpoint/submit/inspect. Agent payloads may identify records and bounded artifacts, never self-consent, raw private context, or hidden transcript. |
| Run Plans | `run-plan create/list/show` call `create_run_plan`, `list_run_plans`, and `read_run_plan` (`cli.py:2329-2431`). | Add graph selection/Graph Set references and Scout record IDs as explicit inputs. Preserve the v1 command and reader for legacy plans. |
| Inspection | `_read_projection` resolves a workpad and rebuilds/reads the journal projection (`cli.py:2846-2857`). `status`, `show`, `history`, `plan`, `check`, and `open` are projections/read-only or declared-location operations (`cli.py:2913-3269`). | Add instance/Graph Set/external-work projections behind these read paths. They must read committed journal/validated records; they must not make a disposable projection a new authority. |
| Listing | `gigs_command` calls `list_gigs` and can list one or all registered projects (`cli.py:2913-2975`). | Extend listing with instance/template metadata only after a private instance is bound; preserve ordinary Gig listing and safe diagnostics. |

### Package and catalog seams

`package.py` defines the portable root as `.gigai/packages/<package-id>` and
the private paths ignored by Git (`package.py:39-50`). `inspect_package`
rejects symlinks, hooks, executable files, oversized files, inventory drift,
and digest mismatch before returning a `PackageInspection`
(`package.py:138-212`). This is the correct validator for a shipped template;
private profile, source, conversation, Run, and application records must not be
placed beneath this package.

`initialize_project_package` binds the target and then either adopts exactly
one existing package or creates one random empty package
(`package.py:253-367`). It has no template/instance concept. `install_package`
copies one validated package and writes a private installation record, but it
does not import lifecycle authority (`package.py:370-427`). `materialize_catalog_package`
has the same one-root assumption and refuses a different existing root
(`catalog.py:207-234`). These are safe reusable seams, but not sufficient for
Scout named initialization on a project that already owns an unrelated package.

The current catalog is an in-source tuple of three generic entries
(`catalog.py:136-158`), with deterministic package identity based on
`catalog_id@definition_version` (`catalog.py:25-81`). Its package bytes are
deterministic and validated without providers (`catalog.py:172-204`). Scout can
reuse this shape and identity algebra, but its graph descriptors and instance
binding must be added as explicit portable content/provenance, not encoded in
private records.

### Existing lifecycle and identity seams

`create_offline` resolves the bound project, recovers or allocates a `gig_id`,
provisions a workpad, records `creation_started`, selects the active workpad,
and writes a proposal (`lifecycle.py:1301-1441`). If `model_output` is absent,
it loads configuration, resolves the model adapter, and invokes it
(`lifecycle.py:1389-1400`). The proposal artifact builder generates a fresh
UUID graph identity and UUID goal/edge identities
(`lifecycle.py:1901-2063`); the current `goal-graph.schema.json` requires this
UUID-form `graph_id` (`goal-graph.schema.json:7-27`). This is the existing
single-goal-graph identity and must not be replaced by a semantic Scout selector.

`approve_offline` validates the proposal, creates the approval/tag commit, and
publishes `manifests/active-gig-version.json` through a journal transition
(`lifecycle.py:1444-1550`). The pointer schema binds `gig_id`, numeric
`active_version`, one `goal_graph` artifact reference, journal commit/tag, and
operator approval (`active-gig-version.schema.json:7-29`). Approval therefore
remains the authority for a sealed Graph Set as well as for legacy one-graph
versions; no per-graph active pointer should be introduced.

`launch_run` resolves the workpad, optionally reads a sealed Run Plan before
the journal projection, resolves approved authority, validates the plan, and
only then allocates a Run ID/directory (`run.py:77-173`). It records
`run_started`, can materialize provider-review artifacts, and starts the
existing deterministic worker (`run.py:175-263`). The Run manifest v1 stores
one `goal_graph` artifact, numeric `gig_version`, sealed sources, effects,
budget, and input digest (`run-manifest.schema.json:7-67`). It is not an
external-agent protocol and should not be made to pretend an imported agent
conversation was a GigAI provider invocation.

`run_plan.py` currently creates a four-phase review plan over explicit file
inputs, resolves the active approved version, seals the UUID Goal Graph and
review contract, derives the deterministic Run Plan ID, and journals
`run_plan_sealed` (`run_plan.py:865-1073`). `read_run_plan` validates bytes,
project/Gig ownership, typed inputs, and sealed source digests before returning
the plan (`run_plan.py:803-846`). This is the right place to add an additive
Graph Set/selected-graph validation hook, but its existing v1 fields and
identity projection must remain readable.

### Resolution, journal, registry, and portability

`resolve_workpad` selects by canonical UUID `gig_id`, then falls back to the
Git binding's `active_gig_id` or registry `active_workpads`
(`workpad.py:227-270`). The registry schema has one `workpads` row per
`gig_id`, one project binding, and one active workpad per project
(`registry.py:24-48`). This supports multiple private Scout instances as
multiple registered Gigs, but the current resolver cannot select by a semantic
template/instance alias.

`read_index` is explicitly a disposable SQLite projection rebuilt from the
committed journal (`index.py:49-57`, `index.py:129-145`). Journal replay
requires one handoff per commit, contiguous sequence, and matching project/Gig
identity (`index.py:60-125`). `record_transition` is the writer lock and
commit boundary, with existing transitions including `run_plan_sealed`,
`run_started`, `run_succeeded`, `run_failed`, and `run_interrupted`
(`journal.py:36-100`, `journal.py:155-179`). New instance/external-work events
must be additive transition names and remain subject to this same journal
authority.

`verify_active_version_portability` verifies that the live active pointer is
the uniquely published journal child and that any capability manifest is
valid (`portability.py:51-93`). Proposal lineage is resolved from ancestors of
the sealed commit (`portability.py:96-124`). Scout package/template portability
should layer on this check; it must not declare private instance records,
profiles, source text, Runs, or credentials portable.

## Identity distinction that SCOUT-00 must freeze

There are three different identities; they must not share a field merely for
convenience:

1. **Legacy/Goal-Graph identity:** `goal-graph.json.graph_id` is a canonical
   `graph_<uuidv4>` validated through `common.schema.json` and
   `goal-graph.schema.json` (`common.schema.json:26-29`; `goal-graph.schema.json:21-27`).
   Existing proposals, active pointers, Run manifests, comparisons, and
   historical journal front matter use its digest/reference.
2. **Scout semantic selector:** names such as `research-role`, `find-jobs`, and
   `tailor-application` are stable human/routing selectors. G43.2 describes
   these as Graph Set descriptor `graph_id` values matching a lowercase slug,
   which conflicts with the existing common definition. The compatible choice
   is to call the new field `graph_selector` (or `graph_key`) and retain
   `goal_graph_ref`/`goal_graph_uuid` for the existing artifact identity. If
   G43.2's field name is contractually fixed as `graph_id`, the new schema must
   explicitly scope that meaning to the Graph Set descriptor and must never
   pass it into legacy `validate_entity_id`; a dual-named compatibility reader
   is required.
3. **Graph Set/version identity:** one content-addressed Graph Set belongs to
   one approved `gig_id` and numeric Gig version. Adding/removing/changing a
   descriptor or referenced artifact creates a new proposal/version. There is
   no graph-local approval, active pointer, or version counter.

Recommended new selection record fields are the G43.2 fields plus an explicit
`selected_graph_selector`, `selected_goal_graph_ref`, and Graph Set digest.
`operator_explicit`, `g44_routed`, and `only_member_default` remain the only
selection kinds. A selector alone is evidence, not Run authority; the sealed
Plan must bind the Graph Set digest, selector, selected descriptor, selected
UUID Goal Graph reference, and selection-record digest.

## Minimal compatible implementation architecture

### A. Graph Set selection and Run binding (SCOUT-02)

Add a versioned strict `gig-graph-set` schema/resource and a strict
`graph-selection-record` schema/resource, plus semantic validators. Store the
Graph Set as an approved-version artifact (for example,
`manifests/graph-set.json`) and retain the existing
`manifests/goal-graph.json` as the legacy one-graph source. For a legacy v1
record, readers synthesize an in-memory one-member Graph Set whose selector is
derived only for display; they do not write the projection back.

Add a Graph Set resolver used by `run_plan.create_run_plan`, `run_plan.read_run_plan`,
`run.launch_run`, and `occurrence`/comparison readers:

```text
approved Gig version
  -> Graph Set ref + digest
  -> explicit semantic selector or only-member default
  -> selected descriptor
  -> selected legacy-compatible Goal Graph ref + digest
  -> sealed Plan
  -> existing direct-confirmed Run authority
```

The resolver must reject an absent selector when the set has multiple graphs,
unknown aliases/selectors, descriptor/Goal-Graph mismatch, policy widening,
foreign Graph Set, and any Plan whose selected selector or digest differs from
the approved version. It must not inspect request prose or silently rerun G44.

Extend the Run Plan v2/additive path (or add a new graph-aware plan schema)
with Graph Set and selection references. Keep `run-plan.schema.json` v1 and
its deterministic identity projection unchanged for historical plans. A new
Plan identity includes Graph Set digest, semantic selector, selected Goal Graph
digest, selection-record digest, explicit record-input digests, and all policy
fields; timestamps remain excluded.

### B. Named `init jsl` and private instance binding (SCOUT-05)

Add one catalog/template descriptor for `job-search-lifecycle` with alias
`jsl`; its package contains only deterministic definition, Graph Set,
contracts, evaluation fixtures, and provenance. Reuse `catalog.py` validation
and `package.py.inspect_package` before any target mutation.

Use an idempotent named-init coordinator with these branches:

```text
resolve target/project binding
  -> inspect existing package roots (without replacing one)
  -> validate built-in Scout template bytes
  -> choose package-backed or built-in-provenance-backed instance
  -> allocate/recover private instance + Gig workpad
  -> journal instance binding and selected initial state
  -> return template/package/instance/Gig IDs and next approval/setup state
```

* Fresh unbound target: materialize the validated Scout package, establish the
  G41 binding, and create the private Scout instance. The operation may reuse
  the explicit catalog-install/adopt sequence internally only if it preserves
  atomic refusal and does not create approval or Run authority.
* Already initialized target with the same Scout package: return the existing
  instance/package identity and reopen it; no new records or package bytes.
* Already initialized target with an unrelated or empty package: leave that
  package and all its bytes untouched. Bind a private Scout instance to
  `builtin:v0.1.7` template provenance (or return a typed
  `template_package_conflict` if the product requires package-backed Scout). Do
  not delete, merge, overwrite, or create a second `.gigai/packages` root.
* Existing unrelated active Gig: do not replace its `active_gig_id` merely to
  open Scout. Resolve named Scout commands through the instance binding; only an
  explicit user action may change the legacy active workpad. On a fresh target
  with no active Gig, selecting the new Scout instance as active is safe and
  should be explicit in the result.

The private instance record should bind `instance_id`, `project_id`,
`gig_id`, template catalog ID/version/source digest, package ID/digest when
present, instance state, customization lineage, and creation/update evidence.
Store it under the authoritative private workpad/home, not the portable
package. A small registry extension may index `instance_id -> gig_id`, but the
record and journal remain the source of truth; do not make a disposable index
or project TOML alias authoritative.

The named command must not call `create_offline`, `start_interview`,
`resolve_model_adapter`, or any provider. It creates the minimum deterministic
definition/proposal/approval prerequisite required by the template contract,
or reports that operator approval is still required. It never silently
approves the template or launches research.

### C. Private profile, source, and artifact records (SCOUT-03)

Add a private-record module with strict schema readers/writers and exact-byte
digest checks. The minimum record families are:

```text
instances/instance_<uuidv4>/instance.json
profiles/profile_<uuidv4>/profile.json
references/ref_<uuidv4>/reference.json + source.txt
run-inputs/input_<uuidv4>/input.json + source.txt
artifacts/artifact_<uuidv4>/artifact.json + content payload
```

The G45 reference/input shapes are the immediate source contract:
`private-reference:1` and `run-input:1`, immutable `sealed` records, bounded
UTF-8 text, project binding, exact snapshot references, imported-byte digests,
and no absolute source path (`G45:93-144`). Reuse that model for Scout profile,
experience-answer, research-source, application, and interview artifacts,
with kind-specific schemas rather than one permissive JSON blob.

Rules:

- Imported bytes are immutable; equivalent `(project, kind, privacy,
  media-type, digest)` imports are idempotent, changed bytes create a new
  record, and original source files are never rewritten.
- Every Run input names exact record/artifact IDs and digests. A current-profile
  projection may resolve the latest selected revision, but a Plan stores the
  resolved revision IDs so profile edits cannot alter historical Runs.
- `private_sensitive` data never enters package/catalog export, public G44
  selection evidence, unrestricted G43.3 briefs, or redacted diagnostics.
- Source status is explicit: `recorded` means bytes were captured,
  `agent_assessed` means an external agent supplied an assessment, and
  `independently_verified` requires a separate GigAI verification record.
  A URL/source label or imported agent result is never by itself `verified`.
- Record creation and terminal decisions are journaled as evidence transitions,
  while the record bytes remain immutable data, not approval/active/Run
  authority. All writes use the existing journal writer lock.

### D. External-agent start/checkpoint/submit/inspect (SCOUT-04)

Do not overload `launch_run` or Run manifest v1. That function resolves a
legacy approved Goal Graph and starts a local multiprocessing worker; using it
for imported agent activity would misstate executor, usage, and provider
accounting. Add a bounded external-work module with an additive schema and
reader/writer, for example:

```text
external-work/work_<uuidv4>/work.json
runs/<run_id>/external/manifest.json
runs/<run_id>/external/checkpoints/checkpoint_<uuidv4>.json
runs/<run_id>/outputs/<validated artifact paths>
```

The external-work record must bind project, instance/Gig, approved numeric
version, Graph Set digest, semantic selector, selected Goal Graph digest,
explicit input record IDs/digests, external actor/session identity, and
disclosure policy. It must state that GigAI did not invoke a model and that
unobserved external tool/cost activity remains unknown. Use a new execution
kind/schema or a v2 Run manifest; preserve Run v1 bytes and readers.

Proposed operations:

| Operation | Required behavior | Authority/effect boundary |
|---|---|---|
| `external start` | Validate selected graph, exact input records, output contract, and target/project binding; allocate one external work/Run identity only after direct operator authorization; write `started` manifest and journal transition. | No model discovery, adapter resolution, provider invocation, network, target write, or hidden transcript capture. Agent identity is provenance, not operator consent. |
| `external checkpoint` | Accept bounded question/partial-result metadata and explicit artifact IDs or bytes; validate safe paths/digests; append an immutable checkpoint with sequence and parent work digest. | Does not complete the work or change profile/approval state. Repeated identical checkpoint is idempotent; conflicting sequence/digest refuses. |
| `external submit` | Require all graph-declared outputs; validate schemas, content digests, source references, privacy, and output completeness; write a single immutable submission and terminal outcome. | Duplicate identical submission returns the existing result; different duplicate or missing output fails closed. Never marks an application submitted or provider call observed. |
| `external inspect` / `run-details` | Read and validate work, checkpoints, outputs, disclosure, and terminal state; expose IDs/digests/status and safe labels. | Read-only; does not repair journal or mutate state. `status/show/history/check/open` may project it through existing read seams. |

For agent envelopes, extend `invocation.py` only with allowlisted bounded
commands/inputs (or use a new external protocol version) and preserve v1
parsing. Current `ALLOWED_COMMANDS` is only `setup/models/doctor/create/run`
and command-specific input fields are strict (`invocation.py:13-40`). The
minimal external payload should carry `instance_id`, `work_id` or
`run_plan_id`, `graph_selector`, `input_ids`, requested output kinds, and
artifact handles—not source text, credentials, transcript, hidden prompt, or
agent-supplied consent. If `external start` allocates an effectful work
identity, reject agent consent just as the current Run path rejects it
(`invocation.py:126-129`); require a direct operator confirmation outside the
agent envelope.

Codex/Claude discoverability should be static shipped instructions and CLI
help/examples. The fixture path should invoke only the external CLI protocol
with synthetic data and monkeypatch/assert that no model adapter or provider
process is called.

### E. Inspection and portability integration (SCOUT-11)

Keep `read_index`/journal replay as authority. Extend `JournalProjection` or
add a read-only `instance_projection` that joins committed instance/work
records by project/Gig, then have:

- `gigs`: show Scout instance/template alias and current state without exposing
  source text;
- `status`/`show`: show selected instance, Graph Set digest/selectors, current
  profile revision, and external-work terminal summary;
- `plan`: render the Graph Set and selected descriptor, while retaining the
  legacy Goal Graph projection;
- `run-plan show/list`: display graph selector and exact input-record IDs;
- `run-details`: discriminate `gigai_managed` versus `external_agent`, with
  unknown external usage/tool activity explicit;
- `check`: validate instance, records, Graph Set, output manifests, and journal
  references without rewriting them;
- `history`: include instance/work transitions in sequence order;
- `open`: open only declared private workpad/target locations.

Package `inspect/export/install` must validate only portable template bytes and
provenance. `verify_active_version_portability` should gain an additive
Graph Set/template provenance check for new versions, while retaining the
legacy pointer/tag/capability behavior and `reported_non_portable` outcome for
legacy records without portable capability evidence. Private backup/restore is
a separate operation with destination checks; catalog export must never include
instance/profile/source/Run data or credentials.

## Coupling risks and required mitigations

1. **`graph_id` name collision:** existing schemas and validators require a
   UUID; G43.2 prose names semantic slugs. Introduce a separate selector field
   or an explicit schema-context adapter. Never loosen `common.schema.json` or
   make old `goal-graph.json` accept slugs.
2. **One package per project:** `materialize_catalog_package` and
   `initialize_project_package` refuse a second root. A named init that blindly
   calls catalog install will break already initialized projects. Use
   package-backed instance when compatible and built-in-provenance-backed
   private instance when not; freeze this behavior in SCOUT-00.
3. **Active-workpad replacement:** `select_active_workpad` updates both Git
   project binding and registry (`workpad.py:177-206`). Named init must not
   silently steal an unrelated active Gig; add an instance selector/resolver.
4. **`create_offline` model/default path:** it defaults to `offline-default`
   and invokes an adapter without supplied output (`lifecycle.py:1301-1400`).
   Scout init and external start must not use this path or inherit its fixture
   identity.
5. **Run authority and external semantics:** `launch_run` allocates a Run
   after validating approved authority and then starts a local process. External
   imported work requires a separate executor/disclosure schema and terminal
   transitions; do not claim GigAI-started model usage for agent work.
6. **Journal/index split:** `state.sqlite` is disposable; adding records only
   to the index would lose authority on rebuild. Journal every state transition
   and rebuild projections from committed bytes.
7. **Strict schema inventory:** new schemas must be added to
   `validators.SCHEMA_NAMES` (`validators.py:28-66`), packaged resources,
   `schemas/SHA256SUMS`, and `schemas/README.md`; validators and semantic
   checks must be synchronized. Existing unknown-field tests are a required
   regression seam.
8. **Canonical IDs:** use `canonical.derive_deterministic_id` for content
   identities and `generate_entity_id` only for new UUID entities
   (`canonical.py:255-365`). Do not add local hash/UUID algebra in product
   modules.
9. **Privacy disclosure:** existing JSON projections deliberately redact paths
   and invocation rejects transcript/credential/hidden-prompt fields. New
   records and output manifests must preserve this boundary; no raw résumé,
   job description, prompt, or absolute home path in package/evidence output.
10. **Deferred broker scope:** G43.3/S43.3/G45.2 remain separate brokered
    research contracts. Scout external-agent recording can accept agent-supplied
    research results as unverified evidence, but must not implement or imply a
    GigAI-owned search engine, Exa broker, scheduler, or complete accounting.

## Acceptance and test seams

No provider calls are needed for SCOUT-00 fixtures. Add focused tests at the
following existing seams, then run installed-artifact checks before any live
UAT:

### Graph Set and compatibility

- Extend `tests/test_g43_run_plan.py` for two independent selectors, alias
  resolution, missing/invalid selection, descriptor/Goal Graph mismatch,
  selection-record kinds, Graph Set digest pinning, and Run manifest binding.
- Add legacy fixture tests using the current UUID Goal Graph and v1 proposal,
  active pointer, Run Plan, Run manifest, comparison, and journal bytes;
  assert readers synthesize a one-member view without writing bytes or changing
  `graph_id`.
- Cover repeated Plan creation identity, stale source/Graph Set digests,
  symlink/traversal paths, unknown schema fields, and graph policy widening.

### Named initialization and package boundary

- Extend `tests/test_g42_catalog.py` for `init jsl` fresh target, same-package
  rerun, empty-package target, unrelated-package target, package conflict,
  invalid template bytes, and interrupted/recovered initialization.
- Extend `tests/test_g41_package_boundary.py` to assert unrelated package bytes,
  `.git/info/exclude`, binding, registry rows, and existing active Gig remain
  unchanged on the conflict/private-fallback path.
- Assert named aliases resolve identically; bare `init` still creates/reconciles
  workspace only; `create` still requires an explicit invocation and does not
  become template installation.

### Private records and disclosure

- Add synthetic G45/Scout record tests for exact imported-byte digest, idempotent
  equivalent import, new revision on changed bytes, source preservation,
  project mismatch, malformed ID, traversal/symlink, binary/UTF-8/size limits,
  and no raw-text/absolute-path JSON leakage.
- Assert package inspection/export/catalog installation never includes private
  record roots. Assert unselected records cannot enter a Plan or external
  brief.
- Verify `recorded`, `agent_assessed`, `independently_verified`, and unknown
  freshness/cost labels remain distinct in human/JSON projections.

### External work without model invocation

- Add synthetic start/checkpoint/submit/inspect tests for both `codex` and
  `claude` actor envelopes using no provider configuration and a monkeypatch
  that fails if `resolve_model_adapter`, `resolve_model_target`, provider
  adapters, or model processes are called.
- Start requires exact selected graph/input/output contracts and direct
  operator authorization; agent self-consent is rejected.
- Checkpoint survives interruption and is idempotent; conflicting sequence or
  digest refuses. Partial artifacts remain inspectable but cannot complete.
- Submit rejects missing/invalid/foreign/private-output leakage, accepts one
  complete synthetic output set, and repeats identically without duplicate
  terminal records. Imported activity remains explicitly external and usage or
  tool costs stay unknown when unobserved.
- Rebuild the disposable index from the journal and assert all instance/work
  state survives. `run-details`, `status`, `show`, `history`, and `check` must
  remain read-only and path-safe.

### Portability and installed release

- Validate the shipped wheel/package rather than importing the source checkout.
- Test package/template relocation and second-home installation without private
  state or credentials; test explicit private backup/restore separately if it
  is claimed.
- Verify active-version/tag/pointer lineage for legacy and Graph Set versions;
  ambiguous/missing publication remains a typed refusal.
- Keep G43.1 subject/baseline snapshots and the approved Gig unchanged; any
  Scout review uses separately identified inputs and evidence.

## SCOUT-00 contract decisions to freeze before implementation

1. New schema field name for semantic selector (`graph_selector` recommended)
   and the mapping to legacy UUID `goal_graph.graph_id`.
2. Whether fresh named init internally performs package materialization/adoption
   or exposes the two-step bootstrap; in either case, no implicit approval or
   Run.
3. Conflict behavior for an existing unrelated/empty package. This audit
   recommends preserving it and creating a private built-in-provenance-backed
   instance; a package-backed-only product must instead return a typed refusal.
4. Whether a named Scout instance may become the project's legacy active workpad
   when another Gig is active. This audit recommends no silent replacement.
5. External work identity: separate `work_id` plus optional Run ID, or an
   additive external Run manifest version. Do not retrofit Run manifest v1.
6. Exact direct-operator authorization for external start and whether it is
   represented by existing Run consent or a new external-work consent record;
   never accept agent-supplied consent.
7. Minimum output contracts for each initial graph, including Markdown/JSON
   only unless a separately verified renderer/parser exists; ATS checks must
   report concrete format issues and limits, never a predicted employer score.
8. Application state transitions and correction semantics: only explicit user
   actions append `saved`, `rejected`, `applied`, `interviewing`, or `closed`;
   tailoring, research, or interview practice never infers `applied`.
9. Template customization/update lineage: compare and adopt/defer explicitly;
   never overwrite private edits, historical Graph Sets, or prior Run inputs.
10. Schema inventory/checksum/installed-wheel acceptance ownership and the
    exact sanitized evidence path for SCOUT-12.

## Existing goal constraints carried forward

This audit applies the approved roadmap and the relevant G41/G42/G43.2/G44/G45
contracts. In particular: G41 owns the portable/private package boundary;
G42 catalog bytes are not approval or Run authority; G43 keeps explicit bounded
profiles and direct consent; G43.2 preserves historical v1 records and makes
Graph Set selection evidence distinct from Run authority; G44 routing cannot
invoke a provider or become Run authority; G45 references/inputs are immutable
private snapshots with exact digest-pinned Plan inputs. G43.3/S43.3/G45.2
brokered research remains deferred and must not be smuggled into the external
agent recording path.
