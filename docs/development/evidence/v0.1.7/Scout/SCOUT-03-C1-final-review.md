# SCOUT-03 C1 — Independent final review

**Date:** 2026-09-08
**Reviewer:** fresh dispatched Claude worker, independent of both C1 implementers
and the prior independent reviewer.
**Verdict:** **Accept.** No C1 blocker survives in the settled source. The six
prior high-severity findings (C1-F1..F6) and the four consolidated risks
(R1, R2, R6, R7) are resolved or are explicitly out-of-scope per the accepted
second-pass disposition. The 11 coordinator acceptance tests genuinely exercise
the claims. Two low-severity, non-blocking observations are recorded at the end.

**Inputs read:** `SCOUT-03-C1-second-pass.md`, `SCOUT-03-C1-second-implementation.md`,
`SCOUT-03-C1-review.md` (prior independent), `SCOUT-03-C1-coordinator-verification.md`,
`SCOUT-03-C1-combined-verification.md`, `SCOUT-03-correction-plan.md`.
**Source read (source-only, NO tests run):** settled working-tree
`src/gigai/private_records.py` (untracked), `src/gigai/journal.py`,
`src/gigai/index.py`, `src/gigai/workpad.py`, `src/gigai/proposal_interview.py`
(G22 trace paths), `src/gigai/cli.py` (record group), `src/gigai/diagnostics.py`
(`journal.index` check), the five C1 schemas, `SHA256SUMS`,
`tools/verify_installed_schemas.py`, `validators.py`. Tests read for coverage:
`tests/test_scout03_c1_acceptance.py` (11), `tests/test_scout03_private_records.py`
(4), diffs of `tests/test_journal_locking_recovery.py`, `tests/test_index_projection.py`,
`tests/test_g22_proposal_interview_contract.py`.
**Coordinator combined offline suite (reported):** 782 passed, 1 live-model skip,
104 subtests, exit 0 in 504.40s.

**Scope discipline:** C2 (native profiles / default-vs-override / archive / CRUD)
and C3 (tool binding / Plan selection / provider ingress) are intentionally
unimplemented and are **not** reported as C1 failures. Additive registrations
made by parallel lanes are separated from C1 logic in the final section and are
not evaluated as C1 omissions.

---

## Pinned committed enumeration and exact identity/media/path authentication

**Requirement (second-pass items 1, 3):** one committed snapshot of the private
families under the writer lock; enumeration from committed journal history, not
`glob`; every working path component and its bytes checked against pinned
evidence; missing / redirected / changed / extra uncommitted evidence diagnosed,
never silently omitted.

**Settled behavior — confirmed resolved.**

- `journal._capture_committed_snapshot` (`journal.py:1071-1118`) is the single
  enumeration primitive. It reads `git ls-tree -r -z --name-only <HEAD> -- <prefixes>`
  at one pinned `HEAD` (`:1084-1086`), authenticates every listed path through
  `read_committed_artifact` (`:1091-1093`), then **cross-checks the working tree
  both directions**: each committed path must be a regular file whose bytes equal
  the committed bytes (`:1100-1101` → `"journal working evidence differs from
  committed bytes"`), no path component may be a symlink (`:1096-1099`), and an
  `os.walk(followlinks=False)` over each family root rejects any extra or
  redirected working file not in the committed set (`:1109-1117` → `"journal
  working evidence is extra or redirected"`).
- `JournalWriter.snapshot` (`journal.py:203-208`) exposes it only while the
  private-Git `_writer_lock` is held (`run_with_journal_writer`, `:233-234`).
- `private_records._private_snapshot` (`private_records.py:193-202`) captures
  `("records/", "references/", "run-inputs/")` under that lock and is the sole
  source for `list_imports`, `read_import`, `list_revisions`, `read_record`,
  `_content_for_reference`, `rebuild_scout_projection`, and the in-critical-section
  `_publish` lookup. No `Path.glob` over the mutable tree drives any C1
  read/CAS/rebuild decision anymore. (`sorted(item for item in
  snapshot.artifacts …)` iterates the already-authenticated committed set.)
- Per-artifact identity: `read_committed_artifact` (`journal.py:1024-1068`)
  requires exactly one publishing commit (`:1045-1046`), exactly one handoff in
  that commit with the path present (`:1050-1051`), reads bytes from the git
  object at the pinned head (`:1054`, `_git_bytes`), and cross-checks the
  handoff `artifact_refs` `content_sha256` **and** `size_bytes` against those
  bytes (`:1066-1067`) plus `gig_id` (`:1057-1058`). Schema validation of the
  decoded record is layered on top in `_committed_json` (`private_records.py:213`).
- Unrelated editable Gig source is **not** required to be globally clean: the
  snapshot walk is bounded to the three private prefixes only.

Coverage: `test_extra_uncommitted_private_evidence_refuses_publication`
(`match="extra"`), `test_hidden_current_revision_cannot_bypass_parent_cas`
(`match="working evidence"`), `test_missing_committed_revision_cannot_silently_revert_current`,
`test_committed_bytes_parent_chain_and_projection_recovery`
(`match="working evidence differs"`). These match the prior C1-F1/F2/F3 defects
and now pass against the settled primitive.

## Generated-ID replay / equivalent-key semantics

**Requirement (second-pass item 2):** hash caller-supplied semantic intent
before allocating IDs; omitted IDs must not become random conflict inputs;
matching requests return original IDs and strict receipt; equivalent-import
handling across distinct keys must not forget an accepted key or permit
duplicate content publication; all decision checks inside the critical section.

**Settled behavior — confirmed resolved.**

- `create_record` builds `intent = {"parent_revision","kind","origin","actor",
  "content"}` (`private_records.py:450`) and only adds `record_id` to the
  conflict identity **when the caller passed one explicitly** (`:451-452`).
  Generated `stable_id` / `revision_id` are never in the hashed payload on an
  omitted-ID retry. `content` is deterministic — `_content_for_reference`
  returns `record_ref = _ref(record_path, committed_bytes, …)` and
  `snapshot_ref = dict(committed_snapshot)` (`:428`), both derived from
  committed bytes.
- `_publish` hashes `payload_sha = digest_imported_bytes(canonical_json_bytes(
  payload))` (`:247`) and, inside the `publish(writer)` closure that runs under
  the writer lock, calls `_existing_receipt` first (`:250`): identical retry →
  same `operation_key` → same `_receipt_path` → committed receipt found →
  `payload_sha` matches → returns `(existing, False)` with the original
  `operation_id`/`artifact_refs`; the caller then re-reads the committed
  revision and returns the **original** `record_id`/`revision_id`
  (`:454-459`). No second `writer.record`, no new commit.
- Changed actor / origin / media / content / parent → different `payload_sha`
  → `_existing_receipt` raises `private_operation_conflict` ("operation key was
  already used with different payload", `:231`).
- Equivalent-import across **distinct keys**: `import_reference` /
  `import_run_input` pass an `equivalent_artifact_path` callback
  (`:320-325`, `:357-362`) checked inside the critical section after
  `_existing_receipt` (`_publish:253-259`). A different `operation_key` whose
  content digest + project + kind + media match a committed import resolves to
  that import's path, `_receipt_for_artifact` finds its receipt, and the call
  returns `(existing, False)` — no duplicate content commit. If the equivalent
  artifact somehow lacks its receipt, it raises `private_operation_conflict`
  rather than publishing (`:257-258`).
- All lookup / equivalence / parent-CAS / receipt-mint / `writer.record` steps
  execute inside the one `run_with_journal_writer` closure.

Coverage: `test_generated_record_identity_replays_original_receipt` (asserts
`created is False`, identical `(record_id, revision_id, receipt)`, and unchanged
`git HEAD`); `test_private_import_revision_context_and_stale_parent_are_journaled`
(import with `operation_key="ref-2"` after `"ref-1"` returns `created is False`,
same `item_id` — distinct-key equivalence). This matches prior C1-F5.

## Exact content read (no unauthenticated working-file substitution)

**Requirement (second-pass item 3):** validate the wrapper's complete selected
record and snapshot references against canonical committed originals, then return
those authenticated bytes; never validate git bytes then return an unchecked
working-file read; reject tampered working evidence.

**Settled behavior — confirmed resolved.**

- `read_record(content=True)` resolves the revision from the pinned snapshot
  (`private_records.py:506-508`), then calls `_content_for_reference(resolved,
  family, item_id, snapshot=selected)` and returns
  `authenticated_bytes` from that call (`:520-521`) — it no longer performs any
  `(resolved.path / snapshot["path"]).read_bytes()` working-tree read (the
  `private_records.py:495` path from the prior review is gone).
- `_content_for_reference` (`:404-428`) pulls the record and the snapshot
  source **from `snapshot.artifacts` only** (`:412`, `:423`), i.e. the committed
  bytes captured by `_capture_committed_snapshot`. It re-checks
  `digest_imported_bytes(data) == snapshot["content_sha256"]` and
  `len(data) == snapshot["size_bytes"]` (`:426-427`) and `project_id` scope
  (`:415-416`) before returning. Because `_capture_committed_snapshot` already
  refused the snapshot if the working `source.txt` differed from committed
  bytes, a tampered working file makes the read raise before this point.

Coverage: `test_content_read_rejects_modified_snapshot` (writes
`b"UNCOMMITTED replacement\n"` to `source.txt`, expects `PrivateRecordError`);
`test_committed_bytes_parent_chain_and_projection_recovery` (shaped uncommitted
`reference.json` → `match="working evidence differs"`). Matches prior C1-F4.

## CAS at the locked HEAD

**Requirement (second-pass items 2, 3):** parent CAS resolved from committed
journal history at the locked HEAD, not `glob`; stale concurrent updates yield
one winner and one typed conflict; no duplicate or branched authoritative
artifact.

**Settled behavior — confirmed resolved.**

- Inside `_publish`'s `publish(writer)` closure, the parent check calls
  `list_revisions(resolved=resolved, record_id=parent_record_id,
  snapshot=snapshot)` (`private_records.py:261`) with the **critical-section
  snapshot**, then compares `revisions[-1]["revision_id"]` to the caller's
  `parent_revision` (`:262-266`), raising `stale_parent` on mismatch.
- `list_revisions` (`:463-501`) builds the chain purely from
  `snapshot.artifacts` committed bytes, follows authenticated `parent_revision`
  links (`:486-498`), and rejects ambiguous roots, branches, cycles, and broken
  chains (`:482-500`) — no lexical UUID ordering.
- A deleted head-revision working file no longer lets a stale parent slip
  through: `_capture_committed_snapshot` fails closed before the CAS is even
  evaluated (`test_hidden_current_revision_cannot_bypass_parent_cas` — the
  publish raises `match="working evidence"` and `git HEAD` is unchanged).
- Immutable no-clobber: `_publish` calls `writer.record(JournalTransition(...))`
  **without** `allow_artifact_replacement=True`, so `JournalWriter.record`
  defaults it to `False` (`journal.py:184`), and `_preflight_artifact_destinations`
  refuses a pre-existing immutable path *before* writing recoverable transaction
  state (`journal.py:930-948` → `"journal immutable artifact already exists"`).

Coverage: `test_subprocess_same_parent_has_one_winner` (two spawned processes,
same parent → `sorted(outcomes) == ["ok", "stale_parent"]`, both exit 0);
`test_immutable_artifact_refusal_precedes_transaction_intent` (new in
`test_journal_locking_recovery.py` — refusal precedes transaction manifest,
bytes and HEAD unchanged); `test_committed_bytes_parent_chain_and_projection_recovery`
(revision UUIDs in lexical-reverse order still order by authenticated parent).

## Post-commit receipt / result separation

**Requirement (second-pass item 5):** once publication succeeds, a projection
exception yields committed + pending/rebuild metadata **on the operation
result**; the receipt object and its committed bytes stay unchanged and
schema-valid; retry/rebuild repairs without another domain event; process
termination is not swallowed and the projection diagnostic is not hidden.

**Settled behavior — confirmed resolved.**

- `_publish` seals authority first (`writer.record`), then calls
  `rebuild_scout_projection` in a `try/except Exception` (`private_records.py:297-304`).
  On exception it returns `(receipt, created, pending=True)`; callers translate
  that into `ImportResult` / `RevisionResult` fields `projection_pending=True`,
  `rebuild_action="rebuild_index"` (`:336`, `:373`, `:460`).
- The `receipt` dict is built and committed once (`_publish:268-283`); it never
  receives `projection_pending` / `rebuild_action`. The schema
  `scout-operation-receipt.schema.json` is `additionalProperties:false` with no
  such keys, so even an accidental merge would fail validation.
- Retry path: `_publish` `not created` branch also runs
  `rebuild_scout_projection` (`:289-296`) but never re-invokes `writer.record`
  — repair without a new domain event; `git HEAD` unchanged.
- `except Exception` does not catch `KeyboardInterrupt` / `SystemExit`
  (`BaseException`), so process termination propagates. The underlying error is
  surfaced as `projection_pending` + an explicit `rebuild_action`, not
  swallowed silently.

Coverage: `test_post_commit_runtime_failure_returns_pending_result`
(`monkeypatch` `rebuild_scout_projection` → `RuntimeError`; asserts
`result.created and result.projection_pending`, `result.receipt is not None`,
`"projection_pending" not in result.receipt`, `"rebuild_action" not in
result.receipt`); `test_committed_bytes_parent_chain_and_projection_recovery`
(projection raises `OSError` → `pending.projection_pending is True and
pending.receipt is not None`). Matches the coordinator's fourth verification row.

## Coherent managed views: closed DB object shape, G22 preservation, cursor, HEAD pinning

**Requirement (second-pass item 4):** Scout records AND operation-receipt
projection built at one pinned HEAD; database serialized in `journal -> database`
lock order; a closed database object inventory and full table shape validated
under that lock — unexpected triggers / views / indexes / constraints or
malformed tables cannot execute supplied SQL or delete G22 history; supported
internal indexes and current G22 connections preserved; database inode not
replaced; safe unique context staging with parent-component validation; cursor
mismatch repairs or reports staleness.

**Settled behavior — confirmed resolved for the C1 obligations.**

- Single pinned HEAD: `rebuild_scout_projection` captures `selected =
  _private_snapshot(resolved)` once and uses `journal_head = selected.head`
  (`private_records.py:526-527`). Both `records` and `operations` are derived
  from `selected.artifacts` (`:528-547`); `scout_records`, `scout_operations`,
  `scout_meta.context`, and `scout_meta.cursor` are all written from that one
  snapshot (`:552-566`). The prior R1 TOCTOU (HEAD read separately from the
  working-tree walk) is gone — the snapshot pins both.
- Lock order: the entire mutation runs inside `with database_lock(resolved.path):`
  (`:549`), and `database_lock` is documented and used as *journal-lock-then-
  database-lock*; `_publish` releases the journal writer lock before calling
  `rebuild_scout_projection`, so there is no inverse acquisition. `validate_state_database`
  now runs **inside** the lock (`:550`), closing the prior R6 window.
- Closed object inventory: `validate_state_database` → `_read_scout_tables`
  (`index.py:250-293`) reads `SELECT type, name, tbl_name FROM sqlite_master
  WHERE name NOT LIKE 'sqlite_autoindex_%'` and rejects **any** row that is not
  a `table` named in `{projection, interview_events, scout_records,
  scout_operations, scout_meta}` (`:269-281` → `"state database has unsupported
  executable objects"` / `"... unsupported tables"`). A trigger, view, or
  extra table fails here *before* the read-write connection is opened and
  before any `DELETE`. Each managed Scout table's columns must be exactly
  `("key","payload")` (`:286-288`). `_read_interview_events` separately
  validates the `interview_events` column shape against
  `_INTERVIEW_EVENTS_COLUMNS` (`index.py:236-241`) and refuses a malformed DB
  rather than replacing it.
- I verified the closed allow-set is complete for the current codebase: only
  `index.py` (`projection`), `proposal_interview.py` (`interview_events`), and
  `private_records.py` (`scout_records` / `scout_operations` / `scout_meta`)
  write workpad `state.sqlite`. No false-positive risk against a legitimate
  workpad.
- G22 preservation: the rebuild only `CREATE TABLE IF NOT EXISTS` + `DELETE` +
  `INSERT` on the three `scout_*` tables of the existing inode (`:552-567`);
  `interview_events` rows are untouched. `index._write_projection` likewise
  mutates only `projection` on the existing inode under `BEGIN IMMEDIATE`
  (`index.py:167-198`) after the same schema gate. The database inode is never
  replaced by either path.
- Context staging: `tempfile.mkstemp(prefix=".context-", suffix=".tmp",
  dir=index_path)` gives a bounded-unique name (`private_records.py:574`); the
  `indexes` root is guarded for symlink / non-dir before `mkdir`
  (`:570-573`); `os.fdopen` + `fsync` + `os.replace` publishes atomically
  inside the same `database_lock` region (`:576-583`). The prior C1-F6 fixed
  `.context.tmp`, symlink-follow, and outside-lock publish are all gone.
- Cursor: `scout_meta.cursor` is rewritten from the same pinned `journal_head`
  on every rebuild, and `rebuild_scout_projection` is invoked after every
  successful publish and on every retry (`_publish:289-304`). It is therefore
  kept coherent, not decorative. There is no *separate* "read the projection
  and detect staleness" path in C1 because every C1 authority read
  (`read_record`, `list_imports`, `list_revisions`, …) reads committed history
  directly via `_private_snapshot` and never consults `scout_meta` /
  `context.json`. An explicit projection-consuming `rebuild` / `context` CLI
  command is C2 scope (correction plan C2: "explicit rebuild/context commands";
  R7 disposition: receipts-not-projected / `scout_operations` seam). This is
  acceptable for C1.

Coverage: `test_projection_rejects_trigger_without_destroying_trace` (seeds one
`interview_events` row + an `AFTER DELETE ON scout_records` trigger; asserts the
rebuild raises `JournalIndexError` and the row count is still 1);
`test_context_staging_never_follows_preexisting_symlink` (pre-seeds
`indexes/.context.tmp` as a symlink to an outside file; asserts the outside file
is byte-unchanged); `test_index_repair_preserves_g22_interview_trace`
(unmodified, still exercises `_write_projection` trace preservation);
`test_private_import_revision_context_and_stale_parent_are_journaled` (context
JSON contains the revision id and does **not** contain the private content
bytes). Matches the coordinator's fifth and sixth verification rows.

## Recoverable layout and legacy writes: normal-vs-explicit migration recovery, legacy mutable recovery

**Requirement (second-pass items 6, and the two added recovery probes):** normal
admission stays strict; only explicit recovery may validate a prepared,
scope-bound migration intent against the committed v1 parent and exact
marker/ignore artifacts before completing it; never turn an arbitrary uncommitted
marker into authority; restore the original supported replacement behavior for
legacy mutable transactions while keeping no-clobber for immutable private
records; cover interruption before and after artifact publication and
ambiguity/refusal paths; preserve ignored drafts; refuse new-root collisions
safely.

**Settled behavior — confirmed resolved.**

- Interrupted-migration recovery reachability (the second added probe): normal
  `_validate_workpad` rejects a workpad whose v2 marker is on disk but
  uncommitted (`workpad_layout_version` refuses a marker with `!= 1` publisher).
  `reconcile_journal` now wraps the admission call in
  `try: root = _validate_workpad(...) except JournalConflictError: root =
  _validate_recovery_migration_root(...)` (`journal.py:416-422`).
  `_validate_recovery_migration_root` (`:576-613`) is the sole path that may
  inspect the prepared intent: it re-checks Git ownership config markers, no
  remote, loads the transaction manifest for the next sequence, requires
  `transaction.transition == "workpad_layout_migrated"`, requires both the
  `.gitignore` and `manifests/workpad-layout.json` artifacts present with the
  ignore bytes **exactly** `WORKPAD_V2_GITIGNORE` and the marker payload
  **exactly** the canonical `{schema_version:"2.0", layout_version:2,
  project_id, gig_id, ignore_sha256}` dict (`:597-612`). Any deviation raises
  `JournalReconciliationRequired`. An arbitrary uncommitted marker cannot become
  authority: its payload/ignore/transition must match the pinned expected
  values, and `_load_transaction_manifest` independently digest-verifies every
  artifact's base64 content (`journal.py:854-864`).
- `_restore_transaction` migration branch (`journal.py:884-890`): splits the
  transaction into `.gitignore` (replaceable) and everything else (no-clobber
  via `_replace_artifacts(..., allow_replacement=False)`), so recovering a
  migration cannot overwrite an immutable private artifact and cannot clobber a
  pre-existing conflicting marker's bytes (it raises
  `JournalReconciliationRequired` on a byte conflict).
- Legacy mutable recovery (the first added probe): for any non-migration
  transition, `_restore_transaction` calls `_replace_artifacts(root,
  transaction.artifacts, allow_replacement=transaction.allow_artifact_replacement)`
  (`journal.py:891-896`). `record_transition` defaults
  `allow_artifact_replacement=True` and `_write_transaction_manifest` persists
  it (`journal.py:775-814`); `_load_transaction_manifest` restores it with a
  strict `bool` check (`:854-856`). A legacy mutable `manifests/*.json` update
  interrupted at `after_transaction_prepare` therefore reconciles to the *new*
  bytes, restoring the pre-Scout behavior, while private immutable publication
  (`_publish` → `record` with `allow_artifact_replacement=False`) stays
  no-clobber.
- Normal admission stays strict: `_validate_workpad` / `_validate_workpad_repository`
  still reject an uncommitted v2 marker outside the recovery path; nothing in
  the normal `record` / `snapshot` / read paths calls
  `_validate_recovery_migration_root`.
- Migration collision refusal: `migrate_workpad_layout` refuses when any of
  `README.md, CHANGELOG.md, gig.py, goalgraphs, ui, docs, references,
  run-inputs, records, indexes` already exists or is a symlink
  (`private_records.py:134-140`). I enumerated every net-new-in-v2 root
  (`workpad._V2_ROOTS`) and confirmed each is either in this collision list or
  is a root already legitimately owned by v1 semantic state
  (`_validate_workpad_repository` `allow_semantic_state` set, plus `tools/` and
  `reports/` which the R3 disposition explicitly keeps allowed). No net-new v2
  root is silently admitted post-migration. Collision is a typed refusal, not a
  delete/relocate — no data loss.
- R4 (text-mode git round-trip): `workpad_layout_version` uses `_git_bytes` for
  all three committed object reads — the handoff front matter
  (`workpad.py:565`), the marker (`:567`), and `.gitignore` (`:568`). Text-mode
  `.stdout` is used only for commit-hash and path-name listings, which cannot
  carry `\r`-sensitive content. Resolved.

Coverage: `test_interrupted_layout_migration_can_reconcile` (interrupt at
`after_artifact_replace`, then `reconcile_journal` reconciles and
`workpad_layout_version == 2`); `test_legacy_mutable_artifact_recovery_remains_supported`
(interrupt a mutable `manifests/*.json` update at `after_transaction_prepare`,
reconcile, assert new bytes `b'{"value":2}'`);
`test_v1_tools_and_reports_do_not_block_layout_migration`;
`test_immutable_artifact_refusal_precedes_transaction_intent`;
`test_v1_workpad_refuses_private_import_until_explicit_migration` (`match="migrate"`).
These match the two corrected recovery probes in the second-pass brief.

## G22 trace integration (`proposal_interview.persist_trace` / `_persist_interview_trace`)

**Settled behavior — matches the accepted R2 disposition.**

- `persist_trace` (`proposal_interview.py:557-612`): when no `workpad` is
  passed, it infers one from `PRAGMA database_list` and takes `database_lock`
  **only** when the connection is file-backed inside a `.git` workpad
  (`:566-575`); an in-memory or out-of-tree connection is written without the
  lock. The second-pass brief R2 disposition explicitly endorses this
  ("R2 does not justify breaking standalone/in-memory interview traces … Prove
  locking and preservation for actual file-backed shared G22/HTTP connections;
  do not invent a lock requirement for an unrelated in-memory database"). No
  `src/` caller passes an in-memory connection to `persist_trace`, so the
  earlier "latent gap" (prior C1-R2) is not a live bug and is dispositioned,
  not a blocker.
- The `interview_events` table shape written here (`:578-582`) is exactly what
  `index._INTERVIEW_EVENTS_COLUMNS` validates, so the closed-schema gate and
  the trace writer agree.
- `_write_projection` and `rebuild_scout_projection` both read the trace shape
  before mutating and never touch `interview_events` rows, so a long-lived G22
  HTTP connection on the shared inode keeps its rows across an index or Scout
  rebuild.

## Existing test coverage of the claims — confirmed adequate

The 11 tests in `tests/test_scout03_c1_acceptance.py` each bind to a specific
C1 claim and, per the second-pass brief and coordinator verification, each
reproduced a real failure in the first C1 handoff:

| Test | Claim exercised |
| --- | --- |
| `test_generated_record_identity_replays_original_receipt` | omitted-ID replay returns original IDs + receipt, no new commit |
| `test_content_read_rejects_modified_snapshot` | `read_record(content=True)` refuses tampered working `source.txt` |
| `test_missing_committed_revision_cannot_silently_revert_current` | deleted head-revision file → diagnosed, not silent revert |
| `test_hidden_current_revision_cannot_bypass_parent_cas` | deleted head-revision file → CAS fails closed, HEAD unchanged |
| `test_extra_uncommitted_private_evidence_refuses_publication` | extra uncommitted `records/operations/*.json` → refuse |
| `test_post_commit_runtime_failure_returns_pending_result` | post-commit projection error → committed + pending on result, receipt clean |
| `test_context_staging_never_follows_preexisting_symlink` | context staging does not follow a pre-seeded symlink |
| `test_projection_rejects_trigger_without_destroying_trace` | closed DB inventory refuses a trigger; G22 row preserved |
| `test_legacy_mutable_artifact_recovery_remains_supported` | legacy mutable transaction recovers with replacement |
| `test_interrupted_layout_migration_can_reconcile` | migration interrupted after artifact replace reaches recovery |
| `test_v1_tools_and_reports_do_not_block_layout_migration` | legitimate v1 `tools/` + `reports/` do not block migration |

Assertions are real acceptance checks (byte content, `git HEAD` equality,
row counts, `pytest.raises` with message matches) — none are xfail / skip /
assert-around-the-bug. Wider coverage in `tests/test_scout03_private_records.py`
(committed-bytes parent chain, non-monotonic UUID ordering, subprocess CAS,
distinct-key equivalence, projection-failure pending) and the
`test_journal_locking_recovery.py` / `test_index_projection.py` diffs
(immutable-refusal-precedes-intent; `read_index` now *refuses* a malformed
`state.sqlite` instead of silently rebuilding) reinforce the same guarantees.
The coordinator's reported combined offline run (782 passed / exit 0) shows the
`read_index` behavior change did not regress the broader suite.

## Additive registrations by parallel lanes — separated from C1 logic

Per the task brief and the coordinator's "Exact additive changes outside C1
logic" note, the following are additive registrations for other in-flight lanes
(multi-graph / G43), **not** C1 logic, and are not evaluated as C1 omissions:

- `canonical.EntityPrefix`: `GRAPH_SET`, `GRAPH_SELECTION` (multi-graph);
  `CHECKPOINT`, `RECEIPT`, `TASK_CONTEXT` (registered, unused by C1 — future
  seams). C1 uses only `REFERENCE`, `RUN_INPUT`, `RECORD`, `REVISION`,
  `OPERATION`, `LAYOUT`, `HANDOFF`.
- `canonical` ID regex: the added `graph_set|graph_selection` alternatives are
  multi-graph; `ref|input|record|revision|operation|layout` are C1.
- `journal.TRANSITIONS`: `gig_graph_set_proposed` is multi-graph;
  `workpad_layout_migrated`, `private_reference_imported`, `run_input_imported`,
  `private_record_revised`, `private_record_archived` are C1.
- Schemas: `gig-graph-set`, `gig-proposal-v2`, `active-gig-version-v2`,
  `graph-selection-record`, `graph-selection-record-v2` (timestamped 11:11-11:23)
  are multi-graph. The five C1 schemas (`reference-record`, `run-input-record`,
  `private-record-revision`, `scout-operation-receipt`, `workpad-layout`,
  timestamped 15:53) are consistently registered across `SHA256SUMS`,
  `tools/verify_installed_schemas.py`, `validators.SCHEMA_NAMES`, and
  `schemas/README.md`; I recomputed all five SHA-256 digests and they match
  `SHA256SUMS`. The `SCHEMA_NAMES == 49` bumps across the G15/G21/G26/G22
  contract tests are the mechanical total (36 base + 5 C1 + 8 multi-graph).
- `src/gigai/graph_set.py`, the `graph-set` CLI subcommand, and
  `src/gigai/private_records.py`'s two-implementer boundary: C1-owned source is
  `private_records.py` + the diffs to `journal.py` / `index.py` / `workpad.py`
  / `proposal_interview.py`; `graph_set.py` is a different lane's new module.

## Non-blocking observations (low severity, no C1 blocker)

### O1 — malformed `state.sqlite` is now unrecoverable via `read_index` / `doctor` without a manual `rm`

**Severity:** Low. **Confirmed in source.** `tests/test_index_projection.py`
was changed so `read_index` against a corrupt `state.sqlite`
(`b"not a SQLite database"`) now raises `JournalIndexError("...malformed")`
(`index.py:_read_interview_events:222-225,232-233`) instead of silently
rebuilding. This is **correct and intentional** per correction plan item 5
("never silently erase malformed trace/unknown tables … legacy trace loss must
refuse with an actionable recovery diagnostic") — the reader cannot distinguish
"torn projection write" from "corrupt G22 trace", so it fails closed. However,
`diagnostics.py:246-254` reports the failure with remediation text "Repair the
authoritative journal; do not trust or edit state.sqlite as a substitute",
which does not tell the operator that `state.sqlite` is `.gitignore`d and fully
rebuildable and that removing it lets `read_index` recreate it. Suggest (C2 or a
follow-up, not a C1 blocker): make the `journal.index` FAIL remediation name the
safe recovery step for the *no-trace-table* case, or add an explicit
`gigai workpad reindex --force` that removes a byte-corrupt (non-openable)
`state.sqlite` after confirming no `interview_events` table is readable.

### O2 — retry path always does a full `rebuild_scout_projection`

**Severity:** Info. `_publish`'s `not created` branch unconditionally calls
`rebuild_scout_projection` (`private_records.py:289-296`) even when the
projection is already current. This is the documented bounded recovery path for
a prior projection failure and is correct; it is an efficiency note only (a full
snapshot capture + DB rewrite on every idempotent retry). `SCOUT-03-C1-review.md`
R5 already flagged per-transition `workpad_layout_version` cost in the same
spirit; both are explicitly deferred by the second-pass brief ("R5 caching
optimization is not required in this pass").

### O3 — distinct-key equivalence "does not forget an accepted key" is covered only transitively

**Severity:** Info. The brief item 2 phrase "does not forget a previously
accepted key" is exercised by import-key-A-then-key-B returning the original
(`test_private_import_revision_context_and_stale_parent_are_journaled:56-57`),
but there is no explicit test for *re-invoking key A again after key B* still
returning key A's own committed receipt. The code path is sound
(`_existing_receipt` keys on the receipt path derived from `operation`+`key`, and
key A's receipt was committed on its first call), so this is a test-coverage
completeness note, not a defect.

---

## Conclusion

Every high-severity finding from `SCOUT-03-C1-review.md` (C1-F1..F6) and every
C1-obligation risk called out in the second-pass brief (R1, R6, R7) and the
coordinator verification (six rows) is resolved in the settled source; R2, R3,
R4, R5 are handled per the accepted dispositions. The 11 coordinator acceptance
tests and the wider C1 suites genuinely bind to the claims with byte-level and
commit-count assertions. The three observations above are low severity and do
not block C1.

**This review accepts the bounded C1 storage correction.** It makes **no claim**
about full SCOUT-03 acceptance: C2 (native record content, default-vs-override,
archive, agent-readable CRUD, explicit rebuild/context commands) and C3 (tool
binding, exact input resolution into the Plan boundary, private-provenance
enforcement at Plan/Run/provider ingress, clean-package guard) remain
unimplemented and must each satisfy their own contract and review before the
original SCOUT-03 acceptance rows can be signed off. The coordinator's separate
independent verification against this settled source and the completed offline
suite should be the final gate before dispatching C2.
