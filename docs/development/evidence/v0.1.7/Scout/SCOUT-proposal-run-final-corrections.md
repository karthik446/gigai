# SCOUT proposal-assessment Run final corrections

Status: bounded implementation evidence, 2026-09-11.  This lane is limited to
the synthetic, injected-local-transport proposal Run path; it is not a live
model, scheduler, UI, Tailor, application, or activation acceptance claim.

## F1/F2: closed operation and Goal shape

`launch_run` now requires the selected Graph Set descriptor to have the exact
`graph_id` `proposal-assessment`; an alias is not an operation authority.  The
request selector and sealed request must use the same canonical value.  The
approved graph is then required to contain exactly one Goal, with exactly one
entry ID and the same single terminal ID, and that Goal must be the required
`proposal-assessment` entry with only the registered `write_workpad` effect,
the `gigai.offline` local capability, and the closed completion evidence shape.
Extra, non-entry, or pending Goals therefore refuse before Run allocation or
transport.  The historical Graph Set alias resolver remains unchanged for
other operations, and Tailor is not substituted for proposal assessment.

The alias-only regression passes `assessment` through the public launch path
and observes `graph_selection_invalid` with no transport, no Run directory,
and unchanged journal `HEAD`.

## F3: result publication and owning Run terminal state

Proposal domain result publication still emits one Goal result transition; the
owning Run is finished only after host validation of that result.  If target
observation or journal publication races after the Goal result, the narrow
proposal recovery path re-reads committed run details under the writer lock,
preserves the completed Goal/result, and writes one `run_interrupted` handoff
when the Run is not already terminal.  It never emits a second Goal terminal
transition or overwrites an already terminal Run.  A target mutation after the
model/result path is covered by a public launch regression and the committed
Run reader sees `interrupted`, one `goal_completed`, and one `run_interrupted`.

## F4: bounded invocation evidence before domain publication

The existing validated model-invocation record and its request/response/usage
artifacts are now committed through a small additive journal transition,
`proposal_invocation_recorded`, immediately after transport and before proposal
domain parsing.  This is a non-terminal host-owned evidence receipt, not a
renamed `goal_completed`; `read_proposal_invocation_attempts` accepts only
committed receipts whose Run, Goal, invocation ID, artifact paths, and
`validate_model_invocation` result agree.  Thus malformed proposal output still
retains the successful invocation evidence, and a competing cancellation after
transport leaves the cancellation as the sole Run terminal transition while
the request/response/record remain recoverable.

If the receipt itself cannot be committed, execution raises a typed
`invocation_evidence_refused`/conflict error and does not claim durable
evidence or success.  This intentionally does not add a second Goal event or
retry inference.  Historical model-invocation schemas and bytes are unchanged;
the only contract addition in this lane is the closed journal transition name.

## Focused verification

Commands run from the repository root after the corrections:

```text
time .venv/bin/pytest -q tests/test_scout_proposal_run.py
6 passed in 97.87s (shell 1:38.00)

time .venv/bin/pytest -q \
  tests/test_scout_proposal_execution.py::test_invalid_model_output_is_not_a_complete_proposal \
  tests/test_scout_proposal_run.py::test_alias_only_proposal_selector_refuses_before_run_allocation
2 passed in 29.69s (shell 29.833s)

time ruff check src/gigai/run.py src/gigai/scout_proposal_execution.py \
  src/gigai/journal.py tests/test_scout_proposal_run.py \
  tests/test_scout_proposal_execution.py
All checks passed (0.070s).
```

After tightening the exact proposal completion-evidence value, the focused
post-guard smoke also passed:

```text
time .venv/bin/pytest -q \
  tests/test_scout_proposal_run.py::test_supported_proposal_entry_allocates_and_terminalizes_real_run \
  tests/test_scout_proposal_run.py::test_alias_only_proposal_selector_refuses_before_run_allocation
2 passed in 29.42s (shell 29.547s)
ruff check src/gigai/run.py src/gigai/scout_proposal_execution.py \
  src/gigai/journal.py tests/test_scout_proposal_run.py \
  tests/test_scout_proposal_execution.py
All checks passed!
```

The six Run tests cover successful terminalization and repeat refusal,
invalid-domain-result failure with preserved invocation evidence, sealed
request/config mutation, target-mutation recovery, canonical-selector refusal,
and post-transport competing cancellation without duplicate Goal terminal
events.  The additional focused execution test checks malformed model output
and the invocation receipt reader.  No schema JSON or inventory manifest was
changed, so no schema-inventory command was required; no live model, provider,
network, private data, activation, or broad suite was used.

## Remaining gates

This closes the bounded correctness contract, not first-class scheduler-owned
proposal allocation, overlap leases, daily ranking, UI/private HTML reports,
Tailor or application behavior, live local-model quality, or OS isolation.
The test fixture allocates through the supported disposable launch lifecycle
and uses synthetic sources plus an injected transport.  A future scheduler
must preserve the same exact operation/descriptor, Goal shape, receipt
authority, and writer-serialized terminalization rather than reusing a
completed discovery Run or inferring authority from a role or alias.
