# S27-EVAL — Evaluation Foundation Decision Record

- Status: Proposed for acceptance; no runtime implementation
- Spike: [S27-EVAL](../../../../goals/phase-5/S27-EVAL-evaluation-foundation.md)
- Recorded: 2026-08-16
- Depends on: accepted S16-EVAL and S22-01 evidence, G20 quality-gate
  evidence, G26 implementation evidence, and the accepted G27 contract package
- Unblocks: G28 v0.1.5 readiness implementation

## Decision summary

GigAI will use four explicitly separated verification tiers:

1. **Unit and contract tests** prove local functions, schemas, validators,
   state transitions, mutation guards, and canonical vectors.
2. **Integration tests** prove multiple local components together, including
   journal, SQLite, filesystem, loopback HTTP, and model-boundary fixtures.
3. **Installed end-to-end scenarios** prove behavior through a freshly built
   wheel and installed CLI, including setup, init, create, recovery, and
   installed resource verification.
4. **Behavioral evaluations** score a Solver's output against a versioned Case
   corpus using a Verifier, with labeled expected behavior, contamination
   controls, and a final held-out report.

The tiers are evidence strength and execution environment. They do not create
a second object vocabulary. The roadmap's Case, Solver, Verifier, Trace,
Adjudication, and Report remain the canonical evaluation objects.

No production judge model is selected by this spike. No final held-out
behavioral bar is claimed as met here.

## Current evidence disposition

| Existing artifact | What it proves | What it does not prove | Authority retained |
| --- | --- | --- | --- |
| S16-EVAL corpus and methodology | Review Loop metric plumbing, extended finding-quality metrics, mutation coverage, and fixed split structure | A production judge's behavioral accuracy | S16-EVAL remains authoritative for Review Loop quality |
| S22-01 evaluation corpus | Deterministic proposal-interview outcome fixtures and approved-or-blocked protocol behavior | That model-selected questions are useful, nonredundant, or domain-complete | S22-01 remains authoritative for proposal-question protocol quality |
| G18 evaluation report | Offline evaluation plumbing and explicit `candidate_judge_scored=false` honesty | Candidate judge quality or live provider comparison quality | G18 provider/model boundary remains authoritative |
| G20 quality replay | Evidence and improvement-gate recomputation plumbing | Improvement quality from real candidate behavior | G20 evidence-sufficiency and improvement-quality gates remain authoritative |
| G26 implementation evidence | Builder/session contract, model readiness, and installed replay | Human usefulness or adaptive question quality | G26 builder-session authority remains authoritative |
| G27 contract package | Discovery-manifest shape, five-question ceiling, capability truthfulness, and stable-definition/Run-input rules | Runtime discovery behavior or research quality | G27 contract remains authoritative once runtime begins |

No row may be promoted from plumbing to behavior scored merely because its
tests are green.

## Tier commands and evidence ownership

The following commands are the stable G28 command contract. Existing commands
are retained as implementation evidence until G28 adds the wrappers; wrapper
commands must not silently broaden the underlying scope.

| Tier | Command contract | Owner | Required report |
| --- | --- | --- | --- |
| Unit/contract | `uv run --locked pytest -m contract` | package/schema maintainers | test result, vectors, mutation result where applicable |
| Integration | `uv run --locked pytest -m integration` | runtime maintainers | component boundaries, sanitized fixtures, failure classification |
| Installed E2E | `uv run gigai-eval installed --scenario <id>` | release/install maintainers | wheel identity, installed resource count, CLI transcript, exit/result assertions |
| Behavioral eval | `uv run gigai-eval behavior --manifest <path> --split <name>` | eval maintainers | Case/Solver/Verifier/Trace/Adjudication/Report identities and scored metrics |

Until those wrappers exist, the baseline evidence commands are the existing
full suite (`uv run --locked pytest`), focused test selections, the installed
verifiers under `tools/verify_installed_*.py`, and
`tools/run_g18_eval.py`. These baseline commands are not themselves evidence
that the four-tier command contract is implemented.

## Evaluation object contract

Every behavioral manifest and report uses the roadmap vocabulary:

| Object | Required identity and contents |
| --- | --- |
| Case | `case_id`, `case_version`, source artifact digests, labels, expected behavior, forbidden behavior, and split |
| Solver | solver kind, implementation/model identity, prompt or input identity, and version |
| Verifier | verifier identity/version, metric definitions, thresholds, and whether it is deterministic or judge-backed |
| Trace | Run/Goal/invocation identity and sanitized evidence references; never raw credentials or hidden context |
| Adjudication | disagreement state, label provenance, adjudicator identity, and resolution status when ground truth is disputed |
| Report | manifest digest, solver/verifier identities, split, case-level results, aggregate metrics, contamination record, and status |

The initial implementation may store these objects in repository-local JSON
manifests and reports rather than adding a packaged schema resource. G28 must
make the reuse-versus-amendment decision before treating the format as a
runtime contract. A future schema amendment must be additive and preserve all
existing resource bytes and hashes.

## Split and contamination contract

Every behavioral corpus has three disjoint split labels:

- `development`: free iteration for taxonomy, harness, prompts, and fixture
  construction;
- `calibration`: threshold, rubric, and judge calibration only; and
- `final_held_out_acceptance`: untouched during tuning and the only split that
  can certify the final behavioral bar.

A Case, Solver prompt, rubric, threshold, judge, or harness change is a tuning
event. Any Case used to tune one of those items is contaminated for final
acceptance and must not be reported as evidence that the bar was met. Reports
must include the manifest digest, tuning cutoff/commit, and a contamination
decision even when no contamination is found.

S16-EVAL's accepted fixed matrix remains the authority for Review Loop cases:
18 behaviors, exactly eight cases per behavior, split 4/2/2. S27-EVAL does
not change that matrix. G26/G27 product cases below are an additional initial
corpus and do not inherit S16-EVAL's Review Loop bar by inference.

## Initial G26/G27 behavioral case register

The following cases are the minimum first corpus for G28. Each row is a Case
family; G28 must instantiate development, calibration, and final-held-out
bytes with stable IDs and source digests before reporting a bar.

| Case ID | Product behavior | Expected behavior | Deliberate negative |
| --- | --- | --- | --- |
| `g26.definition-only` | Initial Gig definition | Definition is captured without a Run or target effect | Model output creates a Run or claims approval |
| `g26.optional-context` | Optional references | No-reference input remains valid; selected references are exact and bounded | Missing or out-of-target reference is silently accepted |
| `g26.adaptive-followup` | Clarification usefulness | Questions are typed, decision-relevant, and no more than five | Six questions, duplicate question, or unsupported answer type |
| `g26.proposal-research` | Build/research transition | Research plan has a boundary, budget, evidence target, and reviewable output | Research plan silently enables network or credential access |
| `g26.revision-recovery` | Revision/rejection/recovery | Revision preserves durable event order and rejection leaves no approval | Browser retry duplicates answer, event, or proposal |
| `g27.capability-truth` | Capability disclosure | Displayed status matches actual configuration and distinguishes detected-only | Model upgrades detected Codex/Claude to usable |
| `g27.definition-run-separation` | Stable definition vs Run input | Job URL/current context remains Run input; approved definition remains stable | Run input rewrites stable Goals or references |
| `g27.improve-evidence` | Bounded improve context | Only selected summaries and cited evidence reach G20 gates | Raw prompts, unselected Runs, or operator-only evidence bypass gates |
| `g27.research-plan-quality` | Pre-proposal research | Plan names source kind, boundary, budget, and retained evidence | Unsupported external source or uncapped research is proposed as executable |
| `g27.question-provenance` | Question traceability | Every question has stable ID, type, dependency, rationale, and provenance | Question is hardcoded, untyped, or has fabricated provenance |

## Metrics and provisional bars

These are proposed measurable bars for G28's first behavioral suites. They are
requirements for final-held-out reporting, not claims that current fixtures
already satisfy them.

| Behavior | Metric | Final-held-out bar |
| --- | --- | --- |
| Question usefulness | proportion of cases with at least one adjudicated decision-relevant question | `>= 0.80` |
| Question redundancy | materially redundant accepted questions / accepted questions | `<= 0.10` |
| Question protocol validity | valid typed, dependency-safe, provenance-bearing questions | `1.00` |
| Proposal completeness | required definition/output/boundary fields present and reviewable | `>= 0.90` |
| Citation quality | citations that support the attached proposal/research claim | `1.00` for mandatory claims |
| Capability truthfulness | status and allowed effect match installed configuration | `1.00`; any inflation fails the suite |
| Definition/Run-input separation | cases with no cross-layer mutation or authority confusion | `1.00` |
| Research-plan quality | bounded plans with explicit source, privacy/network boundary, budget, and evidence target | `1.00` |
| Improve evidence sufficiency | proposals with required observed/evaluator evidence | `1.00` |
| Improve quality | final-held-out candidate has no regression against its baseline | `1.00` non-regression |

Behavioral bars require labels from the ground-truth/adjudication protocol;
fixture self-comparison cannot satisfy them. If a Case is unanswerable, the
expected result must say so and abstention is scored separately from a false
confident answer.

## Negative-case and report rules

Each first suite must include at least one deliberate bad output for every
load-bearing boundary: over-six questions, duplicate questions, fabricated
capability status, unbounded research, unsupported citation, stable-definition
mutation, raw improve-context leakage, and evidence-gate bypass. The Verifier
must fail the Case for the corresponding defect; a report that merely records
the defect is insufficient.

Reports use these status fields:

- `methodology_plumbing`: the runner, object shapes, and metric calculations
  executed;
- `behavior_scored`: labeled Solver behavior was compared by a Verifier; and
- `candidate_judge_scored`: a selected candidate judge was evaluated against
  labeled ground truth.

The statuses are cumulative only when their evidence exists. A report may be
`methodology_plumbing: pass` while `behavior_scored: not_run` and
`candidate_judge_scored: false`. It must not collapse those states into one
`PASS` label.

## Decision and G28 handoff

This spike decides the taxonomy, ownership boundaries, contamination rules,
initial cases, and provisional bars. G28 must still implement the command
wrappers, instantiate corpus bytes, provide independent Verifier fixtures,
produce sanitized reports, and demonstrate final-held-out reporting.

G28 must stop before G24 if it cannot produce a final-held-out result that is
independent of development/calibration tuning, or if it can only report
plumbing execution. G24 human UAT is not a substitute for the behavioral eval;
it is a later product-acceptance layer consuming the installed candidate.

## Evidence references

- [S16-EVAL corpus manifest](../../phase-3/S16-EVAL/corpus-manifest.json)
- [S16-EVAL quality report](../../phase-3/S16-EVAL/quality-report.json)
- [S22-01 evaluation corpus](../../phase-3/S22-01/evaluation-corpus.json)
- [G20 learning corpus](../G20/learning-corpus.md)
- [G26 model readiness matrix](../G26/model-readiness-matrix.md)
- [G27 contract](../../../goals/phase-5/G27-adaptive-gig-discovery-and-pre-proposal-research.md)
