# SCOUT-05 init review — independent read-only

**Reviewer:** dispatched worker (independent, read-only).
**Date:** 2026-09-09.
**Scope:** delivered username / default-initialization slice only —
`src/gigai/default_init.py`, and the SCOUT-05 deltas in `registry.py`,
`target_binding.py`, `workpad.py`, `journal.py`, `cli.py`, plus
`tests/test_scout05_init.py`. Reviewed against the accepted contract
(`SCOUT-00-user-owned-gig-amendment.md` §2–§3 and §7, `SCOUT-00-contract-amendments.md`
B3, roadmap SCOUT-05) and delivered behavior, not just the worker tests.
**Out of scope (moving files, not reviewed):** `scout_template.py`,
`src/gigai/data/scout/`, `scout_tools.py`, `scout_checks.py`, `native_records*`,
`external_cli.py`, `private_records.py`, `graph-set`/`provider-review` CLI, `gig.py`.
**No source or tests were edited. No suites were re-run. No providers called.
No real user state initialized. Private `.gigai` data was not inspected.**

---

## Verdict

The slice delivers a real, mostly-careful recoverable owner-binding primitive:
additive v3 registry migration with backup, a pinned private batch intent,
per-template reserved Gig IDs, journal-authoritative `template_instance_bound`
bindings with a registry cache row rebuilt from the committed artifact on
restart, username gating before any package write, and owner-conflict refusal.
`tests/test_scout05_init.py` (8 cases) exercises the happy path, repeat/extended
inventory, post-journal interruption recovery, CLI gating, additive v2→v3
migration, and the four preflight refusals.

However, several accepted-contract obligations are **not met by the delivered
behavior**, and the "prepared default instance" the result advertises is a
**binding record only** — there is no copied inventoried source, no per-Gig
scaffold, no v2 layout, and no actual prepared proposal behind the
`next_action` string. Two items are blockers for calling the delivered slice
contract-conformant; the rest are correctness / robustness / typing gaps.
The largest missing pieces (source inventory copy, scaffold, proposal
creation, shipping wrapper) are acknowledged in `SCOUT-05-init-implementation.md`
as later integration and are listed here as **deferred**, not blockers, but the
gap between "prepared" as delivered and "prepared proposal" as accepted must be
recorded.

---

## Blockers (delivered behavior contradicts accepted contract)

### B1. New default instances are workpad layout **v1**, not v2

- **Contract:** `SCOUT-00-user-owned-gig-amendment.md` §2: "New instances use
  v2." §2 table + "Introduce an explicit, strict `workpad-layout:2` manifest
  under `manifests/` ... A journaled layout transition binds that marker".
- **Delivered:** `initialize_defaults` (`default_init.py:273`) calls
  `provision_workpad(..., reconcile_existing_journal=True)`. For a *fresh*
  instance `provision_workpad` (`workpad.py:155-166`) stages an empty repo via
  `_initialize_workpad_repository` + `_publish_staged` with only
  `.git` / `.gitignore`, and the ignore file written is `WORKPAD_GITIGNORE`
  (v1, `workpad.py:456`). No `manifests/workpad-layout.json` marker is created,
  no `workpad_layout_migrated` transition is journaled. `workpad_layout_version`
  (`workpad.py:105`) therefore returns `1` for every instance this slice
  provisions.
- **Consequence:** every default instance is created on the v1 allowlist/ignore
  contract. The v2 ignore additions (`state.sqlite-wal/-shm/-journal`,
  `/indexes/`, `/reports/scout/`, working-copy paths) are absent, so if a later
  lane drops `README.md` / `gig.py` / `tools/` / `ui/` into these instances they
  are **not ignored** and `_validate_workpad_repository` will reject the tree
  (fresh provision only admits `_V2_ROOTS` when `layout_version == 2`).
- **Smallest fix:** after `provision_workpad`, in the same batch, journal the
  `workpad_layout_migrated` transition (marker + v2 `.gitignore`, matching
  `workpad_layout_version`'s exact admission checks) for each reserved instance
  before recording the cache row; or teach `provision_workpad` to stage v2
  directly for this caller. Either way the marker must be committed, not just
  written to the working tree.

### B2. `gigai init` failures are untyped even with `--json`

- **Contract:** `SCOUT-00-user-owned-gig-amendment.md` §3: "non-interactive
  init without a saved/provided username returns a typed `username_required`
  result"; "A different provided username ... returns
  `workspace_owner_conflict`". B3: "Provide typed
  `template_reconciliation_required` when recovery cannot prove ownership";
  "`template_instance_conflict`".
- **Delivered:** `init_command` (`cli.py:1839-1840`) catches
  `DefaultInitError` and re-raises `click.ClickException(str(exc))` — no code,
  no JSON error envelope, even when `--json` was passed. Every other new CLI
  surface in this diff routes through `_raise_cli_error(str(exc),
  as_json=as_json, code=exc.code)`. So `username_required`,
  `workspace_owner_conflict`, `username_invalid`, `template_instance_conflict`,
  `template_reconciliation_required`, `default_inventory_invalid`,
  `owner_id_invalid`, `workspace_owner_invalid` are all delivered to a
  `--json` agent caller as a bare stderr string with exit code 1.
- **Note:** the library layer (`initialize_defaults`) *does* raise typed
  `DefaultInitError(code, message)` correctly; only the CLI adapter drops it.
- **Smallest fix:** in `init_command`, replace the bare `raise
  click.ClickException` for `DefaultInitError` with
  `_raise_cli_error(str(exc), as_json=as_json, code=exc.code)`, and emit a
  JSON error object when `as_json`.

---

## Correctness / robustness findings

### C1. `template_instance_bound` can be journaled twice under concurrent `manifests/` writes — MEDIUM

- **Where:** `default_init.py:290-325` (`_binding_from_snapshot` →
  fall-through to `record_transition`).
- **Mechanism:** on batch resume (interrupted after the journal write but
  before the registry cache insert), the code calls `_binding_from_snapshot`,
  which runs `writer.snapshot(("manifests/",))` →
  `_capture_committed_snapshot` (`journal.py:1079`). That helper requires the
  **entire `manifests/` working tree** to equal exactly the committed `ls-tree`
  set, byte-for-byte, with **no extra files** (`journal.py:1118-1125`). Any
  additional file under `manifests/` — e.g. from the coordinator's concurrent
  `scout_template` source-inventory integration, or any future manifest writer
  — makes `_capture_committed_snapshot` raise `JournalConflictError`.
  `_binding_from_snapshot` catches `JournalConflictError` and returns `None`
  (`default_init.py:125-126`), which is indistinguishable from "no binding yet".
- **Consequence:** the resume path then re-enters the fresh-binding branch and
  calls `record_transition(transition="template_instance_bound", ...)` a
  **second time** for an already-bound instance. `record_transition` defaults
  `allow_artifact_replacement=True` (`journal.py:257`, `journal.py:582`), so the
  duplicate binding artifact write is *not* refused; a second
  `template_instance_bound` handoff is committed. Contract §3: interrupted init
  "must not duplicate already bound instances". The registry
  `insert_template_instance` unique-`gig_id` guard only catches this if the
  cache row already exists — which is exactly the state that does *not* hold on
  this interruption window.
- **Smallest fix:** scope the resume lookup to the single binding path (read
  `manifests/template-instance-binding.json` from committed HEAD directly, e.g.
  via `read_committed_artifact`) instead of a whole-`manifests/`-tree snapshot;
  and/or distinguish "snapshot integrity failed" from "artifact absent" so the
  former raises `template_reconciliation_required` rather than silently
  re-journaling.

### C2. Owner row payload/digest is not pinned or verified on retry — MEDIUM

- **Contract:** §3: "The init intent pins the owner row payload and its digest
  before the first instance is published; retries verify it, and conflicting
  rows/snapshots refuse." "Each journaled template-instance binding includes an
  exact typed snapshot of owner ID/project ID/username."
- **Delivered:** the batch intent (`default_init.py:262`) stores `owner_id`,
  `project_id`, `username`, `inventory`, `inventory_digest`,
  `reserved_gig_ids` — but **no digest over the owner row**, and the
  pending-intent retry check (`default_init.py:240`) compares only
  `inventory_digest` and `owner_id`, not `username`. On resume with
  `username=None`, `supplied_username` is taken from whatever
  `workspace_owners.username` currently holds (`_saved_username`,
  `default_init.py:199`); a mutated username in that row is then embedded into
  every subsequent `template_instance_bound` binding without the "conflicting
  rows/snapshots refuse" behavior the contract calls for. The binding artifact
  *does* embed `owner_id` + `project_id` + `username` (partial satisfaction of
  the "typed snapshot" clause), but there is no cross-check that the retry's
  owner row still matches the value pinned before the first publish.
- **Smallest fix:** add `owner_row_sha256` (canonical digest of
  `{project_id, owner_id, username}`) to the intent, computed before the first
  binding; on every resume, recompute from the current registry row and raise
  `workspace_owner_conflict` / `template_instance_conflict` on mismatch. Also
  compare `username` (not only `owner_id`) in the pending-intent guard.

### C3. Unicode control filtering misses the C1 range (and other separators) — LOW/MEDIUM

- **Contract:** §3: "1–64 Unicode code points after trimming, no control
  characters."
- **Delivered:** `normalize_username` (`default_init.py:57`) and the registry
  mirror `_validate_workspace_owner_record` (`registry.py:316-319`) both reject
  only `ord(c) < 32 or ord(c) == 127` — C0 + DEL. Unicode category `Cc` also
  includes U+0080–U+009F (C1), which pass unchecked. Other control-like code
  points that "no control characters" is usually read to exclude also pass:
  bidi overrides U+202A–U+202E / U+2066–U+2069, zero-width U+200B/U+200C/U+200D,
  line/paragraph separators U+2028/U+2029, BOM U+FEFF. `str.strip()` removes
  Unicode whitespace (incl. NBSP) but not any of these.
- **Consequence:** a username containing C1 or bidi/zero-width code points is
  accepted, stored in `workspace_owners`, embedded in every binding artifact,
  written into the private intent JSON, and echoed by the CLI. It is display
  metadata (not a path or auth token), so impact is bounded, but it is a
  contract-explicit rule and both the write path and the stored-row validator
  are equally permissive (so a bad row also round-trips reads).
- **Smallest fix:** reject any code point whose `unicodedata.category(c)`
  starts with `C` (Cc/Cf/Co/Cs), plus the explicit separator set above, in
  `normalize_username`; apply the identical predicate in
  `_validate_workspace_owner_record`.

### C4. v2→v3 (and v1→v2) migration runs from read-only commands — MEDIUM (pre-existing pattern, re-committed here)

- **Contract:** B3: "Read-only commands do not migrate; named init
  exposes/executes the local migration as an explicit setup step in its
  result."
- **Delivered:** `open_project_registry` (`registry.py:439-451`) runs
  `_migrate_registry_v1_to_v2` and now `_migrate_registry_v2_to_v3`
  unconditionally inside `_migration_lock` on **every** open, `create` flag
  notwithstanding. Read-only commands open the registry the same way —
  e.g. `list_gigs` (`listing.py:113`, `create=False`),
  and 19 call sites total. Running `gigai gigs` / `gigai status` against a v2
  registry silently performs the v3 schema change and writes
  `registry.sqlite.v2.bak`.
- **Additionally:** even for `gigai init`, B3 wants the migration surfaced "as
  an explicit setup step in its result". The `init` payload (`cli.py:1846-1862`)
  has no field indicating a v2→v3 (or v1→v2) migration occurred;
  `registry_changed` comes from `package` and refers to project registration,
  not schema version.
- **Note:** this is largely inherited behavior from the v1→v2 design, but
  SCOUT-05 added v3 to the same unconditional path and the accepted amendment
  restates the "read-only commands do not migrate" rule, so it is in scope.
- **Smallest fix:** gate the migration calls on an explicit
  `allow_migration: bool` argument to `open_project_registry`, passed `True`
  only by `init` (and dedicated `migrate` commands); read-only openers get a
  typed "registry migration required; run `gigai init`" error. Add a
  `registry_migrated` / `setup_steps` field to the `init` result.

### C5. Resume after an interrupted *inventory upgrade* hard-conflicts the pre-upgrade inventory — LOW

- **Where:** `default_init.py:240`.
- **Mechanism:** if a post-upgrade `gigai init` (larger inventory) is
  interrupted while its new `pending` intent is on disk, a subsequent `init`
  run with the *pre-upgrade* inventory (a strict subset) hits
  `intent.get("inventory_digest") != inventory_digest` and raises
  `template_instance_conflict` with no resume path. Contract §3 wants
  "Resume/reconcile an older incomplete batch before starting a new one" and
  "must not ... silently switch inventory on retry". Refusing avoids the silent
  switch, but there is no offered way to complete the pinned (newer) batch from
  the older binary/inventory, and the error text does not explain the required
  action.
- **Smallest fix:** when the on-disk pending intent is a **superset** of the
  requested inventory and the owner matches, resume the pinned intent
  (its own `inventory` / `reserved_gig_ids`) rather than the caller's
  narrower list; otherwise keep refusing but say "a newer inventory batch is
  in progress; re-run with the upgraded package".

### C6. `bound`-intent reset discards `reserved_gig_ids` before re-reservation — LOW

- **Where:** `default_init.py:242-262`.
- **Mechanism:** on a re-init after a completed batch, `status == "bound"` and
  matching `owner_id` cause `intent = {}`, then the `if not intent:` block
  re-reserves. Existing templates recover their `gig_id` via
  `find_template_instance`, so identity is preserved for them. But the reset
  happens *before* the new intent is written, so a crash between
  `_atomic(intent_path, ...)` of the fresh `pending` intent and loop completion
  leaves a `pending` intent whose `reserved_gig_ids` for *new* defaults were
  freshly minted — correct, but only because `generate_entity_id` re-checks
  `find_workpad`. There is a narrow window where the old `bound` intent is gone
  and the new one is not yet durable; a crash there yields a first-run-like
  state (acceptable, but undocumented). No data loss observed.
- **Smallest fix:** write the new `pending` intent atomically *before*
  clearing the `bound` marker (single `_atomic` replacing the file with the
  new pending content), so there is never a window with no intent for an
  in-progress batch.

### C7. Fresh-init relies on string-matching an exception message — LOW (quality)

- **Where:** `default_init.py:127-130`.
- **Mechanism:** `_binding_from_snapshot` distinguishes "unborn journal, so no
  binding yet, proceed" from "reconciliation required" by
  `if str(exc) == "journal head is unexpectedly unborn"`. Any reword of that
  literal in `journal.py:634` turns **every fresh instance provision** into a
  `template_reconciliation_required` hard failure.
- **Smallest fix:** have `_head_commit` / `snapshot` raise a distinct typed
  subclass (or accept `required=False`) for the unborn case, and branch on the
  type.

### C8. Inconsistent `home_root` resolution — LOW

- **Where:** `_saved_username` uses
  `home_root.expanduser().resolve()` (`default_init.py:102`); the main body
  opens `open_project_registry(home_root, create=False)` unresolved
  (`default_init.py:222`); `initialize_project_package` /
  `provision_workpad` each resolve independently. If `home_root` is relative or
  `~`-prefixed, the pre-lock read and the in-lock writes could target
  different registry paths.
- **Smallest fix:** resolve `home_root` once at the top of
  `initialize_defaults` and pass the resolved path everywhere.

### C9. Two different target-init lock names; package init runs outside the batch lock — LOW

- **Where:** `initialize_project_package` acquires
  `TargetInitLock(git_path(root, "gigai-package.lock"))` and returns;
  `initialize_defaults` then acquires
  `TargetInitLock(.gigai/locks/default-init.lock)` (`default_init.py:220-221`).
  B3 specifies "one documented lock order: target-init -> registry transaction
  -> workpad journal". The batch is serialized by `default-init.lock`, and the
  fresh-workspace username race resolves correctly via the
  `workspace_owners` unique/PK constraint (verified by reading the code path),
  but package initialization is not under the same lock as the batch, leaving a
  window between the two locks.
- **Smallest fix:** acquire `default-init.lock` first, then call
  `initialize_project_package` inside it (it is re-entrant-safe on its own
  lock name), so the whole init is one critical section in the documented
  order.

---

## Deferred — later Scout release integration (not blockers against this slice)

These are called out because the delivered result advertises "prepared default
instance(s)" and `next_action: "review the prepared proposal"`, and a reader of
§3 / roadmap SCOUT-05 would expect more than a binding row. `SCOUT-05-init-implementation.md`
explicitly scopes them out.

1. **No inventoried source or data schemas are copied into the instance.**
   §3: "Instantiation copies safe inventoried source and data schemas".
   `initialize_project_package` writes an **empty** package manifest
   (`"files": []`, `package.py:392-397`); `provision_workpad` publishes an
   empty repo. The three built-in catalog entries' `files/` are never
   materialized into the instance. (Coordinator's `scout_template` /
   `src/gigai/data/scout/` lane is the intended source of this; out of scope
   here.)
2. **No per-Gig scaffold.** Roadmap SCOUT-05: "Scaffold each Gig's own tools,
   UI source, docs/reference navigation and SQLite projections without running
   copied code." None of `README.md`, `CHANGELOG.md`, `gig.py`, `tools/`,
   `goalgraphs/`, `ui/template.html`, `ui/style.css`, `docs/`, `references/` is
   created. See B1 — the fresh-provision path also has no mechanism to *admit*
   these roots (only `reconcile_existing_journal` adds `handoffs`/`scratch`/
   `manifests`, and only v2 admits `_V2_ROOTS`).
3. **No actual prepared proposal.** §3: "Return each prepared proposal's
   approval state and exact next action." Roadmap: "An agent can open an
   instance without authoring a proposal from scratch." The slice creates a
   `template_instance_bound` binding with `approval: null` and returns
   `next_action="review the prepared proposal"` — but no proposal /
   `proposal_id` is created anywhere in `default_init.py` (no `lifecycle`
   import). The string points at nothing an agent can `gigai approve`.
4. **`scout_status` is a hardcoded literal.** `default_init.py:344` always
   returns `"prepared_unready"`; there is no Scout entry in `instances` and no
   check of Scout graph completeness. Correct for "Scout remains authoring-only"
   today, but it is not derived from any inventory/eligibility state, so it
   will not change when Scout *does* become eligible without a code edit.
5. **`default_inventory()` == `catalog_entries()`.** §3: "the explicit
   versioned default-template inventory shipped in the installed GigAI package
   ... Only complete release-eligible templates". Today that is implicitly "all
   three built-ins"; there is no explicit `release_eligible` flag on
   `CatalogEntry` and no version pin on the inventory itself beyond
   `CATALOG_REVISION`. Acceptable as a placeholder; must become an explicit
   allowlist before Scout or any partial template is added to `CATALOG`.
6. **No central schema resource for the binding.** The
   `template-instance-binding` artifact is validated by an inline dict-equality
   check (`_validate_existing_binding`, `default_init.py:143-164`), not a
   registered JSON Schema; `SHA256SUMS` unchanged. `SCOUT-05-init-implementation.md`
   acknowledges central registration would be needed before replacing the
   inline validator. Strict-typing obligation from §1/§7 is deferred, not met.
7. **SCOUT-05 root wrapper / init shipping surface** is not present
   (acknowledged in the impl doc).

---

## Things checked that are OK

- **Additive v3 migration:** `_migrate_registry_v2_to_v3` (`registry.py:803`)
  adds only the two new tables, preserves `user_version` guardrails, validates
  an existing `registry.sqlite.v2.bak` and refuses to overwrite it with
  different bytes (hard-links a fresh backup, retries on `FileExistsError`),
  runs under `BEGIN EXCLUSIVE` with rollback. `_validate_schema` gains
  version-scoped table/column/uniqueness checks for `workspace_owners` /
  `template_instances` and a `PRAGMA foreign_key_check`. v1 and v2 remain
  accepted versions; old-binary write compatibility is explicitly not claimed.
  Matches B3's "additive", "backup-before-write", "unknown versions refuse".
- **Username gating precedes package/instance writes:** `normalize_username` /
  `_saved_username` / conflict checks all run before
  `initialize_project_package` (`default_init.py:197-214`). Test
  `test_missing_or_conflicting_username_refuses_without_initial_package_write`
  confirms `.gigai` is absent on `username_required`. CLI prompts for username
  only on a tty, before the call.
- **Registry row is a cache, journal is authority:** the cache row is inserted
  *after* the journal commit, with `journal_commit` + `binding_sha256`
  recorded; restart after the journal write reconstructs only the missing row
  from the committed artifact (`_binding_from_snapshot` →
  `_validate_existing_binding` → `insert_template_instance`) and does not mint a
  new Gig ID or add history (test
  `test_interrupted_binding_resumes_the_reserved_instance_and_rebuilds_only_cache`).
- **Reserved Gig IDs are stable across repeat/extended init:**
  `_reserved_gig_id` + `find_template_instance` reuse; test
  `test_defaults_provision_two_instances_..._preserve_them` asserts identical
  `gig_id`s and `status` transitions `prepared` → `existing`, and that a third
  default is added without disturbing the first two.
- **No active-Gig pointer is manufactured:** no `select_active_workpad` call;
  `active_gig_id` stays `None` (asserted in tests). Matches §3 / B3.
- **Preflight path/symlink safety for private roots:** `_preflight_git_target`
  (`target_binding.py:495-534`) only admits `local` / `locks` after a valid
  project binding + matching registry record (`os.path.samefile` against
  `target_locator`) + saved `workspace_owners` row, and requires each present
  private root to be a non-symlink directory. Tests cover symlinked-private-root,
  unknown-sibling, tracked-private, and non-directory refusals. Intent file is
  under `.gigai/local/` which is in `PRIVATE_EXCLUDE_LINES`, so it is
  git-excluded; forced tracking is refused.
- **Intent write is atomic + 0600** (`_atomic`, `default_init.py:71-83`:
  `mkstemp` in target dir, `fsync`, `chmod 0600`, `os.replace`, temp cleanup).
- **`_owner_id` validates UUIDv4** shape and the `owner_` prefix; the registry
  mirror re-validates on read.
- **`digest_imported_bytes` is recomputed** for the cache-row `binding_sha256`
  from the canonical bytes actually journaled (not trusted from a caller).
- **Journal `TRANSITIONS`** now includes `template_instance_bound` and
  `workpad_layout_migrated`; `workpad_layout_version` resolves the marker from
  its single immutable publishing commit (rejecting a shaped working-tree
  marker with no/!=1 publisher, wrong transition, mismatched
  `artifact_refs` digests, or working-tree drift) — solid, though unused by
  this slice because no instance is v2 (see B1).

---

## Suggested disposition

| Item | Severity | Blocks slice acceptance? |
|---|---|---|
| B1 instances are v1 not v2 | Blocker | Yes |
| B2 untyped `init` CLI errors | Blocker | Yes |
| C1 double `template_instance_bound` under concurrent `manifests/` writes | Medium | Recommend fix before merge (concurrent scout_template lane makes it live) |
| C2 owner row digest not pinned/verified on retry | Medium | Recommend fix before merge |
| C3 C1-range / separator controls in username | Low–Med | Fix before merge (cheap, both sites) |
| C4 migration from read-only commands + no setup-step in result | Medium | Fix; partly pre-existing |
| C5 interrupted inventory-upgrade resume conflict | Low | Follow-up acceptable |
| C6 bound-intent reset ordering | Low | Follow-up acceptable |
| C7 exception-message string match | Low | Fix opportunistically |
| C8 home_root resolution | Low | Fix opportunistically |
| C9 lock name/order | Low | Fix opportunistically |
| Deferred 1–7 | n/a | No — later Scout integration, but record the "prepared" vs "prepared proposal" gap |
