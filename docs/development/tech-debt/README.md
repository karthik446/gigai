# GigAI Technical Debt Register

This register tracks operational and engineering debt discovered while
preparing GigAI for human UAT and alpha release. It is a planning record, not
runtime authority. A debt item is closed only when its exit evidence is
recorded in the item and the relevant change is reviewed.

## Active items

| ID | Topic | Status | Primary impact |
|---|---|---|---|
| [TD-0001](TD-0001-test-feedback-latency.md) | Test feedback latency | Open | Pull-request feedback remains about five minutes even after reducing the matrix. |
| [TD-0002](TD-0002-owner-controlled-release.md) | Owner-controlled release | In verification | Manual owner-only dispatch and GitHub-side tag creation are implemented; release-run evidence is pending. |
| [TD-0003](TD-0003-actions-node-runtime.md) | GitHub Actions Node runtime warnings | In verification | Node 24-compatible action versions are implemented; workflow evidence is pending. |
| [TD-0004](TD-0004-setup-ux-and-terminology.md) | Setup UX and terminology | In progress | Setup now offers detected editors and clearer local-mode language; human UAT remains. |
| [TD-0005](TD-0005-proposal-interview-ui.md) | Proposal interview UI | In progress | The interview now opens as a guided local flow with explicit in-browser reference selection; visual UAT remains. |
| [TD-0006](TD-0006-evaluation-taxonomy-and-behavioral-evals.md) | Evaluation taxonomy and behavioral evals | Open; v0.1.5 blocker | Tests, integration checks, installed E2E, and behavioral evals lack one tier/report contract. |
| [TD-0007](TD-0007-central-role-registry.md) | Central namespaced role registry | Open; v0.1.5 blocker | Model, reference, and executor roles are unconstrained strings with overlapping namespaces. |
| [TD-0008](TD-0008-browser-first-create-and-model-setup.md) | Browser-first create and model setup | Open; v0.1.5 blocker | `gigai create tailor-resume-for-job` is not yet a zero-friction configured-project flow. |
| [TD-0009](TD-0009-v0.1.5-readiness-gate.md) | v0.1.5 product-readiness gate | Open; release gate | G24 human UAT is gated on evals, role contracts, and the normal setup/create path. |

## Operating rules

- Keep evidence about timing, workflow runs, and release attempts sanitized.
- Do not put credentials, raw UAT data, model output, or private target content
  in this register.
- Link a debt item to the Goal or workflow that owns its resolution.
- Prefer a small, reversible fix with a measurable exit condition.
