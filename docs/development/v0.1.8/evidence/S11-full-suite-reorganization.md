# S11 full-suite behavior reorganization receipt

Recorded 2026-09-22 for the operator-authorized full-suite implementation
packet. This report covers test organization, test-only environment support,
and bounded offline timing work; it does not claim a release, installed
verifier acceptance, provider/model execution, UAT, or live-lane acceptance.

## Scope and ownership

The former top-level `tests/test_*.py` suite was reorganized into behavior
directories while preserving the existing research package roots. The
implementation owner changed only `tests/**`, `pyproject.toml`, `uv.lock`, and
the `S11-full-suite-*` evidence files. Makefile, CI workflow, runner, and
installed-verifier changes remain sibling B's owned surface; this receipt does
not claim those changes or their final integration until the coordinator
hands off the completed runner.

The source authority remains the configured unfiltered pytest roots in
`pyproject.toml` and the dynamic marker hook in `tests/conftest.py`. Source
behavior names are discoverability and ownership labels, not runtime lane
selectors. No inferred `pytest -m` command was added: the full source command
collects all configured roots, while live/provider/UAT remains an explicit
opt-in boundary.

## Before and after measured baseline

Both runs used the project checkout at `b01675d0b7ef39b49853a26df61aadeea2064a6a`
(`v0.1.7-dirty` / project version `0.1.7`), `GIGAI_G30_UAT=0`,
`PYTHONPATH=src`, and all configured offline roots. The baseline was serialized
before adding the test-only xdist dependency; the after run used the project
local `.venv`, pytest-xdist 3.8.0, and 14 workers. Full raw result fields and
failure nodeids are in [S11-full-suite-baseline.json](S11-full-suite-baseline.json)
and [S11-full-suite-after.json](S11-full-suite-after.json).

| Receipt | Command | Collected | Passed | Failed | Skipped | Exit | Wall |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| before | `GIGAI_G30_UAT=0 PYTHONPATH=src /usr/bin/time -p .venv/bin/python -m pytest -q` | 1,588 | 1,582 | 5 | 1 | 1 | 3,179.01s |
| after | `GIGAI_G30_UAT=0 PYTHONPATH=src /usr/bin/time -p .venv/bin/python -m pytest -n 14 --dist=worksteal -q` | 1,588 | 1,582 | 5 | 1 | 1 | 467.91s |

Pytest's measured session wall was 3,178.83s before and 467.79s after; the
`time -p` real values above are the comparable process wall clock. This is an
85.28% wall reduction with no deselection, skip injection, xfail, assertion
removal, or fixture weakening. The after receipt also records user/system CPU
time (2,251.18s/2,863.61s), making the parallel-workload tradeoff explicit.
The focused path-sensitive repair check was:

```
GIGAI_G30_UAT=0 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/behaviors/installed_release/test_release_check.py \
  tests/behaviors/cli_surface/test_cli_and_scenario_harness.py
29 passed in 20.48s
```

The five failures are unchanged loopback `socket.bind` `PermissionError:
[Errno 1] Operation not permitted` cases in the sandbox:

```
g22_http_approval::test_http_answers_then_operator_approval_reaches_terminal_lifecycle
g22_proposal_interview::test_loopback_http_requires_token_and_preserves_session_boundary
g22_proposal_interview::test_loopback_http_rejects_malformed_payload_and_expires
g26_review_actions::test_builder_review_can_revise_rebuild_and_reject_without_activation
setup_browser::test_setup_page_uses_human_model_labels_and_reports_cli_detection
```

The one skip is the existing explicit `g30_live` provider/UAT guard. No
provider, model, daemon, personal configuration, installed verifier, or live
lane ran. These five sandbox failures are recorded as pre-existing baseline
limitations, not masked regressions; fixing them would cross the protected
runtime/environment boundary.

## Complete behavior tree and identity map

The 156-file source inventory collected exactly 1,588 nodes before and after.
The map contains all 156 records, before/after paths, counts, before/after
node-id hashes, selection commands, and a normalized node identity hash:
[S11-full-suite-migration-map.json](S11-full-suite-migration-map.json).
The map's per-record `node_identity_equivalent` is true for every record; the
raw node IDs differ where a path was intentionally moved. The nine research
files remain in their package roots because their relative imports and
adjacent spike modules are part of their execution boundary.

| Behavior root | Files | Nodes | Selection boundary |
| --- | ---: | ---: | --- |
| `tests/behaviors/acquisition` | 2 | 17 | public acquisition/import state |
| `tests/behaviors/cli_surface` | 9 | 59 | public CLI and wrapper boundaries |
| `tests/behaviors/installed_release` | 12 | 87 | package/resource/release boundaries |
| `tests/behaviors/integrity_canonical` | 2 | 59 | canonical artifact integrity |
| `tests/behaviors/integrity_state` | 5 | 83 | state/registry/workpad integrity |
| `tests/behaviors/runtime_model_boundary` | 13 | 163 | model/provider boundary contracts |
| `tests/behaviors/runtime_run_authority` | 24 | 259 | run/consent/authority contracts |
| `tests/behaviors/scout_assessment` | 11 | 55 | assessment and review behavior |
| `tests/behaviors/scout_discovery` | 5 | 64 | discovery and inventory behavior |
| `tests/behaviors/scout_proposals_tools` | 30 | 311 | proposal/tool contract behavior |
| `tests/behaviors/scout_request_tailoring` | 5 | 98 | request tailoring behavior |
| `tests/behaviors/scout_research` | 12 | 151 | Scout research behavior |
| `tests/behaviors/scout_tracking_reporting` | 8 | 65 | tracking/reporting behavior |
| `tests/behaviors/system_contracts` | 2 | 32 | cross-cutting system contracts |
| `tests/behaviors/research_spikes` | 7 | 44 | retained research behavior grouping |
| retained `research/contract_spike/tests` | 3 | 24 | package-relative contract spike imports |
| retained `research/phase0_spike/tests` | 6 | 17 | package-relative phase0 spike imports |
| **Total** | **156** | **1,588** | **all configured roots** |

All 145 former top-level files moved into the 15 behavior roots (the
`research_spikes` row counts seven existing research-oriented tests that were
already under `tests`). The two acquisition files are the prior S11 migration
and remain in the complete map as `prior-S11-migration`; all 145 new records
are classified as `migrated-behavior-suite`. No test
file was silently omitted, duplicated, or deleted from the collected map.

The path-sensitive repairs were limited to moved-test support: module imports,
repository-root resolution in `test_release_check.py` and
`test_cli_and_scenario_harness.py`, the registry fixture path in
`test_registry_v2_migration.py`, and the same-directory adversarial fixture
reference in `test_scout06_research_run_adversarial.py`. `compileall -q
tests/behaviors` passed. The
protected subprocess, filesystem, symlink, persistence, CLI, and authority
fixtures remain real boundaries; no product runtime was changed.

## Speed change and dependency receipt

The only speed mechanism introduced was process-isolated pytest-xdist with
`--dist=worksteal` and 14 workers, selected from bounded measurements:

```
--dist=loadfile, -n 8: 669.42s
--dist=loadfile, -n 14: 582.03s
--dist=load,     -n 14: 623.49s
--dist=worksteal,-n 14: 478.90s (before final path repair)
--dist=worksteal,-n 16: 480.30s
--dist=worksteal,-n 20: 543.42s
--dist=worksteal,-n 14: 467.91s (final)
```

The final run is under the 480-second source-lane hard cap on this 14-CPU
checkout. Process isolation is explicit; no mutable shared fixture scope was
widened. `pytest-xdist>=3.6` was added only to the test optional extra,
resolved as xdist 3.8.0 plus execnet 2.1.2, and synchronized only into this
checkout's `.venv`. [S11-full-suite-environment.json](S11-full-suite-environment.json)
records the commands, hashes, and environment boundary.

## Runner/CI handoff and acceptance boundary

Sibling B owns `Makefile`, `.github/workflows/**`, `tools/run_ci_tests.py`,
installed verifiers, and their evidence. The source runner must consume the
same unfiltered configured roots and use the measured `-n 14
--dist=worksteal` strategy (or record a fresh bounded measurement); path-based
behavior grouping must not become a marker-based exclusion. The final `make
test` aggregate, including the behavior evaluator and installed verifier lane,
is intentionally not claimed here until B's completed runner is handed back
and executed once by this owner. Live/provider/UAT and Debian or installed
proof remain explicit prerequisites, never silently included in an offline
green result.

The current AST-based installed selection was checked read-only after the move:
31 candidates were found, all under current `tests/behaviors/**` paths and none
under deleted top-level paths; the durable list is
[S11-full-suite-installed-selection.json](S11-full-suite-installed-selection.json).
This is selection evidence only, not installed execution evidence.

At this receipt checkpoint, B's runner exposes only `load`, `loadfile`, and
`loadscope` and defaults to eight `loadfile` workers; it rejects the measured
`--xdist-dist=worksteal` plan with an argument error. That runner-owned
selector/default mismatch is an explicit integration blocker: the measured
source timings show 582.03s for 14 `loadfile` workers and 478.90s for 14
`worksteal` workers before the final path repair, so this owner did not edit
B's files to invent a cross-ownership fix.

The full-suite migration portion is complete: collection/count identity,
behavior placement, fixture/path repairs, and measured source optimization are
evidenced. The overall S11 full-suite gate remains **pending integrated make
test execution** and remains bounded by the five sandbox socket failures and
the explicit live/provider skip. No release or whole-project readiness claim
is made.

## Exact next checks

1. Consume B's runner handoff without editing B-owned files.
2. Execute `GIGAI_G30_UAT=0 make test` once, with the runner's exact phase
   receipts and exit status, after confirming it still collects all 1,588
   source nodes and retains installed-verifier coverage.
3. If the aggregate exceeds the 480-second budget, record the measured phase
   bottleneck and adjust only the runner-owned scheduling/parallelization; do
   not deselect tests or weaken assertions.
4. Terra independently reviews this packet; this worker does not self-declare
   final acceptance.
