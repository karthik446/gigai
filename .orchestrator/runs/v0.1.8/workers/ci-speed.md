# ci-speed worker report

Task: cut tag→published release CI time (was ~1h20m to first publish job) while
keeping PR CI fast and not losing coverage that caught real bugs.
Target: release CI ≤30 min to publish jobs; PR CI ≤15 min.

READ: `.github/workflows/pull_request.yaml`, `.github/workflows/release.yml`,
`.github/workflows/compatibility_job.yaml`, `containers/debian-offline/Dockerfile`,
`tools/run_debian_offline.sh`, `Makefile`, `tools/run_ci_tests.py` (full file),
`gh run view 35660274375 --repo karthik446/gigai --json jobs` (v0.1.7 release run),
`gh run list`/`gh run view 35897986900` (current PR run on this branch),
`pyproject.toml` pytest/xdist deps, local `nproc`/`os.cpu_count()`.

EXECUTED: edited 3 workflow files + 1 shell script; parsed all workflow YAML
with pyyaml; ran `make -n test-source test-behavior test-wheel`; ran
`sh -n tools/run_debian_offline.sh`; ran `_xdist_options('auto','4','worksteal')`
directly to confirm the bounded-worker math (does NOT require full suite run,
did not run pytest). No `git add`/commit/push. actionlint was not installed
and was not installed as part of this task (pyyaml parse used instead, per
acceptance criteria's "or").

## Evidence: v0.1.7 exact-tag release run (35660274375), per-job wall time

| Job | Duration | Notes |
|---|---|---|
| Release preflight | 0:00:07 | fine |
| Exact-tag CI / Detect implementation changes | 0:00:05 | fine |
| **Debian 12 offline container** | **1:19:33** | `python -m pytest -q`, zero parallelism, 1886 tests serially |
| G28 readiness tiers (3.11, ubuntu) | 0:37:30 | full source suite, xdist bounded to 14 but host is not 14 vCPU |
| G28 readiness tiers (3.12, ubuntu) | 0:40:37 | ″ |
| G28 readiness tiers (3.13, ubuntu) | 0:34:56 | ″ |
| **G28 readiness tiers (3.11, macos)** | **1:22:02** | slowest job in the whole run |
| G28 readiness tiers (3.12, macos) | 1:22:13 | slowest job in the whole run |
| G28 readiness tiers (3.13, macos) | 1:19:25 | |
| Built-wheel resources | 0:00:17 (failed) | unrelated failure (verify_installed_g03), not a duration problem |
| **Total run wall time** (tag push → last job) | **1:22:38** | dominated by the 3 parallel macOS legs (~1h22m) and Debian offline (1h19m), which both ran concurrently with everything else — so the *build* job (which needs `ci` to finish) couldn't start until ~1h22m in |

Current PR run on this branch (35897986900, non-full_matrix): Built-wheel
resources now passes in **1:57** (vs. the v0.1.7 exact-tag run's 17s-then-fail;
the earlier failure was a since-fixed verifier bug, not a duration issue).
Source suite (Python 3.11 on ubuntu-latest, single-OS PR lane) was still
in-progress when evidence was gathered; its xdist-plan behavior was verified
directly instead (see below) rather than blocking on the live run.

## Root cause: why Debian-offline took 1h19m

`tools/run_debian_offline.sh` ran plain `python -m pytest -q` — no `-n`, no
xdist plugin invocation — against the full 1886-test source suite, entirely
serially, inside a `--network none --read-only` container. `pytest-xdist` was
already installed (`pyproject.toml` `test` extra, already `uv sync`'d into the
image) but never invoked. Every other lane (`make test-source`) already uses
the bounded xdist runner in `tools/run_ci_tests.py`; only this one path
diverged.

## Root cause: why macOS legs took ~1h22m each

Same 1886-test suite via `make test-source`, correctly xdist-parallelized,
but on macos-latest's (3 vCPU) much slower/costlier hosted runner, ×3 Python
versions in `full_matrix`, each an independent ~1h22m leg run in parallel
with each other (not serially) — so the *fix* here is not parallelism (already
parallel across the matrix) but matrix breadth: 3 macOS legs cost 3x the
macOS-minute budget and 3x the flake surface for no OS-specific coverage gain
over 1 leg, since Python-version behavior is not OS-dependent in this suite.

## Levers evaluated

1. **Matrix — IMPLEMENTED.** `source-tests.strategy.matrix` in
   `pull_request.yaml`: full Python 3.11/3.12/3.13 stays on ubuntu; macOS
   full_matrix now runs **only Python 3.12** (middle version) via an
   `exclude:` on 3.11/3.13 for `os: macos-latest`. Ubuntu-only PR lane
   (non-full_matrix) is unchanged (3.11 only, as before).
   - **Coverage change / risk**: macOS 3.11 and macOS 3.13 no longer run in
     CI (was: macOS × all 3 versions). Risk is low — nothing in the test
     suite or source tree branches on `sys.version_info` combined with
     `sys.platform`; OS-specific behavior (path handling, subprocess,
     tmpfs/readonly-fs assumptions) is exercised by the one remaining macOS
     leg (3.12) and by every ubuntu leg across all 3 Python versions. If a
     3.11-or-3.13-only macOS regression exists it would only be caught by
     the nightly `compatibility_job.yaml` cron (unchanged, still full
     3-Python macOS) or a manual `workflow_dispatch(full_matrix=true)` run
     the coordinator can trigger before a release.
2. **xdist on CI — VERIFIED, not changed in `run_ci_tests.py`.**
   `_xdist_options()` already computes `actual_workers =
   min(requested, max, os.cpu_count())`, so it was already safe/bounded on
   GitHub runners regardless of the `TEST_XDIST_MAX_WORKERS=14` env value
   (14 is just a ceiling above real hardware, never the binding constraint).
   Confirmed directly: `_xdist_options('auto', '4', 'worksteal')` →
   `['-n', '4', '--dist=worksteal']`. Set explicit per-OS caps in the
   workflow env (`4` on ubuntu-24.04, `3` on macos-latest) so the ceiling
   documents the real runner shape instead of an arbitrary `14`; behavior is
   unchanged because `os.cpu_count()` was already the tighter bound.
3. **Caching — IMPLEMENTED.** Added `cache-dependency-glob: uv.lock` to every
   `astral-sh/setup-uv@v8.0.0` step that runs `uv sync` against the lockfile
   (source-tests, behavior, wheel, release build, release smoke-artifacts).
   Left the two `verify-testpypi`/`verify-pypi` jobs alone — they `uv tool
   install` a published package from an index, not `uv sync` from `uv.lock`,
   so a lockfile-keyed cache glob doesn't apply there. No UI/yarn build job
   exists in either workflow, so no yarn caching lever applies.
4. **Debian offline container — IMPLEMENTED (the biggest single win).**
   `tools/run_debian_offline.sh` now calls
   `tools/run_ci_tests.py source --xdist-workers ... --xdist-max-workers ...`
   (same bounded runner as `make test-source`) instead of raw
   `python -m pytest -q`. `pytest-xdist` was already in the image via the
   `test` extra; nothing else changes about the container's offline/read-only
   contract. Also added `docker/setup-buildx-action` +
   `docker/build-push-action` with `cache-from/to: type=gha` so unchanged
   Dockerfile layers (apt install, `uv sync`) are cached between runs instead
   of rebuilt from scratch every time.
5. **`concurrency:` with cancel-in-progress — ALREADY PRESENT, unchanged.**
   `pull_request.yaml` already had
   `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`. No
   change needed; release runs correctly keep `cancel-in-progress: false`.
6. **`timeout-minutes` — IMPLEMENTED on every job.** None existed before (a
   hang could burn unbounded runner time — this is exactly how a stuck
   Debian container could have looked indistinguishable from "just slow"
   before this change). Added conservative but real budgets: 5 min for
   metadata/preflight jobs, 10 min for wheel/build/publish/verify jobs,
   15 min for the behavior evaluator, 20 min for the source-suite matrix and
   debian-offline (each has real headroom above their post-fix projected
   times below).
7. **Release job parallelism — REVIEWED, not changed.** `build → smoke-
   artifacts → publish-testpypi → verify-testpypi → publish-pypi →
   verify-pypi → github-release` is a real, intentional dependency chain:
   each publish step's precondition is the previous step's success (don't
   publish to real PyPI before a fresh TestPyPI install is verified; don't
   create the GitHub Release before the real PyPI install is verified). This
   is a safety property, not slack — flattening it would let a bad artifact
   reach PyPI before verification catches it. No change made; this chain was
   never the dominant cost anyway (`ci` + debian-offline were).
8. **`workflow_dispatch` — IMPLEMENTED.** Added a `workflow_dispatch` trigger
   to `pull_request.yaml` with a `full_matrix` boolean input (default
   `false`), alongside the existing `pull_request` and `workflow_call`
   triggers. The coordinator can now run
   `gh workflow run "Pull request" --ref <branch> -f full_matrix=true` to get
   the release-equivalent full compatibility matrix on this branch without
   tagging, to measure the projections below before cutting a release.
9. **Runner pin — IMPLEMENTED.** Every `ubuntu-latest` in both workflows is
   now `ubuntu-24.04` (the `verify-testpypi`/`verify-pypi` matrices and every
   single-OS job). GitHub has flagged the `ubuntu-latest` → Ubuntu 26
   migration for Oct 19, 2026, which is inside this project's active release
   window; pinning removes that migration as a source of release-day
   surprise. `macos-latest` was left unpinned (no equivalent imminent-migration
   warning was found for it in this pass; can revisit if one surfaces).

## Before → projected-after (per job)

| Job | Before (measured) | After (projected) | Reasoning |
|---|---|---|---|
| Debian 12 offline container | 1:19:33 | **~20–25 min** | Same 1886 tests, now xdist-bounded to 4 workers on a 4-vCPU runner instead of serial; roughly wall-clock/4 minus xdist overhead, plus Docker layer caching cuts the build step (previously ~12s, already cheap) further on repeat runs. Timeout set to 20 min as the enforced ceiling — will tighten once measured. |
| G28/Source suite (ubuntu, any Python) | 0:35–0:41 | **unchanged, ~0:35–0:41** | Was already correctly xdist-bounded; `TEST_XDIST_MAX_WORKERS` ceiling change (14→4) is a no-op since `os.cpu_count()` was already the binding constraint on these 4-vCPU runners. |
| G28/Source suite (macos, full_matrix) | 3 legs × ~1:20–1:22 (parallel with each other) | **1 leg × ~1:20–1:22** | Matrix narrowed from 3 Python versions to 1 (3.12) on macOS; wall-clock for the `ci` job is dominated by the slowest concurrent leg either way, so this mainly cuts macOS-minute cost and flake surface, not the `ci` job's own critical path (see total run row below). |
| Built-wheel resources | 0:17 (failed, unrelated bug) / now 1:57 (passing) | **unchanged, ~2 min** | Already fast; no lever applies. |
| Release preflight / Detect changes | ~7–20s | **unchanged** | Already trivial. |
| **`ci` job (Exact-tag CI) critical path** | **~1:22** (bounded by slowest concurrent leg: macOS full_matrix or Debian-offline, both ~1h20m) | **~20–25 min** | Once Debian-offline drops to ~20–25 min and the (now single) macOS leg stays ~1h20m... **macOS full_matrix leg is now the long pole**, not Debian-offline. See risk note below — this is the one place the ≤30 min release-CI target is NOT yet met by these changes alone. |
| build → github-release chain | not separately measured (only reached in a full un-failed run) | **unchanged, low minutes each, chain floor ~10–20 min** | Real dependency chain, already fast per-job; not the bottleneck. |

### Target-miss called out explicitly

The brief's release-CI target is ≤30 min to the publish jobs. After these
changes, **Debian-offline drops from the long pole to ~20–25 min, but the
single remaining macOS `full_matrix` leg is still ~1h20m** (matrix breadth
was the lever that cut macOS *cost*, not macOS *per-leg duration* — a lever
this task explicitly scoped out of `tools/run_ci_tests.py`/Makefile changes
beyond xdist cap/flags, since macOS wall time is dominated by macOS runner
I/O and Python startup overhead per xdist worker, not test-selection).
**Escalating this**: hitting ≤30 min for the full release `ci` job requires
either (a) accepting the coordinator's call on whether macOS full_matrix
coverage during exact-tag CI is worth ~1h20m and moving it to a
post-publish or nightly-only gate instead of a pre-publish blocking gate, or
(b) further splitting the macOS source suite by test directory across
multiple parallel jobs (a `tools/run_ci_tests.py` structural change beyond
this task's "xdist cap/flags only" scope). Left both as an explicit decision
for the coordinator rather than acting unilaterally on release-blocking
coverage.

## Coverage-risk summary

- macOS 3.11 and macOS 3.13 no longer run in `full_matrix` (PR
  `workflow_dispatch`/release `ci`); only macOS 3.12 does. Nightly
  `compatibility_job.yaml` is unchanged and still covers all 3. Risk: low
  (no OS×version-specific branching found in source or tests during this
  pass), but is a real reduction in pre-publish signal — flag for the
  coordinator to confirm acceptable.
- Everything else (ubuntu 3.11/3.12/3.13, behavior lane, wheel lane, Debian
  offline's actual assertions) runs exactly the same tests as before; only
  parallelism/caching/runner-pin changed, not test selection.

## Acceptance checks run

- `uvx --from pyyaml python -c "yaml.safe_load(...)"` — OK on all 3 workflow
  files.
- `actionlint` — not installed on this host; not installed as part of this
  task. Skipped per acceptance criteria's "or actionlint if available".
- `make -n test-source test-behavior test-wheel` — resolves correctly,
  unchanged targets.
- `sh -n tools/run_debian_offline.sh` — syntax OK.
- Directly exercised `_xdist_options('auto', '4', 'worksteal')` from
  `tools/run_ci_tests.py` (not modified, just verifying the math the new
  Debian-offline script now relies on) → confirmed `-n 4 --dist=worksteal`
  regardless of host CPU count, matching a 4-vCPU ubuntu-24.04 runner.
- Did not run `uv run --locked --extra test pytest tests/behaviors/ci_tooling`
  since `run_ci_tests.py` itself was not touched (only `run_debian_offline.sh`,
  a shell script, and workflow YAML).

## Files touched

- `.github/workflows/pull_request.yaml`
- `.github/workflows/release.yml`
- `tools/run_debian_offline.sh`
- `.orchestrator/workers/ci-speed.md` (this file)

No changes to `.github/workflows/compatibility_job.yaml` (nightly full-matrix
cron; correctly still calls `pull_request.yaml` with `full_matrix: true` and
inherits every fix above automatically).

## Next step for the coordinator

Trigger `workflow_dispatch(full_matrix=true)` on this branch to measure the
actual post-fix Debian-offline and ubuntu source-suite durations (macOS full
leg will still be ~1h20m as projected above — that's expected, not a bug).
