# SCOUT-01 independent regression evidence

> Documentation rename only: `SCOUT-*` refers to the same historical `JSL-*`
> delivery work. Findings, verdicts, test commands and measured results below
> retain their original scope; they do not review or accept the new
> [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md).

**Date:** 2026-09-07  
**Scope:** bounded R1/R2 closeout regressions only. The new test uses the
existing `test_g43_provider_review._fixture`, a fake adapter at the real
`model_execution` port seam, private temporary workpads, and no provider,
credential, live `.gigai`, or operator-consent activity.

## Result

The focused regression module passed all 15 tests. R1 now has direct evidence
that two identical closeout callers publish one immutable receipt and that
exactly one caller returns a successful replay; R2 has direct negative-path
evidence for each required authenticated input and authority/path boundary.

## Exact validation

| Command | Result | Evidence class |
| --- | --- | --- |
| `rtk .venv/bin/pytest -q tests/test_jsl_closeout_regressions.py` | `15 passed in 60.43s` | Offline fixture-backed regression; real `model_execution` evidence path and real journal Git/lock path, fake provider port |
| `rtk .venv/bin/python -m py_compile tests/test_jsl_closeout_regressions.py` | passed | Static syntax check |
| `rtk git diff --check -- tests/test_jsl_closeout_regressions.py docs/development/evidence/v0.1.7/Scout/SCOUT-01-regression-evidence.md` | passed | Changed-artifact whitespace check |

The full suite was intentionally not run because it remains coordinator-owned.

## R1 concurrency regression

`test_concurrent_identical_closeout_publishes_once_and_replays_once` starts two
forked processes with the same authenticated Run/Plan/evidence. A deterministic
barrier is placed after each process has authenticated the clean evidence and
immediately before `record_transition`; publication then uses the real
interprocess journal writer lock and immutable-destination preflight. The test
requires:

- both calls succeed;
- replay flags are exactly `[False, True]`;
- both results carry the same closeout ID;
- one `no-fix-required.json` receipt and one
  `provider-review-no-fix-required` handoff exist; and
- no recovery transaction manifest remains.

This is fixture-backed runtime evidence for the journal race and no-clobber
contract, not live-provider evidence.

## R2 regression coverage

The parameterized test covers both missing and tampered forms of each input:

- reviewer request artifact;
- reviewer response artifact;
- reviewer invocation record;
- terminal `result.json`; and
- terminal `review-loop.json`.

It also covers a foreign Run, a separately registered foreign Gig, and a
symlinked provider-review evidence parent. Each case refuses with the expected
typed error. The artifact-tampering and symlink cases also explicitly assert
that no closeout receipt was created.

`test_interrupted_closeout_is_recovered_then_replayed` injects an interruption
after receipt replacement but before journal commit, verifies the recovery
manifest exists, runs explicit `reconcile_journal`, and then requires the
closeout call to return `replayed: true` with the manifest removed.

## Timing and interpretation boundary

The existing journal mount probes remain production validation and were not
patched or weakened. This run had no mount-probe failure. If a future run fails
at `mount.atomic_replace` before the closeout assertions, retain the original
failure and investigate the probe and shared-path behavior. A bounded rerun can
help distinguish intermittent behavior, but passing on retry does not prove
the first failure was environmental or rule out a runtime race.

## Changed artifacts and remaining limits

Only these new artifacts are owned by this regression task:

- `tests/test_jsl_closeout_regressions.py`
- `docs/development/evidence/v0.1.7/Scout/SCOUT-01-regression-evidence.md`

The evidence is offline and fixture-backed. It does not establish live Claude
or Codex-Luna behavior, real provider readiness, private-Gig dogfood, or full
suite status.
