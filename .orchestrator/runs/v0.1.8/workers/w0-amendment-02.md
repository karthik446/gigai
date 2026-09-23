# w0 — Amendment 02 worker handoff

state: done
output: docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
question_for_operator: none; root/operator review and packet dispatch gate remain required.

The amendment is 156 lines and documents D1–D4, the functional
`acquire -> assess -> present` / `COMPLETE` graph, network capability and consent,
watchlist shape, model/resume/assessment contracts, localhost UI, four disjoint
owned-file lists, focused acceptance commands, risks, non-claims, and evidence
boundaries. It cites Amendment 01 and P3 rather than repeating them.

READ: AGENTS/RTK instructions; P2-FREEZE-04 Amendment 01, P3 integration trace,
interface freeze and dispatch matrix; source lines in `run.py`,
`scout_materialization.py`, acquisition records/CLI, adapter factory,
model execution, proposal/record/execution, tailor/document selection,
projection/report readers/report, `pyproject.toml`, README, and focused test
inventories referenced by the amendment.

EXECUTED: read-only `git status`, `rg`, `nl`/`sed`, `git diff --check` for the
requested path (exit 0), an `awk` trailing-whitespace check, and the required Orca
status check (no messages).
Applied documentation-only edits to the target amendment and this handoff; no
source, test, schema, other-doc, provider/model, Exa, HiringCafe, or network
execution occurred, and no tests/full suite were run.
