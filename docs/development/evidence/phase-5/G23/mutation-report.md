# G23 Mutation Report

- Status: Accepted
- Harness: `tools/run_g23_mutation.py`
- Result: `mutation_killed=6/6`

The harness killed mutants removing:

1. sealed-pointer canonical comparison;
2. unpublished-pointer refusal;
3. manifest digest revalidation;
4. cross-Gig manifest refusal;
5. proposal-lineage cycle refusal; and
6. proposal-lineage missing-parent refusal.

Each mutant ran in a disposable source tree. The cycle mutant is bounded by a
subprocess timeout so a removed loop guard cannot turn the harness into an
unbounded process.
