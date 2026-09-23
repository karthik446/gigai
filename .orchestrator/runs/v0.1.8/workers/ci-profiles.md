# ci-profiles worker report

## Task

Replace the boolean `full_matrix` input on `pull_request.yaml` with an
explicit `profile` input (`pr` | `release` | `full`), per the operator:
"everyone uses macOS, so that's the one to test at release"; the broad
matrix runs nightly and/or after release. Fix the finding that
`compatibility_job.yaml` (nightly cron + dispatch) had silently lost
macOS 3.11/3.13 coverage after the ci-speed change narrowed
`full_matrix` macOS to 3.12-only — the ci-speed report's claim that the
nightly job still ran the full matrix was wrong.

READ vs EXECUTED: READ all three workflow files in full before editing.
EXECUTED edits only (Edit/Write). **No git add/commit/push, no test
runs** — the coordinator commits after the in-flight timing run
(35899145345) finishes.

## Design implemented

`pull_request.yaml` gained a `profile` input (`workflow_call`: free
`string`, default `pr`; `workflow_dispatch`: `choice` with options
`pr`/`release`/`full`, default `pr`) replacing the boolean
`full_matrix`. A new `source-matrix` job (runs when `changes` finds
code changes) computes the exact `os`×`python` `include` list for
`source-tests` from `profile` via a shell `case`, replacing the old
boolean-ternary `fromJSON(...)` + `exclude` matrix — `release` needed a
non-cross-product pair (macOS×3.12 + ubuntu×3.12, no macOS×3.11/3.13),
which the old exclude-based matrix couldn't express directly for three
different profiles. `debian-offline`'s `if` now checks
`profile == 'full'` instead of the boolean. The `changes` job's
diff-skip logic now short-circuits to `code_changed=true` whenever
`profile != 'pr'` (covers both `release` and `full`), preserving the
diff-based skip only for the default `pr` profile.

| profile | jobs | runner / python |
|---|---|---|
| `pr` (default; `pull_request` events, and any caller that omits `profile`) | source-tests, behavior, wheel | ubuntu-24.04 × 3.11 only |
| `release` (`release.yml` `ci` job, blocks publishing) | source-tests (×2), behavior, wheel — **no** debian-offline | macos-latest × 3.12, ubuntu-24.04 × 3.12 |
| `full` (`compatibility_job.yaml` nightly cron + dispatch; new `release.yml` post-release job) | source-tests (×6), behavior, wheel, debian-offline | ubuntu-24.04 × {3.11,3.12,3.13}, macos-latest × {3.11,3.12,3.13}, + Debian 12 offline container |

Kept unchanged: timeouts, `astral-sh/setup-uv` caching, the
ubuntu-24.04 pin, the `concurrency` group, and the Debian xdist
(`TEST_XDIST_*`) fix from the ci-speed change.

## Files changed (OWNED paths only)

- `.github/workflows/pull_request.yaml` — `profile` input (both
  trigger types); `changes` job profile-aware skip logic; new
  `source-matrix` job computing the `include` list per profile;
  `source-tests` now keyed off `matrix.include` from that job instead
  of the old boolean `fromJSON`/`exclude` matrix; `debian-offline`'s
  `if` keyed off `profile == 'full'`.
- `.github/workflows/compatibility_job.yaml` — calls with
  `profile: full` (was `full_matrix: true`). This is the fix: nightly
  cron + manual dispatch now get the true full matrix again
  (macOS+ubuntu × 3.11/3.12/3.13), restoring the macOS 3.11/3.13
  coverage the ci-speed change had dropped.
- `.github/workflows/release.yml` — `ci` job (blocks publishing) now
  calls with `profile: release` (macOS+ubuntu × 3.12 only, no Debian
  offline) instead of `full_matrix: true`. Added a new
  `post-release-compatibility` job, `needs: github-release` (i.e. runs
  *after* the release is published, so it cannot block publishing),
  calling `pull_request.yaml` with `profile: full` and
  `ref: refs/tags/${{ github.ref_name }}` — every release now gets the
  full compatibility matrix run against its own tag after shipping; a
  failure there surfaces on the release workflow run and means a
  follow-up patch release.
- `.orchestrator/workers/ci-profiles.md` — this report.

## Verification performed (READ, no execution)

- YAML parses: `uvx --from pyyaml python -c "yaml.safe_load(...)"` on
  all three files — all OK.
- `actionlint`: not available in this environment (no PyPI package;
  not installed via Homebrew either) — skipped per the "if it runs"
  acceptance clause. Not executed.
- Re-read each file after editing to confirm the three call sites:
  - `compatibility_job.yaml` → `pull_request.yaml` with `profile: full`. ✅
  - `release.yml` `ci` job → `pull_request.yaml` with `profile: release`,
    `ref: refs/tags/${{ github.ref_name }}`. ✅
  - `release.yml` new `post-release-compatibility` job (needs
    `github-release`) → `pull_request.yaml` with `profile: full`,
    `ref: refs/tags/${{ github.ref_name }}`. ✅
  - Plain `pull_request` events (no `profile` given) → `changes` and
    `source-matrix` both default via `inputs.profile || 'pr'` →
    `pr` profile, ubuntu-24.04 × 3.11 only (today's PR behavior,
    unchanged). ✅
- No test runs were performed (explicitly out of scope for this task).

## What's left

Nothing outstanding for this task's scope. The coordinator still needs
to: (1) let the in-flight timing run 35899145345 finish, (2) commit
these changes (no commit was made by this worker, per instructions),
and (3) decide whether `verify-testpypi`/`verify-pypi`'s own
`os: [ubuntu-24.04, macos-latest]` matrices (unrelated, PyPI-install
smoke tests, not the compatibility source-suite) need any follow-up —
out of scope here, left untouched.
