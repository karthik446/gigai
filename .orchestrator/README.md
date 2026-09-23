# .orchestrator: coordination record

The GigAI coordinator's workpad (see `.claude/skills/gigai-orchestrator/SKILL.md`). It's committed so the next worktree (e.g. v0.1.9) picks up where this one left off.

## Read first

1. `status.md`: where things stand, what's blocked on the operator, and what's flagged.
2. `decisions.log`: every coordinator and operator decision with its reason, newest last.

## Durable judgment

- `reviews/`: the coordinator's and Terra's reviews of plans, contracts and code, with triage.
- `research/`: investigations (e.g. `oh-my-pi.md`).
- `local-models.md`, `local-models.jsonl`: the graded local-model scoreboard.

## Archive (evidence, read on demand)

- `runs/<version>/workers/`: each worker's report (state, root causes, READ vs EXECUTED); `rel/` and `w1b/` hold the specs they were given.
- `runs/<version>/logs/`: visible TEST/LOCAL run logs, each ending in `=== EXIT <code> ===`.
- `runs/<version>/release/`: commit plan and PR body.

## Live run

`workers/` and `logs/` at the top level belong to the run in progress. At release, move them into `runs/<version>/`.
