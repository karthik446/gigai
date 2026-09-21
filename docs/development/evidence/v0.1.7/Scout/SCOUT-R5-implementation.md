# SCOUT-R5 interview and portability implementation

Date: 2026-09-12 (America/Denver)  
Scope: Luna implementation, synthetic/offline only.

## Delivered

Interview preparation now has a public facade (`scout_interview.py`), a
journal-backed record service (`scout_interview_records.py`), and a standalone
Click adapter (`scout_interview_cli.py`). It supports explicit role-only and
exact-snapshot opportunity selection, source-checked research/experience
references, explicit research reuse, immutable preparation revisions,
fresh-session read/revise, feedback revisions, evidence-backed stories, and
labelled hypothetical examples. It rejects missing-stage input with focused
questions and refuses application state, unsupported/unreferenced stories,
unlabelled hypothetical examples, and stale parents.

Definition portability and private transfer are separate ZIP formats in
`private_transfer.py`, with standalone commands in `private_transfer_cli.py`.
Both use complete manifests with exact bytes/digests and refuse traversal,
absolute paths, symlinks/link markers, credential/configuration material,
manifest mismatch, and existing destinations. Definition export is limited to
safe inventoried source roots; private transfer preserves selected journaled
history while omitting `state.sqlite`, generated reports, scratch, and local
Git config/hooks. Imported archives grant no activation or execution approval.

`scout_template.py` now exposes read-only digest comparison and explicit
adopt/defer decision helpers; customized working copies are not overwritten.
The copied `data/scout/gig.py` wrapper exposes `interview` and `transfer`
subcommands. `portability.py` retains thin compatibility wrappers that keep
definition and private transfer formats distinct.

## Verification

```text
rtk ruff check src/gigai/scout_template.py src/gigai/scout_interview.py \
  src/gigai/scout_interview_records.py src/gigai/scout_interview_cli.py \
  src/gigai/private_transfer.py src/gigai/private_transfer_cli.py \
  src/gigai/portability.py src/gigai/data/scout/gig.py \
  tests/test_scout_r5_interview_transfer.py
-> passed

rtk python -m compileall -q src/gigai/scout_template.py \
  src/gigai/scout_interview.py src/gigai/scout_interview_records.py \
  src/gigai/scout_interview_cli.py src/gigai/private_transfer.py \
  src/gigai/private_transfer_cli.py src/gigai/data/scout/gig.py
-> passed

rtk .venv/bin/pytest -q tests/test_scout_r5_interview_transfer.py::test_transfer_rejects_path_traversal
-> 1 passed

rtk .venv/bin/python -c '... export_definition/import_definition ...'
-> definition archive round trip passed in a disposable synthetic directory
```

The full new interview fixture is present in
`tests/test_scout_r5_interview_transfer.py`, but its journal-backed setup is
currently blocked before R5 code by the dirty checkout's malformed
`runtime-evaluation-pack.schema.json` (JSON decode failure during existing
`create_offline` validation). R6 owns that shared schema/registry repair and
must rerun the full R5 fixture after repair. No live model/provider/network,
real user data, download, activation, publish, commit, or cleanup was used.

## R6 registration request

Register `scout-interview` and `scout-transfer` in the main CLI and add the
three schema names from `SCOUT-R5-contract.md` to the central registry,
packaged inventory, `schemas/SHA256SUMS`, and installed-schema verifier. Keep
these services as the sole journal writers; do not add a parallel DTO-only
handoff.

## Pending limits

R5 does not claim installed-wheel proof, second-home execution, template
adoption publication, release readiness, or provider/runtime comparison. The
private archive restore is a safe file transfer; a restored home still needs
normal local project/Gig binding and explicit approval before any new Run.
