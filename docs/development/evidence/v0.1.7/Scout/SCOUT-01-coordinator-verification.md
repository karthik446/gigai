# SCOUT-01 — Coordinator verification and next gate

> Documentation rename only: `SCOUT-*` refers to the same historical `JSL-*`
> delivery work. Findings, verdicts, test commands and measured results below
> retain their original scope; they do not review or accept the new
> [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md).

**Date:** 2026-09-07  
**State:** Local implementation accepted; awaiting direct-operator G43.1 live dogfood.  
**Base:** `fda4857`, uncommitted worktree changes.  
**Execution:** [Team ledger](execution-ledger.md)

## Scope and review disposition

The new `gigai provider-review closeout` command records a separate directly
confirmed `no_fix_required` decision only for an authenticated clean standard
review. It does not create a Gig, approve a baseline/version, authorize a new
Run, or invoke a provider. The original public G44 subject/baseline and the
user's `.gigai/` were not changed by this work.

Terra implemented the command, strict receipt schema, journal transition,
no-clobber publication and authenticated Run/Plan/reviewer evidence checks.
Astra's reviews required request/response provenance and immutable-destination
preflight before preparing recovery state. Independent Terra review then
found R1 (concurrent identical callers should replay, not fail) and R2
(missing direct negative-path coverage).

Astra corrected R1: a journal collision revalidates the current evidence and
returns only an exact authenticated existing receipt. It never overwrites
that receipt or treats a shaped file as sufficient. Luna independently
implemented/executed R1/R2 adversarial regressions in a separately owned test
file: all 15 passed in 60.43s. Astra inspected the final file and evidence;
the exact worker was released with its transcript archived.

## Validation evidence

| Check | Result / scope |
|---|---|
| First full suite in sandbox | 686 passed, 5 failed, 1 skipped, 6 errors, 7 subtests; 347.56s |
| Five HTTP-related failures | All affected files passed outside sandbox: 18 passed in 8.79s; loopback sockets were denied in the first run |
| Six schema setup errors | Stale 34-schema golden inventory; added the missing prior G43.1 approval/input fixtures plus new closeout fixture; historical schemas unchanged |
| Corrected schema + provider focused suite | 16 passed, 73 subtests in 26.11s |
| Final full suite with loopback access, `GIGAI_G30_UAT=0` | 697 passed, 1 skipped, 80 subtests in 357.31s; new Luna regression file was not present at collection and is verified separately |
| Luna adversarial source regressions | 15 passed in 60.43s; fake provider port, real evidence/journal/lock paths |
| Astra independent installed-wheel adversarial regressions | 15 passed in 74.70s, outside checkout with `python -I`; includes the deterministic two-process concurrency regression |
| Final installed wheel provider + journal tests | 31 passed in 48.38s, real model-execution layer through a fake adapter; no live providers |
| Installed CLI and closed schema-resource checks | Passed outside checkout with `python -I`; 37 schemas |
| Schema SHA256SUMS | All 37 passed |
| Ruff 0.9.2 | `ruff check src tests tools research/contract_spike/tests/test_schemas.py` passed; existing PATH executable, no install |
| `git diff --check` | Passed after final code/test changes |

The full suite command is:

```text
rtk proxy env GIGAI_G30_UAT=0 .venv/bin/python -m pytest -q
```

The additional installed-wheel regression command, run from the temporary
verification directory, is:

```text
rtk proxy /private/tmp/gigai-jsl-verify.AymQ2y/venv/bin/python -I -m pytest -q /Users/kar/orca/workspaces/gigai/gigai-v0.1.7/tests/test_jsl_closeout_regressions.py
```

R1 and R2 are closed for local implementation acceptance. The 697-test suite
and 15 new regressions are separate runs, not a claimed single 712-test run.
The one skipped test is the explicitly opt-in real local model CLI UAT.

The scoped permission escalation permits local loopback test servers; it does
not enable live provider UAT. One independent journal-test run had a configured
atomic-replace mount-probe failure and passed on immediate rerun (21 tests);
retain that observation rather than reporting every attempt as clean.

## Installed artifact provenance

Offline builds used cached dependencies only. Artifacts are under
`/private/tmp/gigai-jsl-verify.AymQ2y/dist-final`; the isolated verification
environment is its sibling `venv`. The user's existing GigAI installation and
project environment were not replaced. Current package metadata remains
**0.1.6**; these are validation artifacts, not the 0.1.7 release.

- Wheel SHA-256: `d5dbb45a8143f639aaaf3feea36598fe086f71f904951dd4cd2795b17afd3e8e`.
- Sdist SHA-256: `08966ec4c4d2b2c2be3ddf77124a628784df12467c2846ec67d8d774a7b5a4cf`.
- `provider_review.py`: `4138e857ff4096ab38476117d3062dd06a48060ef45feeb67582e1d0641c5a4f`.
- `journal.py`: `af87ab1297b1ee1ee7ff3c0900ee66d1bb27d31ddcb4801c955cbed35045af47`.
- `cli.py`: `a61318bab836b13fbf26512444c2fee528457099f34909f90ad2dd054f5e024c`.
- Closeout schema: `49d04d92e1b35c1aebe06323f9bee6f5bce47b2fbb13b06ccb7d178b3874086a`.

## Direct operator gate after local acceptance

Later operator update: the user has supplied a successful baseline-approval
receipt and configured target names; see the
[latest ledger entry](execution-ledger.md#latest-operator-context-and-documentation-only-update).
The steps below preserve the original handoff. Do not ask for or create a
duplicate approval without first validating the supplied receipt. Future
GigAI commands are invoked directly, without RTK; historical validation
commands above remain exactly as executed.

The existing [G43.1 handoff](../G43.1/terminal-handoff.md) remains authoritative
for the approved Gig, baseline, real Claude/Codex-Luna targets, separate
baseline approval, sealed Plan inspection, direct Run consent, verifier
evidence and correction/re-review branch. No live provider call or direct
operator confirmation was manufactured by the development team.

The first operator step after local acceptance is to inspect and directly
approve the existing baseline, using the same home/target that resolves the
existing approved Gig:

```sh
rtk proxy .venv/bin/gigai run-plan approve-baseline \
  --gig gig_84de15da-f79d-4dd6-b39d-f02f29cc345c \
  --input docs/development/evidence/v0.1.7/G43.1/G44-requirements-baseline.md \
  --confirm --json
```

Add explicit `--home`/`--target` if needed; do not create another Gig when the
binding fails. Baseline SHA-256 remains
`a293b003bfd1f56dd5e9ffb70159eb7d57e624c2021a180ace2c58a73045e59d`.
Share the returned approval JSON and configured `gigai models` target IDs with
the coordinator; secrets/configuration contents are not requested. A ceiling
for separately billed provider calls is still pending.

After the explicitly confirmed real review is clean, the new direct command is:

```sh
rtk proxy .venv/bin/gigai provider-review closeout \
  --gig gig_84de15da-f79d-4dd6-b39d-f02f29cc345c \
  --run RUN_ID --plan RUN_PLAN_ID --confirm --json
```

Use actual returned IDs, never placeholders as authority. Finding-bearing or
failed reviews cannot take this branch. SCOUT-02 remains inactive until the
required live closeout. SCOUT-00 contract completion does not bypass that gate.
