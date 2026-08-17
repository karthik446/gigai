# S27-EVAL — Evaluation Foundation Spike

- Status: Proposed for review; not activated
- Type: Research and contract-design spike; no runtime implementation
- Depends on: S16-EVAL methodology, S22-01 question-quality evidence, G20
  quality-gate evidence, G26 implementation evidence, and the G27 contract
  package
- Unblocks: G28 v0.1.5 readiness implementation

## Outcome

S27-EVAL turns GigAI's scattered test and evaluation work into one explicit,
versioned evaluation model. It must distinguish unit/contract tests,
integration tests, installed end-to-end scenarios, and behavioral evals so a
plumbing check cannot be reported as model or product quality.

## Decisions required

The spike must produce an accepted decision record covering:

1. the four verification tiers, their ownership, commands, CI cadence, and
   required evidence;
2. the evaluator/corpus manifest shape, including evaluator and corpus identity,
   case labels, source digests, expected/forbidden behavior, model/prompt
   identity, split, contamination record, and report artifact;
3. the distinction between `methodology_plumbing`, `behavior_scored`, and
   `candidate_judge_scored`;
4. reuse versus amendment decisions for S16-EVAL, S22-01, G20, and existing
   G15/G16 evaluator resources;
5. the first G26/G27 cases and measurable bars for question usefulness,
   redundancy, proposal completeness, citation quality, capability truthfulness,
   stable-definition/Run-input separation, and research-plan quality; and
6. how deliberate bad outputs fail the eval and how development, calibration,
   and final-held-out cases remain uncontaminated.

## Required boundary

An eval report must never claim behavioral quality when it only proves that a
runner, schema, or scoring pipeline executed. S16-EVAL remains authoritative
for Review Loop quality, and S22-01 remains authoritative for proposal-question
quality. This spike composes their results without merging their acceptance
bars by inference.

## Out of scope

- selecting a production judge model;
- changing G16 Review Loop semantics;
- changing G20 improvement authority;
- declaring G26/G27 quality from fixture self-comparison;
- live provider calls or network-dependent corpus generation; and
- implementing the final runner or CI workflow.

## Acceptance criteria

1. A decision record defines the four tiers and gives each a named command and
   evidence shape.
2. A versioned eval manifest/report contract is specified or an explicit
   no-new-schema reuse decision is accepted.
3. Existing S16-EVAL, S22-01, G18, and G20 claims are mapped to
   plumbing-versus-behavior evidence without overstating any result.
4. Initial G26/G27 eval cases, labels, metrics, negative cases, and bars are
   enumerated and mechanically checkable.
5. Contamination, judge identity, calibration, final-held-out reporting, and
   sanitized evidence rules are explicit.
6. G28 has an implementation checklist and a stop boundary derived from this
   spike.

## Stop boundary

Stop if the proposed framework cannot distinguish test execution from
behavioral scoring, cannot represent a negative case, or cannot report a final
held-out result independently of tuning data.
