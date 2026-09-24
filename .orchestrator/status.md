# Orchestrator status

_Updated 2026-09-23 21:55 MDT by Claude (coordinator of `run_7cba834cb817`, worktree `gigai-v0.1.9`, branch `karthik446/gigai-v0.1.9` main (0.1.8.1, `7adf3bb`) merged in at `8380102`; pushed). **Wave 1 verified, committed and pushed** (workflow + tests first, operator 15:20). PR CI running on `26b3e8a`; next, a docs-only push proves the skip. Draft PR #37: https://github.com/karthik446/gigai/pull/37 The v0.1.8 end state is in git history (`27b6532:.orchestrator/status.md`) and in `handoffs/0.1.8-09-23-26-release-handoff.md`._

## Coordinator
- **Claude**: `run_7cba834cb817` (v0.1.9). Created 21:05Z.
- **Parallel, not ours:** the v0.1.8 coordinator (`run_b12de8fdda28`, worktree `gigai-v0.1.8`) is running the operator's UAT of the published gigai 0.1.8. UAT bugs come here as fix requests. Don't touch that worktree or that Run.

## Goal
- **Wave 1 (operator, 15:20): "fix the workflow and tests first, as I do UAT."** First-things 0–3 + CI job renames + testpaths drop + S19 steps 1–2. UAT fix requests jump the queue.
- Later, not decided: S16/S17 stay deferred until find-jobs works live (decisions.log 10:40); S18 undecided; S20 questions stay open.

## v0.1.8 release check (15:05 MDT)
- PyPI 0.1.8 published (20:13Z). The rerun of the failed jobs passed PyPI clean install (ubuntu + macOS) and created the GitHub Release at 21:02Z: https://github.com/karthik446/gigai/releases/tag/v0.1.8
- The Release still has the **auto notes** (2 PR lines). The real notes (`/Users/kar/orca/workspaces/gigai/v0.1.8-release-notes.md`) are not applied. Waiting on the operator (it's outward-facing).
- Post-release full matrix (run 35909539154) **finished with 2 failures**: Debian 12 offline container, and Source suite Python 3.12 on macos-latest. The other 9 passed (incl. macOS 3.11/3.13). PyPI and the Release are unaffected. Audit proposed, waiting on the operator.

## In progress
| Packet | Model | State | Task / dispatch | Next |
| --- | --- | --- | --- | --- |
| scout-run-supervisor (C): `gigai scout run/stop/status` (background, logs, health, browser) + config/resume-missing messages | Sonnet 5 | running | `task_0391b3958264` / `ctx_a64a205ef6d5` | verify → commit → B4 cards → D |
| acquire-country-filter: B1 filter at acquire + country fix (Lever/Ashby structured, pycountry, region tokens), B3 sponsorship, B5 all-sources-failed | Sonnet 5 | running | `task_461018bb4fcc` / `ctx_28f685c01dc4` | verify → commit |
| selection-diversity-u2: B2 per-company cap + dedupe + round-robin; U2 codex_cli → setup target | Sonnet 5 | running | `task_044fd792fd34` / `ctx_e8c7d3383e77` | verify → commit |
| wire selection.py into acquire's selection loop; B4 progressive cards; D acceptance (README + installed wheel) | — | not started | | after acquire / C |

## Next up
- UAT fix requests from the v0.1.8 coordinator (they jump the queue).
- After wave 1: S16/S17 stay deferred until find-jobs works live (UAT). S18 undecided.

## Done
- Run `run_7cba834cb817` created.
- Handoff + README "Handoffs" section committed as the first v0.1.9 commit.
- **secrets-core committed** `a915c24`: `gigai secrets add/list/rm`, env → ~/.gigai/.env resolver (python-dotenv, interpolation off, 0600 under umask 077). Coordinator reproduced + re-verified the 2 r0 bugs.
- **README uv-only** `f1fb6c6` (+ API command via `uv tool run --from gigai`, verified on 0.1.8.1).
- **Country-data research** accepted (`.orchestrator/research/country-data.md`): Lever/Ashby structured country fields unread; pycountry fallback. Implementation waits on the operator.
- **scout-setup-cmds (B)** `b0eea58`: `gig use`, `scout install`, `scout resume add` (coordinator CLI end-to-end, non-git target). **exa-query-errors** `5fec35d`.
- **secrets-exa** `9e66428` (Workstream 0 P0-P3 done). **scout-ui-package (A)** `f4d0fb1`: UI in the wheel, served by the API; PR CI freshness job.
- **Merged main (0.1.8.1, #38)** at `8380102`: the release.yml conflict was resolved as main's shipped release design + `actions: read` on the pull_request.yaml callers; our preflight notes hard-fail was dropped for main's fallback (worker `workers/merge-main-release.md`; coordinator re-ran 63 focused tests + make unit-tests 869). 0.1.8.1 worker reports archived to `runs/v0.1.8.1/`.
- **S21 spike** (`docs/development/v0.2.0/spikes/S21-gig-repos-and-data-ownership.md`) accepted after r1-r3 (reviews `reviews/s21-r0.md`; r3 fixed a coordinator over-attribution) and committed.
- **Proposed v0.2.0 gig-workbench roadmap** (`docs/development/v0.2.0/roadmaps/v0.2.0-gig-workbench-roadmap.md`) accepted as a proposed doc after r1-r2 (review `reviews/roadmap-workbench-r0.md`), committed `939c5b3`.
- **Docs-only CI skip proven live** (run 35924315765 on `aa9d06a`: only `changes` ran; the `before` run succeeded).
- **release-pipeline verified + committed** (`c4cc1b3`, r1): Release right after publish; install retried ~10 min; notes from CHANGELOG. Fully proven only by the next tag.
- **ci-docs-skip verified + committed** (`384234e`, r1): docs-only skip when `before` passed; callers grant `actions: read`; job renames.
- Wave 1 pushed; PR #37 body updated; workers released and tabs closed (sweep).
- **test-lanes verified + committed** (`acb7728`): `make unit-tests` 729 passed / 6.9s (coordinator re-run); testpaths −41; S19 revision note; skill §4 allows `make unit-tests` for workers.
- Git workflow adopted (skill §9, memory updated); branch pushed; draft PR #37 opened.

## Open tabs
- None started by this Run.

## Blocked on operator
- After the roadmap draft: pull Workstream 0 (connect secrets: `gigai secrets add`, .env fallback, Exa through the resolver) into 0.1.9?
- Post-release CI failures (Debian offline, macOS 3.12): operator said ignore for now.
- Release checklist change: GitHub Release notes = the CHANGELOG `### X.Y.Z` section verbatim. Write 0.1.9's section for users before tagging.
- v0.1.8 release notes: operator will apply later (deferred).

## Flagged
- S16 counts disagree: 45 strict rows in its inventory and in S20, but open question 2 says "the existing 34". Fix before S16 is used for sizing.
- The first-things note cites `release.yml:62-67` for `--generate-notes`; it's at `release.yml:303` today. `github-release` needs `verify-pypi` (`release.yml:239`), which confirms item 0.
- Codex weekly quota was ~23% at the end of v0.1.8.
