# SCOUT R2 Tailor/document implementation handoff

Status: bounded R2 service implemented with injected model and journal seams;
focused tests use synthetic/injected paths only (no provider/network/private
data), and no application action, activation, or shared-file edit occurred.
This is not whole-Scout acceptance and does not certify semantic factuality,
ATS scores, source authority, or a final application.

## Owned implementation

The lane adds:

- `src/gigai/scout_tailor_selection.py`: immutable `TailorSource`,
  `TailorAnswer`, `TailorProposal`, and `TailorSelection` DTOs; exact caller-supplied bytes and
  `sha256:` digests; compact source handles; strict opportunity/snapshot and
  record/revision identity shape; canonical conversion to the existing
  `scout-tailoring-request:1` contract; and
  `build_local_tailor_invocation(...)`, plus `hydrate_discovery_posting_source`,
  `hydrate_g45_source`, and `hydrate_saved_proposal` wrappers around the real
  pinned discovery/G45/R1 proposal readers.
- `src/gigai/scout_documents.py`: immutable `DocumentRevision`,
  `SourceLineage`, and `FinalDocumentSelection`; bounded deterministic checks
  through the existing `scout_checks.check_document`; exact-byte materializing
  below a caller-owned private root; generated bundle validation; and a
  plaintext-only escaped report renderer.
- `src/gigai/scout_tailor_cli.py`: intentionally tiny `tailor digest` and
  `tailor canonical` commands. They accept supplied JSON/base64 values, never
  read arbitrary paths, invoke a model, resolve a source, or submit an action.
- `src/gigai/scout_tailor_execution.py`: one explicit call through the public
  `ModelInvocationPort`, requiring caller-provided local permission and
  rejecting non-bundle/reasoning-only/oversize responses; it performs no
  fallback or journal write.
- `src/gigai/scout_document_records.py`: journal-backed immutable document
  revision and final-selection records/readers, replay-safe by exact revision
  or selection bytes, using the existing `private_record_revised` transition.
- `src/gigai/schemas/scout-tailor-selection-v2.schema.json`,
  `scout-document-revision-v1.schema.json`, and
  `scout-document-selection-v1.schema.json`: strict, uniquely named domain
  schemas. They are not added to the shared registry/inventory in this lane;
  R4 must register and inventory them deliberately.

## Cross-lane contract and integration seam

The host resolves an authenticated discovery posting capture and private
records first, then supplies exact immutable bytes plus their actual identity
descriptors to `TailorSource`/`TailorAnswer`. `identity` is opaque and
preserved, not invented by this helper; compact `source_id` values are local
handles and do not prove journal authority. The host must use the real
discovery/private readers, bind a journal Run/goal/input lineage after these
helpers validate, and retain the exact selector JSON, input bytes/digests,
request bytes/digest, and resulting revision bytes/digests.

`hydrate_saved_proposal(...)` uses R1 `read_proposal_revision(...)` and binds
the committed assessment map to exact canonical bytes; it never accepts a
model-supplied proposal ID as authority. The two source hydration functions
likewise require caller-held pinned resolver state and preserve actual
descriptor families/refs.

`build_tailoring_request` produces the existing request-v1 JSON shape and
validates it with `gigai.scout_tailoring.validate_tailoring_request`; selected
answer revisions are retained in the selection envelope and source lineage,
not silently looked up or replaced with latest values. `build_local_tailor_invocation`
constructs an existing `InvocationRequest` with role `reviewer`, bounded text,
and `reasoning_effort="none"`; the parallel runtime caller must enforce the
registered local-only target, configured identity/digest checks, and no hosted
fallback. `execute_tailor(...)` invokes only the injected public
`ModelInvocationPort` after the caller supplies `local_allowed=True`; target
configuration and loopback/local policy remain the runtime caller's
responsibility. It binds the exact request and returns bounded output bundle
bytes plus output digest and advisory checks; it does not silently strip
think/reasoning text.

`prepare_document_revision` is the journal adapter seam: it returns exact
bytes, digest, source lineage, parent revision (for iteration), and advisory
check output. The R4 host writes a journal revision using its existing
authority/event contract. `materialize_document_revision` is only a bounded
caller-owned private-root convenience and is not a journal writer. The user
must call `select_final_documents` explicitly; there is no Tailor/application
side effect or applied state.

## DTO examples

Selection JSON (bytes remain in the trusted host-side descriptors):

```json
{"selector_version":"scout-tailor-selection:2","opportunity":{"opportunity_id":"opportunity_<32 lowercase hex>","snapshot_id":"snapshot_<32 lowercase hex>"},"proposal_ref":null,"answers":[],"requested_outputs":["resume"],"source_roles":[{"source_id":"posting_1","purpose":"posting"},{"source_id":"candidate_1","purpose":"candidate_evidence"}],"requested_by":{"kind":"operator","id":"local-user"}}
```

Revision JSON (returned by `DocumentRevision.to_json()`):

```json
{"revision_version":"scout-document-revision:1","opportunity":{"opportunity_id":"opportunity_<32 lowercase hex>","snapshot_id":"snapshot_<32 lowercase hex>"},"document_kind":"resume","record_id":"record_<uuidv4>","revision_id":"revision_<uuidv4>","content_sha256":"sha256:<64 lowercase hex>","source_lineage":[{"source_id":"posting_1","content_sha256":"sha256:<64 lowercase hex>","identity":{}}],"checks":{},"parent_revision_id":null}
```

Final selection is the strict `scout-document-selection:1` DTO with exact
document kinds, record/revision IDs, content digests, opportunity/snapshot,
and operator `selected_by`. It is a recommendation/selection record, never an
application submission.

## Evidence

Focused synthetic lane command:

```text
rtk .venv/bin/pytest -q tests/test_scout_r2_document_records.py tests/test_scout_r2_tailor.py
20 passed in 2.79s
```

Focused lint command:

```text
rtk ruff check src/gigai/scout_tailor_selection.py src/gigai/scout_documents.py src/gigai/scout_tailor_cli.py src/gigai/scout_tailor_execution.py src/gigai/scout_document_records.py tests/test_scout_r2_document_records.py tests/test_scout_r2_tailor.py
```

Result: clean (`[]`, under one second). Tests cover journal/public-path
publication and replay, all three output sets,
immutable revised answer lineage, exact digest/refusal, untrusted posting text
remaining data, local reviewer request construction, generated bundle checks,
iterative parent revisions, private-root materialization, explicit final
selection, hostile/malformed container inputs, and no application action.

The tests use synthetic actual-shaped UUIDv4-style record/revision identities
and opaque source descriptors; they do not prove public journal authority or a
live local runtime. R4 remains responsible for real discovery/private reader
hydration, journal event registration, schema registry/inventory updates,
local runtime dispatch, and end-to-end integration tests. Unsupported
experience/metrics are not promoted by this lane; semantic claims remain
model-declared and require the existing tailoring packet evidence contract.
