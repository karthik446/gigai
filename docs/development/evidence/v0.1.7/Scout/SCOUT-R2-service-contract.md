# SCOUT R2 service contract

This is the concrete R2/Luna service seam for the current dirty worktree. It
is bounded private Tailor preparation and document selection, not whole-Scout
acceptance. R4 owns CLI registration, schema registry/inventory registration,
and any graph/Run integration; R3 owns projections and applications.

## Source and selection types

```python
@dataclass(frozen=True)
class TailorSource:
    source_id: str                 # compact local handle, not authority
    purpose: Literal["posting", "candidate_evidence"]
    content: bytes                 # exact selected UTF-8 bytes
    content_sha256: str            # sha256:<64 lowercase hex>
    identity: Mapping[str, object] # host descriptor from a real reader

@dataclass(frozen=True)
class TailorAnswer:
    record_id: str                 # actual record_<uuidv4>
    revision_id: str               # actual revision_<uuidv4>
    question_ids: tuple[str, ...]
    content: bytes
    content_sha256: str
    identity: Mapping[str, object]

@dataclass(frozen=True)
class TailorProposal:
    record_id: str
    revision_id: str
    content: bytes                 # canonical committed assessment map
    content_sha256: str
    identity: Mapping[str, object]

@dataclass(frozen=True)
class TailorSelection:
    opportunity_id: str             # opportunity_<32 lowercase hex>
    snapshot_id: str                # snapshot_<32 lowercase hex>
    requested_outputs: tuple[Literal["resume", "cover_letter"], ...]
    sources: tuple[TailorSource, ...]
    proposal: TailorProposal | None
    answers: tuple[TailorAnswer, ...]
    requested_by: str
```

`hydrate_discovery_posting_source(...)` uses the existing completed-discovery
resolver under a caller-held writer. `hydrate_g45_source(...)` uses the
existing pinned G45 resolver and snapshot. `hydrate_saved_proposal(...)` calls
R1 `read_proposal_revision(...)`, canonicalizes only its already committed
assessment map, and binds the exact revision bytes/digest; caller input cannot
replace a reader result. These DTOs do not themselves prove journal authority.

## Service callables

```python
build_tailoring_request(
    selection: TailorSelection, *,
    requirements: Sequence[Mapping[str, object]] = (),
    claim_evidence: Sequence[Mapping[str, object]] = (),
    gaps: Sequence[Mapping[str, object]] = (),
    questions: Sequence[Mapping[str, object]] = (),
    posting_terms: Sequence[str] = (),
) -> bytes

build_local_tailor_invocation(
    selection: TailorSelection, request_bytes: bytes, *,
    target_name: str, endpoint_name: str, model: str,
    target_capabilities: frozenset[str], max_output_tokens: int = 1024,
) -> InvocationRequest

execute_tailor(
    selection: TailorSelection, request_bytes: bytes, *,
    port: ModelInvocationPort, target_name: str, endpoint_name: str,
    model: str, target_capabilities: frozenset[str], local_allowed: bool,
    max_output_tokens: int = 1024,
) -> TailorExecutionResult

prepare_document_revision(
    selection: TailorSelection, document_kind: str, record_id: str,
    revision_id: str, content: bytes, *, parent_revision_id: str | None = None,
    checks_options: Mapping[str, object] | None = None,
) -> DocumentRevision

record_document_revision(
    *, resolved: ResolvedWorkpad, revision: DocumentRevision,
    invocation: Mapping[str, object], operation_key: str,
) -> DocumentRecordResult

record_final_selection(
    *, resolved: ResolvedWorkpad, selection: FinalDocumentSelection,
    invocation: Mapping[str, object], operation_key: str,
) -> SelectionRecordResult
```

`validate_generated_bundle(bundle, selection)` strictly decodes the existing
Tailor bundle framing and runs bounded deterministic checks. Reasoning-only,
truncated, oversize, malformed, and wrong-output bundles fail; text is never
silently stripped. `select_final_documents(...)` requires exact coverage of
the explicitly selected output set and an operator identity.

## Journal records and readers

The document service publishes two exact artifacts under the caller-resolved
private workpad using the existing `private_record_revised` transition (no new
transition is invented here):

```text
records/scout-documents/<record_id>/revisions/<revision_id>/<kind>.md
records/scout-documents/<record_id>/revisions/<revision_id>/record.json
records/scout-documents/selections/<opportunity_id>_<snapshot_id>.json
```

The JSON record contains `scout-document-revision:1`, exact opportunity and
snapshot, document/record/revision IDs, content digest, source-lineage
handles/digests, deterministic advisory checks, optional parent revision, and
an exact `content_ref`. Handoff `artifact_refs` bind every JSON/Markdown byte;
the reader re-authenticates both via `read_committed_artifact`. Repeated
revision/selection calls compare exact committed bytes and return
`created=False`; conflicting bytes are rejected. No application or applied
state is written.

## Runtime and integration limits

`execute_tailor` calls only an injected existing `ModelInvocationPort`; it
requires explicit `local_allowed=True`, uses reviewer role only as the
existing transport role, and returns exact bounded bundle/provenance. The R4
caller must prove the registered Tailor graph/Run operation and configured
numeric-loopback local target before calling it; a reviewer role does not by
itself authorize Tailor execution. No hosted fallback or network is present.

The current tests use synthetic actual-shaped UUIDv4 identities and temporary
journal workpads. They prove helper/service behavior and journal publication
under injected/local fixtures, not authenticated real discovery/private source
authority, installed model readiness, or an integrated Scout journey.
