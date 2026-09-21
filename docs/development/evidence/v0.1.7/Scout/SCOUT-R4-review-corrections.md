# SCOUT R4 review corrections

Date: 2026-09-11 (America/Denver)

This is a bounded implementation handoff for the source-only findings in
`SCOUT-R4-integrated-review.md`. It does not claim the moving user-action
surface or the full release journey is accepted.

Terminal recovery follow-up, including the lifecycle defect reproduction and
combined verification, is recorded in
[SCOUT-R4-terminal-recovery-completion.md](SCOUT-R4-terminal-recovery-completion.md).
The earlier failure evidence below is intentionally preserved as historical
pre-recovery evidence.

## Dispositions

### P1 proposal lineage

`read_proposal_revision` now captures one `JournalSnapshot` before reading and
redeems the opportunity through `resolve_discovery_posting_input`, every native
input through `_record_revision`/`_sidecar`/`resolve_external_input`, and the
invocation result and model record through exact snapshot bytes. It checks
scope, path identity, size/digest refs, posting artifact identity, question
membership, result assessment equality, invocation-record digest, and commit
ancestry for both sealed heads. `scout_report_readers._proposal_rows` calls this
pinned API and never publishes an unredeemed nested ref.

The production path retains the existing discovery graph-set, compiled
contract, and fixed tool-validator artifacts needed by the resolver; it does
not use working-tree JSON as authority.

### P1 model descriptor binding

The v3 invocation validator rejects non-mappings before field access, duplicate
selected IDs or descriptor IDs, unknown family/purpose values, closed-shape
violations, digest mismatches, and missing/extra descriptor IDs. Validation runs
before adapter resolution. The proposal caller uses host-created `source_N`
transport labels for every selected source (including G45 imported records) and
emits one descriptor per source; the descriptor remains identity-digest
metadata, while `_resolve_sources` remains the hydration and caller authority.
Invocations without descriptors retain v1/v2 ref-ID behavior.

### P1 mutable manifest exception

`read_committed_artifact` now admits replacement semantics only for the actual
versioned capability-manifest path shape under `manifests/capabilities/`.
Replacement requires `capability_review_decided`, while the one-publisher
bootstrap path retains its existing materialization transition; both paths
validate capability-manifest schema, `manifest_id` path identity, and `gig_id`
ownership. Active pointers, graph-set manifests, proposal records, and
arbitrary nested manifests remain immutable. Snapshot filtering retains only
the graph-set/compiled contracts and the fixed Scout discovery tool files
required by existing readers.

### P1 final selection redemption

The report document reader host-validates each committed R2 revision and exact
content ref, then redeems every selected tuple against that same snapshot and
the matching opportunity/snapshot. v2 selections require an authenticated
completed Tailor `run-details.json` goal and matching Tailor result identity,
output digest, and document set. v1 remains readable only as
`legacy_selection: true` with `selected: false`; it cannot mark an
authoritative public document as selected.

### P2 local Run/evidence readers and tamper coverage

The production reader now includes authenticated local `run-details.json`
trees and terminal `result.json` artifacts in `runs` and `evidence`, while
retaining external Run/checkpoint rows. The focused descriptor regression covers
missing, duplicate, unknown-family, closed-shape, and non-mapping cases and
asserts adapter transport is not resolved. The proposal-run reader now has
real committed-journal tamper variants for request bytes, response reference,
record identity, actor, source descriptor, receipt reference, and target, with
all seven refused under one pinned view; the current moving R4 action tests are
not relabeled as accepted here.

## Preserved legitimate paths

- Initial one-publisher capability-manifest materialization remains readable;
  reviewed successor replacements use the explicit review transition.
- Existing run-details replacement remains available to the scheduler's
  `runs/*/run-details.json` path, while immutable records retain one-publisher
  checks.
- Proposal execution keeps imported G45 and native sources in its real
  resolver; no G45 IDs, approvals, model identity, or Run state are fabricated.
- v1/v2 generic model invocation behavior is unchanged when the additive v3
  descriptor array is absent.
- External applications and legacy v1 document selections remain visible but
  are not promoted to Tailor-authoritative selection.

## Verification

Focused checks run in this dirty worktree (synthetic fixtures and injected local
transport only):

```text
rtk ruff check src/gigai/journal.py src/gigai/model_execution.py \
  src/gigai/scout_proposal_records.py src/gigai/scout_report_readers.py \
  tests/test_scout_r4_review_corrections.py
-> passed

rtk .venv/bin/pytest -q tests/test_scout_r4_review_corrections.py \
  tests/test_scout_proposal_run.py -x
-> descriptor cases passed; existing proposal-run suite reached a moving
   shared run.py failure in invalid-result recovery (goal is marked failed but
   run-details status remains running), outside this lane's owned files

rtk .venv/bin/pytest -q \
  tests/test_scout_r4_journey.py::test_r4_real_proposal_to_tailor_journey_persists_exact_documents -x
-> passed (1 passed in 45.23s), including real proposal record reload and
   pinned lineage redemption

rtk .venv/bin/pytest -q \
  tests/test_scout_r4_journey.py::test_r4_full_tailor_report_application_journey -x
-> passed (1 passed in 158.90s), including local Tailor Run/result rows,
   final document redemption, report rebuild and application-history journey;
   do not treat the moving user-action assertions as release acceptance

rtk .venv/bin/pytest -q tests/test_scout_proposal_run.py \
  -k committed_receipt_tamper --durations=0
-> 7 passed, 9 deselected in 145.79s (0:02:25); committed target, request,
   response, record, actor, source, and receipt-reference variants were all
   refused under the pinned reader
```

## R4 integration requests

1. Register `scout_report_cli`/`default_reader_set` and the namespaced report
   rebuild transaction in the R4-owned `cli.py`, `gig.py`, `index.py`, and
   schema inventory surfaces; no shared wrapper was edited here.
2. Preserve the report reader's single-snapshot call contract when wiring the
   R4 command. Do not substitute generic working-tree JSON or a fresh-HEAD
   resolver for `read_proposal_revision(..., snapshot=...)`.
3. Keep the current Luna-owned application/document/action changes separate;
   final integrated review must rerun after those changes and after R2's
   concrete reader contract is frozen.

## IPC note

The required Orca heartbeat attempt returned: `Could not connect to the
running Orca app. Restart Orca and try again. Orca is not running.` No retry loop
was attempted; this file is the exact handoff artifact and the worker result
will report the IPC limitation.
