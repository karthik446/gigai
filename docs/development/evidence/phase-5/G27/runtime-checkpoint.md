# G27 Runtime Checkpoint — Discovery and Improve Boundaries

- Status: In progress; runtime checkpoints verified
- Date: 2026-08-16

## Verified behavior

- Create uses the configured model to produce a bounded direction round rather
  than selecting the old domain-specific prompt sequence.
- The browser discloses current capability status and labels target effects
  and approved Run execution as unsupported in the discovery projection.
- Stable-definition and Run-input projections are shown before approval.
- Discovery manifests are content-addressed, schema-validated, journaled, and
  revisioned with `parent_manifest_id`.
- Improve context carries only the selected G20 learning IDs, active-version
  snapshot, and a bounded omitted-content summary. Existing G20 approval
  remains authoritative.

## Machine evidence

```text
uv run --locked pytest -q tests/test_g27_runtime.py tests/test_g26_cli_builder.py
10 passed

uv run --locked python tools/run_g27_mutation.py
mutation_killed=8/8

uv run --locked python tools/verify_installed_g27.py
verified installed GigAI G27 adaptive discovery manifest
```

The installed replay verified 31 packaged schemas and confirmed that discovery
does not create proposal authority. The same replay was run from a freshly
built `gigai-0.1.4` wheel in a disposable Python 3.11 environment. The
mutation harness covers eight named guards, including capability truthfulness,
network-boundary classification, and duplicate-approval idempotence. The
recovery test reconciles an interrupted journal transaction before exposing
the manifest as durable evidence. Broader evaluator coverage and G29 human
UAT remain open.
