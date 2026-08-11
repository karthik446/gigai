# G21 Mutation Report

- Status: Accepted evidence
- Original commit: `2c8d035`
- Corrective commit: `e8e5269`
- Command: `uv run python tools/run_g21_mutation.py`
- Result: `mutation_killed=9/9`

The harness killed mutations for all six load-bearing guards:

1. occurrence-slot uniqueness;
2. snapshot-identity revalidation;
3. terminal replay idempotency;
4. Run-linkage binding;
5. missing-output blocking;
6. winnerless comparison semantics.
7. prepared-Run terminalization guard;
8. non-null refusal outcome-actor schema guard; and
9. explicit comparison version-mismatch guard.

The same boundary test suite statically rejects scheduler/background activity,
provider/network/credential access, and target-effect imports from the G21
implementation.
