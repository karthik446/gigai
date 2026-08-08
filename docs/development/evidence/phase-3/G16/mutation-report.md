# G16 mutation report

The focused mutation harness `tools/run_g16_mutation.py` copied the source to
disposable trees and applied two semantic mutations:

| Mutant | Guard removed | Test result |
|---|---|---|
| `loop-transition-guard` | skipped loop transitions become accepted | caught by `test_review_loop_rejects_skipped_state_transition` |
| `sealed-run-precondition` | missing sealed Run is treated as successful | caught by `test_loop_requires_a_sealed_run` |

Both mutants were caught; no mutation was applied to the working tree.
