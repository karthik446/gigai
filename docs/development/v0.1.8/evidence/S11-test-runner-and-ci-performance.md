# S11 test runner and CI performance receipt

Recorded 2026-09-22 for the operator-authorized CI/test-runner preparation
packet. The implementation is in `Makefile`, `tools/run_ci_tests.py`,
`.github/workflows/pull_request.yaml`, and the scoped developer command
documentation in `CONTRIBUTING.md`. This receipt is a source and bounded
selector audit; it is not a complete-suite pass, a current remote-CI result,
an installed-release acceptance, or a provider/model/UAT result.

## Authority and scope

The checkout was at `b01675d0b7ef39b49853a26df61aadeea2064a6a` on branch
`karthik446/gigai-v0.1.8`. Existing dirty work was preserved. No `tests/**`,
`pyproject.toml`, `uv.lock`, product source, schema, Phase 2, or spike file was
changed by this packet; Luna A retains the complete `tests/**` migration and
the final full-suite timing run.

The single aggregate entrypoint is `Makefile:15`:

```text
make test
  -> make test-source
  -> make test-behavior
  -> make test-wheel
```

`Makefile:17-28` runs one unfiltered `pytest` invocation through the locked
project environment with a bounded resource-aware xdist plan. The default
request is `TEST_XDIST_WORKERS=auto`, capped at
`TEST_XDIST_MAX_WORKERS=14`, using `TEST_XDIST_DIST=worksteal`, and further
clamped by the host `os.cpu_count()`; the plan prints requested, cap, CPU,
actual worker, scheduler, and offline-UAT values before pytest starts. The cap
matches A's measured 14-CPU host and does not assume 14 workers on smaller CI
runners. A's controlled local measurement override is
`make test-source TEST_XDIST_WORKERS=14 TEST_XDIST_MAX_WORKERS=14
TEST_XDIST_DIST=worksteal`. That broad invocation remains the runtime authority
for all configured pytest roots: unit/fast-unit, integration, CLI,
behavior-directory, and source-environment installed tests. The previous
default PR workflow ran G28 unit and integration file selectors and then
repeated them through the unfiltered suite; those overlapping selectors are
removed.

`Makefile:30-33` runs the existing deterministic G28 evaluator once for its
development, calibration, and `final_held_out_acceptance` splits. The runner
writes these temporary split outputs under a disposable directory and does not
call a model or provider.

`Makefile:35-45` builds one wheel in a disposable build directory, prepares the
Python 3.11 wheel environment from `uv export --locked` runtime requirements,
enumerates every current
`tools/verify_installed_*.py` script, and executes direct AST-derived installed
pytest node ids against the wheel console script. The current filesystem
selection is 24 wheel verifiers plus the separate Debian container verifier;
the old workflow omitted G22 and G26, while the dynamic enumeration includes
them. A verifier or installed test nonzero exit is returned immediately, so a
failure cannot be hidden by a later command.

The runner's implementation and authority boundaries are explicit at
`tools/run_ci_tests.py:42-105`, `:118-206`, `:209-219`, `:222-244`,
`:252-285`, and `:288-411`. Installed selectors are direct `path::node` ids generated from
the configured pytest roots in `pyproject.toml` (`tests`,
`research/contract_spike/tests`, and `research/phase0_spike/tests`); all
functions in
`*_installed_scenarios.py` are included, as are functions whose bodies use the
installed-package fixture/executable contract. This avoids a broad inferred
`-k` selector and currently follows A's moved `tests/behaviors/**` paths. The
31 AST candidates remain discovery input only; pytest collection and conftest
policy remain authoritative.

## CI wiring and preserved gates

`.github/workflows/pull_request.yaml:21-23` cancels superseded pull-request
runs for the same pull request. The expression does not cancel reusable
`workflow_call` invocations used by compatibility/release workflows.

The source matrix remains unchanged at
`.github/workflows/pull_request.yaml:72-98`: default PRs use Ubuntu/Python
3.11, and `full_matrix` continues to run Ubuntu and macOS with Python 3.11,
3.12, and 3.13. Each lane still executes the complete unfiltered source suite
through `make test-source`; the workflow sets the source lane to
`TEST_XDIST_WORKERS=auto`, `TEST_XDIST_MAX_WORKERS=14`, and
`TEST_XDIST_DIST=worksteal`, so actual workers are capped by each runner's
reported CPU count rather than assuming 14 everywhere. No OS or Python
compatibility axis was removed.

The deterministic behavior evaluator is a distinct Ubuntu/Python 3.11 job at
`.github/workflows/pull_request.yaml:100-116`, so it is not repeated in every
source matrix lane and is present in both default and full-matrix workflows.
There are no remaining workflow selectors naming deleted top-level test files;
the source job delegates complete discovery to the configured pytest roots,
including `tests/behaviors/**` and both research roots. The wheel job delegates
installed-case execution to the runner's current direct node ids rather than a
stale pre-migration path list.
The wheel job at `:118-134` uses the same locked test extra and `make
test-wheel`, making the complete wheel-installed verifier/test selection
visible in both profiles rather than a partial default list.

The Debian 12 job at `:136-158` retains its existing network-none,
read-only, non-root, direct-mount contract. It is also exposed as the explicit
`make test-debian-offline` target at `Makefile:55-72`; it is not silently
reported as a portable macOS/Linux developer pass. The real local model and
provider/UAT gate is separately guarded by `Makefile:46-53` and requires
`GIGAI_G30_UAT=1`; it never runs from ordinary `make test`.

`CONTRIBUTING.md:9-30` now documents `uv sync --locked --extra test` followed by
`make test`, the bounded worksteal source plan, separate Debian gate, and the explicit live/UAT gate. The
release workflow's existing reusable full-matrix call remains the platform
coverage handoff; no release workflow was changed.

## Historical bottleneck evidence

The read-only GitHub receipts were:

```text
gh run view 35660274375 --repo karthik446/gigai --json databaseId,displayTitle,headBranch,headSha,status,conclusion,jobs
gh run view 35660274375 --repo karthik446/gigai --job 106533695529 --log
```

The exact machine-readable job/time/conclusion record is
[S11-ci-historical-run.json](S11-ci-historical-run.json). The run tested
`v0.1.7` at the checkout HEAD and concluded `failure`:

| Job | Duration | Result |
| --- | ---: | --- |
| G28 Ubuntu 3.13 | 2,096 s | success |
| G28 Ubuntu 3.11 | 2,250 s | success |
| G28 Ubuntu 3.12 | 2,437 s | success |
| Debian 12 offline | 4,773 s | success |
| G28 macOS 3.13 | 4,765 s | success |
| G28 macOS 3.11 | 4,922 s | success |
| G28 macOS 3.12 | 4,933 s | success |
| Built-wheel resources | 17 s | failure at `tools/verify_installed_g03.py` |

The failed wheel lane passed the installed schemas, canonical identity, and
CLI verifiers, then logged `installed first setup failed:` with no stderr and
exit code 1. The current source explains why that historical receipt lost the
diagnostic: `src/gigai/cli.py:170-182` emits machine-readable setup errors with
`click.echo(...)` on stdout, while the old
`tools/verify_installed_g03.py:60-62` failure message exposed only
`CompletedProcess.stderr`. This is a demonstrated stale harness-observability
defect, not evidence that `--no-open-with-target` was required or that setup
itself was broken; the verifier now reports both captured streams at
`tools/verify_installed_g03.py:27-35` while preserving the same nonzero
assertion and all subsequent G03 payload checks. The correction makes the
historical failure diagnosable without declaring it fixed: an exact wheel-lane
reproduction and A's complete aggregate receipt remain the integration gate
for deciding whether the underlying setup result is a product defect.

## Current socket-bind environment disposition

A's source receipt records five unchanged `PermissionError: [Errno 1]
Operation not permitted at socket.bind` failures at
`docs/development/v0.1.8/evidence/S11-full-suite-reorganization.md:54-62` and
`S11-full-suite-after.json:45-65`. The affected tests construct the intended
loopback servers directly: `tests/behaviors/scout_assessment/test_g22_http_approval.py:47`,
`tests/behaviors/scout_assessment/test_g22_proposal_interview.py:286,338`,
`tests/behaviors/scout_assessment/test_g26_review_actions.py:107`, and
`tests/behaviors/cli_surface/test_setup_browser.py:51`.
The first four nodeid path labels in the A-owned after JSON still name their
former `tests/behaviors/cli_surface/**` locations; this packet preserves that
receipt and uses the current filesystem paths above rather than silently
rewriting A's evidence.

This is an execution-environment permission boundary, not a scheduling defect.
`src/gigai/proposal_interview.py:627-656,783-784` and
`src/gigai/setup_interview.py:41-58,171-172` require `127.0.0.1` and bind an
ephemeral port through `ThreadingHTTPServer`; the current runner has no supported
host/port override, and xdist `worksteal` cannot grant socket permissions.
Because this packet cannot weaken the loopback contract, skip the tests, evade
the sandbox, or edit tests/product source, no truthful runner-only resolution is
available here. A's final aggregate receipt must run in an execution environment
that permits the existing loopback bind and record whether all five cases pass;
the five failures remain an explicit integration/environment gate rather than a
pass or an invented workaround.

## Bounded validation performed

The exact local commands and results are recorded in
[S11-ci-selector-audit.json](S11-ci-selector-audit.json):

```text
rtk .venv/bin/python tools/run_ci_tests.py installed --list-installed   # exit 0; 31 direct node ids; no tests
rtk .venv/bin/python -m py_compile tools/run_ci_tests.py              # exit 0
rtk .venv/bin/python -m py_compile tools/verify_installed_g03.py       # exit 0
rtk .venv/bin/python -c 'import tools.run_ci_tests as runner; print(runner._xdist_options("auto", "14", "worksteal"))'  # exit 0; plan actual=14 on this 14-CPU host
rtk .venv/bin/python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0))'  # exit 1; bounded probe reproduces sandbox EPERM at socket.bind
rtk make -n test-source TEST_XDIST_WORKERS=14 TEST_XDIST_MAX_WORKERS=14 TEST_XDIST_DIST=worksteal  # exit 0; explicit source override
rtk make -n test                                                       # exit 0; source/behavior/wheel only
rtk make -n test-live                                                  # exit 0; explicit GIGAI_G30_UAT=1 guard retained
rtk ruby -e 'require "yaml"; YAML.load_file(".github/workflows/pull_request.yaml")'  # exit 0
rtk git diff --check -- .github/workflows/pull_request.yaml CONTRIBUTING.md docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md tools/verify_installed_g03.py  # exit 0
```

The 31-node snapshot now names the migrated
`tests/behaviors/cli_surface/**` and `tests/behaviors/installed_release/**`
modules, alongside installed-package functions and `test_g10_phase1_audit.py`.
It is AST candidate discovery, not a pytest collection or execution receipt:
configured roots and pytest's conftest, marker, inherited-fixture, and
parametrization policy remain authoritative. A reports 145 moved top-level
modules, retained research roots with 3 and 6 files, and unchanged 1,588
collected cases; those are upstream handoff facts, not a collection run by B.
The selector audit reconciles the earlier 25-verifier inventory as 24
`verify_installed_*.py` wheel scripts plus the separately named Debian gate at
`tools/verify_debian_offline.py`; no wheel verifier is missing from the
current 24-script enumeration. Luna A owns the serialized full baseline and
final complete `make test` execution; this worker did not start collection or
another full suite and did not mutate A's timing environment.

## Runtime/cost disposition

The evidence supports these expectations, but not savings claims:

1. Removing duplicate default-PR G28 unit/integration invocations avoids
   repeating selected files immediately before the full source suite.
2. A single behavior job avoids repeating the deterministic evaluator in each
   source matrix lane and adds that explicit check to full-matrix workflows.
3. Pull-request concurrency cancellation can avoid completing superseded work.
4. Running all installed verifiers in the default wheel lane restores complete
   offline coverage and may increase default PR duration versus the former
   partial verifier list.
5. Bounded source xdist may reduce source wall time, but scheduling alone does
   not establish speedup, fixture isolation, or runner-minute savings; A owns
   the controlled measurement and complete-lane validation.

The historical critical path is 4,933 seconds (macOS Python 3.12), with
Debian at 4,773 seconds. Parallel wall-time reduction can increase total
billable runner minutes; no billable-minute or total-cost savings are claimed
until a future receipt measures source, behavior, wheel/verifier, and aggregate
wall time together with runner minutes.

## Pending integration and evidence gates

The following remain open and are intentionally not represented as passes:

- Luna A's final complete `make test` timing and integration receipt, including
  source, deterministic behavior, wheel build/environment setup, all 24 wheel
  verifiers, installed tests, and the explicit Debian gate disposition. The
  acceptance target is at most 480 seconds for the measured complete
  aggregate; this worker must consume that receipt rather than rerun the suite.
- A's actual pytest collection/conftest receipt must confirm the migrated
  identity map and the upstream reported 1,588-case preservation; the 31 AST
  candidates here are not an exhaustive-collection claim.
- The corrected G03 verifier is ready to expose stdout JSON diagnostics, but
  the historical underlying setup outcome is not promoted to pass/fail until
  A's wheel-lane receipt supplies the exact bounded reproduction.
- A future remote CI result for the changed workflow, including G03, G22, G26,
  all wheel verifiers, all OS/Python source lanes, and Debian.
- Any real provider/model/UAT and release/publication decision gates.

This packet is CI/test-runner preparation in progress, not evidence that the
schema-valid tests are green, that model quality is validated, that a release
is ready, or that the historical G03 failure is fixed.
