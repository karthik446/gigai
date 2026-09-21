# SCOUT-01 independent runtime review

> Documentation rename only: `SCOUT-*` refers to the same historical `JSL-*`
> delivery work. Findings, verdicts, test commands and measured results below
> retain their original scope; they do not review or accept the new
> [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md).

**Date:** 2026-09-07  
**Reviewer scope:** read-only review of the current working-tree SCOUT-01 implementation; this report is the sole review artifact written. No live Gig, provider, credential, or operator-consent command was invoked, and no runtime or test file was changed.

## Verdict

**Not ready to close as written: one Medium concurrency/idempotency defect and one
Low negative-path coverage gap remain.** The inspected code does preserve the
non-authority boundary: the CLI requires direct `--confirm`, the receipt schema
contains only closeout identity/evidence/decision fields, and the implementation
does not call approval, baseline, active-version, or Run-creation paths.

## Findings

### SCOUT-01-R1 — Medium — same-evidence concurrent closeout is not idempotent

`close_provider_review_no_fix_required` checks for an existing receipt before it
enters the journal writer lock ([provider_review.py:68-72](../../../../../src/gigai/provider_review.py)). Two
identical callers can both observe it missing. The first publishes; the second
then acquires the journal lock and the immutable-destination preflight rejects
the now-present receipt ([journal.py:237-240](../../../../../src/gigai/journal.py),
[journal.py:290-297](../../../../../src/gigai/journal.py),
[journal.py:765-783](../../../../../src/gigai/journal.py)). The second call is
wrapped as `provider_review_closeout_journal_failed` rather than re-reading the
authenticated receipt and returning `replayed: true` ([provider_review.py:93-111](../../../../../src/gigai/provider_review.py)).

This conflicts with the closeout contract's same-exact-evidence idempotency
requirement ([SCOUT-01-closeout-contract.md:29-30](SCOUT-01-closeout-contract.md)).
Keep the no-clobber preflight, but after an immutable collision re-read and
validate the receipt under the completed journal state; add a two-process
same-input closeout test that requires one publication and one successful
replay, never a second receipt.

### SCOUT-01-R2 — Low — required closeout negative paths have no direct tests

The closeout test covers the actual fake-port happy path through
`run_model_invocation`, missing `--confirm`, foreign Plan substitution, receipt
forgery, report tampering, and finding-bearing evidence
([test_g43_provider_review.py:290-374](../../../../../tests/test_g43_provider_review.py)). It does not directly exercise the
new guards for missing/tampered request, response, or invocation record
([provider_review.py:407-454](../../../../../src/gigai/provider_review.py)); missing/tampered `result.json` or
`review-loop.json`; foreign Run or Gig; symlinked evidence parents; or an
interrupted closeout transaction followed by journal recovery. The generic
journal test only verifies preflight refusal before transaction intent
([test_journal_locking_recovery.py:157-178](../../../../../tests/test_journal_locking_recovery.py)), not the closeout
caller/replay behavior.

The static checks appear to reject those cases through safe path traversal,
byte-digest checks, and journal handoff matching, but that is not execution
evidence. Add focused CLI tests for each listed negative case and one recovery
test before treating the claim in the implementation record as complete.

## Positive observations

- The successful fixture uses `FakeAdapter` only at adapter resolution, then
  executes the real `run_model_invocation` path; successful reviewer records,
  request bytes, response bytes, and their G18 journal evidence are checked by
  closeout ([test_g43_provider_review.py:300-317](../../../../../tests/test_g43_provider_review.py),
  [provider_review.py:407-479](../../../../../src/gigai/provider_review.py)). This validates a fake-port
  integration path, not live-provider behavior.
- Run/Plan/Gig binding is checked through direct consent, sealed-input bridge,
  identity checks, and committed journal metadata
  ([provider_review.py:316-363](../../../../../src/gigai/provider_review.py)).
- The receipt is schema-validated before the single
  `provider_review_no_fix_required` transition and has no approval/Run
  authority fields ([provider_review.py:74-111](../../../../../src/gigai/provider_review.py),
  [provider-review-closeout-receipt.schema.json:6-30](../../../../../src/gigai/schemas/provider-review-closeout-receipt.schema.json)).

## Validation executed

| Command | Result |
| --- | --- |
| `rtk .venv/bin/pytest -q tests/test_g43_provider_review.py -k 'direct_no_fix or no_fix_closeout'` | `2 passed, 8 deselected` |
| `rtk .venv/bin/pytest -q tests/test_g43_provider_review.py` | `10 passed` |
| `rtk .venv/bin/pytest -q tests/test_journal_locking_recovery.py` | First run: `1 failed, 20 passed`; the spawned race failed its configured `mount.atomic_replace` probe. Immediate rerun: `21 passed`. |
| `rtk .venv/bin/python tools/verify_installed_schemas.py` | `verified 37 installed GigAI schemas` |
| `(cd src/gigai/schemas && rtk shasum -a 256 -c SHA256SUMS)` | All 37 schema hashes `OK` |
| `rtk .venv/bin/python -m compileall -q src/gigai` | passed |
| `rtk git diff --check` | passed |

The full suite was deliberately not run; it remains coordinator-owned. These
fixture checks do not prove live-provider or live-Gig behavior.
