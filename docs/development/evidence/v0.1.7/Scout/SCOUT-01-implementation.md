# SCOUT-01 implementation and test record

> Documentation rename only: `SCOUT-*` refers to the same historical `JSL-*`
> delivery work. Findings, verdicts, test commands and measured results below
> retain their original scope; they do not review or accept the new
> [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md).

**Date:** 2026-09-07  
**Scope:** local implementation only; no live Gig, provider, credential, or
operator-consent command was invoked.

## Delivered boundary

`gigai provider-review closeout --run RUN_ID --plan RUN_PLAN_ID --confirm`
writes one immutable, journaled `no_fix_required` receipt only after it proves:

- the exact resolved Gig's sealed typed G43.1 Plan has the standard profile
  with exactly two distinct independent reviewers;
- the specified Run has direct provider-review consent and a journal-authenticated
  sealed Plan bridge with the same Plan digest;
- the Run/Plan's terminal `result.json`, `report.json`, and `review-loop.json`
  are in-workpad regular files, validate as appropriate, bind the supplied
  identities, report `complete`, and contain zero findings; and
- those exact terminal bytes appear together in the G43.1 provider-review
  terminal journal handoff, rather than merely resembling approval evidence.

The closeout also verifies the terminal result's two `reviewer_invocations`:
each must map one-to-one to a sealed reviewer, have a successful matching
Run-scoped invocation record, and appear in that record's G18 model-execution
journal handoff. This checks Gig, Run, actor, exact bytes, sealed target, and
outcome; it does not claim semantic model quality beyond the sealed response
and zero-finding terminal report.

Immutable closeout artifact paths are preflighted under the journal writer lock
before a recovery transaction manifest is created. A losing concurrent writer
cannot leave scratch recovery intent that could later overwrite the receipt;
recovery continues to use the no-replacement policy.

The typed receipt binds its own ID, project/Gig/Run/Plan identities, sealed
Plan digest, exact evidence references, reviewer participant IDs, decision,
and direct operator actor/time.  It is published only through the new
`provider_review_no_fix_required` journal transition; a replay must reproduce
the same authenticated evidence and receipt bytes.  The schema and command
help explicitly state that the record is not a baseline approval, Plan, Run
consent, provider permission, active-version pointer, or new Run authority.

## Checks completed

| Check | Result |
| --- | --- |
| `rtk .venv/bin/pytest -q tests/test_g43_provider_review.py` | `10 passed` |
| `rtk .venv/bin/pytest -q tests/test_journal_locking_recovery.py` | `20 passed` |
| Second correction focused checks: `test_g43_provider_review.py -k direct_no_fix` and `test_journal_locking_recovery.py -k immutable` | `1 passed` each |
| Fake-adapter clean standard review through actual `model_execution`, missing `--confirm`, foreign Plan substitution, authenticated request/response invocation evidence, receipt write, idempotent replay, forged receipt, tampered report, and finding-bearing refusal | Covered in the focused suite |
| `rtk .venv/bin/python tools/verify_installed_schemas.py` | installed closed inventory: 37 schemas passed |
| `rtk shasum -a 256 -c SHA256SUMS` in `src/gigai/schemas` | 37/37 schema hashes passed |
| `rtk .venv/bin/python -m compileall -q src/gigai` | passed |
| CLI `provider-review closeout --help` assertion | passed |
| `rtk git diff --check` | passed |

The installed Ruff executable was not present at `.venv/bin/ruff`; no package
installation was attempted.  The full suite was intentionally not run.

Closeout publication disables artifact replacement inside the journal writer
lock, so a concurrent conflicting receipt fails rather than overwriting the
first receipt.

## Remaining dogfood gate

This change does not close G43.1 by itself.  The operator must still use the
existing approved Gig and baseline, select the real required Claude and
Codex-Luna targets, seal and directly consent to the real provider Run, and
then use this command only if its authenticated standard review is clean.
Any finding, unavailable/malformed/unaccounted provider call, or altered
evidence leaves G43.1 incomplete and follows the correction/re-review branch.
