# SCOUT R4 narrow durable acquisition review

Date: 2026-09-12 (America/Denver)  
Review mode: independent, read-only review of the current dirty worktree; the
only file changed by this review is this report.  
Reviewed HEAD: `fda48574f8642e66c0e7d53e7303ec04f04d7bb8`  
Verdict: **Conditional bounded acquisition acceptance; one P1 path-containment blocker remains.**

The new durable public-acquisition slice is materially implemented and passes
its focused synthetic lifecycle evidence. It correctly turns already-acquired
public rows into immutable input and journal-backed cumulative progress, with
fresh-process status, deadline checkpointing, exact-input resume, idempotent
completed replay, and separate considered/duplicate/failure/exclusion ledgers.
It is not release-clean yet because symlinked parent directories bypass the
stated unsafe-path boundary for both persisted status and CLI input.

## Scope and evidence limits

The review read:

* `SCOUT-R4-acquisition-completion.md`, `SCOUT-R4-acceptance-review.md`, and
  `SCOUT-R4-bootstrap-report-verification.md`;
* `scout_acquisition_records.py`, `scout_acquisition_cli.py`, the journal
  transition, CLI registration, copied `data/scout/gig.py` wrapper, validator
  inventory, source bundle, package-privacy policy, and both new schemas; and
* the actual tests in `tests/test_scout_acquisition_progress.py` and
  `tests/test_scout_discovery_job.py`.

The completion handoff's seven focused tests mean three new acquisition tests
plus four existing discovery-helper tests; they are not seven new acquisition
tests. I ran only these small synthetic/offline lanes and two disposable
workpad probes:

```text
rtk .venv/bin/pytest -q tests/test_scout_acquisition_progress.py --durations=0
-> 3 passed in 4.32s

rtk .venv/bin/pytest -q tests/test_scout_discovery_job.py --durations=0
-> 4 passed in 0.06s

rtk .venv/bin/python tools/verify_installed_schemas.py
-> verified 75 installed GigAI schemas

rtk ruff check src/gigai/scout_acquisition_records.py
    src/gigai/scout_acquisition_cli.py
    tests/test_scout_acquisition_progress.py
-> passed
```

No live provider/model/network/download/private data, crawler, scheduler,
activation, installation, release suite, seven-minute journey, or broad
release rerun was used. These results prove the bounded local synthetic slice,
not whole v0.1.7 acceptance, installed-runtime behavior, acquisition from an
external source, privacy of arbitrary public prose, or semantic/hiring truth.

## Reviewed bytes

These are SHA-256 digests of the current dirty-worktree bytes, not commit IDs.

| Surface | SHA-256 |
| --- | --- |
| `src/gigai/scout_acquisition_records.py` | `090ba652bd85e131e75f434bdcd1d9f18b19ad42b312cde0e3cd421d33354c08` |
| `src/gigai/scout_acquisition_cli.py` | `dad56b81fb535030907acf58e90d5a97806e589bc8ec7503656e40356ddbe9e3` |
| `src/gigai/journal.py` | `d10effa86188409c1c4aebbe4197e9625b9010b25210c8b684c60c9fa2d8d87e` |
| `src/gigai/cli.py` | `3fdef145f519d2b9d3f8ff113a7e6171def45cdc8c072fa21bfbf0d612e90a46` |
| `src/gigai/data/scout/gig.py` | `ff7aa2a2d947f7b94a1be36d64c04a6e445624a594d9e0f6897ca7675bd8a04a` |
| `src/gigai/validators.py` | `d49bd25ed24f7e4e80c4c912a8ed557350f463191f67cd124ed1abe60dbc4228` |
| `src/gigai/package_privacy.py` | `f5d8c67d4bc9e2399ff4087fe2a9f94e53d90bf922d788ae32e4f15604cf7c3c` |
| `src/gigai/schemas/scout-public-import-input.schema.json` | `074aba64851bd1578e0fdee0bdba3629539b4a0ab5bb71beea191fc578eee374` |
| `src/gigai/schemas/scout-public-import-progress.schema.json` | `1c27e5f098de39c27f6014c5bcbce6677d0e2680742671c9bf338c0b4807d8ef` |
| `tests/test_scout_acquisition_progress.py` | `d6286608b62e71ca939d39377c3333bcf3d72f5bb4653887be5e99e3137b5034` |

The two schema digests also match `tools/verify_installed_schemas.py` and the
installed `SHA256SUMS` inventory. Any earlier 40-hex values labelled SHA-256
in the acceptance review are Git blob SHA-1 values; they are not reused here.

## Accepted acquisition controls

### Public input and row boundary

`_ROW_KEYS` and `_SOURCE_KEYS` form closed allowlists. Rows require bounded
opportunity and snapshot identity; only the documented source kind, descriptive
fields, acquisition outcome fields, and closed source snapshot are accepted.
Unknown/private fields, invalid states, overlong text, oversized batches, unsafe
batch IDs, malformed source digests, and invalid deadlines receive typed
`ScoutAcquisitionError` refusals. The allowlist controls fields and shape; it is
not a claim that free public text contains no PII.

The canonical input payload binds `batch_id`, resolved `project_id`, resolved
`gig_id`, and the normalized rows. It is committed at exactly
`records/scout-acquisition/<batch>/input.json`; the progress carries its exact
`sha256:<64-lowercase-hex>` digest and authenticated artifact reference. A
second import with changed rows is refused, while a completed identical import
returns the existing status without a third progress revision.

### Journal authority and progress protocol

The dedicated `scout_public_acquisition_progress` transition is registered in
the journal transition inventory. The initial input and first progress revision
are committed together; later revisions append under the normal journal writer
lock and carry both the parent revision identity and parent journal head. Each
artifact is no-clobber and has exact path, byte digest, and size references in
the handoff. Status reads the committed bytes, checks the working bytes, schema,
Gig/project scope, immutable input reference, one root, one tip, parent-head /
parent-revision continuity, and an exact processed-index prefix.

The protocol therefore demonstrates the requested deadline-partial -> fresh
status -> exact committed-input resume -> completion -> exact replay sequence.
It does not make acquisition progress a completed discovery packet or posting
authority: no discovery-packet publication, proposal assessment, private
matching, Tailor, application, or automatic action is reachable from this
slice.

The classifier retains one cumulative row in each requested ledger and records
the duplicate/failure/exclusion reason plus the source snapshot. It classifies
already-acquired caller rows only; no crawler or scheduler is implied or
required.

### CLI, consent, wrapper, and package boundaries

Normal Click commands are registered as `scout-acquisition import|resume|status`
and the equivalent existing alias `scout-import`; `--input-file` is an alias of
the rows-file option. Explicit import is a user action and correctly does not
invent a mandatory `--confirm` gate. Resume reads the committed input rather
than accepting replacement rows. Errors are rendered with typed machine codes.

The copied wrapper exposes `acquisition import|resume|status`, authenticates its
own `gig.py` regular-file location, rejects wrapper or workpad redirection,
requires exactly one registered workpad owner, resolves the registry Gig and
target, and passes that authenticated `ResolvedWorkpad` to the shared service.
It does not duplicate journal/domain logic. Existing accepted wrapper guard
ownership is not reopened here.

The two schemas are deliberately additive versioned resources in
`_VERSIONED_SCHEMA_NAMES`, with matching installed digest inventory. The
Scout authoring source bundle remains inert and authority-free: it does not
include private `records/` or runtime state. The package privacy map already
marks `records/` private, so acquisition rows do not create a new portable
export path; ordinary non-private namespaced source files remain outside this
private-record root. No fabricated catalog alias or new provider gate was
introduced.

## Concrete blocker

### P1 — symlinked parent path redirection is accepted

This is a direct violation of the requested unsafe-input/symlink-parent
boundary and blocks final bounded acquisition acceptance.

* Persisted status checks `input_file.is_symlink()` and each progress file's
  `path.is_symlink()`, and checks only whether the final progress directory is
  itself a symlink (`scout_acquisition_records.py:278-300`). It does not walk
  `root` to the input/progress paths and reject symlinks in `records/`,
  `records/scout-acquisition/`, or the batch parent.
* Disposable synthetic probe: import four rows, rename the real `records`
  directory to `records-real`, replace `records` with a symlink to it, then
  call `read_public_acquisition_status`. Actual result: `4` (successful
  status), where the required result is a typed `acquisition_path_unsafe`
  refusal. The committed bytes happen to match, but the authority path has
  been redirected outside the named workpad path.
* The CLI rows source has the same gap: `_rows` rejects a symlink file but not
  a symlinked parent (`scout_acquisition_cli.py:45-54`). A disposable probe
  with `alias -> real` and `alias/rows.json` returned `[]` instead of refusing.
  The copied wrapper repeats the final-file-only check at `gig.py:302-311`.

Minimal fix: add one shared path-containment helper that walks every parent
component from the authenticated root to the requested file/directory and
rejects any symlink, non-directory, or escaped path before `exists`, `glob`,
`is_file`, `read_bytes`, or JSON parsing. Apply it to acquisition input,
batch/progress directories and files, normal CLI rows files, and copied-wrapper
rows files; add disposable tests for a symlink at each parent level. Keep the
existing final-file checks and typed error codes. No broader journal replacement
or authority relaxation is needed.

This is a path-boundary defect, not a bootstrap/report defect and not evidence
that the normal journal writer races. The existing writer lock and no-clobber
behavior still cover duplicate imports and concurrent revision writes when
paths are regular and contained.

## Controls not reopened and remaining product limits

The earlier bootstrap/replacement conflict and exact report-row assertions are
resolved by `SCOUT-R4-bootstrap-report-verification.md` and remain accepted
historical evidence; the stale blocker prose in the acquisition completion
handoff is not a current acquisition defect. The accepted R0-R4 proposal
redemption, model descriptor checks, narrow capability-manifest replacement,
Tailor-bound final selection, journaled Run/evidence rows, terminal recovery,
explicit action consent, and package privacy controls are not re-reviewed as
new failures here.

No conclusion is made about the whole 0.1.7 release. R5 interview/portability
and runtime comparison, R6 comparison/evaluation, and R7 installed release,
UAT, and provider evidence remain pending. After the P1 parent-path fix and
focused negative tests, the bounded synthetic acquisition slice should be
re-reviewed; no crawler, scheduler, provider, model, or activation work is
needed for that closure.

## Consolidated disposition

**Conditional acceptance only.** The delivered acquisition implementation
meets the durable public-row, immutable-input, journal-revision, deadline,
resume/status/replay, ledger, scope, and explicit-user-action requirements in
the demonstrated regular-path synthetic slice. Final bounded R4 acquisition
acceptance is blocked solely by P1 symlinked-parent redirection in persisted
status and CLI input handling; fix that containment seam and add its focused
negative tests, then reassess this report without reopening the already
accepted bootstrap/report or broader R0-R4 controls.
