# SCOUT-03 — Independent adversarial verification

**Date:** 2026-09-08  
**Reviewer:** Luna  
**Scope:** Independent verification of the current Terra SCOUT-03 implementation
against `SCOUT-03-implementation-plan.md`, `SCOUT-03-implementation.md`, the
accepted SCOUT-00 lifecycle/workspace amendments, G45, and the SCOUT-03 caller
audit. This report is the only project file written by this verification. No
source or test files were changed; all behavioral probes used disposable
workpads under the canonical macOS `/private/var/folders/.../T` root.

## Executive result

The implementation has useful working seams, but SCOUT-03 is **not accepted**.
Focused source checks passed, and the coordinator reports the separate complete
offline suite as **769 passed, 1 skipped, 104 subtests, 7 warnings in 464.06s**;
that full-suite result was not rerun here. Independent adversarial probes expose
blocking gaps in atomic CAS/idempotency, projection-failure recovery, archive and
saved-default/task-override behavior, and malformed SQLite handling, so the
worker handoff's two new tests and focused pass counts cannot establish A03.

## Inputs and verification boundary

Read completely or inspected directly:

- `SCOUT-03-implementation-plan.md` — stages, ownership, acceptance gates, and
  explicit requirements for CRUD/archive/CAS, journal recovery, projection
  recovery, G22 compatibility, exact input selection, privacy, and inventory.
- `SCOUT-03-implementation.md` — Terra's delivered boundary, two-test claim,
  focused commands, installed-resource claim, and explicit full-suite limitation.
- `SCOUT-03-caller-audit.md` — pre-implementation authority/caller hazards,
  especially G22 trace locking, SQLite replacement, selected inputs, and A03.
- Accepted `SCOUT-00-contract-amendments.md`, sections 3, 8, B2 and M1;
  `SCOUT-00-user-owned-gig-amendment.md`, sections 2, 4 and 7; G45's local
  import/authority/privacy contract.

No provider, network, live agent, user `.gigai` or user config access, commit,
full-suite rerun, subagent, or source/test mutation occurred. The requested
initial Orca guidance check was attempted first on resume, but Orca was not
running (`Could not connect to the running Orca app`), so no additional fixture
guidance was available through that channel.

## Commands and recorded results

All commands used direct executables; RTK was not used.

```text
.venv/bin/pytest -q \
  tests/test_scout03_private_records.py tests/test_index_projection.py \
  tests/test_workpad_private_git.py tests/test_canonical.py \
  research/contract_spike/tests/test_schemas.py \
  tests/test_journal_locking_recovery.py \
  tests/test_g22_proposal_interview_contract.py \
  tests/test_canonical_ownership.py tests/test_g15_review_substrate.py
PASS: 121 passed, 97 subtests passed in 23.22s

.venv/bin/pytest -q \
  tests/test_cli_and_scenario_harness.py tests/test_bug_002_cli_surface.py \
  tests/test_scout02_independent.py tests/test_scout02_review_corrections.py
PASS: 42 passed in 7.15s

.venv/bin/python -m compileall -q src/gigai
PASS: exit 0

ruff check src/gigai/private_records.py src/gigai/workpad.py \
  src/gigai/index.py src/gigai/lifecycle.py src/gigai/cli.py
FAIL: 10 errors

.venv/bin/python tools/verify_installed_schemas.py
PASS: verified 49 installed GigAI schemas
```

The Ruff failures are concrete implementation hygiene failures: one unused
`select_exact_inputs` import in `cli.py`, one unused `_journal_git` import in
`private_records.py`, and eight E702 semicolon errors in the new CLI handlers
(`cli.py:198,212,226,247,258,273,298,315`).

The existing `dist/gigai-0.1.6-py3-none-any.whl` was inspected and its
`gigai/private_records.py` SHA-256 exactly matched the current source. Its
installed package, imported from a disposable target outside the checkout,
contained the five new schema resources and 49 schema documents (53 total
resource directory entries including non-schema files). The direct `uv build`
attempt was blocked by the environment's unreadable uv cache (`Operation not
permitted`); no network/build workaround was used. Installing that wheel
`--no-deps` into a disposable target allowed resource inspection, but
`python -m gigai.cli --help` stopped at the normal missing runtime dependency
`questionary`; therefore clean-room installed CLI help is **not evidenced** in
this run, while packaged resources are evidenced.

## Adversarial findings

### F1 — Blocking: operation-key replay and parent CAS are not atomic

The contract requires identity, parent, and operation-key checks inside the
journal writer lock. `create_record` enumerates revisions before calling
`_publish` (`src/gigai/private_records.py:307-325`), and `_publish` checks the
receipt before entering `record_transition` (`:174-197`). The implementation
therefore has a read-then-write race.

Disposable probe setup created a clean v2 workpad, imported one reference, and
ran these cases:

```text
same reference + same operation key twice:
  first created=True; replay created=False; replay.receipt=None
same record create arguments + same operation key and record_id:
  PrivateRecordError stale_parent
two forked writers, same record_id and parent_revision=None,
different operation keys:
  both returned ok; list_revisions(..., record_id) == 2
```

The two successful concurrent revisions both claim a null parent, directly
violating stale-parent CAS. Equivalent G45 import discovery returns an item
without the original receipt, contrary to the required matching-key receipt
replay. A changed payload with the same reference operation key correctly
returned `private_operation_conflict`; changed bytes with a new key correctly
created a new immutable reference. Those passing branches do not repair the
atomicity failure.

### F2 — Blocking: committed authority plus projection failure has no typed recovery

`_publish` commits the journal transition and then invokes
`rebuild_scout_projection` outside the publication path
(`src/gigai/private_records.py:189-198`). A disposable monkeypatch made that
rebuild raise immediately after a valid record journal commit. The caller
received:

```text
PROJECTION_FAILURE RuntimeError synthetic projection failure
```

The authoritative record remained present in the journal/revision files. The
required result is committed authority plus `projection_pending` and a rebuild
next action, with retry unable to duplicate the event. The current path leaks
the raw projection exception and supplies no typed pending/reconciliation state.

### F3 — Blocking: archive/tombstone and saved-default/task-override are absent

The private-record module exports imports, revision create/read, projection,
and exact-input helpers, but no archive/delete operation. The probe found
`archive_record` and `delete_record` absent. `create_record` has no
`saved_default` or `run_override` scope, and the CLI only exposes
`layout`, `reference`, `run-input`, and `record` commands; there is no context
or preference-scope command. Consequently A03's required archive-preserved
history, saved preference versus task-only override, and downstream selection
semantics cannot be demonstrated.

The implementation also restricts `create_record` to G45 reference/input
families (`private_records.py:301-307`), while `read_record` explicitly raises
that native content reads are not implemented (`:349-367`). The accepted B2
bridge permits native `jsl_blob` records and selected conversation
excerpt/summary records, with explicit provenance and bounded content; those
families are not delivered.

### F4 — Blocking: malformed SQLite is silently discarded

The implementation correctly rejects a recognized malformed trace table and an
unknown table in the probes:

```text
malformed interview_events schema -> JournalIndexError interview trace table schema is invalid
unknown_table -> JournalIndexError state database has unsupported tables
```

However, replacing `state.sqlite` with the bytes `not sqlite` and running
`rebuild_index` returned successfully with `MALFORMED_SQLITE replaced`. The
current `_read_interview_events` and `_read_scout_tables` catch SQLite errors
and return `None`/`[]` (`src/gigai/index.py:205-243`), after which
`_write_projection` replaces the file. This violates the accepted amendment's
requirement to fail visibly on malformed/unknown/incomplete legacy state rather
than discard possible G22 evidence.

The source review also found two G22 trace callers. Lifecycle's
`_persist_interview_trace` takes `database_lock` (`src/gigai/lifecycle.py:2345-2356`),
but the HTTP handler calls `persist_trace(owner.connection, ...)` directly
(`src/gigai/proposal_interview.py:749-758`), while `persist_trace` itself only
operates on the supplied connection (`:556-575`) and does not acquire the
common interprocess lock. The focused G22 tests passing does not prove the
required concurrent HTTP trace/rebuild race is closed.

### F5 — Partial only: exact selection helper and provider refusal are not a
complete A03 input path

`select_exact_inputs` successfully returned the named historical revision in a
disposable probe (`EXACT_SELECTION 1 True`), and context metadata was generated
without embedding the imported bytes (`indexes/context.json` existed and the
probe's raw text was absent). This is useful helper-level evidence only: no
sealed external recording Plan/Run, revision-chain revalidation at Run
allocation, archive-aware selection, successor behavior, or task override is
implemented in this wave.

Managed G43 Plan creation does refuse direct paths under `references`,
`run-inputs`, `records`, or `docs` (`src/gigai/run_plan.py:880-890`) with
`private_provider_disclosure_refused`. That guard is a necessary privacy
boundary, not proof of the required exact-input family. The imported wrappers
are not accepted by the managed Plan reader, and the external-recording Plan,
Run, checkpoint, submit, and selected-input command family described in B1 is
absent. No provider was called in this verification.

### F6 — Partial only: tool inventory/effect binding is not in the delivered
private operation API

The accepted workspace amendment requires supported Gig-owned mutations to
bind actual approved Gig/version, entry/tool bytes, source inventory, operation,
effects, and agent origin, then recheck those values before publication. The
delivered `create_record`/`_publish` payloads bind record content, actor, and
operation key but expose no approved tool inventory, entry digest, effects, or
Gig version capability check (`private_records.py:174-190,301-325`). The
implementation handoff does not claim an external operation service or tool
dispatch implementation. This remains a required acceptance gap, even though
SCOUT-03 does not need to implement the later external executor itself.

## Passing boundary checks

These probes passed and should be retained as useful evidence, without inflating
them into A03 completion:

- Explicit v1-to-v2 migration preserved a dirty ignored
  `scratch/dirty-draft.md` byte-for-byte and produced layout version 2.
- A forged v2 marker with an invalid ignore digest refused with
  `WorkpadConflictError: workpad layout marker ignore policy is invalid`.
- Symlink, invalid UTF-8, and 1,048,577-byte reference inputs refused with
  `reference_source_unsafe`, `reference_invalid_utf8`, and
  `reference_too_large` respectively.
- Equivalent imports reused the same immutable item; changed bytes under a new
  operation key created a distinct item. Imported bytes remained unchanged.
- Scoped context metadata omitted imported source text, and explicit named
  `read_record(..., content=True)` returned only the selected bytes.
- Source compilation, schema inventory, focused SCOUT/G22/index/journal suites,
  and focused CLI/SCOUT-02 suites passed as recorded above.

## A03 disposition

| A03 obligation | Independent status |
|---|---|
| Saved default versus task-only override | **Missing**; no scope/API |
| Stale-parent conflict under concurrency | **Failed**; two null-parent revisions committed |
| Prior sealed inputs unchanged | **Partial**; helper can select a revision, but no external Plan/Run revalidation |
| Fresh-session metadata recovery | **Partial**; redacted context file is produced, but no CLI/context recovery boundary |
| No cross-Gig/private leak | **Partial**; scope checks and managed-path refusal exist, but no full selected-input/provider ingress |
| CRUD archive/tombstone/history | **Missing**; no archive/delete operation |
| Journal authority and operation recovery | **Failed**; projection exception leaks after authority commit |
| G22 trace/index preservation | **Failed** for malformed DB/direct HTTP writer path |
| Inventory binding and privacy | **Partial**; direct managed path refusal exists, capability binding is absent |

**Disposition: return to bounded implementation/review work; do not mark SCOUT-03
accepted.** The coordinator's completed full-suite result should remain attached
as regression evidence, but it does not override the reproducible acceptance
failures above.
