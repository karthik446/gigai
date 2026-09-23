# I-0 Terra worker outcome

READ: I-0 source, all I-0 fixtures/tests, Amendment 02 Rev 3, roadmap DAG, and current run-details schema. EXECUTED: `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_contracts.py -q` — 22 passed in 0.03s (focused source test only).

Verdict: **rework**. The detailed independent review is `.orchestrator/reviews/terra-i0.md`; blockers are missing/lossy NodeReceipt-to-run-details mapping, unsealed acquire-to-assess provenance, and an edit-diff helper that can never report edits, while the test suite protects only `FindJobsConfig` drift.
