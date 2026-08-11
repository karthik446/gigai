# G20 Learning and Improvement Corpus Evidence

- Status: Accepted implementation evidence
- Goal: G20 local improve and evaluator learning
- Recorded: 2026-08-10

## Corpus and record shape

G20 publishes one normalized learning record per source artifact. Each record
captures the subject Run or Goal, the Gig and active version observed at that
time, the exact active-pointer digest, a typed source identity, an immutable
source path/digest/size citation, and one of four provenance values:

- `observed_outcome`
- `evaluator_judgment`
- `operator_feedback`
- `accepted_outcome`

The contract tests instantiate every provenance value and reject mismatched
source identities and unknown fields. A source artifact can be cited only once
for the same observed Gig/version/subject, preventing duplicate observations
from silently inflating evidence.

## Improvement manifest

An improvement manifest is subordinate to the existing `gig-proposal` resource.
It carries a typed before/after change manifest restricted to review-contract,
rubric, and verifier paths. It cannot allocate proposal identity, advance an
active version, or authorize target effects. The manifest cites one or more
learning records and carries two independently checked gates:

1. evidence sufficiency, requiring at least one `observed_outcome` or
   `evaluator_judgment` record; and
2. improvement quality, requiring baseline/candidate replay across development,
   calibration, and final held-out acceptance splits with no final-set
   regression.

The quality gate is deterministic runtime plumbing. It does not contact a
provider or claim that a candidate model judge was scored. The final held-out
bar is recomputed from the bound metrics and thresholds before approval.

## Contract evidence

- [Accepted G20 contract amendment](learning-contract-amendment.md)
- [G20 contract tests](../../../../tests/test_g20_learning_contract.py)
- [G20 runtime tests](../../../../tests/test_g20_learning_runtime.py)

