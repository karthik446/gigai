# SCOUT-04 — Corrections re-review (source-only)

**Reviewer:** Fresh independent Claude session, dispatched worker. Separate
from the user-taken-over native reviewer terminal and from the coordinator.
**Scope:** Read-only source review of the post-correction SCOUT-04 external
lane, plus small disposable in-memory / workpad adversarial probes. No
source / test / schema / contract edits, no commits, no providers, no real
`.gigai`. `native_records` lane and its `jsl_blob` source integration are
excluded (active worker; explicitly deferred and deliberately refused in this
lane — not a delivered-lane defect). Coordinator owns the combined focused
suite and the 55-family schema inventory; those are not re-run here.

**Files inspected:** `src/gigai/external_recording.py` (all),
`src/gigai/external_cli.py`, the five `external-recording-*.schema.json`
families, `src/gigai/private_records.py`
(`_content_for_reference`, `import_reference`, `import_run_input`,
`create_record`, `list_revisions`), `tests/test_scout04_external_recording.py`,
`tests/test_scout02_graph_set_flow.py::_write_definition` (fixture contracts).
Contract sources: `SCOUT-00-contract-amendments.md` §5 / §9 / B1 / B2 / M1;
`SCOUT-04-independent-review.md`; latest
`SCOUT-04-external-lane-implementation.md`.

**Verdict:** **Changes requested — one confirmed defect (R1).** The prior
review's F2, F3, F4, G1, G3 are resolved. F1 is *partially* resolved: check
identity/binding is now contract-bound, but `submit` still never re-validates
the submitted **output**'s sidecar, so a malformed output/check pairing
produces a terminal `succeeded` receipt (R1, reproduced; coordinator
independently reproduced the same). G2 is operationally satisfied. One
lower-severity contract-scrutiny item (R2) on the three-field evidence
sidecar: it records a *declared* evidence label + output digest, with no
structured result, so it cannot distinguish a passing check from a failing
one. R2 is source-only / disposition-required, not a reproduced crash.

---

## Confirmed finding

### R1 — `submit` does not re-validate the submitted output's sidecar; a malformed output/check pairing journals terminal `succeeded` (HIGH)

**Path:** `src/gigai/external_recording.py:1519-1544` (submit output loop) and
`:1550-1594` (submit check loop). Contrast `checkpoint`
`:1322-1345` where `_output_sidecar_is_valid` /
`_check_sidecar_is_valid` and the exact artifact tuple are enforced.

**Contract:** B1 `submit` row (line 406): "Validates pinned contracts and
**all current content**; journals terminal `succeeded` only when requirements
pass." §9 intro: "`submit` validates exact outputs and checks." Task charge:
"actual submitted output-sidecar pairing and sealed input/kind/digest binding
at submit (not merely checkpoint)."

**Actual behavior:** For each `output_refs` item `{kind, markdown, sidecar}`,
`submit` only:
1. `_safe_ref`s `markdown` and `sidecar` (committed, digest/size match),
2. requires both paths to start with `runs/<run_id>/artifacts/`,
3. checks `{item["kind"] for item in outputs} == approved_output_fields`.

It never parses the output sidecar, never calls `_output_sidecar_is_valid`,
never checks `sidecar.output_kind == item["kind"]`, never checks
`sidecar.document_sha256 == markdown.content_sha256`, never checks
`sidecar.selected_inputs == plan["inputs"]`, and never requires the
`(markdown, sidecar)` pair to be an exact recorded checkpoint output tuple.
The `kind` is re-declared by the caller at submit time. The only cross-check is
that each submitted `check_refs` item is an exact recorded checkpoint check
sidecar whose `output_sha256` is among the submitted outputs' markdown
digests.

**Counterexample (reproduced — `tests/test_scout04_rereview_probe.py`,
PROBE A / PROBE B2):** run the standard fixture through
`plan(output_kinds=["report"]) → start → checkpoint` with one valid `report`
output pair (`01.md` + `01.json` output sidecar) and one valid
`proposal-validation` check pair (`02.md` + `02.json` check sidecar, whose
`output_sha256` = digest of `01.md`). Then:

```
submit(
  output_refs=[{"kind": "report", "markdown": <01.md ref>, "sidecar": <02.json ref>}],
  check_refs=[<02.json ref>],
  disclosure={"execution": "unobserved", "actor_report": "declared"},
)
```

Result: `outcome == "succeeded"`; the journaled
`external_recording_succeeded` receipt has
`outputs[0].sidecar.path == runs/<run>/artifacts/<cp>/02.json` — i.e. the
output's recorded sidecar is a three-field **check** sidecar
(`{evidence_kind, run_id, output_sha256}`), not an output sidecar
(`{document_sha256, output_kind, run_id, selected_inputs}`). The receipt still
passes `external-recording-receipt.schema.json` (its `output` def only
requires `{kind, markdown, sidecar}` as generic `artifact_ref`s).
`outputs[0].sidecar == checks[0]` in the receipt.

PROBE B (relabel the real report output to `kind: "totally_made_up"`) is
correctly refused with `external_output_missing` — so the F2 *kind-set*
binding to the approved output contract holds; the gap is purely the missing
sidecar-shape / pairing re-validation for outputs at submit.

**Impact:** "contract-bound successful completion" is not fully true: a
terminal `succeeded` receipt can carry an output whose sidecar does not bind
the sealed inputs, does not bind the document digest, and does not carry the
declared `output_kind`. `inspect` then projects that receipt as the Run's
`succeeded` status. This is the exact "actual submitted output-sidecar
pairing … at submit (not merely checkpoint)" concern in the task charge, and
it is not closed.

**Minimum scoped remedy (lane-local, no schema change):** in the `submit`
output loop, keep the sidecar **bytes** from `_safe_ref(...)[1]`, parse them,
and require:
- `_output_sidecar_is_valid(sidecar_value, run_id=run_id,
  inputs=plan.get("inputs"), kind=item["kind"], markdown=<markdown bytes>)`
  (this reuses the existing helper and re-binds run_id, declared kind,
  document digest, and sealed `plan["inputs"]`), **and**
- the `(markdown_ref, sidecar_ref)` pair equals an exact recorded checkpoint
  output tuple — build a `{markdown.path: (kind, markdown_ref, sidecar_ref)}`
  map from `checkpoints[*].artifacts` (mirroring the existing
  `check_artifacts` map at `:1550-1557`) and compare, so a submitted output
  cannot pair a real markdown with an unrelated committed JSON.

Add a negative fixture for the output/check sidecar swap and for a
declared-kind / sidecar `output_kind` mismatch; keep the existing
`test_completion_requires_contract_bound_output_and_check` positive path.

**Acceptance:** R1 blocks bounded acceptance of the "submit is contract-bound"
claim. It is local to `external_recording.py:submit` plus one new negative
fixture — no redesign, no schema-family change, no workpad-root change.

---

## Contract-scrutiny item (source-only; disposition required, not a reproduced crash)

### R2 — the three-field evidence sidecar records a declared label, not a check *result* (MEDIUM, disposition)

**Path:** `_check_sidecar_is_valid`
(`src/gigai/external_recording.py:1137-1148`); schema
`external-recording-invocation.schema.json:51` (`check_artifact_input.sidecar`
= `{evidence_kind, run_id, output_sha256}`); `submit` check loop
`:1565-1599`.

**Status of the worker's proposal:** The worker raised an unanswered
check-shape question and then delivered a three-field evidence sidecar.
Per the task charge this is an **implementation proposal requiring contract
scrutiny, not a previously approved refinement.** Scrutiny result below.

**What it enforces:** a check artifact exists, is an exact recorded checkpoint
sidecar of the shape `{evidence_kind, run_id, output_sha256}`, its
`evidence_kind` is in the sealed `completion_evidence_contract.fields`, its
`run_id` matches, and its `output_sha256` binds a recorded output markdown
digest. The check **Markdown body is never inspected.**

**What it does not enforce, versus §5:** §5 says checks "Use rule/version,
inputs/digests, findings, severity, and evidence references" and B1 says
`succeeded` is journaled "only when requirements pass." The three-field
sidecar carries inputs/digests (`output_sha256`) and a kind label
(`evidence_kind`) but **no findings, no severity, and no pass/fail result
field.** There is nothing that distinguishes "a check that passed" from "a
check that failed": an agent can record a check sidecar whose bound Markdown
says `FAILED — missing mandatory requirements` and `submit` still journals
`outcome == "succeeded"` (coordinator independently reproduced this
failure-text variant; it follows directly from the body never being read).

**Tension acknowledged (why this is a disposition, not an automatic defect):**
This is an **external recording** lane. The receipt enforces
`disclosure.execution == "unobserved"` and `actor_report == "declared"`; the
lane's stated job is *recording an externally-claimed check*, not
*executing/verifying* one. Demanding provider execution or a real evaluator
here would be out of scope and is explicitly disclaimed by the task. So a
label + digest + run binding is a *defensible* recording primitive. The
question the owner must settle is narrow: **does §5's "findings / severity" and
B1's "only when requirements pass" require the recorded check to carry at
least a structured pass/fail (and ideally severity) field, or is a declared
evidence label acceptable for a lane whose disclosure is always
`unobserved`/`declared`?**

**If the owner rules the label insufficient — minimum scoped remedy:** add one
required field to `check_artifact_input.sidecar` and
`_check_sidecar_is_valid`, e.g. `result` ∈ `{"pass", "fail"}` (optionally
`severity`), and in `submit` refuse completion unless every required
`evidence_kind` has a `result == "pass"` sidecar. Schema touch is confined to
`external-recording-invocation.schema.json` (`check_artifact_input`), no new
family. Add a negative fixture for a `fail` check sidecar on the completion
path.

**If the owner rules the label acceptable:** record that decision explicitly
in the SCOUT-04 closeout and in the lane implementation doc (the current
implementation doc's phrase "checkpointed output/check sidecars bind the Run,
sealed inputs, distinct evidence kind, and output digest" should be corrected
— check sidecars bind Run + evidence kind + output digest only, **not** sealed
inputs and **not** a result), and note the user-facing display constraint from
§5 / B4: a declared check is agent-reported, not verified.

---

## Prior-review items: re-verification

### F1 — `submit` completion bound to the sealed check contract — **PARTIALLY RESOLVED**
Check side is now bound: `submit` (`:1550-1599`) builds a
`check_artifacts` map from recorded checkpoint sidecars, requires each
submitted `check_refs` item to be an **exact** recorded checkpoint check
sidecar (`check_ref != checkpoint_item[1]` → refuse), requires
`_check_sidecar_is_valid` with `kind` taken from the *recorded* checkpoint
artifact (not caller-declared), requires `evidence_kind ∈
_required_check_kinds(...)` derived from the sealed
`check_contract` bytes via `_approved_fields(..., "completion_evidence_contract")`,
requires `output_sha256` to bind a submitted output markdown digest, and
finally requires `check_kinds == _required_check_kinds(...)`. The prior
"output's own Markdown/sidecar passed as the check" counterexample is now
refused (`tests/…::test_completion_requires_contract_bound_output_and_check`
`output_as_check` case → `external_output_invalid`; independently
re-reproduced). **Residual:** the *output* side is not re-validated at submit
(R1 above), and the check has no result semantics (R2). F1's intent is not
fully met until R1 is fixed.

### F2 — completion bound to the approved output contract, not an agent list — **RESOLVED**
`plan` (`:789-797`) parses the sealed `output_contract` bytes via
`_approved_fields(resolved, output_contract, "run_output_contract")` and
refuses unless `set(typed["output_kinds"]) == approved_outputs`. `checkpoint`
(`:1296`, `:1331`) and `submit` (`:1518`, `:1545`) derive required kinds from
the sealed contract via `_requested_output_kinds` →
`_approved_fields`. Probe: `plan(output_kinds=["made_up"])` →
`external_authority_mismatch` (matches
`test_completion_requires_contract_bound_output_and_check` first assertion);
PROBE B relabel at submit → `external_output_missing`. The arbitrary
`output_kinds` list is now cross-checked against approved authority at every
stage.

### F3 — second direct-CLI `plan`, distinct op key, no `selection_record` — **RESOLVED**
The constructed selection now sets `"created_at":
authority["graph_set"]["created_at"]` (`:697`) — deterministic, matching
`run_plan.py`. `derive_deterministic_id("graph_selection", …)` excludes
`created_at`; before journaling, `plan` reads any committed selection at the
deterministic path and reuses it if byte-equal (`:714-736`). PROBE D:
`plan(op_key="planA") … plan(op_key="planB")` under the same graph both return
the **same** `run_plan_id`, `created == False` on the second, no
`external_reconciliation_required`. F3's misleading remap is gone for this
path.

### F4 — `g45_reference` / `scout_record` inputs cannot be sealed into a Plan — **RESOLVED**
Plan schema `external-recording-plan.schema.json:21,31-35` now uses
`resolved_input` = `oneOf[g45_reference, g45_run_input, scout_record]` for
`inputs`; the invocation schema's `raw_input_ref`
(`external-recording-invocation.schema.json:42-46`) admits all three raw
families; `output_artifact_input.sidecar.selected_inputs` uses the same
`resolved_input`. `_input_ref` (`:335-378`) returns the resolved
`{family, reference_id|run_input_id, record_ref, snapshot_ref}` (for G45) or
`{family:"scout_record", record_id, revision_id, content}` shapes;
`_revalidate_input` (`:381-438`) re-resolves all three families at `start`,
including the `scout_record` chain, and explicitly refuses a `jsl_blob`
content family with `external_input_mismatch` ("native jsl_blob input is not
admitted"). PROBE E (`g45_reference` → plan → start) and PROBE F
(`scout_record` revision wrapper → plan → start) both succeed and the sealed
Plan passes its strict schema. The deliberately-refused native `jsl_blob`
path is a scoped deferral, not a defect.

### G1 — `external_successor_required` carries no predecessor / missing-input metadata — **RESOLVED**
`submit` (`:1482-1496`) now returns a structured `next_action` =
`{action: "create_successor_plan", predecessor: {kind:"checkpoint", run_id,
checkpoint_id}, missing_inputs: [{id, prompt, state:"missing"}],
selected_inputs: plan["inputs"]}`. PROBE G confirms the shape. `selected_inputs`
exposes only the resolved input metadata (record/snapshot refs), matching M1's
"typed next action exposes only selected input metadata."

### G2 — "resume the same Run if sealed inputs are unchanged" — **OPERATIONALLY SATISFIED**
`submit` refuses with `external_successor_required` **only when the last
checkpoint still has a `state == "missing"` question** (`:1469-1481`). PROBE I:
`waiting_input` checkpoint → `answered` checkpoint (same Run, inputs
structurally unchanged since the same sealed Plan) → output checkpoint →
`submit` → `succeeded`. So the same-Run continuation path exists and the
successor is only forced while a missing question is still outstanding.
`test_waiting_input_can_continue_unchanged_or_require_a_pinned_successor`
exercises both branches. **Note:** there is no *explicit* "sealed inputs
unchanged" comparison at the `answered` checkpoint — the guarantee rests on
the Run keeping its one sealed Plan, so inputs cannot change without a new
Plan. That is sound for this design; recommend a one-line comment at
`:1469` stating the invariant so a future editor does not add an
input-mutating checkpoint path.

### G3 — `_journaled` error remapping erases real causes — **RESOLVED**
`_journaled` (`:557-575`) now branches: `JournalConflictError` with
"immutable artifact already exists" → `external_operation_conflict`; other
`JournalConflictError` → `external_reconciliation_required`
(`next_action:"reconcile_journal"`); only a genuinely unexpected
`Exception` falls through to the "transition is not available" message
(`next_action:"install_external_transition_registry"`). The bare
`except Exception` no longer swallows `JournalConflictError`.

---

## Other charge items — status

- **Sealed input / kind / digest binding at submit (not merely checkpoint):**
  **NOT met for outputs** — see R1. Met for checks (kind + digest bound at
  submit; `evidence_kind` from the recorded checkpoint artifact, not the
  caller). Sealed-input binding for outputs happens only at `checkpoint`.
- **Evidence meaningful shape/result vs a label + digest:** see R2 — label +
  digest only, no result field, body never read.
- **Output / check ↔ approved-contract binding:** output *kind set* and check
  *evidence-kind set* are both derived from the sealed contract bytes at
  plan / checkpoint / submit via `_approved_fields`, which accepts only the
  simple `{schema_version:"1.0", kind, gig_id, fields:[…]}` form and refuses
  any richer contract language ("approved contract shape is not supported").
  Bounded-accept.
- **Exact G45 families:** `g45_reference`, `g45_run_input`, `scout_record`
  wrapping the two G45 families — exactly three, `jsl_blob` refused. Schema
  `oneOf` and `_input_ref` / `_revalidate_input` agree. Bounded-accept.
- **Stale / tampered authority:** `start` re-resolves authority at the write
  lock via `_authority` → `_resolve_authority` +
  `resolve_selected_graph_authority`, refuses version / goal-graph drift
  (`:943-950`); `_approved_ref` re-reads each descriptor ref with exact
  digest/size + per-component symlink checks. PROBE K (tamper the committed
  `output_contract.json` on disk, then `start`) is refused before the
  contract is even re-read, by the `_require_clean_authority` git gate in
  `index.read_index` ("authoritative workpad has uncommitted divergence").
  Bounded-accept.
- **Timestamp-stable selection:** PROBE J — two direct plans 1.2 s apart with
  different op keys yield the **same** `graph_selection_*` id and the **same**
  `run_plan_id`. `derive_deterministic_id` excludes `created_at`; selection
  `created_at` is pinned to `graph_set["created_at"]`. Plan identity
  (`identity` dict at `:798-814`) excludes wall-clock and invocation IDs.
  Bounded-accept.
- **Replay / CAS:** `plan` replays by `(operation_key, payload_sha256)` over
  existing `external-plan.json` (`:621-641`) and by deterministic path
  (`:817-832`); `start` / `checkpoint` / `submit` call `_replay` (matched on
  `operation` + `operation_key`, conflict on differing `payload_sha256` →
  `external_operation_conflict`) **before** `_read_run` / `_terminal`.
  Checkpoint parent CAS: `parent_checkpoint != checkpoints[-1].checkpoint_id`
  → `external_checkpoint_conflict` (`:1281-1285`); submit has the same parent
  check (`:1462-1467`). Uniqueness scope `(project_id, gig_id, operation,
  operation_key)` per B1. Existing tests
  (`test_replay_conflict_checkpoint_cas_terminal_and_agent_origin`) cover
  this; re-read confirms the ordering. Bounded-accept.
- **Interruption:** `_journaled` routes a non-"already exists"
  `JournalConflictError` to `external_reconciliation_required` /
  `reconcile_journal`; all mutations run inside `run_with_journal_writer`
  (single writer lock) and publish artifacts + journal commit atomically in
  one `writer.record`. No partial-publish-then-overwrite path is reachable
  from the lane. Bounded-accept (the central journal-recovery path is the
  integration owner's, not re-verified here).
- **Waiting-input successor semantics (M1):** `submit` emits the structured
  successor `next_action`; `plan` accepts a `predecessor` of
  `{kind:"checkpoint", run_id, checkpoint_id}` validated by
  `_validate_predecessor` against exact prior Gig evidence; the successor
  Plan is a new deterministic id (different `inputs` → different identity);
  old Run checkpoints are retained (PROBE-adjacent:
  `test_waiting_input_can_continue_unchanged_or_require_a_pinned_successor`
  asserts the old Run keeps `[missing, continued]`). The successor does **not**
  implicitly clone old artifacts — B1's "a successor is explicit" holds.
  Fresh-session UAT of the full M1 chain remains the coordinator's gate.
- **CLI origin discipline / mount independence:** `_invocation` forbids
  `direct_cli` origin from carrying anything but
  `{"kind":"operator","id":"local-user"}` and requires a bounded agent actor
  + non-empty session for `agent_invocation`; `external_cli._call` always
  builds the envelope from `_input(...)`; the schema's `allOf` binds
  `origin` → `actor` shape. No weaker path around `_schema`. Unchanged from
  the prior review's "what holds up"; re-confirmed. Bounded-accept.
- **Central CLI mounted / 55-family inventory / two changed hashes:**
  coordinator-owned, not this worker's blocker; not assessed here.

---

## Recommended disposition

**Not accepted.** One confirmed defect (R1) blocks the "submit is
contract-bound successful completion" claim: `submit` re-validates checks but
not the submitted output's sidecar, so an output/check sidecar swap journals
a terminal `succeeded` receipt (reproduced here as PROBE A / PROBE B2, and
independently by the coordinator). R1's remedy is lane-local
(`external_recording.py:submit` + one negative fixture), no schema-family
change.

R2 (three-field evidence sidecar records a declared label, not a check
result) is a **contract-disposition question for the SCOUT-04 owner**, not an
automatic defect given this is an `unobserved` / `declared` recording lane —
but it must be answered on the record, and if the answer is "a result field
is required" the fix is a single field added to `check_artifact_input.sidecar`
+ `_check_sidecar_is_valid` + `submit`.

F2, F3, F4, G1, G3 are resolved; G2 is operationally satisfied; F1 is
partially resolved (check side bound, output side is R1). The strict nested
schema work, replay/CAS/terminal gating, timestamp-stable selection,
stale/tampered-authority refusal, and the three-family G45 discriminator all
hold up under independent probing.

Native `jsl_blob` source integration is out of scope (active `native_records`
worker; deliberately refused in this lane) and is **not** counted against
SCOUT-04.

---

## Disposable probe artifact

A disposable probe module (`test_scout04_rereview_probe.py`, PROBE
A/B/B2/D/E/F/G/H/I/J/K) was written for this re-review, run with
`.venv/bin/pytest -q -s` against the worktree (11 tests, 10 pass; PROBE K
"fails" only because the clean-authority git gate refuses the tampered
workpad *before* `start` re-reads the contract — a stronger positive
result), then **removed from `tests/`** and kept only in this session's
scratchpad so the delivered suite is untouched. No source, test, schema, or
contract file under `src/` or `tests/` was modified or left behind.
