# G27 Implementation Checkpoint — Adaptive Discovery Manifest

- Status: Runtime slice accepted; G27 remains in progress
- Scope: model-selected create questions and subordinate discovery-manifest
  persistence
- Date: 2026-08-16

## Delivered

- Added the `g27-discovery-round` deterministic fixture response with three
  domain-neutral direction questions.
- Replaced the create callback's sequential `main-drive` /
  `success-definition` selection with one model-selected discovery round.
- Added `gigai.discovery` to build a schema-validated
  `gig-discovery-manifest.json`.
- Recorded stable-definition, Run-input, and question-generation artifacts
  under the existing `review/` workpad surface.
- Added a journal transition for discovery-manifest publication.
- Derived capability statuses from configured model readiness and the accepted
  G27 capability vocabulary. Target effects and approved Run execution remain
  explicitly unsupported in this discovery projection.
- Refused discovery manifests containing more than five direction questions.

## Authority boundary

The discovery manifest is subordinate evidence. It does not allocate a
proposal ID, approve a proposal, advance an active version, create a Run, or
authorize a target effect. Proposal approval continues through the existing
`gig-proposal` and lifecycle paths.

## Focused verification

```text
uv run --locked pytest -q \
  tests/test_g27_runtime.py \
  tests/test_g26_cli_builder.py \
  tests/test_g27_discovery_contract.py
8 passed
```

This checkpoint does not claim completed pre-proposal research execution,
improve-context filtering, full recovery coverage, installed-wheel replay, or
G29 human UAT. Those remain open G27/G29 obligations.
