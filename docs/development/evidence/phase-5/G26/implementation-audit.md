# G26 Implementation Audit — Active Milestone

- Status: Implementation milestone verified; G24 human UAT remains open
- Date: 2026-08-13
- Scope: deterministic/offline builder path and model-readiness boundary

## Delivered

- Added the additive `gig-builder-session.schema.json` and
  `proposal-draft-manifest.schema.json` resources; the packaged inventory is
  now 29 resources and prior schema hashes remain unchanged.
- Added semantic validators for intent-digest binding, usable-target
  readiness, configured-provider network policy, and workpad-only effects.
- Added read-only discovery for installed `codex` and `claude` executables and
  configured-target readiness reporting through `gigai models`.
- Changed the default browser-first `gigai create` flow so the selected model
  supplies typed follow-up questions, Build proposal is explicit, model draft
  artifacts are subordinate to `gig-proposal`, and approval still uses the
  existing lifecycle authority.
- Added explicit Revise answers and Reject draft actions. Rejection is
  terminal; revision clears the previous draft identity and requires another
  explicit build.
- Added sanitized contract, builder, discovery, browser-flow, and installed
  replay tests. No raw UAT data, prompts, provider output, credentials, or
  local databases are included here.

## Evidence

- Focused G22/G26 regression set: `14 passed`.
- Full repository run: `544 passed, 64 subtests`; one existing
  multiprocessing journal-lock race failed during the full run and passed in
  isolation. The failure was `mount.interprocess_lock` probe timing in
  `tests/test_journal_locking_recovery.py`, outside the G26 change surface.
- Source and installed schema verifier: `verified 29 installed GigAI schemas`.
- Fresh-wheel installed G26 replay: `verified installed GigAI G26 builder
  contract`; it reached model draft creation and explicit approval in a
  disposable home/target.
- `git diff --check` and focused Ruff checks pass.

## Remaining before G26 closeout

- Run real G24 UAT with a configured/evidenced builder target and inspect the
  resulting workpad/SQLite artifacts with the operator.
- Add full timeout, cancellation, budget, stale-event, selected-reference
  exclusion, and mutation evidence for the builder-specific guards.
- Complete the G26 completion audit and terminal handoff after that UAT
  evidence is sanitized and accepted.
