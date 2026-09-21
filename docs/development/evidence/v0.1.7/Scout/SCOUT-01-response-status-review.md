# SCOUT-01 response and provider-Run status review

**Date:** 2026-09-08  
**Scope:** Independent, read-only review of the current response-framing and
provider-only Run-lifecycle changes in `provider_review.py`, `run.py`, and the
two focused test modules. This is not provider dogfood, release acceptance, or
a claim about the original G44 subject/baseline.

## Blocker

### SCOUT-01-RS1 — provider review can remain durably `running` forever after abandonment

`launch_run` commits the `running` state before entering the synchronous
provider call ([`run.py:233-248`](../../../../../src/gigai/run.py)); its local
`BaseException` handler only terminalizes failures that return control to that
same caller ([`run.py:249-268`](../../../../../src/gigai/run.py)). A process
death, forced termination, or an exception raised while `_finish_provider_review`
is authenticating terminal evidence therefore leaves the already-journaled Run
at `running`.

`read_run_details` then explicitly declines ordinary reconciliation whenever
`_provider_review_active` sees a schema-valid manifest plus consent scoped with
`provider_review_requested: true` ([`run.py:387-449`](../../../../../src/gigai/run.py)).
That predicate has no durable worker identity, lease, terminal-intent record,
or recovery rule, so it cannot distinguish an active synchronous caller from
an abandoned one; status polling will preserve `running` rather than produce a
truthful terminal status. Add a journal-backed recovery/terminalization rule
for an abandoned provider pass (without fabricating offline Goal execution),
and cover interruption between `_mark_provider_review_running` and terminal
publication, including a terminal-evidence authentication failure.

## Checks performed

`rtk proxy .venv/bin/pytest -q tests/test_g43_response_framing.py tests/test_g43_provider_run_status.py`
passed: **13 passed in 23.23s**. `rtk proxy ruff check src/gigai/provider_review.py
src/gigai/run.py tests/test_g43_response_framing.py tests/test_g43_provider_run_status.py`
passed. The focused tests substantiate raw/fenced JSON handling, duplicate and
ambiguous JSON rejection, provider exceptions that return to `launch_run`,
target-change interruption, bounded evidence, and preservation of the offline
Goal state; they do not exercise abandonment or terminalization failure after
the `running` transition.
