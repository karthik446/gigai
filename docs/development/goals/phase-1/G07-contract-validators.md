# G07 — Contract Validators

- Status: Approved; blocked by G01 and G02
- Depends on: G01, G02
- Unblocks: G08

## Outcome

Implement complete, named validators for the Gig Proposal artifacts, Goal
documents, and Goal Graph semantics required by V14 Section 6.5, using the
canonical production APIs and installed black-box harness.

## In scope

- Validate all eight serialized contracts against the single packaged schema
  set.
- Rewrite the proven graph validator behind named production interfaces.
- Validate the required Markdown proposal artifacts and their correspondence
  to the machine-readable manifests.
- Prove stable Goal IDs and versions; acyclic dependencies and recovery; entry,
  terminal, reachability, join, and typed-outcome rules.
- Validate compatible parallel effects or declared isolation.
- Validate satisfiable aggregate and per-Goal budgets.
- Prove that every terminal path yields required Gig completion evidence.
- Resolve every referenced tool and executor as installed, materialized by a
  predecessor Goal, or explicitly blocking.
- Return deterministic, actionable validation findings suitable for CLI and
  structured consumers.

## Out of scope

- Generating a proposal or repairing an invalid graph automatically.
- Approving user authority, executing a capability, or resolving live provider
  facts.
- Changing schemas, vectors, graph semantics, or accepted defaults.
- Treating JSON Schema success alone as semantic Goal Graph validity.

## Acceptance criteria

1. Every required artifact and cross-reference has one named production
   validator and corresponding positive and negative tests.
2. Validation rejects duplicate or unstable IDs, dependency or recovery
   cycles, missing entry or terminal paths, unreachable required Goals, and
   invalid join predecessors.
3. Validation rejects undeclared automatic outcomes, incompatible parallel
   effects, impossible budgets, incomplete terminal evidence, and unresolved
   non-blocking tools or executors.
4. Markdown and manifest disagreement fails before approval.
5. Schema enumeration asserts exactly the eight packaged resource names and
   cannot pass vacuously.
6. Findings are deterministic across supported Python versions and contain no
   personal absolute paths.
7. Installed-command `check` scenarios consume the production validators, even
   if the broader command surface remains incomplete.

## Verification and evidence

- A requirement-to-test matrix for every Section 6.5 validation rule.
- Valid multi-node, parallel, join, recovery, and blocking graph fixtures.
- One explicit invalid fixture per semantic rejection class.
- Installed `check` black-box evidence, schema-resource hashes, and completion
  audit.

## Stop boundary

Stop at deterministic validation. Do not generate, revise, approve, execute,
or silently repair a proposal in this goal.
