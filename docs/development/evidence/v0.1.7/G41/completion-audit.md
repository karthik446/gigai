# G41 Completion Audit

**Goal:** G41 — Project-Local `.gigai/` Package Boundary
**Release:** v0.1.7
**Outcome:** Complete

## Delivered contract

G41 now provides a strict portable package boundary at
`.gigai/packages/<package-id>/package.json`. Package identity and content
inventory are schema-validated and content-digested. Package operations do not
create Gig, proposal, approval, active-version, capability, journal, workpad,
or Run authority.

`gigai init` creates or reconciles one package and changes the predecessor
whole-directory Git exclude to the exact private-path rules. Adoption requires
`--adopt-package --confirm`, allows one valid ignored project binding beside a
fully portable tracked package, and refuses tracked private, unknown, mixed, or
conflicting content. The cutover is re-read and verified after publication.
Package inspection rejects symlink roots and components, directory/manifest
identity mismatch, executable material, oversized files, unsafe command
directories, and inventory/digest mismatch.

`gigai package export` copies only validated portable bytes, refuses conflicting
or unsafe destinations, and is idempotent for an equivalent destination.
`gigai package install` validates the destination home configuration before
copying, reconciles the Git package boundary, and writes one idempotent private
installation record. `gigai upgrade --confirm` creates a private predecessor
configuration backup, migrates configuration, fingerprints logical registry,
workpad, and existing private-home evidence, preserves populated authority, and
restores predecessor state if publication or post-publication verification
fails.

## Evidence

- `tests/test_g41_package_boundary.py`: 14 passed, including fresh init,
  exact excludes, adoption, private-content refusal, second-home installation,
  export validation/idempotence, symlinked-parent and package identity refusal,
  home-binding refusal,
  idempotent upgrade after the package is tracked, populated
  registry/workpad/private-home preservation, rollback before and after
  publication, and refusal of unbound tracked packages without explicit
  adoption.
- `tests/test_g04_installed_scenarios.py`,
  `tests/test_project_registry_and_target_binding.py`, and
  `tests/test_g40_runtime.py`: included in the focused 72-test run; cover
  installed CLI behavior, non-Git targets, aliases/symlinks, lock recovery,
  concurrent initialization, and existing runtime invariants.
- `research/contract_spike/tests/test_schemas.py` and the existing schema
  contract suites accept the additive thirty-second package schema resource.
- Full suite: `650 passed, 1 skipped, 70 subtests passed`.
- Source compilation, `git diff --check`, and the focused package/runtime
  validation all pass.

## Review remediation

The follow-up reviews identified five gaps in the original closeout. They are
resolved here: normal `gigai init` accepts its own tracked portable package
only when an existing project binding is present; an unbound tracked package
requires explicit adoption; post-publication upgrade verification failures use
the same targeted rollback boundary as publication failures; package export and
destination identity, including symlinked parent rejection, are validated; and
all G41 Markdown artifacts are free of trailing whitespace.

The installed-scenario guard also required the package Git inspection to use a
resolved executable path, rather than a literal command name; that boundary is
now covered by the complete suite. Upgrade preservation additionally verifies
the configured home identity and hashes existing private-home evidence without
recording its contents.

## Authority and security result

The project binding and private registry remain authoritative. Portable package
installation imports no project or lifecycle authority. No package hook,
network call, provider invocation, credential read, executable installation,
or automatic Run is performed. Migration recovery material remains private.

## Boundary handed forward

G42 may populate validated package definitions for the built-in catalog. G44
may define clone/create-from lineage and semantic authoring. Neither authority
is implemented or activated by G41.

## Known bounded limitation

G41 establishes the package container and manifest; it does not define the
built-in catalog, adaptive orchestration, clone lineage, or domain Gig content.
The initial package is intentionally empty until a later goal supplies a
validated definition.
