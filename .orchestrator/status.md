# Orchestrator status

_Updated 2026-09-23: **PR #35 open** (https://github.com/karthik446/gigai/pull/35), 15 commits, final make test green. Next: PR CI → operator merges → `git tag -a v0.1.8 -m "GigAI v0.1.8" && git push origin v0.1.8` → release (~1h45m) → install the published package → M1 live test._ by Claude (coordinator of `run_b12de8fdda28`; the old coordinator terminal closed, and the Run was rebound with `run-use` to generation 2)._

## Coordinator
- **Claude**: `run_b12de8fdda28` (find-jobs functional).
- Codex coordinator keeps `run_f59ded8ba1c4` (earlier roadmap/P3 trace work). One coordinator per Run.

## Goal
Scout find-jobs graph, working end to end from a UI button:
acquire (Exa + HiringCafe sitemaps + auto watchlist + hourly ATS polling) → assess (explicit local / Luna / OpenRouter-later target; requirements × latest-resume matrix, suggestions, questions) → present (Vite `yarn dev` UI + localhost API, "Run workflow").

## Decisions (operator, 2026-09-22)
- **D12 (2026-09-23): Scout is its own gig package.** All Scout code, data (if not digest-pinned) and UI go under src/gigai/scout/, without the scout_ prefix. Core may import gigai.scout.* in 0.1.8; decoupling core (plugin/registry discovery) is 0.2.0 item #1. Release flow: merge PR → tag v0.1.8 → push tag → release workflow.
- **D11 (2026-09-23): T1 approach A.** A minimal G14 extension: generic `graph_node_registry.py` plus existing effects (`network_read`, `credential_use`, `write_workpad`).
- **AUTHORIZATION (2026-09-23 01:00): "orchestrate until you get this one workflow out on scout. you decide when and what needs to happen."** Standing go-ahead to dispatch the implementation waves through M1. Still excluded: deploys, commits unless asked, live network/provider runs without the operator (M1's live click is the operator's).
- **D10 scope: Scout is gig #1 of many. v0.1.8 = Scout working ASAP; more gigs + foundations in 0.2.0.** New milestone M1 "usable" (operator runs a real search from the UI on a source checkout) comes before the installed/release gates. Generic seams are recorded in a 0.2.0 ledger, not built.
- D1 query-based market search, no hand-picked companies. HiringCafe via public sitemaps only (option a).
- D2 per-run model target from existing adapters; OpenRouter key added later; no silent fallback.
- D3 Vite + React UI + localhost Python API; overrides the README "no new frontend" line.
- D4 accept the P3 trace defaults; acquisition needs an explicit network effect/consent.
- D5 (coordinator reading of "get this to completion", confirm): implementation proceeds now; *publishing* v0.1.8 still waits for v0.1.7 to ship.
- Roadmap: a new file; the old one is marked obsolete, not rewritten.
- D5 UI consent = confirm dialog → `direct_local_ui_confirm` over 127.0.0.1 · D6 assess cap = new + role match, max 10/run · D7 resume = newest saved, pinned per run · D8 polling = button only (hourly deferred to cron) · D9 roles/queries = project-local `find-jobs.json`, sealed per run (coordinator default).

## In progress (release prep; specs in `.orchestrator/runs/v0.1.8/workers/rel/`)
| Packet | Model | State | Task / dispatch | Next |
| --- | --- | --- | --- | --- |
| ci-audit: fix the v0.1.7 release failure (Built-wheel resources → verify_installed_g03 "first setup failed") + audit all CI jobs for v0.1.8 | Sonnet 5 | **done + released**: g03 root cause (setup without model target; error JSON on stdout, verifier read stderr); g22 rewritten to the shipped create contract; release.yml's 3 smoke/verify jobs had the same bug and are fixed; test-wheel + test-installed green (worker) · known flake: g04 two-process init | `task_45e5bb5dbe06` / `ctx_4d45e05b38b6` | coordinator verifies locally |
| polish: P1 API JSON 500 on GET errors; P2 UI shows running/waiting | Sonnet 5 | **verified + released** (26 tests + yarn build re-run) | `task_61f0cd0f6bde` / `ctx_0d0d5de75875` | verify |
| commit-prep: un-ignore .orchestrator, secret scan + redact, commit plan, PR body | Sonnet 5 | **verified + released**; the coordinator's own scan found the operator email in commit-prep.md (redacted), 0 keys/dcap; PR body numbers stale → fix after the final make test | `task_cd426a8509d6` / `ctx_fb5648a63b92` | operator confirms → commit + PR |

| **scout-reorg**: move all Scout code/UI into src/gigai/scout/ (prefix dropped; find_jobs/ subpackage; ui → scout/ui) | Sonnet 5 | running · spec `runs/v0.1.8/workers/rel/scout-reorg.txt` | → `runs/v0.1.8/workers/scout-reorg.md` | coordinator: grep + imports + final make test → regenerate the commit plan → PR |
| v019-spikes: create docs/development/v0.1.9/ with S16 (core never imports a gig), S17 (gig module classes), S18 (Scout interview-prep graphs used by agents; JEV-style probabilities); research docs only, the roadmap is undecided | Sonnet 5 | running · `task_dc2e1e6365af` | spec `workers/rel/v019-spikes.txt` | coordinator reviews content |
| omp-research: is Oh My Pi useful vs Orca (visibility, worktrees, dashboard)? no installs | Sonnet 5 | **done + released** (coordinator verified the Orca tier and GitHub stats) · spec `runs/v0.1.8/workers/rel/omp-research.txt` | → `research/oh-my-pi.md` | coordinator reviews claims/sources |
| final make test | coordinator | **aborted** (layout changing) | — | re-run after the re-org |

## Next up
- Later (operator deferred): OMP trial in one throwaway worktree (plan in `research/oh-my-pi.md`); Beads-style `.orchestrator/pinned-facts.md` idea.
- Polish (non-blocking for M1): P1 C-2 GET handlers catch unexpected backend exceptions → JSON 500; P2 UI shows the API's 'running' status when there are no receipts yet.
- 0.2.0 ledger: move the W1BT-1 present batch_ref normalization from bindings into run.py; generalize the node registry; test-seam env hooks → a proper fixture injection mechanism.
- **I-3 must:** register the step functions inside the run's child process (registry is per-process); wire C-2 `start_run` → `launch_find_jobs_run(...)` then `on_run_allocated(run_id)`; add 504 to POST /api/run ROUTES and map consent errors to 403; bind A-6/B-2/C-1 with functools.partial deps (httpx client, EXA key from env).
- After wave 1b: C-4 UI screens (Sonnet, after C-3 lands, takes over App.jsx/main.jsx) · I-3 bind real callables (after I-1, I-2, A-6, B-2, C-1, C-2) · one full `make test` (TEST tab) · operator prerequisites · M1 click.
- Wave 1b after the I-0 review (**A-4 dropped: I-0 already implements normalize/diff/hash/parse_board_url**): I-1, A-1/A-3/A-4/A-5/A-6, B-1/B-2, C-1..C-4 in parallel (routes per the roadmap DAG). Notes for specs: I-1 widens `shared_policy.provider_eligibility` (today only deterministic, `scout_materialization.py:395`); the graph declares assess's effect superset and I-2 narrows it per run.
- If W0c finishes before W0: send a reconcile follow-up to the W0c worker once Amendment 02 lands.
- W0b Terra/medium reviews the P3 trace, Amendment 02 and W0c in one pass.
- W1 = the small-packet DAG from the new roadmap (replaces the old 4-packet plan): Integration seam (Luna/max) · B assess + matrix (Luna/max) · A acquire (Sonnet) · C API + UI (Sonnet).
- W2 Terra reviews per packet · coordinator `make test` in a TEST tab · operator clicks Run workflow.

## Done (recent)
- **Python repaired (coordinator):** uninstalled the corrupted uv 3.11.14; uv 0.5.25 has no 3.11.14 download, so installed 3.11.11 (nothing pins .14); removed .wheel-venv; `uv python list` works.
- Closed all Run workers: 24 released, 7 orphaned terminals closed; 6 stay 'reclaimable' in bookkeeping only (never-settled dispatches; terminals gone).
- **UI dry run passed** (Chrome, fixture backend + real handler + real UI): every screen renders from the contract DTOs.
- **I-0 accepted** after rework: 182 tests (coordinator re-ran), `to_goal_details()` validated against the real run-details schema; the coordinator walked each consumer row. No second Terra pass (every finding fixed and tested). Clarification carried into the A-6 spec: `AcquireInput.rows` non-empty = supplied mode, empty = live fetch.
- I-0 first pass: 22 tests pass, but **Terra verdict rework** (3 blockers: receipt↔run-details, acquire→assess seal, edit diff). The coordinator verified all 3 and had missed them; lesson logged.
- **W0f verified** 01:15: roadmap Rev 3 (228 lines), Amendment Rev 3 (270). D11 real-node execution design, routes at `127.0.0.1:8765`, M1 = consented traversal, one full make test after wave 3. Wave 0 is closed.
- **W0t Terra review** 00:50: fix first. 2 blockers (the scheduler can't run real nodes; I-0 not freezable), 4 majors, 1 minor; citations 15/16 accurate. The coordinator confirmed T1 in code. Triage: `.orchestrator/reviews/terra-w0-triage.md`.
- **W0e fixes F1–F8 verified** 00:38: all 6 greps re-run by the coordinator pass; the changed rows were checked against the code (`_validate_scheduler_policy` :3438, `_terminal_status` :3491). Roadmap Rev 2.1 205 lines, Amendment Rev 2.1 250 lines.
- **W0d reconcile (Sonnet 5) delivered** 00:15. Acceptance greps re-run by the coordinator: pass. Content review: 1 logic error (I-3 must be M1), 2 claims that don't match the doc, 5 small fixes → W0e. Roadmap 192 lines; Amendment 02 Rev 2 260 lines.
- W0c roadmap delivered (23:30): `roadmaps/v0.1.8-find-jobs-functional-roadmap.md` (149 lines); the old roadmap got the banner only; README edits in 2 spots. Content review: naming drift plus 5 DAG holes.
- W0r Amendment 02 Rev 1 delivered (23:35): B1–B3 and G1–G4 fixed, 4/4 citations accurate; regression: dropped the receipt fields, precedence and watchlist shape; D6 logic error.
- **W0 Amendment 02 checked mechanically** (22:45), then **the coordinator's content review found 3 blockers** (UI consent, hosted-assess network effect, no shared-contract owner); sent back as W0r: `phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md`, 156 lines. Checked: the 4 ownership rows are disjoint; all 8 acceptance test files exist; the 5 missing paths are all designated new; 4/4 spot-checked citations are accurate; no non-doc changes. Worker kept for follow-ups (dispatch `ctx_73b51935ef16`).
- Codex update prompt fixed: the W0 Luna start worked (requested = effective).
- Local model eval: see `local-models.md`.

## Waiter
- A background `check --wait` on the Run (30 min timeout) for worker_done, question or escalation.

## Open tabs
| Tab | Running | Log |
| --- | --- | --- |
| TEST launcher check | finished (exit 3, test) | `runs/v0.1.8/logs/214311-test-launcher-check.log` |

## Blocked on operator
- Env check 01:05: node v26.7, yarn 1.22.22 OK; ollama has muse-glimmer/qwen3-coder/qwen3.8; `EXA_API_KEY` **not set** in the coordinator shell.
- Before M1 (wave 4): `EXA_API_KEY` in the env, a real `<target_root>/find-jobs.json`, and at least one committed resume (`--kind resume`).
- nothing now. Later: an `EXA_API_KEY` env var before the A packet's live run; an OpenRouter key whenever you want that route.

## Flagged
- Codex weekly quota **23% left** (seen in worker footers 04:36).
- The Codex sessions started before the Orca restart (A-5/A-6/B-1/B-2) can't reach Orca; their worker_done will never arrive. Work verified via fallback files + coordinator re-runs; their dispatches stay open (don't impersonate).
- C-3 Haiku worker was blocked on a Claude Code **permission prompt** (an `ls`). The coordinator does not approve prompts; dismissed, and C-3 was folded into c34-ui (Sonnet). If a worker tab shows a permission prompt, **you** approve it.
- A-5/A-6/B-1/B-2 original dispatches stayed open after the outage; asked each worker via terminal to re-send worker_done.
- **~00:47 the Orca runtime went down and the Claude rate limit was hit.** A-5/A-6/B-1/B-2 finished but their worker_done failed; recovered via the fallback files. A-1 was stuck on a local AskUserQuestion (rule violation), so the coordinator dismissed it and typed the answer in. C-1 (rate limit) and C-3 (stalled) were nudged via `orca terminal send`.
- Orca reuse: both Claude and Codex terminals can be reused with `--terminal` after worker_done; the W0 Codex session had exited (`agent_unconfigured`), which is session-specific.
- Quota: the plan has 4 Luna-max + 5 Luna-medium items; consider Sonnet for I-1/I-3 at dispatch.
- A2-1 Hourly board polling has no driver: cron is out of scope, so polling happens only on a Run workflow click. Needs an owner or an explicit deferral.
- A2-2 "Latest resume" = the operator must supply an exact revision and digest. Proposed: the API resolves the newest committed revision at Run start, seals it, and shows it in the UI. Operator decision.
- A2-3 Integration edits `run-details.schema.json` (an additive node receipt, per D4). Terra must confirm it's additive with no migration.
- A2-4 The optional HiringCafe sitemap read was skipped, so it's unproven that the sitemaps expose board tokens. Packet A checks this first.
- Terra's FREEZE-04 closure predates Amendments 01 and 02.
- v0.1.7 is tagged, not shipped.
- The operator is running `make test` locally; the coordinator doesn't re-run it.
