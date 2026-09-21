# SCOUT R5 interview corrections — 2026-09-20

Status: bounded implementation handoff; not release, installed-wheel, provider,
model, publication, or second-home acceptance.

## Original findings disposition

| Finding | Disposition |
| --- | --- |
| R5-1 selected posting/source references were not authenticated | Corrected in `scout_interview_records.py`: source families are closed, exact IDs and artifact refs are required, project/Gig ownership is checked, and native, research, discovery, and prior-interview readers are resolved from committed bytes. Role-only selections reject postings. |
| R5-2 no strict interview schema or supported graph entry | Added `scout-interview-preparation.schema.json`, validate it at publication/read, and added `InterviewRunRequest` plus a `prepare-interview` Graph-owned Run branch. Comparison registration is now present in the main CLI, validator inventory, SHA256SUMS, and installed verifier. |
| R5-3 validation and publication used separate snapshots | Selection/content source hydration, parent CAS, schema validation, operation-key lookup, and journal publication now run under one writer lock and one captured snapshot. |
| R5-4 stories only had nonempty refs | Factual stories, claims, metrics, and skills require authenticated selected experience evidence. Hypotheticals remain explicitly labelled and feedback/preparation refs cannot become factual story evidence. |
| R5-6 continuation/revision | Feedback and revise continue immutable preparation lineage with stale-parent CAS; fresh reads validate the strict record schema. Full second-home materializer acceptance remains outside this interview lane. |

The owned combined fixture now uses one valid native experience question
(`minItems: 1`) and exact `scout_record` identity/artifact bytes. A new negative
test covers foreign family/path spoofing and confirms the journal HEAD is
unchanged on refusal.

## Verification

Commands and results:

    ruff check src/gigai/scout_interview_records.py src/gigai/run.py tests/test_scout_r5_interview_transfer.py
    All checks passed!

    .venv/bin/python -m py_compile src/gigai/scout_interview_records.py src/gigai/run.py
    passed

    .venv/bin/pytest -q tests/test_scout_r5_interview_transfer.py -k 'interview or opportunity'
    5 passed

    .venv/bin/pytest -q tests/test_scout_r5_interview_transfer.py -k 'public_prepare_interview or public_revision_race'
    2 passed

    .venv/bin/pytest -q tests/test_scout_r5_interview_transfer.py
    7 passed

    .venv/bin/pytest -q tests/test_scout_r5_interview_transfer.py -k 'interview_refusal'
    1 passed

The full owned fixture passes after the transfer lane's concurrent correction;
no full repository suite was run. The public integration test invokes the main
`scout-interview prepare --run --confirm` command against a synthetic approved
Graph Set, then reads completed Run details and result evidence. The race test
starts a competing feedback writer while the primary revision holds its writer
lock; the competing stale parent is refused without a second commit.

## Remaining constraints

1. The transfer lane must supply/confirm its source-inventory/package helper
   boundary for portable interview assets; no transfer-owned file was edited.
2. The public proof is synthetic and injected/offline: no provider/model,
   network, personal data, activation, or publication was used. Installed
   verifier output is now `verified 81 installed GigAI schemas`; comparison
   owns the final combined suite and any installed-wheel proof.
