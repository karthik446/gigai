# S16-EVAL assertion matrix

Harness assertions use `assertion_id`. Runtime Review Finding codes are a
separate namespace and appear only where the assertion evaluates an actual
Finding.

| Assertion ID | Requirement | Expected result | Runtime finding code |
|---|---|---|---|
| `s16.case.expected_findings` | Expected findings are emitted or validly unanswerable | Per-case recall meets label | `seeded_defect_recall` |
| `s16.case.precision` | Over-reporting is penalized | Precision/false-positive bar passes | — |
| `s16.case.citation_existence` | Required citations exist | Citation-existence check passes | — |
| `s16.case.citation_support` | Citation supports its attached claim | Evidence-support label passes | `citation_support` |
| `s16.case.severity_confidence` | Severity and confidence match labels | Tier and calibration bars pass | — |
| `s16.case.abstention` | Insufficient evidence abstains; sufficient evidence does not | Sensitivity/specificity bars pass | — |
| `s16.loop.duplicate_provenance` | Duplicate merge retains provenance | Independent sources remain inspectable | `duplicate_finding_provenance` |
| `s16.loop.disagreement_visibility` | Disagreement remains visible and adjudicated | No silent consensus | `disagreement_preserved` |
| `s16.loop.feedback_traceability` | Feedback maps to revision requirements | Finding IDs and source text preserved | — |
| `s16.loop.closure_partial_address` | Partial address cannot close | Closure blocks | `partial_address` |
| `s16.loop.rejected_feedback_non_reapplication` | Rejected/deferred feedback is not reapplied | No unauthorized reapplication | — |
| `s16.loop.blocking_clarification` | Insufficient context blocks addressing | Clarification is required before address | — |
| `s16.loop.cycle_cap` | Cycle cap is enforced | Exhaustion blocks without success | — |
| `s16.loop.replay_stability` | Replays differ only in declared variable fields | Canonical evidence stable | — |
| `s16.harness.mutation_kill` | Named guard/fixture mutations fail | Every required mutation is caught | — |
| `s16.harness.corpus_completeness` | Matrix and split are complete | 18 rows x 8 assignments, 4/2/2 | — |
| `s16.calibration.final_holdout_only` | Final result is uncontaminated | Only Final Held-Out set reports the bar | — |
