# Orchestrator status

_Updated 2026-09-23 15:25 MDT by Claude (coordinator of `run_7cba834cb817`, worktree `gigai-v0.1.9`, branch `karthik446/gigai-v0.1.9` rebased onto `main` at `c36f182`, the #36 hotfix; pushed). **Wave 1 running: workflow + tests first (operator, 15:20), while the operator does UAT.** Draft PR #37: https://github.com/karthik446/gigai/pull/37 The v0.1.8 end state is in git history (`27b6532:.orchestrator/status.md`) and in `handoffs/0.1.8-09-23-26-release-handoff.md`._

## Coordinator
- **Claude**: `run_7cba834cb817` (v0.1.9). Created 21:05Z.
- **Parallel, not ours:** the v0.1.8 coordinator (`run_b12de8fdda28`, worktree `gigai-v0.1.8`) is running the operator's UAT of the published gigai 0.1.8. UAT bugs come here as fix requests. Don't touch that worktree or that Run.

## Goal
Not set yet. Inputs for the roadmap discussion (not decisions):
- First things 0–3 (`/Users/kar/orca/workspaces/gigai/v0.1.9-first-things.txt`): 0 release pipeline (post-publish checks block the GitHub Release), 1 git workflow (draft PR, commit per verified packet), 2 CI skips docs-only pushes, 3 release notes from CHANGELOG + review step.
- Spikes: `docs/development/v0.1.9/spikes/` S16 (core never imports a gig), S17 (gig module classes), S18 (interview-prep graphs), S19 (test diet, `make unit-tests`); `docs/development/v0.2.0/spikes/S20` (stable core; 6 open questions, don't answer yet).
- Small queued items: clearer CI job names; drop `research/*_spike/tests` from pytest `testpaths`.
- Standing constraint (decisions.log 10:40): S16/S17 were deferred until job search → recommendations works live. The live M1 click isn't proven yet; it's what the UAT is testing.

## v0.1.8 release check (15:05 MDT)
- PyPI 0.1.8 published (20:13Z). The rerun of the failed jobs passed PyPI clean install (ubuntu + macOS) and created the GitHub Release at 21:02Z: https://github.com/karthik446/gigai/releases/tag/v0.1.8
- The Release still has the **auto notes** (2 PR lines). The real notes (`/Users/kar/orca/workspaces/gigai/v0.1.8-release-notes.md`) are not applied. Waiting on the operator (it's outward-facing).
- Post-release full matrix (run 35909539154): wheel resources + G28 eval passed; 7 source-suite jobs and Debian **still running** at 21:05Z.

## In progress (wave 1; specs in `workers/specs/`)
| Packet | Model | State | Task / dispatch | Next |
| --- | --- | --- | --- | --- |
| release-pipeline: release.yml Release right after publish; clean-install checks post-release with a bounded retry; notes from the CHANGELOG section (fails preflight if missing) | Sonnet 5 | **rework r1** (review `reviews/wave1-r0.md`: caller `actions: read`, retry the install itself, timeout 20) | `task_41853568a92a` / `ctx_b27ead9fc69b` (r0 `ctx_873d6c06350f`) | verify → commit |
| ci-docs-skip: pull_request.yaml skips heavy jobs on a docs-only push when `before` passed; job renames | Sonnet 5 | **rework r1** (compatibility_job.yaml grants `actions: read`; caller test; event filter) | `task_152fd940c790` / `ctx_269e7458be8f` (r0 `ctx_0e4fbae4e8f0`) | verify → commit → prove live with a docs-only push to #37 |

## Next up
- UAT fix requests from the v0.1.8 coordinator (they jump the queue).
- After wave 1: S16/S17 stay deferred until find-jobs works live (UAT). S18 undecided.

## Done
- Run `run_7cba834cb817` created.
- Handoff + README "Handoffs" section committed as the first v0.1.9 commit.
- **test-lanes verified + committed** (`acb7728`): `make unit-tests` 729 passed / 6.9s (coordinator re-run); testpaths −41; S19 revision note; skill §4 allows `make unit-tests` for workers.
- Git workflow adopted (skill §9, memory updated); branch pushed; draft PR #37 opened.

## Open tabs
- None started by this Run.

## Blocked on operator
- Apply the v0.1.8 release notes? (`gh release edit v0.1.8 --repo karthik446/gigai --notes-file /Users/kar/orca/workspaces/gigai/v0.1.8-release-notes.md`)

## Flagged
- S16 counts disagree: 45 strict rows in its inventory and in S20, but open question 2 says "the existing 34". Fix before S16 is used for sizing.
- The first-things note cites `release.yml:62-67` for `--generate-notes`; it's at `release.yml:303` today. `github-release` needs `verify-pypi` (`release.yml:239`), which confirms item 0.
- Codex weekly quota was ~23% at the end of v0.1.8.
