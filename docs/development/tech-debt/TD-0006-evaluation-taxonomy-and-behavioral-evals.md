# TD-0006 — Evaluation taxonomy and behavioral eval framework

- Status: Open; v0.1.5 release blocker
- Discovered during: G26/G27 sequencing review, 2026-08-16
- Affected surfaces: `tests/`, `research/s16_eval/`, `research/s22_01/`,
  `tools/run_g18_eval.py`, G20 quality replay, and CI workflows
- Owning lane: S27-EVAL -> G28 before G24 final UAT

## Observation

GigAI has useful evaluation ingredients but no single evaluation framework.
Unit/contract tests, integration tests, installed end-to-end scenarios,
mutation harnesses, S16-EVAL scoring, S22-01 question-quality fixtures, and
G20 quality replay are currently reported beside one another without a common
tier taxonomy, corpus manifest, evaluator registry, report shape, or release
bar.

Some current reports prove plumbing rather than behavior. In particular, an
offline G18 run can prove that the scoring pipeline executes while explicitly
not scoring a candidate judge. That distinction must be structural in reports,
not left to a reader to infer.

## Required resolution

Define four separate verification tiers:

1. **Unit/contract** — one module or serialized contract; deterministic; no
   process, network, browser, or external state.
2. **Integration** — real local boundaries such as filesystem, SQLite, Git,
   subprocess, or loopback HTTP; no live provider dependency by default.
3. **Installed end-to-end** — built wheel, CLI, browser/session boundary,
   disposable home/target, and packaged-resource verification.
4. **Behavioral eval** — labeled corpus cases, evaluator/judge identity,
   expected behavior, observed behavior, metric calculation, calibration, and
   final held-out acceptance. An eval is not a test merely because it runs under
   pytest.

The framework must provide a central evaluator/corpus manifest containing:

- evaluator ID and version;
- corpus ID/version and development/calibration/final-held-out split;
- case IDs, source digests, labels, expected behavior, and forbidden behavior;
- model/provider/prompt or deterministic-run identity;
- observed output and normalized score;
- metric definitions and normative acceptance bar;
- `methodology_plumbing`, `behavior_scored`, and `candidate_judge_scored`
  status fields; and
- sanitized report artifact and contamination record.

The first product evals must cover G26/G27 question usefulness and redundancy,
proposal completeness and citation quality, capability-disclosure truthfulness,
stable-definition versus Run-input classification, and bounded research-plan
quality. S16-EVAL remains authoritative for Review Loop quality; S22-01 remains
authoritative for proposal-question quality. The new framework composes those
authorities rather than silently merging their bars.

## Exit evidence

- Test commands and CI jobs identify their tier explicitly.
- Eval manifests and reports use one versioned shape.
- G18 plumbing-only runs cannot report candidate behavior as scored.
- G26/G27 eval corpora have labeled cases, fixed splits, contamination rules,
  metrics, and a falsifiable acceptance bar.
- At least one deliberate bad model output fails each first eval suite.
- Fast PR checks, scheduled exhaustive tests, installed E2E, and behavioral
  evals are separate commands with documented runtime expectations.
