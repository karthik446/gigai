# SCOUT-04 — R1 / R2 final review (source-only, read-only)

**Reviewer:** Fresh independent Claude session, dispatched worker. Separate
from the coordinator and from the user-taken-over native reviewer.
**Scope:** Read-only source review of the post-R1/R2-correction SCOUT-04
external recording lane, plus four small disposable adversarial probes run
against the worktree and then removed. No source / test / schema / baseline
edits, no full-suite run, no providers, no real private Gigs, no commits.
`native_records` / `jsl_blob` integration and the SCOUT-08 domain evaluator
are explicitly out of scope and not counted against SCOUT-04.
**Sole writable artifact:** this file.

**Files inspected:** `src/gigai/external_recording.py`
(`_safe_ref`, `_ref`, `_invocation`, `_approved_fields`, `_journaled`,
`_replay`, `_terminal`, `_read_run`, `_checkpoints`, `_plan_for_run`,
`_requested_output_kinds`, `_required_check_kinds`, `_output_sidecar_is_valid`,
`_check_sidecar_is_valid`, `checkpoint` / `_progress`, `submit`, `inspect`);
`src/gigai/schemas/external-recording-invocation.schema.json` (all);
the other four `external-recording-*.schema.json` families (unchanged);
`tests/test_scout04_external_recording.py::test_completion_requires_contract_bound_output_and_check`
and the surrounding focused suite; `tools/verify_installed_schemas.py`;
`src/gigai/schemas/SHA256SUMS`. Contract sources:
`SCOUT-00-contract-amendments.md` §5 / §9 / B1 / B2 / M1;
`parallel-delivery-plan.md` "R2 coordinator implementation disposition"
(lines 260–293); `SCOUT-04-corrections-rereview.md` (R1 / R2 statements);
current `SCOUT-04-external-lane-implementation.md`.

---

## Verdict: **Bounded accept.**

R1 is **resolved**: `submit` now re-reads and re-validates the submitted
output's sidecar (declared kind, Run, Markdown digest, sealed inputs) **and**
requires the `(kind, markdown_ref, sidecar_ref)` triple to be an exact
recorded checkpoint output tuple, with per-submit deduplication. The
output/check sidecar swap that produced a terminal `succeeded` receipt in the
prior re-review is now refused with `external_output_invalid`
(reproduced-refused here as PROBE *crossed-output-pair*, and covered by the
delivered `completion-swapped-output` / `completion-output-as-check` /
`completion-changed-kind` / `completion-duplicate-output` cases).

R2 is **resolved to the coordinator's frozen disposition**: the nested
check-sidecar shape now carries a required `result ∈ {pass, fail}`; checkpoints
retain either result; `submit` refuses any selected `fail` check and requires
a `result == "pass"` sidecar for every sealed evidence kind; a later
explicitly selected corrected `pass` succeeds without rewriting the immutable
failed checkpoint; mixed pass/fail selection is refused rather than silently
discarding the failure; missing / unknown result is rejected at both schema
and checkpoint API before it can ever reach `submit`. `execution=unobserved`
/ `actor_report=declared` remain mandatory (submit guard on `execution`;
receipt schema `disclosure` const enforces both members before journaling).
No provider execution, no Markdown-truth parsing, no new schema family, no
baseline rewrite, no evidence migration was introduced.

No F2 / F3 / F4 / G1 / G3 regression found; G2 remains operationally
satisfied. Central schema inventory is refreshed and internally consistent —
not a source blocker.

Remaining items are two **source-distinct, non-blocking observations** (O1,
O2) with narrow, optional remedies. Neither reproduces a defect; neither
blocks acceptance of the SCOUT-04 external lane at its stated scope. Final
acceptance still rests on the coordinator's combined focused-suite run,
central digest/count verification, managed/external reader discrimination,
and fresh-session M1 UAT — none of which are this worker's gate.

---

## R1 — output-sidecar re-validation and exact tuple binding at submit — RESOLVED

**Code path:** `external_recording.py:submit` output loop `:1516-1604`.

1. `recorded_outputs` is built from every checkpoint artifact as
   `{markdown.path: [(kind, dict(markdown_ref), dict(sidecar_ref)), …]}`
   (`:1518-1537`).
2. For each submitted `output_refs` item: `_safe_ref` re-reads `markdown` and
   `sidecar` against the snapshot (path safety, digest + size match)
   (`:1550-1555`); both paths must start with `runs/<run_id>/artifacts/`
   (`:1556-1563`); the sidecar bytes are parsed as JSON (`:1564-1569`).
3. The sidecar must satisfy `_output_sidecar_is_valid(sidecar_value,
   run_id=run_id, inputs=plan.get("inputs"), kind=item["kind"],
   markdown=markdown_data)` — this re-binds, at submit, the **sealed
   `plan["inputs"]`**, the Run id, the caller-declared `output_kind`, and the
   document digest of the re-read Markdown bytes (`:1576-1584`, helper
   `:1120-1134`).
4. The `(kind, markdown_ref, sidecar_ref)` triple must equal the **single**
   recorded checkpoint output tuple for that Markdown path
   (`len(recorded) != 1 or recorded[0] != (kind, markdown_ref, sidecar_ref)`,
   `:1585-1586`), and must not repeat within the submit
   (`output_tuple in output_tuples`, `:1587`).
5. The validated output kind-set must equal the sealed
   `_requested_output_kinds` (`:1597-1604`).

`_safe_ref` returns `dict(value)` (the caller-supplied ref, post-validation)
and `_ref` (used by `checkpoint`) always emits exactly
`{path, content_sha256, media_type, size_bytes}`. A caller ref that adds the
optional `canonical_sha256` key, or omits `media_type`, no longer equals the
recorded 4-key dict and is refused — the comparison is genuinely strict, not
a bypass surface.

**Independent probes (disposable, removed after run):**

| Probe | Construction | Result |
|---|---|---|
| `crossed-output-pair` | two `report` checkpoints; submit pairs checkpoint-A Markdown with checkpoint-B's *genuine* output sidecar | `external_output_invalid` (tuple mismatch) |
| `same-key-diff-payload replay` | submit key `K` succeeds; second submit, same `operation_key`, `parent_checkpoint` flipped to `null` | `external_operation_conflict` (replay CAS before `_read_run`/`_terminal`) |
| `failed-checkpoint-retention` | checkpoint with `result:"fail"`; later corrected `pass` checkpoint; corrected `submit` | `succeeded`; the `fail` sidecar file on disk is **byte-identical** before/after; receipt `checks[0].path` is the corrected `pass` sidecar only |
| `selected-fail-after-pass-available` | both `fail` and `pass` sidecars committed; submit selects the `fail` sidecar for the required kind | `external_output_invalid` |

The delivered `test_completion_requires_contract_bound_output_and_check`
(re-run here, 1 passed in 12.7s; full focused file 15 passed in 63.3s)
additionally exercises `made-up-output`, `completion-no-check`,
`completion-output-as-check`, `completion-swapped-output`,
`completion-changed-kind`, `completion-duplicate-output`,
`completion-failed-check`, `completion-mixed-checks`, corrected-pass success,
and identical-payload same-key replay.

**Conclusion:** the prior R1 counterexample is closed. Sealed-input / Run /
declared-kind / document-digest binding for outputs now happens at `submit`,
not merely at `checkpoint`, and the `(markdown, sidecar)` pairing cannot be
crossed, swapped, relabeled, or duplicated.

---

## R2 — declared check `result` gate — RESOLVED to the frozen coordinator disposition

**Schema:** `external-recording-invocation.schema.json:51` —
`check_artifact_input.sidecar` now
`required: [evidence_kind, run_id, output_sha256, result]`,
`additionalProperties: false`, `result: {"enum": ["pass", "fail"]}`.
On-disk SHA-256 `0608170d…7316` matches the implementation-doc handoff hash,
`tools/verify_installed_schemas.py:13`, and `SHA256SUMS:51`.

**Helper:** `_check_sidecar_is_valid` (`:1137-1149`) requires
`set(sidecar) == {"evidence_kind", "run_id", "output_sha256", "result"}`,
`result in {"pass", "fail"}`, `evidence_kind == kind`, `run_id == run_id`,
and a `sha256:`-prefixed 71-char `output_sha256`.

**Checkpoint (`_progress`, `:1334-1346`):** accepts a check artifact whose
sidecar passes `_check_sidecar_is_valid` and whose `kind` is in the sealed
`_required_check_kinds` — with **either** `pass` or `fail`, so a failing
declaration is retained immutably.

**Submit check loop (`:1620-1659`):**
- each `check_refs` item must be an exact recorded checkpoint check sidecar
  (`check_artifacts` map keyed by sidecar path; `check_ref != checkpoint_item[1]`
  → refuse) (`:1624-1628`);
- `_check_sidecar_is_valid` with `kind` taken from the *recorded* checkpoint
  artifact, not the caller (`:1635-1640`);
- `output_sha256` must bind a **submitted validated output's** Markdown digest
  (`output_digests` from `valid_outputs`, `:1615-1619`, `:1641`) — tighter
  than the checkpoint check, which binds any recorded output;
- `kind` must be in `_required_check_kinds` (`:1642`);
- **`check_value.get("result") != "pass"` → `external_output_invalid`
  ("declared required check result is not pass")** (`:1648-1652`);
- final `check_kinds != _required_check_kinds` → `external_output_missing`
  (`:1655-1659`), so every sealed evidence kind needs a `pass` sidecar.

**Disposition-item coverage:**

| R2 coordinator disposition clause | Enforcement | Evidence |
|---|---|---|
| declared label alone insufficient; required `result: pass\|fail` | schema + `_check_sidecar_is_valid` | schema `:51`; helper `:1142-1145` |
| checkpoints retain either result | no result filter in `_progress` | `:1334-1346` |
| submit refuses a selected failing check | `result != "pass"` → error | `:1648-1652`; test `completion-failed-check`; PROBE `selected-fail-after-pass-available` |
| passing declaration required for every approved evidence kind | `check_kinds != _required_check_kinds` | `:1655-1659` |
| corrected `pass` may follow an older immutable failure | distinct checkpoint paths; failed sidecar never mutated | test `completion-corrected-check` → `completion-submit`; PROBE `failed-checkpoint-retention` (byte-identical) |
| mixed supplied pass/fail must not silently discard the failure | fail item hits `result != "pass"` first | `:1648-1652`; test `completion-mixed-checks` |
| missing / unknown result invalid | schema `additionalProperties:false` + enum; checkpoint API rejects before submit | test lines 693–730 (`pop("result")`, `result="unknown"` → `external_invocation_invalid`) |
| `execution=unobserved` / `actor_report=declared` mandatory | submit guards `execution`; receipt schema `disclosure` const enforces both members before `_journaled` | `:1660-1668`; receipt schema `$defs.disclosure` |
| no evaluator / Markdown-truth parsing / new family / baseline rewrite / migration | check Markdown body is never read; only the invocation schema changed; no historical rewrite path | schema diff scope; `_progress` / `submit` |

**Conclusion:** R2 is satisfied exactly as the frozen disposition specifies.
The lane records an external actor's *declared* `pass`/`fail`; it does not
evaluate, execute, or parse the check prose, consistent with §4 / §5 / B4 and
the `unobserved` / `declared` disclosure.

---

## Prior-review items — regression re-check (no regression found)

- **F2 (output kind-set bound to the sealed approved contract):** `plan`
  `:789-797` (`set(typed["output_kinds"]) == approved_outputs`), `checkpoint`
  (`requested_outputs`), `submit` (`:1540`, `:1597-1604`) all derive required
  kinds from the sealed `output_contract` bytes via `_approved_fields` with
  `expected_kind="run_output_contract"`. Test `made-up-output` →
  `external_authority_mismatch`; `completion-changed-kind` →
  `external_output_invalid`. **Intact.**
- **F3 (deterministic repeat selection, cause-specific journal errors):**
  `plan` pins `created_at = graph_set["created_at"]`, reuses a byte-equal
  committed selection at the deterministic path; `_journaled` (`:527-575`)
  still branches `JournalConflictError` "immutable artifact already exists" →
  `external_operation_conflict`, other conflicts →
  `external_reconciliation_required`, only genuine `Exception` →
  transition-registry message. **Intact.**
- **F4 (strict resolved G45 families at sealing / checkpoint / start):** plan
  schema `resolved_input = oneOf[g45_reference, g45_run_input, scout_record]`;
  invocation `raw_input_ref` admits the three raw shapes; `scout_record.content`
  is `oneOf[g45_reference, g45_run_input]`. `output_artifact_input.sidecar.
  selected_inputs` uses the same `resolved_input`. Native `jsl_blob` still
  refused. The `result` addition to `check_artifact_input` does not touch any
  input `$def`. **Intact.**
- **G1 (structured `external_successor_required` next_action):** `submit`
  `:1483-1497` returns `{action:"create_successor_plan", predecessor:
  {kind:"checkpoint", run_id, checkpoint_id}, missing_inputs:[…],
  selected_inputs: plan["inputs"]}`. **Intact.**
- **G2 (resume same Run while sealed inputs unchanged):** `submit` forces a
  successor **only** while the last checkpoint still carries a
  `state == "missing"` question (`:1470-1497`); otherwise the same-Run
  continuation path proceeds to `succeeded`. Guarantee still rests on the Run
  keeping its one sealed Plan (inputs cannot change without a new Plan). No
  input-mutating checkpoint path exists. **Operationally satisfied**; the
  one-line invariant comment recommended in the prior re-review is still not
  present (see O2).
- **G3 (`_journaled` error remapping):** unchanged; bare `except Exception`
  still does not swallow `JournalConflictError`. **Intact.**
- **Replay / CAS / terminal ordering:** `submit` calls `_replay` (matched on
  `operation` + `operation_key`; differing `payload_sha256` →
  `external_operation_conflict`) **before** `_read_run` / `_terminal`
  (`:1454-1461`); parent-checkpoint CAS at `:1462-1468`. PROBE
  `same-key-diff-payload replay` confirms conflict is raised before the
  terminal check. **Intact.**

---

## Non-blocking observations (source-distinct; optional narrow remedies)

### O1 — `submit` does not itself assert `actor_report == "declared"`; it relies on the receipt schema

**Source:** `submit` `:1660-1668` checks `set(disclosure) ==
{"execution", "actor_report"}` and `disclosure.get("execution") !=
"unobserved"`, but never checks the value of `actor_report`. A submit
envelope with `actor_report: "observed"` (or any string) passes this guard.
It is then caught one step later: `_schema("external-recording-receipt.
schema.json", receipt)` (`:1684`) fails because the receipt schema's
`$defs.disclosure` pins `actor_report` to the const `"declared"`, so no
receipt is journaled and the operation raises `external_invocation_invalid`.

**Why non-blocking:** the contract-required outcome (no terminal `succeeded`
receipt with a non-`declared` actor report) does hold, via the receipt
schema. This is a defense-in-depth gap, not a reachable defect — the
malformed submit is refused, only with a less specific error code and one
layer deeper than the sibling `execution` check.

**Narrow remedy (optional):** extend the `:1661-1665` condition to also
require `disclosure.get("actor_report") == "declared"`, mirroring the
`execution` check, so the refusal is `external_output_invalid`
("external disclosure must remain unobserved/declared") at the same site.
Also worth a one-line negative assertion in the delivered completion test.
Source vs probe: source-only, not reproduced as a wrong success.

### O2 — the sealed-inputs-unchanged invariant at the `answered`-checkpoint continuation is still implicit

**Source:** `submit` `:1470` — the same-Run continuation is permitted purely
by the absence of a `state == "missing"` question; there is no explicit
"sealed inputs unchanged since the sealed Plan" comparison, and no comment
stating why one is unnecessary (the Run holds exactly one sealed Plan, so
inputs cannot drift without a new Plan and Run).

**Why non-blocking:** the design is sound as-is; `checkpoint` has no
input-mutating path, and `_plan_for_run` resolves the single sealed Plan.
This repeats the prior re-review's G2 note verbatim — it was recorded, not
actioned.

**Narrow remedy (optional):** a one-line comment at `:1470` stating the
invariant ("the Run carries exactly one sealed Plan; inputs cannot change
without a new Plan, so no per-checkpoint input diff is required here") so a
future editor does not add an input-mutating checkpoint path. No code change.
Source vs probe: source-only, documentation hardening.

---

## Charge items — explicit status

| Charge item | Status |
|---|---|
| final submit exact output tuple comparison | **Met** — `(kind, markdown_ref, sidecar_ref)` triple vs single recorded checkpoint tuple, plus per-submit dedup (`:1585-1593`) |
| reread sidecar validation for sealed inputs / Run / kind / digest | **Met** — `_output_sidecar_is_valid` re-binds `plan["inputs"]`, `run_id`, declared `output_kind`, re-read Markdown digest at submit (`:1576-1584`) |
| swapped / crossed / duplicated pairs | **Met** — `completion-swapped-output`, `completion-output-as-check`, `completion-duplicate-output`; PROBE `crossed-output-pair` all refused `external_output_invalid` |
| declared results strict `pass`\|`fail` | **Met** — schema enum + `_check_sidecar_is_valid` + `additionalProperties:false` |
| failure checkpoint retention | **Met** — PROBE `failed-checkpoint-retention`: `fail` sidecar byte-identical after corrected success |
| mixed selected failure refusal | **Met** — `completion-mixed-checks` → `external_output_invalid` (`:1648-1652`) |
| corrected selected pass succeeds without rewriting failed history | **Met** — `completion-corrected-check` → `completion-submit` succeeds; receipt cites only the corrected `pass` |
| missing / unknown result rejection | **Met** — rejected at schema + checkpoint API (test 693–730); cannot reach submit |
| same-key replay before terminal / CAS | **Met** — `_replay` conflict raised before `_read_run` / `_terminal`; PROBE `same-key-diff-payload replay` → `external_operation_conflict` |
| F2 / F3 / F4 / G1 / G3 regressions | **None found** |
| G2 operationally satisfied | **Confirmed** (see O2 for the still-open one-line comment note) |
| central inventory / latest invocation digest | **Refreshed & consistent** — all five `external-recording-*` SHAs match `verify_installed_schemas.py` and `SHA256SUMS`; 55-family count is coordinator-owned and not a source blocker |
| implementation-doc accuracy (prior R2 correction ask) | **Corrected** — current doc scopes "sealed inputs" to the output sidecar only and describes the check `result` gate accurately; the prior misleading phrase is gone |
| native `jsl_blob` / SCOUT-08 domain evaluator | **Out of scope** — deliberately deferred; not counted against SCOUT-04 |

---

## Recommended disposition

**Bounded accept the SCOUT-04 external recording lane at its stated scope.**
R1 and R2 are both resolved — R1 by real output-sidecar re-validation plus
exact-tuple binding at `submit`, R2 by the required `result ∈ {pass, fail}`
field enforced through schema, checkpoint retention, and a submit-time
`pass`-only completion gate that matches the coordinator's frozen
disposition. F2 / F3 / F4 / G1 / G3 show no regression; G2 remains
operationally satisfied. Focused suite: 15 passed (63.3s); four disposable
adversarial probes all refused the intended attack and were removed; Ruff
clean; `compileall` and `git diff --check` clean.

O1 (submit relies on the receipt schema, not its own guard, to reject a
non-`declared` `actor_report`) and O2 (implicit sealed-inputs-unchanged
invariant at the `answered`-checkpoint path) are **source-only, non-blocking**
and each has a one-line optional remedy. Neither is a reproduced defect.

Final SCOUT-04 acceptance still requires the coordinator's combined
focused-suite run, central digest/count verification, managed/external reader
discrimination, and fresh-session M1 UAT — outside this worker's gate.

---

## Disposable probe artifact

A disposable probe module (`probe_scout04_final.py`, four tests:
`crossed-output-pair`, `same-key-diff-payload replay`,
`failed-checkpoint-retention`, `selected-fail-after-pass-available`) was
written for this review, run against the worktree
(`PYTHONPATH=<repo> .venv/bin/pytest -q -s`, 4 passed in 21.5s), then
**removed** — kept only in this session's scratchpad. No file under `src/` or
`tests/` was modified or left behind; `git status` shows no probe artifact.
