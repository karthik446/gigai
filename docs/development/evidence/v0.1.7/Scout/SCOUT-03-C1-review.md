# SCOUT-03 C1 — Independent Claude review

**Date:** 2026-09-08
**Reviewer:** dispatched Claude worker (independent of the C1 implementation worker).
**Inputs read:** accepted `SCOUT-03-correction-plan.md` (C1 section + review
clarifications), `SCOUT-03-C1-implementation.md`, settled working-tree source
`src/gigai/private_records.py` (untracked), and the working-tree diffs of
`src/gigai/journal.py`, `src/gigai/index.py`, `src/gigai/workpad.py`,
`src/gigai/proposal_interview.py`, `src/gigai/lifecycle.py`. Existing tests
`tests/test_scout03_private_records.py`, `tests/test_scout03_c1_acceptance.py`,
`tests/test_journal_locking_recovery.py` were read. **No tests were run** (root
holds a focused suite). **No source, schema, test, ledger, or private data was
edited.**

**Scope discipline:** C2 (native profiles / default vs. override / archive /
CRUD) and C3 (tool binding / Plan selection / provider ingress) are
intentionally unimplemented and are **not** reported as C1 failures below.
Findings are limited to C1: storage / journal / layout / projection / database
consistency and their coordination.

## Verdict

The C1 handoff delivers real, well-shaped primitives —
`read_committed_artifact` (byte-authenticated single-publisher reads),
`run_with_journal_writer` / `JournalWriter` (lookup + CAS + publish under one
writer lock), `database_lock` + in-inode `_write_projection` transaction
(preserves G22 trace connections), `workpad_layout_version` (committed-marker
admission), and the `workpad_layout_migrated` recovery branch in
`_restore_transaction`. The G22 trace paths (`_persist_interview_trace`,
`persist_trace`) now take the documented `journal -> database` lock order.

However, **the central C1 requirement is not met**: authenticated *reads* are
byte-checked against committed publication, but **enumeration of what exists —
the set of revisions, receipts, imports and projection records — is still
driven entirely by `Path.glob` over the mutable working tree**, never by
committed journal history. Every C1 read/CAS/rebuild path inherits this. Five
concrete defects follow from it or from the disposable-context staging code,
four of which match the acceptance cases root is already running. All are
bounded and fixable without touching C2/C3.

Recommendation: **another C1 correction pass is required** before C2 dispatch.

---

## Confirmed source findings

### C1-F1 — Enumeration is not pinned to committed journal history (root cause)

**Severity:** High. **Confirmed in source.**

`private_records.py` authenticates individual artifacts through
`_committed_json` -> `read_committed_artifact`, but decides *which* artifacts to
authenticate by globbing the working tree:

- `_receipt_for_artifact` — `records/operations/*.json` (`private_records.py:214`)
- `import_reference.equivalent` — `references/ref_*/reference.json` (`:295`)
- `import_run_input.equivalent` — `run-inputs/input_*/input.json` (`:332`)
- `list_imports` — `references|run-inputs` glob (`:361`)
- `list_revisions` — `records/<id>/revisions/revision_*.json` (`:442`)
- `rebuild_scout_projection` — `records/record_*` (`:507`)

Adding a spurious file is safely rejected (it fails committed authentication).
**Removing or hiding a committed working-tree file silently drops it from the
set**, because nothing cross-checks the glob result against
`git log -- <path>` / the operation receipts / a pinned tree at HEAD. `.gitignore`
does not protect these paths (records/references/run-inputs are tracked), and a
`git checkout`-clean tree is not required by these functions.

**Reproduction (realistic):** an agent or tool with workpad filesystem access
(the same trust level SCOUT-03 already assumes for imports) or an interrupted
`git` operation deletes `records/<id>/revisions/<current>.json` from the
working tree. It remains fully committed in the journal. Subsequent
`list_revisions` returns only `[…, <previous>]`.

**Consequences:** drives C1-F2, C1-F3, C1-F4 below; also lets equivalent-import
dedup miss a hidden receipt and re-publish a duplicate import, and lets
`rebuild_scout_projection` emit a projection that omits real records.

**Bounded correction:** derive the candidate set from committed history —
`git log --diff-filter=A --name-only -- <prefix>` (or enumerate the operation
receipts, which already name every published artifact), then authenticate each
with `read_committed_artifact`. Fail closed (typed error) when a
committed-but-absent working-tree file is encountered rather than skipping it.

---

### C1-F2 — `list_revisions` silently reverts to a stale "current" revision

**Severity:** High. **Confirmed in source.** Matches acceptance case
`test_missing_committed_revision_cannot_silently_revert_current`.

`read_record(..., revision_id=None)` returns `revisions[-1]`
(`private_records.py:480`). With the head revision file removed from the working
tree (C1-F1), `list_revisions` returns a chain `[root]` (or `[…, prev]`) that is
*internally consistent* — one root, no branch, `len(seen) == len(values)` —
so no error is raised (`:452-474`). `read_record` then serves the superseded
revision as authoritative with no diagnostic.

**Bounded correction:** as C1-F1 — enumerate revisions from committed history;
if the committed set has a revision whose working-tree file is missing, raise
`private_record_not_found` / a recovery diagnostic instead of returning a
truncated chain.

---

### C1-F3 — Working-tree revision deletion is a writer-side CAS bypass

**Severity:** High. **Confirmed in source.** Sharper consequence of the same
root cause; not covered by `test_subprocess_same_parent_has_one_winner`
(that test keeps all files present).

Inside the writer critical section, `_publish`'s parent check calls
`list_revisions(...)` and compares `current.revision_id` to the caller's
`parent_revision` (`private_records.py:238-243`). If the true current
revision's working-tree file was removed (C1-F1), `list_revisions` reports the
*previous* revision as current, the stale `parent_revision` matches, and
`writer.record(...)` commits a **second child of an already-parented
revision**. The journal now contains a branch. The very next `list_revisions`
raises `"record revision chain branches"` (`:462-463`) — the record is
**permanently unreadable** (DoS), and for the interval between the two commits
a duplicate authoritative revision exists. This directly violates correction
plan C1 item 3 ("Stale concurrent updates yield one winner and one typed
conflict with no duplicate/overwritten authoritative artifact").

**Bounded correction:** the CAS inside the writer section must resolve the
current revision from committed journal history at the locked HEAD, not from
`glob`. `read_committed_artifact` already proves single-publisher immutability
for a known path; the missing piece is a committed *listing* of the revision
directory.

---

### C1-F4 — `read_record(content=True)` returns unauthenticated working-tree bytes

**Severity:** High. **Confirmed in source.** Matches acceptance case
`test_content_read_rejects_modified_snapshot`.

`read_record` calls `_content_for_reference(...)` which *does* validate the
committed snapshot digest (`private_records.py:397-402`, reading git objects via
`read_committed_artifact`). It then **discards that validated data** and returns
`(resolved.path / snapshot["path"]).read_bytes()` from the working tree
(`:495`). A modified working-tree `source.txt` passes the
`_content_for_reference` gate untouched (that gate never inspects the working
tree) and its tampered bytes are returned to the caller as record content.

**Bounded correction:** return the bytes `read_committed_artifact` already
fetched and digest-checked in `_content_for_reference` (have it return the
data), or re-`read_committed_artifact` the snapshot path here and compare
against `snapshot_ref.content_sha256` / `size_bytes` before returning.

---

### C1-F5 — `create_record` replay poisons its own idempotency key

**Severity:** High. **Confirmed in source.** Matches acceptance case
`test_generated_record_identity_replays_original_receipt`.

`create_record` sets `stable_id = record_id or _id(EntityPrefix.RECORD, ...)`
(`private_records.py:413`) and passes `payload={"record_id": stable_id, …}` to
`_publish` (`:425`). The operation key (`operation_key`, e.g. `"record-1"`) is
stable, so the receipt **path** is stable, but on a genuine retry *without* an
explicit `record_id` a fresh UUID is generated each call, so
`digest_imported_bytes(canonical_json_bytes(payload))` differs. `_existing_receipt`
finds the prior receipt at the same path and raises
`private_operation_conflict` — `"operation key was already used with different
payload"` (`:207-208`) — instead of returning the original committed IDs and
receipt with `created=False`. `revision_id` (`:418`) has the same effect.

Existing regressions miss this because
`test_committed_bytes_parent_chain_and_projection_recovery` and
`test_subprocess_same_parent_has_one_winner` always pass an explicit
`record_id`. `import_reference` / `import_run_input` are unaffected — their
default keys and payloads are content-derived with no generated IDs.

**Bounded correction:** exclude generated identifiers from the conflict-identity
payload (hash only the semantic request: `kind`, `origin`, `actor`,
`parent_revision`, `content`, and — for C2 — `relationships`); on an identical
retry return the committed receipt's `record_id` / `revision_id`. Correction
plan C1 item 2 already states this ("Normalize without generated
revision/record IDs defeating same-request replay. Return the ORIGINAL
committed IDs/receipt on an identical retry").

---

### C1-F6 — Disposable-context staging uses a fixed, symlink-following temp path

**Severity:** Medium–High. **Confirmed in source.** Matches acceptance case
`test_context_staging_never_follows_preexisting_symlink`.

`rebuild_scout_projection` finishes with (`private_records.py:528-532`):

```python
index_path = resolved.path / "indexes"
index_path.mkdir(mode=0o700, exist_ok=True)
temporary = index_path / ".context.tmp"
temporary.write_bytes(canonical_json_bytes(context))
os.replace(temporary, index_path / "context.json")
```

Three problems, each contrary to correction plan C1 item 6 ("Atomically publish
scoped context … using bounded unique staging paths"):

1. **Fixed name `.context.tmp`** — not bounded-unique; two concurrent rebuilds
   race on the same staging file and on `context.json`, and one `os.replace`
   can raise `FileNotFoundError`.
2. **`Path.write_bytes` follows a pre-existing symlink** (`O_WRONLY|O_CREAT|O_TRUNC`,
   no `O_EXCL`, no `O_NOFOLLOW`). A pre-seeded `indexes/.context.tmp` symlink
   pointing elsewhere is written through, then unlinked by `os.replace`. The
   `indexes/` tree is `.gitignore`d, so this leaves no journal trace.
3. **`index_path.mkdir(exist_ok=True)`** silently succeeds when `indexes` is a
   symlink to a directory (no `is_symlink()` guard), unlike
   `journal._replace_one`.
4. The `context.json` publish is **outside `database_lock`** (released at
   `:527`).

**Bounded correction:** stage with `tempfile.mkstemp(dir=index_path,
prefix=".context-")` (as `journal._write_temporary` does), guard
`index_path.is_symlink()` before `mkdir`, walk the `indexes` parent chain for
symlinks, and perform the publish inside the same `database_lock` region.

---

## Lower-severity / hypothetical risks (not confirmed C1 blockers)

### C1-R1 — Rebuild HEAD is read but not pinned (TOCTOU)

**Severity:** Medium. **Confirmed drift window; consequence bounded.**
`rebuild_scout_projection` reads `journal_head` (`private_records.py:501`) then
builds `records` / `context` from the working tree (`:504-512`). A concurrent
journal commit between these steps yields a projection whose
`scout_meta.cursor.journal_head` is the old HEAD while `scout_records` reflects
newer state. `read_index`'s self-heal only compares the `projection` table, not
`scout_meta`, so the drift is not auto-repaired. Correction plan C1 item 4 asks
for a rebuild "at a pinned journal HEAD". Fix: enumerate and read record bytes
from `git show <head>:<path>` at the HEAD captured on line 501 (this also
subsumes C1-F1 for the projection path).

### C1-R2 — `persist_trace` silently skips the lock for non-workpad connections

**Severity:** Low (latent). `persist_trace` infers `workpad` from
`PRAGMA database_list` and only locks when the connection is file-backed inside
a `.git` directory (`proposal_interview.py:566-575`). A caller passing a
`:memory:` or out-of-tree connection gets **no** `database_lock`. No current
caller in `src/` or `tests/` passes `connection` to `persist_trace` /
`InterviewHTTPServer` (the in-handler call at `proposal_interview.py:774-775`
is unreachable today), so this is a latent gap versus correction plan item 4
("coordinate ALL trace paths including direct `persist_trace`"), not a live
bug. Fix: require an explicit `workpad` (or an `already_locked` assertion) and
fail closed when neither is available.

### C1-R3 — `migrate_workpad_layout` collision check covers ~6 of ~30 v2 roots

**Severity:** Medium. **Hypothetical (needs pre-migration tree write access).**
`migrate_workpad_layout` refuses migration only if
`records | references | run-inputs | docs | indexes | ui` already exist
(`private_records.py:128`). After migration, `_validate_workpad_repository`
does `allowed.update(_V2_ROOTS)` (`workpad.py:495-496`), so a pre-existing
tracked `tools/`, `goalgraphs/`, `README.md`, `CHANGELOG.md`, `gig.py`, or
`reports/` — all net-new v2 roots that are *not* in the collision list —
survives migration and is then admitted as a valid v2 root. (`gig.md`,
`goals/`, `manifests/`, `runs/`, `decisions/` legitimately pre-exist in v1
semantic state and are not the concern.) Correction plan C1 item 7 asks to
"Validate new roots". Fix: check the full set of *new-in-v2* roots for a
pre-existing tracked entry, or validate each admitted v2 root's provenance.

### C1-R4 — `workpad_layout_version` round-trips git output through text mode

**Severity:** Low. `workpad_layout_version` uses text-mode `_git(...).stdout`
then `.encode("utf-8")` for handoff / marker / `.gitignore` bytes
(`workpad.py:564-568`), where `journal.read_committed_artifact` deliberately
uses `_git_bytes`. Universal-newline translation could in principle corrupt a
digest comparison for content containing `\r`; the current v2 gitignore and
canonical-JSON marker contain none, so this is a latent fragility rather than a
defect. Fix: use a bytes git helper for the three object reads.

### C1-R5 — Hot-path cost of per-transition `workpad_layout_version`

**Severity:** Low (non-correctness). `journal._validate_workpad` now calls
`workpad_layout_version` on **every** journal transition
(`journal.py:538-541`), which shells out to `git log` + `git show` x3 whenever a
layout marker is present. This adds ~4 git subprocesses per journal write. Not a
C1 blocker; note for later caching (e.g. memoize per `(root, HEAD)`).

### C1-R6 — `validate_state_database` runs before `database_lock` in the rebuild

**Severity:** Low. `rebuild_scout_projection` calls `validate_state_database`
(`private_records.py:500`) before acquiring `database_lock` (`:513`). A
concurrent writer could alter the database between the check and the lock. The
window is small and the lock still serializes the mutation; re-validating the
scout-table schema inside the lock would close it.

### C1-R7 — `scout_operations` table created but never populated

**Severity:** Info. `rebuild_scout_projection` creates `scout_operations`
(`private_records.py:516-517`) but never inserts; `index._read_scout_tables`
preserves it if present. Receipts are not projected in C1. Likely a deliberate
C2 seam — flagged only so it is not mistaken for a dropped write.

---

## What C1 got right (verified, no action needed)

- `read_committed_artifact` (`journal.py:941-984`): single-publisher check via
  `git log -- <path>`, exactly-one-handoff check, and `artifact_refs`
  digest + size cross-check against committed handoff metadata. A shaped,
  uncommitted, multiply-published, or digest-mismatched working-tree file is
  refused. Reads from git objects, so working-tree parent symlinks do not
  affect the authenticated read.
- `run_with_journal_writer` / `JournalWriter.record` (`journal.py:169-218`):
  lookup, CAS, and publish run inside one `_writer_lock`; `JournalWriter.record`
  defaults `allow_artifact_replacement=False`, so immutable record / source /
  receipt paths are no-clobber while `.gitignore` replacement stays allowed via
  `record_transition`.
- `_preflight_artifact_destinations` (`journal.py:847-865`): refuses immutable
  collisions and redirected parents *before* writing recoverable transaction
  state.
- `_restore_transaction` `workpad_layout_migrated` branch
  (`journal.py:801-813`): replays the migration with `.gitignore` replaceable
  and the layout marker no-clobber; v1 history is untouched.
- `_write_projection` (`index.py:167-198`): `BEGIN IMMEDIATE` +
  `DROP/CREATE/INSERT projection` on the **existing inode**, with a prior
  `_read_interview_events` / `_read_scout_tables` schema gate; long-lived G22
  HTTP connections keep referring to the same inode. `read_index` /
  `rebuild_index` / `_persist_interview_trace` / `persist_trace` all take
  `database_lock` in the documented `journal -> database` order.
- `_read_interview_events` / `_read_scout_tables` (`index.py:208-281`):
  distinguish absent (`return None`/`[]`) from malformed / unknown-table
  (`JournalIndexError`); a recognized `interview_events` trace or a
  wrong-schema scout table is refused rather than silently replaced.
- `workpad_layout_version` (`workpad.py:538-604`): admits v2 only from a
  single committed publisher whose handoff `artifact_refs` digests match the
  committed marker and `.gitignore`, and whose marker identity
  (`schema_version` / `layout_version` / `project_id` / `gig_id` /
  `ignore_sha256`) is exact; an unknown marker is a conflict, never treated as
  v1.

---

## Suggested C1 correction ordering

1. **C1-F1 / C1-F2 / C1-F3** — one change: committed-history enumeration for
   the revision directory (and receipts), used by `list_revisions`, the
   `_publish` CAS, `read_record`, and `rebuild_scout_projection`. Fail closed on
   committed-but-absent files.
2. **C1-F4** — return committed snapshot bytes from `read_record(content=True)`.
3. **C1-F5** — drop generated IDs from conflict identity; replay returns
   committed IDs/receipt.
4. **C1-F6 / C1-R1 / C1-R6** — symlink-safe bounded-unique context staging
   inside `database_lock`, and pin the rebuild to the HEAD read on entry.
5. **C1-R3** — widen the migration new-root collision check.
6. **C1-R2 / C1-R4 / C1-R5 / C1-R7** — hardening / cost; can trail into a later
   pass if time-boxed.

No OS-sandbox, DLP, or arbitrary-external-copy detection claims are made or
relied on above; every finding is a concrete code path in the settled C1
source or diffs.
