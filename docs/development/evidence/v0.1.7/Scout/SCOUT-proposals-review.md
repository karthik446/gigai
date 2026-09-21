# SCOUT private proposal builder — independent review

Date: 2026-09-11  
Scope: read-only review of `src/gigai/scout_proposals.py`,
`tests/test_scout_proposals.py`, `SCOUT-proposals-implementation.md`, and the
controlling `SCOUT-local-runtime-decision.md`.

## Verdict

The module is a useful pure prompt/shape-validation slice, but it is not yet a
durable discovery/private-record proposal integration. Its exact-byte digest
checks, closed output shape, evidence allowlists, untrusted-posting prompt, and
no-action boundary are sound as local building blocks. A caller cannot safely
construct the current `ScoutProposalRequest` from the existing committed
posting/discovery/native authorities without an explicit host-owned provenance
adapter: blindly filling the required `ref_`/`revision_` fields would fabricate
lineage. The implementation report correctly labels caller, journal, adapter,
projection, and UI work as deferred; this review does not treat those deferred
gates as completed.

## Findings

### F1 — Existing source authorities do not match the mandatory source DTO
**Severity: integration blocker; no source edit made.**

`ExactProposalSource` requires a canonical `ref_` `reference_id`, a canonical
`revision_` `revision_id`, a `source_kind`, exact UTF-8 bytes, and an imported
byte digest (`src/gigai/scout_proposals.py:108-161`). The request then requires
three public objects—posting, opportunity, and capture—and at least one private
object (`:164-205`). That shape is internally closed, but it does not map
one-to-one onto the authorities already present in the worktree:

- `private_records.import_reference` commits `references/ref_.../reference.json`
  with `reference_id` and an exact `snapshot` of source bytes, but it does not
  create a `revision_id` (`src/gigai/private_records.py:307-336`).
- `import_run_input` commits `run-inputs/input_.../input.json` with
  `run_input_id`, not `ref_` or `revision_` (`private_records.py:339-373`).
  This is the accepted G45 operator-paste family, not a proposal revision.
- Native private records have `record_` plus `revision_` identities and a
  `jsl_blob` sidecar; they do not have a `ref_` identity.
- A completed discovery posting resolver returns one exact captured posting
  byte stream plus `run_ref`, `receipt_ref`, `checkpoint_ref`, opportunity and
  snapshot identities, and a supporting-artifact reference
  (`src/gigai/scout_posting_inputs.py:462-492`). It does not return a separate
  `ref_`/`revision_` pair for an opportunity object, nor a second independent
  capture revision. The capture supporting artifact is the posting's source
  bytes, while the opportunity is a domain identity in the discovery packet.

Consequently, constructing three `ExactProposalSource` values from this output
would require either inventing `ref_`/`revision_` IDs, pretending a run,
checkpoint, or supporting artifact is a revision, or serializing domain
metadata as a new “opportunity” source. Digests prove byte equality but do not
grant identity authority. This is a material caller/provenance blocker, not a
reason to weaken digest checks or make the model invent IDs.

The next integration slice needs a host-owned source descriptor that is built
from authenticated committed authorities and carries the actual owner family,
record/run/revision or artifact refs, and exact bytes. It should either model a
single posting bundle with explicit discovery provenance or define a deliberate
derived opportunity/capture mapping; it must not fill the current DTO with
synthetic identities. The model must not be the owner of this mapping.

### F2 — Invocation compatibility exists, but the local target is not enforced
**Severity: caller gate; currently deferred rather than a pure helper defect.**

`build_invocation_request` produces the existing `InvocationRequest` and sets
bounded output, `reasoning_effort="none"`, text capability, and role
`scout_private_assessor` (`scout_proposals.py:261-282`). It does not invoke a
port, which is good. However, that role is not one of the currently registered
model-invocation roles in `src/gigai/roles.py:15-31`; the existing DTO therefore
reports an unresolved legacy role rather than a registered role reference.
The function also accepts arbitrary `target_name`, `endpoint_name`, and model
strings and carries no selected model digest. A caller could route the private
prompt to a remote adapter unless the later factory/config integration forces
the accepted `OllamaLocalAdapter` and its identified endpoint/model digest.

The prompt says “private local assessor” and “no tools/network,” but those are
instructions, not transport authority. The controlling runtime decision requires
the caller to enforce numeric loopback, digest, proxy/redirect refusal, no
hosted fallback, and trusted-local-runtime assumptions. The proposal module
does not overrule that decision; it simply needs a reviewed caller binding
before being called release-ready.

### F3 — Closed shape is not semantic truth or minimum proposal utility
**Severity: output acceptance gate.**

The validator correctly checks exact top-level keys, enum values, IDs, bounded
text/list sizes, exact lineage when a request is supplied, source-type/ref
allowlists, and the ordinal-fit meaning (`scout_proposals.py:285-425`,
`:568-850`). `source_fact`, `user_report`, and `model_assessment` are useful
classifications, but they remain model declarations; they do not prove that a
salary, sponsorship, experience, location, or fit claim is true. The prompt's
unknown/notprovided/declined instructions are also not semantic enforcement.

The validator permits `fit_reasons`, `hard_blockers`, `unknowns`,
`proposed_resume_focus`, and `focused_experience_questions` all to be empty
while still accepting `status="complete"` when the remaining fields are
syntactically valid. A synthetic probe confirmed
`validate_proposal_output(..., request=request).valid == True` after emptying
all five lists. That is not an acceptable one-page proposal utility claim:
every released proposal needs why-it-fits, hard blockers/unknowns (empty
blockers can be explicit), proposed focus, focused questions, and references.
The host acceptance layer should require meaningful coverage and preserve an
explicit “limited facts” outcome when local semantic synthesis is unavailable.

The existing tests correctly cover salary mismatch text and unknown
sponsorship as private assessment content, but pure tests cannot establish
semantic truth or calibrated hiring outcome. Do not describe passing shape
tests as evidence that the model assessed salary/sponsorship accurately.

### F4 — Model lineage is consistency evidence, not source authority
**Severity: ownership design gate.**

The prompt asks the model to repeat the full opaque `proposal_revision_id`,
public/private reference arrays, and lineage digest
(`scout_proposals.py:232-258`). `validate_proposal_output(request=...)` then
compares that object byte-for-byte with the request (`:338-349`, `:568-598`).
This rejects a changed or fabricated response, which is useful, but it does
not make the model an authority over source identity. It also creates a
fragile local-model requirement: a model that produces otherwise complete
assessment text may fail because it mistypes an opaque UUID or digest.

The durable design should have the host retain the sealed request/provenance,
validate model claims against an allowlist, and attach the authoritative
lineage after semantic output validation. If the model-facing JSON retains a
lineage field for consistency, the host must still generate and verify the
published lineage and source owner refs; never accept model-supplied bytes or
IDs as authority. F1 must be solved before this can be wired to existing
discovery/G45 sources.

### F5 — Renderer is local-only in intent but does not safely enforce that
**Severity: local UI/report gate.**

`render_proposal_markdown` validates without a request (`scout_proposals.py:475-482`),
so it checks only syntactic IDs/digests and does not bind evidence refs to the
selected request. A synthetic probe showed that text
`[synthetic](https://example.invalid/canary)` renders unchanged as an active
Markdown link. `html.escape` protects HTML angle brackets but does not remove
Markdown link syntax. If a Markdown viewer follows links, the claim that the
renderer emits “no links or remote assets” is therefore too strong.

The renderer also omits focused questions, ranking, requested actions, and all
evidence references; it is not yet the required one-page proposal/report
surface. A later local authenticated renderer should consume a host-bound DTO,
render untrusted text as plain text (or sanitize Markdown with an explicit
no-link policy), include safe textual reference IDs, and forbid remote assets,
analytics, automatic fetches, and path traversal. Explicit user clicks can be
a separate outbound action; untrusted model/posting text must not create one.

### F6 — Several malformed DTO paths escape the typed refusal boundary
**Severity: focused correctness defect.**

`ExactProposalSource.__post_init__` performs set membership before proving
`source_kind` is scalar (`scout_proposals.py:128-131`). A synthetic probe with a
list produced `TypeError: unhashable type: 'list'`, not `ScoutProposalError`.
Likewise, a request whose public source is `None` produced
`AttributeError: 'NoneType' object has no attribute 'source_kind'`
(`:183-198`). Similar non-JSON/custom Mapping values can escape through
canonical lineage digesting (`:588-598`) or final `json.dumps` binding
(`:428-442`). JSON-decoded model output is the normal path, so this is not a
private-data issue, but a public closed DTO should reject these inputs with a
stable content-free `ScoutProposalError` rather than incidental Python errors.

## What holds

- Source bytes are caller-supplied exact UTF-8 bytes with no path lookup, NUL,
  size, or digest bypass (`ExactProposalSource`); request source refs are
  immutable dataclass values and changed revisions produce different lineage.
- When a request is supplied, evidence refs must be exact selected `ref_` IDs;
  `source_fact` is restricted to public refs and `user_report` to private refs.
  This is ownership/association checking, not factual verification.
- Prompt framing labels posting content as untrusted data and denies tools,
  browsing, network, lookups, applications, Tailor, and verification. There is
  no application event, auto-submit, auto-Tailor, or provider side effect in
  this module.
- Reasoning/Markdown/truncated output is refused; the deterministic fallback
  explicitly says it is not a full proposal and is intentionally rejected by
  the complete validator.
- The implementation report's prior result—12 focused tests and clean Ruff—is
  consistent with the reviewed scope. No full suite, provider, real model,
  private workpad, install, activation, or source/test edit was performed in
  this review.

## Classification and next gates

| Item | Classification | Required owner/gate |
| --- | --- | --- |
| Exact bytes and digest checks | Holds as pure boundary | Preserve; bind to committed owner refs |
| Discovery/G45/native mapping into proposal sources | **Actual blocker** | Host-owned resolver/source descriptor; no fabricated IDs |
| Local model endpoint/digest/no hosted fallback | Deferred caller gate | Factory/config + `OllamaLocalAdapter` integration |
| Registered invocation role | Deferred but currently unresolved | Register reviewed role extension or select an existing approved role |
| Semantic salary/sponsorship/fit truth | Not claimed by this module | Local assessment policy, evidence checks, uncertainty UX |
| Minimum complete proposal utility | **Output gate** | Require meaningful sections before `status=complete` |
| Local HTML/Markdown report safety and references | **Renderer gate** | Bound request, no active remote links/assets, show safe refs/questions |
| Application/Tailor/UI/journal/projection | Intentionally deferred | Separate authority/integration work; no auto action here |

The smallest safe next implementation is a caller-owned authenticated source
mapping plus host-owned lineage wrapper, followed by local adapter binding and
an output/report gate. Do not solve the mismatch by weakening exact digest
checks, accepting fabricated model IDs, or claiming that the current pure
tests prove proposal semantics.
