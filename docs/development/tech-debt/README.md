# GigAI Technical Debt Register

This register tracks operational and engineering debt discovered while
preparing GigAI for human UAT and alpha release. It is a planning record, not
runtime authority. A debt item is closed only when its exit evidence is
recorded in the item and the relevant change is reviewed.

## Active items

| ID | Topic | Status | Primary impact |
|---|---|---|---|
| [TD-0001](TD-0001-test-feedback-latency.md) | Test feedback latency | Open | Pull-request feedback remains about five minutes even after reducing the matrix. |
| [TD-0002](TD-0002-owner-controlled-release.md) | Owner-controlled release | Open | A release currently requires a local tag push and is not an intentional GitHub-only action. |
| [TD-0003](TD-0003-actions-node-runtime.md) | GitHub Actions Node runtime warnings | Open | `checkout@v4`, `setup-python@v5`, and `setup-uv@v5` emit Node.js 20 deprecation warnings. |

## Operating rules

- Keep evidence about timing, workflow runs, and release attempts sanitized.
- Do not put credentials, raw UAT data, model output, or private target content
  in this register.
- Link a debt item to the Goal or workflow that owns its resolution.
- Prefer a small, reversible fix with a measurable exit condition.
