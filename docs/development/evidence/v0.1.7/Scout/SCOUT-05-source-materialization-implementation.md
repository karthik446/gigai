# SCOUT-05 source materialization implementation

Date: 2026-09-09. This handoff implements candidate preparation only; it does
not promote Scout into the release-eligible general default inventory.

## Public integration path

`initialize_defaults(..., inventory=scout_candidate_inventory())` is the
explicit internal candidate path. `default_inventory()` remains the existing
release-eligible catalog and does not add Scout. The closed
`is_scout_candidate` check accepts only the exact bundled candidate digest and
definition version; init has no dynamic materializer import or user-provided
Python hook.

For the reserved Gig, `materialize_scout_candidate` journals the immutable,
private-data-free source snapshot under
`manifests/software/scout-1.0-<source-digest>/`, including the real bundled
`gig.py`, README/changelog, five `goalgraphs/`, UI source, authoring index,
compiled per-Gig Goal Graphs, contracts, creation metadata and the first Graph
Set definition. It copies only editable v2 root paths (`README.md`,
`CHANGELOG.md`, `gig.py`, `goalgraphs/`, and `ui/`); copied Python is never
imported or executed. A source snapshot is journaled with
`scout_source_materialized`, then the existing separate first-proposal service
stages a real pending `kind:create` Graph Set. Init never approves it or
changes active selection.

The new prepared binding is `template-instance-binding:2` in
`manifests/template-instance-binding.json`. It pins the source inventory ref
and digest, actual pending proposal ID/ref, unapproved state and nullable
customization/approved-version fields. Existing v1 bindings remain readable as
binding-only authority; a legacy Scout v1 binding deliberately refuses to
pretend it has prepared source/proposal authority rather than rewriting its
history. `DefaultInstanceResult` and init JSON now expose `proposal_id`,
`approval_state`, and an exact direct-approval next action.

## Verification

Executed only focused disposable tests:

```
.venv/bin/pytest -q tests/test_scout05_materialization.py \
  tests/test_scout05_init.py::test_defaults_provision_two_instances_without_active_selection_and_preserve_them \
  tests/test_scout05_init.py::test_cli_init_is_username_gated_then_reports_prepared_defaults
```

Result: `7 passed in 12.87s` (the final materialization-only rerun was `5
passed in 8.33s`). A separate focused source-boundary plus materialization run
reported `20 passed in 8.51s`. The tests cover the real source bundle copied into a fresh
Gig, all five compiled selectors in the pending proposal, no active selection
or approval, exact source/proposal reuse after interruption, preservation of a
customized root README, separate project-local instances, and refusal of a
redirected editable destination.

Scoped Ruff passed for `default_init.py`, `scout_materialization.py`,
`scout_template.py`, and the new test; compilation passed for those modules
and the changed init CLI. `cli.py` still has independently owned pre-existing
Ruff findings outside this slice, so no suppression or unrelated formatting
was applied.

## Schema and coordinator handoff

Added `src/gigai/schemas/template-instance-binding.schema.json` for the strict
v2 prepared Scout binding. It must be added by the coordinator to the schema
registry, SHA256SUMS, installed-schema verifier and central golden fixtures;
runtime performs the same closed shape validation locally until that
registration is integrated. Its SHA-256 is
`30fd6104b2fa41741149324086478d8338d534d9db2bf3927899898a9e6c2af0`.
Added one necessary shared journal transition name
(`scout_source_materialized`) after coordinator notification; it is the
publisher for immutable software snapshots.

## Remaining gates

Scout is still candidate/unready for general default promotion: no domain
outputs or SCOUT-06–10 workflows prove semantic release eligibility, no source
or capability manifest has been approved for tool execution, and the existing
tool-receipt version floor must be reconciled before a first-version Gig can
issue a supported receipt. This slice does not implement update adoption,
post-approval binding projection, report generation, providers, private data
import, or source/package installation proof.
