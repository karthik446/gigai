# SCOUT R2 completion handoff

R2 now has a bounded service path beyond pure DTOs:

1. R4 resolves a completed discovery posting, G45/private records, selected
   answer revisions, and an optional proposal with the existing pinned readers.
2. R2 `hydrate_discovery_posting_source`, `hydrate_g45_source`, and
   `hydrate_saved_proposal` preserve those exact bytes/digests and opaque
   authority descriptors; compact source handles are not authority IDs.
3. R4 verifies the approved Tailor graph/Run operation and local-only target,
   then calls `build_tailoring_request` and `execute_tailor` with an injected
   existing `ModelInvocationPort` and explicit `local_allowed=True`.
4. R2 strictly validates the returned bundle using existing framing and
   deterministic advisory checks. It rejects malformed/truncated/reasoning-
   only/oversize output and never silently removes text.
5. R2 prepares each immutable `DocumentRevision`, then
   `record_document_revision` commits exact Markdown plus a closed descriptor.
   R4 supplies authenticated invocation provenance and journal Run linkage.
6. After explicit user review, `select_final_documents` and
   `record_final_selection` commit the exact final selection. No application,
   applied state, Tailor action, or auto-start transition occurs.
7. R3 can use `read_document_revision` and `read_final_selection`; each reader
   authenticates committed artifacts, exact digests, opportunity/snapshot, and
   source lineage before returning data.

## Focused evidence

```text
rtk .venv/bin/pytest -q tests/test_scout_r2_document_records.py tests/test_scout_r2_tailor.py
20 passed in 2.79s

rtk ruff check src/gigai/scout_tailor_selection.py src/gigai/scout_documents.py src/gigai/scout_tailor_cli.py src/gigai/scout_tailor_execution.py src/gigai/scout_document_records.py tests/test_scout_r2_document_records.py tests/test_scout_r2_tailor.py
[]
```

The persisted synthetic test publishes two Markdown/JSON revision artifacts,
reads exact bytes back through the journal public path, records both requested
documents, and replays both revision and final-selection calls with
`created=False`. Pure tests cover resume-only, cover-only, both, revised
question answers, parent-capable revisions, source digest/refusal, untrusted
posting text, explicit local-port execution, strict bundle validation,
hostile/malformed containers, private-root materialization, and no action side
effects.

## Remaining bounded integration work

R4 must register the three new domain schemas and CLI/service entry point,
bind the approved Tailor graph and Run/goal effect, connect the configured
local runtime target, and supply real public discovery/private reader outputs.
R3 owns report/projection/application surfaces. This completion is a working
R2 service lane with synthetic persistence evidence, not whole-Scout or live
provider acceptance; no provider, model download, network, real private data,
or activation was used.
