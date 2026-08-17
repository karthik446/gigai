# G28 — v0.1.5 Readiness Evidence

- Status: Implementation in progress
- Recorded: 2026-08-16
- Candidate version: not released

## Current checkpoint

The first G28 evaluation plumbing checkpoint is complete. The committed
G26/G27 corpus is scored through a separately supplied deterministic Solver
observation set across Development, Calibration, and Final Held-Out Acceptance
splits.

The reports prove:

- the manifest contract validates;
- observations are bound to the exact manifest digest;
- contamination metadata is required;
- expected/forbidden behavior is scored independently; and
- report status distinguishes `methodology_plumbing`, `behavior_scored`, and
  `candidate_judge_scored`.

They do **not** prove production model or candidate-judge quality. The Solver
is `deterministic_fixture`, and every report correctly records
`candidate_judge_scored: false`. The final-held-out result is a plumbing
demonstration, not an alpha-quality claim.

## Tiered verification checkpoint

The four G28 tiers are independently runnable and independently reported:

| Tier | Command surface | Current result |
| --- | --- | --- |
| Unit/contract | `python tools/run_g28_tier.py unit` | 11 passed |
| Integration | `python tools/run_g28_tier.py integration` | 9 passed; loopback permission required |
| Installed-E2E | `python tools/run_g28_tier.py installed` | fresh installed replay passed |
| Behavioral eval | `python tools/run_g28_tier.py behavior` | all three fixed splits passed |

Reports are under [`tier-reports/`](tier-reports/). The integration tier is
socket-bearing and must run in an environment that permits the loopback
interview server. The installed tier builds and exercises the installed
candidate, including the no-flag create path from an initialized non-Git
target.

The G28 role registry is also packaged as the additive
`role-reference.schema.json` resource. It defines only the closed namespace,
identifier, and positive version shape; runtime registration remains the
authority for whether an identifier is selectable. The role reference grants
no capability or permission.

## Evidence files

- [G26/G27 behavior manifest](../../../../../research/evals/g28/g26-g27-manifest.json)
- [Deterministic observations](../../../../../research/evals/g28/g26-g27-deterministic-observations.json)
- [Development report](g26-g27-development-report.json)
- [Calibration report](g26-g27-calibration-report.json)
- [Final held-out report](g26-g27-final-held-out-report.json)

## Next checkpoints

1. Expand role validation to review-reference and executor boundaries.
2. Replace deterministic fixture observations with adjudicated behavioral
   cases before any alpha or human-UAT claim.
