# Research evidence

This tree contains executable Phase 0 evidence and bounded experiments. It is
tested from the source checkout but is not included in the `gigai` wheel and
does not expose a stable product API.

- `contract_spike/` proves canonical serialization, schema instances, graph
  semantics, and concurrent journal ordering.
- `phase0_spike/` records feasibility evidence for annotations, provider
  compatibility, planning, source bundles, and tool-process boundaries.
- `experiments/` preserves supporting model-debate and session-resume work.

Production code enters `src/gigai/` only through an explicit implementation
goal and its acceptance tests.
