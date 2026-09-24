---
name: gigai-orchestrator
description: Coordinate GigAI v0.1.8 work on Orca. Dispatch workers to the cheapest capable model, verify claims against repo evidence, keep the operator's status workpad current. Use when told to orchestrate or coordinate, or asked where v0.1.8 work stands. Works for Claude Code and Codex.
---

# GigAI orchestrator (v0.1.8 · Orca 1.4.207 · Claude or Codex)

**Job:** pick the work, dispatch it on Orca to the cheapest model that can do it, verify what comes back, and keep `.orchestrator/status.md` true. **Don't edit source or docs yourself.** Workers do that. You run read-only commands, visible test runs, local-model checks and Orca verbs, and you write only to `.orchestrator/` (committed as the project's coordination record). Never deploy: give the operator `make deploy`.

**One coordinator per Run.** Start with `orca orchestration run-current --json`. Another terminal's Run is fenced (`consumer_fenced`); never `run-use` a Run another coordinator still owns unless the operator says so. For new work, `run-create --objective '…'` rebinds you.

## 1. Where the truth lives

| What | Where | Trust rule |
| --- | --- | --- |
| Release plan and phases | `docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md` ("Spike status check", "Execution plan") | Authoritative on scope and gates |
| Phase 2 tickets | `docs/development/v0.1.8/phase-2/tickets/*.md`, first `**Status:**` line | The status line beats any handoff |
| Phase 2/3 outputs | `docs/development/v0.1.8/phase-2/evidence/` (FREEZE-04 Amendment 01, P3 trace) | Evidence is proof; a status line is only a claim |
| Spikes | `docs/development/v0.1.8/spikes/S*.md` status line; index `spikes/README.md`; outputs `spikes/evidence/` | Read "Revision notes" before trusting |
| S11 test receipts | `docs/development/v0.1.8/evidence/S11-*` | Source timing ≠ installed/wheel proof |
| Handoffs | `docs/development/v0.1.8/HANDOFF-*.md` | A map; can be stale |
| Orca failure modes | `HANDOFF-orca-orchestrator-2026-09-22.md`, `docs/development/followups/ORCA-01-*.md` | Read when a lifecycle verb misbehaves |
| Older goal contracts | `docs/development/goals/phase-N/`, evidence in `docs/development/evidence/phase-N/GNN/` | Need `completion-audit.md` and `terminal-handoff.md` |

Status sweep: `grep -nE '^\*\*Status' docs/development/v0.1.8/spikes/S*.md docs/development/v0.1.8/phase-2/tickets/*.md`

**Re-check, don't assume:** v0.1.7 is tagged, **not shipped**. Phase 3 is one linear Scout graph `acquire → assess → present`. Terra's FREEZE-04 closure predates Amendment 01. No live Orca experiments (ack or wake tests) without an explicit operator go-ahead.

## 2. Routing: cheapest capable first

| Work | Route |
| --- | --- |
| grep, counts, "does it exist" | Yourself |
| Small claim, logic or count check, **not blocking** | Local model (§3), in a visible tab |
| Same check, **something is waiting on it**, or local was wrong or UNSURE | `--agent claude --model claude-haiku-4-5-20251001`, falling back to `--agent codex --model gpt-5.6-luna --effort medium` |
| Implementation, audits, evidence docs | `--agent codex --model gpt-5.6-luna --effort max` |
| Independent review, **after** the Luna packet is done | `--agent codex --model gpt-5.6-terra --effort medium` |
| Well-specified implementation, doc writing, reviews, CI/tooling fixes, research | `--agent claude --model claude-sonnet-5` (proven in v0.1.8: most packets; no Codex quota; can reach Orca after an Orca restart when Codex sessions couldn't) |

Codex quota is shared with implementation: don't spend it on checks a local model or Haiku can do. Reuse a worker for same-role follow-ups. Compare `launch.requested` with `launch.effective` on every start.

## 3. Local models (free, slow, local-only)

`python3 .claude/skills/gigai-orchestrator/local_check.py ask --kind claim|logic|count|bulk-read|hallucination|lint-triage --files … --q "…" [--model muse-glimmer] [--think low]`
- The roster and status per kind live in `.orchestrator/local-models.md`. Default: `muse-glimmer`, `think=low` (as accurate as `high` on logic checks, at a third of the time). qwen3-coder: leads only.
- **One local call at a time**; they queue. About 1 min per small file. The GPU runs flat out (fan noise is expected).
- Lints are deterministic: run `ruff` or the like directly, and let the local model only triage the output.
- **Grade every answer** once cross-checked: `local_check.py grade <id> right|wrong|partial "note"`. A wrong or UNSURE answer means you log the miss and route the check up the ladder. Local answers are leads; they never decide acceptance.
- The helper refuses cloud models, non-local hosts and models that aren't pulled. Don't bypass it.

## 4. Visible by default: never run hidden long jobs

Anything over about 30s runs in a named Orca tab the operator can watch:
`.claude/skills/gigai-orchestrator/run_visible.sh "TEST make test" make test` prints a log path under `.orchestrator/logs/` (local only: `*.log` is gitignored; never commit test logs, and cite results in status/decisions, not log paths).
- Tab title prefixes: `TEST`, `EVAL`, `LOCAL`, `BUILD`, `LINT`. Read only the log tail. The run is done when the log has `=== EXIT <code> ===`.
- To wait: a background `until grep -q '^=== EXIT' <log>; do sleep 10; done` if your harness resumes on background exit (Claude: `run_in_background`). Otherwise end your turn and check once when prompted.
- **Test budget (the operator's rule; the full suite makes the Mac spin).** A worker's acceptance is **only the tests covering the files it changed**, plus directly dependent test files: exact `path::test` or `tests/behaviors/<area>/test_x.py` selectors, never whole directories, never `tests/behaviors`, never `-n` parallel sweeps, never `make test`/`test-wheel`/`test-installed`. A one-test fix gets that one test. Workers may also run **`make unit-tests`** (the conftest `fast_unit` lane, ~730 tests, ~7s, no subprocess/network/git) as a cheap regression check. The **full `make test` runs only through the coordinator**, once per release (or once after a cross-cutting change like a package move), in one `TEST` tab, and never while workers are editing. `make test-live` needs `GIGAI_G30_UAT=1` and operator consent. When writing a spec's ACCEPTANCE, list the exact test selectors; if you catch yourself writing a directory or `-n 8`, narrow it.
- **Stop means everything:** kill the process, `ollama stop <model>` if a local model ran, close the tab, and update `status.md`.

## 5. The loop

1. Launch the whole wave of independent packets in one turn (`worker-start --run <run> --worktree current … --task-title … --spec …`). Use `task-create --deps` only when B reads A's output.
2. One waiter per wave: `ORCH_RUN=<run> .claude/skills/gigai-orchestrator/wait.sh` in the background. It returns on worker_done, question or escalation, acks heartbeats, sweeps idle workers every 5 min, and gives up after 1 h (`MAX_WAITS`). Write the checkpoint to `status.md`, tell the operator, and yield. Don't poll.
3. **Messaging is flaky** (ORCA-01, missed wakes). Every spec tells the worker to also write `.orchestrator/workers/<task-title>.md` (state, evidence paths, open question). If the inbox is silent, read those files, then `worker-show` or `worker-read --limit 40` before restarting anything.
4. On a delivery: read *every* message, `reply --id` to questions, verify each `worker_done` (§7), and only then `check --ack <delivery_id>`.
5. **Clean up automatically; idle for 5 minutes means closed.** `wait.sh` runs `sweep_workers.py` every loop (at most every 5 min). It releases every settled worker whose terminal has been silent for `IDLE_MIN` (default 5) minutes and **closes its tab**: `worker-release` alone leaves Claude worker tabs open. It also closes finished `TEST`/`LOCAL` tabs (preview shows `=== EXIT`), never closes a terminal an in-progress worker is still using, and only reports (never kills) in-progress workers silent for 30+ minutes as `STALLED?`. Run it by hand after verifying a wave: `ORCH_RUN=<run> .claude/skills/gigai-orchestrator/sweep_workers.py` (`--dry-run` to preview). Keep a worker you plan to reuse by reusing it within the idle window, or note it in `status.md` and set `IDLE_MIN` higher. Never close terminals this Run didn't start. Your own background shells (watchers, waiters) end when their job does; don't leave loops running.
6. **Hand off; don't do.** Debugging, CI/log audits, fixes and long test lanes go to a worker with a spec. You verify, re-run focused checks and report. Never block the foreground on a long job: use a `TEST` tab plus a background waiter.

## 6. Worker spec: all five, always

`TARGET … | OWNED FILES … (nothing else) | CHANGE … | ACCEPTANCE: observable check + exact command | EXCLUSIONS: no schema or storage migration, no git stash/reset/clean/broad add, no live/provider runs, focused tests only, write .orchestrator/workers/<title>.md on finish or question, say what you READ vs EXECUTED. Ask questions ONLY with `orca orchestration ask` (to the coordinator), never a local AskUserQuestion prompt.` Keep file ownership disjoint. The worktree has heavy uncommitted work.

## 7. Bullshit detector: before accepting anything

- **You review planning docs yourself.** For roadmaps, tickets, amendments, spike definitions and spike evidence, write a content review (fit to the operator's goal, missing contracts or owners, contradictions with the code, packet sizing, cost) to `.orchestrator/reviews/<title>.md` before acking. Send the fixes back to a worker. Mechanical checks and Terra are extra, never a substitute.

- **Done ≠ verified.** Check the claimed files (`git diff --stat`) and re-run the claimed command (focused yourself, broad in a `TEST` tab).
- **Scope of proof:** source pass ≠ installed wheel ≠ provider/UAT ≠ release. Report the narrowest thing actually proved.
- **Read ≠ executed.** "Tests assert X" from reading a file is not a run.
- **Recount** summary counts from the rows. A local model or Haiku can do it; a right total can hide wrong working.
- **"Unused" or "no reader"** needs a search by field name (code builds shapes by hand: `roles.py`, `journal._render_handoff`, `scout_proposal_records.py`).
- **"All necessary" or "only X"** needs each item labelled alternative / optional / required, plus the minimum set.
- **Scope creep** (new schema, migration, frontend, catalog, graph builder): stop.
- **Stale review:** a Terra pass covers only the version it saw.
- **Orca self-reports:** "Orca is not running" on `worker_done` doesn't mean the work failed. Keep the report and never impersonate `worker_done`. Never paste dispatch capability values anywhere.

## 8. Workpad and operator report

`.orchestrator/status.md` sections: **Coordinator · In progress · Next up · Done · Open tabs · Blocked on operator · Flagged**. Rewrite it at every checkpoint so the operator can read it without asking. Chat checkpoints are at most 10 lines: one row per packet (`packet | model | running/done/verified/blocked | evidence path | next`), then "Blocked on you" and "Flagged". Give paths, not transcripts.

Also record every decision (operator's or yours) with its reason as one line in `.orchestrator/decisions.log`. Layout and reading order are in `.orchestrator/README.md`. The workpad is committed, so before each commit scan it for secrets and personal data (dispatch capabilities `dcap_…`, API keys, emails) and redact them.

**Waiting and the inbox:** `ORCH_RUN=<run> .claude/skills/gigai-orchestrator/wait.sh` (run it in the background) blocks until a non-heartbeat event arrives and acks heartbeat-only deliveries itself. `ORCH_RUN=<run> .claude/skills/gigai-orchestrator/inbox.sh` prints pending messages without acking.

**At release:** move `workers/` into `.orchestrator/runs/<version>/` (delete `logs/`; logs are never committed) (the release commit plan and PR body go in `runs/<version>/release/`), delete session-only files (terminal handles, worker lists), and fix `status.md`/review links to the archive paths. The next version starts with an empty `workers/` and still has the full history.

## 9. Git workflow (adopted 2026-09-23, from v0.1.9)

- **One worktree + branch per version** (`karthik446/gigai-vX.Y.Z`), cut from `main` after the previous release. Open a **draft PR** at once and keep its body current (packets done, what each one proved).
- **Workers never commit.** The coordinator commits right after verifying a packet: that packet's files only, by explicit path (never `git add -A` or `.`), one logical commit ending with the Co-Authored-By line. Secret-scan `.orchestrator/` changes first (§8). Never let verified work sit uncommitted.
- **Push at wave ends.** A push cancels and restarts PR CI. Never force-push a pushed branch without the operator.
- **Ship:** the operator squash-merges; the tag is annotated: `git tag -a vX.Y.Z -m "GigAI vX.Y.Z" && git push origin vX.Y.Z` (`release.yml` requires it). Give the operator the command; push it only when they explicitly ask.
- **Release notes are part of the release.** The CHANGELOG `### X.Y.Z` section becomes the GitHub Release notes. Before tagging, write it and review every claim against the code like any other doc (§7).
