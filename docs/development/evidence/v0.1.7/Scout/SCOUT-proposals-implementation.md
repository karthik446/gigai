# SCOUT bounded private proposal builder/validator

**Date:** 2026-09-11  
**Status:** focused pure construction/validation slice; not durable SCOUT
completion, provider execution, activation, or UI integration.

## Scope and controlling decisions

This implementation follows [SCOUT-local-runtime-decision.md](SCOUT-local-runtime-decision.md)
and the revised [local proposal privacy spike](SCOUT-local-proposals-privacy-spike.md).
It implements only `src/gigai/scout_proposals.py` and its synthetic focused tests.
It does not edit adapters, configuration, schemas, native records, journal,
projection, UI, or graph inventory. The mandatory OS isolation/helper-install
requirement from the earlier spike is superseded for personal-use v0.1.7 by the
trusted identified local runtime decision; this module still makes no network or
provider call and cannot itself prove process isolation.

The module has no filesystem, journal, subprocess, network, model, or lookup
operation. A trusted caller supplies one exact selected posting, opportunity,
and capture plus selected private preference/experience/answer revisions. Each
source carries canonical `ref_` and `revision_` IDs, exact UTF-8 bytes, and an
exact imported-byte SHA-256 digest. There is no path field, latest lookup, or
mutable authority copy. A changed private revision therefore creates a new
input lineage and cannot silently reassess an old proposal.

## Public API

```python
ExactProposalSource(
    reference_id: str,       # canonical ref_<UUIDv4>
    revision_id: str,        # canonical revision_<UUIDv4>
    source_kind: Literal[
        "posting", "opportunity", "capture",
        "preferences", "experience", "answer",
    ],
    content: bytes,          # caller-supplied exact UTF-8 bytes, no path
    content_sha256: str,     # digest_imported_bytes(content)
)

ScoutProposalRequest(
    proposal_revision_id: str,
    posting: ExactProposalSource,
    opportunity: ExactProposalSource,
    capture: ExactProposalSource,
    private_revisions: tuple[ExactProposalSource, ...],
)

build_proposal_prompt(request) -> str
build_invocation_request(
    request, *, target_name, endpoint_name, model,
    target_capabilities=frozenset({"text"}), max_output_tokens=1600,
) -> InvocationRequest
validate_proposal_output(output, *, request=None) -> ProposalValidationReport
validate_and_bind_proposal(output, *, request) -> dict[str, object]
limited_facts_fallback(request) -> dict[str, object]
render_proposal_markdown(proposal) -> str
```

`build_invocation_request` only constructs the existing transport-neutral
`InvocationRequest`; it does not call `ModelInvocationPort`. The adapter/config
owner must later enforce the selected local endpoint and model identity. The
prompt requests JSON-only, complete output, no tools, no URLs, and no external
actions. Posting instructions are explicitly delimited as untrusted data.

## Private proposal JSON DTO

The model must return this closed top-level object (no extra keys):

```json
{
  "schema_version": "1.0",
  "kind": "scout-private-proposal",
  "status": "complete",
  "proposal_revision_id": "revision_<UUIDv4>",
  "input_lineage": {
    "proposal_revision_id": "revision_<UUIDv4>",
    "public_refs": [
      {"reference_id": "ref_<UUIDv4>", "revision_id": "revision_<UUIDv4>", "source_kind": "posting", "content_sha256": "sha256:..."}
    ],
    "private_refs": [],
    "input_sha256": "sha256:<canonical lineage digest>"
  },
  "fit_reasons": [{"text": "...", "source_type": "source_fact", "evidence_refs": ["ref_<UUIDv4>"]}],
  "hard_blockers": [],
  "unknowns": [{"text": "...", "source_type": "model_assessment", "evidence_refs": ["ref_<UUIDv4>"]}],
  "preference_rejection_reason": null,
  "proposed_resume_focus": [{"text": "... (not a draft)", "source_type": "model_assessment", "evidence_refs": ["ref_<UUIDv4>"]}],
  "focused_experience_questions": [{"question": "...", "why": "...", "evidence_refs": []}],
  "ranking": {
    "ordinal_fit": 1,
    "rationale": {"text": "...", "source_type": "model_assessment", "evidence_refs": ["ref_<UUIDv4>"]},
    "meaning": "explainable_fit_only_not_hiring_probability"
  },
  "requested_user_actions": ["answer_questions"]
}
```

The real `public_refs` array contains exactly posting/opportunity/capture in
that order, and `private_refs` contains the selected preference/experience/
answer revision refs in caller order. `validate_and_bind_proposal` compares
the complete lineage object to the request, so output cannot introduce a
fabricated reference or silently bind to a newer revision. Evidence refs are
also restricted to the exact `ref_` allowlist. All text, list sizes, enum
values, IDs, digests, and closed object keys are bounded.

`source_type` distinguishes `source_fact`, `user_report`, and
`model_assessment`; it is not a truth or verification claim. The prompt and
tests require unknown/notprovided/declined values to remain unknown, and do not
promote unsupported employment or metrics. Salary, sponsorship, location,
preference-relative rejection, and derived fit reasons are in the private
proposal only. An action is a recommendation: `request_tailor` never runs
Tailor, and no action creates a resume or application event.

Non-JSON, markdown/reasoning-only, `<think>` output, truncated/closed-key
failures, bad enums, invalid IDs, and fabricated refs fail with stable
content-free diagnostics. A deterministic `limited_facts_fallback` is separate
and explicitly says **NOT a full proposal**; it contains selected refs and
public fact identities only, and `validate_proposal_output` intentionally
rejects its non-complete status. The optional Markdown renderer escapes text
and emits no remote links/assets; HTML/UI and authenticated local report
integration remain later work.

## Focused synthetic evidence

`tests/test_scout_proposals.py` covers:

- a complete valid proposal with exact lineage and evidence associations;
- private salary mismatch and unknown sponsorship;
- focused user questions and optional no-reference questions;
- malformed/unsupported input refs and digest mismatch;
- malicious posting instructions treated as data, not authority;
- revised private input rejection of an old proposal while preserving old bytes;
- extra keys, bad ranking enum, reasoning-only, markdown, and truncated output;
- construction of an existing `InvocationRequest` without calling a port;
- explicit limited facts fallback and escaped local Markdown rendering.

## Verification

Focused commands run after implementation:

```text
<project venv>/bin/pytest -q tests/test_scout_proposals.py
rtk ruff check src/gigai/scout_proposals.py tests/test_scout_proposals.py
```

Observed verification: `tests/test_scout_proposals.py` passed **12 tests in
0.06s**; Ruff reported **0 issues** for both owned Python files. No full suite,
provider, model download, real private
data, network, commit, or activation was used. This is bounded offline proof
of this module only; it is not proof of local endpoint identity, OS isolation,
durable journal projection, scheduled runs, or completed SCOUT.
