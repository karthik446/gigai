# SCOUT R4 durable public acquisition completion

Date: 2026-09-12 (America/Denver)  
Status: bounded synthetic acquisition/import persistence implemented and verified; no crawler, scheduler, network/provider call, private data, model call, Tailor action, application action, or discovery-packet authorization.

## Owned delivery

The missing transient-to-durable acquisition slice is now implemented in:

* `src/gigai/scout_acquisition_records.py` — strict public-row validation, immutable canonical input snapshot and digest, scoped batch identity, journal-backed cumulative progress revisions, deadline checkpoint, processed/next-index CAS chain, considered/duplicate/failure/exclusion outcomes with reasons and source snapshots, fresh-process status, exact resume, idempotent completed replay, and tamper/scope/input refusal.
* `src/gigai/scout_acquisition_cli.py` — normal `gigai scout-acquisition import|resume|status` commands. `scout-import` is an equivalent normal CLI alias. The copied `data/scout/gig.py` wrapper has matching `acquisition import|resume|status` operations.
* `src/gigai/journal.py` — one narrowly named semantic transition, `scout_public_acquisition_progress`; no private-record transition or broad mutable-artifact exception was repurposed.
* `src/gigai/schemas/scout-public-import-input.schema.json` and `src/gigai/schemas/scout-public-import-progress.schema.json` — closed, versioned public input/progress contracts. They are registered through the versioned validator inventory and the installed SHA256 inventory.
* `tests/test_scout_acquisition_progress.py` — deadline interruption, fresh status, resume, exact replay, normal CLI import/status, changed input, private-field, and unsafe-batch negatives.

Schema IDs are `scout-public-import-input:1` and `scout-public-import-progress:1`. Public fields are limited to opportunity and snapshot identity, source kind, title/employer/url, acquisition state/outcome reason fields, and a closed source snapshot. Allowlisting these fields is not claimed to prove that free public text contains no PII.

## Durable protocol

An import commits `records/scout-acquisition/<batch>/input.json` and the first immutable progress revision in one journal commit. Later resumes append a new progress revision containing the complete cumulative ledger and a parent progress revision plus parent journal commit. Status authenticates every artifact against its committed handoff, requires one unbroken root-to-tip CAS chain, checks working bytes against committed bytes, and refuses forks, missing parents, scope changes, input digest changes, path redirection, or schema-invalid/private fields. A completed batch is returned without another write when replayed with the same exact input; a partial batch resumes the committed input snapshot rather than accepting alternate rows.

The smallest public row fixture is:

```json
[{"opportunity_id":"opportunity_a","snapshot_id":"snapshot_1"}]
```

Example commands:

```text
gigai scout-acquisition import --gig GIG_ID --target TARGET --home HOME --batch-id fixture-20260912 --rows-file public-rows.json --deadline-seconds 1 --json
gigai scout-acquisition status --gig GIG_ID --target TARGET --home HOME --batch-id fixture-20260912 --json
gigai scout-acquisition resume --gig GIG_ID --target TARGET --home HOME --batch-id fixture-20260912 --deadline-seconds 30 --json
python gig.py --home HOME --target TARGET acquisition status --batch-id fixture-20260912
```

Acquisition progress is not a validated discovery packet and cannot authorize proposal/Tailor posting input. Existing capture/complete-Run authentication and explicit user-action gates remain required. This implementation imports already-acquired rows only; it does not create a network crawler, installed scheduler, background agent, private assessment, automatic Tailor, or application transition.

## Verification

Commands run in the dirty worktree (synthetic/offline fixtures only):

```text
rtk .venv/bin/pytest -q tests/test_scout_acquisition_progress.py tests/test_scout_discovery_job.py
7 passed in 4.33s
rtk .venv/bin/python tools/verify_installed_schemas.py
verified 75 installed GigAI schemas
rtk ruff check src/gigai/scout_acquisition_records.py src/gigai/scout_acquisition_cli.py tests/test_scout_acquisition_progress.py
All checks passed!
```

The focused test performs a deadline-stopped first revision, reads status from the committed journal, resumes the exact input to completion while retaining one row in each considered/duplicate/failure/exclusion ledger, then replays without creating a third progress revision. The normal Click command test imports a file-backed public row array and reads status through a fresh `CliRunner` invocation.

The older `research/contract_spike/tests/test_schemas.py` was not changed: it already expects a stale 63-resource checkout baseline and fails during setup on pre-existing additional schema resources (including prior Scout resources) before exercising this addition. The authoritative installed resource check above passes with the two new resources and their pinned digests.

## Remaining concrete blocker

No acquisition implementation blocker remains for this bounded synthetic slice. The earlier bootstrap capability-manifest sentence in this report was stale: the bootstrap/report controls and their focused evidence are accepted and were not reopened by the path-containment correction. See `SCOUT-R4-acquisition-path-correction.md` for the concrete P1 that was fixed; the historical reviewer disposition remains preserved in its own report.
