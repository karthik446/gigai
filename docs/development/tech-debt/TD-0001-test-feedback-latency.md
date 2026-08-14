# TD-0001 — Test feedback latency

- Status: Open
- Discovered during: G24 UAT preparation
- Affected surfaces: `.github/workflows/pull_request.yaml`, test suite,
  installed-wheel checks
- Ownering lane: G24 UAT / later alpha-readiness cleanup

## Observation

The pull-request workflow now uses one Ubuntu/Python 3.11 source-test job,
but the full 531-test suite still takes about five minutes. The built-wheel
job adds roughly another minute and a half. The earlier OS/Python matrix was
reduced, but serial test execution remains the dominant cost.

Likely contributors include repeated Git repository creation and commits,
G19/G21/G23 subprocess-heavy fixtures, G22 loopback HTTP and CLI processes,
journal multiprocessing/lock recovery, and repeated setup/workpad/SQLite
initialization.

## Proposed resolution

Profile the suite first with duration reporting. Then evaluate safe parallel
execution and a pinned GHCR Ubuntu/Python test-runner image with dependencies
preinstalled. The image must run the checked-out source at test time; it must
not test stale source baked into the image.

Keep native macOS and the full Python compatibility matrix in
`compatibility_job.yaml`. The fast path must retain the safety-critical tests
needed to catch regressions in the changed surface.

## Exit evidence

- A before/after timing record identifies setup time separately from pytest
  execution time.
- Pull-request feedback is materially below the current five-minute baseline,
  without weakening the required fast-path coverage.
- Parallel execution has no port, SQLite, Git-fixture, multiprocessing, or
  shared-environment flakiness across repeated runs.
- Any GHCR runner is pinned by digest and is rebuilt when test dependencies or
  supported Python versions change.
