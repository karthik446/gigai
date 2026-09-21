# SCOUT-01 — Live review recovery

## First real typed review: completed invocation, blocked review

On 2026-09-08 the operator explicitly requested that the coordinator launch
and analyze the sealed Plan. The coordinator executed the existing CLI with
`--execute-review --confirm --wait --json`. The operator authorized this in
chat; the coordinator, not the operator, typed the command. The current CLI
records all such launches as `direct_cli_confirm`; that label does not prove
who physically typed it. No approval or consent artifacts were written by hand.
This execution must not be described as satisfying the historical requirement
for the operator personally to issue the terminal command.

| Identity | Value |
| --- | --- |
| Gig | `gig_84de15da-f79d-4dd6-b39d-f02f29cc345c`, v1 |
| Plan | `run_plan_c1e9326b-6358-40dd-8477-c7a477cd67ca` |
| Run | `run_b353feab-192a-478f-acc2-bdd3bee32571` |
| Provider terminal journal commit | `79dccf1a25696daa92ae66d9d69226591707f840` (`goal blocked`) |
| CLI / Run-details status | `succeeded` — misleading |
| Provider result status | `blocked`, two schema-valid findings |

All four CLI invocations returned successfully at the model-adapter layer.
No API adapter was used. Their immutable responses and invocation records live
under this Run's `model-invocations/`; provider findings and terminal artifacts
live under `provider-reviews/<Plan ID>/` in the existing private workpad.

| Participant | Actual outcome |
| --- | --- |
| Claude reviewer | Raised two concerns, but prefaced a JSON fence with prose; review parser rejected it |
| Luna reviewer | Returned two valid requirements findings |
| Terra verifier | Verified both Luna findings against subject and baseline |
| Sol adjudicator | Accepted both; explicitly qualified the clipboard omission as weaker |

The accepted findings are:

1. `finding_28f68c5d-eb68-46e7-8902-05f8a4ef0299`: the subject allows durable
   request-text persistence after explicit one-off selection; baseline item 5
   prohibits that for `one_off`. This is a direct contradiction.
2. `finding_f6cca09e-5e68-4479-8ff8-2eafb63a396f`: the subject does not explicitly
   prohibit browser/tool use or target effects during routing. Its source
   allowlist implicitly excludes clipboard input, so omission of the clipboard
   word alone is weaker than the missing effect restrictions.

These are document findings, not permission to rewrite the approved baseline.
The G44 subject and baseline hashes remain respectively
`92434595cd00b311e489c8cef9de7bb529482f43bebcc0de4dd5bda8642eeefc` and
`a293b003bfd1f56dd5e9ffb70159eb7d57e624c2021a180ace2c58a73045e59d`.

## Runtime defects exposed by dogfood

- Response framing: `_json_object` accepts raw JSON or a fence starting at the
  beginning, but not Claude's prose followed by one JSON fence. Preserve raw
  bytes and validate the extracted object; do not rewrite semantic findings.
- Status propagation: `launch_run` discards the provider result, starts the
  ordinary offline scheduler, then reports that scheduler's success. During
  provider execution, Run-details also remains `preparing`.
- Usage presentation: Run-details reports zero tokens and `not_applicable`
  cost despite four real model invocations. Individual records contain 64,519
  input and 5,758 output tokens in aggregate; this is not a billing total and
  does not include all Claude cache categories. Actual monetary cost is unknown.
- Budget limitation observed in source: the provider invocation ledger reserves
  requested output tokens, not actual total input plus output; the sealed
  30,000-token field must not be presented as proven total-token enforcement.
  Broader budget-policy correction is not claimed by the response/status repair.
- The generated human report is only a pointer to private evidence; the analysis
  above comes from the actual result, findings, verification, and adjudication.

No no-fix closeout was attempted. G43.1 and dependent Scout runtime delivery
remain incomplete.

## Authorized recovery work

The operator requested response handling and status fixes, retaining Claude as
a reviewer, followed by a rerun. The G44 subject/baseline remain unchanged so
the rerun tests runtime recovery, not correction of the two document findings.

Orca coordinator Run: `run_43c92f4fc427`.

| Task | Dispatch | Ownership |
| --- | --- | --- |
| `task_3ba8c49cf03f` | `ctx_29c827104dd9` | Luna: bounded response framing and new regressions |
| `task_c1e14e4f091c` | `ctx_21b64dc939e5` | Terra: provider Run lifecycle/status and new regressions |

Both use fresh agent sessions in the existing dirty worktree, with disjoint
file ownership. Independent review, coordinator verification, and live rerun
results will be recorded below when they actually occur.

## Recovery verification in progress

The coordinator replayed the original Claude `output_text` through the updated
parser in memory, using the original bundle's reference IDs and hash-checked
subject/baseline bytes. Both findings now validate (`finding_count: 2`,
`invalid: false`). The response file is byte-identical before/after, SHA-256
`d0cd4c540ef00884903bb094d0406e1419d78082437083e7d712fd425d3f6b42`.
This is offline parsing of a previous live response, not a new provider Run
or a retroactive change to the original blocked result.

The original dependency-gated review task `task_b979380f9b0c` was superseded
without dispatch by independent review `task_b54c44431122`, dispatch
`ctx_46f2840f66d8`. Its scope is the response/status repair only, not release
acceptance.

Coordination recovery: Luna's first completion attempt could not reach Orca
from its sandbox. The coordinator delivered the existing task's pending review
corrections to its terminal; the worker then applied them and attempted a final
completion RPC. That RPC remained pending. After inspecting the finished source,
the attempted final report, and independently passing the five framing tests,
the coordinator recorded task completion as `coordinator_recovery`, not as a
worker-owned receipt. Orca first refused a completion override on an active
Dispatch. Its subsequent `worker-stop` performed no action because the terminal
was `user_owned`; the Dispatch was abandoned without stopping or deleting any
resource, then the task was completed with explicit recovery provenance.
`worker-release` returned `retained / identity_unproven`. Terminal
`term_49131177-e85e-44d6-82cc-98da7574eb21` was therefore left intact.
No coordinator impersonation of `worker_done` or approval of a worker's
permission prompt was used. The coordinator subsequently clarified two prompt
sentences without changing parsing semantics.

Terra subsequently acknowledged its final implementation handoff and explicitly
stopped editing after its Dispatch capability had been revoked during the
coordination recovery. The coordinator inspected the source and worker report,
abandoned `ctx_21b64dc939e5`, and recorded task completion with explicit
`coordinator_recovery` provenance. No accepted worker completion is claimed for
that Dispatch. Its user-owned terminal was retained; no process was forced
closed. Root took ownership of integration and subsequent review corrections.

## Independent review and abandonment recovery

Independent Terra review returned one blocker, recorded in
[response/status review](SCOUT-01-response-status-review.md): a caller that
died after publishing `running`, or failed while authenticating terminal
evidence, could leave that state indefinitely. The review ran 13 focused tests
and Ruff successfully but correctly distinguished those from recovery coverage.
Its worker-owned completion was accepted and its terminal released/archived.

The coordinator added a POSIX process lease in disposable private `.git`
metadata, held before `run_started` through terminal publication. It is local
liveness coordination, not a portable authority record or a provider retry
permission. Status polling leaves the live lock holder alone; after the holder
exits, a reader takes the same lock, rechecks journal-authenticated Run records,
and publishes an `interrupted` handoff. Offline Goal states remain untouched.
Partial provider evidence is neither adopted nor rewritten during recovery;
journal conflicts still fail closed and require reconciliation. Unknown usage
remains unknown. A completed in-memory result cannot produce Run success
without the actual terminal result record.

New regressions exercise a real forked caller exiting during provider execution,
active status polling, repeated recovery, injected terminal authentication
failure, and missing terminal result. An intermediate over-strict check against
an absent handoff `project_id` field failed tests and was removed; the existing
workpad project binding and handoff Gig/Run/actor/commit checks remain.

Re-review task `task_533d1ecb13be`, dispatch `ctx_05b67fb00083`, independently
checked that correction and accepted the running-state abandonment recovery,
but found a narrower publication window: replaced terminal details could be
visible before their journal commit. See
[publication re-review](SCOUT-01-response-status-rereview.md). Its accepted
completion was followed by immediate reuse of the same reviewer terminal for
task `task_bbf1fabd10d7`, dispatch `ctx_c2ced14a00e4`.

The coordinator corrected status reads to authenticate their payload against
committed journal bytes. Uncommitted replacements now produce
`run_details_reconciliation_required`, not apparent success. This may also be
a transient refusal during a live writer; retry after publication. An abandoned
incomplete transaction uses the existing explicit journal-reconciliation path,
not automatic adoption by status reads. Four real-process crash fixtures cover
terminal artifact replacement, handoff replacement, before commit, and after
commit; post-reconciliation outcomes must match committed bytes. This patch is
under focused independent confirmation.

The earlier full-suite snapshot passed **727 tests, 1 skipped, 80 subtests**
in 451.72 seconds. It predates the final recovery changes and is not claimed
as verification of those changes; a fresh final-snapshot suite is running.

The framing, provider lifecycle, provider review, and Run Plan modules together
passed **33 tests in 75.13 seconds** after RS1, before the final RS2 publication
guard. Ruff passed. The final publication guard and added crash cases have
their own ongoing verification; earlier passing counts are not substituted.

## Fresh retry Plan

The coordinator created and inspected
`run_plan_cde78ed0-f2c8-47af-8a1c-33ef8c361680`, digest
`sha256:cf11200f8420c8355d4d6cde937f819aa34ba4cfa095b6b7cebed5985db194e1`.
It is sealed with no diagnostics and explicitly references the original Plan
as `re_review_of`, the same approved Gig and baseline, and unchanged subject
bytes. Participants remain Claude and Luna reviewers, Terra verifier, Sol
adjudicator. No API target was added and no baseline approval was repeated.

## Final-source focused verification

With the publication guard and all crash fixtures present, the coordinator ran:

- `tests/test_g43_provider_run_status.py`, `tests/test_g43_response_framing.py`,
  `tests/test_g43_provider_review.py`, and `tests/test_g43_run_plan.py`:
  **37 passed in 90.94 seconds**.
- `tests/test_g13_run.py` and `tests/test_g14_scheduler.py`: **16 passed in
  21.11 seconds**, covering the ordinary offline Run/scheduler callers.
- Ruff over the two changed runtime modules and focused test modules: passed.

Source SHA-256 for this verification: `run.py`
`2d91355526c4c1a80a62392185efd01643dbae87057c2692cedddf7750d4e852`;
`provider_review.py`
`b2c6bcdadc6264356522dacd1de78d1cbb37d668a2231118093e63be848b8fdb`.
These are temporary-workpad/offline tests, not the fresh provider retry.

[Final independent review](SCOUT-01-response-status-final-review.md) accepts
the RS2 publication correction and confirms RS1 recovery remains intact. The
reviewer independently ran 15 lifecycle tests in 46.48 seconds plus Ruff and
whitespace checks. This is narrow runtime acceptance, not G43.1 closeout.

The coordinator is now launching the fresh Plan using the existing CLI, under
the operator's explicit chat authorization to fix and rerun while retaining
Claude. As on the first Run, this is coordinator-executed CLI confirmation,
not a claim that the operator personally typed the command. Source and evidence
files will remain unchanged during provider execution.

## Live retry outcome — 2026-09-08

The fresh Run completed between **16:31:06 and 16:32:00 UTC**:

| Identity or check | Observed result |
| --- | --- |
| Run | `run_9b92eb24-f8ef-4a69-8b7f-9d3edab9c921` |
| Provider terminal commit | `de8aecd115755c0f42cb626339aa20bfd31ee27c` |
| Run terminal commit | `02ce2be636b616c9861a43051d369e728ba18779` |
| Provider result | `complete`, one finding |
| CLI and authenticated Run-details | `succeeded`, matching provider completion |
| Status queried during execution | `running`; no false offline completion |
| Offline Goal states | `ready` and `pending`, untouched |
| Target before/after | Identical, digest `sha256:696e86fc3553fec721f136e64596aa20eeddb747a44ab9c8644c994a56271c04` |
| Aggregate recorded usage | 63,605 input tokens, 1,693 output tokens; total and monetary cost unknown |

All four model invocations succeeded without invocation errors:

| Participant | Invocation | Validated output |
| --- | --- | --- |
| Claude reviewer | `inv_2e6cf38f-41f2-46d1-8a55-fba5d6f6477e` | Raw `{"findings":[]}`; no framing failure |
| Luna reviewer | `inv_ddc75f48-a73c-468c-afc9-a14c800418ab` | One requirements finding |
| Terra verifier | `inv_b783e553-3b1d-4d78-aabe-bc684527146e` | Finding verified against both sealed inputs |
| Sol adjudicator | `inv_57b3964c-6c89-41e7-a92c-a370fc2168dd` | Finding accepted |

The new finding is
`finding_06736cc8-5da5-4feb-aeaa-f8075f6cd476`: **One-off routing permits
request-text persistence**, severity `high`. The subject allows persistence
after explicit one-off selection; baseline required behavior 5 forbids a
durable request-text record for `one_off`. Both verifier and adjudicator
confirmed the contradiction.

Claude raised no findings on this invocation, unlike its first response.
This is observed evaluator variation, not evidence that either historical
finding was fixed. The second finding from the original Run was not emitted
again; no source changes or explicit resolution occurred, so its historical
record remains intact and must not be silently closed.

This verifies live CLI-backed response parsing and truthful provider Run
terminalization. It does **not** approve the G44 document or close G43.1/SCOUT-01.
No no-fix closeout, baseline edit, new approval, API call, or dependent Scout
activation was performed. The existing token-budget enforcement limitation
remains; `remaining_budget` still describes the sealed offline Graph rather
than a live provider-budget balance. Neither field is proof of billing or
total-token enforcement. In-flight aggregate usage is not a streaming total;
the corrected aggregate is published at terminalization.

The final reviewer's worker-owned completion was accepted and its reused
terminal was released with an archived transcript. Implementation workers
retained under user-ownership protection remain untouched as documented above.

## Final full-suite checkpoint

On the final runtime/test source snapshot, `GIGAI_G30_UAT=0 .venv/bin/pytest -q`
passed **734 tests, 1 skipped, 80 subtests** in **467.31 seconds**. The skip is
the opt-in G30 live-CLI test; the four-participant live retry above was executed
separately, not inferred from this suite. Seven warnings concern Python 3.13
forking from a multithreaded pytest process in the crash/concurrency fixtures;
all those fixtures completed successfully.

The intermediate RS1-only snapshot also passed 730 tests, 1 skipped, 80
subtests in 463.72 seconds. The 734-test result supersedes that snapshot for
final-source verification. Ruff and `git diff --check` pass; runtime, subject,
and baseline hashes match the final-source records above. No commit or release
was created. Runtime recovery is complete for this task; document-findings
resolution and the remaining Scout delivery are still outstanding.
