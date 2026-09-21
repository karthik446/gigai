# SCOUT private proposal corrections — F1, F3, F4, F5, F6

Date: 2026-09-11  
Scope: `src/gigai/scout_proposals.py` and `tests/test_scout_proposals.py` only.

## Disposition

This correction keeps proposal construction pure and caller-owned. It accepts
exact selected UTF-8 bytes plus an immutable, closed provenance descriptor; it
does not resolve paths, read journals, call a model/provider, or perform a
Tailor, resume, application, network, or other action. The descriptor carries
actual source-family identities rather than inventing a `ref_` plus
`revision_` pair for every source.

The public posting source is one `scout_discovery_posting` bundle containing the
selected opportunity/snapshot identities and the actual discovery `run_id`,
`receipt_id`, `checkpoint_id`, `run_ref`, `receipt_ref`, `checkpoint_ref`, and
`posting_ref` artifact references. Private sources use the closed families
`g45_reference` (`reference_id` plus `snapshot_ref`), `g45_run_input`
(`run_input_id` plus `snapshot_ref`), or `scout_record` (`record_id`,
`revision_id`, native kind, scope, and `blob_ref`). Every owner artifact's
digest and byte size must match the exact caller-supplied bytes; family IDs,
artifact paths, scope, native kind, enum values, and source handles are bounded
and validated. The module does not claim that a DTO itself authenticates a
journal authority: the host must authenticate and select these descriptors.

## Model-facing and host-facing DTOs

Public pure seam (no lookup or side effect):

```python
build_proposal_prompt(request: ScoutProposalRequest) -> str
build_invocation_request(request: ScoutProposalRequest, *, target_name: str,
    endpoint_name: str, model: str,
    target_capabilities: frozenset[str] = frozenset({"text"}),
    max_output_tokens: int = 1600) -> InvocationRequest
validate_proposal_output(output: Mapping[str, object] | bytes | str, *,
    request: ScoutProposalRequest | None = None) -> ProposalValidationReport
validate_and_bind_proposal(output: Mapping[str, object] | bytes | str, *,
    request: ScoutProposalRequest, proposal_revision_id: str) -> dict[str, object]
limited_facts_fallback(request: ScoutProposalRequest) -> dict[str, object]
render_proposal_markdown(proposal: Mapping[str, object], *,
    request: ScoutProposalRequest) -> str
```

The host-bound DTO returned by `validate_and_bind_proposal` is the validated
model object with these additional host-owned fields (the model must not supply
them):

```json
{
  "proposal_revision_id": "revision_<UUIDv4>",
  "input_lineage": {
    "public_source": "<exact selected descriptor>",
    "private_sources": ["<exact selected descriptors in caller order>"],
    "lineage_sha256": "sha256:<canonical digest>"
  }
}
```

`ProposalSource(handle, purpose, family, identity, content,
content_sha256)` is the source constructor. Handles are local compact
`source_1` through `source_999` aliases only; they are not durable authority
IDs. `ScoutProposalRequest(posting, private_sources)` permits one selected
posting bundle and ordered private preference/experience/answer sources. Source
identity is JSON-normalized, canonical-validated, and recursively frozen at
construction; `lineage()` returns a host-side descriptor and digest for later
journal/Run linkage without replacing source authority.

`build_proposal_prompt(request)` includes source content as untrusted data and
the compact handles only. The prompt requires a complete JSON assessment and
disallows tools, network, lookup, URLs, verification, chain-of-thought, resume
drafting, Tailor, and application actions. `build_invocation_request` creates
the existing `InvocationRequest` using the already-registered `reviewer` role;
the caller still owns accepted local routing, endpoint/model identity, digest,
proxy/redirect policy, and no-fallback enforcement. If the existing role is
not available, construction fails with `invocation_role_unavailable` rather
than silently registering or falling back to another role.

The model JSON is deliberately assessment-only:

```json
{
  "schema_version": "1.0",
  "kind": "scout-private-proposal",
  "status": "complete",
  "fit_reasons": [{"text": "...", "source_type": "model_assessment", "evidence_handles": ["source_1"]}],
  "hard_blockers": [],
  "unknowns": [{"text": "...", "source_type": "model_assessment", "evidence_handles": ["source_1"]}],
  "preference_rejection_reason": null,
  "proposed_resume_focus": {"state": "focus", "items": []},
  "focused_experience_questions": {"state": "questions", "items": []},
  "ranking": {"ordinal_fit": 3, "rationale": {"text": "...", "source_type": "model_assessment", "evidence_handles": ["source_1"]}, "meaning": "explainable_fit_only_not_hiring_probability"},
  "requested_user_actions": ["keep_for_review"]
}
```

The validator rejects unknown handles, malformed family roles, public
`source_fact` claims attached to private handles, private `user_report` claims
attached to the posting, unsupported enums, extra keys, missing/truncated or
reasoning-only output, overlong text/lists, and incomplete sections. A
`complete` result must contain meaningful fit assessment and explicit focus and
question states. Blockers and unknowns may both be empty when selected
evidence supports no known concern; a hard rejection may use
`not_applicable` or `none_needed`, but exactly one textual explanation is
required; irrelevant focus or questions are not manufactured. Salary,
sponsorship, location, employment, metrics, verification, and factuality stay
unknown when the selected content says unknown/notprovided/declined. The
source-type labels and fit ranking are model declarations and association
checks, not proof of semantic truth, calibrated score, or hiring probability.

`validate_and_bind_proposal` validates this model subset first, then the host
adds its authenticated `proposal_revision_id` and exact `input_lineage`. A
model-supplied revision, UUID array, digest manifest, or lineage is an extra
key and is rejected. Changed selected bytes/descriptors produce a different
host lineage; stale invocation association and persistence/replay checks remain
the caller's responsibility.

`limited_facts_fallback` is a separate `limited_facts_fallback` DTO explicitly
labelled **NOT a full proposal**. It is not accepted by the complete validator
and recommends only user-visible choices; it cannot trigger those choices.

## Rendering and integration seam

`render_proposal_markdown` accepts only the host-bound validated DTO and the
same request. It prints all assessment sections, question text, ordinal
ranking/rationale, requested actions, and compact evidence handles. Untrusted
text is escaped into inline code so Markdown links, images, autolinks, HTML,
and remote assets remain inert; no URL is fetched and no link is emitted as
active markup. Private local content may include PII because local privacy is
the caller boundary, not a detector authorization; this renderer is not an
export path.

The next integration owner should resolve and authenticate the discovery/G45/
native source authorities, seal the request envelope, invoke the accepted local
adapter through the existing `ModelInvocationPort`, recheck target/model
identity before and after the call, and journal the host-bound proposal
revision plus lineage. A private journal projection/Run linkage should store
the returned host DTO and exact selected descriptors, while UI/HTML and any
Tailor/application action remain separate explicitly authorized operations.

## Synthetic evidence

Fixtures use actual-shaped completed discovery output, G45 reference/run-input
identities, and native `record_`/`revision_`/`jsl_blob`-equivalent artifact
descriptors with synthetic bytes only. Focused verification on 2026-09-11:

```text
rtk .venv/bin/pytest -q tests/test_scout_proposals.py
35 passed in 0.12s

rtk ruff check src/gigai/scout_proposals.py tests/test_scout_proposals.py
0 findings

rtk ruff format --check src/gigai/scout_proposals.py tests/test_scout_proposals.py
2 files already formatted
```

The tests cover valid host binding, salary mismatch and unknown sponsorship
text, unknown handles and family roles, malformed IDs/digests and wrong DTO
types, public/private provenance laundering, meaningful complete-output gates,
explicit hard-rejection states, revised input lineage, hostile Markdown,
reasoning-only/scalar output, fallback separation, and absence of action
side-effects. No provider/model call, network, real private data, activation,
installation, download, full suite, or commit was performed.
