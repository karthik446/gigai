# G23 — Gig Self-Containment and Portability

- Status: Proposed for review; not activated
- Depends on: G17 completion (capability manifest and installation), G19
  completion and terminal handoff, G20 completion and terminal handoff, and
  G22 completion audit and terminal handoff; consumes G09 workpad and G04/G05
  registry/target-binding authority
- Unblocks: a later release-lane alpha-declaration goal; informs but does not
  unblock G21

## Outcome

G23 makes an approved Gig version answer, from its own pointer artifact and
without following the Review Bundle, exactly two questions: which capability
manifest does this version declare, and which proposals produced it. Today
`active-gig-version.json` names only `goal_graph`; a Gig's declared tools are
reachable only through the Review Bundle's `tool_requirements`, one authority
level removed from the Gig's own identity, and no code resolves the
`parent_proposal_id` chain backward from an active pointer.

G23 adds one additive `capability_manifest` reference field to
`active-gig-version`, whose requiredness (optional on existing pointers, or
required under a new minor schema version) is settled by the accepted
amendment, and one deterministic read path that verifies and resolves the
referenced capability manifest and the full proposal lineage.
Portability means a pinned capability manifest travels with the Gig version
and can be locally reinstalled through the existing G17 installer on a second
machine; it does not mean installed tool bytes or model outputs are copied
between machines. G23 does not execute a capability, does not change
`resolved_tools` Run-time authority, and does not declare an alpha or public
release; that is a separate, later release-lane goal.

## Contract gate

Before runtime implementation, G23 must read and cite:

- [G17's completion evidence](../../evidence/phase-3/G17/completion-audit.md)
  for the capability-manifest and capability-installation schemas, the
  per-Gig `tools/<capability-id>/` isolation rule, and the `manifests/
  capabilities/<manifest-id>.json` storage path;
- [G19's completion audit](../../evidence/phase-4/G19/completion-audit.md)
  and [terminal handoff](../../evidence/phase-4/G19/terminal-handoff.md) for
  the target-effect authority boundary this goal must not expand;
- [G20's completion audit](../../evidence/phase-5/G20/completion-audit.md)
  and [terminal handoff](../../evidence/phase-5/G20/terminal-handoff.md) for
  the current `active-gig-version.json` consumer set that a new field must not
  break; and
- [G22's completion audit](../../evidence/phase-2/G22/completion-audit.md) and
  [terminal handoff](../../evidence/phase-2/G22/terminal-handoff.md) for the
  proposal/approval surface this goal reads but does not change.

Before runtime code, an accepted additive amendment must resolve six points
raised in contract review and settle their exact schema and runtime shape:

1. **Field naming.** The new reference field is named `capability_manifest`,
   not `tool_manifest`. G17 already owns the term "capability manifest"
   (`capability-manifest.schema.json`); "tool manifest" risks being confused
   with the Run-time `resolved_tools` field in `run-manifest.schema.json`,
   which G23 does not touch.
2. **Backward compatibility.** `active-gig-version.schema.json` currently
   requires exactly `schema_version, gig_id, active_version,
   approved_proposal_id, goal_graph, journal_commit, journal_tag,
   approved_at, approved_by`. The amendment must state explicitly whether
   `capability_manifest` is added as an optional field (existing pointers
   remain valid and schema-conformant but read as non-portable, no
   `schema_version` bump) or as a required field under a new minor
   `schema_version` (existing pointers become invalid until re-approved,
   following the `CONFIG_SCHEMA_VERSION`/`PREVIOUS_CONFIG_SCHEMA_VERSION`
   explicit-migration pattern already used in `config.py`). The amendment
   must not leave this ambiguous or treat a missing field as silently
   equivalent to an empty manifest.
3. **Semantic verification, not just a schema field.** A schema-valid
   `artifact_ref` proves bytes exist at a path; it does not prove the
   referenced capability manifest belongs to this Gig or this version, and it
   does not prove the pointer being read is itself the sealed, approved
   pointer rather than a locally substituted file with the same shape. The
   amendment must define a portability-verification routine with one
   prerequisite step followed by three manifest checks, in order:

   - **Prerequisite — pointer-authority revalidation.** `run.py:268`'s
     `_resolve_authority` does not read `active-gig-version.json` from the
     sealed commit; it reads the *live* projection, confirms the live
     pointer's own `journal_tag` resolves via `git rev-parse --verify` to a
     commit equal to the live pointer's own `journal_commit` field, and only
     then reads `gig-proposal.json` and `goal-graph.json` — not
     `active-gig-version.json` itself — from that sealed commit via
     `git show <commit>:...`. That check proves the live pointer's
     `journal_tag`/`journal_commit` pair is internally consistent with Git;
     it does not prove every other field on the live pointer, including a
     future `capability_manifest`, matches what was actually sealed at that
     commit. A locally substituted live pointer that keeps `journal_tag` and
     `journal_commit` untouched but carries a different, schema-valid,
     same-`gig_id` `capability_manifest` would pass `_resolve_authority`'s
     existing check unmodified.

     G23 therefore adds a new sealed-pointer comparison rather than merely
     reusing `_resolve_authority` as-is: read the live active pointer; read
     `manifests/active-gig-version.json` from `journal_commit` via
     `git show <commit>:manifests/active-gig-version.json`; parse and
     canonicalize both documents, compare the complete live pointer against
     the complete sealed copy, and refuse as
     `refused_unsealed_pointer` on any mismatch, before `capability_manifest`
     is inspected for any other check. This extends the same trust boundary
     `_resolve_authority` and `_validate_authority` (`run.py:319`) already
     enforce for `goal-graph.json` and `gig-proposal.json` to
     `active-gig-version.json` itself, which neither function currently
     covers.
   - The referenced path is workpad-relative and contains no symlink or
     traversal segment.
   - The artifact's `content_sha256` and `size_bytes` match the bytes on
     disk.
   - The capability manifest's own `gig_id` equals the active pointer's
     `gig_id`.

   Because `capability_manifest` is a content-addressed `artifact_ref` (path
   plus exact digest), digest equality is itself the version-binding
   mechanism — the referenced bytes cannot be silently newer or older than
   what the sealed `active-gig-version.json` names. Without the prerequisite
   step, an attacker or a corrupted local checkout could replace the live
   pointer file with a schema-valid pointer to a different same-Gig
   capability manifest, and the three manifest checks alone would pass even
   though the pointer itself was never the one Git actually sealed.

   There is currently no path from a `gig-proposal` to a capability
   manifest: `creation_manifest` is a single generic `artifact_ref` slot
   already populated with `manifests/creation-manifest.json` for `kind:
   "create"` proposals (`lifecycle.py`) and `manifests/improvement-
   manifest.json` for `kind: "improve"` proposals (G20's `improvement.py`);
   G17 capability manifests are linked only through the Review Bundle's
   `tool_requirements`, never through `gig-proposal`. G23 does not add a
   second use of `creation_manifest`, does not add a new proposal field, and
   does not change `gig-proposal.schema.json`.

   The amendment instead makes `active-gig-version.json`'s
   `capability_manifest` reference authoritative on its own terms, exactly
   as `goal_graph` already is: the same trusted approval-time lifecycle code
   that writes the pointer (`lifecycle.py`'s `_approve_*` functions) is what
   binds `capability_manifest` to the approved version, in the same atomic
   write as `goal_graph`, `approved_proposal_id`, and `journal_tag`. There is
   no separate proposal-lineage cross-check to re-derive at verification
   time, because the pointer write is already the single point where Gig
   identity, approved version, and the capability manifest are bound
   together. Verification (this goal's read path) revalidates path safety,
   byte/digest match, and `gig_id` match against the pointer as written; it
   does not re-walk `gig-proposal.parent_proposal_id` to reconstruct that
   binding. The failure named `refused_unbound_manifest` covers the case
   where the referenced bytes' own `gig_id` does not match the pointer's
   `gig_id` — the one case digest equality against a corrupted or
   substituted file cannot catch on its own.
4. **Lineage resolution.** The amendment must fully specify a
   `resolve_proposal_lineage(gig_id, active_version)` read path: it walks
   `parent_proposal_id` backward from `approved_proposal_id` to a proposal
   with `parent_proposal_id: null`; it detects cycles and refuses rather than
   loops; it fails closed on a missing or unreadable parent; it verifies
   every proposal in the chain shares the same `gig_id`; and it reads
   historical proposal bytes from the sealed workpad journal history (the
   Git object the journal commit names), not only whatever proposal file
   currently exists in the live workpad tree, since an intermediate proposal
   file is not guaranteed to remain uncommitted-but-present after later
   approvals.
5. **Two-machine source transport.** A `content_sha256` digest alone cannot
   re-materialize bytes on a machine that never had them. The amendment must
   either define an explicit source locator carried alongside the pinned
   digest in the capability manifest's existing `source_constraints` object
   (`allowed_source_kinds`, `required_digest`, `required_identity` — there is
   currently no `required_source` field), or explicitly scope G23's v1
   fixture to the case where the source artifact is transported out-of-band
   (for example, copied alongside the workpad export) and the digest is used
   only to verify it after arrival. The amendment must not imply that a
   digest by itself
   performs reinstallation, and must state plainly that installed tool bytes
   are never copied between machines — only the pinned source artifact and
   its manifest travel, and G17's existing local installer re-runs on the
   receiving machine.
6. **Install versus execution.** G17 installs a capability into
   `tools/<capability-id>/` but never executes it; `run-manifest.schema.json`
   and `run-brief-frontmatter.schema.json` `resolved_tools` remain the only
   Run-time tool-use authority, and today `run.py` always resolves
   `resolved_tools` to an empty list — no Run currently consumes an
   installed capability. If G23's acceptance evidence includes a fixture
   where a Run consumes a locally reinstalled capability, the amendment must
   define that consumption as an explicit, separately authorized selection
   recorded in the existing `resolved_tools` shape, not an automatic
   consequence of installation, and must not otherwise expand Run authority.
   If no such fixture is included, the amendment must say so explicitly
   rather than let "portable and executable" be implied.

## First implementation boundary

The first implementation is intentionally narrow:

- one bound local Gig with one approved `active-gig-version.json` and zero or
  more prior approved versions;
- one `capability_manifest` artifact reference added to
  `active-gig-version.schema.json`, whose requiredness is settled by the
  accepted amendment;
- one read-only portability-verification routine over an existing active
  pointer;
- one read-only proposal-lineage resolver over the existing `gig-proposal`
  chain and sealed journal history; and
- one local reinstallation fixture that re-runs G17's existing installer from
  a transported pinned source artifact on a second disposable home directory,
  simulating a second machine.

The implementation does not execute a capability by default, does not create
a new proposal kind, does not change `gig-proposal.schema.json`, and does not
add a package registry, remote fetch, or network resolution step.

## In scope

- Amend `active-gig-version.schema.json` with the settled `capability_manifest`
  field per the contract-gate naming and compatibility decision, preserving
  every other existing required field and the schema's `additionalProperties:
  false` shape.
- Bind `capability_manifest` writes to the existing G17
  `manifests/capabilities/<manifest-id>.json` artifact; G23 does not
  introduce a second capability-manifest storage location.
- Implement `verify_gig_portability(resolved_workpad)` that first performs
  the new sealed-pointer comparison from Contract gate point 3 — reading
  `active-gig-version.json` from `journal_commit` via `git show` and
  comparing it against the live pointer, refusing `refused_unsealed_pointer`
  on any mismatch — then performs the three semantic checks against the
  sealed pointer, and returns a typed pass/fail result naming the exact
  failing check; never a boolean alone and never a check performed against
  an unrevalidated live-tree pointer.
- Implement `resolve_proposal_lineage(gig_id, active_version)` performing the
  cycle-detection, missing-parent, cross-Gig, and terminal-root checks from
  Contract gate point 4, reading historical proposal bytes from the sealed
  journal history.
- Define the source-transport shape settled in Contract gate point 5 and
  implement a disposable-fixture reinstallation path that: starts from a
  transported pinned source artifact and its capability manifest; verifies
  the artifact digest before use; and invokes G17's existing
  `install_local_capability` unmodified on a fresh disposable `home_root`.
- Produce fixtures for: a portable version (manifest present, verified,
  reinstallation succeeds); a non-portable legacy version (field absent,
  reported as such, never treated as a failure of the old pointer); a
  cross-Gig manifest mismatch (refused); a symlink/traversal path (refused);
  a digest-mismatch artifact (refused); a lineage cycle (refused); a missing
  intermediate proposal (refused); and a multi-hop lineage (three or more
  proposals) resolved correctly end to end.
- If the optional Run-consumption fixture from Contract gate point 6 is
  included, record the explicit selection artifact and prove
  `resolved_tools` reflects exactly the reinstalled capability with no other
  Run-authority change.

## Out of scope

- Declaring an alpha, beta, or public release status; updating
  `Development Status` classifiers; PyPI republication; or any README/cheat
  sheet "what is implemented" correction. Those belong to a later, separately
  numbered release-lane goal following the G12 pattern, not to G23.
- Bundling installed tool bytes, workpad bytes, or model outputs into a
  single portable archive. G23 defines reference-based portability only.
- A package registry, remote index, network-resolved source, or any
  installation backend beyond G17's existing pinned local artifact path.
- Changing `gig-proposal.schema.json`, adding a new proposal `kind`, or
  changing how `active-gig-version.json` is approved or advanced. G20's
  approval/version authority is unchanged.
- Changing `resolved_tools`, `run-manifest.schema.json`, or default Run
  behavior. A Run consuming a reinstalled capability is, at most, one
  explicit opt-in fixture; it is never a default execution path.
- Automatic capability execution, credential acquisition, provider calls, or
  any network request. G23 reads local bytes and re-runs G17's existing
  offline installer only.
- Target mutation, recurring Runs, comparative history, or scheduling. Those
  remain G19's and G21's authority.
- Retroactively adding `capability_manifest` to already-approved historical
  versions. A prior version without the field stays exactly as approved;
  portability is available going forward from the version that adopts it.

## State and authority contract

Portability verification and lineage resolution are read-only checks over
existing authority; they introduce no new write path:

```text
active_pointer_read (live workpad-tree copy)
        |
        v
sealed_pointer_read (git show journal_commit:manifests/
                      active-gig-version.json)
        |
        v
pointer_sealed? (canonical live pointer equals the complete
                 canonical sealed pointer document)
        |
        +--no--> refused_unsealed_pointer
        | yes
        v
capability_manifest_present? --no--> reported_non_portable (terminal, informational)
        | yes
        v
   path_safe? --no--> refused_unsafe_path
        | yes
        v
  bytes_match? --no--> refused_digest_mismatch
        | yes
        v
  gig_id_match? --no--> refused_unbound_manifest
        | yes
        v
   verified_portable
```

`pointer_sealed` is a prerequisite, not one of the three manifest checks,
and it is new code, not a reuse of `run.py:268`'s `_resolve_authority`.
`_resolve_authority` reads the *live* pointer, checks only that its own
`journal_tag`/`journal_commit` fields are mutually consistent with what Git
actually resolves, and then reads `gig-proposal.json`/`goal-graph.json` —
never `active-gig-version.json` itself — from the sealed commit. That leaves
a gap: a locally substituted live pointer that keeps `journal_tag` and
`journal_commit` untouched but carries a different `capability_manifest`
would satisfy `_resolve_authority`'s existing check unmodified. G23 closes
that gap by reading `active-gig-version.json` from the sealed commit as well
and comparing it against the live copy field-by-field, refusing on any
mismatch before `capability_manifest` is inspected for any other check.

`gig_id_match` is the one remaining semantic check beyond path safety and
digest equality, and it is also the version-binding check: because
`capability_manifest` is a digest-pinned `artifact_ref`, matching bytes
already prove this is the exact manifest revision the pointer names: there
is no separate "correct manifest but wrong version" state to distinguish.
`refused_unbound_manifest` names the case where the manifest's own `gig_id`
does not match the pointer's `gig_id` — a corrupted, substituted, or
cross-Gig file at the referenced path.

Lineage resolution is a separate read:

```text
approved_proposal_id
        |
        v
  walk parent_proposal_id backward
        |
        +--> cycle detected --> refused_cycle
        |
        +--> parent missing/unreadable --> refused_missing_parent
        |
        +--> parent gig_id mismatch --> refused_cross_gig_lineage
        |
        v
parent_proposal_id: null --> lineage_resolved (ordered proposal list)
```

The authority rules are non-negotiable:

1. `active-gig-version.json` and the workpad journal remain the sole
   active-version authority. `capability_manifest` is descriptive metadata on
   that pointer, never a second approval or version signal.
2. `reported_non_portable` is not a refusal and not an error. An existing
   Gig version without the field remains fully valid for every purpose it
   already served; only its portability-check result changes.
3. Every refusal in the diagrams above names its exact failing check. No
   refusal is reported as a generic verification failure.
4. G17's `install_local_capability` is reused unmodified. G23 supplies its
   preconditions (verified source artifact, disposable target root); it does
   not fork or reimplement installation.
5. Lineage resolution never writes, mutates, or reorders proposal or journal
   records. It is a pure read over already-sealed history.
6. `resolved_tools` remains Run-time authority. Nothing in this goal permits
   a Run to consume a capability that was not explicitly selected through the
   existing resolved-tools shape.
7. No manifest check runs against an unrevalidated pointer. `pointer_sealed`
   is checked first, every time, by comparing the live pointer against
   `active-gig-version.json` read from its sealed `journal_commit`; a
   live-tree pointer that diverges from that sealed copy is refused before
   `capability_manifest` is read for any purpose, including the
   `reported_non_portable` case. This check is new: it extends, rather than
   reuses unmodified, the tag/commit boundary `run.py:268` already enforces
   for `gig-proposal.json` and `goal-graph.json`.

## Acceptance criteria

1. **Contract gate.** G23 cites the completed G17, G19, G20, and G22 evidence
   above and records an accepted additive amendment resolving all six
   contract-gate points: field naming, backward compatibility, semantic
   verification, lineage resolution, source transport, and the
   install/execution boundary. No existing schema meaning changes by
   inference.
2. **Schema amendment.** `active-gig-version.schema.json` gains exactly
   `capability_manifest` under the settled optional-or-versioned shape from
   point 2. Every other existing field, the `additionalProperties: false`
   shape, and all prior schema hashes outside this one file remain
   unchanged. The amendment states the exact new packaged-resource count.
3. **Pointer-authority prerequisite.** A fixture with a live
   `active-gig-version.json` whose canonical document diverges from the
   sealed copy read via `git show journal_commit:manifests/active-gig-version.json`
   is refused `refused_unsealed_pointer` before any manifest check runs. The
   fixture must specifically cover the case the tag/commit check alone
   cannot catch: a locally substituted live pointer that keeps a valid,
   resolvable `journal_tag`/`journal_commit` pair but carries a different,
   schema-valid, same-`gig_id` `capability_manifest` than the sealed copy
   names. A fixture with a live pointer that matches the sealed copy exactly
   passes the prerequisite and proceeds to the three manifest checks.
4. **Portability verification.** `verify_gig_portability` fixtures cover
   present-and-valid, absent (`reported_non_portable`), unsafe path,
   digest mismatch, and cross-Gig/unbound manifest
   (`refused_unbound_manifest`) cases, each returning the exact named result
   from the state diagram, and each exercised only after the pointer-
   authority prerequisite has already passed.
5. **Lineage resolution.** `resolve_proposal_lineage` fixtures cover a
   single-hop chain, a three-or-more-hop chain, a cycle, a missing
   intermediate parent, and a cross-Gig parent, each returning the exact
   named result. The resolved lineage for a valid multi-hop chain lists every
   proposal in order from the original `create` proposal to the current
   approved proposal.
6. **Reinstallation fixture.** A disposable two-home-directory fixture
   transports a pinned source artifact and its capability manifest from a
   simulated "machine A" home to a fresh "machine B" home, verifies the
   artifact digest before use, and reproduces an `installed` outcome via
   G17's unmodified installer with a before/after manifest identical in
   shape to G17's existing installation evidence.
7. **No installed-byte transport.** A fixture proves that `tools/
   <capability-id>/` bytes are never read, copied, or referenced by G23's
   portability check or lineage resolver — only the capability manifest and,
   where the source-transport shape requires it, the pinned source artifact.
8. **Install/execution boundary.** If a Run-consumption fixture is included
   per Contract gate point 6, it proves an explicit selection artifact
   exists and that `resolved_tools` names exactly the reinstalled capability
   with no other Run-authority field changed from its G19/G20 baseline
   shape. If no such fixture is included, the completion audit states this
   explicitly rather than leaving it implied.
9. **No approval or version-authority change.** Fixtures prove that running
   portability verification or lineage resolution does not write to
   `active-gig-version.json`, the workpad journal, or any `gig-proposal`
   record, and does not require or perform an approval action.
10. **Backward-compatibility proof.** A fixture using a pre-G23 active-version
    pointer (no `capability_manifest` field) validates against the amended
    schema exactly as settled in point 2, and every existing G16/G19/G20/G22
    consumer of `active-gig-version.json` continues to pass unmodified.
11. **Effect boundary.** G23 performs no network request, credential lookup,
    provider call, target mutation, or Git commit beyond what G17's existing
    installer already performs in its own accepted boundary. Static
    import/process-guard checks prove this.
12. **Installed replay.** A freshly built wheel, installed into a disposable
    environment, verifies the amended schema count and replays the
    pointer-authority, portability-verification, lineage-resolution, and
    two-home reinstallation fixtures without a source checkout or network.
13. **Closeout evidence.** Evidence under
    `docs/development/evidence/phase-5/G23/` includes the accepted
    amendment, portability and lineage fixture corpus, two-home
    reinstallation record, mutation report for the pointer-authority
    prerequisite, the three semantic-verification checks, and the
    cycle/missing-parent lineage checks, installed replay, completion audit,
    and terminal handoff. The handoff explicitly states that alpha/release
    declaration remains a separate, later release-lane goal.

## Verification and evidence

- Schema vectors for `active-gig-version.schema.json` covering the field
  present, absent, and malformed, with all prior fields unchanged.
- A pointer-authority fixture proving a live pointer whose complete canonical
  document matches its sealed `journal_commit` copy passes, and a live pointer
  whose document diverges from that sealed copy — including one that keeps a valid,
  resolvable `journal_tag`/`journal_commit` pair but substitutes an
  otherwise-valid, same-`gig_id` `capability_manifest` — is refused
  `refused_unsealed_pointer` before any manifest check runs.
- Semantic fixtures for each of the three portability checks (path safety,
  digest match, `gig_id` match — digest match doubling as the version-binding
  proof), each independently triggerable and independently refusable, and
  each exercised only against an already-sealed pointer.
- Lineage fixtures for single-hop, multi-hop, cycle, missing-parent, and
  cross-Gig cases, reading proposal bytes from sealed journal history rather
  than only the live workpad tree.
- A two-disposable-home-directory reinstallation fixture with before/after
  manifests matching G17's existing installation-evidence shape.
- A negative fixture proving installed tool bytes are never read or
  transported by the new code paths.
- Mutation tests removing the pointer-authority prerequisite, each semantic-
  verification check, and each lineage guard, proving the corresponding
  negative fixture fails against the mutant.
- A fresh-wheel installed replay with sanitized evidence.

Evidence lives under `docs/development/evidence/phase-5/G23/`. Raw model
outputs, credentials, ambient paths, and workstation-specific logs do not
ship as evidence.

## Stop boundary

Stop before runtime implementation if the amendment cannot settle field
naming, the backward-compatibility shape, the pointer-authority prerequisite
(a new sealed-pointer comparison extending, not merely reusing unmodified,
`run.py:268`'s tag/commit revalidation), the three semantic-verification
checks, full lineage resolution including cycle detection and a
historical-bytes source, the source-transport shape, or the
install/execution boundary without inference.

Stop before any portability check runs against a pointer that has not first
passed pointer-authority revalidation. Reading `capability_manifest` from an
unrevalidated live-tree pointer is never acceptable, even for the
`reported_non_portable` case.

Stop before shipping a reinstallation fixture if the source-transport
mechanism cannot be exactly specified — a bare digest is not sufficient
evidence that reinstallation is possible, and the amendment must not imply
otherwise.

Stop before any Run-consumption fixture if it would require changing
`resolved_tools` semantics, defaulting a Run to use a reinstalled capability
without explicit selection, or expanding Run authority beyond G19/G20's
accepted boundary. G23 does not declare an alpha or public release; that
remains a separate, later release-lane goal outside this contract.
