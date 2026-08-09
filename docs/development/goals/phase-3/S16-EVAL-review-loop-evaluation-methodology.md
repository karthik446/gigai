# S16-EVAL — Review Loop Evaluation Methodology Spike

- Status: Active — approved for research evidence
- Type: Research and contract-design spike; not an implementation Goal
- Depends on: G15 (Review Bundle/evaluator substrate) and G16 (Review Loop
  orchestration)
- Unblocks: G18 and G19 as a hard evaluation gate; G22 may cite this spike only
  where it actually invokes or evaluates the Review Loop

## Purpose

G16 proved the Review Loop's orchestration: state machine, artifact parentage,
closure, cycle caps, replay, and offline guards. It did this with one seeded
defect class (`criterion_digest`) per domain profile, checked by a
deterministic fixture evaluator. That is evidence the skeleton is sound. It is
not evidence that a review pass — deterministic or model-backed — reliably
finds real problems, avoids inventing findings, or can be trusted enough to
gate an operator decision.

Roadmap section 5 ("Evaluation framework") defines the required concepts
(Case, Solver, Verifier, Trace, Adjudication, Report), evaluator order, and
minimum corpus. That section is design prose, not a scoped spike with an
accepted decision record. Nothing currently forces the corpus, quality metrics,
calibration method, and judge methodology to exist as committed, reviewed
evidence before G18 starts sending real content through real providers or
before G19 lets a verified loop's output touch a real target.

This spike closes that gap. It does not implement a new evaluator, provider
adapter, or product surface. It produces the labeled corpus, ground-truth and
adjudication protocol, quality assertions, calibration method, normative
acceptance bar, and mutation-coverage bar that later goals must meet and cite.
S22-01 separately owns proposal-question quality; S16-EVAL owns review-loop
quality and must not absorb the former merely because G22 is downstream.

## Contract and authority boundary

- The G15 evaluator substrate (Case, Solver, Verifier, Trace, Adjudication,
  Report) is the baseline to exercise, not to widen. If the corpus needs a
  field, evidence type, or verifier interface the substrate does not have,
  record the exact additive amendment and defer its application to an explicit
  contract review; this spike does not change a packaged schema.
- Corpus construction may use deterministic fixtures, recorded/replayed model
  outputs, and synthetic seeded defects. It must not require live provider
  credentials or network access to pass. A live judge-calibration run is
  optional operator-run evidence and is separate from fixtures that gate CI.
- Every case carries labeled expected findings, acceptable alternatives,
  forbidden findings, evidence-support labels, severity/confidence labels, and
  an abstention expectation. Seeded-defect presence alone is not ground truth.
- The spike must distinguish what deterministic checks can prove, what a
  calibrated model judge is trusted for, and what remains human adjudication.
  Aggregate agreement between judges is never proof of correctness.
- This spike does not select G18's production judge model, grant a provider
  authority over closure decisions, or change G16's loop state machine,
  feedback policy, or cycle-cap default.
- The accepted S16-EVAL decision record is the normative source for the
  calibration method and numeric acceptance bar. G18 may select a candidate
  judge and report pass/fail against that fixed bar; it may not redefine it.

## Scope

### In scope

- Turn roadmap section 5 into a committed, versioned corpus specification with
  the fixed critical-behavior matrix below. Each behavior requires exactly
  eight labeled coverage cases: four Development, two Calibration, and two
  Final Held-Out Acceptance. Coverage may overlap, but every row must have
  eight explicit coverage assignments.

  | Critical behavior | Minimum cases | Required split |
  |---|---:|---|
  | Tampered artifact rejection | 8 | 4 Development / 2 Calibration / 2 Final |
  | Missing-reference rejection | 8 | 4 Development / 2 Calibration / 2 Final |
  | Invented-citation rejection | 8 | 4 Development / 2 Calibration / 2 Final |
  | Missing-citation detection | 8 | 4 Development / 2 Calibration / 2 Final |
  | Citation supports the attached claim | 8 | 4 Development / 2 Calibration / 2 Final |
  | Duplicate finding merge with provenance | 8 | 4 Development / 2 Calibration / 2 Final |
  | Disagreement preservation and adjudication | 8 | 4 Development / 2 Calibration / 2 Final |
  | Unsupported clarification rejection | 8 | 4 Development / 2 Calibration / 2 Final |
  | Missing-context blocking clarification | 8 | 4 Development / 2 Calibration / 2 Final |
  | Partial-address detection at closure | 8 | 4 Development / 2 Calibration / 2 Final |
  | Deferred-feedback non-reapplication | 8 | 4 Development / 2 Calibration / 2 Final |
  | Cycle-exhaustion rejection | 8 | 4 Development / 2 Calibration / 2 Final |
  | Malformed loop-state rejection | 8 | 4 Development / 2 Calibration / 2 Final |
  | Addressed-artifact parent-mismatch rejection | 8 | 4 Development / 2 Calibration / 2 Final |
  | Valid no-finding versus over-reporting | 8 | 4 Development / 2 Calibration / 2 Final |
  | Severity and confidence correctness | 8 | 4 Development / 2 Calibration / 2 Final |
  | Abstention when evidence is insufficient | 8 | 4 Development / 2 Calibration / 2 Final |
  | Non-abstention when evidence is sufficient | 8 | 4 Development / 2 Calibration / 2 Final |

  The matrix is derived from all ten rejection classes named by G16 acceptance
  criterion 10: tampered artifacts, missing references, invented citations,
  unsupported clarification, unresolved disagreement, partial address,
  deferred feedback, cycle exhaustion, malformed loop state, and addressed-
  artifact parent mismatch. The remaining eight rows are the extended
  review-quality taxonomy: missing citations, citation support, duplicate
  findings, missing-context blocking, over-reporting, severity/confidence,
  insufficient-evidence abstention, and sufficient-evidence non-abstention.
  This is the exhaustive pre-alpha matrix. Adding, splitting, retiring, or
  redefining a behavior requires an accepted amendment to this spike's
  decision record, corpus manifest, assertion matrix, and quality bar; prior
  evidence remains valid only for the unchanged rows.

  Positive, negative, ambiguous, and incomplete-reference are orthogonal case
  category labels, not additional behavior rows. The case manifest must map
  every case to its category labels and behavior rows, and each category must
  appear in every corpus split. A case used to tune the taxonomy, prompt,
  threshold, rubric, judge, or harness cannot also certify that the final bar
  was met.

  The three sets have distinct roles:

  - Development is freely editable while building the taxonomy and harness.
  - Calibration may tune judge thresholds and mappings but may not change the
    normative acceptance bar.
  - Final Held-Out Acceptance is immutable during tuning and is the only set
    used to report that the bar was met.

- Extend G16's single seeded defect class (`criterion_digest`) into the full
  defect taxonomy required by the matrix, including invented and missing
  citations, duplicate findings, unresolved disagreement, partial address,
  cycle exhaustion, missing context, over-reporting, incorrect severity or
  confidence, and incorrect abstention.
- Define review-quality scoring beyond structural validity: expected-finding
  recall, precision and false-positive budget, citation-support correctness,
  severity/confidence calibration, and abstention sensitivity and specificity.
  Each case defines exact expected findings, acceptable alternatives, and
  forbidden findings so over-reporting cannot pass by finding every seeded
  defect.
- Define the human-ground-truth protocol: independent labeling procedure,
  evidence requirements for each label, disagreement handling, adjudicator
  authority, label versioning, and the rule for updating a case without
  contaminating Calibration or Final Held-Out Acceptance evidence.
- Define the calibrated-model-judge tier explicitly: metric definitions and a
  normative numeric acceptance vector for each severity tier, how thresholds
  are tuned only on Calibration, how performance is reported only on Final
  Held-Out Acceptance, how judge disagreement with deterministic checks is
  resolved, and how judge version identity distinguishes reruns.
- Define the mutation-coverage bar for the eval harness: verifier-rule
  removals and fixture corruptions that must flip a passing case to failing,
  the rejection classes they defend, and the minimum percentage of named
  classes that must have a mutation-killed test. The bar is 100% of the named
  S16-EVAL guard/fixture mutations; any surviving mutation blocks acceptance.
  Harness checks use a distinct
  `assertion_id` namespace; only checks that produce actual Review Findings
  map to runtime `finding_code` values.
- Define roadmap section 5's required assertions as executable checks with
  stable `assertion_id` values: seeded-defect recall, citation existence,
  citation support, duplicate merge without provenance loss, disagreement
  visibility, feedback-to-revision traceability, partial-address detection,
  rejected-feedback non-reapplication, blocking clarification, cycle-cap
  enforcement, replay stability, precision/false-positive limits,
  severity/confidence calibration, and abstention correctness. Map to runtime
  finding codes only where the assertion is literally about a Review Finding.
- Produce a scoring and reporting shape that keeps per-finding evidence
  primary. Aggregate scores may summarize results but never replace the
  per-case expected/emitted finding comparison and evidence trail.
- Recommend which cases are domain-neutral across G16's five profiles and
  which require a profile-specific seeded-defect library.

### Out of scope

- Selecting or wiring a production provider/judge model. G18 selects the
  candidate model and must prove it against this spike's fixed bar.
- A new schema resource, unless the corpus specification proves the G15
  substrate cannot represent a required case, judge version, or calibration
  record — in which case this spike stops for an amendment rather than
  inventing an undeclared field.
- Any change to G16's loop state machine, feedback decisions, or cycle-cap
  default, or to G19's target-mutation authority.
- A public benchmark, leaderboard, or cross-Gig comparative scoring system.
  This spike defines evaluation for one Gig's loop, not comparative provider
  ranking.
- Live, credentialed judge-calibration runs as a CI gate. They remain opt-in
  operator evidence and never make CI green.
- Proposal-question quality evaluation, which S22-01 owns separately.

## Required decision-record shape

This spike produces one checked-in decision record under
`docs/development/evidence/phase-3/S16-EVAL/`. The record must include:

1. The fixed critical-behavior matrix, exactly eight coverage cases per row,
   the Development/Calibration/Final Held-Out Acceptance split, required
   category coverage, and each case's labeled expected findings, acceptable
   alternatives, forbidden findings, seeded defects, and expected evidence
   properties. The contamination rule must be explicit.
2. The extended defect taxonomy and its mapping to G16's existing and any
   newly required deterministic finding codes.
3. The human-ground-truth and adjudication protocol, including independent
   labels, evidence requirements, dispute resolution, label versioning, and
   contamination handling.
4. The calibration method: metric definitions, the normative numeric
   acceptance vector by severity tier, how thresholds are tuned only on
   Calibration, how performance is reported only on Final Held-Out Acceptance,
   and how disagreement between the judge and deterministic checks is
   resolved, never silently.
5. The mutation-coverage bar: named guard/fixture mutations, rejection
   classes defended, each test's stable `assertion_id`, and target coverage
   percentage. Runtime `finding_code` mappings appear only for assertions
   about Review Findings.
6. The assertion-to-test matrix for every required assertion in roadmap
   section 5, with assertion IDs, expected outcomes, and applicable finding
   codes.
7. Contract impact: whether the G15 evaluator substrate is sufficient as-is,
   or the exact additive amendment required, naming affected artifacts,
   fields, resource-count change, and preserved hashes.
8. An explicit list of what this spike does not resolve, including judge-model
   selection deferred to G18 and proposal-question quality deferred to S22-01.

## Acceptance criteria

1. The decision record is checked in and accepted by review.
2. The corpus specification contains the fixed critical-behavior matrix with
   exactly eight coverage cases per row, split 4/2/2 across Development,
   Calibration, and Final Held-Out Acceptance, and includes every required
   category plus the extended defect taxonomy.
3. Every case has human-ground-truth labels for expected findings, acceptable
   alternatives, forbidden findings, citation support, severity/confidence,
   and abstention behavior. Label disagreement is resolved through the
   accepted adjudication protocol.
4. The quality report computes expected-finding recall, precision/false
   positives, citation-support correctness, severity/confidence calibration,
   and abstention sensitivity/specificity. Over-reporting and unjustified
   confidence fail the stated case or corpus-level limit.
5. Every required roadmap assertion maps to a named executable check with a
   stable `assertion_id`; only actual Review Finding assertions map to runtime
   `finding_code` values. No required behavior is represented only by an
   aggregate score.
6. The calibration method is falsifiable: it states a concrete normative
   numeric acceptance vector and a concrete procedure for tuning thresholds on
   Calibration and computing the final pass/fail result only on Final Held-Out
   Acceptance. G18 cannot change the bar.
7. The mutation-coverage bar names specific guard and fixture mutations,
   maps them to rejection classes, and states the minimum fraction of named
   classes that must be mutation-killed before later goals may build on the
   eval coverage.
8. Contract impact is explicit against the current G15 evaluator substrate.
   Any required amendment is additive and separately approved before it is
   applied.
9. All corpus fixtures and mutation tests run offline, without live provider
   credentials or network access, and are replayable from committed local
   bytes.
10. The terminal handoff names G18 and G19 as hard dependents that must cite
    this spike's assertions and calibration bar. It names G22 as conditional:
    G22 cites this spike only if its implementation invokes or evaluates the
    Review Loop; S22-01 remains authoritative for proposal-question quality.

## Verification and evidence

- Corpus fixtures with committed source bytes, ground-truth labels, seeded
  defects, expected findings, acceptable alternatives, forbidden findings, and
  expected evidence properties, structured like G16's corpus manifest.
- A requirement-to-assertion matrix mapping every roadmap section 5 assertion
  to a named check and stable `assertion_id`, with runtime finding-code
  mappings only where applicable.
- A quality report showing recall, precision/false positives, citation
  support, severity/confidence, abstention behavior, and per-finding evidence.
- A mutation report naming each guard/fixture mutation, the rejection class it
  defends, the catching test, and its `assertion_id`.
- A calibration procedure document containing the fixed numeric acceptance
  vector, Calibration tuning result, Final Held-Out Acceptance result, and
  judge/version identity. Any live result is stored separately and never makes
  CI green.
- Evidence lives under `docs/development/evidence/phase-3/S16-EVAL/` and
  includes a completion audit and terminal handoff naming the hard dependents.

## Stop boundary

Stop if the G15 evaluator substrate cannot represent a required case, defect
type, judge version, ground-truth label, or calibration record without
inventing an undeclared field — record the amendment and defer it rather than
working around it.

Stop if the spike cannot define a model-independent calibration method and
normative numeric acceptance vector. The method and bar must be fixed by this
spike without selecting a production judge model; G18 only runs its chosen
candidate against the fixed bar and reports pass/fail. Stop if a case has no
defensible ground-truth protocol, if the corpus split is contaminated, or if
review quality cannot be distinguished from structural harness validity.

Do not let this spike select a production judge model, change G16's loop
semantics, or grant any loop verdict authority over a real target. Do not mark
the spike accepted merely because a mutation report exists; every named
rejection class must have a mutation-killed test and every quality metric must
be computed from the labeled corpus.
