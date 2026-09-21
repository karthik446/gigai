# SCOUT R4 acquisition path-containment correction

Date: 2026-09-12 (America/Denver)  
Status: the concrete P1 from `SCOUT-R4-acquisition-review.md` is fixed; this report covers only ancestor/final-component symlink containment for durable acquisition paths and rows-file input.

## Exact correction

`src/gigai/scout_acquisition_records.py` now uses a component-by-component `_guard_relative_path` walk anchored at the authenticated workpad root. It checks `records`, `records/scout-acquisition`, the batch directory, input file, progress directory, and each enumerated progress final file before any corresponding read or journal publication. Existing components must be real directories, final files must be regular files, symlinks are rejected without resolving them, and missing components remain allowed only for a new journal create path. `_guard_acquisition_tree` runs before status input reads or progress enumeration, while import runs it inside the existing writer critical section before checking/publishing artifacts; resume rechecks before its committed input read.

The same module's `read_public_rows_file` is now the shared loader used by `scout_acquisition_cli.py` and copied `data/scout/gig.py`. Its external path walk rejects every redirecting parent component and final symlink with typed `acquisition_source_unsafe` before reading bytes. It accepts the canonical macOS `/tmp` alias only when that system alias points exactly to `/private/tmp`; untrusted descendants are never resolved. No global journal reader, schema, authority, concurrency, or arbitrary-host race guarantee was changed.

## Disposable regression evidence

`tests/test_scout_acquisition_progress.py` starts each case from a legitimate journal workpad and a committed batch. It covers symlink redirection at:

* `records/`;
* `records/scout-acquisition/`;
* `records/scout-acquisition/<batch>/`;
* `records/scout-acquisition/<batch>/progress/`;
* the committed `input.json` final component; and
* the committed progress JSON final component.

For each durable redirect, public status, import, and resume all refuse with `acquisition_path_unsafe`; the Git journal HEAD and committed progress remain unchanged. The normal CLI rows loader test covers both a redirecting parent and final rows-file symlink with `acquisition_source_unsafe`. A copied-wrapper subprocess test exercises both cases through the actual copied `gig.py` and the same shared loader, before any rows read or publication.

The original lifecycle regression remains in the same file: deadline stops partway, fresh status reads the committed checkpoint, resume consumes the exact committed input, all considered/duplicate/failure/exclusion outcomes remain, and exact replay does not append another revision.

## Verification

Commands run against synthetic/offline disposable workpads only:

```text
rtk .venv/bin/pytest -q tests/test_scout_acquisition_progress.py tests/test_scout_discovery_job.py
17 passed in 12.98s
rtk ruff check src/gigai/scout_acquisition_records.py src/gigai/scout_acquisition_cli.py src/gigai/data/scout/gig.py tests/test_scout_acquisition_progress.py
All checks passed!
rtk git diff --check
passed
```

No real private data, live provider/model/network call, download, activation, commit, publish, crawler, scheduler, or broad seven-minute journey rerun was used. Existing writer locking remains in force; these tests do not claim arbitrary host race isolation. The historical review report is unchanged.

The prior acquisition completion report now links this correction and removes its stale bootstrap blocker wording: bootstrap/report tests were already accepted and are outside this P1.
