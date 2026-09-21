# S11 — Refactor tests around behavior, not historical goals

**Requested:** 2026-09-21.  
**Status:** Brief only; research and implementation not started.  
**Scope:** v0.1.8; no new v0.1.7 release gate.

## Question

How should tests be organized by the behavior they protect and the boundary they
exercise, instead of old G01/G28/SCOUT goal identifiers that no longer explain
the current version-based product?

## Investigate

- Map current tests and installed verifiers to product areas and test levels:
  pure units, service integration, CLI, installed-package, end-to-end and live
  provider checks. Version numbers should not become replacement test silos.
- Propose behavior-based directories/names; keep historical goal traceability
  in an optional mapping or evidence document, not mandatory filenames.
- Measure time by test and setup cost. Identify unit tests doing unnecessary
  Git/subprocess/package/model work, duplicated scenarios and host-dependent
  fixtures. Preserve genuine integration tests in an explicit separate lane.
- Define fast deterministic unit tests, focused integration selection and
  release checks. Live-provider tests must be explicit opt-in; ordinary tests
  must not rely on installed Codex/Claude/Ollama or personal configuration.
- Run tests through deterministic commands and completion receipts, not agents
  repeatedly waiting/polling. Local/cloud models may interpret failures, not
  replace test assertions or determine process success.

## Deliverable

A small proposed test tree, old-to-new mapping, measured baseline and incremental
migration plan with CI/marker/installed-verifier impacts. Demonstrate how to find
tests for a changed component without knowing its original goal number. Preserve
coverage and failure semantics; do not delete slow tests or rename everything
without evidence. Scope executable trials separately before running them.

Coordinate with [S10](S10-ollama-invocation-and-harness-onboarding.md) for no-model
execution. This brief authorizes no test moves, deletions or suite reruns now.
