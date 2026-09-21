# Scout — User-owned Gig workspace amendment

**Status:** Accepted for SCOUT-00 workspace-amendment contract scope in the
operator-supplied independent re-review on 2026-09-08; not implemented.  
**Date:** 2026-09-07; revised 2026-09-08.  
**Authorization:** The operator requested the review corrections and confirmed
that general initialization collects a username and instantiates all bundled
default Gigs. This is documentation authority, not runtime acceptance.  
**Related:** [Roadmap](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md),
[earlier lifecycle contract](SCOUT-00-contract-amendments.md),
[execution ledger](execution-ledger.md).

The [review response and re-review verdict](SCOUT-00-workspace-review-response.md)
record resolution of all five findings with no remaining implementation-blocking
contract issue. The original finding 4 recommendation to require
`init scout` is superseded by the operator's later decision to use `gigai init`.
This candidate replaces the conflicting initialization, storage, executable
source, and UI clauses of the earlier design; unaffected lifecycle invariants
remain in force. Do not treat the older examples as a second current interface.

## 1. Review and naming boundary

Scout is the product name for the previously named Job Search Lifecycle (JSL).
`SCOUT-00` through `SCOUT-12` are documentation aliases for `JSL-00` through
`JSL-12`, not newly completed work. Historical G-goal IDs, runtime graph IDs,
Orca tasks, Runs, test module names, executed commands, and artifact hashes
remain unchanged. Renamed reports retain their original verdict and scope.

The prior independent review accepted the earlier lifecycle contracts. It did
not review this per-Gig software layout, SQLite ownership, local CRUD tooling,
HTML tracker, or personal clone naming. The separate 2026-09-08 re-review now
accepts the revised workspace scope. Strict schemas, caller changes, migrations
and regression cases remain implementation evidence obligations, not unresolved
design choices. The original G43.1 public review subject and requirements
baseline are not changed by this amendment.

## 2. A Gig is user-owned software

Scout ships as a useful starting application that becomes the user's private
instance. Its code, goal graphs, data, references, documents, interface, and
history can evolve together. The underlying Gig model remains generic: this
is not a central job-search application with user settings bolted onto it.

Canonical new-layout contract for implementation, not permission to move
existing workpads during this documentation change:

```text
<workpad-root>/projects/<project-id>/gigs/<gig-id>/
  README.md                       how an agent/user operates this Gig
  CHANGELOG.md                    user-owned software changes
  gig.py                          local command entry point
  tools/                          this Gig's Python tools, as needed
  goalgraphs/                     editable graph-definition working copies
  state.sqlite                    existing per-Gig DB; Scout read projections
  docs/                           research, drafts, prep, handoff documents
  references/                     explicitly imported supporting material
  ui/
    template.html                 editable report template, not generated output
    style.css                     locally shipped styling, customizable
  reports/scout/                  generated report bundles + current selector
  runs/                           exact Run evidence and output references
  records/                        authoritative typed record revisions
  manifests/                      approved graph/software snapshots and bindings
  handoffs/                       authoritative journal transitions
  scratch/                        drafts and temporary publication state
```

`docs/` is the agreed name, not `documents/`. Generated docs, sources, and
handoffs identify their originating graph/Run and exact related artifacts.
Indexes provide backlinks for discovery without changing old evidence when
a new relationship is added. Distinguish software changes in `CHANGELOG.md`
from application events and Run outcomes; none substitutes for another.

### Canonical ownership, Git behavior, and transfer map (finding 2)

The physical registry-resolved root does not change. Do not create a second
project-local `.gigai/gigs/` tree. Private journal tracking below means the
Gig's existing private Git repository, never the user's source repository or
a public remote. Sharing a clean template and transferring private state are
different operations.

| Path relative to Gig root | Ownership / authority | Private Git and publication | Transfer and update behavior |
|---|---|---|---|
| `README.md`, `CHANGELOG.md` | User-customizable software working copies, initially package-derived; accepted bytes are inventoried under `manifests/software/<version>/` | Ignored working copies; accepted versions journaled under `manifests/` | Clean export uses selected inventoried source; private transfer also preserves marked draft edits; template updates require explicit comparison/adoption |
| `gig.py`, `tools/` | Gig-owned domain commands/transforms; approved executable source inventory is authority | Ignored editable copies; exact accepted source snapshots and digests journaled; changed copies cannot claim approved execution | Own copies per Gig; no shared mutable domain implementation; update preserves drafts or refuses conflicts |
| `goalgraphs/` | Editable authoring copies and navigation, not independent runtime authority | Ignored; approval validates copies and journals exact graph-set/member artifacts referenced from `manifests/` | Definition export includes selected approved graph artifacts; private transfer retains separately marked drafts; never changes old graph IDs/versions |
| `manifests/` | Authoritative layout/owner/template bindings, approved graph snapshots, source inventories and software snapshots | Tracked; only validated journal publication; inventory pins exact member paths/digests | Preserve original authority as history; import grants no new approval or capability; update appends a version |
| `records/` | Authoritative typed revisions, relationships, archive/tombstone events and operation receipts | Tracked, append-only journaled publication under the writer lock | Private only; preserve history and original input references; no in-place rewrite by template update |
| `docs/<record-id>/<revision-id>/` | Authoritative native document bytes and sidecars referenced by records | Tracked and journaled with exact content digest; new draft/submission produces a new revision | Private only; new native document writers use this path, not another duplicate blob; immutable historical paths stay valid |
| `references/`, `run-inputs/` | Canonical G45 imported source/record families | Tracked and journaled at the existing specified G45 paths; wrappers reference, never duplicate, their bytes | Private transfer only; template update cannot replace imported material |
| `runs/`, `run-plans/`, `review-inputs/`, `handoffs/` | Existing execution, approval/input and journal evidence | Existing tracked, validated publication; retain exact IDs/paths/bytes | Preserve history; do not import fresh execution consent or rewrite old links |
| `ui/template.html`, `ui/style.css`, inventoried local assets | User-customizable source, initially package-derived | Ignored working copies; approved snapshots journaled under `manifests/software/<version>/ui/` | Export source, not personalized rendered data; preserve user edits on update |
| `reports/scout/` | Rebuildable read-only HTML/assets and current-report selector | Ignored only in this new namespaced subtree; transactional report-bundle publication, no custom source written here | Omit/rebuild on private transfer; retained reports are explicitly stale until regenerated; never export as clean template data |
| `state.sqlite` | Existing shared database; Scout-managed tables are wholly rebuildable journal projections | Ignored, sole DB file for this Gig; validated projection service is sole Scout-table writer | Rebuild Scout tables; consistently preserve existing G22 trace as described below; no `gig.sqlite` is introduced |
| `scratch/`, `indexes/context.json` | Disposable drafts/temporary state and metadata projection | Ignored; bounded safe paths; published artifacts live elsewhere | No implicit draft loss; private transfer must select drafts explicitly, omits locks/temp files and rebuilds indexes |
| `.git/`, `.gitignore` | Existing private journal and versioned layout control | `.gitignore` tracked with exact layout-specific bytes; never editable as a policy override | Use validated private transfer rather than copying live locks/config/hooks; never run imported hooks |

Introduce an explicit, strict `workpad-layout:2` manifest under `manifests/`
with project/Gig identity and the layout-policy version. A journaled layout
transition binds that marker and the exact ignore policy. Validators first
check root/manifest safety without following links, then validate the marker
against committed journal history before admitting any additional roots.
Missing markers use the unchanged v1 allowlist/ignore contract; unknown or
forged layouts refuse. Never accept a writable directory just because it is
in this table: validate all path components and inventoried content.

The v2 ignore policy retains the existing `/objects/`, `/scratch/`, and
`/state.sqlite` entries and adds the exact root working-copy paths in the
table, `/indexes/`, `/reports/scout/`, and SQLite's bounded transient sibling
names (`/state.sqlite-journal`, `/state.sqlite-wal`, `/state.sqlite-shm`). Do not
ignore all `reports/`, `manifests/`, `records/`, or `docs/`. Ignore rules are not
trust: command entry, inventory checks and export validate ignored source.

New instances use v2. Existing v1 workpads remain unchanged unless an explicit
layout migration is selected. Migration journals the marker/ignore change,
preserves tracked legacy paths and dirty user files, and refuses collisions
instead of relocating them silently. Older serializers/readers retain v1
support; do not promise older binaries can write v2. New native document
records reference `docs/`; non-document native blobs retain the earlier record
store and G45 paths remain canonical. No duplicate content authority is added.

## 3. Included template and personal instances

### General initialization, not one clone command per Gig (finding 4)

The new v0.1.7 entry is **`gigai init`**. It obtains an explicit username,
initializes the selected project/workspace, and creates the user's private
default instance of every eligible bundled default template, including Scout.
Do not require `init scout`, a `clone` command, personal suffixes, or manual
template-by-template setup. Those command variants are not part of this
v0.1.7 interface. Earlier `jsl`/`job-search-lifecycle` names remain only in
historical design/evidence and existing test identifiers.

For agent/non-interactive use, add `gigai init --username NAME --json`, retaining
existing target/home options. Interactive first init asks for the username;
non-interactive init without a saved/provided username returns a typed
`username_required` result before package/instance writes. A saved username
is reused on subsequent init. Usernames are display metadata (1–64 Unicode
code points after trimming, no control characters), not paths, authentication, global
uniqueness, or hashed Gig identity. Never infer one from the OS account.

Persist the username and a generated stable owner ID in a private
`workspace_owners` registry row keyed by project ID, included in the pending
registry-v3 migration alongside `template_instances`. This row is local owner
metadata, never approval authority. Each journaled template-instance binding
includes an exact typed snapshot of owner ID/project ID/username. The init
intent pins the owner row payload and its digest before the first instance
is published; retries verify it, and conflicting rows/snapshots refuse.
The owner ID is independent of the display username. Template binding rows
remain caches of their journaled bindings, not approval receipts.
Do not place the username in portable `.gigai/packages/` content. A different
provided username on an initialized workspace returns `workspace_owner_conflict`;
owner rename/transfer is a separate later operation, not a new set of copies.

Instance uniqueness remains `(project_id, canonical_template_id, default)`
with a full canonical Gig ID. The canonical template selector is `scout`.
All instances belong to the saved workspace owner; names may simply be Scout
or another template's display name. No `scout-kar46` naming/collision feature
is required. Repeated init returns the same instances and their current state.

"All defaults" means the explicit versioned default-template inventory shipped
in the installed GigAI package, not remote catalogs, arbitrary local folders,
historical fixture Gigs, or every package already present in the project.
Only complete release-eligible templates are included in that inventory.
Instantiation copies safe inventoried source and data schemas; it never runs
copied Python, starts a workflow, imports personal evidence, probes providers,
or grants Gig/Run/capability approval. Return each prepared proposal's approval
state and exact next action. Package installation itself creates no instances.

Preflight username/configuration, the complete default inventory, unrelated
sibling paths and destination ownership before publication. Extend the
previous B3 init intent to a batch pinned to owner binding, inventory digest,
ordered template IDs/digests and each reserved Gig ID. Reuse the same locking
order and per-instance stages. Prompt for username before acquiring locks.
Interrupted init reports partial progress and resumes the pinned batch; it
must not duplicate already bound instances or silently switch inventory on
retry. Resume/reconcile an older incomplete batch before starting a new one.

A fresh explicit init after a package upgrade adds newly bundled defaults
but preserves existing instances, software, data and active-Gig selection.
Changed upstream templates are reported as available updates, never silently
adopted. Removing a default from a later inventory does not delete its private
instance. Failure remains visible per template; no all-ready result while any
default is unbound. Existing empty/custom packages survive. Custom `create`
remains available, and existing `--adopt-package --confirm` authority is not
weakened by default instantiation.

## 4. Per-Gig Python tools and SQLite

Ship small local Python tools with each newly created Gig, including Scout.
The agent is the caller. Required capabilities are local CRUD, structured Run
result import, inspection, and HTML report generation. A common scaffold can
produce the starting files, but the copies belong to each Gig and can diverge.
Do not require a shared mutable domain-tool implementation or HTTP CRUD API.

Illustrative interface only, to be finalized with input/output contracts:

```text
python gig.py jobs add ...
python gig.py applications mark-applied ...
python gig.py import-run ...
python gig.py report
```

Use one SQLite database per Gig for v0.1.7. No Dolt installation, Parquet
requirement, deployed backend, or general scheduler is part of this addition.
Tools should have explicit schemas/migrations, bounded input validation,
predictable machine-readable results, safe retries, and useful errors. A Run
result DTO references exact structured outputs; it is not a request to scrape
arbitrary prose or trust a claimed successful Run.

### Journal authority and a single rebuildable Scout view (finding 1)

Retain the private Git journal and typed record revisions as the sole Scout
data authority. Reuse **`state.sqlite`**; do not add `gig.sqlite`. SQLite stores
queryable projections only for Scout, never a second independently editable
copy of application facts. Create appends a typed record; update appends a
revision with a checked parent; delete appends an archive/tombstone. Archived
records are hidden by default but remain available for history and exact Run
inputs. Hard deletion/privacy erasure is not promised by v0.1.7 CRUD.

The validated operation service checks scope, approved contracts, exact input
refs, actor/tool provenance and operation key under the workpad writer lock,
then journals the authoritative record/event and receipt. After commit, the
projection service updates the affected tables transactionally. Matching-key
retry returns the original receipt; changed payload conflicts. A projection
failure does not roll back or duplicate a committed application event: return
`committed` with `projection_pending` and a rebuild next action. Rebuilds
consume committed records, never uncommitted files or dashboard prose.

Extend the existing index layer with a closed versioned set of Scout-managed
tables and a projection cursor containing journal HEAD and schema version.
The rebuild transaction replaces only those managed tables, recreates them
from validated committed records, and advances its cursor only on success.
Readers compare cursor to authoritative HEAD and repair or report staleness;
no silent use of outdated application state for a new Plan. No arbitrary
custom SQL table is authoritative. Schema changes are declarative inventoried
Gig source, validated before a new version is approved; unsupported schema
versions refuse instead of executing supplied migration SQL.

**Existing database compatibility:** `index._write_projection` currently
replaces the database file while preserving the recognized G22
`interview_events` table. Scout's extension must coordinate index rebuilds and
G22 trace writers with a common database lock, retain the existing projection
contract, and never drop that trace or erase Scout tables during a legacy read.
The entire old file cannot be called disposable merely because Scout's tables
are rebuildable. Preserve legacy trace data on transfer; reconstruct it only
from validated existing interview snapshots where complete evidence exists.
Fail visibly on malformed/unknown tables or incomplete trace recovery rather
than discarding them. Test both legacy-first and Scout-first reads/rebuilds
and concurrent trace activity. New Scout data never depends on that trace as
its sole authority.

### Gig-owned domain code, shared validated persistence (finding 3)

`gig.py` is the local entry point into the validated GigAI operation service.
The service is an in-process Python/library boundary, **not an HTTP API**.
Gig-owned `tools/` retain command behavior, domain transformations and report
composition. They construct typed requests and consume typed results; they
do not write SQL tables, journal files, receipts, approved manifests or Run
evidence directly. The shared layer owns validation, locking and publication,
not a centrally mutable implementation of Scout's business logic.

Each supported invocation binds full Gig/version identity, canonical entry
path, source-inventory digest, entry/tool file digests, operation, effects and
agent origin. Resolve and hash the actual inventory before dispatch and
recheck it at mutation publication; reject changed, extra executable,
foreign or unapproved source rather than trusting a caller-supplied digest.
Persist these bindings in the operation receipt. Do not call this proof of
every instruction executed by an external agent.

Changing executable tool/schema code requires a new proposed Gig version,
inventory validation and existing explicit approval before it is eligible
for supported mutation. Unchanged effects may reference an existing valid
registered capability; new/broadened effects require separate registration
and authorization. There is no shortcut that blesses changed code merely by
changing a capability ID. Frontend/source working copies may be edited freely;
drafts stay distinct from approved bytes and template updates preserve them.

The existing capability executor must check these bindings when GigAI launches
a tool. An external agent's direct `python gig.py` execution remains subject
to its own user's permissions; the validated service still checks every
mutation and cannot manufacture direct CLI consent. Editable Python under
the same OS account is **not a sandbox**: arbitrary bypass writes invalidate
supported evidence or are repaired as disposable SQL divergence, not certified
as valid operations. Init/clone/inspect/imported source never execute code.

## 5. Simple default tracker and core CSS

Ship clean, restrained HTML with readable typography, spacing, tables, status
labels, document links, and obvious navigation. GigAI provides versioned core
CSS as a scaffold asset; each Gig gets local assets it can customize. No CDN,
frontend build system, React, or remote service is needed for the default.

The default UI is read-only; CRUD is still required through the Python tools.
Show opportunities, company/role, posting/source links, recorded application
status and dates, notes, selected documents/checks, pending questions, and links
back to relevant Runs. Label unknown sponsorship/compensation and uncertain
source claims honestly. Never infer an application from a completed draft.

### Source/output separation and safe publication (finding 5)

`ui/template.html` and `ui/style.css` are customizable source. The report tool
must never overwrite them. It reads an explicitly selected inventoried source
version and a consistent projection snapshot, generates a complete sibling
staging bundle, then publishes it under
`reports/scout/generations/<generation-id>/`. Only after successful validation
does it atomically replace `reports/scout/current.json`, which identifies the
complete bundle and its `index.html`. Readers resolve that selector once.
Failure leaves the last complete report selected. Staging and publication
remain within the validated Gig root and use the common report writer lock.

Bundles contain their own CSS/assets, generation time, journal HEAD, source
version/digests and projection schema/cursor. They do not link mutable CSS
working copies into an otherwise immutable generation. The report tool returns
the exact HTML path for the agent to open. Explicit regeneration/rebuild is
available; no daemon or background worker is required.

The default renderer escapes text and attributes and constructs links from
validated typed references, not raw supplied HTML. Allow external `https:`
and `http:` links only after URI parsing and rejection of controls, embedded
credentials and protocol-relative forms; do not auto-fetch them. Refuse
`javascript:`, `data:`, `file:`, other schemes, absolute filesystem paths,
backslashes and encoded separator/traversal tricks in supplied local locators.
Internal stored refs are normalized root-relative artifact IDs/paths with no
`..` components. Check every component for symlinks and ensure the resolved
file stays in the selected Gig; permit no cross-Gig escape. The renderer may
compute the necessary relative href from a deeply nested report to that
validated target; never accept that href directly from untrusted input.
Use inert text instead of an unsafe link. Keep the default report script-free
with no remote assets and test its content policy in local-file mode.

User edits to frontend source are preserved, not asserted to be safe because
they exist. Preview of a customized frontend is an explicit user/agent action
under its own execution permissions; the shipped renderer's safety claims do
not certify an arbitrary replacement React application or handwritten HTML.

Agents can explicitly import results and regenerate the report after saved
changes. No background worker is required for this baseline. An optional local
preview command may be considered; HTMX, a daemon, and HTTP CRUD are not
prerequisites. Hooks and automatic refresh scheduling remain v0.1.8 research.

Users may replace the frontend, including with React, by modifying their own
Gig. Preserve source/schema/UI versions and user customizations across an
upstream template-update decision; do not silently overwrite their files.
Running a user-chosen replacement does not make it a GigAI-managed service.

## 6. Artifact handoffs between graphs

Completion means the next consumer can use the produced material, not simply
that an agent exited or a Markdown file exists. A handoff includes:

- readable documents under `docs/` and typed outputs with exact content refs;
- originating graph/version/Run and selected input revisions;
- source/claim relationships, uncertainty, checks and their evidence;
- unresolved questions, missing material, and explicit usable output roles.

Validate a producer's outputs against its contract. Then evaluate readiness
against the selected consumer's requirements: salary unknown may be acceptable
for tailoring while a missing posting is not. Suggestions do not automatically
launch another graph. Incomplete work stays visible as partial/checkpointed
work, not a successful Run manufactured for the dashboard.

The first useful vertical slice should demonstrate research, a structured
handoff, reuse for one pasted posting, and a refreshed tracker in a fresh agent
session. Include a user change through the local CRUD tools. The dashboard and
handoff proof belong in this slice, not solely in final release polish.

## 7. Goal impact and acceptance additions

These are accepted contract additions to existing delivery goals, not completed
implementation goals or permission to skip the G43.1 live prerequisite.

| Goal | Added responsibility |
|---|---|
| SCOUT-00 | Contract accepted, including the workspace/initialization/SQLite/tool/UI decisions; downstream goals must implement and verify the specified strict schemas and caller changes |
| SCOUT-02 | Preserve canonical graph/version identity and historical references in the organized workspace |
| SCOUT-03 | Implement reviewed per-Gig SQLite/record ownership, local CRUD consistency, linked artifacts and recovery |
| SCOUT-04 | Bind imported Run DTOs and handoffs to validated producer outputs and selected consumer inputs |
| SCOUT-05 | Username-aware general init, full default-inventory batch, private instance bindings, per-Gig source/UI scaffolds, safe restart and updates |
| SCOUT-06/08/09 | Prove research-to-tailoring reuse, user-recorded application changes, and refreshed tracker in the first usable slice |
| SCOUT-11 | Complete navigation, customization, schema/tool compatibility and consistent private transfer |
| SCOUT-12 | Verify installed scaffold, CRUD/import/recovery, useful tracker, fresh-session handoffs, and user-owned customization |

Additional accepted scenarios requiring implementation evidence:

1. A newly created Gig receives its own tools/assets; modifying one instance
   does not change another, its template, or historical approved snapshots.
2. General init obtains/reuses explicit username and provisions all bundled
   defaults once; owner conflicts, new defaults, custom packages, interrupted
   batches and changed upstream templates preserve existing IDs/data/edits.
3. Local CRUD and duplicate Run imports produce consistent state; interrupted
   writes/import/report generation recover without invented completion.
4. A completed research handoff supports the correct downstream inputs; stale,
   missing, or unrelated artifact revisions are rejected or produce questions.
5. The tracker shows recorded application facts and links to selected docs,
   sources, checks, and Runs; markup payloads render as data, not active code.
6. A user frontend/tool customization survives an upstream update decision.
7. An explicit private transfer preserves database/file consistency and
   relative navigation without credentials, unintended disclosure, or authority
   escalation. Clean template export contains no private Gig state.
8. A fresh installed environment can operate the default Gig through its
   documented Python tools without Dolt, a deployed service, or required hooks.
9. CRUD journals typed revisions/tombstones; deleting and rebuilding Scout
   projections preserves application state and old Run inputs. G22 trace data
   survives index rebuild, concurrent access and private transfer.
10. A forged layout marker, unknown root, unsafe ignore change or redirected
    working copy cannot bypass the v1/v2 workpad validation boundary.
11. Changed tool/schema bytes, mismatched inventory, stale version or broadened
    effects refuse supported mutation; successful receipts identify the checked
    tool version. Direct database changes never become journal authority.
12. Report regeneration leaves customized templates/assets unchanged; failure
    before current-selector publication leaves the old report usable. Hostile
    schemes, encoded traversal, symlink escapes and markup are rejected/inert.

Source review must cover `index._write_projection`, `read_index`, G22
`persist_trace` callers, workpad validation/ignore checks, registry/init
recovery, package inventory/export, capability dispatch and strict operation
schemas. A focused successful test cannot substitute for these caller updates.

## 8. Deferred research

The [v0.1.8 spikes](../../../v0.1.8/spikes/README.md) remain after Scout:
user accessibility/onboarding, effective memory-driven improvement, per-Gig
hooks/Markdown drift checks, and whether Dolt is worthwhile. This amendment
does not start any of them or weaken the existing direct-consent requirements.
