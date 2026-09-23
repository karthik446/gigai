# Coordinator review: W0d reconcile (Sonnet 5), 2026-09-23 00:20

Verdict: **good pass; 1 logic error, 2 claims that don't match the doc, 6 small fixes.** Everything from R1–R5/P1–P7 landed. The receipt/precedence restoration is better than Rev 0: it's grounded in `_ZERO_USAGE`, `_terminal_status` and `_run_rows`. D6 is fixed correctly. The watchlist storage is idempotent by token. The 0.2.0 ledger is useful.

## Must fix
F1 **I-3 "bind real callables" is tagged [after-M1], but M1 is impossible without it.** Without I-3 the graph runs fixture stubs, not a real search. Tag it [M1]; M1 happens in wave 4 after I-3.
F2 **The claim doesn't match the table: A-5's route.** The change log says "routed A-5 to Luna medium (P6)", but the DAG row still says `claude-haiku-4-5`. Fix the row.
F3 **The claim doesn't match the table: the ownership audit.** "Each listed path occurs once", but `scout_materialization.py` is in both I-1 and I-3. That's fine as the same sequential owner, but say so accurately. The fixtures dir `tests/behaviors/scout_find_jobs/fixtures/*.json` is claimed by I-0 in prose but missing from I-0's owned-files cell; add it.
F4 **Amendment lines ~194-197** say "the rest of A owns … plus scout_materialization.py's sibling: binding the real callables", which reads as A owning binding. Binding is Integration (I-3). Reword.
F5 **Waves table, wave 0** still says "Coordinator publishes fixtures". Fixtures are I-0 (wave 1a). Remove wave 0 or make it "coordinator verifies Rev 2 ownership only".
F6 **The receipt cut is vague** ("e.g. full producer/usage detail"). Make it concrete: for M1, `producer.model_target` + `adapter` (the UI shows which model ran), status, timestamps and failure are needed; `usage.measured` and the full usage block are after-M1 unless continuation reads them. Tag it the same way in I-2's row.
F7 **No owner for board-token extraction.** Parsing Exa/ATS URLs into `(provider, board_token)` for all four domains. Assign it to A-3 (it owns the ATS URL formats) and add it to A-3's contract cell.
F8 **Citation mismatch:** "Keep: enforced checks … scheduler effect/status check (`run.py:3491-3497`)" points at `_terminal_status` (the buggy aggregate). The effect check is `run.py:3438-3460`. Fix the cite in both places it appears.

## Noted for dispatch (coordinator, no doc change)
- M1 needs `EXA_API_KEY` from the operator, a real `find-jobs.json` and at least one committed resume (`gigai … --kind resume`). Put these in "Blocked on operator" before wave 4.
- There are 4 Luna-max items (I-0..I-3) and 5 Luna-medium items against the Codex quota (24% weekly at last check). At dispatch, consider Sonnet for I-1/I-3 (graph authoring and binding in one file).
- Sonnet 5 via Orca: first trial went well. The two claims that didn't match the doc (F2, F3) are the kind of thing to keep checking.
