# Orchestrator status: FINAL CHECKPOINT (v0.1.9 coordinator, frozen)

_Written 2026-09-24 by Claude, coordinator of Orca Run `run_7cba834cb817` (worktree `gigai-v0.1.9`, branch `karthik446/gigai-v0.1.9`, draft PR #37 https://github.com/karthik446/gigai/pull/37). **This coordinator is FROZEN**: the orchestrator is moving to `/Users/kar/orca/workspaces/gigai/.orchestrator` (its own repo), the skill to `/Users/kar/orca/workspaces/gigai/.claude/skills/`, and a new coordinator runs from `/Users/kar/orca/workspaces/gigai`. Full history: `decisions.log` (newest last), `reviews/` (incl. `pr37-review-findings.md`), `research/`, `workers/` (reports) and `workers/specs/` (every spec)._

## Branch state
- Pushed head: `b635bc4` fix(secrets): --home reaches every key lookup. Local-only orchestrator commits after it: `760738c`, `07fae32`, `be7e0d6`, + this checkpoint commit. Version `0.1.9.dev0` (pyproject + uv.lock). Main (0.1.8.1, #38) merged in at `8380102`.
- Install for UAT: `uv tool install --force "git+https://github.com/karthik446/gigai@karthik446/gigai-v0.1.9"`, then `gigai --version` shows `gigai 0.1.9.dev0`.
- Last PR CI the coordinator verified green: the merge push `ad88708`. Later pushes were not re-watched.

## What v0.1.9 contains (all verified by the coordinator, committed)
- Release/CI/tests: GitHub Release right after publish + CHANGELOG notes (merged with main's 0.1.8.1 design), docs-only CI skip (proven live), `make unit-tests` (~7 s), logs never committed.
- Secrets: `gigai secrets add/list/rm` (python-dotenv, 0600, env first); Exa via secrets; `--home` threaded to every key lookup incl. the model adapters.
- `gigai scout run/stop/status` (background, UI served from the wheel, install + active gig + resume in one step, works inside a non-git target without --target; proven on an installed wheel). `gig use`, `scout install`, `scout resume add` (content-keyed).
- UAT 0.1.8.1 fixes: filter at acquire (Lever/Ashby structured country + pycountry; region-only ≠ US; codes only as uppercase tokens; 73-string location corpus), sponsorship decoding, all-sources-failed, company diversity (≤2/company), U2 codex_cli target, Exa date/country params + HTTP status, progressive per-posting cards + live step status.
- Stage 2: setup interview (11 Qs) + Discover panel in the UI; `gigai scout discover` (OpenAI web_search ×3 + H-1B DOL data ONLY when sponsorship is required; evidence sidecar; budget guard).
- Interview prep: `gigai scout prep <posting-url>` (company research w/ verified sources ≤ $0.50, role research, question categories, prep notes; the resume never goes to web search; plain JSON store).
- PR #37 review fixes: P0-1..P0-5 and the P1s CSRF, stale pid, secrets --home.
- Docs: README uv-only + one-command Scout flow; spikes S21, S23, S24; proposed v0.2.0 roadmap (not accepted).

## In progress
- Nothing dispatched. No coordinator waiter/watcher shells running (verified). All 47 workers settled; every worker terminal closed (Orca's list may still say "retained" for 24; the tabs were closed with `orca terminal close`).
- Not ours, left running: the v0.1.8 worktree's UAT `yarn dev` tab; the reviewer session's git-poll loop.
- The operator is starting UAT on the branch.

## Held (operator: batch with UAT findings)
- Discovery hard-codes US (review P1).
- Empty `source_url` rejected in discovery merge (review P1).
- 'Berlin, DE' → US (the DE-as-Delaware policy beats a known non-US city; a false keep).

## Next up (release path, operator order)
1. Operator UAT → fix UAT issues (+ the held items above).
2. "Prep for interview" button on the posting card (UI follow-up to `gigai scout prep`).
3. Confirm with the operator: acquire watchlist-only by default (Exa optional/off) now that discovery fills the watchlist.
4. User docs + README pass (AFTER the UAT fixes, not before).
5. CHANGELOG `### 0.1.9` (it becomes the GitHub Release notes; write it for users).
6. Set pyproject version `0.1.9` (from 0.1.9.dev0) + `CATALOG_REVISION = "v0.1.9"` (src/gigai/catalog.py:20; stays v0.1.8.1 until then) + `uv lock`.
7. One full `make test` by the coordinator → operator squash-merges #37 → `git tag -a v0.1.9 -m "GigAI v0.1.9" && git push origin v0.1.9`.

## Follow-up debt (recorded, not scheduled)
- A shared `scout/websearch.py` to replace the two OpenAI clients (discovery/openai_source.py + interview_prep/websearch.py).
- `scout-watchlist:2` with native evidence fields (replaces the discovery evidence sidecar).
- Unify posting identities (application_events opportunity_ref vs find-jobs normalized_url) so interview_scheduled can trigger prep automatically.
- Core→scout import added by design this version: cli.py registers scout_group (S16 debt).
- Post-release CI: macOS 3.12 cancelled on the v0.1.8/0.1.8.1 post-release matrix (operator: ignore for now).
- `tools/s11_inventory.py` still lists the research spike test roots.

## Lessons for the next coordinator
- Workers ask ONLY via `orca orchestration ask` (now in skill §6); one still used a local prompt this version.
- Workers repeatedly ran whole test directories despite the test budget: restate it in every spec, and kill a worker that loops on no-op waits after its code is done (verify the change yourself).
- uv only, never pip (memory `gigai_uv_only_no_pip`).
- Discussions aren't decisions: the interview-prep auto-trigger and the resume "market roll-up" are ideas, not scheduled.

## Blocked on operator
- UAT.
- v0.1.8 release notes: the operator will apply them later.
