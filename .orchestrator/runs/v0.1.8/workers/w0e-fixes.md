# w0e — Apply review fixes F1-F8 (Revision 2.1)

state: done

## Files changed

- `docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md` (205 lines)
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md` (250 lines)

Both are targeted edits on top of the existing Revision 2 reconciliation, not a
restructure: table cells, one prose paragraph, one wave-0 sentence, and a new
change-log/change-note entry in each doc.

## Per-fix disposition

| Fix | Verdict | What changed |
| --- | --- | --- |
| F1 | Fixed | I-3's DAG row retagged `[after-M1]` → `[M1]` (roadmap DAG table). M1 (wave 4) already runs after wave 3 (where I-3 lands), so no wave reordering was needed — only the tag was wrong. |
| F2 | Fixed | A-5's DAG row route changed `claude-haiku-4-5` → `gpt-5.6-luna medium`, matching the Revision 2 change-log claim that was already correct. |
| F3 | Fixed | Ownership-audit prose reworded: states plainly that `scout_materialization.py` appears in both I-1 and I-3 rows, same sequential owner, not a conflict — rather than asserting "each path occurs once" and leaving the exception unstated. Added `tests/behaviors/scout_find_jobs/fixtures/*.json` (new) to I-0's owned-files cell in the DAG table (it was already in prose, "Fixtures that unblock parallel work" section, but missing from the table cell itself). |
| F4 | Fixed | Amendment's "Packet A may be split..." paragraph reworded: "Binding real A/B/C callables into `scout_materialization.py` is Integration's work (wave 3, roadmap I-3, same owner as I-1), not A's" — removes the "plus scout_materialization.py's sibling" phrasing that read as A owning it. |
| F5 | Fixed | Roadmap wave 0 changed from "Coordinator publishes fixtures and checks Amendment-02 Revision 2 ownership" to "Coordinator verifies Amendment-02 Revision 2 ownership; fixtures are I-0's own deliverable in wave 1a, not a coordinator artifact." |
| F6 | Fixed | "Cut for M1" table's receipt-fields row now names exactly what M1 keeps (`producer.model_target`, `producer.adapter`, goal status, timestamps, failure detail) vs. defers (`usage.measured`, rest of `usage` block, unless continuation reads them), replacing the vague "e.g. full producer/usage detail." I-2's DAG row tagged the same way inline: `producer.model_target`/`producer.adapter` **[M1]**; `usage.measured` and full usage block **[after-M1]** unless continuation reads them. |
| F7 | Fixed | A-3's DAG row retitled "ATS board clients + board-token extraction" with the contract cell stating it owns parsing Exa/ATS URLs into `(provider, board_token)` across all four domains, feeding A-5. Amendment's packet-A split paragraph updated to match. |
| F8 | Fixed | Roadmap's two "scheduler effect check" citations (Cut-for-M1 table and Gates table) corrected from `run.py:3491-3497` (`_terminal_status`, the buggy aggregate-status function) to `run.py:3438-3460` (`_validate_scheduler_policy`, confirmed by reading it: it raises `"Goal declares an unsafe effect set"` when `goal.get("effects") != ["write_workpad"]`). The amendment's own `3491-3497` citation was already correctly scoped to the aggregate-status bug (in its "Node receipt and aggregate status" section and READ list), not asserted as the effect check, so it needed no change — verified by grepping for "scheduler effect"/"effect check" in the amendment, which found no such claim there. |

## Change notes added

- Amendment: new "Revision 2.1" entries appended to the existing Revision-2
  change-note section (merged into one "Revision 2 / 2.1 change note" heading
  to stay under the line cap), one bullet per F1-F8 fix that touched this doc
  (F4, F7, F8).
- Roadmap: new "2026-09-23 (Revision 2.1)" change-log entry covering all eight
  fixes.

## Acceptance command outputs

```
$ grep -n 'I-3' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md | grep -c 'after-M1'
0

$ grep -n '^| A-5' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md | grep -c 'luna medium'
1

$ grep -n '^| I-0' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md | grep -c 'fixtures/'
1

$ grep -c 'Coordinator publishes fixtures' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md
0

$ grep -n '3491-3497' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md
205:  `run.py:3491-3497` to `run.py:3438-3460` in both places it appeared (F8).

$ grep -n '3491-3497' docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
203:`src/gigai/run.py:100-145,...,3438-3460,3491-3497,3564-3584,...`;
247:  `run.py:3491-3497` (`_terminal_status`, aggregate-status site) to
249:  this doc's own `3491-3497` use was already the aggregate-status bug, not the

$ wc -l docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
205 roadmaps/v0.1.8-find-jobs-functional-roadmap.md
250 phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
```

All six acceptance checks pass: the two `3491-3497` hits remaining in each doc
are the F8 change-note explaining the fix itself and the amendment's original
aggregate-status citation (never the effect check), matching the acceptance
criterion "every hit is about aggregate status, not the effect check." Both
docs are under the 260-line cap.

## READ vs EXECUTED

**READ:** `.orchestrator/reviews/w0d-reconcile.md` (this task's review, in
full); both owned docs in full before editing; `src/gigai/run.py:3438-3460`
(`_validate_scheduler_policy`, confirmed as the real effect check — raises on
`goal.get("effects") != ["write_workpad"]`) and `src/gigai/run.py:3491-3497`
(`_terminal_status`, re-confirmed as the aggregate-status function, not an
effect check) to verify F8 before editing citations.

**EXECUTED:** read-only `grep`/`wc -l` for the six acceptance checks above,
plus intermediate `wc -l` calls while trimming both docs back under the
260-line cap after adding the two change-note sections. No git
stash/reset/clean/add/commit; no source/test/schema/UI file touched; no
network, provider, or model calls; no tests executed. The only writes were to
the two owned docs and this handoff file.

## Question for coordinator

None. All eight fixes applied as targeted edits, both docs internally
consistent, all acceptance checks pass, both under the line cap.
