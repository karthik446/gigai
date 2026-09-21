# SCOUT proposal Run authority — independent bounded review

Date: 2026-09-11  
Scope: read-only review of the corrected proposal Run entry and execution
caller (`run.py`, `scout_template.py`, `scout_materialization.py`,
`scout_proposal_execution.py`), its current synthetic Run tests, and the
proposal Run authority corrections. The accepted local-runtime and adapter
boundaries are not reopened here. No source, schema, test, private workpad,
provider, model, network, activation, or configuration state was changed.

## Verdict

The correction now has a credible bounded host-owned Run path: the proposal
operation is separately inventoried, selected through the approved Graph Set,
sealed with exact request/target/input descriptors, consented before Run
allocation, and checked against committed active Goal authority before model
execution and result publication. However, changes are still requested before
calling this caller release-ready: selector aliases and multi-goal proposal
graphs are not rejected at the proposal entry, and a target-change or journal
failure after Goal publication can leave a completed Goal under a still-running
Run. Invocation-evidence loss on a post-model publication race remains the
explicit unresolved release gate documented by the implementation; this is a
bounded partial slice, not whole-Scout acceptance.

## Accepted controls

### Separate proposal operation and authority

`scout_template.py` retains the five historical `SCOUT_GRAPHS` selectors and
adds `SCOUT_PROPOSAL_GRAPH` as a sixth member of `SCOUT_OPERATION_GRAPHS`
(`:80-100`). `scout_source_files()` inventories the operation tuple, and the
candidate compiler iterates the same tuple (`scout_materialization.py:204-205`)
and emits the proposal graph's own goal contract and completion envelope
(`:312-320`). The proposal branch does not reuse Tailor's `.075` source/schema;
the five historical selectors remain separately represented. The compiler
docstring still says “all five” while iterating six (`scout_materialization.py:
195-205`), which is documentation drift but not evidence that the sixth graph
is omitted.

Before allocation, `run._resolve_authority` re-reads and digests the approved
Graph Set from the immutable approval commit, validates its v2 proposal, and
`resolve_selected_graph_authority` re-reads descriptor attachments and the
selected goal graph (`run.py:1059-1143`, `:1184-1286`). A graph-selected Run
must emit a v2 manifest, an approved Graph Set ref, and sealed operator-explicit
selection evidence (`run.py:1652-1701`, `:1747-1805`); the prepared manifest is
validated with `run-manifest-v2.schema.json` (`:1815-1820`). This is actual
Graph Set/manifest authority, not a selector string or runtime flag alone.

### Sealed request, source bytes, and consent

`ProposalRunRequest` is a small host DTO. `_validate_proposal_run_entry`
requires the exact `proposal-assessment` request selector, one entry Goal with
`write_workpad`, an identified configured local Ollama target, canonical model
digest, explicit boolean local permission, and closed posting/private
selector shapes (`run.py:726-815`). It canonicalizes the selected graph,
version, target endpoint/model/digest/bounds, and exact source selectors into
`sealed/proposal-execution-request.json`. Configuration is loaded once before
sealing and the sealed bytes are parsed after `run_started`; mutable caller
maps are not re-read (`run.py:182-193`, `:302-339`). Direct operator consent is
validated and redeemed before `_allocate_run_id` (`run.py:195-251`).

The execution caller obtains posting bytes through the authenticated discovery
resolver and private bytes through the committed snapshot, preserving exact
content digests and family-specific identity/purpose checks
(`scout_proposal_execution.py:575-621`, `:624-762`). The generated prompt and
host result carry all selected discovery/native source lineage. Existing model
invocation evidence still accepts only real G45 `ref_` anchors; discovery and
native identities are retained in host lineage rather than fabricated as
invocation references. That limitation is explicitly documented and remains a
future versioned invocation-source contract, not a reason to launder an
invented ID.

### Goal lifecycle and output binding

The corrected caller checks committed `run-details.json`, sealed Goal Graph
bytes, Run/gig identity, active `running` status, Goal membership, terminal
handoff history, and exact `write_workpad` effects before touching the model
(`scout_proposal_execution.py:250-360`). It repeats that check immediately
before invocation and inside `_publish_result` (`:195-218`, `:468-569`). The
model execution is called with `commit_goal_transition=False`, so the generic
invocation helper does not independently terminalize the proposal Goal. The
single publication transition includes invocation request/record/response
artifacts, the private host-bound result, updated Goal details, and a
`goal_completed` or `goal_failed` handoff. Model-supplied lineage cannot become
authoritative: `_host_result` derives invocation/request digests, source
lineage, and sealed journal head from host execution/request objects
(`scout_proposal_execution.py:793-840`). Hosted targets and repeated terminal
execution are refused before transport by the existing caller checks.

## Findings requiring follow-up

### F1 — proposal selector aliases can substitute another approved Goal Graph

`graph_set_descriptor` accepts either a descriptor `graph_id` or any listed
alias (`graph_set.py:276-282`). The proposal entry check only tests
`request.graph_selector == "proposal-assessment"` and that a descriptor exists;
it does not require `selected_descriptor["graph_id"] ==
"proposal-assessment"` (`run.py:736-752`). Therefore an approved descriptor
whose aliases contain `proposal-assessment` (for example the historical
Tailor descriptor) can be selected by that request selector. The resulting
sealed manifest records the substituted descriptor/Goal Graph while the
proposal branch still runs, proving the selector string rather than the
separate operation. The shipped candidate currently emits empty alias lists,
so this is a reachable authority-validation gap rather than a demonstrated
current-candidate substitution.

Minimal fix: for this closed operation require the selected descriptor's exact
Graph Set `graph_id` and graph slug to be `proposal-assessment`, and add a
negative fixture with an alias-only match; do not disable aliases globally for
historical Graph Set readers.

### F2 — extra/non-entry Goals are admitted by the proposal entry

`_validate_proposal_run_entry` requires `entry_goal_ids` to be a one-item list,
but does not require the selected proposal graph to contain exactly that one
Goal (`run.py:742-756`). A graph with one proposal entry plus non-entry Goals
can therefore pass the entry check; the caller starts and terminalizes only the
entry while `_finish_run` can mark the whole Run succeeded. The current
materialized proposal graph has one Goal, so this is not evidence that the
current package emits extras, but the Run entry should enforce the operation's
single-Goal authority instead of trusting compiler shape.

Minimal fix: reject any proposal graph whose Goal set is not exactly the one
entry Goal (and assert its terminal/required lists agree), with a synthetic
extra/non-entry graph negative test.

### F3 — terminal Run can be stranded after Goal publication

`_publish_result` commits the terminal Goal, invocation artifacts, result, and
replacement `run-details.json` first (`scout_proposal_execution.py:468-569`),
then `launch_run` calls `_finish_run` to compare target-before/after and append
the Run terminal handoff (`run.py:370-400`, `:3060-3112`). If the target changes
after model/result publication, `_finish_run` raises `_RunInterrupted` at
`:3070-3074`; the outer proposal exception path does not convert that already
completed Goal into a Run interruption. The same stranded `status=running`
state can result from a journal conflict at this second terminalization step.
This is a concrete lifecycle corruption: committed Goal completion and result
evidence coexist with no terminal Run status.

Minimal fix: make the Run terminalization boundary handle target mismatch or
journal conflict with one authenticated interrupted/failure terminal outcome,
or perform the target recheck and Run/Goal terminal publication under one
caller-owned transition. Add a disposable target-mutation-after-publication
probe; the current mutation test only mutates the in-memory request and proves
that sealed request/config values are redeemed (`tests/test_scout_proposal_run.py:
222-275`).

### F4 — invocation evidence is lost on a post-model publication race

The caller deliberately invokes `run_model_invocation(...,
commit_goal_transition=False)` and holds returned invocation artifacts for the
later `_publish_result` transition. If `_publish_result` rechecks the committed
Run and sees cancellation or a competing terminal Goal, it refuses the
publication; the returned artifacts are not independently journaled. The
implementation correction explicitly records this post-invocation,
pre-publication evidence limitation. This remains an unresolved release gate
for invocation auditability; it is distinct from the fixed stale-terminal
handoff protection and does not justify inventing a second terminal event.

## Source semantics and reader limits

The private resolver now uses explicit host purposes and checks native kinds
(`profile_preferences` versus `experience_qa`) instead of silently treating
every source as the same semantic family. G45/reference and native/discovery
identities are authenticated by real readers and exact snapshots; source-local
handles in prompts are not durable authority IDs. The proposal result is a
private generic host envelope, not a future domain output schema: structural
validation does not establish salary, sponsorship, fit, or any other semantic
truth. The caller performs no Tailor, resume, application, UI, or outbound
action. Reviewer-role/provider-review-family distinctions and the previously
accepted local runtime route are outside this review's release verdict.

## Focused evidence and limits

Fresh commands run from the repository root:

```text
$ rtk .venv/bin/pytest -q \
    tests/test_scout_proposal_run.py::test_supported_proposal_entry_allocates_and_terminalizes_real_run
1 passed in 16.17s

$ rtk .venv/bin/pytest --collect-only -q tests/test_scout_proposal_run.py
3 tests collected in 0.11s

$ ruff check src/gigai/scout_proposal_execution.py \
    tests/test_scout_proposal_execution.py
All checks passed!
```

The positive fixture uses a genuine synthetic discovery Run, real committed
private records, operator consent, the separately selected proposal graph,
and an injected offline transport; it checks v2 selection/manifest refs,
transport closure, sealed request mutation behavior, and stale terminal
attempt behavior. I did not run the full caller lane, broad suite, provider or
local model, network, activation, private-data, or schema changes. This report
does not claim end-to-end Scout readiness; the minimal alias/extra-Goal guards,
post-publication Run terminalization, and the explicitly documented invocation
evidence race remain integration/release work.
