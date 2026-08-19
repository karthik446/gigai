# G31 completion audit

- Status: Release-candidate machine gate complete; G31 is not yet complete
- Candidate commit: `c0b95ae`
- Next action: merge, set the release version to `0.1.6`, run exact-tag CI, and
  complete the operator UAT checkpoint

## Current verdict

The implementation is a credible v0.1.6 release candidate on the current
branch. Source tests, installed-wheel replays, evaluation splits, and the
operator's opt-in Codex/Claude readiness checkpoint pass. The goal cannot yet
be marked complete because the package metadata is still `0.1.5`, no merged
`v0.1.6` tag has run the release workflow, and the final human UAT scenarios
remain open.

## Acceptance status

| Criterion | Status | Evidence |
|---|---|---|
| Source and evaluation tiers | Pass | `machine-verification.md` |
| Real CLI readiness evidence | Pass | G30 audit and live checkpoint |
| Fresh install/upgrade of v0.1.6 | Pending | Requires merged release candidate |
| Human UAT record | Partial | `uat-checkpoint.md` |
| SQLite/workpad authority inspection | Pending | Operator-only scenario |
| Fail-closed UAT scenarios | Machine pass; human pending | Source and installed tests plus UAT checkpoint |
| External/internal changelog separation | Pass for current docs | Public README/changelog review |
| Exact-tag v0.1.6 workflow | Pending | G12 release lane |
| Final publishability decision | Pending | Requires the preceding items |

The evaluation reports explicitly distinguish deterministic fixture scoring from
candidate-judge accuracy. G31 does not convert those fixture passes into a
claim that a production evaluator is calibrated.
