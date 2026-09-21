# SCOUT proposal Run entry integration

Status: bounded implementation and synthetic focused verification. This adds a
real local proposal entry to the existing Run lifecycle; it does not add a
daily scheduler, UI, Tailor document generation, application state, or hosted
model path.

## Supported entry

`run.ProposalRunRequest` is a closed host DTO containing the explicit approved
`tailor-application` graph selector, configured local target ID, authenticated
posting/private selectors, and local permission. It contains no prompt or
caller-supplied source bytes. Before allocating a Run, `launch_run` resolves
the approved active authority and selected Graph Set member, requires exactly
one entry Goal with the existing `write_workpad` effect, verifies the local
Ollama endpoint/model digest, requires direct operator consent, and seals the
canonical selector/target envelope at
`runs/<run>/sealed/proposal-execution-request.json`. Existing deterministic and
provider-review invocation paths are unchanged.

After the normal `run_started` handoff, the entry publishes a genuine
`goal_started` transition using the allocated Run/Goal. It calls the accepted
host proposal resolver, which authenticates the completed discovery posting and
private revisions under the journal writer, invokes only the configured local
target, and publishes the domain result with exact invocation evidence. The
existing terminal-consistency path materializes Goal `complete/COMPLETE` or
`failed/FAILED` and then the current `_finish_run` helper terminalizes the
owning Run exactly once. No completed Run is reopened and no test-only started
record is used by the new integration fixture.

The failed-result path records a Goal failure and owning `run_failed` terminal
state; it never relabels an invalid model response as success. A repeat against
the completed Run is refused before transport by the committed Goal terminal
history check, preserving the Run HEAD and call count.

## Evidence and boundaries

Focused tests use disposable public discovery records, synthetic native/private
revisions, and an injected offline transport. They cover a successful real
`launch_run` lifecycle, exact Run-reader terminal state, sealed request
manifest reference, repeat refusal without transport/write, and invalid model
output producing one failed Goal/Run terminalization. Existing proposal caller
tests continue to cover source tamper, foreign/nonmember authority, purpose
mismatch, and local-vs-hosted permission gates.

The proposal selector envelope is sealed as an additive source of the existing
Run manifest; it does not alter historical manifest/schema bytes. The envelope
pins the approved selector, selected graph digest, Gig version, target endpoint,
model, and configured digest, but private source bytes remain resolved only by
the existing authenticated source services. The direct proposal entry requires
the approved graph selector and direct local consent; it does not infer
authority from a reviewer role or arbitrary text.

The current transport occurs before the proposal terminal publication lock, so
the previously documented cancellation/competing-terminal race remains an
explicit runtime gate: there is no accepted invocation-only nonterminal receipt
to preserve an invocation that completed while another terminalizer won. The
new entry does not hold the journal lock through inference or counterfeit a
second Goal event. First-class scheduler allocation, proposal replay service,
UI/final selection, Tailor output, and application transitions remain later
gates.

Verification commands (11 September 2026, synthetic/offline transport only):

* `ruff check src/gigai/run.py src/gigai/scout_proposal_execution.py tests/test_scout_proposal_run.py tests/test_scout_proposal_execution.py` — passed.
* `./.venv/bin/pytest -q tests/test_scout_proposal_run.py` — 2 passed (32.17 s final run).
* `./.venv/bin/pytest -q tests/test_scout_proposal_execution.py` — 8 passed (124.84 s).
* `./.venv/bin/pytest -q tests/test_g13_run.py` — 6 passed (9.31 s).

No provider/model server, network, private workpad, activation, or full-suite
run was used.
