# SCOUT local proposal execution — independent review

Date: 2026-09-11  
Scope: bounded read-only review of `scout_proposal_execution.py`, its focused
synthetic tests, and the implementation report. The accepted local runtime is
not re-reviewed here. No source, test, schema, config, journal, provider,
model, network, private-data, activation, or workpad files were changed.

## Verdict

The new caller correctly joins the real completed-discovery resolver, pinned
private input readers, local-only target gate, reviewer role, exact source
bytes, model-output validation, and private result artifact. It does not
fabricate IDs: discovery/native sources remain in host lineage while a real
G45 reference is used as the existing invocation anchor, which is an honestly
documented contract limitation. The bounded slice is not lifecycle-safe for
general use because it accepts any shape-valid Run/goal IDs and emits goal
terminal transitions without proving an active scheduler-owned Goal; the
model invocation itself also emits a Goal terminal transition before proposal
validation, producing contradictory completion/failure events.

## Findings

### P0 — caller can terminalize an arbitrary or already-terminal Run/Goal

`execute_local_proposal` validates only the syntactic entity shape of
`run_id`/`goal_id` (`src/gigai/scout_proposal_execution.py:102-108`). It does
not read a real Run's committed `run-details.json`, sealed Goal Graph, or
journal history to establish that the Run exists, is active, contains the
Goal, and has that Goal in an allowed state. After the model call it invokes
`record_transition` with `goal_completed` or `goal_failed` and writes
`runs/{run_id}/scout-proposals/.../result.json` (`:216-240`). Generic journal
validation accepts these fields and paths; it does not supply scheduler
authority or enforce the Goal's graph/effect policy for this caller.

This is an actual lifecycle authority violation, not merely a trusted-internal
caller preference. A disposable synthetic probe using the genuine discovery
fixture but a different valid Run ID printed:

```text
foreign_run_result complete
foreign_run_path_exists True
foreign_result_artifact_exists True
selected_discovery_run run_f52efdf0-b0a4-42d6-8e79-7ad1c2e35788
```

Thus the function created a new foreign Run tree and committed proposal
evidence under it. A second disposable probe passed the already-terminal
discovery Run ID with an arbitrary valid Goal ID and printed:

```text
terminal_discovery_run run_376c142a-7fcf-4bda-8f62-becb5f0e996c
caller_goal goal_00000000-0000-4000-8000-000000000999
proposal_result complete
committed_transition goal_completed
committed_run_id run_376c142a-7fcf-4bda-8f62-becb5f0e996c
committed_goal_id goal_00000000-0000-4000-8000-000000000999
```

The fixture intentionally reuses that completed discovery Run
(`tests/test_scout_proposal_execution.py:196-200`), so the positive test
currently demonstrates the corruption rather than authority. Minimal fix
direction: have the scheduler-owned caller prove the exact active Run/Goal
and permitted effect state before invoking, or make this helper refuse to emit
generic Goal transitions and use a separately authorized proposal transition.
Do not treat a valid UUID as Run/Goal allocation or completion authority.

### P0 — nested model invocation and proposal validation emit contradictory
Goal terminal events

`model_execution.run_model_invocation` records its own `goal_completed` event
when the model transport succeeds, before `execute_local_proposal` validates
the proposal (`src/gigai/model_execution.py:307-345`). The caller then records
another `goal_completed` for a valid proposal or `goal_failed` when the model
JSON is malformed/spoofed (`scout_proposal_execution.py:206-240`). Therefore a
successful model call with invalid proposal semantics first terminalizes the
Goal as completed and then terminalizes the same Goal as failed. A valid
proposal gets duplicate Goal completion events. The existing spoof test
(`tests/test_scout_proposal_execution.py:304-331`) proves result status is
failed, but does not inspect the contradictory journal transitions.

The model invocation's own terminal record is useful evidence, but it must not
be confused with proposal completion. The next owner must choose one explicit
authority boundary: scheduler owns the Goal event after proposal validation,
or the invocation is recorded under a separate invocation operation that does
not terminalize the proposal Goal. This is a concrete event-lineage defect,
not a request for a general lifecycle redesign.

### Accepted bounded source authority and private-input handling, with a
purpose-mapping correction required

`_resolve_sources` holds one `JournalWriter`, resolves the discovery posting
through `resolve_discovery_posting_input_from_journal`, then snapshots the
committed `records/`, `references/`, and `run-inputs/` trees before resolving
private selectors (`:244-273`). The posting resolver authenticates the v2
Plan/Run/checkpoint/receipt/output tuple and exact capture bytes; the private
resolver authenticates G45 records or native revision chains and exact blob
bytes. Missing, scalar, malformed, or changed selectors are rejected before
the model call through typed source-resolution errors. The focused tests prove
changed discovery refusal, local permission refusal, and hosted-target
refusal without transport calls.

The caller currently infers purpose by family (`:318-369`): every
`g45_reference` becomes `preferences`, every `g45_run_input` becomes `answer`,
and native `experience_qa` becomes `experience` while any other admitted
native kind becomes `preferences`. That is not a reliable semantic mapping:
`private_records.import_reference` admits `resume`, `project_evidence`,
`role_history`, and `cover_letter`, while `import_run_input` records a
`job_description`; the resolver intentionally returns family/ID/snapshot,
not a preference-vs-experience-vs-answer purpose. The positive fixture imports
a synthetic `resume` but presents it as `preferences`, and a synthetic pasted
answer is represented by the `job_description` run-input family.

This can mislabel private context in the prompt and proposal lineage even
though bytes and authority are authentic. Require an explicit host-owned,
closed purpose association for each selected private source (or preserve a
trusted purpose from the source contract) and reject ambiguous family-only
selections; do not infer semantic purpose from G45 family. No private content
is exported by this helper.

### Accepted lineage and anchor limitation; exact prompt replay remains
deferred

The host result's `input_lineage` contains the discovery identity and every
selected native/G45 source descriptor, with exact content digests and the
sealed journal HEAD. `validate_proposal_output` rejects model-supplied
lineage, and the local target gate rejects hosted adapters before transport.
The pure proposal prompt receives all resolved source bytes. The caller passes
only real `g45_reference` IDs into the unchanged model-invocation contract;
native/discovery inputs are not given fabricated `ref_` IDs. A selection with
no G45 anchor is refused with `invocation_reference_contract`, as documented.

The invocation request artifact itself stores selected reference IDs and an
input digest, not the exact prompt or all native/discovery source descriptors;
the local path's redaction input digest is not a substitute for prompt bytes.
Consequently, host lineage preserves source coverage for this result, but a
later invocation reader cannot independently replay the exact prompt from the
invocation record alone. A versioned invocation-source extension or a host
prompt artifact can address that later; the real-G45-anchor restriction and
prompt replay are deferred limitations, not permission to fabricate IDs in
this slice.

Source objects are frozen with exact bytes and digest, so edits after source
resolution cannot mutate the in-flight prompt. The caller records the old
sealed HEAD but does not CAS that HEAD again before final result publication;
the resulting assessment is still explicitly attributable to the old pinned
bytes. Duplicate/replay handling remains an acknowledged follow-on, not a
claim of idempotent proposal execution.

### Result artifact journal shape is structurally admissible but lacks Goal
authority/effect binding

The result path is relative and under `runs/<run>/scout-proposals/`; its
`_ref` has the existing artifact path, digest, media type, and size fields.
`record_transition` commits the artifact and handoff together, and the
existing positive test reads it back with `read_committed_artifact`, proving
exact committed bytes rather than merely reading a working-tree file. The
handoff transition names (`goal_completed`/`goal_failed`) are in the existing
front-matter enum and the artifact reference shape is accepted.

There is no dedicated proposal-execution schema or effect-policy validation,
and the event is not attached to a sealed Goal Graph/run-details evidence
update. As demonstrated by the P0 probes, generic journal path/schema checks
cannot authorize this semantic event. The artifact publication mechanism is
sound once a scheduler/host authority supplies the correct Run/Goal boundary;
the current caller must not present structural journal admissibility as
complete lifecycle acceptance.

## Evidence and limits

Focused source lint probe:

```text
$ ruff check src/gigai/scout_proposal_execution.py \
    tests/test_scout_proposal_execution.py
All checks passed!
```

The implementation report claims `4 passed in 53.23s` for the focused file and
`39 passed in 53.63s` for that file plus the pure proposal lane; those reported
tests were not rerun to avoid repeating the approximately 54-second lane. The
two disposable synthetic probes above used the existing public discovery and
private-record fixtures, injected only the existing offline HTTP transport,
and made no provider/model/network calls. One probe's wrapper attempted an
unsupported `JournalEntry` attribute after printing the relevant committed
transition fields; the side-effect evidence was already printed and the
fixture directory was disposable.

This review does not claim whole-Scout acceptance. Deferred gates include a
scheduler-owned active Run/Goal caller, non-contradictory proposal terminal
events, explicit private-source purpose binding, exact prompt/source coverage
in invocation evidence, proposal replay/deduplication, UI/report projection,
semantic model quality, and any Tailor/resume/application action. The accepted
local runtime and its no-hosted-fallback boundary are assumed from the prior
review.
