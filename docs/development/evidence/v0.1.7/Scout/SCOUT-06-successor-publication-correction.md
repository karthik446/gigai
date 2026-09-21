# SCOUT-06 successor Graph Set publication provenance correction

Date: 2026-09-10

## Defect and correction

The first Graph Set publisher already records an authenticated artifact
reference for every artifact in its journal handoff.  The successor branch of
`propose_graph_set_offline` staged the same immutable Graph Set descendants
but called `record_transition` without that metadata.  Subsequent v2 external
recording correctly refused a successor-selected Goal Graph because
`read_committed_artifact` could not authenticate its publisher.

The successor branch now supplies `artifact_refs` for exactly the artifacts it
publishes in that new handoff: staged Graph Set, per-graph Goal Graphs and
contracts, and the current proposal record.  Markdown uses `text/markdown`;
the remaining staged JSON artifacts use `application/json`, matching the
first-publication contract.  No historical journal entry is changed, no
reader/multi-publisher rule is relaxed, and no active pointer or approval
authority is bypassed.

## Focused proof

The legacy regression creates and completes an actual retained-v2 research
Run, publishes a same-Gig v3 successor through the public proposal and
approval APIs, then authenticates every immutable successor Graph Set
descendant at the proposal commit with `read_committed_artifact` and exact
digest/size assertions.  It deliberately verifies that
`manifests/gig-proposal.json` is still rejected as a multi-publisher path,
then completes the v3 checkpoint/submit and exact old-v2 submit replay.

Verification:

* `.venv/bin/pytest -q tests/test_scout06_legacy_research_reuse.py` — **1
  passed in 27.43s**.
* `.venv/bin/pytest -q tests/test_scout02_graph_set_flow.py -k
  two_graph_propose_approve_plan_history_and_run` — **1 passed, 15 deselected
  in 4.80s**.
* `.venv/bin/pytest -q tests/test_scout05_first_proposal.py -k
  first_graph_set_is_source_backed_pending_then_explicitly_approved` — **1
  passed, 13 deselected in 2.32s**.
* `ruff check src/gigai/lifecycle.py tests/test_scout06_legacy_research_reuse.py`
  — passed.

This is focused disposable-workpad proof only. Provider/network execution,
private-user data, wheel build, full-suite acceptance, capability activation,
and repository commit were not performed.
