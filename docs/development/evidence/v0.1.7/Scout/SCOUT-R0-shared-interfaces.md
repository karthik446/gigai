# SCOUT R0 shared interface sheet

Status: contract freeze for the next implementation lanes, 2026-09-11.  This
sheet distinguishes fields and readers that exist today from the smallest
additions R1--R3 may propose.  It does not approve a user Gig, create an
application transition, or claim that the proposed DTOs are already shipped.

## Non-negotiable authority rules

The journal is the authority; `state.sqlite` is a rebuildable query view.
Every cross-lane selector names a closed identity and exact committed bytes or
an authenticated artifact reference.  A model response cannot establish an
opportunity, private revision, answer, document, target, approval, or lineage.
Selectors are host-resolved under one pinned `JournalSnapshot` whenever a
caller needs more than one source.  No lane resolves “latest”, follows
historical ancestry recursively, invents `ref_` IDs for native data, or uses a
working-tree/sidecar base64 value as authority.

## Existing records and readers

| Domain | Existing authoritative shape | Existing authority path |
| --- | --- | --- |
| Discovered opportunity/posting | Discovery packet `postings[]` contains `opportunity_id`, `snapshot_id`, employer/title, `source` (source ID, locator, retrieved/published dates, status, capture/evidence refs), and `facts`. The resolver returns `posting_bytes` plus `run_ref`, `receipt_ref`, `checkpoint_ref`, and `posting_ref`. | `resolve_discovery_posting_input_from_journal(resolved, selector)` in `scout_posting_inputs.py`; selector is exactly `{family: scout_discovery, run_id, receipt_id, output_kind, opportunity_id, snapshot_id}`. |
| Saved private revision | `records/{record_id}/revisions/{revision_id}.json`, `private-record-revision:1`: project/Gig scope, kind, privacy class, origin, actor, content and parent revision. Content bytes are referenced by its exact `snapshot_ref`. | `private_records.read_record`, `list_revisions`, `select_exact_inputs`, and `JournalSnapshot`/`read_committed_artifact`. Existing native kinds include `profile_preferences` and `experience_qa`; answer questions are `payload.questions[]`. |
| Saved answer | An `experience_qa` native revision question has `question_id`, `state` (`answered`, `missing`, `declined`, `not_applicable`), optional answer, and provenance. The native validator rejects answered questions without provenance and rejects answer text for unanswered states. | `native_records` content validation plus `private_records` committed revision reader. There is no current proposal-specific answer association. |
| Proposal assessment result | Host result currently has `kind: scout-proposal-execution`, `invocation_id`, `invocation_record_sha256`, `request_sha256`, `input_lineage`, `sealed_journal_head`, `status`, `proposal` or bounded `error`, and optional response artifact. Pure model fields are structurally validated; host lineage is not model-supplied. | `validate_proposal_output` and `validate_and_bind_proposal` in `scout_proposals.py`; execution result is written beneath `runs/{run_id}/scout-proposals/{invocation_id}/result.json`. The R0 receipt reader now authenticates its invocation evidence separately. |
| Tailor request and selected sources | The accepted request bytes use `schema_version: scout-tailoring-request:1`, `requested_outputs`, `source_roles`, `requirements`, and `claim_evidence`. A sealed plan request descriptor is exactly `{input_index, run_input_id, record_ref, snapshot_ref}`. Source roles are `{posting, candidate_evidence}` and source entries are `{source_id, input_index}`. | `external_recording._seal_tailoring_request`, `_tailoring_request_bytes`, `_tailoring_source_bytes`, and `validate_tailoring_request`. Discovery posting is an admitted posting source; canonical G45 request input remains the only Tailor request source. |
| Application event | Existing event includes `opportunity_ref`, event kind, occurred time/timezone, `document_refs[]` of record/revision/content digest, supersession, direct operator request evidence, and requested/payload digests. | `application_events.record_application` and `_validate_event`; requires explicit confirmation and does not prove a Tailor/document workflow by itself. |

## Small proposed cross-lane additions

These are host-owned interface proposals, not silently added schemas. R1--R3
should use existing private revision/journal envelopes where possible and
request a versioned schema only when a new field cannot fit without weakening
an existing strict contract.

### Opportunity reference

R1 may expose a read DTO (not a second record) with this exact closed shape:

```json
{
  "family": "scout_discovery_posting",
  "opportunity_id": "opportunity_<32 lowercase hex>",
  "snapshot_id": "snapshot_<32 lowercase hex>",
  "run_ref": {"path": "runs/...", "content_sha256": "sha256:...", "media_type": "application/json", "size_bytes": 1},
  "receipt_ref": {"path": "runs/...", "content_sha256": "sha256:...", "media_type": "application/json", "size_bytes": 1},
  "checkpoint_ref": {"path": "runs/...", "content_sha256": "sha256:...", "media_type": "application/json", "size_bytes": 1},
  "posting_ref": {"ref": {"path": "runs/...", "content_sha256": "sha256:...", "media_type": "text/plain", "size_bytes": 22}, "content_sha256": "sha256:..."}
}
```

The ellipses are explanatory placeholders only; production values must come
from the resolver and pass its path/digest/receipt checks. Acquisition status
and exclusion reason remain public research facts; salary, sponsorship,
location preference, and fit/rejection are private assessment data.

### Proposal revision

The host needs an immutable proposal revision association, rather than asking
the model to repeat provenance. The minimum proposed value is:

```json
{
  "opportunity": {"opportunity_id": "opportunity_<hex>", "snapshot_id": "snapshot_<hex>"},
  "assessment": {"record_id": "record_<uuid4>", "revision_id": "revision_<uuid4>", "content_sha256": "sha256:<64 hex>"},
  "input_revisions": [{"record_id": "record_<uuid4>", "revision_id": "revision_<uuid4>", "purpose": "preferences", "content_sha256": "sha256:<64 hex>"}],
  "invocation": {"run_id": "run_<uuid4>", "goal_id": "goal_<uuid4>", "invocation_id": "inv_<uuid4>", "record_sha256": "sha256:<64 hex>"},
  "method": {"kind": "ollama_local", "target": "local-proposal", "configured_digest": "sha256:<64 hex>"}
}
```

The `assessment` record should use the existing private revision storage with
a deliberately reviewed kind/contract extension; do not repurpose
`selected_conversation` or fabricate a native `ref_`. Reassessment appends a
new revision with new input revision IDs and invocation identity; it never
overwrites an older proposal. Until R1 owns that extension, the existing Run
result is the only durable proposal output.

### Answer association

Answers are selected explicitly and privately, with no inference from a
record's kind:

```json
{
  "answer_ref": {"record_id": "record_<uuid4>", "revision_id": "revision_<uuid4>", "content_sha256": "sha256:<64 hex>"},
  "question_ids": ["question_example_1"],
  "purpose": "answer"
}
```

The host must verify the committed revision is `experience_qa`, the named
question IDs exist, and its exact purpose is `answer`; unknown or unanswered
questions remain explicit uncertainty. This association is proposed for R1
and must be versioned if the existing private revision envelope cannot carry
it without changing its strict meaning.

### Versioned Tailor selectors and final documents

R2 should redeem an explicit selector envelope, retaining the existing v1
Tailor request descriptor unchanged:

```json
{
  "selector_version": "scout-tailor-selection:2",
  "opportunity": {"opportunity_id": "opportunity_<hex>", "snapshot_id": "snapshot_<hex>"},
  "proposal_ref": {"record_id": "record_<uuid4>", "revision_id": "revision_<uuid4>", "content_sha256": "sha256:<64 hex>"},
  "answers": [{"record_id": "record_<uuid4>", "revision_id": "revision_<uuid4>", "question_ids": ["question_example_1"], "content_sha256": "sha256:<64 hex>"}],
  "requested_outputs": ["resume", "cover_letter"],
  "source_roles": [{"source_id": "posting_1", "purpose": "posting"}, {"source_id": "experience_1", "purpose": "candidate_evidence"}],
  "requested_by": {"kind": "operator", "id": "local-user"}
}
```

`requested_outputs` is a closed set (`resume`, `cover_letter`) and must not
silently imply both. A final selection is another host-owned immutable value,
not model text:

```json
{
  "selection_version": "scout-document-selection:1",
  "opportunity": {"opportunity_id": "opportunity_<hex>", "snapshot_id": "snapshot_<hex>"},
  "documents": [{"document_kind": "resume", "record_id": "record_<uuid4>", "revision_id": "revision_<uuid4>", "content_sha256": "sha256:<64 hex>"}],
  "selected_by": {"kind": "operator", "id": "local-user"}
}
```

R2 must validate each revision and exact bytes before checks or any future
application event. No selection auto-approves a document or submits it.

## Journal-to-SQLite report DTO

The existing projection has `scout_records`, `scout_operations`, and
`scout_meta` payload tables plus `context.json`, all rebuilt from committed
records. R3 should add query views only after the journal shapes exist; it
should not make SQLite a second authority. The smallest report row can be:

```json
{
  "opportunity_id": "opportunity_<hex>",
  "snapshot_id": "snapshot_<hex>",
  "posting_ref": "runs/.../posting.json",
  "acquisition_state": "considered",
  "acquisition_exclusion": null,
  "private_proposal_revision": "revision_<uuid4>",
  "proposal_status": "complete",
  "question_count": 2,
  "tailor_selection_revision": null,
  "application_event_ids": [],
  "source_run_ids": ["run_<uuid4>"],
  "journal_head": "<commit sha>"
}
```

This is a local authenticated report DTO; its HTML may show private fit,
salary, sponsorship, questions, and evidence. It must escape text, allow only
relative committed links, and load no remote resources or analytics. Public
acquisition rows must not receive private assessment fields. A stale view is
rebuilt against its recorded `journal_head` and marked stale while rebuilding.

## File ownership and sequencing

- **R1:** `scout_discovery`, proposal revision/answer association, and focused
  domain tests. It calls the posting resolver and private revision readers; it
  does not edit Tailor or application events.
- **R2:** Tailor selector version, document revisions/checks, and document
  selection tests. It calls R1's immutable proposal/answer refs and existing
  `external_recording` request/source validators; it does not change discovery
  authority.
- **R3:** projection/report DTOs, local HTML/report rendering, and application
  linkage tests. It reads journal records and selected document refs; it does
  not write authority directly to SQLite.
- **Integration owner:** shared `run.py`, `journal.py`, schema registry,
  `cli.py`, source inventory, and `gig.py` changes. Other lanes submit narrow
  requests rather than editing these files concurrently.

The release graph remains explicit: R0 receipt repair and this interface sheet
precede R1--R3; their combined journey is still required before R4. Daily
scheduling, background agents, UI interview rounds, provider/network work, and
application submission remain separate gates.
