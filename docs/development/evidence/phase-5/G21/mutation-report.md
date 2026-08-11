# G21 Mutation Report

- Status: Accepted evidence
- Commit: `2c8d035`
- Command: `uv run python tools/run_g21_mutation.py`
- Result: `mutation_killed=6/6`

The harness killed mutations for all six load-bearing guards:

1. occurrence-slot uniqueness;
2. snapshot-identity revalidation;
3. terminal replay idempotency;
4. Run-linkage binding;
5. missing-output blocking;
6. winnerless comparison semantics.

The same boundary test suite statically rejects scheduler/background activity,
provider/network/credential access, and target-effect imports from the G21
implementation.
