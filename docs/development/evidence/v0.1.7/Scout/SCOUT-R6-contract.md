# R6 runtime comparison contract

Status: Luna implementation contract, 2026-09-12. This is a bounded,
synthetic contract and does not claim release acceptance or live provider proof.

## Public API

`gigai.runtime_comparison` is domain-neutral. It has no Scout imports and accepts
an explicitly selected `EvaluationPack`, configured `local_target` and
`luna_target`, and direct operator consent. `load_evaluation_pack()` loads the
installed `runtime-comparison-pack-v1.json` when no path is supplied;
`validate_grader()` proves one known-good and one known-bad vector before any
candidate case is graded; `grade_output()` uses strict output shape, criterion
identity/status, evidence-ID allowlisting, expected verdict, and structured
unsupported-claim checks. `run_comparison()` calls the existing
`run_model_invocation()` seam by default; tests may supply an injected
invocation service/transport at that seam. `show_comparison()` reads only the
journal-authenticated immutable comparison artifact, and
`render_comparison_markdown()` produces the readable table.

The CLI is explicit: `gigai comparison start` and its `run` alias require
`--gig`, `--local-target`, `--luna-target`, and `--confirm`; `gigai comparison
show COMPARISON_ID --gig GIG_ID` reloads a result without invoking either setup.
The default pack is installed package data, so the operation does not depend on
a source checkout. No command installs models, starts daemons, retries across
setups, forwards private records, changes application state, chooses a winner,
or promotes a default.

## Durable identity and failure rules

Each setup receives a separate `run_...` directory and journaled `run_started`,
per-case terminal evidence, and terminal Run transition. Each case keeps exact
input digest, output text (or null), structured error, wall time, retry count,
usage (including unknown usage), invocation ID, and grader result. A failure in
one setup leaves its Run and the other setup's Run intact. The final immutable
`comparisons/comparison_....json` links both Run manifests, the selected graph,
goal, pack and grader versions, setup identity, prompt/harness settings, and
application-state invariant (`false`). Re-reading the artifact verifies its
journal publication, path, digest, and Gig identity.

The pack freezes the synthetic case IDs/versions, selected graph and goal,
input/output contract versions, source evidence, and grader. It includes both
development and held-out cases. The grader is deterministic and does not use
keyword scores or model self-grading; source IDs must come from the case's
allowlist and unsupported claims must be explicit.

## Schema/resource registration

The additive resources are `runtime-evaluation-pack.schema.json`,
`runtime-comparison-attempt.schema.json`, and `runtime-comparison.schema.json`.
They are registered in the versioned validator inventory and pinned in
`src/gigai/schemas/SHA256SUMS`; the synthetic pack is included as
`gigai.data` package data.

