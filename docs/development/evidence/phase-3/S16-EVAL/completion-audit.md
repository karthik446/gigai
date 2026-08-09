# S16-EVAL completion audit

- Status: Complete for the approved research spike; no runtime or provider
  implementation was performed.
- Contract boundary: current G15/G16 evaluator substrate is sufficient. No
  packaged schema, canonical vector, lifecycle transition, journal authority,
  installed verifier, provider port, or target mutation changed.
- Corpus: fixed 18-row matrix, exactly 8 assignments per row, split 4
  Development / 2 Calibration / 2 Final Held-Out Acceptance, 144 total
  assignments, and all four orthogonal category labels in every split.
- Ground truth: expected findings, alternatives, forbidden findings,
  evidence-support labels, severity/confidence labels, abstention expectations,
  independent review, adjudication, versioning, and contamination rules are
  defined.
- Evaluation: recall, precision/false positives, citation support,
  severity-within-one-tier, confidence ECE, abstention sensitivity/specificity,
  and critical forbidden findings have executable scoring paths and a fixed
  normative bar.
- Assertions: all roadmap section 5 assertions have stable `assertion_id`
  entries; runtime `finding_code` mappings are limited to actual Review
  Finding assertions.
- Mutation coverage: 10/10 named research-harness guard/fixture mutations
  were caught offline.

## Verification

```text
uv run pytest -q tests/test_s16_eval_methodology.py
10 passed

uv run python tools/run_s16_eval_mutation.py
mutation_killed=10/10

rtk git diff --check
pass
```

The Final Held-Out Acceptance result is intentionally not reported here. It
must be run by G18 after selecting a candidate production judge, using this
spike's frozen labels, split, and acceptance vector. No live provider access
was used or required.
