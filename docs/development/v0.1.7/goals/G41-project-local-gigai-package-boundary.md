# G41 — Project-Local `.gigai/` Package Boundary

**Version:** v0.1.7
**Status:** Complete — implementation and closeout evidence accepted
**Depends on:** G40 complete and ratified; existing v0.1.6 configuration,
registry, target-binding, workpad, journal, proposal/version, capability, and
Run authorities
**Unblocks:** G42 built-in Gig catalog, G44 clone/create-from, and the
project-local inputs consumed by later v0.1.7 goals

## Outcome

G41 gives a repository a clear, inspectable project-local `.gigai/` boundary
for portable Gig definitions. It separates material that can be committed,
reviewed, copied, and installed in another GigAI home from owner-private
configuration, credentials, workpad history, Runs, caches, snapshots, and
runtime state.

G41 also defines the v0.1.6-to-v0.1.7 upgrade boundary. An existing installation
must be preserved and made readable before new package behavior is considered
successful. Migration is explicit, idempotent, recoverable, and fail-closed;
it must never silently create a second history or replace an existing
authority.

The resulting ownership model is:

```text
project repository
  -> .gigai/ portable package definitions and project-local package metadata

GigAI home / private workpad
  -> configuration, registry, credentials, local state, journals, Runs,
     snapshots, caches, and other owner-private execution material

existing lifecycle authorities
  -> proposal, approval, active-version, capability, journal, and Run truth
```

`gigai init` establishes or reconciles the project-local package boundary. It
does not create a semantic Gig, allocate a Gig ID, approve a proposal, select
an active version, install a capability, start a Run, or install/execute a
tool.

## v0.1.6-to-v0.1.7 amendment

v0.1.7 pivots ordinary setup and Gig work to the terminal-native CLI and
project-local package boundary established by G40. The project package is the
portable authoring and sharing surface; the private home/workpad remains the
execution and authority surface.

This amendment changes where portable definitions are discovered and how
project initialization is presented. It does not discard v0.1.6 state, copy
private history into the repository, or replace the existing journal/workpad
and approval authorities.

G41 must preserve, or make explicitly migratable, all existing:

- configuration and model/provider references;
- project bindings and registry rows;
- Gig identities and package associations;
- proposal and approved-version identities;
- journal history and workpad locations;
- capability manifests and installations; and
- Run identities, manifests, details, outputs, and terminal evidence.

An upgrade that cannot preserve a record, resolve its owner-private location,
or prove its authority relationship must stop before changing authoritative
state and return a typed diagnostic. Fresh-install success is not upgrade
evidence.

## Contract gate

Before implementation, the following decisions must be reviewed and accepted:

1. the package manifest schema, package identity, schema revision, and strict
   unknown-field behavior;
2. the exact portable/private directory and file boundary under `.gigai/`;
3. the relationship between the existing project binding and portable package
   metadata, including selective ignore rules;
4. repository-root discovery, nested repository behavior, symlink handling,
   and non-Git target behavior;
5. `gigai init` state transitions, idempotence, interruption recovery, and
   refusal cases;
6. package validation, inspection, export/import, and second-home
   installation behavior;
7. v0.1.6 inventory, backup, migration, validation, rollback, and refusal
   semantics;
8. ownership of package definitions versus G42 catalog material and G44
   clone/create-from outputs;
9. authority preservation for configuration, registry, workpad, journal,
   proposal, active-version, capability, and Run records; and
10. the complete acceptance matrix, fixture set, security scans, and evidence
    locations.

No runtime implementation is accepted until each item has executable or
contract evidence.

## Package boundary

### Portable project material

The portable portion of `.gigai/` may contain only validated, shareable
definition material, such as:

- a package manifest at `.gigai/packages/<package-id>/package.json` with
  package identity, format revision, project scope, and content digest;
- Gig definition Markdown and machine-readable projections;
- declared Goal Graph and review-contract inputs;
- relative, content-addressed, or explicitly external reference declarations;
- declared capability requirements without installed tool bytes or credentials;
- package provenance and source/revision metadata; and
- package-local documentation needed to inspect the definition.

For this contract, `.gigai/packages/` is the recognized portable subtree and
`.gigai/packages/<package-id>/` is one package root. The package manifest is
the root's authority for package identity and digest; all files beneath that
root must be declared package content or validated package documentation.
G41 may add a narrower schema-defined layout beneath the package root, but it
must not broaden the recognized subtree implicitly.

Portable material must be deterministic and relocatable. It must not embed a
machine-specific home path, absolute repository path, workpad locator,
credential value, provider payload, private transcript, Run history, cache,
snapshot, installed executable, hidden prompt, or automatic hook.

### Private and derived material

The following remain outside portable package authority and are either ignored,
owner-private, or rebuildable derived state:

- the existing target/project binding and private registry locators;
- GigAI configuration and credential references;
- private workpad Git history and journal records;
- `local/`, `runs/`, `cache/`, and `snapshots/` material;
- discovery snapshots that contain machine-local executable evidence;
- capability installations and provider/runtime state;
- approval records and active-version pointers unless a later contract defines
  a safe portable projection; and
- logs, temporary files, locks, generated payloads, and failure artifacts.

These materials may be referenced by identity from portable definitions where a
later authority allows it, but they are never copied into a package by
`gigai init`, export, or migration.

### Existing binding relationship

The existing `.gigai/project.toml` binding remains the project-identity
boundary established by target binding. It is not replaced by a package
manifest and does not become a Gig definition.

v0.1.6 writes one exact `/.gigai/` line to `.git/info/exclude`, which hides the
whole directory and causes tracked `.gigai/` content to be refused. G41 must
define an atomic v0.1.7 cutover from that predecessor rule to the following
private-path rules, in this exact order:

```text
/.gigai/project.toml
/.gigai/local/
/.gigai/runs/
/.gigai/cache/
/.gigai/snapshots/
/.gigai/locks/
```

The cutover removes the exact `/.gigai/` line and adds those exact private
lines while preserving every unrelated `.git/info/exclude` byte and line in
place. It writes the result through one recoverable replacement, validates the
result, and records before/after exclude digests. A rerun is byte-idempotent.
An interruption must leave either the complete v0.1.6 rule or the complete
v0.1.7 rule, never a mixture; recovery must reconcile the same cutover without
duplicating entries. A malformed or ambiguous predecessor exclude file fails
closed before mutation.

The portable package subtree is explicitly outside those private paths. A
successful cutover therefore permits validated package files under the
accepted portable subtree to be tracked. Expected Git-status evidence is:
the private binding/state remains ignored, portable package files appear as
ordinary tracked/untracked project content according to the user's action, and
no other target content changes.

By default, a pre-existing tracked `.gigai/` entry remains a refusal. The only
exception is explicit package adoption described below; conflicting binding,
malformed manifest, mixed private content, or ambiguous ownership is never
guessed.

### Tracked package adoption

The normal `gigai init` path does not adopt tracked `.gigai/` content. G41
defines an explicit `gigai init --adopt-package` path for a recognized portable
package subtree only.

Adoption is allowed only when every tracked `.gigai/` path other than the
explicitly permitted binding case is inside the accepted portable package
subtree, every package file is schema-valid and digest-valid, and no file is a
symlink or executable hook. An already initialized v0.1.6 repository may have
exactly one untracked, ignored `.gigai/project.toml`; adoption must validate
that binding against the resolved target and private registry, preserve it as
the existing project authority, and allow it to coexist with the fully
portable tracked package subtree. `project.toml` may not be tracked, replaced,
duplicated, malformed, or bound to another project. No tracked `local/`,
`runs/`, `cache/`, `snapshots/`, lock, credential, transcript, or unknown
entry is permitted. The command must show the package and binding identities
before mutation and require explicit operator confirmation.

Adoption records the package identity/digest in owner-private state and
reconciles the selective ignore rules. It does not rewrite package bytes,
create a Gig ID, import private history, approve a definition, select a
version, install a capability, or start a Run. A mixed or unknown tracked
`.gigai/` tree remains a typed refusal, even when it contains one valid
portable package.

## Repository and target resolution

G41 resolves a repository target through the existing target-binding authority
and records the canonical repository root only in private state. It must:

- resolve the requested directory and its actual repository root;
- distinguish the requested nested path from the owning repository root;
- preserve Git and non-Git target behavior explicitly;
- handle ordinary aliases and symlinks by filesystem identity where the
  existing binding contract requires it;
- refuse a broken, repointed, unavailable, or conflicting binding; and
- treat the current working directory only as a selector when no explicit
  target is supplied: inside Git, Git's reported top-level plus the valid
  binding establish the project authority;
- never treat a guessed parent directory as an outer-project authority; and
- select an outer repository from a nested repository only when the caller
  explicitly supplies that outer repository root as `--target` and its own
  Git root/binding validates. No implicit `--outer` or parent search exists.

For a nested repository, the package belongs to the resolved Git root selected
by the requested target. G41 must not silently write package material to an
outer repository. For a non-Git target, the private registry remains the
project authority and package placement must be explicit in the accepted
contract.

## `gigai init`

`gigai init` is a project-boundary operation with stable human, JSON, and
Markdown output. The human form may be compact and colored; JSON is stable,
color-free, and suitable for agent consumption.

The operation is:

```text
resolve target
  -> inspect existing binding and package state
  -> validate configuration and authority prerequisites
  -> plan exact target/package/ignore deltas
  -> apply one atomic or recoverable initialization
  -> validate ownership, bytes, and idempotence
  -> report package and private-state identities
```

It must be safe to repeat. A successful repeated invocation produces no new
identity, duplicate ignore entry, second package root, or semantic Gig state.
An interrupted invocation either reconciles the same exact initialization or
fails closed before replacing ambiguous state.

`gigai init` must refuse before mutation when the target is unavailable, the
configuration is unsupported, the binding is malformed or conflicting, the
package contains unknown or unsafe material, the package root escapes the
target authority, or the requested migration cannot be proven safe.

## Package operations

G41 must define and test the following conceptual operations, whether exposed
as separate commands or one structured CLI surface:

- inspect the resolved package and show portable/private classification;
- validate package schema, identity, digests, references, and authority links;
- initialize or reconcile the project-local boundary idempotently;
- export only validated portable material;
- install or rehydrate a validated package into a second GigAI home without
  copying private state; and
- report refusal reasons without partial package or authority changes.

Second-home installation has a precise durable result: it preserves the same
package identity and content digest, and creates one fresh owner-private
installation record containing that identity/digest, source revision,
destination-home scope, installation status, and timestamp. Repeating the
same package identity/digest in the same home returns that record rather than
creating a duplicate. It creates no imported project binding, Gig identity,
proposal, approved version, active pointer, capability installation, journal
history, workpad history, or Run authority. A separate explicit
target-binding operation may establish a new local project identity; package
installation does not do so. Referenced private artifacts and capabilities
that are not present in the second home are recorded as explicit
`unavailable`/`blocked` requirements; they are not fetched, installed, or
silently substituted.

Package installation does not approve a Gig, select an active version, install
a capability, or start a Run. It creates no hidden fallback when a referenced
private artifact or required capability is absent.

### Implemented CLI surface

The current G41 implementation exposes the boundary through:

- `gigai init`, which creates or reconciles one project-local package and the
  exact private Git exclude rules;
- `gigai init --adopt-package --confirm`, which explicitly adopts one valid,
  already-tracked portable package while preserving one valid ignored binding;
- `gigai package inspect`, which validates package schema, inventory, modes,
  symlinks, and content digest;
- `gigai package install`, which copies only validated portable bytes and writes
  one idempotent private installation record; and
- `gigai upgrade --confirm`, which creates a private v0.1.6 recovery copy,
  migrates configuration, preserves logical registry/workpad identities, and
  publishes the project package boundary.

These commands do not create a Gig, approve a proposal, select an active
version, install a capability, start a Run, execute package material, or
import private authority into a second home.

G41 does not define the built-in catalog, adaptive review profiles, clone
lineage, agent interview behavior, background execution, or domain-specific
Gig semantics. Those belong to G42, G43, G44, and later goals.

## Upgrade and rollback contract

The supported upgrade path begins with a read-only inventory of the v0.1.6
installation. The inventory must capture stable identities, schema versions,
authority links, counts, content digests where applicable, and private
locators without publishing sensitive values.

Migration must then:

1. validate that the predecessor state is a supported, complete v0.1.6 state;
2. create a durable, owner-private recovery copy or staging representation
   without overwriting a conflicting backup;
3. prepare package metadata and any additive registry/configuration changes
   in temporary or transactional state;
4. validate every preserved configuration, project, Gig, version, journal,
   workpad, capability, and Run identity and relationship;
5. publish changes atomically or through the existing authority transaction;
6. re-read the result independently and compare the preservation inventory;
   and
7. record a durable migration result with source revision, destination
   revision, actor, identities, and recovery location.

Migration is idempotent. A rerun after success returns the existing migration
result and does not duplicate package roots, registry rows, backups, or
authority transitions. A failure before publication leaves the original state
readable and untouched. A failure after a publication boundary must either
prove and complete the same exact migration or restore/refuse using the
recovery contract; it must never invent a replacement identity or silently
discard the predecessor.

Unknown, malformed, partial, future, or downgraded predecessor versions fail
closed. Automatic downgrade is not provided. The recovery copy is private
operational evidence, not a second authority and not portable package content.

## State and authority contract

The package lifecycle is distinct from the Gig lifecycle:

```text
uninitialized -> inspected -> planned -> initialized -> validated
                                      |                 |
                                      v                 v
                                   blocked          portable
```

The following rules are normative:

1. The project binding and private registry remain the authority for project
   identity and private target/workpad locations.
2. A package manifest identifies portable material; it does not select an
   active Gig version or replace the journal, proposal, capability, or Run
   authority.
3. Package validation may reject or report a definition but cannot approve,
   execute, install, or mutate it.
4. `gigai init` and package installation do not allocate a Gig ID. Gig
   identity allocation remains with the approved lifecycle goal.
5. A package digest authenticates the package bytes only. It does not prove
   provider readiness, capability installation, approval, or Run success.
6. Private execution artifacts are never made portable merely because a
   package references them.
7. Any package-to-private-state relationship must be resolved by the owning
   GigAI authority and must fail closed when identity, digest, or location
   differs.
8. Migration preserves prior authority and history; it cannot reinterpret
   historical evidence as a new approval, active pointer, or Run.
9. No package hook may install software, invoke a provider, execute code, or
   access credentials during initialization, validation, export, or install.
10. All user-visible failure states identify whether the problem is package
    invalidity, target/binding conflict, private state unavailable, migration
    refusal, or a later capability/readiness issue.

## Security and privacy boundary

G41 must prove that package operations:

- use structured filesystem operations with bounded paths and no shell
  interpolation;
- reject path traversal, symlink escapes, unexpected file types, and unsafe
  executable or hook material;
- preserve file modes and ownership expectations for private recovery data;
- do not read or serialize credentials, raw transcripts, hidden prompts, or
  unrelated repository contents;
- do not write secrets, absolute personal paths, private Run data, or machine
  discovery evidence into tracked package files;
- do not fetch network content or execute package-defined commands; and
- produce sanitized diagnostics and package inspection output.

## Out of scope

- Built-in Gig definitions and catalog installation; G42 owns those.
- Adaptive review profiles, VAR routing, role assignment, or sealed Run Plans;
  G43 owns those.
- Clone/create-from lineage and agent-backed semantic Gig authoring; G44 owns
  those.
- Reference synchronization, research workflows, domain Gigs, background
  Runs, dashboards, HTML projections, or release-wide quality gates.
- Automatic package installation, dependency installation, provider calls,
  credential acquisition, network access, or executable hooks.
- Replacing the existing target binding, registry, workpad, journal, proposal,
  active-version, capability, or Run authority.
- Publishing private workpad Git history, Runs, caches, snapshots, or
  credentials as package content.
- Automatic downgrade or migration from an unsupported predecessor state.

## Acceptance evidence

### Contract and package shape

- strict package schema, unknown-field, version, digest, and identity tests;
- portable/private classification fixtures with secret, path, transcript,
  executable, hook, cache, snapshot, and Run canaries;
- package relocation and second-home installation with equivalent portable
  bytes and fresh private state;
- package inspection and export reports that redact private locators; and
- proof that package validation and installation create no Gig authority.

### Repository and initialization

- clean Git target with exact pre/post target and status manifests;
- dirty Git target whose tracked and untracked content is byte-preserved;
- non-Git target with explicit private registry authority;
- nested repository, symlink alias, broken alias, and repository-root conflict;
- repeated `gigai init` with no duplicate identity or ignore delta;
- v0.1.6 whole-directory exclude to the exact v0.1.7 private-path exclude
  cutover, preserving unrelated exclude bytes and expected Git-status deltas;
- interruption before, during, and after exclude/package publication with
  truthful reconciliation to one complete rule set;
- default refusal of tracked `.gigai/` plus explicit `--adopt-package` success
  for a valid portable subtree alongside one untracked valid project binding,
  and refusal for tracked private, mixed, conflicting, or unknown content;
- malformed binding, conflicting package, path escape, permission failure,
  and unexpected file-type refusal; and
- concurrent initialization proving one project/package identity or a typed
  pre-mutation conflict with no lock residue.

### v0.1.6 upgrade and rollback

- populated v0.1.6 configuration, registry, project, Gig, proposal, approved
  version, journal, workpad, capability, and Run fixture;
- read-only inventory before migration and independent preservation comparison
  afterward;
- successful migration, idempotent rerun, conflicting backup, unavailable
  mount, malformed predecessor, unsupported version, and permission cases;
- failpoints before staging, after staging, before authority publication,
  after each transaction/replace boundary, and after publication;
- recovery copy readability and owner-private permissions; and
- proof that failed migration leaves v0.1.6 state readable or completes the
  exact same migration without replacement identities.

### Authority and security

- project binding remains authoritative and unchanged except for an accepted
  additive package-boundary amendment;
- existing journals, approved versions, active pointers, capabilities, and
  Runs remain byte- and identity-preserved;
- package operations cannot approve, select, execute, install, or mutate;
- second-home installation preserves package identity/digest while producing
  only fresh private installation state and explicit unavailable requirements;
- no credentials, absolute personal paths, private Run history, provider
  payloads, or installed tool bytes appear in portable artifacts; and
- `git diff --check`, source compilation, package validation, and sanitized
  CLI/JSON evidence pass.

## Stop conditions

Stop G41 and record a blocking result if:

- the portable/private boundary cannot be represented without exposing
  credentials, absolute paths, private history, or machine state;
- `gigai init` cannot be made idempotent and recoverable;
- repository-root or symlink ambiguity would cause writes to the wrong
  project;
- package installation would need to create or replace lifecycle authority;
- a v0.1.6 record cannot be preserved, independently verified, or recovered;
- migration can leave a partial authoritative state or silently create a
  second history;
- package material can install, execute, fetch, or access credentials through
  an implicit hook; or
- acceptance depends on a fresh-install fixture while populated upgrade and
  rollback evidence is absent.

## Completion gate

G41 may be marked complete only when:

1. this contract and any additive schemas are accepted;
2. package shape, ownership, repository resolution, and initialization evidence
   pass for Git, nested, symlink, and non-Git scenarios;
3. portable packages install into a second home without private leakage or
   authority substitution;
4. the v0.1.6-to-v0.1.7 migration and rollback matrix passes with preservation
   evidence for existing configuration, Gigs, versions, journals, workpads,
   capabilities, and Runs;
5. all refusal and interruption cases are typed, recoverable, and free of
   partial authority changes;
6. G42/G44 handoff inputs are explicitly identified without pulling their
   implementation into G41; and
7. a completion audit and terminal handoff record the accepted package schema,
   migration revision, evidence paths, known limitations, and exact next-goal
   boundary.

G41 is complete. It supplies the package-boundary inputs for G42 and G44 but
does not activate or implement those goals.
