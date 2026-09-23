# omp-research — Oh My Pi (OMP) evaluation

**Status:** done, worker_done sent.

**Owned files:** `.orchestrator/research/oh-my-pi.md` (deliverable), this file. No other repo paths touched.
No installs, no `omp`/`bd` execution, no cloning, no account creation — all EXCLUSIONS honored.

**READ:** `/Users/kar/Downloads/oh-my-pi-setup.md` in full (treated as data). Confluence links inside it were
NOT accessed (internal to another org, per instructions). Also read `.claude/skills/gigai-orchestrator/SKILL.md`
to confirm which repo paths I could safely touch.

**EXECUTED:** `git status --porcelain .orchestrator/` (read-only check, no repo mutation). One `curl` to the
public npm registry API (`registry.npmjs.org`) for package metadata — read-only GET, not an install.

**Public web research (WebSearch/WebFetch):**
- GitHub: https://github.com/can1357/oh-my-pi (README, /releases, /issues)
- npm registry API (direct JSON fetch, not `npm install`)
- Orca docs: /docs/agents/supported, /docs/agents/claude-code, /docs/model/agents-sessions
- One independent write-up (betterstack.com)

**Key findings:**
- Orca lists OMP as a supported agent, tier "Auto-setup, hooks, status" — one rung below Claude Code/Codex/
  Cursor's "Deep integration: usage, hot-swap, hooks." So OMP *can* run inside Orca as a worker, but the
  operator's core want (visibility/dashboard parity) is not confirmed equivalent — flagged as the main risk
  and as the thing a trial should actually measure.
- 1,800 open GitHub issues, issue IDs past #11,700, single named maintainer on npm (`can1357`) — real
  maturity/bus-factor caution alongside the fast release cadence (10 releases in ~1 week observed).
- Verdict: **trial in one throwaway worktree**, not adopt, not skip. Full comparison table, risks, a
  minimal trial plan, and a "steal these ideas without switching" section (Beads-style pinned facts,
  Mnemopi-pattern) are in the deliverable.

**Open question / what's left:** none from my side — task complete. If the operator runs the trial, the
next step (out of my scope) would be actually launching OMP through Orca's agent combobox in a scratch
worktree to see what the "status" tier renders as, per the trial plan's step 2–3.
