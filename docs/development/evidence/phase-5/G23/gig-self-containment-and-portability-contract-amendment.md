# G23 Gig Self-Containment and Portability Contract Amendment

- Status: Accepted additive amendment; runtime not activated
- Type: Contract and serialized-resource amendment for G23
- Depends on: G17, G19, G20, and G22 completion audits and terminal handoffs
- Unblocks: G23 runtime implementation only
- Baseline: twenty-seven packaged schema resources

## Decision

G23 changes one existing serialized resource, `active-gig-version.schema.json`.
It adds an optional `capability_manifest` artifact reference and adds no new
packaged resource. The schema inventory remains twenty-seven resources. All
other schema bytes, hashes, meanings, approval authorities, and Run authorities
remain unchanged.

The field is optional for backward compatibility. An existing active pointer
without `capability_manifest` remains valid and is reported by G23 as
`reported_non_portable`; omission is never interpreted as an empty manifest or
as portability success. A pointer that carries the field must pass every
sealed-pointer and manifest verification check before it can be reported as
`verified_portable`.

G23 does not change the meaning of `journal_commit`. It remains the approval/tag
commit already named by existing active pointers. G23 derives the pointer's
publication commit deterministically from sealed journal history rather than
using the current `HEAD` or redefining historical fields.

## Pointer authority and publication resolution

The existing approval path writes two ordered journal commits. The first
approval commit contains the approved `gig-proposal.json` and becomes the tag
target and `journal_commit`. The continuation commit contains the
`gig_accepted` handoff and `manifests/active-gig-version.json`; the continuation
can safely refer to the first commit without self-reference.

For a live active pointer, G23 must perform these checks in order:

1. Validate the live pointer's schema and read its `journal_tag` and
   `journal_commit`.
2. Resolve `journal_tag` with Git and require that it equals `journal_commit`.
3. Enumerate the direct child commits of `journal_commit` in the sealed local
   journal history. Exactly one child must contain a handoff whose front matter
   has `transition: gig_accepted` and
   `previous_journal_commit: <journal_commit>`, and that child must contain
   `manifests/active-gig-version.json`.
4. Read the active pointer from that child commit with `git show`, validate it,
   and require that its `journal_tag` and `journal_commit` identify the same
   approval. This child is the derived `pointer_publication_commit`; it is a
   read-time identity, not a new serialized authority field.
5. Canonicalize the live pointer and the pointer from the publication commit.
   Any difference, including a substituted `capability_manifest`, returns
   `refused_unsealed_pointer` before any manifest check or legacy portability
   classification occurs.

The result is independent of the current `HEAD`. A sealed approval whose
publication continuation has not yet been committed is a legitimate recovery
boundary, not a portable or corrupt pointer:

- zero matching publication children returns `refused_unpublished_pointer`;
  the caller may run the existing journal recovery path and retry later;
- more than one matching publication child, or a child with inconsistent
  handoff/pointer identity, returns `refused_ambiguous_publication`;
- neither result inspects `capability_manifest` or claims portability.

The implementation must not treat an arbitrary child, current `HEAD`, or a
schema-valid live projection as the sealed pointer authority.

## Capability-manifest verification

After `pointer_publication_commit` has passed, G23 performs the three manifest
checks in order:

1. `path_safe`: the pointer's artifact path is workpad-relative, contains no
   traversal segment, and resolves through no symlink;
2. `bytes_match`: the referenced manifest bytes exist and match both
   `content_sha256` and `size_bytes`; and
3. `gig_id_match`: the manifest's own `gig_id` equals the pointer's `gig_id`.

Failures return `refused_unsafe_path`, `refused_digest_mismatch`, or
`refused_unbound_manifest` respectively. The content digest is the manifest
version binding; no separate proposal-lineage cross-check or version ledger is
created.

## Source transport and installation

G23 v1 uses out-of-band local transport. A portability fixture copies the
capability manifest and its pinned source artifact from a simulated machine A
to a fresh machine B. The receiving home places the source artifact at the
existing G17 installer location:

```text
tools/.sources/<capability-id>.artifact
```

G23 verifies the source bytes against the manifest's existing
`source_constraints.required_digest` and identity before invoking
`install_local_capability` unchanged. No source locator field, package
registry, remote fetch, network access, or new installation backend is added.
Installed `tools/<capability-id>/` bytes are never transported between
machines; they are materialized locally by G17 from the transported source.

G23 does not add a Run-consumption fixture. Installation is not execution, and
`resolved_tools` remains the only Run-time tool-use authority. Any later Run
consumption requires an explicit, separately authorized selection under the
existing Run contract.

## Proposal-lineage resolution

`resolve_proposal_lineage(gig_id, active_version)` starts at the approved
proposal named by the sealed active pointer and walks `parent_proposal_id`
backward. For each proposal it reads the historical `gig-proposal.json` bytes
from the sealed Git journal history, validates the proposal, and verifies the
same `gig_id`. It returns the ordered chain from the original `create`
proposal through the current approved proposal.

The read path returns named terminal failures rather than looping or silently
using the live proposal file:

- `refused_cycle` for a repeated proposal identity;
- `refused_missing_parent` for an absent or unreadable parent;
- `refused_cross_gig_lineage` for a parent bound to another Gig; and
- `refused_lineage_authority` for a proposal that cannot be tied to sealed
  journal history.

Lineage resolution is read-only and cannot approve a proposal, advance the
active pointer, rewrite proposal bytes, or create a second authority ledger.

## Amendment invariants

1. `journal_commit` retains its existing tag/approval-commit meaning.
2. Pointer publication is derived from the unique matching `gig_accepted`
   child; G23 never trusts `HEAD` by itself.
3. A sealed-but-unpublished approval is explicitly refused as recoverable
   `refused_unpublished_pointer`, not treated as portable.
4. `capability_manifest` is optional-additive; legacy pointers remain valid and
   are reported non-portable rather than migrated implicitly.
5. Only `active-gig-version.schema.json` changes; the packaged resource count
   remains twenty-seven and all other resource hashes remain unchanged.
6. G17's installer remains unmodified and is the only installation authority.
7. No installed tool bytes, credentials, provider calls, network access,
   target effects, scheduler behavior, or Run execution authority is added.

## Required evidence before G23 closeout

The implementation must provide fixtures for:

- exact publication-child resolution, including sealed-but-unpublished and
  ambiguous-publication recovery states;
- live-pointer substitution with valid tag/commit fields refused before
  manifest inspection;
- legacy absent field, unsafe path, digest mismatch, and cross-Gig manifest;
- single-hop and three-or-more-hop lineage, cycle, missing parent,
  cross-Gig parent, and unsealed-history refusal;
- two-home out-of-band source transport and G17 installation with identical
  before/after evidence shape; and
- no writes to active-version, journal, proposals, installed-byte transport,
  network, credentials, or Run execution authority.

Mutation tests must kill the publication-child prerequisite, each manifest
check, every lineage guard, and the no-installed-byte transport boundary.
A fresh-wheel replay must exercise the same corpus without the source
checkout.
