# SCOUT-05 source → pending-proposal integration — independent review

**Type:** Independent, read-only review of the delivered source-materialization /
compiler / recoverable-init / first-proposal-robustness integration in the
current worktree. No source, test, or schema files were edited. No provider,
`init`, `approve`, activation, Run, or commit was executed. No private `.gigai`
or user workspace state was touched. Two disposable read-only probes were run
(schema-registry validation of a synthetic v2 binding; `shasum -c` of
`SHA256SUMS`); nothing was written to the repo.

**Date:** 2026-09-09.
**Repo state:** branch `karthik446/gigai-v0.1.7`, `HEAD` `fda4857`. All reviewed
Scout modules and schemas are untracked/working-tree only; the `.md`/`pyproject`
and central `src/gigai/*.py` edits are modified-vs-HEAD.

**Scope inputs read:** `SCOUT-00-contract-amendments.md`,
`SCOUT-00-user-owned-gig-amendment.md`,
`SCOUT-05-source-proposal-integration-plan.md`,
`SCOUT-05-source-materialization-implementation.md`,
`SCOUT-05-first-proposal-robustness.md`, `SCOUT-05-first-proposal-review.md`.

**Reviewed source (working tree):**
`src/gigai/scout_materialization.py` (391 L), `src/gigai/scout_template.py`
(131 L), `src/gigai/default_init.py` (878 L), `src/gigai/lifecycle.py`
first-proposal path (`_definition_source_path` 102–164;
`propose_graph_set_offline` / `propose_first_graph_set_offline` 1537–1987;
`approve_offline` explicit-`gig_id` path 1989–2133),
`src/gigai/cli.py` init/approve/`graph-set` surfaces,
`src/gigai/schemas/template-instance-binding.schema.json`,
`src/gigai/validators.py` / `SHA256SUMS` / `tools/verify_installed_schemas.py`
registrations, and the tests
`tests/test_scout05_materialization.py`,
`tests/test_scout05_first_proposal.py`,
`tests/test_scout05_init.py`,
`tests/test_scout05_init_recovery.py`,
`tests/test_scout05_init_corrections.py`.

**Recorded evidence relied on (not re-run per coordinator instruction):**
the handoffs' focused runs — materialization `7 passed`, first-proposal
`13 passed`, plus the coordinator's combined focused integration pass and the
central `7 tests / 134 subtests` schema pass. No pytest was executed here.

---

## Verdict

**The implementation meets the bounded "source → pending first proposal"
contract for candidate preparation.** It is approvable *as a candidate-preparation
slice*. This is **not** a whole-SCOUT-05 acceptance and makes no release-eligibility
or approval claim.

The three prior first-proposal review findings that required code changes
(F1 path-first link validation, F3 typed error for a non-canonical Gig document,
F4 append-free in-lock re-verification) are **fixed and now tested**. Source
copying is inert (byte reads only; no import, no `exec`, no dynamic materializer
hook). The first proposal is `kind:"create"`, `status:"proposed"`, null
predecessor/base, with a separate direct-approval next action; `init` never
approves, never allocates an active pointer, and never changes an existing
active Gig. Interruption/replay at every published stage recovers the exact
sealed source identity and proposal ID. Schema registration is internally
consistent (56/56 across `SHA256SUMS`, `SCHEMA_NAMES`, the installed verifier,
and disk; final binding SHA-256 `23b99113…d12ed40` matches all four).

Findings are **one LOW** and **two NIT**. None blocks the candidate slice. No
finding requires a schema change.

---

## Deferred gates (correctly out of this slice, verified as such)

These are contract-sanctioned deferrals, not defects:

1. **Scout is not in the release-eligible default inventory.**
   `catalog.CATALOG` contains only `sync-references`, `plan-a-research`,
   `review-plan`. `default_init.default_inventory()` returns exactly those.
   `gigai init` with no `inventory=` therefore does **not** provision Scout;
   Scout is reachable only via the explicit internal
   `scout_candidate_inventory()` path (`scout_template.py:128`), gated by the
   closed `is_scout_candidate` digest+version+package-id check
   (`scout_materialization.py:53-62`). This matches the plan's goal 4 and the
   amendment §3 "default eligibility" deferral to SCOUT-06–10.

2. **First-version tool receipts.** A first Graph Set approves as numeric
   version `1`; `scout-operation-receipt.schema.json` still floors `gig_version`
   at `2`. No receipt is issued in this slice. The plan (goal 1) and the prior
   review both record this as the later tool-receipt slice's obligation.

3. **No approved tool/source manifest, no post-approval binding projection,
   no wheel/installed-CLI proof, no update *adoption*, no report generation,
   no private-data import.** The compiled graphs pin
   `executor.kind = local_capability` / `gigai.offline` with empty `tools` and
   `effects:["write_workpad"]`; binding authority for real executable tool
   source is explicitly *not* conferred by including `gig.py` in the snapshot.
   `default_init.py` reports `update_available` but never adopts.

---

## Findings

### R1 — LOW (strict-schema-caller consistency): the v2 `template-instance-binding` artifact is journaled without `validate_serialized_contract`

**Where:** `src/gigai/default_init.py`, Scout branch — the binding dict is built
at `default_init.py:761-779` and written directly at
`default_init.py:797-825` via `record_transition(... JournalArtifact(binding_path,
binding_bytes) ...)`. The non-Scout branch calls `_validate_existing_binding`
(`default_init.py:782-790`); the Scout v2 branch calls **nothing** equivalent,
and neither `default_init.py` nor `scout_materialization.py` ever calls
`validate_serialized_contract("template-instance-binding.schema.json", …)`
(confirmed by full-text search of both modules).

**Contrast — every other new strict artifact in this delivery *is* validated at
its write site:** `gig-proposal-v2` (`lifecycle.py:1830`), `active-gig-version-v2`
(`lifecycle.py:2074`), `workpad-layout` (`private_records.py:146`),
`reference-record` (`:317`), `run-input-record` (`:354`),
`private-record-revision` (`:446`), `scout-operation-receipt`
(`native_records.py:364`, `private_records.py:275`). The
`template-instance-binding:2` artifact — the one strict artifact this slice
adds — is the sole exception, even though Root has now registered its schema in
`SCHEMA_NAMES` / `SHA256SUMS` / the installed verifier.

**Proof vs assertion:** The recovery/read path (`_binding_authority`,
`default_init.py:210-278`) performs a thorough manual closed-shape check that is
equivalent to the schema for the fields it enumerates, and
`test_scout05_materialization.py:44-48` reads back `schema_version == "2.0"` and
a few fields — but no test validates a written binding against the registered
JSON Schema. The *write* path's correctness rests entirely on the dict literal
at `default_init.py:761-779` being hand-correct.

**Failure scenario:** A future edit to that dict literal (a renamed key, a
non-`sha256:`-prefixed digest passed through from a changed `entry`, an
`approval` shape drift) would be journaled as authority and would pass every
existing test; it would only be caught later, on a *reconciliation* read, as a
`template_reconciliation_required` on an otherwise-healthy workspace. The
implementation handoff's stated intent ("runtime performs the same closed shape
validation locally until that registration is integrated") is now only
half-met: registration is integrated; the runtime writer still does not use it.

**Smallest correction:** In the Scout branch, before `record_transition`, call
`validate_serialized_contract("template-instance-binding.schema.json",
binding_bytes)` and raise `DefaultInitError("template_reconciliation_required",
…)` on failure; optionally also validate `authority.binding_bytes` in
`_binding_authority`'s v2 branch. Add one test asserting a written Scout binding
validates against the registered schema.

### R2 — NIT (documentation completeness): `template-instance-binding.schema.json` is undocumented in `schemas/README.md`

**Where:** `src/gigai/schemas/README.md`. The working-tree diff adds descriptive
bullets for every other new SCOUT-02/03/04 and JSL-01 schema
(`gig-graph-set`, `graph-selection-record[-v2]`, `gig-proposal-v2`,
`active-gig-version-v2`, `run-plan-v2`, `run-manifest-v2`, `reference-record`,
`run-input-record`, `private-record-revision`, `workpad-layout`,
`scout-operation-receipt`, `provider-review-closeout-receipt`). There is no
mention of `template-instance-binding` (grep: no hit). The schema README is the
documented catalogue of serialized contracts; the one binding contract this
slice introduces is missing from it.

**Impact:** Documentation only. Same root cause as R1 — this one artifact is
under-integrated relative to its siblings.

**Smallest correction:** One bullet under "Production identity API" noting the
v2 prepared-instance binding pins source-inventory ref/digest, the pending
`kind:create` proposal ref, unapproved state, and nullable
customization/approved-version, and that the registry row is only its cache.

### R3 — NIT (residual test gap carried from prior review, F2): the committed-proposal multi-publisher recovery branch is still unexercised

**Where:** `src/gigai/lifecycle.py:1870-1871` — `except JournalConflictError …
raise LifecycleError("existing first Graph Set proposal cannot be
authenticated")` inside `publish_first`.

**Proof vs assertion:** `test_first_graph_set_replays_exact_pending_publication_and_refuses_changed_source`
now covers working-copy drift (`+ b"\n"` on `gig-proposal.json`), sealed-identity
tamper (`+ b"\n"` on `first-proposal-inputs.json`), sealed-identity deletion,
and changed definition bytes — a real improvement over the prior review's F2.
It does **not** cover a committed `manifests/gig-proposal.json` with two
journal publishers, so the `JournalConflictError` → "cannot be authenticated"
path is code-reading only. The analogous inventory path in
`scout_materialization._read_snapshot` (`:270-271`) is likewise untested.

**Impact:** Low. The guard reads correctly and uses `read_committed_artifact`
(not the working tree) for authentication. This is a coverage gap, not a
demonstrated defect.

**Smallest correction:** Extend the retry test with a second no-op commit
touching `manifests/gig-proposal.json`, then assert the multi-publisher refusal
and an unchanged journal commit set.

---

## Audit dimension results

| Dimension (from task) | Result | Basis |
|---|---|---|
| Source / proposal / binding pins | **OK** | `_source_digest` folds compiler+definition versions + sorted `{path,digest,size}`; `version`/`prefix`/`inventory_path` are deterministic → idempotent resume. Binding pins `software_inventory` ref, `source_digest`, pending `proposal_id`+`proposal_ref`, `approval={unapproved,null}`, `customization_parent=null`. |
| Committed vs working authority | **OK** | `_read_snapshot` (`scout_materialization.py:263-281`) and `_binding_from_snapshot` (`default_init.py:149-188`) both re-read committed bytes, reject symlink / non-file / drift / malformed, and raise `template_reconciliation_required` on `JournalConflictError`/`JournalReconciliationRequired`. First-proposal recovery authenticates via `read_committed_artifact`, never the working tree. |
| Locking / replay / interruption / cache recovery | **OK** | Lock order target-init (`default_init.py:599`) → short `registry.transaction()` blocks (601/645/671/687/841) → workpad journal (730/738/749/799), never a registry txn held across a journal write. `migrate_workpad_layout` is idempotent (`return "already_v2"`). Observer-injected interruptions at `intent_prepared` / `binding_published` / `source_snapshot_published` / `candidate_proposal_prepared` all resume to the same reserved Gig ID, same proposal ID, unchanged binding commit set (tests in `test_scout05_init_recovery.py`, `test_scout05_materialization.py`). Cache-loss recovery reconstructs the row from the committed binding only after full validation; a corrupted `binding_sha256` refuses (`template_reconciliation_required`). |
| Path / symlink / collision / customization | **OK** | `_safe_root_path` rejects absolute / `..` / backslash / non-allowlisted `parts[0]`, and walks every component for symlinks → "redirected" (test: redirected `gig.py` refused). First-run editable-root collision → "collides before materialization". Resume rewrites only *missing* editable copies; a byte-differing copy is recorded as `customized_paths` and preserved (test: customized `README.md` survives rerun; per-project `ui/style.css` edits isolated). `_definition_source_path` does lstat-walk-before-resolve, re-checks after `resolve`, and re-checks inside the writer lock (F1/F4 tests). |
| No implicit execution / approval | **OK** | `scout_source_files()` returns byte reads of a fixed path tuple; `_compiled_snapshot` only parses/re-emits JSON; no `import`, `runpy`, `exec`, or subprocess anywhere in `scout_materialization.py` / `default_init.py`. Proposal is `status:"proposed"`; `approve_offline` is a separate call; `DefaultInstanceResult.next_action` = `gigai approve <id> --gig <gig>`. Tests assert `active_gig_id is None` and no `active-gig-version.json` post-init; approving with a *different* active Gig leaves the active pointer unchanged. `creation_manifest.model_target = "none"`. |
| Strict schema callers | **One gap (R1)** | All new strict artifacts validated at write time *except* `template-instance-binding:2`. First-proposal `gig-proposal-v2` and `active-gig-version-v2` are validated (`lifecycle.py:1830`, `:2074`, `:2174-2181`). Schema refs resolve (`common:1#/$defs/{project_id,gig_id,gig_proposal_id,sha256,artifact_ref}`, `gig-package:1#/properties/package_id`); a synthetic v2 binding validates, and control-char username / `schema_version:"1.0"` are rejected (probe). |
| Username `Cc` exclusion | **OK / consistent** | `normalize_username` rejects only category `Cc`; schema `username` `not` pattern is `[ --]` — exactly the `Cc` code-point set. ZWJ / private-use pass both by design (`test_username_controls_are_rejected_but_joiners_and_private_use_are_preserved`); NEL `` rejected by both. Matches amendment §3 "no control characters". |
| Journal transition registration | **OK** | `workpad_layout_migrated`, `template_instance_bound`, `scout_source_materialized` all added to `journal.TRANSITIONS`. |

---

## Distinguishing proof from schema / assertion

- **The first-proposal null-predecessor invariant is code-owned, not
  schema-owned.** `gig-proposal-v2.schema.json` permits `kind:"create"` with a
  non-null `base_gig_version`; the "first ⇒ null base / null parent" rule lives
  only in `propose_graph_set_offline`'s `_first_proposal` branch and its
  `publish_first` recovery re-checks (`lifecycle.py:1880-1889`). Correct design;
  do not read schema validity as the guarantee. **Proven** by
  `test_first_graph_set_is_source_backed_pending_then_explicitly_approved`.
- **The compiled Scout Graph Set's schema validity is proven by the staging
  validator, not a dedicated assertion.** `propose_graph_set_offline` writes the
  staged members to a tempdir and runs `validate_graph_set` before any journal
  write (`lifecycle.py:1735-1745`); the recorded materialization run
  (`7 passed`) exercises the real compiled output through that path.
- **`is_scout_candidate` is an identity check, not a tautology.** It compares
  four `CatalogEntry` fields against a freshly built `scout_catalog_candidate()`;
  `package_id` and `entry_content_digest` are derived from `catalog_id` /
  `definition_version` / `files`, so the check accepts only an entry whose file
  set hashes exactly to the bundled candidate — the three real `CATALOG`
  entries all fail it.

---

## Scope statement

No source, test, or schema files were modified. Two disposable read-only probes
were run (a `validate_serialized_contract` call on a synthetic in-memory v2
binding via `.venv/bin/python -c`; `shasum -a 256 -c SHA256SUMS`); neither
wrote to the repository. No suites were re-run — the coordinator's central
`7 tests / 134 subtests` pass and combined focused integration pass are relied
on as recorded. No provider call, `init`, `approve`, activation, Run, private
`.gigai` access, or commit was performed. Only this file
(`docs/development/evidence/v0.1.7/Scout/SCOUT-05-source-proposal-integration-review.md`)
was written. All other dirty working-tree changes were left untouched.
