# G43.1 Terminal Handoff — Typed G44 Contract Review

**Current status (2026-09-08):** public correction/re-review completed under
explicit operator delegation; see the completion addendum below. The original
handoff sections are retained as history, not current pending instructions.

## Where we are

G43.1 now has a local, tested implementation for a provider-backed review of a
public document against a separately approved public requirements baseline. It
is not complete: its closure requires a fresh, directly confirmed review using
real Claude and Codex-Luna reviewers, verifier evidence for any schema-valid
finding, and either a human-applied correction plus re-review or a separately
operator-confirmed `no_fix_required` decision when both reviewers are clean.

G43.2 remains **Proposed and inactive**. Its contract explicitly depends on
completed G43.1 provider-review dogfood; do not activate or implement it until
the evidence below is recorded.

## Approved dogfood authority

Use the existing authority only. Do not create another Gig.

| Field | Value |
| --- | --- |
| Project | `project_c5c0ebb7-06e6-45ee-abe7-c910057d47b4` |
| Gig | `gig_84de15da-f79d-4dd6-b39d-f02f29cc345c` |
| Name/version | `g44-contract-review` v1 |
| Authority state | approved and active |
| Review subject | `docs/development/v0.1.7/goals/G44-intent-routing-and-gig-candidates.md` |
| Requirements baseline | `docs/development/evidence/v0.1.7/G43.1/G44-requirements-baseline.md` |
| Baseline SHA-256 | `a293b003bfd1f56dd5e9ffb70159eb7d57e624c2021a180ace2c58a73045e59d` |

The baseline is committed public review material. It is **not** an approval,
provider permission, G44 activation, graph selection, or Run consent.

## Delivered implementation

`aaaf46f` (`feat: add typed G43.1 review inputs`) adds:

- `gigai run-plan approve-baseline --confirm`, which snapshots one explicit
  text/Markdown baseline and journals a digest-bound operator approval record;
- strict `review-input-record:1` and
  `requirements-baseline-approval:1` schemas, raising the packaged inventory
  from 34 to 36 without changing historical schema bytes;
- typed `review_subject` and `requirements_baseline` Plan inputs, with the
  subject, baseline, approval receipt, and records all sealed as sources;
- refusal of unjournaled, foreign, altered, missing, unsealed, duplicate, or
  role-mismatched baseline material;
- re-review binding to the original Plan and enforcement of an identical
  approved baseline digest; and
- role-labelled reviewer/verifier prompts. A `criterion_requirements` finding
  is accepted only when it cites both the subject and baseline locators.

`5bee36a` (`docs: add G43.1 G44 review baseline`) adds the baseline above and
links it from the G43.1 contract.

## Local verification already completed

- Focused G43 Run Plan/provider-review coverage passed, including new direct
  approval, forged-receipt, tampered-baseline, role-labelled evidence, and
  re-review regressions.
- Installed CLI harness passed.
- Full local test suite, Ruff, schema checksum verification, and diff checks
  passed before the implementation commit.
- No Claude, Codex, or other live provider was invoked.

The repository worktree is otherwise clean; the pre-existing untracked
`.gigai/` directory was deliberately left untouched.

## Remaining clean-closeout gap

The executor intentionally skips verifier and adjudicator calls when neither
reviewer produces a schema-valid finding. The G43.1 acceptance contract now
makes verifier evidence conditional on findings and still requires a terminal
report for every outcome.

The current implementation does **not** provide the separate direct-operator
confirmation, journal transition, or typed record needed to express
`no_fix_required`. A zero-finding provider report is model evidence, not that
operator decision. Implement and test that narrow closeout authority before
starting the cost-bearing public provider Run; do not invent the record by hand
or treat the clean report as equivalent. G43.1 remains incomplete until the
finding/re-review branch or the implemented no-fix branch closes honestly.

## Required operator actions

Run these only from the authority context that resolves the approved Gig above.
Add the same `--home` and `--target` values used to resolve that binding if
they are not implicit.

1. Inspect the baseline and directly approve its exact current bytes:

   ```sh
   shasum -a 256 docs/development/evidence/v0.1.7/G43.1/G44-requirements-baseline.md
   uv run gigai run-plan approve-baseline \
     --gig gig_84de15da-f79d-4dd6-b39d-f02f29cc345c \
     --input docs/development/evidence/v0.1.7/G43.1/G44-requirements-baseline.md \
     --confirm --json
   ```

   Record the returned `approval_id`. Chat authorization cannot replace this
   direct CLI confirmation.

2. After the no-fix closeout authority above exists, inspect configured target
   IDs before sealing. The first dogfood requires a real Claude Code reviewer,
   a real Codex-Luna reviewer, a sealed verifier, and a sealed adjudicator.
   Verifier and adjudicator calls occur only when a schema-valid finding is
   produced. Do not guess any target ID or silently reuse one.

   ```sh
   uv run gigai models
   ```

3. Seal the typed standard Plan with the selected targets. The standard profile
   has exactly two reviewers, one verifier, and one adjudicator. Supplying a
   target flag requires its exact participant count; a known configured default
   may be used only when intentionally omitted.

   ```sh
   uv run gigai run-plan create \
     --gig gig_84de15da-f79d-4dd6-b39d-f02f29cc345c \
     --review-subject docs/development/v0.1.7/goals/G44-intent-routing-and-gig-candidates.md \
     --requirements-baseline-approval REQUIREMENTS_BASELINE_APPROVAL_ID \
     --profile standard \
     --reviewer-target CLAUDE_CODE_TARGET \
     --reviewer-target CODEX_LUNA_TARGET \
     --verifier-target VERIFIER_TARGET \
     --adjudicator-target ADJUDICATOR_TARGET \
     --json
   ```

4. Inspect the sealed Plan, then separately and directly consent to the real
   provider Run:

   ```sh
   uv run gigai run-plan show RUN_PLAN_ID --json
   uv run gigai run --plan RUN_PLAN_ID --execute-review --confirm --wait --json
   ```

## Completion decision after the Run

- If a valid finding exists, a human applies the G44 contract correction. Create
  a fresh typed Plan using the **same** baseline approval and
  `--re-review-of ORIGINAL_RUN_PLAN_ID`, then directly confirm a fresh Run.
- If both independent reviewers are clean, use the separately implemented
  direct-operator closeout to record `no_fix_required`. Until that authority
  exists, stop and leave G43.1 incomplete; the clean provider report alone is
  insufficient.
- Preserve the plan, consent, invocation, finding, verifier/adjudicator, report,
  and terminal artifacts under the existing private workpad. Commit only
  sanitized public completion evidence to this repository.
- Stop and leave G43.1 incomplete if any provider call is unavailable, malformed,
  unaccounted, broadened beyond the sealed inputs, or lacks direct consent.

## Next owner

First implement and verify the narrow direct-operator `no_fix_required`
closeout authority described above. After the operator completes the direct
baseline approval, provide the approval JSON and `gigai models` output to the
implementation/review coordinator. After the real Run and the applicable
correction/re-review or operator no-fix branch close G43.1, activate G43.2 and
implement its Graph Set and graph-selection authority in a separate change.

## Addendum — 2026-09-07 local closeout implementation verified

The narrow `gigai provider-review closeout` authority described above is now
implemented and locally verified in the uncommitted worktree. Terra/Luna
implementation, independent review, corrections and adversarial regressions
are recorded in [SCOUT-01 coordinator verification](../Scout/SCOUT-01-coordinator-verification.md).
The full suite passed 697 tests with one explicit live-UAT skip; 15 new
adversarial tests passed separately from source and against the installed wheel.

This supersedes only the instruction to implement that missing local command.
It does **not** close G43.1 or supply baseline approval, provider readiness,
direct Run consent, a real review or a no-fix decision. The original public
G44 subject/baseline and existing private Gig remain unchanged by this work.
The next action is the direct baseline approval above, then share its JSON
and configured model target IDs. Separately billed provider budget is still
pending. SCOUT-02 / G43.2 runtime implementation remains gated on honest live
closeout, as tracked in the approved Scout roadmap.

## Addendum — 2026-09-08 operator-directed correction/re-review completed

The operator explicitly instructed the coordinator to correct the G44 subject
against the existing approved baseline and perform the fresh Claude-retaining
review. Both original document conflicts were corrected. The baseline approval
and baseline bytes remain unchanged; prior Run snapshots/findings remain intact.

Fresh Plan `run_plan_3322ecf0-b016-4730-bdc2-95e953c4f434` produced Run
`run_23375ef2-59f8-4139-848f-37a70da3f4c7`. Claude and Luna reviewed; Terra checked
all three emitted findings, and Sol rejected all three as requirements defects.
The provider result is `complete` and the committed Run is `succeeded`, with
no accepted or deferred findings. Target-before and target-after match.

This closes the correction/re-review branch under the latest explicit delegated
instruction. The coordinator, not the operator, applied the edits and typed
the CLI command. It does not attest to the historical manual-keyboard wording,
manufacture operator consent, or create a zero-finding `no_fix_required` receipt.
That separate command was not used and is not eligible for a three-finding Run.

[Full correction/re-review evidence](../Scout/SCOUT-01-G44-correction-rereview.md)
records the mapping, hashes, invocation identities, dispositions, focused test
result, delegation provenance, and remaining accounting limitations. This
supersedes the pending baseline/dogfood/next-owner instructions above. SCOUT-02
can now receive its separate implementation dispatch; it was not implemented
or activated by this correction task. No commit or release was created.
