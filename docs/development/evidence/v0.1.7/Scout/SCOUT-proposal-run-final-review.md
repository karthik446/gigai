# SCOUT proposal Run final corrections — independent bounded review

Date: 2026-09-11  
Scope: read-only review of the final proposal Run corrections against the
current `run.py`, `scout_proposal_execution.py`, `journal.py`, and synthetic
tests. This is not a repeat of the accepted local-runtime review and does not
reopen OS isolation or add policy architecture. No source, schema, test,
private workpad, provider, model, network, configuration, or activation state
was changed.

## Verdict

The final corrections close the previously identified selector/Goal-shape and
post-result Run-terminalization defects in source, and add a non-terminal
invocation receipt before proposal parsing. The bounded path now preserves a
Goal result across target mutation/cancellation races and prevents duplicate
Goal/Run terminal events. Changes remain requested for the receipt reader:
its current committed-record check does not authenticate receipt artifact refs
or request/response bytes, nor receipt actor/target binding; this is the
remaining concrete evidence-authority gap, separate from deferred whole-Scout
integration.

## Accepted corrections

### F1/F2 — exact operation and one-Goal shape

`_validate_proposal_run_entry` now requires both the request selector and
selected Graph Set descriptor `graph_id` to be exactly
`proposal-assessment` (`run.py:750-772`). It additionally requires exactly one
Goal, one entry ID, identical terminal ID, the exact completion evidence value,
and a required automatic `proposal-assessment` Goal with no tools, the
`gigai.offline` capability, and only `write_workpad` (`run.py:773-809`). This
prevents an approved Tailor or other Goal Graph from being substituted by a
runtime selector/flag and refuses extra/non-entry Goals before Run allocation.
The historical alias resolver remains available for other operations.

The source guard is correct, but the alias regression is narrower than the
corrections report suggests. `test_alias_only_proposal_selector_refuses...`
passes `graph_selector="assessment"` (`tests/test_scout_proposal_run.py:349-375`);
it tests an unavailable selector and does not modify an approved Tailor
descriptor to contain the alias `proposal-assessment`. Therefore it does not
prove that a real alias-only match is refused. Add a disposable Graph Set
fixture with a Tailor descriptor aliasing `proposal-assessment`, then assert
`graph_selection_invalid`, no Run directory, and unchanged HEAD. Likewise,
the six currently collected Run tests contain no extra/non-entry Goal fixture;
the exact source guard is present, but that negative behavior remains a test
coverage gap rather than a source finding.

### F3 — result publication and owning Run recovery

The normal path commits a single `goal_completed` or `goal_failed` result
transition, then finishes the owning Run. `launch_run` catches
`_RunInterrupted` and `JournalConflictError` from `_finish_run` and invokes
`_recover_proposal_run_terminal` (`run.py:399-434`). Recovery re-reads committed
Run details under `run_with_journal_writer`, leaves the already committed Goal
and proposal result intact, and appends one `run_interrupted`; if a competing
writer already terminalized the Run it returns that existing terminal entry
without another Goal event (`run.py:3165-3233`).

The public target-mutation fixture exercises this recovery and asserts
`interrupted`, one `goal_completed`, one `run_interrupted`, and one invocation
attempt (`tests/test_scout_proposal_run.py:289-347`). The competing-cancel
fixture exercises a post-transport cancellation before domain publication and
asserts the cancellation is the sole Run terminal transition while invocation
evidence remains (`:378-470`). These are useful bounded race controls. They do
not simulate a real `JournalConflictError` from the final writer; that focused
probe remains desirable before claiming conflict recovery, but source
recovery is correctly writer-serialized and does not duplicate Goal events.

### F4 — receipt is non-terminal and ordered before domain parsing

After transport, the caller invokes `_publish_invocation_attempt` before
`_host_result` parses/validates proposal output (`scout_proposal_execution.py:
225-259`). The receipt uses the additive `proposal_invocation_recorded`
transition in `journal.TRANSITIONS` (`journal.py:71-78`) and carries the
invocation artifacts without changing historical model-invocation v1 bytes or
terminalizing the Goal. Its writer path rejects existing/conflicting artifact
bytes and scopes paths to the exact Run/invocation directory
(`scout_proposal_execution.py:262-336`). This preserves successful invocation
evidence for malformed proposal output and for a competing cancellation.

The current focused positive, invalid-output, target-mutation, and cancellation
fixtures all observe the receipt before the Goal/Run terminal event. No second
Goal event is invented, and receipt publication after cancellation is a
deliberate non-terminal evidence operation.

## Remaining concrete blocker — receipt reader lacks byte/reference binding

`read_proposal_invocation_attempts` reads committed handoff front matter and,
for matching `proposal_invocation_recorded` / Run / Goal fields, loads only
`runs/<run>/model-invocations/<invocation>/record.json` and checks its
Run/Goal/invocation IDs plus `validate_model_invocation(record)`
(`scout_proposal_execution.py:339-388`). It does not validate:

- the receipt's `artifact_refs` list, including exact paths, digests, sizes,
  and the expected request/record/response artifact set;
- the record's `request.request_artifact` and optional response artifact
  against bytes actually committed at those paths;
- that the receipt `model_target` agrees with the record's configured target;
- that receipt actor/source metadata identifies the proposal host, or that a
  receipt is unique and unambiguously tied to its invocation evidence.

Consequently, a committed adversarial record can retain valid v2 shape and
the same Run/Goal/invocation IDs while changing its request hash, selected
reference list, configured target, or artifact refs; the reader accepts it
without checking the corresponding committed request/response bytes. A
similarly shaped handoff can carry mismatched artifact refs or model target.
This is not fixed by Git HEAD alone: Git authenticates that bytes were
committed, while the reader must authenticate that the receipt and record
agree about those bytes and their owner/source.

Minimal fix: make the receipt reader validate a closed receipt envelope, then
load each referenced committed artifact through the existing journal authority
reader (or equivalent pinned HEAD read), verify digest/size, require exact
request/record/response paths, and compare the record's request/response refs
and Run/Goal/invocation IDs to the receipt. Require the deliberate proposal
host actor/source and configured target binding. This can be an additive
receipt-reader contract; do not rewrite historical handoff semantics or
upgrade old model-invocation records. Add synthetic committed-record,
request-byte, response-byte, and owner/actor/source mismatch negatives.

The new transition's membership in `journal.TRANSITIONS` is necessary for
recording but is not, by itself, a typed receipt contract. The historical
`handoff-frontmatter.schema.json` transition enum predates
`proposal_invocation_recorded`; it is reasonable not to retroactively require
every historical handoff to validate against a new enum, but the new receipt
reader must perform the deliberate strict validation described above (or use a
versioned/additive receipt schema in the normal inventory process). Until then,
the corrections report's claim of a strict receipt reader is stronger than the
actual hash/reference proof.

## Identity, lineage, and semantic limits

The sealed request still binds the approved graph/descriptor, exact selected
posting/private selectors, configured local target, canonical model digest,
and source lineage; host-created result lineage is not replaceable by model
claims. The invocation contract's real-G45-anchor restriction remains honestly
documented; native/discovery sources are not given fabricated `ref_` IDs. The
generic proposal envelope and structural tests do not establish semantic truth
about salary, sponsorship, fit, or hiring outcomes. No automatic Tailor,
resume, application, UI, or outbound action is implied, and reviewer role is
not evidence of a provider-review family.

## Focused evidence and limits

Fresh read-only collection from the repository root:

```text
$ rtk .venv/bin/pytest --collect-only -q tests/test_scout_proposal_run.py
6 tests collected in 0.14s

$ rtk .venv/bin/pytest --collect-only -q tests/test_scout_proposal_execution.py
8 tests collected in 0.14s
```

The current six Run tests include positive/invalid results, sealed input
mutation, target-mutation recovery, unavailable-selector refusal, and
post-transport cancellation; they do not include a real Tailor alias-only
match, extra Goal graph, committed artifact tamper, or real journal-conflict
negative. I did not rerun the full 100/125-second lanes, provider/model or
network operations, private data, activation, or broad architecture checks.
This report is a bounded final-correction review, not whole-Scout acceptance;
receipt byte/reference authentication and the listed narrow negative fixtures
are the minimal remaining evidence work.
