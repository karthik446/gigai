# SCOUT-03 — Full-suite verification recovery

**Status:** Full offline suite passed on the first partial implementation;
SCOUT-03 remains unaccepted because of functional/review gaps.  
**Command started:** 2026-09-08 22:15 UTC; result collected after the operator's
usage reset (2026-09-09 UTC).  
**Owner:** Astra coordinator, after Terra's implementation handoff.

## Completed verification

Command in the existing worktree:

```text
env GIGAI_G30_UAT=0 .venv/bin/python -m pytest -q
```

Executed through the shell proxy in a persistent PTY, session `22542`.
The session yielded normally and its output was collected with `write_stdin`.
It was not treated as failed or killed merely because an initial tool call
returned before the process completed.

Final result: **769 passed, 1 skipped, 104 subtests passed, 7 warnings in
464.06 seconds; exit code 0.** The skip is the intentional live-model CLI test
(`GIGAI_G30_UAT=1` is required to enable it). Warnings concern forking from a
multithreaded process in existing G43/JSL concurrency tests.

No source edits were made by the coordinator during this run. Claude's source
review and Luna's disposable verification ran separately; their findings remain
required corrections even though the existing full suite passes. Only two new
SCOUT-03 tests existed in this handoff, so this is regression evidence, not
complete A03 acceptance or private human UAT.

## Earlier attempts and correction to the worker report

The implementation report described a fixed 30-second cutoff and inferred
termination. That inference was not supported: root found the original
`pytest` PID 30461, parent RTK PID 30442, still alive at 22:11–22:14 UTC.
Root immediately interrupted only its newly started duplicate PTY session
`96883`; that interrupted run has no acceptance result. The original process
was left untouched and later exited.

Historical Terra terminal output also exposed an earlier aggregate run with
15 failures, 754 passes and 104 subtests, followed by corrective focused runs.
Its position in that evolving implementation means it is not the final-source
verification result above. Root did not infer the later pending run's result
from this earlier terminal output.

A narrowly scoped recovery dispatch `ctx_788ec92713c2` for
`task_f80a5320701f` failed before injection at a Codex interactive low-quota
reminder. No model setting or original test process was changed. After the
original process exited, root ran the completed verification recorded above.
The failed recovery attempt remains failed; it was not retroactively relabeled
as a successful worker run.

The operator subsequently reported a usage reset. Luna's existing verification
session was resumed to finish its own probes and report. The implementation
and review provenance remain separate from this coordinator-run suite.
