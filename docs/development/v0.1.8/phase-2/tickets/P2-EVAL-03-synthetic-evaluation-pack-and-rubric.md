# P2-EVAL-03 — Prepare the synthetic evaluation pack and rubric

**Status:** Preparation complete; execution not authorized. Output in [`../evidence/P2-EVAL-03-pack-and-rubric.md`](../evidence/P2-EVAL-03-pack-and-rubric.md). Terra-reviewed, with the sole manifest finding corrected and closed: Terra `ctx_7ff479b90fc9` → Luna fix `ctx_461e730dc554` → Terra pass `ctx_57133c5bc2bb`. **Owner role:** Luna/max evaluation-prep
writer; root owns authorization and later integration. **Review:** Terra
independent review after the pack and rubric are complete.

## Jira-style ticket

**User problem:** S08 defines a fair comparison method, but no Phase 2 pack is
yet frozen for the Scout-shaped decision tasks. A model-produced label is not
human gold, `13/13` deterministic cases are not model-quality acceptance, and
an adapter or synthetic transport result does not prove the actual caller or
installed route.

**Intended behavior/outcome:** Prepare a bounded, versioned, synthetic pack,
shared decision envelope, deterministic-first grader, setup-parity record, and
human-adjudication queue that a later authorized comparison can execute without
changing case bytes or quietly favoring one backend. Separate objective cases
that can complete offline from judgment cases awaiting a named human
adjudicator, and define explicit quality/failure disposition criteria.

**Scope:** Documentation and offline pack/rubric design only; synthetic posting,
evidence, preference/constraint and failure fixtures; schema/field-level
acceptance plan; no model execution.

**Non-goals:** No Qwen/Luna/GPT/Claude/model call, Ollama daemon or download,
provider/paid API/search, personal resume/preferences, gold-label declaration
without human adjudication, production evaluator, runtime schema change, or
release-quality conclusion.

**Tasks:**

1. Reuse S08's `gigai.typed_decision.v1` envelope and the existing
   `runtime-evaluation-pack`/R6 comparison evidence where compatible; record
   exact reuse and any proposed amendment rather than inventing a parallel
   contract.
2. Freeze a small pack target (roughly 15–30 cases) with stable pack/case IDs,
   source bytes, task criteria, setup wrappers, held-out cases, known-good and
   known-bad variants, and no real user data. Include extraction,
   requirement-status, evidence sufficiency, focused-question, abstention and
   malformed-output cases.
3. Split objective and judgment cases before labels are drafted. Compute
   objective gold mechanically; preserve model/frontier drafts as drafts only;
   create a human adjudication table with identity, date, blind-to-backend mode,
   disagreement log, multi-acceptable labels, and an explicit not-ready state.
4. Define deterministic grader stages: envelope/schema validity, expected
   outcome branch, evidence-ID/forbidden-claim integrity, enum/gold match, and
   narrowly disclosed LLM-judge fallback only when mechanical checks cannot
   resolve a case. Validate any judge against human gold.
5. Define per-case/per-setup receipts for wrapper text, model/digest,
   attempts/retries, malformed output, timeout/cancellation, token usage,
   cold/warm timing, cost, latency, quality, and terminal failure class. Keep
   quality, cost and latency separate.
6. Mark which pack preparation completes offline and which steps require human
   adjudication, operator consent, installed proof, or live/provider/model
   authorization. Reconcile case terms with P2-AUD-02 before P2-FREEZE-04.

**Acceptance:**

- The pack is reviewable without model/provider/network access and contains
  only synthetic data; source bytes and case IDs are immutable once frozen.
- Every judgment case is visibly pending human adjudication until a real human
  role/identity, date, decision, and disagreements are recorded; a model draft
  never counts as gold. Ambiguous cases may have multiple acceptable labels.
- The grader has known-good/known-bad self-checks and reports envelope outcome,
  evidence integrity, label match, abstention correctness, judge use, quality,
  cost, latency, retries and terminal failures separately.
- Setup rows distinguish local adapter, CLI/harness, hosted, and any future
  provider route; wrappers/tools/retries are logged rather than pretending
  setup parity. No hosted fallback is accepted for private inputs.
- The report states “preparation complete, execution not authorized” and does
  not turn S08 research, R6 injected proof, or S11 test passes into model,
  caller, installed, live, or release acceptance.

**Required evidence:** Planned pack and rubric report at
`docs/development/v0.1.8/phase-2/evidence/P2-EVAL-03-pack-and-rubric.md`,
synthetic pack under
`docs/development/v0.1.8/phase-2/evidence/synthetic-evaluation/`, and a
machine-readable manifest at
`docs/development/v0.1.8/phase-2/evidence/P2-EVAL-03-manifest.json`.
Human adjudication records must be separate from model drafts and may not be
backfilled by this story.

**Dependencies:** Can start independently of P2-AUD-01 and P2-AUD-02. It
should consume P2-AUD-02's operation vocabulary and source-boundary findings
before final freeze, and P2-FREEZE-04 consumes its disposition table. S10 route
claims are inputs to setup-parity rows, not permission to execute them.

## Granular pack and rubric specification

### Case shape and provenance

Each case should carry at least:

```text
pack_version, case_id, case_kind(objective|judgment), synthetic state,
question/criteria, expected_envelope_outcome, gold.acceptable_labels,
gold.rationale, gold.provenance, evidence_requirements,
known_bad_variant_of, held_out, notes
```

Use the S08 distinctions exactly:

- Objective cases cover schema validity, enum membership, evidence-ID existence,
  literal/source-field checks, and known-good/known-bad envelopes. Their gold is
  script-derived with no model.
- Judgment cases cover support versus unclear/unsupported, evidence sufficiency,
  constraint mismatch, or focused-question usefulness. A frontier draft may
  reduce human effort, but the draft author is never the gold authority.
- A human adjudicator reads the case source directly, may override the draft,
  records disagreement and can mark multiple acceptable labels. Held-out status
  is independent from expected outcome and must not be used to skip integrity
  checks.

Reuse the shared states `typed_decision`, `abstain`, `uncertain`, and
`refused_or_failed`; a transport/schema/timeout failure must not publish a
decision, and an abstention may still be checked for invented evidence IDs.

### Grader and evidence rules

Implementability is a later packet concern; this story specifies behavior:

1. Validate the shared envelope and case identity.
2. Compare actual versus expected envelope outcome, classifying over-confident
   and under-confident mismatches separately.
3. Check evidence references and forbidden claims for every outcome, including
   abstention rationale.
4. Check enum and acceptable-label membership only for a valid expected
   `typed_decision`.
5. Use an LLM judge only for irreducibly semantic text; name the judge and
   validate its verdicts against human gold, with fenced/escaped case content
   and final structured-field parsing.

Known-bad cases must fail for the intended reason. A passing schema check is
not a semantic quality score, and agreement with a judge is not gold unless
that judge has itself been checked against human adjudication.

### Setup and completion boundaries

Record a row for each future setup, using S08's fixed-varying split:

| Held constant | Per-setup values that must be logged |
| --- | --- |
| Pack/case IDs and source bytes, criteria, envelope/schema, grader, thresholds, abstention rules | Wrapper/system text, tools/context, retry policy, model ID/digest, runtime/CLI version, endpoint/locality, permissions, token usage/cost, cold/warm timing, cancellation and observed failure |

Offline preparation includes case authoring, objective grader vectors,
manifest validation, wrapper templates, receipt schema and known-bad grader
self-checks. Human gold, installed execution, local-model execution, hosted
comparison, paid calls, and any privacy/route proof remain separately gated.

### Failure and quality disposition

The later comparison report must be able to choose one disposition per failure:
fix/re-evaluate, defer assessment, or reassess release scope with the user.
Do not hide model failure behind a retry, hosted fallback, omitted case,
single blended score, or a skipped human label. Report coverage/abstention and
quality separately from cost and latency; local compute is not a fabricated
zero-dollar cost, and a timeout/cancellation is not a low-quality label.
