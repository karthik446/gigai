# SCOUT-04 — Independent source-only review (post-correction)

**Reviewer:** Fresh independent Claude session (separate from the
user-taken-over native reviewer terminal).
**Scope:** Source-only. No source/test/provider/private-data edits, no
commits. Coordinator owns the 13-test rerun and central schema inventory.
**Verdict:** **Changes requested.** Four confirmed, independently reproduced
defects, three of which go to the heart of "contract-bound successful
completion." The strict-nested-schema work and idempotency/CAS/terminal
gating are otherwise in good shape.

**Files inspected:** `src/gigai/external_recording.py`,
`src/gigai/external_cli.py`, the five `external-recording-*.schema.json`
families, `tests/test_scout04_external_recording.py`,
`src/gigai/private_records.py` (`_content_for_reference`, `import_reference`,
`import_run_input`, `list_revisions`), `src/gigai/run.py`
(`_resolve_authority`, `resolve_selected_graph_authority`),
`src/gigai/run_plan.py` (reference selection construction),
`src/gigai/journal.py` (`record`, `_preflight_artifact_destinations`),
`src/gigai/graph_set.py`, `src/gigai/workpad.py` (canonical roots).
Contract sources: SCOUT-00-contract-amendments.md §9 / B1 / B2 / M1 and the
§9 CLI intro paragraph; parallel-delivery-plan.md; SCOUT-04-external-lane-
implementation.md.

---

## Confirmed findings (prioritized)

### F1 — `submit` completion is NOT bound to the sealed check contract; any run artifact counts as a "check" (HIGH)

**Path:** `src/gigai/external_recording.py:1354-1364` (submit `valid_checks`),
contrast with `:659-663` / `:715` where `check_contract`
(`completion_evidence_contract`) is sealed into the Plan but never read again.

**Contract:** B1 (`submit` row, line 406): "Validates pinned contracts and
all current content; journals terminal `succeeded` only when requirements
pass." §9 intro: "`submit` validates exact outputs and checks, then journals
completion." The `check_contract` is pinned in the Plan precisely so submit
can enforce it.

**Actual behavior:** `submit` requires `check_refs` to be non-empty and each
ref to (a) resolve via `_safe_ref` and (b) have a path under
`runs/<run_id>/artifacts/`. Nothing ties a check to the sealed
`check_contract` content, its `fields`, or any completion-evidence shape.

**Counterexample (reproduced):** With the standard fixture graph
(`completion_evidence_contract` = `{"fields": ["proposal-validation"]}`),
plan → start → checkpoint one `role_summary` output, then
`submit(output_refs=[output], check_refs=[output["markdown"]])` — i.e. the
output document's own Markdown blob passed as the "check." Result:
`outcome == "succeeded"`, receipt journaled via
`external_recording_succeeded`. The output's own sidecar
(`check_refs=[output["sidecar"]]`) works identically and is what the passing
test `test_completion_requires_contract_bound_output_and_check` actually
does.

**Minimum fix:** In `submit`, load the sealed `check_contract` content
(already available as `plan["check_contract"]` ref → bytes) and validate each
submitted check against that contract's declared evidence shape/fields; at
minimum reject a check ref whose bytes are byte-identical to a submitted
output blob, and require checks to be a distinct artifact family (e.g. a
checkpoint-recorded check sidecar) rather than "anything under
`runs/<id>/artifacts/`."

---

### F2 — Successful completion is bound to an arbitrary agent-declared `output_kinds` list, not the approved output contract (HIGH)

**Path:** `src/gigai/external_recording.py:487-504` (plan reads
`typed["output_kinds"]` straight from the caller envelope),
`:984-996` (`_requested_output_kinds` reads it back from
`plan["invocation"]["input"]["output_kinds"]`), `:1349-1353` (submit requires
submitted kinds == that same caller-supplied set). The sealed
`output_contract` (`descriptor["output_contract"]`, fixture
`{"fields": ["report"]}`) is stored at `:714` and never consulted for
validation anywhere.

**Contract:** B1 (`plan` row): validates "approved authority and required
inputs." §9 intro: "`submit` validates exact outputs." The whole point of
sealing `output_contract` into the Plan is that "required outputs" is defined
by the approved contract, not by the requesting agent.

**Actual behavior:** `output_kinds` is a free-form
`^[a-z][a-z0-9_-]{0,63}$` string array chosen by the caller. As long as the
checkpoint sidecar's `output_kind` and the submit output `kind` match that
same self-declared list, submit succeeds.

**Counterexample (reproduced):** `plan(output_kinds=["totally_made_up_kind"])`
→ start → checkpoint a `totally_made_up_kind` artifact whose sidecar is
self-consistent → `submit` → `outcome == "succeeded"`. The approved
`output_contract.fields == ["report"]` is never checked; `"report"` is never
required.

**Minimum fix:** Derive the required output kinds/shape from the sealed
`output_contract` bytes (parse `descriptor["output_contract"]` content, which
`_approved_contract` already knows how to read) and validate both the
checkpoint sidecar `output_kind` and the submit output `kind` set against
that. Reject `output_kinds` entries that are not present in the approved
output contract.

**Combined impact of F1 + F2:** a terminal `external_recording_succeeded`
receipt can be produced for an approved Gig using (a) an output kind that
appears in no approved contract and (b) a "check" that is just a copy of the
output. This is exactly the "arbitrary requested kind plus any check"
failure the task asked to rule out; it is not fixed.

---

### F3 — Second direct-CLI `plan` under the same graph (distinct operation key, no `selection_record`) fails with a misleading `external_reconciliation_required` (MEDIUM–HIGH)

**Path:** `src/gigai/external_recording.py:561-611`. The constructed
selection record embeds `"created_at": _now()` (line 592) while its ID is
`derive_deterministic_id("graph_selection", {...})` (line 561, `created_at`
excluded). The artifact is written to the deterministic path
`graph-selections/<selection_id>.json` via `_journaled` → `writer.record`
without `allow_artifact_replacement`, so
`journal._preflight_artifact_destinations`
(`src/gigai/journal.py:954-955`) raises
`JournalConflictError("journal immutable artifact already exists")` on the
second construction. `_journaled`'s bare `except Exception`
(`external_recording.py:463-470`) then remaps it to
`external_reconciliation_required` / `next_action:
install_external_transition_registry`.

**Contract:** B1: "With a different operation key, the caller must explicitly
reuse that sealed selection **or obtain a new selection and Plan**." The
"obtain a new selection" branch is only reachable for `direct_cli`
(line 555-560), and it is broken on the second use.

**Reference implementation:** `src/gigai/run_plan.py:911` builds the exact
same selection record but sets `"created_at": graph_set["created_at"]`
(deterministic) precisely to keep re-construction byte-idempotent. SCOUT-04
diverged from that.

**Counterexample (reproduced):** `_fixture`; `plan(op_key="planA",
selection_record=None)` succeeds and writes
`graph-selections/graph_selection_<uuid>.json`; ~1.1s later
`plan(op_key="planB", output_kinds=["role_summary","skills_gap"],
selection_record=None)` fails:
`ExternalRecordingError: external_reconciliation_required — "external
recording transition is not available for journal publication"`, root cause
`JournalConflictError: journal immutable artifact already exists` at
`journal.py:955`.

**Minimum fix:** Set the constructed selection's `created_at` to a
deterministic value (`graph_set["created_at"]`, matching `run_plan.py`), OR
detect an existing byte-equal selection at the deterministic path and reuse
it (read-committed + ref compare) instead of re-journaling. Independently:
`_journaled`'s `except Exception` should not swallow `JournalConflictError`
into a "transition not available" message — surface `external_operation_conflict`
/ `external_checkpoint_conflict` / `external_reconciliation_required` by
cause type.

---

### F4 — `g45_reference` inputs (and `scout_record` revision inputs) cannot be sealed into a Plan; the emitted Plan fails its own strict schema (MEDIUM)

**Path:** `_input_ref` (`src/gigai/external_recording.py:334-376`) returns
`{"family": "g45_reference", "reference_id": ...}` for the reference family
and `{"family": "scout_record", "record_id": ..., "revision_id": ...}` for
the revision family. The Plan schema
`external-recording-plan.schema.json` `$defs/g45_input`
(`src/gigai/schemas/external-recording-plan.schema.json:32`) hard-requires
`"family": {"const": "g45_run_input"}` with key `run_input_id`. `plan` then
calls `_schema("external-recording-plan.schema.json", plan_payload)` at
`external_recording.py:724`, which rejects any non-`g45_run_input` input as
`external_invocation_invalid` / "external protocol payload failed strict
validation."

**Contract:** B1 `raw_input_ref` oneOf explicitly admits `g45_reference` and
`scout_record` (invocation schema
`external-recording-invocation.schema.json:39-43` matches). B2: "Plan input
discriminator accepts exact G45 IDs with record/snapshot digests or exact
Scout record/revision IDs." So the *input* schema accepts three families but
the *sealed Plan* schema accepts one.

**Corroborating evidence this is an omission, not intent:** `start`'s
sealed-source re-resolution at `external_recording.py:824` already branches on
`family in {"g45_reference", "g45_run_input"}` and reads
`item.get("reference_id") or item.get("run_input_id")` — code that can never
execute because the Plan can't hold a `g45_reference`. The `scout_record`
family is not handled in `start` re-resolution at all (silently skipped).

**Counterexample (reproduced):** `import_reference(kind="resume", ...)` →
`plan(input_refs=[{"family": "g45_reference", "id": ref_id}], ...)` →
`ExternalRecordingError: external_invocation_invalid — "external protocol
payload failed strict validation"` (raised at `external_recording.py:99`).

**Minimum fix:** Extend the Plan schema `inputs` items (and the checkpoint
`artifact_input.sidecar.selected_inputs` items, which must equal
`plan["inputs"]` per `external_recording.py:1180`) to the same three-family
`oneOf` used for resolved inputs — a `g45_reference` resolved shape
(`family` + `reference_id` + `record_ref` + `snapshot_ref`), a
`g45_run_input` resolved shape (current `g45_input`), and a `scout_record`
resolved shape (`family` + `record_id` + `revision_id` + `content`). Then
add `start` re-resolution for `scout_record`. Add parameterized negative +
positive fixtures for all three families.

*Note on scope:* native-record (`jsl_blob`) source integration is explicitly
out of scope for this bounded G45 lane and is not counted here. `g45_reference`
is G45 content and is in scope for B2; `scout_record` is the B2 wrapper over
G45 content and is likewise in scope.

---

## Contract gaps (not reproduced as runtime crashes, but acceptance-relevant)

### G1 — `external_successor_required` carries no pinned predecessor / missing-input metadata

B1 (line 415-417) and M1 require: return `external_successor_required`
"**plus pinned predecessor and missing-input metadata**" and "the typed next
action exposes only selected input metadata." `ExternalRecordingError.result()`
(`external_recording.py:56-64`) returns only `{code, message, next_action}`.
`submit`'s successor refusal (`:1295-1299`) supplies
`next_action="create_successor_plan"` and nothing else. A caller cannot
build the successor Plan from the error. Add a structured, redacted
`next_action` payload (predecessor ref = the current Run/last checkpoint;
the missing question ids/prompts; the selected input metadata only).

### G2 — No "resume the same Run if sealed inputs are unchanged" path

B1: "A required-input checkpoint **can resume the same Run only if sealed
inputs are unchanged**; otherwise return `external_successor_required`."
`submit` currently *always* refuses with `external_successor_required` when
the last checkpoint has any `state == "missing"` question, with no
inputs-unchanged fast path and no mechanism to record a user-reported answer
and continue the same Run. M1's full chain (save user-reported answer →
inspect old Run `waiting_input` → seal/start successor with the new answer +
selected old checkpoint artifacts → old bytes/checks retained) is only
partially realizable and has no fixture.

### G3 — `_journaled` error remapping erases real failure causes

`external_recording.py:463-470` catches **every** exception from
`writer.record` and reports `external_reconciliation_required` /
"transition is not available for journal publication." This masks
`JournalConflictError` (F3), and would mask a genuine journal-recovery
condition vs. an unregistered-transition condition. Branch on the caught
exception type.

---

## What holds up (bounded accept for these areas)

- **Strict nested schema on emission and read.** The prior malformed-Plan
  probe now rejects: empty `invocation` / empty `inputs` item / empty
  `limits`, empty `inputs` array, empty `invocation.input`, extra `effects`
  member, and `receipt` `succeeded` with empty `outputs`/`checks` are all
  rejected (independently re-verified against the five installed schemas).
  Every read path (`_read_plan`, `_read_run`, `_run_receipts`,
  `_checkpoints`, `_replay`, `inspect`, `_validate_predecessor`,
  `_plan_for_run`) routes through `_recorded(...)` which strict-validates.
- **Same-key replay vs conflicting payload, before CAS/terminal gates.**
  `plan` (`:513-536`, `:683-698`), `start` (`_replay` at `:790`),
  `checkpoint`/`cancel` (`_replay` at `:1067`, before `_read_run` and the
  `_terminal` gate at `:1071`), and `submit` (`_replay` at `:1277`, before
  `_read_run`/`_terminal` at `:1280`) all check replay/conflict first,
  scoped by `(operation, operation_key)` matching B1's
  `(project_id, gig_id, operation, operation_key)` uniqueness. Identical
  retry returns the original object; conflicting payload returns
  `external_operation_conflict`. Reproduced for `plan` (output_kinds change →
  conflict) and `cancel` (idempotent replay returns same `receipt_id`).
- **Checkpoint CAS and terminal immutability.** Parent-checkpoint mismatch →
  `external_checkpoint_conflict` (`:1129-1133`); post-cancel checkpoint →
  `external_run_terminal` (`:1071-1074`). Reproduced.
- **Approved graph selection reuse / identity.** Reuse requires a committed,
  byte-exact `graph-selections/` ref (`read_committed_artifact` +
  `_ref` compare at `:625-640`), re-validated via `validate_selection_record`
  against the approved Graph Set, with `selected_graph_id` cross-check
  (`:642-655`). Plan identity includes the `selection_record` ref
  (`:672`), so a different selection yields a different `run_plan_id`. Agent
  reuse of a direct-created selection for a distinct plan key is exercised
  and passes.
- **Start authority and source revalidation.** `start` re-resolves authority
  via `_authority` → `_resolve_authority` (reads bytes from the immutable
  git tag/commit, `run.py:620-642`) + `resolve_selected_graph_authority`
  (re-reads every descriptor ref from the approval commit,
  `run.py:702-711`), and rejects a post-seal version/goal-graph drift
  (`external_recording.py:809-816`). `_approved_ref` adds per-component
  symlink checks and exact digest/size revalidation on the output/check
  contract refs (`:386-415`).
- **Canonical roots and no new top-level root.** Artifacts use
  `run-plans/<id>/external-plan.json`, `runs/<id>/{external-run.json,
  checkpoints/,receipts/,artifacts/}`, and the *existing* central
  `graph-selections/` directory (`workpad.py:48`, identical to
  `run_plan.py`). Snapshot roots are the existing
  `references/ run-inputs/ records/external/ run-plans/ runs/`.
- **Cancellation and limits.** Cancel journals a terminal `cancelled`
  receipt with empty outputs/checks and retains all prior evidence;
  `_terminal` then blocks further writes. Envelope 262144, artifact
  1048576, per-op artifact count 32, per-op total 4194304, checkpoint
  questions 32, checkpoints per Run 256 are all enforced at the right call
  sites.
- **Concurrency.** All mutations run inside `run_with_journal_writer`
  (single writer lock); snapshot, replay/CAS, and publication happen under
  that one lock.
- **CLI origin discipline.** `external_cli._call` always constructs the
  envelope from `_input(...)`; `_invocation` forbids `direct_cli` origin
  from carrying anything but `{"kind":"operator","id":"local-user"}` and
  requires a bounded agent actor + session for `agent_invocation`
  (`:252-269`). No weaker path around `_schema` validation.
- **`external_cli` mount independence.** `external_group` is a standalone
  Click group; writers require `--invocation PATH` or `--stdin` (exactly
  one), bounded to 262144 UTF-8 bytes.

## Whole-SCOUT-04 acceptance note

This review covers the bounded G45 external-recording lane
(`external_recording.py` / `external_cli.py` / five schemas / focused
tests). It does **not** clear: top-level `gigai external` registration and
managed-vs-external reader discrimination (coordinator integration gate),
the central schema digest/count/fixture inventory (Astra), native-source
(`jsl_blob`) resolution (explicitly deferred to its own lane), and
fresh-session UAT of the M1 successor chain. Those remain open regardless of
the four findings above.

## Recommended disposition

Not accepted. F1 and F2 must be fixed for "contract-bound successful
completion" to be true at all; F3 breaks a legitimate multi-recording
workflow that B1 explicitly contemplates; F4 makes half the B2 input
discriminator unusable. F1–F4 are all local to the lane's owned files
(schemas + `external_recording.py`) plus new negative/positive fixtures —
no redesign, no schema-family addition, no workpad-root change.
