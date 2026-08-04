# ADR 0003: Distribute schemas as package resources, version them additively, and keep them closed to user extension

- Status: Accepted
- Date: 2026-08-03

## Context

GigAI's serialized contracts in `src/gigai/schemas/` define bytes that outlive
the process that wrote them. A Gig Proposal, an approved Gig version, a Run
manifest, and every text handoff are committed into a private per-Gig Git
repository and read back later — potentially by a different GigAI version, by a
Codex or Claude caller consuming the JSON pointer envelope, or by a human
reading files directly. Approval "freezes the exact Markdown and graph as an
immutable Gig version," so the definition of a valid artifact cannot be
whatever the currently installed code happens to believe.

The schema README already states the versioning rule in one paragraph: an
additive optional field creates a new minor schema version, a breaking change
creates a new major schema version, and a reader accepts only versions it
explicitly supports. What was never written down is the operational half — how
the schemas reach a user, what physically changes on disk when a contract
version is added, and whether a user may supply schemas of their own. Three
questions kept recurring during Phase 1 implementation with no documented
answer:

1. Are the schemas shipped to users, or are they a repository-internal artifact?
2. When a new proposal status or field is needed, what exactly happens to the
   files, the `$id` values, and `SHA256SUMS`?
3. Can a user extend or override a schema for their own Gig shapes?

Absent a written answer, the risk is that a future goal edits a frozen schema in
place because it looks like an ordinary source file. That would retroactively
redefine the contract under which already-approved Gig versions were sealed,
which is precisely what freezing exists to prevent.

The current state this ADR records and constrains:

- Eight schemas plus `README.md` and `SHA256SUMS` live in `src/gigai/schemas/`
  with an `__init__.py` describing them as "Frozen serialized contracts
  distributed as package resources."
- `pyproject.toml` declares
  `[tool.setuptools.package-data] "gigai.schemas" = ["*.schema.json", "README.md", "SHA256SUMS"]`.
- Every `$id` already carries a major version, e.g.
  `urn:gigai:schema:gig-proposal:1`.
- Every non-common schema `$ref`s `urn:gigai:schema:common:1#/$defs/...`, and
  its `schema_version` property is a `$ref` to the shared
  `{"const": "1.0"}` definition.
- `tools/verify_installed_schemas.py` resolves them through
  `resources.files("gigai.schemas")` — the installed package, not the source
  tree.

## Decision

### Applicability before the first public release

Until GigAI deliberately declares its first public release, the repository has
no distributed contract artifacts to preserve. During that pre-release period,
the immutability and additive-version requirements in decision 2, and the
per-change ADR/goal requirement in decision 5, are suspended for source
contracts. A reviewed change may edit schemas and canonical vectors in place,
regenerate `SHA256SUMS`, and update their tests together.

The delivery, exact-version-reader, closed-extension, and installed-validation
rules remain in force. This does not weaken canonical-byte identity, immutable
approved Gig versions, or journal authority. The full immutability regime takes
effect at the deliberately declared first public release, not at an incidental
development tag or an undefined version label.

### 1. Schemas ship inside the wheel as package resources

The schemas are part of the distributed artifact. `pip install gigai` places
them in `site-packages/gigai/schemas/` as ordinary readable files. They are not
withheld, not fetched at runtime, and not served from a network location. GigAI
has no maintainer network service, and contract resolution must not become the
first exception.

"Frozen" is a constraint on GigAI maintainers, not a restriction on user
access. Users may read, copy, diff, and hash the schemas they were shipped.

All code and tooling resolves schemas through `importlib.resources` against the
`gigai.schemas` package. No module reads them by filesystem path relative to a
source checkout, so installed behavior and repository behavior cannot diverge.

`SHA256SUMS` ships alongside them so an installation can be verified against the
contract set it claims to implement.

### 2. Contract versions are added as new files; existing files are immutable

A published `.schema.json` is immutable once released. Changing a contract
never edits the existing file.

To add a contract version:

1. Leave the existing schema file byte-for-byte unchanged. It remains in the
   wheel for as long as any supported reader accepts artifacts written under it.
2. Add a new sibling file whose `$id` carries the new major version, e.g.
   `urn:gigai:schema:gig-proposal:2`. The URN major segment is the version
   authority; filenames follow it and never carry meaning the `$id` does not.
3. If shared definitions change, publish `urn:gigai:schema:common:2` as a new
   file. Version-2 schemas `$ref` `common:2`; version-1 schemas continue to
   `$ref` `common:1`. A published schema's resolved reference graph never
   changes.
4. Append the new files to `SHA256SUMS`. Existing lines are never rewritten;
   an altered existing line is proof of an illegal in-place edit.
5. Add golden vectors for the new version. Existing vectors remain and must
   continue to pass unchanged.

Minor versions are additive-only: a new optional field, no change to required
fields, types, enums, or identity semantics. A minor version publishes a new
schema file under the same major `$id` and bumps the `schema_version` constant
(`"1.0"` to `"1.1"`). Any breaking change — a new required field, a removed or
retyped field, a narrowed enum, or any change to canonical bytes or digest
semantics — requires a new major `$id`.

Adding a member to an existing closed enum, such as a new proposal status, is a
breaking change for readers, because an older reader cannot interpret the new
value. It requires a new major version. Enums in v1 contracts are closed:
top-level objects reject unknown fields, and readers must not accept, drop, and
rewrite values they do not understand.

### 3. Readers dispatch on the declared version and refuse unknown ones

Every artifact carries `schema_version` as an exact constant. A reader selects
the validator by the value it read, never by "newest available." A version the
reader does not explicitly support fails with `unsupported_schema_version`.

GigAI never upgrades an artifact implicitly on read. Migration, if ever
required, is an explicit operator-visible transition that writes a new artifact
and preserves the original bytes; it is not a side effect of opening a file.

### 4. Schemas are closed to user extension in v1

Users cannot add, override, or extend GigAI's serialized contracts. There is no
schema search path, no user schema directory, no environment variable pointing
at alternative contracts, and no plugin hook that injects `$defs`.

This is deliberate. A Gig Proposal's meaning is what makes an approved Gig
version portable and its evidence checkable. If a user could redefine the
contract locally, an approved Gig version would no longer mean the same thing
across two installations, and the digests attesting it would attest a private
dialect.

User-specific variation is expressed inside the contracts, not by changing them:
the Goal Graph is a fixed DAG representation whose node content is
domain-neutral, and the schema README's own exit criterion is that a technical
spike, a domain research task, and an article "produce materially different Goal
Graphs and verification through the same domain-neutral structural schema."
Extension is a content concern, not a schema concern.

If a user needs a shape the contracts cannot express, that is a contract change
request against GigAI, reviewed and published as a new version by the process
above — not a local override.

### 5. Changing a contract is an explicit operator decision

A contract change stops implementation. It is raised for operator review,
recorded as an ADR and a plan addendum, and implemented as its own goal. It is
never made opportunistically inside a goal doing other work, and a later goal
must not alter a frozen schema, golden vector, or completed goal contract.

### 6. Validation dependencies must ship with the validator

`jsonschema` and `referencing` are currently declared only under
`[project.optional-dependencies] test`, while runtime dependencies are `click`
alone. A default install therefore carries the schemas but nothing able to
execute them. This is acceptable only while no shipped command validates.

When G07 introduces the named validators, the validation dependency moves into
runtime `dependencies`, or the validators are implemented without a JSON Schema
engine. Shipping a validating command whose validator is an optional extra is
not an acceptable end state.

## Consequences

- The schema set only grows. Removing a published contract version is itself a
  breaking change requiring its own decision, and requires evidence that no
  supported reader accepts artifacts written under it.
- Old contract versions are supported for as long as they remain published, so
  every added major version has a real ongoing maintenance cost. That cost is
  the intended brake on casual contract churn.
- Adding a proposal status, a Run state, or any enum member is expensive by
  construction. Enum membership should be settled deliberately before Phase 1
  seals rather than discovered afterward.
- Users get inspectable, hashable contracts on disk and can verify an
  installation against `SHA256SUMS` without contacting any service.
- Users cannot locally adapt the contracts. Genuine expressive gaps surface as
  contract change requests, which is slower than a local override and is the
  intended tradeoff for portable, comparable Gig versions.
- Because installed resolution is through `importlib.resources`, a source
  checkout cannot accidentally validate against schemas the installed wheel does
  not contain.
- `SHA256SUMS` becomes an audit surface: any diff to an existing line indicates
  an illegal in-place edit of a frozen contract, independent of review.
