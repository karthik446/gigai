# SCOUT-05 first Graph Set proposal — independent review

**Type:** Independent, read-only review of the delivered first Graph Set
proposal slice. No production, test, or schema edits were made. No provider,
init, approval, activation, or commit was executed. Findings below distinguish
*proof* (behaviour a test or the journal actually pins) from *code assertion*
(a guard that is present and reads correctly but is not exercised) from
*schema* (what the strict JSON Schema does and does not constrain).

**Date:** 2026-09-09.
**Reviewer scope inputs:** SCOUT-05 source-proposal integration plan,
SCOUT-05 first-proposal implementation handoff, accepted SCOUT-00
workspace/contract authority (`SCOUT-00-user-owned-gig-amendment.md`,
`SCOUT-00-contract-amendments.md`).

## Reviewed source hashes (SHA-256)

Recorded so later integration can detect a stale review. Repo `HEAD` at
review time: `fda48574f8642e66c0e7d53e7303ec04f04d7bb8`.

| File | Tracked? | SHA-256 |
|---|---|---|
| `src/gigai/lifecycle.py` | modified vs HEAD | `1ab937ce6989107ad8bcf148b6f62b58e3ded492231273d4727cc1239bd6555c` |
| `src/gigai/cli.py` | modified vs HEAD | `8ca6e52b52d40a599d5297139f30ab0d9018e61d5f78fdef05eb6df1aa5b0969` |
| `tests/test_scout05_first_proposal.py` | untracked (new) | `af17742c0815bc96e2966de64f2fc088d7de76f4c5cf45d40012f20f2c7a4dc0` |
| `src/gigai/schemas/gig-proposal-v2.schema.json` | untracked | `fe1e712aa86b83f60b48de95bfbedbe5ff411e4427ebfb0f85b7b6fc9b640cf2` |
| `src/gigai/schemas/gig-graph-set.schema.json` | untracked | `82fffb4f786bdef460a4f096dc3fc4f909f970ecf6c572d46b9a18538cb62174` |
| `src/gigai/schemas/active-gig-version-v2.schema.json` | untracked | `95220e21bd6e6b0eb26eee9229b00d9fee94a172b563cce21d7176ff72089178` |
| `src/gigai/schemas/scout-operation-receipt.schema.json` | untracked | `3506617a28ca0211748fdf2937cbbb6eb70201a681472ef9316d8a5bdea0d8a6` |
| `src/gigai/workpad.py` (context) | modified vs HEAD | `76e9e88066252dc83dd0fe5360f659eefabf33f35430c1440843eb8e06bd6a38` |
| `src/gigai/journal.py` (context) | modified vs HEAD | `a4051d6cb2db728ed6e86af21e1e40c00e34ee386491f04f5fe1f3e335b314d2` |
| `docs/.../SCOUT-05-source-proposal-integration-plan.md` | modified/untracked | `7480852978eb2dbb9163f6652a4c907c6c7f6596e9b716e3e00db6a752fdaf61` |
| `docs/.../SCOUT-05-first-proposal-implementation.md` | untracked | `b6a5212945949412e330cd8c21ccecba70d8288e297f1aee2c3625754713ea9c` |

The `SHA256SUMS` manifest entries for the three v2 schemas and the receipt
schema match the on-disk bytes (checked by `shasum -a 256`).

## Recorded evidence relied on (not re-run)

The implementation handoff records, from a disposable fixture:

```
.venv/bin/pytest -q tests/test_scout05_first_proposal.py \
  tests/test_scout02_graph_set_flow.py::test_two_graph_propose_approve_plan_history_and_run
=> 5 passed in 10.26s
```

Four new test functions plus one SCOUT-02 amendment regression. This review
does **not** restate that run as its own. No pytest was executed here and no
suite was run.

## Verdict

**Approve the slice for staging the first Graph Set proposal**, bounded as
follows. The delivered `propose_first_graph_set_offline` /
`propose_graph_set_offline(_first_proposal=True)` branch, the optional
explicit `gig_id` on `approve_offline`, and the two CLI surfaces implement the
SCOUT-00 first-proposal authority correctly for the properties that matter:
first `kind:"create"` proposal with no pre-approved predecessor, null
`base_gig_version` / `parent_proposal_id`, exact-target resolution with no
active-selection mutation, separate direct approval, source bounds / path /
link safety anchored to a resolved source root, exact imported-bytes digests,
preflight + writer-lock source re-read, journal-authenticated replay after a
post-publication interruption, immutable committed-authority checks on retry,
and first-vs-amendment compatibility. All findings are **LOW / NIT** — none
blocks staging. No finding requires a schema change.

The known **`scout-operation-receipt` `gig_version >= 2`** gate is recorded
below as a *separate integration obligation for later tool-receipt work*, not
a blocker for this slice (see §"Known integration gate").

## Findings

### F1 — LOW (test coverage): source path / link safety guards are unexercised

**Where:** `lifecycle.py` `safe_source` (`src/gigai/lifecycle.py:1568-1597`),
in particular the `is_absolute()` / `"\\" in path` / `".." in relative.parts`
rejection (1577-1578), the per-component `cursor.is_symlink()` walk
(1580-1584), and the `candidate.is_symlink() or not candidate.is_file()`
check (1585-1586).

**Proof vs assertion:** These are *code assertions only*. `test_scout05_first_proposal.py`
never feeds an absolute member path, a `..` segment, a backslash, a symlinked
member file, or a symlinked `definition.json`. `test_..._refuses_changed_source`
exercises only a content change (commission edit), which trips the sealed
*identity* comparison, not the path guards.

**Failure scenario:** A regression that loosened `safe_source` (e.g. dropped
the per-component symlink walk, or resolved `candidate` before the symlink
check) would let a first-proposal definition stage bytes from outside the
source root — an out-of-tree read into the journalled proposal — and no test
would fail.

**Smallest correction:** Add one parametrized negative case to
`test_scout05_first_proposal.py` that rewrites one member `path` in the
definition to (a) `"../escape.json"`, (b) an absolute path, and (c) a symlink
planted under the source dir, asserting `LifecycleError` with the
`unsafe` / `redirected` / `unavailable` message for each. Also cover a
symlinked `definition.json` (the `definition_path.is_symlink()` guard at
`lifecycle.py:1487`).

### F2 — LOW (test coverage): tampered / malformed sealed receipt and committed working-copy drift are unexercised

**Where:** `publish_first` recovery branch,
`src/gigai/lifecycle.py:1787-1834`, and `first-proposal-inputs.json`
authentication at 1808-1828; the committed-vs-working-copy check at 1788-1790.

**Proof vs assertion:** `test_..._replays_exact_pending_publication_and_refuses_changed_source`
proves the *happy* replay (identical retry → same `proposal_id`, one journal
publisher) and the *changed-definition* refusal (`"changed source inputs"`).
It does **not** prove:
- refusal when `manifests/gig-proposal.json` working copy is edited to differ
  from committed authority (`"working copy differs from committed authority"`,
  1790);
- refusal when `first-proposal-inputs.json` is deleted / edited on disk after
  the interrupted publish (`"lacks sealed source identity"` / `"differs from
  committed authority"`, 1817 / 1826);
- refusal when the committed `gig-proposal.json` has two publishers
  (`JournalConflictError` → `"cannot be authenticated"`, 1786).

**Failure scenario:** A future edit that read the working-tree
`gig-proposal.json` before the `read_committed_artifact` authentication (the
exact anti-pattern `journal.py:1051-1052` warns against) would let a
hand-crafted pending proposal be "recovered" and then explicitly approved as
version 1, with no test catching it.

**Smallest correction:** Extend the retry test with three assertions after the
injected interruption: mutate the working `gig-proposal.json` by one byte and
expect the working-copy-drift `LifecycleError`; `unlink()` the committed
`first-proposal-inputs.json` working copy and expect the sealed-identity
`LifecycleError`; and (optionally) `git commit` a second no-op change touching
`manifests/gig-proposal.json` and expect the multi-publisher refusal.

### F3 — NIT (typed-error consistency): non-canonical Gig document raises raw `ValueError`, not `LifecycleError`

**Where:** `src/gigai/lifecycle.py:1673-1678`. `document_text.decode("utf-8")`
is wrapped for `UnicodeDecodeError`, but the following
`canonicalize_owned_text(document_text)` can raise `InvalidOwnedTextError`
(a `CanonicalizationError`, i.e. a `ValueError`) for a NUL byte, a leading
UTF-8 BOM, or a lone surrogate. That path is **not** converted to the typed
`LifecycleError("first Graph Set Gig document is not canonical Markdown")`
that the surrounding validation uses for every other malformed input.

**Impact:** Library callers of `propose_first_graph_set_offline` get a bare
`ValueError` instead of `LifecycleError` for one class of bad Gig document.
The CLI is unaffected — `graph_set_propose_command` already catches
`ValueError` (`src/gigai/cli.py:2579`) and maps it to
`graph_set_proposal_refused`.

**Proof vs assertion:** No test feeds a NUL/BOM/surrogate Gig document, so
this is code-reading only.

**Smallest correction:** Wrap lines 1677 in
`try/except InvalidOwnedTextError` (or the broader `CanonicalizationError`)
and `raise LifecycleError("first Graph Set Gig document is not canonical
Markdown") from exc`.

### F4 — NIT (latent footgun): `revalidate_source_inputs` calls `safe_source`, which has an append side effect

**Where:** `src/gigai/lifecycle.py:1767-1771` (`revalidate_source_inputs`)
calls `safe_source(member)` for validation, and `safe_source`
(`1590-1596`) unconditionally `source_members.append(...)` on every success.

**Current effect:** Harmless in the code as written. `identity_data`
(`1753-1760`) is built from `source_members` *before* the writer lock is
taken, and `source_members` is not consumed again after
`revalidate_source_inputs` runs (the `"recovered"` branch at 1862-1865
re-reads from disk). So the in-lock re-validation silently doubles
`source_members` with no observable consequence today.

**Failure scenario:** Any future change that reads `source_members` *after*
`publish_first` runs (e.g. to build a post-publish summary, or to re-seal
identity on the recovery path) will get 2× duplicated members, and — because
`identity_data` is `sorted(...)` with no de-dup — could silently change the
sealed identity or a downstream count.

**Smallest correction:** Give the in-lock re-check a pure helper that
validates bytes/size/digest/path without appending — e.g. factor the
`candidate` resolution + `len/digest` check out of `safe_source` into a
`_verify_source_member(ref)` that both call, with only `safe_source`
appending. Alternatively snapshot `len(source_members)` before
`revalidate_source_inputs` and `del source_members[n:]` after.

### F5 — NIT (observation, no change requested): "simultaneous first publication and existing amendment" is proven only sequentially

`test_approved_gig_graph_set_amendment_path_remains_available` proves the
amendment branch still functions after the first-proposal branch was added,
using a *different* Gig with a real approved predecessor. True concurrency is
out of scope for the single-process offline model, and the two branches
cannot both apply to one Gig (first requires no `active-gig-version.json` and
`current` either absent or `kind:"create"/proposed`; amendment requires
`current.status == "approved"` plus a valid `active-gig-version.json`). Both
take the same per-workpad `.git/<LOCK_FILENAME>` writer lock. No change
needed; recording that the compatibility claim is structural + sequential,
not a concurrency test.

## Property-by-property assessment

| Property (from task) | Status | Basis |
|---|---|---|
| First `kind:"create"` proposal, no pre-approved predecessor | **Proven** | Test 1 asserts `pending["kind"] == "create"`; `_write_first_definition` docstring + fixture never approve a predecessor; `_first_proposal` branch forbids an existing `active-gig-version.json` (`lifecycle.py:1519-1520`) and requires `current` absent or `kind:"create"/proposed` (1521-1524). |
| Null `base_gig_version` / `parent_proposal_id` | **Proven** | Test 1 asserts both `is None`; proposal dict literal sets both `None` (`lifecycle.py:1729`); recovery branch re-checks both are `None` on committed authority (1801-1802). |
| Exact target, no / different active selection | **Proven** | `resolve_workpad` with explicit `gig_id` skips both `select_active_workpad` branches (`workpad.py:265-297`); test 1 selects a *different* active Gig then approves the first proposal with `--gig` and asserts the active Gig is unchanged. |
| No active selection mutation | **Proven** | Test 1 (`active_gig_id is None` before/after propose; `== second_gig` after approve of first); test 3 (`not load_project_binding(target).active_gig_id` after CLI approve). Code path never calls `select_active_workpad`. |
| Separate direct confirmation | **Proven (by construction)** | `propose_*` only stages (`status:"proposed"`, body "direct approval is still required"); approval is a distinct `approve_offline` / `gigai approve` call. No auto-approve anywhere in the branch. |
| Source bounds / path / link safety incl. source root resolution | **Code-asserted; unproven** | `definition_path` resolved + non-symlink + regular file (`lifecycle.py:1486-1487`); `source_root = definition_path.parent` (1564); `safe_source` rejects absolute / `\\` / `..`, walks every component for symlinks, rejects symlinked or non-regular target, and re-checks `len`+`digest` (1568-1597). **F1**: no negative test. |
| Exact definition and member digests | **Proven (definition); code-asserted (member tamper)** | `definition_sha256 = digest_imported_bytes(definition_data)` sealed into `first-proposal-inputs.json` (1757); test 2 changes the definition and gets `"changed source inputs"`. Per-member digest mismatch (`safe_source` 1588) is not separately tested. |
| Preflight + writer-lock source re-read | **Proven** | `run_with_journal_writer` holds `_writer_lock` for the whole `publish_first` call (`journal.py:253-254`); `publish_first` first calls `revalidate_source_inputs()` (1774), which re-reads `definition_path` bytes and re-validates each member — inside the lock. Test 2's post-publish edit-then-retry proves the re-read rejects drift. |
| Journal-authenticated replay after interruption | **Proven** | Test 2 injects `RuntimeError` at `after_first_graph_set_proposed`, then an identical retry returns the same `proposal_id` and `git log` shows exactly one publisher of `manifests/gig-proposal.json`. `publish_first` authenticates via `read_committed_artifact` (`journal.py:1044-1088`), not the working tree. |
| Immutable pending authority; malformed / tampered receipt refusal | **Partially proven** | Recovery branch checks committed==working bytes (1788-1790), re-parses and re-checks `gig_id`/`project_id`/`status`/`kind`/`base`/`parent` (1795-1804), requires the sealed `first-proposal-inputs.json` to exist, match committed authority, and equal this call's `identity_data` (1808-1828). Changed-source refusal is tested; direct on-disk tamper of the receipt / committed proposal is not (**F2**). |
| First publication vs existing amendment compatibility | **Proven (sequential / structural)** | Test 4; branches are mutually exclusive per-Gig and share the writer lock (**F5**). |

## Distinguishing proof from schema

- **`gig-proposal-v2.schema.json` does *not* encode the first-proposal
  invariant.** `base_gig_version` is `oneOf {positive_version, null}` and
  `parent_proposal_id` is `oneOf {gig_proposal_id, null}` *independently of
  `kind`*. A `kind:"create"` proposal with a non-null `base_gig_version`
  would pass the schema. The "first proposal ⇒ null predecessor / null base"
  rule is enforced **only in `lifecycle.py`** (the `_first_proposal` branch
  and the `publish_first` recovery re-checks). The implementation handoff's
  claim that "the existing strict schema already represents a `kind:create`
  proposal with null predecessor/base references" is true only in the weak
  sense that the schema *permits* it — it does not *require* it. This is a
  correct design (code owns the cross-field rule) but the reader should not
  treat schema validation as the guarantee.
- **`active-gig-version-v2.schema.json` permits first approved version `1`**
  — `active_version` → `common#/$defs/positive_version` → `minimum: 1`. The
  handoff claim is accurate. `_next_version` returns `1` when no pointer
  exists (`lifecycle.py:2697-2706`). **Proven** by test 1/3 asserting
  `version == 1`.
- **`gig-graph-set.schema.json` fully constrains `shared_policy`** (the
  `#/$defs/policy` object: required `effects`, `required_capability_ids`,
  `provider_eligibility`, `budget`, `additionalProperties:false`). The
  lifecycle code passes `source_set.get("shared_policy")` through unchecked
  (`lifecycle.py:1651`), but `validate_graph_set` against the staging
  tempdir (1664) rejects a malformed policy before any journal write.
  So policy validation is *proven by schema + staging validation*, not by a
  dedicated test.
- **`scout-operation-receipt.schema.json` requires `gig_version` `minimum: 2`**
  (line 121-123). Confirmed on disk. See next section.

## Known integration gate (recorded separately, not a slice blocker)

A first Graph Set Gig is approved as **numeric version `1`**. The existing
`scout-operation-receipt.schema.json` requires `gig_version >= 2`. Therefore a
future Scout **tool receipt** cannot yet be issued against a first-version
Gig. This slice deliberately does **not**:

- materialize editable root software or a real Scout Graph Set body,
- wire init batches to the proposal service,
- bind an approved tool manifest, or
- promote any default to release eligibility.

Per the SCOUT-05 integration plan (goal 1: "Audit schema-family versus
numeric Gig-version assumptions, including first-version tool receipts; do not
weaken unrelated constraints globally") and the implementation handoff
("coordinator integration obligation for later tool-receipt work, not a
reason to forge first-Gig history or loosen the receipt schema"), this is the
**correct** disposition. The gate belongs to the later tool-receipt slice,
which must either (a) raise the first eligible Gig version before a receipt is
possible, or (b) adjust the receipt schema's `gig_version` floor with its own
review. It is **not** a defect in, or a blocker for, staging the first
proposal.

## Scope statement

No source, test, or schema files were modified. No probe was run — every
finding is supported by reading the frozen files plus the recorded 5-case
evidence. No provider call, `init`, approval, activation, or commit was
performed. Only this file
(`docs/development/evidence/v0.1.7/Scout/SCOUT-05-first-proposal-review.md`)
was written.
