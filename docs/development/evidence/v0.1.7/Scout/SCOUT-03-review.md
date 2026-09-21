# SCOUT-03 — Independent implementation review

**Date:** 2026-09-08
**Reviewer:** Claude (`task_efeb4b00f438`, dispatch `ctx_cd6503a3277b`)
**Status:** Independent source review of an incomplete implementation handoff.
This document records review findings only. It is **not** a release-acceptance
decision, and it does not run or own the SCOUT-03 test suite (independent
verification owns tests).

## Scope and method

- Read the accepted contracts: [SCOUT-00 lifecycle amendments](SCOUT-00-contract-amendments.md)
  (sections 1, 3, 4, 9/B1–B4, M1), the [user-owned Gig workspace amendment](SCOUT-00-user-owned-gig-amendment.md)
  (sections 2, 4, 7 and the accepted scenarios), and
  [G45](../../../v0.1.7/goals/G45-private-references-and-pasted-run-inputs.md)
  by reference through B2.
- Read [SCOUT-03-implementation-plan.md](SCOUT-03-implementation-plan.md)
  including its caller-audit disposition, [SCOUT-03-caller-audit.md](SCOUT-03-caller-audit.md),
  and [SCOUT-03-implementation.md](SCOUT-03-implementation.md).
- Inspected the frozen worktree source at `fda4857` + dirty tree. SCOUT-03
  additions were isolated from the co-resident dirty SCOUT-02 / G43 work by
  file and by `git diff HEAD`.
- Terra task `task_ec34c05cade5` settled **failed** (full suite unavailable);
  no source writer is active. The original queued review `task_27278b1bcc11`
  is superseded because its dependency settled failed.

### Files that carry SCOUT-03 changes

| File | Nature of change |
|---|---|
| `src/gigai/private_records.py` (new, 419 lines) | Whole SCOUT-03 runtime: imports, revisions, receipts, projection, migration, selection |
| `src/gigai/schemas/private-record-revision.schema.json` (new) | `private-record-revision:1` |
| `src/gigai/schemas/reference-record.schema.json` (new) | `private-reference:1` |
| `src/gigai/schemas/run-input-record.schema.json` (new) | `run-input-record:1` |
| `src/gigai/schemas/scout-operation-receipt.schema.json` (new) | `scout-operation-receipt:1` |
| `src/gigai/schemas/workpad-layout.schema.json` (new) | `workpad-layout:2` |
| `src/gigai/index.py` | `database_lock`, `_read_scout_tables`, projection-preserve wiring |
| `src/gigai/journal.py` | new transitions, `_preflight_artifact_destinations`, v2 `.gitignore` dispatch, `allow_artifact_replacement` plumbing |
| `src/gigai/workpad.py` | `WORKPAD_V2_GITIGNORE`, `WORKPAD_LAYOUT_PATH`, `_V2_ROOTS`, `workpad_layout_version` |
| `src/gigai/lifecycle.py` | `_persist_interview_trace` wrapped in `database_lock` |
| `src/gigai/run_plan.py` | private-root refusal in `create_run_plan` (also large SCOUT-02 graph-set work) |
| `src/gigai/canonical.py` | new `EntityPrefix` values and canonical-ID regex prefixes |
| `src/gigai/validators.py`, `SHA256SUMS`, `schemas/README.md`, `tools/verify_installed_schemas.py` | schema inventory (49 total; 6 are SCOUT-03's) |
| `src/gigai/cli.py` | `layout migrate`, `reference add/list/show`, `run-input add/show`, `record create/read` |
| `src/gigai/portability.py` | v2 active-version inspection-only refusal (SCOUT-02 boundary) |
| `tests/test_scout03_private_records.py` (new, 2 tests) | one substantive happy path + stale parent; one v1-refusal |

`src/gigai/run.py`, `src/gigai/proposal_interview.py`, `src/gigai/package.py`,
`src/gigai/registry.py`, `src/gigai/invocation.py` received **no SCOUT-03
changes**. Several of these were named as required seams by the plan and audit
(see findings).

## Verdict

**SCOUT-03 as delivered does not meet the accepted contract.** The
schema/layout/journal skeleton is real and mostly well-formed, but multiple
first-order acceptance requirements are unimplemented, not merely untested:
`saved_default` vs `run_override`, the approved-tool/effect binding, the
`projection_pending` recovery contract, native profile/experience records, and
the full set of G22-trace-writer coordination. Two of the audit's named
authority/data-loss blockers are only partially closed. The implementation
handoff's acceptance-mapping table overstates coverage on rows 2, 3, 5 and 7.

Findings below map to the plan's seven acceptance rows. Later-goal scope
(SCOUT-04 external Run executor, SCOUT-05 registry-v3 / general init,
SCOUT-11 private transfer) is correctly excluded and is not held against this
delivery.

---

## Findings

### F1 — `saved_default` vs `run_override` is entirely absent (Row 2; blocker)

**Contract:** SCOUT-00 §3: "Updates explicitly choose `saved_default` or
`run_override`. A Run resolves selected defaults to exact revisions ... and
retains that selection even if the user later edits the default." Plan stage 4
first bullet. Acceptance Row 2: "Saved preference revision versus task-only
override produces distinct intended behavior ... Archive retains old
selections." Amendment §7 scenario, A03 matrix
(`SCOUT-00-contract-amendments.md:307-317`).

**Observed:** No `saved_default` / `run_override` concept exists anywhere in
the runtime. `grep` across `src/gigai/private_records.py` and `src/gigai/cli.py`
returns nothing. `create_record` (`private_records.py:301-326`) has `kind`,
`origin`, `parent_revision`, `operation_key` but no override/default
discriminator; `scout-operation-receipt.schema.json` and
`private-record-revision.schema.json` have no such field; `select_exact_inputs`
(`private_records.py:400-416`) resolves caller-named `(record_id, revision_id)`
pairs with no notion of "the saved default for kind X" vs "this task only."

**Impact:** The central A03 behavior — a task-only override that does not
change the saved preference and does not affect another task's selection —
cannot be performed or demonstrated. The handoff's acceptance-mapping row 2
("`create_record` checks current parent and operation receipt identity; focused
fixture proves stale parent refusal") describes the CAS check and silently
substitutes it for this requirement.

---

### F2 — Approved Gig-owned tool / effect binding is unimplemented (Row 5; blocker)

**Contract:** Plan stage 3: "Supported Gig-owned tool requests bind the actual
approved Gig/version, entry and tool bytes, source inventory, operation/effects
and agent origin; recheck before publication. Reject changed/extra/foreign/
unapproved source and broadened effects. Do not accept a caller-supplied digest
as proof." Amendment §4 finding 3
(`SCOUT-00-user-owned-gig-amendment.md:254-286`). Acceptance Row 5:
"Approved-tool mutations prove inventory/effect/version checks." Amendment §7
scenario 11.

**Observed:** No implementation. `grep` for
`source_inventory|inventory_digest|entry_digest|tool_digest|effect.*bind|agent_origin`
in `private_records.py` / `cli.py` returns nothing. Every CLI command
hard-codes `actor={"kind":"operator","id":"local-user"}`
(`cli.py` `record_create_command`, `reference_add_command`, etc.); there is no
agent-origin path, no `gig.py` capability-executor integration, and
`scout-operation-receipt.schema.json` has no tool/version/inventory/effect
fields. `_publish` (`private_records.py:174-198`) writes a receipt with
`operation`, `operation_key`, `payload_sha256`, `artifact_refs` only.

**Impact:** Row 5's tool half is not deliverable. The handoff's
acceptance-mapping row 5 addresses only the layout half
("`migrate_workpad_layout` is explicit and journal-authenticates v2") and
omits the tool-mutation requirement. Note the amendment itself defers the
per-Gig *scaffold shipping* to SCOUT-05, but the *validated binding on a
supported mutation* is SCOUT-03 scope per plan stage 3 and the audit
disposition ("covered by stages 1 and 3 above").

---

### F3 — Projection failure after journal commit is an uncaught exception, not `committed` + `projection_pending` (Row 3; blocker)

**Contract:** Plan stage 3: "A committed operation whose projection fails
returns committed plus `projection_pending` and a rebuild action. Retry cannot
duplicate authority." Amendment §4 finding 1
(`SCOUT-00-user-owned-gig-amendment.md:224-229`). Acceptance Row 3: "Inject
failures around journal publication, projection commit and recovery; prove no
duplicate or invented success."

**Observed:** `private_records.py:197` — `rebuild_scout_projection(resolved=resolved)`
is called **unguarded**, immediately after `record_transition` returns, outside
any `try`. If the rebuild raises (`database_lock` timeout →
`JournalIndexError`, `sqlite3.Error`, an `OSError` on the `indexes/` mkdir or
`os.replace`), the exception propagates out of `import_reference` /
`import_run_input` / `create_record`. The journal commit and the operation
receipt are already durably written, so the caller sees a raw failure for an
operation that in fact committed. There is:
- no typed `projection_pending` result object,
- no rebuild next-action returned to the CLI,
- no `outcome:"projection_pending"` in `scout-operation-receipt.schema.json`
  (it is `{"const":"committed"}` only),
- no cursor/marker recording the pending state.

`reconcile_journal` (`journal.py:335-395`) is generic and will replay an
interrupted *journal* transaction, but nothing re-runs `rebuild_scout_projection`
after reconciliation, and the `private_records` read functions do not check a
projection cursor (see F4), so `indexes/context.json` and `scout_records`
silently remain stale until the next `_publish` or `rebuild_index`.

**Impact:** Row 3's "projection commit" failure branch is not implemented to
contract. Data is not lost (reads go to journal files), but the required
typed-result / rebuild-action / no-invented-success surface is absent, and
there is no test exercising it.

---

### F4 — No projection cursor; private-record readers have no staleness detection and no clean-authority guard (Rows 3, 4; major)

**Contract:** Amendment §4 finding 1
(`SCOUT-00-user-owned-gig-amendment.md:232-239`): "a projection cursor
containing journal HEAD and schema version ... Readers compare cursor to
authoritative HEAD and repair or report staleness; no silent use of outdated
application state for a new Plan." Audit `index.py:129-145` row and
`index.py:297-306` row ("Preserve this [clean authority] boundary for
authority reads").

**Observed:**
- `rebuild_scout_projection` (`private_records.py:370-397`) writes
  `scout_records` and `scout_meta` (key `context` only). It never writes a
  journal-HEAD / schema-version cursor. `scout_operations` is `CREATE TABLE IF
  NOT EXISTS`'d in three places but never populated — a dead table that
  `_read_scout_tables` nonetheless preserves.
- `list_imports`, `read_import`, `read_record`, `list_revisions`,
  `select_exact_inputs` all read straight from committed *and uncommitted*
  workpad files. `_resolved` (`private_records.py:138-141`) calls
  `resolve_workpad(..., allow_semantic_state=True)` then only `_require_v2`. It
  does **not** call `_validate_workpad_repository` or any
  `_require_clean_authority` equivalent, so a dirty workpad with an
  uncommitted edit to `references/ref_x/reference.json` is read and returned as
  metadata by `list_imports` (schema-valid but not journal-authenticated).
  `read_record --content` re-checks the snapshot digest so tampered *reference
  bytes* are caught (`private_records.py:296-297`), but the metadata/listing
  path is not guarded.

**Impact:** Row 4 ("Cursor drift/direct SQL tampering do not affect
authoritative selection") is satisfiable only because selection reads bypass
SQLite entirely — but that also means the contract's cursor mechanism does not
exist, and Row 3's "readers repair or report staleness" is not implemented.
Uncommitted-file reads on the metadata path weaken the journal-authority
guarantee the audit asked to preserve.

---

### F5 — Only one of three G22 trace-write paths was coordinated through `database_lock` (Rows 1, 4; major)

**Contract:** Plan stage 1: "Coordinate `index._write_projection`, `read_index`,
**all G22 trace writers** and Scout projection writes through a common database
lock." Audit blocker #2 (`SCOUT-03-caller-audit.md:36-39`) and the "G22 direct
writers" paragraph (`SCOUT-03-caller-audit.md:90-99`) name three seams:
`proposal_interview.persist_trace` itself, the HTTP handler at
`proposal_interview.py:754-758`, and `lifecycle._persist_interview_trace`.

**Observed:** `git diff HEAD -- src/gigai/proposal_interview.py` is empty —
the file is untouched.
- `lifecycle._persist_interview_trace` (`lifecycle.py:2345-2362`) — **wired**:
  now opens `with database_lock(workpad):` before `sqlite3.connect(... state.sqlite)`.
- `proposal_interview.persist_trace` (`proposal_interview.py:556-594`) — still
  `CREATE TABLE ... / INSERT / connection.commit()` on a caller-supplied
  connection with no lock. It remains a public exported API (`__all__`,
  `proposal_interview.py:1015`).
- HTTP handler `proposal_interview.py:757` — `persist_trace(owner.connection, owner.session)`
  on a long-lived connection, no `database_lock`.

Practical reachability of the HTTP path: the shipped CLI `improve` flow
(`cli.py:2275`) constructs `InterviewHTTPServer(...)` **without** `connection=`,
so `owner.connection is None` and the handler does not call `persist_trace`.
The uncoordinated path is therefore currently exercised only by tests
(`tests/test_g22_http_approval.py:47`, `tests/test_g26_review_actions.py:107`)
and any future caller that passes a connection.

**Impact:** The audit's stated race — `index` rebuild's `os.replace` of
`state.sqlite` interleaving with a G22 `INSERT` — is closed for the
lifecycle writer but not for `persist_trace` callers in general. Row 4's
"concurrent G22 trace writes preserve both data families" holds for the CLI
path but not for the full seam the plan required. The handoff's acceptance
row 4 ("`index.database_lock`, lifecycle trace lock, closed table
preservation") does not disclose the un-wired `persist_trace` path.

---

### F6 — Managed-Run private-input refusal is a path-prefix check with bypasses (Row 6; major)

**Contract:** Plan stage 4 last bullet: "Import/selection/content read is NOT
provider disclosure authority. Reject private selected inputs at unrestricted
provider ingress before any call; do not relabel them public to reuse document
review." Plan "Ownership and exclusions": "**Do not accept metadata/path-prefix
checks as substitutes for requirements.**" Acceptance Row 6: "No
unselected/private provider leakage."

**Observed:** The only refusal is in `run_plan.create_run_plan`
(`run_plan.py`, in the diff around the `inputs_raw` loop):

```python
for source in inputs_raw:
    try:
        relative = source.resolve(strict=True).relative_to(resolved.path.resolve())
    except (OSError, ValueError):
        continue
    if relative.parts and relative.parts[0] in {"references", "run-inputs", "records", "docs"}:
        raise RunPlanError("private_provider_disclosure_refused", ...)
```

Problems:
1. It is exactly the path-prefix substitute the plan forbids. There is no
   `privacy_class` / content-family inspection. A caller who copies a private
   reference to `/tmp/resume.txt`, or into any non-listed subtree
   (`scratch/`, `reports/`, project root), passes it as `--input` unrefused.
2. `source.resolve(strict=True)` + `.relative_to(...)` in a `try` whose
   `except` is `continue`: a path that does not exist yet, or a symlink inside
   `references/` whose target resolves **outside** the workpad, raises
   `ValueError` / `OSError` and is silently **allowed through**.
3. `run.py` received no SCOUT-03 changes. `_validate_plan_handoff`
   (`run.py:758-900`) and `_prepare_records` (`run.py:933-1117`) — the
   pre-provider-call validation the audit named — do **not** independently
   reject private artifact refs in a sealed Plan's `sealed_sources`. Any route
   that places a private ref into a Plan other than `create_run_plan`'s
   `input_paths` (typed inputs, a hand-authored Plan, a future
   graph-selected Plan) reaches the provider unchecked.

**Impact:** Row 6's "no private provider leakage" is only weakly enforced at
one call site and is bypassable. `read_record --content` correctly returns
bytes only to the invoking agent and list/metadata paths are redacted
(`private_records.py:266`, `355`, `read_record` `safe` dict) — that half of
Row 6 is met. The provider-ingress half is not met to contract.

---

### F7 — Immutable G45 imports and record revisions are journal-overwrite-eligible (Row 1; medium)

**Contract:** Plan stage 2: "B2's discriminated G45 reference/Run-input/
native-content forms must preserve ONE canonical content authority; do not
duplicate imported bytes." Audit `journal.py:604-627, 749-800` row: "enforce
canonical G45 paths, immutable snapshots, wrapper-to-source digest equality
... Keep CAS parent and operation-key checks inside the writer lock."
Acceptance Row 1: "changed content is a new immutable object."

**Observed:** `journal.py` now supports `allow_artifact_replacement=False`,
which gives idempotent-or-refuse semantics via
`_preflight_artifact_destinations` (`journal.py:781-800`) and `_replace_artifacts`
(`journal.py:765-779`). But `private_records._publish` (`private_records.py:190`)
and `migrate_workpad_layout` (`private_records.py:126`) call `record_transition`
**without** `allow_artifact_replacement=False`, so it defaults to `True`:
`records/<id>/revisions/<rev>.json`, `references/ref_<id>/source.txt`,
`run-inputs/input_<id>/source.txt` and the receipt are all silently
overwritable at the journal layer.

Mitigations that exist: `_existing_receipt` (`private_records.py:163-172`)
enforces operation-key idempotency (same key + same payload → return original
receipt; same key + different payload → `private_operation_conflict`) *before*
the write, and all IDs are fresh UUIDv4 so a colliding `revision_id` path
requires a UUID collision. So the practical exposure is narrow. But the
immutable-collision guarantee the plan and audit asked for at the artifact
layer for these families is not asserted; a different `operation_key` that
reuses a caller-supplied `--record-id` with a caller-supplied
`--parent-revision` pointing at a non-current revision is rejected by the CAS
check in `create_record` (`private_records.py:313-318`), not by the journal.

**Recommendation:** pass `allow_artifact_replacement=False` for the G45
source blobs, revision JSON and receipts; keep it `True` only for the
`.gitignore` replacement in `migrate_workpad_layout`.

---

### F8 — `package` / privacy callers not updated for the v2 private roots (Row 7; medium)

**Contract:** Plan stage 1: "Honor the complete ownership map ... no second
workpad tree or second database." Audit section 6
(`SCOUT-03-caller-audit.md:180-188`): `inspect_package` "make inventory
explicitly exclude/reject `records`, `references`, `run-inputs`, `runs`,
`docs` with private state, context indexes, reports, credentials";
`private-home fingerprint` "include explicit v2 migration/transfer inventory
boundaries." Amendment §7: "Source review must cover ... package
inventory/export."

**Observed:** `src/gigai/package.py` is untouched (`git diff HEAD --stat`
shows no entry). `inspect_package` / `export_package` / `_initialize_and_prepare`
/ `_private_home_fingerprint` have no awareness of `references/`,
`run-inputs/`, `records/`, `indexes/`, `reports/scout/`.

**Impact:** No direct leak path was found in the *shipped* package flow —
packages live under `.gigai/packages/<id>/`, the private roots are journaled
in the Gig's private Git repo and are not physically inside a package
directory, so `inspect_package` does not currently walk them. But the
defensive checks the audit required ("if they are ever presented as package
material") are absent, and the private-home migration fingerprint has no v2
boundary. Row 7's package/privacy dimension is unverified and partly
un-implemented. SCOUT-11 owns full private transfer; the *defensive package
exclusion* is SCOUT-03 per the audit disposition.

---

### F9 — Delivered record coverage is narrower than the contract families (Rows 1, 6; medium)

**Contract:** Plan stage 2: "closed kind-specific content/sidecar validation
for the SCOUT-03 record families: profile/preferences, experience Q&A,
imported references, supplied sources/artifacts and selected conversation
excerpts/summaries." "Preserve explicit missing/answered/declined/
not-applicable question states, hard constraints versus soft priorities, and
user-reported/imported/inferred provenance." SCOUT-00 §3 kinds list and
question-state model.

**Observed:**
- `private-record-revision.schema.json` `content` is `oneOf[g45_reference,
  g45_run_input, jsl_blob]`, but the CLI `record create` only offers
  `--content-family [g45_reference|g45_run_input]`. `read_record`
  (`private_records.py:363`) explicitly raises "native content read is not
  implemented in SCOUT-03" for anything that is not a G45 family. So a
  `profile_preferences` or `experience_qa` record can only *wrap a G45 import*
  — there is no way to store an actual structured answer, question state
  (`missing`/`answered`/`declined`/`not_applicable`), hard-vs-soft preference,
  or provenance beyond the single `origin` enum on the wrapper.
- There is no kind-specific sidecar validation. `create_record` accepts any of
  the five `kind` values against any G45 content with no structural check that
  the content matches the kind.
- Selected-conversation capture (SCOUT-00 §3 last paragraph, plan stage 4
  "labels excerpt versus summary") has no dedicated bounded operation — the
  only ingress is `run-input add` (job-description-shaped) or wrapping a
  generic reference.

**Impact:** "save or revise a candidate's preferences and experience" and
"question/context records" from the brief are only nominally present. Rows 1
and 6 are demonstrable for imported-document wrappers, not for the
answer/preference/question model the contract specifies. This may be a
deliberate partial delivery, but the handoff does not disclose it and the
acceptance-mapping table implies full coverage.

---

### F10 — Strict-ID patterns in the new schemas are looser than canonical (Row 1; low)

**Observed:** `private-record-revision.schema.json` uses
`"pattern":"^revision_[0-9a-f-]{36}$"` for `revision_id` / `parent_revision`,
`^record_[0-9a-f-]{36}$` for `record_id`, and `reference-record` /
`run-input-record` use `^ref_[0-9a-f-]{36}$` / `^input_[0-9a-f-]{36}$`.
`[0-9a-f-]{36}` accepts any 36-char mix of hex and hyphens, not a UUIDv4
shape. The runtime `_id()` helper (`private_records.py:54-58`) does enforce a
real UUIDv4, and `create_record` calls `validate_entity_id(stable_id,
expected_prefix=EntityPrefix.RECORD)` for a caller-supplied `--record-id`. But
`list_revisions` (`private_records.py:329-346`) validates read-back revisions
against the schema pattern only, so a hand-crafted
`revision_<36 chars>.json` that is not canonical would pass strict validation
on read.

**Contract:** B1: "IDs reuse canonical ... namespaces ... no feature-local
derivation." Defense-in-depth gap, not an exploit path.

**Recommendation:** tighten the patterns to the canonical UUIDv4 regex
(`[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}`) already
used in `canonical.py`.

---

### F11 — `rebuild_scout_projection` writes `context.json` outside the database lock (Row 4; low)

**Observed:** `private_records.py:381-397` — the `with database_lock(resolved.path):`
block covers the `state.sqlite` table writes and commit, then closes. The
`indexes/context.json` `os.replace` (lines 393-397) happens **after** the lock
is released. Two concurrent `_publish` calls could interleave the SQLite table
state and the `context.json` file state.

**Impact:** Low — both artifacts are disposable and rebuilt wholesale from
journal authority on the next operation; no authoritative data is at risk.
Still, the atomic-projection intent of the lock is not fully honored.

---

## Acceptance-row mapping

| Row | Requirement (abbreviated) | Assessment |
|---|---|---|
| 1 | Sanitized import preserves bytes; equivalent imports idempotent; changed content is new immutable object; reject malformed IDs / binary / bad-UTF-8 / oversize / unsafe parents / foreign scope | **Partially met.** Byte preservation, digest idempotency, UTF-8 / size / symlink-parent rejection are implemented (`private_records.py:72-96`, `202-219`, `222-245`) and one fixture proves the happy path + byte preservation. Immutable-collision guarantee is journal-overwrite-eligible (F7). Native answer/preference/question records absent (F9). Adversarial rejection cases largely untested. |
| 2 | Saved-default vs task-only override; stale/concurrent parents; changed-payload retry fails without extra commits; archive retains old selections | **Not met.** `saved_default`/`run_override` unimplemented (F1). CAS stale-parent refusal and operation-key idempotency/conflict *are* implemented (`private_records.py:163-172`, `313-318`) and one fixture covers stale parent. "Archive retains old selections" — `record archive` operation is in `scout-operation-receipt` enum and `state:"archived"` is in the revision schema, but there is no CLI `record archive` command and no test. |
| 3 | Restart/rebuild recovers records, relationships, questions, exact historical inputs; inject failures around journal publication, projection commit, recovery; no duplicate/invented success | **Partially met.** Journal publication + generic `reconcile_journal` recovery are sound and transition-agnostic. Projection-failure branch is an uncaught exception, not `committed`+`projection_pending` (F3). No cursor (F4). No relationships (`relationships:[]` hard-coded, `private_records.py:320`). No failure-injection tests. |
| 4 | Legacy-first / Scout-first index access, rebuilds, concurrent G22 writes preserve both families; unknown/malformed tables fail visibly; cursor drift / direct SQL tampering do not affect selection | **Partially met.** `_read_scout_tables` (`index.py:254-283`) raises `JournalIndexError` on unknown tables and validates the `(key,payload)` column shape — good. `database_lock` documents journal→database order. But only the lifecycle G22 writer is wired (F5); `persist_trace` callers are not. Selection is tamper-safe only because it bypasses SQLite (F4). |
| 5 | Layout migration preserves v1 bytes and ignored drafts; forged markers / ignore changes / unknown roots / collisions / symlink escapes refuse; approved-tool mutations prove inventory/effect/version | **Partially met.** `workpad_layout_version` (`workpad.py:535-573`) checks marker identity, ignore-policy digest in the marker, and journal-authenticates via `git log --format=%s` matching `"journal: workpad layout migrated"` (subject format confirmed at `journal.py:863`). `_preflight_artifact_destinations` rejects redirected parents. `migrate_workpad_layout` refuses pre-existing v2 roots (`private_records.py:114-117`). But: the approved-tool-mutation half is entirely absent (F2); migration collision handling refuses rather than preserving-and-reconciling dirty v2-named drafts (plan stage 1 wants "Preserve tracked legacy paths and dirty ignored drafts"); no forged-marker / symlink-escape tests. |
| 6 | Fresh agent lists scoped metadata, reads one chosen revision, selects it with a posting; later saved answer leaves older sealed Plan unchanged; missing/changed/unjournaled/foreign refs refuse before Run allocation; no unselected/private provider leakage | **Partially met.** Metadata listing is redacted (`private_records.py:266`, `355`); `read_record --content` returns only the named bytes to the invoking agent and re-checks the digest (`private_records.py:296-297`, `364`). Provider-ingress refusal is a bypassable path-prefix check at one call site (F6). "Older sealed Plan unchanged when a newer wrapper revision exists" is not demonstrated — `select_exact_inputs` dedups by `(path, sha256)` (`private_records.py:412-415`) which the plan flags as a risk ("Deduplication must not erase an explicit revision choice"); no test. |
| 7 | Source/schema/CLI + legacy tests pass; schema inventories + installed-resource verification pass; isolated wheel proves new commands/resources; run full offline suite, distinguish env-only failures | **Partially met.** Schema inventory is internally consistent: 49 entries across `SHA256SUMS`, `verify_installed_schemas.py`, `validators.SCHEMA_NAMES` and disk; `tools/verify_installed_schemas.py` passes ("verified 49 installed GigAI schemas"). 6 of the 49 are SCOUT-03's; the handoff's "recognize 49 schemas" conflates SCOUT-02 / G43 schemas into SCOUT-03 evidence. The 2 SCOUT-03 fixtures pass locally. **The full offline suite is not verified** — Terra's task settled failed for this reason; this reviewer observed an ordering-dependent flake (below). `package` privacy checks not done (F8). |

## Full-suite / environment observations (not SCOUT-03 findings)

- `.venv/bin/pytest -q tests/test_scout03_private_records.py tests/test_journal_locking_recovery.py`
  produced `1 failed` —
  `test_journal_locking_recovery.py::test_eight_process_race_allocates_strict_committed_order`
  with `InterprocessLockUnavailable: ... mount probe failed mount.atomic_replace`.
- The same race test **passes** in isolation (0.86 s), passes in reverse
  order with the scout03 tests (`23 passed`), and passes when scout03 runs
  twice before it (`3 passed`, 2.85 s). It failed only in the one run where
  the full 22-test `test_journal_locking_recovery.py` ran *after* scout03 and
  took 13.86 s.
- Assessment: a load/timing-sensitive **environment flake** in the
  multi-process mount-probe path, consistent with Terra's noted "environment-only
  failures" and the reason `task_ec34c05cade5` settled failed. Not attributed
  to SCOUT-03 code. The full offline suite remains genuinely unverified and is
  a verification follow-up, not a review conclusion.

## Out-of-scope confirmations (correctly excluded per brief)

- **SCOUT-04**: no external-recording Plan/Run/checkpoint executor was added
  (`run.py` external family absent); the handoff states this explicitly. The
  B1 `external-recording-*` schemas are not present. Correct — SCOUT-04 owns
  them and consumes the same selection resolver.
- **SCOUT-05**: no registry-v3 (`workspace_owners`, `template_instances`),
  no username-aware `gigai init` batch, no per-Gig scaffold shipping.
  `registry.py` and `package.py` init paths untouched. Correct.
- **SCOUT-11**: no private transfer / backup command. Correct.
- **G22 `interview_events`** is preserved as a legacy projection table by
  `_read_scout_tables` and not treated as Scout record authority. Correct.

## Recommended disposition

Return to a bounded implementation task. Minimum to meet contract:
F1, F2, F3 (blockers), then F4, F5, F6 (major). F7–F11 are corrections that
should land in the same wave. No acceptance is inferred from the worker's
completion claim; the full offline suite and adversarial fixtures for all
seven rows remain owed to independent verification.

## Evidence limits

Source and document review only in the current worktree. No provider or
network calls, no private `.gigai` mutation, no config or commit, no
subagents. The 2 SCOUT-03 fixtures were run for signal only; this reviewer
does not own or expand the SCOUT-03 test suite.
