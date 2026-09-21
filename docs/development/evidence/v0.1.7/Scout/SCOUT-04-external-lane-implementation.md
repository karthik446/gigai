# SCOUT-04 — External recording lane implementation

**Status:** Lane implementation delivered for integration; no provider or
network execution occurred.

`external_recording.py` is an external-only recording service, not a managed
provider adapter. It resolves approved Graph Set authority, pins tested
immutable G45 inputs (reference, run-input, and Scout revision wrappers over
those G45 families), seals a deterministic external Plan, revalidates it
before start, records checkpoints/cancellation/submission receipts under the
existing writer lock, and rejects scope/origin/limit/terminal/missing-output
failures with typed redacted errors. Every emitted and read external envelope
is validated by its strict schema: exact operation input, provenance actor,
canonical IDs, G45 sealed-input reference, predecessor, artifact/output/check,
question/disclosure, and fixed limits shapes reject unknown members. Successful
submit derives required output and check fields from the exact sealed simple
approved contracts. Submit rereads both output artifacts, parses and verifies
the output sidecar against its declared kind, Run, Markdown digest, sealed
inputs, and exact checkpoint tuple; it also accepts only separately recorded
check sidecars with a required declared `result` of `pass` or `fail`. Failed
evidence remains journaled but cannot make a receipt succeed, while a later
explicitly selected corrected `pass` may; these are external actor declarations
under `execution=unobserved`/`actor_report=declared`, not GigAI evaluation.
Unsupported contract shapes refuse. Artifacts use the frozen map:
`run-plans/<id>/external-plan.json`, `runs/<id>/external-run.json`,
`runs/<id>/checkpoints/`, `runs/<id>/receipts/`, and `runs/<id>/artifacts/`;
no new top-level root is used. `external_cli.py` supplies the separately
mountable Click group; writer requests are bounded UTF-8 JSON envelopes and
cannot silently acquire direct-user provenance.

The implementation calls the central journal transitions and canonical prefixes
rather than adding local substitutes. It includes pure schema fixture constants
for the central inventory and returns agent-readable input/output/check contract
content from `external requirements`; bundled Scout source should pass the exact
`plan` input shape from B1 and checkpoint-produced Markdown plus JSON sidecars
to `submit`.

Focused checks completed:

```text
uv run pytest -q tests/test_scout04_external_recording.py -k 'schema or rejects_agent or replay_conflict'
9 passed, 6 deselected in 8.15s

uv run pytest -q tests/test_scout04_external_recording.py::test_completion_requires_contract_bound_output_and_check
1 passed in 11.94s

uv run pytest -q tests/test_scout04_external_recording.py::test_agent_reuses_authenticated_selection_for_distinct_plan_key tests/test_scout04_external_recording.py::test_all_admitted_g45_input_families_revalidate_at_start tests/test_scout04_external_recording.py::test_waiting_input_can_continue_unchanged_or_require_a_pinned_successor
3 passed in 28.35s

uv run pytest -q tests/test_scout04_external_recording.py::test_external_plan_start_checkpoint_and_missing_submit_are_journaled tests/test_scout04_external_recording.py::test_requirements_returns_agent_readable_contracts_and_missing_question_needs_successor
2 passed in 10.32s

uv run ruff check src/gigai/external_recording.py src/gigai/external_cli.py tests/test_scout04_external_recording.py
All checks passed

.venv/bin/python -m compileall -q src/gigai/external_recording.py src/gigai/external_cli.py
git diff --check
passed
```

Coordinator handoff: the pre-release `result` refinement changes only
`external-recording-invocation.schema.json`, whose SHA-256 is
`0608170d6b707198f903e4097f6a14cba61a92581a8f119b0e074da685df7316`.
The valid central-inventory fixture helper remains
`tests/test_scout04_external_recording.py::external_schema_fixtures`.
Representative `pass` evidence is exercised by the disposable-Gig completion
fixture; result-free and unknown-result check sidecars are rejected both by
schema validation and by the checkpoint API.

The disposable-Gig focused suite currently passes fifteen tests, covering
strict positive fixtures and actual emitted envelopes plus nested required-field,
unknown-member, enum/type, malformed-ID, incompatible-origin, and the exact
empty invocation/input/limits Plan mutation rejection. It also covers graph
authority planning, replay conflict, start, checkpoint CAS, waiting-input/
successor refusal, cancellation terminal immutability, required outputs/checks,
strict direct/agent origin validation, contract-derived `report` output and
`proposal-validation` evidence completion, output-as-check refusal,
deterministic second direct Plan selection reuse, all three admitted G45
families with start revalidation, and unchanged-input continuation versus a
pinned changed-input successor. The latest correction adds submit-time exact
output tuple/sidecar revalidation, swapped/changed-kind/duplicate output refusal, failed and
mixed declared evidence refusal without a terminal receipt, immutable failed
checkpoint retention, a corrected pass checkpoint, and successful same-key
submit replay. Native `jsl_blob` source resolution remains deliberately refused
in this lane. The external `result` gate is a pre-release SCOUT-04 contract
refinement only: no historical evidence migration or detailed domain evaluator
semantics were added; SCOUT-08 owns rule/version/findings/severity. Remaining
gates are Astra's central schema digest/count/fixture inventory and combined
integration verification; no full suite was run.
