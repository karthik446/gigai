# ci-docs-skip worker report

**State:** done, ready for review/commit by coordinator.

## Files changed (owned files only)

- `.github/workflows/pull_request.yaml` (modified) — `detect` step now calls
  `tools/ci_changes.py`; added `permissions: {contents: read, actions: read}`
  to the `changes` job; renamed three job display names.
- `tools/ci_changes.py` (new) — classification + diff-range decision logic,
  plus the CLI (`main()`) that the workflow step invokes.
- `tests/behaviors/ci_tooling/test_ci_changes.py` (new) — 37 unit tests.
- `.orchestrator/workers/ci-docs-skip.md` (this file, new).

Not touched: `release.yml`, `compatibility_job.yaml`, `pyproject.toml`,
`Makefile` (other workers' pending changes to some of these are visible in
this shared worktree's `git status` — I did not modify or read into those
diffs beyond confirming they weren't mine).

## What I did

1. **Extracted the classification into `tools/ci_changes.py`.** Pure
   functions (`is_code_path`, `classify_paths`, `decide`,
   `decide_synchronize`) hold all the decision logic with no I/O, so they're
   fully unit-testable; `main()` is a thin CLI wrapper doing the actual
   `git diff` / `git merge-base --is-ancestor` / `gh api` calls and writing
   `GITHUB_OUTPUT`. The `detect` step's `run:` is now one line:
   `python3 tools/ci_changes.py`.

2. **Non-code glob set** implemented via `fnmatch`: `*.md`, `*.markdown`,
   `*.txt`, `docs/**`, `.orchestrator/**`, `.claude/**/*.md`. Everything
   else — including `.claude/**/*.py`/`*.sh`, workflows, `pyproject.toml`,
   `uv.lock`, `src`, `tests`, `tools` — counts as code. Verified the exact
   acceptance case: `.claude/skills/x/SKILL.md` → non-code,
   `.claude/skills/x/run.sh` → code (same directory, different verdict).

3. **Synchronize skip logic**: on `pull_request`/`synchronize`, computes the
   incremental diff (`before`..`SHA`) only when `before` is present,
   non-zero, and a real ancestor of `SHA` (`git merge-base --is-ancestor`);
   otherwise falls back to the whole-PR diff (`base`...`SHA`), covering
   force-push, missing `before`, and all-zero `before`. When the
   incremental diff is available and all-non-code, it looks up this
   workflow's run conclusion for head SHA `before` via
   `gh api repos/<repo>/actions/workflows/pull_request.yaml/runs?head_sha=<before>`
   (added `actions: read` permission to the `changes` job for this). Skips
   (`code_changed=false`) only when that conclusion is exactly `"success"`;
   any other conclusion (cancelled/failure/in-progress/unknown/no run
   found) falls back to classifying the whole-PR diff instead of trusting
   the incremental one. `opened`/`reopened`/`ready_for_review` and non-`pr`
   profiles (`release`/`full` via `workflow_call`) are unchanged — always
   whole-PR diff / always run, respectively.

4. **Job renames** (display name only; job ids/`needs:`/outputs untouched):
   - `Source suite (Python X on OS)` → `All tests (source) (Python X on OS)`
   - `Deterministic G28 behavior evaluation` → `Eval scoring check (frozen cases)`
   - `Built-wheel resources` → `Wheel install + packaging checks`

   Grep for the three old names across the whole repo (see below) — the
   only live/renameable hit was in `.github/workflows/pull_request.yaml`
   itself, which I fixed. Every other hit is in a historical/frozen
   document (v0.1.8 orchestrator run archives under `.orchestrator/runs/`,
   evidence docs under `docs/development/...`, and the task spec file
   itself, which just quotes this task's own instructions) — those are
   records of what CI *was called* at the time, not live references, so I
   did not edit them; they're out of my owned-files list regardless.

5. **Step logging**: `main()` prints the diff mode used (`whole_pr` /
   `incremental`) with its base/head SHAs, a `reason:` line explaining why
   (e.g. "before SHA is a valid ancestor; using incremental diff;
   incremental diff is all non-code and the source suite already passed for
   before SHA -- skipping"), the before-run conclusion when one was looked
   up, the final `code_changed` verdict, and the full list of changed
   paths.

## Old-name grep hits (full list)

```
=== "Source suite" ===
.orchestrator/workers/specs/ci-docs-skip.txt:8   (this task's own spec text — quotes the rename instruction, not a live reference)
.orchestrator/runs/v0.1.8/workers/ci-speed.md:41,150,151   (frozen v0.1.8 run record)
docs/development/evidence/phase-5/G31/machine-verification.md:7   (frozen evidence doc)
docs/development/evidence/phase-1/{G01,G02,G03,G04,G05}/terminal-handoff.md   (frozen evidence docs)
.github/workflows/pull_request.yaml:118   -> FIXED (owned file)

=== "Deterministic G28 behavior evaluation" ===
.orchestrator/workers/specs/ci-docs-skip.txt:8   (spec text, as above)
.github/workflows/pull_request.yaml:150   -> FIXED (owned file)

=== "Built-wheel resources" ===
.orchestrator/workers/specs/ci-docs-skip.txt:8   (spec text, as above)
.orchestrator/runs/v0.1.8/workers/ci-audit.md:112
.orchestrator/runs/v0.1.8/workers/ci-speed.md:35,152
.orchestrator/runs/v0.1.8/workers/rel/ci-audit.txt:1,2,4   (frozen v0.1.8 worker task text)
docs/development/v0.1.8/evidence/S11-ci-historical-run.json:53
docs/development/v0.1.8/evidence/S11-test-runner-and-ci-performance.md:132
docs/development/v0.1.8/phase-2/evidence/P2-AUD-01-claims.json:120,172,211
docs/development/v0.1.8/phase-2/evidence/P2-AUD-01-baseline-and-invocation.md:29,140
.github/workflows/pull_request.yaml:170   -> FIXED (owned file)
```

Repo has no branch protection / required checks (confirmed by the task
spec: `gh api .../branches/main/protection` → 404, only ruleset disabled),
so these renames carry no required-check-name risk.

## Final `detect` step

```yaml
  changes:
    name: Detect implementation changes
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    permissions:
      contents: read
      actions: read
    outputs:
      code_changed: ${{ steps.detect.outputs.code_changed }}
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0
          ref: ${{ inputs.ref || github.sha }}
      - id: detect
        env:
          EVENT_NAME: ${{ github.event_name }}
          PR_ACTION: ${{ github.event.action }}
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          BEFORE_SHA: ${{ github.event.before }}
          PROFILE: ${{ inputs.profile || 'pr' }}
          SHA: ${{ github.sha }}
          GH_TOKEN: ${{ github.token }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          WORKFLOW_FILE: pull_request.yaml
        run: python3 tools/ci_changes.py
```

## Evidence

**READ:** `.github/workflows/pull_request.yaml`, `.github/workflows/release.yml`
and `compatibility_job.yaml` (to confirm how they call this workflow via
`workflow_call` with `profile: release`/`profile: full`, not modified),
`tools/run_ci_tests.py` and an existing `tests/behaviors/ci_tooling/` test
file (for import/style conventions), `Makefile` test targets (read-only, not
modified), repo tree under `.claude/` and `.orchestrator/` (to confirm real
`.py`/`.sh` files exist under `.claude/` for the classification test case).

**EXECUTED:**
- `uv run --locked --extra test pytest tests/behaviors/ci_tooling/test_ci_changes.py -q`
  → **37 passed** (exact acceptance selector, run twice, final state clean).
- `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/pull_request.yaml'))"`
  → **OK**.
- `actionlint` → **not installed** in this environment (`which actionlint`
  found nothing); could not run it.
- `grep -rn` for all three old job-name strings across the whole repo
  (results above).
- Synthetic local-git end-to-end runs of `tools/ci_changes.py main()`
  (temp repo in the scratchpad dir, cleaned up after): whole-PR mode on
  `opened`, force-push fallback (`before` not an ancestor → whole-PR,
  confirmed `code_changed=true` from a real code file).
- Real-repo end-to-end runs of `main()` against this repo's actual commit
  history and the real `gh api` endpoint (read-only `gh api` calls only,
  no writes): confirmed `git merge-base --is-ancestor` correctly rejects a
  commit that's actually on a sibling branch (`v0.1.8`, not an ancestor of
  this `v0.1.9` HEAD) and falls back to whole-PR; confirmed a real
  docs-only incremental commit (`0a033c7` vs ancestor `d407a3d`, touching
  only `.orchestrator/**` and a `.claude/**/*.md` file) is classified
  non-code, the `gh api ...runs?head_sha=<before>` lookup returns `null`
  conclusion for a commit with no recorded run, and the tool correctly
  falls back to the whole-PR diff (which then found real code changes and
  set `code_changed=true`) rather than skipping. Also confirmed the same
  `gh api` endpoint/query-string shape returns `"success"` for a commit
  with a completed passing run in this repo, validating the lookup format
  independent of a full ancestor chain being available locally.
- Did **not** run `git add`/`commit`/`stash`/`reset`/`clean`/`push`, no
  `workflow_dispatch` runs, no live/provider runs, no directory-wide or
  `-n`/`make test` runs — focused selector only, per the task's exclusions.

## Open questions

None blocking. One judgment call, flagged for the coordinator's awareness:
`gh api ... -f head_sha=<sha>` behaved inconsistently across quoting styles
in ad hoc shell testing (worked as separate argv words, 404'd when the
`key=value` pair was passed as a single quoted string to `-f`); I sidestepped
that ambiguity entirely by building the query string explicitly
(`...runs?head_sha=<sha>`) rather than relying on `-f`/`-F` argv encoding,
and verified that exact form against the real GitHub API. Worth a second
pair of eyes on that one call site given it's the mechanism that lets a
docs-only push skip the 17-minute suite.

---

## r1 (rework after coordinator review)

**State:** done, ready for review/commit by coordinator.

### Files changed this round (owned files only)

- `.github/workflows/compatibility_job.yaml` (modified) — added job-level
  `permissions: {contents: read, actions: read}` to the `compatibility` job.
- `tools/ci_changes.py` (modified) — added `build_runs_endpoint()` (now
  filters the run lookup to `event=pull_request`), added
  `parse_caller_jobs()` / `CallerJob` / `caller_jobs_missing_actions_read()`
  (the minimal workflow-job parser for the new permissions test), added a
  runtime-requirements paragraph to the module docstring.
- `tests/behaviors/ci_tooling/test_ci_changes.py` (modified) — added tests
  for `build_runs_endpoint`, the caller-job parser, and a live check that
  every real workflow job calling `pull_request.yaml` grants `actions: read`.
- `.orchestrator/workers/ci-docs-skip.md` (this section).

`release.yml` was **not** touched (owned by the release-pipeline worker,
per instructions), `pull_request.yaml` was **not** further modified this
round (only `tools/ci_changes.py` changed on the diff-checking side).

### What I did

1. **BLOCKER fix — compatibility_job.yaml permissions.** Added
   `permissions: {contents: read, actions: read}` at job level to the
   `compatibility` job in `.github/workflows/compatibility_job.yaml`, so
   GitHub will start the called `pull_request.yaml` workflow now that its
   `changes` job requests `actions: read` for the before-run `gh api`
   lookup. `release.yml`'s two calling jobs (`ci`, `post-release-compatibility`)
   are explicitly out of scope here (release-pipeline worker's job) — see
   "release.yml status" below for what I observed.

2. **New test: every caller of `pull_request.yaml` grants `actions: read`.**
   No PyYAML in the test environment (confirmed: `uv run --locked --extra
   test python3 -c "import yaml"` → `ModuleNotFoundError`, even though the
   ambient system `python3` has it installed globally — see "stdlib
   confirmation" below). Wrote a minimal parser in `tools/ci_changes.py`
   modeled directly on `tools/release_notes.py`'s `parse_workflow_job_needs`
   (same 2-space job-id / 4-space job-key indent assumptions, extended to
   also read a job's `permissions:` block at 4-space with 6-space entries).
   `parse_caller_jobs(workflow_text, path, target_uses)` returns every job
   whose `uses:` matches, with whatever `permissions:` it parsed;
   `caller_jobs_missing_actions_read({path: text}, target_uses)` runs that
   across a `{path: text}` mapping and returns the ones missing
   `actions: read`. Test file adds: unit tests for the parser against
   synthetic YAML text (found-with-permissions, missing-permissions-block,
   permissions-without-actions-read, non-matching `uses:`, non-`uses:` job),
   plus one live test (`test_every_caller_of_pull_request_workflow_grants_actions_read`)
   that reads every real `.github/workflows/*.y*ml` file in this repo and
   asserts `caller_jobs_missing_actions_read(...) == []`.

   **Result: this live test currently PASSES against all three workflow
   files**, not just `compatibility_job.yaml`. I read `release.yml` fresh at
   the start of this round and it had no `permissions:` block on either
   `ci` or `post-release-compatibility`; by the time I ran the parser
   against it a few steps later (shared worktree), both jobs already had
   `permissions: {contents: read, actions: read}` — the release-pipeline
   worker's fix had landed. So the "expected failure until the other worker
   lands" case did not materialize in my run; the test is written to fail
   correctly if that ever regresses (verified by hand against synthetic
   YAML text lacking the permission, in the parser unit tests above), but
   there was nothing to report as currently-failing at the time I ran it.

3. **Run lookup filtered to `pull_request` events.** Extracted the
   endpoint-building into `build_runs_endpoint(repo, workflow_file,
   before_sha)`, now appending `&event=pull_request` to the query string, so
   a `workflow_dispatch` run on the same SHA (e.g. someone manually running
   the `release`/`full` profile against a PR branch) can't be mistaken for
   "the pr-profile source suite passed for before". Added
   `test_build_runs_endpoint_filters_to_pull_request_event` and a second
   endpoint-shape test. Verified the `event=pull_request` filter against
   the real GitHub API (read-only `gh api` call, confirmed it returns only
   `pull_request`-triggered runs).

4. **stdlib-only confirmation.** `tools/ci_changes.py` imports only
   `argparse`, `dataclasses`, `fnmatch`, `os`, `re` (added this round for
   the parser), `subprocess`, `sys` — no third-party packages, so it runs
   on the `changes` job's bare `python3` with no `setup-python`/`uv sync`
   step. Minimum Python: **3.11**, matching this repo's
   `pyproject.toml` `requires-python = ">=3.11"`; nothing in the module
   needs newer syntax (no `match` statements, no walrus, no 3.12-only
   stdlib — `from __future__ import annotations` defers all the `X | None`
   type hints so they don't need 3.10+ at runtime either, but the project
   floor is 3.11 regardless). `ubuntu-24.04` GitHub-hosted runners ship
   Python 3.12 as system `python3` by default, which also satisfies this
   floor. Documented this in the module docstring.

### Evidence

**READ:** `.github/workflows/compatibility_job.yaml` (before and after
editing), `.github/workflows/release.yml` (twice — once at the start of
this round showing no `permissions:` on the two caller jobs, once later
via the parser showing both jobs already fixed by the other worker),
`tools/release_notes.py` (`parse_workflow_job_needs`, as the explicit model
for the new parser), `pyproject.toml` (`requires-python`).

**EXECUTED:**
- `uv run --locked --extra test pytest tests/behaviors/ci_tooling/test_ci_changes.py -q`
  → **46 passed** (up from 37 in r0; all new tests pass, including the live
  caller-permissions check against the real repo state).
- `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/compatibility_job.yaml'))"`
  → **OK**.
- `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/pull_request.yaml'))"`
  → **OK**.
- `uv run --locked --extra test python3 -c "import yaml"` → confirmed
  `ModuleNotFoundError` in the actual pytest environment, vs. ambient system
  `python3` where `import yaml` succeeds — this is why the new test uses
  the hand-rolled parser rather than PyYAML.
- Ad hoc `python3 -c "..."` script exercising `parse_caller_jobs` /
  `caller_jobs_missing_actions_read` directly against the three real
  workflow files, to sanity-check the parser before trusting it in the test
  (output matched the live pytest run).
- `gh api "repos/karthik446/gigai/actions/workflows/pull_request.yaml/runs?event=pull_request&per_page=2"`
  (read-only) → confirmed the `event=pull_request` filter returns only
  `pull_request`-triggered runs in this real repo.
- `git status --short` / `git diff` on owned files only, to confirm I did
  not touch `release.yml`, `Makefile`, `pyproject.toml`, or other workers'
  pending changes visible in this shared worktree.
- Did **not** run `git add`/`commit`/`stash`/`reset`/`clean`/`push`, no
  `workflow_dispatch` runs, no live/provider runs beyond the two read-only
  `gh api` calls above, no directory-wide or `-n`/`make test` runs — focused
  selector only, per the task's exclusions.

### release.yml status (informational, not my change)

At the time I finished this round, `.github/workflows/release.yml` already
had `permissions: {contents: read, actions: read}` on both `ci` (line ~53)
and `post-release-compatibility` (line ~365) — the release-pipeline
worker's fix for the same blocker landed in this shared worktree before I
ran the live test. Nothing further needed from me there.

### Open questions

None blocking.
