# G20 Mutation Report

- Status: Accepted implementation evidence
- Recorded: 2026-08-10
- Command: `uv run python tools/run_g20_mutation.py`

The mutation harness removed each independent proposal gate in a disposable
copy of the source tree. Both mutants were detected by the G20 runtime test:

| Mutant | Expected result | Result |
|---|---|---|
| Remove evidence-sufficiency provenance gate | operator-feedback-only evidence must be accepted incorrectly | killed |
| Remove quality split/final-held-out gate | a regressing or held-out-failing candidate must be accepted incorrectly | killed |

Result:

```text
caught G20 mutations: evidence-sufficiency-gate, quality-split-bar-gate
mutation_killed=2/2
```

This proves the two gates are independently load-bearing. It does not claim
that G20 itself performs live provider evaluation.

