# SCOUT-01-RS1 recovery correction re-review

**Date:** 2026-09-08  
**Scope:** Independent read-only re-review of the SCOUT-01-RS1 provider-Run
recovery correction in `src/gigai/run.py` and
`tests/test_g43_provider_run_status.py`. This report is limited to the new
lease/status-recovery behavior. It is neither a live-provider result nor G43.1
closeout or release acceptance.

## Verdict

**Not accepted yet: one remaining High recovery/terminal-publication blocker.**
The correction does resolve the originally demonstrated abandonment window in
which the caller dies while `run-details.json` is still the journaled
`running` record: the status reader obtains the same stable POSIX lease only
after its holder has died, re-authenticates the three Run records against
`HEAD`, and writes one `run_interrupted` transition without importing provider
result or invocation evidence or changing offline Goal state.

## Remaining blocker

### SCOUT-01-RS2 — a crash while the terminal transition is publishing can expose an uncommitted terminal status

`_finish_provider_review` changes `run-details.json` to `succeeded`, `blocked`,
`failed`, or `interrupted` before it calls `record_transition`
([`run.py:1219-1242`](../../../../../src/gigai/run.py)). The journal writes its
transaction manifest and replaces artifacts before it creates and commits the
handoff ([`journal.py:293-319`](../../../../../src/gigai/journal.py)). If the
provider caller is forcibly terminated in that interval, the lease is released
but the working file can already contain `succeeded` while its terminal
handoff/commit is absent. `read_run_details` only attempts provider recovery
for `preparing` or `running` ([`run.py:399-401`](../../../../../src/gigai/run.py)),
so it returns that syntactically valid, uncommitted terminal payload rather
than reconciling the journal transaction or durably recording the required
abandoned-provider interruption.

This is not an authority bypass: the recovery code has good symlink checks,
canonical Run IDs, a regular-file lease check, and byte equality against
`HEAD` before it writes the interruption. It is nevertheless a liveness and
truthfulness failure for the stated boundary "through terminal publication":
a death after artifact replacement is precisely an abandoned caller, but the
new status path neither proves a committed terminal result nor invokes the
existing explicit journal reconciliation. Add an injected crash at each
terminal `record_transition` boundary (at least after artifact replacement and
after handoff replacement/before commit), then require status to return only a
committed terminal record or a typed reconciliation-required state. Do not
silently report the replaced, uncommitted `run-details.json` as success.

## Positive findings

- The lease is acquired before `run_started` and stays open until the provider
  terminalizer returns or unwinds. It is on a stable canonical filename below
  workpad-private `.git`, uses `O_NOFOLLOW`, rejects symlinked components and
  non-regular files, and is never deleted while held ([`run.py:177-182`](../../../../../src/gigai/run.py),
  [`run.py:462-489`](../../../../../src/gigai/run.py)). A reader that encounters
  the live holder leaves `running` to that caller; after process death it can
  acquire the same inode and recover.
- Recovery rechecks state under the acquired lease, is idempotent for ordinary
  sequential polls, authenticates `run-details.json`, `run-manifest.json`, and
  `operator-consent.json` against `HEAD`, and publishes only replacement Run
  details plus a recovery terminal handoff. It does not enumerate, attach, or
  endorse partial provider result/invocation bytes ([`run.py:498-556`](../../../../../src/gigai/run.py)).
- The recovery transition leaves the sealed offline Goals untouched; that is
  the correct separation from the deterministic-worker interruption path,
  which marks Goals failed and therefore must not be reused here.

## Executed checks

| Command | Result | Evidence boundary |
| --- | --- | --- |
| `rtk .venv/bin/pytest -q tests/test_g43_provider_run_status.py` | `11 passed in 31.27s` | Offline temporary-workpad tests; fake adapter at the model port; no provider call. |
| `rtk ruff check src/gigai/run.py tests/test_g43_provider_run_status.py` | Passed (no findings) | Static lint only. |
| `rtk git diff --check -- src/gigai/run.py tests/test_g43_provider_run_status.py` | Passed | Whitespace check only. |

The focused tests do cover a real forked process that calls `os._exit`, active
status polling while the lease is held, terminal-evidence authentication
failure, and missing terminal `result.json`. They do **not** crash the journal
writer after terminal artifact replacement, exercise explicit reconciliation
from status, prove real-provider behavior, or establish live G43.1 completion.
