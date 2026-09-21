# SCOUT R7 candidate verification — v0.1.7

Date: 2026-09-20 (America/Denver)  
Status: **candidate built and exact-wheel checks performed; not approved,
published, live-provider verified, or human-UAT accepted.**

This handoff follows the release order in
`SCOUT-release-execution-graph.md`: automated candidate checks precede any
authorized publication, and publication precedes the user's normal install and
UAT. No provider/model call, API key, model download, real user `.gigai` data,
activation, external publication, commit, reset, or cleanup was performed.

## Version and worktree boundary

The release metadata was checked before editing. Both files were consistently
at `0.1.6`, with no competing version edit in the dirty worktree; only the
planned candidate bump was made:

- `pyproject.toml [project].version`: `0.1.6` -> `0.1.7`
- `uv.lock` editable `gigai` record: `0.1.6` -> `0.1.7`

The pre-existing package-data and pytest-marker edits in `pyproject.toml`, and
all unrelated G43/runtime and Scout source/test changes, were preserved. The
release metadata check passed:

```text
rtk python tools/release_check.py verify-lockfile
verified uv.lock project entry for gigai 0.1.7
```

## Candidate artifacts and provenance

Artifacts were rebuilt after the release-note and UAT-handoff edits into the
task-specific directory `/private/tmp/gigai-r7-candidate-20260920/dist`:

| Artifact | Size | SHA-256 |
|---|---:|---|
| `gigai-0.1.7-py3-none-any.whl` | 758279 | `bb7dd5873f7197f88006fb51351cafccf926c88362001dbda4b4d4754dc54ef4` |
| `gigai-0.1.7.tar.gz` | 643452 | `3d82e1ad259068885a435f1082260272fb7d25f3e00194fa3f031739b993699a` |

The release checker passed artifact metadata and checksum verification. The
full source/member manifest is
`/private/tmp/gigai-r7-candidate-20260920/release-manifest.json` (SHA-256
`50bca5be2b883968f723fc4a263bc499167938afdbfef0f1fd8754e7c84629ba`). It
records checkout HEAD `fda48574f8642e66c0e7d53e7303ec04f04d7bb8`, all 218 wheel
members with exact packaged and checkout hashes, and dirty/untracked source
provenance. `SHA256SUMS` is retained beside the artifacts.

## Exact-wheel installed verification

The wheel was installed without `--no-deps` into the disposable environment
`/private/tmp/gigai-r7-candidate-20260920/final-venv`; 17 declared/runtime
dependencies were installed. The environment was run outside the checkout
with `python -I` for direct imports and the exact wheel's console script.

Passed checks:

- `gigai --version` -> `gigai 0.1.7`; `gigai --help`, `init --help`, and
  `scout-report --help` expose the installed command surface.
- Generic synthetic `setup` and username-gated `init` completed in disposable
  paths; no provider probe was made.
- Explicit installed Scout candidate materialization produced `approval_required`
  state and 21 private-data-free source members, including the copied wrapper,
  goal graphs, UI, and bundled tool schemas/sources.
- Copied-wrapper `context`/`list`, public acquisition `import`/`status` using
  one synthetic row, and `report generate`/`status` all completed. The initial
  report-status call correctly refused before generation because no selector
  existed; the subsequent generation published a local report and status read
  it as `stale: false`.

Raw installed workflow logs are retained under
`/private/tmp/gigai-r7-candidate-20260920/logs/`.

## CI-derived automated matrix

The required commands were taken from `.github/workflows/pull_request.yaml`,
`.github/workflows/release.yml`, and `pyproject.toml`. Dependency sync passed
in the disposable matrix environment:

```text
UV_PROJECT_ENVIRONMENT=/private/tmp/gigai-r7-candidate-20260920/matrix-venv \
  rtk uv sync --locked --extra test --python 3.11
26 packages installed; source checkout resolved as gigai 0.1.7
```

The full source command was invoked once with `GIGAI_G30_UAT=0` and the locked
matrix environment. The process completed, but the Orca command wrapper did
not surface its final stdout/stderr or exit code and therefore no pass claim or
test count is made for that tier. This is an evidence gap, not green proof;
the attempted command was:

```text
env UV_PROJECT_ENVIRONMENT=/private/tmp/gigai-r7-candidate-20260920/matrix-venv \
  GIGAI_G30_UAT=0 rtk uv run --locked pytest
```

The installed verifier commands were each run once against the exact final
wheel. Result: **10 passed, 12 failed** out of 22 verifier scripts. Logs are
`installed-*.log` in the retained log directory.

Passed: schemas, canonical, CLI, G06, G07, G08, G15, G16, G17, and G19. The
failure groups are:

1. G03 expects an obsolete `adapter.offline` doctor check; current installed
   doctor returns PASS with the current mount/editor/journal checks.
2. G04 and G05 invoke `init` without the now-required username and therefore
   fail before their intended target/workpad assertions.
3. G09 invokes `gigs` without an explicit target from a non-Git working path.
4. G11's synthetic setup has no usable configured runtime under the current
   fail-closed setup policy; no provider was contacted.
5. G13/G14 invoke `run` without the now-required direct `--confirm` consent.
6. G20/G21/G23/G27/G28 retain a stale expected schema inventory of 34; the
   installed inventory is 64 for those historical verifiers. The authoritative
   `tools/verify_installed_schemas.py` check passed its current 82-schema
   inventory.

The CI installed-scenario selection was also run once against the exact wheel:

```text
GIGAI_TEST_EXECUTABLE=/private/tmp/gigai-r7-candidate-20260920/final-venv/bin/gigai \
  pytest tests/test_cli_and_scenario_harness.py tests/test_g*_installed_scenarios.py \
  tests/test_g10_phase1_audit.py -k 'installed_help_version or installed_'
35 selected, 19 passed, 16 failed, 20 deselected, 26.09s
```

The 16 failures are concentrated in the stale command-list assertion and G04
installed-init scenarios, whose fixtures omit the required username and then
cannot reach their intended target/registry assertions. This is a concrete
domain/test-contract correction list, not a packaging/import failure.

The Debian offline workflow was not run because the local Docker client could
not connect to its daemon (`docker.sock` unavailable). No attempt was made to
start a daemon or download a base image.

## Deferred gates and exact next commands

| Gate | Current truth |
|---|---|
| Candidate built | Yes; exact hashes above |
| Dependency-complete exact-wheel install | Yes; disposable final environment |
| Synthetic installed Scout/init/report/public checks | Yes; bounded results above |
| Full source matrix | Attempted once; result unavailable from wrapper, no green claim |
| Installed CI verifier/scenario matrix | No; 12 verifier failures and 16 scenario failures above |
| Independent R5/R6 review | Existing review says 26 focused tests/82 schemas; synthetic only, not R7 proof |
| Live Ollama/Luna comparison | Not run; separate live-proof gate |
| Authorized publication | Not authorized or performed |
| Personal install/UAT | Deferred until authorized publication |

Before publication, reconcile the concrete verifier/test contract failures above
and rerun the affected narrow checks plus one captured full source matrix; do
not mask or exclude them. After explicit publication authorization, install
the published `gigai==0.1.7` normally in a new disposable environment, verify
`gigai --version`, and execute the scenarios in
`SCOUT-12-user-uat-checklist.md` using synthetic data first. Only then may
personal data, provider choice, activation, and live comparison be considered
by the operator.

