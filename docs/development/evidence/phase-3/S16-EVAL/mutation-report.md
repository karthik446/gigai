# S16-EVAL mutation report

The mutation bar is 100% of the named research-harness guard and fixture
mutations. The command below mutates only in-memory methodology inputs; it
does not edit files, invoke a provider, use credentials, or touch a GigAI
target.

```text
uv run python tools/run_s16_eval_mutation.py
M01 remove behavior coverage -> caught
M02 change fixed split count -> caught
M03 remove required category -> caught
M04 tune on final held-out case -> caught
M05 emit forbidden extra finding -> caught
M06 accept unsupported citation -> caught
M07 fail to abstain on insufficient evidence -> caught
M08 map harness assertion to runtime finding -> caught
M09 accept severity outside adjacent tier -> caught
M10 accept unjustified confidence -> caught
mutation_killed=10/10
```

| Mutation | Defended behavior | Catching assertion | Result |
|---|---|---|---|
| M01 | Fixed behavior matrix completeness | `s16.harness.corpus_completeness` | caught |
| M02 | Exact Development/Calibration/Final split | `s16.harness.corpus_completeness` | caught |
| M03 | Required category coverage | `s16.harness.corpus_completeness` | caught |
| M04 | Final Held-Out contamination boundary | `s16.calibration.final_holdout_only` | caught |
| M05 | Over-reporting and false positives | `s16.case.precision` | caught |
| M06 | Citation-support correctness | `s16.case.citation_support` | caught |
| M07 | Insufficient-evidence abstention | `s16.case.abstention` | caught |
| M08 | Harness/runtime namespace separation | `s16.harness.mutation_kill` | caught |
| M09 | Severity calibration | `s16.case.severity_confidence` | caught |
| M10 | Confidence calibration | `s16.case.severity_confidence` | caught |

This report covers the S16-EVAL harness and fixture guards. It does not claim
mutation coverage of runtime evaluator implementation; G16's runtime semantic
mutation evidence remains the authority for the already-implemented loop.
