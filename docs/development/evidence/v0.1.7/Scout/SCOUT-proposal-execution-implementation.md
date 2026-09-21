# SCOUT local proposal execution implementation

Date: 2026-09-11  
Status: bounded synthetic caller implemented; no live model/provider execution

## Delivered slice

`src/gigai/scout_proposal_execution.py` joins the existing authority paths:

1. It resolves a completed discovery posting through
   `resolve_discovery_posting_input_from_journal` while holding the caller's
   journal writer, then resolves each explicitly selected private reference or
   native revision from the same committed input snapshot.
2. It constructs `ScoutProposalRequest` and its exact host-generated prompt;
   posting bytes, private bytes, identities, and digests come from the
   authenticated resolvers, never from request-embedded bytes.
3. It admits only a configured `ollama_local` target, explicit
   `local_allowed=True`, and the registered `model_invocation/reviewer` role.
   The existing sole factory and `run_model_invocation` perform local model
   identity checks, request execution, bounded response handling, resource
   closure, and durable invocation request/response/record publication.
4. It validates the model response with `validate_proposal_output`, then
   host-binds the exact invocation ID, invocation-record digest, request digest,
   response-artifact ref, selected source lineage, and sealed journal HEAD.
   Model-supplied lineage cannot become authoritative.
   The host writes one private `runs/<run>/scout-proposals/<invocation>/result.json`
   through `record_transition`; invalid, blocked, cancelled, or failed calls
   produce `status=failed` with no complete proposal.

The result intentionally does not invent a `proposal_revision_id`: the current
pure proposal helper's revision DTO has no accepted durable result-allocation
caller contract. The durable result uses the existing invocation ID and exact
lineage instead; adding a proposal-result revision/schema is a separate narrow
follow-up, not fabricated here.

## Synthetic verification

`tests/test_scout_proposal_execution.py` creates a disposable committed
find-jobs discovery Run through the existing public fixture, imports synthetic
preference and answer G45 inputs, creates a synthetic native experience
revision, and injects a bounded HTTP transport into the existing factory. The
positive test proves the actual posting resolver, committed private revision
resolution, generated prompt, local Ollama identity checks, response
validation, resource close, and committed private result artifact together.

Focused command and result:

```text
ruff format src/gigai/scout_proposal_execution.py tests/test_scout_proposal_execution.py
ruff check src/gigai/scout_proposal_execution.py tests/test_scout_proposal_execution.py
.venv/bin/pytest -q tests/test_scout_proposal_execution.py
```

Ruff: `All checks passed!` (final check wall time 0.06 s). The focused file
passed `4 passed in 53.23s` during development; the final combined command
passed `39 passed in 53.63s` (`tests/test_scout_proposal_execution.py` plus the
35-test pure `tests/test_scout_proposals.py` compatibility lane; wall time
53.75 s). The focused cases cover:

* real discovery + private preference/experience/answer source joining and
  exact committed result readback;
* explicit local permission refusal and hosted-target refusal before transport;
* changed discovery tuple refusal before transport;
* model output attempting to spoof host lineage, resulting in failed status and
  no complete proposal.

No live local model request, provider call, network operation, private Gig,
activation, schema/inventory edit, UI, scheduler, Tailor, resume, application,
or SQLite edit was performed.

## Remaining gates

The accepted model-invocation record contract requires at least one canonical
`ref_...` selected reference. Discovery and native revisions do not have that
identity shape, so the caller uses a real selected G45 reference as the
invocation evidence anchor while retaining every discovery/native source in
the host-owned proposal lineage. A caller selecting only native records is
refused with `invocation_reference_contract` rather than receiving a fabricated
ref ID; the minimal future change is a versioned invocation source-descriptor
extension reviewed with the invocation schema owners.

The API requires the caller to provide an already allocated valid Run and goal
identity. The fixture reuses the genuine discovery Run ID and a disposable
valid goal ID because this slice does not own scheduler/goal allocation. It
does not claim a new approved pointer or application transition. UI/report
rendering, daily scheduling, Tailor request selection, model semantic quality,
replay deduplication for proposal result calls, and a first-class proposal
revision contract remain follow-on gates.
