# S11 — Refactor tests around behavior, not historical goals

**Requested:** 2026-09-21.  
**Status:** Bounded Phase 1 groundwork implemented on 2026-09-21. The source
inventory, measured baseline, explicit lanes and representative acquisition
migration are recorded under [S11 evidence](../evidence/); the focused
project-local lifecycle receipt now records all 13 cases passing. The earlier
missing-`questionary` result remains a historical shared-interpreter baseline,
not the current focused result. Terra independently confirmed the migration,
selector correction and exact focused receipt (13/13, exit 0, no skips/errors,
exact identities/local environment); this does not claim v0.1.7 shipped, clear
the later interface/vertical-proof gates, or authorize provider/model/live work.
**Scope:** v0.1.8 Phase 1 only. The operator's explicit Phase 1 authorization
supersedes this brief's earlier no-rerun/no-change constraint narrowly for the
bounded tests, support, markers and evidence listed below; later phases remain
planned and separately gated.

## Question

How should tests be organized by the behavior they protect and the boundary they
exercise, instead of old G01/G28/SCOUT goal identifiers that no longer explain
the current version-based product?

## First-foundation boundary

S11 establishes enough deterministic test structure to make the first vertical
slice reviewable without turning v0.1.8 into a repo-wide cleanup project. The
initial work is a bounded inventory and baseline, followed by one small
representative migration. It must preserve existing coverage, negative cases,
failure meanings and historical traceability while making the next feature's
behavior easy to find.

The inventory records, for each current test or installed verifier:

- the behavior/component it protects and any historical G01/G28/SCOUT reference;
- the exercised level (pure unit, service integration, CLI, installed package,
  end-to-end or live/provider);
- collection and execution time, setup cost and host/model/provider
  dependencies; and
- the current pass/fail/error/skip semantics, including known environment
  refusals, rather than treating a rename or a green subset as proof of parity.

The baseline is measured in the linked receipt; it is evidence for this bounded
slice, not a release-wide pass claim.
Ordinary deterministic checks must not depend on installed Codex/Claude/Ollama,
personal configuration, a provider, or an agent's interpretation of success.

Use explicit lanes so a changed component can be found by behavior without
collapsing unlike evidence:

| Lane | Boundary | Required evidence |
| --- | --- | --- |
| Unit | Pure behavior with deterministic fixtures; no Git, subprocess, package, model or provider work unless the behavior itself is the subject of a separately named test | Fast result and stable failure assertions |
| Integration | Real local service/storage seams selected explicitly, with bounded fixtures and recovery/duplicate cases | Focused receipt and setup declaration |
| CLI | Public command/Run paths, including exit code and operation receipt | Captured argv, output and failure semantics |
| Installed | Normally installed package/wheel and its public entry points | Installed identity plus verifier receipt; source-tree success is insufficient |
| Live/provider | Explicit opt-in only, with endpoint/model/consent and privacy boundary recorded | Separate live receipt; never part of ordinary tests |

The first migration is limited to representative behavior areas selected from
the inventory. Keep a small old-to-new mapping or evidence note where needed;
do not mass-rename goal-number files, delete slow tests, or block user value on
repo-wide cleanup without measured duplication, setup-cost or discovery
evidence. After the initial migration, every feature packet adds behavior tests
in the appropriate lane and updates the mapping only when evidence warrants it.

## Investigate

- Map current tests and installed verifiers to product behaviors and test levels:
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

## Incremental migration sequence

1. Inventory current behavior and historical identifiers, then capture the
   measured baseline and lane commands.
2. Freeze the mapping and failure/coverage semantics for a small pilot area;
   prove that the new behavior grouping finds the same cases before expanding.
3. Add deterministic behavior tests alongside the first v0.1.8 feature packet,
   keeping integration, CLI, installed and live checks in their explicit lanes.
4. Re-measure collection/setup cost and inspect failures. Migrate another area
   only when the evidence shows a discovery or cost benefit; otherwise retain
   the historical name and mapping.

This sequence makes S11 a foundation for delivery, not a prerequisite to
rename the entire repository before any feature can be useful.

## Phase 1 authorization and disposition

The 2026-09-21 operator instruction authorized GPT Luna to complete the S11
groundwork packet now, even though the original brief said to wait for v0.1.7
and to avoid reruns. That authorization is recorded narrowly: offline
inventory, deterministic measurements, lane markers, test-support tooling and
the bounded public-acquisition migration are in scope; production source,
schemas, storage, UI/acquisition features, provider/model execution, daemon
startup, private resumes/workpads, releases and commits remain out of scope.
The v0.1.7 prerequisite is still an independent status gate for later phases.

## Deliverable

A small proposed test tree, old-to-new mapping, measured baseline and incremental
migration plan with CI/marker/installed-verifier impacts. Demonstrate how to find
tests for a changed component without knowing its original goal number. Preserve
coverage and failure semantics; do not delete slow tests or rename everything
without evidence. Scope executable trials separately before running them. The
release roadmap uses the resulting inventory to freeze shared interfaces and
evaluation acceptance criteria before the acquisition, assessment and UI
packets diverge.

Phase 1 evidence is now recorded in:

- [S11 groundwork report](../evidence/S11-groundwork.md)
- [machine-readable test/verifier inventory](../evidence/S11-test-inventory.json)
- [bounded acquisition old-to-new mapping](../evidence/S11-acquisition-mapping.json)
- [before/after measurement receipts](../evidence/S11-baseline-collection.json)
- [project-local lifecycle measurement](../evidence/S11-lifecycle-uv.json)
- [project-local environment receipt](../evidence/S11-lifecycle-uv-environment.json)

Coordinate with [S10](S10-ollama-invocation-and-harness-onboarding.md) for no-model
execution. The migration moves no tests out of their protected behavior: all
ten pilot cases, including negative and CLI-boundary cases, remain mapped and
discoverable. The reused shared interpreter's missing `questionary` dependency
is retained as historical baseline evidence; after the declared locked extras
were synced into this checkout's project-local `.venv`, the lifecycle module
collected 13 parameterized cases and passed all 13 without provider/model work.

## Change log

- 2026-09-21: operator-authorized bounded Phase 1 groundwork completed; added
  source inventory, measured collection/pilot receipts, explicit behavior/lane
  markers, and the public-acquisition migration with historical mapping. Later
  release phases and v0.1.7 shipment status remain unchanged.
- 2026-09-21: synced declared extras only into this checkout's project-local
  `.venv` and recorded the focused lifecycle receipt: 13 collected/passed,
  with no broad, installed or live/provider execution.
- 2026-09-21: Terra independently verified the bounded migration/selector and
  focused execution receipt under task `task_6795b28e61ad` / dispatch
  `ctx_b6f405f97b53`; broad, installed, live/provider and release gates remain
  separate.
