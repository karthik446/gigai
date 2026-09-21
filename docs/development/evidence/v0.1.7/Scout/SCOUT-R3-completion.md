# SCOUT-R3 completion handoff

Date: 2026-09-11 (America/Denver)  
Lane: R3 / Luna  
Status: owned implementation complete; shared registration and the grouped R4 journey remain pending.

## Concrete API contract

`scout_report_readers.default_reader_set(resolved)` returns an R0-compatible
`ScoutReaderSet`. Every reader receives `(snapshot, project_id, gig_id)` and
reads only the supplied pinned `JournalSnapshot`; the resolved workpad is used
only to authenticate proposal bytes against `snapshot.head` and to run the
existing pinned discovery resolver. The concrete rows are:

- `opportunities`: host-resolved discovery postings with `opportunity_id`,
  `snapshot_id`, readable title/employer, source, and authenticated
  Run/receipt/checkpoint/posting references.
- `proposals`: R1 `records/scout-proposals/.../revisions/...json` revisions,
  validated with `validate_proposal_revision`, with assessment content,
  answer associations, source references and immutable path/digest.
- `documents`: validated R2 document revision envelopes at
  `records/scout-documents/<record>/revisions/<revision>/record.json` plus
  final selections at `records/scout-documents/selections/*.json`; missing R2
  authority remains empty.
- `runs` and `evidence`: validated external Run/checkpoint/receipt records;
  evidence is labeled `reported`, never independently verified.

`rebuild_projection(resolved=...)` now installs this production reader set by
default. Passing `readers=ScoutReaderSet(...)` remains the explicit fixture
seam for unit tests. `projection_from_snapshot` does not silently discover
fresh authority. `scout_report_cli.generate` therefore uses real committed
readers through `publish_report` and atomically publishes a generated bundle
without replacing editable UI source.

`application_events.record_application(..., opportunity_reader=...)` accepts an
optional host reader and validates opportunity/document links during the same
writer snapshot. If no reader is supplied but committed Run receipts exist,
the default committed opportunity reader is used; a missing selected
opportunity refuses the mutation. Event-only legacy Gigs remain explicit
unresolved history and do not gain fabricated opportunity authority. External
applications can have an empty `document_refs` list; R3 never invents Tailor.

## Delivered behavior

- Discovery rows are derived from succeeded v2 discovery receipts, their exact
  checkpoint output, domain sidecar and supporting capture; each posting is
  resolved with `resolve_discovery_posting_input` against the one pinned
  snapshot. Duplicate `(opportunity_id, snapshot_id)` rows collapse.
- Proposal rows read the actual R1 immutable revision path and authenticate its
  committed publication under the pinned HEAD. The generated UI shows proposal
  content (resume focus, fit reasons, blockers, unknowns and focused questions)
  plus source and revision links, escaped as text; proposals whose host
  opportunity association is absent from the same snapshot are labeled
  unresolved and never create a job row.
- Runs expose active, waiting-input, succeeded or cancelled state from
  committed records. Evidence is linked to committed checkpoint markdown and
  marked as reported. Documents are refused when the R2 envelope or supplied
  content digest is invalid.
- Application history continues to expose duplicate/corrected events and now
  receives opportunity verification from the production reader during
  projection. Invalid document references still refuse at mutation and read
  time.

## Exact verification

Commands run in the current dirty worktree:

```text
rtk python -m compileall -q src/gigai/scout_report_readers.py src/gigai/scout_projection.py src/gigai/scout_report.py src/gigai/application_events.py
rtk ruff check src/gigai/scout_report_readers.py src/gigai/scout_projection.py src/gigai/scout_report.py src/gigai/application_events.py
rtk .venv/bin/pytest -q tests/test_scout_r3_report.py -k 'not correction'
5 passed, 1 deselected in 0.19s
rtk .venv/bin/pytest -q tests/test_scout09_application_events.py -k 'no_confirm'
1 passed, 11 deselected in 2.20s
```

An attempted combined focused run was `rtk .venv/bin/pytest -q
tests/test_scout_r3_report.py tests/test_scout09_application_events.py` and
reported `6 passed, 12 failed in 2.26s`; all 12 failures were pre-existing
fixture/bootstrap failures because the current dirty schema set lacks
`src/gigai/schemas/model-invocation-v3.schema.json`, before R3 code was
entered. The report tests that do not bootstrap the missing schema passed,
and Ruff/compile checks passed. No full suite, provider, hosted service,
activation, commit, or publication was run.

## R4 integration requests

1. Register `scout_report_cli.report_group` under the shared `gigai` command
   wrapper and include `scout_report_readers.py` plus the R3 report modules in
   the package/source inventory. Use the exact existing wrapper conventions;
   do not alter shared wrappers in this lane.
2. Extend the existing index rebuild transaction with a namespaced Scout
   projection/cache registration, preserving the closed `scout_meta` table and
   G22 trace rows. `state.sqlite` remains rebuildable cache; journal artifacts
   and the selector's pinned `journal_head` remain authority.
3. Register the already-added R2 schema only when its reviewed owner supplies
   the authoritative record path and writer. Until then, the reader's empty
   document state is intentional and must not be filled with model IDs.
4. Run one grouped integrated journey after R1/R2 records are available:
   committed discovery -> R1 proposal -> optional R2 document selection ->
   report rebuild/publication -> external application event and correction.

Concrete reader/module changes are in the working tree; no shared index,
schema registry, CLI wrapper or `gig.py` files were edited.

## Completion transport

The required single `worker_done` attempt for task `task_0789ed15dbbb` and
dispatch `ctx_7ba566c2c50b` was issued at 2026-09-11 20:33 MDT. Exact result:
`Could not connect to the running Orca app. Restart Orca and try again.` and
`Orca is not running. Run 'orca open' first.` No retry was issued.
