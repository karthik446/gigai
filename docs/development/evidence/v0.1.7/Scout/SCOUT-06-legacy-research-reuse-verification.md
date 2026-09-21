# SCOUT-06 legacy v2 to v3 research reuse verification

Date: 2026-09-10

This focused regression creates a disposable same-Gig fixture whose first
approved Graph Set has the retained v2 domain identity
`research-packet:2` / `scout-role-research:2`. Its logical `.073` schema and
renderer references contain the exact fixed packaged `.071` bytes; normal
proposal/approval publication then completes a real v2 external-recording
Plan, Run, checkpoint, and receipt without relabeling it.

It authors a v3 successor definition outside the workpad and publishes it
only through `propose_graph_set_offline` and `approve_offline`. The successor
uses the current `.076` v3 domain, and its v3 Plan successfully selects and
starts from the completed v2 Run through the closed public selector; the old
committed output/check/supporting bytes remain unchanged and the original v2
submit retry remains an idempotent replay.

The successor-publication correction adds authenticated `artifact_refs` only
to new amendment publication handoffs.  The same fixture now completes the v3
`checkpoint_v2` and `submit_v2` calls, while retaining the exact selected v2
output tuple and the original v2 submit replay.  It also redeems each newly
published immutable Graph Set descendant through `read_committed_artifact` at
the successor publication commit; the intentionally shared mutable
`manifests/gig-proposal.json` remains multi-publisher-refused rather than
being weakened or backfilled.

The test is fixture-local: it does not reconstruct an archived catalog release,
change production source, write an active pointer or sealed artifact directly,
contact a provider, use private user data, activate a capability, or commit
repository changes. It establishes the completed legacy-v2-to-v3 public-path
acceptance proof, but does not test separate v3-to-v3 ancestry behavior.

Verification: `.venv/bin/pytest -q tests/test_scout06_legacy_research_reuse.py`
reported **1 passed in 27.43s**. Narrow existing Graph Set regressions passed:
`test_two_graph_propose_approve_plan_history_and_run` (**1 passed, 15
deselected in 4.80s**) and
`test_first_graph_set_is_source_backed_pending_then_explicitly_approved`
(**1 passed, 13 deselected in 2.32s**). `ruff check src/gigai/lifecycle.py
tests/test_scout06_legacy_research_reuse.py` passed. No 29-test lane, broad
suite, provider/network, activation, wheel, or commit was run.
