# Worker: p0-assess-resume

**Task:** PR #37 review fixes P0-3 (assess progress writer target vs
workpad) and P0-4 (resume-add operation key conflicts on same-name edits),
both PLAUSIBLE. Dispatched under Orca task `task_1b1b38236387`. Source:
`.orchestrator/reviews/pr37-review-findings.md`.

**Status:** Done. Both owned files changed; reproduction tests fail before
the fix and pass after. `git status` also shows another worker's in-flight
edits in the same worktree (`filters.py`, `test_filters.py` — P0-1's
country-words fix) — not mine, left untouched.

READ vs EXECUTED: READ `proposal_execution.py` (`_assess_progress_writer`,
`assess_node`), `find_jobs/bindings.py` (`_assess_bound`, the
`register_find_jobs_nodes` registration binding `target=root`),
`find_jobs/present_api.py` (`_target_root()`, `start_run`, `run_progress`),
`workpad.py` (`resolve_workpad`), `run.py` (`_build_node_context`, where
`context.workpad_path = str(resolved.path)`), `find_jobs/market_acquisition.py`
(`_progress_writer`/`_resolved`, acquire's mirror pattern), `scout_cli.py`
(`resume_add_command`), `private_records.py` (`import_reference`,
`create_record`, `_publish`, `_existing_receipt` — the exact
`private_operation_conflict` raise site), `run.py`'s `resolve_newest_resume`,
and the existing `test_progress.py` / `test_scout_setup_cli.py` fixtures
before writing anything. EXECUTED both new reproduction tests individually
(confirmed failing against pre-fix code with the exact error text quoted
below), then EXECUTED the full owned test files (44 passed), EXECUTED
`test_m1_end_to_end.py` (1 xfailed — a pre-existing, operator-acknowledged
0.1.8.1 regression unrelated to this change), and EXECUTED `make unit-tests`
(951 passed, 1 failed in `test_filters.py` — another worker's in-progress
P0-1 fix, confirmed via `git status` showing `filters.py`/`test_filters.py`
already modified before this dispatch touched anything).

## P0-3: assess's progress writer preferred `target` over the workpad

**Root cause confirmed exactly as described.** Trace of `target`'s real
identity at the graph-worker call site:

- `present_api.py`'s `start_run` binds `target = self._target_root()` (the
  operator's bound repo/target root — `self.target`, set at `Backend`
  construction) and passes it into `register_find_jobs_nodes(home_root=...,
  target=target)`.
- `bindings.py`'s `register_find_jobs_nodes` → internal registration binds
  `assess = partial(_assess_bound, home_root=home, target=root, config=config)`
  where `root` is that same bare target root.
- `_assess_bound` forwards it unchanged into `assess_node(context, input,
  home_root=home_root, target=target, config=config)`.
- Separately, `run.py`'s `_build_node_context` sets
  `context.workpad_path = str(resolved.path)`, where `resolved =
  resolve_workpad(...)` — the real per-Gig workpad root, which is a
  *different* path than the bound target root whenever the active Gig's
  workpad isn't the target root itself (the normal case once more than one
  Gig exists under one target).
- Before this fix, `_assess_progress_writer` did `Path(target) if
  isinstance(target, Path) else Path(workpad_path)...` — i.e. it preferred
  the bare target root over `context.workpad_path` whenever `target` was
  given (which it always is on the real graph-worker path). Acquire's own
  writer (`market_acquisition.py`'s `_progress_writer`/`_resolved`) and
  `present_api.run_progress` (`/progress`) both always resolve and use the
  real workpad root — never the bare target — so assess's progress landed
  under `<target>/runs/<run_id>/progress` (a stray dir in the operator's
  repo) while `/progress` read `<workpad>/runs/<run_id>/progress` and saw
  nothing, leaving the UI's cards stuck "waiting".

**Fix (`proposal_execution.py`, `_assess_progress_writer` only):** the
writer now always resolves from `context.workpad_path`; the `target`
parameter is no longer read for path resolution (kept in the signature only
because `assess_node` still passes it positionally as part of its stable
call shape — not touched, per the owned-files scope).

**Test (repro-first):**
`tests/behaviors/scout_find_jobs/test_progress.py::test_assess_node_writes_progress_under_the_workpad_not_the_target_root`
— builds a `NodeContext` with `workpad_path` pointed at one directory and
calls `assess_node(..., target=<a different directory>, ...)`, mirroring the
real divergence. Verified failing before the fix
(`assert (workpad_root / "runs" / "run_01" / "progress").exists()` →
`AssertionError: assert False`, and a manual check confirmed the progress
dir was written under `target_root` instead) and passing after: progress now
lands under `workpad_root` and nothing is written under `target_root`. Note
the pre-existing
`test_assess_node_wrapper_completes_and_propagates_correctly_when_progress_writes_fail`
test always passed `target=tmp_path` equal to `workpad_path`, so it never
exercised the divergent case and didn't catch this bug — left unchanged, it
still passes.

Files: `src/gigai/scout/proposal_execution.py` (`_assess_progress_writer`
only), `tests/behaviors/scout_find_jobs/test_progress.py`.

## P0-4: resume-add's operation key conflicted on same-name edits

**Root cause confirmed exactly as described, reproduced with the exact
error text from the finding.** `scout_cli.py`'s `resume_add_command` called
`import_reference(..., operation_key=f"scout-resume-add:{file.name}")` —
keyed only by the file's name, overriding `import_reference`'s own
content-digest-derived default key (`f"reference:{kind}:{digest}"`).
`private_records.py`'s `_publish`/`_existing_receipt` treats a resolvable
`operation_key` matching an existing operation receipt as a request to
replay that same operation: if the *new* payload's digest doesn't match the
receipt's stored `payload_sha256`, it raises exactly
`PrivateRecordError("private_operation_conflict", "operation key was
already used with different payload")`. Re-adding an edited resume under
the same file name hit this: same key, different content digest → conflict,
instead of a new resume revision.

**Fix (`scout_cli.py`, `resume_add_command`'s `import_reference` call
only):** the operation key is now `f"scout-resume-add:{file.name}:
{content_digest}"`, where `content_digest = digest_imported_bytes
(file.read_bytes())` — the same digest function `import_reference` uses
internally for its own default key. Same bytes under the same name → same
key → the existing receipt is reused (idempotent, unchanged from before).
Edited bytes under the same name → a new key → a new reference + a new
`g45_reference` record, no conflict.

**The record-wrapper step's key (`create_record`'s
`operation_key=f"scout-resume-record:{imported.item_id}"`, scout_cli.py
~:215/221) was already correct and needed no change:** `imported.item_id` is
the *reference_id* `import_reference` returns, which is itself
content-derived (a fresh reference for new content gets a fresh
`reference_id`; identical content dedupes to the existing one via
`import_reference`'s own `equivalent_artifact_path` check). So the record
key naturally tracks content identity once the reference key does.

**`resolve_newest_resume` (`run.py`) needed no change:** it already sorts
resume imports by `created_at` and resolves the record referencing the
newest one; the bug was solely that an edited-same-name resume never got a
new reference/record to begin with. With the key fix, editing produces a
genuinely new (later) reference, so `resolve_newest_resume` picks it up
correctly.

**Test (repro-first):**
`tests/behaviors/scout_find_jobs/test_scout_setup_cli.py::test_scout_resume_add_creates_a_new_revision_when_the_same_file_name_is_edited`
— adds a resume, edits the same file's bytes under the same name, re-adds
it. Verified failing before the fix with the exact reported error
(`{"error":{"code":"private_operation_conflict","message":"operation key
was already used with different payload"}}`, `exit_code == 1`) and passing
after: `reference_created`/`record_created` are `True` on the second add,
`reference_id`/`record_id` differ from the first, and
`resolve_newest_resume(home, target).record_id` matches the second (newest)
record. The existing idempotency test
(`test_scout_resume_add_is_idempotent_for_the_same_file_bytes`, same bytes
re-added) still passes unchanged, confirming the fix didn't regress the
same-bytes case.

Files: `src/gigai/scout/scout_cli.py` (`resume_add_command`'s
`import_reference` call only), `tests/behaviors/scout_find_jobs/test_scout_setup_cli.py`.

## Verification run summary

- `test_progress.py`: 25 passed (24 pre-existing + 1 new).
- `test_scout_setup_cli.py`: 19 passed (18 pre-existing + 1 new).
- `test_m1_end_to_end.py`: 1 xfailed (pre-existing, operator-acknowledged
  0.1.8.1 regression, unrelated to this change).
- `make unit-tests`: 951 passed, 1 failed — `test_filters.py::
  test_country_match_pr37_p0_us_postings_no_longer_dropped[...]`, confirmed
  via `git status` to be another worker's in-progress P0-1 (country-words)
  fix already present before this dispatch started; `filters.py` is not in
  this dispatch's owned-files list and was not touched.

## Out of scope / left alone

- P0-1 (country filter / `filters.py`), P0-2/P0-5 (UI setup/discover) — other
  workers' dispatches; not touched.
- `make unit-tests`'s one failure (`test_filters.py`) is P0-1's in-progress
  fix, unrelated to the two files this dispatch owns.
