# Local model roster

Raw rows: `local-models.jsonl` · summary: `python3 .claude/skills/gigai-orchestrator/local_check.py stats`

**Status rules (per model × kind):** `trial` until graded. `trusted` after 10 graded rows, 0 wrong, ≤1 partial; then spot-check only. `rejected` after 2 wrong or 3 partial; send that kind elsewhere. Operator can override.

| Model | Kind | Status | Evidence (2026-09-22) |
| --- | --- | --- | --- |
| muse-glimmer (default) | claim | trial | 1/1 right, 58s at low; only model to catch installed tests via `test-wheel` |
| muse-glimmer | logic | trial | 2/2 right: low 61s = high 172s on the same answer → use low |
| qwen3.8 | claim | trial | 1/1 right, 63s at low |
| qwen3-coder | claim, logic, count | **rejected for final answers** | 3/3 partial: right conclusions with wrong reasoning, self-contradiction. Fast (4–19s); leads only |
| all | bulk-read, hallucination | untested | T4 (all of `run.py`, 7 conditions, call at :3279) and T5 (no schema; hand-checked) never finished |

**Known answer fixtures** for re-evaluating a new model: T1 Makefile claim, T2 3-bug file, T3 miscounted table, T4 `run.py` `_validate_scheduler_policy` (7 raises, called at 3279), T5 "which schema" (none). Fixtures are recreated from this description when needed.
