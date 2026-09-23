# Coordinator triage: Terra wave-1b review + I-3 (2026-09-23 ~06:30)
- W1BT-1 blocker (present got the acquisition-record path): **mitigated by I-3.** scout_find_jobs_bindings.py:250-264 `_present_bound` normalizes batch_ref to runs/{run_id}/outputs/acquire.json, proven by test_m1_end_to_end (three real node receipts). A root fix in run.py goes on the 0.2.0 ledger.
- W1BT-2 major (all sources fail ⇒ success with empty): **fix now** (fp-1).
- I-3 required-command failure: test_contracts.py:477 still expects the old POST statuses (no 504). **Fix now** (fp-1).
- New (coordinator): I-3 test-only env hooks GIGAI_SCOUT_FIND_JOBS_TEST_HTTP/_MODEL swap real HTTP/model for fixtures. The real API must refuse to start when either is set unless `--allow-test-seams` is passed, and then warn loudly. **Fix now** (fp-1).
Security checks Terra passed: the Exa key never leaks; loopback uses the socket peer only; no provider fallback; no resume bytes to Exa/ATS; no widening of other graphs.
