# SCOUT-01 closeout contract

> Documentation rename only: `SCOUT-*` refers to the same historical `JSL-*`
> delivery work. Findings, verdicts, test commands and measured results below
> retain their original scope; they do not review or accept the new
> [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md).

## Scope

This amendment adds only the missing terminal `no_fix_required` decision for
an already completed, clean G43.1 provider review.  It does not approve a
baseline, seal or authorize a Run, activate G44, create a Gig, or authorize a
provider invocation.

## Direct operator boundary

`gigai provider-review closeout --run RUN_ID --plan RUN_PLAN_ID --confirm`
requires a direct local `--confirm`.  No file, model response, agent message,
or approval-shaped JSON substitutes for that confirmation.  Without it the
command refuses before writing a receipt or journal transition.

## Eligibility

The command resolves one existing project and Gig, reads the exact sealed Run
Plan and its exact Run, and accepts only all of the following:

- the Plan is a typed G43.1 review with the `standard` profile and exactly two
  distinct reviewer participants in distinct independence groups;
- the Run is for that exact Plan, with matching Plan digest and direct
  provider-review Run consent;
- the provider-review `result.json`, `report.json`, and `review-loop.json`
  are regular, in-workpad artifacts, have matching IDs/digests, validate their
  schemas, are terminal `complete`, and report zero findings; and
- those evidence artifacts are present in the committed private journal entry
  for the G43.1 provider-review terminal transition.  A merely well-formed or
  separately journaled approval-shaped file is not evidence.

Missing, malformed, symlinked, altered, foreign, replayed-under-another-Run,
Plan-substituted, or finding-bearing inputs refuse.  A receipt is idempotent
only for the same exact Gig, Run, and Plan evidence identity; a changed source
does not produce a second receipt.

## Receipt and transition

The immutable `provider-review-closeout-receipt:1` records its own identifier,
project/Gig/Run/Plan identities, Plan digest, authenticated report/loop/result
references, the two reviewer participant IDs, and the operator actor/time.
It is validated before publication and written only with the journal transition
`provider_review_no_fix_required`.  Readers revalidate both bytes and the
committed transition, so hand-authored receipts do not count.

The receipt is terminal closure evidence only.  It cannot be consumed as an
approval receipt, baseline approval, sealed Plan, Run consent, new Run
authority, active-version pointer, or provider permission.
