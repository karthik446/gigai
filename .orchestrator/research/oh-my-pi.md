# Oh My Pi (OMP) — is it useful for this operator?

Researched 2026-09-23. Setup doc READ in full (not executed — no installs, no `omp`/`bd` runs).
Web research via public sources only (GitHub, npm registry, Orca docs, one independent write-up).

## TL;DR

1. **Verdict: trial in one throwaway worktree — do not adopt, do not skip.** OMP is a real, fast-moving,
   single-maintainer-adjacent fork of Pi with genuinely different tech (LSP/DAP-backed edits, built-in
   subagents+worktrees, model-agnostic routing) that Claude Code doesn't have natively [public].
2. Orca does **not** require dropping Claude Code/Codex to try this: Orca's own docs list OMP as a supported
   agent with "Auto-setup, hooks, status" — one tier below Claude Code/Codex/Cursor's "Deep integration:
   usage, hot-swap, hooks," but still enough for Orca to show status in the sidebar [public].
3. The operator's core requirement — **visibility** into what an agent is doing across worktrees — is only
   *partially* answered: OMP's own Agent Hub gives visibility inside one OMP session, but Orca's richest
   dashboard treatment (state dots via OSC hooks, account hot-swap) is confirmed only for Claude Code/Codex/
   Cursor, not OMP [public, inferred from tier language — UNVERIFIED how "status" renders for OMP specifically].
4. **1,800 open GitHub issues** and issue numbers already past #11,700 are a real maturity/quality caution flag
   worth weighing against the "colleagues say it's great" signal [public] — this is a very actively developed,
   also very-actively-breaking project, per the setup doc's own defensive tone (merge-don't-append YAML,
   "never use a force flag," "never run `bd edit`," stealth-init warnings) [setup doc].
5. **Steal the ideas before the tool**: Beads-style pinned per-repo facts and Mnemopi-style auto-recall memory
   are both independently portable to the current Claude Code + gigai-orchestrator setup without adopting OMP
   at all — see "Worth stealing" below.

## Comparison: OMP vs Orca + Claude Code (current setup)

| Dimension | OMP (standalone) | Orca + Claude Code (current) | Source |
|---|---|---|---|
| **Visibility (see what agent is doing)** | Own TUI + Agent Hub (`Alt+A`) shows subagent activity/cost inside one OMP process [public] | Orca sidebar with state dots (spinner/question/check/fail/idle) across worktrees, driven by a status-line hook emitting OSC title events — this is the thing the operator explicitly values [public] | Orca docs: agents/claude-code, model/agents-sessions |
| **Worktrees** | Built-in: subagents fan out into isolated worktrees via APFS clones/btrfs/zfs reflinks/overlayfs/projfs/rcopy — no external orchestrator needed [public] | Orca creates/manages worktrees explicitly as its core unit; Claude Code itself has no native worktree concept — Orca supplies it [public + repo: AGENTS.md, gigai-orchestrator skill] | GitHub README (via fetch), Orca docs |
| **Dashboard/sidebar** | Own TUI ("card-based rendering," differential rendering) + `@oh-my-pi/omp-stats` for local usage observability; `/collab` for read-only session sharing over a relay | Orca's Agent Dashboard: kanban columns (Needs You/Working/Done/Idle), per-worktree cards, cross-agent (Claude+Codex+…) in one view | Orca docs: model/agents-sessions |
| **Multi-agent/subagent orchestration** | First-class `task` tool: typed result schemas parent↔child, in-process | Orca is the orchestrator *above* the agent layer: coordinates separate Claude Code / Codex processes, each single-agent internally (this repo's gigai-orchestrator skill is the multi-agent layer, built on top, not inside Claude Code) | public (README) + repo: `.claude/skills/gigai-orchestrator/SKILL.md` |
| **Memory** | Mnemopi: auto-recall/auto-retain, per-project + shared banks, embeddings-backed (218MB model) [setup doc]; "Learn" promotes lessons to skills [public] | None built-in to Claude Code; project relies on CLAUDE.md, repo docs, and this session's own file-based `~/.claude/projects/.../memory/` convention (not equivalent — manual, not auto-recalled) | setup doc; this session's own memory system (project convention, not Claude Code feature) |
| **Task tracking** | Beads (`bd`): work-graph DB (dolt-backed) surviving across sessions, `bd ready/claim/close`, pinned `bd remember` facts injected every session [setup doc] | `.orchestrator/status.md` + this repo's own markdown ticket/roadmap files, manually maintained by the coordinator skill; no cross-session automatic injection | setup doc; repo: `.orchestrator/status.md`, `gigai-orchestrator` skill §8 |
| **Token/cost features** | Hashline edits (hash-anchored line refs) claim ~50–61% output-token reduction vs `str_replace` on some models [public, vendor-adjacent claim, UNVERIFIED independently]; per-session cost/duration in status cards; 9-role model routing to route cheap models to cheap work | RTK already in use for bash-output compression; gigai-orchestrator skill has its own cheapest-capable-model routing ladder (local model → Haiku → Luna → Terra → Sonnet) achieving a similar goal at the orchestration layer rather than the harness layer | setup doc; repo: `gigai-orchestrator` SKILL.md §2–3 |
| **Maturity** | Extremely fast release cadence — 10 releases in ~1 week observed (v18.2.1 → v18.2.11, Sep 15–23 2026); 1,800 open GitHub issues, issue IDs past #11,700 (implies either huge volume or long history, or both); single top-line maintainer (`can1357`) with a small company credit (Stencil Labs, Inc.) in copyright [public] | Claude Code: Anthropic-maintained, slower/more conservative release cadence, broad enterprise usage; Orca: separate vendor, already adopted and trusted by operator | public (GitHub releases/issues, npm registry) |
| **Lock-in** | New CLI, new config paths (`~/.omp/agent/`), new extension language (TypeScript "ExtensionAPI"), own memory/task-tracker stack (Mnemopi/Beads) — moderate lock-in if fully adopted, though it explicitly inherits `.claude`/`CLAUDE.md`/`.claude/skills` so migration *away* is not a hard cliff [setup doc, public] | Already the operator's committed stack; this repo's whole coordination layer (`.orchestrator/`, `gigai-orchestrator` skill) is built assuming Claude Code + Codex workers | repo: AGENTS.md, gigai-orchestrator skill |

## Worth stealing even without switching

- **Beads-style pinned facts** (`bd remember` → injected every session): the closest existing analog in this
  setup is `.orchestrator/status.md` plus this session's own `~/.claude/.../memory/` files, but neither is
  *automatically* injected into every new Claude Code session the way `bd remember` is into every OMP session
  in a Beads-initialized repo. A lightweight equivalent: a `.orchestrator/pinned-facts.md` that the
  gigai-orchestrator skill explicitly greps and quotes at the top of every worker spec — this is a doc/skill
  change, not a tool install. [idea derived from setup doc §8.4]
- **Mnemopi-style auto-recall**: not directly portable (Claude Code has no memory-backend hook point), but the
  *pattern* — retain narrative decisions distinct from pinned facts — maps onto formalizing this session's
  existing memory-file convention (already in use per `MEMORY.md`) rather than building anything new.
- **Extension idea → skill idea**: OMP's RTK extension (`tool_call` hook rewriting `bash` commands before
  they reach the model) is conceptually identical to what RTK's Claude Code hook already does per user
  CLAUDE.md ("git status → rtk git status, transparent, 0 tokens overhead") — no new work needed here, this
  is already covered.
- **Hashline-edit idea**: if Claude Code's own edit tool ever becomes a token bottleneck, "hash-anchored line
  references instead of full string match" is a technique worth remembering as a possible future Edit-tool
  optimization — not actionable today, filed as an idea only.

## Risks

- **1,800 open issues / high issue-ID churn** [public, GitHub issues page] — either heavy real-world friction
  or a very young numbering scheme reused across a fork; either way it signals rough edges relative to Claude
  Code's stability bar. UNVERIFIED: exact closed-issue ratio (GitHub didn't surface it in the fetch).
  UNVERIFIED: whether issue numbers are inherited from the upstream `pi-mono` fork or original to `oh-my-pi`.
- **Single-maintainer-adjacent bus factor**: npm registry lists exactly one maintainer (`can1357`,
  [email redacted]) on the current package; copyright also credits "Stencil Labs, Inc." but no team
  roster is public in what was fetched [public, npm registry + README]. UNVERIFIED: actual team size behind
  Stencil Labs.
- **Extension trust model is thin**: the README gives no sandboxing/permission model for TypeScript
  extensions beyond "same tool API as built-ins" [public] — the setup doc's own `beads.ts`/`rtk.ts` snippets
  run `pi.exec` with a real shell, which is standard for this class of tool but is a real trust surface if a
  malicious or buggy extension is pulled in.
- **Visibility gap is the crux risk for this operator specifically**: the one thing valued about Orca
  (seeing what each agent is doing, opening/checking worktrees, the dashboard) is *not* confirmed to work
  identically for OMP as it does for Claude Code/Codex — Orca's docs put OMP one tier down ("Auto-setup,
  hooks, status" vs "Deep integration: usage, hot-swap, hooks"). Running OMP inside Orca might give a
  degraded version of exactly the property the operator cares about most. UNVERIFIED without an actual trial:
  what "status" renders as for OMP in the sidebar (full state dots? partial? none beyond a running/idle dot?).
- **Config/extension merge risk**, called out repeatedly by the setup doc itself: YAML top-level keys don't
  merge, `bd init` on a dirty tree sweeps uncommitted changes into its commit, hand-editing `.beads/dolt` can
  corrupt the workspace [setup doc] — these are real footguns baked into the tool's current UX, not just
  colleague caution.
- **Telemetry/privacy**: no explicit telemetry statement found in the README fetch [public — search may be
  incomplete, UNVERIFIED]. Setup doc explicitly states "Memory data is stored locally, but fact extraction
  still sends content to your model provider" [setup doc] — same category of exposure as any LLM tool, not
  novel, but worth carrying into any trial's data-handling assumptions.

## If trial: minimal no-commitment plan

Scope: prove or disprove the visibility question above, cheaply, without touching this repo's real work.

1. **One throwaway worktree**, created via Orca as usual, on a disposable branch with no real gigai work in
   it (e.g. a scratch repo or a tiny subdirectory copy) — never this repo's active `.orchestrator/`-tracked
   lanes.
2. Launch OMP through Orca's agent combobox (not by typing the binary) so Orca's "auto-setup, hooks, status"
   path is actually exercised — confirms or refutes whether sidebar state dots appear at all.
3. **Measure, don't vibe-check**: (a) does the worktree show up in Orca's dashboard the same way a Claude
   Code worktree does — same kanban column, same click-to-open? (b) does the state dot update on
   working/idle/needs-you transitions? (c) time-to-first-useful-answer on the exact "read this repo, explain
   entry point, don't edit" smoke test from the setup doc §6, compared cold against a fresh Claude Code
   session in an equivalent worktree.
4. Do **not** install Beads or Mnemopi in this pass — that's a second, separable trial only if step 3 justifies
   continuing, and only in a repo that isn't `gigai`'s real work.
5. Kill criteria: if Orca's dashboard treats the OMP worktree as a "plain shell, not a recognized agent" (per
   Orca's own docs' explicit warning about unrecognized agents), stop — the core visibility requirement isn't
   met and no further OMP investment is worth it for this operator.
6. Report back: one paragraph, dashboard behavior observed vs Claude Code, nothing more — this is a
   visibility spike, not a feature bake-off.

## Sources

All claims above are labeled inline as `[public]` (verified against a public web source below), `[setup doc]`
(from the colleague's consolidated guide, `/Users/kar/Downloads/oh-my-pi-setup.md`, READ not executed), or
`[UNVERIFIED]` (could not confirm from either).

- GitHub repo: https://github.com/can1357/oh-my-pi (README, releases, issues pages — fetched)
- npm registry API: `https://registry.npmjs.org/@oh-my-pi/pi-coding-agent` (queried directly — maintainer,
  license, latest version)
- Orca docs — supported agents: https://www.onorca.dev/docs/agents/supported (tier language: "Deep
  integration: usage, hot-swap, hooks" vs "Auto-setup, hooks, status")
- Orca docs — Claude Code integration: https://www.onorca.dev/docs/agents/claude-code (status-line hook / OSC
  title events mechanism)
- Orca docs — agent sessions/dashboard: https://www.onorca.dev/docs/model/agents-sessions (state dots,
  kanban dashboard, "agent CLI... isn't one Orca recognizes" warning)
- Independent write-up: https://betterstack.com/community/guides/ai/oh-my-pi-ai-coding-agent/ (LSP/DAP/
  hashline feature description; no comparison to Claude Code/Codex was offered by this source, and no
  maturity assessment — noted as a gap, not filled in)
- Setup doc (treated as data): `/Users/kar/Downloads/oh-my-pi-setup.md`, consolidated from a colleague's
  internal Confluence (links in that doc are internal to another org and were **not** accessed, per
  instructions)

Not independently verified in this pass (flagged above where relevant): exact OMP GitHub star/fork count
beyond what one aggregated search summary reported (33.0k stars / 3.5k forks) — this figure came from a
search-engine summary, not a direct fetch of the repo's rendered star count, so it is marked UNVERIFIED
despite being labeled "public" in origin; treat it as directionally plausible, not confirmed.

## Coordinator verification (2026-09-23)

- **Orca tier: VERIFIED** at https://www.onorca.dev/docs/agents/supported. OMP is "Auto-setup, hooks, status"; Claude Code is "Deep integration: usage, hot-swap, hooks"; Codex is "Deep integration: usage, hot-swap".
- **GitHub stats: VERIFIED** via the GitHub API (`gh api repos/can1357/oh-my-pi`): **~33k stars**, MIT, created 2025-12-31, pushed today; **1,845 open issues** (3,075 open issues + PRs). Top contributors: can1357 (~12.3k commits), roboomp (a bot, ~3.8k), badlogic (~1.3k, the upstream Pi author). The report left out the star count: the issue volume comes with very wide adoption, not just churn. The bus-factor concern stands: one human does nearly all the commits.
