# P2-AUD-02 — Map Scout operations, records, and public callers

**Status:** Complete; output in [`../evidence/P2-AUD-02-scout-operation-map.md`](../evidence/P2-AUD-02-scout-operation-map.md). Terra-reviewed: Terra `ctx_7ff479b90fc9` → Luna fix `ctx_461e730dc554` → Terra pass `ctx_57133c5bc2bb`. **Owner role:** Luna/max audit writer;
root owns cross-packet reconciliation. **Review:** Terra independently checks
the completed map.

## Jira-style ticket

**User problem:** Scout already has journal-backed acquisition, proposal,
document, application, Run, projection, and report code, but those seams were
delivered in separate slices. Later packets could mistake a reader/cache for
authority, conflate assessment with tracking, or invent a duplicate CLI/UI
write path without first mapping actual producers, consumers, and gaps.

**Intended behavior/outcome:** Produce one source-verified operation and caller
map for the first v0.1.8 vertical slice. It identifies what exists today,
which records and schemas are authoritative, which public CLI/Run/UI callers
read or write them, where acquisition visibility is independent of assessment,
and which capabilities are missing or conflicting. It carries S09's discovery,
fetch, change-detection, privacy, storage-rights and provider caveats as
explicit design inputs rather than claiming a live connector.

**Scope:** Read-only source/schema/test/evidence audit of acquisition/save/
pending visibility, assessment/proposals, resume selection/tailoring,
explicit tracking/application events, shared persisted records, journal
readers/projections/reports, and public CLI/Run/UI callers.

**Non-goals:** No feature implementation, schema/storage migration, UI build,
provider search/fetch, scheduler/daemon, model call, private data, application
submission, broad suite, or replacement of the R0 authority rules.

**Tasks:**

1. Start from `SCOUT-R0-shared-interfaces.md`, then verify each claimed shape
   against the current source, schemas, and v0.1.7 baseline evidence rather
   than treating that historical sheet as current proof.
2. Trace acquisition from supplied/public discovery input through save,
   progress/status/resume, posting resolution, Run receipts and report rows;
   state clearly whether an operation discovers/fetches, persists, projects,
   or only accepts already-acquired rows.
3. Trace assessment/proposal from authenticated posting and private revision
   selection through local invocation, result/revision publication, answer
   association, Tailor selection, and document revisions. Keep assessment
   state and user tracking state in separate rows and transitions.
4. Trace explicit application/tracking events, correction/idempotency rules,
   document redemption and opportunity authority into CLI/history/status and
   projection/report/UI paths; identify unresolved opportunity or caller gaps.
5. Build a producer/consumer ledger and caller matrix with exact paths,
   schemas, revision/provenance, failure/recovery, entry points, and evidence.
   Mark every item existing, partial, missing, conflicting, or proposed.
6. Record the smallest non-feature amendments needed for the later freeze,
   with alternatives and disposition questions instead of silently expanding
   a strict schema or banning all amendments.

**Acceptance:**

- Every roadmap-required concern has at least one source/schema owner or an
  explicit “missing/partial” disposition: acquisition/save/progress/pending,
  assessment/proposals, selected resume, explicit tracking, shared records,
  CLI/Run/UI readers and writers.
- The map proves the distinction between assessment status (`new`, `pending`,
  `running`, `succeeded`, `failed`, `skipped` where supported) and explicit
  tracking/application status; no resume finalization, tailoring, proposal, or
  ranking is described as an application event.
- Journal artifacts are authority and SQLite/report/HTML are derived views;
  each reader path states its snapshot/digest/provenance checks and stale or
  missing-data behavior.
- S09's ATS-watchlist and general-search options are recorded as proposed
  acquisition capabilities with unmeasured freshness/storage/ToS boundaries;
  no live provider route is implied by a source row or tool schema.
- A Terra reviewer can follow each caller edge and reproduce the map from
  cited source/evidence without relying on a broad test run.

**Required evidence:** Planned report at
`docs/development/v0.1.8/phase-2/evidence/P2-AUD-02-scout-operation-map.md`
and machine-readable ledger at
`docs/development/v0.1.8/phase-2/evidence/P2-AUD-02-interface-ledger.json`.
The ledger may point at existing schema files and reports; it must not create
or modify product schemas.

**Dependencies:** Independent of P2-AUD-01 for initial source inventory; use
the current tree and R0 sheet. P2-EVAL-03 may consume its operation vocabulary;
P2-FREEZE-04 requires this map. V018-01 is a downstream UI consumer, not a
prerequisite for this audit.

## Granular source and caller map

### Acquisition and immediate visibility

Inspect the following as separate responsibilities:

| Responsibility | Source/schema/caller targets | Questions to answer |
| --- | --- | --- |
| Supplied public-row persistence | `src/gigai/scout_acquisition_records.py`: `import_public_rows`, `read_public_acquisition_status`, `resume_public_acquisition`; `scout-public-import-input.schema.json`, `scout-public-import-progress.schema.json` | Which rows are accepted, deduplicated, failed, excluded, or partial; what is immutable; what exact progress/deadline/idempotency evidence exists |
| Acquisition CLI | `src/gigai/scout_acquisition_cli.py`; `src/gigai/cli.py`; `src/gigai/data/scout/gig.py` acquisition parser | Whether the command only persists already-acquired rows or performs discovery; flags, exit/error JSON, workpad scope, and offline behavior |
| Discovery Run outputs | `src/gigai/scout_discovery.py`, `scout_discovery_job.py`, `scout_posting_inputs.py`, `external_recording.py`, `run.py`; discovery schemas and R7 evidence | Which public posting/snapshot/run/receipt/checkpoint references form opportunity authority; what happens on timeout, failure, duplicate, exclusion, or restart |
| Report opportunity rows | `src/gigai/scout_report_readers.py` (`_opportunity_rows`), `scout_projection.py`, `scout_report.py`, `scout_report_cli.py` | Whether a new/pending/failed acquisition is visible before assessment; how a pinned snapshot and digest produce a view; what remains empty/partial |
| S09 capability boundary | S09 research evidence and direction doc | Which ATS feeds can be proposed as primary watchlist discovery/fetch and which general-search provider is only a future backstop; freshness, query privacy, caching/storage and ToS claims that still require authorized proof |

The map must not turn `scout_acquisition_records` into a scheduler: its module
explicitly accepts rows already acquired. Likewise, a discovery domain sidecar
or report row is not evidence that a public provider was polled in this phase.

### Assessment, proposals, and selected resume

Trace identity and lineage rather than just names:

- `scout_proposal_execution.execute_local_proposal` resolves an authenticated
  posting and selected private sources, requires the local Ollama target, writes
  invocation evidence, validates a bounded result, and deliberately does not
  create a resume, Tailor action, application state, or approval pointer.
- `scout_proposals.py` owns request/response validation and source-handle
  semantics; `scout_proposal_records.py` owns immutable proposal revision and
  answer-association records. Verify their exact schema versions, parent/revision
  behavior, input purpose, invocation identity, and retry/conflict semantics.
- `scout_inputs.py`, `private_records.py`, native record validators, and
  `scout_research*.py` define private revisions, answers, role/evidence inputs,
  and research packets. Record which are existing selectors versus merely
  proposed DTOs in R0/S08.
- `scout_tailor_selection.py`, `scout_tailoring.py`,
  `scout_document_records.py`, `scout_documents.py`, and the document schemas
  define explicit posting/candidate sources, requested outputs, revisions,
  final selection, and provenance. A selected/final document is never an
  inferred application.
- `scout_report_readers._proposal_rows` and `_document_rows` redeem committed
  revisions into a report projection; note any missing reader, source lineage,
  or current-selection limitation instead of filling it with a model claim.

For each assessment/proposal row, record whether status means model
assessment progress, proposal revision state, document lifecycle, or explicit
operator choice. Preserve `refused_or_failed`, `abstain`, `uncertain`, and
missing-input outcomes where the existing contract defines them.

### Explicit tracking and shared views

Inspect `src/gigai/application_events.py` and `application_cli.py`,
`application-event.schema.json`, `tests/test_scout09_application_events.py`,
`scout_projection._application_rows`, and report/UI templates. Capture:

- direct confirmation, event/receipt publication, operation-key retry,
  correction/supersession, UTC ordering, same-opportunity checks, document
  redemption and semantic evidence validation;
- public commands `gigai application record/history/status` and any copied
  `gig.py` wrapper or Run caller, including whether an operation is read-only,
  journal-writing, or merely rendering;
- projection cursor/journal-head behavior, current/superseded event status,
  opportunity verification, stale cache behavior, relative-link/escaping
  controls, and missing-reader behavior;
- the smallest V018-01 UI/reader contract needed to browse and update tracking
  with inference disabled, without creating a second source of truth.

### Ledger row contract and edge cases

Each row in the planned JSON ledger should include:

```text
id, behavior, status(existing|partial|missing|conflicting|proposed),
producer, consumer, input_shape, output_shape, schema_paths,
record_paths, revision_and_provenance, failure_and_recovery,
public_entry_points, view_or_authority, acceptance_evidence,
owner_role, dependencies, disposition
```

Exercise no runtime behavior here, but account for cases that later packets
must prove: duplicate posting/snapshot, missing posting bytes, stale journal
head, failed or stopped local model after acquisition, proposal input revision
change, unanswered question, selected-document digest mismatch, application
retry/correction, unresolved opportunity, report rebuild, and a user changing
tracking while assessment is absent. A row marked “proposed” must include the
smallest contract amendment and why existing records cannot carry it; “not
implemented” is an acceptable result.
