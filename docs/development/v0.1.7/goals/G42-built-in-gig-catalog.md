# G42 — Built-in Gig Catalog

**Version:** v0.1.7
**Status:** Complete — implementation and closeout evidence accepted
**Depends on:** G40 and G41 complete and ratified; existing configuration,
registry, proposal/version, capability, journal, workpad, and Run authorities
**Unblocks:** G43 adaptive review profiles and the catalog consumers in G45/G46

## Outcome

G42 provides a small, versioned catalog of useful Gig definitions that can be
discovered and copied into a project-local `.gigai` package. Catalog entries
are portable authoring material. They are not automatically installed,
approved, selected, executed, or connected to credentials.

The initial catalog contains three definitions:

- `sync-references` — collect and reconcile explicitly supplied references;
- `plan-a-research` — turn an approved research question into a bounded plan;
- `review-plan` — inspect a proposed plan against declared requirements and
  produce review findings.

Each entry is a normal Gig definition with an explicit identity, version,
contract, Goal Graph, input requirements, capability requirements, output
contract, evidence requirements, and evaluation metadata. The catalog is a
source of validated definitions, not a second Gig lifecycle.

## Product boundary

G42 is consumed through the CLI and durable local artifacts. It must support
catalog listing, inspection, validation, and explicit installation or copy
into a project package. These operations must be usable in JSON and readable
terminal output.

Catalog operations do not require provider credentials, network access, a
browser, an agent interview, or a Run. A catalog entry may declare that a
capability or provider is required, but discovery of that requirement is not
proof that it is installed, authenticated, compatible, verified, or usable.

## Contract gate

Before implementation, G42 must settle and test:

1. the catalog manifest schema, strict field set, catalog revision, and entry
   identity rules;
2. the canonical relationship between a catalog entry, its package manifest,
   and a project-local package identity;
3. deterministic definition bytes, content digests, source revision, and
   provenance requirements;
4. the exact initial definitions and their Goal Graph, review-contract,
   input, output, capability, and evidence contracts;
5. list, inspect, validate, and install/copy CLI behavior, including JSON
   output and typed refusal diagnostics;
6. duplicate, conflicting, deprecated, unknown, malformed, and incompatible
   catalog-entry behavior;
7. the exact catalog-entry package layout and deterministic package identity
   algebra defined below;
8. preservation of proposal, approval, active-version, capability, journal,
   workpad, and Run authority boundaries; and
9. the catalog fixture, schema, digest, relocation, upgrade, and UAT evidence
   required for acceptance.

No implementation is accepted until each item has executable or contract
evidence.

## Chosen package and identity algebra

G42 uses one complete G41 package artifact per catalog entry/version. The
catalog may contain all three initial artifacts, but v0.1.7 project
installation is deliberately limited to one installed catalog package per
project. This preserves G41's current single-package initialization and
adoption semantics; G42 does not amend G41 to support multiple package roots.
Installation does not merge files into an arbitrary existing package. The
portable layout is:

```text
.gigai/packages/<package-id>/
  package.json
  catalog-entry.json
  definition/gig.md
  definition/goal-graph.json
  definition/review-contract.json
  definition/evaluation.json
  provenance.json
```

`package.json` remains the unchanged G41 manifest and remains authoritative for
the complete file inventory and package content digest. The other files are
portable package content and every byte is listed in `package.json.files`.
`catalog-entry.json` is the strict G42 entry metadata file; it records the
catalog ID, definition version, catalog revision, source revision, and entry
content digest. No catalog metadata is added to the G41 manifest schema.

The catalog identity key is:

```text
(catalog_id, definition_version, entry_content_digest)
```

The package identity is deterministic for `(catalog_id, definition_version)`
using this literal G42 namespace UUID:
`6d4f3a9e-3f76-4a2b-9d17-5c8e2f1a4b63`. The canonical UTF-8 name is
`catalog_id + "@" + definition_version`. Compute SHA-256 over the 16 raw
namespace UUID bytes followed by that UTF-8 name, take the first 16 digest
bytes, set the UUID version nibble to `4`, set the RFC 4122 variant bits, and
format the result as the existing G41 `package_` UUID. This explicit procedure
reconciles deterministic catalog identity with G41's v4 UUID validator. The
package content digest is the G41 digest over all listed files, including
`catalog-entry.json`. Golden vector:

```text
namespace: 6d4f3a9e-3f76-4a2b-9d17-5c8e2f1a4b63
canonical: sync-references@1.0
package_id: package_190960c2-7114-4c0e-8ccb-2dc2d1686f6e
```

Therefore:

- the same catalog ID/version/digest maps to the same package ID and is
  idempotent;
- the same catalog ID/version with a different digest is a catalog conflict
  and cannot replace the existing package silently; and
- a changed definition version receives a different package ID and package
  root.

Installation validates the entry and complete package bytes before mutation.
The supported bootstrap order for a new project is:

```text
catalog validate
  -> catalog install into the selected unbound target
  -> gigai init --adopt-package --confirm
  -> normal proposal/approval lifecycle
```

Catalog install may create the one validated portable package root in an
otherwise unbound target, but it creates no `.gigai/project.toml`, registry
binding, or lifecycle authority. The explicit G41 adoption command then binds
that existing package to the selected target. It requires the configured
GigAI home, an explicit target, one valid package, and direct confirmation;
failure leaves the package and target unchanged. Catalog install must not
silently invoke `gigai init` or create the binding itself.

During this bootstrap, G41 may temporarily hide the pre-existing untracked
portable package while it creates the binding; that exact package-only status
change is reconciled by G41, and its selective exclude cutover then exposes
portable files while keeping private paths ignored. Any other Git-status
change remains a refusal.

If `gigai init` has already created G41's empty random package, catalog install
refuses with typed `project_package_exists`; G42 does not delete, merge, or
replace that package. The operator must use a later explicit package-authoring
or replacement contract. If the selected project already has the same catalog
package, installation is idempotent. Any different package root, identity,
content digest, manifest inventory, or byte mismatch refuses without replacing
the existing package. A project-local copy may later be submitted to the
normal proposal/approval lifecycle; copying it does not make it an approved
Gig.

## Installation record decision

G42 creates no new catalog installation record. The durable installation result
is the validated project-local G41 package itself and the CLI's structured
evidence of its source identity, package identity, digest, destination, and
`authority_changed: false`. G41's existing private package-installation
record remains the only installation record for a second-home rehydration; G42
does not add a competing catalog record or reinterpret that record as
lifecycle authority.

Repeated installation is keyed by the catalog identity tuple above plus the
resolved project target. It returns the existing matching package, refuses a
second package root or same-version digest conflict, and never creates a
proposal, approval, active pointer, capability installation, journal decision,
workpad record, or Run.

## Catalog authority model

The catalog owns only immutable, validated definition sources:

```text
catalog entry
  -> validated portable Gig definition
  -> explicit project-package copy
  -> proposal/approval/version lifecycle
  -> Run preparation and execution
```

The following rules are normative:

1. A catalog entry is not a Gig instance and does not allocate a user Gig ID.
2. Catalog identity and digest authenticate definition bytes only; they do not
   prove approval, readiness, capability installation, or Run success.
3. Copying an entry into `.gigai/packages/` preserves the entry's source
   identity and digest, while the project package remains subject to G41
   validation and normal project scope.
4. A catalog operation cannot create a proposal, approve a version, advance an
   active pointer, install a capability, write a journal decision, or start a
   Run.
5. A user may edit or compose copied material only through a later authoring
   or proposal contract. Catalog bytes remain unchanged and content-addressed.
6. A catalog entry that is unavailable, incompatible, deprecated, or invalid
   is reported explicitly; it is never silently substituted or downgraded.
7. Catalog definitions must not contain credentials, absolute machine paths,
   private workpad/run data, executable hooks, hidden prompts, or provider
   payloads.

## Entry contract

Every initial entry must define, in canonical portable form:

- stable catalog ID and semantic definition version;
- catalog revision and source/provenance identity;
- human-readable purpose and bounded use cases;
- required inputs, allowed reference forms, and input validation rules;
- Goal Graph nodes, ordering, stopping rules, and failure outcomes;
- reviewer/agent role requirements without selecting a local model;
- declared capability requirements without installed capability state;
- output artifacts, schemas, provenance, and evidence expectations;
- privacy and network policy defaults;
- explicit approval and Run prerequisites; and
- evaluation fixtures, expected invariants, and known limitations.

The three initial entries must be useful as definitions even when no live
provider is available. Their offline validation fixtures must exercise shape,
authority, refusal, and deterministic evidence behavior. A fixture is not a
claim of live provider quality.

### `sync-references`

This entry accepts an explicitly supplied reference set, records classification
and provenance requirements, and produces a validated reference snapshot or a
typed refusal. It must not crawl arbitrary locations, fetch network content,
change a target, or rewrite prior evidence by default.

### `plan-a-research`

This entry accepts a declared research question, scope, constraints, and
reference policy, then produces a bounded research plan. It must declare
questions, tasks, evidence expectations, stopping rules, and unresolved risks.
It must not claim that research was performed merely because a plan exists.

### `review-plan`

This entry accepts a candidate plan and its declared requirements, then
produces typed findings and evidence references. It must distinguish invalid,
incomplete, blocked, and acceptable findings. It must not approve a Gig,
select a Run Plan, adjudicate provider output, or mutate the reviewed plan.

## Package and installation behavior

G42 consumes G41's package validator and portable/private boundary. The
catalog source itself may be shipped with GigAI, but a project-local copy must
be a normal validated package resource with deterministic bytes and a source
catalog identity.

The implementation must define one explicit installation command or command
family with these properties:

- validate catalog entry before any project mutation;
- require an explicit target/project selection for project-local copying;
- preserve an existing package identity and refuse conflicting replacement;
- make repeated installation idempotent;
- report source catalog ID, entry version, package ID, and content digest;
- create no private authority beyond any explicitly documented installation
  record; and
- leave the target unchanged on validation, conflict, or interrupted-copy
  failure.

Installing a catalog entry into a second home must preserve G41's rule: only
portable definition bytes move. Configuration, credentials, registry rows,
bindings, approval history, capabilities, journals, workpads, Runs, and
provider state remain private and are not imported.

## CLI evidence

The accepted CLI surface must provide equivalent structured and human-readable
forms for:

- listing catalog IDs, versions, status, and short descriptions;
- inspecting one entry's complete portable contract and digest;
- validating an entry without changing project or private state;
- explicitly copying/installing an entry into a selected project package; and
- reporting refusal reason, source identity, destination identity, and whether
  any authority changed.

The output must distinguish catalog availability from project configuration,
provider authentication, capability readiness, Gig approval, and Run
usability. It must not imply that listing or validation makes an entry runnable.

## Acceptance evidence

G42 acceptance requires:

- strict catalog and entry schemas with unknown-field, version, identity, and
  digest tests;
- canonical bytes, the literal namespace/derivation procedure, and
  reproducible package-ID golden vectors for all three initial entries;
- list, inspect, validate, and explicit install/copy CLI tests in human and
  JSON modes;
- bootstrap tests proving catalog install before `gigai init`, followed by
  explicit `--adopt-package --confirm`, and refusal after an empty G41 package
  already exists;
- relocation and second-home tests proving portable-only transfer;
- one-catalog-package-per-project tests proving a second entry is refused
  without creating a second `.gigai/packages/<package-id>` root;
- idempotent repeated installation and conflicting-entry refusal;
- malformed, unknown, deprecated, incompatible, missing-capability, and
  missing-provider diagnostics;
- proof that catalog operations perform no network access, credential reads,
  package hook execution, hidden prompt injection, approval, capability
  installation, or Run allocation;
- package-boundary tests proving no private paths, transcripts, Runs, caches,
  snapshots, or absolute machine paths enter catalog material;
- deterministic offline fixtures for each initial Gig's inputs, outputs,
  evidence, refusal states, and stopping rules; and
- human UAT confirming that a user can discover, inspect, validate, and
  explicitly copy a catalog Gig through the CLI without a second interaction
  surface.

## Out of scope

- adaptive review profiles, VAR routing, model assignment, or sealed Run Plans;
- clone/create-from, agent-backed semantic authoring, or conversational
  interview behavior;
- reference crawling, live research execution, domain-specific Gig content,
  background Runs, scheduling, dashboards, or reporting projections;
- automatic catalog installation, dependency installation, provider calls,
  credential acquisition, network access, or executable hooks;
- approval, active-version selection, capability installation, journal
  decisions, workpad mutation, target effects, or Run execution; and
- changing G41's package identity, project binding, or private-state authority.

## Stop conditions

G42 stops and records a typed refusal when catalog bytes are malformed,
unknown, conflicting, non-deterministic, non-portable, privately contaminated,
or incompatible with the requested package boundary. It also stops when an
operation would require credentials, network access, provider execution,
authority mutation, or an implicit fallback.

The terminal contract outcomes are:

```text
ACCEPT
ACCEPT WITH AMENDMENT
REJECT
BLOCKED
```

This document defines the G42 contract only. It does not activate G42 or
authorize implementation until reviewed and explicitly activated.
