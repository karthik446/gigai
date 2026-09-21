# SCOUT proposal-assessment Run authority corrections

Status: bounded implementation evidence, 2026-09-11. This slice is not daily
scheduling, UI, Tailor, application submission, or approval of a user instance.
It uses disposable synthetic fixtures and an injected local transport only.

## Corrections

The proposal caller now requires the distinct, explicitly registered
`proposal-assessment` Graph Set member. It no longer aliases
`tailor-application`; the historical Tailor graph, `.075` source/schema bytes,
and Tailor readers are untouched. The candidate package compiler registers a
separate inert goal-graph source and a closed v1 output envelope until a future
versioned proposal-assessment domain schema is approved. The operation still
requires the normal approved Graph Set and direct operator consent at Run
entry; this change does not approve or activate any user Gig.

Proposal Runs now preserve v2 Graph Set identity in `run-manifest.json`,
including an operator-explicit `proposal-graph-selection.json` sealed source.
The manifest is validated against `run-manifest-v2.schema.json`; legacy
single-graph and existing Run Plan paths retain their prior behavior.

Before allocation, the host resolves the configured local target once and
seals the exact target identity and bounds. The execution branch redeems the
canonical sealed request bytes rather than the caller's mutable selector maps,
and does not reload configuration after `run_started`. Bare lowercase
64-hex model digests are accepted at the config boundary and canonicalized to
`sha256:<64 hex>`; a cloud/remote target, missing identity, or malformed target
is refused before transport.

The pre-result failure handoff now executes under the journal writer and first
rechecks committed Goal terminal history and committed Run details. If another
writer already completed, failed, blocked, or cancelled the Goal, the stale
local exception produces no second Goal failure or Run overwrite. A complete
invocation still uses the existing host-owned result publication path; no
second whole-Run transition is invented.

## Focused evidence

Commands run from the repository root:

```text
.venv/bin/pytest -q tests/test_scout_proposal_run.py
3 passed in 49.63s

.venv/bin/pytest -q tests/test_scout05_source_bundle.py tests/test_scout_proposal_run.py
19 passed in 48.85s

ruff check src/gigai/run.py src/gigai/scout_template.py \
  src/gigai/scout_materialization.py tests/test_scout_proposal_run.py
All checks passed!
```

The tests allocate and approve a disposable Scout instance through the normal
fixture lifecycle, create a genuine completed synthetic discovery Run, then
launch the proposal-assessment entry with an injected Ollama-shaped transport.
They assert successful and invalid-result Run terminalization, transport
closure, sealed request bytes, v2 Graph Set/selection manifest references,
post-seal selector mutation and forbidden config reload, and a stale failure
attempt after a committed Goal terminal transition. The existing source-bundle
lane confirms the five historical selector contract and package source
boundary still pass.

## Remaining gates

This proves the bounded caller and Run authority path, not a live model result,
host OS isolation, scheduler/lease overlap, first-class proposal revisions,
Tailor output, UI report rendering, or outbound application behavior. The
proposal output remains private and host-validated; model claims cannot create
source authority. A cancellation race that occurs after model invocation but
before result publication still depends on the existing invocation evidence
contract; this slice refuses stale Goal transitions and does not counterfeit a
second terminal event. A future versioned proposal-assessment domain contract
may replace the currently supported generic output envelope after its schema,
inventory, and reader compatibility are reviewed.
