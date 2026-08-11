# G23 Mutation Report

- Status: Accepted
- Harness: `tools/run_g23_mutation.py`
- Result: `mutation_killed=10/10`

The harness killed mutants removing:

1. sealed-pointer canonical comparison;
2. unpublished-pointer refusal;
3. manifest digest revalidation;
4. cross-Gig manifest refusal;
5. proposal-lineage cycle refusal;
6. proposal-lineage missing-parent refusal;
7. forged publication proposal-identity refusal;
8. approval-tag resolution;
9. create-proposal lineage termination; and
10. historical-proposal schema validation.

Each mutant ran in a disposable source tree and was killed by a real fixture.
The cycle mutant is bounded by a
subprocess timeout so a removed loop guard cannot turn the harness into an
unbounded process.
