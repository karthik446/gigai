# G03 — Setup, Configuration, and Diagnostics

- Status: Approved; blocked by G01 and G02
- Depends on: G01, G02
- Unblocks: G04

## Outcome

Implement idempotent local setup, typed configuration, credential references,
deterministic offline adapter materialization, and the base diagnostics needed
to prove that the configured environment is safe for later target and workpad
operations.

## In scope

- Interactive and non-interactive setup for the user-local GigAI home,
  user-selected workpad mount, editor command, deterministic offline endpoints,
  model targets, profiles, and the standard pack.
- Typed, canonical configuration with explicit schema/version handling.
- Credential references that never copy secret values into configuration,
  workpads, logs, manifests, or test fixtures.
- Idempotent reruns that preserve equivalent configured state.
- Base offline `doctor` checks for paths, permissions, atomic replacement,
  interprocess exclusion, editor resolution, and offline adapter availability.
- Deterministic offline adapters used by Phase 1 scenarios.

## Out of scope

- Live provider authentication or network probes.
- Target binding, private Gig repositories, full journal semantics, or creation.
- Silent migration of ambiguous or corrupt configuration.
- Making the default workpad location authoritative when the user configured a
  different mount.

## Acceptance criteria

1. Setup produces a complete typed configuration without storing raw
   credentials.
2. Repeating setup with the same answers produces no semantic configuration
   change and no duplicate standard-pack materialization.
3. A user-selected workpad root is preserved and reported as the authority.
4. Missing, read-only, malformed, and version-incompatible configuration fails
   with actionable diagnostics rather than partial mutation.
5. Base `doctor` runs without network access or tokens and reports structured
   pass, warning, and failure results.
6. Atomic-replacement and two-process exclusion probes execute on the actual
   configured workpad mount.
7. Editor and subprocess commands are represented and invoked as structured
   argv.
8. All behavior passes through the installed-CLI scenario harness.

## Verification and evidence

- Fresh setup, idempotent rerun, alternate-mount, and corrupt-config scenarios.
- Secret-canary scans over configuration, logs, manifests, and output.
- Two-process exclusion and atomic-replacement probe evidence.
- Offline/network-denial evidence and a completion audit.

## Completion evidence

- [Completion audit](../../evidence/phase-1/G03/completion-audit.md)
- [Terminal handoff](../../evidence/phase-1/G03/terminal-handoff.md)
- [Verification summary](../../evidence/phase-1/G03/verification-summary.json)

## Stop boundary

Stop after setup and base diagnostics are independently useful and verifiable.
Do not bind a target or create a Gig workpad in this goal.
