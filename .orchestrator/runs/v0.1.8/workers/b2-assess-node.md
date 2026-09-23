# B-2 assess node

State: implemented; coordinator crash follow-up fixed; focused tests passed.

Files changed:

- `src/gigai/scout_proposal_execution.py`: added `assess_node`, explicit local/hosted `InvocationPolicy` construction, adapter-factory target resolution, exact pinned resume/acquisition readers, bounded per-posting calls, lazy B-1 parser/saver imports, model-unavailable/denied row handling, producer/usage output helpers.
- `tests/behaviors/scout_find_jobs/test_assess_model_policy.py`: focused local/hosted policy and unknown-target tests.
- Follow-up test now exercises a fake adapter plus the real B-1 `parse_assessment_proposal` and `save_assessment_revision`, then reads the persisted revision.
- `src/gigai/scout_tailor_selection.py`: read only; unchanged.

Choices: the sealed contract enum is the routing authority; a string `target` is accepted as the configured model target name when provided, while a filesystem target is used as the workpad root. Hosted targets require factory credential resolution and use `network_allowed=True, offline=False`; local uses `local_allowed=True, network_allowed=False, offline=True`; no fallback is performed. Resume hydration uses the exact record/revision and verifies its digest, while posting text is taken from the exact acquisition batch reference.

READ: `.claude/skills/gigai-orchestrator/SKILL.md`, `scout_find_jobs_contracts.py`, Amendment 02, find-jobs roadmap, `model_execution.py`, adapter factory/port, acquisition/private/proposal record modules, and existing proposal tests/fixtures.

EXECUTED: `python -m py_compile src/gigai/scout_proposal_execution.py tests/behaviors/scout_find_jobs/test_assess_model_policy.py`; `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_assess_model_policy.py tests/behaviors/scout_proposals_tools/test_scout_proposal_execution.py -q` -> `13 passed in 142.25s (0:02:22)`; `uv run --locked python -c "import gigai.scout_proposal_execution as m; from gigai.scout_proposals import parse_assessment_proposal; from gigai.scout_proposal_records import save_assessment_revision; print('ok')"` -> `ok`. `git diff --check` was run; it reported only pre-existing trailing whitespace in unrelated documentation files.

Follow-up: corrected the lazy imports: `parse_assessment_proposal` comes from `scout_proposals`, while `save_assessment_revision` comes from `scout_proposal_records`. Orca was available for this follow-up; the original worker_done remains to be sent after this report update.
