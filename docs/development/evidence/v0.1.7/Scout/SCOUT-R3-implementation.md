# SCOUT-R3 tracker and application linkage handoff

Date: 2026-09-11 (America/Denver)
Lane: R3, Luna implementation
Status: implementation handoff; integration and release acceptance remain pending.

## Delivered

R3 adds three local-library/CLI surfaces and a focused test module:

- `src/gigai/scout_projection.py` defines `ScoutReaderSet`, `ScoutProjection`,
  `projection_from_snapshot`, `query_projection`, `read_cached_projection`, and
  `rebuild_projection`.
  Projection construction consumes one pinned `JournalSnapshot`, validates
  application event and document identities through the existing application
  contract, reads native question sidecars from committed bytes, and keeps
  application opportunities explicitly unresolved until R1 supplies a reviewed
  opportunity reader. `query_projection` is an in-memory SQLite view and is
  deliberately non-authoritative; `rebuild_projection` caches only a namespaced
  payload in the existing Scout metadata table pending the shared index
  registration owned by R4.
- `src/gigai/application_events.py` adds
  `validate_application_links`, a snapshot-only companion for event readers.
  It checks the event contract, scope, exact committed document revision and
  snapshot bytes, and optionally invokes an explicit opportunity reader. No
  model-owned ID or dashboard row can establish a verified opportunity.
- `src/gigai/scout_report.py` renders escaped, script-free HTML with jobs,
  application history, saved proposals, pending questions, documents/checks,
  evidence, and Run/source rows. Local links require regular files within the selected
  Gig; external links require credential-free HTTP(S). Generation bundles are
  separate from editable `ui/` source, and `current.json` is atomically
  replaced only after staging validation succeeds.
- `src/gigai/scout_report_cli.py` provides standalone `scout-report generate`
  and `scout-report status` commands for R4 registration.
- `integration-patches/r3/SCOUT-R3-cli-registration.patch` contains the
  requested shared CLI/copied-wrapper registration patch. It is not applied by
  this lane. No new schema was added, so no schema registry/SHA256SUMS change is
  requested.

## Reader expectations

R1/R2 readers should return iterable mapping rows through `ScoutReaderSet`:

- opportunities: at minimum `opportunity_id`, `snapshot_id`, readable title or
  role/employer fields, and optional validated `source.locator` plus committed
  source/run references;
- proposals: `opportunity_id`, immutable proposal revision identity, status,
  and optional validated document/path references;
- documents: immutable record/revision/content digest plus optional validated
  report-relative path and check summary;
- evidence: a readable claim/evidence identity, status, and optional validated
  committed source path;
- runs: immutable Run identity/status and optional committed artifact path.

Readers receive `(snapshot, project_id, gig_id)` and must use host-authenticated
committed references. They must not return latest-by-default, raw model IDs, or
working-tree/sidecar authority. If an opportunity reader is not supplied, the
report displays application events as `opportunity unresolved`; this is an
honest pending-integration state, not acceptance of the event's opportunity.

## Commands and evidence

Exact focused commands run in this dirty worktree:

```text
rtk python -m py_compile src/gigai/scout_projection.py src/gigai/scout_report.py src/gigai/scout_report_cli.py src/gigai/application_events.py
rtk .venv/bin/pytest -q tests/test_scout09_application_events.py
12 passed in 20.16s
rtk .venv/bin/pytest -q tests/test_scout_r3_report.py tests/test_scout09_application_events.py
18 passed in 23.20s
rtk ruff check src/gigai/scout_projection.py src/gigai/scout_report.py src/gigai/scout_report_cli.py src/gigai/application_events.py tests/test_scout_r3_report.py
All checks passed
```

The R3 tests are synthetic/injected reader and local-file publication tests;
they do not prove a live provider, hosted service, installed wheel, or R1/R2
opportunity/Tailor records. Existing SCOUT-09 tests exercise the real direct
application journal path and remain passing. A full integrated journey and
closed SQLite schema/index registration are R4 responsibilities.

## Integration notes and remaining work

The current shared index validator allows the closed tables
`scout_records`, `scout_operations`, and `scout_meta`; it does not yet inventory
the R3 report cursor as a first-class table. R4 should reconcile the provided
patch with current `cli.py`/`gig.py`, register these modules/source assets, and
extend the shared index rebuild transaction so report projection rows/cursor
survive legacy index rebuilds without dropping G22 trace data. R4 should then
run one grouped integrated review and correction pass using actual R1/R2
records. No application transition, Tailor finalization, provider execution,
activation, commit, or publication was performed here.

## Worker completion transport

The required single completion notification was attempted with
`orca orchestration send --type worker_done` for task
`task_e9170c226d71` / dispatch `ctx_f235548ce673`, but the local IPC endpoint
was unavailable. Exact command result: `Could not connect to the running Orca
app. Restart Orca and try again.` followed by `Orca is not running. Run 'orca
open' first.` No retry was issued; this handoff is the durable final report.
