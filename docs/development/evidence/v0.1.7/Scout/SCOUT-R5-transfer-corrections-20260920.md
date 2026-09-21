# SCOUT R5 transfer and template correction evidence

Date: 2026-09-20 (America/Denver)  
Lane: Luna, private transfer/export and Scout source materialization  
Input class: disposable synthetic directories and one disposable journaled
Gig; no real user data, providers, models, network, activation, publishing, or
commits outside disposable test journals.

## Delivered

- Hardened private_transfer.py against duplicate/colliding ZIP members,
  directory and link markers, traversal, symlinked ancestors, declared-size
  allocation, total/member/compression limits, manifest/member mismatch,
  absolute operational JSON/Markdown links, .env variants, known
  credential/config names, and destination overwrite races.
- Kept definition export and private transfer as separate formats. Definition
  export remains inventory-only. Private transfer omits rebuildable
  state.sqlite, reports/scratch, configuration/credential material, and
  active machine authority; imported authority-bearing manifest paths are
  rejected. Historical prose is retained without rewriting bytes.
- ID-scoped private backup now captures selected journal families through one
  JournalWriter snapshot and records the pinned journal head in the private
  manifest. Editable source roots remain working-copy material and are
  captured under the same writer lock without being represented as immutable
  journal authority.
- Added explicit bind_restored_private for second-home continuation. It
  provisions a fresh local v2 journal, preserves exact record/reference/history
  bytes through a new journal transition, keeps source/UI files inert, refuses
  old active selection until the operator clears it, imports no active pointer
  or consent, and leaves active selection false for fresh approval.
- Added `read_scout_source_snapshot` (also exposed through `portability.py`) as
  the portable interview/package boundary. It authenticates the supplied
  source-inventory artifact reference, reads inventory and every member from
  one committed journal head, verifies current bytes and digests, and returns
  inert source bytes without importing approval, consent, active selection,
  private records, or machine state.
- Added strict additive definition/private manifest schema resources. Central
  validator inventory, schema checksum/package verification, and supported
  main-CLI registration are now provided by the comparison lane; this lane
  did not edit those shared files.
- Added explicit source comparison with baseline-aware upstream updates and a
  journaled materialize_scout_template_update adopt path. Adopt applies only
  missing/upstream-unchanged files, preserves customized and local-only files,
  records an immutable update snapshot, and never changes active selection,
  consent, graph selection, or prior history. Defer is a no-op.

## Verification

```
.venv/bin/pytest -q tests/test_scout_r5_transfer_corrections.py \
  tests/test_scout_r5_interview_transfer.py::test_transfer_rejects_path_traversal \
  tests/test_scout05_source_bundle.py tests/test_scout05_materialization.py \
  tests/test_scout07_inventory_members.py
-> 42 passed, 1 warning in 136.20s

The transfer correction suite includes the explicit source-inventory/package
boundary proof: the public `portability.read_scout_source_snapshot` reloads the bundled
`goalgraphs/prepare-interview.md` and `gig.py` bytes from one committed
inventory head and confirms no active pointer is imported. The same suite
proves duplicate members, archive limits/authority exclusions, journaled
history restore, old-selection refusal, standard native-reader continuation,
and customization-safe update adoption.

After exposing the helper through `portability.py`, the source-inventory
materialization proof was rerun independently: `1 passed in 12.30s`.

ruff check src/gigai/private_transfer.py src/gigai/private_transfer_cli.py \
  src/gigai/portability.py src/gigai/scout_template.py \
  src/gigai/scout_materialization.py \
  tests/test_scout_r5_transfer_corrections.py
-> passed

.venv/bin/python -m compileall -q src/gigai/private_transfer.py \
  src/gigai/private_transfer_cli.py src/gigai/portability.py \
  src/gigai/scout_template.py src/gigai/scout_materialization.py \
  tests/test_scout_r5_transfer_corrections.py
-> passed

Schema check for the two new manifest resources
-> both Draft 2020-12 schemas valid
```

The focused combined R5 interview suite remains blocked before interview or
transfer logic by its historical invalid native-record fixture
(payload.questions=[] while the installed native schema requires at least one
question); the interview lane has separately repaired that fixture and reports
five passing interview tests. This lane did not modify the interview-owned
fixture.

## Shared integration requests

The comparison lane has registered the two transfer resources and the
interview lane's preparation schema in the central versioned validator,
package-data/checksum inventory, installed verifier, and supported main CLI.
The interview lane's portable source-inventory/package request is satisfied by
`read_scout_source_snapshot`; no interview-owned file was edited. The remaining
release boundary is a future independent review of fresh approval/execution
after local bind; this lane proves old-selection refusal, standard-reader
continuation, archive authority refusal, and journaled synthetic transfer only.
