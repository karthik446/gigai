# G43.1 Terminal Handoff — Typed G44 Contract Review

**Status:** G43.1 implementation committed; public provider dogfood pending.

## Where we are

G43.1 now has a local, tested implementation for a provider-backed review of a
public document against a separately approved public requirements baseline. It
is not complete: its closure requires a fresh, directly confirmed review using
real Claude and Codex-Luna reviewers, a configured verifier, and a human
decision on any valid finding.

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

2. Inspect configured target IDs before sealing. The first dogfood requires a
   real Claude Code reviewer, a real Codex-Luna reviewer, a verifier, and—if a
   finding is produced—the standard profile's adjudicator. Do not guess any
   target ID or silently reuse one.

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
- If both independent reviewers are clean, record `no_fix_required` evidence.
- Preserve the plan, consent, invocation, finding, verifier/adjudicator, report,
  and terminal artifacts under the existing private workpad. Commit only
  sanitized public completion evidence to this repository.
- Stop and leave G43.1 incomplete if any provider call is unavailable, malformed,
  unaccounted, broadened beyond the sealed inputs, or lacks direct consent.

## Next owner

After the operator completes the direct baseline approval, provide the approval
JSON and `gigai models` output to the implementation/review coordinator. After
the real Run and any required correction/re-review close G43.1, activate G43.2
and implement its Graph Set and graph-selection authority in a separate change.
