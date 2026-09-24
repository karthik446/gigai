# .orchestrator: coordination record

The GigAI coordinator's workpad (see `.claude/skills/gigai-orchestrator/SKILL.md`). It's committed so the next worktree (e.g. v0.1.9) picks up where this one left off.

## Read first

1. `status.md`: where things stand, what's blocked on the operator, and what's flagged.
2. `decisions.log`: every coordinator and operator decision with its reason, newest last.

## Handoffs

- `handoffs/`: handoff notes between coordinators, versions or sessions (e.g. `0.1.8-09-23-26-release-handoff.md`). **Starting a new version or session? Read the latest handoff first.**

## Durable judgment

- `reviews/`: the coordinator's and Terra's reviews of plans, contracts and code, with triage.
- `research/`: investigations (e.g. `oh-my-pi.md`).
- `local-models.md`, `local-models.jsonl`: the graded local-model scoreboard.

## Archive (evidence, read on demand)

- `runs/<version>/workers/`: each worker's report (state, root causes, READ vs EXECUTED); `rel/` and `w1b/` hold the specs they were given.
- `runs/<version>/release/`: commit plan and PR body.

## Live run

`workers/` at the top level belongs to the run in progress; at release, move it into `runs/<version>/`. `logs/` holds local TEST/LOCAL run logs (each ends in `=== EXIT <code> ===`); `*.log` is gitignored except `decisions.log`, so logs are never committed.
